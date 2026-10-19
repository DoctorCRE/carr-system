#!/usr/bin/env python3
"""Exercise scheduler reconciliation through the installer and read-only CLI."""
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]


class SchedulerHygieneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.repo = self.home / 'carr-system'
        folder = self.repo / 'ops/headless-tasks'
        folder.mkdir(parents=True)
        (folder / 'tasks.json').write_text(json.dumps({
            'timezone': 'America/Chicago', 'tasks': {
                'test-task': {'cron': '45 9 * * 1', 'timeout_seconds': 5400}}}))
        (folder / 'com.carr.headless.test-task.plist').write_bytes(plistlib.dumps({
            'Label': 'com.carr.headless.test-task',
            'StartCalendarInterval': [{'Minute': 45, 'Hour': 9, 'Weekday': 1}]}))
        (self.repo / 'ops/config').mkdir()
        self.workflow = {'key': 'test-task', 'enabled': True,
                         'recurrence': {'cron': '45 9 * * 1', 'timezone': 'America/Chicago'},
                         'legacy_schedule': {'provider': 'claude-code', 'status': 'enabled'}}
        self.write_workflow()
        prompt = self.home / '.claude/scheduled-tasks/test-task/SKILL.md'
        prompt.parent.mkdir(parents=True)
        prompt.write_text('---\nname: test-task\n---\nSynthetic audit.\n')
        portable = self.repo / 'ops/scheduled-tasks/test-task.SKILL.md'
        portable.parent.mkdir(parents=True)
        portable.write_text(prompt.read_text())
        self.snapshot = self.home / 'Library/Application Support/Claude/claude-code-sessions/a/b/scheduled-tasks.json'
        self.snapshot.parent.mkdir(parents=True)
        self.row = {'id': 'test-task', 'enabled': False, 'cronExpression': '45 9 * * 1'}
        self.write_snapshot()
        fakebin = self.home / 'fakebin'
        fakebin.mkdir()
        launch = fakebin / 'launchctl'
        launch.write_text('#!/usr/bin/env python3\nimport os, pathlib, sys\n'
                         'with pathlib.Path(os.environ["LAUNCH_CALLS"]).open("a") as f: f.write(" ".join(sys.argv[1:])+"\\n")\n'
                         'sys.exit(113 if sys.argv[1] == "print" else 0)\n')
        launch.chmod(0o755)
        (fakebin / 'sitecustomize.py').write_text(
            f'import sys; sys.path.insert(0, {str(REPO)!r})\n'
            'import lib.claude_scheduler_native as native\n'
            'native.system_timezone = lambda: \"America/Chicago\"\n')
        self.calls = self.home / 'launch-calls'
        self.env = {**os.environ, 'HOME': str(self.home),
                    'PATH': str(fakebin) + os.pathsep + os.environ['PATH'],
                    'LAUNCH_CALLS': str(self.calls), 'PYTHONPATH': str(fakebin)}

    def write_workflow(self):
        (self.repo / 'ops/config/control-plane-workflows.v1.json').write_text(
            json.dumps({'workflows': [self.workflow]}))

    def write_snapshot(self, rows=None):
        self.snapshot.write_text(json.dumps({'scheduledTasks': rows if rows is not None else [self.row]}))

    def install(self, action='install'):
        return subprocess.run(['sh', str(REPO / 'ops/headless-tasks/install.sh'), action, 'test-task'],
                              env=self.env, text=True, capture_output=True, timeout=15)

    def report(self):
        return subprocess.run([sys.executable, str(REPO / 'ops/scheduler-hygiene.py'),
                               '--repo', str(self.repo), '--home', str(self.home)],
                              env=self.env, text=True, capture_output=True, timeout=15)

    def assert_refused_without_launchctl(self):
        result = self.install()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.calls.exists(), result.stdout + result.stderr)
        self.assertFalse((self.home / 'Library/LaunchAgents').exists())

    def test_enabled_native_task_refuses_headless_install(self):
        self.row['enabled'] = True
        self.write_snapshot()
        self.assert_refused_without_launchctl()

    def test_paused_workflow_cannot_be_reactivated_by_install(self):
        self.workflow['enabled'] = False
        self.write_workflow()
        self.assert_refused_without_launchctl()

    def test_unknown_native_state_refuses_install(self):
        self.snapshot.write_text('{broken')
        self.assert_refused_without_launchctl()

    def test_ambiguous_task_identity_refuses_install(self):
        self.write_snapshot([self.row, self.row])
        self.assert_refused_without_launchctl()

    def test_conflicting_enabled_keys_must_refuse_before_launchctl(self):
        self.snapshot.write_text('{"scheduledTasks":[{"id":"test-task","enabled":true,"enabled":false,"cronExpression":"45 9 * * 1"}]}')
        self.assert_refused_without_launchctl()

    def test_conflicting_task_lists_must_refuse_before_launchctl(self):
        self.snapshot.write_text('{"scheduledTasks":[{"id":"test-task","enabled":true,"cronExpression":"45 9 * * 1"}],"scheduledTasks":[]}')
        self.assert_refused_without_launchctl()

    def test_duplicate_keys_in_reviewed_inventories_refuse_install(self):
        paths = [self.repo / 'ops/headless-tasks/tasks.json',
                 self.repo / 'ops/config/control-plane-workflows.v1.json']
        for path in paths:
            original = path.read_text()
            with self.subTest(inventory=path.name):
                path.write_text(original.replace('"cron": "45 9 * * 1"',
                                                '"cron": "0 8 * * 1", "cron": "45 9 * * 1"'))
                self.assert_refused_without_launchctl()
            path.write_text(original)

    def test_native_cron_drift_refuses_install(self):
        self.row['cronExpression'] = '0 8 * * 1'
        self.write_snapshot()
        self.assert_refused_without_launchctl()

    def test_disabled_native_task_allows_explicit_cutover(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('bootstrap', self.calls.read_text())
        self.assertFalse(json.loads(self.snapshot.read_text())['scheduledTasks'][0]['enabled'])

    def test_deleted_native_task_allows_cutover_with_prompt_retained(self):
        self.write_snapshot([])
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_uninstall_does_not_depend_on_native_state(self):
        self.snapshot.unlink()
        result = self.install('uninstall')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_report_marks_duplicate_without_writes(self):
        self.row['enabled'] = True
        self.write_snapshot()
        agents = self.home / 'Library/LaunchAgents'
        agents.mkdir(parents=True)
        (agents / 'com.carr.headless.test-task.plist').write_bytes(
            (self.repo / 'ops/headless-tasks/com.carr.headless.test-task.plist').read_bytes())
        before = {p: p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
        result = self.report()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('duplicate', result.stdout)
        self.assertFalse(self.calls.exists())
        self.assertEqual(before, {p: p.read_bytes() for p in self.home.rglob('*') if p.is_file()})

    def test_report_only_explicit_completed_ephemeral_one_shots_for_deletion(self):
        ephemeral = self.home / '.claude/scheduled-tasks/one-shot/SKILL.md'
        ephemeral.parent.mkdir()
        ephemeral.write_text('---\nname: one-shot\ndescription: Synthetic continuation.\nephemeral: true\n---\nSynthetic continuation.\n')
        completed = {'id': 'one-shot', 'enabled': False, 'cronExpression': None,
                     'lastRunAt': 123, 'lastRunStatus': 'succeeded'}
        self.write_snapshot([self.row, completed, {**completed, 'id': 'personal-task'}])
        result = self.report()
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = json.loads(result.stdout)['tasks']
        self.assertEqual([r['task_id'] for r in rows if r['action'] == 'delete_completed_one_shot'], ['one-shot'])
        self.assertTrue(ephemeral.exists())
        self.assertFalse(self.calls.exists())

    def test_report_flags_missing_native_registration_for_retained_tasks(self):
        settings = self.repo / 'ops/headless-tasks/tasks.json'
        settings.write_text(json.dumps({'timezone': 'America/Chicago', 'tasks': {}}))
        self.write_snapshot([])
        result = self.report()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(json.loads(result.stdout)['tasks'][0]['action'], 'missing_native_registration')

    def test_report_preserves_paused_native_tasks(self):
        self.workflow['enabled'] = False
        self.write_workflow()
        result = self.report()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['tasks'][0]['action'], 'keep_paused')
        self.assertFalse(self.calls.exists())

    def test_report_keeps_failed_running_and_recurring_ephemeral_tasks(self):
        rows = [self.row]
        for task_id, values in {
                'failed': {'enabled': False, 'cronExpression': None, 'lastRunAt': 123, 'lastRunStatus': 'failed'},
                'running': {'enabled': True, 'cronExpression': None, 'lastRunAt': 123, 'lastRunStatus': 'succeeded'},
                'recurring': {'enabled': False, 'cronExpression': '0 9 * * *', 'lastRunAt': 123, 'lastRunStatus': 'succeeded'},
                'unverified': {'enabled': False, 'cronExpression': None, 'lastRunAt': 123}}.items():
            prompt = self.home / '.claude/scheduled-tasks' / task_id / 'SKILL.md'
            prompt.parent.mkdir()
            prompt.write_text('---\nephemeral: true\n---\nSynthetic task.\n')
            rows.append({'id': task_id, **values})
        self.write_snapshot(rows)
        result = self.report()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(r['action'] == 'delete_completed_one_shot' for r in json.loads(result.stdout)['tasks']))

    def test_one_shot_cleanup_requires_closed_top_level_marker(self):
        cases = ['---\ndescription: |\n  ephemeral: true\n---\n',
                 '---\nephemeral: true\n',
                 '---\nephemeral: true\nephemeral: false\n---\n']
        for text in cases:
            with self.subTest(frontmatter=text):
                prompt = self.home / '.claude/scheduled-tasks/temporary/SKILL.md'
                prompt.parent.mkdir(exist_ok=True)
                prompt.write_text(text)
                self.write_snapshot([self.row, {'id': 'temporary', 'enabled': False,
                    'cronExpression': None, 'lastRunAt': 123, 'lastRunStatus': 'succeeded'}])
                result = self.report()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(any(r['action'] == 'delete_completed_one_shot' for r in json.loads(result.stdout)['tasks']))

    def assert_no_cleanup_candidate(self, marker, include_cron):
        prompt = self.home / '.claude/scheduled-tasks/temporary/SKILL.md'
        prompt.parent.mkdir(exist_ok=True)
        prompt.write_text('---\n' + marker + '\n---\nSynthetic task.\n')
        row = {'id': 'temporary', 'enabled': False, 'lastRunAt': 123, 'lastRunStatus': 'succeeded'}
        if include_cron:
            row['cronExpression'] = None
        self.write_snapshot([self.row, row])
        result = self.report()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.calls.exists())
        self.assertNotIn('delete_completed_one_shot', result.stdout, result.stdout)
        self.assertTrue(prompt.exists())

    def test_conflicting_yaml_marker_must_not_nominate_cleanup(self):
        for marker in ['ephemeral: true\nephemeral : false',
                       'ephemeral: true\n"ephemeral": false',
                       "ephemeral: true\n'ephemeral': false",
                       'ephemeral: true\n"\\u0065phemeral": false',
                       'description: "\nephemeral: true\nname: continuation"',
                       'ephemeral: true\n<<: {ephemeral: false}']:
            with self.subTest(marker=marker):
                self.assert_no_cleanup_candidate(marker, True)

    def test_missing_recurrence_must_not_nominate_cleanup(self):
        self.assert_no_cleanup_candidate('ephemeral: true', False)

    def test_report_detects_leftover_headless_registration_after_manifest_removal(self):
        settings = self.repo / 'ops/headless-tasks/tasks.json'
        settings.write_text(json.dumps({'timezone': 'America/Chicago', 'tasks': {}}))
        self.row['enabled'] = True
        self.write_snapshot()
        agents = self.home / 'Library/LaunchAgents'
        agents.mkdir(parents=True)
        (agents / 'com.carr.headless.test-task.plist').touch()
        result = self.report()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(json.loads(result.stdout)['tasks'][0]['action'], 'duplicate_registration')

    def test_report_includes_orphan_headless_registration(self):
        agents = self.home / 'Library/LaunchAgents'
        agents.mkdir(parents=True)
        (agents / 'com.carr.headless.orphan.plist').touch()
        result = self.report()
        self.assertEqual(result.returncode, 1, result.stderr)
        orphan = next(r for r in json.loads(result.stdout)['tasks'] if r['task_id'] == 'orphan')
        self.assertEqual(orphan['action'], 'orphan_headless_registration')

    def test_remaining_registration_set_uses_reviewed_crons(self):
        tasks = json.loads((REPO / 'ops/headless-tasks/tasks.json').read_text())['tasks']
        for task_id, cron in {'cc-update-audit': '45 9 * * 1',
                              'deal-history-research-weekly': '0 10 * * 5',
                              'restore-rehearse-weekly': '40 7 * * 2',
                              'social-metrics-pull-weekly': '45 8 * * 3'}.items():
            with self.subTest(task=task_id):
                self.assertIn(task_id, tasks)
                self.assertEqual(tasks[task_id]['cron'], cron)
                plist = plistlib.loads((REPO / f'ops/headless-tasks/com.carr.headless.{task_id}.plist').read_bytes())
                self.assertEqual(plist['ProgramArguments'], ['{{REPO}}/bin/headless-task', task_id])
                self.assertNotIn('RunAtLoad', plist)
                self.assertNotIn('KeepAlive', plist)
        for excluded in ['ai-capability-builder', 'linkedin-engagement-daily', 'x-reply-run-daily', 'notes-sweep-hourly']:
            self.assertNotIn(excluded, tasks)


if __name__ == '__main__':
    unittest.main()
