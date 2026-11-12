"""Tests the engine reader role contract and resumable password decisions."""
import importlib.util
import pathlib
import os
import subprocess
import sys
import tempfile
import io
from contextlib import redirect_stdout
from unittest.mock import patch
import unittest

PATH = pathlib.Path(__file__).with_name('engine-db-role.py')
spec = importlib.util.spec_from_file_location('engine_db_role', PATH)
assert spec is not None and spec.loader is not None
role = importlib.util.module_from_spec(spec)
spec.loader.exec_module(role)


class ReaderRoleTests(unittest.TestCase):
    def test_cli_removes_staging_and_retains_named_branch(self):
        help_run = subprocess.run([sys.executable, str(PATH), '--help'], capture_output=True, text=True)
        self.assertEqual(help_run.returncode, 0)
        self.assertIn('--branch', help_run.stdout)
        self.assertNotIn('staging', help_run.stdout)
        staging = subprocess.run([sys.executable, str(PATH), '--environment', 'staging'],
                                 capture_output=True, text=True)
        self.assertEqual(staging.returncode, 2)

    def test_new_role_sets_generated_password(self):
        self.assertTrue(role.password_change(exists=False, rotate=False, authenticated=False))

    def test_rerun_preserves_password(self):
        self.assertFalse(role.password_change(exists=True, rotate=False, authenticated=True))

    def test_committed_pending_recovers_without_password_reset(self):
        self.assertFalse(role.password_change(exists=True, rotate=False, authenticated=True))

    def test_missing_or_wrong_existing_secret_refuses_without_rotation(self):
        with self.assertRaises(role.Refusal):
            role.password_change(exists=True, rotate=False, authenticated=False)

    def test_explicit_rotation_sets_password(self):
        self.assertTrue(role.password_change(exists=True, rotate=True, authenticated=False))

    def test_managed_owner_does_not_reassert_false_superuser_flags(self):
        self.assertEqual(role.attribute_options((False, False, False)),
                         'LOGIN NOCREATEDB NOCREATEROLE NOINHERIT')
        self.assertIn('NOSUPERUSER', role.attribute_options((True, False, False)))
        self.assertIn('NOREPLICATION', role.attribute_options((False, True, False)))
        self.assertIn('NOBYPASSRLS', role.attribute_options((False, False, True)))

    def test_role_spec(self):
        data = role.load_spec()
        self.assertEqual(data['role'], 'engine_business_reader')
        self.assertEqual(len(data['tables']), 70)
        self.assertIn('activity', data['tables'])
        self.assertIn('v_deal_room_current_lease', data['views'])
        self.assertEqual(data['settings']['timezone'], 'UTC')
        self.assertEqual(len(set(data['tables'] + data['views'])), 98)

    def test_spec_rejects_role_or_identifier_expansion(self):
        data = role.load_spec()
        data['role'] = 'app_writer'
        with self.assertRaises(role.Refusal):
            role.validate_spec(data)
        data = role.load_spec()
        data['tables'].append('ops.work_request')
        with self.assertRaises(role.Refusal):
            role.validate_spec(data)

    def test_grants_are_whole_table_select_only(self):
        statements = role.grant_statements(role.load_spec())
        text = '\n'.join(statements)
        self.assertIn('GRANT SELECT ON TABLE "public"."activity"', text)
        self.assertIn('GRANT SELECT ON TABLE "public"."v_deal_room_presence" TO "engine_business_reader"', statements)
        self.assertIn('GRANT SELECT ON TABLE "public"."v_capture_session_status" TO "engine_business_reader"', statements)
        self.assertIn('GRANT EXECUTE ON FUNCTION public.carr_business_date(timestamp with time zone)', text)
        self.assertEqual(len([s for s in statements if 'FUNCTION' in s]), 1)
        self.assertNotIn('SEQUENCE', text)
        self.assertNotIn('DEFAULT PRIVILEGES', text)
        self.assertNotIn('INSERT', text)

    def test_spec_rejects_function_expansion(self):
        for functions in [[], ['extra_fn()'], ['carr_business_date(timestamp with time zone)', 'extra_fn()']]:
            data = role.load_spec()
            data['functions'] = functions
            with self.assertRaises(role.Refusal):
                role.validate_spec(data)


