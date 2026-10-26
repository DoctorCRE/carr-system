#!/usr/bin/env python3
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import subprocess
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.record_call import OK, REFUSED, TRANSIENT, VerbResult

SPEC = importlib.util.spec_from_file_location('checklist_reminders', ROOT / 'tools/deal-checklist-reminders.py')
assert SPEC is not None and SPEC.loader is not None
reminders = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reminders)

DEAL = '10000000-0000-4000-8000-000000000001'


class SweepTests(unittest.TestCase):
    def test_apply_queues_reminders_through_record_layer(self):
        calls = []

        def call(verb, args):
            calls.append((verb, args))
            reply = ({'schema_version': 'home-agenda.v1', 'deals': [{'id': DEAL, 'name': 'Private synthetic deal'}]}
                     if verb == 'read-home-agenda' else
                     {'ok': True, 'schema_version': 'deal-checklist-pings.v1', 'deal_id': DEAL,
                      'queued': 2, 'deduplicated': 1, 'notifications': []})
            return VerbResult(verb, OK, reply)

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            rc = reminders.main(['--apply'], call=call)
        self.assertEqual(rc, 0)
        self.assertEqual([verb for verb, _ in calls], ['read-home-agenda', 'ping-deal-checklist'])
        self.assertEqual(calls[1][1]['deal'], DEAL)
        report = json.loads(output.getvalue())
        self.assertEqual((report['queued'], report['deduplicated'], report['skipped'], report['failed']), (2, 1, 0, 0))
        self.assertNotIn('Private synthetic deal', output.getvalue())

    def test_invalid_agenda_never_starts_a_write(self):
        replies = [
            VerbResult('read-home-agenda', REFUSED, {'error': 'agenda_limit_exceeded'}),
            VerbResult('read-home-agenda', TRANSIENT, detail='private synthetic connection detail'),
            VerbResult('read-home-agenda', OK, None),
            VerbResult('read-home-agenda', OK, {'schema_version': 'wrong', 'deals': []}),
            VerbResult('read-home-agenda', OK, {'schema_version': 'home-agenda.v1', 'deals': None}),
            VerbResult('read-home-agenda', OK, {'schema_version': 'home-agenda.v1', 'deals': [{'id': DEAL}, {'id': 'invalid'}]}),
            VerbResult('read-home-agenda', OK, {'schema_version': 'home-agenda.v1', 'deals': [{'id': DEAL}] * 1001}),
        ]
        for index, reply in enumerate(replies):
            with self.subTest(case=index):
                calls = []
                def call(verb, args):
                    calls.append((verb, args))
                    return reply
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    rc = reminders.main(['--apply'], call=call)
                self.assertEqual(rc, 1)
                self.assertEqual([verb for verb, _ in calls], ['read-home-agenda'])
                self.assertEqual(json.loads(output.getvalue())['failed'], 1)
                self.assertNotIn('private synthetic', output.getvalue())

    def test_sweep_continues_after_failure_and_reports_skips_without_private_text(self):
        ids = [f'10000000-0000-4000-8000-{i:012d}' for i in range(1, 7)]
        replies = [
            VerbResult('ping-deal-checklist', REFUSED, {'error': 'deal_checklist_unavailable'}, 'Private synthetic detail'),
            VerbResult('ping-deal-checklist', TRANSIENT, detail='Private synthetic connection'),
            VerbResult('ping-deal-checklist', OK, {'ok': True}),
            VerbResult('ping-deal-checklist', REFUSED, {'error': 'Private synthetic name'}),
            VerbResult('ping-deal-checklist', OK, {'ok': True, 'schema_version': 'deal-checklist-pings.v1',
                       'deal_id': ids[4], 'queued': 0, 'deduplicated': 0, 'reason': 'deal_not_active'}),
            VerbResult('ping-deal-checklist', OK, {'ok': True, 'schema_version': 'deal-checklist-pings.v1',
                       'deal_id': ids[5], 'queued': 3, 'deduplicated': 2}),
        ]
        calls = []
        def call(verb, args):
            calls.append((verb, args))
            if verb == 'read-home-agenda':
                return VerbResult(verb, OK, {'schema_version': 'home-agenda.v1', 'deals': [{'id': ident} for ident in ids]})
            return replies.pop(0)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            rc = reminders.main(['--apply'], call=call)
        report = json.loads(output.getvalue())
        self.assertEqual(rc, 1)
        self.assertEqual([args['deal'] for verb, args in calls if verb == 'ping-deal-checklist'], ids)
        self.assertEqual((report['queued'], report['deduplicated'], report['skipped'], report['failed']), (3, 2, 1, 4))
        self.assertEqual(report['errors'], [
            {'deal_id': ids[0], 'error': 'deal_checklist_unavailable'},
            {'deal_id': ids[1], 'error': 'transient'},
            {'deal_id': ids[2], 'error': 'invalid_ping_reply'},
            {'deal_id': ids[3], 'error': 'refused'},
        ])
        self.assertNotIn('Private synthetic', output.getvalue())

    def test_default_dry_run_reads_only_and_empty_agenda_succeeds(self):
        for argv, deals in (([], [{'id': DEAL}]), (['--apply'], [])):
            with self.subTest(argv=argv):
                calls = []
                def call(verb, args):
                    calls.append((verb, args))
                    return VerbResult(verb, OK, {'schema_version': 'home-agenda.v1', 'deals': deals})
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    rc = reminders.main(argv, call=call)
                report = json.loads(output.getvalue())
                self.assertEqual(rc, 0)
                self.assertEqual(calls, [('read-home-agenda', {})])
                self.assertEqual(report['deals'], len(deals))
                self.assertEqual((report['queued'], report['deduplicated'], report['failed']), (0, 0, 0))

    def test_each_sweep_rechecks_deals_with_fresh_keys_and_deduplicates_ids(self):
        keys = []
        def call(verb, args):
            if verb == 'read-home-agenda':
                return VerbResult(verb, OK, {'schema_version': 'home-agenda.v1',
                    'deals': [{'id': DEAL}, {'id': DEAL}]})
            keys.append(args['idempotency_key'])
            return VerbResult(verb, OK, {'ok': True, 'schema_version': 'deal-checklist-pings.v1',
                'deal_id': DEAL, 'queued': 0, 'deduplicated': 0})
        for _ in range(2):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(reminders.main(['--apply'], call=call), 0)
            self.assertEqual(json.loads(output.getvalue())['deals'], 1)
        self.assertEqual(len(keys), 2)
        self.assertNotEqual(keys[0], keys[1])
        self.assertTrue(all(str(uuid.UUID(key)) == key for key in keys))

    def test_cli_help_starts_without_record_layer_access(self):
        result = subprocess.run([sys.executable, str(ROOT / 'tools/deal-checklist-reminders.py'), '--help'],
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--apply', result.stdout)
        self.assertIn('default only reads', result.stdout)


if __name__ == '__main__':
    unittest.main()
