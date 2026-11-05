"""Worker lane receipt transitions, independent of aggregate workflow state."""
import copy
import importlib.util
import subprocess
import tempfile
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.worker_release_receipt import seal, discover, github, REPOSITORY

SHA = 'a' * 40
VERSION = '11111111-1111-4111-8111-111111111111'
LIVE = {'git_sha': {'value': SHA}, 'worker_version': {'id': VERSION},
        'env': {'value': 'production'}, 'verb_count': 410}
RUN = {'id': 42, 'run_attempt': 1, 'path': '.github/workflows/deploy.yml',
       'event': 'workflow_run', 'head_branch': 'main', 'status': 'in_progress',
       'conclusion': None, 'repository': {'full_name': REPOSITORY},
       'head_repository': {'full_name': REPOSITORY}}


def archive(receipt):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as output:
        output.writestr('worker-release.json', json.dumps(receipt))
    return data.getvalue()


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.receipt = seal(LIVE, SHA, VERSION, 410, 42, 1)
        self.run = copy.deepcopy(RUN)
        self.artifact = {'id': 7, 'name': f'worker-release-{SHA}',
                         'expired': False, 'size_in_bytes': 1000,
                         'workflow_run': {'id': 42}}
        self.calls = []

    def api(self, path, *, binary=False):
        self.calls.append(path)
        if binary:
            return archive(self.receipt)
        if '/workflows/deploy.yml/runs?' in path:
            return {'workflow_runs': [self.run]}
        if '/artifacts?' in path:
            return {'artifacts': [self.artifact]}
        return self.run

    def test_lane_receipt_accepted_while_parent_waits(self):
        got = discover(LIVE, api=self.api)
        self.assertEqual(got['source_sha'], SHA)
        self.assertEqual(got['run_id'], 42)
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(self.run['status'], 'in_progress')

    def test_parent_title_and_final_conclusion_are_irrelevant(self):
        for status, conclusion in [('in_progress', None), ('completed', 'failure')]:
            self.run.update(status=status, conclusion=conclusion, display_title='unrelated')
            self.assertEqual(discover(LIVE, api=self.api)['source_sha'], SHA)

    def test_wrong_release_and_unavailable_identity_refuse(self):
        for live in ({}, {**LIVE, 'git_sha': {'value': 'b' * 40}},
                     {**LIVE, 'worker_version': {'id': None}},
                     {**LIVE, 'env': {'value': 'staging'}}):
            with self.subTest(live=live), self.assertRaises(ValueError):
                discover(live, api=self.api)
        for field, value in [('source_sha', 'b' * 40), ('worker_version', 'wrong'),
                             ('run_id', 43), ('run_attempt', 2), ('environment', 'staging'),
                             ('repository', 'other/repo'), ('schema', 'unknown')]:
            original = self.receipt
            self.receipt = {**original, field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                discover(LIVE, api=self.api)
            self.receipt = original

    def test_untrusted_run_or_expired_artifact_refuses(self):
        for field, value in [('path', '.github/workflows/ci.yml'), ('event', 'pull_request'),
                             ('head_branch', 'feature'), ('run_attempt', 2),
                             ('head_repository', {'full_name': 'fork/repo'})]:
            self.run = {**RUN, field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                discover(LIVE, api=self.api)
        self.run = copy.deepcopy(RUN)
        self.artifact['expired'] = True
        with self.assertRaises(ValueError):
            discover(LIVE, api=self.api)

    def test_missing_or_oversized_archive_refuses_and_request_cap(self):
        for payload in (b'not zip', b'x' * 65537, archive({'wrong': True})):
            def api(path, *, binary=False):
                return payload if binary else self.api(path)
            with self.assertRaises(ValueError):
                discover(LIVE, api=api)
        with self.assertRaises(ValueError):
            discover(LIVE, api=lambda *a, **k: {'workflow_runs': []})
        self.artifact['expired'] = True
        self.calls.clear()
        def many(path, *, binary=False):
            if '/artifacts?' in path:
                return {'artifacts': [self.artifact] * 20}
            return self.api(path, binary=binary)
        with self.assertRaises(ValueError):
            discover(LIVE, api=many)
        self.assertEqual(len(self.calls), 1)

    def test_transport_has_deadline_and_response_byte_cap(self):
        with patch('lib.worker_release_receipt.subprocess.run', return_value=Mock(stdout=b'{}')) as run:
            self.assertEqual(github('repos/example'), {})
            self.assertEqual(run.call_args.kwargs['timeout'], 20)
            self.assertEqual(run.call_args.args[0], ['gh', 'api', 'repos/example'])
        with patch('lib.worker_release_receipt.subprocess.run', return_value=Mock(stdout=b'x' * 65537)):
            with self.assertRaises(ValueError):
                github('repos/example', binary=True)

    def test_discovery_checks_at_most_two_candidates(self):
        self.receipt['source_sha'] = 'b' * 40
        def many(path, *, binary=False):
            if '/artifacts?' in path:
                return {'artifacts': [self.artifact] * 20}
            return self.api(path, binary=binary)
        with self.assertRaises(ValueError):
            discover(LIVE, api=many)
        self.assertEqual(len(self.calls), 3)

    def test_bad_archive_does_not_shadow_valid_sibling(self):
        def api(path, *, binary=False):
            if '/artifacts?' in path:
                return {'artifacts': [{**self.artifact, 'id': 8}, self.artifact]}
            if path.endswith('/artifacts/8/zip'):
                return b'broken archive'
            return self.api(path, binary=binary)
        self.assertEqual(discover(LIVE, api=api)['artifact_id'], 7)

    def test_trusted_producer_budget_and_full_request_cap(self):
        calls = []
        def api(path, *, binary=False):
            calls.append(path)
            if '/workflows/' in path:
                return {'workflow_runs': [RUN, {**RUN, 'id': 43}] * 10}
            if '/artifacts?' in path:
                run_id = 42 if '/runs/42/' in path else 43
                return {'artifacts': [{**self.artifact, 'id': 8, 'workflow_run': {'id': run_id}},
                                      {**self.artifact, 'workflow_run': {'id': run_id}}] * 10}
            if binary:
                return archive(seal(LIVE, SHA, VERSION, 410, 43, 1)) if len(calls) == 7 else b'bad'
            return {**RUN, 'id': 43}
        self.assertEqual(discover(LIVE, api=api)['run_id'], 43)
        self.assertEqual(len(calls), 8)

    def test_changed_producer_attempt_refuses_current_receipt(self):
        def api(path, *, binary=False):
            if path.endswith('/runs/42'):
                return {**RUN, 'run_attempt': 2}
            return self.api(path, binary=binary)
        with self.assertRaisesRegex(ValueError, 'producer changed'):
            discover(LIVE, api=api)

    def test_receipt_numbers_must_not_accept_boolean_or_float_aliases(self):
        self.receipt['run_attempt'] = True
        with self.assertRaises(ValueError):
            discover(LIVE, api=self.api)
        self.receipt['run_attempt'] = 1.0
        with self.assertRaises(ValueError):
            discover(LIVE, api=self.api)

    def test_sealer_refuses_wrong_verified_release(self):
        for sha, version, count in [('b' * 40, VERSION, 410), (SHA, 'wrong', 410), (SHA, VERSION, 0)]:
            with self.assertRaises(ValueError):
                seal(LIVE, sha, version, count, 42, 1)


class DiscoveryRecoveryTests(unittest.TestCase):
    def setUp(self):
        ReceiptTests.setUp(self)
        from lib.studio_runtime_auto_refresh import Host
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.host = object.__new__(Host)
        self.host.worker_receipt = None
        self.host.recover = Mock()
        self.host.alert = Mock()
        self.host.installed = Mock(return_value='b' * 40)
        self.host.verify_current = Mock()
        self.host.refresh = Mock(side_effect=lambda sha: setattr(self.host.installed, 'return_value', sha))
        self.live = LIVE
        self.payload = None

    def transport(self, path, *, binary=False):
        self.calls.append(path)
        base = f'repos/{REPOSITORY}/actions'
        if path == f'{base}/artifacts?name=worker-release-{SHA}&per_page=2':
            untrusted = {**self.artifact, 'id': 8, 'workflow_run': {'id': 99}}
            return {'total_count': 3, 'artifacts': [untrusted, untrusted]}
        if path == f'{base}/workflows/deploy.yml/runs?branch=main&per_page=2':
            return {'workflow_runs': [self.run]}
        if path == f'{base}/runs/42/artifacts?name=worker-release-{SHA}&per_page=2':
            return {'artifacts': [self.artifact]}
        if path == f'{base}/runs/42':
            return self.run
        if path == f'{base}/runs/99':
            return {**RUN, 'id': 99, 'event': 'pull_request', 'head_repository': {'full_name': 'fork/repo'}}
        if path == f'{base}/artifacts/7/zip' and binary:
            return self.payload if self.payload is not None else archive(self.receipt)
        raise AssertionError(f'unexpected GitHub request {path}')

    def tick(self, now):
        from types import SimpleNamespace
        from lib.studio_runtime_auto_refresh import tick
        with patch('lib.studio_runtime_auto_refresh.read', return_value=SimpleNamespace(
                status=200, body=json.dumps(self.live).encode())), patch(
                'lib.studio_runtime_auto_refresh.discover', side_effect=lambda live: discover(live, api=self.transport)):
            return tick(self.root, self.host, now=now)

    def test_untrusted_artifacts_cannot_shadow_valid_receipt_or_open_tick(self):
        self.assertEqual(self.tick(10000), 'refreshed')
        self.assertEqual(self.tick(10300), 'current')
        self.assertEqual(self.tick(10600), 'current')
        self.host.alert.assert_not_called()
        self.host.refresh.assert_called_once_with(SHA)
        self.assertEqual(json.loads((self.root / 'state.json').read_text())['discovery_failures'], 0)

    def test_requested_refresh_while_parent_is_in_progress(self):
        self.assertEqual(self.run['status'], 'in_progress')
        self.assertEqual(self.tick(10000), 'refreshed')
        row = json.loads((self.root / 'runs.jsonl').read_text().splitlines()[-1])
        self.assertEqual(row['installed_sha'], SHA)
        self.assertEqual(row['worker_receipt']['run_id'], 42)
        self.assertEqual(self.run['status'], 'in_progress')

    def test_unavailable_identity_names_reason_and_retries_without_blind_refresh(self):
        self.live = {}
        self.assertEqual(self.tick(10000), 'refused')
        self.host.refresh.assert_not_called()
        self.assertEqual(self.calls, [])
        row = json.loads((self.root / 'runs.jsonl').read_text().splitlines()[-1])
        self.assertEqual(row['reason'], 'production identity unavailable')
        self.assertEqual(self.tick(10001), 'wait')
        self.live = LIVE
        self.assertEqual(self.tick(10300), 'refreshed')

    def test_two_same_cause_failures_open_and_next_valid_receipt_recovers(self):
        self.payload = b'broken archive'
        self.assertEqual(self.tick(10000), 'refused')
        self.assertEqual(self.tick(10300), 'open')
        self.host.refresh.assert_not_called()
        self.host.alert.assert_called_once_with(None, 10300)
        calls = len(self.calls)
        self.assertEqual(self.tick(10301), 'wait')
        self.assertEqual(len(self.calls), calls)
        self.payload = None
        self.assertEqual(self.tick(10600), 'refreshed')
        self.assertEqual(json.loads((self.root / 'state.json').read_text())['discovery_failures'], 0)

    def test_reader_never_consults_overall_success_or_title(self):
        class LaneMetadata(dict):
            def get(self, key, *args):
                if key in {'status', 'conclusion', 'display_title', 'displayTitle', 'name'}:
                    raise AssertionError(f'aggregate metadata read {key}')
                return super().get(key, *args)
            def __getitem__(self, key):
                if key in {'status', 'conclusion', 'display_title', 'displayTitle', 'name'}:
                    raise AssertionError(f'aggregate metadata read {key}')
                return super().__getitem__(key)
        self.run = LaneMetadata(self.run)
        self.assertEqual(self.tick(10000), 'refreshed')

    def test_half_open_failed_probe_remains_open_and_rate_limited(self):
        self.live = {}
        self.assertEqual([self.tick(n) for n in (10000, 10300, 10600, 10601)],
                         ['refused', 'open', 'open', 'wait'])
        self.host.refresh.assert_not_called()
        self.assertEqual(json.loads((self.root / 'state.json').read_text())['discovery_failures'], 2)


class ReadbackTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / 'tools/studio-release-readback.py'
        spec = importlib.util.spec_from_file_location('studio_readback', path)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'out/studio-runtime-auto-refresh/runs.jsonl'
        self.path.parent.mkdir(parents=True)
        self.worker = {**seal(LIVE, SHA, VERSION, 410, 42, 1), 'artifact_id': 7}
        self.row = {'status': 'refreshed', 'sha': SHA, 'installed_sha': SHA,
                    'at': '2026-11-05T00:00:00Z', 'worker_receipt': self.worker,
                    'installation_started_at': '2026-11-05T00:00:00Z',
                    'installation_completed_at': '2026-11-05T00:00:01Z'}
        self.path.write_text(json.dumps(self.row) + '\n')
        self.host = Mock()
        self.host.installed.return_value = SHA

    def test_readback_observes_installed_identity_while_parent_waits(self):
        got = self.module.observe(self.root, self.host, self.worker, RUN)
        self.assertEqual(got['installed_sha'], SHA)
        self.assertEqual(got['parent_status'], 'in_progress')
        self.assertFalse(got['canonical_proof'])
        self.host.verify_current.assert_called_once_with(SHA)

    def test_wrong_parent_installed_identity_or_report_refuses(self):
        for parent in ({**RUN, 'status': 'completed'}, {**RUN, 'run_attempt': 2}, {**RUN, 'id': 99}):
            with self.assertRaises(ValueError):
                self.module.observe(self.root, self.host, self.worker, parent)
        self.host.installed.return_value = 'b' * 40
        with self.assertRaises(ValueError):
            self.module.observe(self.root, self.host, self.worker, RUN)
        self.host.installed.return_value = SHA
        for row in ({**self.row, 'status': 'failed'}, {**self.row, 'status': 'current'}, {**self.row, 'installed_sha': 'b' * 40},
                    {**self.row, 'worker_receipt': {**self.worker, 'artifact_id': 8}}):
            self.path.write_text(json.dumps(row) + '\n')
            with self.assertRaises(ValueError):
                self.module.observe(self.root, self.host, self.worker, RUN)

    def test_cli_brackets_local_evidence_with_same_waiting_parent(self):
        order = []
        def parent(path):
            order.append('parent')
            return RUN
        self.host.verify_current.side_effect = lambda sha: order.append('installed')
        with patch.object(self.module, 'released_receipt', return_value=self.worker), \
                patch.object(self.module, 'Host', return_value=self.host), \
                patch.object(self.module, 'github', side_effect=parent), \
                patch.object(sys, 'argv', ['studio-release-readback.py', '--source', str(self.root)]), \
                patch('builtins.print') as output:
            self.module.main()
        self.assertEqual(order, ['parent', 'installed', 'parent'])
        self.assertEqual(json.loads(output.call_args.args[0])['parent_status'], 'in_progress')

    def test_parent_completion_during_local_readback_refuses(self):
        with patch.object(self.module, 'released_receipt', return_value=self.worker), \
                patch.object(self.module, 'Host', return_value=self.host), \
                patch.object(self.module, 'github', side_effect=[RUN, {**RUN, 'status': 'completed'}]), \
                patch.object(sys, 'argv', ['studio-release-readback.py', '--source', str(self.root)]), \
                patch('builtins.print'):
            with self.assertRaises(ValueError):
                self.module.main()

    def test_parent_attempt_change_during_local_readback_refuses(self):
        with patch.object(self.module, 'released_receipt', return_value=self.worker), \
                patch.object(self.module, 'Host', return_value=self.host), \
                patch.object(self.module, 'github', side_effect=[RUN, {**RUN, 'run_attempt': 2}]), \
                patch.object(sys, 'argv', ['studio-release-readback.py', '--source', str(self.root)]), \
                patch('builtins.print'):
            with self.assertRaises(ValueError):
                self.module.main()

    def test_readback_bounds_local_report_records(self):
        self.path.write_text(json.dumps(self.row) + '\n' +
                             (json.dumps({**self.row, 'status': 'current'}) + '\n') * 100)
        with self.assertRaises(ValueError):
            self.module.observe(self.root, self.host, self.worker, RUN)

    def test_producer_cli_in_clean_home_seals_verified_readback(self):
        root = Path(__file__).resolve().parents[1]
        live = self.root / 'release.json'
        live.write_text(json.dumps(LIVE))
        output = self.root / 'worker-release.json'
        args = [sys.executable, str(root / 'tools/worker-release-receipt.py'),
                '--release', str(live), '--sha', SHA, '--version', VERSION, '--verbs', '410',
                '--run-id', '42', '--attempt', '1', '--output', str(output)]
        result = subprocess.run(args, cwd=self.root, env={'HOME': str(self.root), 'PATH': '/usr/bin:/bin'},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(output.read_text()), seal(LIVE, SHA, VERSION, 410, 42, 1))
        output.unlink()
        live.write_text(json.dumps({**LIVE, 'git_sha': {'value': 'b' * 40}}))
        result = subprocess.run(args, cwd=self.root, env={'HOME': str(self.root), 'PATH': '/usr/bin:/bin'},
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
