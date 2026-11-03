#!/usr/bin/env python3
"""Exercise queue status binding without GitHub writes."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('signal_tool', Path(__file__).with_name('merge-queue-signal.py'))
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


if __name__ == '__main__':
    unittest.main()
