# Test Salesforce browser refresh behavior.
import contextlib
import io
import datetime as dt
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
import subprocess
import plistlib
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import salesforce_browser_refresh as refresh
from lib.salesforce_active_snapshot import HEADERS, build_plan as snapshot_plan

spec = importlib.util.spec_from_file_location('sync_tests', Path(__file__).with_name('test-salesforce-deal-sync.py'))
assert spec and spec.loader
sync_tests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync_tests)

NOW = dt.datetime(2026, 10, 20, 11, 30, tzinfo=dt.timezone.utc)
URL = 'https://example.lightning.force.com/lightning/o/Opportunity/list?filterName=AllOpportunities'


def capture():
    return {'state': 'complete', 'expected_count': 1, 'rows': [
        {'id': '006000000000001AAA', 'name': 'Synthetic office', 'type': 'New', 'close': '12/01/2026',
         'commission': '$100', 'phase': 'Legal', 'transaction': 'Lease', 'owner': 'Joe Bookout',
         'company': 'Example Practice'}]}


class RefreshTests(unittest.TestCase):
    def test_complete_capture_saves_loader_compatible_private_snapshot_and_sync_is_idempotent(self):
        records = sync_tests.RecordLayer()
        def call(verb, args):
            if verb == 'find':
                return {'parties': [{'kind': 'client', 'name': 'Example Practice', 'ref': 'C-001', 'merged': False}]}
            return records(verb, args)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = refresh.run_refresh(root, capture, call, now=NOW)
            self.assertEqual(first['counts'], {'added': 1, 'updated': 0, 'unchanged': 0, 'failed': 0})
            snapshots = list(root.glob('*.psv'))
            self.assertEqual(len(snapshots), 1)
            self.assertEqual(snapshots[0].read_text().splitlines()[0], '|'.join(HEADERS))
            self.assertEqual(snapshots[0].stat().st_mode & 0o777, 0o600)
            self.assertIn('2026-10-20', snapshots[0].name)
            loaded = snapshot_plan(snapshots[0].read_text(), {'deals': []}, expected_count=1,
                bindings={'00001AAA': {'salesforce_id': '006000000000001AAA', 'client_ref': 'C-001'}})
            self.assertEqual(loaded['items'][0]['kind'], 'create')
            self.assertEqual(loaded['items'][0]['name'], 'Synthetic office')
            second = refresh.run_refresh(root, capture, call, now=NOW + dt.timedelta(minutes=1))
            self.assertEqual(second['counts'], {'added': 0, 'updated': 0, 'unchanged': 1, 'failed': 0})
            self.assertEqual(len(records.deals), 1)
            self.assertEqual(json.loads((root / 'state.json').read_text())['last_success'], second['at'])

    def test_signout_and_incomplete_capture_never_import_or_replace_last_success(self):
        for bad in ({'state': 'failed', 'reason': 'Salesforce sign-in needed'},
                    {**capture(), 'expected_count': 2},
                    {**capture(), 'rows': [capture()['rows'][0], capture()['rows'][0]], 'expected_count': 2}):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                root.joinpath('state.json').write_text(json.dumps({'last_success': NOW.isoformat()}))
                receipt = refresh.run_refresh(root, lambda: bad,
                    lambda verb, args: self.fail('invalid capture must not contact CARR'), now=NOW)
                self.assertEqual(receipt['status'], 'failed')
                self.assertEqual(receipt['counts']['failed'], 1)
                self.assertFalse(list(root.glob('*.psv')))
                self.assertEqual(json.loads(root.joinpath('state.json').read_text())['last_success'], NOW.isoformat())
                self.assertEqual(receipt['reason'], 'Salesforce sign-in needed' if 'reason' in bad else 'Salesforce page shape changed')

    def test_pending_write_stops_next_run_before_any_browser_or_record_access(self):
        records = sync_tests.RecordLayer()
        def interrupted(verb, args):
            if verb == 'find':
                return {'parties': [{'kind': 'client', 'name': 'Example Practice', 'ref': 'C-001', 'merged': False}]}
            result = records(verb, args)
            if verb == 'new-deal':
                raise TimeoutError('response lost')
            return result
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = refresh.run_refresh(root, capture, interrupted, now=NOW)
            self.assertEqual(first['status'], 'failed')
            self.assertEqual(len(records.deals), 1)
            second = refresh.run_refresh(root, lambda: self.fail('must reconcile before another capture'),
                lambda verb, args: self.fail('must reconcile before another call'), now=NOW)
            self.assertEqual(second['reason'], 'Salesforce prior run incomplete; reconcile private journal before retry')
            self.assertEqual(len(records.deals), 1)

    def test_watchdog_reports_signout_shape_and_strictly_older_than_26_hours(self):
        state = {'last_success': NOW.isoformat(), 'latest': {'status': 'success'}}
        self.assertEqual(refresh.health_findings(state, now=NOW + dt.timedelta(hours=26)), [])
        self.assertEqual(refresh.health_findings(state, now=NOW + dt.timedelta(hours=26, seconds=1))[0]['reason'],
                         'Salesforce last success older than 26h')
        for reason in ('Salesforce sign-in needed', 'Salesforce page shape changed'):
            state['latest'] = {'status': 'failed', 'reason': reason}
            findings = refresh.health_findings(state, now=NOW)
            self.assertEqual(findings[0]['reason'], reason)
        self.assertEqual(refresh.health_findings({}, now=NOW)[0]['reason'], 'Salesforce has no successful refresh')

    def test_scheduled_gate_uses_chicago_weekdays_and_attempts_only_once_even_after_failure(self):
        monday = dt.datetime(2026, 10, 19, 11, 30, tzinfo=dt.timezone.utc)
        self.assertFalse(refresh.schedule_due({}, now=monday - dt.timedelta(minutes=1)))
        self.assertTrue(refresh.schedule_due({}, now=monday))
        self.assertFalse(refresh.schedule_due({'attempted_on': '2026-10-19'}, now=monday))
        self.assertFalse(refresh.schedule_due({}, now=monday - dt.timedelta(days=1)))
        winter = dt.datetime(2026, 11, 2, 12, 30, tzinfo=dt.timezone.utc)
        self.assertTrue(refresh.schedule_due({}, now=winter))
        self.assertFalse(refresh.schedule_due({}, now=winter - dt.timedelta(minutes=1)))

    def test_url_validation_refuses_foreign_pages_and_credentials(self):
        self.assertEqual(refresh.validate_url(URL), URL)
        for bad in ('https://evil.example/lightning/o/Opportunity/list?filterName=All',
                    'https://user:secret@example.invalid/lightning/o/Opportunity/list?filterName=All',
                    URL.replace('/list?', '/report?'), URL.replace('https:', 'http:'), URL + '&x=extra',
                    URL.replace('AllOpportunities', '')):
            with self.subTest(url=bad), self.assertRaises(ValueError):
                refresh.validate_url(bad)

    def test_chrome_bridge_closes_its_owned_tab_on_signout_without_opening_a_report(self):
        run = Mock(side_effect=[subprocess.CompletedProcess([], 0, '123', ''),
                               subprocess.CompletedProcess([], 0, json.dumps({'state': 'failed', 'reason': 'Salesforce sign-in needed'}), ''),
                               subprocess.CompletedProcess([], 0, '', '')])
        result = refresh.capture_chrome(URL, run=run, wait=lambda _: None)
        self.assertEqual(result['reason'], 'Salesforce sign-in needed')
        self.assertEqual(run.call_args_list[0].args[0][-2:], ['open', URL])
        self.assertEqual(run.call_args_list[-1].args[0][-2:], ['close', '123'])

    def test_installer_preview_renders_a_quiet_chicago_schedule_without_installing(self):
        script = Path(__file__).with_name('install-salesforce-browser-refresh.py')
        result = subprocess.run([sys.executable, str(script), '--url', URL], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        plist = plistlib.loads(result.stdout.encode())
        self.assertEqual(plist['StartInterval'], 60)
        self.assertFalse(plist.get('RunAtLoad', False))
        self.assertEqual(plist['ProcessType'], 'Background')
        self.assertIn('--scheduled', plist['ProgramArguments'])
        self.assertEqual(plist['StandardOutPath'], '/dev/null')
        self.assertEqual(plist['EnvironmentVariables']['TZ'], 'America/Chicago')

    def test_partial_sync_receipt_counts_verified_rows_and_preserves_pending_journal(self):
        rows = capture()
        rows['rows'].append({**rows['rows'][0], 'id': '006000000000002AAA', 'name': 'Second synthetic office'})
        rows['expected_count'] = 2
        deals = [{'id': f'd{i}', 'name': row['name'], 'salesforce_id': row['id'], 'base_version': 1,
                  'phase': 'research', 'owner': 'joe', 'sf_close_date_placeholder': '2026-12-01'}
                 for i, row in enumerate(rows['rows'], 1)]
        records = sync_tests.RecordLayer(deals)
        def interrupted(verb, args):
            if verb == 'find':
                return {'parties': []}
            if verb == 'update-deal' and args['deal'] == 'd2':
                raise TimeoutError('response lost')
            return records(verb, args)
        with tempfile.TemporaryDirectory() as folder:
            receipt = refresh.run_refresh(Path(folder), lambda: rows, interrupted, now=NOW)
            self.assertEqual(receipt['counts'], {'added': 0, 'updated': 1, 'unchanged': 0, 'failed': 1})
            self.assertEqual(receipt['status'], 'failed')
            self.assertNotIn('last_success', json.loads(Path(folder, 'state.json').read_text()))

    def test_won_and_lost_sync_terminal_outcomes_then_repeat_unchanged(self):
        for stage, outcome in (('Closed Won', 'won'), ('Closed Lost', 'lost')):
            rows = capture()
            rows['rows'][0]['phase'] = stage
            records = sync_tests.RecordLayer()
            def call(verb, args):
                if verb == 'find':
                    return {'parties': [{'kind': 'client', 'name': 'Example Practice', 'ref': 'C-001', 'merged': False}]}
                return records(verb, args)
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as folder:
                first = refresh.run_refresh(Path(folder), lambda: rows, call, now=NOW)
                self.assertEqual(first['status'], 'success')
                self.assertEqual(records.deals[0].get('phase'), 'closed' if outcome == 'won' else 'pending')
                self.assertEqual(records.deals[0]['outcome'], outcome)
                second = refresh.run_refresh(Path(folder), lambda: rows, call, now=NOW)
                self.assertEqual(second['counts']['unchanged'], 1)

    def test_cli_signout_exits_failed_with_private_receipt_and_zero_imports(self):
        spec = importlib.util.spec_from_file_location('refresh_cli', Path(__file__).with_name('salesforce-browser-refresh.py'))
        assert spec and spec.loader
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            directory = home / '.config/carr/salesforce-refresh'
            directory.mkdir(parents=True)
            (directory / 'config.json').write_text(json.dumps({'url': URL}))
            output = io.StringIO()
            with patch.object(Path, 'home', return_value=home), patch.object(sys, 'argv', ['refresh']), \
                 patch.object(cli, 'capture_chrome', return_value={'state': 'failed', 'reason': 'Salesforce sign-in needed'}), \
                 patch.object(cli, 'deployed_verb', side_effect=AssertionError('No import on signout')), \
                 contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(), 1)
            receipt = json.loads(output.getvalue())
            self.assertEqual(receipt['reason'], 'Salesforce sign-in needed')
            self.assertEqual(receipt['counts'], {'added': 0, 'updated': 0, 'unchanged': 0, 'failed': 1})
            self.assertFalse(list(directory.glob('*.psv')))
            self.assertTrue(list(directory.glob('*.run.json')))

    def test_scheduled_failure_records_attempt_before_capture_and_does_not_retry_that_day(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = refresh.run_refresh(root, lambda: {'state': 'failed', 'reason': 'Salesforce sign-in needed'},
                lambda verb, args: self.fail('signout must not import'), now=NOW, scheduled=True)
            self.assertEqual(first['status'], 'failed')
            self.assertEqual(json.loads((root / 'state.json').read_text())['attempted_on'], '2026-10-20')
            second = refresh.run_refresh(root, lambda: self.fail('no same-day retry'),
                lambda verb, args: self.fail('no same-day import'), now=NOW + dt.timedelta(hours=1), scheduled=True)
            self.assertEqual(second['status'], 'skipped')
            self.assertEqual(len(list(root.glob('*.run.json'))), 1)


if __name__ == '__main__':
    unittest.main()
