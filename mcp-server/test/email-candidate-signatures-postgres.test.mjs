import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { executeRegisteredTool } from '../src/tools.js';
let fixture, c, actor, pool;
before(async () => {
  fixture = await acquireDisposablePostgres({ prefix: 'candidate-signatures-', snapshot: true });
  c = new pg.Client(fixture.connection); await c.connect();
  actor = (await c.query("select id,slug from actor where slug='joe'")).rows[0];
  actor.human = true;
  const reply = await call('propose-email-lead', {address:'candidate@synthetic.test',summary:'Synthetic request',first_seen_at:'2026-10-01T12:00:00Z',message_ref:'synthetic-1'});
  pool = reply.pool_id;
});
after(async () => { try { await c?.end(); } finally { await fixture?.close(); } });
async function call(verb, args) {
  await c.query('begin');
  try {
    await c.query('set local role carr_writer');
    const reply = await executeRegisteredTool(c,actor,verb,{idempotency_key:randomUUID(),...args});
    await c.query('commit'); return reply;
  } catch (error) { await c.query('rollback'); throw error; }
}
const observation = (source, digest='a') => ({source_id:source.repeat(64),item_ids:[source],raw_digest:digest.repeat(64),extraction_version:'email-contact-candidates/v3',extraction:{verified:false},observed_at:'2026-10-01T12:00:00Z'});
const append = observations => call('observe-email-candidate-signatures',{address:'candidate@synthetic.test',mailbox_account:'synthetic@example.test',observations});
const read = () => executeRegisteredTool(c,actor,'read-email-candidate-signatures',{pool_id:pool});
test('two sources, duplicates and lost acknowledgements converge',async () => {
  const args = {idempotency_key:randomUUID(),address:'candidate@synthetic.test',mailbox_account:'synthetic@example.test',observations:[observation('a'),observation('b')]};
  const first = await call('observe-email-candidate-signatures',args);
  assert.equal(first.inserted,2);
  assert.equal((await call('observe-email-candidate-signatures',args)).replayed,true);
  assert.equal((await append([observation('a')])).inserted,0);
  const page = await read();
  assert.equal(page.contract,'email-candidate-signatures.v1');
  assert.equal(page.observations.length,2);
  assert.equal(page.source_invalid,false);
  assert.equal(page.candidate_version,first.candidate_version);
  assert.ok(page.observations.every(o=>o.candidate_version>0));
});
test('source edit retains history and prevents pending promotion',async () => {
  const before = await read();
  await append([observation('a','c')]);
  const page = await read();
  assert.equal(page.observations.length,3);
  assert.equal(page.observations.filter(o=>o.invalidated_at).length,1);
  assert.equal(page.source_invalid,true);
  assert.ok(page.evidence_version>before.evidence_version);
  await assert.rejects(c.query("update candidate_pool set status='confirmed' where id=$1",[pool]),e=>e.code==='23514' && e.message.includes('email_candidate_source_invalid'));
  await assert.rejects(call('confirm-email-lead',{pool_id:pool,base_version:page.candidate_version,applies_to:'new_matter'}));
});
test('rollback before commit, aliases, pagination and unverified boundary',async () => {
  await c.query('begin');
  await executeRegisteredTool(c,actor,'observe-email-candidate-signatures',{idempotency_key:randomUUID(),address:'candidate@synthetic.test',mailbox_account:'synthetic@example.test',observations:[observation('e')]});
  await c.query('rollback');
  assert.equal((await read()).observations.length,3);
  const alias={...observation('b'),item_ids:['b','alias-b']};
  assert.equal((await append([alias])).inserted,0);
  const page=await executeRegisteredTool(c,actor,'read-email-candidate-signatures',{pool_id:pool,limit:1});
  assert.equal(page.observations.length,1);
  assert.ok(page.next);
  const rest=await executeRegisteredTool(c,actor,'read-email-candidate-signatures',{pool_id:pool,after:page.next});
  assert.equal(rest.observations.length,2);
  await assert.rejects(append([{...observation('f'),extraction:{verified:true}}]),e=>e.payload?.error==='email_candidate_evidence_invalid');
  const counts=await executeRegisteredTool(c,actor,'read-email-candidate-signature-counts',{since:'2026-10-01T00:00:00Z',until:'2026-10-02T00:00:00Z'});
  assert.equal(counts.observations,3);
  assert.equal(counts.duplicate_observations,0);
  assert.equal(counts.sources,2);
  assert.equal(counts.invalidated_sources,1);
  assert.deepEqual(counts.observations_per_candidate,[{observations:3,sources:2,candidates:1}]);
  await c.query('set role carr_reader');
  try {
    assert.equal((await read()).observations.length,3);
    assert.equal((await executeRegisteredTool(c,actor,'read-email-candidate-signature-counts',{since:'2026-10-01T00:00:00Z',until:'2026-10-02T00:00:00Z'})).observations,3);
  } finally { await c.query('reset role'); }
});
test('installed lifecycle handles candidate-only sources and terminal retries',async () => {
  const args={email_identity:{mailbox_owner:'joe',mailbox_account:'synthetic@example.test',item_id:'alias-b'},base_version:0,state:'deleted',observation_id:randomUUID(),observed_at:'2026-10-01T12:00:00Z',method:'outlook_tombstone'};
  await call('record-email-lifecycle',args);
  assert.equal((await call('record-email-lifecycle',args)).replayed,true);
  const page=await read();
  assert.ok(page.observations.find(o=>o.source_id==='b'.repeat(64)).invalidated_at);
  const held = await append([observation('b','d')]);
  assert.equal(held.inserted,0);
  assert.equal(held.source_invalid,true);
  const other = await call('propose-email-lead',{address:'alias@synthetic.test',summary:'Synthetic request',first_seen_at:'2026-10-01T12:00:00Z',message_ref:'synthetic-alias'});
  const terminal = await call('observe-email-candidate-signatures',{address:'alias@synthetic.test',mailbox_account:'synthetic@example.test',observations:[observation('b','d')]});
  assert.equal(terminal.source_invalid,true);
  const otherEvidence=await executeRegisteredTool(c,actor,'read-email-candidate-signatures',{pool_id:other.pool_id});
  assert.equal(otherEvidence.source_invalid,true);
  assert.equal(otherEvidence.observations.length,0);
});
test('decline persists and ownership protects readback',async () => {
  await c.query("update candidate_pool set status='declined',declined_at=now(),declined_by=$1,decline_reason='Synthetic' where id=$2",[actor.id,pool]);
  assert.equal((await append([observation('d')])).status,'declined');
  assert.equal((await read()).observations.length,3);
  await assert.rejects(executeRegisteredTool(c,{...actor,slug:'dell'},'read-email-candidate-signatures',{pool_id:pool}));
});
