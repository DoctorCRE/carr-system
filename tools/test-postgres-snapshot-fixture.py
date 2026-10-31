#!/usr/bin/env python3
"""Snapshot copies preserve seed state without sharing roles, grants, or data."""
import sys
from pathlib import Path
import unittest
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.disposable_pg_fixture import DisposablePostgres, postgres_bin_dir, postgres_fixture_group
from lib.postgres_snapshot_fixture import start_snapshot, snapshot_key


class SnapshotIsolation(unittest.TestCase):
    def test_key_covers_schema_and_migration_contents_and_inventory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'db').mkdir()
            (root / 'migrations').mkdir()
            schema = root / 'db/schema.sql'
            schema.write_text('select 1;')
            original = snapshot_key(schema)
            original_baseline = snapshot_key(schema, baseline=True)
            migration = root / 'migrations/9999_probe.sql'
            migration.write_text('select 2;')
            added = snapshot_key(schema)
            self.assertNotEqual(original, added)
            self.assertNotEqual(original_baseline, snapshot_key(schema, baseline=True))
            migration.write_text('select 3;')
            self.assertNotEqual(added, snapshot_key(schema))
            migration.unlink()
            self.assertEqual(original, snapshot_key(schema))
            schema.write_text('select 4;')
            self.assertNotEqual(original, snapshot_key(schema))
            self.assertNotEqual(snapshot_key(schema), snapshot_key(schema, baseline=True))

    def test_copies_have_independent_roles_memberships_grants_and_data(self):
        binaries = postgres_bin_dir()
        with postgres_fixture_group(), DisposablePostgres('snapshot-proof-', binaries / 'pg_ctl') as template:
            schema = template.root / 'seed.sql'
            schema.write_text('create role snapshot_reader; create role snapshot_group; '
                              'create table seeded(value text); insert into seeded values (\'original\'); '
                              'grant select on seeded to snapshot_reader;')
            start_snapshot(template, binaries, schema, template=True)
            source = template.root / 'data'
            schema.write_text('select 1 / 0;')  # Copies must reuse the prepared snapshot, never replay SQL.
            for changed in (True, False):
                with DisposablePostgres('snapshot-copy-', binaries / 'pg_ctl') as copy:
                    start_snapshot(copy, binaries, schema, source)
                    def query(sql):
                        return copy.run([binaries / 'psql', '-h', copy.root, '-U', 'carr_ci',
                                         '-d', 'carr_ci', '-XAt', '-v', 'ON_ERROR_STOP=1', '-c', sql],
                                        check=True, capture_output=True, text=True).stdout.strip()
                    self.assertEqual(query('select value from seeded'), 'original')
                    self.assertEqual(query("select rolcanlogin,rolcreatedb,rolsuper,rolinherit,rolreplication,"
                                           "rolbypassrls,rolconnlimit,rolconfig is null from pg_roles "
                                           "where rolname='snapshot_reader'"), 'f|f|f|t|f|f|-1|t')
                    self.assertEqual(query("select pg_has_role('snapshot_reader','snapshot_group','member')"), 'f')
                    self.assertEqual(query("select has_table_privilege('snapshot_reader','seeded','select')"), 't')
                    self.assertEqual(query("select has_database_privilege('snapshot_reader','carr_ci','create')"), 'f')
                    if changed:
                        query("alter role snapshot_reader login createdb superuser noinherit replication bypassrls connection limit 3; "
                              "alter role snapshot_reader set statement_timeout='9s'; grant snapshot_group to snapshot_reader; "
                              "revoke select on seeded from snapshot_reader; grant create on database carr_ci to snapshot_reader; "
                              "update seeded set value='changed'")
                    with self.assertRaisesRegex(ValueError, 'must be stopped'):
                        start_snapshot(template, binaries, schema, copy.root / 'data')
            self.assertFalse((source / 'postmaster.pid').exists())


if __name__ == '__main__':
    unittest.main()
