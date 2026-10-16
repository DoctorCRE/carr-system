#!/usr/bin/env python3
import importlib.util
import json
import sys
import tempfile
import threading
import unittest
import os
import subprocess
import contextlib
import io
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class TokenTests(unittest.TestCase):
    def test_install_and_remote_program(self):
        from lib import cloudflare_tokens as cf
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                active = self.headers.get('Authorization') == 'Bearer fake-active'
                self.send_response(200 if active else 401)
                self.end_headers()
                self.wfile.write(json.dumps({'success': active, 'result': {'status': 'active'}}).encode())
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            endpoint = f'http://127.0.0.1:{server.server_port}/verify'
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / '.config/carr/tokens.env'
                def runner(command, **kwargs):
                    self.assertNotIn('fake-active', ' '.join(command))
                    self.assertEqual(command[0], 'ssh')
                    remote = command[-1].replace(cf.VERIFY_URL, endpoint)
                    return subprocess.run(remote, shell=True, env={**os.environ, 'HOME': directory}, **kwargs)
                states = cf.on_machine('studio', {'operation': 'install', 'name': cf.TOKEN_NAMES[0], 'value': 'fake-active'}, runner=runner)
                self.assertEqual(states, {cf.TOKEN_NAMES[0]: 'ok'})
                states = cf.on_machine('studio', {'operation': 'check'}, runner=runner)
                self.assertEqual(states, {cf.TOKEN_NAMES[0]: 'ok', cf.TOKEN_NAMES[1]: 'missing'})
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                path.write_text(path.read_text() + "UNRELATED='keep me'\nexport CLOUDFLARE_API_TOKEN=old\n")
                before = path.read_bytes()
                self.assertEqual(cf.install(path, cf.TOKEN_NAMES[0], 'fake-revoked', endpoint=endpoint), 'candidate_failed')
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(cf.install(path, cf.TOKEN_NAMES[0], 'fake-active', endpoint=endpoint), 'ok')
                self.assertEqual(cf.read_env_file(path)['UNRELATED'], 'keep me')
                self.assertEqual(cf.install(path, 'UNRELATED', 'fake-active', endpoint=endpoint), 'invalid_input')
                self.assertEqual(cf.install(path, cf.TOKEN_NAMES[1], 'fake-active', endpoint=endpoint), 'ok')
                source_program = cf.remote_source(export=True).replace(cf.VERIFY_URL, endpoint)
                source_result = subprocess.run([sys.executable, '-c', source_program],
                                               env={**os.environ, 'HOME': directory},
                                               capture_output=True, text=True, timeout=10)
                self.assertEqual(source_result.returncode, 0, source_result.stderr)
                self.assertEqual(json.loads(source_result.stdout), dict.fromkeys(cf.TOKEN_NAMES, 'fake-active'))
                self.assertEqual(cf.install(path, cf.TOKEN_NAMES[0], 'fake-active', endpoint=endpoint), 'ok')
                path.write_text(path.read_text().replace('CLOUDFLARE_API_TOKEN=fake-active', 'CLOUDFLARE_API_TOKEN=fake-revoked'))
                refused = subprocess.run([sys.executable, '-c', source_program],
                                         env={**os.environ, 'HOME': directory},
                                         capture_output=True, text=True, timeout=10)
                self.assertNotEqual(refused.returncode, 0)
                self.assertEqual(refused.stdout, '')
        finally:
            server.shutdown()
            server.server_close()

    def test_sync_attempts_every_machine_and_redacts(self):
        from lib import cloudflare_tokens as cf
        spec = importlib.util.spec_from_file_location('cloudflare_cli', ROOT / 'tools/cloudflare-tokens.py')
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        calls = []
        def transport(machine, payload):
            calls.append(machine)
            return {payload['name']: 'unreachable' if machine == 'dell' else 'ok'}
        output = io.StringIO()
        with patch.object(cf, 'on_machine', transport), patch('sys.stdin', io.StringIO(json.dumps({cf.TOKEN_NAMES[0]: 'fake-active'}))), contextlib.redirect_stdout(output):
            self.assertEqual(cli.main(['sync', '--stdin']), 1)
        self.assertEqual(calls, ['studio', 'macbook', 'dell'])
        self.assertNotIn('fake-active', output.getvalue())
        self.assertIn('dell CLOUDFLARE_API_TOKEN unreachable', output.getvalue())

    def test_loops_deduplicate_update_and_clear_only_positive_readback(self):
        from lib import cloudflare_tokens as cf
        sys.path.insert(0, str(ROOT / 'tools'))
        import health_breach
        spec = importlib.util.spec_from_file_location('cloudflare_door', ROOT / 'ops/health-act-selftest.py')
        harness = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(harness)
        door = harness.FakeDoor()
        rows = cf.health_rows(transport=lambda machine, payload: dict.fromkeys(cf.TOKEN_NAMES, 'failed' if machine == 'studio' else 'ok'))
        findings = [{'key': 'cloudflare_token', 'subject': row['subject'], 'detail': row['line']} for row in rows if row['status'] != 'ok']
        with tempfile.TemporaryDirectory() as directory:
            receipts = Path(directory) / 'receipts.jsonl'
            def act(current, green):
                return health_breach.act(current, door, receipts_path=receipts, complete=True, green={'cloudflare_token': green})
            self.assertEqual(act(findings, set()).count('filed'), 2)
            self.assertEqual(act(findings, set()).count('unchanged'), 2)
            changed = [{**row, 'detail': row['detail'] + ' retry the sync'} for row in findings]
            self.assertEqual(act(changed, set()).count('updated'), 2)
            self.assertEqual(act([], set()).count('cleared'), 0)
            self.assertEqual(act([], {row['subject'] for row in findings}).count('cleared'), 2)
        for row in rows:
            action = health_breach.parse_action(row['line'])
            self.assertEqual(action.owner, 'orchestrator')
            self.assertTrue(action.remediation and action.verify and action.auto_clear)

    def test_verify_and_redaction(self):
        from lib import cloudflare_tokens as cf
        secret = 'fake-secret-for-http-test'
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                value = self.headers.get('Authorization')
                if value == 'Bearer fake-redirect':
                    self.send_response(302)
                    self.send_header('Location', '/must-not-follow')
                    self.end_headers()
                    return
                self.send_response(200)
                self.end_headers()
                body = {'success': True, 'result': {'status': 'active'}}
                if value == 'Bearer fake-expiring':
                    body['result']['expires_on'] = '2020-01-01T00:00:00Z'
                if value == 'Bearer fake-inactive':
                    body['result']['status'] = 'disabled'
                self.wfile.write(json.dumps(body).encode())
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            endpoint = f'http://127.0.0.1:{server.server_port}/verify'
            self.assertEqual(cf.verify(secret, endpoint=endpoint), 'ok')
            self.assertEqual(cf.verify('fake-expiring', endpoint=endpoint), 'expiring_soon')
            self.assertEqual(cf.verify('fake-inactive', endpoint=endpoint), 'failed')
            self.assertEqual(cf.verify('fake-redirect', endpoint=endpoint), 'unverifiable')
            self.assertEqual(cf.verify('', endpoint=endpoint), 'missing')
            self.assertNotIn(secret, json.dumps(cf.check_values({cf.TOKEN_NAMES[0]: secret}, endpoint=endpoint)))
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
