"""Budget admission and recovery behavior at the durable ledger interface."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

import nous_budget


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.results = Path(self.tmp.name) / 'results.jsonl'
        self.ledger = nous_budget.Ledger(self.results)

    def test_concurrent_reservations_cannot_spend_the_same_last_dollar(self):
        for index in range(21):
            self.ledger.reserve(str(index))
        barrier = threading.Barrier(2)
        admitted, refused = [], []
        def reserve(run_id):
            barrier.wait()
            try:
                nous_budget.Ledger(self.results).reserve(run_id)
                admitted.append(run_id)
            except nous_budget.BudgetStop as exc:
                refused.append(str(exc))
        jobs = [threading.Thread(target=reserve, args=(str(index),)) for index in (21, 22)]
        for job in jobs:
            job.start()
        for job in jobs:
            job.join(5)
            self.assertFalse(job.is_alive())
        self.assertEqual(len(admitted), 1)
        self.assertEqual(refused, ['Nous credit budget spent'])
        self.assertEqual(self.ledger.read()['reserved_usd'], '22.00')

    def test_repeated_settlement_does_not_double_charge(self):
        self.ledger.reserve('job')
        for _ in range(2):
            self.ledger.settle('job', {'estimated_cost_usd': '0.12'}, True)
        self.assertEqual(self.ledger.read()['spent_usd'], '0.12')
        self.assertEqual(self.ledger.read()['reserved_usd'], '0.00')

    def test_a_crashed_run_requires_reconciliation_before_more_spending(self):
        subprocess.run([sys.executable, '-c',
            'from nous_budget import Ledger; import sys; Ledger(sys.argv[1]).reserve("crashed")',
            str(self.results)], env={**os.environ, 'PYTHONPATH': str(Path(__file__).parent)}, check=True)
        with self.assertRaisesRegex(nous_budget.BudgetStop, 'reconcile ledger'):
            self.ledger.reserve('later')

    def test_corrupt_ledger_never_resets_to_zero(self):
        self.ledger.path.write_text('{broken')
        with self.assertRaises(ValueError):
            self.ledger.reserve('job')
        self.assertEqual(self.ledger.path.read_text(), '{broken')

    def test_reported_total_includes_auxiliary_spending(self):
        self.ledger.reserve('job')
        self.ledger.settle('job', {'estimated_cost_usd': '0.12',
            'total_including_auxiliary': {'estimated_cost_usd': '0.37'}}, True)
        self.assertEqual(self.ledger.read()['spent_usd'], '0.37')

    def test_malformed_cost_receipt_stops_later_spending(self):
        self.ledger.reserve('job')
        self.ledger.settle('job', {'total_including_auxiliary': ['invalid']}, True)
        with self.assertRaisesRegex(nous_budget.BudgetStop, 'reconcile ledger'):
            self.ledger.reserve('later')

    def test_another_runs_credit_stop_blocks_a_previously_reserved_launch(self):
        self.ledger.reserve('first')
        self.ledger.reserve('second')
        self.ledger.settle('first', {'estimated_cost_usd': '0.12'}, True, exhausted=True)
        with self.assertRaisesRegex(nous_budget.BudgetStop, 'nous credits exhausted'):
            self.ledger.launch('second')

    def test_settled_spend_rechecks_the_budget_before_a_reserved_launch(self):
        self.ledger.reserve('first')
        self.ledger.reserve('second')
        self.ledger.settle('first', {'estimated_cost_usd': '21.50'}, True)
        with self.assertRaisesRegex(nous_budget.BudgetStop, 'Nous credit budget spent'):
            self.ledger.launch('second')


if __name__ == '__main__':
    unittest.main()
