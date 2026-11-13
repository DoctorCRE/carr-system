"""Test bounded engine role grants, convergence and credential custody."""
import importlib.util
import pathlib
import os
import subprocess
import sys
import tempfile
import io
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch
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
        self.assertEqual(len(data['tables']), 73)
        self.assertIn('activity', data['tables'])
        self.assertIn('v_deal_room_current_lease', data['views'])
        self.assertEqual(data['settings']['timezone'], 'UTC')
        self.assertEqual(len(set(data['tables'] + data['views'])), 117)

    def test_page_frame_grants_are_whole_relations(self):
        data = role.load_spec()
        statements = role.grant_statements(data)
        self.assertIn('board_snapshot', data['tables'])
        for name in ['board_snapshot', 'v_export_clients', 'v_export_deals', 'v_today_triage']:
            self.assertIn(f'GRANT SELECT ON TABLE "public"."{name}" TO "engine_business_reader"', statements)
        self.assertNotIn('tool_call', data['tables'] + data['views'])

    def test_spec_rejects_role_or_identifier_expansion(self):
        data = role.load_spec()
        data['role'] = 'app_writer'
        with self.assertRaises(role.Refusal):
            role.validate_spec(data)
        data = role.load_spec()
        data['tables'].append('ops.work_request')
        with self.assertRaises(role.Refusal):
            role.validate_spec(data)

    def test_grants_preserve_existing_select_and_execute_contract(self):
        statements = role.grant_statements(role.load_spec())
        text = '\n'.join(statements)
        self.assertIn('GRANT SELECT ON TABLE "public"."activity"', text)
        self.assertIn('GRANT SELECT ON TABLE "public"."v_deal_room_presence" TO "engine_business_reader"', statements)
        self.assertIn('GRANT SELECT ON TABLE "public"."v_capture_session_status" TO "engine_business_reader"', statements)
        self.assertIn('GRANT SELECT ON TABLE "public"."v_capture_candidate_queue" TO "engine_business_reader"', statements)
        self.assertIn('GRANT SELECT ON TABLE "public"."v_party_graph" TO "engine_business_reader"', statements)
        self.assertIn('GRANT SELECT ON TABLE "public"."v_lead_client_best" TO "engine_business_reader"', statements)
        self.assertIn('GRANT SELECT ON TABLE "public"."v_lead_prospect_engagement" TO "engine_business_reader"', statements)
        self.assertIn('GRANT SELECT ON TABLE "public"."v_lead_workspace" TO "engine_business_reader"', statements)
        self.assertIn('GRANT SELECT ON TABLE "public"."v_practice_origination_party_address" TO "engine_business_reader"', statements)
        self.assertIn('GRANT SELECT ON TABLE "public"."v_vendor_assignment_context" TO "engine_business_reader"', statements)
        self.assertIn('GRANT SELECT ON TABLE "public"."v_calendar_capture_receipt" TO "engine_business_reader"', statements)
        self.assertIn('GRANT SELECT ON TABLE "public"."v_invoice_tracker" TO "engine_business_reader"',
                      statements)
        self.assertIn('GRANT EXECUTE ON FUNCTION public.carr_business_date(timestamp with time zone)', text)
        self.assertIn('GRANT EXECUTE ON FUNCTION public.practice_address_key(text) TO "engine_business_reader"', statements)
        self.assertEqual(len([s for s in statements if 'FUNCTION' in s]), 2)
        self.assertIn('GRANT SELECT ON TABLE "public"."practice_origination_event" TO "engine_business_reader"', statements)
        self.assertIn('GRANT SELECT ON TABLE "public"."practice_origination_feed" TO "engine_business_reader"', statements)
        self.assertIn('GRANT SELECT ON TABLE "public"."v_record_flag_subject" TO "engine_business_reader"', statements)
        self.assertIn('GRANT SELECT ON TABLE "public"."v_lead_workspace_correspondence" TO "engine_business_reader"', statements)
        self.assertIn('GRANT SELECT ON TABLE "public"."v_lead_workspace_stage_history" TO "engine_business_reader"', statements)
        self.assertIn('GRANT SELECT ON TABLE "public"."v_subject_timeline" TO "engine_business_reader"', statements)
        self.assertNotIn('GRANT SELECT ON TABLE "public"."tool_call" TO "engine_business_reader"', statements)
        self.assertNotIn('SEQUENCE', text)
        self.assertNotIn('DEFAULT PRIVILEGES', text)
        self.assertNotIn('INSERT', text)

    def test_spec_rejects_function_expansion(self):
        for functions in [['extra_fn()'], ['carr_business_date(timestamp with time zone)', 'extra_fn()'],
                          ['practice_address_key(text)', 'practice_address_key(text)'],
                          None, 'practice_address_key(text)', [None], [[]]]:
            data = role.load_spec()
            data['functions'] = functions
            with self.assertRaises(role.Refusal):
                role.validate_spec(data)

    def test_spec_accepts_each_authorized_function_subset(self):
        for functions in [[], ['carr_business_date(timestamp with time zone)'],
                          ['practice_address_key(text)'],
                          ['practice_address_key(text)', 'carr_business_date(timestamp with time zone)']]:
            with self.subTest(functions=functions):
                data = role.load_spec() | {'functions': functions}
                self.assertEqual(role.validate_spec(data)['functions'], functions)

    def test_proof_checks_function_execute_and_create_denials(self):
        data = role.load_spec() | {'views': []}
        for denied in [None, 'public.practice_address_key(text)',
                       'public.carr_business_date(timestamp with time zone)',
                       'no_schema_create', 'no_database_create']:
            with self.subTest(denied=denied):
                conn = MagicMock()
                def execute(statement, params=None):
                    query = str(statement)
                    if query in [
                        'SELECT 1 FROM ops.client_portal LIMIT 1',
                        'EXPLAIN INSERT INTO public.activity DEFAULT VALUES',
                        'EXPLAIN UPDATE public.activity SET id=id WHERE false',
                        'EXPLAIN DELETE FROM public.activity WHERE false',
                    ]:
                        raise role.psycopg.errors.InsufficientPrivilege()
                    value = {
                        'SHOW TimeZone': 'UTC', 'SHOW statement_timeout': '15s',
                        'SHOW idle_in_transaction_session_timeout': '30s',
                        'SHOW default_transaction_read_only': 'on',
                    }.get(query, True)
                    if 'has_function_privilege' in query:
                        value = params[0] != denied
                    if 'has_schema_privilege' in query and "'CREATE'" in query:
                        value = denied != 'no_schema_create'
                    if 'has_database_privilege' in query and "'CREATE'" in query:
                        value = denied != 'no_database_create'
                    cursor = MagicMock()
                    cursor.fetchone.return_value = (value,)
                    return cursor
                conn.execute.side_effect = execute
                with patch.object(role, 'connect') as connect, patch.object(
                    role, 'function_grants_match', return_value=True
                ):
                    connect.return_value.__enter__.return_value = conn
                    result = role.proof('synthetic-reader-uri', data)
                function_calls = [call for call in conn.execute.call_args_list
                                  if 'has_function_privilege' in str(call.args[0])]
                self.assertEqual([call.args for call in function_calls], [
                    ("SELECT has_function_privilege(current_user,%s,'EXECUTE')",
                     ('public.carr_business_date(timestamp with time zone)',)),
                    ("SELECT has_function_privilege(current_user,%s,'EXECUTE')",
                     ('public.practice_address_key(text)',)),
                ])
                for key in ['functions_executable', 'no_schema_create', 'no_database_create']:
                    self.assertIn(key, role.CHECKS)
                    failed_check = ('functions_executable' if denied and denied.startswith('public.')
                                    else denied)
                    self.assertEqual(result[key], key != failed_check, key)


