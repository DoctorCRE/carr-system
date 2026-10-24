#!/usr/bin/env python3
"""Synthetic STEP-0 decisions and real command wiring, without model or store access."""
from datetime import datetime
from pathlib import Path
import sys
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


class PredicateTests(unittest.TestCase):
    def test_monthly_window_uses_chicago_date(self):
        from lib.scheduled_step0 import stop_reason
        self.assertEqual(stop_reason('health-audit-monthly', datetime.fromisoformat('2026-10-11T05:01:00+00:00'), [], monthly_days=list(range(6, 11))), 'outside_monthly_window')
        self.assertIsNone(stop_reason('health-audit-monthly', datetime.fromisoformat('2026-10-11T04:59:00+00:00'), [], monthly_days=list(range(6, 11))))

    def test_weekly_completion_requires_verified_current_week_evidence(self):
        from lib.scheduled_step0 import stop_reason
        now = datetime.fromisoformat('2026-10-19T14:00:00+00:00')
        row = {'status': 'success', 'exit_code': 0, 'canonical_recorded': True,
               'end': '2026-10-19T13:00:00+00:00', 'window': '2026-10-19T12:00:00+00:00'}
        self.assertEqual(stop_reason('radar-weekly', now, [row]), 'weekly_completed')
        for change in ({'status': 'skipped'}, {'exit_code': 1}, {'canonical_recorded': False},
                       {'end': '2026-10-19T04:59:59+00:00'}, {'end': '2026-10-19T14:01:00+00:00'},
                       {'window': '2026-10-18T13:00:00+00:00'}):
            with self.subTest(change=change):
                self.assertIsNone(stop_reason('radar-weekly', now, [{**row, **change}]))

    def test_versions_stop_only_on_two_valid_identical_observations(self):
        from lib.scheduled_step0 import version_unchanged
        self.assertTrue(version_unchanged('cli=2.1.1 app=2.1.2', 'cli=2.1.1 app=2.1.2'))
        for current, last in [('cli=2.1.2 app=2.1.2', 'cli=2.1.1 app=2.1.2'),
                              ('cli=2.1.1 app=2.1.3', 'cli=2.1.1 app=2.1.2'),
                              ('cli=none app=none', 'cli=none app=none'),
                              ('', ''), ('garbage', 'garbage')]:
            with self.subTest(current=current, last=last):
                self.assertFalse(version_unchanged(current, last))

    def test_dst_fallback_orders_receipts_by_instant(self):
        from lib.scheduled_step0 import stop_reason
        row = dict(status='success', exit_code=0, canonical_recorded=True,
                   window='2026-11-01T05:00:00+00:00')
        for now, end, expected in [
                ('2026-11-01T06:30:00+00:00', '2026-11-01T07:15:00+00:00', None),
                ('2026-11-01T07:15:00+00:00', '2026-11-01T06:45:00+00:00', 'weekly_completed')]:
            with self.subTest(now=now, end=end):
                self.assertEqual(stop_reason('radar-weekly', datetime.fromisoformat(now),
                                             [{**row, 'end': end}]), expected)


if __name__ == '__main__':
    unittest.main()
