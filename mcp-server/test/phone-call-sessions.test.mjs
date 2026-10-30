import test from 'node:test';
import assert from 'node:assert/strict';
import { PhoneAudioHandoff } from '../src/phone-audio-handoff.js';
import { createPhoneCallHandler } from '../src/phone-call-sessions.js';
import { readFileSync } from 'node:fs';
import { handoffState } from './helpers/phone-handoff-state.mjs';

const joe = { id: '10000000-0000-0000-0000-000000000001', slug: 'joe', human: true };
const root = 'https://app.example.test/api/v1/call-sessions';
const recording = {
  sha256: 'a'.repeat(64), byte_count: 100, duration_seconds: 10, mime_type: 'audio/wav',
  started_at: '2026-01-01T12:00:00Z', consent_announced_at: '2026-01-01T12:00:00Z',
};
const environment = { CALL_STUDIO_TOKEN: 'synthetic-studio-secret' };

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

function rig() {
  const store = new PhoneStore();
  const handler = createPhoneCallHandler({ withWriter: fn => fn(store) });
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

test('Studio reports reject transcript fields and leases preserve the session capability', async () => {
  const { send, handler } = rig();
  const { body: session } = await send('', 'POST', recording);
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
  const { store, send } = rig();
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

test('transient audio expires without extending on replay and framing failures release capacity', async t => {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'], now: 1000 });
  const state = handoffState();
  const handoff = new PhoneAudioHandoff(state);
  const put = (id, size, bytes) => handoff.fetch(new Request(`https://fixture.test/${id}`, {
    method: 'PUT', headers: { 'x-carr-call': JSON.stringify({ ...recording, byte_count: size }) }, body: bytes,
  }));
  const get = id => handoff.fetch(new Request(`https://fixture.test/${id}`));
  assert.equal((await put('short', 100, new Uint8Array(99))).status, 400);
  assert.equal((await put('long', 100, new Uint8Array(101))).status, 400);
  assert.equal((await put('a', 100, new Uint8Array(100))).status, 202);
  const mismatch = new Request('https://fixture.test/a', { method: 'PUT',
    headers: { 'x-carr-call': JSON.stringify({ ...recording, sha256: 'b'.repeat(64) }) } });
  Object.defineProperty(mismatch, 'body', { get() { assert.fail('hash mismatch read the body'); } });
  assert.equal((await handoff.fetch(mismatch)).status, 409);
  t.mock.timers.tick(14 * 60 * 1000);
  assert.equal((await put('a', 100, new Uint8Array(100))).status, 202);
  assert.equal((await get('a')).status, 200);
  assert.equal(await state.storage.getAlarm(), 901000);
  assert.equal((await put('full', 50 * 1024 * 1024, new Uint8Array(1))).status, 429);
  t.mock.timers.tick(60 * 1000);
  await handoff.alarm();
  assert.equal((await get('a')).status, 404);
  assert.deepEqual(await (await get('')).json(), { sessions: [] });
  assert.equal((await put('retry', 100, new Uint8Array(100))).status, 202);
  const restarted = new PhoneAudioHandoff(state);
  assert.deepEqual(await (await restarted.fetch(new Request('https://fixture.test/'))).json(), { sessions: [{ ...recording, byte_count: 100 }] });
  t.mock.timers.tick(15 * 60 * 1000);
  await restarted.alarm();
});

test('upload write budget survives restart and refuses the fourth attempt before reading audio', async t => {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'], now: 1000 });
  const state = handoffState();
  const upload = () => new Request('https://fixture.test/budget', { method: 'PUT',
    headers: { 'x-carr-call': JSON.stringify({ ...recording, owner: 'joe' }) }, body: new Uint8Array(99) });
  let writes = 0;
  for (const method of ['put', 'delete', 'setAlarm', 'deleteAlarm']) {
    const original = state.storage[method];
    state.storage[method] = async (...args) => { writes++; return original(...args); };
  }
  for (let i = 0; i < 3; i++) {
    const before = writes;
    assert.equal((await new PhoneAudioHandoff(state).fetch(upload())).status, 400);
    assert.ok(writes - before <= 5, `failed attempt used ${writes - before} writes`);
  }
  const refused = upload();
  Object.defineProperty(refused, 'body', { get() { assert.fail('rate limited request read audio'); } });
  const before = writes;
  const response = await new PhoneAudioHandoff(state).fetch(refused);
  assert.equal(response.status, 429);
  assert.equal(response.headers.get('retry-after'), '60');
  assert.equal(writes, before);
  t.mock.timers.tick(60000);
  assert.equal((await new PhoneAudioHandoff(state).fetch(upload())).status, 400);
});

test('phone transfer forwards the upload retry delay', async () => {
  const { send, handler } = rig();
  const session = (await send('', 'POST', recording)).body;
  const handoff = new PhoneAudioHandoff(handoffState());
  const env = { ...environment, PHONE_AUDIO_HANDOFF: { idFromName: n => n, get: () => handoff } };
  const transfer = () => handler.fetch(new Request(`${root}/${session.session_id}/transfer`, {
    method: 'PUT', headers: { 'content-type': 'audio/wav', 'content-length': '100' }, body: new Uint8Array(99),
  }), env, joe);
  for (let i = 0; i < 3; i++) assert.equal((await transfer()).status, 400);
  const response = await transfer();
  assert.equal(response.status, 429);
  assert.ok(Number(response.headers.get('retry-after')) > 0);
});

test('queued audio survives a Durable Object restart', async t => {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'], now: 1000 });
  const state = handoffState();
  const handoff = new PhoneAudioHandoff(state);
  const bytes = new Uint8Array(100).fill(7);
  assert.equal((await handoff.fetch(new Request('https://fixture.test/a', {
    method: 'PUT', headers: { 'x-carr-call': JSON.stringify(recording) }, body: bytes,
  }))).status, 202);
  const restarted = new PhoneAudioHandoff(state);
  const downloaded = await restarted.fetch(new Request('https://fixture.test/a'));
  assert.equal(downloaded.status, 200);
  assert.deepEqual(new Uint8Array(await downloaded.arrayBuffer()), bytes);
  t.mock.timers.tick(15 * 60 * 1000);
});

test('every Studio handoff operation rejects missing credentials before accessing the buffer', async () => {
  const { handler } = rig();
  const id = '10000000-0000-0000-0000-000000000001';
  for (const [suffix, method] of [['', 'GET'], [`/${id}/audio`, 'GET'], [`/${id}/ack`, 'POST']]) {
    const response = await handler.studioFetch(new Request(
      'https://api.example.test/capture/phone/v1/call-sessions' + suffix, { method }), environment);
    assert.equal(response.status, 401);
  }
});

test('audio storage receives independent bounded chunks totaling the upload size', async () => {
  const state = handoffState();
  const chunks = [];
  const put = state.storage.put;
  state.storage.put = async (key, value, ...options) => {
    if (key.startsWith('audio:')) chunks.push(value);
    return put(key, value, ...options);
  };
  const bytes = new Uint8Array(3_800_001).fill(7);
  const response = await new PhoneAudioHandoff(state).fetch(new Request('https://fixture.test/audio', {
    method: 'PUT', headers: { 'x-carr-call': JSON.stringify({ ...recording, byte_count: bytes.length }) }, body: bytes,
  }));
  assert.equal(response.status, 202);
  assert.ok(chunks.every(chunk => chunk.buffer.byteLength <= 1_900_000));
  assert.equal(chunks.length, 3);
  assert.equal(chunks.reduce((sum, chunk) => sum + chunk.byteLength, 0), bytes.length);
});

test('workerd keeps chunked audio across object re-instantiation and deletes it on acknowledgement', async t => {
  const { Miniflare } = await import('miniflare');
  const source = readFileSync(new URL('../src/phone-audio-handoff.js', import.meta.url), 'utf8');
  const worker = new Miniflare({ cf: false, workers: [{ config: {
    name: 'phone-test', compatibilityDate: '2026-07-01',
    manifest: { mainModule: 'phone.js', modules: { 'phone.js': { type: 'esm', contents: source + `
      export class RestartableHandoff {
        constructor(ctx) { this.ctx = ctx; this.handoff = new PhoneAudioHandoff(ctx); }
        fetch(request) {
          if (request.headers.has('x-test-restart')) this.handoff = new PhoneAudioHandoff(this.ctx);
          return this.handoff.fetch(request);
        }
        alarm() { return this.handoff.alarm(); }
      }
      export default { fetch(request, env) {
        return env.PHONE_AUDIO_HANDOFF.get(env.PHONE_AUDIO_HANDOFF.idFromName('phone-audio-v1')).fetch(request);
      } };` } } },
    env: { PHONE_AUDIO_HANDOFF: { type: 'durable-object', worker: 'phone-test', exportName: 'RestartableHandoff' } },
    exports: { RestartableHandoff: { type: 'durable-object', storage: 'sqlite' } },
  } }] });
  t.after(() => worker.dispose());
  const bytes = new Uint8Array(3 * 1024 * 1024 + 1).fill(7);
  const uploaded = await worker.dispatchFetch('https://fixture.test/audio', {
    method: 'PUT', headers: { 'x-carr-call': JSON.stringify({ ...recording, byte_count: bytes.length }) }, body: bytes,
  });
  assert.equal(uploaded.status, 202);
  const downloaded = await worker.dispatchFetch('https://fixture.test/audio', { headers: { 'x-test-restart': 'true' } });
  assert.deepEqual(new Uint8Array(await downloaded.arrayBuffer()), bytes);
  assert.equal((await worker.dispatchFetch('https://fixture.test/audio', { method: 'DELETE' })).status, 200);
  assert.equal((await worker.dispatchFetch('https://fixture.test/audio')).status, 404);
});

test('phone audio waits for an outbound Studio download and durable acknowledgement', async t => {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'], now: 1000 });
  const state = handoffState();
  let handoff = new PhoneAudioHandoff(state);
  const env = { CALL_STUDIO_TOKEN: environment.CALL_STUDIO_TOKEN,
    PHONE_AUDIO_HANDOFF: { idFromName: n => n, get: () => handoff } };
  const { handler, send } = rig();
  const studio = (suffix, method = 'GET', body) => handler.studioFetch(new Request(
    'https://api.example.test/capture/phone/v1/call-sessions' + suffix, {
      method, headers: { authorization: `Bearer ${env.CALL_STUDIO_TOKEN}` },
      ...(body ? { body: JSON.stringify(body) } : {}),
    }), env);
  const session = (await send('', 'POST', recording, joe, env)).body;
  const path = `/${session.session_id}`;
  const uploaded = await send(path + '/transfer', 'PUT', new Uint8Array(100), joe, env);
  assert.equal(uploaded.status, 202);
  assert.equal(uploaded.body.transferred, false);
  assert.equal((await send(path, 'GET', undefined, joe, env)).body.state, 'queued');
  const duplicate = new Request(root + path + '/transfer', { method: 'PUT',
    headers: { 'content-type': 'audio/wav', 'content-length': '100' }, body: new Uint8Array(100) });
  Object.defineProperty(duplicate, 'body', { get() { assert.fail('queued retry read the body'); } });
  const repeated = await handler.fetch(duplicate, env, joe);
  assert.equal(repeated.status, 202);
  assert.equal((await repeated.json()).queued, true);
  const wrongSize = new Request(root + path + '/transfer', { method: 'PUT',
    headers: { 'content-type': 'audio/wav', 'content-length': '99' } });
  Object.defineProperty(wrongSize, 'body', { get() { assert.fail('size mismatch read the body'); } });
  assert.equal((await handler.fetch(wrongSize, env, joe)).status, 400);
  handoff = new PhoneAudioHandoff(state);
  const queued = await (await studio('')).json();
  assert.equal(queued.sessions[0].session_id, session.session_id);
  const downloaded = await studio(path + '/audio');
  assert.equal(downloaded.status, 200);
  assert.equal((await downloaded.arrayBuffer()).byteLength, 100);
  assert.equal((await send(path, 'GET', undefined, joe, env)).body.transferred, false);
  assert.equal((await studio(path + '/ack', 'POST', { sha256: 'wrong' })).status, 409);
  assert.equal((await studio(path + '/ack', 'POST', { sha256: recording.sha256 })).status, 200);
  assert.equal((await state.storage.list({ prefix: 'audio:' })).size, 0);
  assert.equal(await state.storage.getAlarm(), null);
  assert.equal((await studio(path + '/audio')).status, 404);
  assert.equal((await send(path, 'GET', undefined, joe, env)).body.transferred, true);
  assert.equal((await send(path, 'GET', undefined, joe, env)).body.state, 'transferred');
  assert.equal((await studio(path + '/ack', 'POST', { sha256: recording.sha256 })).status, 200);
});

test('alarm deletes queued chunks and session read reports expired across restart', async t => {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'], now: 1000 });
  const state = handoffState();
  let handoff = new PhoneAudioHandoff(state);
  const env = { ...environment, PHONE_AUDIO_HANDOFF: { idFromName: n => n, get: () => handoff } };
  const { send } = rig();
  const session = (await send('', 'POST', recording, joe, env)).body;
  const path = `/${session.session_id}`;
  assert.equal((await send(path + '/transfer', 'PUT', new Uint8Array(100), joe, env)).status, 202);
  assert.equal(await state.storage.getAlarm(), 901000);
  t.mock.timers.tick(15 * 60 * 1000);
  handoff = new PhoneAudioHandoff(state);
  await handoff.alarm();
  assert.equal((await state.storage.list({ prefix: 'audio:' })).size, 0);
  assert.equal(await state.storage.getAlarm(), null);
  handoff = new PhoneAudioHandoff(state);
  assert.equal((await send(path, 'GET', undefined, joe, env)).body.state, 'expired');
  assert.equal((await send(path + '/transfer', 'PUT', new Uint8Array(100), joe, env)).status, 202);
  assert.equal((await send(path, 'GET', undefined, joe, env)).body.state, 'queued');
});

test('abandoned upload after restart can be retried while concurrent upload is refused', async t => {
  t.mock.timers.enable({ apis: ['Date'], now: 1000 });
  const state = handoffState();
  const headers = { 'x-carr-call': JSON.stringify({ ...recording, owner: 'joe' }) };
  let finish;
  const stream = new ReadableStream({ start(controller) { finish = () => controller.close(); } });
  const first = new PhoneAudioHandoff(state).fetch(new Request('https://fixture.test/restart', {
    method: 'PUT', headers, body: stream, duplex: 'half',
  }));
  await new Promise(resolve => setTimeout(resolve, 10));
  const upload = handoff => handoff.fetch(new Request('https://fixture.test/restart', {
    method: 'PUT', headers, body: new Uint8Array(100),
  }));
  assert.equal((await upload(new PhoneAudioHandoff(state))).status, 409);
  t.mock.timers.tick(120000);
  assert.equal((await upload(new PhoneAudioHandoff(state))).status, 202);
  finish();
  assert.equal((await first).status, 400);
});

test('session read returns database state when handoff is unreachable', async () => {
  const { send } = rig();
  const session = (await send('', 'POST', recording)).body;
  const env = { ...environment, PHONE_AUDIO_HANDOFF: { idFromName: n => n, get() { throw new Error('unreachable'); } } };
  const result = await send(`/${session.session_id}`, 'GET', undefined, joe, env);
  assert.equal(result.status, 200);
  assert.equal(result.body.state, 'pending');
  assert.equal(result.body.upload_state_unavailable, true);
});
