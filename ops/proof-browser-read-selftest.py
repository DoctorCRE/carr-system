#!/usr/bin/env python3
"""Exercise the proof reader's actual emitted browser code with synthetic data."""
import json
import http.client
import http.server
import threading
import time
import io
import sys
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
import runpy
import subprocess
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
READER = runpy.run_path(str(ROOT / 'tools/proof-browser-read.py'))


class ProofReaderTest(unittest.TestCase):
    def test_browser_deadline_aborts_pending_mcp_read(self):
        script = r'''
import assert from 'node:assert/strict';
import vm from 'node:vm';
const request = JSON.parse(await new Response(process.stdin).text());
let aborted = false;
const context = {TextEncoder, Blob, AbortController, setTimeout, clearTimeout,
  location:{origin:'https://app.doctorcre.com'}, fetch:async (_url, {signal}) =>
    new Promise((_resolve, reject) => signal.addEventListener('abort', () => {
      aborted = true;
      reject(new Error('aborted'));
    }))};
await assert.rejects(vm.runInNewContext(request.params.expression, context), /aborted/);
assert.equal(aborted, true);
'''
        command = READER['request'](['config'], time.time() * 1000 + 1500)
        subprocess.run(['node', '--input-type=module', '-e', script],
                       input=json.dumps(command), text=True, check=True, timeout=5)

    def test_parent_deadline_reaps_its_owned_transport(self):
        connection = Mock()
        connection.getresponse.return_value.status = 200
        connection.getresponse.return_value.read.return_value = json.dumps([
            {'type':'page', 'url':'https://app.doctorcre.com/',
             'webSocketDebuggerUrl':'ws://127.0.0.1:9333/devtools/page/ABC'}]).encode()
        real_popen, owned = subprocess.Popen, []
        def launch(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            owned.append(child)
            return child
        with patch.dict(READER['collect'].__globals__, TOTAL_SECONDS=.2,
                        TRANSPORT='setInterval(() => {}, 1000);'), patch(
            'http.client.HTTPConnection', return_value=connection
        ), patch('subprocess.Popen', side_effect=launch):
            with self.assertRaises(subprocess.TimeoutExpired):
                READER['collect'](['text'])
        self.assertEqual(len(owned), 1)
        self.assertIsNotNone(owned[0].poll())
        connection.close.assert_called_once()

    def test_transport_acquired_after_deadline_is_reaped(self):
        connection = Mock()
        connection.getresponse.return_value.status = 200
        connection.getresponse.return_value.read.return_value = json.dumps([
            {'type':'page', 'url':'https://app.doctorcre.com/',
             'webSocketDebuggerUrl':'ws://127.0.0.1:9333/devtools/page/ABC'}]).encode()
        real_popen, owned = subprocess.Popen, []
        def launch(*args, **kwargs):
            time.sleep(.15)
            child = real_popen(*args, **kwargs)
            owned.append(child)
            return child
        with patch.dict(READER['collect'].__globals__, TOTAL_SECONDS=.1,
                        TRANSPORT='setInterval(() => {}, 1000);'), patch(
            'http.client.HTTPConnection', return_value=connection
        ), patch('subprocess.Popen', side_effect=launch):
            with self.assertRaises(TimeoutError):
                READER['collect'](['text'])
        self.assertEqual(len(owned), 1)
        self.assertIsNotNone(owned[0].poll())
        self.assertTrue(owned[0].stdin.closed)
        self.assertTrue(owned[0].stdout.closed)
        self.assertTrue(owned[0].stderr.closed)

    def test_browser_caps_text_mcp_and_combined_utf8_output(self):
        script = r'''
import assert from 'node:assert/strict';
import vm from 'node:vm';
const request = JSON.parse(await new Response(process.stdin).text());
for (const [text, body] of [
  ['X'.repeat(8388608), '{}'],
  ['界'.repeat(65536), '{}'],
  ['home', JSON.stringify({data:'X'.repeat(8388608)})],
  ['X'.repeat(90000), JSON.stringify({data:'X'.repeat(90000)})],
]) {
  let canceled = false;
  const context = {TextEncoder, Blob, AbortController, setTimeout, clearTimeout, location:{origin:'https://app.doctorcre.com'},
    document:{body:{innerText:text}}, fetch:async () => ({ok:true, body:new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(body));
      }, cancel() { canceled = true; }
    })})};
  // A finite MCP stream must terminate for a combined-size check.
  if (body.length < 131072) context.fetch = async () => new Response(body);
  await assert.rejects(vm.runInNewContext(request.params.expression, context), /TRUNCATED/);
  if (body.length > 131072 && text === 'home') assert.equal(canceled, true);
}
'''
        subprocess.run(['node', '--input-type=module', '-e', script],
                       input=json.dumps(READER['request'](['text', 'config'])),
                       text=True, check=True, timeout=5)

    def test_transport_deadline_closes_stalled_and_late_open_sockets(self):
        for delay in [0, 80]:
            script = r'''
globalThis.WebSocket = class {
  addEventListener(event, handler) {
    if(event === 'open') setTimeout(handler, DELAY);
  }
  send() { process.stderr.write('sent;'); }
  close() { process.stderr.write('closed;'); }
};
'''.replace('DELAY', str(delay))
            result = subprocess.run(['node', '--input-type=module', '-e', script + READER['TRANSPORT']],
                                    input=json.dumps({'url': 'synthetic', 'request': READER['request'](['text']),
                                                      'timeout': 30}),
                                    text=True, capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 78)
            self.assertEqual(result.stdout, '')
            self.assertIn('closed;', result.stderr)
            if delay:
                self.assertNotIn('sent;', result.stderr)
                self.assertEqual(result.stderr.count('closed;'), 2)

    def test_cli_acquisition_failure_and_truncation_exit_78(self):
        for error in [ConnectionRefusedError(), TimeoutError(), ValueError('[TRUNCATED: proof output exceeds byte limit]')]:
            out, err = io.StringIO(), io.StringIO()
            with patch.object(sys, 'argv', ['reader', 'text']), patch.dict(
                READER['main'].__globals__, collect=Mock(side_effect=error)
            ), redirect_stdout(out), redirect_stderr(err), self.assertRaises(SystemExit) as caught:
                READER['main']()
            self.assertEqual(caught.exception.code, 78)
            self.assertEqual(out.getvalue(), '')
            if 'TRUNCATED' in str(error):
                self.assertIn('TRUNCATED', err.getvalue())

    def test_discovery_time_is_charged_to_transport(self):
        connection = Mock()
        connection.getresponse.return_value.status = 200
        def body(_size):
            time.sleep(.06)
            return json.dumps([{'type':'page', 'url':'https://app.doctorcre.com/',
                                'webSocketDebuggerUrl':'ws://127.0.0.1:9333/devtools/page/ABC'}]).encode()
        connection.getresponse.return_value.read.side_effect = body
        result = Mock(returncode=0)
        result.poll.return_value = 0
        result.communicate.return_value = (json.dumps({'result':{'result':{'value':{'text':'home'}}}}), '')
        with patch.dict(READER['collect'].__globals__, TOTAL_SECONDS=.3), patch(
            'http.client.HTTPConnection', return_value=connection
        ), patch('subprocess.Popen', return_value=result):
            self.assertEqual(READER['collect'](['text']), {'text':'home'})
        call = result.communicate.call_args_list[0]
        self.assertLess(call.kwargs['timeout'], .25)
        self.assertLess(json.loads(call.args[0])['timeout'], 250)
        connection.close.assert_called_once()

    def test_continuous_discovery_data_cannot_extend_deadline(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Length', '22')
                self.end_headers()
                try:
                    for byte in b'[]' + b' ' * 20:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(.04)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.handle_request)
        thread.start()
        real_connection = http.client.HTTPConnection
        try:
            with patch.dict(READER['collect'].__globals__, TOTAL_SECONDS=.25), patch(
                'http.client.HTTPConnection', side_effect=lambda _host, _port, timeout:
                    real_connection('127.0.0.1', server.server_port, timeout=timeout)
            ):
                start = time.monotonic()
                with self.assertRaises(TimeoutError):
                    READER['collect'](['text'])
                self.assertLess(time.monotonic() - start, .7)
        finally:
            thread.join(timeout=3)
            server.server_close()
        self.assertFalse(thread.is_alive())

    def test_transport_refuses_oversized_reply_before_stdout(self):
        script = r'''
globalThis.WebSocket = class {
  handlers = {};
  addEventListener(event, handler) {
    this.handlers[event] = handler;
    if (event === 'open') queueMicrotask(handler);
  }
  send() { this.handlers.message({data: JSON.stringify({id:1, result:'X'.repeat(8388608)})}); }
  close() { process.stderr.write('disconnected'); }
};
'''
        result = subprocess.run(['node', '--input-type=module', '-e', script + READER['TRANSPORT']],
                                input=json.dumps({'url': 'synthetic', 'request': READER['request'](['text']),
                                                  'timeout': 1000, 'maxBytes': 131072}),
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 78)
        self.assertEqual(result.stdout, '')
        self.assertIn('TRUNCATED', result.stderr)
        self.assertIn('disconnected', result.stderr)

    def test_discovery_failure_closes_connection_without_launch(self):
        for status, body, error in [(302, b'[]', None), (200, b'[]', None),
                                    (200, b'[]', TimeoutError('synthetic timeout'))]:
            connection = Mock()
            connection.getresponse.return_value.status = status
            connection.getresponse.return_value.read.return_value = body
            connection.request.side_effect = error
            with self.subTest(status=status, error=error), patch('http.client.HTTPConnection', return_value=connection), patch('subprocess.Popen') as launch:
                with self.assertRaises((ValueError, TimeoutError)):
                    READER['collect'](['config'])
                connection.close.assert_called_once()
                launch.assert_not_called()

    def test_transport_reads_one_reply_and_disconnects(self):
        script = r'''
import assert from 'node:assert/strict';
globalThis.WebSocket = class {
  handlers = {};
  constructor(url) { assert.equal(url, 'synthetic'); }
  addEventListener(event, handler) {
    this.handlers[event] = handler;
    if (event === 'open') queueMicrotask(handler);
  }
  send(body) {
    assert.equal(JSON.parse(body).method, 'Runtime.evaluate');
    this.handlers.message({data: JSON.stringify({id: 99})});
    this.handlers.message({data: JSON.stringify({id: 1, result: 'synthetic'})});
  }
  close() { process.stderr.write('disconnected'); }
};
'''
        result = subprocess.run(['node', '--input-type=module', '-e', script + READER['TRANSPORT']],
                                input=json.dumps({'url': 'synthetic', 'request': READER['request'](['text'])}),
                                text=True, capture_output=True, check=True, timeout=5)
        self.assertEqual(json.loads(result.stdout), {'id': 1, 'result': 'synthetic'})
        self.assertEqual(result.stderr, 'disconnected')

    def test_only_existing_app_page_at_designated_endpoint(self):
        valid = {'type': 'page', 'url': 'https://app.doctorcre.com/home',
                 'webSocketDebuggerUrl': 'ws://127.0.0.1:9333/devtools/page/ABC-123'}
        self.assertEqual(READER['target']([valid]), valid['webSocketDebuggerUrl'])
        for key, value in [
            ('type', 'service_worker'), ('url', 'https://app.doctorcre.com.evil/home'),
            ('webSocketDebuggerUrl', 'ws://127.0.0.1:9334/devtools/page/ABC'),
            ('webSocketDebuggerUrl', 'ws://evil.invalid:9333/devtools/page/ABC'),
            ('webSocketDebuggerUrl', 'ws://127.0.0.1:9333/devtools/page/ABC?token=x'),
        ]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                READER['target']([{**valid, key: value}])
        with self.assertRaises(ValueError):
            READER['target']([])

    def test_no_arbitrary_browser_code_or_write_verbs(self):
        for reads in [[], ['send-email'], ['fetch("https://evil.invalid")'], ['config', 'logout']]:
            with self.subTest(reads=reads), self.assertRaises(ValueError):
                READER['request'](reads)

    def test_browser_requests_and_origin_refusal(self):
        script = r'''
import assert from 'node:assert/strict';
import vm from 'node:vm';
const request = JSON.parse(await new Response(process.stdin).text());
assert.equal(request.method, 'Runtime.evaluate');
assert.equal(request.params.timeout, 12000);
for (const origin of ['https://app.doctorcre.com', 'https://evil.invalid']) {
  const calls = [];
  const context = {
    TextEncoder, Blob, AbortController, setTimeout, clearTimeout, location: {origin}, document: {body: {innerText: 'Synthetic Home'}},
    fetch: async (url, options) => {
      calls.push({url, options});
      return new Response(JSON.stringify({ok: true}));
    }
  };
  const promise = vm.runInNewContext(request.params.expression, context);
  if (origin !== 'https://app.doctorcre.com') {
    await assert.rejects(promise, /Wrong origin/);
    assert.equal(calls.length, 0);
    continue;
  }
  const result = await promise;
  assert.equal(result.text, 'Synthetic Home');
  assert.equal(calls.length, 2);
  assert.deepEqual(calls.map(c => JSON.parse(c.options.body).params.name),
    ['read-web-push-config-v1', 'read-web-push-subscriptions-v1']);
  for (const {url, options} of calls) {
    assert.equal(url, '/mcp');
    assert.equal(options.redirect, 'error');
    assert.equal(options.credentials, 'same-origin');
    assert.equal(options.method, 'POST');
    assert.deepEqual(JSON.parse(options.body).params.arguments, {});
  }
}
'''
        subprocess.run(['node', '--input-type=module', '-e', script],
                       input=json.dumps(READER['request'](['config', 'subscriptions', 'text'])),
                       text=True, check=True, timeout=5)


if __name__ == '__main__':
    unittest.main()
