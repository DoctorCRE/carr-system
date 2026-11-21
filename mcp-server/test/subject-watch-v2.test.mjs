import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync, execFileSync } from 'node:child_process';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.ts';
import { sweepSubjectWatches } from '../src/subject-watch-scheduler.ts';
import { Validator } from '@cfworker/json-schema';
import { subjectWatchV2Tools, parseSubjectWatchV2 } from '../src/subject-watches.js';
import { subjectWatchPolicySchema, validSubjectWatchPolicy } from '../contracts/subject-watch.v2.ts';

test('recorded registered response passes the production parser; malformed policies refuse',()=>{
  const fixture=JSON.parse(readFileSync(new URL('../contracts/subject-watch.v2.fixture.json',import.meta.url)));
  assert.equal(parseSubjectWatchV2(fixture).schema_version,'subject-watch.v2');
  for(const mutate of [
    value=>value.watch.actor_id='dell',
    value=>value.watch.lifecycle_version=0,
    value=>value.watch.policy.change_kinds=['unknown'],
    value=>delete value.watch.policy.include_self_changes,
    value=>value.watch.policy.lease_threshold_days=[-1],
  ]) {
    const invalid=structuredClone(fixture);mutate(invalid);
    assert.throws(()=>parseSubjectWatchV2(invalid),/invalid_subject_watch_v2/);
  }
  assert.throws(()=>parseSubjectWatchV2({ok:true,schema_version:'subject-watch.v2'}));
});

test('policy requires explicit lease thresholds and defaults self changes in the producer',()=>{
  const validator=new Validator(subjectWatchPolicySchema);
  for(const policy of [{change_kinds:[]},
    {change_kinds:['reply_arrived','reply_arrived']},{change_kinds:['reply_arrived'],actor_id:'joe'}])
    assert.equal(validator.validate(policy).valid,false);
  assert.equal(validator.validate({change_kinds:['reply_arrived']}).valid,true);
  assert.equal(validSubjectWatchPolicy({change_kinds:['lease_threshold_crossed']}),false);
  assert.equal(validSubjectWatchPolicy({change_kinds:['lease_threshold_crossed'],lease_threshold_months:[12]}),true);
  assert.equal(validSubjectWatchPolicy({change_kinds:['lease_threshold_crossed'],lease_threshold_months:[121]}),false);
});

test('v2 schemas exclude actor overrides and require generation for lifecycle mutations',()=>{
  const tools=subjectWatchV2Tools({});
  assert.equal(Object.keys(tools).length,7);
  for(const [name,verb] of Object.entries(tools)) {
    assert.equal(verb.inputSchema.additionalProperties,false);
    assert.equal(Object.hasOwn(verb.inputSchema.properties,'actor_id'),false);
    if(verb.write) assert.ok(verb.inputSchema.required.includes('idempotency_key'));
    if(verb.write && name!=='watch-subject-v2')
      assert.ok(verb.inputSchema.required.includes('subscription_id') && verb.inputSchema.required.includes('base_version'));
  }
});

test('change read declares and enforces verified human authority', async()=>{
  const { TOOLS, executeRegisteredTool } = await import('../src/tools.js');
  const tool=TOOLS['read-subject-watch-changes-v1'];
  assert.ok(tool);
  assert.equal(tool.humanOnly,true);
  assert.equal(tool.humanCallerOnly,true);
  let queries=0;
  await assert.rejects(executeRegisteredTool({query:async()=>{queries++;}},
    {slug:'joe-local',human:false},'read-subject-watch-changes-v1',
    {subject_type:'deal',subject_id:'00000000-0000-4000-8000-000000000001',subscription_id:'00000000-0000-4000-8000-000000000002'}));
  assert.equal(queries,0);
});

