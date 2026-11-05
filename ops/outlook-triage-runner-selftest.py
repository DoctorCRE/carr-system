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
        self.assertEqual(first[0].reason, 'snapshot_producer_unavailable')
        self.assertEqual(first[1].planning['planned'], 0)
        self.assertEqual(first[1].planning['held'], 0)
        self.assertEqual(self.row['read_state'], 'unread')

    def test_evidence_parser_and_planner_contracts(self):
        from lib.outlook_triage_snapshot import parse_snapshot, SnapshotError
        from lib.outlook_triage_plan import plan_triage
        self.value['observations'] = [self.row | {'message_id': f'message-{n}'} for n in range(151)]
        parsed = parse_snapshot(json.dumps(self.value).encode(), self.settings, 150)
        plan = plan_triage(*parsed[:4])
        self.assertEqual(len(plan.moves), 150)
        self.assertEqual(plan.leaves[0].reason, 'intent_cap_reached')
        for stamp in ('2000-01-01T00:00:00+00:00', '2099-01-01T00:00:00+00:00'):
            self.value['observed_at'] = stamp
            with self.assertRaisesRegex(SnapshotError, 'snapshot_stale'):
                parse_snapshot(json.dumps(self.value).encode(), self.settings, 150)

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
        self.assertEqual(self.execute()[0].reason, 'snapshot_producer_unavailable')

    def test_closed_window_is_timed_out(self):
        self.assertEqual(self.execute(closed=True)[0].state, 'timed_out')

    def test_private_file_bounds_and_malformed_input_fail_closed(self):
        self.save()
        for contents in ('{', '[]', json.dumps(self.value | {'routing_version': []}),
                         json.dumps(self.value | {'observations': [self.row] * 10001})):
            self.path.write_text(contents)
            outcome, report = triage.triage(self.repo, self.home, triage.Waits(), triage.Limits(), lambda: False)
            self.assertEqual(outcome.reason, 'snapshot_producer_unavailable')
            self.assertEqual(report.planning['planned'], 0)
        self.save()
        self.path.chmod(0o644)
        self.assertEqual(triage.triage(self.repo, self.home, triage.Waits(), triage.Limits(),
                                     lambda: False)[0].reason, 'snapshot_producer_unavailable')
        self.path.unlink()
        target = self.path.parent / 'target.json'
        target.write_text(json.dumps(self.value))
        target.chmod(0o600)
        self.path.symlink_to(target)
        self.assertEqual(triage.triage(self.repo, self.home, triage.Waits(), triage.Limits(),
                                     lambda: False)[0].reason, 'snapshot_producer_unavailable')

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

    def test_proof_window_reaches_current_attempt_outside_evening(self):
        moment = datetime.now(self.settings.timezone).replace(hour=9, minute=0)
        copy = self.repo / 'explicit-copy'
        with patch.object(triage, 'load_settings', return_value=self.settings), \
                patch.object(triage, '_triage', return_value=(triage.Outcome('skipped', 'snapshot_producer_unavailable'), triage.Report())) as attempt:
            outcome = triage.run(self.repo, self.home, moment, now_fn=lambda: moment,
                                 research_copy=copy, proof_window=True)
        self.assertEqual(outcome.reason, 'snapshot_producer_unavailable')
        self.assertEqual(attempt.call_args.args[-1], copy)
        self.assertFalse(attempt.call_args.args[-2]())
        attempt.reset_mock()
        with patch.object(triage, 'load_settings', return_value=self.settings), \
                patch.object(triage, '_triage') as attempt:
            self.assertEqual(triage.run(self.repo, self.home, moment, now_fn=lambda: moment).reason, 'outside_window')
        attempt.assert_not_called()

    def test_relative_copy_refuses_before_proof_output(self):
        moment = datetime.now(self.settings.timezone).replace(hour=9, minute=0)
        with patch.object(triage, 'load_settings', return_value=self.settings), \
                patch.object(triage, '_triage', side_effect=AssertionError('producer started')):
            outcome = triage.run(self.repo, self.home, moment, now_fn=lambda: moment,
                                 research_copy=Path('copy'), proof_window=True)
        self.assertEqual(outcome.reason, 'research_copy_not_absolute')
        self.assertFalse(self.path.exists())
        self.assertEqual(list(self.path.parent.glob('*.report.md')), [])

    def test_relative_copy_fire_refuses_before_directory_change_or_wrapper(self):
        moment = datetime.now(self.settings.timezone).replace(hour=9, minute=0)
        capture = io.StringIO()
        with patch.object(triage, 'load_settings', return_value=self.settings), \
                patch.object(triage.os, 'chdir') as chdir, \
                patch.object(triage.os, 'execv') as execute, redirect_stdout(capture):
            result = triage.fire(self.repo, moment, proof_window=True, research_copy=Path('copy'))
        chdir.assert_not_called()
        execute.assert_not_called()
        self.assertEqual(result, 78)
        self.assertIn('research_copy_not_absolute', capture.getvalue())

    def test_relative_copy_main_emits_named_refusal_without_output_files(self):
        capture = io.StringIO()
        with patch.object(triage, 'load_settings', return_value=self.settings), \
                patch.object(triage, '__file__', str(self.repo / 'lib/outlook_triage.py')), \
                patch('lib.outlook_runtime.stamp', return_value={'runtime_sha': 'a' * 40}), \
                patch.object(triage, '_triage', side_effect=AssertionError('producer started')), \
                redirect_stdout(capture):
            self.assertEqual(triage.main(['--proof-window', '--copy', 'copy']), 78)
        receipt = json.loads(next(line.split(' ', 1)[1] for line in capture.getvalue().splitlines()
                                  if line.startswith('outlook-triage-receipt ')))
        self.assertEqual(receipt['reason'], 'research_copy_not_absolute')
        self.assertFalse(self.path.exists())
        self.assertEqual(list(self.path.parent.glob('*.report.md')), [])

    def test_proof_deadline_refuses_planning_after_180_seconds(self):
        from datetime import timedelta
        moment = datetime.now(self.settings.timezone).replace(hour=9, minute=0)
        with patch.object(triage, 'load_settings', return_value=self.settings), \
                patch.object(triage, '_triage') as attempt:
            outcome = triage.run(self.repo, self.home, moment, proof_window=True,
                                 now_fn=lambda: moment + timedelta(seconds=180))
        self.assertEqual(outcome.reason, 'window_closed')
        attempt.assert_not_called()

    def test_proof_fire_uses_scheduled_wrapper_with_process_timeout(self):
        moment = datetime.now(self.settings.timezone).replace(hour=9, minute=0)
        copy = self.repo / 'explicit copy'
        with patch.object(triage, 'load_settings', return_value=self.settings), \
                patch.object(triage.os, 'chdir'), patch.object(triage.os, 'execv') as execute:
            triage.fire(self.repo, moment, proof_window=True, research_copy=copy)
        argv = execute.call_args.args[1]
        self.assertEqual(argv[:4], ['/bin/zsh', str(self.repo / 'bin/run-scheduled.sh'), triage.SERVICE, triage.RUN_KEY])
        self.assertEqual(argv[4:8], [sys.executable, str(self.repo / 'bin/with-timeout.py'), '180', sys.executable])
        self.assertEqual(argv[-3:], ['--proof-window', '--copy', str(copy)])

    def test_main_proof_emits_real_attempt_receipt_outside_evening(self):
        moment = datetime.now(self.settings.timezone).replace(hour=9, minute=0)
        capture = io.StringIO()
        with patch.object(triage, 'load_settings', return_value=self.settings), \
                patch.object(triage.Path, 'home', return_value=self.home), \
                patch.object(triage, '__file__', str(self.repo / 'lib/outlook_triage.py')), \
                patch('lib.outlook_runtime.stamp', return_value={'runtime_sha': 'a' * 40}), \
                patch.object(triage, 'datetime', wraps=datetime) as clock, redirect_stdout(capture):
            clock.now.return_value = moment
            self.assertEqual(triage.main(['--proof-window']), 78)
        output = capture.getvalue()
        self.assertNotIn('outside_window', output)
        receipt = json.loads(next(line.split(' ', 1)[1] for line in output.splitlines()
                                  if line.startswith('outlook-triage-receipt ')))
        self.assertEqual(receipt['reason'], 'snapshot_producer_unavailable')
        self.assertEqual(receipt['runtime_sha'], 'a' * 40)
        reports = list(self.path.parent.glob('*.report.md'))
        self.assertEqual(len(reports), 1)
        self.assertIn(moment.isoformat(), reports[0].read_text())

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
            self.assertEqual(triage.main([]), 78)
        output = capture.getvalue()
        self.assertIn('planned=0 held=0 moved=0', output)
        self.assertNotIn('client-001', output)
        receipt = self.path.parent / 'receipt.txt'
        receipt.write_text(next(line for line in output.splitlines()
                                if line.startswith('outlook-triage-receipt ')))
        payload = json.loads(receipt.read_text().split(' ', 1)[1])
        self.assertEqual(payload['state'], 'skipped')
        self.assertEqual(payload['runtime_sha'], 'a' * 40)
        self.assertEqual(payload['counts']['run'], dict(snapshot_validated=False, observed=0,
                         moved=0, held=False, exit_code=78))
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
            self.assertEqual(outcome.reason, 'snapshot_producer_unavailable')
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
