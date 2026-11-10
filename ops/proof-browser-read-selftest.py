#!/usr/bin/env python3
"""Exercise the proof reader's actual emitted browser code with synthetic data."""
import json
from pathlib import Path
import runpy
import subprocess
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
READER = runpy.run_path(str(ROOT / 'tools/proof-browser-read.py'))


class ProofReaderTest(unittest.TestCase):
    def test_discovery_failure_closes_connection_without_launch(self):
        for status, body, error in [(302, b'[]', None), (200, b'[]', None),
                                    (200, b'[]', TimeoutError('synthetic timeout'))]:
            connection = Mock()
            connection.getresponse.return_value.status = status
            connection.getresponse.return_value.read.return_value = body
            connection.request.side_effect = error
            with self.subTest(status=status, error=error), patch('http.client.HTTPConnection', return_value=connection), patch('subprocess.run') as launch:
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
    location: {origin}, document: {body: {innerText: 'Synthetic Home'}},
    fetch: async (url, options) => {
      calls.push({url, options});
      return {ok: true, json: async () => ({ok: true})};
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
