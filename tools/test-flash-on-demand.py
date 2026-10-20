#!/usr/bin/env python3
"""Flash policy tests with isolated state and no live launchd or model traffic."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
(ROOT / "out").mkdir(exist_ok=True)
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'tools/room-bridge'), str(ROOT / 'lib'), str(ROOT / 'tools/dictation-rig/bin')]
import flashlib


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class FlashPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'out')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        env = patch.dict(os.environ, {'CARR_FLASH_STATE_DIR': str(self.directory)})
        env.start()
        self.addCleanup(env.stop)
        self.process = patch.object(subprocess, 'run', side_effect=AssertionError('unexpected process')).start()
        patch.object(subprocess, 'Popen', side_effect=AssertionError('unexpected child')).start()
        self.addCleanup(patch.stopall)
        self.network = patch.object(flashlib.urllib.request, 'urlopen', side_effect=AssertionError('unexpected network')).start()

    def test_flash_on_grants_two_hours_then_expiry_stops_even_an_active_request(self):
        launch = Mock(return_value=subprocess.CompletedProcess([], 0, '', ''))
        with patch.object(flashlib.time, 'time', return_value=1000):
            self.assertTrue(flashlib.turn_on(ready=lambda **_: True, launch=launch))
            self.assertEqual(json.loads(flashlib.grant_path().read_text())['expires_at'], 8200)
            self.assertFalse(flashlib.is_switched_off())
            self.assertEqual([c.args[0] for c in launch.call_args_list], ['enable', 'bootstrap', 'kickstart'])
            stop = Mock()
            with flashlib.activity_scope():
                self.assertTrue(flashlib.idle_stop(now=8200, stop=stop))
            stop.assert_called_once()
        self.assertTrue(flashlib.is_switched_off())
        self.assertFalse(flashlib.grant_path().exists())
        self.assertTrue(flashlib.off_switch_path().exists())

    def test_grant_never_lets_automated_readiness_restart_a_stopped_server_or_desk(self):
        flashlib.grant_path().write_text(json.dumps({'issued_at': 0, 'expires_at': 4e9}))
        launch = Mock(side_effect=AssertionError('automatic restart'))
        self.assertFalse(flashlib.ensure(ready=lambda **_: False))
        with patch.object(flashlib, 'is_ready', return_value=False):
            with self.assertRaises(TimeoutError):
                flashlib.ensure_server()
        with patch.object(flashlib, 'ensure_server'):
            with self.assertRaises(TimeoutError):
                flashlib.ensure_desk(lambda: False)
        launch.assert_not_called()

    def test_flash_off_revokes_grant_immediately(self):
        flashlib.grant_path().write_text(json.dumps({'issued_at': 0, 'expires_at': 4e9}))
        stop = Mock()
        flashlib.turn_off(stop=stop)
        self.assertTrue(flashlib.is_switched_off())
        stop.assert_called_once()

    def test_invalid_grants_are_off(self):
        for text in ('{}', 'null', '[]', 'oops', '{"issued_at": 0, "expires_at": NaN}',
                     '{"issued_at": 0, "expires_at": 1}', '{"issued_at": 3.9e9, "expires_at": 4e9}'):
            with self.subTest(text=text):
                flashlib.grant_path().write_text(text)
                self.assertTrue(flashlib.is_switched_off())

    def test_resource_collector_only_uses_a_passive_probe(self):
        collector = load('tools/resource-collector.py', 'off_collector')
        with patch.object(flashlib, 'request_scope') as scope:
            opener = Mock(side_effect=OSError('offline'))
            self.assertEqual(collector.flash_server_available(opener=opener), (False, None))
            scope.assert_not_called()

    def test_health_check_reports_off_without_starting_or_probing_launchd(self):
        self.assertTrue(flashlib.health_row().startswith('OK'))
        self.process.assert_not_called()

    def test_dictation_post_call_reports_off_without_starting(self):
        post = load('tools/dictation-rig/bin/post_call.py', 'off_post_call')
        self.assertFalse(post.flash_server_available())
        with self.assertRaisesRegex(post.DistillerUnavailable, 'switched off'):
            post.default_distiller({})
        self.process.assert_not_called()

    def test_flash_wire_reports_off_without_a_start_or_request(self):
        import flash_wire
        self.assertFalse(flash_wire.is_up())
        self.assertEqual(flash_wire.run_turn('fixture')['detail'], flashlib.OFF_REASON)
        self.assertEqual(flash_wire.run_task({'body': 'fixture'})['detail'], flashlib.OFF_REASON)
        self.process.assert_not_called()
        self.network.assert_not_called()

    def test_dispatch_and_desk_start_refuse_flash_while_off(self):
        import desks
        import dispatch
        registry = desks.Registry(self.directory / 'desks.json')
        registry.register('flash', 'claude-session', socket='/tmp/fixture-flash.sock')
        with self.assertRaises(desks.DeskError):
            dispatch.dispatch('flash', 'fixture', registry=registry, results_path=self.directory / 'result')
        with self.assertRaises(desks.DeskError):
            dispatch.desk_start('flash', registry=registry, state_dir=self.directory, sock_dir=self.directory)
        self.process.assert_not_called()

    def test_room_probe_and_turns_never_auto_assign_flash(self):
        import bridge
        import state
        self.assertFalse(bridge.probe_live({'kind': 'flash-local'}))
        self.assertFalse(bridge.probe_live({'kind': 'claude-session', 'room_seat': 'flash'}))
        turn = {'kind': 'turn', 'msg_id': 'm1', 'seat': 'joe', 'seq': 1, 'body': '@flash hi'}
        self.assertEqual(state.route_turn({'desks': {}, 'last_seq': 0}, turn, {'flash': 'flash'}), [])

    def test_auto_routes_and_kanban_targets_exclude_flash(self):
        import kanban_adapter
        policy = json.loads((ROOT / 'ops/config/model-routes.v1.json').read_text())
        for route in ('code', 'script', 'direct'):
            self.assertNotEqual(policy['routes'][route]['model'], 'flash')
            self.assertNotIn(policy['queue_targets'][route], ('flash', 'flash-model'))
        catalog = kanban_adapter.load_catalog()
        self.assertFalse(catalog['targets']['flash']['enabled'])
        self.assertFalse(kanban_adapter._flash_is_up())

    def test_install_converges_drifted_plists_and_stops_both_labels(self):
        import plistlib
        import flash_install
        home = self.directory / 'home'
        agents = home / 'Library/LaunchAgents'
        agents.mkdir(parents=True)
        for label in (flashlib.SERVER_LABEL, flashlib.DESK_LABEL):
            (agents / (label + '.plist')).write_bytes(plistlib.dumps({
                'Label': label, 'RunAtLoad': True, 'KeepAlive': {'SuccessfulExit': False}}))
        launch = Mock(return_value=subprocess.CompletedProcess([], 0, '', ''))
        flash_install.install(ROOT, home, launch=launch)
        for label in (flashlib.SERVER_LABEL, flashlib.DESK_LABEL):
            body = plistlib.loads((agents / (label + '.plist')).read_bytes())
            self.assertFalse(body['RunAtLoad'])
            self.assertNotIn('KeepAlive', body)
            commands = [c.args[0][1] for c in launch.call_args_list if any(label in a for a in c.args[0])]
            self.assertIn('disable', commands)
            self.assertIn('bootout', commands)
            self.assertNotIn('enable', commands)
            self.assertNotIn('bootstrap', commands)

    def test_scheduled_jobs_rejects_enabled_and_drifted_flash_without_a_grant(self):
        import scheduled_jobs
        for label in (flashlib.SERVER_LABEL, flashlib.DESK_LABEL):
            with self.subTest(label=label):
                manifest = {'jobs': [{'label': label, 'scheduler': 'launchd',
                    'owner': 'orchestrator', 'required_checkout': '~/carr-system',
                    'expected_enabled': False, 'expected_installed': True,
                    'program_arguments': ['/fixture'], 'interval': {'RunAtLoad': False}}]}
                snapshot = {'machine_role': 'primary', 'plists': {label: {
                    'Label': label, 'ProgramArguments': ['/fixture'], 'RunAtLoad': True,
                    'KeepAlive': {'SuccessfulExit': False}}}, 'launchctl_list': '',
                    'launchctl_print': {}, 'launchctl_disabled': '"' + label + '" => false',
                    'cron': '', 'git': {'branch': 'main'}}
                rows = scheduled_jobs.report(manifest, snapshot, 1000)
                codes = {r['code'] for r in rows}
                self.assertIn('flash_without_grant', codes)
                snapshot['launchctl_disabled'] = '"' + label + '" => true'
                rows = scheduled_jobs.report(manifest, snapshot, 1000)
                self.assertIn('flash_without_grant', {r['code'] for r in rows})

    def test_config_check_detects_drift_without_registered_processes(self):
        import flash_install
        import plistlib
        home = self.directory / 'home'
        agents = home / 'Library/LaunchAgents'
        agents.mkdir(parents=True)
        for label in flash_install.LABELS:
            (agents / (label + '.plist')).write_bytes(plistlib.dumps({
                'Label': label, 'RunAtLoad': True, 'KeepAlive': True}))
        launch = Mock(return_value=subprocess.CompletedProcess([], 0, '{}', ''))
        self.assertTrue(flash_install.check(home, launch=launch))

    def test_granted_session_accepts_a_disabled_unused_desk(self):
        import scheduled_jobs
        label = flashlib.DESK_LABEL
        manifest = {'jobs': [{'label': label, 'scheduler': 'launchd',
            'owner': 'orchestrator', 'required_checkout': '~/carr-system',
            'expected_enabled': False, 'expected_installed': True,
            'program_arguments': ['/fixture'], 'interval': {'RunAtLoad': False}}]}
        snapshot = {'machine_role': 'primary', 'plists': {label: {
            'Label': label, 'ProgramArguments': ['/fixture'], 'RunAtLoad': False}},
            'launchctl_list': '', 'launchctl_print': {}, 'launchctl_disabled': '"' + label + '" => true',
            'cron': '', 'git': {'branch': 'main'}, 'flash_grant': {'issued_at': 900, 'expires_at': 1100}}
        self.assertEqual(scheduled_jobs.report(manifest, snapshot, 1000), [])

    def test_failed_explicit_start_revokes_the_grant_and_cleans_up(self):
        launch = Mock(side_effect=[subprocess.CompletedProcess([], 1, '', 'fixture failure')] +
                      [subprocess.CompletedProcess([], 0, '', '')] * 4)
        self.process.side_effect = None
        self.process.return_value = subprocess.CompletedProcess([], 0, '', '')
        self.assertFalse(flashlib.turn_on(ready=lambda **_: False, launch=launch))
        self.assertTrue(flashlib.is_switched_off())
        self.assertFalse(flashlib.grant_path().exists())

    def test_install_revokes_active_grant_when_correcting_automatic_startup(self):
        import flash_install
        import plistlib
        home = self.directory / 'home'
        agents = home / 'Library/LaunchAgents'
        agents.mkdir(parents=True)
        flashlib.grant_path().write_text(json.dumps({'issued_at': 0, 'expires_at': 4e9}))
        for label in flash_install.LABELS:
            (agents / (label + '.plist')).write_bytes(plistlib.dumps({
                'Label': label, 'RunAtLoad': True, 'KeepAlive': True}))
        launch = Mock(return_value=subprocess.CompletedProcess([], 0, '', ''))
        flash_install.install(ROOT, home, launch=launch)
        self.assertTrue(flashlib.is_switched_off())
        self.assertEqual(sum(c.args[0][1] == 'disable' for c in launch.call_args_list), 2)

    def test_installer_keeps_grant_state_in_its_explicit_home(self):
        import flash_install
        home = self.directory / 'installed-home'
        unexpected = self.directory / 'ambient-home'
        launch = Mock(return_value=subprocess.CompletedProcess([], 0, '', ''))
        with patch.dict(os.environ, {'HOME': str(unexpected)}):
            os.environ.pop('CARR_FLASH_STATE_DIR', None)
            flash_install.install(ROOT, home, launch=launch)
        self.assertTrue((home / '.config/carr/flash.off').exists())
        self.assertFalse(unexpected.exists())
        stop = next(c for c in launch.call_args_list if any('dispatch.py' in a for a in c.args[0]))
        self.assertEqual(stop.kwargs['env']['HOME'], str(home))

    def test_missing_grant_is_off_and_automated_ensure_cannot_start(self):
        self.assertTrue(flashlib.is_switched_off())
        launch = Mock(side_effect=AssertionError('automatic cold start'))
        self.assertFalse(flashlib.ensure(ready=lambda **_: True))
        with self.assertRaises(flashlib.FlashSwitchedOff):
            flashlib.ensure_server()
        with self.assertRaises(flashlib.FlashSwitchedOff):
            flashlib.ensure_desk(lambda: True)
        for scope in (flashlib.request_scope, flashlib.activity_scope):
            with self.assertRaises(flashlib.FlashSwitchedOff), scope():
                self.fail('Flash is off')
        launch.assert_not_called()


class FlashCommandTests(unittest.TestCase):
    def test_off_cli_callers_never_reach_launchctl(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'out') as directory:
            env = {'HOME': directory, 'PATH': '/usr/bin:/bin:/opt/homebrew/bin',
                   'CARR_FLASH_STATE_DIR': directory}
            for command in ([sys.executable, str(ROOT / 'bin/flash-ensure')],
                            ['/bin/zsh', str(ROOT / 'bin/flash-desk-start')]):
                result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn('switched off', result.stderr)

    def test_prepared_script_observes_live_grant_and_revocation(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'out') as directory, \
             patch.dict(os.environ, {'CARR_FLASH_STATE_DIR': directory}):
            script = load('tools/flash-script.py', 'prepared_script')
            work = Path(directory) / 'work'
            work.mkdir()
            script.stage([], str(work))
            flashlib.grant_path().write_text(json.dumps({'issued_at': 0, 'expires_at': 4e9}))
            code = 'import flashlib; print(flashlib.is_switched_off())'
            out, _ = script.run_code(code, str(work), 1, sandbox=Path(script.SANDBOX_EXEC).exists())
            self.assertEqual(out, 'False')
            flashlib.grant_path().unlink()
            with patch.object(script.flashlib, 'activity_scope', return_value=__import__('contextlib').nullcontext()):
                out, _ = script.run_code(code, str(work), 2, sandbox=Path(script.SANDBOX_EXEC).exists())
            self.assertEqual(out, 'True')


if __name__ == '__main__':
    unittest.main()
