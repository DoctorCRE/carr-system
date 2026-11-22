#!/usr/bin/env python3
"""Pure calendar projection regressions; synthetic meetings only."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch
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
                attendees=[]) | fields


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

    def test_address_free_cancellation_fails_the_account_check(self):
        snapshot, counts = self.project([event(), event(organizer_email=None, lifecycle_status='canceled')])
        self.assertEqual(snapshot['items'], [])
        self.assertEqual(snapshot['follow_up_reviews'], [])
        self.assertEqual(counts['calendar_agenda_other_accounts'], 1)

    def test_account_evidence_does_not_cover_another_start(self):
        snapshot, counts = self.project([event(), event(hours=3, organizer_email=None)])
        self.assertEqual(snapshot['items'], [])
        self.assertEqual(counts['calendar_agenda_other_accounts'], 1)

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

    def test_ended_meeting_keeps_its_full_row_and_review(self):
        snapshot, _ = self.project([
            event(hours=-2, lifecycle_status='unsupported', location='Example Room',
                  attendees=[('Pat', 'Pat@example.test')]),
        ])
        self.assertEqual(len(snapshot['items']), 1)
        [meeting] = snapshot['items']
        self.assertEqual(meeting['start'], '2026-10-20T13:00:00+00:00')
        self.assertEqual(meeting['end'], '2026-10-20T14:00:00+00:00')
        self.assertEqual(meeting['location'], 'Example Room')
        self.assertEqual(meeting['attendees'], [{'name': 'Pat', 'email': 'pat@example.test'}])
        self.assertEqual([r['key'] for r in snapshot['follow_up_reviews']], [meeting['key']])

    def test_ended_conflicting_meeting_has_a_full_row_without_review(self):
        snapshot, _ = self.project([
            event(hours=-2, location='Example Room'),
            event(hours=-2, location='Other Room'),
        ])
        self.assertEqual(len(snapshot['items']), 1)
        self.assertTrue(snapshot['items'][0]['conflict'])
        self.assertEqual(snapshot['follow_up_reviews'], [])

    def test_shorter_display_title_does_not_change_which_items_get_trimmed(self):
        short = event(1, subject='Short')
        long = event(2, subject='Z' * 1000)
        unreadable = long | dict(subject=None, subject_undecoded=True, legacy_subject=long['subject'])
        snapshot, _ = self.project([short, unreadable])
        self.assertEqual(len(snapshot['items']), 2)
        with patch('lib.calendar_agenda.AGENDA_MAX_BYTES', len(json.dumps(snapshot)) + 100):
            expected, _ = self.project([short, long])
            actual, counts = self.project([short, unreadable])
        self.assertEqual([item['title'] for item in actual['items']], ['Short'])
        self.assertEqual(actual['items'], expected['items'])
        self.assertEqual(counts['calendar_agenda_trimmed'], 1)

    def test_ended_meetings_use_the_central_midnight_boundary(self):
        snapshot, _ = self.project([
            event(1, hours=-13, subject='Previous day'),
            event(2, hours=-11, subject='Ends at midnight'),
            event(3, hours=-11, end_unix=int((NOW - timedelta(hours=9)).timestamp()),
                  subject='Crosses midnight'),
            event(4, hours=-10, subject='Starts at midnight'),
        ])
        self.assertEqual([i['title'] for i in snapshot['items']], ['Crosses midnight', 'Starts at midnight'])
        self.assertEqual({r['title'] for r in snapshot['follow_up_reviews']},
                         {'Previous day', 'Ends at midnight', 'Crosses midnight', 'Starts at midnight'})

    def test_byte_limit_trims_the_oldest_end_first(self):
        large_location = 'é' * 12_000
        snapshot, counts = self.project([
            event(1, hours=-5, end_unix=int((NOW - timedelta(hours=2)).timestamp()),
                  subject='Longer meeting', location=large_location),
            event(2, hours=-4, subject='First to end', location=large_location),
            event(3, hours=-2, subject='Latest to end', location=large_location),
            event(4, subject='Upcoming'),
        ])
        self.assertLessEqual(len(json.dumps(snapshot).encode('utf-8')), 200_000)
        self.assertEqual([i['title'] for i in snapshot['items']],
                         ['Longer meeting', 'Latest to end', 'Upcoming'])
        self.assertEqual([r['title'] for r in snapshot['follow_up_reviews']],
                         ['Latest to end', 'Longer meeting', 'First to end'])
        self.assertEqual(counts['calendar_agenda_trimmed'], 1)
        self.assertIn({'code': 'ended_trimmed', 'count': 1}, snapshot['warnings'])
        self.assertNotIn('trimmed', [warning['code'] for warning in snapshot['warnings']])

    def test_zero_duration_occurrence_at_opening_midnight(self):
        for month, day in ((3, 8), (11, 1)):
            with self.subTest(month=month, day=day):
                midnight = datetime(2026, month, day, tzinfo=SETTINGS.timezone)
                opening = int(midnight.timestamp())
                closing = int((midnight + timedelta(days=14)).timestamp())
                snapshot, _ = agenda([
                    event(1, start_unix=opening - 3600, end_unix=opening),
                    event(2, start_unix=opening, end_unix=opening, subject='At opening midnight'),
                    event(3, start_unix=closing, end_unix=closing),
                ], {}, SETTINGS, midnight + timedelta(hours=12))
                self.assertEqual([i['title'] for i in snapshot['items']], ['At opening midnight'])

    def test_moved_meeting_uses_start_and_end_of_the_selected_copy(self):
        snapshot, _ = self.project([
            event(hours=-11, end_unix=int((NOW - timedelta(hours=8)).timestamp())),
            event(hours=-1, end_unix=int((NOW - timedelta(minutes=30)).timestamp()),
                  attendees=[('Pat', 'pat@example.test')]),
        ])
        [meeting] = snapshot['items']
        self.assertEqual((meeting['start'], meeting['end']),
                         ('2026-10-20T14:00:00+00:00', '2026-10-20T14:30:00+00:00'))
        self.assertTrue(meeting['time_changed'])
        self.assertEqual(meeting['times'], ['2026-10-20T04:00:00+00:00', '2026-10-20T14:00:00+00:00'])
        self.assertEqual(snapshot['follow_up_reviews'], [])

    def test_byte_limit_counts_ended_and_later_trims_separately(self):
        snapshot, counts = self.project([
            event(n, hours=hours, location='é' * 12_000)
            for n, hours in enumerate((-3, -2, 2, 3, 4))
        ])
        self.assertEqual([i['start'] for i in snapshot['items']],
                         ['2026-10-20T17:00:00+00:00', '2026-10-20T18:00:00+00:00'])
        self.assertEqual(counts['calendar_agenda_trimmed'], 3)
        warnings = {w['code']: w.get('count') for w in snapshot['warnings']}
        self.assertEqual(warnings.get('ended_trimmed'), 2)
        self.assertEqual(warnings.get('trimmed'), 1)

    def test_other_accounts_and_outside_window_do_not_count_as_hidden_cancellations(self):
        other = event(lifecycle_status='canceled')
        other['organizer_email'] = 'other@example.test'
        snapshot, counts = self.project([other, event(2, hours=24*20, lifecycle_status='canceled')])
        self.assertEqual(snapshot['items'], [])
        self.assertEqual(counts['calendar_agenda_canceled'], 0)
        self.assertEqual(counts['calendar_agenda_other_accounts'], 1)


if __name__ == '__main__':
    unittest.main()
