import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { TOOLS } from '../src/tools.js';

let fixture, c, connection, actor, party, client;
before(async () => {
  fixture = await acquireDisposablePostgres({ prefix: 'signature-binding-', snapshot: true });
  connection = fixture.connection;
  c = new pg.Client(connection);
  await c.connect();
  actor = { ...(await c.query("insert into actor(slug,kind,display_name) values('joe','human','Synthetic partner') on conflict(slug) do update set active=true returning id,slug")).rows[0], human: true };
  party = (await c.query("insert into party(kind,name,email,created_by,updated_by) values('person','Synthetic contact','sender@example.test',$1,$1) returning id", [actor.id])).rows[0].id;
  await c.query("insert into client_status(slug,label) values('fixture_active','Synthetic active') on conflict do nothing");
  client = (await c.query("insert into client(party_id,status,created_by,updated_by,roster_ref) values($1,'fixture_active',$2,$2,'C-999443') returning id", [party, actor.id])).rows[0].id;
});
after(async () => { try { await c?.end(); } finally { await fixture?.close(); } });
async function evidence() {
  const row = (await c.query('select id,version,email from party where id=$1', [party])).rows[0];
  return { idempotency_key: randomUUID(), subject: 'C-999443', subject_kind: 'party',
    kind: 'email_signature_observation', source: `outlook-signature/synthetic/${randomUUID()}`,
    observed_binding: { party_id: row.id, party_version: row.version, sender: 'sender@example.test', email: row.email },
    value: { party_id: row.id, party_version: row.version, field: 'phone', proposed: '555-0100' } };
}
async function finding(args, db = c) {
  await db.query('begin');
  try {
    const result = await TOOLS['record-finding'].handler(db, actor, args);
    await db.query('commit');
    return result;
  } catch (e) { await db.query('rollback'); throw e; }
}
function held(result) {
  assert.equal(result.ok, false);
  assert.equal(result.disposition, 'held');
  assert.equal(result.reason, 'email_signature_binding_changed');
}
async function empty(args) {
  assert.equal((await c.query('select count(*) from record_flag where source=$1', [args.source])).rows[0].count, '0');
  assert.equal((await c.query('select count(*) from tool_call where idempotency_key=$1', [args.idempotency_key])).rows[0].count, '0');
  assert.equal((await c.query("select count(*) from event where idempotency_key=$1", [args.idempotency_key])).rows[0].count, '0');
}

test('stale snapshot holds without receipts and current retry records once', async () => {
  const args = await evidence();
  await c.query('update party set version=version+1 where id=$1', [party]);
  held(await finding(args));
  await empty(args);
  const fresh = await evidence();
  fresh.source = args.source;
  const first = await finding(fresh);
  assert.equal(first.ok, true);
  assert.equal((await finding(fresh)).flag_id, first.flag_id);
  assert.equal((await c.query('select count(*) from record_flag where source=$1', [args.source])).rows[0].count, '1');
});

test('changed sender and ambiguous sender hold', async () => {
  const args = await evidence();
  await c.query("update party set email='other@example.test' where id=$1", [party]);
  held(await finding(args));
  await empty(args);
  await c.query("update party set email='sender@example.test' where id=$1", [party]);
  const second = (await c.query("insert into party(kind,name,email,created_by,updated_by) values('person','Synthetic second','sender@example.test',$1,$1) returning id", [actor.id])).rows[0].id;
  try { held(await finding(await evidence())); } finally { await c.query('delete from party where id=$1', [second]); }
});

test('changed role binding holds', async () => {
  const args = await evidence();
  await c.query('update client set party_id=null where id=$1', [client]);
  try { held(await finding(args)); await empty(args); }
  finally { await c.query('update client set party_id=$2 where id=$1', [client, party]); }
});

test('finding transaction excludes competing identity writes', async () => {
  const other = new pg.Client(connection);
  await other.connect();
  await c.query('begin');
  try {
    const args = await evidence();
    assert.equal((await TOOLS['record-finding'].handler(c, actor, args)).ok, true);
    for (const [sql, params] of [
      ['update party set version=version+1 where id=$1', [party]],
      ['update client set party_id=null where id=$1', [client]],
      ["insert into party(kind,name,email,created_by,updated_by) values('person','Synthetic competing','sender@example.test',$1,$1)", [actor.id]],
    ]) {
      await other.query('begin');
      await other.query("set local lock_timeout='100ms'");
      await assert.rejects(other.query(sql, params), e => e.code === '55P03');
      await other.query('rollback');
    }
    await c.query('commit');
    await other.query('update party set version=version+1 where id=$1', [party]);
    assert.equal((await finding(args)).replayed, true);
  } finally { await c.query('rollback'); await other.end(); }
});

test('waiting finding validates after a competing party change', async () => {
  const other = new pg.Client(connection);
  await other.connect();
  const args = await evidence();
  try {
    await other.query('begin');
    await other.query('update party set version=version+1 where id=$1', [party]);
    const waiting = finding(args);
    await other.query('commit');
    held(await waiting);
    await empty(args);
  } finally { await other.query('rollback'); await other.end(); }
});

test('missing binding and inconsistent evidence hold', async () => {
  const args = await evidence();
  held(await finding({ ...args, observed_binding: undefined }));
  await empty(args);
  held(await finding({ ...args, value: { ...args.value, party_id: party + 1 } }));
  await empty(args);
});