test('change response fixture uses the production parser and refuses malformed receipts',async()=>{
  const {parseSubjectWatchChanges}=await import('../contracts/subject-watch-change.v1.js');
  const schema=JSON.parse(readFileSync(new URL('../contracts/subject-watch-change.v1.json',import.meta.url)));
  const fixture=JSON.parse(readFileSync(new URL('../contracts/subject-watch-change.v1.fixture.json',import.meta.url)));
  assert.equal(new Validator(schema).validate(fixture).valid,true);
  assert.equal(parseSubjectWatchChanges(fixture),fixture);
  const malformed=structuredClone(fixture);malformed.changes[0].disposition='delivered';
  assert.throws(()=>parseSubjectWatchChanges(malformed),/invalid_subject_watch_change_v1/);
});

for(const failure of [null,'connect','begin','select','commit']) {
 test(`scheduled sweep ${failure ?? 'four batches'} cleans its connection`,async()=>{
  const calls=[]; let batches=0;
  class Pool {
   async connect(){calls.push('connect');if(failure==='connect')throw Error('offline');return this;}
   async query(input){const sql=typeof input==='string'?input:input.text;calls.push(sql);if(sql.startsWith(failure??'!'))throw Error('injected');
    if(sql.startsWith('select')){assert.ok(input.query_timeout>1500 && input.query_timeout<=2000,'client deadline leaves time after the 1500ms SQL cancellation to receive its bounded failure result');batches++;return {rows:[{result:{scanned:50,emitted:0}}]};}}
   release(destroy){assert.equal(destroy,true);calls.push('release');} async end(){calls.push('end');}
  }
  const action=sweepSubjectWatches({scheduledTime:Date.UTC(2027,1,1)},{DATABASE_URL_WRITER:'synthetic'},Pool);
  if(failure) await assert.rejects(action); else {await action;assert.equal(batches,4);}
  assert.equal(calls.at(-1),'end');if(failure!=='connect') assert.ok(calls.includes('release'));
  if(['select','commit'].includes(failure))assert.ok(calls.includes('rollback'));
 });
}

const proof=fileURLToPath(new URL('../../ops/verify-subject-watch-delivery-proof.py',import.meta.url));
test('proof CLI passes exact scheduled evidence and refuses duplicates and insufficient evidence',t=>{
 const sample=JSON.parse(readFileSync(new URL('../contracts/subject-watch-scheduled-proof.v1.fixture.json',import.meta.url)));
 const root=mkdtempSync(join(tmpdir(),'watch-proof-'));t.after(()=>rmSync(root,{recursive:true,force:true}));
 for (const [name,mutate,status] of [
  ['pass',()=>{},0],['duplicate',v=>v.sample.notifications=2,1],
  ['business mutation',v=>v.sample.business_mutations=1,1],
  ['missing retry',v=>v.sample.scheduled_at=v.sample.first_delivery_at,78],
  ['unknown release',v=>v.release.git_sha.value=null,78],
  ['older release',v=>v.release.git_sha.value='b'.repeat(40),78],
 ]) {
  const fixture=structuredClone(sample);mutate(fixture);const path=join(root,name+'.json');writeFileSync(path,JSON.stringify(fixture));
  const result=spawnSync(process.env.CARR_CI_PYTHON??'python3',[proof,'--fixture',path,'--expected-merge','a'.repeat(40),'--watch-id',sample.sample.watch_id],{encoding:'utf8'});
  assert.equal(result.status,status,name+' '+result.stdout+' '+result.stderr);
 }
});

