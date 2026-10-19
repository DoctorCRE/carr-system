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


if __name__ == '__main__':
    unittest.main()
