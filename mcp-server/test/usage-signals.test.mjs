import test from 'node:test';
import assert from 'node:assert/strict';
import { usageResponse } from '../src/usage-signals.js';
import { RETENTION_SECONDS } from '../src/usage-contract.v1.js';
import * as contract from '../src/usage-contract.v1.js';
import { readFile } from 'node:fs/promises';

const origin = 'https://app.doctorcre.com';
const sha = 'a'.repeat(40);
const now = Date.parse('2026-10-05T12:00:00.000Z');
const session = { actor: { slug: 'joe' }, csrfToken: 'synthetic-token' };
class Kv {
  values = new Map();
  writes = [];
  async get(key, options) { const v = this.values.get(key); return v == null ? null : options?.type === 'json' ? JSON.parse(v.value) : v.value; }
  async put(key, value, options) { this.writes.push({ key, value, options }); this.values.set(key, { value, ...options }); }
  async list({ prefix, cursor }) {
    const keys = [...this.values.entries()].filter(([key]) => key.startsWith(prefix)).map(([name, v]) => ({ name, metadata: v.metadata }));
    const offset = Number(cursor || 0);
    return { keys: keys.slice(offset, offset + 2), list_complete: offset + 2 >= keys.length, cursor: String(offset + 2) };
  }
}
const event = extra => ({ event_name: 'screen_viewed', screen: 'home', partner: 'joe', release_sha: sha, timestamp: new Date(now).toISOString(), ...extra });
const guard = async request => ({ value: await request.json() });
const post = body => new Request(`${origin}/api/v1/usage-signals`, { method: 'POST', body: JSON.stringify(body), headers: { 'content-type': 'application/json' } });
test('server rejects client data, free text, extra fields, forged partners and malformed timestamps', async () => {
  const env = { OAUTH_KV: new Kv() };
  for (const body of [event({ client: { name: 'Synthetic Client', lease: 'Synthetic Lease' } }), event({ screen: 'Synthetic Client' }), event({ event_name: 'Lease for Synthetic Client' }), event({ partner: 'dell' }), event({ timestamp: '2026-02-30T12:00:00.000Z' }), event({ release_sha: 'Synthetic Client' })]) {
    const response = await usageResponse(post(body), env, session, { now: () => now }, guard);
    assert.equal(response.status, 400);
    assert.equal((await response.text()).includes('Synthetic'), false);
  }
  assert.deepEqual(env.OAUTH_KV.writes, []);
  assert.equal((await usageResponse(post(event()), env, session, { now: () => now }, guard)).status, 202);
  const stored = env.OAUTH_KV.writes.find(write => write.key.includes(':event:'));
  assert.equal(stored.options.expirationTtl, RETENTION_SECONDS);
  assert.deepEqual(JSON.parse(stored.value), event());
  assert.equal(env.OAUTH_KV.writes.every(write => write.options?.expirationTtl === RETENTION_SECONDS), true);
});

test('weekly summary separates partners, paginates, records last use and avoids false never-used after retention', async () => {
  const env = { OAUTH_KV: new Kv() };
  for (const [partner, time, screen] of [['joe', now, 'home'], ['joe', now - 8 * 86400000, 'deals'], ['dell', now - 1000, 'home']]) {
    await usageResponse(post(event({ partner, screen, timestamp: new Date(time).toISOString() })), env, { ...session, actor: { slug: partner } }, { now: () => time }, guard);
  }
  let releaseStartedAt = new Date(now - 9 * 86400000).toISOString();
  const get = () => usageResponse(new Request(`${origin}/api/v1/usage-signals?release_sha=${sha}&release_started_at=${releaseStartedAt}`), env, session, { now: () => now }, guard);
  let body = await (await get()).json();
  assert.equal(body.coverage, 'retained_window');
  assert.deepEqual(body.features.find(row => row.id === 'home:view').uses, { joe: 1, dell: 1 });
  const deals = body.features.find(row => row.id === 'deals:view');
  assert.deepEqual(deals.uses, { joe: 0, dell: 0 });
  assert.equal(deals.last_used.joe, '2026-09-27T12:00:00.000Z');
  assert.equal(deals.never_used.joe, false);
  assert.equal(deals.never_used.dell, null);
  releaseStartedAt = new Date(now - 181 * 86400000).toISOString();
  body = await (await get()).json();
  assert.equal(body.coverage, 'retained_window');
  assert.equal(body.features.find(row => row.id === 'tours:view').never_used.joe, null);
});