test('release containment accepts descendants and rejects older and unrelated commits',t=>{
 const root=mkdtempSync(join(tmpdir(),'watch-proof-git-'));t.after(()=>rmSync(root,{recursive:true,force:true}));
 const gitEnv={...process.env};
 for(const key of ['GIT_SHALLOW_FILE','GIT_DIR','GIT_WORK_TREE','GIT_INDEX_FILE']) delete gitEnv[key];
 const git=(...args)=>execFileSync('git',['-C',root,...args],{encoding:'utf8',env:gitEnv,stdio:['ignore','pipe','pipe']}).trim();
 git('init','--initial-branch=main');git('config','user.name','Synthetic proof');git('config','user.email','synthetic@example.test');
 const message=join(root,'message');writeFileSync(message,'synthetic proof commit');
 const commit=()=>{git('commit','--allow-empty','-F',message);return git('rev-parse','HEAD');};
 const older=commit(),expected=commit(),descendant=commit();
 git('switch','--orphan','unrelated');const unrelated=commit();
 const base=JSON.parse(readFileSync(new URL('../contracts/subject-watch-scheduled-proof.v1.fixture.json',import.meta.url)));
 for(const [observed,status] of [[expected,0],[descendant,0],[older,78],[unrelated,78]]) {
  const fixture=structuredClone(base);fixture.release.git_sha.value=observed;const path=join(root,'fixture.json');writeFileSync(path,JSON.stringify(fixture));
  const result=spawnSync(process.env.CARR_CI_PYTHON??'python3',[proof,'--fixture',path,'--expected-merge',expected,'--watch-id',fixture.sample.watch_id],{cwd:root,encoding:'utf8',env:gitEnv});
  assert.equal(result.status,status,result.stdout+result.stderr);
 }
});

const engineFixture = await acquireDisposablePostgres({ prefix: 'subject-watch-engine-', snapshot: true });
after(() => engineFixture.close());
const engineDsn = engineFixture.dsn;
const writer = new URL(engineDsn);
writer.searchParams.set('options', '-c role=carr_writer');

test('engine intake stays off by default and retries an overlapping sweep without duplicate or paused receipts', async t => {
  const c = new pg.Client({ connectionString: engineDsn });
  await c.connect();
  t.after(() => c.end());
  const { id: joe } = (await c.query("select id from actor where slug='joe'")).rows[0];
  const { id: party } = (await c.query(
    "insert into party(kind,name,created_by,updated_by) values('person','Synthetic engine client',$1,$1) returning id", [joe],
  )).rows[0];
  await c.query("insert into client_status(slug,label) values('active','Active') on conflict do nothing");
  const { id: client } = (await c.query(
    "insert into client(party_id,status,created_by,updated_by) values($1,'active',$2,$2) returning id", [party, joe],
  )).rows[0];
  const { id: deal } = (await c.query(
    "insert into deal(client_id,name,deal_type,phase,owner,created_by,updated_by) values($1,'Synthetic engine deal','purchase','research','joe',$2,$2) returning id", [client, joe],
  )).rows[0];
  await c.query(`
    insert into ops.subject_watch_v2_subscription(actor_id,subject_type,subject_id,baseline,policy,state,subscribed_at)
    select id,'deal',$1,'{}','{"change_kinds":["reply_arrived"],"include_self_changes":false}',
      case slug when 'joe' then 'watching' else 'paused' end,'2027-02-01T11:50:00Z'
    from actor where slug in ('joe','dell')`, [deal]);

  const change = phaseMove(deal, '9007199254740993', 3, 'research', 'negotiation');
  const calls = [];
  let upstreamFails = true;
  let pauseDuringRead = false;
  t.mock.method(globalThis, 'fetch', async (target, request) => {
    calls.push(target.searchParams.get('since'));
    assert.equal(target.pathname, '/jobs/deal-watches/deal/' + deal);
    assert.equal(target.searchParams.get('subscriber'), 'joe');
    assert.equal(request.headers['Engine-Job'], 'agent');
    if (upstreamFails) return new Response(null, { status: 503 });
    if (pauseDuringRead) await c.query("update ops.subject_watch_v2_subscription set state='paused',lifecycle_version=2 where actor_id=$1", [joe]);
    return engineWatchResponse(deal, [
      ...change, ...change,
      ...phaseMove(deal, '9007199254740994', 4, 'negotiation', 'legal', 'joe'),
      ...phaseMove(deal, '9007199254740995', 12, 'legal', 'closed'),
    ]);
  });
  const sweep = (minute, switched = {}) => sweepSubjectWatches({ scheduledTime: Date.UTC(2027, 1, 1, 12, minute) }, {
    DATABASE_URL_WRITER: writer.href, ENGINE_URL: 'https://engine.example.test',
    ENGINE_ACCESS_CLIENT_ID: 'test-client', ENGINE_ACCESS_CLIENT_SECRET: 'test-secret', ...switched,
  }, pg.Pool);
  const on = { ENGINE_DEAL_WATCH_CHANGES: 'true' };
  const cursor = async () => (await c.query('select scheduled_at, after_watch_id from ops.subject_watch_sweep')).rows[0];
  const receipts = async () => (await c.query(`
    select a.slug, semantic_key, changes, disposition from ops.subject_watch_change c join actor a on a.id=c.actor_id
    where c.subject_id=$1 order by semantic_key`, [deal])).rows;

  await sweep(0);
  assert.deepEqual(calls, []);
  assert.deepEqual(await receipts(), []);
  const before = await cursor();
  assert.equal(before.scheduled_at.toISOString(), '2027-02-01T12:00:00.000Z');

  await assert.rejects(sweep(5, on));
  assert.deepEqual(await cursor(), before);
  assert.deepEqual(await receipts(), []);
  upstreamFails = false;
  await sweep(5, on);
  await sweep(10, on);
  assert.deepEqual(calls.map(value => new Date(value).toISOString()), [
    '2027-02-01T11:50:00.000Z', '2027-02-01T11:50:00.000Z', '2027-02-01T12:00:00.000Z',
  ]);
  const expected = [{
    disposition: 'emitted', slug: 'joe', semantic_key: 'engine:9007199254740993:phase',
    changes: [{ field: 'phase', before: 'research', after: 'negotiation', reason_code: 'value_changed' }],
  }];
  assert.deepEqual(await receipts(), expected);
  assert.equal((await cursor()).scheduled_at.toISOString(), '2027-02-01T12:10:00.000Z');
  pauseDuringRead = true;
  await sweep(15, on);
  assert.deepEqual(await receipts(), expected);
  assert.equal(calls.length, 4);
});

