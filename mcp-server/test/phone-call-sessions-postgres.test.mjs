// Verify phone session SQL, concurrency, and grants on disposable PostgreSQL.
import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { createPhoneCallHandler } from '../src/phone-call-sessions.js';

let fixture, pool, actor, handler;
before(async () => {
  const bin = ['/opt/homebrew/opt/postgresql@18/bin', '/usr/lib/postgresql/18/bin',
    '/opt/homebrew/opt/postgresql@17/bin', '/usr/lib/postgresql/17/bin'].find(p => existsSync(`${p}/initdb`));
  assert.ok(bin, 'Disposable PostgreSQL is required');
  fixture = await acquireDisposablePostgres({ prefix: 'phone-call-', pgCtl: `${bin}/pg_ctl` });
  await fixture.run(`${bin}/initdb`, ['-D', `${fixture.root}/data`, '-U', 'carr_ci', '--auth=trust', '--no-locale']);
  await fixture.run(`${bin}/pg_ctl`, ['-D', `${fixture.root}/data`, '-l', `${fixture.root}/server.log`, '-o', `-k ${fixture.root} -h ''`, '-w', 'start']);
  await fixture.run(`${bin}/createdb`, ['-h', fixture.root, '-U', 'carr_ci', 'carr_ci']);
  pool = new pg.Pool({ host: fixture.root, user: 'carr_ci', database: 'carr_ci' });
  await pool.query('create role neondb_owner');
  await fixture.run(`${bin}/psql`, ['-h', fixture.root, '-U', 'carr_ci', '-d', 'carr_ci', '-X', '-q', '-v', 'ON_ERROR_STOP=1', '-f', new URL('../../db/schema.sql', import.meta.url).pathname]);
  await pool.query(readFileSync(new URL('../../migrations/1065_phone_call_sessions.sql', import.meta.url), 'utf8'));
  actor = { ...(await pool.query("select id,slug from actor where slug='joe'")).rows[0], human: true };
  assert.ok(actor.id);
  handler = createPhoneCallHandler({ withWriter: async fn => {
    const c = await pool.connect();
    try {
      await c.query('begin');
      await c.query('set local role carr_writer');
      const result = await fn(c);
      await c.query('commit');
      return result;
    } catch (error) { await c.query('rollback'); console.error(error.code, error.message); throw error; }
    finally { c.release(); }
  }, fetchFn: async (_url, options) => {
    const metadata = JSON.parse(options.headers['x-carr-call']);
    return Response.json({ session_id: metadata.session_id, sha256: metadata.sha256, transferred: true });
  } });
});
after(async () => { try { await pool?.end(); } finally { await fixture?.close(); } });

const env = { CALL_STUDIO_TOKEN: 'synthetic-service-secret', CALL_STUDIO_URL: 'https://studio.example.test' };
const url = 'https://app.example.test/api/v1/call-sessions';
const metadata = { sha256: 'b'.repeat(64), byte_count: 100, duration_seconds: 1, mime_type: 'audio/wav',
  started_at: '2026-01-01T12:00:00Z', consent_announced_at: '2026-01-01T12:00:00Z' };

test('concurrent imports admit one session under carr_writer and expose its report without filing', async () => {
  const admit = () => handler.fetch(new Request(url, { method: 'POST', body: JSON.stringify(metadata) }), env, actor);
  const replies = await Promise.all(Array.from({ length: 6 }, async () => {
    const response = await admit();
    assert.ok([200, 201].includes(response.status), await response.clone().text());
    return response.json();
  }));
  assert.equal(new Set(replies.map(r => r.session_id)).size, 1);
  assert.equal(replies.filter(r => !r.duplicate).length, 1);
  const id = replies[0].session_id;
  const transfer = await handler.fetch(new Request(`${url}/${id}/transfer`, { method: 'PUT',
    headers: { 'content-type': 'audio/wav', 'content-length': '100' }, body: new Uint8Array(100) }), env, actor);
  assert.equal(transfer.status, 200, await transfer.clone().text());
  const projection = { summary: 'Synthetic imported conversation.', decisions: [], open_questions: [] };
  const report = await handler.studioFetch(new Request(`https://api.example.test/capture/phone/v1/call-sessions/${id}/report`, {
    method: 'POST', headers: { authorization: `Bearer ${env.CALL_STUDIO_TOKEN}` }, body: JSON.stringify(projection),
  }), env);
  assert.equal(report.status, 200);
  const read = await handler.fetch(new Request(`${url}/${id}/report`), env, actor);
  assert.equal(read.status, 200);
  const visible = await read.json();
  assert.deepEqual(visible.report, projection);
  assert.equal(visible.filed, false);
  assert.deepEqual(visible.actions, []);
  assert.deepEqual(visible.deal_updates, []);
  assert.equal((await handler.fetch(new Request(`${url}/${id}`), env, { ...actor, id: '10000000-0000-0000-0000-000000000009', slug: 'dell' })).status, 404);
});
