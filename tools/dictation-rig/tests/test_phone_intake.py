"""Synthetic recordings through the Studio's intake and processing interfaces."""
import hashlib
import importlib.util
import os
import http.client
import io
import json
import plistlib
import subprocess
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import wave
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import phone_intake
import post_call


def test_bridge_paths_are_isolated(tmp_path):
    bridge = phone_intake.bridge_module()
    assert bridge.LOG_PATH == tmp_path / 'capture-bridge.log'
    assert bridge.CONFIG_PATH == tmp_path / 'capture-config.json'


class PhoneIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        isolated_env = patch.dict('os.environ', {'HOME': str(self.root),
                                                   'CARR_CAPTURE_LOG': str(self.root / 'capture-bridge.log')})
        isolated_env.start()
        self.addCleanup(isolated_env.stop)
        log_patch = patch.object(post_call, 'LOG_PATH', self.root / 'call-mode.log')
        log_patch.start()
        self.addCleanup(log_patch.stop)
        self.audio = b'synthetic fixture only'
        self.metadata = {
            'schema_version': 1, 'session_id': '10000000-0000-0000-0000-000000000001',
            'sha256': hashlib.sha256(self.audio).hexdigest(), 'byte_count': len(self.audio),
            'duration_seconds': 1, 'mime_type': 'audio/wav', 'owner': 'joe',
            'started_at': '2026-01-01T12:00:00Z', 'consent_announced_at': '2026-01-01T12:00:00Z',
            'session_token': 'synthetic-session-token',
        }

    def test_agent_renders_released_program_standard_root_and_logs(self):
        repo = Path(__file__).resolve().parents[3]
        body = (repo / 'ops/launchd/com.carr.phone-intake.plist').read_text()
        spec = importlib.util.spec_from_file_location('phone_render', repo / 'ops/config-as-code.py')
        config = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(config)
        with patch.object(config, 'TOKENS', [('{{REPO}}', '/released/carr'),
                                             ('{{HOME}}', '/fixture/home')]), \
                patch.dict(os.environ, {'HOME': str(self.root)}):
            body = config.concrete(body)
        agent = plistlib.loads(body.encode())
        self.assertEqual(agent['ProgramArguments'], [
            '/released/carr/.venv/bin/python3',
            '/released/carr/tools/dictation-rig/bin/phone_intake.py',
            '--root', '/fixture/home/Recordings'])
        self.assertEqual(agent['Label'], 'com.carr.phone-intake')
        self.assertEqual(agent['KeepAlive'], {'SuccessfulExit': False})
        self.assertEqual(agent['ThrottleInterval'], 30)
        self.assertEqual(agent['StandardOutPath'], '/fixture/home/Library/Logs/phone-intake.log')
        self.assertEqual(agent['StandardErrorPath'], agent['StandardOutPath'])
        self.assertNotIn('CALL_STUDIO_TOKEN', body)

    def test_scoped_install_touches_only_requested_label(self):
        repo = Path(__file__).resolve().parents[3]
        sys.path.insert(0, str(repo))
        spec = importlib.util.spec_from_file_location('phone_config', repo / 'ops/config-as-code.py')
        config = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(config)
        with patch.multiple(config, HOME=str(self.root), LAUNCHD_SRC=str(self.root / 'agents'),
                            IS_PRIMARY=True), patch.object(config, 'missing_targets', return_value=[]), \
                patch.object(config, 'launchd_template_refusal', return_value=None), \
                patch.object(config, 'install_launchd_plist', return_value='loaded') as install:
            self.assertEqual(config.cmd_install_launchd(['com.carr.phone-intake'], True), 0)
        self.assertEqual(install.call_count, 1)
        self.assertEqual(install.call_args.args[0], 'com.carr.phone-intake.plist')
        with patch.object(config, 'install_launchd_plist') as install:
            self.assertEqual(config.cmd_install_launchd(['../untracked'], True), 2)
        install.assert_not_called()

    def test_startup_requires_every_machine_setting_before_touching_recordings(self):
        names = ('CALL_STUDIO_TOKEN', 'CARR_CAPTURE_BASE_URL', 'CALL_MAX_BYTES',
                 'CALL_MAX_SECONDS', 'CALL_DAILY_ADMISSIONS', 'CALL_PROCESSORS',
                 'CALL_EXTRACTION_ATTEMPTS', 'TYPESAFE_API_KEY')
        values = dict.fromkeys(names, '1')
        values['CARR_CAPTURE_BASE_URL'] = 'https://synthetic.invalid'
        config = self.root / '.config/carr/typesafe.env'
        config.parent.mkdir(parents=True)
        for missing in names:
            with self.subTest(missing=missing):
                config.write_text(''.join(f'{name}={value}\n' for name, value in values.items()
                                          if name != missing))
                config.chmod(0o600)
                result = subprocess.run([sys.executable, str(Path(phone_intake.__file__)),
                                         '--root', str(self.root / 'Recordings'), '--process'],
                                        env={'HOME': str(self.root), 'PATH': os.defpath},
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(len(result.stderr.splitlines()), 1)
                self.assertIn(missing, result.stderr)
                self.assertFalse((self.root / 'Recordings').exists())

    def test_invalid_settings_stop_without_restart(self):
        values = dict.fromkeys(('CALL_STUDIO_TOKEN', 'CARR_CAPTURE_BASE_URL', 'CALL_MAX_BYTES',
                                'CALL_MAX_SECONDS', 'CALL_DAILY_ADMISSIONS', 'CALL_PROCESSORS',
                                'CALL_EXTRACTION_ATTEMPTS', 'TYPESAFE_API_KEY'), '1')
        values['CARR_CAPTURE_BASE_URL'] = 'https://synthetic.invalid'
        config = self.root / '.config/carr/typesafe.env'
        config.parent.mkdir(parents=True)
        for name, value, mode, message in [('CALL_MAX_BYTES', 'bad', 0o600, 'CALL_MAX_BYTES'),
                                           ('CARR_CAPTURE_BASE_URL', 'http://invalid', 0o600, 'CARR_CAPTURE_BASE_URL'),
                                           ('CALL_MAX_BYTES', '1', 0o644, 'mode 600')]:
            with self.subTest(message=message):
                config.write_text(''.join(f'{key}={item}\n' for key, item in {**values, name: value}.items()))
                config.chmod(mode)
                result = subprocess.run([sys.executable, str(Path(phone_intake.__file__)),
                                         '--root', str(self.root / 'Recordings'), '--process'],
                                        env={'HOME': str(self.root), 'PATH': os.defpath},
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(len(result.stderr.splitlines()), 1)
                self.assertIn(message, result.stderr)
                self.assertFalse((self.root / 'Recordings').exists())

    def test_studio_token_is_not_in_child_environment(self):
        import runpy
        settings = dict.fromkeys(phone_intake.REQUIRED_SETTINGS, '1')
        settings.update(CALL_STUDIO_TOKEN='synthetic-token', CARR_CAPTURE_BASE_URL='https://synthetic.invalid')
        def check_child(_event, _seconds):
            child = subprocess.run([sys.executable, '-c',
                                    "import os, sys; sys.exit('CALL_STUDIO_TOKEN' in os.environ)"], check=False)
            self.assertEqual(child.returncode, 0)
            raise SystemExit
        with patch.object(sys, 'argv', ['phone_intake.py', '--root', str(self.root)]), \
                patch.dict(os.environ, {'CALL_STUDIO_TOKEN': 'inherited-token'}), \
                patch('credential_env.load_carr_tokens', return_value=settings), \
                patch('threading.Thread.start'), \
                patch('threading.Event.wait', check_child), \
                self.assertRaises(SystemExit):
            with patch('urllib.request.urlopen', side_effect=OSError('offline')):
                runpy.run_path(phone_intake.__file__, run_name='__main__')

    def test_machine_settings_load_only_named_values_and_reject_invalid_limits(self):
        settings = {'CALL_STUDIO_TOKEN': 'synthetic-token',
                    'CARR_CAPTURE_BASE_URL': 'https://synthetic.invalid',
                    'CALL_MAX_BYTES': '1024', 'CALL_MAX_SECONDS': '60',
                    'CALL_DAILY_ADMISSIONS': '5', 'CALL_PROCESSORS': '1',
                    'CALL_EXTRACTION_ATTEMPTS': '3',
                    'TYPESAFE_API_KEY': 'synthetic-key'}
        config = self.root / '.config/carr/typesafe.env'
        config.parent.mkdir(parents=True)
        def write(values):
            config.write_text(''.join(f'{name}={value}\n' for name, value in values.items()))
            config.chmod(0o600)
        with patch.dict(os.environ, {'HOME': str(self.root)}, clear=True):
            write({**settings, 'UNRELATED': 'synthetic-unrelated'})
            self.assertEqual(phone_intake.load_machine_settings(), settings)
            for name, value in [('CALL_MAX_SECONDS', '0'), ('CALL_PROCESSORS', 'oops'),
                                ('CARR_CAPTURE_BASE_URL', 'http://synthetic.invalid')]:
                write({**settings, name: value})
                with self.assertRaisesRegex(ValueError, name):
                    phone_intake.load_machine_settings()
            write(settings)
            config.chmod(0o644)
            with self.assertRaisesRegex(ValueError, 'private'):
                phone_intake.load_machine_settings()


    def test_duplicate_transfer_has_one_durable_private_session(self):
        receipt = phone_intake.receive(self.root, self.metadata, io.BytesIO(self.audio))
        replay = phone_intake.receive(self.root, self.metadata, io.BytesIO(self.audio))
        self.assertEqual(receipt, replay)
        self.assertTrue(receipt['transferred'])
        sessions = list(self.root.glob('phone-*'))
        self.assertEqual(len(sessions), 1)
        self.assertEqual((sessions[0] / 'imported.audio').read_bytes(), self.audio)
        self.assertEqual((sessions[0] / 'imported.audio').stat().st_mode & 0o777, 0o600)
        self.assertFalse((sessions[0] / 'ingested.json').exists())

    def test_hash_mismatch_never_receives_a_transfer_receipt(self):
        with self.assertRaises(ValueError):
            phone_intake.receive(self.root, self.metadata, io.BytesIO(b'x' * len(self.audio)))
        self.assertFalse(list(self.root.glob('phone-*/transfer.json')))

    def test_status_exchange_picks_up_answers_and_reports_the_local_count(self):
        import post_call
        phone_intake.receive(self.root, self.metadata, io.BytesIO(self.audio))
        session = self.root / f"phone-{self.metadata['session_id']}"
        post_call.write_private(session / post_call.REPORT_FILE, {
            'report': {'summary': 'Private summary'},
            'review_questions': [{'id': 'q1', 'question': 'Confirm closure?', 'resolved': False}],
        })
        bridge = phone_intake.bridge_module()
        updates = []

        def http(_method, _url, body, headers):
            self.assertEqual(headers['Authorization'], 'Bearer synthetic-service-secret')
            updates.append(body)
            return 200, {'ok': True, 'question_answers': [] if len(updates) == 1 else [
                {'question_id': 'q1', 'answer': 'dismiss'}]}

        cfg = {'base_url': 'https://api.example.test'}
        with patch.object(bridge, 'http_request', side_effect=http):
            self.assertTrue(phone_intake.publish_status(session, cfg, 'synthetic-service-secret', bridge))
            self.assertTrue(phone_intake.publish_status(session, cfg, 'synthetic-service-secret', bridge))
            self.assertTrue(phone_intake.publish_status(session, cfg, 'synthetic-service-secret', bridge))
        question = post_call.read_json(session / post_call.REPORT_FILE)['review_questions'][0]
        self.assertTrue(question['resolved'])
        self.assertEqual(question['resolution'], 'dismiss')
        self.assertEqual(updates[0]['review_questions'], [{'id': 'q1', 'question': 'Confirm closure?'}])
        self.assertEqual(updates[1], updates[0])
        self.assertEqual(updates[2], {'state': None, 'reason': None, 'open_questions': 0, 'review_questions': [],
                                      'answer_outcomes': [{'question_id': 'q1', 'outcome': 'applied'}]})
        self.assertNotIn('Private summary', json.dumps(updates))

    def test_locally_resolved_answer_is_acknowledged_to_the_app(self):
        import post_call
        phone_intake.receive(self.root, self.metadata, io.BytesIO(self.audio))
        session = self.root / f"phone-{self.metadata['session_id']}"
        post_call.write_private(session / post_call.REPORT_FILE, {
            'review_questions': [{'id': 'q1', 'question': 'Confirm closure?',
                                  'resolved': True, 'resolution': 'dismiss'}],
        })
        bridge = phone_intake.bridge_module()
        updates = []

        def http(_method, _url, body, _headers):
            updates.append(body)
            if len(updates) == 2:
                return 503, {}
            return 200, {'ok': True, 'question_answers': [] if 'answer_outcomes' in body else [
                {'question_id': 'q1', 'answer': 'confirm'}]}

        cfg = {'base_url': 'https://api.example.test'}
        with patch.object(bridge, 'http_request', side_effect=http):
            self.assertTrue(phone_intake.publish_status(session, cfg, 'synthetic-service-secret', bridge))
            self.assertFalse(phone_intake.publish_status(session, cfg, 'synthetic-service-secret', bridge))
            self.assertTrue(phone_intake.publish_status(session, cfg, 'synthetic-service-secret', bridge))
        self.assertEqual(updates[1], updates[2])
        self.assertEqual(updates[-1].get('answer_outcomes'), [
            {'question_id': 'q1', 'outcome': 'already_resolved'}])
        self.assertEqual(post_call.read_json(session / post_call.REPORT_FILE)[
            'review_questions'][0]['resolution'], 'dismiss')

    def test_processor_reports_questions_and_holds_without_transcript_or_summary(self):
        import post_call
        phone_intake.receive(self.root, self.metadata, io.BytesIO(self.audio))
        session = self.root / f"phone-{self.metadata['session_id']}"
        post_call.write_private(session / 'transcript.json', {'segments': [{'text': 'private transcript'}]})
        bridge = phone_intake.bridge_module()
        updates = []
        fail = [False]

        def http(_method, url, body, headers):
            self.assertEqual(headers['Authorization'], 'Bearer synthetic-service-secret')
            if url.endswith('/status'):
                updates.append(body)
                return (503, {}) if fail[0] else (200, {'ok': True})
            self.fail(f'Unexpected external route {url}')

        cfg = {'base_url': 'https://api.example.test'}
        output = io.StringIO()
        with (patch.object(phone_intake, 'bridge_module', return_value=bridge),
              patch.object(bridge, 'http_request', side_effect=http),
              patch('sys.stdout', output), patch('sys.stderr', output)):
            for state, reason in [('needs_attention', 'private error'), ('held_review', 'summary_repeats_transcript'),
                                  ('held_review', 'transcript_unavailable'), ('needs_attention', 'transient_ceiling')]:
                post_call.write_private(session / post_call.STATUS_FILE, {'state': state, 'reason': reason})
                phone_intake.process_pending(self.root, cfg, 'synthetic-service-secret')
                expected = {'state': state, 'reason': 'extraction_failed' if reason == 'private error' else reason, 'open_questions': 0}
                self.assertEqual(updates[-1], expected)
                count = len(updates)
                phone_intake.process_pending(self.root, cfg, 'synthetic-service-secret')
                self.assertEqual(len(updates), count)
            post_call.write_private(session / 'ingested.json', {'ok': True})
            post_call.write_private(session / post_call.STATUS_FILE, {'state': 'filed'})
            report = {'report': {'summary': 'private summary'}, 'review_questions': [
                {'id': 'q1', 'question': 'Review the closure?', 'resolved': False}, {'id': 'q2', 'question': 'resolved question', 'resolved': True}]}
            post_call.write_private(session / post_call.REPORT_FILE, report)
            fail[0] = True
            phone_intake.process_pending(self.root, cfg, 'synthetic-service-secret')
            self.assertEqual(post_call.read_json(session / post_call.STATUS_FILE)['state'], 'filed')
            fail[0] = False
            status = post_call.read_json(session / post_call.STATUS_FILE)
            status['next_attempt_at'] = '2000-01-01T00:00:00Z'
            post_call.write_private(session / post_call.STATUS_FILE, status)
            phone_intake.process_pending(self.root, cfg, 'synthetic-service-secret')
            self.assertEqual(updates[-2:], [{'state': 'held_review', 'reason': 'open_review_questions', 'open_questions': 1,
                                           'review_questions': [{'id': 'q1', 'question': 'Review the closure?'}]}] * 2)
            report['review_questions'][0]['resolved'] = True
            post_call.write_private(session / post_call.REPORT_FILE, report)
            phone_intake.process_pending(self.root, cfg, 'synthetic-service-secret')
            self.assertEqual(updates[-1], {'state': None, 'reason': None, 'open_questions': 0, 'review_questions': []})
        payloads = json.dumps(updates)
        self.assertNotIn('private', payloads)
        self.assertEqual(output.getvalue(), '')
        self.assertEqual((session / 'phone-status.json').stat().st_mode & 0o777, 0o600)

    def test_outbound_download_is_acknowledged_only_after_durable_hash_verification(self):
        received = []
        listed = [True]
        ack_status = [503]
        fixture = self

        class Server(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                if self.headers.get('Authorization') != 'Bearer synthetic-service-secret':
                    self.send_error(401)
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                if self.path.endswith('/audio'):
                    self.wfile.write(fixture.audio)
                else:
                    self.wfile.write(json.dumps({'sessions': [fixture.metadata] if listed[0] else []}).encode())

            def do_POST(self):
                received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                session = fixture.root / f"phone-{fixture.metadata['session_id']}"
                fixture.assertEqual((session / 'imported.audio').read_bytes(), fixture.audio)
                self.send_response(ack_status[0])
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

        server = ThreadingHTTPServer(('127.0.0.1', 0), Server)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            cfg = {'base_url': f'http://127.0.0.1:{server.server_port}'}
            self.assertEqual(phone_intake.pull_pending(self.root, cfg, 'synthetic-service-secret')['received'], 0)
            listed[0] = False
            ack_status[0] = 200
            received.clear()
            self.assertEqual(phone_intake.pull_pending(self.root, cfg, 'synthetic-service-secret')['received'], 1)
            self.assertEqual(received, [{'sha256': self.metadata['sha256']}])
            received.clear()
            listed[0] = True
            self.metadata['sha256'] = 'f' * 64
            self.assertEqual(phone_intake.pull_pending(self.root, cfg, 'synthetic-service-secret')['received'], 0)
            self.assertEqual(received, [])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_malformed_lease_does_not_stop_other_sessions(self):
        phone_intake.receive(self.root, self.metadata, io.BytesIO(self.audio))
        bad = self.root / f"phone-{self.metadata['session_id']}"
        self.metadata['session_id'] = '10000000-0000-0000-0000-000000000002'
        phone_intake.receive(self.root, self.metadata, io.BytesIO(self.audio))
        good = self.root / f"phone-{self.metadata['session_id']}"
        bridge = phone_intake.bridge_module()

        def http(_method, url, _body, _headers):
            if url.endswith('/lease'):
                return 200, [] if bad.name.removeprefix('phone-') in url else {'session_token': 'fixture'}
            return 200, {'ok': True}

        phone_intake.post_call.write_private(good / phone_intake.post_call.REPORT_FILE, {'report': {}})
        with patch.object(phone_intake, 'bridge_module', return_value=bridge), \
             patch.object(bridge, 'http_request', side_effect=http), \
             patch.object(bridge, 'publish_cmd', return_value=0), \
             patch.object(bridge, 'poll_cmd'), \
             patch.object(phone_intake, 'extract', return_value={'state': 'ready_review'}):
            result = phone_intake.process_pending(self.root, {'base_url': 'https://api.example.test'}, 'fixture')
        self.assertEqual(result, {'ready': 1, 'pending': 1})
        status = phone_intake.post_call.read_json(bad / phone_intake.post_call.STATUS_FILE)
        self.assertEqual(status['extraction_attempts'], 1)
        self.assertTrue(status['reason'])
        with patch.object(phone_intake, 'bridge_module', return_value=bridge), \
             patch.object(bridge, 'http_request', side_effect=http), \
             patch.object(bridge, 'publish_cmd', return_value=0), \
             patch.object(bridge, 'poll_cmd'), \
             patch.object(phone_intake, 'extract', return_value={'state': 'ready_review'}):
            for _ in range(4):
                phone_intake.process_pending(self.root, {'base_url': 'https://api.example.test'}, 'fixture')
        status = phone_intake.post_call.read_json(bad / phone_intake.post_call.STATUS_FILE)
        self.assertEqual(status['extraction_attempts'], 3)
        self.assertEqual(status['state'], 'needs_attention')
    def test_exhausted_extraction_posts_attention_in_the_same_sweep(self):
        import post_call
        phone_intake.receive(self.root, self.metadata, io.BytesIO(self.audio))
        session = self.root / f"phone-{self.metadata['session_id']}"
        post_call.write_private(session / 'transcript.json', {'segments': [{'text': 'private transcript'}]})
        post_call.store_context(session, {'session': session.name, 'workspace_kind': 'all',
                                          'generated_at': '2026-01-01T12:00:00Z', 'deals': []})
        post_call.write_private(session / post_call.STATUS_FILE, {'state': 'blocked', 'extraction_attempts': 2})
        fixture = self.root / 'failed_distiller.py'
        fixture.write_text('raise SystemExit(1)\n')
        bridge = phone_intake.bridge_module()
        updates = []

        def http(_method, url, body, _headers):
            if url.endswith('/status'):
                updates.append(body)
                return 200, {'ok': True}
            if url.endswith('/lease'):
                return 200, {'session_token': 'synthetic-session-token'}
            self.fail(f'Unexpected external route {url}')

        with (patch.object(phone_intake, 'bridge_module', return_value=bridge),
              patch.object(bridge, 'http_request', side_effect=http),
              patch.dict('os.environ', {'CARR_POST_CALL_DISTILLER_COMMAND': f'{sys.executable} {fixture}'})):
            result = phone_intake.process_pending(self.root, {'base_url': 'https://api.example.test'}, 'synthetic-service-secret')
        self.assertEqual(result, {'ready': 0, 'pending': 1})
        self.assertEqual(updates[-1], {'state': 'needs_attention', 'reason': 'extraction_failed', 'open_questions': 0})
        self.assertEqual(post_call.read_json(session / post_call.STATUS_FILE)['state'], 'needs_attention')

    def test_network_blips_do_not_spend_attempts(self):
        phone_intake.receive(self.root, self.metadata, io.BytesIO(self.audio))
        session = self.root / f"phone-{self.metadata['session_id']}"
        phone_intake.post_call.write_private(session / phone_intake.post_call.REPORT_FILE, {'report': {}})
        bridge = phone_intake.bridge_module()
        replies = [(503, {})] * 5 + [(200, {'ok': True}), (200, {'session_token': 'fixture'}), (200, {'ok': True})]
        with patch.object(phone_intake, 'bridge_module', return_value=bridge), \
             patch.object(bridge, 'http_request', side_effect=replies), \
             patch.object(bridge, 'publish_cmd', return_value=0), \
             patch.object(bridge, 'poll_cmd'), \
             patch.object(phone_intake, 'extract', return_value={'state': 'ready_review'}), \
             patch.object(phone_intake.post_call, 'now') as clock:
            for timestamp in ['2026-01-01T00:00:00Z', '2026-01-01T00:00:30Z',
                              '2026-01-01T00:01:30Z', '2026-01-01T00:03:30Z',
                              '2026-01-01T00:07:30Z']:
                clock.return_value = timestamp
                phone_intake.process_pending(self.root, {'base_url': 'https://api.example.test'}, 'fixture')
            status = phone_intake.post_call.read_json(session / phone_intake.post_call.STATUS_FILE)
            self.assertEqual(status.get('extraction_attempts', 0), 0)
            self.assertEqual(status['state'], 'blocked')
            clock.return_value = '2026-01-01T00:15:30Z'
            result = phone_intake.process_pending(self.root, {'base_url': 'https://api.example.test'}, 'fixture')
        self.assertEqual(result['ready'], 1)

    def test_transient_errors_expire_after_a_day(self):
        phone_intake.receive(self.root, self.metadata, io.BytesIO(self.audio))
        session = self.root / f"phone-{self.metadata['session_id']}"
        bridge = phone_intake.bridge_module()
        with patch.object(phone_intake, 'bridge_module', return_value=bridge), \
             patch.object(bridge, 'http_request', return_value=(429, {})), \
             patch.object(phone_intake.post_call, 'now') as clock:
            clock.return_value = '2026-01-01T00:00:00Z'
            phone_intake.process_pending(self.root, {'base_url': 'https://api.example.test'}, 'fixture')
            clock.return_value = '2026-01-02T00:00:00Z'
            phone_intake.process_pending(self.root, {'base_url': 'https://api.example.test'}, 'fixture')
            phone_intake.process_pending(self.root, {'base_url': 'https://api.example.test'}, 'fixture')
        status = phone_intake.post_call.read_json(session / phone_intake.post_call.STATUS_FILE)
        self.assertEqual(status['state'], 'needs_attention')
        self.assertIn('Transient network error', status['reason'])
        self.assertEqual(status.get('extraction_attempts', 0), 0)

    def test_prolonged_status_outage_preserves_review_and_filed_states_through_recovery(self):
        for state, ingested in [('held_review', False), ('filed', True), ('filed', False)]:
            with self.subTest(state=state, ingested=ingested):
                root = self.root / f'{state}-{ingested}'
                phone_intake.receive(root, self.metadata, io.BytesIO(self.audio))
                session = root / f"phone-{self.metadata['session_id']}"
                reason = 'summary_repeats_transcript' if state == 'held_review' else 'All candidates filed.'
                original = {'state': state, 'reason': reason, 'extraction_attempts': 2}
                if state == 'held_review':
                    original['reason_code'] = reason
                post_call.write_private(session / post_call.STATUS_FILE, original)
                if ingested:
                    post_call.write_private(session / 'ingested.json', {'ok': True})
                bridge = phone_intake.bridge_module()
                updates = []
                replies = iter([(503, {}), (503, {}), (503, {}), (200, {'ok': True})])

                def http(_method, url, body, _headers):
                    self.assertTrue(url.endswith('/status'), url)
                    updates.append(body)
                    return next(replies)

                with (patch.object(phone_intake, 'bridge_module', return_value=bridge),
                      patch.object(bridge, 'http_request', side_effect=http),
                      patch.object(phone_intake, 'extract') as extract,
                      patch.object(post_call, 'now') as clock):
                    for timestamp in ['2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z',
                                      '2026-01-02T01:00:00Z']:
                        clock.return_value = timestamp
                        phone_intake.process_pending(root, {'base_url': 'https://api.example.test'}, 'fixture')
                        status = post_call.read_json(session / post_call.STATUS_FILE)
                        for key, value in original.items():
                            self.assertEqual(status[key], value)
                    self.assertTrue(status['transient_expired'])
                    self.assertEqual(status['transient_since'], '2026-01-01T00:00:00Z')
                    clock.return_value = '2026-01-02T01:00:01Z'
                    phone_intake.process_pending(root, {'base_url': 'https://api.example.test'}, 'fixture')
                    phone_intake.process_pending(root, {'base_url': 'https://api.example.test'}, 'fixture')
                    extract.assert_not_called()
                recovered = post_call.read_json(session / post_call.STATUS_FILE)
                for key, value in original.items():
                    self.assertEqual(recovered[key], value)
                for key in ('transient_since', 'transient_count', 'transient_expired'):
                    self.assertNotIn(key, recovered)
                self.assertIsNone(recovered['next_attempt_at'])
                expected = {'state': state if state == 'held_review' else None,
                            'reason': reason if state == 'held_review' else None, 'open_questions': 0}
                self.assertEqual(updates, [expected] * 4)
                self.assertEqual((session / 'imported.audio').read_bytes(), self.audio)

    def test_processor_fatal_error_exits_process(self):
        class FatalExit(BaseException):
            pass

        class ImmediateThread:
            def __init__(self, *, target, args, daemon):
                self.target, self.args = target, args

            def start(self):
                self.target(*self.args)

        with patch.object(phone_intake, 'process_pending', side_effect=RuntimeError('fatal')), \
             patch.object(phone_intake.threading, 'Thread', ImmediateThread), \
             patch.object(phone_intake.os, '_exit', side_effect=FatalExit) as exit_process:
            with self.assertRaises(FatalExit):
                phone_intake.serve(self.root, {}, 'fixture')
        exit_process.assert_called_once_with(1)

    def test_imported_recording_uses_existing_extraction_and_candidate_processing(self):
        wav = io.BytesIO()
        with wave.open(wav, 'wb') as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(b'\0\0' * 16000)
        self.audio = wav.getvalue()
        self.metadata.update(byte_count=len(self.audio), sha256=hashlib.sha256(self.audio).hexdigest())
        phone_intake.receive(self.root, self.metadata, io.BytesIO(self.audio))
        session = self.root / f"phone-{self.metadata['session_id']}"
        model = self.root / 'synthetic-model'
        model.write_bytes(b'fixture model, never speech recognition')
        decoder = self.root / 'decoder'
        decoder.write_text(f'#!{sys.executable}\nimport shutil,sys\nshutil.copyfile(sys.argv[sys.argv.index("-i")+1],sys.argv[-1])\n')
        whisper = self.root / 'whisper'
        whisper.write_text(f'#!{sys.executable}\nimport json,sys\njson.dump({{"transcription":[{{"offsets":{{"from":0,"to":1000}},"text":"Synthetic Clinic needs a revised proposal."}}]}},open(sys.argv[sys.argv.index("-of")+1]+".json","w"))\n')
        decoder.chmod(0o700)
        whisper.chmod(0o700)
        import transcribe_session
        import post_call
        deal = '20000000-0000-0000-0000-000000000001'
        context = {'session': session.name, 'workspace_kind': 'all', 'generated_at': '2026-01-01T12:00:00Z',
                   'deals': [{'id': deal, 'name': 'Synthetic Clinic', 'owner': 'joe', 'operating_state': 'active', 'participants': []}]}
        post_call.store_context(session, context)

        def distiller(request):
            self.assertEqual(request['transcript']['segments'][0]['speaker'], 'Imported speaker')
            self.assertEqual(request['transcript']['segments'][0]['text'], 'Synthetic Clinic needs a revised proposal.')
            return {'schema_version': 1, 'report': {'summary': 'The proposal needs revision.', 'decisions': [], 'open_questions': []},
                    'joe_tasks': [{'title': 'Revise the proposal', 'deal_id': deal, 'participant_ids': [], 'evidence': 'needs a revised proposal'}],
                    'dell_tasks': [], 'deal_updates': [{'deal_id': deal, 'summary': 'Proposal revision discussed.',
                        'participant_ids': [], 'evidence': 'Synthetic Clinic needs a revised proposal',
                        'next_step': {'text': 'Revise proposal', 'next_date': '2026-01-02', 'needs_review': None}}],
                    'draft_proposals': [], 'review_questions': []}

        with patch.dict('os.environ', {'CARR_WHISPER_MODEL': str(model)}), patch.object(transcribe_session, 'WHISPER_CLI_BIN', str(whisper)):
            status = phone_intake.extract(session, decoder=str(decoder), distiller=distiller)
        self.assertEqual(status['state'], 'ready_review')
        report = post_call.read_json(session / post_call.REPORT_FILE)
        self.assertEqual(report['report']['summary'], 'The proposal needs revision.')
        self.assertEqual(report['deal_updates'][0]['next_step']['apply'], 'review')
        candidates = post_call.sanitized_candidates(session)
        self.assertEqual(candidates['post_call_items'][0]['assignee'], 'joe')
        self.assertEqual([i['kind'] for i in candidates['legacy_items']], ['activity', 'next_step'])
        self.assertNotIn('transcript', json.dumps(report['report']))
        self.assertFalse((session / 'ingested.json').exists())
        bridge = phone_intake.bridge_module()
        uploaded = {}

        def http(_method, url, body, _headers):
            if url.endswith('/lease'):
                return 200, {'session_token': 'synthetic-session-token'}
            if url.endswith('/candidates'):
                uploaded[url] = body
                return 200, {'candidate_ids': [f"fixture-{len(uploaded)}-{i}" for i in range(len(body['items']))]}
            if url.endswith('/status'):
                return 200, {'ok': True}
            if url.endswith('/report'):
                uploaded['narrative'] = body
                return 200, {'ok': True}
            if url.endswith('/session'):
                return 200, {'state': 'distilling', 'meeting_record': None, 'post_call': True,
                             'candidates': {'pending': 3}, 'candidate_statuses': [], 'post_call_report': {'filed': False}}
            self.fail(f'Unexpected external route {url}')

        with patch.object(phone_intake, 'bridge_module', return_value=bridge), patch.object(bridge, 'http_request', side_effect=http):
            result = phone_intake.process_pending(self.root, {'base_url': 'https://api.example.test'}, 'synthetic-service-secret')
        self.assertEqual(result['ready'], 1)
        self.assertEqual(uploaded['narrative']['summary'], 'The proposal needs revision.')
        self.assertTrue(any('/capture/post-call/candidates' in url for url in uploaded))
        self.assertTrue(any('/capture/candidates' in url for url in uploaded))
        self.assertFalse((session / 'ingested.json').exists())

    def test_limits_reject_before_reading_audio_even_with_larger_configuration(self):
        source = unittest.mock.Mock()
        with patch.dict('os.environ', {'CALL_MAX_BYTES': '999999999', 'CALL_MAX_SECONDS': '99999'}):
            for changes in ({'byte_count': 52428801}, {'duration_seconds': 3601}):
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    phone_intake.receive(self.root, {**self.metadata, **changes}, source)
        source.read.assert_not_called()
        self.assertFalse(list(self.root.glob('phone-*')))

    def test_exhausted_extraction_rejects_before_decoder_or_model_work(self):
        phone_intake.receive(self.root, self.metadata, io.BytesIO(self.audio))
        session = self.root / f"phone-{self.metadata['session_id']}"
        phone_intake.post_call.write_private(session / 'post-call.json', {'extraction_attempts': 3})
        with patch.dict('os.environ', {'CALL_EXTRACTION_ATTEMPTS': '99'}), patch.object(phone_intake.subprocess, 'run') as run:
            status = phone_intake.extract(session)
        self.assertEqual(status['state'], 'needs_attention')
        run.assert_not_called()

    def test_second_processor_slot_rejects_before_any_work(self):
        with patch.dict('os.environ', {'CALL_PROCESSORS': '99'}), patch.object(phone_intake, 'bridge_module') as bridge:
            with self.assertRaises(ValueError):
                phone_intake.process_pending(self.root, {}, 'synthetic', slot=1)
        bridge.assert_not_called()

    def test_processor_lock_refuses_overlapping_sweep(self):
        with phone_intake.exclusive(self.root / '.phone-processor-0.lock'):
            result = phone_intake.process_pending(self.root, {}, 'synthetic')
        self.assertEqual(result, {'busy': True})

    def test_failed_decode_work_stops_after_three_attempts(self):
        phone_intake.receive(self.root, self.metadata, io.BytesIO(self.audio))
        session = self.root / f"phone-{self.metadata['session_id']}"
        import transcribe_session
        with patch.object(transcribe_session, 'resolve_model', side_effect=ValueError('synthetic model unavailable')) as model:
            for _ in range(3):
                with self.assertRaises(ValueError):
                    phone_intake.extract(session)
            status = phone_intake.extract(session)
        self.assertEqual(model.call_count, 3)
        self.assertEqual(status['state'], 'needs_attention')

    def test_phone_retention_holds_unresolved_review_questions(self):
        session = self.root / f"phone-{self.metadata['session_id']}"
        session.mkdir()
        report = {'joe_tasks': [], 'dell_tasks': [], 'deal_updates': [], 'draft_proposals': [],
                  'review_questions': [{'id': 'synthetic', 'resolved': False}]}
        phone_intake.post_call.write_private(session / 'post-call-report.json', report)
        self.assertIsNone(phone_intake.post_call.retention_ready(session, 'b' * 64))
        phone_intake.post_call.resolve_question(session, 'synthetic', 'Reviewed synthetic fixture')
        self.assertEqual(phone_intake.post_call.retention_ready(session, 'b' * 64)['pending_items'], 0)


if __name__ == '__main__':
    unittest.main()
