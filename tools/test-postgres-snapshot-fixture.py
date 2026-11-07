#!/usr/bin/env python3
"""Snapshot copies preserve seed state without sharing roles, grants, or data."""
import sys
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import tempfile
import json
import os
import subprocess

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.disposable_pg_fixture import DisposablePostgres, postgres_bin_dir, postgres_fixture_group
from lib.postgres_snapshot_fixture import start_snapshot, snapshot_key
from lib.postgres_service_fixture import service_database


class SnapshotIsolation(unittest.TestCase):
    def test_service_transport_uses_one_validated_endpoint(self):
        import lib.postgres_service_fixture as module
        for host, hosted in [('127.0.0.1', False), ('localhost', False), ('postgres', True)]:
            admin = Mock()
            admin.info.port, admin.info.user = 5432, 'carr_ci'
            admin.__enter__ = Mock(return_value=admin)
            admin.__exit__ = Mock(return_value=False)
            with patch.dict(os.environ, GITHUB_ACTIONS='true' if hosted else '',
                            CARR_CI_POSTGRES_IMAGE='both', PGHOSTADDR='203.0.113.1',
                            PGSERVICE='', PGSERVICEFILE='', PGPASSWORD='postgres'), \
                 patch('socket.getaddrinfo', return_value=[(2, 1, 6, '', ('172.18.0.2', 5432))]), \
                 patch.object(module.psycopg, 'connect', return_value=admin) as connect, \
                 patch.object(module, 'role_state', return_value=({}, set())), \
                 patch.object(module, 'restore_roles'), \
                 patch.object(module.subprocess, 'run', return_value=Mock(returncode=0)) as run:
                with service_database(f'postgres://carr_ci@{host}:5432/carr_ci', 'probe-', schema='seed.sql') as metadata:
                    self.assertIn(f'@{host}:5432/probe_', metadata['dsn'])
                    self.assertIsNone(module.urlsplit(metadata['dsn']).password)
                    restore = run.call_args.kwargs['env']
                    address = '172.18.0.2' if hosted else '127.0.0.1'
                    self.assertEqual(connect.call_args.kwargs['hostaddr'], address)
                    self.assertEqual(restore['PGHOSTADDR'], address)
                    self.assertEqual(restore['PGHOST'], host)
                    self.assertEqual(restore['PGDATABASE'], metadata['dsn'].rsplit('/', 1)[1])
                    self.assertEqual(restore['PGPASSWORD'], 'postgres')
                    self.assertNotIn('PGSERVICE', restore)
                    self.assertNotIn('PGSERVICEFILE', restore)

    def test_snapshot_migrations_cannot_inherit_transport_overrides(self):
        from lib.postgres_snapshot_fixture import migrate_snapshot
        with patch.dict(os.environ, PGHOSTADDR='203.0.113.1', PGSERVICE='production',
                        PGSERVICEFILE='/untrusted', PGPASSWORD='postgres'), \
             patch('lib.postgres_snapshot_fixture.subprocess.run', return_value=Mock(returncode=0)) as run:
            migrate_snapshot(ROOT / 'db/schema.sql', 'postgres://carr_ci@postgres:5432/owned')
            env = run.call_args.kwargs['env']
            self.assertEqual(env['DATABASE_URL'], 'postgres://carr_ci@postgres:5432/owned')
            self.assertEqual(env['PGPASSWORD'], 'postgres')
            self.assertFalse({'PGHOSTADDR', 'PGSERVICE', 'PGSERVICEFILE'} & env.keys())

    def test_restore_failure_drops_owned_database_and_restores_roles(self):
        import lib.postgres_service_fixture as module
        admin = Mock()
        admin.info.port, admin.info.user = 5432, 'carr_ci'
        admin.__enter__ = Mock(return_value=admin)
        admin.__exit__ = Mock(return_value=False)
        before = ({}, set())
        with patch.dict(os.environ, PGSERVICE='', PGSERVICEFILE=''), \
             patch.object(module.psycopg, 'connect', return_value=admin), \
             patch.object(module, 'role_state', return_value=before), \
             patch.object(module, 'restore_roles') as restore, \
             patch.object(module.subprocess, 'run', return_value=Mock(returncode=1, stderr='restore refused')):
            with self.assertRaisesRegex(RuntimeError, 'restore refused'):
                with service_database('postgres://carr_ci@127.0.0.1:5432/carr_ci', 'probe-', schema='seed.sql'):
                    self.fail('failed restore yielded a database')
            statements = [call.args[0].as_string() if hasattr(call.args[0], 'as_string') else call.args[0]
                          for call in admin.execute.call_args_list]
            self.assertTrue(any(statement.startswith('drop database "probe_') and statement.endswith('with (force)')
                                for statement in statements))
            restore.assert_called_once_with(admin, before)

    def test_ambient_libpq_service_refused_before_connect(self):
        import lib.postgres_service_fixture as module
        for key in ('PGSERVICE', 'PGSERVICEFILE'):
            with patch.dict(os.environ, {key: 'untrusted'}), patch.object(module.psycopg, 'connect') as connect:
                with self.assertRaisesRegex(ValueError, 'ambient libpq'):
                    with service_database('postgres://carr_ci@127.0.0.1:5432/carr_ci', 'probe-'):
                        self.fail('ambient service accepted')
                connect.assert_not_called()

    def test_service_endpoint_refuses_unsafe_urls_before_network(self):
        import lib.postgres_service_fixture as module
        for hosted in (False, True):
            urls = ['postgres://carr_ci@remote:5432/carr_ci',
                    'postgres://carr_ci@postgres:5432/carr_ci?hostaddr=127.0.0.1',
                    'postgres://carr_ci@postgres:5432/carr_ci#override',
                    'http://carr_ci@postgres:5432/carr_ci',
                    'postgres://other@postgres:5432/carr_ci',
                    'postgres://carr_ci:pw@postgres:5432/carr_ci',
                    'postgres://carr_ci@postgres:5433/carr_ci',
                    'postgres://carr_ci@postgres:5432/production']
            if not hosted:
                urls.append('postgres://carr_ci@postgres:5432/carr_ci')
            with patch.dict(os.environ, GITHUB_ACTIONS='true' if hosted else '', CARR_CI_POSTGRES_IMAGE='both'), \
                 patch.object(module.psycopg, 'connect') as connect:
                for url in urls:
                    with self.subTest(url=url, hosted=hosted), self.assertRaises(ValueError):
                        with service_database(url, 'probe-'):
                            self.fail('unsafe URL accepted')
                connect.assert_not_called()

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

    def test_supervised_snapshot_connects_and_cleans_up_under_long_root(self):
        binaries = postgres_bin_dir()
        with postgres_fixture_group(), tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / ('long-ci-root-' * 10)
            parent.mkdir()
            environment = {key: value for key, value in os.environ.items()
                           if key != 'CARR_CI_DATABASE_URL'}
            command = [sys.executable, '-u', str(ROOT / 'lib/disposable_pg_fixture.py'),
                       '--supervise', '--snapshot', '--snapshot-baseline',
                       '--directory', str(parent), '--prefix', 'snapshot-long-']
            with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, env=environment) as process:
                try:
                    line = process.stdout.readline()
                    if not line:
                        self.fail(process.stderr.read())
                    metadata = json.loads(line)
                    result = subprocess.run([binaries / 'psql', metadata['dsn'], '-XAt',
                                             '-c', 'select 42'], check=True,
                                            capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.stdout.strip(), '42')
                    socket = Path(metadata['connection']['host'])
                    self.assertGreater(len(str(Path(metadata['root']) / '.s.PGSQL.5432').encode()), 107)
                    self.assertTrue((socket / '.s.PGSQL.5432').exists())
                finally:
                    process.stdin.close()
                    process.wait(timeout=60)
                self.assertEqual(process.returncode, 0, process.stderr.read())
                self.assertFalse(Path(metadata['root']).exists())
                self.assertFalse(socket.exists())

    def test_copies_have_independent_roles_memberships_grants_and_data(self):
        binaries = postgres_bin_dir()
        with postgres_fixture_group(), tempfile.TemporaryDirectory(prefix='pg-', dir='/tmp') as sockets, \
                DisposablePostgres('snapshot-proof-', binaries / 'pg_ctl') as template:
            schema = template.root / 'seed.sql'
            schema.write_text('create role snapshot_reader; create role snapshot_group; '
                              'create table seeded(value text); insert into seeded values (\'original\'); '
                              'grant select on seeded to snapshot_reader;')
            start_snapshot(template, binaries, schema, socket_directory=sockets, template=True)
            source = template.root / 'data'
            schema.write_text('select 1 / 0;')  # Copies must reuse the prepared snapshot, never replay SQL.
            for changed in (True, False):
                with DisposablePostgres('snapshot-copy-', binaries / 'pg_ctl') as copy:
                    start_snapshot(copy, binaries, schema, source, socket_directory=sockets)
                    def query(sql):
                        return copy.run([binaries / 'psql', '-h', sockets, '-U', 'carr_ci',
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
                        start_snapshot(template, binaries, schema, copy.root / 'data', socket_directory=sockets)
            self.assertFalse((source / 'postmaster.pid').exists())


if __name__ == '__main__':
    unittest.main()
