#!/usr/bin/env python3
"""Offline tests for the Outlook intake. Made-up fixtures and fake ports only:
no Outlook store, no record layer, no model."""
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import outlook_intake as intake

NOW = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)
SCHEMA = """
CREATE TABLE messages (id INTEGER PRIMARY KEY, block INTEGER, sender TEXT, sender_name TEXT,
  recipients TEXT, subject TEXT, message_id TEXT, body TEXT, html TEXT, body_words INTEGER,
  html_bytes INTEGER, body_kind TEXT, sent_unix INTEGER, sent_utc TEXT, subject_inherited INTEGER,
  preview TEXT, body_source TEXT, copies INTEGER, copy_key TEXT);
CREATE TABLE events (id INTEGER PRIMARY KEY, goid TEXT NOT NULL, start_unix INTEGER NOT NULL,
  end_unix INTEGER NOT NULL, start_utc TEXT NOT NULL, end_utc TEXT NOT NULL, subject TEXT,
  location TEXT, organizer_name TEXT, organizer_email TEXT, body TEXT, UNIQUE (goid, start_unix));
CREATE TABLE event_attendees (event_id INTEGER NOT NULL, name TEXT, email TEXT);
CREATE TABLE meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
"""
CLIENT = intake.Contact(email='pat@example.com', ref='C-901', name='Pat Example')
LEAD = intake.Contact(email='lee@example.org', ref='L-902', name='Lee Sample')
OWNER = 'owner@' + intake.INTERNAL_DOMAIN
PARTNER = 'partner@' + intake.INTERNAL_DOMAIN


def ts(delta: timedelta) -> int:
    return int((NOW + delta).timestamp())


def mail(sender, sent, subject='Suite question', body='Can we meet Thursday?', name=None,
         recipients=OWNER, kind='full', copy_key=None):
    return dict(sender=sender, sender_name=name, recipients=recipients, subject=subject,
                body=body, body_kind=kind, sent_unix=ts(sent),
                copy_key=copy_key or f'{sender}\x1f{subject.lower()}\x1f{body.lower()}')


def ident(m):
    return intake.mail_identity(m['copy_key'], m['sender'], m['subject'], m['sent_unix'])[1]


def event(goid, start, minutes=60, subject='Site visit', attendees=(), organizer=('Owner', OWNER)):
    return dict(goid=goid, start_unix=ts(start), end_unix=ts(start + timedelta(minutes=minutes)),
                subject=subject, location='Example Room', organizer_name=organizer[0],
                organizer_email=organizer[1], body='Agenda', attendees=list(attendees))


def write_db(path: Path, mails=(), events=(), found=1000, verified=999, identities=None, decoded=None):
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    for m in mails:
        con.execute('INSERT INTO messages(sender,sender_name,recipients,subject,body,preview,'
                    'body_kind,sent_unix,copy_key) VALUES (?,?,?,?,?,?,?,?,?)',
                    (m['sender'], m['sender_name'], m['recipients'], m['subject'],
                     m['body'] if m['body_kind'] == 'full' else '', m['body'][:200],
                     m['body_kind'], m['sent_unix'], m['copy_key']))
    for e in events:
        cur = con.execute('INSERT INTO events(goid,start_unix,end_unix,start_utc,end_utc,subject,location,'
                          'organizer_name,organizer_email,body) VALUES (?,?,?,?,?,?,?,?,?,?)',
                          (e['goid'], e['start_unix'], e['end_unix'], '', '', e['subject'], e['location'],
                           e['organizer_name'], e['organizer_email'], e['body']))
        for name, email in e['attendees']:
            con.execute('INSERT INTO event_attendees VALUES (?,?,?)', (cur.lastrowid, name, email))
    con.executemany('INSERT INTO meta VALUES (?,?)',
                    [('blocks_found', found), ('blocks_verified', verified), ('messages', len(mails)),
                     ('event_identities', len(events) if identities is None else identities),
                     ('events_decoded', len(events) if decoded is None else decoded)])
    con.commit()
    con.close()


