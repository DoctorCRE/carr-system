import test from 'node:test';
import { TOOLS } from '../src/tools.mjs';
import assert from 'node:assert/strict';
import { Miniflare, convertV4MiniflareOptions } from 'miniflare';

test('bundled Worker completes MCP requests in local workerd with no bindings', async () => {
  const worker = new Miniflare(convertV4MiniflareOptions({
    workers: [{ name: 'practice-synthetic', modules: true, scriptPath: '.build/worker.js', compatibilityDate: '2026-10-01' }],
  }));
  const post = (body, extra = {}) => worker.dispatchFetch('https://practice.synthetic.invalid/mcp', {
    method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream', ...extra },
    body: JSON.stringify(body),
  });
  try {
    const init = await post({ jsonrpc: '2.0', id: 1, method: 'initialize', params: {
      protocolVersion: '2025-11-25', capabilities: {}, clientInfo: { name: 'synthetic-workerd-client', version: '0.1.0' },
    } });
    assert.equal(init.status, 200);
    assert.ok((await init.json()).result.capabilities.tools);
    const listing = await post({ jsonrpc: '2.0', id: 2, method: 'tools/list' });
    assert.equal((await listing.json()).result.tools.length, TOOLS.length);
    const call = await post({ jsonrpc: '2.0', id: 3, method: 'tools/call', params: {
      name: 'estimate_occupancy_cost', arguments: { square_feet: 2000, market: 'mobile_downtown', lease_type: 'full_service' },
    } });
    const result = (await call.json()).result;
    assert.equal(result.structuredContent.results.monthly_cost.low, 2980);
    assert.equal(result.structuredContent.results.source.data_date, '2024-12-31');
    const rejected = await post({ jsonrpc: '2.0', id: 4, method: 'ping' }, {
      'MCP-Protocol-Version': 'REJECTED_MARKER',
    });
    assert.equal(rejected.status, 400);
    const text = await rejected.text();
    assert.doesNotMatch(text, /REJECTED_MARKER/);
    assert.deepEqual(JSON.parse(text), {
      jsonrpc: '2.0', error: { code: -32000, message: 'Unsupported protocol version.' }, id: null,
    });
  } finally { await worker.dispose(); }
});
