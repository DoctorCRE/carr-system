#!/usr/bin/env python3
"""Rebuild the checked-in snapshot through the exact source migration prefix."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tools')]
from lib.disposable_pg_fixture import DisposablePostgres, postgres_fixture_group
from migration_baseline import MigrationBaseline, repository_ledger

POSTGRES = '--postgres' in sys.argv
if POSTGRES:
    sys.argv.remove('--postgres')


class ReconstructionTests(unittest.TestCase):
    @unittest.skipUnless(POSTGRES, 'database fixture runs in the migration lane')
    def test_snapshot_and_pending_migrations_reach_exact_source_ledger(self):
        import psycopg
        configured = os.environ.get('CARR_TEST_POSTGRES_BIN')
        initdb = shutil.which('initdb')
        if initdb and not (Path(initdb).parent / 'postgres').is_file():
            initdb = None
        bins = Path(configured) if configured else Path(initdb).parent if initdb else Path('/opt/homebrew/opt/postgresql@17/bin')
        self.assertTrue((bins / 'initdb').is_file(), 'PostgreSQL server binaries required')
        expected = repository_ledger(ROOT, MigrationBaseline.load(ROOT))
        highest = next(reversed(expected))
        with postgres_fixture_group(), DisposablePostgres('carr-reconstruct-', bins / 'pg_ctl') as fixture:
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                port = listener.getsockname()[1]
            data = fixture.root / 'data'
            for command in (
                [bins / 'initdb', '-D', data, '-U', 'neondb_owner', '-A', 'trust', '--no-locale', '-E', 'UTF8'],
                [bins / 'pg_ctl', '-D', data, '-l', fixture.root / 'postgres.log',
                 '-o', f'-h 127.0.0.1 -p {port} -k {fixture.root}', '-w', 'start'],
            ):
                result = fixture.run(command, capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)
            dsn = f'postgresql://neondb_owner@127.0.0.1:{port}/postgres'
            loaded = subprocess.run([str(bins / 'psql'), '-X', '--single-transaction',
                '-v', 'ON_ERROR_STOP=1', '-q', '-d', dsn, '-f', str(ROOT / 'db/schema.sql')],
                capture_output=True, text=True, timeout=180)
            self.assertEqual(loaded.returncode, 0, loaded.stderr)
            migrated = subprocess.run([sys.executable, str(ROOT / 'tools/migrate.py'),
                '--apply', '--yes', '--through', highest], cwd=ROOT,
                env={**os.environ, 'DATABASE_URL': dsn}, capture_output=True, text=True, timeout=600)
            self.assertEqual(migrated.returncode, 0, migrated.stdout + migrated.stderr)
            with psycopg.connect(dsn) as conn:
                actual = dict(conn.execute('select filename, sha256 from schema_migrations').fetchall())
            self.assertEqual(actual, expected)

            # Replay only the correction: later DDL is already in this snapshot.
            correction = '0897_national_account_classification_correction.sql'
            correction_ledger = {name: digest for name, digest in expected.items() if name <= correction}
            with psycopg.connect(dsn) as conn:
                conn.execute("""insert into party (id,kind,name,created_by,updated_by)
                    select '00000000-0000-0000-0000-000000000011','org',
                        'Synthetic reconstruction account',id,id from actor where slug='system'""")
                conn.execute("""insert into client (id,roster_ref,party_id,client_type,created_by,updated_by)
                    select '00000000-0000-0000-0000-000000000012','SYNTHETIC-ACCOUNT',
                        '00000000-0000-0000-0000-000000000011','national_account',id,id
                    from actor where slug='system'""")
                conn.execute("delete from schema_migrations where filename >= %s",
                    (correction,))
            resumed = subprocess.run([sys.executable, str(ROOT / 'tools/migrate.py'),
                '--apply', '--yes', '--through', correction], cwd=ROOT,
                env={**os.environ, 'DATABASE_URL': dsn}, capture_output=True, text=True, timeout=600)
            self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
            with psycopg.connect(dsn) as conn:
                self.assertEqual(dict(conn.execute(
                    'select filename, sha256 from schema_migrations').fetchall()), correction_ledger)
                self.assertEqual(conn.execute("select roster_ref,client_type from client").fetchall(),
                    [('SYNTHETIC-ACCOUNT', 'national_account')])

            historical_ids = ('9c323aa5-b9c0-45e7-b958-ef1ee3738660',
                '8550b702-e6fb-4bea-8b3e-1fec5b9499bd',
                '2f7364a7-8167-421d-8492-79815307fba3')
            for roster_ref, client_id, client_type, merged in [
                (ref, '00000000-0000-0000-0000-000000000012', None, True)
                for ref in ('C-161', 'C-204', 'C-205')
            ] + [('SYNTHETIC-ACCOUNT', identity, 'national_account', False) for identity in historical_ids]:
                with self.subTest(roster_ref=roster_ref, client_id=client_id):
                    with psycopg.connect(dsn) as conn:
                        conn.execute("delete from schema_migrations where filename >= %s",
                            (correction,))
                        conn.execute('update client set id=%s,roster_ref=%s,client_type=%s,merged_into=%s',
                            (client_id, roster_ref, client_type, client_id if merged else None))
                    refused = subprocess.run([sys.executable, str(ROOT / 'tools/migrate.py'),
                        '--apply', '--yes', '--through', correction], cwd=ROOT,
                        env={**os.environ, 'DATABASE_URL': dsn}, capture_output=True, text=True, timeout=600)
                    self.assertNotEqual(refused.returncode, 0)
                    self.assertIn('national-account correction: live account census changed', refused.stderr)
                    with psycopg.connect(dsn) as conn:
                        self.assertEqual(conn.execute("select count(*) from schema_migrations where filename=%s",
                            (correction,)).fetchone()[0], 0)
                        self.assertEqual(conn.execute("select count(*) from event where "
                            "verb='correct-national-account-classification'").fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
