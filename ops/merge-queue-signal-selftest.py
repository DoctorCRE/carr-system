#!/usr/bin/env python3
"""Exercise queue status binding without GitHub writes."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('signal_tool', Path(__file__).with_name('merge-queue-signal.py'))
assert spec is not None and spec.loader is not None
signal_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(signal_tool)


class QueueSignal(unittest.TestCase):
    def test_failure_is_bound_to_the_queued_pr_head(self):
        calls = []
        def api(path, payload=None):
            calls.append((path, payload))
            if path == 'pulls/123':
                return {'head': {'sha': 'a' * 40}}
            if path.startswith('compare/'):
                return {'status': 'ahead'}
            return {}
        signal_tool.publish({'merge_group': {'head_ref': 'refs/heads/gh-readonly-queue/main/pr-123-' + 'b' * 40,
                                              'head_sha': 'c' * 40}}, 'failure', 'https://github.com/o/r/actions/runs/1', api)
        self.assertEqual(calls[-1][0], 'statuses/' + 'a' * 40)
        self.assertEqual(calls[-1][1]['state'], 'failure')
        self.assertEqual(calls[-1][1]['context'], 'merge-queue/CI')

    def test_workflow_always_exposes_queue_result_with_status_permission(self):
        workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/ci.yml').read_text()
        checks = workflow.split('  checks:\n', 1)[1]
        self.assertIn('statuses: write', checks)
        self.assertIn("if: ${{ always() && github.event_name == 'merge_group' }}", checks)
        self.assertIn('run: python3 ops/merge-queue-signal.py', checks)

    def test_changed_pr_head_is_not_marked(self):
        calls = []
        def api(path, payload=None):
            calls.append((path, payload))
            return {'head': {'sha': 'a' * 40}} if path.startswith('pulls/') else {'status': 'diverged'}
        signal_tool.publish({'merge_group': {'head_ref': 'refs/heads/gh-readonly-queue/main/pr-123-' + 'b' * 40,
                                              'head_sha': 'c' * 40}}, 'failure', 'https://github.com/o/r/actions/runs/1', api)
        self.assertFalse(any(path.startswith('statuses/') for path, _ in calls))

    def test_success_clears_previous_failure_and_non_queue_events_do_nothing(self):
        calls = []
        def api(path, payload=None):
            calls.append((path, payload))
            if path.startswith('pulls/'): return {'head': {'sha': 'a' * 40}}
            return {'status': 'identical'}
        signal_tool.publish({}, 'success', 'https://github.com/o/r/actions/runs/1', api)
        self.assertEqual(calls, [])
        signal_tool.publish({'merge_group': {'head_ref': 'refs/heads/gh-readonly-queue/main/pr-123-' + 'b' * 40,
                                              'head_sha': 'c' * 40}}, 'success', 'https://github.com/o/r/actions/runs/1', api)
        self.assertEqual(calls[-1][1]['state'], 'success')


class QueueRecovery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from lib.loadpy import load_module_from_path
        cls.recovery = load_module_from_path('bounded_queue_recovery', str(Path(__file__).with_name('merge-queue-recovery.py')))

    def setUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / 'journal.sqlite'
        self.calls = []

    def drop(self, **changes):
        values = dict(repository='DoctorCRE/carr-system', number=123,
                      event_id='RFMQE_example', head='a' * 40, approval_id=17,
                      group_sha='b' * 40, reason='failed_checks', actor='github-merge-queue',
                      runs=({'event': 'merge_group', 'head_sha': 'b' * 40, 'status': 'completed',
                               'conclusion': 'startup_failure'},), runs_complete=True)
        values.update(changes)
        return self.recovery.Drop(**values)

    def observe(self, drop):
        return self.recovery.Observation(drop.head, drop.approval_id, False, 'open', False)

    def arm(self, drop):
        self.calls.append(drop.event_id)
        return {'state': 'armed', 'head_sha': drop.head}

    def run_drop(self, drop=None, observe=None, arm=None):
        return self.recovery.recover(drop or self.drop(), self.database,
                                     observe or self.observe, arm or self.arm)

    def test_transient_recovers_once(self):
        self.assertEqual(self.run_drop()['state'], 'armed')
        self.assertEqual(self.calls, ['RFMQE_example'])

    def test_duplicate_delivery_does_not_repeat_write(self):
        self.run_drop()
        self.assertEqual(self.run_drop()['state'], 'duplicate')
        self.assertEqual(len(self.calls), 1)

    def test_unsafe_causes_never_arm(self):
        cases = [dict(reason='manual'), dict(reason='merged'), dict(actor='jbookout'),
                 dict(reason='future_unknown'), dict(runs_complete=False), dict(runs=())]
        for conclusion in ('cancelled', 'timed_out', 'failure', None, 'success'):
            cases.append(dict(runs=({'event': 'merge_group', 'head_sha': 'b' * 40, 'status': 'completed',
                                      'conclusion': conclusion},)))
        cases.append(dict(runs=({'event': 'merge_group', 'head_sha': 'c' * 40, 'status': 'completed',
                                  'conclusion': 'startup_failure'},)))
        for i, changes in enumerate(cases):
            with self.subTest(changes=changes):
                result = self.run_drop(self.drop(event_id=str(i), **changes))
                self.assertEqual(result['state'], 'diagnosis')
        self.assertEqual(self.calls, [])

    def test_changed_authority_never_arms(self):
        for i, values in enumerate((('c' * 40, 17, False), ('a' * 40, 18, False),
                                    ('a' * 40, None, False), ('a' * 40, 17, True))):
            result = self.run_drop(self.drop(event_id=str(i)),
                observe=lambda _: self.recovery.Observation(*values, 'open', False))
            self.assertEqual(result['state'], 'diagnosis')
        self.assertEqual(self.calls, [])

    def test_terminal_queue_state_never_arms(self):
        for i, (state, queued) in enumerate((('closed', False), ('merged', False), ('open', True))):
            self.assertEqual(self.run_drop(self.drop(event_id=str(i)), observe=lambda d:
                self.recovery.Observation(d.head, d.approval_id, False, state, queued))['state'], 'diagnosis')
        self.assertEqual(self.calls, [])

    def test_retry_budget_survives_restart_and_reapproval(self):
        for i in range(3):
            result = self.run_drop(self.drop(event_id=str(i), approval_id=17+i))
            self.assertEqual(result['state'], 'armed' if i < 2 else 'diagnosis')
        self.assertEqual(result['reason'], 'retry-limit')
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.run_drop(self.drop(event_id='new-head', head='c' * 40))['state'], 'armed')

    def test_write_failure_is_not_retried(self):
        def fail(drop):
            self.calls.append(drop.event_id)
            raise TimeoutError('ambiguous native write')
        self.assertEqual(self.run_drop(arm=fail)['state'], 'uncertain')
        self.assertEqual(self.run_drop()['state'], 'duplicate')
        self.assertEqual(self.run_drop(self.drop(event_id='another'))['reason'], 'unresolved-write')
        self.assertEqual(len(self.calls), 1)

    def test_crash_reservation_is_not_replayed(self):
        def crash(drop):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.run_drop(arm=crash)
        self.assertEqual(self.run_drop()['previous'], 'reserved')
        self.assertEqual(self.run_drop(self.drop(event_id='another'))['reason'], 'unresolved-write')
        self.assertEqual(self.calls, [])

    def test_concurrent_delivery_reserves_once(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        started, release = Event(), Event()
        def arm(drop):
            started.set()
            self.assertTrue(release.wait(5))
            return self.arm(drop)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.run_drop, arm=arm)
            self.assertTrue(started.wait(5))
            try:
                self.assertEqual(self.run_drop()['previous'], 'reserved')
                self.assertEqual(self.run_drop(self.drop(event_id='other'))['reason'], 'unresolved-write')
            finally:
                release.set()
            self.assertEqual(first.result()['state'], 'armed')
        self.assertEqual(len(self.calls), 1)

    def test_adapter_refusal_is_not_success(self):
        self.assertEqual(self.run_drop(arm=lambda _: {'state': 'held'})['state'], 'uncertain')

    def test_unavailable_evidence_never_writes(self):
        def fail(_):
            raise ValueError('incomplete read')
        with self.assertRaises(ValueError):
            self.run_drop(observe=fail)
        self.assertEqual(self.calls, [])

    def test_invalid_identity_never_observes_or_writes(self):
        for changes in (dict(repository='other/repo'), dict(event_id=''), dict(head='short'),
                        dict(approval_id=True), dict(number=0)):
            with self.assertRaises(ValueError):
                self.run_drop(self.drop(**changes))
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
