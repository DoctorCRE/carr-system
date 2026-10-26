#!/usr/bin/env python3
"""Offline tests for the Outlook intake. Made-up fixtures and fake ports only:
no Outlook store, no record layer, no model."""
from datetime import datetime, timedelta, timezone
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import outlook_intake as intake
from lib.outlook_settings import load_settings
from outlook_test_support import write_settings

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
CLIENT = intake.Contact(email='pat@example.test', ref='C-901', name='Pat Example')
LEAD = intake.Contact(email='lee@example.test', ref='L-902', name='Lee Sample')
OWNER = 'owner@example.test'
PARTNER = 'partner@example.test'


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
        self.actions = {}
        self.fail_verbs = set()
        self.model_reply = {'next_step': None, 'due_on': None}
        self.contacts_rows = [CLIENT, LEAD]
        self.now = NOW
        self.on_decode = None

    def carr(self, verb, args):
        self.calls.append((verb, args))
        if verb in self.fail_verbs:
            raise intake.CarrError()
        if verb == 'find':
            return {'parties': self.parties.get(args['query'], []), 'deals': []}
        if verb == 'catch-me-up':
            return {'timeline': self.timeline.get(args['ref'], [])[:args['limit']]}
        if verb == 'get-next-action':
            return {'ok': True, 'owner': args['owner'], 'action': self.actions.get((args['ref'], args['owner']))}
        if verb == 'set-next-action':
            current = self.actions.get((args['ref'], args.get('owner', 'runtime')))
            expected = {k: current[k] for k in ('id', 'version')} if current else None
            if 'expected_action' in args and args['expected_action'] != expected:
                raise intake.CarrError(code='next_action_conflict')
            action_id = str(uuid.uuid4())
            self.actions[args['ref'], args.get('owner', 'runtime')] = {
                'id': action_id, 'version': 1, 'updated_at': self.now.isoformat()}
            return {'ok': True, 'next_action_id': action_id}
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
        scope = patch('lib.outlook_settings.OutlookSettings.verify_helper')
        scope.start()
        self.addCleanup(scope.stop)
        internal = patch.object(intake, '_internal', side_effect=lambda address, domain: address.lower() in (OWNER, PARTNER))
        internal.start()
        self.addCleanup(internal.stop)
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / 'repo'
        self.home = self.tmp / 'home'
        write_settings(self.home, self.repo, owner='joe')
        settings = patch.object(intake, 'load_settings', lambda home=None: load_settings(home or self.home))
        settings.start()
        self.addCleanup(settings.stop)
        self.store = self.home / 'Library/Group Containers/UBF8T346G9.Office/Outlook/Outlook 15 Profiles/Main Identity/HxStore.hxd'
        self.store.parent.mkdir(parents=True)
        self.store.write_bytes(b'Nostromo-fixture')
        live = self.store.parent / 'Files' / 'S0' / '845' / 'EFMData'
        live.mkdir(parents=True)
        (live / '7.dat').write_bytes(b'gzip-body')
        self.fake = Fake(self)
        self.fake.fixture = self.tmp / 'fixture.db'
        copier = patch('subprocess.run', side_effect=self.copy_snapshot)
        copier.start()
        self.addCleanup(copier.stop)

    def copy_snapshot(self, command, *, cwd=None, **kwargs):
        if command[-1] == '--scope':
            return subprocess.CompletedProcess(command, 0, stdout=f'{self.store.parent}\n{self.repo / intake.OUT}\n{self.repo}\n')
        self.assertEqual(command, [str(self.home / 'carr-local/bin/outlook-intake-copy')])
        shutil.copyfile(self.store, cwd / 'store.hxd')
        os.chmod(cwd / 'store.hxd', 0o600)
        shutil.copytree(self.store.parent / 'Files', cwd / 'Files')
        for directory, _, files in os.walk(cwd / 'Files'):
            os.chmod(directory, 0o700)
            for name in files:
                os.chmod(Path(directory) / name, 0o600)
        return subprocess.CompletedProcess(command, 0)

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

    def test_transient_backoff_and_blocked_requests_preserve_frozen_payloads(self):
        m = mail('pat@example.test', -timedelta(hours=1))
        self.fake.fail_verbs.add('log-activity')
        self.assertEqual(self.run_intake([m]).reason, 'writes_failed')
        self.assertEqual(self.run_intake([m], touch=False).reason, 'writes_deferred')
        self.assertEqual(len(self.fake.writes()), 1)
        self.fake.now += timedelta(seconds=60)
        self.assertEqual(self.run_intake([m], touch=False).reason, 'writes_failed')
        state = json.loads((self.out() / intake.STATE).read_text())
        [request] = state['requests'].values()
        self.assertEqual(request['attempts'], 2)
        self.assertEqual(request['retry_at'], int(self.fake.now.timestamp()) + 120)
        self.fake.fail_verbs.clear()
        self.fake.now += timedelta(seconds=120)
        self.assertEqual(self.run_intake([m], touch=False).state, 'succeeded')
        self.assertTrue(all(args == self.fake.writes()[0][1] for _, args in self.fake.writes()))
        shutil.rmtree(self.out())
        original = self.fake.carr
        def refuse(verb, args):
            if verb in intake.WRITE_VERBS:
                raise intake.CarrError(code='invalid_request')
            return original(verb, args)
        self.fake.carr = refuse
        self.assertEqual(self.run_intake([m]).reason, 'writes_failed')
        self.fake.now += timedelta(hours=1)
        self.assertEqual(self.run_intake([m], touch=False).reason, 'request_blocked')
        [request] = json.loads((self.out() / intake.STATE).read_text())['requests'].values()
        self.assertEqual(request['attempts'], 1)
        self.assertFalse(request['acknowledged'])

    def test_unsupported_candidates_never_establish_reminders_or_attendance(self):
        candidates = [event('series', timedelta(days=1), attendees=[('Pat', 'pat@example.test')]),
                      event('series', timedelta(days=2), attendees=[]),
                      event('elapsed', -timedelta(hours=3), attendees=[('Lee', 'lee@example.test')])]
        result = self.run_intake(events=candidates)
        self.assertEqual(self.fake.writes(), [])
        self.assertEqual(result.reason, 'nothing_new')
        self.assertEqual(result.counts['calendar_lifecycle_unsupported'], 3)
        evidence = json.loads((self.out() / intake.STATE).read_text())['calendar_evidence']
        self.assertTrue(all(e['evidence'] == 'provisional_schedule' for e in evidence.values()))
        self.assertNotIn('series', self.report_text())

    def test_unsupported_calendar_does_not_block_mail_or_hide_on_unchanged_store(self):
        e = event('unknown', timedelta(days=1), attendees=[('Pat', 'pat@example.test')])
        m = mail('lee@example.test', -timedelta(hours=1))
        result = self.run_intake([m], [e])
        self.assertEqual([(v, a.get('kind')) for v, a in self.fake.writes()], [('log-activity', 'email_in')])
        self.assertEqual(result.state, 'succeeded')
        self.assertEqual(result.reason, 'processed')
        changed = self.run_intake([m], [e])
        self.assertEqual(changed.reason, 'nothing_new')
        quiet = self.run_intake([m], [e], touch=False)
        self.assertEqual(quiet.reason, 'store_unchanged')
        self.assertEqual(quiet.counts['calendar_lifecycle_unsupported'], 1)
        self.assertEqual(self.fake.decodes, 2)

    def test_calendar_schedule_never_becomes_attendance_as_time_passes(self):
        events = [event('elapsed', timedelta(hours=1), attendees=[('Pat', 'pat@example.test')])]
        self.run_intake(events=events)
        self.fake.now += timedelta(hours=3)
        result = self.run_intake(events=events, touch=False)
        self.assertEqual(result.reason, 'store_unchanged')
        self.assertEqual(self.fake.writes(), [])
        self.assertEqual(self.fake.decodes, 1)

    def test_orphan_calendar_requests_are_quarantined_before_replay(self):
        self.out().mkdir(parents=True)
        requests = {}
        for verb, kind, origin in [('log-activity', 'meeting', 'legacy'),
                                   ('set-next-action', None, 'event|legacy')]:
            ident = intake.key(verb, 'C-901', origin)
            args = {'idempotency_key': ident, 'ref': 'C-901'}
            if kind:
                args.update(kind=kind, summary='Meeting: synthetic', occurred_at=intake._iso(ts(-timedelta(hours=2))))
            else:
                args.update(description='Synthetic reminder', due_on='2026-10-21',
                            owner=load_settings(self.home).owner, expected_action=None)
            requests[ident] = dict(verb=verb, payload=json.dumps(args), acknowledged=False,
                                   created_at=ts(-timedelta(hours=1)))
        (self.out() / intake.STATE).write_text(json.dumps({'requests': requests}))
        result = self.run_intake()
        self.assertEqual(self.fake.writes(), [])
        self.assertEqual(result.counts['calendar_requests_quarantined'], 2)
        state = json.loads((self.out() / intake.STATE).read_text())
        self.assertTrue(all(r['disposition'] == 'calendar_unsupported' and not r['acknowledged']
                            for r in state['requests'].values()))

    def test_internal_calendar_candidates_are_not_unresolved_meetings(self):
        result = self.run_intake(events=[event('internal', timedelta(days=1),
                                              attendees=[('Partner', PARTNER)])])
        self.assertEqual(result.counts['calendar_lifecycle_unsupported'], 1)
        self.assertEqual(result.counts['unresolved_calendar'], 0)
        self.assertEqual(result.counts['unresolved_addresses'], 0)

    def test_prior_calendar_policy_rechecks_internal_unresolved_state(self):
        candidates = [event('internal', timedelta(days=1), attendees=[('Partner', PARTNER)])]
        self.run_intake(events=candidates)
        path = self.out() / intake.STATE
        state = json.loads(path.read_text())
        state['calendar_policy'] = 1
        state['calendar_unresolved'] = {candidate: {'participants': {}}
                                        for candidate in state['calendar_evidence']}
        path.write_text(json.dumps(state))
        result = self.run_intake(events=candidates, touch=False)
        self.assertEqual(result.counts['unresolved_calendar'], 0)
        self.assertEqual(result.counts['calendar_lifecycle_unsupported'], 1)
        self.assertEqual(self.fake.decodes, 2)
        self.assertEqual(self.fake.writes(), [])

    def test_unmatched_calendar_report_only_includes_recent_events(self):
        events = [event('old', -timedelta(days=30), attendees=[('Old Visitor', 'old@example.test')]),
                  event('future', timedelta(days=30), attendees=[('Future Visitor', 'future@example.test')]),
                  event('recent', -timedelta(hours=3), attendees=[('Recent Visitor', 'recent@example.test')]),
                  event('soon', timedelta(days=2), attendees=[('Soon Visitor', 'soon@example.test')])]
        result = self.run_intake(events=events)
        self.assertEqual(result.counts['calendar_lifecycle_unsupported'], 4)
        self.assertEqual(result.counts['unresolved_calendar'], 4)
        self.assertIn('Recent Visitor', self.report_text())
        self.assertIn('Soon Visitor', self.report_text())
        self.assertNotIn('Old Visitor', self.report_text())
        self.assertNotIn('Future Visitor', self.report_text())

    def test_quiet_calendar_runs_do_not_write_reports(self):
        candidates = [event('internal', timedelta(days=1), attendees=[('Partner', PARTNER)]),
                      event('matched', timedelta(days=2), attendees=[('Pat', 'pat@example.test')])]
        self.run_intake(events=candidates)
        self.assertEqual(list(self.out().glob('report-*.md')), [])
        self.fake.now += timedelta(minutes=30)
        self.run_intake(events=candidates)
        self.assertEqual(list(self.out().glob('report-*.md')), [])

    def test_calendar_mapping_ambiguity_resolves_without_asserting_attendance(self):
        e = event('partial', -timedelta(hours=3), attendees=[('Pat', 'pat@example.test'), ('New', 'new@example.test')])
        self.fake.contacts_rows += [intake.Contact('new@example.test', 'C-903', ''), intake.Contact('new@example.test', 'L-904', '')]
        first = self.run_intake(events=[e])
        self.assertEqual(first.counts['mapping_ambiguous'], 1)
        self.fake.now += timedelta(days=30)
        self.fake.contacts_rows.pop()
        second = self.run_intake(events=[e], touch=False)
        self.assertEqual(second.counts['unresolved_calendar'], 0)
        self.assertEqual(second.counts['calendar_lifecycle_unsupported'], 1)
        self.assertEqual(self.fake.writes(), [])

    def test_duplicate_recipients_and_aliases_are_resolved_once(self):
        self.fake.contacts_rows.append(intake.Contact('alias@example.test', 'C-901', 'Pat'))
        result = self.run_intake([mail(OWNER, -timedelta(hours=1), recipients='pat@example.test,pat@example.test,alias@example.test')])
        self.assertEqual(result.counts['unmatched'], 0)
        self.assertEqual(result.counts['unresolved_addresses'], 0)
        self.assertEqual(len(self.fake.writes()), 1)

    def test_main_emits_monitoring_receipt_from_the_actual_outcome(self):
        from io import StringIO
        outcome = intake.Outcome('failed', 'writes_failed', {'writes_failed': 3})
        with patch.object(intake, 'load_settings', return_value=load_settings(self.home)), patch.object(type(load_settings(self.home)), 'require_repo'), patch.object(intake, 'run', return_value=outcome), patch('sys.stdout', new_callable=StringIO) as stdout:
            self.assertEqual(intake.main(), 1)
        receipt = stdout.getvalue().split('outlook-intake-receipt ', 1)[1]
        self.assertEqual(json.loads(receipt), {'state': 'failed', 'reason': 'writes_failed', 'counts': {'writes_failed': 3}})

    def test_contact_failures_have_safe_distinct_reasons(self):
        from types import SimpleNamespace
        for rows, code in (([], 'contacts_empty'), (ValueError('PRIVATE-SENTINEL'), 'contacts_read_failed')):
            with self.subTest(code=code):
                def read_view(view, **kwargs):
                    if isinstance(rows, Exception):
                        raise rows
                    return [], rows
                exporter = SimpleNamespace(RECORD_VIEWS=[('fake', 'id', 'name', 'org')], read_view=read_view)
                with patch('lib.loadpy.load_module_from_path', return_value=exporter):
                    self.fake.contacts = intake._contacts(self.repo)
                    result = self.run_intake([])
                self.assertEqual(result.reason, code)
                self.assertNotIn('PRIVATE-SENTINEL', intake.status_line(result))

    def test_partial_preview_recovery_refreshes_every_completed_participant(self):
        m = mail(OWNER, -timedelta(hours=1), recipients='pat@example.test,new@example.test', kind='preview')
        self.run_intake([m])
        self.fake.contacts_rows.append(intake.Contact('new@example.test', 'L-908', 'New Example'))
        self.run_intake([m], touch=False)
        self.run_intake([{**m, 'body_kind': 'full', 'body': 'Full synthetic body'}])
        notes = [a for _, a in self.fake.writes('log-activity') if a['kind'] == 'note']
        self.assertEqual({a['ref'] for a in notes}, {'C-901', 'L-908'})

    def test_mapping_diagnostics_count_sources_and_distinct_addresses_with_reasons(self):
        self.fake.contacts_rows += [intake.Contact('ambiguous@example.test', 'C-903', ''),
                                   intake.Contact('ambiguous@example.test', 'L-904', ''),
                                   intake.Contact('unsupported@example.test', 'D-905', '')]
        m = mail(OWNER, -timedelta(hours=1), recipients='pat@example.test, pat@example.test, missing@example.test, ambiguous@example.test, unsupported@example.test')
        e = event('unmapped', -timedelta(hours=3), attendees=[('Missing', 'missing@example.test')])
        result = self.run_intake([m], [e])
        self.assertEqual(result.counts['unresolved_messages'], 1)
        self.assertEqual(result.counts['unresolved_addresses'], 3)
        self.assertEqual(result.counts['unresolved_calendar'], 1)
        self.assertEqual([result.counts['mapping_' + reason] for reason in ('missing', 'ambiguous', 'unsupported')], [1, 1, 1])
        self.assertEqual(result.counts['unmatched'], 3)
        for reason in ('missing', 'ambiguous', 'unsupported'):
            self.assertIn(reason, self.report_text())
        self.assertNotIn('missing@example.test', self.report_text())

    def test_write_failures_keep_only_safe_codes_attempts_and_retry_disposition(self):
        m = mail('pat@example.test', -timedelta(hours=1), body='PRIVATE-SENTINEL')
        cases = [
            ('timeout', subprocess.TimeoutExpired('fake', 120)),
            ('auth_refused', subprocess.CompletedProcess([], 1, '', 'RPC ERROR "unauthorized"')),
            ('key_reuse', subprocess.CompletedProcess([], 1, '', 'TOOL ERROR {"error":"key_reuse","detail":"PRIVATE-SENTINEL"}')),
            ('transport_oversized', OSError(errno.E2BIG, 'PRIVATE-SENTINEL')),
            ('no_acknowledgement', subprocess.CompletedProcess([], 0, '{"ok":false}', 'PRIVATE-SENTINEL')),
        ]
        for expected, response in cases:
            with self.subTest(code=expected):
                shutil.rmtree(self.out(), ignore_errors=True)
                carr = intake._carr(self.repo)
                def fake_carr(verb, args):
                    if verb not in intake.WRITE_VERBS:
                        return {'timeline': []}
                    options = {'side_effect': response} if isinstance(response, Exception) else {'return_value': response}
                    with patch('subprocess.run', **options):
                        return carr(verb, args)
                self.fake.carr = fake_carr
                result = self.run_intake([m])
                self.assertEqual(result.state, 'failed')
                state = json.loads((self.out() / intake.STATE).read_text())
                request = state['requests'][intake.key('log-activity', 'C-901', ident(m))]
                self.assertEqual(request['error_code'], expected)
                self.assertEqual(result.counts['error_' + expected], 1)
                self.assertEqual(request['attempts'], 1)
                self.assertEqual(request['disposition'], 'blocked' if expected in ('key_reuse', 'transport_oversized') else 'retry')
                self.assertNotIn('PRIVATE-SENTINEL', json.dumps({k:v for k,v in request.items() if k != 'payload'}))
                self.assertNotIn('PRIVATE-SENTINEL', self.report_text())
                self.assertNotIn('PRIVATE-SENTINEL', intake.status_line(result))
                self.assertTrue(request['retry_at'] is None or request['retry_at'] > self.fake.now.timestamp())

    def test_unmatched_calendar_recovers_on_mapping_change_without_store_change_after_horizon(self):
        e = event('unmapped', -timedelta(hours=3), attendees=[('New Example', 'new@example.test')])
        self.run_intake(events=[e])
        state = json.loads((self.out() / intake.STATE).read_text())
        self.assertNotIn('unmapped|' + str(e['start_unix']), state.get('events_logged', {}))
        self.fake.now += timedelta(days=30)
        self.fake.contacts_rows.append(intake.Contact('new@example.test', 'L-908', 'New Example'))
        self.run_intake(events=[e], touch=False)
        self.assertEqual(self.fake.writes(), [])
        self.assertEqual(json.loads((self.out() / intake.STATE).read_text())['calendar_unresolved'], {})

    def test_partial_mail_recovers_new_participant_after_mapping_change_outside_horizon(self):
        m = mail(OWNER, -timedelta(hours=1), recipients='pat@example.test, new@example.test')
        self.run_intake([m])
        self.assertIn(ident(m), json.loads((self.out() / intake.STATE).read_text())['mail_unresolved'])
        self.assertEqual([a['ref'] for _, a in self.fake.writes('log-activity')], ['C-901'])
        self.fake.now += timedelta(days=30)
        self.fake.contacts_rows.append(intake.Contact('new@example.test', 'L-908', 'New Example'))
        self.run_intake([m], touch=False)
        self.assertEqual([a['ref'] for _, a in self.fake.writes('log-activity')], ['C-901', 'L-908'])
        self.assertEqual(json.loads((self.out() / intake.STATE).read_text())['mail_unresolved'], {})

    def test_carr_large_payload_uses_stdin_without_client_content_in_argv(self):
        args = {'detail': 'PRIVATE-SENTINEL-' + 'x' * 300000}
        with patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, '{"ok":true}', '')) as call:
            self.assertEqual(intake._carr(self.repo)('log-activity', args), {'ok': True})
        argv = call.call_args.args[0]
        self.assertEqual(argv, [str(self.repo / 'run.sh'), 'call', 'log-activity', '-'])
        self.assertEqual(json.loads(call.call_args.kwargs['input']), args)
        self.assertNotIn('PRIVATE-SENTINEL', str(argv))

    def test_inbound_mail_from_a_known_address_logs_email_in_with_a_uuid5_key(self):
        m = mail('pat@example.test', -timedelta(hours=1), name='Pat Example')
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
        self.run_intake([mail(OWNER, -timedelta(hours=1), recipients='lee@example.test')])
        [(_, args)] = self.fake.writes('log-activity')
        self.assertEqual((args['ref'], args['kind']), ('L-902', 'email_out'))

    def test_internal_addresses_are_never_matched_or_listed(self):
        self.fake.contacts_rows.append(intake.Contact(PARTNER, 'V-903', 'Internal Row'))
        self.run_intake([mail(PARTNER, -timedelta(hours=1), recipients=OWNER)])
        self.assertEqual(self.fake.writes(), [])
        self.assertNotIn('example.test', self.report_text().split('## Unmatched', 1)[-1])

    def test_a_second_run_over_the_same_mail_writes_nothing(self):
        mails = [mail('pat@example.test', -timedelta(hours=1))]
        self.run_intake(mails)
        before = len(self.fake.writes())
        result = self.run_intake(mails)
        self.assertEqual(result.state, 'succeeded')
        self.assertEqual(len(self.fake.writes()), before, 'each email is handled once')

    def test_an_unchanged_store_is_neither_copied_nor_decoded_and_calls_nothing(self):
        self.run_intake([mail('pat@example.test', -timedelta(hours=1))])
        calls, decodes, models = len(self.fake.calls), self.fake.decodes, len(self.fake.model_calls)
        result = self.run_intake(touch=False)
        self.assertEqual((result.state, result.reason), ('succeeded', 'store_unchanged'))
        self.assertEqual(self.fake.decodes, decodes, 'no copy or decode')
        self.assertEqual(len(self.fake.calls), calls, 'no record-layer call')
        self.assertEqual(len(self.fake.model_calls), models, 'no model call')

    def test_a_changed_store_with_nothing_new_is_a_quiet_success_with_no_calls(self):
        result = self.run_intake([mail('pat@example.test', -timedelta(days=5))])
        self.assertEqual((result.state, result.reason), ('succeeded', 'nothing_new'))
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self.fake.model_calls, [])

    def test_first_run_backfills_48_hours_only(self):
        self.run_intake([mail('pat@example.test', -timedelta(hours=47)),
                         mail('lee@example.test', -timedelta(hours=49), subject='Older')])
        self.assertEqual([a['ref'] for _, a in self.fake.writes('log-activity')], ['C-901'])

    def test_late_synced_mail_inside_the_lookback_is_still_handled(self):
        self.run_intake([mail('pat@example.test', -timedelta(hours=1))])
        self.fake.now = NOW + timedelta(minutes=30)
        self.run_intake([mail('pat@example.test', -timedelta(hours=1)),
                         mail('lee@example.test', -timedelta(hours=2), subject='Delivered late')])
        self.assertEqual([a['ref'] for _, a in self.fake.writes('log-activity')], ['C-901', 'L-902'])

    def test_unmatched_people_are_reported_by_name_and_domain_only(self):
        self.run_intake([mail('stranger@example.test', -timedelta(hours=1), name='Sam Stranger')])
        self.assertEqual(self.fake.writes(), [])
        text = self.report_text()
        self.assertIn('Sam Stranger', text)
        self.assertIn('example.test', text)
        self.assertNotIn('stranger@example.test', text)

    def test_unmatched_mail_retries_after_a_contact_is_added_without_a_store_change(self):
        m = mail('new@example.test', -timedelta(hours=1), name='New Example')
        self.assertEqual(self.run_intake([m]).state, 'succeeded')
        self.assertEqual(self.fake.writes(), [])
        decodes = self.fake.decodes
        self.assertEqual(self.run_intake([m], touch=False).reason, 'store_unchanged')
        self.assertEqual(self.fake.decodes, decodes)
        self.fake.now += timedelta(days=14)
        self.assertEqual(self.run_intake([m]).state, 'succeeded')
        state = json.loads((self.out() / intake.STATE).read_text())
        self.assertIn(ident(m), state['mail_unresolved'])
        self.assertEqual(state['mail_done'], {})
        self.fake.contacts_rows.append(intake.Contact('new@example.test', 'L-908', 'New Example'))
        self.assertEqual(self.run_intake([m], touch=False).state, 'succeeded')
        [(_, args)] = self.fake.writes('log-activity')
        self.assertEqual((args['ref'], args['detail']), ('L-908', m['body']))
        self.assertEqual(args['idempotency_key'],
                         str(uuid.uuid5(uuid.NAMESPACE_URL, f'log-activity|L-908|{ident(m)}')))
        self.run_intake([m])
        self.assertEqual(len(self.fake.writes('log-activity')), 1)

    def test_unresolved_mail_keeps_its_identity_when_a_later_copy_is_decoded(self):
        m = mail('new@example.test', -timedelta(hours=1), copy_key='unresolved-copy')
        self.run_intake([m])
        self.fake.now += timedelta(days=14)
        self.fake.contacts_rows.append(intake.Contact('new@example.test', 'L-908', 'New Example'))
        later_copy = {**m, 'sent_unix': m['sent_unix'] + 20}
        self.run_intake([later_copy])
        activities = self.fake.writes('log-activity')
        self.assertEqual(len(activities), 1)
        args = activities[0][1]
        self.assertEqual(args['idempotency_key'],
                         str(uuid.uuid5(uuid.NAMESPACE_URL, f'log-activity|L-908|{ident(m)}')))
        self.assertEqual(args['occurred_at'], (NOW - timedelta(hours=1)).isoformat())
        self.assertEqual(json.loads((self.out() / intake.STATE).read_text())['mail_unresolved'], {})

    def test_report_and_state_are_private(self):
        self.run_intake([mail('pat@example.test', -timedelta(hours=1))])
        self.assertEqual(stat.S_IMODE(self.out().stat().st_mode), 0o700)
        for path in self.out().iterdir():
            if path.is_file():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, path.name)

    def test_the_store_copy_and_decoded_database_are_deleted_after_a_run(self):
        self.run_intake([mail('pat@example.test', -timedelta(hours=1))])
        self.assertEqual(sorted(p.name for p in self.out().iterdir() if p.is_dir()), [])
        self.assertEqual([p for p in self.out().rglob('*') if p.suffix in ('.hxd', '.db')], [])

    def test_a_unique_exact_name_cannot_attach_an_unknown_address(self):
        self.fake.parties['Rae Named'] = [{'name': 'Rae Named', 'ref': 'L-904', 'kind': 'lead', 'merged': False}]
        m = mail('rae@example.test', -timedelta(hours=1), name='Rae Named')
        self.run_intake([m])
        self.assertEqual(self.fake.writes(), [])
        self.assertEqual(self.fake.model_calls, [])
        self.assertIn(ident(m), json.loads((self.out() / intake.STATE).read_text())['mail_unresolved'])
        self.assertIn('Rae Named', self.report_text())

    def test_an_ambiguous_name_is_unmatched(self):
        self.fake.parties['Rae Named'] = [
            {'name': 'Rae Named', 'ref': 'L-904', 'kind': 'lead', 'merged': False},
            {'name': 'Rae Named', 'ref': 'C-905', 'kind': 'client', 'merged': False}]
        self.run_intake([mail('rae@example.test', -timedelta(hours=1), name='Rae Named')])
        self.assertEqual(self.fake.writes(), [])
        self.assertIn('Rae Named', self.report_text())

    def test_a_fuzzy_or_merged_name_hit_is_not_a_match(self):
        self.fake.parties['Rae Named'] = [
            {'name': 'Rae Namedson', 'ref': 'L-904', 'kind': 'lead', 'merged': False},
            {'name': 'Rae Named', 'ref': 'L-906', 'kind': 'lead', 'merged': True}]
        self.run_intake([mail('rae@example.test', -timedelta(hours=1), name='Rae Named')])
        self.assertEqual(self.fake.writes(), [])

    def test_an_address_shared_by_two_records_is_ambiguous(self):
        self.fake.contacts_rows.append(intake.Contact('pat@example.test', 'L-907', 'Pat Example'))
        self.run_intake([mail('pat@example.test', -timedelta(hours=1))])
        self.assertEqual(self.fake.writes(), [])

    def test_ambiguous_address_retries_when_the_conflicting_mapping_is_removed(self):
        conflicting = intake.Contact('pat@example.test', 'L-907', 'Pat Example')
        self.fake.contacts_rows.append(conflicting)
        self.fake.parties['Pat Example'] = [{'name': 'Pat Example', 'ref': 'C-901', 'merged': False}]
        m = mail('pat@example.test', -timedelta(hours=1), name='Pat Example')
        self.run_intake([m])
        self.assertEqual(self.fake.writes(), [])
        self.fake.contacts_rows.remove(conflicting)
        self.run_intake([m], touch=False)
        self.assertEqual([args['ref'] for _, args in self.fake.writes('log-activity')], ['C-901'])
        self.assertEqual(json.loads((self.out() / intake.STATE).read_text())['mail_unresolved'], {})

    def test_contact_order_names_and_duplicate_rows_do_not_retry_unresolved_mail(self):
        m = mail('new@example.test', -timedelta(hours=1))
        self.run_intake([m])
        decodes = self.fake.decodes
        self.fake.contacts_rows.reverse()
        self.fake.contacts_rows.append(intake.Contact('PAT@example.test', 'C-901', 'Changed Name'))
        result = self.run_intake([m], touch=False)
        self.assertEqual(result.reason, 'store_unchanged')
        self.assertEqual(self.fake.decodes, decodes)
        self.assertEqual(self.fake.writes(), [])

    def test_missing_unresolved_source_is_retained_until_it_decodes_again(self):
        m = mail('new@example.test', -timedelta(hours=1))
        self.run_intake([m])
        self.fake.now += timedelta(days=14)
        self.fake.contacts_rows.append(intake.Contact('new@example.test', 'L-908', 'New Example'))
        self.assertEqual(self.run_intake([], touch=False).state, 'succeeded')
        self.assertIn(ident(m), json.loads((self.out() / intake.STATE).read_text())['mail_unresolved'])
        self.run_intake([m])
        self.assertEqual([args['ref'] for _, args in self.fake.writes('log-activity')], ['L-908'])

    def test_unresolved_contact_read_failure_preserves_state_without_decoding(self):
        m = mail('new@example.test', -timedelta(hours=1))
        self.run_intake([m])
        before = (self.out() / intake.STATE).read_bytes()
        decodes = self.fake.decodes
        def down():
            raise intake.CarrError()
        self.fake.contacts = down
        result = self.run_intake([m], touch=False)
        self.assertEqual((result.state, result.reason), ('failed', 'carr_unreachable'))
        self.assertEqual(self.fake.decodes, decodes)
        self.assertEqual((self.out() / intake.STATE).read_bytes(), before)
        self.assertEqual(self.fake.writes(), [])

    def test_resolved_mail_write_failure_reuses_the_original_key_and_payload(self):
        m = mail('new@example.test', -timedelta(hours=1))
        self.run_intake([m])
        self.fake.contacts_rows.append(intake.Contact('new@example.test', 'L-908', 'New Example'))
        self.fake.fail_verbs.add('log-activity')
        self.assertEqual(self.run_intake([m], touch=False).reason, 'writes_failed')
        self.assertIn(ident(m), json.loads((self.out() / intake.STATE).read_text())['mail_unresolved'])
        self.fake.fail_verbs.clear()
        self.fake.now += timedelta(minutes=30)
        self.assertEqual(self.run_intake([m], touch=False).state, 'succeeded')
        writes = self.fake.writes('log-activity')
        self.assertEqual(len(writes), 2)
        self.assertEqual(writes[0], writes[1])
        self.assertEqual(json.loads((self.out() / intake.STATE).read_text())['mail_unresolved'], {})

    def test_calendar_names_alone_cannot_log_an_activity_or_set_an_action(self):
        self.fake.parties['Rae Named'] = [{'name': 'Rae Named', 'ref': 'L-904', 'merged': False}]
        self.run_intake(events=[
            event('name-past', -timedelta(hours=3), attendees=[('Rae Named', 'rae@example.test')]),
            event('name-soon', timedelta(days=2), attendees=[('Rae Named', 'rae@example.test')])])
        self.assertEqual(self.fake.writes(), [])

    def test_duplicate_copies_collapse_and_the_full_body_wins(self):
        self.run_intake([mail('pat@example.test', -timedelta(hours=1), body='the whole', kind='preview',
                              copy_key='k1'),
                         mail('pat@example.test', -timedelta(hours=1) + timedelta(seconds=20),
                              body='the whole message body', copy_key='k1')])
        [(_, args)] = self.fake.writes('log-activity')
        self.assertEqual(args['detail'], 'the whole message body')

    def test_a_sender_name_that_is_really_the_subject_or_a_file_is_dropped(self):
        self.run_intake([mail('pat@example.test', -timedelta(hours=2), name='Suite question'),
                         mail('pat@example.test', -timedelta(hours=1), subject='Plans', name='floorplan.pdf')])
        summaries = [a['summary'] for _, a in self.fake.writes('log-activity')]
        self.assertEqual(len(summaries), 2)
        for s in summaries:
            self.assertIn('pat@example.test', s)
            self.assertNotIn('floorplan.pdf', s)

    def test_the_full_body_is_logged_untruncated(self):
        body = 'Paragraph one.\n\n' + 'x' * 20000
        self.run_intake([mail('pat@example.test', -timedelta(hours=1), body=body)])
        [(_, args)] = self.fake.writes('log-activity')
        self.assertEqual(args['detail'], body)

    def test_a_preview_only_email_logs_the_preview_and_is_listed_for_refresh(self):
        m = mail('pat@example.test', -timedelta(hours=1), body='Can we meet Thursday at ten', kind='preview')
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
                                 'sender_email': 'pat@example.test'})

    def test_hydration_rechecks_sidecars_despite_unsupported_calendar(self):
        preview = mail('pat@example.test', -timedelta(hours=1), body='Hello', kind='preview', copy_key='hydrate')
        events = [event('unsupported', timedelta(days=1), attendees=[('Pat', 'pat@example.test')])]
        self.assertEqual(self.run_intake([preview], events).reason, 'processed')
        self.assertEqual(self.fake.model_calls, [], 'a preview cannot settle an obligation')
        self.fake.now += timedelta(minutes=30)
        full = {**preview, 'body': 'Hello. Send the floor plan by Thursday.', 'body_kind': 'full'}
        self.fake.model_reply = {'next_step': 'Send the floor plan', 'due_on': '2026-10-22'}
        self.assertEqual(self.run_intake([full], events, touch=False).state, 'succeeded')
        self.assertEqual([m['text'] for m in self.fake.model_calls], [full['body']])
        self.assertEqual([a['description'] for _, a in self.fake.writes('set-next-action')], ['Send the floor plan'])
        self.assertEqual(json.loads((self.out() / intake.REFRESH_LIST).read_text()), [])
        self.run_intake([full], events, touch=False)
        self.assertEqual(len(self.fake.model_calls), 1)
        self.assertEqual([a['kind'] for _, a in self.fake.writes('log-activity')], ['email_in', 'note'])

    def test_expired_hydration_remains_visible_and_can_recover(self):
        preview = mail('pat@example.test', -timedelta(hours=1), body='Hello', kind='preview', copy_key='expired')
        self.run_intake([preview])
        self.fake.now += timedelta(days=12)
        result = self.run_intake([preview], touch=False)
        self.assertEqual(result.counts['hydration_expired'], 1)
        [entry] = json.loads((self.out() / intake.REFRESH_LIST).read_text())
        self.assertTrue(entry['expired'])
        self.assertEqual(self.fake.model_calls, [])
        state = json.loads((self.out() / intake.STATE).read_text())
        self.assertNotIn(intake.key('log-activity', CLIENT.ref, ident(preview)), state['requests'])
        self.fake.now += timedelta(minutes=30)
        self.fake.model_reply = {'next_step': 'Send the floor plan', 'due_on': None}
        self.fake.actions[CLIENT.ref, 'joe'] = {
            'id': 'synthetic-human-action', 'version': 2, 'updated_at': NOW.isoformat()}
        full = {**preview, 'body': 'Hello. Send the floor plan.', 'body_kind': 'full'}
        self.run_intake([full], touch=False)
        self.assertEqual(len(self.fake.model_calls), 1)
        self.assertEqual(self.fake.writes('set-next-action'), [], 'keep the intervening human action')
        self.assertEqual(json.loads((self.out() / intake.REFRESH_LIST).read_text()), [])

    def test_legacy_preview_null_is_reconsidered_after_full_body_note_ack(self):
        preview = mail('pat@example.test', -timedelta(hours=1), body='Hello', kind='preview', copy_key='legacy')
        self.run_intake([preview])
        state_path = self.out() / intake.STATE
        state = json.loads(state_path.read_text())
        state['pending_full'][ident(preview)].pop('outbound')
        state['pending_full'][ident(preview)].pop('who')
        state_path.write_text(json.dumps(state))
        full = {**preview, 'body': 'Hello. Send the floor plan.', 'body_kind': 'full'}
        self.fake.model_reply = {'next_step': 'Send the floor plan', 'due_on': None}
        self.fake.fail_verbs.add('log-activity')
        self.fake.now += timedelta(minutes=30)
        self.run_intake([full], touch=False)
        self.assertEqual(self.fake.model_calls, [], 'full text must be acknowledged first')
        self.fake.fail_verbs.clear()
        self.fake.now += timedelta(minutes=30)
        self.run_intake([full], touch=False)
        self.assertEqual([m['text'] for m in self.fake.model_calls], [full['body']])
        self.assertEqual(len(self.fake.writes('set-next-action')), 1)

    def test_once_the_full_body_decodes_it_is_logged_as_a_note_and_leaves_the_list(self):
        preview = mail('pat@example.test', -timedelta(hours=1), body='Can we meet', kind='preview', copy_key='k2')
        self.run_intake([preview])
        path = self.out() / intake.REFRESH_LIST
        entries = json.loads(path.read_text())
        entries[0]['opened_at'] = NOW.isoformat()
        path.write_text(json.dumps(entries))
        self.run_intake([preview, mail('pat@example.test', -timedelta(hours=1) + timedelta(seconds=5),
                                       body='Can we meet Thursday? Full text.', copy_key='k2')])
        self.assertEqual(json.loads(path.read_text()), [], 'refreshed email leaves the list')
        notes = [a for _, a in self.fake.writes('log-activity') if a['kind'] == 'note']
        self.assertEqual([n['detail'] for n in notes], ['Can we meet Thursday? Full text.'])
        self.assertEqual(notes[0]['idempotency_key'],
                         str(uuid.uuid5(uuid.NAMESPACE_URL, f'log-activity|C-901|fullbody|{ident(preview)}')))
        self.assertEqual(len(self.fake.writes('log-activity')), 2, 'the email itself is not logged twice')

    def test_lost_preview_ack_replays_original_payload_after_full_body_arrives(self):
        preview = mail('pat@example.test', -timedelta(hours=1), body='Preview', kind='preview', copy_key='lost-ack')
        full = {**preview, 'body': 'Full body after download', 'body_kind': 'full'}
        accepted = {}
        original = self.fake.carr

        def server(verb, args):
            if verb not in intake.WRITE_VERBS:
                return original(verb, args)
            payload = json.dumps(args)
            request_key = args['idempotency_key']
            if request_key in accepted:
                self.assertEqual(payload, accepted[request_key], 'same key must replay byte-identical arguments')
                return original(verb, args)
            accepted[request_key] = payload
            result = original(verb, args)
            if args.get('kind') == 'email_in':
                raise intake.CarrError()
            return result

        self.fake.carr = server
        self.assertEqual(self.run_intake([preview]).state, 'failed')
        self.fake.now += timedelta(minutes=30)
        result = self.run_intake([full])
        self.assertEqual(result.state, 'succeeded')
        activities = [a for _, a in self.fake.writes('log-activity')]
        self.assertEqual([(a['kind'], a['detail']) for a in activities],
                         [('email_in', 'Preview'), ('email_in', 'Preview'), ('note', 'Full body after download')])
        self.assertNotEqual(activities[0]['idempotency_key'], activities[2]['idempotency_key'])
        self.assertEqual(json.loads((self.out() / intake.REFRESH_LIST).read_text()), [])

    def test_triage_opened_at_survives_a_rewrite(self):
        first = mail('pat@example.test', -timedelta(hours=2), body='Can we meet', kind='preview', copy_key='k3')
        self.run_intake([first])
        path = self.out() / intake.REFRESH_LIST
        entries = json.loads(path.read_text())
        entries[0]['opened_at'] = NOW.isoformat()
        path.write_text(json.dumps(entries))
        self.run_intake([first, mail('lee@example.test', -timedelta(hours=1), body='Hello there', kind='preview',
                                     subject='Other')])
        opened = {e['message_id']: e.get('opened_at') for e in json.loads(path.read_text())}
        self.assertEqual(opened[ident(first)], NOW.isoformat())
        self.assertEqual(len(opened), 2)

    def test_meeting_keeps_open_action_hidden_by_thirty_newer_timeline_rows(self):
        self.fake.timeline['C-901'] = [
            {'verb': 'log-activity', 'occurred_at': NOW.isoformat(), 'summary': 'Synthetic note'}
            for _ in range(31)]
        action = {'id': 'synthetic-action', 'version': 2,
                  'updated_at': (NOW - timedelta(days=3)).isoformat()}
        original = self.fake.carr
        def server(verb, args):
            if verb == 'get-next-action':
                self.assertEqual(args, {'ref': 'C-901', 'owner': 'joe'})
                return {'ok': True, 'owner': 'joe', 'action': action}
            return original(verb, args)
        self.fake.carr = server
        result = self.run_intake(events=[event('G-protected', timedelta(days=2),
                                  attendees=[('Pat Example', 'pat@example.test')])])
        self.assertEqual(result.state, 'succeeded')
        self.assertEqual(self.fake.writes('set-next-action'), [])
        self.assertEqual(result.counts['calendar_lifecycle_unsupported'], 1)
        self.assertEqual(action['version'], 2)

    def test_one_model_call_per_new_matched_email_and_none_for_unmatched(self):
        self.run_intake([mail('pat@example.test', -timedelta(hours=1)),
                         mail('stranger@example.test', -timedelta(hours=1), subject='Spam')])
        self.assertEqual(len(self.fake.model_calls), 1)

    def test_a_next_step_sets_a_ball_when_none_exists(self):
        self.fake.model_reply = {'next_step': 'Send the floor plan', 'due_on': '2026-10-22'}
        m = mail('pat@example.test', -timedelta(hours=1))
        self.run_intake([m])
        [(_, args)] = self.fake.writes('set-next-action')
        self.assertEqual((args['ref'], args['description'], args['due_on']),
                         ('C-901', 'Send the floor plan', '2026-10-22'))
        self.assertEqual(args['idempotency_key'],
                         str(uuid.uuid5(uuid.NAMESPACE_URL, f'set-next-action|C-901|next|{ident(m)}')))
        self.assertIn('no prior ball', self.report_text())

    def test_a_ball_newer_than_the_email_is_kept(self):
        self.fake.model_reply = {'next_step': 'Send the floor plan', 'due_on': None}
        self.fake.actions['C-901', 'joe'] = {'id': 'current', 'version': 1,
            'updated_at': (NOW - timedelta(minutes=10)).isoformat()}
        self.run_intake([mail('pat@example.test', -timedelta(hours=1))])
        self.assertEqual(self.fake.writes('set-next-action'), [])
        self.assertIn('kept newer ball', self.report_text())

    def test_a_ball_older_than_the_email_is_replaced_conditionally(self):
        self.fake.model_reply = {'next_step': 'Send the floor plan', 'due_on': None}
        self.fake.actions['C-901', 'joe'] = {'id': 'older', 'version': 3,
            'updated_at': (NOW - timedelta(days=3)).isoformat()}
        self.run_intake([mail('pat@example.test', -timedelta(hours=1))])
        [(_, args)] = self.fake.writes('set-next-action')
        self.assertEqual(args['due_on'], '2026-10-20')
        self.assertEqual(args['owner'], 'joe')
        self.assertEqual(args['expected_action'], {'id': 'older', 'version': 3})
        self.assertIn('replaced ball', self.report_text())

    def test_another_owners_ball_is_untouched(self):
        self.fake.model_reply = {'next_step': 'Send the floor plan', 'due_on': None}
        other = {'id': 'other', 'version': 4, 'updated_at': NOW.isoformat()}
        self.fake.actions['C-901', 'dell'] = other.copy()
        self.run_intake([mail('pat@example.test', -timedelta(hours=1))])
        [(_, args)] = self.fake.writes('set-next-action')
        self.assertEqual((args['owner'], args['expected_action']), ('joe', None))
        self.assertEqual(self.fake.actions['C-901', 'dell'], other)

    def test_intervening_human_action_is_kept_and_conflict_is_not_retried(self):
        self.fake.model_reply = {'next_step': 'Send the floor plan', 'due_on': None}
        original = self.fake.carr
        human = {'id': 'human', 'version': 1, 'updated_at': NOW.isoformat()}
        def server(verb, args):
            if verb == 'set-next-action':
                self.fake.actions[args['ref'], 'joe'] = human.copy()
            return original(verb, args)
        self.fake.carr = server
        m = mail('pat@example.test', -timedelta(hours=1))
        result = self.run_intake([m])
        self.assertEqual(result.state, 'succeeded')
        self.assertEqual((result.counts['balls_kept'], result.counts['balls_set']), (1, 0))
        self.assertEqual(self.fake.actions['C-901', 'joe'], human)
        request = json.loads((self.out() / intake.STATE).read_text())['requests'][
            intake.key('set-next-action', 'C-901', f'next|{ident(m)}')]
        self.assertEqual(request['disposition'], 'superseded')
        self.fake.now += timedelta(minutes=30)
        self.run_intake([m], touch=False)
        self.assertEqual(len(self.fake.writes('set-next-action')), 1)

    def test_human_revision_of_same_action_between_emails_is_kept(self):
        self.fake.model_reply = {'next_step': 'Reply to email', 'due_on': None}
        original = self.fake.carr
        human = None
        def server(verb, args):
            nonlocal human
            result = original(verb, args)
            if verb == 'set-next-action' and human is None:
                human = {**self.fake.actions[args['ref'], 'joe'], 'version': 2,
                         'updated_at': NOW.isoformat()}
                self.fake.actions[args['ref'], 'joe'] = human.copy()
            return result
        self.fake.carr = server
        result = self.run_intake([mail('pat@example.test', -timedelta(hours=2), subject='First'),
                                  mail('pat@example.test', -timedelta(hours=1), subject='Second')])
        self.assertEqual(result.state, 'succeeded')
        self.assertEqual(len(self.fake.writes('set-next-action')), 1)
        self.assertEqual(self.fake.actions['C-901', 'joe'], human)

    def test_unreadable_canonical_action_never_defaults_to_empty(self):
        self.fake.model_reply = {'next_step': 'Send the floor plan', 'due_on': None}
        original = self.fake.carr
        for reply in ({'ok': False}, {'ok': True, 'owner': 'joe'},
                      {'ok': True, 'owner': 'dell', 'action': None},
                      {'ok': True, 'owner': 'joe', 'action': {}}):
            with self.subTest(reply=reply):
                shutil.rmtree(self.out(), ignore_errors=True)
                self.fake.calls.clear()
                self.fake.carr = lambda verb, args: reply if verb == 'get-next-action' else original(verb, args)
                self.assertEqual(self.run_intake([mail('pat@example.test', -timedelta(hours=1))]).state, 'failed')
                self.assertEqual(self.fake.writes('set-next-action'), [])

    def test_the_later_of_two_emails_in_one_run_owns_the_ball(self):
        self.fake.model_reply = {'next_step': 'Reply', 'due_on': None}
        second = mail('pat@example.test', -timedelta(hours=1), subject='Second')
        self.run_intake([mail('pat@example.test', -timedelta(hours=2), subject='First'), second])
        keys = [a['idempotency_key'] for _, a in self.fake.writes('set-next-action')]
        self.assertEqual(keys[-1], str(uuid.uuid5(uuid.NAMESPACE_URL, f'set-next-action|C-901|next|{ident(second)}')))

    def test_a_model_failure_logs_the_activity_fails_the_run_and_retries_the_step(self):
        mails = [mail('pat@example.test', -timedelta(hours=1))]
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
        mails = [mail('pat@example.test', -timedelta(hours=1))]
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

    def test_a_failed_action_retry_preserves_an_intervening_newer_joe_action(self):
        mails = [mail('pat@example.test', -timedelta(hours=1))]
        self.fake.model_reply = {'next_step': 'Send old plan', 'due_on': None}
        self.fake.fail_verbs = {'set-next-action'}
        self.assertEqual(self.run_intake(mails).state, 'failed')
        newer = {'id': 'human-newer', 'version': 2,
                 'updated_at': (NOW + timedelta(minutes=10)).isoformat()}
        self.fake.actions['C-901', 'joe'] = newer.copy()
        self.fake.fail_verbs.clear()
        self.fake.now += timedelta(minutes=30)
        self.assertEqual(self.run_intake(mails, touch=False).state, 'succeeded')
        self.assertEqual(self.fake.actions['C-901', 'joe'], newer)
        self.assertEqual(self.run_intake(mails, touch=False).reason, 'store_unchanged')

    def test_conditional_conflict_from_transport_is_a_safe_code(self):
        response = subprocess.CompletedProcess([], 1, '',
            'TOOL ERROR {"error":"next_action_conflict","detail":"PRIVATE-SENTINEL"}')
        with patch('subprocess.run', return_value=response):
            with self.assertRaises(intake.CarrError) as raised:
                intake._carr(self.repo)('set-next-action', {'idempotency_key': 'synthetic'})
        self.assertEqual(raised.exception.code, 'next_action_conflict')
        self.assertNotIn('PRIVATE-SENTINEL', str(raised.exception))

    def test_legacy_unguarded_action_request_is_not_replayed(self):
        m = mail('pat@example.test', -timedelta(hours=1))
        self.fake.model_reply = {'next_step': 'Send the old plan', 'due_on': None}
        self.fake.fail_verbs = {'set-next-action'}
        self.assertEqual(self.run_intake([m]).state, 'failed')
        path = self.out() / intake.STATE
        state = json.loads(path.read_text())
        request = state['requests'][intake.key('set-next-action', 'C-901', f'next|{ident(m)}')]
        args = json.loads(request['payload'])
        del args['expected_action']
        del args['owner']
        request['payload'] = json.dumps(args)
        path.write_text(json.dumps(state))
        self.fake.fail_verbs.clear()
        self.fake.now += timedelta(minutes=30)
        self.fake.calls.clear()
        self.assertEqual(self.run_intake([m], touch=False).reason, 'request_blocked')
        self.assertEqual(self.fake.writes('set-next-action'), [])
        saved = json.loads(path.read_text())
        pending = saved['requests'][args['idempotency_key']]
        self.assertFalse(pending['acknowledged'])
        self.assertEqual(pending['disposition'], 'blocked')
        self.assertEqual(pending['error_code'], 'legacy_action_guard_required')
        self.assertEqual(pending['payload'], request['payload'])
        self.assertIn(ident(m), saved['steps'])

    def test_pending_action_survives_two_write_failures_then_a_model_failure(self):
        mails = [mail('pat@example.test', -timedelta(hours=1))]
        self.fake.model_reply = {'next_step': 'Send the floor plan', 'due_on': None}
        self.fake.fail_verbs = {'set-next-action'}
        for _ in range(2):
            self.assertEqual(self.run_intake(mails).state, 'failed')
            self.fake.now += timedelta(minutes=30)
        self.fake.model_reply = RuntimeError('model down')
        self.assertEqual(self.run_intake(mails).state, 'failed')
        fourth = self.run_intake(mails, touch=False)
        self.assertEqual(fourth.state, 'failed', 'an unacknowledged action cannot become nothing_new')
        self.assertNotIn('high_water', json.loads((self.out() / intake.STATE).read_text()))
        self.fake.fail_verbs.clear()
        self.fake.now += timedelta(minutes=30)
        recovered = self.run_intake([], touch=False)
        self.assertEqual(recovered.state, 'succeeded', 'frozen actions no longer need the source or model')
        self.assertEqual(self.fake.writes('set-next-action')[-1][1]['description'], 'Send the floor plan')
        self.assertEqual(len(self.fake.model_calls), 1, 'model proposal is frozen before the first write')

    def test_interrupted_activity_with_missing_source_keeps_its_model_step_pending(self):
        message = mail('pat@example.test', -timedelta(hours=1))
        original = self.fake.carr
        interrupted = False
        def server(verb, args):
            nonlocal interrupted
            result = original(verb, args)
            if verb == 'log-activity' and not interrupted:
                interrupted = True
                raise SystemExit('interrupted after activity commit')
            return result
        self.fake.carr = server
        with self.assertRaises(SystemExit):
            self.run_intake([message])
        result = self.run_intake([], touch=False)
        self.assertEqual((result.state, result.reason), ('failed', 'steps_pending'))
        self.assertNotIn('high_water', json.loads((self.out() / intake.STATE).read_text()))
        self.fake.model_reply = {'next_step': 'Send the plan', 'due_on': None}
        self.assertEqual(self.run_intake([message], touch=False).state, 'succeeded')
        self.assertEqual(len(self.fake.writes('set-next-action')), 1)


    def test_outside_seven_to_seven_nothing_is_decoded_or_called(self):
        local = load_settings(self.home).timezone
        for when in (datetime(2026, 10, 20, 0, 0, tzinfo=local), datetime(2026, 10, 20, 6, 59, tzinfo=local),
                     datetime(2026, 10, 20, 19, 31, tzinfo=local)):
            self.fake.now = when
            result = self.run_intake([mail('pat@example.test', -timedelta(hours=1))])
            self.assertEqual((result.state, result.reason), ('skipped', 'outside_window'), when)
            self.assertEqual(intake.EXIT[result.state], 78)
        self.assertEqual((self.fake.decodes, self.fake.calls), (0, []))
        self.assertFalse((self.out() / intake.STATE).exists())
        self.fake.now = datetime(2026, 10, 20, 7, 0, tzinfo=local)
        self.assertEqual(self.run_intake([mail('pat@example.test', -timedelta(hours=1))]).state, 'succeeded')
        self.fake.now = datetime(2026, 10, 20, 19, 0, 30, tzinfo=local)
        self.assertNotEqual(self.run_intake([mail('lee@example.test', -timedelta(hours=1))]).reason, 'outside_window')

    def test_calendar_objects_with_no_decoded_event_fail_without_advancing(self):
        result = self.run_intake([mail('pat@example.test', -timedelta(hours=1))], identities=5, decoded=0)
        self.assertEqual((result.state, result.reason), ('failed', 'calendar_decode_empty'))
        self.assertEqual(self.fake.calls, [])
        self.assertFalse((self.out() / intake.STATE).exists(), 'the watermark does not move')




    def test_a_failed_cleanup_fails_the_run_loudly(self):
        def jam(work):
            locked = work / 'locked'
            locked.mkdir()
            (locked / 'f').write_bytes(b'x')
            os.chmod(locked, 0o500)
            self.addCleanup(os.chmod, locked, 0o700)
        self.fake.on_decode = jam
        result = self.run_intake([mail('pat@example.test', -timedelta(hours=1))])
        self.assertEqual((result.state, result.reason), ('failed', 'cleanup_failed'))
        self.assertEqual(self.fake.calls, [])

    def test_private_files_are_narrowed_to_0600_even_when_they_already_existed(self):
        self.out().mkdir(parents=True)
        for name in ('lock', intake.STATE, intake.REFRESH_LIST):
            path = self.out() / name
            path.write_text('[]' if name == intake.REFRESH_LIST else '{}')
            os.chmod(path, 0o644)
        self.run_intake([mail('pat@example.test', -timedelta(hours=1), kind='preview', body='Can we meet')])
        for path in self.out().iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, path.name)

    def test_a_malformed_model_reply_sets_no_ball(self):
        self.fake.model_reply = {'next_step': 'Reply', 'due_on': 'next tuesday'}
        result = self.run_intake([mail('pat@example.test', -timedelta(hours=1))])
        self.assertEqual(self.fake.writes('set-next-action'), [])
        self.assertEqual(result.counts['model_failed'], 1)





    def test_low_verification_fails_without_calling_the_record_layer(self):
        result = self.run_intake([mail('pat@example.test', -timedelta(hours=1))], found=1000, verified=900)
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

    def test_copy_permission_denied_names_the_helper_that_needs_full_disk_access(self):
        with patch('subprocess.run', side_effect=PermissionError(errno.EPERM, 'Operation not permitted')):
            result = self.run_intake()
        self.assertEqual(result.state, 'failed')
        self.assertIn(f'grant Full Disk Access to {self.home}/carr-local/bin/outlook-intake-copy',
                      intake.status_line(result))
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self.fake.decodes, 0)
        self.assertFalse((self.out() / intake.STATE).exists())
        self.assertEqual([p for p in self.out().iterdir() if p.is_dir()], [])

    def test_helper_permission_exit_codes_name_full_disk_access(self):
        for code in (errno.EPERM, errno.EACCES):
            with patch('subprocess.run', return_value=subprocess.CompletedProcess([], code)):
                result = self.run_intake()
            self.assertIn('grant Full Disk Access to', intake.status_line(result))
            self.assertEqual(result.state, 'failed')

    def test_helper_timeout_fails_and_cleans_up(self):
        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired('copy', 900)):
            result = self.run_intake()
        self.assertEqual((result.state, result.reason), ('failed', 'decode_failed'))
        self.assertEqual([p for p in self.out().iterdir() if p.is_dir()], [])

    def test_unreachable_contacts_fail_loudly(self):
        def down():
            raise intake.CarrError()
        ports = self.fake.ports()
        ports.contacts = down
        write_db(self.fake.fixture, [mail('pat@example.test', -timedelta(hours=1))])
        result = intake.run(self.repo, self.home, ports)
        self.assertEqual((result.state, result.reason), ('failed', 'carr_unreachable'))

    def test_a_failed_write_fails_the_run_and_is_retried_next_time(self):
        self.fake.fail_verbs = {'log-activity'}
        first = self.run_intake([mail('pat@example.test', -timedelta(hours=1))])
        self.assertEqual((first.state, first.reason), ('failed', 'writes_failed'))
        self.fake.fail_verbs = set()
        self.fake.now = NOW + timedelta(minutes=30)
        second = self.run_intake([mail('pat@example.test', -timedelta(hours=1))])
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
        result = self.run_intake([mail('pat@example.test', -timedelta(hours=1), name='Pat Example')])
        line = intake.status_line(result)
        for secret in ('pat@example.test', 'Pat Example', 'Suite question', 'C-901'):
            self.assertNotIn(secret, line)
        self.assertTrue(line.startswith('outlook-intake succeeded'))


