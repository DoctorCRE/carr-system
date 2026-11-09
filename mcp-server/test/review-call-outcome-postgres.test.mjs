import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { Validator } from '@cfworker/json-schema';
import pg from 'pg';
import { TOOLS, ToolError, executeRegisteredTool } from '../src/tools.js';
import { publicError } from '../src/public-errors.ts';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';

let fixture, db, actor;
before(async () => {
  fixture = await acquireDisposablePostgres({ prefix: 'review-version-', snapshot: true });
  db = new pg.Client(fixture.connection);
  await db.connect();
  actor = { ...(await db.query("select id,slug from actor where slug='joe'")).rows[0], human: true };
});
after(async () => { try { await db?.end(); } finally { await fixture?.close(); } });

async function seed() {
  const session = randomUUID(), candidate = randomUUID();
  await db.query(`insert into capture_session(id,nonce,device_id,actor_id,mode,started_at,consent_announced_at,
    session_token_hash,expires_at,state,state_at,post_call) values($1::uuid,$1::text,'fixture',$2,'meeting',
    now(),now(),$1::text,now()+interval '1 day','done',now(),true)`, [session, actor.id]);
  await db.query(`insert into capture_candidate(id,session_id,idempotency_key,batch_hash,item_index,kind,payload,evidence_quote,confidence)
    values($1::uuid,$2,$1::text,'fixture',0,'next_step','{"action":"Original"}','Synthetic evidence',0.95)`, [candidate, session]);
  return { session, candidate };
}
async function version(session) {
  return (await db.query("select ops.resolve_action_card($1,'post_call_review',$2) target", [actor.id,session])).rows[0].target.source_version;
}
async function review(args, client = db) {
  await client.query('begin');
  try {
    await client.query('set local role carr_writer');
    const result = await executeRegisteredTool(client, actor, 'review-call-outcome', args);
    await client.query('commit');
    return result;
  } catch (error) { await client.query('rollback'); throw error; }
}
async function unchanged(session, key) {
  assert.equal((await db.query('select outcome_reviewed_at from capture_session where id=$1',[session])).rows[0].outcome_reviewed_at,null);
  assert.equal((await db.query('select count(*)::int n from tool_call where idempotency_key=$1',[key])).rows[0].n,0);
  assert.equal((await db.query('select count(*)::int n from event where idempotency_key=$1',[key])).rows[0].n,0);
}
const stale = error => {
  assert.equal(error.payload.error,'source_version_changed');
  assert.deepEqual(publicError(error,{env:{CORRELATION_ID:'20000000-0000-4000-8000-000000000001'}}),{
    error:'source_version_changed', correlation_id:'20000000-0000-4000-8000-000000000001',
    hint:'This call changed. Reopen the card and review the current outcome.',
  });
  return true;
};

test('changed candidate after card read refuses without session, event or receipt writes', async () => {
  const { session,candidate } = await seed(), key = randomUUID();
  const expected_source_version = await version(session);
  await db.query(`update capture_candidate set payload='{"action":"Unseen edit"}' where id=$1`,[candidate]);
  await assert.rejects(review({session_id:session, expected_source_version, idempotency_key:key}), stale);
  await unchanged(session,key);
  assert.equal((await review({session_id:session,expected_source_version:await version(session),idempotency_key:randomUUID()})).reviewed,true);
});

test('matching review stores one receipt and replay returns original receipt after source changes', async () => {
  const { session,candidate } = await seed();
  const args = {session_id:session, expected_source_version:await version(session), idempotency_key:randomUUID()};
  const first = await review(args);
  assert.equal(first.reviewed,true);
  await db.query(`update capture_candidate set payload='{"action":"Later edit"}' where id=$1`,[candidate]);
  assert.deepEqual(await review(args),{replayed:true,...first});
  assert.equal((await db.query('select count(*)::int n from tool_call where idempotency_key=$1',[args.idempotency_key])).rows[0].n,1);
  assert.equal((await db.query('select count(*)::int n from event where idempotency_key=$1',[args.idempotency_key])).rows[0].n,1);
});

test('legacy Home caller can omit version until its 2026-12-08 retirement', async () => {
  const {session} = await seed();
  assert.equal((await review({session_id:session,idempotency_key:randomUUID()})).reviewed,true);
});

