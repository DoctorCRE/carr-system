"""Approved controls survive the pending deploy chain without losing enforcement."""
from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest

import migrate

ROOT = Path(__file__).resolve().parents[1]
BLOCKED = '0940_codex_job_delegation_control_refs.sql'
REPAIR = '0945_allow_approved_control_evidence_refresh.sql'
CONTROL = 'delegation_names_model_and_effort'

spec = importlib.util.spec_from_file_location('control_metadata_shadow', ROOT / 'ops/migration-shadow.py')
shadow = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = shadow
spec.loader.exec_module(shadow)


class ControlRepairOrderingTests(unittest.TestCase):
    def setUp(self):
        self.names = [BLOCKED, '0944_retire_orphan_control_inventory.sql', REPAIR]
        self.files = [(name, 'select 1;', 'synthetic') for name in self.names]

    def test_repair_precedes_each_pending_recovery_suffix_in_one_transaction(self):
        for offset in range(3):
            with self.subTest(applied=self.names[:offset]):
                batches = migrate.migration_batches(self.files[offset:])
                self.assertEqual([[item[0] for item in batch] for batch in batches],
                                 [[REPAIR, *self.names[offset:-1]]])

    def test_through_cannot_commit_a_recovery_prefix_without_the_repair(self):
        for offset in (0, 1):
            with self.subTest(through=self.names[offset]):
                with self.assertRaisesRegex(ValueError, 'atomic migration group'):
                    migrate.migrations_through(self.files, self.files, self.names[offset])

    def test_existing_0940_checksum_and_ledger_order_remain_immutable(self):
        current = {name: digest for name, _, digest in self.files}
        with self.assertRaisesRegex(migrate.AppliedMigrationLedgerError, 'sha mismatch'):
            migrate.validate_ledger_identities(current, {BLOCKED: 'changed'})
        with self.assertRaisesRegex(migrate.AppliedMigrationLedgerError, 'reordered'):
            migrate.validate_ledger_identities(current, {REPAIR: 'synthetic'})
        for count in range(4):
            migrate.validate_ledger_identities(current, dict(list(current.items())[:count]))


class ApprovedControlMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bins = shadow.find_pg18(None)
        if cls.bins is None:
            raise unittest.SkipTest('disposable PostgreSQL 18 is unavailable')
        import psycopg
        cls.psycopg = psycopg
        cls.child = shadow.local_pg.scrub_cloud_environment(os.environ)
        cls.fixture = shadow.local_pg.DisposablePostgres('approved-control.', cls.bins / 'pg_ctl', cls.child)
        cls.addClassCleanup(cls.fixture.close)
        cls.port = shadow.free_port(55800)
        cls.dsn = f'postgres://carr_ci@127.0.0.1:{cls.port}/carr_ci'
        cls.fixture.run([cls.bins / 'initdb', '-D', cls.fixture.root / 'data', '-U', 'carr_ci',
                         '--auth=trust', '--encoding=UTF8', '--no-locale'], check=True, capture_output=True)
        cls.fixture.run([cls.bins / 'pg_ctl', '-D', cls.fixture.root / 'data', '-l', cls.fixture.root / 'postgres.log',
                         '-o', f'-h 127.0.0.1 -p {cls.port} -k {cls.fixture.root}', '-w', 'start'],
                        check=True, capture_output=True)
        cls.fixture.run([cls.bins / 'createdb', '-h', '127.0.0.1', '-p', str(cls.port), '-U', 'carr_ci', 'carr_ci'],
                        check=True, capture_output=True)
        with psycopg.connect(cls.dsn, autocommit=True) as conn:
            conn.execute('create role neondb_owner')
        loaded = subprocess.run([str(cls.bins / 'psql'), cls.dsn, '-X', '-v', 'ON_ERROR_STOP=1', '-q',
                                 '-f', str(ROOT / 'db/schema.sql')], env=cls.child,
                                capture_output=True, text=True, timeout=120)
        if loaded.returncode:
            raise RuntimeError(loaded.stderr[-2000:])
        with psycopg.connect(cls.dsn, autocommit=True) as conn:
            conn.execute('delete from schema_migrations where filename >= %s', (BLOCKED,))
        sys.path.insert(0, str(ROOT / 'ops'))
        from rule_projection_fixture import seed_reviewed_rule_projection
        with psycopg.connect(cls.dsn) as conn, conn.cursor() as cur:
            seed_reviewed_rule_projection(cur, slug='control-metadata-fixture',
                title='Control metadata regression', rule_label='Control metadata fixture',
                uuid_tail='000000009445')

    def runner(self, *args):
        return subprocess.run([sys.executable, str(ROOT / 'tools/migrate.py'), '--apply', '--yes', *args],
                              cwd=ROOT, env={**self.child, 'DATABASE_URL': self.dsn},
                              capture_output=True, text=True, timeout=120)

    @contextmanager
    def refused(self, conn):
        before = conn.execute('select to_jsonb(c) from ops.enforcement_control_catalog c where control_key=%s',
                              (CONTROL,)).fetchone()
        with self.assertRaisesRegex(self.psycopg.errors.RaiseException, 'backs an active approved rule'):
            with conn.transaction():
                yield
        self.assertEqual(before, conn.execute(
            'select to_jsonb(c) from ops.enforcement_control_catalog c where control_key=%s', (CONTROL,)).fetchone())

    def test_pending_chain_refreshes_evidence_and_keeps_approved_rule_delivered(self):
        with self.psycopg.connect(self.dsn, autocommit=True) as conn:
            source = (ROOT / 'migrations/0228_atomic_rule_lifecycle_forward_upgrade.sql').read_text()
            start = source.index('create or replace function ops.refuse_live_approved_control_rewrite()')
            end = source.index('create or replace view ops.v_rule_enforcement_status', start)
            conn.execute(source[start:end])
            conn.execute("""update ops.enforcement_control_catalog
                set implementation_ref='old-launcher',test_ref='old-test'
                where control_key=%s""", (CONTROL,))
            with conn.transaction():
                conn.execute("insert into actor(slug,kind,display_name) values ('joe','human','Joe') on conflict(slug) do nothing")
                joe = conn.execute("select id from actor where slug='joe'").fetchone()[0]
                rule = conn.execute("""insert into rule(statement,human_quote,taught_by,status)
                    values ('Name the delegation model and effort.','Local regression approval.',%s,'proposed')
                    returning id""", (joe,)).fetchone()[0]
                conn.execute("""insert into ops.rule_load_layer(rule_id,short_id,load_layer,packs,scope,why,source,map_digest)
                    values (%s,left(%s::text,8),'control','{}','shared','Migration regression fixture',
                    'tools/test_migrate_control_metadata.py',repeat('0',64))""", (rule, rule))
                conn.execute("""insert into ops.rule_control_binding(rule_id,control_key,statement_hash,binding_contract)
                    select id,%s,encode(digest(statement,'sha256'),'hex'),'{"fixture":"approved control metadata"}'
                    from rule where id=%s""", (CONTROL, rule))
                conn.execute('create role carr_authority_joe login; grant carr_authority to carr_authority_joe')
                conn.execute("""select set_config('carr.acting_actor_slug','joe',true),
                    set_config('carr.sponsoring_human_slug','joe',true),
                    set_config('carr.verified_human_actor_slug','joe',true)""")
                conn.execute('set session authorization carr_authority_joe')
                approved = conn.execute("""select ops.approve_rule(%s,'machine_enforceable',array[%s],
                    'metadata-regression','Local migration regression')""", (rule, CONTROL)).fetchone()[0]
                conn.execute('reset session authorization')
                self.assertEqual(approved['policy_status'], 'active')
            receipt = conn.execute('select to_jsonb(ar) from ops.rule_approval_receipt ar where rule_id=%s',
                                   (rule,)).fetchone()
            before = conn.execute('select * from ops.v_rule_enforcement_status where rule_id=%s', (rule,)).fetchone()
            self.assertEqual(conn.execute('select count(*) from ops.applicable_rules() where rule_id=%s', (rule,)).fetchone()[0], 1)
            guard = conn.execute("select pg_get_functiondef('ops.refuse_live_approved_control_rewrite()'::regprocedure)").fetchone()[0]
            conn.execute("""alter table schema_migrations add constraint control_metadata_failure_fixture
                check (filename <> '0944_retire_orphan_control_inventory.sql') not valid""")
            failed = self.runner()
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn('control_metadata_failure_fixture', failed.stderr)
            self.assertEqual(conn.execute('select count(*) from schema_migrations where filename >= %s', (BLOCKED,)).fetchone()[0], 0)
            self.assertEqual(conn.execute('select implementation_ref from ops.enforcement_control_catalog where control_key=%s', (CONTROL,)).fetchone()[0], 'old-launcher')
            self.assertEqual(conn.execute("select pg_get_functiondef('ops.refuse_live_approved_control_rewrite()'::regprocedure)").fetchone()[0], guard)
            conn.execute('alter table schema_migrations drop constraint control_metadata_failure_fixture')
            result = self.runner()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(conn.execute('select implementation_ref,test_ref,enforcement_class,installed from ops.enforcement_control_catalog where control_key=%s',
                (CONTROL,)).fetchone(), ('bin/codex-job', 'tools/test-codex-job.py', 'deny_gate', True))
            self.assertEqual(conn.execute('select to_jsonb(ar) from ops.rule_approval_receipt ar where rule_id=%s', (rule,)).fetchone(), receipt)
            self.assertEqual(conn.execute('select * from ops.v_rule_enforcement_status where rule_id=%s', (rule,)).fetchone(), before)
            self.assertEqual(conn.execute('select count(*) from ops.applicable_rules() where rule_id=%s', (rule,)).fetchone()[0], 1)
            for assignment in ("enforcement_class='stop_gate'", 'installed=false', "control_key='changed-control'"):
                with self.subTest(assignment=assignment), self.refused(conn):
                    conn.execute(f"update ops.enforcement_control_catalog set implementation_ref='bad-ref',{assignment} where control_key=%s", (CONTROL,))
            with self.refused(conn):
                conn.execute('delete from ops.enforcement_control_catalog where control_key=%s', (CONTROL,))
            self.assertEqual(conn.execute("select tgenabled from pg_trigger where tgname='active_approved_control_immutable'").fetchone()[0], 'O')
            applied = dict(conn.execute('select filename,sha256 from schema_migrations').fetchall())
            migrate.validate_applied_ledger(migrate.load_migrations(), applied, migrate.load_baseline())
            self.assertIn(REPAIR, applied)
            self.assertEqual(self.runner().returncode, 0)


if __name__ == '__main__':
    unittest.main()
