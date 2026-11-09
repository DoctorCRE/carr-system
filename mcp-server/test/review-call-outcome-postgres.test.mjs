import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { executeRegisteredTool } from '../src/tools.js';
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
    now(),now(),'fixture',now()+interval '1 day','done',now(),true)`, [session, actor.id]);
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
      // Observe the writer waiting on the editor's real PostgreSQL lock, with a bounded deadline.
      pending = review(args,writer);
      const outcome = pending.then(value=>({value}),error=>({error}));
      const deadline = Date.now()+5000;
      let blocked = false;
      while (Date.now()<deadline) {
        blocked = (await db.query('select $1::int=any(pg_blocking_pids($2)) blocked',[editor.processID,writer.processID])).rows[0].blocked;
        if (blocked) break;
        await new Promise(resolve=>setTimeout(resolve,10));
      }
      assert.equal(blocked,true,'review must wait for the concurrent edit');
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