class Fake:
    """The record layer, the contact export, the model and the decoder."""

    def __init__(self, test):
        self.test = test
        self.calls = []
        self.model_calls = []
        self.decodes = 0
        self.fixture = None
        self.parties = {}
        self.timeline = {}
        self.fail_verbs = set()
        self.model_reply = {'next_step': None, 'due_on': None}
        self.contacts_rows = [CLIENT, LEAD]
        self.now = NOW
        self.on_decode = None

    def carr(self, verb, args):
        self.calls.append((verb, args))
        if verb in self.fail_verbs:
            raise intake.CarrError(f'{verb} refused')
        if verb == 'find':
            return {'parties': self.parties.get(args['query'], []), 'deals': []}
        if verb == 'catch-me-up':
            return {'timeline': self.timeline.get(args['ref'], [])}
        return {'ok': True}

    def contacts(self):
        return list(self.contacts_rows)

    def next_step(self, mail, today):
        self.model_calls.append(mail)
        if isinstance(self.model_reply, Exception):
            raise self.model_reply
        return self.model_reply

    def decode(self, store_copy, files_copy, db_path):
        self.decodes += 1
        self.test.assertTrue(store_copy.exists(), 'decoder reads a copy')
        self.test.assertNotEqual(store_copy, self.test.store, 'never the live store')
        self.test.assertEqual(stat.S_IMODE(store_copy.stat().st_mode), 0o600, 'copy is 0600')
        self.test.assertEqual(stat.S_IMODE(store_copy.parent.stat().st_mode), 0o700, 'copy dir is 0700')
        self.test.assertEqual(files_copy.parent, store_copy.parent, 'sidecars are read from the private copy')
        sidecar = files_copy / 'S0' / '845' / 'EFMData' / '7.dat'
        self.test.assertEqual(sidecar.read_bytes(), b'gzip-body', 'the sidecar is snapshotted')
        self.test.assertEqual(stat.S_IMODE(sidecar.stat().st_mode), 0o600, 'sidecar copy is 0600')
        if self.on_decode:
            self.on_decode(store_copy.parent)
        db_path.write_bytes(self.fixture.read_bytes())

    def ports(self):
        return intake.Ports(carr=self.carr, contacts=self.contacts, next_step=self.next_step,
                            decode=self.decode, now=lambda: self.now)

    def writes(self, verb=None):
        return [(v, a) for v, a in self.calls if v in intake.WRITE_VERBS and (verb is None or v == verb)]


class IntakeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / 'repo'
        self.home = self.tmp / 'home'
        self.store = self.home / 'Library/Group Containers/UBF8T346G9.Office/Outlook/Outlook 15 Profiles/Main Identity/HxStore.hxd'
        self.store.parent.mkdir(parents=True)
        self.store.write_bytes(b'Nostromo-fixture')
        live = self.store.parent / 'Files' / 'S0' / '845' / 'EFMData'
        live.mkdir(parents=True)
        (live / '7.dat').write_bytes(b'gzip-body')
        self.fake = Fake(self)
        self.fake.fixture = self.tmp / 'fixture.db'

    def run_intake(self, mails=(), events=(), touch=True, **db):
        if self.fake.fixture.exists():
            self.fake.fixture.unlink()
        write_db(self.fake.fixture, mails, events, **db)
        if touch:
            self.store.write_bytes(self.store.read_bytes() + b'.')
        return intake.run(self.repo, self.home, self.fake.ports())

    def out(self):
        return self.repo / intake.OUT

    def report_text(self):
        return '\n'.join(p.read_text() for p in sorted(self.out().glob('report-*.md')))

    def test_inbound_mail_from_a_known_address_logs_email_in_with_a_uuid5_key(self):
        m = mail('pat@example.com', -timedelta(hours=1), name='Pat Example')
        result = self.run_intake([m])
        self.assertEqual(result.state, 'succeeded')
        [(verb, args)] = self.fake.writes('log-activity')
        self.assertEqual(args['ref'], 'C-901')
        self.assertEqual(args['kind'], 'email_in')
        self.assertIn('Suite question', args['summary'])
        self.assertIn('Pat Example', args['summary'])
        self.assertEqual(args['detail'], 'Can we meet Thursday?')
        self.assertEqual(args['occurred_at'], (NOW - timedelta(hours=1)).isoformat())
        self.assertEqual(args['idempotency_key'],
                         str(uuid.uuid5(uuid.NAMESPACE_URL, f'log-activity|C-901|{ident(m)}')))

    def test_mail_sent_from_the_internal_domain_is_email_out(self):
        self.run_intake([mail(OWNER, -timedelta(hours=1), recipients='lee@example.org')])
        [(_, args)] = self.fake.writes('log-activity')
        self.assertEqual((args['ref'], args['kind']), ('L-902', 'email_out'))

    def test_internal_addresses_are_never_matched_or_listed(self):
        self.fake.contacts_rows.append(intake.Contact(PARTNER, 'V-903', 'Internal Row'))
        self.run_intake([mail(PARTNER, -timedelta(hours=1), recipients=OWNER)])
        self.assertEqual(self.fake.writes(), [])
        self.assertNotIn('carr.us', self.report_text().split('## Unmatched', 1)[-1])

    def test_a_second_run_over_the_same_mail_writes_nothing(self):
        mails = [mail('pat@example.com', -timedelta(hours=1))]
        self.run_intake(mails)
        before = len(self.fake.writes())
        result = self.run_intake(mails)
        self.assertEqual(result.state, 'succeeded')
        self.assertEqual(len(self.fake.writes()), before, 'each email is handled once')

    def test_an_unchanged_store_is_neither_copied_nor_decoded_and_calls_nothing(self):
        self.run_intake([mail('pat@example.com', -timedelta(hours=1))])
        calls, decodes, models = len(self.fake.calls), self.fake.decodes, len(self.fake.model_calls)
        result = self.run_intake(touch=False)
        self.assertEqual((result.state, result.reason), ('succeeded', 'store_unchanged'))
        self.assertEqual(self.fake.decodes, decodes, 'no copy or decode')
        self.assertEqual(len(self.fake.calls), calls, 'no record-layer call')
        self.assertEqual(len(self.fake.model_calls), models, 'no model call')

    def test_a_changed_store_with_nothing_new_is_a_quiet_success_with_no_calls(self):
        result = self.run_intake([mail('pat@example.com', -timedelta(days=5))])
        self.assertEqual((result.state, result.reason), ('succeeded', 'nothing_new'))
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self.fake.model_calls, [])

    def test_first_run_backfills_48_hours_only(self):
        self.run_intake([mail('pat@example.com', -timedelta(hours=47)),
                         mail('lee@example.org', -timedelta(hours=49), subject='Older')])
        self.assertEqual([a['ref'] for _, a in self.fake.writes('log-activity')], ['C-901'])

    def test_late_synced_mail_inside_the_lookback_is_still_handled(self):
        self.run_intake([mail('pat@example.com', -timedelta(hours=1))])
        self.fake.now = NOW + timedelta(minutes=30)
        self.run_intake([mail('pat@example.com', -timedelta(hours=1)),
                         mail('lee@example.org', -timedelta(hours=2), subject='Delivered late')])
        self.assertEqual([a['ref'] for _, a in self.fake.writes('log-activity')], ['C-901', 'L-902'])

    def test_unmatched_people_are_reported_by_name_and_domain_only(self):
        self.run_intake([mail('stranger@unknown.example', -timedelta(hours=1), name='Sam Stranger')])
        self.assertEqual(self.fake.writes(), [])
        text = self.report_text()
        self.assertIn('Sam Stranger', text)
        self.assertIn('unknown.example', text)
        self.assertNotIn('stranger@unknown.example', text)

    def test_report_and_state_are_private(self):
        self.run_intake([mail('pat@example.com', -timedelta(hours=1))])
        self.assertEqual(stat.S_IMODE(self.out().stat().st_mode), 0o700)
        for path in self.out().iterdir():
            if path.is_file():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, path.name)

    def test_the_store_copy_and_decoded_database_are_deleted_after_a_run(self):
        self.run_intake([mail('pat@example.com', -timedelta(hours=1))])
        self.assertEqual(sorted(p.name for p in self.out().iterdir() if p.is_dir()), [])
        self.assertEqual([p for p in self.out().rglob('*') if p.suffix in ('.hxd', '.db')], [])

    def test_a_unique_exact_name_matches_when_no_address_does(self):
        self.fake.parties['Rae Named'] = [{'name': 'Rae Named', 'ref': 'L-904', 'kind': 'lead', 'merged': False}]
        self.run_intake([mail('rae@elsewhere.example', -timedelta(hours=1), name='Rae Named')])
        [(_, args)] = self.fake.writes('log-activity')
        self.assertEqual(args['ref'], 'L-904')

    def test_an_ambiguous_name_is_unmatched(self):
        self.fake.parties['Rae Named'] = [
            {'name': 'Rae Named', 'ref': 'L-904', 'kind': 'lead', 'merged': False},
            {'name': 'Rae Named', 'ref': 'C-905', 'kind': 'client', 'merged': False}]
        self.run_intake([mail('rae@elsewhere.example', -timedelta(hours=1), name='Rae Named')])
        self.assertEqual(self.fake.writes(), [])
        self.assertIn('Rae Named', self.report_text())

    def test_a_fuzzy_or_merged_name_hit_is_not_a_match(self):
        self.fake.parties['Rae Named'] = [
            {'name': 'Rae Namedson', 'ref': 'L-904', 'kind': 'lead', 'merged': False},
            {'name': 'Rae Named', 'ref': 'L-906', 'kind': 'lead', 'merged': True}]
        self.run_intake([mail('rae@elsewhere.example', -timedelta(hours=1), name='Rae Named')])
        self.assertEqual(self.fake.writes(), [])

    def test_an_address_shared_by_two_records_is_ambiguous(self):
        self.fake.contacts_rows.append(intake.Contact('pat@example.com', 'L-907', 'Pat Example'))
        self.run_intake([mail('pat@example.com', -timedelta(hours=1))])
        self.assertEqual(self.fake.writes(), [])

    def test_duplicate_copies_collapse_and_the_full_body_wins(self):
        self.run_intake([mail('pat@example.com', -timedelta(hours=1), body='the whole', kind='preview',
                              copy_key='k1'),
                         mail('pat@example.com', -timedelta(hours=1) + timedelta(seconds=20),
                              body='the whole message body', copy_key='k1')])
        [(_, args)] = self.fake.writes('log-activity')
        self.assertEqual(args['detail'], 'the whole message body')

    def test_a_sender_name_that_is_really_the_subject_or_a_file_is_dropped(self):
        self.run_intake([mail('pat@example.com', -timedelta(hours=2), name='Suite question'),
                         mail('pat@example.com', -timedelta(hours=1), subject='Plans', name='floorplan.pdf')])
        summaries = [a['summary'] for _, a in self.fake.writes('log-activity')]
        self.assertEqual(len(summaries), 2)
        for s in summaries:
            self.assertIn('pat@example.com', s)
            self.assertNotIn('floorplan.pdf', s)

    def test_the_full_body_is_logged_untruncated(self):
        body = 'Paragraph one.\n\n' + 'x' * 20000
        self.run_intake([mail('pat@example.com', -timedelta(hours=1), body=body)])
        [(_, args)] = self.fake.writes('log-activity')
        self.assertEqual(args['detail'], body)

    def test_a_preview_only_email_logs_the_preview_and_is_listed_for_refresh(self):
        m = mail('pat@example.com', -timedelta(hours=1), body='Can we meet Thursday at ten', kind='preview')
        result = self.run_intake([m])
        [(_, args)] = self.fake.writes('log-activity')
        self.assertEqual(args['detail'], 'Can we meet Thursday at ten')
        self.assertEqual(result.counts['preview_only'], 1)
        self.assertIn('preview_only', self.report_text())
        path = self.out() / intake.REFRESH_LIST
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        [entry] = json.loads(path.read_text())
        self.assertEqual(entry, {'message_id': ident(m), 'sent_utc': (NOW - timedelta(hours=1)).isoformat(),
                                 'subject_hash': hashlib.sha256(b'Suite question').hexdigest(),
                                 'sender_email': 'pat@example.com'})

    def test_once_the_full_body_decodes_it_is_logged_as_a_note_and_leaves_the_list(self):
        preview = mail('pat@example.com', -timedelta(hours=1), body='Can we meet', kind='preview', copy_key='k2')
        self.run_intake([preview])
        path = self.out() / intake.REFRESH_LIST
        entries = json.loads(path.read_text())
        entries[0]['opened_at'] = NOW.isoformat()
        path.write_text(json.dumps(entries))
        self.run_intake([preview, mail('pat@example.com', -timedelta(hours=1) + timedelta(seconds=5),
                                       body='Can we meet Thursday? Full text.', copy_key='k2')])
        self.assertEqual(json.loads(path.read_text()), [], 'refreshed email leaves the list')
        notes = [a for _, a in self.fake.writes('log-activity') if a['kind'] == 'note']
        self.assertEqual([n['detail'] for n in notes], ['Can we meet Thursday? Full text.'])
        self.assertEqual(notes[0]['idempotency_key'],
                         str(uuid.uuid5(uuid.NAMESPACE_URL, f'log-activity|C-901|fullbody|{ident(preview)}')))
        self.assertEqual(len(self.fake.writes('log-activity')), 2, 'the email itself is not logged twice')

    def test_triage_opened_at_survives_a_rewrite(self):
        first = mail('pat@example.com', -timedelta(hours=2), body='Can we meet', kind='preview', copy_key='k3')
        self.run_intake([first])
        path = self.out() / intake.REFRESH_LIST
        entries = json.loads(path.read_text())
        entries[0]['opened_at'] = NOW.isoformat()
        path.write_text(json.dumps(entries))
        self.run_intake([first, mail('lee@example.org', -timedelta(hours=1), body='Hello there', kind='preview',
                                     subject='Other')])
        opened = {e['message_id']: e.get('opened_at') for e in json.loads(path.read_text())}
        self.assertEqual(opened[ident(first)], NOW.isoformat())
        self.assertEqual(len(opened), 2)

    def test_one_model_call_per_new_matched_email_and_none_for_unmatched(self):
        self.run_intake([mail('pat@example.com', -timedelta(hours=1)),
                         mail('stranger@unknown.example', -timedelta(hours=1), subject='Spam')])
        self.assertEqual(len(self.fake.model_calls), 1)

    def test_a_next_step_sets_a_ball_when_none_exists(self):
        self.fake.model_reply = {'next_step': 'Send the floor plan', 'due_on': '2026-10-22'}
        m = mail('pat@example.com', -timedelta(hours=1))
        self.run_intake([m])
        [(_, args)] = self.fake.writes('set-next-action')
        self.assertEqual((args['ref'], args['description'], args['due_on']),
                         ('C-901', 'Send the floor plan', '2026-10-22'))
        self.assertEqual(args['idempotency_key'],
                         str(uuid.uuid5(uuid.NAMESPACE_URL, f'set-next-action|C-901|next|{ident(m)}')))
        self.assertIn('no prior ball', self.report_text())

    def test_a_ball_newer_than_the_email_is_kept(self):
        self.fake.model_reply = {'next_step': 'Send the floor plan', 'due_on': None}
        self.fake.timeline['C-901'] = [{'verb': 'set-next-action', 'summary': 'ball → joe-local: call back',
                                        'occurred_at': (NOW - timedelta(minutes=10)).isoformat()}]
        self.run_intake([mail('pat@example.com', -timedelta(hours=1))])
        self.assertEqual(self.fake.writes('set-next-action'), [])
        self.assertIn('kept newer ball', self.report_text())

    def test_a_ball_older_than_the_email_is_replaced(self):
        self.fake.model_reply = {'next_step': 'Send the floor plan', 'due_on': None}
        self.fake.timeline['C-901'] = [{'verb': 'set-next-action', 'summary': 'ball → joe: call back',
                                        'occurred_at': (NOW - timedelta(days=3)).isoformat()}]
        self.run_intake([mail('pat@example.com', -timedelta(hours=1))])
        [(_, args)] = self.fake.writes('set-next-action')
        self.assertEqual(args['due_on'], '2026-10-20', 'an undated step is due today, so it shows today')
        self.assertIn('replaced ball', self.report_text())

    def test_a_completed_ball_counts_as_no_ball(self):
        self.fake.model_reply = {'next_step': 'Send the floor plan', 'due_on': None}
        self.fake.timeline['C-901'] = [
            {'verb': 'complete-action', 'summary': 'done', 'occurred_at': (NOW - timedelta(minutes=5)).isoformat()},
            {'verb': 'set-next-action', 'summary': 'ball → joe: call back',
             'occurred_at': (NOW - timedelta(minutes=10)).isoformat()}]
        self.run_intake([mail('pat@example.com', -timedelta(hours=1))])
        self.assertEqual(len(self.fake.writes('set-next-action')), 1)

    def test_the_later_of_two_emails_in_one_run_owns_the_ball(self):
        self.fake.model_reply = {'next_step': 'Reply', 'due_on': None}
        second = mail('pat@example.com', -timedelta(hours=1), subject='Second')
        self.run_intake([mail('pat@example.com', -timedelta(hours=2), subject='First'), second])
        keys = [a['idempotency_key'] for _, a in self.fake.writes('set-next-action')]
        self.assertEqual(keys[-1], str(uuid.uuid5(uuid.NAMESPACE_URL, f'set-next-action|C-901|next|{ident(second)}')))

    def test_a_model_failure_logs_the_activity_fails_the_run_and_retries_the_step(self):
        mails = [mail('pat@example.com', -timedelta(hours=1))]
        self.fake.model_reply = RuntimeError('model down')
        result = self.run_intake(mails)
        self.assertEqual((result.state, result.reason), ('failed', 'model_failed'))
        self.assertEqual(len(self.fake.writes('log-activity')), 1)
        self.assertEqual(result.counts['model_failed'], 1)
        self.fake.model_reply = {'next_step': 'Send the floor plan', 'due_on': None}
        self.fake.now = NOW + timedelta(minutes=30)
        again = self.run_intake(mails, touch=False)
        self.assertEqual(again.state, 'succeeded')
        self.assertEqual(len(self.fake.writes('log-activity')), 1, 'the activity is not logged twice')
        self.assertEqual(len(self.fake.writes('set-next-action')), 1, 'the step is retried')

    def test_a_failed_ball_write_for_an_email_is_retried(self):
        mails = [mail('pat@example.com', -timedelta(hours=1))]
        self.fake.model_reply = {'next_step': 'Send the floor plan', 'due_on': None}
        self.fake.fail_verbs = {'set-next-action'}
        first = self.run_intake(mails)
        self.assertEqual((first.state, first.reason), ('failed', 'writes_failed'))
        self.fake.fail_verbs = set()
        self.fake.now = NOW + timedelta(minutes=30)
        second = self.run_intake(mails, touch=False)
        self.assertEqual(second.state, 'succeeded')
        keys = [a['idempotency_key'] for v, a in self.fake.calls if v == 'set-next-action']
        self.assertEqual(len(keys), 2)
        self.assertEqual(keys[0], keys[1], 'the retry reuses the key')
        self.assertEqual(len(self.fake.writes('log-activity')), 1)

    def test_a_failed_ball_write_for_an_upcoming_meeting_is_retried(self):
        events = [event('G7', timedelta(days=2), attendees=[('Pat Example', 'pat@example.com')])]
        self.fake.fail_verbs = {'set-next-action'}
        first = self.run_intake(events=events)
        self.assertEqual(first.state, 'failed')
        self.fake.fail_verbs = set()
        self.fake.now = NOW + timedelta(minutes=30)
        second = self.run_intake(events=events, touch=False)
        self.assertEqual(second.state, 'succeeded')
        self.assertEqual(len([1 for v, _ in self.fake.calls if v == 'set-next-action']), 2)

    def test_outside_seven_to_seven_nothing_is_decoded_or_called(self):
        local = intake.LOCAL
        for when in (datetime(2026, 10, 20, 0, 0, tzinfo=local), datetime(2026, 10, 20, 6, 59, tzinfo=local),
                     datetime(2026, 10, 20, 19, 31, tzinfo=local)):
            self.fake.now = when
            result = self.run_intake([mail('pat@example.com', -timedelta(hours=1))])
            self.assertEqual((result.state, result.reason), ('skipped', 'outside_window'), when)
            self.assertEqual(intake.EXIT[result.state], 78)
        self.assertEqual((self.fake.decodes, self.fake.calls), (0, []))
        self.assertFalse((self.out() / intake.STATE).exists())
        self.fake.now = datetime(2026, 10, 20, 7, 0, tzinfo=local)
        self.assertEqual(self.run_intake([mail('pat@example.com', -timedelta(hours=1))]).state, 'succeeded')
        self.fake.now = datetime(2026, 10, 20, 19, 0, 30, tzinfo=local)
        self.assertNotEqual(self.run_intake([mail('lee@example.org', -timedelta(hours=1))]).reason, 'outside_window')

    def test_calendar_objects_with_no_decoded_event_fail_without_advancing(self):
        result = self.run_intake([mail('pat@example.com', -timedelta(hours=1))], identities=5, decoded=0)
        self.assertEqual((result.state, result.reason), ('failed', 'calendar_decode_empty'))
        self.assertEqual(self.fake.calls, [])
        self.assertFalse((self.out() / intake.STATE).exists(), 'the watermark does not move')

    def test_an_upcoming_meeting_is_logged_once_over_even_when_the_store_is_unchanged(self):
        events = [event('G8', timedelta(hours=1), attendees=[('Pat Example', 'pat@example.com')])]
        self.run_intake(events=events)
        self.fake.now = NOW + timedelta(minutes=30)
        quiet = self.run_intake(events=events, touch=False)
        self.assertEqual(quiet.reason, 'store_unchanged', 'nothing is due yet')
        self.fake.now = NOW + timedelta(hours=3)
        self.run_intake(events=events, touch=False)
        self.assertEqual([a['kind'] for _, a in self.fake.writes('log-activity')], ['meeting'])

    def test_a_failed_cleanup_fails_the_run_loudly(self):
        def jam(work):
            locked = work / 'locked'
            locked.mkdir()
            (locked / 'f').write_bytes(b'x')
            os.chmod(locked, 0o500)
            self.addCleanup(os.chmod, locked, 0o700)
        self.fake.on_decode = jam
        result = self.run_intake([mail('pat@example.com', -timedelta(hours=1))])
        self.assertEqual((result.state, result.reason), ('failed', 'cleanup_failed'))
        self.assertEqual(self.fake.calls, [])

    def test_private_files_are_narrowed_to_0600_even_when_they_already_existed(self):
        self.out().mkdir(parents=True)
        for name in ('lock', intake.STATE, intake.REFRESH_LIST):
            path = self.out() / name
            path.write_text('[]' if name == intake.REFRESH_LIST else '{}')
            os.chmod(path, 0o644)
        self.run_intake([mail('pat@example.com', -timedelta(hours=1), kind='preview', body='Can we meet')])
        for path in self.out().iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, path.name)

    def test_a_malformed_model_reply_sets_no_ball(self):
        self.fake.model_reply = {'next_step': 'Reply', 'due_on': 'next tuesday'}
        result = self.run_intake([mail('pat@example.com', -timedelta(hours=1))])
        self.assertEqual(self.fake.writes('set-next-action'), [])
        self.assertEqual(result.counts['model_failed'], 1)

    def test_a_past_meeting_logs_a_meeting_and_a_tour_title_logs_a_tour(self):
        self.run_intake(events=[
            event('G1', -timedelta(hours=3), attendees=[('Pat Example', 'pat@example.com')]),
            event('G2', -timedelta(hours=6), subject='Tour of Elm suite', attendees=[('Lee Sample', 'lee@example.org')])])
        kinds = {a['ref']: a['kind'] for _, a in self.fake.writes('log-activity')}
        self.assertEqual(kinds, {'C-901': 'meeting', 'L-902': 'tour'})
        key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"log-activity|C-901|G1|{ts(-timedelta(hours=3))}"))
        self.assertIn(key, [a['idempotency_key'] for _, a in self.fake.writes('log-activity')])

    def test_an_upcoming_meeting_sets_a_dated_ball(self):
        self.run_intake(events=[event('G3', timedelta(days=2), attendees=[('Pat Example', 'pat@example.com')])])
        [(_, args)] = self.fake.writes('set-next-action')
        self.assertEqual(args['due_on'], '2026-10-22')
        self.assertIn('Pat Example', args['description'])
        self.assertIn('Site visit', args['description'])
        self.assertEqual(self.fake.writes('log-activity'), [])

    def test_meetings_beyond_seven_days_are_skipped_and_an_outside_organizer_counts(self):
        self.run_intake(events=[event('G4', timedelta(days=9), attendees=[('Pat Example', 'pat@example.com')]),
                                event('G5', timedelta(days=1), organizer=('Pat Example', 'pat@example.com'))])
        [(_, args)] = self.fake.writes('set-next-action')
        self.assertIn('Site visit', args['description'])
        self.assertEqual(args['ref'], 'C-901')

    def test_an_upcoming_meeting_becomes_an_activity_once_it_is_over(self):
        events = [event('G6', timedelta(hours=1), attendees=[('Pat Example', 'pat@example.com')])]
        self.run_intake(events=events)
        self.fake.now = NOW + timedelta(hours=3)
        self.run_intake(events=events)
        self.assertEqual(len(self.fake.writes('set-next-action')), 1)
        self.assertEqual(len(self.fake.writes('log-activity')), 1)
        self.fake.now = NOW + timedelta(hours=4)
        self.run_intake(events=events)
        self.assertEqual(len(self.fake.writes('log-activity')), 1, 'once only')

    def test_low_verification_fails_without_calling_the_record_layer(self):
        result = self.run_intake([mail('pat@example.com', -timedelta(hours=1))], found=1000, verified=900)
        self.assertEqual((result.state, result.reason), ('failed', 'store_verification_low'))
        self.assertEqual(self.fake.calls, [])

    def test_a_missing_store_fails(self):
        self.store.unlink()
        result = intake.run(self.repo, self.home, self.fake.ports())
        self.assertEqual((result.state, result.reason), ('failed', 'store_missing'))

    def test_a_decoder_failure_fails_and_cleans_up(self):
        def broken(store_copy, files_copy, db_path):
            raise intake.DecodeError('exit 101')
        ports = self.fake.ports()
        ports.decode = broken
        self.store.write_bytes(b'changed')
        result = intake.run(self.repo, self.home, ports)
        self.assertEqual((result.state, result.reason), ('failed', 'decode_failed'))
        self.assertEqual([p for p in self.out().iterdir() if p.is_dir()], [])

    def test_unreachable_contacts_fail_loudly(self):
        def down():
            raise intake.CarrError('views unreachable')
        ports = self.fake.ports()
        ports.contacts = down
        write_db(self.fake.fixture, [mail('pat@example.com', -timedelta(hours=1))])
        result = intake.run(self.repo, self.home, ports)
        self.assertEqual((result.state, result.reason), ('failed', 'carr_unreachable'))

    def test_a_failed_write_fails_the_run_and_is_retried_next_time(self):
        self.fake.fail_verbs = {'log-activity'}
        first = self.run_intake([mail('pat@example.com', -timedelta(hours=1))])
        self.assertEqual((first.state, first.reason), ('failed', 'writes_failed'))
        self.fake.fail_verbs = set()
        self.fake.now = NOW + timedelta(minutes=30)
        second = self.run_intake([mail('pat@example.com', -timedelta(hours=1))])
        self.assertEqual(second.state, 'succeeded')
        keys = [a['idempotency_key'] for v, a in self.fake.calls if v == 'log-activity']
        self.assertEqual(len(keys), 2)
        self.assertEqual(keys[0], keys[1], 'the retry reuses the key')

    def test_a_held_lock_skips(self):
        self.out().mkdir(parents=True)
        with open(self.out() / 'lock', 'w') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = intake.run(self.repo, self.home, self.fake.ports())
        self.assertEqual((result.state, result.reason), ('skipped', 'previous_run_active'))
        self.assertEqual(intake.EXIT[result.state], 78)

    def test_the_status_line_carries_no_mail_content(self):
        result = self.run_intake([mail('pat@example.com', -timedelta(hours=1), name='Pat Example')])
        line = intake.status_line(result)
        for secret in ('pat@example.com', 'Pat Example', 'Suite question', 'C-901'):
            self.assertNotIn(secret, line)
        self.assertTrue(line.startswith('outlook-intake succeeded'))


