#!/usr/bin/env python3
"""Check synthetic calendar exports. Set CARR_HXPROBE_BASELINE_BINARY to compare with main."""
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
from itertools import product
import os
import runpy
from pathlib import Path
import sqlite3
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from zoneinfo import ZoneInfo
from hxprobe_test_binary import test_binary

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.calendar_agenda import agenda
from lib.outlook_intake import _load

STORE = runpy.run_path(str(ROOT / 'ops/hxstore-observe-selftest.py'))['store']
BASELINE_BINARY = os.environ.get('CARR_HXPROBE_BASELINE_BINARY')
BASELINE_AGENDA = os.environ.get('CARR_CALENDAR_AGENDA_BASELINE')
NOW = 1_793_000_000
SETTINGS = SimpleNamespace(account='ada@example.test', timezone=ZoneInfo('America/Chicago'))
UNREADABLE = 'Meeting title unreadable'
ENDED = NOW - 7_200


def meeting_key(goid):
    return sha256(goid.encode()).hexdigest()[:16]


def candidate(attendees=(), start=NOW, metadata=0, response=0, *,
              subject='Synthetic Calendar Review', first_subject=None, duration=3600,
              zones=('America/Chicago', 'US/Central'), instance='00000000',
              organizer='ada@example.test', notes='Synthetic notes',
              omit_attendee_count=False, trailer=b'', before_notes=(), after_notes=(),
              before_fields=(), after_subject=(), identity=0xAAAA):
    obj = bytearray(574)
    struct.pack_into('<H', obj, 0, 0x006b)
    obj[100] = metadata
    if instance != '00000000':
        struct.pack_into('<Q', obj, 2, start * 10_000_000 + 621_355_968_000_000_000)
    for unix in (start, start + duration):
        obj.extend(struct.pack('<Q', unix * 10_000_000 + 621_355_968_000_000_000))
    obj.extend(bytes(517))
    obj.extend(f'040000008200E00074C5B7101A82E008{instance}{identity:08X}\0'.encode('utf-16le'))
    for text in zones:
        raw = text.encode('utf-16le')
        obj.extend(bytes([len(raw)]) + raw)
    notes = () if notes is None else (notes,)
    for text in (*before_fields, subject if first_subject is None else first_subject, 'Example Room', 'Ada Example',
                 organizer, 'Owner Example', *before_notes, *notes, *after_notes, subject, *after_subject):
        obj.extend((text + '\0').encode('utf-16le', errors='surrogatepass'))
    if not omit_attendee_count:
        obj.extend(struct.pack('<I', len(attendees)))
    for name, email in attendees:
        for text in (name, email):
            raw = text.encode('utf-16le')
            obj.extend(bytes([len(raw)]) + raw)
        obj.extend(bytes([response]) + bytes(11))
    obj.extend(trailer)
    return struct.pack('<HHIH', 5, 0x0455, len(obj) + 10, 0) + obj


class CalendarExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_binary()

    def test_distinct_schedule_candidates_export_as_unsupported_without_merging(self):
        attendee = [('Bo Example', 'bo@example.test')]
        original = candidate(attendee)
        rows = [original, candidate(), candidate(attendee, start=1_793_086_400),
                candidate(attendee, metadata=1), candidate(attendee, response=1), original]
        with tempfile.TemporaryDirectory() as tmp:
            target = self.export(Path(tmp), rows)
            with closing(sqlite3.connect(target)) as db:
                events = db.execute('SELECT lifecycle_status,lifecycle_reason FROM events').fetchall()
                self.assertEqual(events, [('unsupported', 'hxstore_lifecycle_unverified')] * 5)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM event_attendees').fetchone(), (4,))
                self.assertEqual(dict(db.execute('SELECT key,value FROM meta'))['calendar_lifecycle_unsupported'], 5)
                self.assertEqual(db.execute('SELECT COUNT(DISTINCT start_unix) FROM events').fetchone(), (2,))

    def export(self, directory, entries, *, generated=False):
        source, target = directory / 'synthetic.hxd', directory / 'synthetic.db'
        source.write_bytes(STORE(b''.join(entries)))
        self.decode(test_binary(), source, target)
        if BASELINE_BINARY:
            baseline = directory / 'baseline.db'
            self.decode(BASELINE_BINARY, source, baseline)
            with self.subTest(differential='decoder'):
                self.assert_evidence_matches(baseline, target, minimum=300 if generated else 0)
            if BASELINE_AGENDA:
                with self.subTest(differential='agenda'):
                    self.assert_agenda_matches(baseline, target, per_meeting=generated)
        return target

    def decode(self, binary, source, target):
        result = subprocess.run([str(binary), 'db', str(source), str(target)],
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def assert_evidence_matches(self, baseline, target, *, minimum=0):
        expected = Counter((copy, legacy) for copy, legacy, _ in self.evidence(baseline, 'subject, subject'))
        actual = Counter()
        for copy, legacy, title in self.evidence(target, 'legacy_subject, subject'):
            if legacy is not None:
                self.assertIn(title, (None, legacy))
                actual[copy, legacy] += 1
        self.assertGreaterEqual(sum(expected.values()), minimum)
        self.assertEqual(actual, expected, 'main-decoded copy evidence changed')

    def assert_agenda_matches(self, baseline, target, *, per_meeting=False):
        main_agenda = runpy.run_path(BASELINE_AGENDA)['agenda']
        main_meta, _, main_rows = _load(baseline)
        new_meta, _, new_rows = _load(target)
        cases = [('batch', main_rows, new_rows, main_meta, new_meta)]
        if per_meeting:
            groups = defaultdict(lambda: ([], []))
            for i, rows in enumerate((main_rows, new_rows)):
                for row in rows:
                    groups[row['goid']][i].append(row)
            self.assertGreaterEqual(len(groups), 3_000)
            cases.extend((key, left, right, {}, {}) for key, (left, right) in groups.items())

        now = datetime.fromtimestamp(NOW, timezone.utc)
        for key, left, right, left_meta, right_meta in cases:
            expected, main_counts = main_agenda(left, left_meta, SETTINGS, now)
            actual, new_counts = agenda(right, right_meta, SETTINGS, now)
            if per_meeting and key == 'batch':
                self.assertGreater(main_counts['calendar_follow_up_trimmed'], 0)
                self.assertGreater(main_counts['calendar_agenda_trimmed'], 0)
            main_goids = {row['goid'] for row in left}
            legacy_ids = {row['id'] for row in right if row['legacy_subject'] is not None}
            recovered_keys = {meeting_key(row['goid']) for row in right
                              if row['legacy_subject'] is None and row['goid'] not in main_goids
                              and row['id'] not in legacy_ids}
            for section in ('items', 'follow_up_reviews'):
                old_keys = {row['key'] for row in expected[section]}
                kept = [row for row in actual[section] if row['key'] in old_keys]
                self.assertEqual([r['key'] for r in kept], [r['key'] for r in expected[section]],
                                 f'{key}: main {section} changed membership or order')
                for before, after in zip(expected[section], kept):
                    self.assertEqual(after, before | {'title': after['title']}
                                     if after['title'] == UNREADABLE else before, key)
                allowed_keys = recovered_keys if section == 'items' else set()
                self.assertLessEqual({row['key'] for row in actual[section]} - old_keys, allowed_keys, key)
            self.assertEqual(new_counts['calendar_agenda_items'], len(actual['items']), key)
            for name in ('calendar_agenda_canceled', 'calendar_agenda_conflicts', 'calendar_follow_up_trimmed'):
                self.assertEqual(new_counts[name], main_counts[name], (key, name))
            for name in ('calendar_agenda_other_accounts', 'calendar_agenda_time_changed',
                         'calendar_agenda_recurring', 'calendar_agenda_trimmed'):
                self.assertGreaterEqual(new_counts[name], main_counts[name], (key, name))
                if not recovered_keys:
                    self.assertEqual(new_counts[name], main_counts[name], (key, name))

    @staticmethod
    def evidence(target, subject_columns):
        with closing(sqlite3.connect(target)) as db:
            return [
                ((goid, start, end, location, name, email, body, lifecycle, recurrence,
                  tuple(db.execute('SELECT name,email,kind_code,response_code FROM event_attendees '
                                   'WHERE event_id=? ORDER BY rowid', (row_id,)))), legacy, subject)
                for row_id, goid, start, end, location, name, email, body, lifecycle, recurrence, legacy, subject
                in db.execute('SELECT id,goid,start_unix,end_unix,location,organizer_name,organizer_email,'
                              f'body,lifecycle_status,recurrence,{subject_columns} FROM events')]

    @unittest.skipUnless(BASELINE_BINARY, 'requires the main decoder binary')
    def test_main_copy_evidence_survives_layout_variants(self):
        formats = product(
            (('America/Chicago', 'US/Central'), ('', ''), (), ('Asia/Tokyo', 'Tokyo Standard Time')),
            ((), ('Extra text before notes',)),
            ((), ('Extra text after notes',)),
            ((), ('Extra text after subject',), ('Repeated label', 'Repeated label')),
            (None, 'Example Room', 'Synthetic notes'),
            (None, 'Review', 'Canceled: Synthetic Calendar Review'),
            ('Synthetic Calendar Review', 'Example Room', '会議'),
            ('absent', 'empty', 'present'),
        )
        entries = []
        for i, (zones, before, after, trailer, notes, first, subject, attendees) in enumerate(formats):
            offset = (0, 1, 2, 3, 1, 1, 2, 3)[i % 8]
            timing = dict(start=NOW - offset * 7_200, identity=i)
            entries.append(candidate(
                [('Bo Example', 'bo@example.test')] if attendees == 'present' else (),
                **timing, zones=zones, before_notes=before, after_notes=after,
                after_subject=trailer, notes=notes, first_subject=first, subject=subject,
                omit_attendee_count=attendees == 'absent',
            ))
            if i % 4 == 1:
                entries.append(candidate(**timing, subject='Cancellation', first_subject='Canceled: Cancellation'))
            if i % 4 == 2:
                entries.extend(candidate(**timing, subject=title, notes=title) for title in ('Planning', 'Review'))
            if i % 4 == 3:
                entries.extend(candidate(**timing, subject=title) for title in ('Planning', 'Review'))
            recovered = (
                dict(subject='会議'), dict(subject='会議', first_subject='Canceled: 会議'),
                dict(subject='会議', organizer='bo@example.test'),
                dict(subject='会議', identity=i + 10_000),
                dict(subject='会議', start=timing['start'] + 1),
            )[i % 5]
            entries.append(candidate(**(timing | recovered)))
        with tempfile.TemporaryDirectory() as tmp:
            target = self.export(Path(tmp), entries, generated=True)
            with closing(sqlite3.connect(target)) as db:
                identities = db.execute("SELECT value FROM meta WHERE key = 'event_identities'").fetchone()
                self.assertEqual(identities, (i + 1 + sum(j % 5 in (3, 4) for j in range(i + 1)),))

    def project(self, *entries):
        with tempfile.TemporaryDirectory() as tmp:
            target = self.export(Path(tmp), entries)
            meta, _, events = _load(target)
        return agenda(events, meta, SETTINGS, datetime.fromtimestamp(NOW, timezone.utc))

    def test_short_title_without_attendee_count(self):
        snapshot, counts = self.project(candidate(subject='A', omit_attendee_count=True))
        self.assertEqual(counts['calendar_agenda_undecoded'], 0)
        self.assertEqual([item['title'] for item in snapshot['items']], ['A'])

    def test_private_placeholder_with_no_subject(self):
        snapshot, counts = self.project(candidate(subject='', omit_attendee_count=True))
        self.assertEqual(counts['calendar_agenda_undecoded'], 0)
        self.assertEqual([item['title'] for item in snapshot['items']], ['Meeting'])

    def test_email_shaped_title(self):
        snapshot, counts = self.project(candidate(subject='team@example.test'))
        self.assertEqual(counts['calendar_agenda_undecoded'], 0)
        self.assertEqual([item['title'] for item in snapshot['items']], ['team@example.test'])

    def test_title_is_never_used_as_evidence_of_account_membership(self):
        snapshot, counts = self.project(candidate(subject='ada@example.test', organizer=''))
        self.assertEqual(counts['calendar_agenda_undecoded'], 0)
        self.assertEqual(counts['calendar_agenda_other_accounts'], 1)
        self.assertEqual(snapshot['items'], [])

    def test_unicode_recurring_all_day_event_with_different_zone_metadata(self):
        snapshot, counts = self.project(candidate(
            [('Ada Example', 'ada@example.test')], subject='会議 📅', organizer='',
            instance='07EA0A1B', start=1_793_077_200, duration=86_400,
            zones=('Asia/Tokyo', 'Tokyo Standard Time')))
        self.assertEqual(counts['calendar_agenda_undecoded'], 0)
        [item] = snapshot['items']
        self.assertEqual(item['title'], '会議 📅')
        self.assertTrue(item['recurring'])
        self.assertTrue(item['all_day'])
        self.assertEqual(item['location'], 'Example Room')
        self.assertIsNone(item['organizer']['email'])

    def test_unicode_cancellation_is_decoded_and_hidden(self):
        snapshot, counts = self.project(candidate(subject='会議', first_subject='Cancelled: 会議'))
        self.assertEqual(counts['calendar_agenda_undecoded'], 0)
        self.assertEqual(counts['calendar_agenda_canceled'], 0)
        self.assertEqual(snapshot['items'], [])
        self.assertNotIn('canceled_hidden', {warning['code'] for warning in snapshot['warnings']})

    def test_address_free_cancellation_hides_an_undecoded_account_copy(self):
        snapshot, counts = self.project(
            candidate(first_subject='Planning', subject='Review'),
            candidate(subject='会議', first_subject='Cancelled: 会議', organizer=''))
        self.assertEqual(counts['calendar_agenda_undecoded'], 1)
        self.assertEqual(counts['calendar_agenda_other_accounts'], 1)
        self.assertEqual(snapshot['items'], [])

    def test_recovered_copies_cannot_change_a_main_meeting_or_review(self):
        original = candidate(subject='Planning', start=ENDED)
        expected, baseline = self.project(original)
        self.assertEqual(len(expected['items']), 1)
        self.assertEqual(len(expected['follow_up_reviews']), 1)
        for fields in (
            dict(subject='会議 📅'), dict(subject='会議', first_subject='Cancelled: 会議'),
            dict(subject='会議', organizer='bo@example.test'), dict(subject='会議', organizer=''),
            dict(first_subject='Planning', subject='Review', organizer=''),
            dict(first_subject='Planning', subject='Review', organizer='bo@example.test'),
            dict(first_subject='\ud800', subject='Review', organizer='bo@example.test'),
            dict(subject='会議', start=ENDED + 3600),
            dict(subject='会議', attendees=[('Bo Example', 'bo@example.test')]),
        ):
            with self.subTest(fields=fields):
                duplicate = candidate(**(dict(start=ENDED) | fields))
                snapshot, counts = self.project(original, duplicate)
                for section in ('items', 'follow_up_reviews'):
                    self.assertEqual(snapshot[section], expected[section])
                for suffix in ('canceled', 'conflicts', 'other_accounts', 'time_changed'):
                    self.assertEqual(counts['calendar_agenda_' + suffix], baseline['calendar_agenda_' + suffix])

    def test_recovered_occurrence_cannot_replace_a_main_series_master(self):
        main = candidate(subject='Planning')
        recovered = candidate(subject='会議', instance='07EA0A1B')
        snapshot, _ = self.project(main, recovered)
        self.assertEqual({(row['title'], row['recurring']) for row in snapshot['items']},
                         {('Planning', False), ('会議', True)})

    def test_recovered_series_master_stays_hidden_when_main_has_an_occurrence(self):
        main = candidate(subject='Planning', instance='07EA0A1B')
        recovered = candidate(subject='会議')
        snapshot, _ = self.project(main, recovered)
        self.assertEqual([(row['title'], row['recurring']) for row in snapshot['items']],
                         [('Planning', True)])

    def test_winning_recovered_copy_keeps_its_own_title_state(self):
        snapshot, counts = self.project(
            candidate(subject='会議'),
            candidate([('Bo Example', 'bo@example.test')], first_subject='Planning', subject='Review'))
        self.assertEqual(counts['calendar_agenda_undecoded'], 1)
        [item] = snapshot['items']
        self.assertEqual(item['title'], UNREADABLE)
        self.assertEqual(item['attendee_count'], 1)

    def test_matching_trailing_text_cannot_complete_a_subject_after_a_fake_zone(self):
        snapshot, _ = self.project(candidate(zones=(), notes=None,
                                             first_subject='P' + 'x' * 40 + 'Review',
                                             subject='Planning', after_subject=('Review',)))
        self.assertEqual([item['title'] for item in snapshot['items']], [UNREADABLE])

    def test_zoneless_fields_cannot_pose_as_zone_lengths(self):
        for fields in (
            dict(first_subject='', after_subject=('Example Room',)),
            dict(first_subject='\u5402\u0200ZReview', notes=None, after_subject=('Review',)),
        ):
            with self.subTest(fields=fields):
                snapshot, counts = self.project(candidate(zones=(), subject='Planning', **fields))
                self.assertEqual([item['title'] for item in snapshot['items']], [UNREADABLE])
                self.assertEqual(counts['calendar_agenda_undecoded'], 1)

    def test_zoneless_blank_title_survives_with_or_without_attendee_count(self):
        for omit_attendee_count in (True, False):
            with self.subTest(omit_attendee_count=omit_attendee_count):
                snapshot, counts = self.project(candidate(
                    zones=(), subject='', omit_attendee_count=omit_attendee_count))
                self.assertEqual([item['title'] for item in snapshot['items']], ['Meeting'])
                self.assertEqual(counts['calendar_agenda_undecoded'], 0)

    def test_recovered_copy_groups_add_rows_without_follow_ups_or_conflicts(self):
        for subjects in ((('会議', '商談'), ('別件', '相談')), (('会議', '会議'), ('相談', '相談'))):
            with self.subTest(subjects=subjects):
                snapshot, counts = self.project(*(
                    candidate(start=ENDED, first_subject=first, subject=second)
                    for first, second in subjects
                ))
                [item] = snapshot['items']
                self.assertFalse(item['conflict'])
                self.assertEqual(snapshot['follow_up_reviews'], [])
                self.assertEqual(counts['calendar_agenda_conflicts'], 0)

    def test_masking_at_the_byte_cap_keeps_independently_readable_meetings(self):
        rows = [candidate(identity=i, subject='Plan', omit_attendee_count=True) for i in range(549)]
        rows[1] = candidate(identity=1, start=NOW - 1, subject='Plan' + 'z' * 290, omit_attendee_count=True)
        expected, _ = self.project(*rows)
        rows[0] = candidate(identity=0, subject='Plan', notes='Plan', omit_attendee_count=True)
        snapshot, counts = self.project(*rows)
        self.assertEqual([row['key'] for row in snapshot['items']], [row['key'] for row in expected['items']])
        self.assertEqual(counts['calendar_agenda_trimmed'], 0)
        self.assertEqual(sum(row['title'] == UNREADABLE for row in snapshot['items']), 1)

    def test_conflicting_subject_slots_count_as_undecoded(self):
        snapshot, counts = self.project(candidate(
            [('Ada Example', 'ada@example.test')], first_subject='Planning',
            subject='Review', notes='Example Room'))
        self.assertEqual(counts['calendar_agenda_undecoded'], 1)
        self.assertIn({'code': 'undecoded', 'count': 1}, snapshot['warnings'])
        self.assertEqual([item['title'] for item in snapshot['items']], ['Meeting title unreadable'])

    def test_extra_field_before_notes_keeps_local_copy_and_foreign_blocker(self):
        extra = ('Extra text before notes',)
        with self.subTest(copy='configured'):
            snapshot, _ = self.project(candidate(before_notes=extra))
            self.assertEqual([item['title'] for item in snapshot['items']], [UNREADABLE])
        with self.subTest(copy='foreign'):
            snapshot, counts = self.project(candidate(), candidate(organizer='bo@example.test', before_notes=extra))
            self.assertEqual(snapshot['items'], [])
            self.assertEqual(counts['calendar_agenda_other_accounts'], 1)

    def test_legacy_copy_with_no_attendees_keeps_organizer_on_subject_conflict(self):
        snapshot, counts = self.project(candidate(
            zones=(), first_subject='Planning', subject='Review', omit_attendee_count=True))
        self.assertEqual(counts['calendar_agenda_undecoded'], 1)
        self.assertEqual([item['title'] for item in snapshot['items']], ['Meeting title unreadable'])
        self.assertEqual(snapshot['items'][0]['organizer']['email'], 'ada@example.test')

    def test_matching_subject_slots_that_repeat_the_room_leave_title_undecoded(self):
        snapshot, counts = self.project(candidate(subject='Example Room'))
        self.assertEqual(counts['calendar_agenda_undecoded'], 1)
        self.assertEqual([item['title'] for item in snapshot['items']], ['Meeting title unreadable'])

    def test_matching_subject_slots_that_repeat_notes_or_organizer_leave_title_undecoded(self):
        for subject, notes in [('Synthetic notes', 'Synthetic notes'), ('Ada Example', 'Other notes'),
                               ('ada@example.test', 'Other notes')]:
            with self.subTest(subject=subject):
                snapshot, counts = self.project(candidate(subject=subject, notes=notes))
                self.assertEqual(counts['calendar_agenda_undecoded'], 1)
                self.assertEqual([item['title'] for item in snapshot['items']], ['Meeting title unreadable'])

    def test_extra_text_cannot_supply_matching_subject_slots(self):
        for name, entry in (
            ('fake outer slots', candidate(subject='Planning', before_fields=('Format label',),
                                           after_subject=('Format label',))),
            ('fake last slot', candidate(first_subject='Planning', subject='Review', after_subject=('Planning',))),
        ):
            with self.subTest(layout=name):
                snapshot, counts = self.project(entry)
                self.assertEqual([item['title'] for item in snapshot['items']], ['Meeting title unreadable'])
                self.assertEqual(counts['calendar_agenda_undecoded'], 1)

    def test_matching_subject_slots_cannot_repeat_attendee_or_zone(self):
        for fields in (
            dict(subject='Bo Example', attendees=[('Bo Example', 'bo@example.test')]),
            dict(subject='America/Chicago'),
        ):
            with self.subTest(title=fields['subject']):
                snapshot, counts = self.project(candidate(**fields))
                self.assertEqual([item['title'] for item in snapshot['items']], [UNREADABLE])
                self.assertEqual(counts['calendar_agenda_undecoded'], 1)

    def test_clearing_a_repeated_title_preserves_main_cancellation(self):
        title = 'Canceled: Planning'
        snapshot, counts = self.project(candidate(subject=title, notes=title))
        self.assertEqual(snapshot['items'], [])
        self.assertEqual(counts['calendar_agenda_canceled'], 1)

    def test_ended_copies_with_two_unreadable_titles_keep_main_conflict(self):
        snapshot, counts = self.project(*(
            candidate(subject=title, notes=title, start=1_792_992_800)
            for title in ('Planning', 'Review')
        ))
        [item] = snapshot['items']
        self.assertEqual(item['title'], 'Meeting title unreadable')
        self.assertTrue(item['conflict'])
        self.assertEqual(counts['calendar_agenda_conflicts'], 1)
        self.assertEqual(snapshot['follow_up_reviews'], [])

    def test_warning_counts_only_unreadable_identities_before_date_filter(self):
        snapshot, counts = self.project(
            candidate(start=1_790_000_000, subject='会議'),
            candidate([('Ada Example', 'ada@example.test')], trailer=b'\0\0'),
            candidate(start=1_793_086_400, duration=-1))
        self.assertEqual(counts['calendar_agenda_undecoded'], 2)
        self.assertIn({'code': 'undecoded', 'count': 2}, snapshot['warnings'])
        self.assertEqual(snapshot['items'], [])


if __name__ == '__main__':
    unittest.main()
