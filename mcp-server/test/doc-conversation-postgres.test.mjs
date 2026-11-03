import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { readFileSync } from 'node:fs';
import { Validator } from '@cfworker/json-schema';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { TOOLS, executeRegisteredTool } from '../src/tools.js';

let fixture, pool, joe, dell;
const shutdowns = [];
before(async () => {
  fixture = await acquireDisposablePostgres({ prefix: 'doc-request-', snapshot: true });
  pool = new pg.Pool(fixture.connection);
  pool.on('connect', c => shutdowns.push(new Promise(resolve => c.once('end', resolve))));
  for (const slug of ['joe', 'dell']) {
    const actor = (await pool.query("insert into actor(slug,kind,display_name) values($1,'human','Synthetic partner') on conflict(slug) do update set active=true returning id,slug", [slug])).rows[0];
    if (slug === 'joe') joe = { ...actor, human: true }; else dell = { ...actor, human: true };
  }
});
after(async () => {
  try { await pool?.end(); await Promise.all(shutdowns); } finally { await fixture?.close(); }
});
async function setup() {
  const args = { idempotency_key: randomUUID(), conversation_id: randomUUID(), body: 'Synthetic queued ask' };
  await pool.query('insert into ops.doc_conversation(id,title,created_by_actor) values($1,$2,$3)', [args.conversation_id, 'Queued ask test', joe.id]);
  return args;
}
async function submit(args, actor = joe, { verified = actor.slug, direct = false } = {}) {
  const c = await pool.connect();
  try {
    await c.query('begin');
    await c.query('set local role carr_writer');
    await c.query("select set_config('carr.acting_actor_slug',$1,true), set_config('carr.verified_human_actor_slug',$2,true)", [actor.slug, verified]);
    const result = direct
      ? (await c.query('select ops.submit_doc_request($1::uuid,$2::uuid,$3::text) as result', [args.idempotency_key, args.conversation_id, args.body])).rows[0].result
      : await TOOLS['submit-doc-request'].handler(c, actor, args);
    await c.query('commit');
    return result;
  } catch (e) { await c.query('rollback'); throw e; } finally { c.release(); }
}
async function counts(args, n) {
  assert.equal((await pool.query('select count(*)::int n from ops.doc_conversation_turn where msg_id=$1', [args.idempotency_key])).rows[0].n, n);
  assert.equal((await pool.query('select count(*)::int n from ops.doc_request_receipt where idempotency_key=$1', [args.idempotency_key])).rows[0].n, n);
}
function accepted(args) {
  return { ok: true, contract: 'doc-request.v1', status: 'accepted', conversation_id: args.conversation_id, idempotency_key: args.idempotency_key, request_msg_id: args.idempotency_key };
}
function refused(result, reason) {
  assert.equal(result.ok, true);
  assert.equal(result.contract, 'doc-request.v1');
  assert.equal(result.status, 'refused');
  assert.equal(result.reason, reason);
}
test('partner submission stores one ask and contract receipt', async () => {
  const args = await setup();
  const result = await submit(args);
  assert.deepEqual(result, accepted(args));
  const contract = JSON.parse(readFileSync(new URL('../contracts/doc-request.v1.json', import.meta.url)));
  assert.equal(new Validator(contract.$defs.response).validate(result).valid, true);
  await counts(args, 1);
  const turn = (await pool.query('select role,body,origin_actor,origin_channel from ops.doc_conversation_turn where msg_id=$1', [args.idempotency_key])).rows[0];
  assert.deepEqual(turn, { role: 'human', body: args.body, origin_actor: 'joe', origin_channel: 'browser-human' });
});
test('immutable replay and body or conversation mismatch', async () => {
  const args = await setup();
  const result = await submit(args);
  assert.deepEqual(await submit(args), result);
  refused(await submit({ ...args, body: 'Changed' }), 'doc_request_key_reuse');
  const other = await setup();
  refused(await submit({ ...args, conversation_id: other.conversation_id }), 'doc_request_key_reuse');
  await counts(args, 1);
  assert.equal((await pool.query('select count(*)::int n from ops.doc_conversation_turn where conversation_id=$1', [other.conversation_id])).rows[0].n, 0);
  await assert.rejects(pool.query('update ops.doc_request_receipt set body=$1 where idempotency_key=$2', ['Changed', args.idempotency_key]), /immutable/);
});
test('concurrent duplicate submissions store one ask', async () => {
  const args = await setup();
  const results = await Promise.all(Array.from({ length: 4 }, () => submit(args)));
  for (const result of results) assert.deepEqual(result, accepted(args));
  await counts(args, 1);
});
test('distinct keys allocate distinct consecutive turns', async () => {
  const args = await setup();
  const other = { ...args, idempotency_key: randomUUID() };
  await Promise.all([submit(args), submit(other)]);
  await counts(args, 1); await counts(other, 1);
  assert.deepEqual((await pool.query('select sequence from ops.doc_conversation_turn where conversation_id=$1 order by sequence', [args.conversation_id])).rows.map(x => x.sequence), [0, 1]);
});
test('unauthorized and revoked access refuse before writes including replay', async () => {
  const args = await setup();
  refused(await submit(args, dell), 'doc_conversation_not_found');
  await counts(args, 0);
  await pool.query('insert into ops.doc_conversation_grant(conversation_id,grantee_actor,granted_by_actor) values($1,$2,$3)', [args.conversation_id, dell.id, joe.id]);
  await submit(args, dell);
  await pool.query('update ops.doc_conversation_grant set revoked_at=now() where conversation_id=$1', [args.conversation_id]);
  refused(await submit(args, dell), 'doc_conversation_not_found');
  const next = { ...args, idempotency_key: randomUUID() };
  refused(await submit(next, dell), 'doc_conversation_not_found');
  await counts(args, 1); await counts(next, 0);
});
test('inactive and unverified actors and non-partners cannot submit directly', async () => {
  const args = await setup();
  refused(await submit(args, joe, { verified: '', direct: true }), 'doc_request_partner_required');
  await pool.query('update actor set active=false where id=$1', [joe.id]);
  try { refused(await submit(args, joe, { direct: true }), 'doc_request_partner_required'); }
  finally { await pool.query('update actor set active=true where id=$1', [joe.id]); }
  const agent = (await pool.query("insert into actor(slug,kind,display_name) values('request-agent','automation','Synthetic agent') returning id,slug")).rows[0];
  refused(await submit(args, agent, { verified: 'joe', direct: true }), 'doc_request_partner_required');
  await counts(args, 0);
});
test('receipt insert failure rolls back ask and receipt together', async () => {
  const args = await setup();
  await pool.query(`create function ops.test_doc_request_failure() returns trigger language plpgsql as $$ begin raise exception 'synthetic receipt failure'; end $$;
    create trigger test_doc_request_failure before insert on ops.doc_request_receipt for each row execute function ops.test_doc_request_failure()`);
  try { await assert.rejects(submit(args), /synthetic receipt failure/); }
  finally { await pool.query('drop trigger test_doc_request_failure on ops.doc_request_receipt; drop function ops.test_doc_request_failure()'); }
  await counts(args, 0);
  assert.deepEqual(await submit(args), accepted(args));
});
test('partner-only registration and invalid body or key refuse without writes', async () => {
  const tool = TOOLS['submit-doc-request'];
  assert.equal(tool.write, true); assert.equal(tool.humanOnly, true); assert.equal(tool.humanCallerOnly, true);
  assert.equal(tool.authorityOnly, undefined);
  assert.deepEqual(Object.keys(tool.inputSchema.properties).sort(), ['body', 'conversation_id', 'idempotency_key']);
  const args = await setup();
  refused(await submit({ ...args, body: '   ' }), 'doc_conversation_body_invalid');
  refused(await submit({ ...args, idempotency_key: 'invalid' }), 'doc_request_key_invalid');
  await counts(args, 0);
});