test('only the scheduler writer can execute the bounded engine watch functions', async t => {
  const c = new pg.Client({ connectionString: writer.href });
  await c.connect();
  t.after(() => c.end());
  assert.deepEqual((await c.query("select ops.read_subject_watch_engine_batch('2027-01-01T00:00:00Z',4) result")).rows[0].result, []);
  await assert.rejects(c.query("select ops.read_subject_watch_engine_batch('2027-01-01T00:00:00Z',5)"), /watch_batch_limit/);
  for (const table of ['subject_watch', 'subject_watch_v2_subscription', 'subject_watch_delivery', 'subject_watch_sweep', 'subject_watch_change']) {
    await assert.rejects(c.query('select * from ops.' + table), { code: '42501' });
  }
  await assert.rejects(c.query('select * from ops.subject_watch_engine_targets()'), { code: '42501' });
  for (const name of ['read_subject_watch_engine_batch', 'sweep_subject_watches']) {
    const { rows } = await c.query(`
      select coalesce(r.rolname,'public') as role
      from pg_proc p cross join lateral aclexplode(p.proacl) a left join pg_roles r on r.oid=a.grantee
      where p.pronamespace='ops'::regnamespace and p.proname=$1
        and ($1<>'sweep_subject_watches' or p.pronargs=3) and a.grantee<>p.proowner`, [name]);
    assert.deepEqual(rows, [{ role: 'carr_writer' }]);
  }
});

