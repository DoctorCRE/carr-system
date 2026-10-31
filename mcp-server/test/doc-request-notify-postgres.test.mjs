import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { existsSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { TOOLS } from '../src/tools.js';

let fixture, pool, actor;
before(async () => {
  const bin = ['/opt/homebrew/opt/postgresql@18/bin', '/usr/lib/postgresql/18/bin', '/opt/homebrew/opt/postgresql@17/bin', '/usr/lib/postgresql/17/bin'].find(p => existsSync(`${p}/initdb`));
  assert.ok(bin, 'Disposable PostgreSQL is required');
  fixture = await acquireDisposablePostgres({ prefix: 'doc-completion-', pgCtl: `${bin}/pg_ctl` });
  await fixture.run(`${bin}/initdb`, ['-D', `${fixture.root}/data`, '-U', 'carr_ci', '--auth=trust', '--no-locale']);
  await fixture.run(`${bin}/pg_ctl`, ['-D', `${fixture.root}/data`, '-l', `${fixture.root}/server.log`, '-o', `-k ${fixture.root} -h ''`, '-w', 'start']);
  await fixture.run(`${bin}/createdb`, ['-h', fixture.root, '-U', 'carr_ci', 'carr_ci']);
  pool = new pg.Pool({ host: fixture.root, user: 'carr_ci', database: 'carr_ci' });
  await pool.query('create role neondb_owner');
  await fixture.run(`${bin}/psql`, ['-h', fixture.root, '-U', 'carr_ci', '-d', 'carr_ci', '-X', '-q', '-v', 'ON_ERROR_STOP=1', '-f', new URL('../../db/schema.sql', import.meta.url).pathname]);
  execFileSync(new URL('../../.venv/bin/python', import.meta.url).pathname, [new URL('../../tools/migrate.py', import.meta.url).pathname, '--apply', '--yes'], { env: { ...process.env, DATABASE_URL: `postgresql://carr_ci@/carr_ci?host=${fixture.root}` }, timeout: 180000 });
  actor = { ...(await pool.query("select id,slug from actor where slug='joe'")).rows[0], human: true };
});
after(async () => { try { await pool?.end(); } finally { await fixture?.close(); } });
async function setup() {
  const conversation = `a${randomUUID().slice(1)}`, request = `b${randomUUID().slice(1)}`, answer = `c${randomUUID().slice(1)}`;
  await pool.query('insert into ops.doc_conversation(id,title,created_by_actor) values ($1,$2,$3)', [conversation, 'Completion test', actor.id]);
  await pool.query("insert into ops.doc_conversation_turn(conversation_id,sequence,role,body,msg_id,origin_actor) values ($1,0,'human','question',$2,'joe'),($1,1,'assistant','answer',$3,'claude')", [conversation, request, answer]);
  return { conversation, request, answer };
}
async function complete(ids, extra = {}) {
  const c = await pool.connect();
  try {
    await c.query('begin');
    await c.query('set local role carr_authority');
    const result = await TOOLS['complete-doc-request'].handler(c, actor, { idempotency_key: randomUUID(), conversation_id: ids.conversation, request_msg_id: ids.request, outcome: 'answered', answer_msg_id: ids.answer, ...extra });
    await c.query('commit');
    return result;
  } catch (e) { await c.query('rollback'); throw e; } finally { c.release(); }
}
async function counts(ids) {
  const events = await pool.query("select count(*)::int as n from event where verb='complete-doc-request' and subject_id=$1 and new_value->>'request_msg_id'=$2", [ids.conversation, ids.request]);
  const notifications = await pool.query("select count(*)::int as n from ops.notification where subject_type='doc_conversation_request' and subject_ref=$1", [ids.request]);
  assert.equal(events.rows[0].n, 1);
  assert.equal(notifications.rows[0].n, 1);
}
test('mixed-case retries store one event and notification', async () => {
  const ids = await setup();
  const first = await complete(ids);
  const retry = await complete({ conversation: ids.conversation.toUpperCase(), request: ids.request.toUpperCase(), answer: ids.answer.toUpperCase() });
  assert.deepEqual(retry, { ...first, deduplicated: true });
  await counts(ids);
});
test('conflicting retries preserve the first completion', async () => {
  const ids = await setup();
  const first = await complete(ids);
  const retry = await complete(ids, { outcome: 'failed', answer_msg_id: undefined });
  assert.deepEqual(retry, { ...first, deduplicated: true });
  await counts(ids);
});
test('concurrent completions store one event and notification', async () => {
  const ids = await setup();
  const results = await Promise.all([complete(ids), complete({ ...ids, request: ids.request.toUpperCase() })]);
  assert.equal(results[0].notification_id, results[1].notification_id);
  await counts(ids);
});
