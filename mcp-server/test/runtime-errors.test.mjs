import test from 'node:test';
import assert from 'node:assert/strict';
import { scrubError, RuntimeErrorStore, withRuntimeErrors, QUIET_MS } from '../src/runtime-errors.js';
import { scheduleFailureRecord } from '../src/trace.js';

test('unexpected MCP throws are captured even when JSON-RPC returns HTTP 200', async () => {
  const events = [], waits = [];
  const env = { GIT_SHA: 'c'.repeat(40), RUNTIME_ERRORS: { idFromName: name => name, get: () => ({ fetch: async (_, init) => { events.push(JSON.parse(init.body)); return Response.json({ ok: true }); } }) } };
  scheduleFailureRecord(env, { waitUntil: promise => waits.push(promise) }, { routeKey: 'mcp:tools/call:find', failureClass: 'verb_internal_error', error: new TypeError('Cannot read properties of Alice alice@example.test') });
  await Promise.all(waits);
  assert.equal(events.length, 1);
  assert.equal(events[0].type, 'TypeError');
  assert.equal(events[0].route, '/mcp');
  assert.doesNotMatch(JSON.stringify(events), /Alice|example/);
});

function fixture() {
  const data = new Map();
  const state = { blockConcurrencyWhile: async fn => fn(), storage: {
    get: async key => structuredClone(data.get(key)), put: async (key, value) => data.set(key, structuredClone(value)),
    list: async () => structuredClone(data),
  } };
  const store = new RuntimeErrorStore(state, {});
  const call = async (path, input = {}) => (await store.fetch(new Request(`https://errors${path}`, { method: 'POST', body: JSON.stringify(input) }))).json();
  return { call, data, store };
}

test('grouping is value independent, pending loop creation survives restart, and spikes update one loop', async () => {
  const { call, data, store } = fixture();
  const error = { source: 'browser', type: 'TypeError', route: '/clients/Alice', message: 'Cannot read properties of Alice', stack: 'at fn (https://app/js/client.js:12:3)', release_sha: 'a'.repeat(40) };
  const first = await call('/capture', error);
  await call('/capture', { ...error, route: '/clients/Bob', message: 'Cannot read properties of Bob' });
  assert.equal(data.size, 1);
  const plan = await call('/plan');
  assert.equal(plan.operations.length, 1);
  assert.equal(plan.operations[0].verb, 'add-loop');
  assert.equal(plan.operations[0].count, 2);
  const replay = await (await new RuntimeErrorStore(store.state, {}).fetch(new Request('https://errors/plan', { method: 'POST', body: '{}' }))).json();
  assert.deepEqual(replay.operations, plan.operations);
  await call('/ack', { fingerprint: first.fingerprint, key: plan.operations[0].key, loop_id: '12345678-1234-1234-1234-123456789abc' });
  assert.equal((await call('/plan')).operations.length, 0);
  for (let n = 0; n < 8; n++) await call('/capture', error);
  const spike = (await call('/plan')).operations[0];
  assert.equal(spike.verb, 'update-loop');
  assert.equal(spike.args.loop_id, '12345678-1234-1234-1234-123456789abc');
  assert.match(spike.args.source_note, /Count 10/);
  assert.equal(spike.args.body, undefined);
});

test('quiet alone cannot close; a newer observed release permits close after 24h', async () => {
  const { call, data } = fixture();
  const { fingerprint } = await call('/capture', { release_sha: 'a'.repeat(40) });
  const operation = (await call('/plan')).operations[0];
  await call('/ack', { fingerprint, key: operation.key, loop_id: '12345678-1234-1234-1234-123456789abc' });
  const group = data.get(fingerprint);
  group.last_seen = Date.now() - QUIET_MS - 1;
  assert.equal((await call('/plan', { releases: { browser: { sha: 'a'.repeat(40), created_at: new Date().toISOString() } } })).operations.length, 0);
  assert.equal((await call('/plan', { releases: { browser: { sha: 'b'.repeat(40), created_at: new Date(group.last_seen - 1000).toISOString() } } })).operations.length, 0, 'an older release or rollback cannot clear');
  const close = (await call('/plan', { releases: { browser: { sha: 'b'.repeat(40), created_at: new Date().toISOString() } } })).operations[0];
  assert.equal(close.verb, 'close-loop');
  await call('/ack', { fingerprint, key: close.key });
  assert.equal((await call('/plan')).operations.length, 0);
  await call('/capture', { release_sha: 'b'.repeat(40) });
  assert.equal((await call('/plan')).operations[0].verb, 'add-loop');
});

test('Worker captures throws and 5xx without reading response or request bodies', async () => {
  for (const failure of ['throw', '5xx']) {
    const events = [], waits = [];
    const env = { GIT_SHA: 'c'.repeat(40), RUNTIME_ERRORS: { idFromName: name => name, get: () => ({ fetch: async (_, init) => { events.push(JSON.parse(init.body)); return Response.json({ ok: true }); } }) } };
    const handler = withRuntimeErrors(async () => {
      if (failure === 'throw') throw new Error('Alice alice@example.test');
      return new Response('private response', { status: 503 });
    });
    const run = handler(new Request('https://app/clients/Alice?email=alice@example.test', { method: 'POST', body: 'private request' }), env, { waitUntil: promise => waits.push(promise) });
    if (failure === 'throw') await assert.rejects(run, /Alice/); else assert.equal((await run).status, 503);
    await Promise.all(waits);
    assert.equal(events.length, 1);
    assert.equal(events[0].source, 'carr-worker');
    assert.equal(events[0].release_sha, 'c'.repeat(40));
    assert.doesNotMatch(JSON.stringify(events), /Alice|example|private/);
  }
});

test('a previously observed release cannot clear when the current release is unknown', async () => {
  const { call, data } = fixture();
  const { fingerprint } = await call('/capture', { release_sha: 'a'.repeat(40) });
  const operation = (await call('/plan')).operations[0];
  await call('/ack', { fingerprint, key: operation.key, loop_id: '12345678-1234-1234-1234-123456789abc' });
  await call('/plan', { releases: { browser: { sha: 'b'.repeat(40), created_at: new Date().toISOString() } } });
  data.get(fingerprint).last_seen = Date.now() - QUIET_MS - 1;
  assert.equal((await call('/plan')).operations.length, 0);
});

test('PII and record contents never survive the capture seam', () => {
  const result = scrubError({ type: 'TypeError', message: 'Cannot read properties of Alice Smith alice@example.test patient diagnosis',
    stack: 'TypeError: Alice Smith record:9876543:1234567\n at Alice (https://app.test/js/client.js?email=alice@example.test:12:3)\n at /Users/Alice/private.js:45:2',
    route: '/clients/Alice?email=alice@example.test', body: 'medical record', release_sha: 'a'.repeat(40) });
  assert.equal(result.message, 'Cannot read properties of [redacted]');
  assert.equal(result.route, '/clients/:value');
  assert.equal(result.stack, 'asset:12:3\nasset:45:2');
  assert.equal(result.release_sha, 'a'.repeat(40));
  assert.doesNotMatch(JSON.stringify(result), /Alice|Smith|example|diagnosis|medical|Users|private/);
});
