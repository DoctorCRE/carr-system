import copy
import contextlib
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import job_watchdog as w

FIXTURES = ROOT / "tools/fixtures/job-watchdog"
INDEX = "https://developers.openai.com/api/reference/llms.txt"
RECAP = "https://openai.com/index/devday-2026-recap/"
DOC = "https://developers.openai.com/api/reference/decisions"


@contextlib.contextmanager
def document_server(*, body=b'x' * 20, drip=0, header_delay=0, redirects=0):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            time.sleep(header_delay)
            remaining = int(self.path.strip('/') or redirects)
            if remaining:
                self.send_response(302)
                self.send_header('Location', '/' + str(remaining - 1))
                self.end_headers()
                return
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            try:
                for byte in body:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    time.sleep(drip)
            except (BrokenPipeError, ConnectionResetError):
                pass  # Deadline cancellation disconnects the test client.

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01})
    thread.start()
    try:
        yield 'http://127.0.0.1:' + str(server.server_port) + '/'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class VendorWatchTests(unittest.TestCase):
    def setUp(self):
        publication = patch.dict(os.environ, {"PROGRESS_BOARD_LOCAL_ONLY": "1"})
        publication.start()
        self.addCleanup(publication.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = copy.deepcopy(w.load_config(ROOT / "ops/config/job-watchdog.json"))
        self.config["actions"]["file_defects"] = False
        self.calls = []

    def fetch(self, url, timeout, max_bytes):
        self.calls.append(url)
        if url == INDEX and getattr(self, "error", False):
            raise OSError(json.loads((FIXTURES / "vendor-fetch-error.json").read_text())["error"])
        name = "vendor-recap.html" if url == RECAP else getattr(self, "index", "vendor-index-before.txt")
        return (FIXTURES / name).read_text()

    def check(self, now):
        return w.vendor_release_findings(self.root, self.config, now, fetch=self.fetch)

    def report(self, found, now):
        return w.reconcile(self.root, self.config, found, w.Effects(self.root, self.config), now, complete=False)

    def test_no_reference_and_hourly_boundary(self):
        self.assertEqual(self.check(1000), [])
        self.assertEqual(self.calls, [INDEX, RECAP])
        self.assertEqual(self.check(4599), [])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.check(4600), [])
        self.assertEqual(len(self.calls), 4)

    def test_first_reference_board_and_durable_repeat(self):
        self.index = "vendor-index-live.txt"
        found = self.check(1000)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["kind"], "decisions_docs_live")
        self.assertEqual(found[0]["urls"], [DOC])
        self.assertEqual(found[0]["url"], DOC)
        self.assertEqual(found[0]["owner"], "orchestrator")
        self.report(found, 1000)
        board = json.loads((self.root / "out/boards/carr-v5.json").read_text())
        self.assertEqual(board["tasks"][found[0]["card"]]["status"], "question-for-orchestrator")
        self.assertEqual(self.check(1001), [])
        self.assertEqual(self.check(4600), [])
        w.reconcile(self.root, self.config, [], w.Effects(self.root, self.config), 4700)
        self.assertEqual(self.check(8200), [])

    def test_fetch_failure_is_finding_and_recovery_clears(self):
        self.error = True
        found = self.check(1000)
        self.assertEqual([f["kind"] for f in found], ["vendor_release_fetch_error"])
        self.assertIn("503", found[0]["reason"])
        self.assertEqual(found[0]["url"], INDEX)
        self.assertEqual(self.calls, [INDEX, RECAP])
        self.report(found, 1000)
        self.assertEqual(self.check(1001)[0]["kind"], "vendor_release_fetch_error")
        self.error = False
        self.assertEqual(self.check(4600), [])
        self.assertEqual(w.reconcile(self.root, self.config, [], w.Effects(self.root, self.config), 4600), [])
        rows = w.read_latest(self.root / self.config["paths"]["findings"])
        self.assertIsNotNone(rows[found[0]["key"]]["cleared_at"])
        board = json.loads((self.root / 'out/boards/carr-v5.json').read_text())
        self.assertEqual(len(board['tasks']), 1)
        card = next(iter(board['tasks'].values()))
        self.assertEqual(card['status'], 'done')
        self.assertNotEqual(card.get('health'), 'blocked')
        self.assertNotIn('blocked_reason', card)
        self.assertNotIn('next_action', card)
        self.assertNotIn('restore', card['note'].lower())
        self.assertIn('recovered', card['note'].lower())

    def test_failed_reporting_retries_without_losing_detection(self):
        self.index = "vendor-index-live.txt"
        found = self.check(1000)
        with patch.object(w.Effects, "report", side_effect=RuntimeError("board unavailable")):
            self.report(found, 1000)
        pending = self.check(1001)
        self.assertEqual(pending[0]["kind"], "decisions_docs_live")
        self.report(pending, 1001)
        self.assertEqual(self.check(1002), [])

    def test_recovery_closes_durable_loop_and_recurrence_files_new_incident(self):
        self.config['actions']['file_defects'] = True
        loops, creates, closes = {}, {}, []
        def record(argv, config):
            verb, payload = argv[2], json.loads(argv[3])
            if verb == 'add-loop':
                key = payload['idempotency_key']
                if key not in creates:
                    loop_id = 'loop-' + str(len(loops) + 1)
                    creates[key] = loop_id
                    loops[loop_id] = {'loop_id': loop_id, 'version': 1, 'status': 'open'}
                return json.dumps({'ok': True, 'loop_id': creates[key]})
            if verb == 'read-loop':
                return json.dumps({'loop': loops[payload['loop_id']], 'amended': False, 'amendments': []})
            if verb == 'close-loop':
                closes.append(payload)
                loop = loops[payload['loop_id']]
                self.assertEqual(payload['base_version'], loop['version'])
                loop.update(status='done', version=loop['version'] + 1)
                return json.dumps({'ok': True})
            raise AssertionError(verb)

        with patch.object(w, 'command', side_effect=record):
            self.error = True
            found = self.check(1000)
            self.report(found, 1000)
            rows = w.read_latest(self.root / self.config['paths']['findings'])
            self.assertEqual(rows[found[0]['key']].get('loop_id'), 'loop-1')
            self.error = False
            w.reconcile(self.root, self.config, self.check(4600), w.Effects(self.root, self.config), 4600)
            self.assertEqual(loops['loop-1']['status'], 'done')
            board = json.loads((self.root / 'out/boards/carr-v5.json').read_text())
            card = next(iter(board['tasks'].values()))
            self.assertEqual(card['status'], 'done')
            self.assertNotEqual(card.get('health'), 'blocked')
            self.assertNotIn('blocked_reason', card)
            self.assertNotIn('next_action', card)
            rows = w.read_latest(self.root / self.config['paths']['findings'])
            self.assertIsNotNone(rows[found[0]['key']]['cleared_at'])
            self.assertTrue(rows[found[0]['key']]['recovery_reported'])
            self.assertEqual(closes[0]['resolution'], 'done')
            self.assertIn('recovered', closes[0]['outcome'].lower())
            self.error = True
            self.report(self.check(8200), 8200)
            self.assertEqual(len(loops), 2)
            self.assertEqual(loops['loop-2']['status'], 'open')
            self.error = False
            w.reconcile(self.root, self.config, self.check(11800), w.Effects(self.root, self.config), 11800)
            self.assertEqual(loops['loop-2']['status'], 'done')

    def test_recovery_reporting_failure_remains_pending_until_retry(self):
        self.error = True
        found = self.check(1000)
        self.report(found, 1000)
        self.error = False
        effects = w.Effects(self.root, self.config)
        with patch.object(w, 'board_task', side_effect=RuntimeError('board unavailable')):
            pending = w.reconcile(self.root, self.config, self.check(4600), effects, 4600)
        self.assertEqual(len(pending), 1)
        self.assertIn('board unavailable', pending[0]['reason'])
        rows = w.read_latest(self.root / self.config['paths']['findings'])
        self.assertIsNone(rows[found[0]['key']].get('cleared_at'))
        self.assertEqual(w.reconcile(self.root, self.config, self.check(4601), effects, 4601), [])
        rows = w.read_latest(self.root / self.config['paths']['findings'])
        self.assertIsNotNone(rows[found[0]['key']]['cleared_at'])

    def test_recovery_refuses_missing_or_invalid_nested_loop(self):
        record = {'loop_id': 'source-error-loop', 'version': 4, 'status': 'open'}
        invalid = [None, [], {}, {**record, 'loop_id': 'other-loop'},
                   {**record, 'version': True}, {**record, 'version': 0},
                   {**record, 'status': 'unknown'}]
        for loop in invalid:
            # Outer record-like fields must never substitute for the nested record.
            response = {**record, 'loop': loop, 'amended': False, 'amendments': []}
            with self.subTest(loop=loop), patch.object(w.Effects, '_record', side_effect=[response, {'ok': True}]) as read, patch.object(w, 'board_task') as board:
                with self.assertRaisesRegex(RuntimeError, 'recovery read'):
                    w.Effects(self.root, self.config).resolve({'loop_id': record['loop_id'], 'url': INDEX, 'subject': 'source-error'})
                read.assert_called_once_with('read-loop', {'loop_id': record['loop_id']})
                board.assert_not_called()

    def test_pre_fix_cleared_ledger_reconciles_stale_board(self):
        self.error = True
        found = self.check(1000)
        self.report(found, 1000)
        # The old code cleared only the ledger, leaving its blocked card.
        w.append(self.root / self.config['paths']['findings'],
                 {'key': found[0]['key'], 'cleared_at': w.stamp(4600)})
        self.error = False
        w.reconcile(self.root, self.config, self.check(4600), w.Effects(self.root, self.config), 4600)
        board = json.loads((self.root / 'out/boards/carr-v5.json').read_text())
        self.assertEqual(next(iter(board['tasks'].values()))['status'], 'done')

    def test_loop_close_refusal_keeps_board_blocked_then_retries(self):
        self.config['actions']['file_defects'] = True
        closed, refuse = [], [True]
        def record(argv, config):
            verb, payload = argv[2], json.loads(argv[3])
            if verb == 'add-loop':
                return json.dumps({'ok': True, 'loop_id': 'source-error-loop'})
            if verb == 'read-loop':
                return json.dumps({'loop': {'loop_id': 'source-error-loop', 'version': 4,
                                            'status': 'done' if closed else 'open'},
                                   'amended': False, 'amendments': []})
            if verb == 'close-loop':
                if refuse[0]:
                    return json.dumps({'ok': False, 'error': 'temporary refusal'})
                closed.append(payload)
                return json.dumps({'ok': True})
            raise AssertionError(verb)
        with patch.object(w, 'command', side_effect=record):
            self.error = True
            found = self.check(1000)
            self.report(found, 1000)
            self.error = False
            effects = w.Effects(self.root, self.config)
            pending = w.reconcile(self.root, self.config, self.check(4600), effects, 4600)
            self.assertIn('temporary refusal', pending[0]['reason'])
            board = json.loads((self.root / 'out/boards/carr-v5.json').read_text())
            self.assertEqual(next(iter(board['tasks'].values()))['status'], 'blocked')
            refuse[0] = False
            # Closure succeeds but board write fails: retry must read the closed
            # loop and finish the board without closing the loop a second time.
            with patch.object(w, 'board_task', side_effect=RuntimeError('board unavailable')):
                w.reconcile(self.root, self.config, self.check(4601), effects, 4601)
            self.assertEqual(w.reconcile(self.root, self.config, self.check(4602), effects, 4602), [])
        self.assertEqual(len(closed), 1)
        rows = w.read_latest(self.root / self.config['paths']['findings'])
        self.assertIsNotNone(rows[found[0]['key']]['cleared_at'])

    def test_uncertain_loop_filing_recovers_same_incident_without_duplicate(self):
        self.config['actions']['file_defects'] = True
        creates, calls = {}, []
        def record(argv, config):
            verb, payload = argv[2], json.loads(argv[3])
            calls.append((verb, payload))
            if verb == 'add-loop':
                if not payload['source_note'].startswith('job watchdog: openai-decisions:'):
                    return json.dumps({'ok': True, 'loop_id': 'reporting-error-loop'})
                key = payload['idempotency_key']
                if key not in creates:
                    creates[key] = 'loop-' + str(len(creates) + 1)
                    raise RuntimeError('response lost after filing')
                return json.dumps({'ok': True, 'loop_id': creates[key]})
            if verb == 'read-loop':
                return json.dumps({'loop': {'loop_id': payload['loop_id'], 'version': 1, 'status': 'open'},
                                   'amended': False, 'amendments': []})
            if verb == 'close-loop':
                return json.dumps({'ok': True})
            raise AssertionError(verb)
        with patch.object(w, 'command', side_effect=record):
            self.error = True
            self.report(self.check(1000), 1000)
            self.assertEqual(len(creates), 1)
            self.error = False
            w.reconcile(self.root, self.config, self.check(4600), w.Effects(self.root, self.config), 4600)
        self.assertEqual(len(creates), 1)
        closes = [p for verb, p in calls if verb == 'close-loop']
        self.assertEqual([p['loop_id'] for p in closes], ['loop-1'])

    def test_changed_url_set_and_return_to_old_set(self):
        self.index = "vendor-index-live.txt"
        self.report(self.check(1000), 1000)
        def changed(url, timeout, max_bytes):
            text = self.fetch(url, timeout, max_bytes)
            return text + "\nhttps://developers.openai.com/api/reference/decisions/create\n" if url == INDEX else text
        found = w.vendor_release_findings(self.root, self.config, 4600, fetch=changed)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["urls"], [DOC, DOC + "/create"])
        self.report(found, 4600)
        self.assertEqual(self.check(8200), [])

    def test_scan_wires_watch_without_model_calls(self):
        self.index = "vendor-index-live.txt"
        with patch.object(w, "collect", return_value={"errors": []}), patch.object(w, "fetch_document", self.fetch), patch.object(w.Effects, "act", side_effect=AssertionError("watch must only report")):
            self.assertEqual(w.scan(self.root, self.config), 0)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(w.read_latest(self.root / self.config["paths"]["findings"])), 1)

    def test_reference_entry_label_and_announcement_reference_link(self):
        watch = self.config["vendor_release_watches"][0]
        text = '[Decisions](https://developers.openai.com/api/reference/resources/new-resource.md)'
        self.assertEqual(w.release_reference_urls(text, INDEX, watch),
                         ['https://developers.openai.com/api/reference/resources/new-resource.md'])
        self.assertEqual(w.release_reference_urls(f'<a href="{DOC}.md">API reference</a>', RECAP, watch), [DOC + '.md'])
        self.assertEqual(w.release_reference_urls('<a href="/api/reference/decisions.md">Docs</a>', INDEX, watch),
                         ['https://developers.openai.com/api/reference/decisions.md'])
        self.assertEqual(w.release_reference_urls('https://developers.openai.com.evil.invalid/api/reference/decisions', INDEX, watch), [])

    def test_vendor_only_cli_fetch_failure_has_no_agent_actions(self):
        self.error = True
        # This clock loses precision when rendered to the ledger's ISO timestamp.
        with patch.object(w, 'collect', side_effect=AssertionError('vendor-only must not collect PRs')), patch.object(w, 'fetch_document', self.fetch), patch.object(w.Effects, 'act', side_effect=AssertionError('no model actions')), patch.object(w.time, 'time', return_value=1790736000.1234562):
            self.assertEqual(w.scan(self.root, self.config, vendor_only=True), 1)
        rows = w.read_latest(self.root / self.config['paths']['findings'])
        self.assertEqual([f['kind'] for f in rows.values()], ['vendor_release_fetch_error'])
        w.append(self.root / self.config['paths']['findings'],
                 {'key': 'unrelated', 'kind': 'job_dead', 'reported': True, 'reason': 'unrelated job', 'next_action': 'inspect'})
        self.error = False
        with patch.object(w, 'fetch_document', self.fetch), patch.object(w, 'time') as clock:
            state = w.read_latest(self.root / self.config['paths']['vendor_release_ledger'])
            clock.time.return_value = state['openai-decisions']['checked_at'] + 3600
            self.assertEqual(w.scan(self.root, self.config, vendor_only=True), 0)
        rows = w.read_latest(self.root / self.config['paths']['findings'])
        error = next(f for f in rows.values() if f['kind'] == 'vendor_release_fetch_error')
        self.assertIsNotNone(error.get('cleared_at'))
        self.assertIsNone(rows['unrelated'].get('cleared_at'))

    def test_transport_enforces_size_and_timeout(self):
        with document_server(body=b'hello') as url:
            self.assertEqual(w.fetch_document(url, 2, 5), 'hello')
        with document_server(body=b'x' * 11) as url:
            with self.assertRaisesRegex(ValueError, 'size limit'):
                w.fetch_document(url, 2, 10)

    def test_transport_deadline_interrupts_continuously_dripping_body(self):
        with document_server(drip=0.04) as url:
            started = time.monotonic()
            children = []
            popen = w.subprocess.Popen
            def start(*args, **kwargs):
                child = popen(*args, **kwargs)
                children.append(child)
                return child
            with patch.object(w.subprocess, 'Popen', side_effect=start):
                with self.assertRaises(TimeoutError):
                    w.fetch_document(url, 0.2, 100)
            self.assertLess(time.monotonic() - started, 0.6)
            self.assertEqual(len(children), 1)
            self.assertIsNotNone(children[0].returncode)

    def test_transport_redirects_share_one_elapsed_deadline(self):
        with document_server(header_delay=0.08, redirects=3) as url:
            started = time.monotonic()
            with self.assertRaises(TimeoutError):
                w.fetch_document(url, 0.2, 100)
            self.assertLess(time.monotonic() - started, 0.6)

    def test_transport_deadline_works_from_worker_thread(self):
        with document_server(drip=0.04) as url:
            errors = []
            def fetch():
                try:
                    w.fetch_document(url, 0.2, 100)
                except Exception as exc:
                    errors.append(exc)
            thread = threading.Thread(target=fetch)
            thread.start()
            thread.join(timeout=0.6)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], TimeoutError)

    def test_other_vendor_uses_only_configuration(self):
        watch = self.config['vendor_release_watches'][0]
        watch.update(id='vendor-feature', match='new-feature', finding_kind='vendor_feature_live',
                     card='vendor-feature', sources=['https://docs.example.org/api/index.txt'],
                     reference_prefixes=['https://docs.example.org/api/'])
        found = w.vendor_release_findings(self.root, self.config, 1000,
                                         fetch=lambda *args: 'https://docs.example.org/api/new-feature')
        self.assertEqual([f['kind'] for f in found], ['vendor_feature_live'])


if __name__ == "__main__":
    unittest.main()
