#!/usr/bin/env python3
"""Exercise the 0882 recovery through the migration runner on PostgreSQL."""
import importlib.util
import hashlib
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import unittest

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
from lib.disposable_pg_fixture import DisposablePostgres, postgres_fixture_group
import migration_0882

spec = importlib.util.spec_from_file_location('migrate', ROOT / 'tools/migrate.py')
assert spec is not None and spec.loader is not None
migrate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migrate)

TARGET = '0882_retire_reply_gate_controls.sql'
COMPLETION = '937252fb-f230-444a-9fe6-dee8885fa237'
CONDUCT = '3fa422b7-7c99-49fc-8e22-1e551a975c6f'
OLD_MAP = '9dff077bb048cd7813b5f50651f5e572eab2aa9c4ecf3c2f7164d3fae026c3ac'
SNAPSHOT = (ROOT / 'db/schema.sql').read_text()


def ddl(kind, name):
    pattern = rf'CREATE {kind} {re.escape(name)}\b.*?\n(?:\);|end \$\$;)'
    match = re.search(pattern, SNAPSHOT, re.S | re.I)
    if match is None:
        raise AssertionError(f'missing fixture DDL {name}')
    return match.group()


class Recovery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        initdb = shutil.which('initdb')
        paths = [Path(initdb).parent] if initdb else []
        paths.extend([Path('/opt/homebrew/opt/postgresql@18/bin'),
                      Path('/usr/lib/postgresql/18/bin'), Path('/usr/lib/postgresql/17/bin')])
        cls.bindir = next((p for p in paths if (p / 'initdb').is_file()), None)
        if cls.bindir is None:
            raise unittest.SkipTest('PostgreSQL server binaries unavailable')
        cls.group = postgres_fixture_group()
        cls.group.__enter__()
        cls.addClassCleanup(cls.group.__exit__, None, None, None)
        cls.pg = DisposablePostgres('carr-0882-fixture.', cls.bindir / 'pg_ctl')
        cls.addClassCleanup(cls.pg.close)
        cls.data = cls.pg.root / 'data'
        cls.pg.run([cls.bindir / 'initdb', '-D', cls.data, '-U', 'carr_ci',
                    '--auth=trust', '--encoding=UTF8', '--no-locale'],
                   check=True, capture_output=True)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            cls.port = sock.getsockname()[1]
        cls.pg.run([cls.bindir / 'pg_ctl', '-D', cls.data, '-l', cls.pg.root / 'server.log',
                    '-o', f'-h 127.0.0.1 -p {cls.port} -k {cls.pg.root}', '-w', 'start'],
                   check=True, capture_output=True)
        cls.base_dsn = f'postgresql://carr_ci@127.0.0.1:{cls.port}/postgres'
        with psycopg.connect(cls.base_dsn, autocommit=True) as conn:
            conn.execute('create role carr_authority')
        cls.migrations = migrate.load_migrations()

    def setUp(self):
        name = self.id().split('.')[-1]
        with psycopg.connect(self.base_dsn, autocommit=True) as conn:
            conn.execute(sql.SQL('create database {}').format(sql.Identifier(name)))
        self.dsn = self.base_dsn.rsplit('/', 1)[0] + '/' + name
        self.conn = psycopg.connect(self.dsn)
        self.addCleanup(self.conn.close)
        self.conn.execute('create schema ops; create extension pgcrypto')
        for table in ['public.rule', 'ops.rule_admission', 'ops.rule_amendment_receipt',
                      'ops.rule_approval_receipt', 'ops.rule_control_binding',
                      'ops.enforcement_control_catalog', 'ops.rule_delivery_activation_target',
                      'ops.rule_delivery_policy']:
            definition = ddl('TABLE', table)
            if table == 'ops.rule_amendment_receipt':
                definition = definition.replace('    prior_scope jsonb,\n', '').replace('    new_scope jsonb,\n', '')
                definition = re.sub(r'    CONSTRAINT rule_amendment_receipt_(?:scope_pair|not_noop).*?(?:,\n|\n\))',
                                    '', definition)
                definition = definition.replace(',\n);', '\n);')
            self.conn.execute(definition)
        self.conn.execute('alter table public.rule add primary key(id)')
        self.conn.execute('alter table ops.enforcement_control_catalog add primary key(control_key)')
        self.conn.execute('alter table ops.rule_control_binding add primary key(rule_id,control_key), '
                          'add foreign key(rule_id) references public.rule(id) on delete restrict, '
                          'add foreign key(control_key) references ops.enforcement_control_catalog(control_key) on delete restrict')
        self.conn.execute('alter table ops.rule_amendment_receipt add constraint rule_amendment_receipt_not_noop '
                          'check(prior_statement_hash<>new_statement_hash)')
        for function in ['ops.refuse_approved_rule_contract_rewrite',
                         'ops.refuse_live_approved_control_rewrite',
                         'ops.refuse_rule_approval_receipt_rewrite']:
            self.conn.execute(ddl('FUNCTION', function))
        for table, event, function in [
            ('rule_control_binding', 'insert or update or delete', 'refuse_approved_rule_contract_rewrite'),
            ('enforcement_control_catalog', 'update or delete', 'refuse_live_approved_control_rewrite'),
            ('rule_approval_receipt', 'update or delete', 'refuse_rule_approval_receipt_rewrite')]:
            self.conn.execute(f'create trigger immutable before {event} on ops.{table} '
                              f'for each row execute function ops.{function}()')
        self.conn.execute(migrate.BOOTSTRAP)
        for filename, _, digest in self.migrations:
            if filename < TARGET:
                self.conn.execute('insert into schema_migrations(filename,sha256) values (%s,%s)', (filename,digest))
        self.conn.execute('insert into ops.rule_delivery_policy(singleton) values(true)')
        for short_id, scope, pack in [
            ('113b3833','joe','governance-rules'), ('25fcddee','shared','governance-rules'),
            ('3fa17fa0','shared','client-deal'), ('49533583','joe','joe-comms'),
            ('557838a5','joe','joe-comms'), ('57d13061','joe','joe-comms'),
            ('72e06bdf','shared','client-deal'), ('c66dc739','joe','joe-comms')]:
            self.conn.execute('insert into ops.rule_delivery_activation_target values '
                              '(%s,%s,%s,\'session_boot\',\'surfacing\',%s,%s,\'pack_delivery\',\'stop_gate\',%s,%s,%s)',
                              (short_id,scope,pack,
                               'hooks/session-brief.py; hooks/machine-converge.py; mcp-server/src/mcp.js',
                               'command:python3 hooks/gate-integrity.py --selftest',
                               'hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py',
                               'ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py', OLD_MAP))
        keys = re.findall(r"^    '([a-z_]+)',?$", (ROOT / 'migrations' / TARGET).read_text(), re.M)
        for key in keys + ['context_rail', 'pack_delivery']:
            self.conn.execute('insert into ops.enforcement_control_catalog '
                              '(control_key,implementation_ref,test_ref,enforcement_class,installed,verified_at) '
                              'values(%s,%s,%s,\'stop_gate\',true,now())',
                              (key, 'hooks/rule-pack-drift-gate.py; hooks/rule-pack-preuse-reselection.py',
                               'ops/rule-pack-drift-gate-selftest.py; ops/rule-load-layer-check-selftest.py; ops/rule-pack-preuse-reselection-selftest.py'))
        self.conn.commit()

    def seed(self, active=False):
        for rule_id, key in [(COMPLETION,'completion_evidence'), (CONDUCT,'conduct_stop')]:
            self.conn.execute('insert into rule(id,statement,status,version,taught_by,activated_by) '
                              'values(%s,\'fixture\',%s,3,gen_random_uuid(),gen_random_uuid())',
                              (rule_id,'active' if active else 'retired'))
            self.conn.execute('insert into ops.rule_control_binding(rule_id,control_key,statement_hash,binding_contract) '
                              'values(%s,%s,%s,\'{}\')', (rule_id,key,'a'*64))
        self.conn.execute('insert into ops.rule_approval_receipt '
                          '(idempotency_key,rule_id,rule_version,statement_hash,actor_id,reason,policy_kind,'
                          'enforcement_status,normalized_contract,requested_control_keys,installed_control_keys,contract_hash,evidence_refs) '
                          'values(\'historical-v2\',%s,2,%s,gen_random_uuid(),\'fixture\',\'machine_enforceable\','
                          '\'hard_enforced\',\'{}\',array[\'completion_evidence\'],array[\'completion_evidence\'],'
                          'encode(digest(\'{}\'::jsonb::text,\'sha256\'),\'hex\'),array[\'fixture\'])', (COMPLETION,'a'*64))
        self.conn.commit()

    def run_runner(self):
        return subprocess.run([sys.executable, str(ROOT / 'tools/migrate.py'), '--apply','--yes',
                               '--through','0886_active_rule_scope_backfill.sql'],
                              env={**os.environ,'DATABASE_URL':self.dsn}, capture_output=True,text=True)

    def test_retired_history_survives_and_suffix_applies(self):
        self.seed()
        history = self.conn.execute('select to_jsonb(ar) from ops.rule_approval_receipt ar').fetchall()
        bindings = self.conn.execute('select to_jsonb(b) from ops.rule_control_binding b order by control_key').fetchall()
        self.conn.commit()
        result = self.run_runner()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('retained retired-v3 historical controls', result.stdout)
        self.assertEqual(self.conn.execute('select to_jsonb(ar) from ops.rule_approval_receipt ar').fetchall(), history)
        self.assertEqual(self.conn.execute('select to_jsonb(b) from ops.rule_control_binding b order by control_key').fetchall(), bindings)
        self.assertEqual(self.conn.execute('select control_key from ops.rule_control_binding order by 1').fetchall(),
                         [('completion_evidence',),('conduct_stop',)])
        self.assertEqual(self.conn.execute('select control_key from ops.enforcement_control_catalog order by 1').fetchall(),
                         [('completion_evidence',),('conduct_stop',),('pack_delivery',)])
        self.assertEqual(self.conn.execute("select count(*) from schema_migrations where filename>=%s", (TARGET,)).fetchone()[0],5)
        for name, _, digest in self.migrations:
            if TARGET <= name <= '0886_active_rule_scope_backfill.sql':
                self.assertEqual(self.conn.execute('select sha256 from schema_migrations where filename=%s', (name,)).fetchone()[0], digest)
        with self.assertRaisesRegex(psycopg.errors.RaiseException, 'contract is immutable'):
            with self.conn.transaction():
                self.conn.execute('delete from ops.rule_control_binding where rule_id=%s', (COMPLETION,))
        # PostgreSQL 18 gives RESTRICT its own SQLSTATE; 17 uses ForeignKeyViolation.
        with self.assertRaises((psycopg.errors.ForeignKeyViolation,
                                psycopg.errors.RestrictViolation)) as refusal:
            with self.conn.transaction():
                self.conn.execute("delete from ops.enforcement_control_catalog where control_key='conduct_stop'")
        self.assertEqual(refusal.exception.diag.constraint_name,
                         'rule_control_binding_control_key_fkey')
        self.assertEqual(self.conn.execute('select to_jsonb(b) from ops.rule_control_binding b order by control_key').fetchall(), bindings)
        self.assertEqual(self.conn.execute('select control_key from ops.enforcement_control_catalog order by 1').fetchall(),
                         [('completion_evidence',),('conduct_stop',),('pack_delivery',)])
        self.conn.commit()
        repeat = self.run_runner()
        self.assertEqual(repeat.returncode,0,repeat.stderr)
        self.assertIn('nothing to do',repeat.stdout)

    def test_staging_without_bindings_and_already_applied(self):
        result = self.run_runner()
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertNotIn('retained retired-v3 historical controls',result.stdout)
        self.assertEqual(self.conn.execute('select control_key from ops.enforcement_control_catalog order by 1').fetchall(), [('pack_delivery',)])
        self.conn.commit()
        self.assertEqual(self.run_runner().returncode,0)

    def test_active_rule_refuses_atomically(self):
        self.seed(active=True)
        result = self.run_runner()
        self.assertNotEqual(result.returncode,0)
        self.assertIn('0882 recovery REFUSED',result.stderr)
        self.assertEqual(self.conn.execute('select count(*) from schema_migrations where filename>=%s',(TARGET,)).fetchone()[0],0)
        self.assertTrue(self.conn.execute("select 1 from ops.enforcement_control_catalog where control_key='artifact_read'").fetchone())

    def test_active_binding_without_approval_refuses(self):
        self.seed()
        self.conn.execute('update rule set status=\'active\' where id=%s', (CONDUCT,))
        self.conn.commit()
        result = self.run_runner()
        self.assertNotEqual(result.returncode,0)
        self.assertIn('0882 recovery REFUSED',result.stderr)

    def test_active_approval_on_another_rule_refuses(self):
        self.seed()
        other = '11111111-1111-4111-8111-111111111111'
        self.conn.execute('insert into rule(id,statement,status,taught_by,activated_by) '
                          'values(%s,\'other fixture\',\'active\',gen_random_uuid(),gen_random_uuid())', (other,))
        self.conn.execute("insert into ops.rule_approval_receipt select "
                          "(jsonb_populate_record(null::ops.rule_approval_receipt, "
                          "to_jsonb(ar)||jsonb_build_object('id',gen_random_uuid(), "
                          "'idempotency_key','other-active','rule_id',%s::uuid))).* "
                          "from ops.rule_approval_receipt ar", (other,))
        self.conn.commit()
        with self.assertRaisesRegex(psycopg.errors.RaiseException, 'backs an active approved rule'):
            with self.conn.transaction():
                self.conn.execute("delete from ops.enforcement_control_catalog where control_key='completion_evidence'")
        self.conn.commit()
        result = self.run_runner()
        self.assertNotEqual(result.returncode,0)
        self.assertIn('backs an active approval',result.stderr)

    def test_unexpected_version_refuses(self):
        self.seed()
        self.conn.execute('update rule set version=4 where id=%s', (CONDUCT,))
        self.conn.commit()
        result = self.run_runner()
        self.assertNotEqual(result.returncode,0)
        self.assertIn('unexpected binding or rule state',result.stderr)

    def test_source_drift_refuses(self):
        original = (ROOT / 'migrations' / TARGET).read_text()
        changed = original + '\n'
        with self.assertRaisesRegex(ValueError,'source checksum changed'):
            migration_0882.retirement_sql(None,changed,hashlib.sha256(changed.encode()).hexdigest())

    def test_absent_bindings_preserve_original_execution(self):
        original = (ROOT / 'migrations' / TARGET).read_text()
        with self.conn.cursor() as cur:
            self.assertEqual(migration_0882.retirement_sql(cur,original,hashlib.sha256(original.encode()).hexdigest()), original)


if __name__ == '__main__':
    unittest.main()