@unittest.skipUnless(os.environ.get('ENGINE_ROLE_TEST_DSN'), 'canonical PostgreSQL fixture not requested')
class ReaderRolePostgresTests(unittest.TestCase):
    def test_create_converge_crash_recover_rotate_and_denials(self):
        owner = os.environ['ENGINE_ROLE_TEST_DSN']
        data = role.load_spec()
        with role.connect(owner) as conn:
            conn.execute('CREATE SCHEMA ops')
            conn.execute('CREATE SCHEMA neon_auth')
            conn.execute('CREATE TABLE ops.client_portal(id int)')
            for name in data['tables']:
                conn.execute(role.sql.SQL('CREATE TABLE public.{}(id int)').format(role.sql.Identifier(name)))
            conn.execute('CREATE FUNCTION public.carr_business_date(timestamp with time zone) '
                         'RETURNS date LANGUAGE sql STABLE STRICT SET search_path=pg_catalog '
                         "AS $$ SELECT ($1 AT TIME ZONE 'America/Chicago')::date $$")
            conn.execute('REVOKE ALL ON FUNCTION public.carr_business_date(timestamp with time zone) FROM PUBLIC')
            for name in data['views']:
                conn.execute(role.sql.SQL('CREATE VIEW public.{} AS SELECT 1 AS id').format(role.sql.Identifier(name)))
            conn.execute('CREATE OR REPLACE VIEW public.v_open_deal_room_board AS '
                         'SELECT extract(day FROM public.carr_business_date(current_timestamp))::int AS id')
            conn.execute('CREATE TABLE public.extra(id int)')
            conn.execute('CREATE ROLE extra_bundle')
            conn.execute('CREATE FUNCTION public.extra_fn() RETURNS integer LANGUAGE sql AS $$ SELECT 1 $$')
            conn.execute('REVOKE ALL ON FUNCTION public.extra_fn() FROM PUBLIC')
        # The production helper requires a neondb_owner URI and TLS. This local
        # fixture deliberately uses trust authentication; only its URI builder varies.
        def fixture_uri(uri, name, password):
            return uri.replace('fixture@', name + ':' + password + '@')
        with tempfile.TemporaryDirectory() as directory, patch.object(
            role.credentials, 'build_role_uri', side_effect=fixture_uri
        ), patch.object(role.credentials, 'validate_uri', return_value=('synthetic-password', '127.0.0.1', 5432, 'postgres')):
            root = pathlib.Path(directory)
            first = role.provision(owner, data, 'engine-test', False, root)
            stream = io.StringIO()
            with redirect_stdout(stream):
                role.print_result(first)
            output = stream.getvalue()
            self.assertNotIn('postgresql://', output)
            self.assertNotIn('synthetic-password', output)
            self.assertTrue(first['created'])
            self.assertTrue(first['password_changed'])
            self.assertTrue(first['proof']['timezone_utc'])
            self.assertTrue(first['proof']['statement_timeout_15s'])
            self.assertTrue(first['proof']['idle_timeout_30s'])
            for key in ['ops_read_refused', 'insert_refused', 'update_refused', 'delete_refused']:
                self.assertTrue(first['proof'][key], key)
            self.assertTrue(first['proof']['function_grants_match'])
            self.assertTrue(first['proof']['v_open_deal_room_board']['read'])
            self.assertTrue(first['proof']['v_deal_room_presence']['read'])
            self.assertTrue(first['proof']['v_capture_session_status']['read'])
            final = root / 'engine-test-engine-business-reader.env'
            original = final.read_bytes()
            secret = original.decode().split('=', 1)[1].strip()
            from urllib.parse import unquote, urlsplit
            generated_password = unquote(urlsplit(secret).password)
            self.assertNotIn(generated_password, output)
            self.assertNotIn(secret, output)
            with role.connect(owner) as conn:
                conn.execute('GRANT extra_bundle TO engine_business_reader')
                conn.execute('GRANT EXECUTE ON FUNCTION public.extra_fn() TO engine_business_reader')
                conn.execute('GRANT SELECT,INSERT ON public.extra TO engine_business_reader')
                conn.execute('GRANT UPDATE(id) ON public.activity TO engine_business_reader')
                conn.execute('GRANT USAGE ON SCHEMA ops TO engine_business_reader')
                conn.execute('ALTER DEFAULT PRIVILEGES GRANT SELECT ON TABLES TO engine_business_reader')
            second = role.provision(owner, data, 'engine-test', False, root)
            self.assertTrue(second['proof']['function_grants_match'])
            self.assertTrue(second['proof']['v_open_deal_room_board']['read'])
            self.assertTrue(second['proof']['v_deal_room_presence']['read'])
            self.assertTrue(second['proof']['v_capture_session_status']['read'])
            self.assertFalse(second['created'])
            self.assertFalse(second['password_changed'])
            self.assertEqual(final.read_bytes(), original)
            with role.connect(owner) as conn:
                self.assertFalse(conn.execute("SELECT has_table_privilege('engine_business_reader','public.extra','SELECT')").fetchone()[0])
                self.assertFalse(conn.execute("SELECT has_column_privilege('engine_business_reader','public.activity','id','UPDATE')").fetchone()[0])
                self.assertFalse(conn.execute("SELECT EXISTS(SELECT FROM pg_default_acl d,aclexplode(d.defaclacl) a WHERE a.grantee='engine_business_reader'::regrole)").fetchone()[0])
            with role.connect(owner) as conn:
                conn.execute('CREATE ROLE role_manager CREATEDB CREATEROLE')
                conn.execute('GRANT fixture TO role_manager')
                conn.execute('GRANT engine_business_reader TO role_manager WITH ADMIN OPTION')
                conn.execute('SET ROLE role_manager')
                role.reconcile(conn, data)
            final.rename(str(final) + '.pending')
            recovered = role.provision(owner, data, 'engine-test', False, root)
            self.assertFalse(recovered['password_changed'])
            self.assertTrue(final.exists())
            rotated = role.provision(owner, data, 'engine-test', True, root)
            self.assertTrue(rotated['password_changed'])
            self.assertNotEqual(final.read_bytes(), original)
            with patch.object(role, 'authenticated', return_value=False):
                with self.assertRaises(role.Refusal):
                    role.provision(owner, data, 'engine-test', False, root)
            final.unlink()
            with self.assertRaises(role.Refusal):
                role.provision(owner, data, 'engine-test', False, root)
            self.assertTrue(role.provision(owner, data, 'engine-test', True, root)['password_changed'])


    def test_database_overrides_converge_without_changing_other_roles(self):
        owner = os.environ['ENGINE_ROLE_TEST_DSN']
        database = 'engine settings "fixture'
        data = role.load_spec() | {'tables': ['activity'], 'views': []}
        with role.connect(owner) as conn:
            conn.autocommit = True
            conn.execute(role.sql.SQL('CREATE DATABASE {}').format(role.sql.Identifier(database)))
        from urllib.parse import quote, urlsplit, urlunsplit
        target = urlunsplit(urlsplit(owner)._replace(path='/' + quote(database)))
        settings_query = """SELECT setdatabase, setconfig FROM pg_db_role_setting
            WHERE setrole=%s::regrole ORDER BY setdatabase"""
        try:
            with role.connect(target) as conn:
                conn.execute('CREATE TABLE public.activity(id int)')
                conn.execute('CREATE FUNCTION public.carr_business_date(timestamp with time zone) '
                             'RETURNS date LANGUAGE sql AS $$ SELECT $1::date $$')
                role.reconcile(conn, data)
                conn.execute('CREATE ROLE settings_bystander')
                for db in [database, 'postgres']:
                    for key, value in {
                        'default_transaction_read_only': 'off', 'timezone': 'America/New_York',
                        'statement_timeout': '2s', 'idle_in_transaction_session_timeout': '3s',
                        'search_path': 'pg_catalog', 'application_name': 'unexpected override',
                    }.items():
                        conn.execute(role.sql.SQL('ALTER ROLE {} IN DATABASE {} SET {} = {}').format(
                            role.sql.Identifier(role.ROLE), role.sql.Identifier(db),
                            role.sql.Identifier(key), role.sql.Literal(value)))
                conn.execute(role.sql.SQL('ALTER ROLE settings_bystander IN DATABASE {} '
                                         "SET timezone = 'America/New_York'").format(
                                             role.sql.Identifier(database)))
                conn.execute("ALTER ROLE settings_bystander SET statement_timeout = '4s'")
                before = conn.execute(settings_query, ('settings_bystander',)).fetchall()
            reader = target.replace('fixture@', role.ROLE + '@')
            for attempt in range(2):
                with role.connect(target) as conn:
                    role.reconcile(conn, data)
                    observed = conn.execute(settings_query, (role.ROLE,)).fetchall()
                    self.assertEqual(conn.execute(settings_query, ('settings_bystander',)).fetchall(), before)
                with role.connect(reader) as conn:
                    for key, value in data['settings'].items():
                        with self.subTest(attempt=attempt, setting=key):
                            self.assertEqual(conn.execute(role.sql.SQL('SHOW {}').format(
                                role.sql.Identifier(key))).fetchone()[0], value)
                self.assertEqual(len(observed), 1)
                self.assertEqual(observed[0][0], 0)
                self.assertEqual({key.lower() + '=' + value for key, value in
                                  (setting.split('=', 1) for setting in observed[0][1])},
                                 {key + '=' + value for key, value in data['settings'].items()})
        finally:
            with role.connect(owner) as conn:
                conn.autocommit = True
                conn.execute(role.sql.SQL('DROP DATABASE {}').format(role.sql.Identifier(database)))
                conn.execute('DROP ROLE IF EXISTS settings_bystander')


if __name__ == '__main__':
    unittest.main()