class FolderTest(unittest.TestCase):
    FOLDERS = """
    CREATE TABLE folders (id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL, name TEXT,
      block INTEGER NOT NULL, parent_status TEXT NOT NULL, parent_id INTEGER);
    CREATE TABLE message_folders (message_id INTEGER NOT NULL, folder_id INTEGER NOT NULL);
    """

    def db(self, folders, links, columns=True):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 'store.db'
        write_db(path, [mail('pat@example.test', -timedelta(hours=1), subject=f'm{i}', copy_key=f'k{i}')
                        for i in range(1, 6)])
        con = sqlite3.connect(path)
        con.executescript(self.FOLDERS if columns else self.FOLDERS.replace(
            ',\n      block INTEGER NOT NULL, parent_status TEXT NOT NULL, parent_id INTEGER', ', block INTEGER'))
        for row in folders:
            con.execute('INSERT INTO folders VALUES (?,?,?,?,?,?)' if columns
                        else 'INSERT INTO folders VALUES (?,?,?,?)', row if columns else row[:3] + (0,))
        con.executemany('INSERT INTO message_folders VALUES (?,?)', links)
        con.commit()
        con.close()
        _, mails, _ = intake._load(path)
        return {m['subject']: m['folders'] for m in mails}

    def test_messages_carry_full_folder_paths(self):
        found = self.db([(1, 7, 'Inbox', 0, 'top', None), (2, 7, 'Sent Items', 0, 'top', None),
                         (3, 7, 'Active Deals', 0, 'top', None), (4, 7, 'Deal A', 0, 'folder', 3)],
                        [(1, 1), (2, 2), (3, 4), (3, 1)])
        self.assertEqual(found, {'m1': ['Inbox'], 'm2': ['Sent Items'], 'm3': ['Active Deals/Deal A', 'Inbox'],
                                 'm4': [], 'm5': []})

    def test_unresolved_ancestors_give_no_path(self):
        found = self.db([(1, 7, None, 0, 'top', None), (2, 7, 'Child', 0, 'folder', 1),
                         (3, 7, 'Moved', 0, 'unknown', None), (4, 7, 'Loop', 0, 'folder', 5),
                         (5, 7, 'Back', 0, 'folder', 4), (6, 7, 'Orphan', 0, 'folder', 99)],
                        [(1, 2), (2, 3), (3, 4), (4, 6)])
        self.assertEqual(set(map(tuple, found.values())), {()})

    def test_older_decoders_without_parents_or_folders_load(self):
        self.assertEqual(set(map(tuple, self.db([(1, 7, 'Inbox', 0, 'top', None)], [(1, 1)],
                                                columns=False).values())), {()})
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        write_db(Path(tmp.name) / 'old.db', [mail('pat@example.test', -timedelta(hours=1))])
        _, [m], _ = intake._load(Path(tmp.name) / 'old.db')
        self.assertEqual(m['folders'], [])

    def test_collapsed_copies_keep_every_folder(self):
        rows = [dict(mail('pat@example.test', -timedelta(hours=1), copy_key='k'), folders=['Inbox']),
                dict(mail('pat@example.test', -timedelta(hours=1) + timedelta(seconds=5), copy_key='k'),
                     folders=['Active Deals/Deal A'])]
        [best] = intake._best_copies(rows)
        self.assertEqual(best['folders'], ['Active Deals/Deal A', 'Inbox'])


