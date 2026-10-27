import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { executeRegisteredTool } from '../src/tools.js';

let fixture, admin, database, c, actor, connection;
before(async () => {
  const ciDsn = process.env.CARR_CI_DATABASE_URL;
  let bin;
  if (ciDsn) {
    const url = new URL(ciDsn);
    assert.ok(['postgres:', 'postgresql:'].includes(url.protocol));
    assert.ok(['127.0.0.1', 'localhost'].includes(url.hostname), 'fixture requires disposable loopback PostgreSQL');
    assert.equal(url.search, '', 'fixture connection must not override its loopback host');
    admin = new pg.Client({ connectionString: ciDsn });
    await admin.connect();
    const name = `complete_wr_${randomUUID().replaceAll('-', '')}`;
    await admin.query(`create database "${name}" template template0`);
    database = name;
    url.pathname = `/${database}`;
    connection = { connectionString: url.href };
  } else {
    bin = ['/opt/homebrew/opt/postgresql@17/bin', '/usr/lib/postgresql/17/bin',
      '/opt/homebrew/opt/postgresql@18/bin', '/usr/lib/postgresql/18/bin'].find(p => existsSync(`${p}/initdb`));
    assert.ok(bin, 'PostgreSQL is required for the isolated record-layer proof');
    fixture = await acquireDisposablePostgres({ prefix: 'complete-wr-', pgCtl: `${bin}/pg_ctl` });
    await fixture.run(`${bin}/initdb`, ['-D', `${fixture.root}/data`, '-U', 'carr_ci', '--auth=trust', '--no-locale']);
    await fixture.run(`${bin}/pg_ctl`, ['-D', `${fixture.root}/data`, '-l', `${fixture.root}/server.log`, '-o', `-k ${fixture.root} -h ''`, '-w', 'start']);
    await fixture.run(`${bin}/createdb`, ['-h', fixture.root, '-U', 'carr_ci', 'carr_ci']);
    connection = { host: fixture.root, user: 'carr_ci', database: 'carr_ci' };
  }
  c = new pg.Client(connection);
  await c.connect();
  await c.query('do $$ begin create role neondb_owner; exception when duplicate_object then null; end $$;');
  const restore = ['-X', '-v', 'ON_ERROR_STOP=1', '-q', '-f', new URL('../../db/schema.sql', import.meta.url).pathname];
  if (ciDsn) await promisify(execFile)('psql', [connection.connectionString, ...restore], { maxBuffer: 2 * 1024 * 1024 });
  else await fixture.run(`${bin}/psql`, ['-h', fixture.root, '-U', 'carr_ci', '-d', 'carr_ci', ...restore]);
  await c.query(readFileSync(new URL('../../migrations/1006_complete_work_request.sql', import.meta.url), 'utf8'));
  actor = { ...(await c.query("insert into actor(slug,kind,display_name) values('joe','human','Synthetic partner') on conflict(slug) do update set active=true returning id,slug")).rows[0], human: true };
});
after(async () => {
  try { await c?.end(); }
  finally {
    try { if (database) await admin.query(`drop database "${database}"`); }
    finally { await Promise.all([admin?.end(), fixture?.close()]); }
  }
});

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
  ['missing criterion','WR-999004',a=>{a.criterion_evidence=[];},'missing_criterion_evidence'],
  ['missing change','WR-999005',a=>{a.change_refs=[];},'missing_change_ref'],
  ['scalar changes','WR-999008',a=>{a.change_refs=[7];},'missing_change_ref'],
  ['scalar evidence','WR-999009',a=>{a.criterion_evidence=[7];},'missing_criterion_evidence'],
  ['boolean evidence','WR-999010',a=>{a.criterion_evidence[0].evidence=true;},'missing_criterion_evidence'],
  ['missing journey','WR-999006',a=>{a.user_facing=true;},'missing_user_journey_ref'],
  ['self attestation','WR-999007',()=>{},'completion_capsule_refused'],
]) {
  test(`complete-work-request refuses ${label} without closing or saving replay`, async () => {
    if (label!=='unknown reference') await request(ref, 'captured', label==='self attestation' ? 'joe' : 'builder');
    if (label==='terminal state') await c.query("update ops.work_request set state='declined',exit_reason='Synthetic obsolete',closed_at=now() where ref=$1", [ref]);
    const args=completion(ref); modify(args);
    await assert.rejects(command('complete-work-request',args,actor,'carr_writer'),error=>{
      assert.equal(error.payload?.error,expected); return true;
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
test('repair migration preserves named rows with a different reason or open state', async()=>{
  await sourcedRequest('WR-999025');
  await c.query('begin');
  try {
    for (const ref of ['WR-000414','WR-000436','WR-000499']) await sourcedRequest(ref);
    for (const [ref,reason] of [['WR-000414','Synthetic obsolete'],['WR-000499','Done: synthetic']])
      await c.query('select ops.decline_sourced_work_request($1,1,$2,$3,$4)',[ref,reason,actor.slug,randomUUID()]);
    await c.query(readFileSync(new URL('../../migrations/1006_complete_work_request.sql',import.meta.url),'utf8'));
    assert.deepEqual((await c.query("select ref,state,version from ops.work_request where ref=any($1) order by ref",[['WR-000414','WR-000436','WR-000499']])).rows,[
      {ref:'WR-000414',state:'declined',version:2}, {ref:'WR-000436',state:'captured',version:1}, {ref:'WR-000499',state:'captured',version:3},
    ]);
  } finally { await c.query('rollback'); }
});
test('repair migration reopens exactly eligible named rows, preserves receipts, and replays without events', async()=>{
  const refs=['WR-000414','WR-000436','WR-000499','WR-000500','WR-000501','WR-000507','WR-000510','WR-000520','WR-000542','WR-000545','WR-000546','WR-000551','WR-000552','WR-000553'];
  for (const [i,ref] of refs.entries()) {
    await sourcedRequest(ref);
    await command('decline-work-request',{idempotency_key:randomUUID(),human_ref:ref,base_version:1,
      exit_reason:i%2 ? 'Done: synthetic built work' : 'Done on main synthetic'},actor,'carr_writer');
  }
  await sourcedRequest('WR-999024');
  await command('decline-work-request',{idempotency_key:randomUUID(),human_ref:'WR-999024',base_version:1,
    exit_reason:'Done: unlisted synthetic'},actor,'carr_writer');
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
