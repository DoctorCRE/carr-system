#!/usr/bin/env python3
"""Synthetic STEP-0 decisions and real command wiring, without model or store access."""
from datetime import datetime
from pathlib import Path
import sys
import os
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


class PredicateTests(unittest.TestCase):
    def test_monthly_window_uses_chicago_date(self):
        from lib.scheduled_step0 import stop_reason
        self.assertEqual(stop_reason('health-audit-monthly', datetime.fromisoformat('2026-10-11T05:01:00+00:00'), [], monthly_days=list(range(6, 11))), 'outside_monthly_window')
        self.assertIsNone(stop_reason('health-audit-monthly', datetime.fromisoformat('2026-10-11T04:59:00+00:00'), [], monthly_days=list(range(6, 11))))

    def test_weekly_completion_requires_verified_current_week_evidence(self):
        from lib.scheduled_step0 import stop_reason
        now = datetime.fromisoformat('2026-10-19T14:00:00+00:00')
        row = {'status': 'success', 'exit_code': 0, 'canonical_recorded': True,
               'end': '2026-10-19T13:00:00+00:00', 'window': '2026-10-19T12:00:00+00:00'}
        self.assertEqual(stop_reason('radar-weekly', now, [row]), 'weekly_completed')
        for change in ({'status': 'skipped'}, {'exit_code': 1}, {'canonical_recorded': False},
                       {'end': '2026-10-19T04:59:59+00:00'}, {'end': '2026-10-19T14:01:00+00:00'},
                       {'window': '2026-10-18T13:00:00+00:00'}):
            with self.subTest(change=change):
                self.assertIsNone(stop_reason('radar-weekly', now, [{**row, **change}]))

    def test_versions_stop_only_on_two_valid_identical_observations(self):
        from lib.scheduled_step0 import version_unchanged
        self.assertTrue(version_unchanged('cli=2.1.1 app=2.1.2', 'cli=2.1.1 app=2.1.2'))
        for current, last in [('cli=2.1.2 app=2.1.2', 'cli=2.1.1 app=2.1.2'),
                              ('cli=2.1.1 app=2.1.3', 'cli=2.1.1 app=2.1.2'),
                              ('cli=none app=none', 'cli=none app=none'),
                              ('', ''), ('garbage', 'garbage')]:
            with self.subTest(current=current, last=last):
                self.assertFalse(version_unchanged(current, last))

    def test_prompt_calls_share_the_step0_command(self):
        for task in ['health-audit-monthly', 'idea-resurface-monthly',
                     'system-sweep-monthly', 'playbook-review-monthly', 'cc-update-audit']:
            prompt = (REPO/'ops/scheduled-tasks'/f'{task}.SKILL.md').read_text()
            with self.subTest(task=task):
                self.assertIn(f'python3 bin/scheduled-step0.py {task}', prompt)
        audit = (REPO/'ops/scheduled-tasks/cc-update-audit.SKILL.md').read_text()
        self.assertNotIn('If `current` equals `last_audited`', audit)

class CommandTests(unittest.TestCase):
    def test_version_cli_stops_and_reports_changes_without_writing_sentinel(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'bin').mkdir()
            (root/'ops/headless-tasks').mkdir(parents=True)
            shutil.copyfile(REPO/'ops/headless-tasks/tasks.json', root/'ops/headless-tasks/tasks.json')
            (root/'lib').mkdir()
            for name in ['scheduled_step0.py', 'headless_tasks.py']:
                shutil.copyfile(REPO/'lib'/name, root/'lib'/name)
            shutil.copyfile(REPO/'bin/scheduled-step0.py', root/'bin/scheduled-step0.py')
            helper = root/'bin/cc-version-string.sh'
            helper.write_text('#!/bin/sh\n echo "cli=2.1.1 app=2.1.2"\n')
            helper.chmod(0o755)
            sentinel = root/'.claude/scheduled-tasks/cc-update-audit/last-audited-version.txt'
            sentinel.parent.mkdir(parents=True)
            sentinel.write_text('cli=2.1.1 app=2.1.2\n')
            def run():
                return subprocess.run([sys.executable, str(root/'bin/scheduled-step0.py'),
                                       'cc-update-audit'], env={**os.environ, 'HOME': str(root)},
                                      capture_output=True, text=True)
            result = run()
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn('version_unchanged', result.stdout)
            sentinel.write_text('cli=2.1.1 app=2.1.1\n')
            result = run()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('current: cli=2.1.1 app=2.1.2', result.stdout)
            self.assertEqual(sentinel.read_text(), 'cli=2.1.1 app=2.1.1\n')
            helper.unlink()
            self.assertEqual(run().returncode, 0)

    def test_monthly_cli_delegates_ledger_exit_and_weekly_cli_reads_receipts(self):
        import json
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for folder in ['bin', 'lib', 'ops/headless-tasks']:
                (root/folder).mkdir(parents=True)
            for name in ['scheduled_step0.py', 'headless_tasks.py']:
                shutil.copyfile(REPO/'lib'/name, root/'lib'/name)
            shutil.copyfile(REPO/'bin/scheduled-step0.py', root/'bin/scheduled-step0.py')
            (root/'ops/headless-tasks/tasks.json').write_text(json.dumps({'tasks': {
                'health-audit-monthly': {'cron': '0 9 * * *', 'monthly': True},
                'radar-weekly': {'cron': '0 9 * * 1'}}}))
            gate = root/'bin/monthly-gate.py'
            for code in [0, 1, 2]:
                gate.write_text(f'import sys; print(sys.argv[1]); raise SystemExit({code})')
                result = subprocess.run([sys.executable, str(root/'bin/scheduled-step0.py'),
                                         'health-audit-monthly'], capture_output=True, text=True)
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertIn('health-audit-monthly', result.stdout)
            folder = root/'out/headless/radar-weekly'
            folder.mkdir(parents=True)
            instant = datetime.now().astimezone().isoformat()
            (folder/'ledger.jsonl').write_text(json.dumps({'status': 'success', 'exit_code': 0,
                'canonical_recorded': True, 'end': instant, 'window': instant}))
            command = [sys.executable, str(root/'bin/scheduled-step0.py'), 'radar-weekly']
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn('weekly_completed', result.stdout)
            (folder/'ledger.jsonl').write_text('corrupt synthetic row')
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 2)


if __name__ == '__main__':
    unittest.main()
