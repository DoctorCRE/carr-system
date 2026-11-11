#!/usr/bin/env python3
"""Rate-limit retries through the shared reader and real CLI, using loopback only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import release_reader as reader

REPO = Path(__file__).resolve().parents[1]


class BackoffTest(unittest.TestCase):
    def setUp(self):
        self.responses = []
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                owner.requests.append(self.rfile.read(int(self.headers.get('Content-Length', 0))))
                status, headers, body = owner.responses.pop(0)
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)

            do_POST = do_GET

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/mcp'
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_reader_429_then_success(self):
        self.responses = [(429, {'Retry-After': '0'}, b'limited'), (200, {}, b'recovered')]
        self.assertEqual(reader.read(self.url).body, b'recovered')
        self.assertEqual(len(self.requests), 2)

    def test_reader_date_then_1015_then_success(self):
        self.responses = [(429, {'Retry-After': formatdate(1007, usegmt=True)}, b'limited'),
                          (403, {'Retry-After': '0'}, b'<html>Error 1015</html>'),
                          (200, {}, b'recovered')]
        with patch.object(reader.time, 'time', return_value=1000), patch.object(reader.time, 'sleep') as sleep:
            self.assertEqual(reader.read(self.url).body, b'recovered')
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [7, 0])

    def test_reader_exhaustion(self):
        self.responses = [(429, {'Retry-After': '0'}, b'limited')] * 6
        with patch.object(reader.time, 'sleep'), patch('sys.stderr') as stderr:
            self.assertEqual(reader.read(self.url).status, 429)
        self.assertEqual(len(self.requests), 6)
        self.assertIn('attempts=6', ''.join(c.args[0] for c in stderr.write.call_args_list))

    def test_other_errors_never_retry(self):
        for status in (400, 401, 403, 404, 409, 500, 503):
            with self.subTest(status=status):
                self.responses = [(status, {}, b'error')]
                self.assertEqual(reader.read(self.url).status, status)
        self.assertEqual(len(self.requests), 7)

    def test_retry_after_variants_and_bounds(self):
        for value, expected in [('7', 7), (formatdate(1007, usegmt=True), 7),
                                (formatdate(999, usegmt=True), 0)]:
            with self.subTest(value=value):
                self.assertEqual(reader.retry_delay(429, {'rEtRy-AfTeR': value}, b'', 1, 0, now=1000), expected)
        for value in ('invalid', '-1', '1.5'):
            with patch.object(reader.random, 'uniform', return_value=0.5):
                self.assertEqual(reader.retry_delay(429, {'Retry-After': value}, b'', 2, 0), 10.5)
        self.assertIsNone(reader.retry_delay(429, {'Retry-After': '91'}, b'', 1, 0))
        self.assertIsNone(reader.retry_delay(429, {}, b'', 6, 0))
        self.assertIsNone(reader.retry_delay(429, {'Retry-After': '1'}, b'', 1, 89.5))
        self.assertIsNone(reader.retry_delay(200, {}, b'{"record":1015}', 1, 0))

    def cli(self, args, **extra):
        env = {**os.environ, 'CARR_MCP_LOCAL_TOKEN': 'synthetic-bearer',
               'CARR_MCP_ENV': '/nonexistent/J281-tokens', 'CARR_MCP_URL': self.url}
        env.pop('DATABASE_URL', None)
        env.update(extra)
        return subprocess.run([str(REPO / 'run.sh'), 'call', 'synthetic-write', json.dumps(args)],
                              capture_output=True, text=True, env=env, timeout=15)

    def test_cli_write_1015_reuses_exact_body(self):
        self.responses = [(403, {'Retry-After': '0'}, b'<html>Cloudflare Error 1015</html>'),
                          (200, {}, b'{"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"null"}]}}')]
        result = self.cli({'idempotency_key': 'same-key', 'value': 'unchanged'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.requests[0], self.requests[1])
        self.assertEqual(json.loads(self.requests[1])['params']['arguments']['idempotency_key'], 'same-key')

    def test_cli_exhaustion_preserves_error(self):
        self.responses = [(429, {'Retry-After': '0'}, b'limited')] * 6
        result = self.cli({})
        self.assertEqual(result.returncode, 1)
        self.assertIn('non-JSON response (HTTP 429)', result.stderr)
        self.assertIn('rate limit (HTTP 429 / Cloudflare 1015); attempts=6', result.stderr)
        self.assertEqual(len(self.requests), 6)

    def test_cli_429_then_success(self):
        self.responses = [(429, {'Retry-After': '0'}, b'limited'),
                          (200, {}, b'{"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"null"}]}}')]
        result = self.cli({})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('rate limit', result.stderr)
        self.assertEqual(len(self.requests), 2)

    def test_cli_other_4xx_never_retry(self):
        self.responses = [(403, {}, b'<html>Access denied</html>')]
        result = self.cli({})
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('rate limit', result.stderr)
        self.assertEqual(len(self.requests), 1)

    def test_exhausted_call_is_transient(self):
        from lib.record_call import call_verb, TRANSIENT
        def runner(*args, **kwargs):
            return subprocess.CompletedProcess(args, 1, '', 'non-JSON response (HTTP 429) from fixture\nrate limit (HTTP 429 / Cloudflare 1015); attempts=6')
        result = call_verb('synthetic-read', {}, runner=runner)
        self.assertEqual(result.kind, TRANSIENT)
        self.assertIn('attempts=6', result.detail)

    def test_cli_deadline_during_wait(self):
        self.responses = [(429, {'Retry-After': '5'}, b'limited')]
        result = self.cli({}, CARR_RECORD_DEADLINE_MS=str(int(time.time() * 1000 + 1500)))
        self.assertEqual(result.returncode, 1)
        self.assertIn('record call deadline exceeded', result.stderr)
        self.assertIn('rate limit (HTTP 429 / Cloudflare 1015); attempts=1', result.stderr)
        self.assertEqual(len(self.requests), 1)


if __name__ == '__main__':
    unittest.main()
