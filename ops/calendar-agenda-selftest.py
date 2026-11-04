#!/usr/bin/env python3
"""Pure calendar projection regressions; synthetic meetings only."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.calendar_agenda import agenda

NOW = datetime(2026, 10, 20, 15, tzinfo=timezone.utc)
SETTINGS = SimpleNamespace(account='owner@example.test', timezone=ZoneInfo('America/Chicago'))


def event(n=1, *, instance='00000000', hours=2, **fields):
    start = int((NOW + timedelta(hours=hours)).timestamp())
    return dict(goid=f'040000008200E00074C5B7101A82E008{instance}{n:012X}',
                start_unix=start, end_unix=start + 3600, subject='Synthetic meeting',
                location=None, organizer_name='Owner', organizer_email=SETTINGS.account,
                attendees=[], **fields)


class CalendarAgendaTest(unittest.TestCase):
    def project(self, events):
        return agenda(events, {}, SETTINGS, NOW)

    def hidden(self, events, count=1):
        snapshot, counts = self.project(events)
        self.assertEqual(snapshot['items'], [])
        self.assertEqual(snapshot['follow_up_reviews'], [])
        self.assertEqual(counts['calendar_agenda_canceled'], count)
        self.assertIn({'code': 'canceled_hidden', 'count': count}, snapshot['warnings'])
        self.assertEqual(self.project(events), (snapshot, counts), 'replay must be deterministic')

    def test_decoded_cancellation_hides_ordinary_subject_upcoming_and_recent(self):
        for hours in (2, -2):
            with self.subTest(hours=hours):
                self.hidden([event(hours=hours, lifecycle_status='canceled',
                                   lifecycle_reason='outlook_canceled_subject')])

    def test_legacy_cancellation_spellings_remain_supported(self):
        for title in ('Canceled: Synthetic', 'Cancelled: Synthetic', '  cAnCeLeD : Synthetic'):
            with self.subTest(title=title):
                row = event()
                row['subject'] = title
                self.hidden([row])

    def test_one_canceled_copy_dominates_conflicting_display_and_time_updates(self):
        for hours in (2, -2):
            original = event(hours=hours, lifecycle_status='unsupported')
            cancellation = event(hours=hours + 1, lifecycle_status='canceled')
            cancellation.update(subject='Short', attendees=[('Person', 'person@example.test')])
            before = deepcopy([original, cancellation])
            self.hidden([original, cancellation])
            self.hidden([cancellation, original])
            self.assertEqual([original, cancellation], before, 'input evidence must stay immutable')

    def test_occurrence_cancellation_preserves_sibling_and_suppresses_master(self):
        rows = [event(), event(instance='07EA0A14', lifecycle_status='canceled'),
                event(instance='07EA0A15', hours=26, lifecycle_status='unsupported')]
        snapshot, counts = self.project(rows)
        self.assertEqual(len(snapshot['items']), 1)
        self.assertTrue(snapshot['items'][0]['recurring'])
        self.assertEqual(counts['calendar_agenda_canceled'], 1)
        self.assertEqual(counts['calendar_agenda_recurring'], 1)
        self.assertIn({'code': 'recurring_unverified', 'count': 1}, snapshot['warnings'])

    def test_moved_exception_cancellation_hides_all_copies_of_that_instance(self):
        self.hidden([event(instance='07EA0A14', lifecycle_status='unsupported'),
                     event(instance='07EA0A14', hours=26, lifecycle_status='canceled')])

    def test_unsupported_or_absent_lifecycle_remains_visible_and_moved_warns(self):
        for status in ({}, {'lifecycle_status': 'unsupported'}):
            rows = [event(**status), event(hours=3, **status)]
            snapshot, counts = self.project(rows)
            self.assertEqual(len(snapshot['items']), 1)
            self.assertTrue(snapshot['items'][0]['time_changed'])
            self.assertEqual(counts['calendar_agenda_canceled'], 0)
            self.assertIn({'code': 'time_changed', 'count': 1}, snapshot['warnings'])

    def test_recent_unsupported_meeting_still_proposes_review(self):
        snapshot, _ = self.project([event(hours=-2, lifecycle_status='unsupported')])
        self.assertEqual(len(snapshot['follow_up_reviews']), 1)

    def test_other_accounts_and_outside_window_do_not_count_as_hidden_cancellations(self):
        other = event(lifecycle_status='canceled')
        other['organizer_email'] = 'other@example.test'
        snapshot, counts = self.project([other, event(2, hours=24*20, lifecycle_status='canceled')])
        self.assertEqual(snapshot['items'], [])
        self.assertEqual(counts['calendar_agenda_canceled'], 0)
        self.assertEqual(counts['calendar_agenda_other_accounts'], 1)


if __name__ == '__main__':
    unittest.main()
