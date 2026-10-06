#!/usr/bin/env python3
import importlib.util
import json
from datetime import date
from pathlib import Path
import tempfile
import unittest
import plistlib
import subprocess
import sys
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('expert_watch', ROOT / 'tools/expert_reply_watch.py')
assert spec and spec.loader
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)
TODAY = date(2026, 10, 6)
ENTRY = {'handle': '@ihurricanez', 'topics': ['postgres/data-modelling'],
         'why': 'Stable columns and JSONB', 'trust': 'high', 'added_on': '2026-10-06', 'source': 'Joe screenshot'}
NUGGET = {'handle': '@ihurricanez', 'reply_url': 'https://x.com/ihurricanez/status/123',
          'parent_url': 'https://x.com/other/status/456', 'date': '2026-10-05',
          'quote': 'Store stable fields as columns.', 'technique': 'Use columns for stable fields and JSONB for changing fields.',
          'topics': ['postgres/data-modelling']}


class WatchTests(unittest.TestCase):
    def test_seed_and_schema(self):
        entries = watch.load_watchlist(ROOT / 'ops/config/expert-watchlist.v1.json')
        self.assertTrue(set([
            '@ihurricanez', '@FranckPachot', '@AntonMartyniuk', '@hecodesforme',
            '@reallevelbrook', '@josepollman82', '@shahidcodes']).issubset({e['handle'] for e in entries}))
        for change in ({'trust': 'yes'}, {'topics': []}, {'added_on': 'yesterday'}, {'handle': 'bad/name'}, {'why': ''}):
            with self.subTest(change=change), self.assertRaises(watch.WatchError):
                watch.validate_watchlist({'version': 1, 'entries': [{**ENTRY, **change}]})
        with self.assertRaises(watch.WatchError):
            watch.validate_watchlist({'version': 1, 'entries': [ENTRY, {**ENTRY, 'handle': '@IHURRICANEZ'}]})

    def test_parse_urls_and_empty(self):
        alternate = {**NUGGET, 'reply_url': 'https://twitter.com/IHURRICANEZ/status/123?s=20#reply'}
        self.assertEqual(watch.parse_replies(json.dumps(alternate), ENTRY, TODAY), [NUGGET])
        self.assertEqual(watch.parse_replies('\n', ENTRY, TODAY), [])
        self.assertEqual(watch.parse_replies(json.dumps({**NUGGET, 'reply_url': None}), ENTRY, TODAY), [])
        self.assertEqual(watch.parse_replies(json.dumps({**NUGGET, 'reply_url': 'https://example.com/123'}), ENTRY, TODAY), [])

    def test_json_validation(self):
        for output in ('prose', '[]', '{', json.dumps({**NUGGET, 'extra': 1})):
            with self.subTest(output=output), self.assertRaises(watch.WatchError):
                watch.parse_replies(output, ENTRY, TODAY)
        for change in ({'quote': 'word ' * 31}, {'technique': ''}, {'topics': ['unknown']},
                       {'handle': '@other'}, {'date': '2026-09-29'}, {'date': '2026-10-07'},
                       {'parent_url': None}, {'date': '2026-10-99'}):
            with self.subTest(change=change), self.assertRaises(watch.WatchError):
                watch.parse_replies(json.dumps({**NUGGET, **change}), ENTRY, TODAY)

    def test_seven_calendar_dates_include_today(self):
        for day in ('2026-09-30', '2026-10-06'):
            self.assertEqual(watch.parse_replies(json.dumps({**NUGGET, 'date': day}), ENTRY, TODAY)[0]['date'], day)

    def run_watch(self, root, grok=None, record=None):
        return watch.run([ENTRY], root, grok or Mock(return_value=json.dumps(NUGGET)),
                         record or Mock(return_value={'ok': True, 'capture_id': 'capture-123'}), today=TODAY)

    def test_dedupe_and_dated_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            record = Mock(return_value={'ok': True, 'capture_id': 'capture-123'})
            grok = Mock(return_value='\n'.join([json.dumps(NUGGET)] * 2))
            self.assertEqual(self.run_watch(root, grok, record), 1)
            self.assertEqual(self.run_watch(root, grok, record), 0)
            self.assertEqual(record.call_count, 1)
            verb, payload = record.call_args.args
            self.assertEqual(verb, 'log-capture')
            self.assertEqual(payload['status'], 'queued')
            self.assertEqual(payload['source_url'], NUGGET['reply_url'])
            self.assertIn('@ihurricanez', payload['session'])
            self.assertIn('postgres/data-modelling', payload['session'])
            ledger = json.loads((root / 'ledger.json').read_text())
            self.assertEqual([r['date'] for r in ledger['runs']], ['2026-10-06', '2026-10-06'])
            self.assertEqual(ledger['replies'][NUGGET['reply_url']]['status'], 'captured')

    def test_empty_week_writes_no_record(self):
        with tempfile.TemporaryDirectory() as d:
            record = Mock()
            self.assertEqual(self.run_watch(Path(d), Mock(return_value=''), record), 0)
            record.assert_not_called()

    def test_grok_failure_stops_once_and_records_defect(self):
        with tempfile.TemporaryDirectory() as d:
            grok = Mock(side_effect=watch.WatchError('grok exited 1'))
            record = Mock(return_value={'ok': True, 'defect_id': 'd1'})
            with self.assertRaises(watch.WatchError):
                watch.run([ENTRY, {**ENTRY, 'handle': '@other'}], Path(d), grok, record, today=TODAY)
            self.assertEqual(grok.call_count, 1)
            self.assertEqual(record.call_count, 1)
            self.assertEqual(record.call_args.args[0], 'record-defect')
            ledger = json.loads((Path(d) / 'ledger.json').read_text())
            self.assertEqual(ledger['runs'][0]['status'], 'failed')
            self.assertEqual(ledger['runs'][0]['defect_status'], 'recorded')

    def test_pending_write_recovers_same_payload(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            sent = []
            def interrupted(verb, payload):
                sent.append((verb, payload))
                raise KeyboardInterrupt()
            with self.assertRaises(KeyboardInterrupt):
                self.run_watch(root, record=interrupted)
            record = Mock(return_value={'ok': True, 'capture_id': 'recovered'})
            self.assertEqual(self.run_watch(root, Mock(return_value=''), record), 1)
            self.assertEqual(record.call_args.args, sent[0])
            ledger = json.loads((root / 'ledger.json').read_text())
            self.assertEqual(ledger['runs'][0]['status'], 'interrupted')

    def test_similarity_refusal_not_marked_captured_or_replayed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            record = Mock(return_value={'needs_confirm': True, 'candidates': [{'session': 'similar'}]})
            with self.assertRaises(watch.WatchError):
                self.run_watch(root, record=record)
            ledger = json.loads((root / 'ledger.json').read_text())
            self.assertEqual(ledger['replies'][NUGGET['reply_url']]['status'], 'unresolved')
            fresh_record = Mock()
            self.assertEqual(self.run_watch(root, record=fresh_record), 0)
            fresh_record.assert_not_called()

    def test_corrupt_ledger_refuses_before_grok(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'ledger.json').write_text('{')
            grok = Mock()
            with self.assertRaises(watch.WatchError):
                self.run_watch(root, grok)
            grok.assert_not_called()

    def test_cli_command_and_timeout(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []
            def execute(args, **kwargs):
                calls.append((args, kwargs))
                return ''
            watch.grok_replies(ENTRY, TODAY, Path(d), execute=execute)
            args, kwargs = calls[0]
            self.assertEqual(args[0:2], ['grok', '-p'])
            self.assertIn('from:ihurricanez filter:replies', args[2])
            self.assertIn('since:2026-09-30 until:2026-10-07', args[2])
            self.assertIn('ONLY', args[2])
            self.assertEqual(args[3:], ['-m', 'grok-4.5', '--reasoning-effort', 'high', '--sandbox', 'workspace', '--cwd', d])
            self.assertGreater(kwargs['timeout'], 0)

    def test_schedule_and_installer_registration(self):
        plist = plistlib.loads((ROOT / 'ops/launchd/com.carr.expert-reply-watch.plist').read_bytes())
        self.assertEqual(plist['StartCalendarInterval'], {'Weekday': 1, 'Hour': 6, 'Minute': 0})
        jobs = json.loads((ROOT / 'ops/config/scheduled-jobs.v1.json').read_text())
        job = next(row for row in jobs['jobs'] if row['label'] == plist['Label'])
        expected = [v.replace('{{REPO}}', '~/carr-system') for v in plist['ProgramArguments']]
        self.assertEqual(job['program_arguments'], expected)
        self.assertEqual(job['interval']['StartCalendarInterval'], plist['StartCalendarInterval'])
        sys.path.insert(0, str(ROOT))
        from lib.launchd_scope import allowed_on_machine
        self.assertTrue(allowed_on_machine('com.carr.expert-reply-watch.plist', True))
        self.assertFalse(allowed_on_machine('com.carr.expert-reply-watch.plist', False))
        services = json.loads((ROOT / 'ops/config/services.json').read_text())
        service = next(row for row in services['services'] if row['key'] == 'expert-reply-watch')
        self.assertEqual(service['environments'][0]['expected_cadence_seconds'], 7 * 86400)

    def test_ci_watchlist_command(self):
        result = subprocess.run([sys.executable, str(ROOT / 'tools/expert_reply_watch.py'), '--check-watchlist'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'Expert reply watchlist schema valid')

    def test_real_process_failure_hides_stderr_and_is_bounded(self):
        with self.assertRaisesRegex(watch.WatchError, 'exited 3'):
            watch.execute([sys.executable, '-c', 'import sys; print("SECRET", file=sys.stderr); sys.exit(3)'], timeout=5)
        with self.assertRaisesRegex(watch.WatchError, 'timed out'):
            watch.execute([sys.executable, '-c', 'import time; time.sleep(60)'], timeout=0.1)

    def test_invalid_ack_keeps_pending_and_defect_failure_is_durable(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            record = Mock(side_effect=[{'ok': True}, watch.WatchError('record unavailable')])
            with self.assertRaises(watch.WatchError):
                self.run_watch(root, record=record)
            ledger = watch.load_ledger(root)
            self.assertEqual(ledger['replies'][NUGGET['reply_url']]['status'], 'pending')
            self.assertEqual(ledger['runs'][0]['defect_status'], 'failed')
            self.assertEqual(record.call_count, 2)

    def test_record_wire_contract(self):
        with patch.object(watch, 'execute', return_value='{"ok":true,"capture_id":"one"}') as execute:
            self.assertEqual(watch.record_call('log-capture', {'status': 'queued'}), {'ok': True, 'capture_id': 'one'})
            self.assertEqual(execute.call_args.args[0][1:3], ['call', 'log-capture'])
        for output in ('invalid', '[]', '{"error":"refused"}', '{"isError":true}'):
            with self.subTest(output=output), patch.object(watch, 'execute', return_value=output), self.assertRaises(watch.WatchError):
                watch.record_call('log-capture', {})


if __name__ == '__main__':
    unittest.main()
