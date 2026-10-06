import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as runtime from '../src/runtime-errors.js';
import * as consumer from '../../ops/runtime-error-watch.mjs';
import { runtimeErrorControl } from '../src/runtime-error-web.js';
import { incidentFactText } from '../src/trace.js';
import { TOOLS } from '../src/tools.js';

const loopId = '12345678-1234-1234-1234-123456789abc';
const actor = { id: loopId, slug: 'joe', human: true, via: 'mcp' };
function loopDatabase({ status = 'open', readVersion = 1, writeVersion = readVersion } = {}) {
  return { query: async text => {
    const sql = text.replace(/\s+/g, ' ').trim();
    if (sql.startsWith('select request_hash, response')) return { rows: [] };
    if (sql.startsWith('select id as loop_id')) return { rows: [{ loop_id: loopId, version: readVersion, status }] };
    if (sql.includes('from loop_amendment_history')) return { rows: [] };
    if (sql.includes('from loop_item li join loop_block')) return { rows: [{ id: loopId, kind: 'open_loop', status, section: 'backlog' }] };
    if (sql.startsWith('select version from loop_item')) return { rows: [{ version: writeVersion }] };
    if (sql.startsWith('select created_at from loop_item')) return { rows: [{ created_at: '2026-10-01T00:00:00Z' }] };
    if (sql.includes('from event e join actor')) return { rows: [{ actor: 'joe', verb: 'update-loop', field: 'body' }] };
    throw new Error(sql);
  } };
}
function fixture() {
  const data = new Map(); let puts = 0;
  const store = new runtime.RuntimeErrorStore({ blockConcurrencyWhile: fn => fn(), storage: {
    get: async key => structuredClone(data.get(key)),
    put: async (key, value) => { puts++; data.set(key, structuredClone(value)); },
    delete: async key => data.delete(key), list: async () => structuredClone(data),
  } });
  const control = async (path, body = {}) => (await store.fetch(new Request('https://store' + path.replace('/_runtime-errors', ''), { method: 'POST', body: JSON.stringify(body) }))).json();
  return { data, control, puts: () => puts };
}
async function spike(control) { for (let n = 0; n < 9; n++) await control('/capture'); }
async function filed(control) {
  await control('/capture'); const op = (await control('/plan')).operations[0];
  await control('/ack', { fingerprint: op.fingerprint, key: op.key, loop_id: loopId });
  return op.fingerprint;
}

test('1 planned add-loop passes the real handler owner and placement validation', async () => {
  const { control } = fixture(); await control('/capture');
  const op = (await control('/plan')).operations[0];
  const db = { query: async (text, params = []) => {
    const sql = text.replace(/\s+/g, ' ').trim();
    if (sql.startsWith('select request_hash, response')) return { rows: [] };
    if (sql.startsWith('select slug from loop_domain')) return { rows: [{ slug: 'system' }] };
    if (sql.startsWith('select id, rel_path, col_order from loop_block')) return { rows: [{ id: loopId, rel_path: 'backlog', col_order: null }] };
    if (sql.includes('from loop_item where kind = $1')) return { rows: [{ m: 1 }] };
    if (sql.startsWith('select coalesce(max(render_seq)')) return { rows: [{ n: 1 }] };
    if (sql.startsWith('insert into loop_item')) return { rows: [{ id: loopId }] };
    if (sql.startsWith('insert into event') || sql.startsWith('insert into tool_call')) return { rows: [] };
    throw new Error(sql);
  } };
  const result = await TOOLS['add-loop'].handler(db, { id: loopId, slug: 'joe', human: true, via: 'mcp' }, { ...op.args, idempotency_key: op.key });
  assert.equal(result.ok, true);
});

test('2 stderr conflict is structured, retires stale operation and preserves the cause', async () => {
  const { control, data } = fixture(); const fp = await filed(control); await spike(control);
  const db = loopDatabase({ writeVersion: 2 });
  let writes = 0;
  await assert.rejects(consumer.consumeRuntimeErrors(control, async (name, args) => {
    if (name === 'read-loop') return TOOLS[name].handler(db, actor, args);
    writes++;
    try { return await TOOLS[name].handler(db, actor, args); }
    catch (error) { return consumer.parseVerbFailure({ stdout: '', stderr: 'identity diagnostic\nTOOL ERROR ' + JSON.stringify(error.payload, null, 2) + '\n' }); }
  }), /version_conflict/);
  assert.equal(data.get(fp).pending, null);
  assert.equal((await control('/plan')).operations.length, 0, 'no retry over another writer');
  assert.equal(writes, 1);
});