class WriterRoleTests(unittest.TestCase):
    def test_writer_inherits_reader_and_exact_write_contract(self):
        reader = role.load_spec()
        writer = role.load_spec('writer')
        self.assertEqual(writer['role'], 'engine_business_writer')
        excluded = (
            'board_snapshot practice_origination_event practice_origination_feed '
            'v_calendar_capture_receipt v_capture_candidate_queue v_capture_session_status '
            'v_deal_room_presence v_export_clients v_export_deals v_lead_client_best '
            'v_lead_prospect_engagement v_lead_workspace v_lead_workspace_correspondence '
            'v_lead_workspace_stage_history v_party_graph v_practice_origination_party_address '
            'v_record_flag_subject v_subject_timeline v_today_triage v_vendor_assignment_context'
        ).split()
        self.assertEqual(writer['tables'], [name for name in reader['tables'] if name not in excluded])
        self.assertEqual(writer['views'], [name for name in reader['views'] if name not in excluded])
        self.assertEqual(len(writer['tables'] + writer['views']), 97)
        expected = {
            'INSERT': {
                'deal_participant': 'deal_id actor_id role set_by'.split(),
                'deal_note': 'deal_id kind text actor_id created_at'.split(),
                'next_action': (
                    'subject_type subject_id owner_id due_on description created_by updated_by updated_at '
                    'status'
                ).split(),
                'event': (
                    'occurred_at recorded_at actor_id verb subject_type subject_id field old_value '
                    'new_value cause human_quote agent_rationale idempotency_key via client_id '
                    'organization_tenant_id sponsoring_human_slug personal_scope authorization_class '
                    'correlation_id'
                ).split(),
                'tool_call': (
                    'idempotency_key verb actor_id request_hash response via client_id '
                    'organization_tenant_id sponsoring_human_slug personal_scope authorization_class '
                    'correlation_id'
                ).split(),
                'deal_conflict': 'deal_id field value_a actor_a event_a value_b actor_b'.split(),
                'deal_presence_lease': 'actor_id deal_id field expires_at'.split(),
            },
            'UPDATE': {
                'deal': (
                    'phase owner attention next_date operating_state parking_reason parking_note '
                    'parked_at parked_by updated_by'
                ).split(),
                'deal_participant': 'to_at'.split(),
                'next_action': 'status updated_by updated_at'.split(),
                'capture_post_call_action': 'status updated_at completed_at'.split(),
                'deal_conflict': 'status resolved_by winner resolved_at'.split(),
                'deal_presence_lease': 'expires_at'.split(),
            },
            'SELECT': {
                'tool_call': 'idempotency_key request_hash response created_at'.split(),
            },
        }
        self.assertEqual(writer['columns'], expected)
        self.assertEqual(writer['settings']['default_transaction_read_only'], 'off')
        self.assertEqual(writer['functions'], ['carr_business_date(timestamp with time zone)',
                         'carr_is_partner_actor(uuid)', 'carr_is_partner_made(uuid,text)'])
        grants = role.grant_statements(writer)
        self.assertEqual(role.column_grants(writer), {
            (table, column, privilege) for privilege, tables in expected.items()
            for table, columns in tables.items() for column in columns})
        self.assertTrue(all('engine_business_writer' in item for item in grants))
        self.assertFalse(any('DELETE' in item for item in grants))
        self.assertFalse(any('GRANT UPDATE' in item and 'tool_call' in item for item in grants))
        self.assertFalse(any('tool_call' in item for item in role.grant_statements(reader)))
        for table, column, privilege in role.column_grants(writer):
            self.assertIn(f'GRANT {privilege} ("{column}") ON TABLE "public"."{table}" '
                          'TO "engine_business_writer"', grants)

    def test_writer_spec_rejects_forbidden_or_invalid_grants(self):
        for writes in [{'DELETE': ['deal']}, {'INSERT': ['tool_call']},
                       {'UPDATE': ['ops.work_request']}, {'UPDATE': ['deal', 'deal']},
                       {'UPDATE': ['v_deal_board']}, {'UPDATE': ['deal; DROP TABLE deal']}]:
            with self.subTest(writes=writes), self.assertRaises(role.Refusal):
                role.validate_spec(role.load_spec('writer') | {'writes': writes})
        with self.assertRaises(role.Refusal):
            role.validate_spec(role.load_spec() | {'writes': {'INSERT': ['deal']}})
        with self.assertRaises(role.Refusal):
            role.validate_spec(role.load_spec() | {'tables': ['tool_call']})
        with self.assertRaises(role.Refusal):
            role.load_spec('owner')
        for columns in [{'DELETE': {'deal': ['id']}}, {'UPDATE': {'tool_call': ['response']}},
                        {'SELECT': {'deal': ['id']}}, {'INSERT': {'ops.work_request': ['id']}},
                        {'INSERT': {'deal': ['id', 'id']}}, {'INSERT': {'deal': ['id;bad']}},
                        {'INSERT': {'deal': []}}, {'INSERT': ['deal']}]:
            with self.subTest(columns=columns), self.assertRaises(role.Refusal):
                role.validate_spec(role.load_spec('writer') | {'columns': columns})
        with self.assertRaises(role.Refusal):
            role.validate_spec(role.load_spec() | {'columns': {'SELECT': {'tool_call': ['id']}}})


    def test_effective_matrix_requires_exact_table_and_column_rights(self):
        data = role.load_spec('writer')
        rows = [('public', name, privilege, True, False)
                for name, privilege in role.relation_grants(data)]
        columns = [('public', name, column, privilege, True, False)
                   for name, column, privilege in role.column_grants(data)]
        conn = MagicMock()
        def check(tables, cols):
            conn.execute.side_effect = [MagicMock(fetchall=lambda: tables),
                                        MagicMock(fetchall=lambda: cols)]
            return role.effective_relation_grants_match(conn, data)
        self.assertTrue(check(rows, columns))
        self.assertFalse(check(rows[:-1], columns))
        self.assertFalse(check(rows, columns[:-1]))
        self.assertFalse(check(rows, columns + [('public', 'tool_call', 'verb', 'SELECT', True, False)]))
        self.assertFalse(check(rows, columns + [('public', 'tool_call', 'response', 'UPDATE', True, False)]))
        self.assertFalse(check(rows, columns + [('public', 'deal', 'phase', 'UPDATE', True, True)]))
        self.assertFalse(check(rows + [('public', 'deal', 'DELETE', True, False)], columns))

    def test_writer_cannot_use_local_credential_protocol(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(role, 'connect') as connect:
            with self.assertRaises(role.Refusal):
                role.provision('synthetic-owner', role.load_spec('writer'), 'production', False,
                               pathlib.Path(directory))
            connect.assert_not_called()
            self.assertEqual(list(pathlib.Path(directory).iterdir()), [])

    def test_writer_read_exclusions_are_bounded_by_reader(self):
        reader = role.load_spec()
        writer = role.json.loads(role.SPEC.with_name('engine-business-writer.json').read_text())
        for excluded in ['activity', ['activity', 'activity'], ['unknown'], [None]]:
            with self.subTest(excluded=excluded), patch.object(
                pathlib.Path, 'read_text', side_effect=[role.json.dumps(reader),
                    role.json.dumps(writer | {'exclude_reads': excluded})]
            ), self.assertRaises(role.Refusal):
                role.load_spec('writer')


@unittest.skipUnless(os.environ.get('ENGINE_ROLE_TEST_DSN'), 'canonical PostgreSQL fixture not requested')
class PasswordPostgresTests(unittest.TestCase):
    def test_password_never_reaches_sql_on_create_rotate_or_failure(self):
        owner = os.environ['ENGINE_ROLE_TEST_DSN']
        with role.connect(owner) as conn:
            conn.autocommit = True
            conn.execute('CREATE DATABASE password_fixture')
            hba = pathlib.Path(conn.execute('SHOW hba_file').fetchone()[0])
            original = hba.read_text()
            hba.write_text('host all engine_business_reader,engine_business_writer 127.0.0.1/32 scram-sha-256\n' + original)
            conn.execute('SELECT pg_reload_conf()')
        target = owner.rsplit('/', 1)[0] + '/password_fixture'
        try:
            with role.connect(target) as conn:
                conn.execute('CREATE TABLE public.activity(id int)')
            for kind in ['reader', 'writer']:
                data = role.load_spec(kind) | {'tables': ['activity'], 'views': [],
                                              'functions': [], 'writes': {}, 'columns': {}}
                name = data['role']
                for attempt in ['create', 'rotate', 'failure']:
                    with self.subTest(kind=kind, attempt=attempt):
                        password = 'Synthetic-Secret-' + kind + '-' + attempt
                        statements = []
                        with role.connect(target) as conn:
                            class Captured:
                                def __getattr__(self, key):
                                    return getattr(conn, key)

                                def execute(self, statement, params=None):
                                    text = (statement.as_string(conn) if isinstance(statement, role.sql.Composable)
                                            else statement)
                                    if attempt == 'failure' and text.startswith('ALTER ROLE') and ' PASSWORD ' in text:
                                        text += ' INVALID'
                                    statements.append(text)
                                    return conn.execute(text, params)
                            if attempt == 'failure':
                                with self.assertRaises(role.psycopg.errors.SyntaxError):
                                    role.reconcile(Captured(), data, password)
                                conn.rollback()
                            else:
                                role.reconcile(Captured(), data, password)
                        self.assertTrue(any(' PASSWORD ' in text and 'SCRAM-SHA-256$' in text
                                            for text in statements))
                        self.assertFalse(any(password in text for text in statements),
                                         'plaintext password was sent in SQL')
                        if attempt != 'failure':
                            uri = target.replace('fixture@', name + ':' + password + '@')
                            self.assertTrue(role.authenticated(uri, name))
                            self.assertFalse(role.authenticated(uri.replace(password, 'wrong-password'), name))
                with role.connect(target) as conn:
                    conn.execute(role.sql.SQL('DROP OWNED BY {}').format(role.sql.Identifier(name)))
                    conn.execute(role.sql.SQL('DROP ROLE {}').format(role.sql.Identifier(name)))
        finally:
            with role.connect(owner) as conn:
                conn.autocommit = True
                hba.write_text(original)
                conn.execute('SELECT pg_reload_conf()')
                conn.execute('DROP DATABASE password_fixture')
                for name in ['engine_business_reader', 'engine_business_writer']:
                    conn.execute(role.sql.SQL('DROP ROLE IF EXISTS {}').format(role.sql.Identifier(name)))


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
            conn.execute('CREATE FUNCTION public.practice_address_key(text) RETURNS text '
                         'LANGUAGE sql IMMUTABLE STRICT AS $$ SELECT lower($1) $$')
            conn.execute('REVOKE ALL ON FUNCTION public.practice_address_key(text) FROM PUBLIC')
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
            for name in ['v_export_clients', 'v_export_deals', 'v_today_triage']:
                self.assertTrue(first['proof'][name]['read'], name)
            self.assertTrue(first['proof']['function_grants_match'])
            self.assertTrue(first['proof']['functions_executable'])
            self.assertTrue(first['proof']['no_schema_create'])
            self.assertTrue(first['proof']['no_database_create'])
            self.assertTrue(first['proof']['v_open_deal_room_board']['read'])
            self.assertTrue(first['proof']['v_deal_room_presence']['read'])
            self.assertTrue(first['proof']['v_capture_session_status']['read'])
            self.assertTrue(first['proof']['v_capture_candidate_queue']['read'])
            self.assertTrue(first['proof']['v_party_graph']['read'])
            self.assertTrue(first['proof']['v_lead_client_best']['read'])
            self.assertTrue(first['proof']['v_lead_prospect_engagement']['read'])
            self.assertTrue(first['proof']['v_lead_workspace']['read'])
            self.assertTrue(first['proof']['v_practice_origination_party_address']['read'])
            self.assertTrue(first['proof']['v_vendor_assignment_context']['read'])
            self.assertTrue(first['proof']['v_calendar_capture_receipt']['read'])
            self.assertTrue(first['proof']['v_record_flag_subject']['read'])
            self.assertTrue(first['proof']['v_lead_workspace_correspondence']['read'])
            self.assertTrue(first['proof']['v_lead_workspace_stage_history']['read'])
            self.assertTrue(first['proof']['v_subject_timeline']['read'])
            self.assertEqual(first['proof']['v_invoice_tracker'], {'read': True, 'has_rows': True})
            final = root / 'engine-test-engine-business-reader.env'
            original = final.read_bytes()
            secret = original.decode().split('=', 1)[1].strip()
            from urllib.parse import unquote, urlsplit
            generated_password = unquote(urlsplit(secret).password)
            self.assertNotIn(generated_password, output)
            self.assertNotIn(secret, output)
            table_probe = data | {'views': data['views'] + ['practice_origination_event', 'practice_origination_feed', 'board_snapshot']}
            provision_reads = role.proof(secret, table_probe)
            self.assertTrue(provision_reads['practice_origination_event']['read'])
            self.assertTrue(provision_reads['practice_origination_feed']['read'])
            self.assertTrue(provision_reads['board_snapshot']['read'])
            with role.connect(owner) as conn:
                conn.execute('GRANT extra_bundle TO engine_business_reader')
                conn.execute('GRANT EXECUTE ON FUNCTION public.extra_fn() TO engine_business_reader')
                conn.execute('GRANT SELECT,INSERT ON public.extra TO engine_business_reader')
                conn.execute('GRANT UPDATE(id) ON public.activity TO engine_business_reader')
                conn.execute('GRANT USAGE ON SCHEMA ops TO engine_business_reader')
                conn.execute('ALTER DEFAULT PRIVILEGES GRANT SELECT ON TABLES TO engine_business_reader')
            second = role.provision(owner, data, 'engine-test', False, root)
            for name in ['v_export_clients', 'v_export_deals', 'v_today_triage']:
                self.assertTrue(second['proof'][name]['read'], name)
            self.assertTrue(second['proof']['function_grants_match'])
            self.assertTrue(second['proof']['v_open_deal_room_board']['read'])
            self.assertTrue(second['proof']['v_deal_room_presence']['read'])
            self.assertTrue(second['proof']['v_capture_session_status']['read'])
            self.assertTrue(second['proof']['v_capture_candidate_queue']['read'])
            self.assertTrue(second['proof']['v_party_graph']['read'])
            self.assertTrue(second['proof']['v_lead_client_best']['read'])
            self.assertTrue(second['proof']['v_lead_prospect_engagement']['read'])
            self.assertTrue(second['proof']['v_lead_workspace']['read'])
            self.assertTrue(second['proof']['v_practice_origination_party_address']['read'])
            self.assertTrue(second['proof']['v_vendor_assignment_context']['read'])
            self.assertTrue(second['proof']['v_calendar_capture_receipt']['read'])
            self.assertTrue(second['proof']['v_record_flag_subject']['read'])
            self.assertTrue(second['proof']['v_lead_workspace_correspondence']['read'])
            self.assertTrue(second['proof']['v_lead_workspace_stage_history']['read'])
            self.assertTrue(second['proof']['v_subject_timeline']['read'])
            self.assertEqual(second['proof']['v_invoice_tracker'], {'read': True, 'has_rows': True})
            reconcile_reads = role.proof(secret, table_probe)
            self.assertTrue(reconcile_reads['practice_origination_event']['read'])
            self.assertTrue(reconcile_reads['practice_origination_feed']['read'])
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
                conn.execute('CREATE FUNCTION public.practice_address_key(text) RETURNS text '
                             'LANGUAGE sql IMMUTABLE STRICT AS $$ SELECT lower($1) $$')
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

    def test_writer_converges_and_refuses_public_privilege_leaks(self):
        owner = os.environ['ENGINE_ROLE_TEST_DSN']
        data = role.load_spec('writer')
        with role.connect(owner) as conn:
            conn.execute('CREATE FUNCTION public.carr_is_partner_actor(uuid) RETURNS boolean '
                         'LANGUAGE sql AS $$ SELECT true $$')
            conn.execute('CREATE FUNCTION public.carr_is_partner_made(uuid,text) RETURNS boolean '
                         'LANGUAGE sql AS $$ SELECT true $$')
            conn.execute('CREATE TABLE public.tool_call(id int)')
            for table in {name for name, _, _ in role.column_grants(data)}:
                columns = {column for name, column, _ in role.column_grants(data) if name == table}
                for column in sorted(columns):
                    conn.execute(role.sql.SQL('ALTER TABLE public.{} ADD COLUMN {} text').format(
                        role.sql.Identifier(table), role.sql.Identifier(column)))
            conn.execute('ALTER TABLE public.event ADD COLUMN mutation_order bigint GENERATED ALWAYS AS IDENTITY')
            self.assertTrue(role.reconcile(conn, data))
        with role.connect(owner) as conn:
            self.assertTrue(role.effective_relation_grants_match(conn, data))
            conn.execute('GRANT SELECT(id), UPDATE(id) ON public.tool_call TO engine_business_writer')
            conn.execute('GRANT DELETE ON public.deal TO engine_business_writer')
            conn.execute('GRANT extra_bundle TO engine_business_writer')
        with role.connect(owner) as conn:
            self.assertFalse(role.reconcile(conn, data))
            self.assertTrue(role.effective_relation_grants_match(conn, data))
            self.assertFalse(conn.execute("SELECT has_sequence_privilege('engine_business_writer',"
                "'public.event_mutation_order_seq','USAGE')").fetchone()[0])
        writer_uri = owner.replace('fixture@', 'engine_business_writer@')
        self.assertTrue(role.authenticated(writer_uri, data['role']))
        with role.connect(writer_uri) as conn:
            self.assertEqual(conn.execute('SHOW default_transaction_read_only').fetchone()[0], 'off')
            for name, column, privilege in sorted(role.column_grants(data)):
                if privilege == 'SELECT':
                    conn.execute(role.sql.SQL('SELECT {} FROM public.{} LIMIT 1').format(
                        role.sql.Identifier(column), role.sql.Identifier(name)))
                elif privilege == 'INSERT':
                    conn.execute(role.sql.SQL('INSERT INTO public.{} ({}) VALUES (NULL)').format(
                        role.sql.Identifier(name), role.sql.Identifier(column)))
                else:
                    conn.execute(role.sql.SQL('UPDATE public.{} SET {}=NULL WHERE false').format(
                        role.sql.Identifier(name), role.sql.Identifier(column)))
            for statement in ['SELECT verb FROM public.tool_call',
                              'UPDATE public.tool_call SET response=NULL WHERE false',
                              'DELETE FROM public.tool_call WHERE false',
                              'UPDATE public.deal SET id=NULL WHERE false']:
                with self.assertRaises(role.psycopg.errors.InsufficientPrivilege):
                    with conn.transaction():
                        conn.execute(statement)
            conn.rollback()
        result = role.proof(writer_uri, data)
        for check in role.CHECKS:
            self.assertTrue(result[check], check)
        for statement in ['GRANT SELECT ON public.tool_call TO PUBLIC',
                          'GRANT SELECT(verb) ON public.tool_call TO PUBLIC',
                          'GRANT UPDATE(id) ON public.tool_call TO PUBLIC',
                          'GRANT DELETE ON public.deal TO PUBLIC']:
            with role.connect(owner) as conn:
                conn.execute(statement)
            try:
                with self.assertRaises(role.Refusal), role.connect(owner) as conn:
                    role.reconcile(conn, data)
            finally:
                with role.connect(owner) as conn:
                    conn.execute('REVOKE ALL ON public.tool_call, public.deal FROM PUBLIC')
                    conn.execute('REVOKE ALL(id,verb) ON public.tool_call FROM PUBLIC')
        with role.connect(owner) as conn:
            conn.execute('ALTER TABLE public.tool_call OWNER TO engine_business_writer')
        with self.assertRaises(role.Refusal), role.connect(owner) as conn:
            role.reconcile(conn, data)


if __name__ == '__main__':
    unittest.main()
