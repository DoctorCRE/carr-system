// Exercise clock capture and transactional delivery on the migrated snapshot.
import test from 'node:test';
import { randomUUID } from 'node:crypto';
import assert from 'node:assert/strict';
import pg from 'pg';
import { executeRegisteredTool } from '../src/tools.js';
import { sweepSubjectWatches } from '../src/subject-watch-scheduler.js';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { snapshotDatabase } from './helpers/postgres-database.mjs';
const dsn = await snapshotDatabase('watch-delivery-');
test('quiet lease clocks and pending delivery', async t => {
 const c = new pg.Client({connectionString:dsn}); await c.connect(); t.after(()=>c.end());
 const actor=(await c.query("select id from actor where slug='joe'")).rows[0].id;
 const party=(await c.query("insert into party(kind,name,created_by,updated_by) values('person','Synthetic clock client',$1,$1) returning id",[actor])).rows[0].id;
 await c.query("insert into client_status(slug,label) values('active','Active') on conflict do nothing");
 const client=(await c.query("insert into client(party_id,status,created_by,updated_by) values($1,'active',$2,$2) returning id",[party,actor])).rows[0].id;
 const deal=(await c.query("insert into deal(client_id,name,deal_type,phase,owner,created_by,updated_by) values($1,'Synthetic clock deal','purchase','research','joe',$2,$2) returning id",[client,actor])).rows[0].id;
 const lease=(await c.query("insert into lease(deal_id,client_id,expiration_on,status,owner_id,executed_on,evidence_kind,evidence_ref,created_by,updated_by) values($1,$2,'2028-03-31','current',$3,'2026-01-01','executed_lease','synthetic:clock',$3,$3) returning id",[deal,client,actor])).rows[0].id;
 const make=async(date='2027-01-01T12:00:00Z')=>(await c.query("insert into ops.subject_watch_v2_subscription(actor_id,subject_type,subject_id,baseline,policy,state,subscribed_at) values($1,'lease',$2,ops.subject_watch_target('lease',$2),$3,'watching',$4) returning id",[actor,lease,{change_kinds:['lease_threshold_crossed'],lease_threshold_days:[0,30,90,365]},date])).rows[0].id;
 let watch=await make();
 const sweep=async(now,conn=c)=>(await conn.query('select ops.sweep_subject_watches($1,50) result',[now])).rows[0].result;
 const count=async() => Number((await c.query('select count(*) n from ops.subject_watch_change where watch_generation_id=$1 and disposition=\'emitted\'',[watch])).rows[0].n);
 await t.test('crossing without business mutation emits once and later days stay quiet',async()=>{
  await sweep('2027-03-31T12:00:00Z'); assert.equal(await count(),0);
  await sweepSubjectWatches({scheduledTime:Date.parse('2027-04-01T12:00:00Z')},{DATABASE_URL_WRITER:dsn},pg.Pool); assert.equal(await count(),1);
  await sweep('2027-04-02T12:00:00Z'); assert.equal(await count(),1);
  await c.query("select set_config('carr.acting_actor_slug','joe',false),set_config('carr.verified_human_actor_slug','joe',false)");
  const response=await executeRegisteredTool(c,{id:actor,slug:'joe',human:true},'read-subject-watch-changes-v1',{subject_type:'lease',subject_id:lease,subscription_id:watch});
  assert.equal(response.changes[0].category,'lease_threshold_crossed');
  assert.equal(response.changes[0].disposition,'emitted');

  if (process.env.J139_CAPTURE_PROOF) {
   const proof=fileURLToPath(new URL('../../ops/verify-subject-watch-proof.py',import.meta.url));
   const script="import importlib.util,json,sys; s=importlib.util.spec_from_file_location('proof',sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); sample=m.capture(sys.argv[2],sys.argv[3],5); print(json.dumps({'release':{'ok':True,'git_sha':{'value':'a'*40,'reason':None}},'sample':sample}))";
   const fixture=execFileSync(process.env.CARR_CI_PYTHON??'python3',['-c',script,proof,dsn,watch],{encoding:'utf8'});
   const {writeFileSync}=await import('node:fs');writeFileSync(process.env.J139_CAPTURE_PROOF,fixture);
  }

 });
 await t.test('outage catch-up summarizes the most urgent threshold',async()=>{
  await sweep('2028-03-15T12:00:00Z'); assert.equal(await count(),2);
  assert.equal(Number((await c.query('select count(*) n from ops.notification where id in (select notification_id from ops.subject_watch_change where watch_generation_id=$1)',[watch])).rows[0].n),2);
  await sweep('2028-03-16T12:00:00Z'); assert.equal(await count(),2);
 });
 await t.test('pause suppresses pending and resume takes quiet baseline',async()=>{
  await c.query("update ops.subject_watch_v2_subscription set state='paused' where id=$1",[watch]);
  await sweep('2028-03-30T12:00:00Z'); assert.equal(await count(),2);
  await c.query("update ops.subject_watch_v2_subscription set state='watching' where id=$1",[watch]);
  await sweep('2028-03-30T12:00:00Z'); assert.equal(await count(),2);
 });
 await t.test('removed generation never emits',async()=>{
  await c.query("update ops.subject_watch_v2_subscription set state='removed' where id=$1",[watch]);
  await sweep('2028-03-31T12:00:00Z'); assert.equal(await count(),2);
 });
 await t.test('busy locks skip and later sweep catches up',async()=>{
  await c.query('delete from ops.subject_watch_v2_subscription where id=$1',[watch]); watch=await make();
  const other=new pg.Client({connectionString:dsn}); await other.connect();
  try {await other.query('begin'); await other.query("select pg_advisory_xact_lock(hashtextextended($1,0))",[`${actor}:lease:${lease}`]);
   await sweep('2027-04-01T12:00:00Z'); assert.equal(await count(),0);
   await other.query('commit'); await sweep('2027-04-01T12:00:00Z'); assert.equal(await count(),1);
  } finally {await other.query('rollback'); await other.end();}
 });
 await t.test('concurrent sweepers converge',async()=>{
  const other=new pg.Client({connectionString:dsn}); await other.connect();
  try {await Promise.all([sweep('2028-03-15T12:00:00Z'),sweep('2028-03-15T12:00:00Z',other)]); assert.equal(await count(),2);} finally {await other.end();}
 });
 await t.test('calendar and DST clocks use Chicago dates',async()=>{
  for(const [instant,expected] of [['2028-03-01T05:59:00Z','2028-02-29'],['2028-03-01T06:00:00Z','2028-03-01'],['2027-03-14T07:59:00Z','2027-03-14'],['2027-03-14T08:01:00Z','2027-03-14']]) {
   assert.equal((await c.query("select ($1::timestamptz at time zone 'America/Chicago')::date::text d",[instant])).rows[0].d,expected);
  }
 }); await t.test('mint rollback preserves pending and repeated failures quarantine',async()=>{
  await c.query('begin');
  try {
   await c.query("update ops.subject_watch_delivery set next_evaluation_at='2028-03-31',last_emitted_at=null where watch_id=$1",[watch]);
   await c.query("create function ops.watch_delivery_fail() returns trigger language plpgsql as $$ begin raise exception 'synthetic failure'; end $$");
   await c.query('create trigger watch_delivery_fail before insert on ops.notification for each row execute function ops.watch_delivery_fail()');
   await sweep('2028-03-31T12:00:00Z');
   assert.equal((await c.query('select failure_count from ops.subject_watch_delivery where watch_id=$1',[watch])).rows[0].failure_count,1);
   assert.equal(Number((await c.query("select count(*) n from ops.subject_watch_change where watch_generation_id=$1 and disposition='pending'",[watch])).rows[0].n),1);
   await sweep('2028-03-31T12:05:00Z');
   assert.equal((await c.query('select failure_count from ops.subject_watch_delivery where watch_id=$1',[watch])).rows[0].failure_count,2);
   await sweep('2028-03-31T12:10:00Z'); assert.equal(await count(),2);
   await c.query('drop trigger watch_delivery_fail on ops.notification');
   await c.query('update ops.subject_watch_delivery set failure_count=0 where watch_id=$1',[watch]);
   await sweep('2028-03-31T12:15:00Z'); assert.equal(await count(),3);
   await sweep('2028-03-31T12:20:00Z'); assert.equal(await count(),3);
  } finally {await c.query('rollback');}
 });
 await t.test('removing a selected threshold suppresses its pending clock receipt',async()=>{
  await c.query('begin');
  try {
   await c.query("create function ops.watch_policy_fail() returns trigger language plpgsql as $$ begin raise exception 'synthetic policy failure'; end $$");
   await c.query('create trigger watch_policy_fail before insert on ops.notification for each row execute function ops.watch_policy_fail()');
   await sweep('2028-03-31T12:00:00Z');
   await c.query('drop trigger watch_policy_fail on ops.notification');
   await c.query("update ops.subject_watch_v2_subscription set policy=jsonb_set(policy,'{lease_threshold_days}','[30,90,365]') where id=$1",[watch]);
   await sweep('2028-03-31T12:05:00Z');
   assert.equal(Number((await c.query("select count(*) n from ops.subject_watch_change where watch_generation_id=$1 and disposition='pending'",[watch])).rows[0].n),0);
   assert.equal(await count(),2);
  } finally {await c.query('rollback');}
 });
 await t.test('whole transaction rollback retries without loss or duplication',async()=>{
  await c.query('begin'); await sweep('2028-03-31T12:00:00Z'); assert.equal(await count(),3); await c.query('rollback'); assert.equal(await count(),2);
  await sweep('2028-03-31T12:00:00Z'); assert.equal(await count(),3);
 });
 await t.test('access loss suppresses pending and health detects stale scheduler',async()=>{
  await c.query('begin');
  try {
   await c.query('update party set deleted_at=now() where id=$1',[party]);
   await sweep('2028-04-01T12:00:00Z');assert.equal(await count(),3);
   await c.query("update ops.subject_watch_sweep set scheduled_at=clock_timestamp()-interval '16 minutes' where singleton");
   assert.equal((await c.query('select ops.subject_watch_health($1) health',[watch])).rows[0].health.status,'delayed');
  } finally {await c.query('rollback');}
 });
 await t.test('coalescing, rate limit, busy receipts and delivery policy use current state',async()=>{
  const target=(await c.query("insert into ops.subject_watch_v2_subscription(actor_id,subject_type,subject_id,baseline,policy,state) values($1,'deal',$2,'{}',$3,'watching') returning id",[actor,deal,{change_kinds:['reply_arrived'],include_self_changes:true}])).rows[0].id;
  const change=async(day)=>{
   await c.query('update deal set closed_on=$2 where id=$1',[deal,day]);
   await c.query("insert into event(occurred_at,actor_id,verb,subject_type,subject_id,cause) values(now(),$1,'update-deal','deal',$2,'automation_job')",[actor,deal]);
  };
  const pending=async()=>Number((await c.query("select count(*) n from ops.subject_watch_change where watch_generation_id=$1 and disposition='pending'",[target])).rows[0].n);
  const notices=async()=>Number((await c.query('select count(distinct notification_id) n from ops.subject_watch_change where watch_generation_id=$1',[target])).rows[0].n);
  await change('2027-01-01');await change('2027-01-02');await change('2027-01-03');assert.equal(await pending(),3);
  await sweep('2029-01-01T12:00:00Z');assert.equal(await pending(),0);assert.equal(await notices(),1);
  await change('2027-01-04');await sweep('2029-01-01T12:01:00Z');assert.equal(await pending(),1);assert.equal(await notices(),1);
  const peer=new pg.Client({connectionString:dsn});await peer.connect();
  try {
   await peer.query('begin');await peer.query("select id from ops.subject_watch_change where watch_generation_id=$1 and disposition='pending' for update",[target]);
   await sweep('2029-01-01T12:05:00Z');assert.equal(await notices(),1);
   await peer.query('commit');await sweep('2029-01-01T12:05:00Z');assert.equal(await pending(),0);assert.equal(await notices(),2);
  } finally {await peer.query('rollback');await peer.end();}
  await change('2027-01-05');
  await c.query("update ops.subject_watch_v2_subscription set policy=jsonb_set(policy,'{include_self_changes}','false') where id=$1",[target]);
  await sweep('2029-01-01T12:10:00Z');assert.equal(await pending(),0);assert.equal(await notices(),2);
  await c.query("update ops.subject_watch_v2_subscription set policy=jsonb_set(policy,'{include_self_changes}','true') where id=$1",[target]);
  await change('2027-01-06');assert.equal(await pending(),1);
  await c.query('update party set deleted_at=now() where id=$1',[party]);
  await sweep('2029-01-01T12:15:00Z');assert.equal(await pending(),0);assert.equal(await notices(),2);
  await c.query('update party set deleted_at=null where id=$1',[party]);
 });
 await t.test('1x and 10x synthetic pending loads retain every receipt',async()=>{
  for(const size of [50,500]) {
   await c.query('begin');
   try {
    const deals=(await c.query("insert into deal(client_id,name,deal_type,phase,owner,created_by,updated_by) select $1,'Synthetic load '||n,'purchase','research','joe',$2,$2 from generate_series(1,$3) n returning id",[client,actor,size])).rows.map(r=>r.id);
    await c.query("insert into ops.subject_watch_v2_subscription(actor_id,subject_type,subject_id,baseline,policy,state) select $1,'deal',id,'{}','{\"change_kinds\":[\"reply_arrived\"]}','watching' from deal where id=any($2::uuid[])",[actor,deals]);
    await c.query("update deal set closed_on='2027-01-01' where id=any($1::uuid[])",[deals]);
    await c.query("insert into event(occurred_at,actor_id,verb,subject_type,subject_id,cause) select now(),$1,'update-deal','deal',id,'automation_job' from deal where id=any($2::uuid[])",[actor,deals]);
    const started=performance.now();let batches=0;
    for(let n=0;n<Math.ceil(size/50)+2;n++){await sweep('2030-01-01T12:00:00Z');batches++;}
    const duration=performance.now()-started;
    const counts=(await c.query("select count(*)::integer total,count(*) filter(where disposition='emitted')::integer emitted,count(distinct notification_id)::integer notices from ops.subject_watch_change where subject_id=any($1::uuid[])",[deals])).rows[0];
    assert.deepEqual(counts,{total:size,emitted:size,notices:size});
    console.log(JSON.stringify({synthetic_watches:size,batches,sweep_ms:duration,errors:0,...counts}));
   } finally {await c.query('rollback');}
  }
 });
 await t.test('leap-day threshold crosses only at Chicago midnight',async()=>{
  await c.query('begin');
  try {
   await c.query('delete from ops.subject_watch_v2_subscription where id=$1',[watch]);
   await c.query("update lease set expiration_on='2028-02-29' where id=$1",[lease]);
   const w=(await c.query("insert into ops.subject_watch_v2_subscription(actor_id,subject_type,subject_id,baseline,policy,state,subscribed_at) values($1,'lease',$2,'{}',$3,'watching','2028-02-27T12:00:00Z') returning id",[actor,lease,{change_kinds:['lease_threshold_crossed'],lease_threshold_days:[1]}])).rows[0].id;
   const emitted=async()=>Number((await c.query("select count(*) n from ops.subject_watch_change where watch_generation_id=$1 and disposition='emitted'",[w])).rows[0].n);
   await sweep('2028-02-28T05:59:00Z');assert.equal(await emitted(),0);
   await sweep('2028-02-28T06:00:00Z');assert.equal(await emitted(),1);
   await sweep('2028-02-29T12:00:00Z');assert.equal(await emitted(),1);
  } finally {await c.query('rollback');}
 });
 await t.test('12-calendar-month window honors leap dates and month ends',async()=>{
  await c.query('begin');
  try {
   await c.query('delete from ops.subject_watch_v2_subscription where id=$1',[watch]);
   await c.query("update lease set expiration_on='2028-02-29' where id=$1",[lease]);
   const w=(await c.query("insert into ops.subject_watch_v2_subscription(actor_id,subject_type,subject_id,baseline,policy,state,subscribed_at) values($1,'lease',$2,'{}',$3,'watching','2027-02-27T12:00:00Z') returning id",[actor,lease,{change_kinds:['lease_threshold_crossed'],lease_threshold_months:[12]}])).rows[0].id;
   const emitted=async()=>Number((await c.query("select count(*) n from ops.subject_watch_change where watch_generation_id=$1 and disposition='emitted'",[w])).rows[0].n);
   await sweep('2027-02-28T12:00:00Z');assert.equal(await emitted(),0);
   await sweep('2027-03-01T05:59:00Z');assert.equal(await emitted(),0);
   await sweep('2027-03-01T06:00:00Z');assert.equal(await emitted(),1);
   await sweep('2027-03-31T12:00:00Z');assert.equal(await emitted(),1);
  } finally {await c.query('rollback');}
 });
 await t.test('registered month policy and current health pass the producer parser',async()=>{
  await c.query('begin');
  try {
   const current=(await c.query('select lifecycle_version from ops.subject_watch_v2_subscription where id=$1',[watch])).rows[0];
   await c.query('set local role carr_writer');
   const result=await executeRegisteredTool(c,{id:actor,slug:'joe',human:true},'replace-subject-watch-policy-v2',{
    subject_type:'lease',subject_id:lease,subscription_id:watch,base_version:current.lifecycle_version,
    policy:{change_kinds:['lease_threshold_crossed'],lease_threshold_months:[12]},idempotency_key:randomUUID()});
   assert.deepEqual(result.watch.policy.lease_threshold_months,[12]);
   assert.ok(['healthy','delayed','needs_repair'].includes(result.watch.health.status));
  } finally {await c.query('rollback');}
 });

});
