import importlib.util
import contextlib
import io
import json
import os
import pathlib
import subprocess
import sys
import unittest
import uuid
from unittest.mock import MagicMock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('journey', ROOT / 'tools/staging-journey-fixtures.py')
assert spec is not None and spec.loader is not None
journey = importlib.util.module_from_spec(spec)
spec.loader.exec_module(journey)


class JourneyContractTests(unittest.TestCase):
    def test_run_identity_is_deterministic_and_disjoint(self):
        run = str(uuid.uuid4())
        value = journey.identities(run)
        self.assertEqual(journey.identities(run), value)
        self.assertNotEqual(value, journey.identities(str(uuid.uuid4())))
        self.assertEqual(len(value.values()), len(set(value.values())))

    def test_rejects_non_run_identifiers(self):
        for run in ('', '../main', 'real-client', '00000000-0000-0000-0000-000000000000'):
            with self.subTest(run=run), self.assertRaises(ValueError):
                journey.identities(run)

    def test_cli_refuses_unverified_target_before_any_connection(self):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {}, clear=True), patch.object(sys, 'argv', ['staging-journey-fixtures.py', 'prepare', '--run', str(uuid.uuid4())]), \
             patch.object(journey.psycopg, 'connect', side_effect=AssertionError('unverified connection')) as connect:
            with self.assertRaisesRegex(Exception, 'identity is absent or forbidden'):
                journey.main()
            connect.assert_not_called()


class JourneyBindingTests(unittest.TestCase):
    def test_remaining_resolves_configured_replacement_without_fixture_exports(self):
        operation = json.loads((ROOT / 'ops/config/release-pipeline.v1.json').read_text())['worker']['staging_candidate_operation_id']
        projects = [
            {'id': 'steep-field-48688294', 'name': 'production'},
            {'id': 'synthetic-old', 'name': 'carr-staging'},
            {'id': 'synthetic-candidate', 'name': 'carr-staging-replacement-' + uuid.UUID(operation).hex,
             'pg_version': 18, 'region_id': 'aws-us-east-1'},
        ]
        host = 'ep-synthetic-candidate.us-east-1.aws.neon.tech'
        owner = f'postgresql://neondb_owner:password@{host}/neondb?sslmode=require&channel_binding=require'  # ci-secret-scan: allow — synthetic provider fixture
        calls = []

        def provider(args, **kwargs):
            calls.append(args)
            self.assertEqual(kwargs['env']['NEON_API_KEY'], 'synthetic-provider-key')
            if args[1:3] == ['projects', 'list']:
                value = projects
            elif args[1:3] == ['branches', 'list']:
                project = args[args.index('--project-id') + 1]
                value = [{'id': 'br-' + project, 'project_id': project,
                          'name': 'production' if project == 'steep-field-48688294' else 'main', 'default': True}]
            elif args[1] == 'api':
                project = args[2].split('/')[2]
                value = [{'id': 'ep-' + project, 'branch_id': 'br-' + project, 'type': 'read_write',
                          'host': 'ep-' + project + '.us-east-1.aws.neon.tech',
                          'autoscaling_limit_min_cu': 0.25, 'autoscaling_limit_max_cu': 2}]
            elif args[1] == 'connection-string':
                self.assertEqual(args[args.index('--project-id') + 1], 'synthetic-candidate')
                self.assertEqual(args[args.index('--role-name') + 1], 'neondb_owner')
                return subprocess.CompletedProcess(args, 0, owner, '')
            else:
                self.fail('unexpected provider action')
            return subprocess.CompletedProcess(args, 0, json.dumps(value), '')

        conn = MagicMock()
        cur = conn.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = lambda: (True,) if 'session_user' in cur.execute.call_args.args[0] else (0,)
        output = io.StringIO()
        run = str(uuid.uuid4())
        with patch.dict(os.environ, {'NEON_API_KEY': 'synthetic-provider-key'}, clear=True), \
             patch.object(subprocess, 'run', side_effect=provider), \
             patch.object(journey.psycopg, 'connect', return_value=conn) as connect, \
             patch.object(sys, 'argv', ['staging-journey-fixtures.py', 'remaining', '--run', run]), \
             contextlib.redirect_stdout(output):
            journey.main()
        self.assertEqual(json.loads(output.getvalue()), {'schema': journey.SCHEMA, 'run': run, 'remaining': 0})
        connect.assert_called_once_with(owner, connect_timeout=10)
        self.assertNotIn(owner, output.getvalue())
        self.assertTrue(calls)


class JourneyPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import os
        import shutil
        import psycopg
        from lib.disposable_pg_fixture import DisposablePostgres, postgres_fixture_group
        cls.group = postgres_fixture_group()
        cls.group.__enter__()
        binary = pathlib.Path(shutil.which('pg_config')).parent
        if not (binary / 'postgres').exists():
            binary = pathlib.Path('/opt/homebrew/opt/postgresql@18/bin')
        if not (binary / 'postgres').exists():
            raise RuntimeError('local PostgreSQL is required; fixture proof never skips')
        cls.fixture = DisposablePostgres('journey-', binary / 'pg_ctl', env={
            key: value for key, value in os.environ.items() if key in ('PATH', 'HOME', 'TMPDIR')})
        cls.addClassCleanup(cls.group.__exit__, None, None, None)
        cls.addClassCleanup(cls.fixture.close)
        root = cls.fixture.root
        for command in ([binary / 'initdb', '-D', root / 'data', '-U', 'neondb_owner', '--auth=trust', '--no-locale', '--encoding=UTF8'],
                        [binary / 'pg_ctl', '-D', root / 'data', '-l', root / 'postgres.log', '-o', f"-k {root} -h ''", '-w', 'start'],
                        [binary / 'createdb', '-h', root, '-U', 'neondb_owner', 'neondb'],
                        [binary / 'psql', '-h', root, '-U', 'neondb_owner', '-d', 'neondb', '-v', 'ON_ERROR_STOP=1', '-q', '-f', ROOT / 'db/schema.sql']):
            result = cls.fixture.run(command, capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError(result.stderr[-3000:])
        cls.conn = psycopg.connect(host=str(root), user='neondb_owner', dbname='neondb')
        cls.addClassCleanup(cls.conn.close)
        with cls.conn.cursor() as cur:
            cur.execute("insert into actor(slug,display_name,kind) values('joe','Synthetic broker','human'),('system','Synthetic system','system') on conflict do nothing")
            cur.execute("insert into client_status(slug,label,sort) values('past_client','Past client',1) on conflict do nothing")
        cls.conn.commit()

    def test_create_read_repeat_and_complete_cleanup(self):
        run = str(uuid.uuid4())
        manifest = journey.prepare(self.conn, run)
        self.assertEqual(manifest['schema'], 'doctorcre-staging-journey-fixture.v1')
        self.assertEqual(journey.prepare(self.conn, run), manifest)
        readback = journey.read(self.conn, run)
        self.assertEqual(readback['lease']['client_name'], manifest['lease']['client_name'])
        self.assertEqual(readback['tour']['routes'][0]['id'], manifest['tour']['route_id'])
        self.assertTrue(readback['tour']['routes'][0]['accepted'])
        self.assertEqual([s['property_id'] for s in readback['tour']['routes'][0]['stops']], manifest['tour']['property_ids'])
        journey.cleanup(self.conn, run)
        self.assertEqual(journey.remaining(self.conn, run), 0)
        journey.cleanup(self.conn, run)

    def test_share_issue_exchange_scope_revoke_and_cleanup(self):
        import hashlib
        from psycopg.types.json import Jsonb
        run = str(uuid.uuid4())
        fixture = journey.prepare(self.conn, run)
        ids = journey.identities(run)
        digest = lambda text: 'sha256:' + hashlib.sha256(text.encode()).hexdigest()
        token, session = digest('synthetic-token-' + run), digest('synthetic-session-' + run)
        with self.conn.cursor() as cur:
            cur.execute("set local session authorization carr_authority")
            grant = journey.scalar(cur, "select ops.issue_tour_share_grant(%s,%s,%s,%s,now()+interval '1 hour',%s,'joe')",
                                   (journey.TENANT, ids['projection'], token, Jsonb(['view_packet','shortlist']), digest(run)))
        self.conn.commit()
        with self.conn.cursor() as cur:
            cur.execute('set local session authorization carr_writer')
            exchanged = journey.scalar(cur, "select ops.exchange_tour_share_token(%s,%s,now()+interval '30 minutes',%s)", (token, session, digest(run)))
            self.assertEqual(exchanged['permission_scopes'], ['view_packet','shortlist'])
            packet = journey.scalar(cur, 'select ops.read_tour_share_packet(%s)', (session,))
            self.assertEqual([stop['name'] for stop in packet['stops']], fixture['tour']['property_names'])
            self.assertNotIn('Synthetic Unrelated Office', str(packet))
            self.assertNotIn(ids['unrelated_property'], str(packet))
            feedback = journey.scalar(cur, 'select ops.read_tour_share_feedback(%s)', (session,))
            unrelated = 'property:public:' + hashlib.sha256(f"{journey.TENANT}:{ids['projection']}:{ids['unrelated_property']}".encode()).hexdigest()[:32]
            self.assertIsNone(journey.scalar(cur, 'select ops.write_tour_share_shortlist(%s,%s,%s,true,%s)',
                                            (session, feedback['projection_ref'], unrelated, uuid.uuid4())))
            self.assertEqual(journey.scalar(cur, 'select ops.write_tour_share_shortlist(%s,%s,%s,true,%s)',
                                            (session, feedback['projection_ref'], packet['stops'][0]['property_ref'], uuid.uuid4())), {'saved': True})
        self.conn.commit()
        with self.conn.cursor() as cur:
            cur.execute('set local session authorization carr_authority')
            journey.scalar(cur, "select ops.revoke_tour_share_grant(%s,%s,'Synthetic fixture complete',%s,now(),'joe')", (journey.TENANT, grant, digest(run)))
        self.conn.commit()
        with self.conn.cursor() as cur:
            cur.execute('set local session authorization carr_writer')
            self.assertIsNone(journey.scalar(cur, 'select ops.read_tour_share_packet(%s)', (session,)))
            self.assertIsNone(journey.scalar(cur, "select ops.exchange_tour_share_token(%s,%s,now()+interval '30 minutes',%s)", (token, digest('new-session-' + run), digest(run))))
        self.conn.commit()
        journey.cleanup(self.conn, run)
        self.assertEqual(journey.remaining(self.conn, run), 0)

    def test_cleanup_preserves_another_run_and_rejects_changed_provenance(self):
        first, second = str(uuid.uuid4()), str(uuid.uuid4())
        journey.prepare(self.conn, first)
        journey.prepare(self.conn, second)
        with self.conn.cursor() as cur:
            cur.execute('update party set notes_path=%s where id=%s', ('real-record', journey.identities(first)['party']))
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, 'provenance changed'):
            journey.cleanup(self.conn, first)
        self.assertGreater(journey.remaining(self.conn, first), 0)
        with self.conn.cursor() as cur:
            cur.execute('update party set notes_path=%s where id=%s', (f'fixtures/staging-journey/{first}', journey.identities(first)['party']))
        self.conn.commit()
        journey.cleanup(self.conn, first)
        journey.verify(self.conn, second)
        journey.cleanup(self.conn, second)
        self.assertEqual(journey.remaining(self.conn, first) + journey.remaining(self.conn, second), 0)

    def test_cleanup_removes_share_envelopes_and_events(self):
        from psycopg.types.json import Jsonb
        run = str(uuid.uuid4())
        fixture = journey.prepare(self.conn, run)
        ids, key, event = journey.identities(run), str(uuid.uuid4()), uuid.uuid4()
        with self.conn.cursor() as cur:
            joe = journey.scalar(cur, "select id from actor where slug='joe'")
            grant = journey.scalar(cur, "select ops.issue_tour_share_grant(%s,%s,%s,%s,now()+interval '1 hour',%s,'joe')",
                                   (journey.TENANT, ids['projection'], 'sha256:' + uuid.uuid4().hex * 2, Jsonb(['view_packet']), 'sha256:' + 'b'*64))
            journey.insert(cur, 'public.tool_call', idempotency_key=key, verb='issue-tour-share-grant', actor_id=joe,
                           request_hash='synthetic-fixture', response=Jsonb({'ok': True, 'share_grant_id': str(grant)}))
            cur.execute("insert into event(id,occurred_at,actor_id,verb,subject_type,subject_id,cause,idempotency_key) values(%s,now(),%s,'issue-tour-share-grant','tour_share_grant',%s,'automation_job',%s)", (event, joe, grant, key))
        self.conn.commit()
        journey.cleanup(self.conn, run)
        with self.conn.cursor() as cur:
            self.assertEqual(journey.scalar(cur, 'select count(*) from event where id=%s', (event,)), 0)
            self.assertEqual(journey.scalar(cur, 'select count(*) from tool_call where idempotency_key=%s', (key,)), 0)


if __name__ == '__main__':
    unittest.main()
