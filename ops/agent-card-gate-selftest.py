#!/usr/bin/env python3
"""Exercise direct admission through JSON stdin and synthetic HTTPS."""
import json
import os
from pathlib import Path
import runpy
import shutil
import signal
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from git_env import fixture_env

REPO = Path(__file__).resolve().parent.parent


class AgentCardGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.certs = tempfile.TemporaryDirectory()
        cls.cert = Path(cls.certs.name) / 'cert.pem'
        key = Path(cls.certs.name) / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', str(key), '-out', str(cls.cert), '-days', '1',
                        '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost'],
                       check=True, capture_output=True)
        cls.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cls.context.load_cert_chain(cls.cert, key)

    @classmethod
    def tearDownClass(cls):
        cls.certs.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for directory in ('hooks', 'lib', 'tools', 'ops'):
            (self.root / directory).mkdir()
        for relative in ('hooks/agent-card-gate.py', 'hooks/cmd_text.py', 'hooks/guard-unattended.py',
                         'hooks/gate_paths.py', 'hooks/refused_content.py', 'lib/hook_runtime.py',
                         'ops/git_env.py'):
            shutil.copyfile(REPO / relative, self.root / relative)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True, env=fixture_env())
        subprocess.run(['git', 'remote', 'add', 'origin',
                        'https://github.com/DoctorCRE/carr-system.git'], cwd=self.root, check=True, env=fixture_env())
        self.home = self.root / 'home'
        self.home.mkdir()
        self.lookup = self.root / 'reply.py'
        self.reply_fixture(False)
        self.status = 200
        self.body = None
        self.delay = 0
        self.trickle = False
        self.calls = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                try:
                    request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    owner.calls.append((request, self.headers.get('Authorization')))
                    assert request['params']['name'] == 'job-card-registration'
                    args = request['params']['arguments']
                    result = subprocess.run([sys.executable, str(owner.lookup),
                                             'job-card-registration', json.dumps(args)],
                                            capture_output=True, text=True, timeout=1,
                                            env=fixture_env())
                    body = owner.body
                    if body is None:
                        body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': {
                            'content': [{'type': 'text', 'text': result.stdout}],
                            'isError': result.returncode != 0}}).encode()
                    time.sleep(owner.delay)
                    self.send_response(owner.status)
                    self.send_header('Location', 'https://localhost/never-follow')
                    self.end_headers()
                    if owner.trickle:
                        for byte in body:
                            self.wfile.write(bytes([byte]))
                            self.wfile.flush()
                            time.sleep(0.2)
                    else:
                        self.wfile.write(body)
                except (OSError, AssertionError, subprocess.SubprocessError):
                    pass

        self.server = ThreadingHTTPServer(('localhost', 0), Handler)
        self.server.socket = self.context.wrap_socket(self.server.socket, server_side=True)
        thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.config = dict(fixture_env(), HOME=str(self.home),
                           CARR_MCP_ENV=str(self.home / 'missing-tokens'),
                           CARR_MCP_LOCAL_TOKEN='synthetic-only',
                           CARR_MCP_URL=f'https://localhost:{self.server.server_port}/mcp',
                           SSL_CERT_FILE=str(self.cert))
        self.prelude = ''

    def run_hook(self, tool='Agent', cwd=None, **inputs):
        hook = self.root / 'hooks/agent-card-gate.py'
        code = self.prelude + f'\nimport runpy; runpy.run_path({str(hook)!r}, run_name="__main__")'
        return subprocess.run([sys.executable, '-c', code],
                              input=json.dumps({'hook_event_name': 'PreToolUse', 'cwd': str(cwd or self.root),
                                                'tool_name': tool, 'tool_input': inputs}),
                              text=True, capture_output=True, env=self.config, timeout=10)

    def reply_fixture(self, registered):
        self.lookup.write_text('import json, sys\n'
                               'assert sys.argv[1] == "job-card-registration"\n'
                               'card = json.loads(sys.argv[2])["card_id"]\n'
                               'print(json.dumps({"ok": True, "card_id": card, '
                               f'"registered": {registered!r} and card in '
                               '("WR557.S6.J1", "WR557.S6.J2"), '
                               '"executor": "codex", "model": "synthetic", '
                               '"repository": "DoctorCRE/carr-system", "work_request_state": "captured"}))\n')

    def test_agent_without_card_denied_with_mint_command(self):
        result = self.run_hook(prompt='Review the plan.')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('./run.sh call dispatch-job', result.stderr)

    def test_unregistered_card_denied(self):
        result = self.run_hook(prompt='Card: WR557.S6.J99999\nReview the plan.')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('not registered', result.stderr)

    def register_fixture(self):
        self.reply_fixture(True)

    def test_registered_card_allowed(self):
        self.register_fixture()
        result = self.run_hook(prompt='Card: WR557.S6.J1\nReview the plan.')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_registered_card_allows_foreground_agent(self):
        self.register_fixture()
        result = self.run_hook(prompt='Card: WR557.S6.J1\nReview the plan.', run_in_background=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_bash_codex_exec_without_card_denied(self):
        result = self.run_hook('Bash', command='codex exec "Review the plan."')
        self.assertEqual(result.returncode, 2, result.stderr)

    def test_codex_resume_continues_without_card(self):
        self.lookup.write_text('raise SystemExit("queue must not be read")')
        for command in ('codex exec resume --last "Continue."',
                        'codex exec resume session-id "Continue."',
                        'codex -m default e resume --last --full-auto',
                        'codex exec resume --last; codex exec resume session-id'):
            with self.subTest(command=command):
                result = self.run_hook('Bash', command=command)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_resume_does_not_authorize_a_new_session(self):
        result = self.run_hook('Bash', command='codex exec resume --last; codex exec "New job."')
        self.assertEqual(result.returncode, 2, result.stderr)

    def test_other_bash_and_launcher_untouched(self):
        for command in ('rg Card hooks',
                        "printf '%s' 'codex exec review'", "cat <<'EOF'\ncodex exec review\nEOF"):
            with self.subTest(command=command):
                self.assertEqual(self.run_hook('Bash', command=command).returncode, 0)

    def test_unknown_codex_option_requires_inline_prompt(self):
        result = self.run_hook('Bash', command='codex exec --unknown value')
        self.assertEqual(result.returncode, 2)
        self.assertIn('use a literal inline prompt', result.stderr)

    def test_bash_registered_prompt_allowed(self):
        self.register_fixture()
        for command in ("codex exec 'Card: WR557.S6.J1\nReview.'",
                        "timeout 30 /opt/bin/codex exec -c 'model=default' 'Card: WR557.S6.J1\nReview.'",
                        "codex -m default exec --skip-git-repo-check 'Card: WR557.S6.J1\nReview.'",
                        "codex cloud exec --env 6ae0a2a8 --branch main --attempts 2 'Card: WR557.S6.J1\nReview.'"):
            with self.subTest(command=command):
                result = self.run_hook('Bash', command=command)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_codex_cloud_exec_needs_card(self):
        result = self.run_hook('Bash', command="codex cloud exec --env 6ae0a2a8 --branch main 'Review.'")
        self.assertEqual(result.returncode, 2, result.stderr)

    def test_codex_help_is_not_a_launch(self):
        for command in ('codex cloud exec --help', 'codex exec -h'):
            with self.subTest(command=command):
                self.assertEqual(self.run_hook('Bash', command=command).returncode, 0)

    def test_card_outside_launch_prompt_does_not_authorize(self):
        self.register_fixture()
        for command in ("printf 'Card: WR557.S6.J1\n'; codex exec Review",
                        "codex exec -c 'Card: WR557.S6.J1\n' Review",
                        "codex exec 'Card: WR557.S6.J1\nReview.'; codex exec Review",
                        "codex -m default exec Review"):
            with self.subTest(command=command):
                result = self.run_hook('Bash', command=command)
                self.assertEqual(result.returncode, 2, result.stderr)

    def test_foreground_agents_need_card(self):
        for kind in ('Plan', 'general-purpose'):
            with self.subTest(kind=kind):
                shutil.rmtree(self.home)
                self.home.mkdir()
                result = self.run_hook(prompt='Search code.', subagent_type=kind,
                                       run_in_background=False)
                self.assertEqual(result.returncode, 2, result.stderr)

    def test_agent_default_foreground_needs_card(self):
        self.assertEqual(self.run_hook(prompt='Search code.', run_in_background=None).returncode, 2)

    def test_read_only_agents_need_no_card(self):
        for kind in ('web-fetch', 'Explore'):
            for background in (False, True):
                with self.subTest(kind=kind, background=background):
                    result = self.run_hook(prompt='Read.', subagent_type=kind, run_in_background=background)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_project_settings_guard_both_launch_tools(self):
        groups = json.loads((REPO / '.claude/settings.json').read_text())['hooks']['PreToolUse']
        for tool in ('Agent', 'Bash'):
            self.assertTrue(any(tool in group.get('matcher', '').split('|') and
                                any('agent-card-gate.py' in hook['command']
                                    for hook in group['hooks']) for group in groups))

    def test_malformed_or_duplicate_card_denied(self):
        self.register_fixture()
        for prompt in ('Card: WR0.S6.J1', 'Card: WR557.S6.J0',
                       'Card: WR557.S6.J1 extra', 'text Card: WR557.S6.J1',
                       'Card: WR557.S6.J1\nCard: WR557.S6.J1'):
            with self.subTest(prompt=prompt):
                self.assertEqual(self.run_hook(prompt=prompt).returncode, 2)

    def test_direct_https_without_wrapper_or_cache(self):
        self.register_fixture()
        cache = self.home / '.cache/carr/agent-card-cache-v1'
        cache.parent.mkdir(parents=True)
        cache.write_text('unusable legacy storage')
        result = self.run_hook(prompt='Card: WR557.S6.J1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(cache.read_text(), 'unusable legacy storage')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][1], 'Bearer synthetic-only')

    def test_outage_after_success_refuses(self):
        self.register_fixture()
        self.assertEqual(self.run_hook(prompt='Card: WR557.S6.J1').returncode, 0)
        self.status = 503
        result = self.run_hook(prompt='Card: WR557.S6.J1')
        self.assertEqual(result.returncode, 2)
        self.assertIn('WR557.S6.J1', result.stderr)

    def test_failure_matrix(self):
        self.register_fixture()
        good = {'ok': True, 'card_id': 'WR557.S6.J1', 'registered': True,
                'repository': 'DoctorCRE/carr-system', 'executor': 'codex',
                'model': 'synthetic', 'work_request_state': 'captured'}
        invalid = [dict(good, card_id='WR557.S6.J2'), dict(good, registered=1),
                   dict(good, model=''), dict(good, repository=42), {'ok': True}, []]
        bodies = [b'private-upstream-text', b'x' * 65537,
                  b'{"error":{"message":"private-upstream-text"}}',
                  b'{"result":{"isError":true,"content":[]}}']
        bodies += [json.dumps({'result': {'content': [{'type': 'text', 'text': json.dumps(item)}]}}).encode()
                   for item in invalid]
        for body in bodies:
            with self.subTest(body=body[:80]):
                self.body = body
                result = self.run_hook(prompt='Card: WR557.S6.J1')
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('WR557.S6.J1', result.stderr)
                self.assertNotIn('private-upstream-text', result.stderr)
                self.assertNotIn('synthetic-only', result.stderr)
        self.body = None
        for status in (301, 401, 403, 429, 500):
            with self.subTest(status=status):
                self.status = status
                result = self.run_hook(prompt='Card: WR557.S6.J1')
                self.assertEqual(result.returncode, 2)
                self.assertIn(str(status), result.stderr)
        self.assertEqual(len(self.calls), len(bodies) + 5)

    def test_tls_and_disconnect_refuse(self):
        self.config.pop('SSL_CERT_FILE')
        self.assertEqual(self.run_hook(prompt='Card: WR557.S6.J1').returncode, 2)
        self.config['CARR_MCP_URL'] = 'https://127.0.0.1:1/mcp'
        self.assertEqual(self.run_hook(prompt='Card: WR557.S6.J1').returncode, 2)

    def test_profiles_and_credential_security(self):
        module = runpy.run_path(str(REPO / 'hooks/agent-card-gate.py'))
        token_file = self.home / 'tokens'
        for profile, variable in module['TOKEN_VARIABLES'].items():
            token_file.write_text(f'{variable}="file-synthetic"\n')
            token_file.chmod(0o600)
            config = dict(self.config, CARR_MCP_ENV=str(token_file), CARR_MCP_CLIENT_PROFILE=profile)
            config.pop('CARR_MCP_LOCAL_TOKEN')
            with patch.dict(os.environ, config, clear=True):
                self.assertEqual(module['credential'](), 'file-synthetic')
                os.environ[variable] = 'environment-synthetic'
                self.assertEqual(module['credential'](), 'environment-synthetic')
                token_file.chmod(0o644)
                with self.assertRaises(ValueError):
                    module['credential']()
        token_file.unlink()
        for kind in ('missing', 'symlink', 'directory', 'foreign-owner', 'profile'):
            with self.subTest(kind=kind), patch.dict(os.environ, self.config, clear=True):
                os.environ.pop('CARR_MCP_LOCAL_TOKEN')
                os.environ['CARR_MCP_ENV'] = str(token_file)
                if kind == 'symlink':
                    token_file.symlink_to(self.cert)
                elif kind == 'directory':
                    token_file.mkdir()
                elif kind == 'profile':
                    os.environ['CARR_MCP_CLIENT_PROFILE'] = 'unsupported'
                elif kind == 'foreign-owner':
                    token_file.write_text('')
                    token_file.chmod(0o600)
                with patch('os.getuid', return_value=-1) if kind == 'foreign-owner' else patch.dict(os.environ, {}):
                    with self.assertRaises((ValueError, OSError)):
                        module['credential']()
                if token_file.is_dir():
                    token_file.rmdir()
                else:
                    token_file.unlink(missing_ok=True)

    def test_deadlines_cover_entire_request(self):
        self.register_fixture()
        for phase in ('dns', 'tls', 'headers', 'body'):
            with self.subTest(phase=phase):
                self.delay = 4 if phase == 'headers' else 0
                self.trickle = phase == 'body'
                self.prelude = ''
                if phase == 'dns':
                    self.prelude = 'import socket,time; socket.getaddrinfo=lambda *a,**k: time.sleep(20)'
                elif phase == 'tls':
                    self.prelude = 'import ssl,time; ssl.SSLContext.wrap_socket=lambda *a,**k: time.sleep(20)'
                started = time.monotonic()
                result = self.run_hook(prompt='Card: WR557.S6.J1')
                elapsed = time.monotonic() - started
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('WR557.S6.J1', result.stderr)
                self.assertIn('timeout', result.stderr.lower())
                self.assertLess(elapsed, 4.5)

    def test_batch_deduplicates_and_shares_deadline(self):
        self.register_fixture()
        result = self.run_hook('mcp__t3-code__create_threads', threads=[{'prompt': 'Card: WR557.S6.J1'}] * 2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls), 1)
        self.delay = 2.7
        self.lookup.write_text(self.lookup.read_text().replace('("WR557.S6.J1", "WR557.S6.J2")',
                                                            '("WR557.S6.J1", "WR557.S6.J2", "WR557.S6.J3")'))
        started = time.monotonic()
        result = self.run_hook('mcp__t3-code__create_threads', threads=[
            {'prompt': f'Card: WR557.S6.J{job}'} for job in (1, 2, 3)])
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertLess(time.monotonic() - started, 9)

    def test_repository_binding_and_git_isolation(self):
        self.register_fixture()
        other = self.root / 'other'
        subprocess.run(['git', 'init', '-q', str(other)], check=True, env=fixture_env())
        subprocess.run(['git', '-C', str(other), 'remote', 'add', 'origin',
                        'https://github.com/jbookout/doctorcre-app.git'], check=True, env=fixture_env())
        self.config['GIT_DIR'] = str(other / '.git')
        self.assertEqual(self.run_hook(prompt='Card: WR557.S6.J1').returncode, 0)
        self.lookup.write_text(self.lookup.read_text().replace('DoctorCRE/carr-system', 'jbookout/doctorcre-app'))
        self.assertEqual(self.run_hook(prompt='Card: WR557.S6.J1').returncode, 2)
        self.lookup.write_text(self.lookup.read_text().replace('"jbookout/doctorcre-app"', 'None'))
        self.assertEqual(self.run_hook(prompt='Card: WR557.S6.J1').returncode, 0)

    def test_timer_restored_and_late_success_refuses(self):
        self.register_fixture()
        module = runpy.run_path(str(REPO / 'hooks/agent-card-gate.py'))
        original = signal.getsignal(signal.SIGALRM)
        handler = lambda *args: None
        signal.signal(signal.SIGALRM, handler)
        signal.setitimer(signal.ITIMER_REAL, 20, 2)
        self.addCleanup(signal.signal, signal.SIGALRM, original)
        self.addCleanup(signal.setitimer, signal.ITIMER_REAL, 0)
        with patch.dict(os.environ, self.config, clear=True):
            for status in (200, 401):
                self.status = status
                try:
                    module['lookup_registration']('WR557.S6.J1', time.monotonic() + 3)
                except ValueError:
                    self.assertEqual(status, 401)
                self.assertIs(signal.getsignal(signal.SIGALRM), handler)
                delay, interval = signal.getitimer(signal.ITIMER_REAL)
                self.assertGreater(delay, 15)
                self.assertEqual(interval, 2)
            self.status = 200
            clock = time.monotonic()
            with patch('time.monotonic', side_effect=[clock, clock, clock + 4, clock + 4]):
                with self.assertRaises(TimeoutError):
                    module['lookup_registration']('WR557.S6.J1', clock + 3)

    def test_missing_token_refuses_at_hook_boundary(self):
        self.config.pop('CARR_MCP_LOCAL_TOKEN')
        result = self.run_hook(prompt='Card: WR557.S6.J1')
        self.assertEqual(result.returncode, 2)
        self.assertIn('credential', result.stderr)
        self.assertIn('WR557.S6.J1', result.stderr)
        self.assertEqual(self.calls, [])

    def test_same_repository_worktree_accepts(self):
        self.register_fixture()
        subprocess.run(['git', '-c', 'user.name=Synthetic', '-c', 'user.email=synthetic@example.invalid',
                        'commit', '-q', '--allow-empty', '-m', 'fixture'], cwd=self.root,
                       check=True, env=fixture_env())
        worktree = self.root / 'worktree'
        subprocess.run(['git', 'worktree', 'add', '-q', '--detach', str(worktree)],
                       cwd=self.root, check=True, env=fixture_env())
        self.assertEqual(self.run_hook(cwd=worktree, prompt='Card: WR557.S6.J1').returncode, 0)

    def test_dispatch_wrapper_forwards_without_seed(self):
        wrapper = self.root / 'tools/call-verb.py'
        shutil.copyfile(REPO / 'tools/call-verb.py', wrapper)
        (self.root / 'mcp-server').mkdir()
        fake = self.root / 'mcp-server/local-verb.mjs'
        fake.write_text('import sys,json\nprint(json.dumps([sys.argv[1:],sys.stdin.read()]))\nraise SystemExit(7)\n')
        code = (f'import runpy,sys; m=runpy.run_path({str(wrapper)!r}); '
                'm["main"].__globals__["NODE_CANDIDATES"]=[sys.executable]; '
                'sys.argv=["call-verb.py","dispatch-job","-","extra"]; m["main"]()')
        result = subprocess.run([sys.executable, '-c', code], input='{"synthetic":true}',
                                capture_output=True, text=True, env=self.config, timeout=10)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(json.loads(result.stdout), [['dispatch-job', '-', 'extra'], '{"synthetic":true}'])
        self.assertFalse((self.home / '.cache').exists())


if __name__ == '__main__':
    unittest.main()