class ScheduleTest(unittest.TestCase):
    def test_the_plist_runs_every_half_hour_from_seven_to_seven_under_the_scheduler(self):
        import plistlib
        from lib.launchd_calendar import calendar_for_window
        plist = plistlib.loads((ROOT / 'ops/launchd/com.carr.outlook-intake.plist').read_bytes())
        self.assertEqual(plist['StartCalendarInterval'], calendar_for_window((7, 0), (19, 0), 30))
        self.assertEqual(len(plist['StartCalendarInterval']), 25)
        self.assertNotIn('RunAtLoad', plist)
        self.assertNotIn('StartInterval', plist)
        self.assertEqual(plist['ProgramArguments'][1:4],
                         ['{{REPO}}/bin/run-scheduled.sh', 'outlook-intake', 'outlook.intake'])
        services = json.loads((ROOT / 'ops/config/services.json').read_text())['services']
        [env] = [s for s in services if s['key'] == 'outlook-intake'][0]['environments']
        self.assertEqual(env['runs_on'], 'secondary')
        self.assertGreaterEqual(env['expected_cadence_seconds'] + env['cadence_grace_seconds'], 12 * 3600 + 1800,
                                'the overnight stand-down plus the spool flush is not stale')
        self.assertEqual(plist['Umask'], 0o077)

    def test_the_installer_never_loads_the_job_and_prepares_a_private_log(self):
        text = (ROOT / 'bin/install-outlook-intake.sh').read_text()
        self.assertNotRegex(text, r'(?m)^\s*/bin/launchctl\b', 'the operator loads it')
        self.assertNotRegex(text, r'(?m)^\s*/usr/bin/install\b', 'nothing lands in LaunchAgents to load at login')
        self.assertIn('launchctl bootstrap', text, 'the command is printed for the operator')
        self.assertRegex(text, r'chmod 600 "\$LOG"')


