#!/usr/bin/env python3
# Test nightly deal workflow-suggestions, attention-alerts, and checklist-reminders engine sweeps.
import contextlib
from datetime import datetime, timezone
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.engine_mail import Reply
from lib.outlook_settings import SettingsError, load_settings
from ops.outlook_test_support import write_settings

SPEC = importlib.util.spec_from_file_location('nightly_deal_jobs', ROOT / 'tools/nightly-deal-jobs.py')
assert SPEC is not None and SPEC.loader is not None
jobs = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = jobs
SPEC.loader.exec_module(jobs)


CASES = (
    ('workflow-suggestions', 'engine_workflow_suggestions', 'workflow_suggestions',
     '/jobs/deals/workflow-suggestions', 'workflow suggestions',
     {'proposed': 4, 'already_proposed': 1}),
    ('attention-alerts', 'engine_attention_alerts', 'attention_alerts',
     '/jobs/deals/attention-alerts', 'attention alerts',
     {'alerted': 2, 'unchanged': 5, 'cleared': 1}),
)


class NightlyDealJobsTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.credentials = self.home / 'engine.env'
        self.enterContext(patch('lib.outlook_settings.Path.home', return_value=self.home))
        self.connect = self.enterContext(patch('lib.engine_mail.connect'))
        self.engine = self.connect.return_value

    def run_cli(self, *argv):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            rc = jobs.main(list(argv))
        return rc, output.getvalue(), errors.getvalue()

    def enable_switches(self, **switches):
        write_settings(self.home, self.home / 'repo', engine_credentials=str(self.credentials), **switches)

    def test_missing_disabled_and_unrelated_switches_skip_without_calls_or_output(self):
        for name, switch, _, _, _, _ in CASES:
            other = next(other_switch for _, other_switch, *_ in CASES if other_switch != switch)
            with self.subTest(job=name):
                self.connect.reset_mock()
                settings_file = self.home / '.config/carr/outlook.json'
                if settings_file.exists():
                    settings_file.unlink()
                self.assertEqual(self.run_cli(name, '--check-nightly-switch'), (78, '', ''))
                self.assertEqual(self.run_cli(name, '--nightly'), (78, '', ''))
                write_settings(self.home, self.home / 'repo', **{switch: False})
                self.assertEqual(self.run_cli(name, '--check-nightly-switch'), (78, '', ''))
                self.assertEqual(self.run_cli(name, '--nightly'), (78, '', ''))
                self.enable_switches(**{other: True})
                self.assertEqual(self.run_cli(name, '--check-nightly-switch'), (78, '', ''))
                self.assertEqual(self.run_cli(name, '--nightly'), (78, '', ''))
                self.connect.assert_not_called()

    def test_nightly_calls_each_engine_door_with_expected_header_body_and_counts(self):
        now = datetime(2026, 1, 9, 5, 30, tzinfo=timezone.utc)
        for name, switch, job_header, path, _, counts in CASES:
            with self.subTest(job=name):
                self.connect.reset_mock()
                self.engine.reset_mock()
                self.enable_switches(**{switch: True})
                self.assertEqual(self.run_cli(name, '--check-nightly-switch'), (0, '', ''))
                self.engine.return_value = Reply(200, dict(counts))
                with patch.object(jobs, 'datetime', create=True) as clock:
                    clock.now.side_effect = now.astimezone
                    rc, output, errors = self.run_cli(name, '--nightly')
                self.assertEqual((rc, errors), (0, ''))
                self.assertEqual(json.loads(output), counts)
                self.connect.assert_called_once_with(self.credentials, job=job_header)
                self.engine.assert_called_once()
                method, called_path, body = self.engine.call_args.args
                self.assertEqual((method, called_path), ('POST', path))
                request = json.loads(body)
                expected_key = str(uuid.uuid5(uuid.NAMESPACE_URL, f'engine|{job_header}|2026-01-08'))
                self.assertEqual(request, {'idempotency_key': expected_key})

    def test_idempotency_key_reuses_same_central_date_and_rotates_next_day(self):
        self.enable_switches(engine_workflow_suggestions=True, engine_attention_alerts=True)
        seen = {}
        for name, _, _, _, _, counts in CASES:
            self.engine.return_value = Reply(200, dict(counts))
            keys = []
            for moment in (
                datetime(2026, 1, 8, 18, 0, tzinfo=timezone.utc),
                datetime(2026, 1, 9, 5, 30, tzinfo=timezone.utc),
                datetime(2026, 1, 9, 6, 5, tzinfo=timezone.utc),
            ):
                self.engine.reset_mock()
                with patch.object(jobs, 'datetime', create=True) as clock:
                    clock.now.side_effect = moment.astimezone
                    rc, _, errors = self.run_cli(name, '--nightly')
                self.assertEqual((rc, errors), (0, ''))
                keys.append(json.loads(self.engine.call_args.args[2])['idempotency_key'])
            self.assertEqual(keys[0], keys[1])
            self.assertNotEqual(keys[1], keys[2])
            self.assertEqual(uuid.UUID(keys[0]).version, 5)
            seen[name] = keys[0]
        self.assertNotEqual(seen['workflow-suggestions'], seen['attention-alerts'])

    def test_engine_failures_and_invalid_counts_log_once_without_retry_or_private_text(self):
        for name, switch, _, _, label, counts in CASES:
            first_key = next(iter(counts))
            bad_replies = (
                Reply(503, {'error': 'private synthetic detail'}),
                Reply(403, {'error': 'unknown_job'}),
                Reply(0, {}),
                Reply(200, {**counts, first_key: True}),
                Reply(200, {**counts, first_key: -1}),
                Reply(200, {k: v for k, v in counts.items() if k != first_key}),
            )
            self.enable_switches(**{switch: True})
            for reply in bad_replies:
                with self.subTest(job=name, status=reply.status, body=reply.body):
                    self.engine.reset_mock()
                    self.engine.return_value = reply
                    rc, output, errors = self.run_cli(name, '--nightly')
                    self.assertEqual((rc, output), (1, ''))
                    self.assertEqual(len(errors.splitlines()), 1)
                    self.assertIn(label, errors)
                    self.assertNotIn('private synthetic detail', errors)
                    self.engine.assert_called_once()

    def test_credential_errors_log_once_without_private_text(self):
        for name, switch, _, _, label, _ in CASES:
            with self.subTest(job=name):
                self.enable_switches(**{switch: True})
                self.connect.side_effect = PermissionError('private synthetic path')
                try:
                    rc, output, errors = self.run_cli(name, '--nightly')
                finally:
                    self.connect.side_effect = None
                self.assertEqual((rc, output), (1, ''))
                self.assertEqual(len(errors.splitlines()), 1)
                self.assertIn(label, errors)
                self.assertNotIn('private synthetic path', errors)

    def test_enabled_switch_with_missing_credentials_fails_in_scheduled_step(self):
        for name, switch, _, _, label, _ in CASES:
            with self.subTest(job=name):
                self.connect.reset_mock()
                write_settings(self.home, self.home / 'repo', **{switch: True})
                self.assertEqual(self.run_cli(name, '--check-nightly-switch'), (0, '', ''))
                rc, output, errors = self.run_cli(name, '--nightly')
                self.assertEqual((rc, output), (1, ''))
                self.assertEqual(len(errors.splitlines()), 1)
                self.assertIn(label, errors)
                self.connect.assert_not_called()

    def test_non_boolean_switch_values_are_refused(self):
        for _, switch, *_ in CASES:
            for bad_value in ('true', 1, None):
                with self.subTest(switch=switch, value=bad_value):
                    write_settings(self.home, self.home / 'repo', **{switch: bad_value})
                    with self.assertRaisesRegex(SettingsError, switch):
                        load_settings(self.home)

    def test_checklist_reminders_nightly_sends_run_date_and_uuid4_key(self):
        self.enable_switches(engine_checklist_reminders=True)
        self.assertEqual(self.run_cli('checklist-reminders', '--check-nightly-switch'), (0, '', ''))
        self.engine.return_value = Reply(200, {'queued': 3, 'already_queued': 2})
        now = datetime(2026, 1, 9, 5, 30, tzinfo=timezone.utc)
        with patch.object(jobs, 'datetime', create=True) as clock:
            clock.now.side_effect = now.astimezone
            rc, output, errors = self.run_cli('checklist-reminders', '--nightly')
        self.assertEqual((rc, errors), (0, ''))
        self.assertEqual(json.loads(output), {'queued': 3, 'already_queued': 2})
        self.connect.assert_called_once_with(self.credentials, job='nightly_checklist')
        method, path, body = self.engine.call_args.args
        self.assertEqual((method, path), ('POST', '/jobs/deals/checklist-reminders'))
        request = json.loads(body)
        self.assertEqual(request.pop('run_date'), '2026-01-08')
        key = uuid.UUID(request['idempotency_key'])
        self.assertEqual(key.version, 4)
        self.assertEqual(request, {'idempotency_key': str(key)})

    def test_cli_help_starts_without_settings_or_network(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / 'tools/nightly-deal-jobs.py'), '--help'],
            text=True, capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('workflow-suggestions', result.stdout)
        self.assertIn('attention-alerts', result.stdout)
        self.assertIn('checklist-reminders', result.stdout)


if __name__ == '__main__':
    unittest.main()
