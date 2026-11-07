"""Replay actual registered responses through the proof's production parser."""
import copy
import contextlib
import io
import importlib.util
import json
import http.server
import signal
import threading
import time
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ops'))
from git_env import fixture_env

RUNNER = ROOT / 'tools/prove-quiet-follow-up-owner-filter.py'
spec = importlib.util.spec_from_file_location('quiet_owner_proof', RUNNER)
assert spec is not None and spec.loader is not None
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)


class OwnerProofTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repository = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.repository.cleanup)
        cls.git_root = Path(cls.repository.name)
        def git(*args, input=None):
            return subprocess.run(['git', '-c', 'user.name=Proof test', '-c',
                'user.email=proof@example.invalid', *args], cwd=cls.git_root,
                input=input, text=True, capture_output=True, check=True, timeout=5,
                env=fixture_env()).stdout.strip()
        git('init', '-q')
        tree = git('mktree', input='')
        cls.older = git('commit-tree', tree, input='older')
        cls.merge = git('commit-tree', tree, '-p', cls.older, input='merge')
        cls.descendant = git('commit-tree', tree, '-p', cls.merge, input='descendant')
        cls.unrelated = git('commit-tree', tree, input='unrelated')
        cls.recording = json.loads((ROOT / 'tools/fixtures/quiet-follow-up-owner-filter.json').read_text())

    def run_fixture(self, value):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'recording.json'
            path.write_text(json.dumps(value))
            result = subprocess.run([sys.executable, str(RUNNER), '--fixture', str(path)],
                                    text=True, capture_output=True, timeout=10)
            return result.returncode, json.loads(result.stdout)

    def test_registered_recording_passes_response_contract_and_cli(self):
        for key in ('all', 'owned', 'unassigned'):
            proof.response(self.recording[key])
        self.assertEqual(self.run_fixture(self.recording)[0], 0)

    def test_date_contract_refuses_timestamps_and_invalid_days(self):
        for field in ('anchor_on', 'due_on'):
            for day in ('2000-02-29T06:00:00.000Z', '2000-02-30', '2000-2-9', None):
                with self.subTest(field=field, day=day):
                    value = copy.deepcopy(self.recording)
                    value['all']['proposals'][0][field] = day
                    self.assertEqual(self.run_fixture(value)[0], 78)

    def test_empty_unassigned_subset_passes_with_owned_evidence(self):
        for owned_only in (False, True):
            with self.subTest(owned_only=owned_only):
                value = copy.deepcopy(self.recording)
                value['all']['proposals'] = [row for row in value['all']['proposals']
                    if row['owner_id'] is not None
                    and (not owned_only or row['owner'] == value['owner_slug'])]
                value['unassigned']['proposals'] = []
                code, result = self.run_fixture(value)
                self.assertEqual(code, 0, result)
                self.assertEqual(result['counts']['unassigned'], 0)

    def test_empty_unassigned_subset_refuses_missing_owned_evidence(self):
        value = copy.deepcopy(self.recording)
        value['all']['proposals'] = [row for row in value['all']['proposals']
            if row['owner_id'] is not None and row['owner'] != value['owner_slug']]
        value['owned']['proposals'] = []
        value['unassigned']['proposals'] = []
        code, result = self.run_fixture(value)
        self.assertEqual((code, result['reason']), (78, 'owner_cohorts_unavailable'))

    def test_empty_unassigned_baseline_rejects_extra_unassigned_rows(self):
        value = copy.deepcopy(self.recording)
        value['all']['proposals'] = [row for row in value['all']['proposals']
            if row['owner_id'] is not None]
        code, result = self.run_fixture(value)
        self.assertEqual((code, result['reason']), (1, 'unassigned_subset_changed'))

    def test_subset_assertion_failure_exits_one(self):
        value = copy.deepcopy(self.recording)
        value['owned']['proposals'] = []
        code, result = self.run_fixture(value)
        self.assertEqual((code, result['reason']), (1, 'owned_subset_changed'))

    def test_flags_cadence_order_and_unassigned_contradictions(self):
        for mode in ('sent', 'cadence', 'order', 'unassigned'):
            with self.subTest(mode=mode):
                value = copy.deepcopy(self.recording)
                if mode == 'sent': value['all']['sent'] = True
                if mode == 'cadence': value['owned']['cadence']['lead_days'] = 1
                if mode == 'order': value['all']['proposals'].reverse()
                if mode == 'unassigned': value['unassigned']['proposals'] = []
                self.assertEqual(self.run_fixture(value)[0], 1)

    def test_insufficient_contract_or_cohorts_exit_78(self):
        for mode in ('missing', 'shape', 'cohorts', 'provenance'):
            value = copy.deepcopy(self.recording)
            if mode == 'missing': del value['owned']
            if mode == 'shape': value['owned'] = {'ok': True}
            if mode == 'cohorts': value['all']['proposals'] = []
            if mode == 'provenance': value['provenance']['query']['owned'] = {}
            self.assertEqual(self.run_fixture(value)[0], 78)


    def run_production(self, release_sha, value=None, evidence_sha=None):
        from lib.release_reader import Reply
        value = copy.deepcopy(self.recording if value is None else value)
        value['provenance'].update(release_sha=evidence_sha or release_sha,
            transport='signed-in-app-origin/mcp', app_origin='https://app.doctorcre.com')
        release = {'ok': True, 'env': {'value': 'production'}, 'git_sha': {'value': release_sha}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'evidence.json'
            path.write_text(json.dumps(value))
            output = io.StringIO()
            with patch.object(proof, 'ROOT', self.git_root, create=True), patch.dict(
                    'os.environ', fixture_env(), clear=True), patch.object(proof,
                    'release_read', return_value=Reply(200, {}, json.dumps(release).encode())), \
                    contextlib.redirect_stdout(output):
                code = proof.main(['--evidence', str(path), '--merge-sha', self.merge])
            return code, json.loads(output.getvalue())

    def test_release_containment_matrix(self):
        for case, sha, expected in [('equal', self.merge, 0),
                ('descendant', self.descendant, 0), ('older', self.older, 78),
                ('unrelated', self.unrelated, 78), ('unknown', 'f' * 40, 78)]:
            with self.subTest(case=case):
                code, result = self.run_production(sha)
                self.assertEqual(code, expected, result)
                self.assertEqual(result['status'], 'passed' if expected == 0 else 'insufficient-evidence')

    def test_http_inactivity_error_is_insufficient(self):
        previous = signal.getsignal(signal.SIGALRM)
        with patch.object(proof, 'release_read', side_effect=TimeoutError('timed out')), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            code = proof.main(['--merge-sha', self.merge])
        self.assertEqual((code, json.loads(output.getvalue())['reason']), (78, 'timed out'))
        self.assertEqual(signal.getsignal(signal.SIGALRM), previous)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))

    def test_equal_unknown_release_is_insufficient(self):
        with patch.object(self, 'merge', 'f' * 40):
            code, result = self.run_production('f' * 40)
        self.assertEqual(code, 78, result)

    def test_http_deadline_interrupts_progress_and_inactivity(self):
        for phase in ('body', 'headers', 'inactive'):
            with self.subTest(phase=phase):
                stopped = threading.Event()
                class Handler(http.server.BaseHTTPRequestHandler):
                    def log_message(self, *args):
                        pass

                    def do_GET(self):
                        self.send_response(200)
                        self.send_header('Content-Length', '100')
                        try:
                            if phase == 'headers':
                                self.flush_headers()
                            else:
                                self.end_headers()
                            for _ in range(30):
                                if phase == 'body':
                                    self.wfile.write(b' ')
                                    self.wfile.flush()
                                elif phase == 'headers':
                                    self.wfile.write(b'X-Progress: yes\r\n')
                                    self.wfile.flush()
                                if stopped.wait(0.05):
                                    return
                        except (BrokenPipeError, ConnectionResetError):
                            pass

                server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
                thread = threading.Thread(target=server.serve_forever)
                thread.start()
                previous = signal.getsignal(signal.SIGALRM)
                output = io.StringIO()
                start = time.monotonic()
                try:
                    with patch.object(proof, 'PROOF_BUDGET_SECONDS', 0.3, create=True), \
                            contextlib.redirect_stdout(output):
                        code = proof.main(['--origin', f'http://127.0.0.1:{server.server_port}',
                            '--merge-sha', self.merge, '--evidence', str(
                                ROOT / 'tools/fixtures/quiet-follow-up-owner-filter.json')])
                    elapsed = time.monotonic() - start
                    result = json.loads(output.getvalue())
                    self.assertEqual((code, result['reason']), (78, 'deadline'))
                    self.assertLess(elapsed, 0.9)
                    self.assertEqual(signal.getsignal(signal.SIGALRM), previous)
                    self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
                finally:
                    stopped.set()
                    server.shutdown()
                    server.server_close()
                    thread.join()

    def test_evidence_must_match_observed_containing_release(self):
        code, result = self.run_production(self.descendant, evidence_sha=self.merge)
        self.assertEqual((code, result['reason']),
            (78, 'signed_in_reads_or_merge_identity_unavailable'))

    def test_ancestry_timeout_is_insufficient_evidence(self):
        with patch.object(proof.subprocess, 'run', side_effect=subprocess.TimeoutExpired('git', 1)):
            code, result = self.run_production(self.descendant)
        self.assertEqual((code, result['status']), (78, 'insufficient-evidence'))

    def test_containing_release_subset_failure_exits_one(self):
        value = copy.deepcopy(self.recording)
        value['owned']['proposals'] = []
        code, result = self.run_production(self.descendant, value)
        self.assertEqual((code, result['reason']), (1, 'owned_subset_changed'))

    def test_identity_only_is_insufficient(self):
        from lib.release_reader import Reply
        release = {'ok': True, 'env': {'value': 'production'}, 'git_sha': {'value': 'a' * 40}}
        with patch.object(proof, 'release_read', return_value=Reply(200, {}, json.dumps(release).encode())) as read:
            self.assertEqual(proof.main(['--origin', 'https://api.doctorcre.com']), 78)
            read.assert_called_once()


if __name__ == '__main__':
    unittest.main()
