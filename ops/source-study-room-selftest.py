#!/usr/bin/env python3
"""Research desk confinement and provider evidence, without model calls."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import time
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class ResearchDeskTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('research_room', ROOT / 'tools/room-bridge/source_study.py')
        self.room = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.room)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name).resolve()

    def test_route_requires_named_model_effort_and_no_added_authority(self):
        entry = {'name': 'source-study', 'kind': 'codex-session', 'family': 'sol',
                 'effort': 'high', 'sandbox': 'read-only'}
        self.assertEqual(self.room.validate_desk(entry), entry)
        for change in ({'family': None}, {'effort': None}, {'sandbox': 'danger-full-access'},
                       {'kind': 'claude-session'}, {'add_dirs': ['/']}, {'name': 'unreviewed'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.room.validate_desk({**entry, **change})

    @unittest.skipUnless(shutil.which('sandbox-exec') and shutil.which('codex'), 'installed macOS Codex health probe')
    def test_installed_codex_starts_without_forking_its_npm_launcher(self):
        job = self.folder / 'health'
        job.mkdir()
        command = [shutil.which('codex'), '--version']
        proc = subprocess.run(self.room.confine(command, job), cwd=job,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('codex', proc.stdout.lower())

    def test_streaming_cannot_bypass_provider_confinement(self):
        sys.path.insert(0, str(ROOT / 'tools/room-bridge'))
        import dispatch
        from desks import DeskError
        class Registry:
            def resolve(self, name):
                return {'kind': 'codex-session', 'model': 'fixture-model', 'effort': 'high'}
        with patch.object(dispatch, '_run_codex_process', side_effect=AssertionError('unconfined runner')):
            with self.assertRaises(DeskError):
                dispatch.dispatch('source-study', 'fixture', registry=Registry(),
                                  stream_output=True, provider_run=lambda *_: None)

    def test_model_readback_is_bound_to_result_thread(self):
        sessions = self.folder / 'sessions'
        sessions.mkdir()
        path = sessions / 'rollout.jsonl'
        records = [{'type': 'session_meta', 'payload': {'id': 'thread-a'}},
                   {'type': 'turn_context', 'payload': {'model': 'fixture-model', 'effort': 'high'}}]
        path.write_text('\n'.join(json.dumps(v) for v in records))
        entry = {'model': 'fixture-model', 'effort': 'high'}
        self.assertEqual(self.room.observed_model(self.folder, 'thread-a', entry),
                         {'model': 'fixture-model', 'effort': 'high', 'thread_id': 'thread-a'})
        for thread, model in (('other-thread', 'fixture-model'), ('thread-a', 'wrong-model')):
            with self.subTest(thread=thread, model=model), self.assertRaises(ValueError):
                self.room.observed_model(self.folder, thread, {**entry, 'model': model})
        records.append({'type': 'turn_context', 'payload': {'model': 'wrong-model', 'effort': 'high'}})
        path.write_text('\n'.join(json.dumps(v) for v in records))
        with self.assertRaises(ValueError):
            self.room.observed_model(self.folder, 'thread-a', entry)

    def test_runtime_drops_ambient_credentials_and_mcp_configuration(self):
        home = self.folder / 'ambient'
        home.mkdir()
        (home / 'auth.json').write_text('{"provider_auth":"synthetic"}')
        (home / 'config.toml').write_text('[mcp_servers.carr]\ncommand="mutation-tool"')
        env = self.room.runtime_env(self.folder / 'job', home,
                                    {'PATH': '/usr/bin:/bin', 'DATABASE_URL': 'synthetic-dsn',
                                     'CARR_TOKEN': 'synthetic-token', 'OPENAI_API_KEY': 'synthetic-key'})
        self.assertNotIn('DATABASE_URL', env)
        self.assertNotIn('CARR_TOKEN', env)
        self.assertNotIn('OPENAI_API_KEY', env)
        config = (Path(env['CODEX_HOME']) / 'config.toml').read_text()
        self.assertNotIn('mcp_servers', config)
        self.assertNotIn('mutation-tool', config)
        self.assertIn('multi_agent=false', config)
        self.assertNotEqual(Path(env['HOME']), home)

    def test_retrieval_runtime_needs_only_grok_provider_auth(self):
        auth = self.folder / 'grok-auth'
        auth.mkdir()
        (auth / 'auth.json').write_text('{"provider_auth":"synthetic"}')
        env = self.room.runtime_env(self.folder / 'job', auth, {'PATH': '/usr/bin:/bin'}, provider='grok')
        self.assertNotIn('CODEX_HOME', env)
        self.assertTrue((Path(env['HOME']) / '.grok/auth.json').is_file())
        self.assertFalse((Path(env['HOME']) / '.codex').exists())

    @unittest.skipUnless(shutil.which('sandbox-exec'), 'macOS confinement probe')
    def test_os_scope_denies_repo_write_secret_read_and_socket_access(self):
        job = self.folder / 'job'
        job.mkdir()
        outside = self.folder / 'secret'
        outside.write_text('synthetic-secret')
        listener = socket.socket(socket.AF_UNIX)
        sockpath = str(self.folder / 'carr.sock')
        listener.bind(sockpath)
        listener.listen()
        self.addCleanup(listener.close)
        code = ('from pathlib import Path; import socket\n'
                f'job=Path({str(job)!r}); outside=Path({str(outside)!r})\n'
                "job.joinpath('allowed').write_text('report')\n"
                "for action in (lambda: outside.read_text(), lambda: outside.write_text('bad'), "
                f"lambda: socket.socket(socket.AF_UNIX).connect({sockpath!r})):\n"
                " try: action()\n"
                " except OSError: pass\n"
                " else: raise SystemExit('authority escaped')\n")
        result = subprocess.run(self.room.confine([sys.executable, '-c', code], job),
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(outside.read_text(), 'synthetic-secret')
        self.assertEqual((job / 'allowed').read_text(), 'report')

    def provider_detach_attempt(self, termination):
        job = self.folder / termination
        job.mkdir()
        intermediary = (
            "import subprocess, sys\nfrom pathlib import Path\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], "
            "start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, "
            "stderr=subprocess.DEVNULL)\n"
            "Path('escaped.pid').write_text(str(child.pid))\n"
        )
        code = (
            "import subprocess, sys, time\nfrom pathlib import Path\n"
            "try:\n"
            f" child = subprocess.Popen([sys.executable, '-c', {intermediary!r}])\n"
            " child.wait()\n"
            "except OSError:\n Path('fork-denied').write_text('denied')\n"
            "Path('ready').touch()\n"
            + ("time.sleep(60)\n" if termination != 'completion' else "")
        )
        proc = subprocess.Popen(self.room.confine([sys.executable, '-c', code], job),
                                cwd=job, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 10
            while not (job / 'ready').exists() and proc.poll() is None and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue((job / 'ready').exists(), 'provider did not reach its lifetime fixture')
            if termination == 'timeout':
                with self.assertRaises(subprocess.TimeoutExpired):
                    proc.wait(timeout=.1)
                proc.kill()
            elif termination == 'cancellation':
                proc.terminate()
            proc.communicate(timeout=5)
            self.assertTrue((job / 'fork-denied').exists(), 'provider could create an unowned child')
            self.assertFalse((job / 'escaped.pid').exists(), 'detached grandchild reached execution')
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.communicate(timeout=5)
            if (job / 'escaped.pid').exists():
                try:
                    os.kill(int((job / 'escaped.pid').read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass

    @unittest.skipUnless(shutil.which('sandbox-exec'), 'macOS confinement probe')
    def test_completion_prevents_reparented_provider_child(self):
        self.provider_detach_attempt('completion')

    @unittest.skipUnless(shutil.which('sandbox-exec'), 'macOS confinement probe')
    def test_timeout_prevents_reparented_provider_child(self):
        self.provider_detach_attempt('timeout')

    @unittest.skipUnless(shutil.which('sandbox-exec'), 'macOS confinement probe')
    def test_cancellation_prevents_reparented_provider_child(self):
        self.provider_detach_attempt('cancellation')

    @unittest.skipUnless(shutil.which('sandbox-exec'), 'macOS confinement probe')
    def test_real_room_dispatch_with_fake_provider_binds_model_and_result(self):
        job = self.folder / 'job'
        (job / 'bin').mkdir(parents=True)
        provider = job / 'bin/codex'
        provider.write_text('#!/usr/bin/env python3\n' + '''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
assert args[args.index('-s') + 1] == 'read-only'
assert 'DATABASE_URL' not in os.environ and 'CARR_TOKEN' not in os.environ
config = (Path(os.environ['CODEX_HOME']) / 'config.toml').read_text()
assert 'mcp_servers' not in config and 'shell_tool=false' in config
Path(args[args.index('-o') + 1]).write_text('verified fixture report')
sessions = Path(os.environ['CODEX_HOME']) / 'sessions'
sessions.mkdir()
rows = [{'type': 'session_meta', 'payload': {'id': 'fixture-thread'}},
        {'type': 'turn_context', 'payload': {'model': 'gpt-9.9-sol', 'effort': 'high'}}]
(sessions / 'rollout.jsonl').write_text('\\n'.join(json.dumps(r) for r in rows))
print(json.dumps({'type': 'thread.started', 'thread_id': 'fixture-thread'}))
print(json.dumps({'type': 'turn.completed'}))
''')
        provider.chmod(0o755)
        auth = self.folder / 'auth'
        auth.mkdir()
        (auth / 'auth.json').write_text('{"provider_auth":"synthetic"}')
        (auth / 'models_cache.json').write_text(json.dumps({'models': [{'slug': 'gpt-9.9-sol'}]}))
        registry = self.folder / 'registry.json'
        registry.write_text(json.dumps({'desks': {'source-study': {
            'kind': 'codex-session', 'family': 'sol', 'effort': 'high', 'sandbox': 'read-only'}}}))
        env = dict(os.environ, CARR_HERMES_DESKS=str(registry), CODEX_HOME=str(auth),
                   DATABASE_URL='synthetic-dsn', CARR_TOKEN='synthetic-token',
                   PATH=str(job / 'bin') + os.pathsep + os.environ['PATH'])
        command = [sys.executable, str(ROOT / 'tools/room-bridge/source_study.py')]
        route = subprocess.run([*command, '--preflight'], env=env, capture_output=True, text=True, check=True)
        (job / 'request.json').write_text(json.dumps({'route': json.loads(route.stdout),
                                                     'prompt': 'Synthetic fixture', 'timeout': 10}))
        result = subprocess.run([*command, '--job', str(job)], env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        row = json.loads(result.stdout)
        self.assertEqual(row['result'], 'verified fixture report')
        self.assertEqual(row['observed'], {'model': 'gpt-9.9-sol', 'effort': 'high', 'thread_id': 'fixture-thread'})
        self.assertFalse(list(job.rglob('auth.json')), 'provider credentials must not persist after dispatch')

    @unittest.skipUnless(shutil.which('sandbox-exec'), 'macOS confinement probe')
    def test_grok_room_wire_uses_confinement_and_provider_readback(self):
        job = self.folder / 'job'
        (job / 'bin').mkdir(parents=True)
        outside = self.folder / 'host-secret'
        outside.write_text('synthetic')
        provider = job / 'bin/grok'
        provider.write_text('#!/usr/bin/env python3\n' +
                            'from pathlib import Path; import json, sys\n' +
                            f'try: Path({str(outside)!r}).read_text()\n' +
                            'except OSError: pass\nelse: sys.exit(99)\n' +
                            "print(json.dumps({'type': 'text', 'data': 'RAW SOURCE'}))\n" +
                            "print(json.dumps({'type': 'end', 'stopReason': 'end_turn', "
                            "'requestId': 'fixture-request', 'sessionId': 'fixture-session', "
                            "'modelUsage': {'grok-4.7-build': {'modelCalls': 1}}}))\n")
        provider.chmod(0o755)
        ambient = self.folder / 'ambient'
        (ambient / '.grok').mkdir(parents=True)
        (ambient / '.grok/auth.json').write_text('{"provider_auth":"synthetic"}')
        registry = self.folder / 'registry.json'
        registry.write_text(json.dumps({'desks': {'grok-build': {
            'kind': 'grok-cli', 'model': 'grok-4.7', 'effort': 'high', 'sandbox': 'read-only'}}}))
        env = dict(os.environ, HOME=str(ambient), CARR_HERMES_DESKS=str(registry),
                   PATH=str(job / 'bin') + os.pathsep + os.environ['PATH'])
        command = [sys.executable, str(ROOT / 'tools/room-bridge/source_study.py'), '--phase', 'retrieval']
        route = subprocess.run([*command, '--preflight'], env=env, capture_output=True, text=True, check=True)
        (job / 'request.json').write_text(json.dumps({'route': json.loads(route.stdout),
                                                     'prompt': 'Synthetic public retrieval', 'timeout': 10}))
        result = subprocess.run([*command, '--job', str(job)], env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        row = json.loads(result.stdout)
        self.assertEqual(row['result'], 'RAW SOURCE')
        self.assertEqual(row['observed']['model'], 'grok-4.7-build')
        self.assertFalse(list(job.rglob('auth.json')))


if __name__ == '__main__':
    unittest.main()
