"""Offline acceptance for release-bound Studio runtime refresh."""
import json
import io
import subprocess
from types import SimpleNamespace
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.studio_runtime_auto_refresh import tick, released_sha, Host
from git_env import fixture_env

A, B = 'a' * 40, 'b' * 40


class AutoRefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.host = Mock()
        self.host.release.return_value = A
        self.host.installed.return_value = B
        self.host.refresh.side_effect = lambda sha: setattr(self.host.installed, 'return_value', sha)

    def run_tick(self, now=10000):
        return tick(self.root, self.host, now=now)

    def test_success_then_same_sha_noop(self):
        self.assertEqual(self.run_tick(), 'refreshed')
        self.assertEqual(self.run_tick(10600), 'current')
        self.host.refresh.assert_called_once_with(A)
        rows = [json.loads(s) for s in (self.root / 'runs.jsonl').read_text().splitlines()]
        self.assertTrue(any(r['status'] == 'refreshed' and r['sha'] == A for r in rows))

    def test_two_failures_open_breaker_no_third_attempt(self):
        self.host.refresh.side_effect = RuntimeError('failed')
        self.assertEqual(self.run_tick(), 'failed')
        self.assertEqual(self.run_tick(10600), 'open')
        self.assertEqual(self.run_tick(11200), 'open')
        self.assertEqual(self.host.refresh.call_count, 2)
        self.assertTrue(self.host.alert.called)

    def test_rate_limit_survives_release_change(self):
        self.host.refresh.side_effect = RuntimeError('failed')
        self.run_tick()
        self.host.release.return_value = B
        self.host.installed.return_value = A
        self.assertEqual(self.run_tick(10300), 'cooldown')
        self.assertEqual(self.host.refresh.call_count, 1)

    def test_unknown_release_never_refreshes_and_discovery_is_bounded(self):
        self.host.release.side_effect = ValueError('not deployed')
        self.assertEqual(self.run_tick(), 'refused')
        self.assertEqual(self.run_tick(10001), 'wait')
        self.host.refresh.assert_not_called()
        self.assertEqual(self.host.release.call_count, 1)

    def test_attempt_reservation_survives_crash(self):
        self.host.refresh.side_effect = SystemExit('crash')
        with self.assertRaises(SystemExit):
            self.run_tick()
        self.assertEqual(self.run_tick(10300), 'cooldown')
        with self.assertRaises(SystemExit):
            self.run_tick(10600)
        self.assertEqual(self.run_tick(11200), 'open')
        self.assertEqual(self.host.refresh.call_count, 2)

    def test_crash_after_install_reconciles_to_current(self):
        def crash(sha):
            self.host.installed.return_value = sha
            raise SystemExit()
        self.host.refresh.side_effect = crash
        with self.assertRaises(SystemExit):
            self.run_tick()
        self.assertEqual(self.run_tick(10600), 'current')
        self.host.refresh.assert_called_once()

    def test_success_requires_runtime_readback(self):
        self.host.refresh.side_effect = None
        self.assertEqual(self.run_tick(), 'failed')

    def test_corrupt_ledger_refuses_effects(self):
        (self.root / 'state.json').write_text('{')
        with self.assertRaises(ValueError):
            self.run_tick()
        self.host.refresh.assert_not_called()

    def test_release_change_gets_new_budget_without_resetting_old_breaker(self):
        self.host.refresh.side_effect = RuntimeError('failure')
        self.run_tick()
        self.run_tick(10600)
        self.host.release.return_value = B
        self.host.installed.return_value = 'c' * 40
        self.assertEqual(self.run_tick(11200), 'failed')
        self.host.release.return_value = A
        self.assertEqual(self.run_tick(11800), 'open')
        self.assertEqual(self.host.refresh.call_count, 3)

    def test_concurrent_invocation_cannot_start_another_refresh(self):
        from lib.local_jobs import locked
        self.root.mkdir(exist_ok=True)
        with locked(self.root / 'lock'):
            with self.assertRaises(BlockingIOError):
                self.run_tick()
        self.host.release.assert_not_called()



