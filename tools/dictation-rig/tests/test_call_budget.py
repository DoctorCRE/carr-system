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

    def test_absent_and_empty_budget_hold_before_client_construction(self):
        import os
        import post_call_jev
        from unittest.mock import Mock
        for value in (None, ''):
            with self.subTest(value=value), patch.dict(os.environ, {}, clear=True):
                if value is not None:
                    os.environ['CALL_JEV_DAILY_DOLLARS'] = value
                result = {'session': 'synthetic', 'joe_tasks': [
                    {'title': 'Review proposal', 'evidence': 'Review proposal'}]}
                client = Mock()
                with patch.object(post_call_jev, 'TypeSafeClient') as factory:
                    checked = post_call_jev.check_distillation(
                        result, {}, {}, client=client, budget_ledger=self.ledger)
                    factory.assert_not_called()
                client.system_one.assert_not_called()
                self.assertIn('unconfigured', checked['checks_hold'])
                self.assertIn('unconfigured', checked['joe_tasks'][0]['checks']['reason'])
                self.assertTrue(checked['review_questions'])
                self.assertFalse(self.ledger.exists())
                with patch.object(post_call_jev, 'TypeSafeClient') as factory:
                    post_call_jev.check_distillation(result, {}, {}, budget_ledger=self.ledger)
                    factory.assert_not_called()

    def test_configured_and_lowered_caps(self):
        for value, count in (('0.50', 181), ('0.10', 36)):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                ledger = Path(directory) / 'budget.json'
                with patch.dict('os.environ', {'CALL_JEV_DAILY_DOLLARS': value}):
                    for _ in range(count):
                        call_budget.reserve(ledger)
                    with self.assertRaises(call_budget.BudgetHold):
                        call_budget.reserve(ledger)

    def test_invalid_or_exhausted_budget_holds(self):
        for value in ('0', '-1', 'NaN', 'Infinity', 'abc', '0.002752'):
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
        with patch.dict('os.environ', {'CALL_JEV_DAILY_DOLLARS': 'invalid'}):
            checked = post_call_jev.check_distillation(result, {}, {}, client=client, budget_ledger=self.ledger)
        client.system_one.assert_not_called()
        self.assertIn('unconfigured', checked['joe_tasks'][0]['checks']['reason'])
        self.assertTrue(checked['review_questions'])

    def test_client_open_failure_records_reason_after_reservation(self):
        import post_call_jev
        result = {'session': 'synthetic', 'joe_tasks': [
            {'title': 'Review', 'evidence': 'Review proposal'}]}
        with patch.dict('os.environ', {'CALL_JEV_DAILY_DOLLARS': '0.002753'}), \
             patch.object(post_call_jev, 'TypeSafeClient', side_effect=RuntimeError('no key')):
            checked = post_call_jev.check_distillation(result, {}, {}, budget_ledger=self.ledger)
        checks = checked['joe_tasks'][0]['checks']
        self.assertTrue(checks['unavailable'])
        self.assertIn('RuntimeError, missing key', checks['reasons'][0])
        question = checked['review_questions'][0]['question']
        self.assertTrue(question.startswith('Automatic Jev checks did not run'))
        self.assertIn(checks['reasons'][0], question)
        self.assertTrue(self.ledger.exists())

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