class ModelCallTest(unittest.TestCase):
    def test_the_model_runs_isolated_with_no_tools_settings_or_mcp(self):
        command = intake.model_command()
        for flag in ('--tools', '--setting-sources', '--strict-mcp-config', '--disable-slash-commands',
                     '--no-session-persistence', '--json-schema'):
            self.assertIn(flag, command)
        self.assertEqual(command[command.index('--tools') + 1], '')
        self.assertEqual(command[command.index('--setting-sources') + 1], '')
        self.assertEqual(command[command.index('--model') + 1], 'haiku')

    def test_reply_parsing_accepts_only_the_schema(self):
        good = {'next_step': 'Reply', 'due_on': '2026-10-22'}
        self.assertEqual(intake.parse_reply({'is_error': False, 'structured_output': good}), good)
        for bad in ({'is_error': True, 'structured_output': good},
                    {'is_error': False, 'structured_output': {**good, 'due_on': '22 Oct'}},
                    {'is_error': False, 'structured_output': {'next_step': 7, 'due_on': None}},
                    {'is_error': False, 'structured_output': {**good, 'due_on': '2026-02-30'}},
                    {'is_error': False}):
            with self.assertRaises(ValueError):
                intake.parse_reply(bad)


class StoreResolverTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.profiles = self.home / 'Library/Group Containers/UBF8T346G9.Office/Outlook/Outlook 15 Profiles'
        self.env = patch.dict(os.environ, {}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        os.environ.pop('CARR_OUTLOOK_STORE', None)

    def store(self, profile):
        path = self.profiles / profile / 'HxStore.hxd'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'synthetic-store')
        return path

    def test_main_identity_profile(self):
        expected = self.store('Main Identity')
        self.assertEqual(intake.resolve_store(self.home), expected)

    def test_main_profile(self):
        expected = self.store('Main Profile')
        self.assertEqual(intake.resolve_store(self.home), expected)

    def test_no_store_names_the_searched_directory(self):
        with self.assertLogs(level='ERROR') as logs, self.assertRaises(FileNotFoundError) as refusal:
            intake.resolve_store(self.home)
        self.assertIn(str(self.profiles), str(refusal.exception))
        self.assertIn(str(self.profiles), '\n'.join(logs.output))

    def test_newest_store_wins_and_is_logged(self):
        older = self.store('Main Identity')
        newer = self.store('Main Profile')
        for old, new in ((older, newer), (newer, older)):
            os.utime(old, ns=(1_000_000_000, 1_000_000_000))
            os.utime(new, ns=(2_000_000_000, 2_000_000_000))
            with self.assertLogs(level='WARNING') as logs:
                self.assertEqual(intake.resolve_store(self.home), new)
            self.assertIn(str(new), '\n'.join(logs.output))

    def test_override_takes_precedence(self):
        self.store('Main Identity')
        override = self.home / 'chosen.hxd'
        override.write_bytes(b'synthetic-override')
        os.environ['CARR_OUTLOOK_STORE'] = str(override)
        self.assertEqual(intake.resolve_store(self.home), override)

    def test_invalid_override_refuses_without_fallback(self):
        self.store('Main Identity')
        override = self.home / 'missing.hxd'
        os.environ['CARR_OUTLOOK_STORE'] = str(override)
        with self.assertLogs(level='ERROR') as logs, self.assertRaises(FileNotFoundError) as refusal:
            intake.resolve_store(self.home)
        self.assertIn('CARR_OUTLOOK_STORE', str(refusal.exception))
        self.assertIn(str(override), str(refusal.exception))
        self.assertIn(str(override), '\n'.join(logs.output))

if __name__ == '__main__':
    unittest.main()