class ReleaseTests(unittest.TestCase):
    def release(self, live=None, runs=None):
        live = live if live is not None else {'git_sha': {'value': A}, 'env': {'value': 'production'}}
        runs = runs if runs is not None else [{'displayTitle': f'Production release {A}',
                   'headBranch': 'main', 'event': 'workflow_run', 'conclusion': 'success'}]
        with patch('lib.studio_runtime_auto_refresh.urlopen', return_value=io.BytesIO(json.dumps(live).encode())), \
                patch('lib.studio_runtime_auto_refresh.command', return_value=json.dumps(runs)):
            return released_sha()

    def test_exact_successful_release(self):
        self.assertEqual(self.release(), A)

    def test_wrong_environment_sha_missing_and_failed_deploy_refuse(self):
        for live in ({'git_sha': {'value': A}, 'env': {'value': 'staging'}},
                     {'git_sha': {'value': 'main'}, 'env': {'value': 'production'}}):
            with self.assertRaises(ValueError):
                self.release(live=live)
        for field, value in [('displayTitle', f'Production release {B}'), ('headBranch', 'feature'),
                             ('conclusion', 'failure'), ('event', 'pull_request')]:
            runs = [{'displayTitle': f'Production release {A}', 'headBranch': 'main',
                     'event': 'workflow_run', 'conclusion': 'success', field: value}]
            with self.assertRaises(ValueError):
                self.release(runs=runs)
        with self.assertRaises(ValueError):
            self.release(runs=[])

    def test_network_failure_refuses(self):
        with patch('lib.studio_runtime_auto_refresh.urlopen', side_effect=TimeoutError()):
            with self.assertRaises(TimeoutError):
                released_sha()

    def test_alert_enters_existing_watchdog_inbox_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            host = object.__new__(Host)
            host.source = Path(temporary)
            host.alert(A, 10000)
            host.alert(A, 10600)
            rows = (host.source / 'out/watchdog/orchestrator-inbox.jsonl').read_text().splitlines()
            self.assertEqual(len(rows), 1)
            self.assertEqual(json.loads(rows[0])['finding']['exit_code'], 1)

    def test_released_source_clone_uses_release_while_main_ahead_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            upstream, source = root / 'upstream', root / 'source'
            upstream.mkdir()
            def git(*args):
                return subprocess.run(['git', *map(str, args)], env=fixture_env(),
                                      check=True, capture_output=True, text=True).stdout.strip()
            git('init', '-b', 'main', upstream)
            git('-C', upstream, 'config', 'user.name', 'Fixture')
            git('-C', upstream, 'config', 'user.email', 'fixture@example.invalid')
            (upstream / 'version').write_text('released')
            git('-C', upstream, 'add', 'version')
            git('-C', upstream, 'commit', '-m', 'released')
            release = git('-C', upstream, 'rev-parse', 'HEAD')
            (upstream / 'version').write_text('not released')
            git('-C', upstream, 'commit', '-am', 'newer')
            git('clone', upstream, source)
            with patch('lib.studio_runtime_auto_refresh.load_settings', return_value=SimpleNamespace(repo=root / 'runtime')):
                host = Host(source)
            from lib.studio_runtime_auto_refresh import command as real_command
            clones = []
            def run(*args, **kwargs):
                if str(args[0]) == sys.executable:
                    clone = Path(args[args.index('--source') + 1])
                    clones.append(clone)
                    self.assertEqual(git('-C', clone, 'rev-parse', 'HEAD'), release)
                    self.assertEqual((clone / 'version').read_text(), 'released')
                    self.assertIn('--released', args)
                    return ''
                return real_command(*args, **kwargs)
            before = git('-C', source, 'rev-parse', 'HEAD')
            with patch('lib.studio_runtime_auto_refresh.command', side_effect=run):
                host.refresh(release)
            self.assertEqual(git('-C', source, 'rev-parse', 'HEAD'), before)
            self.assertTrue(clones)
            self.assertFalse(clones[0].exists())

    def test_released_wrapper_accepts_clean_ancestor_but_refuses_other_live_sha(self):
        from lib.outlook_runtime_refresh import Host as RefreshHost
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = SimpleNamespace(repo=root / 'runtime', decoder=root / 'decoder',
                                       services={'intake', 'triage', 'outbox'})
            host = RefreshHost(root / 'source', A, settings, released=True)
            host.plist_path = lambda label: root / label
            calls = []
            def git(repo, *args):
                calls.append(args)
                if args == ('remote', 'get-url', 'origin'):
                    return 'https://github.com/DoctorCRE/carr-system.git'
                if args[:1] == ('ls-remote',):
                    return B + ' refs/heads/main'
                if args == ('rev-parse', 'HEAD'):
                    return A if repo == host.source else 'c' * 40
                return ''
            host.git = git
            host.command = Mock(return_value=SimpleNamespace(stdout=''))
            with patch('lib.studio_runtime_auto_refresh.released_sha', return_value=A):
                host.validate()
            self.assertIn(('merge-base', '--is-ancestor', A, B), calls)
            with patch('lib.studio_runtime_auto_refresh.released_sha', return_value=B):
                with self.assertRaises(ValueError):
                    host.validate()

    def test_calendar_and_machine_scope(self):
        import plistlib
        from lib.launchd_scope import allowed_on_machine
        source = Path(__file__).resolve().parents[1]
        filename = 'com.carr.studio-runtime-auto-refresh.plist'
        definition = plistlib.loads((source / 'ops/launchd' / filename).read_bytes())
        self.assertEqual(definition['StartCalendarInterval'], [{'Minute': n} for n in range(0, 60, 5)])
        self.assertTrue(allowed_on_machine(filename, True))
        self.assertFalse(allowed_on_machine(filename, False))


if __name__ == '__main__':
    unittest.main()
