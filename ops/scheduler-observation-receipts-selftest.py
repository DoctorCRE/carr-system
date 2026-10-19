#!/usr/bin/env python3
"""Synthetic device, scheduler, and source fixtures for observation receipts."""
from datetime import datetime, timezone
import hashlib
import contextlib
import io
from unittest.mock import patch
import json
from pathlib import Path
import plistlib
import subprocess
import shutil
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
        (self.repo / 'ops/headless-tasks/tasks.json').write_text(json.dumps({
            'timezone': 'America/Chicago', 'tasks': {self.task: {'cron': self.cron}}}))
        (self.repo / 'ops/config/control-plane-workflows.v1.json').write_text(json.dumps({
            'workflows': [{'key': self.task, 'version': 7, 'enabled': True,
                           'recurrence': {'cron': self.cron, 'timezone': 'America/Chicago'},
                           'legacy_schedule': {'provider': 'claude-code', 'status': 'enabled'}}]}))
        self.prompt = self.home / '.claude/scheduled-tasks' / self.task / 'SKILL.md'
        self.prompt.parent.mkdir(parents=True)
        self.prompt.write_text('---\nname: audit-task\n---\nSynthetic audit.\n')
        self.portable = self.repo / 'ops/scheduled-tasks/audit-task.SKILL.md'
        self.portable.write_bytes(self.prompt.read_bytes())
        self.snapshot = self.home / 'Library/Application Support/Claude/claude-code-sessions/a/b/scheduled-tasks.json'
        self.snapshot.parent.mkdir(parents=True)
        self.native_row = {'id': self.task, 'enabled': False, 'cronExpression': self.cron,
                           'filePath': str(self.prompt), 'cwd': str(self.repo)}
        self.snapshot.write_text(json.dumps({'scheduledTasks': [self.native_row]}))
        self.hardware_uuid = '00000000-0000-4000-8000-000000000001'
        self.revision = 'a' * 40
        self.calls = []
        self.now = datetime(2026, 10, 18, 14, 0, tzinfo=timezone.utc)

    def runner(self, command):
        self.calls.append(command)
        if command[0] == '/bin/launchctl':
            output = f'{{ "{self.label}" => enabled }}' if command[1] == 'print-disabled' else 'state = waiting'
            return subprocess.CompletedProcess(command, 0, output, '')
        if command[0] == '/usr/sbin/ioreg':
            return subprocess.CompletedProcess(command, 0,
                plistlib.dumps([{'IOPlatformUUID': self.hardware_uuid}]).decode(), '')
        if command[0] == 'git':
            return subprocess.CompletedProcess(command, 0,
                self.revision if 'rev-parse' in command else '', '')
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

    def collect(self, runner=None):
        from lib.scheduler_observation_receipts import collect_receipt
        return collect_receipt(self.task, repo=self.repo, home=self.home,
                               runner=runner or self.runner, host_timezone='America/Chicago',
                               uid=501, observed_at=self.now)

    def test_receipt_binds_device_workflow_and_both_scheduler_readbacks(self):
        receipt = self.collect()
        self.assertEqual(receipt['kind'], 'local_scheduler_observation_receipt')
        self.assertEqual(receipt['workflow']['key'], self.task)
        self.assertEqual(receipt['workflow']['version'], 7)
        self.assertEqual(receipt['workflow']['source_revision'], self.revision)
        self.assertEqual(receipt['device']['uid'], 501)
        self.assertEqual(len(receipt['device']['hardware_fingerprint']), 64)
        self.assertNotIn(self.hardware_uuid, json.dumps(receipt))
        self.assertFalse(receipt['native']['enabled'])
        self.assertTrue(receipt['headless']['enabled'])
        self.assertEqual(receipt['cutover_state'], 'headless_only')
        self.assertIsNone(receipt['canonical_receipt_ref'])
        self.assertTrue(receipt['receipt_ref'].startswith('local-scheduler-observation:sha256:'))
        self.assertTrue(all(c[0] in {'git', '/usr/sbin/ioreg', '/bin/launchctl'} for c in self.calls))
        self.assertTrue(all(c[1] in {'print', 'print-disabled'} for c in self.calls if c[0] == '/bin/launchctl'))

    def test_permission_error_cannot_be_reported_as_disabled(self):
        from lib.control_plane_scheduler_cutover import CutoverRefusal
        original = self.runner
        def denied(command):
            if command[:2] == ['/bin/launchctl', 'print']:
                return subprocess.CompletedProcess(command, 1, '', 'Operation not permitted')
            if command[:2] == ['/bin/launchctl', 'print-disabled']:
                return subprocess.CompletedProcess(command, 0, f'{{ "{self.label}" => disabled }}', '')
            return original(command)
        self.runner = denied
        with self.assertRaises(CutoverRefusal):
            self.read_launchd()

    def test_observer_clone_matches_the_installed_runtime_revision(self):
        text = 'Read {{REPO}}/ops/config/control-plane-workflows.v1.json.\n'
        self.portable.write_text(text)
        self.prompt.write_text(text.replace('{{REPO}}', str(self.repo)))
        observer = self.home / 'observer-clone'
        shutil.copytree(self.repo, observer)
        self.repo = observer
        receipt = self.collect()
        self.assertEqual(receipt['workflow']['source_revision'], self.revision)
        self.assertEqual(receipt['cutover_state'], 'headless_only')

    def test_unavailable_or_nil_hardware_identity_refuses_receipt(self):
        from lib.control_plane_scheduler_cutover import CutoverRefusal
        self.hardware_uuid = '00000000-0000-0000-0000-000000000000'
        with self.assertRaises(CutoverRefusal):
            self.collect()

    def test_paused_workflow_observation_reports_activation_violation(self):
        path = self.repo / 'ops/config/control-plane-workflows.v1.json'
        document = json.loads(path.read_text())
        document['workflows'][0]['enabled'] = False
        path.write_text(json.dumps(document))
        receipt = self.collect()
        self.assertEqual(receipt['cutover_state'], 'pause_violation')

    def test_native_deletion_is_absence_not_disabled_evidence(self):
        self.snapshot.write_text(json.dumps({'scheduledTasks': []}))
        receipt = self.collect()
        self.assertEqual(receipt['native']['state'], 'absent')
        self.assertIsNone(receipt['native']['enabled'])
        self.assertEqual(receipt['cutover_state'], 'headless_only')

    def test_duplicate_registrations_are_measured_without_changing_them(self):
        self.native_row['enabled'] = True
        self.snapshot.write_text(json.dumps({'scheduledTasks': [self.native_row]}))
        before = self.snapshot.read_bytes(), self.installed.read_bytes(), self.prompt.read_bytes()
        self.assertEqual(self.collect()['cutover_state'], 'duplicate')
        self.assertEqual(before, (self.snapshot.read_bytes(), self.installed.read_bytes(), self.prompt.read_bytes()))

    def test_local_receipt_cannot_claim_canonical_device_principal_authority(self):
        from lib.control_plane_scheduler_cutover import CutoverRefusal, observe_claude_scheduler_receipt
        with self.assertRaises(CutoverRefusal):
            observe_claude_scheduler_receipt({'scheduler_kind': 'claude-code'}, self.collect())

    def test_same_evidence_has_same_receipt_but_changed_hardware_does_not(self):
        first = self.collect()
        self.assertEqual(first, self.collect())
        self.hardware_uuid = '00000000-0000-4000-8000-000000000002'
        changed = self.collect()
        self.assertNotEqual(changed['device']['hardware_fingerprint'], first['device']['hardware_fingerprint'])
        self.assertNotEqual(changed['receipt_ref'], first['receipt_ref'])

    def test_source_revision_mismatch_or_dirty_checkout_refuses_receipt(self):
        from lib.control_plane_scheduler_cutover import CutoverRefusal
        observer = self.home / 'observer-clone'
        shutil.copytree(self.repo, observer)
        self.repo = observer
        for mode in ['mismatch', 'dirty', 'changes_during_read']:
            with self.subTest(mode=mode):
                count = 0
                def changed(command):
                    nonlocal count
                    result = self.runner(command)
                    if command[0] == 'git':
                        if mode == 'mismatch' and command[2] == str(self.home / 'carr-system') and 'rev-parse' in command:
                            return subprocess.CompletedProcess(command, 0, 'b' * 40, '')
                        if mode == 'dirty' and 'status' in command:
                            return subprocess.CompletedProcess(command, 0, ' M lib/headless_tasks.py', '')
                        if mode == 'changes_during_read' and 'rev-parse' in command:
                            count += 1
                            if count > 2:
                                return subprocess.CompletedProcess(command, 0, 'c' * 40, '')
                    return result
                with self.assertRaises(CutoverRefusal):
                    self.collect(changed)

    def test_source_change_while_reading_workflow_refuses_receipt(self):
        from lib.control_plane_scheduler_cutover import CutoverRefusal
        workflow_path = self.repo / 'ops/config/control-plane-workflows.v1.json'
        read_text = Path.read_text
        def advance_source(path, *args, **kwargs):
            text = read_text(path, *args, **kwargs)
            if path == workflow_path:
                document = json.loads(text)
                document['workflows'][0].update(version=8, enabled=False)
                path.write_text(json.dumps(document))
                self.revision = 'b' * 40
            return text
        with patch.object(Path, 'read_text', advance_source):
            with self.assertRaisesRegex(CutoverRefusal, 'source revision changed'):
                self.collect()

    def test_unavailable_hardware_or_launchctl_refuses_receipt(self):
        from lib.control_plane_scheduler_cutover import CutoverRefusal
        for executable in ['/usr/sbin/ioreg', '/bin/launchctl']:
            with self.subTest(executable=executable):
                def unavailable(command):
                    if command[0] == executable:
                        return subprocess.CompletedProcess(command, 1, '', 'synthetic unavailable')
                    return self.runner(command)
                with self.assertRaises(CutoverRefusal):
                    self.collect(unavailable)

    def test_native_change_during_launchd_read_refuses_mixed_receipt(self):
        from lib.control_plane_scheduler_cutover import CutoverRefusal
        def changes(command):
            if command[:2] == ['/bin/launchctl', 'print']:
                self.snapshot.write_text(json.dumps({'scheduledTasks': []}))
            return self.runner(command)
        with self.assertRaises(CutoverRefusal):
            self.collect(changes)

    def test_prompt_or_installed_plist_drift_refuses_receipt(self):
        from lib.control_plane_scheduler_cutover import CutoverRefusal
        for path in [self.prompt, self.installed]:
            with self.subTest(path=path.name):
                before = path.read_bytes()
                path.write_text('synthetic drift')
                with self.assertRaises((CutoverRefusal, plistlib.InvalidFileException)):
                    self.collect()
                path.write_bytes(before)

    def test_unknown_task_refuses_before_device_reads(self):
        from lib.control_plane_scheduler_cutover import CutoverRefusal
        self.task = 'unknown-task'
        with self.assertRaises(CutoverRefusal):
            self.collect()
        self.assertEqual(self.calls, [])

    def test_cli_prints_the_measured_receipt_and_sanitizes_failure(self):
        import lib.scheduler_observation_receipts as receipts
        measured = self.collect()
        output = io.StringIO()
        with patch.object(receipts, 'collect_receipt', return_value=measured), contextlib.redirect_stdout(output):
            self.assertEqual(receipts.main([self.task]), 0)
        self.assertEqual(json.loads(output.getvalue()), measured)
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(receipts, 'collect_receipt', side_effect=OSError('synthetic private diagnostic')), contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            self.assertEqual(receipts.main([self.task]), 2)
        self.assertEqual(output.getvalue(), '')
        self.assertNotIn('synthetic private diagnostic', errors.getvalue())

    def test_unrendered_installed_plist_is_not_installation_evidence(self):
        from lib.control_plane_scheduler_cutover import CutoverRefusal
        self.installed.write_bytes(plistlib.dumps(self.plist))
        with self.assertRaises(CutoverRefusal):
            self.collect()

    def test_retained_prompt_symlink_is_not_definition_evidence(self):
        from lib.control_plane_scheduler_cutover import CutoverRefusal
        self.prompt.unlink()
        self.prompt.symlink_to(self.portable)
        with self.assertRaises(CutoverRefusal):
            self.collect()

    def test_deleted_native_row_cannot_guess_vault_expansion(self):
        from lib.control_plane_scheduler_cutover import CutoverRefusal
        self.portable.write_text('Read {{VAULT}}/synthetic-instructions.\n')
        self.prompt.write_text('Read /synthetic/vault/synthetic-instructions.\n')
        self.native_row['cwd'] = '/synthetic/vault'
        self.snapshot.write_text(json.dumps({'scheduledTasks': [self.native_row]}))
        self.assertEqual(self.collect()['cutover_state'], 'headless_only')
        self.snapshot.write_text(json.dumps({'scheduledTasks': []}))
        with self.assertRaisesRegex(CutoverRefusal, 'vault.*disabled native row'):
            self.collect()

    def test_unrendered_retained_prompt_is_not_definition_evidence(self):
        from lib.control_plane_scheduler_cutover import CutoverRefusal
        self.portable.write_text('Read {{REPO}}/ops/config/control-plane-workflows.v1.json.\n')
        self.prompt.write_bytes(self.portable.read_bytes())
        for rows in [[self.native_row], []]:
            with self.subTest(native_row=bool(rows)):
                self.snapshot.write_text(json.dumps({'scheduledTasks': rows}))
                with self.assertRaises(CutoverRefusal):
                    self.collect()

    def test_disabled_headless_job_preserves_native_only_and_inactive_states(self):
        def disabled(command):
            if command[:2] == ['/bin/launchctl', 'print']:
                return subprocess.CompletedProcess(command, 113, '', 'Could not find service')
            if command[:2] == ['/bin/launchctl', 'print-disabled']:
                return subprocess.CompletedProcess(command, 0, f'{{ "{self.label}" => disabled }}', '')
            return self.runner(command)
        for enabled, expected in [(True, 'native_only'), (False, 'inactive')]:
            with self.subTest(native_enabled=enabled):
                self.native_row['enabled'] = enabled
                self.snapshot.write_text(json.dumps({'scheduledTasks': [self.native_row]}))
                receipt = self.collect(disabled)
                self.assertEqual(receipt['cutover_state'], expected)
                self.assertFalse(receipt['headless']['enabled'])

    def test_duplicate_manifest_keys_cannot_bind_an_ambiguous_version(self):
        from lib.control_plane_scheduler_cutover import CutoverRefusal
        path = self.repo / 'ops/headless-tasks/tasks.json'
        tasks = json.dumps(json.loads(path.read_text())['tasks'])
        path.write_text('{"timezone":"America/Chicago","tasks":' + tasks + ',"tasks":' + tasks + '}')
        with self.assertRaises(CutoverRefusal):
            self.collect()

    def test_headless_plist_with_home_tokens_can_be_observed(self):
        observed = self.read_launchd()
        self.assertTrue(observed['enabled'])
        self.assertEqual(observed['label'], self.label)
        self.assertEqual(observed['observed_at'], '2026-10-18T14:00:00Z')


if __name__ == '__main__':
    unittest.main()
