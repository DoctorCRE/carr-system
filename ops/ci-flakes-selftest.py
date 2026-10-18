#!/usr/bin/env python3
"""Prove same-tree measurement and one fix loop per suite."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
import contextlib
import io
import importlib.util

REPO = Path(__file__).resolve().parents[1]
COLLECTOR = REPO / 'ops/ci-flakes.py'


class FlakeTests(unittest.TestCase):
    def test_measure_requires_later_pass_of_same_test_tree_and_workflow(self):
        observations = [
            {'test': 'ops/a-selftest.py', 'tree': 'aaa', 'workflow': 'CI', 'result': 'fail', 'at': 1, 'url': 'failure'},
            {'test': 'ops/a-selftest.py', 'tree': 'bbb', 'workflow': 'CI', 'result': 'pass', 'at': 2, 'url': 'wrong tree'},
            {'test': 'ops/a-selftest.py', 'tree': 'aaa', 'workflow': 'other', 'result': 'pass', 'at': 3, 'url': 'wrong workflow'},
            {'test': 'ops/b-selftest.py', 'tree': 'aaa', 'workflow': 'CI', 'result': 'pass', 'at': 4, 'url': 'wrong test'},
            {'test': 'ops/a-selftest.py', 'tree': 'aaa', 'workflow': 'CI', 'result': 'pass', 'at': 5, 'url': 'pass'},
        ]
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / 'observations.json'
            fixture.write_text(json.dumps(observations))
            result = subprocess.run([sys.executable, str(COLLECTOR), 'analyze', str(fixture)],
                capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            rows = json.loads(result.stdout)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['test'], 'ops/a-selftest.py')
            self.assertEqual(rows[0]['pass_url'], 'pass')

    def test_proposals_reuse_one_fix_loop_across_trees(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gh = root / 'gh'
            store = root / 'issues.json'
            store.write_text('[]')
            gh.write_text("#!/usr/bin/env python3\nimport json,os,sys\nfrom pathlib import Path\np=Path(os.environ['ISSUE_STORE']); rows=json.loads(p.read_text()); a=sys.argv[1:]\nif a[0]=='api' and '-X' not in a: print(json.dumps(rows))\nelif a[0]=='api' and a[a.index('-X')+1]=='POST':\n payload=json.loads(sys.stdin.read()); rows.append(dict(payload,number=len(rows)+1,html_url='https://github.com/jbookout/carr-system/issues/'+str(len(rows)+1),state='open')); p.write_text(json.dumps(rows)); print(json.dumps(rows[-1]))\nelse: raise SystemExit('unexpected gh arguments')\n")
            gh.chmod(0o755)
            env = {**os.environ, 'PATH': str(root) + os.pathsep + os.environ['PATH'], 'ISSUE_STORE': str(store)}
            proposals = root / 'proposals.json'
            for tree in ('aaa', 'bbb'):
                proposals.write_text(json.dumps([{'repo': 'jbookout/carr-system', 'test': 'ops/a-selftest.py',
                    'tree': tree, 'workflow': 'CI', 'fail_url': 'https://github.com/jbookout/carr-system/actions/runs/1',
                    'pass_url': 'https://github.com/jbookout/carr-system/actions/runs/2'}]))
                result = subprocess.run([sys.executable, str(COLLECTOR), 'propose', '--repo', 'jbookout/carr-system',
                    '--input', str(proposals)], env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(json.loads(store.read_text())), 1)
            self.assertIn('quarantine', json.loads(store.read_text())[0]['body'])

    def test_job_logs_bind_checkout_and_only_successful_class_passes(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('ci_flakes', COLLECTOR)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sha = 'a' * 40
        checkout = f'2026-10-05T00:00:00Z [command]/usr/bin/git log -1 --format=%H\n2026-10-05T00:00:00Z {sha}\n'
        run = {'id': 1, 'name': 'CI', 'head_sha': 'b' * 40, 'created_at': '2026-10-05T00:00:00Z'}
        rows = module.observations_from_logs('jbookout/carr-system', run, 1,
            {'gates.txt': checkout + 'FAIL gates selftest suites failed: a-selftest.py\n'}, ['ops/a-selftest.py'])
        self.assertEqual(rows[0]['source_sha'], sha)
        self.assertEqual(rows[0]['result'], 'fail')
        self.assertEqual(module.observations_from_logs('jbookout/carr-system', run, 2,
            {'gates.txt': checkout + 'FAIL gates dependency missing\n'}, ['ops/a-selftest.py']), [])
        passed = module.observations_from_logs('jbookout/carr-system', run, 2,
            {'gates.txt': checkout + 'OK gates 500 selftest suites + baseline integrity\n'}, ['ops/a-selftest.py'])
        self.assertEqual(passed, [])

    def test_candidate_receipts_are_bound_and_duplicates_collapse(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('ci_flakes', COLLECTOR)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sha = 'a' * 40
        checkout = f'2026-10-05T00:00:00Z git log -1 --format=%H\n2026-10-05T00:00:00Z {sha}\n'
        receipt = {'version': 1, 'test': 'ops/a-selftest.py', 'candidate': True, 'sha': sha,
            'tree': 'b' * 40, 'first_exit': 1, 'rerun_exit': 0, 'status': 'flake-candidate',
            'fingerprint': module.hashlib.sha256(b'').hexdigest()}
        event = {'id': 1, 'name': 'CI', 'head_repository': {'full_name': 'jbookout/carr-system'}}
        line = '2026-10-05T00:00:00Z CARR_FLAKE_RESULT ' + json.dumps(receipt) + '\n'
        def source_api(endpoint, *args, **kwargs):
            if '/git/commits/' in endpoint:
                return {'sha': sha, 'tree': {'sha': 'b' * 40}}
            return {'truncated': False, 'tree': [{'type': 'blob', 'path': 'ops/a-selftest.py'}]}
        with patch.object(module, 'gh_api', side_effect=source_api):
            rows = module.candidate_rows('jbookout/carr-system', event, 1, {'job.txt': checkout + line + line})
        self.assertEqual(len(rows), 1)
        receipt['sha'] = 'c' * 40
        wrong = 'CARR_FLAKE_RESULT ' + json.dumps(receipt)
        self.assertEqual(module.candidate_rows('jbookout/carr-system', event, 1, {'job.txt': checkout + wrong}), [])
        event['head_repository']['full_name'] = 'someone/fork'
        self.assertEqual(module.candidate_rows('jbookout/carr-system', event, 1, {'job.txt': checkout + line}), [])

    def test_mixed_node_outcomes_belong_only_to_the_named_suite(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('ci_flakes_mixed', COLLECTOR)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sha = 'a' * 40
        text = f'git log -1 --format=%H\n{sha}\n2026-10-05T00:00:01Z ok 1 a.test.mjs\n2026-10-05T00:00:02Z not ok 2 b.test.mjs\n'
        tests = ['mcp-server/test/a.test.mjs', 'mcp-server/test/b.test.mjs']
        run = {'id': 1, 'name': 'CI', 'created_at': '2026-10-05T00:00:00Z'}
        rows = module.observations_from_logs('jbookout/carr-system', run, 1, {'job.txt': text}, tests)
        self.assertEqual({r['test']: r['result'] for r in rows}, dict(zip(tests, ['pass', 'fail'])))
        self.assertEqual(module.failed_tests({'job.txt': text}, tests), [tests[1]])
        later = module.observations_from_logs('jbookout/carr-system', run, 2,
            {'job.txt': f'git log -1 --format=%H\n{sha}\n2026-10-05T00:01:00Z ok 1 b.test.mjs\n'}, tests)
        self.assertEqual([r['test'] for r in module.analyze(rows + later)], [tests[1]])


class ReviewRegressions(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('flake_review', COLLECTOR)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.repo = 'jbookout/carr-system'
        self.sha = 'a' * 40
        self.tree = 'b' * 40
        self.test = 'mcp-server/test/a.test.mjs'
        self.run = {'id': 1, 'name': 'CI', 'head_repository': {'full_name': self.repo},
            'status': 'completed', 'conclusion': 'success', 'run_attempt': 2,
            'workflow_id': 1, 'head_sha': self.sha, 'created_at': '2026-10-01T00:00:00Z',
            'updated_at': '2026-10-05T00:30:00Z'}
        self.checkout = f'git log -1 --format=%H\n{self.sha}\n'
        self.receipt = {'version': 1, 'test': self.test, 'candidate': True, 'sha': self.sha,
            'tree': self.tree, 'first_exit': 1, 'rerun_exit': 0, 'status': 'flake-candidate',
            'fingerprint': self.module.hashlib.sha256(b'').hexdigest()}

    def api(self, endpoint, *args, **kwargs):
        if '/git/commits/' in endpoint:
            return {'sha': self.sha, 'tree': {'sha': self.tree}}
        if '/git/trees/' in endpoint:
            return {'truncated': False, 'tree': [{'path': self.test, 'type': 'blob'},
                {'path': 'ops/a-selftest.py', 'type': 'blob'}, {'path': 'mcp-server/src/plain.js', 'type': 'blob'}]}
        raise AssertionError(endpoint)

    def log(self, receipt):
        return 'CARR_FLAKE_RESULT ' + json.dumps(receipt) + '\n'

    def test_malformed_receipts_do_not_discard_valid_candidate(self):
        invalid = [[], {'candidate': True, 'version': 1},
            {**self.receipt, 'tree': []}, {**self.receipt, 'test': []},
            {k: v for k, v in self.receipt.items() if k != 'test'}]
        with patch.object(self.module, 'gh_api', side_effect=self.api):
            rows = self.module.candidate_rows(self.repo, self.run, 2, {'job.txt': self.checkout +
                ''.join(self.log(row) for row in invalid) + self.log(self.receipt)})
        self.assertEqual([row['test'] for row in rows], [self.test])

    def test_receipts_require_every_field_and_preserve_other_candidates(self):
        invalid = [{key: value for key, value in self.receipt.items() if key != missing}
            for missing in self.receipt]
        with patch.object(self.module, 'gh_api', side_effect=self.api):
            rows = self.module.candidate_rows(self.repo, self.run, 2, {'job.txt': self.checkout +
                ''.join(self.log(row) for row in invalid) + self.log(self.receipt)})
        self.assertEqual([row['test'] for row in rows], [self.test])

    def test_source_inventory_includes_collected_nested_and_tool_selftest_suites(self):
        paths = ['tools/room-bridge/test_queue_unit.py', 'tools/room-bridge/test_activation_reliability.py',
                 'tools/example-selftest.py']
        def api(endpoint, *args, **kwargs):
            if '/git/trees/' in endpoint:
                return {'truncated': False, 'tree': [{'path': name, 'type': 'blob'} for name in paths]}
            return self.api(endpoint)
        with patch.object(self.module, 'gh_api', side_effect=api):
            rows = self.module.candidate_rows(self.repo, self.run, 2, {'job.txt': self.checkout +
                ''.join(self.log({**self.receipt, 'test': name}) for name in paths)})
        self.assertEqual(sorted(row['test'] for row in rows), sorted(paths))

    def test_explicit_passing_receipt_proves_only_its_suite_passed(self):
        passed = {**self.receipt, 'first_exit': 0, 'rerun_exit': None, 'candidate': False, 'status': 'passed'}
        rows = self.module.observations_from_logs(self.repo, self.run, 1,
            {'job.txt': self.checkout + self.log(passed) + 'OK unit all passed\n'},
            [self.test, 'mcp-server/test/unexecuted.test.mjs'])
        self.assertEqual({row['test']: row['result'] for row in rows}, {self.test: 'pass'})

    def test_candidates_bind_checkout_tree_and_collected_suite(self):
        for changes in ({'tree': 'c' * 40}, {'test': '/invented/test.py'},
                        {'test': 'ops/invented-selftest.py'}, {'test': 'mcp-server/src/plain.js'},
                        {'test': 'mcp-server/test/./a.test.mjs'}):
            with self.subTest(changes=changes), patch.object(self.module, 'gh_api', side_effect=self.api):
                rows = self.module.candidate_rows(self.repo, self.run, 1,
                    {'job.txt': self.checkout + self.log({**self.receipt, **changes})})
                self.assertEqual(rows, [])

    def test_propose_rejects_absolute_path_before_writing_issue(self):
        with patch.object(self.module, 'paged', return_value=[]), patch.object(self.module, 'gh_api') as api:
            with self.assertRaises(ValueError):
                self.module.propose(self.repo, [{'test': '/invented/test.py', 'tree': self.tree,
                    'fail_url': 'failure', 'pass_url': 'pass'}])
            api.assert_not_called()

    def test_reconcile_consumes_every_attempt_and_isolates_log_gaps(self):
        for missing in (False, True):
            calls = []
            def consume(repo, run_id, attempt, cache):
                calls.append((run_id, attempt))
                if missing and run_id == 1 and attempt == 1:
                    raise RuntimeError('archive unavailable')
                return []
            runs = [self.run, {**self.run, 'id': 2, 'run_attempt': 1}]
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as directory, \
                 patch.object(self.module, 'list_runs', return_value=runs), \
                 patch.object(self.module, 'consume', side_effect=consume), \
                 patch.object(sys, 'argv', ['ci-flakes', 'reconcile', '--repo', self.repo, '--cache', directory]), \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                result = self.module.main()
                self.assertEqual(calls, [(1, 1), (1, 2), (2, 1)])
                self.assertEqual(result, 1 if missing else 0)
                if missing:
                    self.assertIn('archive unavailable', output.getvalue())

    def test_reconcile_discovers_old_runs_with_recent_retry_activity(self):
        def api(endpoint, *args, **kwargs):
            # GitHub's created filter cannot return this old run's new attempt.
            return {'total_count': 0, 'workflow_runs': []} if 'created=' in endpoint else {
                'total_count': 1, 'workflow_runs': [self.run]}
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(self.module, 'gh_api', side_effect=api), \
             patch.object(self.module, 'consume', return_value=[]) as consume, \
             patch.object(self.module, 'datetime', wraps=datetime) as clock, \
             patch.object(sys, 'argv', ['ci-flakes', 'reconcile', '--repo', self.repo, '--cache', directory]), \
             contextlib.redirect_stdout(io.StringIO()):
            clock.now.return_value = datetime(2026, 10, 5, 1, tzinfo=timezone.utc)
            self.assertEqual(self.module.main(), 0)
            self.assertEqual([c.args[2] for c in consume.call_args_list], [1, 2])

    def test_history_records_download_timeout_as_log_gap(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(self.module, 'list_runs', return_value=[self.run]), \
             patch.object(self.module, 'read_logs', side_effect=subprocess.TimeoutExpired('gh', 120)):
            report = self.module.history(self.repo, datetime.now(timezone.utc),
                datetime.now(timezone.utc), Path(directory))
        self.assertEqual(len(report['log_gaps']), 2)
        self.assertEqual(report['rows'], [])

    def test_structured_receipts_attribute_node_spec_failures(self):
        other = 'mcp-server/test/b.test.mjs'
        failure = {**self.receipt, 'first_exit': 1, 'rerun_exit': 1, 'candidate': False, 'status': 'failed'}
        text = self.checkout + '✖ failure in a.test.mjs\nb.test.mjs referenced\n' + self.log(failure)
        rows = self.module.observations_from_logs(self.repo, self.run, 1, {'job.txt': text}, [self.test, other])
        self.assertEqual({r['test']: r['result'] for r in rows}, {self.test: 'fail'})
        self.assertEqual(self.module.failed_tests({'job.txt': text}, [self.test, other]), [self.test])

    def test_class_success_does_not_prove_quarantined_or_unexecuted_suite_passed(self):
        for first, status in ((1, 'quarantined-failure'), (78, 'failed'), (124, 'failed')):
            receipt = {**self.receipt, 'test': 'ops/a-selftest.py', 'first_exit': first,
                'rerun_exit': 1, 'candidate': False, 'status': status}
            text = self.checkout + self.log(receipt) + 'OK gates 500 selftests\n'
            with self.subTest(first=first):
                rows = self.module.observations_from_logs(self.repo, self.run, 1,
                    {'job.txt': text}, ['ops/a-selftest.py', 'ops/unexecuted-selftest.py'])
                self.assertFalse(any(r['result'] == 'pass' for r in rows))
        self.assertEqual(self.module.observations_from_logs(self.repo, self.run, 1,
            {'job.txt': self.checkout + 'OK gates 500 selftests\n'}, ['ops/a-selftest.py']), [])

    def test_history_preserves_offset_instants(self):
        report = {'rows': [], 'observations': [], 'log_gaps': []}
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(self.module, 'history', return_value=report) as history, \
             patch.object(sys, 'argv', ['ci-flakes', 'history', '--repo', self.repo,
                '--since', '2026-10-05T00:00:00-05:00', '--until', '2026-10-05T01:00:00-05:00',
                '--cache', directory, '--output', str(Path(directory) / 'report.json')]), \
             contextlib.redirect_stdout(io.StringIO()):
            self.module.main()
            self.assertEqual(history.call_args.args[1], datetime(2026, 10, 5, 5, tzinfo=timezone.utc))
            self.assertEqual(history.call_args.args[2], datetime(2026, 10, 5, 6, tzinfo=timezone.utc))


if __name__ == '__main__':
    unittest.main()
