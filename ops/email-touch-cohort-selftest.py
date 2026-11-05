"""Exercise the cohort command using real disposable PostgreSQL."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.disposable_pg_fixture import DisposablePostgres, postgres_bin_dir, postgres_fixture_group

RUNNER = ROOT / 'ops/email-touch-cohort-proof.py'
ACTOR, TARGET, KEY, ACTIVITY, OTHER = [f'00000000-0000-4000-8000-{i:012d}' for i in range(1, 6)]
SECRET = 'Hostile private name private@example.test message body'
EFFECT = dict(source_id=SECRET, source_version='private-revision', original_key=KEY,
              role='client', target_type='client', target_id=TARGET, occurred_at='2026-01-01T12:00:00.000Z')
JOURNAL = dict(schema='email-touch-expectations.v1', principal_slug='fixture', actor_id=ACTOR,
               actor_slug='fixture', tenant='carr-internal', release_sha='a'*40,
               start='2026-01-01T00:00:00Z', end='2026-01-02T00:00:00Z',
               expected_count=1, expected=[EFFECT])


def runner_module():
    spec = importlib.util.spec_from_file_location('cohort_proof', RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BoundaryTest(unittest.TestCase):
    def test_release_circuit_breaker(self):
        module = runner_module()
        for payload in (
            {'ok':True, 'env':{'value':'production'}, 'git_sha':{'value':'a'*40}},
            {'ok':True, 'env':{'value':'staging'}, 'git_sha':{'value':'a'*40}},
            {'ok':False}, {'ok':True}, 'x'*65537,
        ):
            with self.subTest(payload_type=type(payload).__name__):
                response = MagicMock()
                response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
                with patch.object(module, 'urlopen', return_value=response) as transport:
                    if isinstance(payload, dict) and payload.get('env', {}).get('value') == 'production':
                        self.assertEqual(module.live_release(), 'a'*40)
                    else:
                        with self.assertRaisesRegex(module.Refused, 'release_unavailable'):
                            module.live_release()
                    transport.assert_called_once_with('https://api.doctorcre.com/release', timeout=5)
                    response.__enter__.return_value.read.assert_called_once_with(65537)
        with patch.object(module, 'urlopen', side_effect=TimeoutError) as transport:
            with self.assertRaisesRegex(module.Refused, 'release_unavailable'):
                module.live_release()
            self.assertEqual(transport.call_count, 1)

    def test_deadline_and_arguments(self):
        module = runner_module()
        with self.assertRaisesRegex(module.Refused, 'deadline'):
            module.deadline(None, None)
        result = subprocess.run([sys.executable, str(RUNNER), '--unknown-private-option'],
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)['reason'], 'invalid_arguments')
        self.assertNotIn('unknown-private', result.stdout + result.stderr)


class RunnerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        group = postgres_fixture_group()
        group.__enter__()
        cls.addClassCleanup(group.__exit__, None, None, None)
        binary = postgres_bin_dir()
        pg = DisposablePostgres('email-proof-', binary / 'pg_ctl')
        cls.addClassCleanup(pg.close)
        data = pg.root / 'data'
        pg.run([str(binary / 'initdb'), '-D', str(data), '-A', 'trust'], capture_output=True, check=True)
        pg.run([str(binary / 'pg_ctl'), '-D', str(data), '-l', str(pg.root / 'postgres.log'),
                '-o', f'-k {pg.root} -c listen_addresses= -p 55474', '-w', 'start'], capture_output=True, check=True)
        cls.socket = str(pg.root)
        cls.dsn = f'host={pg.root} port=55474 dbname=postgres'
        cls.psql = [str(binary / 'psql'), '-X', '-qAt', '-v', 'ON_ERROR_STOP=1', cls.dsn]
        cls.sql('''create table actor(id uuid primary key, slug text);
create table party(id uuid primary key, deleted_at timestamptz);
create table refs(subject_type text, subject_id uuid, party_id uuid, merged boolean);
create view v_ref_index as select * from refs;
create table activity(id uuid primary key, kind text, occurred_at timestamptz, created_by uuid,
 client_id uuid, lead_id uuid, vendor_id uuid, email_context jsonb);
create table event(idempotency_key text, verb text, actor_id uuid, organization_tenant_id text,
 recorded_at timestamptz, new_value jsonb);''')

    @classmethod
    def sql(cls, sql):
        return subprocess.run(cls.psql, input=sql, text=True, capture_output=True, check=True, timeout=15).stdout

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='email-proof-input-')
        self.addCleanup(self.tmp.cleanup)
        self.sql('truncate event,activity,refs,party,actor;')
        self.sql(f"insert into actor values ('{ACTOR}','fixture'); insert into party values ('{TARGET}',null); "
                 f"insert into refs values ('client','{TARGET}','{TARGET}',false);")
        self.activity()
        self.event()

    def activity(self, ident=ACTIVITY):
        self.sql(f"insert into activity values ('{ident}','email_in','2026-01-01T12:00Z','{ACTOR}',"
                 f"'{TARGET}',null,null,'{{}}');")

    def event(self, key=KEY, binding=None):
        payload = json.dumps(dict(activity=ACTIVITY, email_application=dict(binding=binding or EFFECT))).replace("'", "''")
        self.sql(f"insert into event values ('{key}','log-activity','{ACTOR}','carr-internal',"
                 f"'2026-01-02T00:00Z','{payload}');")

    def command(self, journal=None, digest=None, missing=False):
        path = Path(self.tmp.name) / 'journal.json'
        raw = json.dumps(JOURNAL if journal is None else journal).encode()
        if missing:
            path.unlink(missing_ok=True)
        if not missing:
            path.write_bytes(raw)
        sha = digest or hashlib.sha256(raw).hexdigest()
        result = subprocess.run([sys.executable, str(RUNNER), '--journal', str(path),
            '--journal-ref', 'sha256:'+sha, '--journal-sha256', sha,
            '--fixture-socket', self.socket, '--fixture-principal', 'fixture',
            '--fixture-release-sha', 'a'*40], text=True, capture_output=True, timeout=70)
        self.assertLess(len(result.stdout.encode()), 4096)
        for secret in (SECRET, 'private-revision', ACTOR, TARGET, KEY):
            self.assertNotIn(secret, result.stdout + result.stderr)
        return result.returncode, json.loads(result.stdout)

    def refuses(self, reason, **kwargs):
        code, result = self.command(**kwargs)
        self.assertNotEqual(code, 0)
        self.assertEqual(result['reason'], reason)
        return result

    def test_positive_and_idempotent(self):
        first = self.command()
        self.assertEqual(first[0], 0)
        self.assertEqual(first[1]['counts']['applied'], 1)
        self.assertEqual(first, self.command())
        self.assertEqual(self.sql('select count(*) from event;').strip(), '1')

    def test_orphan_touch(self):
        self.activity(OTHER)
        self.refuses('orphan_touch')

    def test_missing_lineage(self):
        self.sql('truncate event;')
        result = self.refuses('missing_lineage')
        self.assertEqual(result['counts']['missing'], 1)
        self.assertEqual(result['counts']['dangling'], 0)

    def test_input_refusals(self):
        self.refuses('count_mismatch', journal={**JOURNAL, 'expected_count':2})
        self.refuses('digest_mismatch', digest='0'*64)
        self.refuses('missing_journal', missing=True)

    def test_extra_touch_and_duplicate(self):
        self.event(OTHER)
        self.refuses('extra_touch')

    def test_exact_bindings(self):
        for field, value in [('source_id','different'), ('source_version','2'), ('role','vendor'),
                             ('target_id', OTHER), ('original_key', OTHER), ('occurred_at','2026-01-01T13:00:00.000Z')]:
            with self.subTest(field=field):
                self.sql('truncate event;')
                self.event(binding={**EFFECT, field:value})
                self.refuses('conflicting')

    def test_dangling_and_tombstoned(self):
        self.sql('truncate activity;')
        self.refuses('dangling')
        self.activity()
        self.sql('update refs set merged=true;')
        self.refuses('tombstoned')
        self.sql('update refs set merged=false; update party set deleted_at=now();')
        self.refuses('tombstoned')

    def test_source_lineage_and_lifecycle(self):
        self.sql(f"""update activity set email_context='{{"source_activity_id":"{OTHER}"}}';""")
        self.refuses('dangling')
        self.sql("""update activity set email_context='{"lifecycle":{"state":"deleted"}}';""")
        self.refuses('tombstoned')

    def test_windows(self):
        journal = {**JOURNAL, 'start':'2026-01-01T12:00:00Z', 'end':'2026-01-01T12:00:01Z'}
        self.assertEqual(self.command(journal)[0], 0)
        journal.update(start='2026-01-01T11:00:00Z', end='2026-01-01T12:00:00Z')
        self.refuses('out_of_window', journal=journal)
        journal = {**JOURNAL, 'recorded_start':'2026-01-02T00:00Z', 'recorded_end':'2026-01-02T00:01Z'}
        self.assertEqual(self.command(journal)[0], 0)
        journal.update(recorded_end='2026-01-02T00:00Z', recorded_start='2026-01-01T23:59Z')
        self.refuses('out_of_window', journal=journal)
        del journal['recorded_start']
        self.refuses('invalid_journal', journal=journal)

    def test_identity_and_release(self):
        self.refuses('principal_mismatch', journal={**JOURNAL, 'principal_slug':'other'})
        self.refuses('actor_mismatch', journal={**JOURNAL, 'actor_slug':'other'})
        self.refuses('release_mismatch', journal={**JOURNAL, 'release_sha':'b'*40})
        self.sql(f"update event set actor_id='{OTHER}';")
        self.refuses('missing_lineage')

    def test_bounds_and_empty_population(self):
        self.refuses('invalid_journal', journal={**JOURNAL, 'expected':[], 'expected_count':0})
        self.refuses('invalid_journal', journal={**JOURNAL, 'expected':[EFFECT]*501})
        self.refuses('duplicate_expectation', journal={**JOURNAL, 'expected':[EFFECT]*2, 'expected_count':2})
        self.sql(f"insert into event select '{OTHER}', 'log-activity','{ACTOR}','carr-internal',"
                 f"'2026-01-02', new_value from event cross join generate_series(1,1001);")
        self.refuses('row_cap')

    def test_read_only_write_attempt_fails(self):
        module = runner_module()
        import psycopg
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            module.begin_read_only(conn)
            self.assertEqual(conn.execute('show transaction_read_only').fetchone()[0], 'on')
            with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
                conn.execute("insert into actor values (gen_random_uuid(),'forbidden')")
            conn.rollback()


if __name__ == '__main__':
    unittest.main()
