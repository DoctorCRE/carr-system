#!/usr/bin/env python3
"""Offline account requirement qualification. Exit 1 means intake is unsafe.

The owning accounts below are synthetic ground truth, deliberately separate
from participants. Use the real export schema, fake ports and temporary homes.
No live Outlook files, helper, model or CARR connection are accessed.
"""
from datetime import timedelta
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('intake_fixture', ROOT / 'ops/outlook-intake-selftest.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
SOURCE = ROOT / 'vendor/hxprobe/src/main.rs'
source = SOURCE.read_text()
fixture.SCHEMA = re.search(r'db.execute_batch\(\s*"(.*?)",\s*\)', source, re.S)[1]
ALLOWED = 'joe.bookout@carr.us'
TESTER = 'joe.b.tester@outlook.com'
fixture.OWNER = ALLOWED
CASES = {
    'allowed': [ALLOWED],
    'tester': [TESTER],
    'other': ['other@example.test'],
    'missing': [],
    'ambiguous': [ALLOWED, TESTER],
}


def observe(labels, collide=False, preview=False, upcoming=False):
    test = fixture.IntakeTest()
    test.setUp()
    try:
        mails = [fixture.mail('pat@example.test', -timedelta(hours=1),
                              subject='Shared subject' if collide else label,
                              body='Shared body' if collide else label,
                              name='Pat Example', recipients=ALLOWED,
                              kind='preview' if preview else 'full') for label in labels]
        start = timedelta(days=1) if upcoming else -timedelta(hours=2)
        events = [fixture.event('Shared GOID' if collide else label, start,
                                subject='Shared meeting' if collide else label,
                                attendees=[('Pat Example', 'pat@example.test')],
                                organizer=('Owner', ALLOWED)) for label in labels]
        # A GOID/start pair is already globally collapsed by the decoder.
        if collide:
            events = events[:1]
        folder_owners = {ALLOWED: 7, TESTER: 8, 'other@example.test': 9}

        def add_folder_evidence(_):
            with sqlite3.connect(test.fake.fixture) as con:
                con.executemany('INSERT INTO folders VALUES (?,?,?,?)',
                                [(n, n, 'Inbox', 0) for n in folder_owners.values()])
                for message_id, label in enumerate(labels, 1):
                    for owner in CASES[label]:
                        con.execute('INSERT INTO message_folders VALUES (?,?)',
                                    (message_id, folder_owners[owner]))

        test.fake.on_decode = add_folder_evidence
        result = test.run_intake(mails, events)
        assert result.state == 'succeeded', result
        writes = test.fake.writes('log-activity')
        refresh = json.loads((test.out() / fixture.intake.REFRESH_LIST).read_text())
        reports = list(test.out().glob('report-*.md'))
        return {
            'ground_truth_owners': {label: CASES[label] for label in labels},
            'mail_participants': {'sender': 'pat@example.test', 'recipients': ALLOWED},
            'event_participants': {'organizer': ALLOWED, 'attendee': 'pat@example.test'},
            'email_writes': sum(a['kind'] == 'email_in' for _, a in writes),
            'meeting_writes': sum(a['kind'] == 'meeting' for _, a in writes),
            'action_writes': len(test.fake.writes('set-next-action')),
            'model_calls': len(test.fake.model_calls),
            'reports': len(reports),
            'refresh_entries': len(refresh),
            'summaries': [a['summary'] for _, a in writes],
        }
    finally:
        test.doCleanups()


def observe_legacy_retry():
    test = fixture.IntakeTest()
    test.setUp()
    try:
        test.fake.fail_verbs = {'log-activity'}
        message = fixture.mail('pat@example.test', -timedelta(hours=1),
                               subject='Legacy tester request', recipients=ALLOWED)
        first = test.run_intake([message])
        assert first.reason == 'writes_failed', first
        test.fake.calls.clear()
        test.fake.model_calls.clear()
        test.fake.fail_verbs.clear()
        second = test.run_intake()
        return {'ground_truth_owner': TESTER, 'source_rows_on_retry': 0,
                'retry_outcome': second.reason, 'replayed_writes': len(test.fake.writes()),
                'reports': len(list(test.out().glob('report-*.md')))}
    finally:
        test.doCleanups()


def main():
    observations = {label: observe([label]) for label in CASES}
    observations['mixed'] = observe(list(CASES))
    observations['colliding_allowed_and_tester'] = observe(['allowed', 'tester'], collide=True)
    observations['missing_preview'] = observe(['missing'], preview=True)
    observations['tester_upcoming'] = observe(['tester'], upcoming=True)
    observations['legacy_retry'] = observe_legacy_retry()
    assert observations['allowed']['email_writes'] == 1
    assert observations['allowed']['meeting_writes'] == 1
    qualified = all(observations[label]['email_writes'] == 0
                    and observations[label]['meeting_writes'] == 0
                    and observations[label]['model_calls'] == 0
                    and observations[label]['reports'] == 0
                    for label in CASES if label != 'allowed')
    qualified = (qualified and observations['missing_preview']['refresh_entries'] == 0
                 and observations['tester_upcoming']['action_writes'] == 0
                 and observations['legacy_retry']['replayed_writes'] == 0)
    with sqlite3.connect(':memory:') as con:
        con.executescript(fixture.SCHEMA)
        columns = {table: [r[1] for r in con.execute(f'PRAGMA table_info({table})')]
                   for table in ('folders', 'messages', 'message_folders', 'events', 'meta')}
    print(json.dumps({
        'qualified': qualified,
        'runtime_changed': False,
        'decoder_source_sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        'decoded_columns': columns,
        'observations': observations,
        'missing_facts': [
            'An authoritative local account-key to owning mailbox address mapping.',
            'An event revision to owning account reference, independent of organizer and attendees.',
            'Ownership preserved before message, event, conversation and sidecar revision merging.',
            'Ownership provenance for persisted requests, next-step replies and pending refresh entries.',
        ],
        'limitations': [
            'Ground truth labels belong to the synthetic harness, not to decoder output.',
            'Allowed positive control proves processing only, not verified account ownership.',
            'No binary HxStore parsing or live account ownership was exercised.',
        ],
    }, indent=2))
    return 0 if qualified else 1


if __name__ == '__main__':
    sys.exit(main())
