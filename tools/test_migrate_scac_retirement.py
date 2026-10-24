"""Retire the stale catalog seal without weakening deferred source integrity."""
import contextlib
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest.mock import patch

import migrate

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_SOURCE = '7f6055b112e103e8af9e2568eca23796e2332ff2'
MAIN_SOURCE = 'fd3c46a45a7d0cc2e08c4e9b3268c73ec8a14e17'
BLOCKED = '0951_deal_vendor_assignments.sql'
REPAIR = '0957_retire_scac_catalog_commit_check.sql'
PENDING = (
    BLOCKED,
    '0952_deal_vendor_grant_retirements.sql',
    '0953_morning_brief_chicago_calendar.sql',
    '0954_lead_workspace_set_reads.sql',
    '0955_retrieval_concept_question_matching.sql',
    REPAIR,
)

spec = importlib.util.spec_from_file_location('scac_retirement_shadow', ROOT / 'ops/migration-shadow.py')
assert spec is not None and spec.loader is not None
shadow = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = shadow
spec.loader.exec_module(shadow)


class ScacRecoveryOrderingTests(unittest.TestCase):
    def test_repair_precedes_every_pending_suffix_in_one_transaction(self):
        for offset in range(len(PENDING)):
            with self.subTest(applied=PENDING[:offset]):
                files = [(name, 'select 1;', 'synthetic') for name in PENDING[offset:]]
                batches = migrate.migration_batches(files)
                self.assertEqual([[item[0] for item in batch] for batch in batches],
                                 [[REPAIR, *PENDING[offset:-1]]])

    def test_through_cannot_split_the_recovery(self):
        files = [(name, 'select 1;', 'synthetic') for name in PENDING]
        for name in PENDING[:-1]:
            with self.subTest(through=name), self.assertRaisesRegex(ValueError, 'atomic migration group'):
                migrate.migrations_through(files, files, name)

    def test_future_mutation_needs_no_successor(self):
        future = ('0999_future_mutation.sql', 'select 1;', 'synthetic')
        self.assertEqual(migrate.migration_batches([future]), [[future]])


class ScacRetirementMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bins = shadow.find_pg18(None)
        if cls.bins is None:
            raise unittest.SkipTest('disposable PostgreSQL 18 is unavailable')
        import psycopg
        cls.psycopg = psycopg
        cls.child = shadow.local_pg.scrub_cloud_environment(os.environ)
        cls.fixture = shadow.local_pg.DisposablePostgres('scac-retirement.', cls.bins / 'pg_ctl', cls.child)
        cls.addClassCleanup(cls.fixture.close)
        cls.port = shadow.free_port(55900)
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
        source = subprocess.check_output(['git', 'show', f'{PRODUCTION_SOURCE}:db/schema.sql'], cwd=ROOT)
        loaded = subprocess.run([str(cls.bins / 'psql'), cls.dsn, '-X', '-v', 'ON_ERROR_STOP=1', '-q'],
                                input=source, env=cls.child, capture_output=True, timeout=120)
        if loaded.returncode:
            raise RuntimeError(loaded.stderr.decode()[-2000:])
        with psycopg.connect(cls.dsn, autocommit=True) as conn:
            highest = conn.execute('select max(filename) from schema_migrations').fetchone()[0]
            if highest != '0950_remove_retired_workflow_manifest_binding.sql':
                raise RuntimeError(f'fixture must end at production migration 0950; found {highest}')
        sys.path.insert(0, str(ROOT / 'ops'))
        from rule_projection_fixture import seed_reviewed_rule_projection
        with psycopg.connect(cls.dsn) as conn, conn.cursor() as cur:
            seed_reviewed_rule_projection(cur, slug='scac-retirement-fixture',
                title='SCAC retirement regression', rule_label='SCAC retirement fixture', uuid_tail='000000009556')
        cls.main = types.ModuleType('original_main_migrate')
        cls.main.__file__ = str(ROOT / 'tools/migrate.py')
        source = subprocess.check_output(['git', 'show', f'{MAIN_SOURCE}:tools/migrate.py'], cwd=ROOT, text=True)
        exec(compile(source, cls.main.__file__, 'exec'), cls.main.__dict__)

    def runner(self, module=migrate, through=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = ['migrate.py', '--apply', '--yes']
        if through:
            argv.extend(['--through', through])
        with patch.dict(os.environ, {'DATABASE_URL': self.dsn}), patch.object(sys, 'argv', argv), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                module.main()
            except SystemExit as exc:
                return exc.code, stdout.getvalue(), stderr.getvalue()
            except self.psycopg.Error as exc:
                return 1, stdout.getvalue(), stderr.getvalue() + str(exc)
        return 0, stdout.getvalue(), stderr.getvalue()

    def test_production_shaped_chain_and_future_mutation_preserve_integrity(self):
        with self.psycopg.connect(self.dsn, autocommit=True) as conn:
            self.assertGreater(conn.execute('select count(*) from ops.scac_policy_epoch').fetchone()[0], 0)
            before = dict(conn.execute('select filename,sha256 from schema_migrations').fetchall())
            failed, _, error = self.runner(self.main, through=BLOCKED)
            self.assertEqual(failed, 1)
            expected = f'commit refused for migration batch [{BLOCKED}] and the whole batch was rolled back: live SCAC v37 mutation catalog drifted'
            self.assertIn(expected, error)
            print(expected)
            self.assertEqual(dict(conn.execute('select filename,sha256 from schema_migrations').fetchall()), before)
            self.assertIsNone(conn.execute("select to_regclass('public.deal_vendor_assignment')").fetchone()[0])
            snapshot_before = conn.execute("select pg_get_functiondef('ops.scac_policy_epoch_snapshot()'::regprocedure)").fetchone()[0]
            refresh_before = conn.execute("select pg_get_functiondef('ops.scac_policy_epoch_refresh()'::regprocedure)").fetchone()[0]
            snapshot_security_before = conn.execute("select prosecdef,proconfig,proacl from pg_proc where oid='ops.scac_policy_epoch_snapshot()'::regprocedure").fetchone()
            conn.execute("""alter table schema_migrations add constraint scac_retirement_rollback_fixture
                check (filename <> '0955_retrieval_concept_question_matching.sql') not valid""")
            result, output, error = self.runner()
            self.assertNotEqual(result, 0)
            self.assertIn('scac_retirement_rollback_fixture', error)
            self.assertEqual(dict(conn.execute('select filename,sha256 from schema_migrations').fetchall()), before)
            self.assertEqual(conn.execute("select pg_get_functiondef('ops.scac_policy_epoch_snapshot()'::regprocedure)").fetchone()[0], snapshot_before)
            self.assertIsNone(conn.execute("select to_regclass('public.deal_vendor_assignment')").fetchone()[0])
            self.assertEqual(conn.execute("select pg_get_functiondef('ops.scac_policy_epoch_refresh()'::regprocedure)").fetchone()[0], refresh_before)
            self.assertIsNone(conn.execute("select to_regprocedure('ops.policy_integrity_snapshot()')").fetchone()[0])
            conn.execute('alter table schema_migrations drop constraint scac_retirement_rollback_fixture')
            result, output, error = self.runner()
            self.assertEqual(result, 0, output + error)
            applied = dict(conn.execute('select filename,sha256 from schema_migrations').fetchall())
            self.assertTrue(set(PENDING).issubset(applied))
            self.assertEqual(conn.execute("select prosecdef,proconfig,proacl from pg_proc where oid='ops.scac_policy_epoch_snapshot()'::regprocedure").fetchone(), snapshot_security_before)
            self.assertEqual(conn.execute("select pg_get_functiondef('ops.scac_policy_epoch_snapshot()'::regprocedure)").fetchone()[0], snapshot_before)
            self.assertEqual(conn.execute("select prosecdef,proconfig from pg_proc where oid='ops.policy_integrity_snapshot()'::regprocedure").fetchone(),
                             (True, ['search_path=pg_catalog, public, ops, pg_temp']))
            self.assertEqual(conn.execute("""select count(*) from pg_proc p,
                lateral aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
                where p.oid='ops.policy_integrity_snapshot()'::regprocedure
                and a.grantee=0 and a.privilege_type='EXECUTE'""").fetchone()[0], 0)
            for role in ('carr_reader', 'carr_writer', 'carr_jobs', 'carr_authority'):
                with self.subTest(helper_access=role):
                    self.assertFalse(conn.execute("select has_function_privilege(%s,'ops.policy_integrity_snapshot()','execute')", (role,)).fetchone()[0])
            self.assertEqual({name: applied[name] for name in before}, before)
            migrate.validate_applied_ledger(migrate.load_migrations(), applied, migrate.load_baseline())
            self.assertTrue(conn.execute("select has_table_privilege('carr_writer','public.deal_vendor_assignment','insert')").fetchone()[0])
            self.assertLess(output.index(f'applying {REPAIR}'), output.index(f'applying {BLOCKED}'))
            print('0951-0955 committed unchanged after SCAC retirement')
            future_sql = '''create function public.scac_retirement_future_mutation() returns integer
                language sql security definer set search_path=pg_catalog as 'select 1';
                revoke all on function public.scac_retirement_future_mutation() from public;
                grant execute on function public.scac_retirement_future_mutation() to carr_writer;'''
            future = ('0999_future_mutation.sql', future_sql, hashlib.sha256(future_sql.encode()).hexdigest())
            with patch.object(migrate, 'load_migrations', return_value=[*migrate.load_migrations(), future]):
                result, output, error = self.runner()
            self.assertEqual(result, 0, output + error)
            self.assertEqual(conn.execute('select public.scac_retirement_future_mutation()').fetchone()[0], 1)
            self.assertEqual(conn.execute('select sha256 from schema_migrations where filename=%s', (future[0],)).fetchone()[0], future[2])
            conn.execute('update doctrine_meta set generation=generation+1 where id=1')
            for statement, message in (
                ("update schema_migrations set sha256=repeat('f',64) where filename=(select min(filename) from schema_migrations)", 'schema ledger prefix rewrite refused'),
                ("delete from schema_migrations where filename='0999_future_mutation.sql'", 'schema ledger rollback refused'),
                ('update doctrine_meta set generation=generation-1 where id=1', 'doctrine generation rollback refused'),
                ('delete from ops.rule_load_layer where rule_id=(select rule_id from ops.rule_load_layer limit 1)', 'rule delivery projection is not activation-safe'),
            ):
                with self.subTest(integrity=message), self.assertRaisesRegex(self.psycopg.errors.RaiseException, message):
                    with conn.transaction():
                        conn.execute(statement)
            for trigger in ('scac_epoch_schema_ledger', 'scac_epoch_doctrine_generation', 'scac_epoch_schema_truncate_refused',
                            'active_approved_control_immutable', 'approved_rule_control_binding_immutable'):
                with self.subTest(trigger=trigger):
                    self.assertEqual(conn.execute('select tgenabled from pg_trigger where tgname=%s', (trigger,)).fetchone(), ('O',))


if __name__ == '__main__':
    unittest.main()
