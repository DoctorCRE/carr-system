import test from 'node:test';
import assert from 'node:assert/strict';
import { createDealroomHandler, isDealroomRequest } from '../src/dealroom-web.js';
import { readFile } from 'node:fs/promises';

const origin = 'https://app.doctorcre.com';
const kv = () => {
  const values = new Map();
  return { values, async get(key, options) { const value = values.get(key); return value == null ? null : options?.type === 'json' ? JSON.parse(value) : value; }, async put(key, value) { values.set(key, value); }, async delete(key) { values.delete(key); } };
};
test('usage route requires cookie identity and the existing origin, metadata and CSRF guard', async () => {
  const { request, env, headers, event } = await authenticatedUsage();
  assert.equal(isDealroomRequest(new Request(`${origin}/api/v1/usage-signals`), env), true);
  assert.equal((await request('/api/v1/usage-signals')).status, 401);
  const body = JSON.stringify({ ...event, event_name: 'chat_sent' });
  for (const changed of [{ origin: 'https://other.doctorcre.com' }, { 'sec-fetch-site': 'cross-site' }, { 'x-carr-csrf': 'invalid' }]) assert.equal((await request('/api/v1/usage-signals', { method: 'POST', body, headers: { ...headers, ...changed } })).status, 403);
  assert.equal((await request('/api/v1/usage-signals', { method: 'POST', body, headers })).status, 202);
  assert.equal([...env.OAUTH_KV.values.keys()].filter(key => key.includes(':event:')).length, 1);
});

test('public usage fixtures contain only reserved synthetic email domains', async () => {
  const source = await readFile(new URL(import.meta.url), 'utf8');
  const emails = source.match(/[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}/gi) || [];
  assert.ok(emails.length > 0);
  assert.ok(emails.every(email => /@example\.(com|org|net)$/.test(email)), 'fixture identities must be synthetic');
});

async function authenticatedUsage() {
  const now = Date.parse('2026-10-05T12:00:00.000Z');
  const env = { DEALROOM_HOST: 'app.doctorcre.com', GOOGLE_CLIENT_ID: 'fixture', GOOGLE_CLIENT_SECRET: 'fixture', OAUTH_KV: kv() };
  const handler = createDealroomHandler({ now: () => now,
    exchangeGoogleCodeFn: async () => ({ id_token: 'fixture' }),
    verifyGoogleIdTokenFn: async () => ({ email: 'usage-partner@example.com', email_verified: true, sub: 'fixture' }),
    slugForEmailFn: email => email === 'usage-partner@example.com' ? 'joe' : null,
  });
  const request = (path, options) => handler.fetch(new Request(`${origin}${path}`, options), env, {});
  const start = await request('/auth/login');
  const state = new URL(start.headers.get('location')).searchParams.get('state');
  const login = await request(`/auth/callback?state=${state}&code=fixture`, { headers: { cookie: start.headers.getSetCookie()[0].split(';')[0] } });
  const cookie = login.headers.getSetCookie().find(value => value.startsWith('__Host-dealroom_session=')).split(';')[0];
  const bootstrap = await (await request('/api/v1/usage-signals/session', { headers: { cookie } })).json();
  assert.equal(bootstrap.partner, 'joe');
  const headers = { cookie, origin, 'sec-fetch-site': 'same-origin', 'content-type': 'application/json', 'x-carr-csrf': bootstrap.csrf_token };
  const event = { event_name: 'screen_viewed', screen: 'home', partner: 'joe', release_sha: 'a'.repeat(40), timestamp: new Date(now).toISOString() };
  return { request, env, now, headers, event };
}

test('browser route rejects malformed and oversized JSON before storing events', async () => {
  const { request, env, headers, event } = await authenticatedUsage();
  for (const [body, extra, status, error] of [
    ['{', {}, 400, 'invalid_json'],
    [JSON.stringify({ ...event, extra: 'x'.repeat(512) }), {}, 413, 'payload_too_large'],
    [JSON.stringify(event), { 'content-length': '513' }, 413, 'payload_too_large'],
    ['é'.repeat(257), {}, 413, 'payload_too_large'],
    [JSON.stringify(event), { 'content-type': 'text/plain' }, 415, 'unsupported_media_type'],
  ]) {
    const response = await request('/api/v1/usage-signals', { method: 'POST', body, headers: { ...headers, ...extra } });
    assert.equal(response.status, status);
    assert.equal((await response.json()).error, error);
  }
  assert.equal([...env.OAUTH_KV.values.keys()].some(key => key.includes(':event:')), false);
});

test('browser route accepts exactly five minutes of clock skew and rejects one millisecond beyond', async () => {
  const { request, now, headers, event, env } = await authenticatedUsage();
  for (const [offset, status] of [[-300000, 202], [300000, 202], [-300001, 400], [300001, 400]]) {
    const response = await request('/api/v1/usage-signals', { method: 'POST', headers, body: JSON.stringify({ ...event, timestamp: new Date(now + offset).toISOString() }) });
    assert.equal(response.status, status);
  }
  const events = [...env.OAUTH_KV.values.entries()].filter(([key]) => key.includes(':event:'));
  assert.equal(events.length, 2);
  assert.ok(events.every(([, value]) => JSON.parse(value).timestamp === new Date(now).toISOString()));
});

test('browser route reports KV write and list failures as unavailable', async () => {
  const { request, headers, event, env } = await authenticatedUsage();
  const put = env.OAUTH_KV.put;
  env.OAUTH_KV.put = async (key, ...args) => {
    if (key.includes(':event:')) throw new Error('write failed');
    return put(key, ...args);
  };
  let response = await request('/api/v1/usage-signals', { method: 'POST', headers, body: JSON.stringify(event) });
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { error: 'usage_unavailable' });
  env.OAUTH_KV.list = async () => { throw new Error('list failed'); };
  response = await request(`/api/v1/usage-signals?release_sha=${event.release_sha}`, { headers });
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { error: 'usage_unavailable' });
});

for (const operation of ['put', 'list']) {
  test(`browser route bounds a stalled KV ${operation} to five seconds`, { timeout: 1000 }, async t => {
    const { request, headers, event, env } = await authenticatedUsage();
    t.mock.timers.enable({ apis: ['setTimeout'] });
    let entered;
    const started = new Promise(resolve => { entered = resolve; });
    const original = env.OAUTH_KV[operation];
    env.OAUTH_KV[operation] = (key, ...args) => {
      if (operation === 'put' && !key.includes(':event:')) return original(key, ...args);
      entered();
      return new Promise(() => {});
    };
    const pending = operation === 'put'
      ? request('/api/v1/usage-signals', { method: 'POST', headers, body: JSON.stringify(event) })
      : request(`/api/v1/usage-signals?release_sha=${event.release_sha}`, { headers });
    await started;
    t.mock.timers.tick(5000);
    const response = await pending;
    assert.equal(response.status, 503);
    assert.deepEqual(await response.json(), { error: 'usage_unavailable' });
  });
}
