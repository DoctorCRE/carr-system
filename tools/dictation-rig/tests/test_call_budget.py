"""Offline dollar reservations at the call-check interface."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import call_budget


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = Path(self.temp.name) / 'budget.json'

    def test_missing_invalid_or_exhausted_budget_holds(self):
        for value in ('', '0', '-1', 'NaN', 'Infinity', 'abc', '0.002752'):
            with self.subTest(value=value), patch.dict('os.environ', {'CALL_JEV_DAILY_DOLLARS': value}):
                with self.assertRaises(call_budget.BudgetHold):
                    call_budget.reserve(self.ledger)
        self.assertFalse(self.ledger.exists())

    def test_concurrent_reservations_never_exceed_daily_dollars(self):
        def reserve(_):
            try:
                call_budget.reserve(self.ledger)
                return True
            except call_budget.BudgetHold:
                return False
        with patch.dict('os.environ', {'CALL_JEV_DAILY_DOLLARS': '0.005506'}):
            with ThreadPoolExecutor(max_workers=8) as pool:
                self.assertEqual(sum(pool.map(reserve, range(20))), 2)
            with self.assertRaisesRegex(call_budget.BudgetHold, 'exhausted'):
                call_budget.reserve(self.ledger)
        self.assertEqual(self.ledger.stat().st_mode & 0o777, 0o600)

    def test_day_rollover_uses_chicago_and_unreadable_ledger_holds(self):
        with patch.dict('os.environ', {'CALL_JEV_DAILY_DOLLARS': '0.002753'}):
            call_budget.reserve(self.ledger, instant=datetime(2026, 1, 1, 23, tzinfo=timezone.utc))
            with self.assertRaises(call_budget.BudgetHold):
                call_budget.reserve(self.ledger, instant=datetime(2026, 1, 2, 5, tzinfo=timezone.utc))
            call_budget.reserve(self.ledger, instant=datetime(2026, 1, 2, 6, tzinfo=timezone.utc))
            self.ledger.write_text('broken')
            with self.assertRaises(call_budget.BudgetHold):
                call_budget.reserve(self.ledger)

    def test_budget_hold_is_visible_and_sends_no_request(self):
        import post_call_jev
        from unittest.mock import Mock
        result = {'session': 'synthetic', 'joe_tasks': [{'title': 'Review proposal', 'evidence': 'Review proposal'}]}
        client = Mock()
        with patch.dict('os.environ', {'CALL_JEV_DAILY_DOLLARS': ''}):
            checked = post_call_jev.check_distillation(result, {}, {}, client=client, budget_ledger=self.ledger)
        client.system_one.assert_not_called()
        self.assertIn('unconfigured', checked['joe_tasks'][0]['checks']['reason'])
        self.assertTrue(checked['review_questions'])

    def test_failed_request_keeps_reservation_and_disables_hidden_retries(self):
        import post_call_jev
        from typesafe_sdk import TypeSafeError
        from unittest.mock import Mock
        client = Mock()
        client.system_one.side_effect = TypeSafeError('synthetic failure')
        def result():
            return {'session': 'synthetic', 'joe_tasks': [{'title': 'Review', 'evidence': 'Review proposal'}]}
        with patch.dict('os.environ', {'CALL_JEV_DAILY_DOLLARS': '0.002753'}):
            post_call_jev.check_distillation(result(), {}, {}, client=client, budget_ledger=self.ledger)
            checked = post_call_jev.check_distillation(result(), {}, {}, client=client, budget_ledger=self.ledger)
        self.assertEqual(client.system_one.call_count, 1)
        arguments = client.system_one.call_args.kwargs
        self.assertEqual(arguments['retry'].max_retries, 0)
        self.assertEqual(arguments['model'], 'jev-1.14.0')
        self.assertIn('exhausted', checked['joe_tasks'][0]['checks']['reason'])


if __name__ == '__main__':
    unittest.main()
