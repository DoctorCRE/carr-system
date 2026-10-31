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
            jev_question = hashlib.sha256(f'{session.name}:jev_partial_unavailable'.encode()).hexdigest()[:16]
            report = {'session': session.name, 'report': {'summary': 'private summary'}, 'review_questions': [
                {'id': jev_question, 'question': 'Automatic Jev checks did not run', 'resolved': False},
                {'id': 'other', 'question': 'Review the closure?', 'resolved': False}, {'id': 'q2', 'question': 'resolved question', 'resolved': True}]}
            post_call.write_private(session / post_call.REPORT_FILE, report)
            report['checks_hold'] = 'Jev daily dollar budget is exhausted.'
            post_call.write_private(session / post_call.REPORT_FILE, report)
            phone_intake.process_pending(self.root, cfg, 'synthetic-service-secret')
            self.assertEqual(updates[-1], {'state': 'held_review', 'reason': 'budget', 'open_questions': 2,
                                           'review_questions': [{'id': jev_question, 'question': 'Automatic Jev checks did not run'},
                                                                {'id': 'other', 'question': 'Review the closure?'}]})
            post_call.resolve_question(session, jev_question, 'Reviewed the unchecked items')
            report = post_call.read_json(session / post_call.REPORT_FILE)
            fail[0] = True
            phone_intake.process_pending(self.root, cfg, 'synthetic-service-secret')
            fail[0] = False
            phone_intake.process_pending(self.root, cfg, 'synthetic-service-secret')
            self.assertEqual(updates[-2:], [{'state': 'held_review', 'reason': 'open_review_questions', 'open_questions': 1,
                                           'review_questions': [{'id': 'other', 'question': 'Review the closure?'}]}] * 2)
            post_call.resolve_question(session, 'other', 'Reviewed the remaining question')
            phone_intake.process_pending(self.root, cfg, 'synthetic-service-secret')
            self.assertEqual(updates[-1], {'state': None, 'reason': None, 'open_questions': 0, 'review_questions': []})
        payloads = json.dumps(updates)
        self.assertNotIn('private', payloads)
        self.assertEqual(output.getvalue(), '')
        self.assertEqual((session / 'phone-status.json').stat().st_mode & 0o777, 0o600)

    def test_budget_reset_resumes_checks_and_clears_filed_phone_status(self):
        import call_budget
        import post_call
        import post_call_jev
        from datetime import datetime, timezone
        from types import SimpleNamespace
        from unittest.mock import Mock

        phone_intake.receive(self.root, self.metadata, io.BytesIO(self.audio))
        session = self.root / f"phone-{self.metadata['session_id']}"
        transcript = {'segments': [{'speaker': 'Joe', 'text': 'Review the proposal'}]}
        context = {'session': session.name, 'workspace_kind': 'all',
                   'generated_at': '2026-01-01T12:00:00Z', 'deals': []}
        post_call.store_context(session, context)
        post_call.write_private(session / 'transcript.json', transcript)
        report = {'session': session.name, 'report': {'summary': 'Review proposals', 'open_questions': []},
                  'joe_tasks': [{'title': 'Review proposal', 'evidence': 'Review the proposal'}] * 2,
                  'dell_tasks': [], 'deal_updates': [], 'draft_proposals': [], 'review_questions': []}
        report['joe_tasks'] = [dict(item) for item in report['joe_tasks']]
        client = Mock()
        client.system_one.return_value = SimpleNamespace(answers={
            'deal_match': SimpleNamespace(choice='none', confidence=0.9),
            'deal_in_candidates': SimpleNamespace(noul=0.9),
            'speaker_right': SimpleNamespace(noul=0.9),
            'details_supported': SimpleNamespace(noul=0.9)})
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        bridge = phone_intake.bridge_module()
        updates = []

        def http(_method, url, body, _headers):
            self.assertTrue(url.endswith('/status'))
            updates.append(body)
            return 200, {'ok': True}

        with (patch.dict('os.environ', {'CALL_JEV_DAILY_DOLLARS': '0.005506'}),
              patch.object(call_budget, 'datetime') as clock,
              patch.object(post_call_jev, 'TypeSafeClient', return_value=client),
              patch.object(post_call, 'call_record_command', return_value=(True, {'deals': []})),
              patch.object(phone_intake, 'bridge_module', return_value=bridge),
              patch.object(bridge, 'http_request', side_effect=http)):
            clock.now.return_value = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
            ledger = self.root / '.jev-budget.json'
            call_budget.reserve(ledger)
            post_call_jev.check_distillation(report, context, transcript, client=client, budget_ledger=ledger)
            post_call.write_private(session / post_call.REPORT_FILE, report)
            post_call.process_session(session)
            self.assertIsNone(post_call.context_from_session(session))
            post_call.write_private(session / post_call.STATUS_FILE, {'state': 'filed'})
            post_call.write_private(session / 'ingested.json', {'ok': True})
            phone_intake.process_pending(self.root, {'base_url': 'https://api.example.test'}, 'synthetic')
            self.assertEqual(updates[-1]['state'], 'held_review')
            self.assertEqual(updates[-1]['reason'], 'budget')
            self.assertEqual(updates[-1]['open_questions'], 1)
            self.assertEqual(len(updates[-1]['review_questions']), 1)
            self.assertEqual(client.system_one.call_count, 1)
            self.assertEqual(len(post_call.read_json(session / post_call.REPORT_FILE)['review_questions']), 1)
            clock.now.return_value = datetime(2026, 1, 2, 6, tzinfo=timezone.utc)
            phone_intake.process_pending(self.root, {'base_url': 'https://api.example.test'}, 'synthetic')
            self.assertEqual(updates[-1], {'state': None, 'reason': None, 'open_questions': 0, 'review_questions': []})
            self.assertEqual(client.system_one.call_count, 2)
            phone_intake.process_pending(self.root, {'base_url': 'https://api.example.test'}, 'synthetic')
            self.assertEqual(client.system_one.call_count, 2)
        cleared = post_call.read_json(session / post_call.REPORT_FILE)
        self.assertNotIn('checks_hold', cleared)
        self.assertNotIn('checks_hold_day', cleared)
        self.assertEqual(cleared['report']['open_questions'], [])
        self.assertEqual(post_call.read_json(session / post_call.STATUS_FILE)['state'], 'filed')

    def budget_held_phone(self):
        import call_budget
        import post_call
        import post_call_jev
        from datetime import datetime, timezone

        phone_intake.receive(self.root, self.metadata, io.BytesIO(self.audio))
        session = self.root / f"phone-{self.metadata['session_id']}"
        transcript = {'segments': [{'speaker': 'Joe', 'text': 'Review the proposal'}]}
        report = {'session': session.name, 'report': {'open_questions': []},
                  'joe_tasks': [{'title': 'Review proposal', 'evidence': 'Review the proposal'}],
                  'review_questions': []}
        with (patch.dict('os.environ', {'CALL_JEV_DAILY_DOLLARS': '0.001'}),
              patch.object(call_budget, 'datetime') as clock):
            clock.now.return_value = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
            post_call_jev.check_distillation(report, {'deals': []}, transcript,
                                            budget_ledger=self.root / '.jev-budget.json')
        post_call.write_private(session / 'transcript.json', transcript)
        post_call.write_private(session / post_call.REPORT_FILE, report)
        post_call.write_private(session / post_call.STATUS_FILE, {'state': 'filed'})
        post_call.write_private(session / 'ingested.json', {'ok': True})
        return session

    def test_resume_error_keeps_processor_alive_and_other_calls_processing(self):
        import call_budget
        import post_call
        from datetime import datetime, timezone
        from unittest.mock import Mock

        session = self.budget_held_phone()
        original = post_call.read_json(session / post_call.REPORT_FILE)
        other = self.root / 'phone-20000000-0000-0000-0000-000000000002'
        other.mkdir()
        post_call.write_private(other / 'transfer.json', {'ok': True})
        post_call.write_private(other / 'ingested.json', {'ok': True})
        visited, errors = [], []
        swept, stopped = threading.Event(), threading.Event()
        client = Mock()
        client.system_one.side_effect = KeyError('private provider details')

        def processor():
            try:
                phone_intake.process_pending(self.root, {}, 'synthetic')
                swept.set()
                stopped.wait(5)
            except Exception as exc:
                errors.append(type(exc).__name__)
                swept.set()

        with (patch.dict('os.environ', {'CALL_JEV_DAILY_DOLLARS': '100'}),
              patch.object(call_budget, 'datetime') as clock,
              patch.object(post_call, 'fetch_context', return_value={'deals': []}),
              patch.object(post_call, '_jev_fixture_client', return_value=client),
              patch.object(phone_intake, 'publish_status', side_effect=lambda path, *_: visited.append(path) or True)):
            clock.now.return_value = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
            thread = threading.Thread(target=processor)
            thread.start()
            try:
                self.assertTrue(swept.wait(5))
                self.assertEqual(errors, [])
                self.assertTrue(thread.is_alive())
                self.assertIn(other, visited)
            finally:
                stopped.set()
                thread.join(5)
        held = post_call.read_json(session / post_call.REPORT_FILE)
        self.assertEqual(held['checks_hold'], original['checks_hold'])
        self.assertEqual(held['joe_tasks'], original['joe_tasks'])
        self.assertEqual(held['deal_check'], {'status': 'unavailable', 'exception_class': 'KeyError'})
        self.assertIn('exception=KeyError', post_call.LOG_PATH.read_text())
        self.assertNotIn('private provider details', post_call.LOG_PATH.read_text())

    def test_same_chicago_budget_day_sweep_does_no_context_fetch_or_report_write(self):
        import call_budget
        import post_call
        from datetime import datetime, timezone

        session = self.budget_held_phone()
        with (patch.object(call_budget, 'datetime') as clock,
              patch.object(post_call, 'fetch_context', return_value={'deals': []}) as fetch,
              patch.object(post_call, 'write_private', wraps=post_call.write_private) as write,
              patch.object(phone_intake, 'publish_status', return_value=True)):
            # UTC has rolled over, but Chicago is still on the hold's budget day.
            clock.now.return_value = datetime(2026, 1, 2, 5, 59, tzinfo=timezone.utc)
            with patch.dict('os.environ', {'CALL_JEV_DAILY_DOLLARS': '0.001'}):
                phone_intake.process_pending(self.root, {}, 'synthetic')
            fetch.assert_not_called()
            self.assertEqual([c for c in write.call_args_list if c.args[0] == session / post_call.REPORT_FILE], [])

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
