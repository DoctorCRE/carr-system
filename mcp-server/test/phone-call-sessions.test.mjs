import test from 'node:test';
import assert from 'node:assert/strict';
import { createPhoneCallHandler } from '../src/phone-call-sessions.js';

const joe = { id: '10000000-0000-0000-0000-000000000001', slug: 'joe', human: true };
const root = 'https://app.example.test/api/v1/call-sessions';
const recording = {
  sha256: 'a'.repeat(64), byte_count: 100, duration_seconds: 10, mime_type: 'audio/wav',
  started_at: '2026-01-01T12:00:00Z', consent_announced_at: '2026-01-01T12:00:00Z',
};
const environment = { CALL_STUDIO_TOKEN: 'synthetic-studio-secret', CALL_STUDIO_URL: 'https://studio.example.test' };

class PhoneStore {
  sessions = [];
  captures = [];
  async query(sql, p = []) {
    if (sql.includes('phone:owner-lock')) return { rows: [] };
    if (sql.includes('phone:renew')) return { rows: [] };
    if (sql.includes('phone:actions') || sql.includes('phone:updates')) return { rows: [] };
    if (sql.includes('phone:duplicate')) return { rows: this.sessions.filter(s => s.owner_id === p[0] && s.sha256 === p[1]) };
    if (sql.includes('phone:daily')) return { rows: [{ count: this.sessions.length }] };
    if (sql.includes('phone:capture')) { this.captures.push(p); return { rows: [] }; }
    if (sql.includes('phone:admit')) {
      const [id, owner_id, sha256, byte_count, duration_seconds, mime_type, started_at, consent_announced_at] = p;
      const s = { id, owner_id, sha256, byte_count, duration_seconds, mime_type, started_at,
        consent_announced_at, transferred_at: null, state: 'recording', projection: null };
      this.sessions.push(s);
      return { rows: [s] };
    }
    if (sql.includes('phone:read')) return { rows: this.sessions.filter(s => s.id === p[0] && (!p[1] || s.owner_id === p[1])) };
    if (sql.includes('phone:transferred')) {
      this.sessions.find(s => s.id === p[0]).transferred_at = '2026-01-01T12:01:00Z';
      return { rows: [] };
    }
    if (sql.includes('phone:projection')) { this.sessions.find(s => s.id === p[0]).projection = JSON.parse(p[1]); return { rows: [] }; }
    throw new Error(`Unexpected query ${sql}`);
  }
}

function rig(fetchFn) {
  const store = new PhoneStore();
  const handler = createPhoneCallHandler({ withWriter: fn => fn(store), fetchFn });
  const send = async (suffix = '', method = 'GET', value, actor = joe, env = environment) => {
    const response = await handler.fetch(new Request(root + suffix, {
      method, headers: { 'content-type': value instanceof Uint8Array ? 'audio/wav' : 'application/json', 'content-length': String(value instanceof Uint8Array ? value.length : 0) },
      ...(value === undefined ? {} : { body: value instanceof Uint8Array ? value : JSON.stringify(value) }),
    }), env, actor);
    return { status: response.status, body: await response.json() };
  };
  return { store, handler, send };
}

test('phone sessions refuse anonymous and machine actors before reading input or storage', async () => {
  const handler = createPhoneCallHandler({ withWriter: () => assert.fail('unauthorized database access') });
  for (const actor of [null, { ...joe, human: false }, { ...joe, slug: 'other' }]) {
    const response = await handler.fetch(new Request(root, { method: 'POST', body: 'invalid' }), {}, actor);
    assert.equal(response.status, 401);
  }
});

test('verified Studio transfer is replayable and the report rejects transcript fields', async () => {
  const { send, handler } = rig(async (_url, options) => {
    const metadata = JSON.parse(options.headers['x-carr-call']);
    assert.equal(metadata.owner, 'joe');
    assert.equal(metadata.byte_count, 100);
    return Response.json({ session_id: metadata.session_id, sha256: metadata.sha256, transferred: true });
  });
  const { body: session } = await send('', 'POST', recording);
  const upload = await send(`/${session.session_id}/transfer`, 'PUT', new Uint8Array(100));
  assert.equal(upload.status, 200);
  assert.equal(upload.body.transferred, true);
  assert.equal(upload.body.filed, false);
  const url = `https://api.example.test/capture/phone/v1/call-sessions/${session.session_id}/report`;
  const publish = report => handler.studioFetch(new Request(url, { method: 'POST',
    headers: { authorization: `Bearer ${environment.CALL_STUDIO_TOKEN}` }, body: JSON.stringify(report) }), environment);
  assert.equal((await publish({ summary: 'Synthetic summary', decisions: [], open_questions: [], transcript: 'private words' })).status, 400);
  const report = { summary: 'Synthetic summary', decisions: ['Review the lease'], open_questions: [] };
  assert.equal((await publish(report)).status, 200);
  const read = await send(`/${session.session_id}/report`);
  assert.deepEqual(read.body.report, report);
  assert.equal(read.body.state, 'ready_review');
  assert.equal(read.body.filed, false);
  const renew = () => handler.studioFetch(new Request(url.replace('/report', '/lease'), {
    method: 'POST', headers: { authorization: `Bearer ${environment.CALL_STUDIO_TOKEN}` },
  }), environment);
  const firstLease = await (await renew()).json();
  assert.match(firstLease.session_token, /^[0-9a-f]{64}$/);
  assert.deepEqual(await (await renew()).json(), firstLease);
  assert.equal(read.body.session_token, undefined);
});

test('duplicate admissions use one session and outage leaves the recording pending, never filed', async () => {
  const { store, send } = rig(async () => { throw new Error('Studio offline'); });
  const first = await send('', 'POST', recording);
  assert.equal(first.status, 201);
  const duplicate = await send('', 'POST', recording);
  assert.equal(duplicate.body.session_id, first.body.session_id);
  assert.equal(store.sessions.length, 1);
  const upload = await send(`/${first.body.session_id}/transfer`, 'PUT', new Uint8Array(100));
  assert.equal(upload.status, 202);
  assert.equal(upload.body.state, 'pending');
  assert.equal(upload.body.filed, false);
  const status = await send(`/${first.body.session_id}`);
  assert.equal(status.body.state, 'pending');
  assert.equal(status.body.filed, false);
  const outsider = await send(`/${first.body.session_id}`, 'GET', undefined, { ...joe, id: 'other', slug: 'dell' });
  assert.equal(outsider.status, 404);
});

test('limits reject before admission and duplicates consume no extra daily admission', async () => {
  const { send, store } = rig();
  for (const changes of [{ byte_count: 52428801 }, { duration_seconds: 3601 }, { mime_type: 'text/plain' },
    { transcript: 'forbidden' }, { consent_announced_at: null }]) {
    assert.equal((await send('', 'POST', { ...recording, ...changes })).status, 400);
  }
  assert.equal(store.sessions.length, 0);
  for (let n = 0; n < 5; n++) {
    const body = { ...recording, sha256: String(n).repeat(64) };
    assert.equal((await send('', 'POST', body)).status, 201);
    assert.equal((await send('', 'POST', body)).status, 200);
  }
  assert.equal((await send('', 'POST', recording)).status, 429);
  assert.equal(store.sessions.length, 5);
});

test('Studio report publication authenticates before reading input and cannot claim filing', async () => {
  const { handler } = rig();
  const response = await handler.studioFetch(new Request('https://api.example.test/capture/phone/v1/call-sessions/x/report',
    { method: 'POST', body: 'invalid' }), environment);
  assert.equal(response.status, 401);
});
