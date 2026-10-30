"""The database ledger accepts reverse arrivals and preserves every applied row."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import test_migrate_control_metadata as control_fixture


class UnorderedLedgerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        control_fixture.ApprovedControlMigrationTests.setUpClass.__func__(cls)
        with cls.psycopg.connect(cls.dsn, autocommit=True) as conn:
            conn.execute('create database wr578_ledger_template template carr_ci')

    def test_adoption_refuses_history_that_differs_from_the_prior_epoch(self):
        migration = next((Path(__file__).resolve().parents[1] / 'migrations').glob('*_unordered_applied_ledger.sql'))
        with self.psycopg.connect(self.dsn, autocommit=True) as conn:
            with self.assertRaisesRegex(self.psycopg.errors.RaiseException, 'schema ledger adoption refused'):
                with conn.transaction():
                    conn.execute('alter table schema_migrations disable trigger schema_migrations_immutable')
                    conn.execute("update schema_migrations set sha256=repeat('f',64) where filename=(select min(filename) from schema_migrations)")
                    conn.execute(migration.read_text())

    def test_reverse_arrivals_extend_the_epoch_without_rewriting_history(self):
        with self.psycopg.connect(self.dsn, autocommit=True) as conn:
            before = conn.execute("select epoch from ops.scac_policy_epoch order by epoch desc limit 1").fetchone()[0]
            for name, digest in [('9998_unordered_high.sql', 'a' * 64),
                                 ('9997_unordered_low.sql', 'b' * 64)]:
                with conn.transaction():
                    conn.execute('insert into schema_migrations(filename,sha256) values(%s,%s)', (name, digest))
            self.assertEqual(conn.execute("select filename,sha256 from schema_migrations where filename like '999%unordered%' order by filename").fetchall(),
                             [('9997_unordered_low.sql', 'b' * 64), ('9998_unordered_high.sql', 'a' * 64)])
            self.assertEqual(conn.execute("select epoch from ops.scac_policy_epoch order by epoch desc limit 1").fetchone()[0], before + 2)
            source = conn.execute('select ops.policy_integrity_snapshot()').fetchone()[0]
            epoch = conn.execute('select schema_applied_count,schema_ledger_digest from ops.scac_policy_epoch order by epoch desc limit 1').fetchone()
            self.assertEqual(epoch, (source['schema_applied_count'], source['schema_ledger_digest']))

    def test_applied_rows_cannot_be_changed_deleted_or_replaced(self):
        with self.psycopg.connect(self.dsn, autocommit=True) as conn:
            before = conn.execute('select filename,sha256,applied_at from schema_migrations order by filename').fetchall()
            for statement in (
                "update schema_migrations set sha256=repeat('f',64) where filename=(select min(filename) from schema_migrations)",
                "update schema_migrations set filename='9996_renamed.sql' where filename=(select min(filename) from schema_migrations)",
                "update schema_migrations set applied_at=clock_timestamp() where filename=(select min(filename) from schema_migrations)",
                'delete from schema_migrations where filename=(select min(filename) from schema_migrations)',
                'truncate schema_migrations',
                "delete from schema_migrations where filename=(select min(filename) from schema_migrations); insert into schema_migrations(filename,sha256) values('9995_replacement.sql',repeat('a',64))",
            ):
                with self.subTest(statement=statement), self.assertRaises(self.psycopg.errors.RaiseException):
                    with conn.transaction():
                        conn.execute(statement)
            self.assertEqual(conn.execute('select filename,sha256,applied_at from schema_migrations order by filename').fetchall(), before)

    def test_runner_applies_higher_then_lower_once_without_changing_applied_rows(self):
        root = Path(__file__).resolve().parents[1]
        with self.psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute('create database wr578_runner template wr578_ledger_template')
        dsn = self.dsn.rsplit('/', 1)[0] + '/wr578_runner'
        with tempfile.TemporaryDirectory(prefix='wr578-runner-') as directory:
            repo = Path(directory)
            (repo / 'tools').mkdir()
            (repo / 'db').mkdir()
            for name in ('migrate.py', 'migration_baseline.py', 'migration_number_contract.py'):
                shutil.copyfile(root / 'tools' / name, repo / 'tools' / name)
            shutil.copyfile(root / 'db/migration-baseline.json', repo / 'db/migration-baseline.json')
            shutil.copytree(root / 'migrations', repo / 'migrations')
            with self.psycopg.connect(dsn) as conn:
                before = conn.execute('select filename,sha256,applied_at from schema_migrations order by filename').fetchall()
            expected = []
            for filename, table in [('9999_wr578_higher.sql', 'wr578_higher'),
                                    ('9998_wr578_lower.sql', 'wr578_lower')]:
                sql = f'create table public.{table}(id integer primary key);\n'
                (repo / 'migrations' / filename).write_text(sql)
                result = subprocess.run([sys.executable, str(repo / 'tools/migrate.py'), '--apply', '--yes'],
                                        env={**self.child, 'DATABASE_URL': dsn}, capture_output=True,
                                        text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(f'applying {filename}', result.stdout)
                expected.append((filename, hashlib.sha256(sql.encode()).hexdigest()))
            with self.psycopg.connect(dsn) as conn:
                self.assertEqual(conn.execute("select filename,sha256 from schema_migrations where filename like '%_wr578_%' order by filename").fetchall(), sorted(expected))
                self.assertEqual(conn.execute("select filename,sha256,applied_at from schema_migrations where filename not like '%_wr578_%' order by filename").fetchall(), before)
                self.assertEqual(conn.execute("select to_regclass('public.wr578_higher')::text,to_regclass('public.wr578_lower')::text").fetchone(), ('wr578_higher', 'wr578_lower'))
                completed = conn.execute('select filename,sha256,applied_at from schema_migrations order by filename').fetchall()
            result = subprocess.run([sys.executable, str(repo / 'tools/migrate.py'), '--apply', '--yes'],
                                    env={**self.child, 'DATABASE_URL': dsn}, capture_output=True,
                                    text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('pending: 0', result.stdout)
            with self.psycopg.connect(dsn) as conn:
                self.assertEqual(conn.execute('select filename,sha256,applied_at from schema_migrations order by filename').fetchall(), completed)

    def test_runner_rolls_back_a_missing_dependency_then_applies_a_before_b(self):
        root = Path(__file__).resolve().parents[1]
        with self.psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute('create database wr578_dependency template wr578_ledger_template')
        dsn = self.dsn.rsplit('/', 1)[0] + '/wr578_dependency'
        with tempfile.TemporaryDirectory(prefix='wr578-dependency-') as directory:
            repo = Path(directory)
            (repo / 'tools').mkdir()
            (repo / 'db').mkdir()
            for name in ('migrate.py', 'migration_baseline.py', 'migration_number_contract.py'):
                shutil.copyfile(root / 'tools' / name, repo / 'tools' / name)
            shutil.copyfile(root / 'db/migration-baseline.json', repo / 'db/migration-baseline.json')
            shutil.copytree(root / 'migrations', repo / 'migrations')
            a = '9998_wr578_dependency_a.sql'
            b = '9999_wr578_dependency_b.sql'
            (repo / 'migrations' / b).write_text(
                'create table public.wr578_rollback_probe(id integer);'
                'alter table public.wr578_dependency add column changed integer;')
            command = [sys.executable, str(repo / 'tools/migrate.py'), '--apply', '--yes']
            with self.psycopg.connect(dsn) as conn:
                before = conn.execute('select filename,sha256,applied_at from schema_migrations order by filename').fetchall()
            failed = subprocess.run(command, env={**self.child, 'DATABASE_URL': dsn},
                capture_output=True, text=True, timeout=60)
            self.assertNotEqual(failed.returncode, 0, failed.stdout + failed.stderr)
            self.assertIn('UndefinedTable', failed.stderr)
            with self.psycopg.connect(dsn) as conn:
                self.assertEqual(conn.execute('select filename,sha256,applied_at from schema_migrations order by filename').fetchall(), before)
                self.assertIsNone(conn.execute("select to_regclass('public.wr578_rollback_probe')").fetchone()[0])
            (repo / 'migrations' / a).write_text('create table public.wr578_dependency(id integer);')
            passed = subprocess.run(command, env={**self.child, 'DATABASE_URL': dsn},
                capture_output=True, text=True, timeout=60)
            self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
            self.assertLess(passed.stdout.index(f'applying {a}'), passed.stdout.index(f'applying {b}'))
            with self.psycopg.connect(dsn) as conn:
                self.assertEqual(conn.execute("select filename from schema_migrations where filename in (%s,%s) order by filename", (a,b)).fetchall(), [(a,), (b,)])
                self.assertEqual(conn.execute('select changed from public.wr578_dependency').fetchall(), [])


if __name__ == '__main__':
    unittest.main()
