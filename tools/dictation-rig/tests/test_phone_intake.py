"""Synthetic recordings through the Studio's intake and processing interfaces."""
import hashlib
import importlib.util
import http.client
import io
import json
import os
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


def test_bridge_paths_are_isolated(tmp_path):
    bridge = phone_intake.bridge_module()
    assert bridge.LOG_PATH == tmp_path / 'capture-bridge.log'
    assert bridge.CONFIG_PATH == tmp_path / 'capture-config.json'


class PhoneIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
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
        decoder.write_text(f'#!{sys.executable}\nimport os,shutil,sys\nassert not os.environ.get("SYNTHETIC_UNRELATED_SECRET")\nshutil.copyfile(sys.argv[sys.argv.index("-i")+1],sys.argv[-1])\n')
        whisper = self.root / 'whisper'
        whisper.write_text(f'#!{sys.executable}\nimport json,os,sys\nassert not os.environ.get("SYNTHETIC_UNRELATED_SECRET")\njson.dump({{"transcription":[{{"offsets":{{"from":0,"to":1000}},"text":"Synthetic Clinic needs a revised proposal."}}]}},open(sys.argv[sys.argv.index("-of")+1]+".json","w"))\n')
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

        with patch.dict('os.environ', {'CARR_WHISPER_MODEL': str(model), 'SYNTHETIC_UNRELATED_SECRET': 'fixture'}), patch.object(transcribe_session, 'WHISPER_CLI_BIN', str(whisper)):
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


if __name__ == '__main__':
    unittest.main()
