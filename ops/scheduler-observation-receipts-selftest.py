#!/usr/bin/env python3
"""Synthetic device, scheduler, and source fixtures for observation receipts."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.repo = self.home / 'carr-system'
        (self.repo / 'ops/headless-tasks').mkdir(parents=True)
        (self.repo / 'ops/config').mkdir()
        (self.repo / 'ops/scheduled-tasks').mkdir()
        self.task = 'audit-task'
        self.label = 'com.carr.headless.audit-task'
        self.cron = '45 9 * * 1'
        self.plist = {'Label': self.label, 'Umask': 63,
                      'ProgramArguments': ['{{REPO}}/bin/headless-task', self.task],
                      'WorkingDirectory': '{{REPO}}',
                      'EnvironmentVariables': {'PATH': '{{HOME}}/.local/bin:/usr/bin:/bin'},
                      'StartCalendarInterval': [{'Minute': 45, 'Hour': 9, 'Weekday': 1}]}
        self.relpath = f'ops/headless-tasks/{self.label}.plist'
        (self.repo / self.relpath).write_bytes(plistlib.dumps(self.plist))
        self.installed = self.home / 'Library/LaunchAgents' / f'{self.label}.plist'
        self.installed.parent.mkdir(parents=True)
        self.installed.write_text(plistlib.dumps(self.plist).decode().replace('{{REPO}}', str(self.repo)).replace('{{HOME}}', str(self.home)))
        self.calls = []
        self.now = datetime(2026, 10, 18, 14, 0, tzinfo=timezone.utc)

    def runner(self, command):
        self.calls.append(command)
        if command[0] == '/bin/launchctl':
            output = f'{{ "{self.label}" => enabled }}' if command[1] == 'print-disabled' else 'state = waiting'
            return subprocess.CompletedProcess(command, 0, output, '')
        raise AssertionError(command)

    def read_launchd(self):
        from lib.launchd_scheduler_native import read_native_launchd
        return read_native_launchd(
            home=self.home, repo=self.repo, locator=self.label,
            repo_plist_relpath=self.relpath, installed_plist_name=self.installed.name,
            expected_program_arguments=self.plist['ProgramArguments'],
            plist_sha256=digest(self.plist),
            schedule_sha256=digest({'StartCalendarInterval': self.plist['StartCalendarInterval']}),
            expected_timezone='America/Chicago', host_timezone='America/Chicago',
            runner=self.runner, uid=501, observed_at=self.now)

    def test_headless_plist_with_home_tokens_can_be_observed(self):
        observed = self.read_launchd()
        self.assertTrue(observed['enabled'])
        self.assertEqual(observed['label'], self.label)
        self.assertEqual(observed['observed_at'], '2026-10-18T14:00:00Z')


if __name__ == '__main__':
    unittest.main()