test('3 closed loop is detached and re-filed while an unrelated fingerprint still proceeds', async () => {
  const { control } = fixture(); await filed(control); await spike(control);
  const db = loopDatabase({ status: 'done', readVersion: 2 });
  await control('/capture', { route: '/health' });
  let adds = 0;
  await consumer.consumeRuntimeErrors(control, async (name, args) => {
    if (name === 'read-loop') return TOOLS[name].handler(db, actor, args);
    adds++; return { ok: true, loop_id: loopId };
  });
  assert.equal(adds, 1, 'the other fingerprint is filed in the same tick');
  assert.equal((await control('/plan')).operations[0].verb, 'add-loop');
});

test('3 one refused operation cannot stall other fingerprints', async () => {
  const { control } = fixture(); await control('/capture'); await control('/capture', { route: '/health' });
  let writes = 0;
  await assert.rejects(consumer.consumeRuntimeErrors(control, async () => ++writes === 1 ? { error: 'no_block' } : { ok: true, loop_id: loopId }), /no_block/);
  assert.equal(writes, 2);
});

test('4 spike preserves body and human version change prevents automated mutations', async () => {
  const { control } = fixture(); await filed(control); await spike(control);
  const op = (await control('/plan')).operations[0];
  assert.equal(op.args.body, undefined);
  assert.match(op.args.source_note, /Count 10/);
  const writes = [];
  await consumer.consumeRuntimeErrors(control, async name => {
    if (name === 'read-loop') return { loop: { version: 2, status: 'open', body: 'Human repair plan' } };
    writes.push(name); return { ok: true, loop_id: loopId };
  });
  assert.deepEqual(writes, []);
  assert.equal((await control('/plan')).operations.length, 0);
});

test('5 release and bundle coordinates do not change error identity or clear live recurrence', async () => {
  const { control, data } = fixture();
  const first = await control('/capture', { stack: 'at fn (https://app/js/a.js:12:3)', release_sha: 'a'.repeat(40) });
  const op = (await control('/plan')).operations[0]; await control('/ack', { ...op, loop_id: loopId });
  const second = await control('/capture', { stack: 'at fn (https://app/js/b.js:99:7)', release_sha: 'b'.repeat(40) });
  assert.equal(first.fingerprint, second.fingerprint); assert.equal(data.size, 1);
  assert.equal((await control('/plan', { releases: { browser: { sha: 'b'.repeat(40), created_at: new Date().toISOString() } } })).operations.length, 0);
  assert.equal(data.get(first.fingerprint).stack, 'asset:99:7');
});

test('7 ledger owns bounded runtime-error recurrence and launchd invokes only the ledger', () => {
  const manifest = JSON.parse(readFileSync(new URL('../../ops/config/control-plane-workflows.v1.json', import.meta.url)));
  const workflow = manifest.workflows.find(w => w.key === 'runtime-error-watch');
  assert.ok(workflow);
  assert.equal(workflow.execution.entrypoint, 'ops/runtime-error-watch.mjs');
  assert.equal(workflow.recurrence.cron, '*/5 * * * *');
  assert.ok(workflow.retry.timeout_seconds <= 600);
  const wrapper = readFileSync(new URL('../../bin/control-plane-tick.sh', import.meta.url), 'utf8');
  assert.doesNotMatch(wrapper, /node .*runtime-error-watch/);
  assert.match(wrapper, /neither owns recurrence nor invokes workflow code/);
});

test('8 server incident route identity survives scrubbing without meaningless detail', () => {
  for (const route of ['mcp:tools/call:find', '/token', '/authorize', '/register', '/oauth/callback', '/.well-known/oauth-authorization-server', '/dealroom/session']) {
    assert.equal(incidentFactText({ routeKey: route, failureClass: 'internal', correlationId: 'c', detail: 'private text' }), `${route} failed (internal), correlation c`);
  }
  assert.match(incidentFactText({ routeKey: '/clients/Alice', failureClass: 'internal', correlationId: 'c' }), /^\/clients\/:value failed/);
});