test('server off switch suppresses storage and guard refusals stay refusals', async () => {
  const env = { OAUTH_KV: new Kv(), DOCTORCRE_USAGE_CAPTURE_ENABLED: 'false' };
  const response = await usageResponse(post(event()), env, session, { now: () => now }, guard);
  assert.deepEqual(await response.json(), { captured: false });
  assert.deepEqual(env.OAUTH_KV.writes, []);
  const refused = await usageResponse(post(event()), env, session, { now: () => now }, async () => ({ error: new Response('', { status: 403 }) }));
  assert.equal(refused.status, 403);
});

test('concurrent captures keep distinct events and server time determines retention', async () => {
  const env = { OAUTH_KV: new Kv() };
  await Promise.all([1, 2].map(() => usageResponse(post(event({ timestamp: new Date(now - 60000).toISOString() })), env, session, { now: () => now }, guard)));
  const events = env.OAUTH_KV.writes.filter(write => write.key.includes(':event:'));
  assert.equal(new Set(events.map(write => write.key)).size, 2);
  assert.equal(events.every(write => JSON.parse(write.value).timestamp === new Date(now).toISOString() && write.options.expirationTtl === 15552000), true);
});

test('missing release provenance leaves never-used history unknown', async () => {
  const response = await usageResponse(new Request(`${origin}/api/v1/usage-signals?release_sha=${sha}`), { OAUTH_KV: new Kv() }, session, { now: () => now }, guard);
  const body = await response.json();
  assert.equal(body.coverage, 'retained_window');
  assert.equal(body.features[0].never_used.joe, null);
});

test('weekly uses and last use survive app releases while never-used describes the current release', async () => {
  const env = { OAUTH_KV: new Kv() }, previous = now - 86400000;
  await usageResponse(post(event({ release_sha: 'b'.repeat(40), screen: 'deals', timestamp: new Date(previous).toISOString() })), env, session, { now: () => previous }, guard);
  const response = await usageResponse(new Request(`${origin}/api/v1/usage-signals?release_sha=${sha}&release_started_at=${new Date(now - 1000).toISOString()}`), env, session, { now: () => now }, guard);
  const row = (await response.json()).features.find(row => row.id === 'deals:view');
  assert.deepEqual(row.uses, { joe: 1, dell: 0 });
  assert.equal(row.last_used.joe, '2026-10-04T12:00:00.000Z');
  assert.equal(row.never_used.joe, null);
});

const summary = env => usageResponse(new Request(`${origin}/api/v1/usage-signals?release_sha=${sha}&release_started_at=${new Date(now - 86400000).toISOString()}`), env, session, { now: () => now }, guard);

for (const gap of ['disabled then reenabled', 'late capture start', 'failed capture']) {
  test(`${gap} leaves absent use unknown`, async () => {
    const env = { OAUTH_KV: new Kv() };
    if (gap === 'disabled then reenabled') {
      env.DOCTORCRE_USAGE_CAPTURE_ENABLED = 'false';
      assert.deepEqual(await (await usageResponse(post(event()), env, session, { now: () => now }, guard)).json(), { captured: false });
      env.DOCTORCRE_USAGE_CAPTURE_ENABLED = 'true';
    } else if (gap === 'failed capture') {
      env.OAUTH_KV.put = async () => { throw new Error('write failed'); };
      assert.equal((await usageResponse(post(event()), env, session, { now: () => now }, guard)).status, 503);
    }
    const body = await (await summary(env)).json();
    assert.equal(body.coverage, 'retained_window');
    assert.deepEqual(body.features.find(row => row.id === 'home:view').never_used, { joe: null, dell: null });
  });
}

