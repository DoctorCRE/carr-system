#!/usr/bin/env python3
"""Synthetic scheduled runner integration checks."""
from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sys
import tempfile
import shutil
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import outlook_triage as triage
from lib.outlook_ui import Window
from outlook_test_support import write_settings
from lib.outlook_settings import load_settings
from lib.outlook_triage_capability import REQUIRED_EVIDENCE


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / 'home'
        self.repo = Path(self.tmp.name) / 'repo'
        self.repo.mkdir()
        write_settings(self.home, self.repo, vendor_prefix='Vendors/')
        self.settings = load_settings(self.home)
        contract = self.repo / 'spec/outlook/triage-executor-contract.v1.json'
        contract.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / 'spec/outlook/triage-executor-contract.v1.json', contract)
        self.contract = contract
        self.path = self.repo / triage.OUT / 'snapshot.json'
        self.path.parent.mkdir(parents=True)
        fixture = json.loads((ROOT / 'ops/fixtures/outlook-triage/routing.json').read_text())
        observed = datetime.now(timezone.utc).isoformat()
        self.row = dict(account=self.settings.account, message_id='message-001', revision='rev-001',
                        source_folder_id='inbox-001', read_state='unread', snapshot_digest='digest-001',
                        decoder_pin='decoder-001', observed_at=observed, sender='client-001 folder-A',
                        subject='subject-001', body='body-001', recipients=['recipient@example.test'],
                        content_complete=True)
        self.value = dict(schema='outlook-triage-snapshot/v1', account=self.settings.account,
                          observed_at=observed, snapshot_digest='digest-001', decoder_pin='decoder-001',
                          observations=[self.row], inbox_folder_id='inbox-001', routing_version='route-001',
                          routing=dict(version='route-001', guide=fixture['guide'],
                                       find_results=fixture['find'], complete=True),
                          catalog=dict(version='catalog-001', folders=[dict(account=self.settings.account,
                                       folder_id=f'folder-{n}', path=path)
                                       for n, path in enumerate(fixture['paths'])]), effect_state='none')

    def save(self):
        self.path.write_text(json.dumps(self.value))
        self.path.chmod(0o600)

    def execute(self, closed=False):
        self.save()
        before = self.path.read_bytes()
        with patch('lib.outlook_ui.prepare_outlook', side_effect=AssertionError('desktop touched')), \
                patch.object(Window, 'move', side_effect=AssertionError('move attempted')):
            outcome, report = triage.triage(self.repo, self.home, triage.Waits(), triage.Limits(), lambda: closed)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(report.moved, [])
        return outcome, report

    def test_unread_plan_is_held_and_repeated_run_is_identical(self):
        first = self.execute()
        second = self.execute()
        self.assertEqual(first, second)
        self.assertEqual(first[0].reason, 'capability_not_verified')
        self.assertEqual(first[1].planning['planned'], 1)
        self.assertEqual(first[1].planning['held'], 1)
        self.assertEqual(self.row['read_state'], 'unread')

    def test_planner_holds_from_real_runner(self):
        for change, reason in [('duplicate', 'duplicate_identity'), ('folder', 'destination_unresolved'),
                               ('routing', 'routing_unavailable'), ('policy', 'stale_policy'),
                               ('ambiguous', 'deal_ambiguous')]:
            with self.subTest(change=change):
                old = json.loads(json.dumps(self.value))
                if change == 'duplicate':
                    self.value['observations'] *= 2
                elif change == 'folder':
                    self.value['catalog']['folders'] = []
                elif change == 'routing':
                    self.value['routing']['complete'] = False
                elif change == 'policy':
                    self.value['routing_version'] = 'new-route'
                else:
                    self.value['routing']['find_results']['client-001 folder-A']['parties'][0]['org_name'] = 'folder-B'
                _, report = self.execute()
                self.assertGreater(report.left[reason], 0)
                self.assertEqual(report.planning['planned'], 0)
                self.value = old

    def test_cap_is_enforced_by_runner(self):
        self.value['observations'] = [self.row | {'message_id': f'message-{n}'} for n in range(151)]
        _, report = self.execute()
        self.assertEqual(report.planning['planned'], 150)
        self.assertEqual(report.left['intent_cap_reached'], 1)

    def test_stale_future_and_inconsistent_provenance_refuse(self):
        for stamp in ('2000-01-01T00:00:00+00:00', '2099-01-01T00:00:00+00:00'):
            self.value['observed_at'] = stamp
            self.assertEqual(self.execute()[0].reason, 'snapshot_stale')
        self.value['observed_at'] = datetime.now(timezone.utc).isoformat()
        self.assertEqual(self.execute()[0].reason, 'snapshot_invalid')

    def test_uncertain_and_interrupted_never_retry(self):
        for state in ('uncertain', 'interrupted'):
            self.value['effect_state'] = state
            self.assertEqual(self.execute()[0].reason, 'reconciliation_required')
            self.assertEqual(self.execute()[0].reason, 'reconciliation_required')

    def test_sanctioned_metadata_cannot_invent_executor(self):
        value = json.loads(self.contract.read_text())
        candidate = value['candidates'][0]
        candidate.update(status='sanctioned', lifecycle='verified',
                         evidence={key: 'synthetic-receipt' for key in REQUIRED_EVIDENCE},
                         verified_read_policy=value['read_state_policy'])
        self.contract.write_text(json.dumps(value))
        self.assertEqual(self.execute()[0].reason, 'executor_unavailable')

    def test_closed_window_is_timed_out(self):
        self.assertEqual(self.execute(closed=True)[0].state, 'timed_out')

    def test_private_file_bounds_and_malformed_input_fail_closed(self):
        self.save()
        for contents in ('{', '[]', json.dumps(self.value | {'routing_version': []}),
                         json.dumps(self.value | {'observations': [self.row] * 10001})):
            self.path.write_text(contents)
            outcome, report = triage.triage(self.repo, self.home, triage.Waits(), triage.Limits(), lambda: False)
            self.assertEqual(outcome.reason, 'snapshot_invalid')
            self.assertEqual(report.planning['planned'], 0)
        self.save()
        self.path.chmod(0o644)
        self.assertEqual(triage.triage(self.repo, self.home, triage.Waits(), triage.Limits(),
                                     lambda: False)[0].reason, 'snapshot_invalid')
        self.path.unlink()
        target = self.path.parent / 'target.json'
        target.write_text(json.dumps(self.value))
        target.chmod(0o600)
        self.path.symlink_to(target)
        self.assertEqual(triage.triage(self.repo, self.home, triage.Waits(), triage.Limits(),
                                     lambda: False)[0].reason, 'snapshot_invalid')

    def test_fire_runs_main_despite_legacy_settlement_marker(self):
        self.save()
        moment = datetime.now(self.settings.timezone).replace(hour=19, minute=15)
        (self.path.parent / f'{moment:%Y-%m-%d}.settled').write_text('succeeded filed')
        with patch.object(triage, 'load_settings', return_value=self.settings), \
                patch.object(triage.os, 'chdir'), patch.object(triage.os, 'execv') as execute:
            triage.fire(self.repo, moment)
        self.assertEqual(execute.call_args.args[1][-1], triage.ENTRY)
        self.assertIn('main()', triage.ENTRY)
        with patch.object(triage, 'load_settings', return_value=self.settings), \
                patch.object(triage.os, 'execv') as execute:
            self.assertEqual(triage.fire(self.repo, moment.replace(hour=9)), 0)
        execute.assert_not_called()

    def test_main_writes_aggregate_hold_receipt_and_returns_skipped(self):
        self.save()
        moment = datetime.now(self.settings.timezone).replace(hour=19, minute=15)
        capture = io.StringIO()
        with patch.object(triage, 'load_settings', return_value=self.settings), \
                patch.object(triage.Path, 'home', return_value=self.home), \
                patch.object(triage, '__file__', str(self.repo / 'lib/outlook_triage.py')), \
                patch('lib.outlook_runtime.stamp', return_value={'runtime_sha': 'a' * 40}), \
                patch.object(triage, 'datetime', wraps=datetime) as clock, redirect_stdout(capture):
            clock.now.return_value = moment
            self.assertEqual(triage.main(), 78)
        output = capture.getvalue()
        self.assertIn('planned=1 held=1 moved=0', output)
        self.assertNotIn('client-001', output)
        receipt = self.path.parent / 'receipt.txt'
        receipt.write_text(next(line for line in output.splitlines()
                                if line.startswith('outlook-triage-receipt ')))
        payload = json.loads(receipt.read_text().split(' ', 1)[1])
        self.assertEqual(payload['state'], 'skipped')
        self.assertEqual(payload['runtime_sha'], 'a' * 40)
        self.assertEqual(payload['counts'], {})
        observer = subprocess.run([sys.executable, str(ROOT / 'ops/studio-proof-observe.py'),
                                   '--receipt-file', str(receipt), '--service', 'outlook-triage',
                                   '--runtime-sha', 'a' * 40, '--count', 'run.planned', '--minimum', '1'],
                                  capture_output=True, text=True, timeout=10)
        self.assertNotEqual(observer.returncode, 0, observer.stdout + observer.stderr)
        self.assertIn('successful receipt for expected full runtime SHA required', observer.stdout)
        reports = list(self.path.parent.glob('*.report.md'))
        self.assertEqual(len(reports), 1)
        self.assertIn('capability_not_verified', reports[0].read_text())
        self.assertEqual(reports[0].stat().st_mode & 0o777, 0o600)

    def test_missing_snapshot_reports_hold_without_touching_desktop(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            home = Path(tmp) / "home"
            write_settings(home, repo)
            with patch('lib.outlook_ui.prepare_outlook', side_effect=AssertionError('desktop touched')):
                outcome, report = triage.triage(repo, home, triage.Waits(), triage.Limits(), lambda: False)
            self.assertEqual(outcome.reason, 'snapshot_unavailable')
            self.assertEqual(report.planning['planned'], 0)
            self.assertEqual(report.planning['capability'], 'capability_not_verified')


class ProofDeclarationTest(unittest.TestCase):
    def test_checked_in_studio_requirement_passes_real_admission(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = Path(tmp) / 'body.md'
            files = Path(tmp) / 'files.json'
            body.write_text('## Production proof\nproofs/requirements/wr634-triage-runner.json\n')
            files.write_text(json.dumps(['lib/outlook_triage.py', 'lib/outlook_triage_snapshot.py',
                                         'lib/outlook_triage_capability.py']))
            result = subprocess.run([sys.executable, str(ROOT / 'ops/production-proof-plan.py'),
                                     '--body-file', str(body), '--files-file', str(files)],
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('execution unproven', result.stdout)


if __name__ == '__main__':
    unittest.main()
