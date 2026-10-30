"""Synthetic recordings through the Studio's intake and processing interfaces."""
import hashlib
import http.client
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import wave
from unittest.mock import patch
from http.server import ThreadingHTTPServer

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

    def test_http_transfer_requires_studio_identity_and_matching_content(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), phone_intake.handler_for(self.root, 'synthetic-service-secret'))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            def send(auth):
                client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                client.request('PUT', f"/v1/call-sessions/{self.metadata['session_id']}/transfer", self.audio,
                    {'Authorization': auth, 'Content-Type': 'audio/wav', 'X-Carr-Call': json.dumps(self.metadata)})
                response = client.getresponse()
                status, body = response.status, json.loads(response.read())
                client.close()
                return status, body
            self.assertEqual(send('Bearer outsider')[0], 401)
            self.assertFalse(list(self.root.glob('phone-*')))
            status, receipt = send('Bearer synthetic-service-secret')
            self.assertEqual(status, 200)
            self.assertEqual(receipt['sha256'], self.metadata['sha256'])
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

    def test_network_blips_do_not_spend_attempts(self):
        phone_intake.receive(self.root, self.metadata, io.BytesIO(self.audio))
        session = self.root / f"phone-{self.metadata['session_id']}"
        phone_intake.post_call.write_private(session / phone_intake.post_call.REPORT_FILE, {'report': {}})
        bridge = phone_intake.bridge_module()
        replies = [(503, {})] * 5 + [(200, {'session_token': 'fixture'}), (200, {'ok': True})]
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
             patch.object(phone_intake, 'ThreadingHTTPServer'), \
             patch.object(phone_intake.os, '_exit', side_effect=FatalExit) as exit_process:
            with self.assertRaises(FatalExit):
                phone_intake.serve(self.root, 0, {}, 'fixture')
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


if __name__ == '__main__':
    unittest.main()