async function engineWatchFixture(t) {
  const db = new pg.Client({ connectionString: engineDsn });
  await db.connect();
  const watchIds = [];
  t.after(async () => {
    try { await db.query("update ops.subject_watch_v2_subscription set state='paused' where id=any($1::uuid[])", [watchIds]); }
    finally { await db.end(); }
  });
  const { id: actor } = (await db.query("select id from actor where slug='joe'")).rows[0];
  const { id: party } = (await db.query(
    "insert into party(kind,name,created_by,updated_by) values('person','Synthetic watch client',$1,$1) returning id", [actor],
  )).rows[0];
  await db.query("insert into client_status(slug,label) values('active','Active') on conflict do nothing");
  const { id: client } = (await db.query(
    "insert into client(party_id,status,created_by,updated_by) values($1,'active',$2,$2) returning id", [party, actor],
  )).rows[0];
  const addWatch = async (name, subscribedAt = '2027-02-01T11:50:00Z') => {
    const { rows: [watch] } = await db.query(`
      with d as (
        insert into deal(client_id,name,deal_type,phase,owner,created_by,updated_by)
        values($1,$2,'purchase','research','joe',$3,$3) returning id
      )
      insert into ops.subject_watch_v2_subscription(actor_id,subject_type,subject_id,baseline,policy,state,subscribed_at)
      select $3,'deal',id,'{}','{"change_kinds":["reply_arrived"],"include_self_changes":false}',
        'watching',$4 from d returning id, subject_id as deal`, [client, name, actor, subscribedAt]);
    watchIds.push(watch.id);
    return watch;
  };
  const sweep = minute => sweepSubjectWatches({ scheduledTime: Date.UTC(2027, 1, 1, 12, minute) }, {
    DATABASE_URL_WRITER: writer.href, ENGINE_URL: 'https://engine.example.test',
    ENGINE_ACCESS_CLIENT_ID: 'test-client', ENGINE_ACCESS_CLIENT_SECRET: 'test-secret', ENGINE_DEAL_WATCH_CHANGES: 'true',
  }, pg.Pool);
  return { db, addWatch, sweep };
}

function phaseMove(deal, changeId, minute, before, after, actor = 'dell') {
  const stamp = new Date(Date.UTC(2027, 1, 1, 12, minute)).toISOString();
  const entry = {
    change_id: changeId, table_name: 'deal_phase_change', record: { deal_id: deal, happened_at: stamp },
    action: 'insert', actor, for_user: null, changed_at: stamp,
  };
  return [
    { ...entry, field: 'happened_at', old_value: null, new_value: stamp },
    { ...entry, field: 'phase', old_value: before, new_value: after },
  ];
}

function engineWatchResponse(deal, changes) {
  return Response.json({
    kind: 'deal', id: deal, deal_id: deal, href: '/deals?deal=' + deal, changes, reply_activity_ids: [],
  });
}

test('missing and retryable deals do not stall another watch in the same sweep', async t => {
  const { db, addWatch, sweep } = await engineWatchFixture(t);
  const missing = await addWatch('Synthetic missing deal');
  const retry = await addWatch('Synthetic retry deal');
  const healthy = await addWatch('Synthetic healthy deal');
  const requests = [];
  let recovered = false;
  t.mock.method(globalThis, 'fetch', async target => {
    const deal = target.pathname.split('/').at(-1);
    requests.push([deal, target.searchParams.get('since')]);
    if (deal === missing.deal) {
      return Response.json({
        error: 'not_found', correlation_id: '00000000-0000-4000-8000-000000000001',
        hint: 'The requested record was not found.',
      }, { status: 404 });
    }
    if (deal === retry.deal && !recovered) return Response.json({ error: 'not_found' }, { status: 404 });
    return engineWatchResponse(deal, phaseMove(deal, '1', 3, 'research', 'negotiation'));
  });

  await sweep(5);
  const progress = async ({ id }) => (await db.query(
    'select engine_checked_at from ops.subject_watch_delivery where watch_id=$1', [id],
  )).rows[0].engine_checked_at;
  const receipts = async () => (await db.query(
    'select subject_id, disposition from ops.subject_watch_change where watch_generation_id=any($1::uuid[]) order by subject_id',
    [[missing.id, retry.id, healthy.id]],
  )).rows;
  assert.equal((await progress(healthy)).toISOString(), '2027-02-01T12:05:00.000Z');
  assert.equal(await progress(retry), null);
  assert.deepEqual(await receipts(), [{ subject_id: healthy.deal, disposition: 'emitted' }]);

  await db.query("update ops.subject_watch_delivery set next_evaluation_at='2027-02-01T12:05:00Z' where watch_id=$1", [healthy.id]);
  await sweep(5);
  assert.ok((await db.query(
    "select next_evaluation_at>'2027-02-01T12:05:00Z' as rescheduled from ops.subject_watch_delivery where watch_id=$1", [healthy.id],
  )).rows[0].rescheduled);
  assert.equal(await progress(retry), null);

  recovered = true;
  await sweep(10);
  assert.equal((await progress(retry)).toISOString(), '2027-02-01T12:10:00.000Z');
  assert.deepEqual((await receipts()).map(row => row.subject_id), [healthy.deal, retry.deal].sort());
  assert.equal(requests.filter(([deal]) => deal === missing.deal).length, 1);
  assert.deepEqual(requests.filter(([deal]) => deal === retry.deal).map(([, since]) => new Date(since).toISOString()), [
    '2027-02-01T11:50:00.000Z', '2027-02-01T11:50:00.000Z', '2027-02-01T11:50:00.000Z',
  ]);
  assert.equal((await db.query(
    "select count(*)::int as count from event where verb='subject-watch-engine-target-missing' and subject_id=$1", [missing.id],
  )).rows[0].count, 1);
});

