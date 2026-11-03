"""Synthetic transition coverage for the drained Outlook runtime refresh."""
import fcntl
import json
import os
import shutil
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

    def test_nonfinite_or_nonpositive_timeout_is_rejected_before_effects(self):
        for timeout in (float('inf'), float('nan'), 0, -1):
            a = self.adapter()
            with self.assertRaises(ValueError):
                refresh(a, apply=True, timeout=timeout)
            self.assertEqual(a.method_calls, [])

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
                            out = 'git' + '@' + 'github.com:DoctorCRE/carr-system.git' if invalid else 'https://github.com/DoctorCRE/carr-system.git'
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

    def failed_symlink_install(self):
        target = self.root / 'vendor-target'
        target.mkdir()
        self.settings.vendor.symlink_to(target, target_is_directory=True)
        decoder = target / 'decoder'
        decoder.write_text('old')
        self.settings.copy_helper.write_text('old helper')
        registered = {LABELS[0], LABELS[1]}
        overrides = {LABELS[1]}
        sha = [self.host.previous]
        fail_bootstrap = [False]
        for label in LABELS:
            self.host.plist_path(label).write_text('old plist')
        def command(*args, **kwargs):
            out = ''
            if args[:2] == ('launchctl', 'list'):
                out = '\n'.join('- 0 ' + label for label in registered)
            elif args[:2] == ('launchctl', 'print-disabled'):
                out = '\n'.join('"' + label + '" => true' for label in overrides)
            elif args[:2] == ('launchctl', 'disable'):
                overrides.add(args[2].split('/')[-1])
            elif args[:2] == ('launchctl', 'enable'):
                overrides.discard(args[2].split('/')[-1])
            elif args[:2] == ('launchctl', 'bootout'):
                self.assertTrue(self.host.locked)
                registered.discard(args[2].split('/')[-1])
            elif args[:2] == ('launchctl', 'bootstrap'):
                self.assertTrue(self.host.locked)
                if fail_bootstrap[0]:
                    raise OSError('synthetic bootstrap failure')
                registered.add(Path(args[3]).stem)
            elif args[0] == 'git' and args[3] == 'checkout':
                sha[0] = args[-1]
            elif args[0] == '/bin/zsh':
                sha[0] = self.host.sha
                decoder.write_text('new')
                self.settings.copy_helper.write_text('new helper')
                raise ValueError('synthetic installer failure')
            return subprocess.CompletedProcess(args, 0, out, '')
        self.host.command = command
        self.host.validate = Mock()
        return target, decoder, registered, overrides, sha, fail_bootstrap

    def assert_symlink_restored(self, fixture):
        target, decoder, registered, overrides, sha, _ = fixture
        self.assertTrue(self.settings.vendor.is_symlink())
        self.assertEqual(self.settings.vendor.resolve(), target.resolve())
        self.assertEqual(decoder.read_text(), 'old')
        self.assertEqual(self.settings.copy_helper.read_text(), 'old helper')
        self.assertEqual(sha[0], self.host.previous)
        self.assertEqual(registered, {LABELS[0], LABELS[1]})
        self.assertEqual(overrides, {LABELS[1]})
        self.assertFalse(self.host.recovery_pending())
        self.assertFalse(self.host.locked)

    def test_symlink_vendor_installer_failure_rolls_back(self):
        fixture = self.failed_symlink_install()
        with self.assertRaisesRegex(ValueError, 'synthetic installer failure'):
            refresh(self.host, apply=True)
        self.assert_symlink_restored(fixture)

    def test_symlink_crash_journal_recovers_with_real_host(self):
        fixture = self.failed_symlink_install()
        self.host.snapshot()
        state = json.loads((self.host.journal / 'state.json').read_text())
        state['artifacts'][0]['path'] = str(self.settings.vendor)
        self.host.write_state(state)
        self.host.quiesce()
        self.assertTrue(self.host.drained())
        with self.assertRaisesRegex(ValueError, 'synthetic installer failure'):
            self.host.swap()
        self.host.locks.close()
        self.host.locked = False
        self.host.recover()
        self.assert_symlink_restored(fixture)

    def test_interrupted_symlink_restoration_retries_from_journal(self):
        fixture = self.failed_symlink_install()
        fixture[-1][0] = True
        with self.assertRaises(OSError):
            refresh(self.host, apply=True)
        self.assertTrue(self.host.recovery_pending())
        self.assertFalse(self.host.locked)
        fixture[-1][0] = False
        self.host.recover()
        self.assert_symlink_restored(fixture)
        self.host.previous = self.host.sha
        self.assertEqual(refresh(self.host, apply=True), 'already current')

    def test_path_type_failure_matrix(self):
        for kind in ('file', 'directory', 'symlink', 'dangling', 'absent'):
            for failure in ('intake', 'triage', 'verify', 'restore'):
                for crash in (False, True):
                    with self.subTest(kind=kind, failure=failure, crash=crash):
                        self.path_type_failure(kind, failure, crash)

    def path_type_failure(self, kind, failure, crash):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / 'runtime'
            runtime.mkdir()
            (runtime / 'revision').write_text('old')
            runtime_link = root / 'runtime-link'
            runtime_link.symlink_to('runtime', target_is_directory=True)
            settings = SimpleNamespace(repo=runtime_link, vendor=root / 'vendor',
                                       copy_helper=root / 'helper', send_state=root / 'send',
                                       render_plist=lambda path: b'new plist', verify_helper=lambda: None)
            host = Host(root / 'main', 'a' * 40, settings)
            host.previous = 'b' * 40
            host.validate = Mock()
            host.plan = Mock()
            host.plist_path = lambda label: root / (label + '.plist')
            paths = [settings.vendor, settings.copy_helper, settings.copy_helper.with_suffix('.pin')]
            paths += [host.plist_path(label) for label in LABELS]
            targets = []
            for i, path in enumerate(paths):
                target = root / ('target-' + str(i))
                if kind in ('file', 'symlink'):
                    target.write_text('old')
                    target.chmod(0o700)
                elif kind == 'directory':
                    target.mkdir()
                    (target / 'payload').write_text('old')
                    (target / 'relative').symlink_to('payload')
                if kind in ('symlink', 'dangling'):
                    middle = root / ('middle-' + str(i))
                    middle.symlink_to(target.name)
                    path.symlink_to(middle.name)
                elif kind != 'absent':
                    target.rename(path)
                targets.append(target)
            registered = {LABELS[0], LABELS[1]}
            overrides = {LABELS[1]}
            sha = [host.previous]
            fired = [False]
            def remove(path):
                if path.is_symlink() or path.is_file():
                    path.unlink()
                elif path.exists():
                    shutil.rmtree(path)
            def fail(step):
                if failure == step and not fired[0]:
                    fired[0] = True
                    raise OSError('injected ' + step)
            def command(*args, **kwargs):
                out = ''
                if args[:2] == ('launchctl', 'list'):
                    out = '\n'.join('- 0 ' + label for label in registered)
                elif args[:2] == ('launchctl', 'print-disabled'):
                    out = '\n'.join('"' + label + '" => true' for label in overrides)
                elif args[:2] == ('launchctl', 'disable'):
                    overrides.add(args[2].split('/')[-1])
                elif args[:2] == ('launchctl', 'enable'):
                    overrides.discard(args[2].split('/')[-1])
                elif args[:2] == ('launchctl', 'bootout'):
                    registered.discard(args[2].split('/')[-1])
                elif args[:2] == ('launchctl', 'bootstrap'):
                    fail('restore')
                    registered.add(Path(args[3]).stem)
                elif args[0] == 'git':
                    if args[3] == 'checkout':
                        sha[0] = args[-1]
                        (Path(args[2]) / 'revision').write_text('old')
                    elif args[3] == 'rev-parse':
                        out = sha[0]
                    else:
                        fail('verify')
                elif args[0] == '/bin/zsh':
                    if 'intake' in str(args[1]):
                        sha[0] = host.sha
                        (runtime / 'revision').write_text('new')
                        for path, target in zip(paths, targets):
                            if kind in ('symlink', 'dangling'):
                                target.write_text('new through link')
                            remove(path)
                            built = root / 'built'
                            built.write_text('new atomic replacement')
                            os.replace(built, path)
                        fail('intake')
                    else:
                        fail('triage')
                return subprocess.CompletedProcess(args, 0, out, '')
            host.command = command
            try:
                if crash:
                    host.snapshot()
                    host.quiesce()
                    self.assertTrue(host.drained())
                    host.swap()
                    host.verify()
                    host.restore(rollback=False)
                else:
                    refresh(host, apply=True)
            except OSError:
                pass
            finally:
                host.locks.close()
                host.locked = False
            if host.recovery_pending():
                host.recover()
            self.assertTrue(fired[0])
            self.assertTrue(runtime_link.is_symlink())
            self.assertEqual(os.readlink(runtime_link), 'runtime')
            self.assertEqual((runtime / 'revision').read_text(), 'old')
            self.assertEqual(sha[0], host.previous)
            for i, (path, target) in enumerate(zip(paths, targets)):
                if kind in ('symlink', 'dangling'):
                    self.assertTrue(path.is_symlink())
                    self.assertEqual(os.readlink(path), 'middle-' + str(i))
                    self.assertEqual(os.readlink(root / ('middle-' + str(i))), target.name)
                    self.assertEqual(target.exists(), kind == 'symlink')
                elif kind == 'absent':
                    self.assertFalse(path.exists())
                    continue
                if kind == 'directory':
                    self.assertTrue(path.is_dir())
                    self.assertEqual((path / 'payload').read_text(), 'old')
                    self.assertEqual(os.readlink(path / 'relative'), 'payload')
                elif kind != 'dangling':
                    self.assertEqual(path.read_text(), 'old')
                    self.assertEqual(path.stat().st_mode & 0o777, 0o700)
            self.assertEqual(registered, {LABELS[0], LABELS[1]})
            self.assertEqual(overrides, {LABELS[1]})
            self.assertFalse(host.recovery_pending())
            self.assertFalse(host.locked)

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
            pinned = settings.repo / 'ops/launchd'
            pinned.mkdir(parents=True)
            def render(path):
                return path.read_bytes().replace(b'{{REPO}}', str(settings.repo).encode()).replace(
                    b'{{COPY_HELPER}}', str(root / 'helper').encode()).replace(b'{{EXECUTABLE_PATH}}', b'/usr/bin')
            settings.render_plist = render
            for label in LABELS:
                (target / (label + '.plist')).write_bytes(plistlib.dumps({
                    'Label': label, 'ProgramArguments': ['{{COPY_HELPER}}', '--run'],
                    'WorkingDirectory': '{{REPO}}', 'RunAtLoad': True,
                    'StandardOutPath': '{{REPO}}/out/log', 'StandardErrorPath': '{{REPO}}/out/log'}))
            for path in target.glob('*.plist'):
                (pinned / path.name).write_bytes(path.read_bytes())
                definition = plistlib.loads(path.read_bytes())
                definition['ProgramArguments'][-1] = '--new'
                definition['RunAtLoad'] = False
                definition['StartCalendarInterval'] = {'Minute': 15}
                path.write_bytes(plistlib.dumps(definition))
            with patch.object(jobs.launchd_hold, 'read_holds', return_value={}):
                manifest = jobs.native_manifest(root, outlook_settings=settings)
            snapshot = {'launchctl_list': '\n'.join('- 0 ' + label for label in LABELS),
                        'launchctl_disabled': '', 'cron': '', 'machine_role': 'primary',
                        'plists': {label: plistlib.loads(render(pinned / (label + '.plist'))) for label in LABELS},
                        'launchctl_print': {}, 'log_mtimes': {}, 'git': {'branch': 'main'},
                        'runtime_freshness': {label: {'commits_behind_main': 77, 'main_ancestor': True} for label in LABELS}}
            rows = jobs.report(manifest, snapshot, 0)
            self.assertFalse({r['code'] for r in rows} & {'wrong_checkout', 'program_drift', 'log_path_drift', 'interval_drift', 'loaded_interval_drift'})
            self.assertEqual(len([r for r in rows if r.get('freshness', {}).get('commits_behind_main') == 77]), 3)
            snapshot['plists'][LABELS[0]]['WorkingDirectory'] = str(root / 'outside')
            self.assertIn('wrong_checkout', {r['code'] for r in jobs.report(manifest, snapshot, 0)})
            snapshot['plists'][LABELS[0]]['WorkingDirectory'] = str(settings.repo)
            snapshot['launchctl_print'][LABELS[0]] = 'working directory = ' + str(root / 'outside')
            self.assertIn('wrong_checkout', {r['code'] for r in jobs.report(manifest, snapshot, 0)})

    def test_missing_pinned_definition_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            templates = root / 'ops/launchd'
            templates.mkdir(parents=True)
            label = LABELS[0]
            (templates / (label + '.plist')).write_bytes(plistlib.dumps({'Label': label}))
            settings = SimpleNamespace(repo=root / 'pinned', render_plist=lambda path: path.read_bytes())
            with patch.object(jobs.launchd_hold, 'read_holds', return_value={}):
                manifest = jobs.native_manifest(root, outlook_settings=settings)
            self.assertEqual(manifest['jobs'], [])
            self.assertEqual(manifest['errors'], [label])
            rows = jobs.report(manifest, {'launchctl_list': '- 0 ' + label,
                               'launchctl_disabled': '', 'cron': '', 'machine_role': 'primary',
                               'plists': {}, 'launchctl_print': {}, 'log_mtimes': {},
                               'git': {'branch': 'main'}}, 0)
            self.assertIn((label, 'evidence_unavailable'), {(r['label'], r['code']) for r in rows})


if __name__ == '__main__':
    unittest.main()
