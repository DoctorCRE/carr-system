import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { mkdtemp, mkdir, copyFile, writeFile, chmod, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { executeRegisteredTool } from '../src/tools.js';
import { publicError } from '../src/public-errors.ts';

let fixture, c, actor, connection;
before(async () => {
  fixture = await acquireDisposablePostgres({ prefix: 'complete-wr-', snapshot: true });
  connection = fixture.connection;
  c = new pg.Client(connection);
  await c.connect();

  actor = { ...(await c.query("insert into actor(slug,kind,display_name) values('joe','human','Synthetic partner') on conflict(slug) do update set active=true returning id,slug")).rows[0], human: true };
});
after(async () => { try { await c?.end(); } finally { await fixture?.close(); } });

async function command(name, args = {}, identity = actor, role = null, client = c) {
  await client.query('begin');
  try {
    if (role) await client.query(`set local role ${role}`);
    const result = await executeRegisteredTool(client, identity, name, args);
    await client.query('commit');
    return result;
  } catch (error) { await client.query('rollback'); throw error; }
}

let doctrine;
async function sourcedRequest(ref) {
  if (!doctrine) {
    const doc = (await c.query(`insert into public.doctrine_document(slug,title,content_class,visibility)
      values('completion-test','Synthetic completion','reference','shared') returning id`)).rows[0];
    const section = (await c.query(`insert into public.doctrine_section(document_id,section_key,title,ordinal,status,current_version)
      values($1,'origin','Synthetic origin',1,'active',1) returning id`,[doc.id])).rows[0];
    const revision = (await c.query(`insert into public.doctrine_revision(section_id,version,actor_id,body,plain_text,content_hash)
      values($1,1,$2,'{"text":"Synthetic"}','Synthetic',$3) returning id`,[section.id,actor.id,'b'.repeat(64)])).rows[0];
    await c.query('update public.doctrine_section set current_revision_id=$1 where id=$2',[revision.id,section.id]);
    doctrine={section:section.id,revision:revision.id};
  }
  await c.query(`insert into ops.work_request(ref,title,requester_actor,owner_actor,acceptance_criteria,
    capture_idempotency_key,organization_tenant_id,doctrine_section_id,doctrine_revision_id,sourced_capture_sequence,origin_ref)
    values($1,'Synthetic sourced completion','joe','builder','[{"id":"AC-1","text":"Synthetic criterion"}]',
      $2,'carr-internal',$3,$4,$5,'doctrine:completion-test#origin')`,
    [ref,randomUUID(),doctrine.section,doctrine.revision,Number(ref.slice(3))]);
}
async function request(ref, state = 'captured', owner = 'builder') {
  await c.query(`insert into ops.work_request(ref,title,state,requester_actor,owner_actor,acceptance_criteria)
    values($1,'Synthetic completion',$2,'joe',$3,'[{"id":"AC-1","text":"Synthetic criterion"}]')`, [ref,state,owner]);
}
function completion(ref) {
  return { idempotency_key: randomUUID(), human_ref: ref, base_version: 1,
    change_refs: [{ pr_url: 'https://github.com/DoctorCRE/carr-system/pull/123', merge_commit_sha: 'a'.repeat(40) }],
    criterion_evidence: [{ id: 'AC-1', evidence: 'Synthetic passing check' }], user_facing: false };
}
async function attestedRequest(ref, patch = {}) {
  const attestor=(await c.query("insert into actor(slug,kind,display_name) values('dell','human','Synthetic independent attestor') on conflict(slug) do update set active=true returning id")).rows[0].id;
  const {idempotency_key,human_ref,base_version,...baseEvidence}=completion(ref);
  const evidence={...baseEvidence,...patch};
  const capsule={...evidence,acceptance_predicates:['AC-1: Synthetic passing check'],
    change_ref:evidence.change_refs.map(change=>`${change.pr_url}#${change.merge_commit_sha}`).join('\n'),attested_by:patch.attested_by ?? attestor};
  await c.query(`insert into ops.work_request(ref,title,state,requester_actor,owner_actor,organization_tenant_id,capture_idempotency_key,sourced_capture_sequence,origin_ref,acceptance_criteria,completion_evidence,verification_accepted_at)
    values($1,'Synthetic attested work','captured','joe','joe','carr-internal',$3,$4,$5,'[{"id":"AC-1","text":"Synthetic criterion"}]',$2::jsonb,now())`,[ref,JSON.stringify(capsule),randomUUID(),Number(ref.slice(3)),'record:loop:'+ref]);
  return {attestor,evidence};
}
test('complete-work-request closes built work and replays without a second event', async () => {
  await request('WR-999001');
  const args = completion('WR-999001');
  const out = await command('complete-work-request', args, actor, 'carr_writer');
  assert.equal(out.state, 'confirmed_closed');
  assert.equal(out.completion_kind, 'built');
  assert.equal(out.version, 2);
  assert.deepEqual(await command('complete-work-request', args, actor, 'carr_writer'), { ...JSON.parse(JSON.stringify(out)), replayed: true });
  const row = (await c.query("select * from ops.work_request where ref='WR-999001'")).rows[0];
  assert.equal(row.completion_evidence.attested_by, actor.id);
  assert.equal(row.completion_evidence.criterion_evidence[0].evidence, 'Synthetic passing check');
  assert.equal((await c.query('select count(*) from event where idempotency_key=$1', [args.idempotency_key])).rows[0].count, '1');
});

for (const [label, ref, modify, expected] of [
  ['unknown reference','WR-999099',()=>{},'work_request_not_found'],
  ['stale version','WR-999002',a=>{a.base_version=2;},'version_conflict'],
  ['terminal state','WR-999003',()=>{},'work_request_terminal_state'],
  ['invalid completion','WR-999013',a=>{a.base_version=0;},'invalid_completion'],
  ['active execution','WR-999014',()=>{},'work_request_not_completable'],
  ['missing criterion','WR-999004',a=>{a.criterion_evidence=[];},'missing_criterion_evidence'],
  ['missing change','WR-999005',a=>{a.change_refs=[];},'missing_change_ref'],
  ['scalar changes','WR-999008',a=>{a.change_refs=[7];},'missing_change_ref'],
  ['scalar evidence','WR-999009',a=>{a.criterion_evidence=[7];},'missing_criterion_evidence'],
  ['boolean evidence','WR-999010',a=>{a.criterion_evidence[0].evidence=true;},'missing_criterion_evidence'],
  ['bad change URL','WR-999011',a=>{a.change_refs[0].pr_url='https://example.com/pull/1';},'missing_change_ref'],
  ['bad merge SHA','WR-999012',a=>{a.change_refs[0].merge_commit_sha='abc';},'missing_change_ref'],
  ['missing journey','WR-999006',a=>{a.user_facing=true;},'missing_user_journey_ref'],
  ['self attestation','WR-999007',()=>{},'completion_capsule_refused'],
]) {
  test(`complete-work-request refuses ${label} without closing or saving replay`, async () => {
    if (label!=='unknown reference') await request(ref, 'captured', label==='self attestation' ? 'joe' : 'builder');
    if (label==='terminal state') await c.query("update ops.work_request set state='declined',exit_reason='Synthetic obsolete',closed_at=now() where ref=$1", [ref]);
    if (label==='active execution') await c.query("update ops.work_request set state='needs_joe' where ref=$1",[ref]);
    const args=completion(ref); modify(args);
    await assert.rejects(command('complete-work-request',args,actor,'carr_writer'),error=>{
      assert.equal(error.payload?.error,expected);
      assert.equal(publicError(error).error,expected);
      return true;
    });
    assert.equal((await c.query('select count(*) from tool_call where idempotency_key=$1',[args.idempotency_key])).rows[0].count,'0');
    assert.equal((await c.query("select count(*) from ops.work_request where ref=$1 and state='confirmed_closed'",[ref])).rows[0].count,'0');
  });
}

test('complete-work-request completes sourced capture through its immutability guard', async () => {
  await sourcedRequest('WR-999020');
  const out = await command('complete-work-request', completion('WR-999020'), actor, 'carr_writer');
  assert.equal(out.state,'confirmed_closed');
  assert.equal(out.version,2);
});
for (const state of ['triaged','ready','verification']) {
  test(`complete-work-request accepts ${state} work`, async()=>{
    const ref={triaged:'WR-999021',ready:'WR-999022',verification:'WR-999023'}[state];
    await request(ref);
    await c.query('update ops.work_request set state=$1 where ref=$2',[state,ref]);
    assert.equal((await command('complete-work-request', completion(ref), actor, 'carr_writer')).state,'confirmed_closed');
  });
}
test('repair migration reopens exactly eligible named rows, preserves receipts, and replays without events', async t=>{
  const shape=(await c.query("select pg_get_constraintdef(oid) as definition from pg_constraint where conrelid='ops.work_request'::regclass and conname='work_request_sourced_capture_shape'")).rows[0].definition;
  t.after(()=>c.query(`alter table ops.work_request drop constraint work_request_sourced_capture_shape,
    add constraint work_request_sourced_capture_shape ${shape}`));
  const refs=['WR-000414','WR-000436','WR-000499','WR-000500','WR-000501','WR-000507','WR-000510','WR-000520','WR-000542','WR-000545','WR-000546','WR-000551','WR-000552','WR-000553'];
  for (const [i,ref] of refs.entries()) {
    await sourcedRequest(ref);
    await c.query('select ops.decline_sourced_work_request($1,1,$2,$3,$4)',
      [ref,i%2 ? 'Done: synthetic built work' : 'Done on main synthetic',actor.slug,randomUUID()]);
  }
  await sourcedRequest('WR-999024');
  await c.query('select ops.decline_sourced_work_request($1,1,$2,$3,$4)',
    ['WR-999024','Done: unlisted synthetic',actor.slug,randomUUID()]);
  const migration=readFileSync(new URL('../../migrations/1006_complete_work_request.sql',import.meta.url),'utf8');
  await c.query(migration);
  const rows=(await c.query('select ref,state,closed_at,exit_reason,version from ops.work_request where ref=any($1) order by ref',[refs])).rows;
  assert.equal(rows.length,14);
  for (const row of rows) assert.deepEqual(row,{ref:row.ref,state:'captured',closed_at:null,exit_reason:null,version:3});
  assert.equal((await c.query("select count(*) from ops.work_request_withdrawal_receipt r join ops.work_request w on w.id=r.work_request_id where w.ref=any($1)",[refs])).rows[0].count,'14');
  assert.equal((await c.query("select state from ops.work_request where ref='WR-999024'")).rows[0].state,'declined');
  const events=await c.query("select count(*) from event where verb='repair-built-work-request-decline'");
  assert.equal(events.rows[0].count,'14');
  await c.query(migration);
  assert.equal((await c.query("select count(*) from event where verb='repair-built-work-request-decline'")).rows[0].count,'14');
  assert.equal((await command('complete-work-request',{...completion(refs[0]),base_version:3},actor,'carr_writer')).state,'confirmed_closed');
});

test('completion refuses caller attestor fields and unsponsored actors',async()=>{
  await request('WR-999026');
  await assert.rejects(command('complete-work-request',{...completion('WR-999026'),attestor:'joe'},actor,'carr_writer'),e=>e.payload?.error==='unregistered_operation_fields');
  await assert.rejects(command('complete-work-request',completion('WR-999026'),{...actor,human:false,slug:'synthetic-unsponsored'},'carr_writer'),e=>e.payload?.error==='sponsored_actor_required');
});
test('completion key cannot be reused for different evidence',async()=>{
  await request('WR-999027');
  const args=completion('WR-999027');
  await command('complete-work-request',args,actor,'carr_writer');
  args.criterion_evidence[0].evidence='Different synthetic evidence';
  await assert.rejects(command('complete-work-request',args,actor,'carr_writer'),e=>e.payload?.error==='key_reuse');
});

test('user-facing completion preserves an exercised journey and refuses duplicate criterion evidence',async()=>{
  await request('WR-999028');
  const args=completion('WR-999028'); args.user_facing=true; args.user_journey_ref='Synthetic exercised journey';
  const duplicate={...args,criterion_evidence:[...args.criterion_evidence,...args.criterion_evidence]};
  await assert.rejects(command('complete-work-request',duplicate,actor,'carr_writer'),e=>e.payload?.error==='missing_criterion_evidence');
  assert.equal((await command('complete-work-request',args,actor,'carr_writer')).state,'confirmed_closed');
  const row=(await c.query("select completion_evidence from ops.work_request where ref='WR-999028'")).rows[0];
  assert.equal(row.completion_evidence.user_journey_ref,'Synthetic exercised journey');
});


test('completion refuses open slices and accepts an all-retired plan', async () => {
  const ref = 'WR-999030';
  await sourcedRequest(ref);
  const id = (await c.query('select id from ops.work_request where ref=$1', [ref])).rows[0].id;
  await c.query("insert into ops.work_request_slice(work_request_id,ordinal,title) values($1,1,'Build scope'),($1,2,'Retired scope')", [id]);
  const args = completion(ref);
  await assert.rejects(command('complete-work-request', args, actor, 'carr_writer'), error => {
    assert.equal(publicError(error).error, 'work_request_open_slices'); return true;
  });
  const retire = async slice => command('close-work-request-slice', { work_request: ref, slice, base_version: (await command('work-request-card', { work_request: ref }, actor, 'carr_reader')).slices[slice - 1].version, outcome: 'retired', reason: 'Scope is no longer needed.', idempotency_key: randomUUID() }, actor, 'carr_writer');
  await retire(1);
  await retire(2);
  assert.equal((await command('work-request-card',{work_request:ref},actor,'carr_reader')).ready_to_close,false);
  args.base_version = (await command('work-request-card', { work_request: ref }, actor, 'carr_reader')).version;
  assert.equal((await command('complete-work-request', args, actor, 'carr_writer')).state, 'confirmed_closed');
  for (const [name, extra] of [['close-work-request-slice', { slice: 1, base_version: 4, outcome: 'retired', reason: 'More scope.' }],
    ['plan-work-request-slices', { slices: ['Build scope', 'Retired scope', 'New scope'] }]])
    await assert.rejects(command(name, { work_request: ref, idempotency_key: randomUUID(), ...extra }, actor, 'carr_writer'),
      error => publicError(error).error === 'work_request_terminal_state');
});

for (const preverified of [false,true]) test(`close command discovers merged Cards with ${preverified ? 'stored attestation' : 'manual attestation'}`, async () => {
  const ref = preverified ? 'WR-999071' : 'WR-999070';
  const shipped=[1,2].map(n=>({pr_url:`https://github.com/DoctorCRE/carr-system/pull/${120+n}`,merge_commit_sha:String(n).repeat(40)}));
  if (preverified) await attestedRequest(ref,{change_refs:shipped}); else await sourcedRequest(ref);
  await command('plan-work-request-slices', { work_request: ref, idempotency_key: randomUUID(), slices: ['First', 'Second'] });
  const root = await mkdtemp(path.join(tmpdir(), 'close-wr-cli-'));
  try {
    await mkdir(path.join(root, 'bin'));
    await mkdir(path.join(root, 'mcp-server/src'), { recursive: true });
    await copyFile(new URL('../../bin/close-work-request', import.meta.url), path.join(root, 'bin/close-work-request'));
    await copyFile(new URL('../src/progress-address.v1.json', import.meta.url), path.join(root, 'mcp-server/src/progress-address.v1.json'));
    await writeFile(path.join(root, 'adapter.mjs'), `
const { executeRegisteredTool } = await import(${JSON.stringify(new URL('../src/tools.js', import.meta.url).href)});
const { default: pg } = await import(${JSON.stringify(new URL('../node_modules/pg/lib/index.js', import.meta.url).href)});
const { appendFileSync } = await import('node:fs');
if (process.argv[2] !== 'call') throw new Error('expected call');
const verb = process.argv[3], args = JSON.parse(process.argv[4]);
appendFileSync(${JSON.stringify(path.join(root, 'calls.jsonl'))}, JSON.stringify({ verb, args }) + '\\n');
const client = new pg.Client(${JSON.stringify(connection)});
await client.connect();
try {
  await client.query('begin');
  await client.query('set local role ' + (verb === 'work-request-card' ? 'carr_reader' : 'carr_writer'));
  const out = await executeRegisteredTool(client, ${JSON.stringify(actor)}, verb, args);
  await client.query('commit');
  console.log(JSON.stringify(out));
} finally { await client.end(); }
`);
    await writeFile(path.join(root, 'run.sh'), `#!/usr/bin/env python3
import os, sys
os.execvp('node', ['node', ${JSON.stringify(path.join(root, 'adapter.mjs'))}, *sys.argv[1:]])
`);
    const merges = [1, 2].map(n => ({ body: `Card: WR${Number(ref.slice(3))}.S${n}.J1`, state: 'MERGED',
      url: `https://github.com/DoctorCRE/carr-system/pull/${120 + n}`, mergeCommit: { oid: String(n).repeat(40) } }));
    await writeFile(path.join(root, 'prs.json'), JSON.stringify(merges));
    await writeFile(path.join(root, 'bin/gh'), `#!/usr/bin/env python3
import pathlib, sys
assert sys.argv[1:3] == ['pr', 'list']
print(pathlib.Path(${JSON.stringify(root)}, 'prs.json').read_text() if sys.argv[sys.argv.index('--repo')+1] == 'DoctorCRE/carr-system' else '[]')
`);
    await chmod(path.join(root, 'run.sh'), 0o755);
    await chmod(path.join(root, 'bin/gh'), 0o755);
    await writeFile(path.join(root, 'evidence.json'), JSON.stringify({ user_facing: false,
      criterion_evidence: [{ id: 'AC-1', evidence: 'Synthetic closure exercised through the command and real registered verbs' }] }));
    const cli = (...args) => promisify(execFile)('python3', [path.join(root, 'bin/close-work-request'), ref,
      '--completion-evidence', path.join(root, 'evidence.json'), ...args], {
      env: { ...process.env, PATH: path.join(root, 'bin') + path.delimiter + process.env.PATH },
    });
    const seen = () => readFileSync(path.join(root, 'calls.jsonl'), 'utf8').trim().split('\n').map(line => JSON.parse(line).verb);
    const preview = JSON.parse((await cli()).stdout);
    assert.deepEqual(preview.calls.map(call => call.verb), ['close-work-request-slice', 'close-work-request-slice', 'complete-work-request']);
    assert.deepEqual(seen(), ['work-request-card']);
    assert.ok((await command('work-request-card', { work_request: ref })).slices.every(slice => slice.outcome === 'open'));
    const applied = (await cli('--apply')).stdout;
    const last=JSON.parse(applied.trim().split('\n').at(-1)).result;
    assert.equal(last.state ?? last.work_request_state, 'confirmed_closed');
    assert.deepEqual(seen().slice(1), ['work-request-card', 'close-work-request-slice', 'close-work-request-slice', ...(preverified?[]:['complete-work-request'])]);
    const card = await command('work-request-card', { work_request: ref });
    assert.equal(card.state, 'confirmed_closed');
    assert.ok(card.slices.every(slice => slice.outcome === 'done'));
    assert.equal(card.slices[1].change_refs[0].merge_commit_sha, '2'.repeat(40));
    assert.deepEqual(JSON.parse((await cli('--apply')).stdout).calls, []);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('last merged slice awaits independent attestation when none is recorded', async () => {
  const ref = 'WR-999080';
  await sourcedRequest(ref);
  await command('plan-work-request-slices', {work_request:ref,slices:['First','Last'],idempotency_key:randomUUID()},actor,'carr_writer');
  const close = slice => command('close-work-request-slice', {work_request:ref,slice,base_version:1,outcome:'done',
    change_refs:completion(ref).change_refs,idempotency_key:randomUUID()},actor,'carr_writer');
  assert.equal((await command('work-request-card',{work_request:ref},actor,'carr_reader')).ready_to_close,false);
  await close(1);
  assert.equal((await command('work-request-card',{work_request:ref},actor,'carr_reader')).ready_to_close,false);
  const args={work_request:ref,slice:2,base_version:1,outcome:'done',change_refs:completion(ref).change_refs,idempotency_key:randomUUID()};
  await command('close-work-request-slice',args,actor,'carr_writer');
  const card=await command('work-request-card',{work_request:ref},actor,'carr_reader');
  assert.equal(card.ready_to_close,true);
  assert.equal(card.state,'captured');
  const queue=await command('work-queue',{},actor,'carr_reader');
  assert.ok(queue.ready_to_close.some(row=>row.human_ref===ref));
  await command('close-work-request-slice',args,actor,'carr_writer');
  assert.equal((await command('work-request-card',{work_request:ref},actor,'carr_reader')).ready_to_close,true);
  const done=completion(ref);
  done.base_version=card.version;
  assert.equal((await command('complete-work-request',done,actor,'carr_writer')).state,'confirmed_closed');
  assert.equal((await command('work-request-card',{work_request:ref},actor,'carr_reader')).ready_to_close,false);
});

test('appending scope removes derived readiness until the added slice ships', async()=>{
  const ref='WR-999081';
  await sourcedRequest(ref);
  const plan=slices=>command('plan-work-request-slices',{work_request:ref,slices,idempotency_key:randomUUID()},actor,'carr_writer');
  const close=slice=>command('close-work-request-slice',{work_request:ref,slice,base_version:1,outcome:'done',change_refs:completion(ref).change_refs,idempotency_key:randomUUID()},actor,'carr_writer');
  const card=()=>command('work-request-card',{work_request:ref},actor,'carr_reader');
  await plan(['Original']); await close(1);
  assert.equal((await card()).ready_to_close,true);
  await plan(['Original','Added']);
  assert.equal((await card()).ready_to_close,false);
  await close(2);
  assert.equal((await card()).ready_to_close,true);
});

test('shipped in-progress work is listed for closure while completion keeps its lifecycle guard',async()=>{
  const ref='WR-999082';
  await c.query(`insert into ops.work_request(ref,title,state,requester_actor,owner_actor,organization_tenant_id,program_key,program_ordinal,acceptance_criteria)
    values($1,'Synthetic in-progress work','captured','joe','joe','carr-internal','carr-ai-engineering-suite-v1',9990,'[{"id":"AC-1","text":"Synthetic criterion"}]')`,[ref]);
  await c.query("update ops.work_request set state='in_progress' where ref=$1",[ref]);
  await command('plan-work-request-slices',{work_request:ref,slices:['Shipped scope'],idempotency_key:randomUUID()},actor,'carr_writer');
  await command('close-work-request-slice',{work_request:ref,slice:1,base_version:1,outcome:'done',change_refs:completion(ref).change_refs,idempotency_key:randomUUID()},actor,'carr_writer');
  const card=await command('work-request-card',{work_request:ref},actor,'carr_reader');
  assert.equal(card.ready_to_close,true);
  assert.equal(card.state,'in_progress');
  await assert.rejects(command('complete-work-request',{...completion(ref),base_version:card.version},actor,'carr_writer'),e=>e.payload?.error==='work_request_not_completable');
});

test('last merged slice auto-closes an independently attested WR in the slice transaction',async()=>{
  const ref='WR-999083';
  const {attestor,evidence}=await attestedRequest(ref);
  await command('plan-work-request-slices',{work_request:ref,slices:['First','Last'],idempotency_key:randomUUID()},actor,'carr_writer');
  const close=slice=>({work_request:ref,slice,base_version:1,outcome:'done',change_refs:evidence.change_refs,idempotency_key:randomUUID()});
  await command('close-work-request-slice',close(1),actor,'carr_writer');
  assert.equal((await command('work-request-card',{work_request:ref},actor,'carr_reader')).state,'captured');
  const args=close(2);
  let inside;
  await c.query('begin');
  try {
    await c.query('set local role carr_writer');
    await executeRegisteredTool(c,actor,'close-work-request-slice',args);
    inside=(await c.query('select state,completion_evidence from ops.work_request where ref=$1',[ref])).rows[0];
  } finally {await c.query('rollback');}
  assert.equal(inside.state,'confirmed_closed');
  assert.equal(inside.completion_evidence.attested_by,attestor);
  const rolledBack=await command('work-request-card',{work_request:ref},actor,'carr_reader');
  assert.equal(rolledBack.state,'captured');
  assert.equal(rolledBack.slices[1].outcome,'open');
  await command('close-work-request-slice',args,actor,'carr_writer');
  const finished=(await c.query('select state,version,completion_kind,completion_evidence from ops.work_request where ref=$1',[ref])).rows[0];
  assert.equal(finished.state,'confirmed_closed');assert.equal(finished.completion_kind,'built');
  assert.equal(finished.completion_evidence.attested_by,attestor);
  assert.deepEqual(finished.completion_evidence.criterion_evidence,evidence.criterion_evidence);
  await command('close-work-request-slice',args,actor,'carr_writer');
  assert.equal((await c.query('select version from ops.work_request where ref=$1',[ref])).rows[0].version,finished.version);
});

for(const [label,ref,error] of [['self-attestation','WR-999084','completion_capsule_refused'],['missing criterion evidence','WR-999085','missing_criterion_evidence']]) {
  test(`automatic slice completion preserves the refusal for ${label}`,async()=>{
    const {evidence}=await attestedRequest(ref,label==='self-attestation'?{attested_by:actor.id}:{criterion_evidence:[]});
    await command('plan-work-request-slices',{work_request:ref,slices:['Only'],idempotency_key:randomUUID()},actor,'carr_writer');
    const args={work_request:ref,slice:1,base_version:1,outcome:'done',change_refs:evidence.change_refs,idempotency_key:randomUUID()};
    await assert.rejects(command('close-work-request-slice',args,actor,'carr_writer'),e=>e.payload?.error===error);
    const card=await command('work-request-card',{work_request:ref},actor,'carr_reader');
    assert.equal(card.state,'captured');assert.equal(card.slices[0].outcome,'open');
    assert.equal((await c.query('select count(*) from tool_call where idempotency_key=$1',[args.idempotency_key])).rows[0].count,'0');
  });
}
