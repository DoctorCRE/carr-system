#!/usr/bin/env python3
"""Capacity retry receipts through dispatch, without a live model."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import desks
import dispatch
from test_codex_models_unit import catalog_fixture


class CapacityTests(unittest.TestCase):
    def send(self, failures, detail='Selected model is at capacity', terminated=True):
        calls = []
        def runner(argv, **options):
            calls.append(argv)
            if len(calls) <= failures:
                result = subprocess.CompletedProcess(argv, 1, json.dumps({
                    'type': 'turn.failed', 'error': {'message': detail}}), '')
                result.termination_confirmed = terminated
                return result
            Path(argv[argv.index('-o') + 1]).write_text('done')
            return subprocess.CompletedProcess(argv, 0, '{"type":"turn.completed"}', '')
        with tempfile.TemporaryDirectory() as tmp, catalog_fixture():
            root = Path(tmp)
            registry = desks.Registry(root / 'desks.json')
            registry.register('cx', 'codex-session', family='sol', effort='high', cwd=tmp)
            with patch.object(dispatch.time, 'sleep') as sleep:
                row = dispatch.dispatch('cx', 'inspect', registry=registry,
                    results_path=root / 'results.jsonl', provider_run=runner)
            recorded = [json.loads(line) for line in (root / 'results.jsonl').read_text().splitlines()]
        self.assertEqual(recorded, [row])
        return row, calls, sleep

    def test_capacity_retries_then_completes(self):
        row, calls, sleep = self.send(2)
        self.assertEqual(row['status'], 'completed')
        self.assertEqual(row['result'], 'done')
        self.assertEqual(len(calls), 3)
        self.assertEqual([call.args for call in sleep.call_args_list], [(300,), (300,)])

    def test_capacity_exhaustion_keeps_detail_and_records_once(self):
        row, calls, sleep = self.send(10)
        self.assertEqual(row['status'], 'capacity')
        self.assertEqual(row['detail'], 'Selected model is at capacity')
        self.assertEqual(len(calls), 6)
        self.assertEqual([call.args for call in sleep.call_args_list], [(300,)] * 5)

    def test_other_failures_are_never_retried(self):
        row, calls, sleep = self.send(10, 'connection lost after starting work')
        self.assertEqual(row['status'], 'failed')
        self.assertEqual(len(calls), 1)
        sleep.assert_not_called()

    def test_unconfirmed_termination_never_relaunches(self):
        row, calls, sleep = self.send(10, terminated=False)
        self.assertEqual(row['status'], 'capacity')
        self.assertFalse(row['termination_confirmed'])
        self.assertEqual(len(calls), 1)
        sleep.assert_not_called()


if __name__ == '__main__':
    unittest.main()
