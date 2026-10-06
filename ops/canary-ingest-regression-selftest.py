#!/usr/bin/env python3
"""Synthetic HTTP, storage, scope and reconciliation regressions for PR 810."""
import contextlib
import errno
import http.client
import importlib.util
import io
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from unittest.mock import Mock, patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sink = load('canary_sink_regression', 'tools/canary-ingest-sink.py')
TOKEN = 'synthetic-token'


@contextlib.contextmanager
def serving(ledger, **kwargs):
    cls = getattr(sink, 'BoundedHTTPServer', ThreadingHTTPServer)
    server = cls(('127.0.0.1', 0), sink.make_handler(TOKEN, '/ingest', ledger),
                 **(kwargs if cls is not ThreadingHTTPServer else {}))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def post(server, body, token=TOKEN):
    conn = http.client.HTTPConnection(*server.server_address, timeout=2)
    try:
        conn.request('POST', '/ingest', body, {
            'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


def raw_post(server, body, length, auth=b'Bearer synthetic-token', extra=b''):
    with socket.create_connection(server.server_address, timeout=2) as sock:
        sock.sendall(b'POST /ingest HTTP/1.0\r\nAuthorization: ' + auth +
                     b'\r\nContent-Type: application/json\r\nContent-Length: ' +
                     str(length).encode() + b'\r\n' + extra + b'\r\n' + body)
        sock.shutdown(socket.SHUT_WR)
        response = http.client.HTTPResponse(sock)
        response.begin()
        return response.status, response.read()


class Regressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='canary-regressions-')
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.ledger = sink.Ledger(str(self.directory / 'ledger'))

    def test_1_simultaneous_http_duplicates(self):
        barrier = threading.Barrier(2)
        if hasattr(self.ledger, 'has_seen'):
            original = self.ledger.has_seen
            def simultaneous(identifier):
                result = original(identifier)
                barrier.wait(2)
                return result
            self.ledger.has_seen = simultaneous
        start = threading.Barrier(2)
        def request(_):
            start.wait(2)
            return post(server, b'{"external_id":"race"}')
        with serving(self.ledger) as server, ThreadPoolExecutor(2) as pool:
            results = list(pool.map(request, range(2)))
        self.assertEqual([r[0] for r in results], [200, 200])
        self.assertEqual(sorted(json.loads(r[1])['duplicate'] for r in results), [False, True])
        self.assertEqual(len(self.ledger.path.read_text().splitlines()), 1)

    def test_2_failed_persistence_is_retryable(self):
        full = OSError(errno.ENOSPC, 'synthetic full disk')
        with serving(self.ledger) as server:
            with patch.object(Path, 'write_text', side_effect=full), patch.object(sink.os, 'fsync', side_effect=full):
                try:
                    result = post(server, b'{"external_id":"retry"}')
                except http.client.RemoteDisconnected:
                    result = (0, b'')
            retry = post(server, b'{"external_id":"retry"}')
        self.assertEqual(result, (503, b''))
        self.assertEqual(retry, (200, b'{"duplicate": false}'))
        self.assertEqual(len(self.ledger.path.read_text().splitlines()), 1)

    def test_3_interrupted_publication_preserves_prior_receipt_on_restart(self):
        self.ledger.record('prior', b'synthetic prior')
        prior = self.ledger.path.read_bytes()
        def interrupted(path, *args, **kwargs):
            with path.open('w') as handle:
                handle.write('{')
            raise OSError(errno.EIO, 'synthetic interrupted write')
        fdopen = sink.os.fdopen
        @contextlib.contextmanager
        def interrupted_stream(*args, **kwargs):
            with fdopen(*args, **kwargs) as handle:
                class PartialWriter:
                    def write(self, text):
                        handle.write(text[:1])
                        handle.flush()
                        raise OSError(errno.EIO, 'synthetic partial write')
                yield PartialWriter()
        with patch.object(Path, 'write_text', interrupted), patch.object(sink.os, 'fdopen', interrupted_stream):
            with self.assertRaises(OSError):
                self.ledger.record('new', b'synthetic new')
        self.assertEqual(self.ledger.path.read_bytes(), prior)
        restarted = sink.Ledger(str(self.ledger.dir))
        with serving(restarted) as server:
            self.assertEqual(post(server, b'{"external_id":"prior"}'), (200, b'{"duplicate": true}'))
            self.assertEqual(post(server, b'{"external_id":"new"}'), (200, b'{"duplicate": false}'))
        self.assertEqual(sorted(p.name for p in self.ledger.dir.iterdir()), ['ledger.jsonl'])

    def test_3_failed_replace_preserves_prior_file(self):
        self.ledger.record('prior', b'synthetic')
        prior = self.ledger.path.read_bytes()
        with patch.object(sink.os, 'replace', side_effect=OSError(errno.EIO, 'synthetic replace')):
            with self.assertRaises(OSError):
                self.ledger.record('new', b'synthetic')
        self.assertEqual(self.ledger.path.read_bytes(), prior)

    def test_existing_partial_ledger_recovers_over_http_and_restart(self):
        self.ledger.record('prior', b'synthetic prior')
        prior = self.ledger.path.read_bytes()
        self.ledger.path.write_bytes(prior + b'{"external_id":')
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            recovered = sink.Ledger(str(self.ledger.dir))
            with serving(recovered) as server:
                self.assertEqual(post(server, b'{"external_id":"prior"}'),
                                 (200, b'{"duplicate": true}'))
                for identifier in ('new-one', 'new-two'):
                    body = json.dumps({'external_id': identifier,
                                       'note_text': 'private-marker'}).encode()
                    self.assertEqual(post(server, body),
                                     (200, b'{"duplicate": false}'))
            restarted = sink.Ledger(str(self.ledger.dir))
            with serving(restarted) as server:
                for identifier in ('prior', 'new-one', 'new-two'):
                    body = json.dumps({'external_id': identifier}).encode()
                    self.assertEqual(post(server, body),
                                     (200, b'{"duplicate": true}'))
        receipts = self.ledger.path.read_bytes()
        self.assertTrue(receipts.startswith(prior))
        self.assertEqual([json.loads(line)['external_id']
                          for line in receipts.splitlines()],
                         ['prior', 'new-one', 'new-two'])
        self.assertNotIn(b'private-marker', receipts)
        self.assertEqual(stdout.getvalue() + stderr.getvalue(), '')

    def test_4_negative_and_incomplete_framing(self):
        with serving(self.ledger) as server:
            body = b'{"external_id":"short"}'
            cases = [(-1, b''), (200, b''), ('invalid', b''), ('+22', b''),
                     (len(body), b'Transfer-Encoding: chunked\r\n'),
                     (len(body), b'Content-Length: 22\r\n')]
            results = [raw_post(server, body, length, extra=extra)[0] for length, extra in cases]
        self.assertEqual(results, [400] * len(cases))
        self.assertFalse(self.ledger.path.exists())

    def test_4_long_numeric_length_is_refused_before_integer_conversion(self):
        with serving(self.ledger) as server:
            try:
                result = raw_post(server, b'', '9' * 5000)
            except http.client.RemoteDisconnected:
                result = (0, b'')
        self.assertEqual(result, (413, b''))

    def test_5_overload_reset_during_connect_is_a_closed_connection(self):
        connect = socket.create_connection
        calls = 0
        def reset_on_third(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise ConnectionResetError(errno.ECONNRESET, 'synthetic overload reset')
            return connect(*args, **kwargs)
        with patch.object(socket, 'create_connection', reset_on_third):
            self.test_5_abandoned_headers_and_bodies_release_bounded_workers()

    def test_5_all_connect_rejections_cannot_pass_vacuously(self):
        with patch.object(socket, 'create_connection', side_effect=ConnectionResetError('all rejected')):
            with self.assertRaises(ConnectionResetError):
                self.test_5_abandoned_headers_and_bodies_release_bounded_workers()

    def test_5_connection_rejected_before_send_is_closed_and_workers_recover(self):
        connect = socket.create_connection
        rejected = Mock()
        rejected.sendall.side_effect = BrokenPipeError('worker limit rejected connection')
        rejected.recv.return_value = b''
        calls = 0

        def connect_or_reject(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 3:
                return rejected
            return connect(*args, **kwargs)

        with patch.object(socket, 'create_connection', side_effect=connect_or_reject):
            self.test_5_abandoned_headers_and_bodies_release_bounded_workers()
        rejected.close.assert_called_once()

    def test_5_abandoned_headers_and_bodies_release_bounded_workers(self):
        with serving(self.ledger, read_timeout=0.2, max_workers=2) as server:
            stalled = []
            occupied = threading.Event()
            release = threading.Event()
            cleanup_allowed = threading.Event()
            workers_finished = threading.Event()
            stalled_ports = set()
            shutdown = server.shutdown_request
            def delay_worker_cleanup(request):
                stalled_request = request.getpeername()[1] in stalled_ports
                shutdown(request)
                if stalled_request:
                    cleanup_allowed.wait(10)
            lock = threading.Lock()
            started = 0
            finished = 0
            handle = server.process_request_thread
            def hold_worker(*args):
                nonlocal started, finished
                with lock:
                    started += 1
                    if started == 2:
                        occupied.set()
                release.wait(10)
                handle(*args)
                with lock:
                    finished += 1
                    if finished == 2:
                        workers_finished.set()
            def assert_closed(sock):
                try:
                    while sock.recv(4096):
                        pass
                except ConnectionResetError:
                    pass
            try:
                with patch.object(server, 'process_request_thread', hold_worker), \
                        patch.object(server, 'shutdown_request', delay_worker_cleanup):
                    for i in range(2):
                        sock = socket.create_connection(server.server_address, timeout=2)
                        stalled.append(sock)
                        stalled_ports.add(sock.getsockname()[1])
                        sock.sendall(b'POST /ingest HTTP/1.0\r\n' if i == 0 else
                                     b'POST /ingest HTTP/1.0\r\nAuthorization: Bearer synthetic-token\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n{')
                    self.assertTrue(occupied.wait(2), 'both workers must be occupied before overload')
                    # All excess connections must close while the two readers
                    # remain occupied. TCP may report rejection at connect,
                    # send, or recv; all three prove the same server boundary.
                    for _ in range(6):
                        try:
                            with contextlib.closing(socket.create_connection(server.server_address, timeout=2)) as sock:
                                sock.sendall(b'POST /ingest HTTP/1.0\r\n')
                                assert_closed(sock)
                        except (ConnectionResetError, BrokenPipeError):
                            pass
                    release.set()
                    for sock in stalled:
                        assert_closed(sock)
                    # Socket closure precedes the server's semaphore release.
                    # Observe worker completion before asking for recovery.
                    cleanup_allowed.set()
                    self.assertTrue(workers_finished.wait(2), 'both worker slots must be released')
                    self.assertEqual(post(server, b'{"external_id":"recovered"}')[0], 200)
            finally:
                release.set()
                cleanup_allowed.set()
                for sock in stalled:
                    sock.close()

    def test_6_malformed_auth_and_json_have_defined_responses_and_private_logs(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), serving(self.ledger) as server:
            results = []
            for body, auth in [(b'{"external_id":"private-marker"}', b'Bearer secret-\xe9'), (b'\xff', b'Bearer synthetic-token')]:
                try:
                    results.append(raw_post(server, body, len(body), auth=auth))
                except http.client.RemoteDisconnected:
                    results.append((0, b''))
            self.assertEqual(post(server, b'{"external_id":"safe","note_text":"private-marker"}')[0], 200)
        self.assertEqual(results, [(401, b''), (400, b'')])
        self.assertEqual(stdout.getvalue() + stderr.getvalue(), '')
        self.assertNotIn('private-marker', self.ledger.path.read_text())

    def test_7_machine_scope_matches_installation(self):
        probe = load('probe_scope_regression', 'bin/probe-keepalive.py')
        config = load('config_scope_regression', 'ops/config-as-code.py')
        from lib import machine_role, launchd_scope
        self.assertIs(config.PRIMARY_ONLY, launchd_scope.PRIMARY_ONLY)
        self.assertIs(config.SECONDARY_ONLY, launchd_scope.SECONDARY_ONLY)
        for primary in (False, True):
            observed = []
            with patch.object(machine_role, 'is_primary', return_value=primary), patch.object(probe, 'probe', return_value=(False, 'process', 'synthetic')), patch.object(probe, 'record', side_effect=lambda key, *args: observed.append(key) or 0), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(probe.main(), 0)
            self.assertEqual('canary-ingest-sink' in observed, primary)
            self.assertTrue({'call-mode', 'doc-engine', 'quill-dictate'}.issubset(observed))
        self.assertIn('com.carr.canary-ingest-sink.plist', config.PRIMARY_ONLY)

    def test_8_scheduler_reconciles_probe_backed_direct_service(self):
        scheduler = load('scheduler_regression', 'tools/scheduler-truth.py')
        plist_dir = self.directory / 'plists'
        plist_dir.mkdir()
        source = REPO / 'ops/launchd/com.carr.canary-ingest-sink.plist'
        (plist_dir / source.name).write_bytes(source.read_bytes())
        registry = self.directory / 'services.json'
        service = next(s for s in json.loads((REPO / 'ops/config/services.json').read_text())['services'] if s['key'] == 'canary-ingest-sink')
        registry.write_text(json.dumps({'services': [service]}))
        with patch.object(scheduler, 'REPO', str(self.directory)), patch.object(scheduler, 'REPO_PLISTS', str(plist_dir)), patch.object(scheduler, 'INSTALLED', str(plist_dir)), patch.object(scheduler, 'SERVICES', str(registry)), patch.object(scheduler, 'loaded_labels', return_value={'com.carr.canary-ingest-sink'}), contextlib.redirect_stdout(io.StringIO()):
            result = scheduler.main()
        self.assertFalse(scheduler.DRIFT, scheduler.DRIFT)
        self.assertEqual(result, 0)

    def test_9_live_and_canary_exact_body_limit(self):
        # Execute the live handler through its guard, without touching a DB.
        index = (REPO / 'mcp-server/src/index.js').read_text()
        function = index[index.index('async function ingest('):index.index('  let payload;', index.index('async function ingest('))]
        contract_path = REPO / 'mcp-server/src/ingest-transport.v1.json'
        contract = json.loads(contract_path.read_text()) if contract_path.exists() else {'max_body_bytes': 1048576}
        limit = contract['max_body_bytes']
        js = 'const INGEST_TRANSPORT = ' + json.dumps(contract) + '; const json=(value,status)=>({status});\n' + function + '\nreturn {status: 200};}\n'
        js += 'for (const size of [' + str(limit) + ',' + str(limit + 1) + ']) console.log((await ingest(new Request("http://127.0.0.1/ingest", {method:"POST", headers:{authorization:"Bearer synthetic", "content-length":String(size)}}), {INGEST_TOKENS: JSON.stringify({notes:"synthetic"})})).status);'
        live = subprocess.run(['node', '--input-type=module', '-e', js], capture_output=True, text=True, check=True)
        self.assertEqual(live.stdout.splitlines(), ['200', '413'])
        with serving(self.ledger) as server:
            base = b'{"external_id":"limit","note_text":""}'
            body = base[:-2] + b'x' * (limit - len(base)) + base[-2:]
            self.assertEqual(len(body), limit)
            accepted = post(server, body)[0]
            refused = raw_post(server, b'', limit + 1)[0]
        self.assertEqual((accepted, refused), (200, 413))


if __name__ == '__main__':
    unittest.main()