test('9 response text and quiet policy have one contract source', () => {
  const contract = JSON.parse(readFileSync(new URL('../src/runtime-errors.v1.json', import.meta.url)));
  assert.equal(runtime.ERROR_RESPONSE, contract.response_action.replace('{quiet_hours}', String(contract.auto_clear.quiet_ms / 3600000)));
  assert.ok(runtime.ERROR_RESPONSE.includes(`${contract.auto_clear.quiet_ms / 3600000}h`));
});

test('10 an unset app host cannot fetch a fallback release or plan a browser close', async () => {
  let body;
  const env = { CARR_ENV: 'staging', RUNTIME_ERRORS: { idFromName: x => x, get: () => ({ fetch: async (_, init) => { body = JSON.parse(init.body); return Response.json({ ok: true }); } }) } };
  const originalFetch = globalThis.fetch; let fetches = 0;
  globalThis.fetch = async () => { fetches++; return Response.json({ service: 'doctorcre-app', environment: 'staging', source_commit: 'a'.repeat(40) }); };
  try { await runtimeErrorControl(new Request('https://app/_runtime-errors/plan', { method: 'POST', body: '{}' }), env); }
  finally { globalThis.fetch = originalFetch; }
  assert.equal(fetches, 0); assert.equal(body.releases.browser, null);
});

test('11 no-op plans do not write, cleared groups expire, source census and plan work are capped', async () => {
  const { control, data, puts } = fixture(); const fp = await filed(control);
  const before = puts(); await control('/plan'); assert.equal(puts(), before);
  const group = data.get(fp); group.cleared = true; group.loop_id = null; group.cleared_at = Date.now() - 31 * runtime.QUIET_MS;
  await control('/plan'); assert.equal(data.size, 0);
  for (let n = 0; n < 1005; n++) await control('/capture', { route: '/clients/' + 'health/'.repeat(n % 7), type: ['Error', 'TypeError'][n % 2], message: 'Failed to fetch' });
  // Seed distinct historical rows to exercise the plan cap independently of scrubbing.
  for (let n = 0; n < 20; n++) data.set('historical-' + n, { ...group, fingerprint: 'historical-' + n, cleared: false, pending: null, notified_count: 0 });
  assert.ok((await control('/plan')).operations.length <= 5);
  data.clear();
  for (let n = 0; n < 1000; n++) data.set('existing-' + n, { source: 'browser' });
  assert.equal((await control('/capture', { route: '/new' })).error, 'runtime_error_group_limit');
});

test('7 shadow observes pending work without preparing, acknowledging or deleting it', async () => {
  const { control, data, puts } = fixture(); await control('/capture');
  const before = puts();
  const plan = await control('/plan', { observe_only: true });
  assert.match(plan.health, /^WARN/); assert.deepEqual(plan.operations, []);
  assert.equal(puts(), before); assert.equal([...data.values()][0].pending, undefined);
});

test('3 closure after a human edit can still detach the fingerprint on its next recurrence', async () => {
  const { control } = fixture(); await filed(control); await spike(control);
  await consumer.consumeRuntimeErrors(control, async () => ({ loop: { version: 2, status: 'open' } }));
  await control('/capture');
  await consumer.consumeRuntimeErrors(control, async () => ({ loop: { version: 3, status: 'done' } }));
  assert.equal((await control('/plan')).operations[0].verb, 'add-loop');
});

test('3 and 11 a full batch of refusals cannot starve fingerprints beyond the plan cap', async () => {
  const { control } = fixture();
  for (const route of ['/health', '/clients', '/vendors', '/deals', '/leads', '/tours']) await control('/capture', { route });
  const refused = new Set((await control('/plan')).operations.map(op => op.fingerprint));
  let fresh = 0;
  const call = async (_, args) => {
    if ([...refused].some(fp => args.body?.includes(fp))) return { error: 'no_block' };
    fresh++; return { ok: true, loop_id: loopId };
  };
  await assert.rejects(consumer.consumeRuntimeErrors(control, call), /no_block/);
  await assert.rejects(consumer.consumeRuntimeErrors(control, call), /no_block/);
  assert.equal(fresh, 1);
});