test('KV failures and incomplete pagination return an unavailable summary', async () => {
  const writeFailure = new Kv();
  writeFailure.put = async () => { throw new Error('write failed'); };
  const response = await usageResponse(post(event()), { OAUTH_KV: writeFailure }, session, { now: () => now }, guard);
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { error: 'usage_unavailable' });
  for (const list of [async () => { throw new Error('list failed'); }, async () => ({ keys: [], list_complete: false })]) {
    const response = await summary({ OAUTH_KV: { list } });
    assert.equal(response.status, 503);
    assert.deepEqual(await response.json(), { error: 'usage_unavailable' });
  }
});

test('an empty unfinished KV page continues with the cursor and same namespace', async () => {
  const calls = [];
  const response = await summary({ OAUTH_KV: { async list(options) {
    calls.push(options);
    return calls.length === 1 ? { keys: [], list_complete: false, cursor: 'next' }
      : { keys: [{ metadata: event() }], list_complete: true };
  } } });
  assert.equal(response.status, 200);
  assert.deepEqual(calls, [{ prefix: `doctorcre_usage:v1:${origin}:event:` }, { prefix: `doctorcre_usage:v1:${origin}:event:`, cursor: 'next' }]);
  const row = (await response.json()).features.find(row => row.id === 'home:view');
  assert.equal(row.uses.joe, 1);
  assert.equal(row.never_used.joe, false);
});

for (const operation of ['put', 'list']) {
  test(`a stalled KV ${operation} returns 503 after five seconds`, { timeout: 1000 }, async t => {
    t.mock.timers.enable({ apis: ['setTimeout'] });
    const env = { OAUTH_KV: new Kv() };
    let entered;
    const started = new Promise(resolve => { entered = resolve; });
    env.OAUTH_KV[operation] = () => { entered(); return new Promise(() => {}); };
    const pending = operation === 'put' ? usageResponse(post(event()), env, session, { now: () => now }, guard) : summary(env);
    await started;
    t.mock.timers.tick(5000);
    const response = await pending;
    assert.equal(response.status, 503);
    assert.deepEqual(await response.json(), { error: 'usage_unavailable' });
  });
}

test('contract owns partner eligibility at both trust checks', async () => {
  assert.deepEqual(contract.PARTNERS, ['joe', 'dell']);
  assert.equal(Object.isFrozen(contract.PARTNERS), true);
  for (const partner of [...contract.PARTNERS, 'outsider']) {
    assert.equal(contract.validUsageEvent(event({ partner })), contract.PARTNERS.includes(partner));
    const response = await usageResponse(new Request(`${origin}/api/v1/usage-signals/session`), {}, { ...session, actor: { slug: partner } }, { now: () => now }, guard);
    assert.equal(response.status, contract.PARTNERS.includes(partner) ? 200 : 403);
  }
  const source = await readFile(new URL('../src/usage-signals.js', import.meta.url), 'utf8');
  assert.equal(source.match(/doctorcre_usage:v1/g)?.length, 1, 'capture and summary must share one namespace definition');
});

test('screen mappings use the contract paths and reject unknown or query-bearing paths', () => {
  for (const [screen, [path]] of Object.entries(contract.SCREENS)) assert.equal(contract.usageScreen(path), screen);
  for (const path of ['/tours/day', '/tours/day.html']) assert.equal(contract.usageScreen(path), 'tours');
  assert.equal(contract.usageScreen('/control-room/progress/board/synthetic'), 'progress');
  for (const path of [null, '', '/unknown', '/deals/extra', '/home?name=synthetic', '/deals#fragment', '/tours/day?name=synthetic']) assert.equal(contract.usageScreen(path), null);
});