for (const table of ['capture_candidate','capture_session']) {
  test(`concurrent ${table} edit committed during review is compared before write`, async () => {
    const {session,candidate} = await seed();
    const args = {session_id:session,expected_source_version:await version(session),idempotency_key:randomUUID()};
    const editor = new pg.Client(fixture.connection), writer = new pg.Client(fixture.connection);
    await editor.connect(); await writer.connect();
    let pending;
    try {
      await editor.query('begin');
      if (table === 'capture_candidate') await editor.query(`update capture_candidate set payload='{"action":"Concurrent edit"}' where id=$1`,[candidate]);
      else await editor.query("update capture_session set started_at=started_at+interval '1 second' where id=$1",[session]);
      pending = review(args,writer);
      const outcome = pending.then(value=>({value}),error=>({error}));
      await waitForLock(editor,writer);
      await editor.query('commit');
      const result = await outcome;
      assert.ok(result.error,'stale review must refuse'); stale(result.error);
      await unchanged(session,args.idempotency_key);
    } finally {
      await editor.query('rollback');
      await pending?.catch(()=>{});
      await editor.end(); await writer.end();
    }
  });
}

async function waitForLock(holder, waiter) {
  const deadline = Date.now()+5000;
  while (Date.now()<deadline) {
    if ((await db.query('select $1::int=any(pg_blocking_pids($2)) blocked',
      [holder.processID,waiter.processID])).rows[0].blocked) return;
    await new Promise(resolve=>setTimeout(resolve,10));
  }
  assert.fail('expected PostgreSQL lock wait');
}

test('candidate edits, inserts and same-key retries wait until review commits', async () => {
  const {session,candidate} = await seed();
  const args = {session_id:session,expected_source_version:await version(session),idempotency_key:randomUUID()};
  const connections = await Promise.all(Array.from({length:4},async()=>{
    const client = new pg.Client(fixture.connection); await client.connect(); return client;
  }));
  const [writer,editor,inserter,retry] = connections;
  let edits,inserts,replay;
  try {
    await writer.query('begin');
    await writer.query('set local role carr_writer');
    const first = await executeRegisteredTool(writer,actor,'review-call-outcome',args);
    assert.equal(first.reviewed,true);
    edits = editor.query(`update capture_candidate set payload='{"action":"After review"}' where id=$1`,[candidate]);
    inserts = inserter.query(`insert into capture_candidate(session_id,idempotency_key,batch_hash,item_index,kind,payload,evidence_quote,confidence)
      values($1,$2,'fixture',1,'next_step','{}','New evidence',0.95)`,[session,randomUUID()]);
    replay = review(args,retry);
    await Promise.all([waitForLock(writer,editor),waitForLock(writer,inserter),waitForLock(writer,retry)]);
    await writer.query('commit');
    await Promise.all([edits,inserts]);
    assert.deepEqual(await replay,{replayed:true,...first});
    assert.equal((await db.query('select payload from capture_candidate where id=$1',[candidate])).rows[0].payload.action,'After review');
    assert.equal((await db.query('select count(*)::int n from capture_candidate where session_id=$1',[session])).rows[0].n,2);
    assert.equal((await db.query('select count(*)::int n from event where idempotency_key=$1',[args.idempotency_key])).rows[0].n,1);
  } finally {
    await writer.query('rollback');
    await Promise.allSettled([edits,inserts,replay]);
    await Promise.all(connections.map(client=>client.end()));
  }
});

test('versioned write contract matches registered schema and public projection is closed', async () => {
  const contract = JSON.parse(await readFile(new URL('../contracts/review-call-outcome.v1.json',import.meta.url),'utf8'));
  assert.deepEqual(contract.inputSchema,TOOLS['review-call-outcome'].inputSchema);
  const {session} = await seed();
  const receipt = await review({session_id:session,expected_source_version:await version(session),idempotency_key:randomUUID()});
  assert.equal(new Validator(contract.responseSchema).validate(receipt).valid,true);
  const projected = publicError(new ToolError({error:'source_version_changed',
    source_version:'private',hint:'private',session_id:session}),{env:{CORRELATION_ID:'20000000-0000-4000-8000-000000000001'}});
  assert.deepEqual(Object.keys(projected).sort(),['correlation_id','error','hint']);
  assert.equal(new Validator(contract.refusalSchema).validate(projected).valid,true);
});