test('engine phase inserts emit one transition per move across overlapping polls', async t => {
  const { db, addWatch, sweep } = await engineWatchFixture(t);
  const { deal } = await addWatch('Synthetic phase deal');
  const changes = phaseMove(deal, '9007199254740993', 3, 'research', 'negotiation');
  t.mock.method(globalThis, 'fetch', async () => engineWatchResponse(deal, changes));
  const receipts = async () => (await db.query(
    'select changes from ops.subject_watch_change where subject_id=$1 order by occurred_at', [deal],
  )).rows.flatMap(row => row.changes);
  const first = { field: 'phase', before: 'research', after: 'negotiation', reason_code: 'value_changed' };

  await sweep(5);
  assert.deepEqual(await receipts(), [first]);
  changes.push(...phaseMove(deal, '9007199254740994', 6, 'negotiation', 'legal', 'joe'));
  changes.push(...phaseMove(deal, '9007199254740995', 7, 'legal', 'closed'));
  await sweep(10);
  await sweep(15);
  assert.deepEqual(await receipts(), [
    first, { field: 'phase', before: 'legal', after: 'closed', reason_code: 'value_changed' },
  ]);
});

test('a watch created after an engine move sees a return to the copied phase', async t => {
  const { db, addWatch, sweep } = await engineWatchFixture(t);
  const { id, deal } = await addWatch('Synthetic return to copied phase', '2027-02-01T12:02:00Z');
  const history = [
    ...phaseMove(deal, '1', 1, 'research', 'negotiation'),
    ...phaseMove(deal, '2', 3, 'negotiation', 'research'),
  ];
  t.mock.method(globalThis, 'fetch', async target => {
    const since = Date.parse(target.searchParams.get('since'));
    assert.equal(since, Date.UTC(2027, 1, 1, 12, 2));
    return engineWatchResponse(deal, history.filter(edit => Date.parse(edit.changed_at) >= since));
  });

  await sweep(5);
  assert.deepEqual((await db.query(
    'select semantic_key, changes from ops.subject_watch_change where watch_generation_id=$1', [id],
  )).rows, [{
    semantic_key: 'engine:2:phase',
    changes: [{ field: 'phase', before: 'negotiation', after: 'research', reason_code: 'value_changed' }],
  }]);
});

test('the first recorded engine phase emits a transition with no prior phase', async t => {
  const { db, addWatch, sweep } = await engineWatchFixture(t);
  const { id, deal } = await addWatch('Synthetic first phase');
  t.mock.method(globalThis, 'fetch', async () => engineWatchResponse(deal, phaseMove(deal, '1', 3, null, 'negotiation')));

  await sweep(5);
  assert.deepEqual((await db.query(
    'select semantic_key, changes from ops.subject_watch_change where watch_generation_id=$1', [id],
  )).rows, [{
    semantic_key: 'engine:1:phase',
    changes: [{ field: 'phase', before: null, after: 'negotiation', reason_code: 'unknown_to_known' }],
  }]);
});