class ScheduleTest(unittest.TestCase):
    def test_intake_runs_only_on_the_studio_primary_role(self):
        from lib.launchd_scope import allowed_on_machine
        self.assertTrue(allowed_on_machine('com.carr.outlook-intake.plist', True))
        self.assertFalse(allowed_on_machine('com.carr.outlook-intake.plist', False))

    def test_the_plist_runs_every_half_hour_from_seven_to_seven_under_the_scheduler(self):
        import plistlib
        from lib.launchd_calendar import calendar_for_window
        plist = plistlib.loads((ROOT / 'ops/launchd/com.carr.outlook-intake.plist').read_bytes())
        self.assertEqual(plist['StartCalendarInterval'], calendar_for_window((7, 0), (19, 0), 30))
        self.assertEqual(len(plist['StartCalendarInterval']), 25)
        self.assertNotIn('RunAtLoad', plist)
        self.assertNotIn('StartInterval', plist)
        self.assertEqual(plist['ProgramArguments'],
                         ['{{COPY_HELPER}}', '--run-intake'])
        services = json.loads((ROOT / 'ops/config/services.json').read_text())['services']
        [env] = [s for s in services if s['key'] == 'outlook-intake'][0]['environments']
        self.assertEqual(env['runs_on'], 'primary')
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
        write_settings(self.home, self.home / 'repo', owner='joe')
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

    def test_other_profiles_and_store_override_do_not_widen_the_copy_scope(self):
        expected = self.store('Main Identity')
        newer = self.store('Main Profile')
        os.utime(newer, ns=(2**62, 2**62))
        os.environ['CARR_OUTLOOK_STORE'] = str(newer)
        self.assertEqual(intake.resolve_store(self.home), expected)

    def test_no_main_identity_store_names_the_fixed_path(self):
        self.store('Main Profile')
        with self.assertRaises(FileNotFoundError) as refusal:
            intake.resolve_store(self.home)
        self.assertIn('Main Identity/HxStore.hxd', str(refusal.exception))

if __name__ == '__main__':
    unittest.main()
