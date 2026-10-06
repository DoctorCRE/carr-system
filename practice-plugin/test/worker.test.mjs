import test from 'node:test';
import { TOOLS } from '../src/tools.mjs';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { SUPPORTED_PROTOCOL_VERSIONS } from '@modelcontextprotocol/sdk/types.js';
import worker from '../src/worker.mjs';

const endpoint = 'https://practice.synthetic.invalid/mcp';
const headers = { 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream' };
const post = (body, extra = {}) => worker.fetch(new Request(endpoint, {
  method: 'POST', headers: { ...headers, ...extra }, body: JSON.stringify(body),
}));

test('SDK client initializes, lists, pings and calls without authentication or sessions', async () => {
  const transport = new StreamableHTTPClientTransport(new URL(endpoint), {
    fetch: (url, init) => worker.fetch(new Request(url, init)),
  });
  const client = new Client({ name: 'synthetic-review-client', version: '0.1.0' });
  try {
    await client.connect(transport);
    assert.equal(transport.sessionId, undefined);
    assert.equal((await client.listTools()).tools.length, TOOLS.length);
    await client.ping();
    const r = await client.callTool({ name: 'plan_practice_space', arguments: {
      practice_type: 'medical', providers: 1, operatories: 0, exam_rooms: 3,
    } });
    assert.ok(!r.isError);
    assert.match(r.structuredContent.notice, /not legal/i);
  } finally { await client.close(); }
});

test('transport refuses foreign origins and invalid protocol/header/body requests', async () => {
  const ping = { jsonrpc: '2.0', id: 1, method: 'ping' };
  assert.equal((await post(ping, { Origin: 'https://foreign.synthetic.invalid' })).status, 403);
  assert.equal((await post(ping, { Origin: 'null' })).status, 403);
  assert.equal((await post(ping, { 'MCP-Protocol-Version': 'unsupported' })).status, 400);
  assert.equal((await post(ping, { Accept: 'text/plain' })).status, 406);
  assert.equal((await post(ping, { 'Content-Type': 'text/plain' })).status, 415);
  assert.equal((await post({ ...ping, padding: 'x'.repeat(17000) })).status, 413);
  assert.equal((await worker.fetch(new Request(endpoint, { method: 'POST', headers, body: '{' }))).status, 400);
  assert.equal((await worker.fetch(new Request(endpoint, { method: 'GET' }))).status, 405);
  assert.equal((await worker.fetch(new Request(endpoint, { method: 'DELETE' }))).status, 405);
  assert.equal((await worker.fetch(new Request('https://practice.synthetic.invalid/'))).status, 404);
});

test('transport rejects unsupported protocol headers without echoing caller text', async () => {
  const response = await post({ jsonrpc: '2.0', id: 1, method: 'ping' }, {
    'MCP-Protocol-Version': 'REJECTED_MARKER',
  });
  assert.equal(response.status, 400);
  const text = await response.text();
  assert.doesNotMatch(text, /REJECTED_MARKER/);
  const body = JSON.parse(text);
  assert.equal(body.jsonrpc, '2.0');
  assert.equal(body.error.code, -32000);
  assert.equal(body.error.message, 'Unsupported protocol version.');
  assert.equal(body.id, null);
  assert.equal(response.headers.get('Cache-Control'), 'no-store');
  assert.equal(response.headers.get('X-Content-Type-Options'), 'nosniff');
});

test('transport accepts every SDK-supported protocol header and the missing-header default', async () => {
  for (const version of [undefined, ...SUPPORTED_PROTOCOL_VERSIONS]) {
    const extra = version === undefined ? {} : { 'MCP-Protocol-Version': version };
    const response = await post({ jsonrpc: '2.0', id: 1, method: 'ping' }, extra);
    assert.equal(response.status, 200, version);
    assert.deepEqual(await response.json(), { jsonrpc: '2.0', id: 1, result: {} });
  }
});

test('Worker has no binding, secrets, OAuth, logs or outbound request capability in source', () => {
  const config = JSON.parse(fs.readFileSync(new URL('../wrangler.json', import.meta.url)));
  const allowed = ['name', 'main', 'compatibility_date', 'workers_dev', 'preview_urls', 'observability'];
  assert.ok(Object.keys(config).every(k => allowed.includes(k)));
  assert.deepEqual(config.observability, { enabled: false });
  assert.equal(config.workers_dev, false);
  assert.equal(config.preview_urls, false);
  assert.equal(config.main, 'src/worker.mjs');
  for (const file of fs.readdirSync(new URL('../src/', import.meta.url))) {
    const code = fs.readFileSync(new URL(`../src/${file}`, import.meta.url), 'utf8');
    assert.doesNotMatch(code, /process\.env|env\.|console\.|\bfetch\s*\(|DATABASE|OAuth|Authorization|\.dev\.vars/);
  }
});

test('calls do not make network requests, log data or echo rejected input', async () => {
  const oldFetch = globalThis.fetch;
  globalThis.fetch = () => { throw new Error('runtime egress forbidden'); };
  try {
    const response = await post({ jsonrpc: '2.0', id: 2, method: 'tools/call', params: {
      name: 'get_broker_search_help', arguments: { request: 'actual_search_help', contact: 'synthetic-rejected' },
    } });
    const body = await response.json();
    assert.ok(body.result.isError);
    assert.doesNotMatch(JSON.stringify(body), /synthetic-rejected/);
    assert.equal(response.headers.get('Cache-Control'), 'no-store');
  } finally { globalThis.fetch = oldFetch; }
});
