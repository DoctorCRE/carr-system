import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { TOOLS } from '../src/tools.js';

let fixture, pool, actor;
const clientShutdowns = [];
before(async () => {
  fixture = await acquireDisposablePostgres({ prefix: 'doc-completion-', snapshot: true });
  pool = new pg.Pool(fixture.connection);
  pool.on('connect', client => {
    clientShutdowns.push(new Promise(resolve => client.once('end', resolve)));
  });

  actor = { ...(await pool.query("insert into actor(slug,kind,display_name) values('joe','human','Synthetic partner') on conflict(slug) do update set active=true returning id,slug")).rows[0], human: true };
});
after(async () => {
  try {
    await pool?.end();
    await Promise.all(clientShutdowns);
  } finally { await fixture?.close(); }
});
async function setup() {
  const conversation = `a${randomUUID().slice(1)}`, request = `b${randomUUID().slice(1)}`, answer = `c${randomUUID().slice(1)}`;
  await pool.query('insert into ops.doc_conversation(id,title,created_by_actor) values ($1,$2,$3)', [conversation, 'Completion test', actor.id]);
  await pool.query("insert into ops.doc_conversation_turn(conversation_id,sequence,role,body,msg_id,origin_actor) values ($1,0,'human','question',$2,'joe'),($1,1,'assistant','answer',$3,'claude')", [conversation, request, answer]);
  return { conversation, request, answer };
}
async function complete(ids, extra = {}, client) {
  const c = client ?? await pool.connect();
  try {
    await c.query('begin');
    await c.query('set local role carr_authority');
    const result = await TOOLS['complete-doc-request'].handler(c, actor, { idempotency_key: randomUUID(), conversation_id: ids.conversation, request_msg_id: ids.request, outcome: 'answered', answer_msg_id: ids.answer, ...extra });
    await c.query('commit');
    return result;
  } catch (e) { await c.query('rollback'); throw e; } finally { if (!client) c.release(); }
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
test('overlapping calls with the same key both succeed and store one completion', async () => {
  const ids = await setup();
  const idempotency_key = randomUUID();
  const clients = await Promise.all([pool.connect(), pool.connect()]);
  try {
    const [a, b] = await Promise.all(clients.map(client => complete(ids, { idempotency_key }, client)));
    const first = a.replayed ? b : a;
    const retry = a.replayed ? a : b;
    assert.equal(first.replayed, undefined);
    assert.deepEqual(retry, { replayed: true, ...first });
    assert.equal(first.outcome, 'answered');
    assert.equal(first.request_msg_id, ids.request);
  } finally {
    clients.forEach(client => client.release());
  }
  await counts(ids);
  const receipts = await pool.query("select count(*)::int as n from tool_call where verb='complete-doc-request' and idempotency_key=$1", [idempotency_key]);
  assert.equal(receipts.rows[0].n, 1);
});
