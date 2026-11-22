#!/usr/bin/env python3
"""Check synthetic calendar exports. Set CARR_HXPROBE_BASELINE_BINARY to compare with main."""
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
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
        self.assertGreaterEqual(expected.total(), minimum)
        self.assertEqual(actual, expected, 'main-decoded copy evidence changed')

    def assert_agenda_matches(self, baseline, target, *, per_meeting=False):
        main_agenda = runpy.run_path(BASELINE_AGENDA)['agenda']
        main_meta, _, main_rows = _load(baseline)
        new_meta, _, new_rows = _load(target)
        new_rows = [row for row in new_rows if row['legacy_subject'] is not None]

        if per_meeting:
            groups = defaultdict(lambda: ([], []))
            for i, rows in enumerate((main_rows, new_rows)):
                for row in rows:
                    groups[row['goid']][i].append(row)
            cases = [(key, left, right, {}, {}) for key, (left, right) in groups.items()]
        else:
            cases = [('fixture', main_rows, new_rows, main_meta, new_meta)]

        now = datetime.fromtimestamp(NOW, timezone.utc)
        for key, left, right, left_meta, right_meta in cases:
            expected, main_counts = main_agenda(left, left_meta, SETTINGS, now)
            actual, new_counts = agenda(right, right_meta, SETTINGS, now)
            unreadable = {(row['goid'], row['start_unix']) for row in right if row['subject_undecoded']}
            self.assertEqual(new_counts.pop('calendar_agenda_undecoded'),
                             main_counts.pop('calendar_agenda_undecoded') + len(unreadable), key)
            self.assertEqual(new_counts, main_counts, key)
            for output in (expected, actual):
                output['warnings'] = [warning for warning in output['warnings'] if warning['code'] != 'undecoded']
            for section in ('items', 'follow_up_reviews'):
                for before, after in zip(expected[section], actual[section]):
                    if after['title'] == UNREADABLE:
                        before['title'] = UNREADABLE
            self.assertEqual(actual, expected, key)

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
            (('America/Chicago', 'US/Central'), ('', ''), ()),
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
            timing = dict(start=NOW - (i % 4) * 7_200, identity=i)
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
        with tempfile.TemporaryDirectory() as tmp:
            target = self.export(Path(tmp), entries, generated=True)
            with closing(sqlite3.connect(target)) as db:
                identities = db.execute("SELECT value FROM meta WHERE key = 'event_identities'").fetchone()
                self.assertEqual(identities, (i + 1,))

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
        self.assertEqual(counts['calendar_agenda_canceled'], 1)
        self.assertEqual(snapshot['items'], [])

    def test_address_free_cancellation_hides_an_undecoded_account_copy(self):
        snapshot, counts = self.project(
            candidate(first_subject='Planning', subject='Review'),
            candidate(subject='会議', first_subject='Cancelled: 会議', organizer=''))
        self.assertEqual(counts['calendar_agenda_undecoded'], 1)
        self.assertEqual(counts['calendar_agenda_other_accounts'], 1)
        self.assertEqual(snapshot['items'], [])

    def test_recovered_address_free_duplicate_blocks_the_account_copy(self):
        original = candidate(subject='Plan')
        duplicate = candidate(subject='会議 📅', organizer='')
        snapshot, counts = self.project(original, duplicate)
        self.assertEqual(counts['calendar_agenda_undecoded'], 0)
        self.assertEqual(counts['calendar_agenda_other_accounts'], 1)
        self.assertEqual(snapshot['items'], [])

    def test_foreign_duplicate_still_blocks_the_account_copy(self):
        snapshot, counts = self.project(
            candidate(), candidate(subject='会議 📅', organizer='bo@example.test'))
        self.assertEqual(counts['calendar_agenda_undecoded'], 0)
        self.assertEqual(counts['calendar_agenda_other_accounts'], 1)
        self.assertEqual(snapshot['items'], [])

    def test_foreign_duplicate_blocks_even_when_its_subject_is_undecoded(self):
        original = candidate(subject='Plan')
        for first_subject in ('Planning', '\ud800'):
            for evidence in ({'organizer': 'bo@example.test'},
                             {'organizer': '', 'attendees': [('Bo Example', 'bo@example.test')]}):
                with self.subTest(first_subject=first_subject, evidence=evidence):
                    snapshot, counts = self.project(
                        original, candidate(first_subject=first_subject, subject='Review', **evidence))
                    self.assertEqual(snapshot['items'], [])
                    self.assertEqual(counts['calendar_agenda_other_accounts'], 1)
                    self.assertIn({'code': 'undecoded', 'count': 1}, snapshot['warnings'])

    def test_recovered_address_free_duplicate_blocks_with_undecoded_title(self):
        snapshot, counts = self.project(
            candidate(subject='Plan'),
            candidate(first_subject='Planning', subject='Review', organizer=''))
        self.assertEqual(counts['calendar_agenda_undecoded'], 1)
        self.assertEqual(counts['calendar_agenda_other_accounts'], 1)
        self.assertEqual(snapshot['items'], [])

    def test_undecoded_winning_copy_keeps_its_own_title_state(self):
        snapshot, counts = self.project(
            candidate(subject='A readable title'),
            candidate([('Bo Example', 'bo@example.test')], first_subject='Planning', subject='Review'))
        self.assertEqual(counts['calendar_agenda_undecoded'], 1)
        [item] = snapshot['items']
        self.assertEqual(item['title'], 'Meeting title unreadable')
        self.assertEqual(item['attendee_count'], 1)

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
