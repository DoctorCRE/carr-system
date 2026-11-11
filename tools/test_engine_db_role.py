"""Tests the engine reader role contract and resumable password decisions."""
import importlib.util
import pathlib
import os
import tempfile
from unittest.mock import patch
import unittest

PATH = pathlib.Path(__file__).with_name('engine-db-role.py')
spec = importlib.util.spec_from_file_location('engine_db_role', PATH)
assert spec is not None and spec.loader is not None
role = importlib.util.module_from_spec(spec)
spec.loader.exec_module(role)


class ReaderRoleTests(unittest.TestCase):
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
        self.assertEqual(len(set(data['tables'] + data['views'])), 96)

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
        self.assertNotIn('FUNCTION', text)
        self.assertNotIn('SEQUENCE', text)
        self.assertNotIn('DEFAULT PRIVILEGES', text)
        self.assertNotIn('INSERT', text)


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
            for name in data['views']:
                conn.execute(role.sql.SQL('CREATE VIEW public.{} AS SELECT 1 AS id').format(role.sql.Identifier(name)))
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
            first = role.provision(owner, data, 'staging', False, root)
            self.assertTrue(first['created'])
            self.assertTrue(first['password_changed'])
            self.assertEqual(first['proof']['TimeZone'], 'UTC')
            self.assertEqual(first['proof']['statement_timeout'], '15s')
            self.assertEqual(first['proof']['idle_in_transaction_session_timeout'], '30s')
            for key in ['ops_read_refused', 'insert_refused', 'update_refused', 'delete_refused']:
                self.assertTrue(first['proof'][key], key)
            final = root / 'staging-engine-business-reader.env'
            original = final.read_bytes()
            with role.connect(owner) as conn:
                conn.execute('GRANT extra_bundle TO engine_business_reader')
                conn.execute('GRANT EXECUTE ON FUNCTION public.extra_fn() TO engine_business_reader')
                conn.execute('GRANT SELECT,INSERT ON public.extra TO engine_business_reader')
                conn.execute('GRANT UPDATE(id) ON public.activity TO engine_business_reader')
                conn.execute('GRANT USAGE ON SCHEMA ops TO engine_business_reader')
                conn.execute('ALTER DEFAULT PRIVILEGES GRANT SELECT ON TABLES TO engine_business_reader')
            second = role.provision(owner, data, 'staging', False, root)
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
            recovered = role.provision(owner, data, 'staging', False, root)
            self.assertFalse(recovered['password_changed'])
            self.assertTrue(final.exists())
            rotated = role.provision(owner, data, 'staging', True, root)
            self.assertTrue(rotated['password_changed'])
            self.assertNotEqual(final.read_bytes(), original)
            with patch.object(role, 'authenticated', return_value=False):
                with self.assertRaises(role.Refusal):
                    role.provision(owner, data, 'staging', False, root)
            final.unlink()
            with self.assertRaises(role.Refusal):
                role.provision(owner, data, 'staging', False, root)
            self.assertTrue(role.provision(owner, data, 'staging', True, root)['password_changed'])


if __name__ == '__main__':
    unittest.main()
