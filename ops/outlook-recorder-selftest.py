#!/usr/bin/env python3
"""Prove the intake registry seed on isolated PostgreSQL, without registry sync."""
import json
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.disposable_pg_fixture import DisposablePostgres, postgres_bin_dir
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from migration_source import read_migration

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / 'migrations/1152_outlook_intake_service.sql'


class IntakeRegistry(unittest.TestCase):
    def test_seed_restores_declared_service_and_cadence_idempotently(self):
        import psycopg

        migration = MIGRATION.read_text()
        bins = postgres_bin_dir()
        with DisposablePostgres('intake-registry-', bins / 'pg_ctl') as fixture:
            data = fixture.root / 'data'
            fixture.run([bins / 'initdb', '-D', data, '-A', 'trust'],
                        check=True, capture_output=True)
            fixture.run([bins / 'pg_ctl', '-D', data, '-l', fixture.root / 'pg.log',
                         '-o', f"-h '' -k {fixture.root}", '-w', 'start'],
                        check=True, capture_output=True)
            with psycopg.connect(host=str(fixture.root), dbname='postgres',
                                 autocommit=True) as conn:
                conn.execute('create schema ops')
                base = read_migration(ROOT, '0115_ops_observability.sql')
                for table in ('service', 'service_environment'):
                    ddl = re.search(r'create table if not exists ops\.' + table
                                    + r' \(.*?\n\);', base, re.S)
                    assert ddl
                    conn.execute(ddl.group())
                self.assertIsNone(conn.execute(
                    "select id from ops.service where key='outlook-intake'").fetchone())
                conn.execute(migration)
                seeded = conn.execute(
                    "select id from ops.service where key='outlook-intake'").fetchone()
                self.assertIsNotNone(seeded, "deployment must register the recorder service")
                first_id = seeded[0]
                conn.execute("update ops.service set retired_at=now()")
                conn.execute("update ops.service_environment set expected_cadence_seconds=1")
                conn.execute(migration)
                spec = next(s for s in json.loads((ROOT / 'ops/config/services.json').read_text())
                            ['services'] if s['key'] == 'outlook-intake')
                fields = ('name', 'purpose', 'family', 'criticality', 'owner_actor',
                          'repo_path', 'runtime')
                actual = conn.execute('select id, retired_at, ' + ','.join(fields)
                                      + " from ops.service where key='outlook-intake'").fetchone()
                self.assertEqual(actual, (first_id, None, *(spec[f] for f in fields)))
                env = spec['environments'][0]
                fields = ('environment', 'deploy_mechanism', 'expected_cadence_seconds',
                          'cadence_grace_seconds', 'notes')
                actual = conn.execute('select ' + ','.join(fields)
                                      + ' from ops.service_environment where service_id=%s',
                                      (first_id,)).fetchall()
                self.assertEqual(actual, [tuple(env[f] for f in fields)])


if __name__ == '__main__':
    unittest.main()
