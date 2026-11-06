"""Exercise the proof CLI with registered-handler responses from disposable Postgres."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / 'tools/prove-post-call-exact-edge-whitespace.py'


class ProofCLI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        fixture = Path(cls.folder.name) / 'response.json'
        contract_fixture = Path(cls.folder.name) / 'contract-response.json'
        result = subprocess.run([shutil.which('node'), '--test', 'mcp-server/test/post-call-followups.test.mjs'],
                                cwd=ROOT, env={**os.environ, 'POST_CALL_EDGE_FIXTURE_OUTPUT': str(fixture),
                                                          'POST_CALL_EDGE_CONTRACT_FIXTURE_OUTPUT': str(contract_fixture)},
                                capture_output=True, text=True, timeout=240)
        if result.returncode:
            cls.folder.cleanup()
            raise AssertionError(result.stdout + result.stderr)
        cls.response = json.loads(fixture.read_text())
        cls.contract_response = json.loads(contract_fixture.read_text())
        source = ROOT / 'mcp-server/src/release.js'
        module = Path(cls.folder.name) / 'release.mjs'
        module.write_text(f"import {{ buildRelease }} from {json.dumps(source.as_uri())};\n" +
                          "console.log(JSON.stringify(await buildRelease({env: {CARR_ENV: 'production', GIT_SHA: 'a'.repeat(40), CF_VERSION_METADATA: {id: 'fixture-version'}}, verbCount: 1, sql: async () => []})));\n")
        cls.release = json.loads(subprocess.check_output([shutil.which('node'), str(module)], text=True))

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    @contextmanager
    def fixture(self, mutate=None, mode='ok', change_release=False, invalid_release=False, root_mutate=None, release_mode='ok'):
        value = copy.deepcopy(self.response)
        if mutate:
            mutate(value['calls'][0]['open_cards'][0])
        if root_mutate:
            root_mutate(value)
        requests = []
        release = self.release

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                if self.path != '/release':
                    self.send_error(404)
                    return
                if self.headers.get('User-Agent') != 'doctorcre-production-smoke/1 (+ops/doctorcre-production-smoke.py)':
                    self.send_error(403)
                    return
                if release_mode == 'redirect':
                    self.send_response(302)
                    self.send_header('Location', '/redirect-target')
                    self.end_headers()
                    return
                if release_mode == 'timeout':
                    time.sleep(1)
                self.send_response(200)
                self.end_headers()
                reply = copy.deepcopy(release)
                if change_release and len(requests) > 1:
                    reply['worker_version']['id'] = 'different-version'
                if invalid_release:
                    reply['env']['value'] = 'staging'
                raw = json.dumps(reply).encode()
                if release_mode == 'oversized':
                    raw += b' ' * 1048576
                try:
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                folder = Path(directory)
                (folder / 'response.json').write_text(json.dumps(value))
                wrapper = folder / 'wrapper'
                wrapper.write_text('#!' + sys.executable + '\n' + '''import json, pathlib, sys, time
root = pathlib.Path(__file__).parent
with (root / 'calls').open('a') as log:
    log.write(json.dumps(sys.argv[1:]) + '\\n')
assert sys.argv[1:] == ['call', 'read-call-outcomes', '{}']
''' + {
                    'ok': "print((root / 'response.json').read_text())\n",
                    'refusal': "print('TOOL ERROR ' + json.dumps({'error': 'forbidden', 'hint': 'PRIVATE'}), file=sys.stderr)\nsys.exit(1)\n",
                    'transport': "print('PRIVATE transport', file=sys.stderr)\nsys.exit(1)\n",
                    'timeout': 'time.sleep(3)\n',
                    'oversized': "sys.stdout.write('x' * 1048577)\n",
                }[mode])
                wrapper.chmod(0o700)
                yield folder, wrapper, f'http://127.0.0.1:{server.server_port}', requests
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def run_cli(self, expected, **kwargs):
        with self.fixture(**kwargs) as (folder, wrapper, origin, requests):
            result = subprocess.run([sys.executable, str(RUNNER), '--origin', origin, '--call-wrapper', str(wrapper),
                                     '--timeout-seconds', '0.5' if kwargs.get('mode') == 'timeout' or kwargs.get('release_mode') == 'timeout' else '30'],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
            receipt = json.loads(result.stdout)
            print(json.dumps({'exit': result.returncode, **receipt}, sort_keys=True))
            self.assertEqual(receipt['schema'], 'post-call-exact-edge-whitespace-proof.v1')
            self.assertLessEqual(receipt['record_reads'], 1)
            self.assertNotIn('PRIVATE', result.stdout + result.stderr)
            self.assertNotIn('Synthetic', result.stdout + result.stderr)
            self.assertNotIn('description', result.stdout)
            calls = (folder / 'calls').read_text().splitlines() if (folder / 'calls').exists() else []
            self.assertLessEqual(len(calls), 1)
            self.assertLessEqual(len(requests), 2)
            self.assertTrue(all(path == '/release' for path in requests))
            return receipt

    def test_release_transport_preserves_refusals(self):
        for mode in ['redirect', 'oversized', 'timeout']:
            with self.subTest(mode=mode):
                receipt = self.run_cli(78, release_mode=mode)
                self.assertEqual(receipt['status'], 'timeout' if mode == 'timeout' else 'release_unavailable')
                self.assertEqual(receipt['record_reads'], 0)

    def test_registered_exact_cohort_passes(self):
        receipt = self.run_cli(0)
        self.assertEqual(receipt['eligible_cohorts'], 1)
        self.assertEqual(receipt['assertions'], {'exact_identity_and_owner': True, 'preserved_date_context': True})

    def test_registered_unrelated_owners_and_postgres_dates_pass(self):
        from unittest.mock import patch
        with patch.object(self, 'response', self.contract_response):
            receipt = self.run_cli(0)
        self.assertEqual(receipt['inspected_cards'], 2)
        self.assertEqual(receipt['eligible_cohorts'], 1)
        self.assertTrue(all(receipt['assertions'].values()))

    def test_malformed_task_owner_and_date_are_unavailable(self):
        for change in [{'owner': None}, {'owner': 7}, {'due_on': 'not-a-date'}, {'due_on': '2027-02-30'}]:
            self.assertEqual(self.run_cli(78, mutate=lambda c: c['existing_action_comparison']['exact_match'].update(change))['status'], 'invalid_contract')

    def test_related_exact_cohort_is_contradiction(self):
        def related(card):
            comparison = card['existing_action_comparison']
            comparison.update(status='related_open_tasks', related_open_tasks=[comparison['exact_match']],
                              related_open_task_total=1, exact_match=None, date_effect=None)
        self.assertEqual(self.run_cli(1, mutate=related)['status'], 'contradiction')

    def test_incorrect_date_is_contradiction(self):
        self.run_cli(1, mutate=lambda c: c['existing_action_comparison']['date_effect'].update(resulting_due_on='2028-01-01'))

    def test_missing_cohort_is_unavailable(self):
        def missing(card):
            card['existing_action_comparison'].update(status='no_match', exact_match=None, date_effect=None)
        self.assertEqual(self.run_cli(78, mutate=missing)['status'], 'natural_cohort_unavailable')

    def test_different_owner_or_text_cannot_prove(self):
        for change in [{'owner': 'dell'}, {'description': 'Different document for another recipient'}]:
            self.run_cli(78, mutate=lambda c: c['existing_action_comparison']['exact_match'].update(change))

    def test_plain_spaces_and_closed_tasks_are_not_regression_evidence(self):
        self.run_cli(78, mutate=lambda c: c['existing_action_comparison']['exact_match'].update(description='  REVIEW SYNTHETIC SURVEY  '))
        self.run_cli(78, mutate=lambda c: c['existing_action_comparison']['exact_match'].update(status='done'))

    def test_invalid_contract_is_unavailable(self):
        self.run_cli(78, mutate=lambda c: c['existing_action_comparison'].update(schema_version='v1'))
        self.run_cli(78, mutate=lambda c: c['payload'].update(due_on='not-a-date'))
        self.run_cli(78, invalid_release=True)

    def test_release_change_refuses_pass(self):
        self.assertEqual(self.run_cli(78, change_release=True)['status'], 'release_changed')

    def test_refusal_differs_from_transport_failure(self):
        self.assertEqual(self.run_cli(78, mode='refusal')['status'], 'declared_refusal')
        self.assertEqual(self.run_cli(78, mode='transport')['status'], 'transport_failure')

    def test_response_shape_and_iteration_caps(self):
        self.run_cli(78, root_mutate=lambda v: v.update(send=True))
        self.run_cli(78, root_mutate=lambda v: v.update(calls=[{'open_cards': []}] * 51))
        self.run_cli(78, root_mutate=lambda v: v.update(calls=[{'open_cards': [{'kind': 'email_draft'}] * 1001}]))

    def test_timeout_and_output_cap(self):
        self.assertEqual(self.run_cli(78, mode='timeout')['status'], 'timeout')
        self.assertEqual(self.run_cli(78, mode='oversized')['status'], 'response_too_large')


if __name__ == '__main__':
    unittest.main()
