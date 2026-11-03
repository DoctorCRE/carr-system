"""Synthetic transition coverage for the drained Outlook runtime refresh."""
import fcntl
import json
import plistlib
import subprocess
import tempfile
from types import SimpleNamespace
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.outlook_runtime_refresh import refresh, Host, LABELS
from lib import scheduled_jobs as jobs


class RefreshTests(unittest.TestCase):
    def adapter(self):
        adapter = Mock()
        adapter.current.return_value = False
        adapter.drained.side_effect = [False, True]
        adapter.clock.side_effect = range(100)
        adapter.recovery_pending.return_value = False
        return adapter

    def test_success_waits_then_swaps_verifies_restores(self):
        a = self.adapter()
        self.assertEqual(refresh(a, apply=True, timeout=10), 'refreshed')
        names = [c[0] for c in a.method_calls]
        self.assertLess(names.index('quiesce'), names.index('drained'))
        self.assertLess(names.index('sleep'), names.index('swap'))
        self.assertLess(names.index('swap'), names.index('verify'))
        self.assertLess(names.index('verify'), names.index('restore'))
        a.restore.assert_called_once_with(rollback=False)

    def test_send_lock_timeout_never_swaps_or_removes(self):
        a = self.adapter()
        a.drained.side_effect = None
        a.drained.return_value = False
        with self.assertRaises(TimeoutError):
            refresh(a, apply=True, timeout=2)
        a.swap.assert_not_called()
        a.restore.assert_called_once_with(rollback=True)

    def test_verify_failure_restores(self):
        a = self.adapter()
        a.verify.side_effect = ValueError('wrong SHA')
        with self.assertRaises(ValueError):
            refresh(a, apply=True, timeout=10)
        a.restore.assert_called_once_with(rollback=True)

    def test_swap_failure_restores(self):
        a = self.adapter()
        a.swap.side_effect = ValueError('installer failed')
        with self.assertRaises(ValueError):
            refresh(a, apply=True, timeout=10)
        a.restore.assert_called_once_with(rollback=True)

    def test_quiesce_failure_restores(self):
        a = self.adapter()
        a.quiesce.side_effect = ValueError('disable failed')
        with self.assertRaises(ValueError):
            refresh(a, apply=True, timeout=10)
        a.restore.assert_called_once_with(rollback=True)

    def test_already_current_no_mutations(self):
        a = self.adapter()
        a.current.return_value = True
        self.assertEqual(refresh(a, apply=True), 'already current')
        self.assertEqual([c[0] for c in a.method_calls], ['recovery_pending', 'validate', 'current'])

    def test_dry_run_no_mutations(self):
        a = self.adapter()
        self.assertEqual(refresh(a), 'dry run')
        self.assertEqual([c[0] for c in a.method_calls], ['recovery_pending', 'validate', 'current', 'plan'])

    def test_recovery_precedes_current_check(self):
        a = self.adapter()
        a.recovery_pending.return_value = True
        a.current.return_value = True
        refresh(a, apply=True)
        self.assertEqual(a.method_calls[1][0], 'recover')


class HostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = self.root / 'runtime'
        self.runtime.mkdir()
        self.settings = SimpleNamespace(repo=self.runtime, send_state=self.root / 'send',
                                        vendor=self.root / 'vendor', copy_helper=self.root / 'helper')
        self.host = Host(self.root / 'main', 'a' * 40, self.settings)
        self.host.previous = 'b' * 40
        self.host.command = Mock(return_value=subprocess.CompletedProcess([], 0, '', ''))
        self.host.plist_path = lambda label: self.root / (label + '.plist')
        self.addCleanup(self.host.locks.close)

    def test_real_send_lock_waits_and_timeout_does_not_remove_or_swap(self):
        self.settings.send_state.mkdir()
        with (self.settings.send_state / 'lock').open('a') as sender:
            fcntl.flock(sender, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertFalse(self.host.drained())
            self.host.validate = Mock()
            self.host.clock = Mock(side_effect=[0, 2])
            with self.assertRaises(TimeoutError):
                refresh(self.host, apply=True, timeout=1)
            self.assertTrue((self.settings.send_state / 'lock').exists())
            mutations = [c.args for c in self.host.command.call_args_list]
            self.assertFalse(any('bootout' in c or '/bin/zsh' in c for c in mutations))
            probe = (self.settings.send_state / 'lock').open('a')
            with probe, self.assertRaises(BlockingIOError):
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.assertTrue(self.host.drained())
        self.host.locks.close()

    def test_outbox_lock_release_and_active_pid_drain(self):
        directory = self.settings.send_state / 'outbox'
        directory.mkdir(parents=True)
        with (directory / 'lock').open('a') as sender:
            fcntl.flock(sender, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertFalse(self.host.drained())
        self.host.command.return_value.stdout = '123 0 com.carr.outlook-outbox'
        self.assertFalse(self.host.drained())
        self.host.command.return_value.stdout = '- 0 com.carr.outlook-outbox'
        self.assertTrue(self.host.drained())
        self.host.locks.close()

    def test_snapshot_restores_artifacts_and_mixed_registration(self):
        self.settings.vendor.mkdir()
        binary = self.settings.vendor / 'decoder'
        binary.write_text('old decoder')
        self.settings.copy_helper.write_text('old helper')
        for label in LABELS:
            self.host.plist_path(label).write_text('old plist')
        def command(*args, **kwargs):
            out = '- 0 ' + LABELS[0] if args == ('launchctl', 'list') else ''
            if 'print-disabled' in args:
                out = '"' + LABELS[1] + '" => true'
            return subprocess.CompletedProcess(args, 0, out, '')
        self.host.command = Mock(side_effect=command)
        self.host.snapshot()
        state = json.loads((self.host.journal / 'state.json').read_text())
        state['swapped'] = True
        self.host.write_state(state)
        binary.write_text('new decoder')
        self.settings.copy_helper.write_text('new helper')
        self.host.git = Mock()
        self.host.restore(rollback=True)
        self.assertEqual(binary.read_text(), 'old decoder')
        self.assertEqual(self.settings.copy_helper.read_text(), 'old helper')
        self.assertFalse(self.host.recovery_pending())
        calls = [c.args for c in self.host.command.call_args_list]
        self.assertIn(('launchctl', 'disable', self.host.domain + '/' + LABELS[1]), calls)
        self.assertFalse(any('bootstrap' in c and str(self.host.plist_path(LABELS[1])) in c for c in calls))

    def test_complete_host_transaction_and_verify_rollback(self):
        for invalid in (False, True):
            with self.subTest(invalid=invalid):
                self.settings.vendor.mkdir(exist_ok=True)
                decoder = self.settings.vendor / 'decoder'
                decoder.write_text('old')
                self.settings.decoder = decoder
                self.settings.services = ('intake', 'triage', 'outbox')
                self.settings.verify_helper = Mock()
                templates = self.runtime / 'ops/launchd'
                templates.mkdir(parents=True, exist_ok=True)
                for label in LABELS:
                    (templates / (label + '.plist')).write_bytes(plistlib.dumps({'Label': label}))
                    self.host.plist_path(label).write_text('old plist')
                self.settings.render_plist = lambda path: path.read_bytes()
                registered = {LABELS[0]}
                sha = ['b' * 40]
                calls = []
                def command(*args, **kwargs):
                    calls.append(args)
                    out = ''
                    if args[0] == 'git':
                        tail = args[3:]
                        repo = args[2]
                        if tail == ('remote', 'get-url', 'origin'):
                            out = 'https://github.com/DoctorCRE/carr-system.git'
                        elif tail == ('symbolic-ref', '--short', 'HEAD'):
                            out = 'main'
                        elif tail[0] == 'ls-remote':
                            out = 'a' * 40 + ' refs/heads/main'
                        elif tail == ('rev-parse', 'HEAD'):
                            out = sha[0] if repo == self.runtime else 'a' * 40
                        elif tail[0] == 'checkout':
                            sha[0] = tail[-1]
                    elif args[:2] == ('launchctl', 'list'):
                        out = '\n'.join('- 0 ' + label for label in registered)
                    elif args[:2] == ('launchctl', 'bootout'):
                        self.assertTrue(self.host.locked)
                        registered.discard(args[2].split('/')[-1])
                    elif args[:2] == ('launchctl', 'bootstrap'):
                        registered.add(Path(args[3]).stem)
                    elif args[0] == '/bin/zsh' and 'install-outlook-intake.sh' in str(args[1]):
                        self.assertFalse(registered)
                        self.assertTrue(self.host.locked)
                        sha[0] = 'c' * 40 if invalid else 'a' * 40
                        decoder.write_text('new')
                    return subprocess.CompletedProcess(args, 0, out, '')
                self.host.command = command
                self.assertEqual(refresh(self.host), 'dry run')
                self.assertFalse(self.host.journal.exists())
                self.assertFalse(any(c[0] == '/bin/zsh' or c[:2] in (
                    ('launchctl', 'disable'), ('launchctl', 'bootout'), ('launchctl', 'bootstrap')) for c in calls))
                calls.clear()
                if invalid:
                    with self.assertRaises(ValueError):
                        refresh(self.host, apply=True)
                    self.assertEqual(sha[0], 'b' * 40)
                    self.assertEqual(decoder.read_text(), 'old')
                    self.assertEqual(self.host.plist_path(LABELS[0]).read_text(), 'old plist')
                else:
                    self.assertEqual(refresh(self.host, apply=True), 'refreshed')
                    self.assertEqual(sha[0], 'a' * 40)
                    self.assertEqual(decoder.read_text(), 'new')
                    calls.clear()
                    self.assertEqual(refresh(self.host, apply=True), 'already current')
                    self.assertFalse(any(c[0] == '/bin/zsh' or c[:2] in (
                        ('launchctl', 'disable'), ('launchctl', 'bootout'), ('launchctl', 'bootstrap')) for c in calls))
                self.assertEqual(registered, {LABELS[0]})
                self.assertFalse(self.host.recovery_pending())

    def test_restore_failure_keeps_journal(self):
        self.host.snapshot()
        self.host.command.side_effect = OSError('launchd unavailable')
        with self.assertRaises(OSError):
            self.host.restore(rollback=True)
        self.assertTrue(self.host.recovery_pending())

    def test_verify_rejects_wrong_sha_and_checks_ancestry(self):
        self.host.git = Mock(return_value='b' * 40)
        with self.assertRaises(ValueError):
            self.host.verify()
        self.host.git = Mock(return_value='a' * 40)
        self.settings.verify_helper = Mock()
        self.host.verify()
        self.assertIn(unittest.mock.call(self.host.source, 'merge-base', '--is-ancestor', 'a' * 40, 'origin/main'),
                      self.host.git.call_args_list)


class HealthTests(unittest.TestCase):
    def test_pinned_settings_render_and_freshness(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'ops/launchd'
            target.mkdir(parents=True)
            settings = SimpleNamespace(repo=root / 'pinned')
            def render(path):
                return path.read_bytes().replace(b'{{REPO}}', str(settings.repo).encode()).replace(
                    b'{{COPY_HELPER}}', str(root / 'helper').encode()).replace(b'{{EXECUTABLE_PATH}}', b'/usr/bin')
            settings.render_plist = render
            for label in LABELS:
                (target / (label + '.plist')).write_bytes(plistlib.dumps({
                    'Label': label, 'ProgramArguments': ['{{COPY_HELPER}}', '--run'],
                    'WorkingDirectory': '{{REPO}}', 'RunAtLoad': True,
                    'StandardOutPath': '{{REPO}}/out/log', 'StandardErrorPath': '{{REPO}}/out/log'}))
            with patch.object(jobs.launchd_hold, 'read_holds', return_value={}):
                manifest = jobs.native_manifest(root, outlook_settings=settings)
            snapshot = {'launchctl_list': '\n'.join('- 0 ' + label for label in LABELS),
                        'launchctl_disabled': '', 'cron': '', 'machine_role': 'primary',
                        'plists': {label: plistlib.loads(render(target / (label + '.plist'))) for label in LABELS},
                        'launchctl_print': {}, 'log_mtimes': {}, 'git': {'branch': 'main'},
                        'runtime_freshness': {label: {'commits_behind_main': 77, 'main_ancestor': True} for label in LABELS}}
            rows = jobs.report(manifest, snapshot, 0)
            self.assertFalse({r['code'] for r in rows} & {'wrong_checkout', 'program_drift', 'log_path_drift'})
            self.assertEqual(len([r for r in rows if r.get('freshness', {}).get('commits_behind_main') == 77]), 3)
            snapshot['plists'][LABELS[0]]['WorkingDirectory'] = str(root / 'outside')
            self.assertIn('wrong_checkout', {r['code'] for r in jobs.report(manifest, snapshot, 0)})
            snapshot['plists'][LABELS[0]]['WorkingDirectory'] = str(settings.repo)
            snapshot['launchctl_print'][LABELS[0]] = 'working directory = ' + str(root / 'outside')
            self.assertIn('wrong_checkout', {r['code'] for r in jobs.report(manifest, snapshot, 0)})


if __name__ == '__main__':
    unittest.main()