test('registered dispatcher rejects machines and caller-controlled provenance before database use', async () => {
  const args = await setup();
  const c = { query() { throw new Error('unexpected database access'); } };
  await assert.rejects(executeRegisteredTool(c, { id: randomUUID(), slug: 'codex', human: false }, 'submit-doc-request', args), /human_only_verb_requires_verified_partner/);
  await assert.rejects(executeRegisteredTool(c, joe, 'submit-doc-request', { ...args, role: 'assistant' }));
  await assert.rejects(executeRegisteredTool(c, joe, 'submit-doc-request', { ...args, actor: 'dell' }));
  await counts(args, 0);
});
test('cross-actor key collision refuses instead of replaying another actor receipt', async () => {
  const args = await setup();
  await pool.query('insert into ops.doc_conversation_grant(conversation_id,grantee_actor,granted_by_actor) values($1,$2,$3)', [args.conversation_id, dell.id, joe.id]);
  await submit(args);
  refused(await submit(args, dell), 'doc_request_key_reuse');
  await counts(args, 1);
});
test('writer cannot edit receipts or call the authority append; reader cannot submit', async () => {
  const privileges = (await pool.query("select has_table_privilege('carr_writer','ops.doc_request_receipt','insert') i, has_table_privilege('carr_writer','ops.doc_request_receipt','update') u, has_function_privilege('carr_writer','ops.append_doc_conversation_turn(uuid,text,text,uuid,uuid,jsonb)','execute') a, has_function_privilege('carr_reader','ops.submit_doc_request(uuid,uuid,text)','execute') r")).rows[0];
  assert.deepEqual(privileges, { i: false, u: false, a: false, r: false });
});
