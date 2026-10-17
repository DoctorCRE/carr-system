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
    def test_nightly_issuer_failed_readback_restores_original(self):
        self._nightly_issuer_rollback('failed')

    def test_nightly_issuer_interrupt_restores_original(self):
        self._nightly_issuer_rollback(KeyboardInterrupt())

    def _nightly_issuer_rollback(self, failure):
        spec = importlib.util.spec_from_file_location('mint_harness', ROOT / 'ops/credential-mint-selftest.py')
        harness = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = harness
        spec.loader.exec_module(harness)
        from lib import cloudflare_tokens as cf, token_store
        with harness.world(roots=('CLOUDFLARE_TOKEN_ROOT',)) as world:
            path = token_store.destination(cf.TOKEN_NAMES[0]).path
            offline = False
            candidates = []
            def transport(machine, payload, **kwargs):
                if machine == 'studio':
                    candidates.append(payload['value'])
                    return {payload['name']: cf.install(path, payload['name'], payload['value'])}
                return {payload['name']: 'unreachable' if offline else 'ok'}
            with patch.object(cf, 'on_machine', side_effect=transport):
                with patch.object(cf, 'verify', return_value='ok'):
                    world.night()
                original = path.read_bytes()
                first = world.stored(cf.TOKEN_NAMES[0])
                offline = True
                world.clock.now = harness.T0 + harness.timedelta(days=85)
                with patch.object(cf, 'verify', side_effect=['ok', failure]):
                    if isinstance(failure, BaseException):
                        with self.assertRaises(type(failure)):
                            world.night()
                    else:
                        world.night()
                self.assertEqual(path.read_bytes(), original, 'original_restored')
                backup = json.loads(Path(str(path) + '.install-backup').read_text())
                self.assertEqual(backup['original'], original.decode(), 'backup_contains_original')
                self.assertIn(first, world.http.active['cloudflare'])
                self.assertTrue(world.saved_state()['credentials']['cloudflare-deploy-token']['install_pending'])
                candidate = candidates[-1]
                offline = False
                with patch.object(cf, 'verify', return_value='ok'):
                    world.night()
                self.assertEqual(candidates[-1], candidate)
                self.assertEqual(world.stored(cf.TOKEN_NAMES[0]), candidate)
                self.assertEqual(len(world.http.minted), 2)
                self.assertNotIn(first, world.http.active['cloudflare'])

    def test_postwrite_failure_restores_private_backup(self):
        from lib import cloudflare_tokens as cf
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'tokens.env'
            original = b"CLOUDFLARE_API_TOKEN=original\r\nOTHER='keep me'\r\n"
            path.write_bytes(original)
            path.chmod(0o600)
            with patch.object(cf, 'verify', side_effect=['ok', 'failed']):
                self.assertEqual(cf.install(path, cf.TOKEN_NAMES[0], 'fake-new'), 'failed')
            self.assertEqual(path.read_bytes(), original)
            backup = Path(str(path) + '.install-backup')
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
            self.assertIn('original', backup.read_text())

    def test_verification_holds_writer_lock_and_preserves_absence(self):
        from lib import cloudflare_tokens as cf
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'tokens.env'
            calls = []
            def verify(value, **kwargs):
                probe = subprocess.run([sys.executable, '-c',
                    "import fcntl, sys; f = open(sys.argv[1], 'a'); fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)",
                    str(path) + '.rotate-credential.lock'], capture_output=True, timeout=10)
                self.assertNotEqual(probe.returncode, 0)
                calls.append(value)
                if len(calls) == 2:
                    journal = Path(str(path) + '.install-pending')
                    self.assertEqual(journal.stat().st_mode & 0o777, 0o600)
                return 'ok' if len(calls) == 1 else 'failed'
            with patch.object(cf, 'verify', side_effect=verify):
                self.assertEqual(cf.install(path, cf.TOKEN_NAMES[0], 'fake-new'), 'failed')
            self.assertFalse(path.exists())
            self.assertEqual(len(calls), 2)

    def test_interrupted_install_restores_original(self):
        from lib import cloudflare_tokens as cf
        for interruption in (KeyboardInterrupt, SystemExit):
            with self.subTest(interruption=interruption), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'tokens.env'
                original = b'CLOUDFLARE_API_TOKEN=original\n'
                path.write_bytes(original)
                with patch.object(cf, 'verify', side_effect=['ok', interruption()]):
                    with self.assertRaises(interruption):
                        cf.install(path, cf.TOKEN_NAMES[0], 'fake-new')
                self.assertEqual(path.read_bytes(), original)

    def test_process_death_recovers_before_next_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'tokens.env'
            original = b'CLOUDFLARE_API_TOKEN=original\n'
            path.write_bytes(original)
            program = """
import os, sys
from lib import cloudflare_tokens as cf
calls = 0
def verify(value, **kwargs):
    global calls
    calls += 1
    if calls == 2:
        os._exit(73)
    return 'ok'
cf.verify = verify
cf.install(sys.argv[1], cf.TOKEN_NAMES[0], 'fake-new')
"""
            result = subprocess.run([sys.executable, '-c', program, str(path)],
                                    cwd=ROOT, env={**os.environ, 'HOME': directory}, timeout=10)
            self.assertEqual(result.returncode, 73)
            from lib import cloudflare_tokens as cf
            with patch.object(cf, 'verify', return_value='failed'):
                self.assertEqual(cf.install(path, cf.TOKEN_NAMES[0], 'fake-rejected'), 'candidate_failed')
            self.assertEqual(path.read_bytes(), original)

    def test_regular_writer_recovers_before_preserving_other_keys(self):
        from lib import token_store
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'tokens.env'
            path.write_text('CLOUDFLARE_API_TOKEN=unconfirmed\n')
            journal = Path(str(path) + '.install-pending')
            journal.write_text(json.dumps({'original': 'CLOUDFLARE_API_TOKEN=original\n'}))
            journal.chmod(0o600)
            token_store.write_env_line(path, 'OTHER', 'new-other-value')
            self.assertEqual(token_store.read_env_file(path),
                             {'CLOUDFLARE_API_TOKEN': 'original', 'OTHER': 'new-other-value'})
            self.assertFalse(journal.exists())

    def test_nightly_offline_fleet_retries_before_retirement(self):
        spec = importlib.util.spec_from_file_location('mint_harness', ROOT / 'ops/credential-mint-selftest.py')
        harness = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = harness
        spec.loader.exec_module(harness)
        from lib import cloudflare_tokens as cf
        with harness.world(roots=('CLOUDFLARE_TOKEN_ROOT',)) as world:
            calls = []
            offline = False
            def transport(machine, payload, **kwargs):
                calls.append((machine, payload['value']))
                return {payload['name']: 'unreachable' if offline and machine == 'dell' else 'ok'}
            with patch.object(cf, 'on_machine', side_effect=transport):
                world.night()
                first = world.stored('CLOUDFLARE_API_TOKEN')
                offline = True
                world.clock.now = harness.T0 + harness.timedelta(days=85)
                world.night()
                self.assertIn(first, world.http.active['cloudflare'])
                entry = world.saved_state()['credentials']['cloudflare-deploy-token']
                self.assertTrue(entry['install_pending'])
                self.assertEqual(entry['previous_child_id'], 'cloudflare-id-1')
                offline = False
                calls.clear()
                world.night()
                self.assertEqual([m for m, _ in calls], list(cf.MACHINES))
                self.assertEqual(len(world.http.minted), 2)
                self.assertNotIn(first, world.http.active['cloudflare'])
                self.assertFalse(world.saved_state()['credentials']['cloudflare-deploy-token']['install_pending'])

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
        def transport(machine, payload, **kwargs):
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
