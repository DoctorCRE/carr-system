// Verify actor-scoped inbox reads and disposition commands on PostgreSQL.
import test from 'node:test';
import assert from 'node:assert/strict';
import pg from 'pg';
import { setTimeout } from 'node:timers/promises';
import { readFileSync, writeFileSync } from 'node:fs';
import { postgresDatabase } from './helpers/postgres-database.mjs';
import { HOME_INBOX_CONTRACT, HOME_INBOX_SETTLEMENT_CONTRACT } from '../src/home-inbox.js';
import { homeReviewReference } from '../src/home-review.js';
import { executeRegisteredTool } from '../src/tools.js';

const id = n => `ae000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const joe = { id: id(1), slug: 'joe', human: true };
const dell = { id: id(2), slug: 'dell', human: true };

async function initialize(c) {
  await c.query(`create schema ops;
    create function ops.scac_current_grant_contract() returns jsonb language sql as
      $$select '{"secdef_execute":[]}'::jsonb$$;
    create role carr_reader; create role carr_writer; create role dot_reader; create role carr_authority; create role carr_jobs;
    create table public.actor(id uuid primary key, slug text, kind text default 'human', active boolean default true);

    create table public.next_action(id uuid primary key, owner_id uuid, description text, status text,
      version integer, created_at timestamptz, updated_at timestamptz, hold_until date);
    create table public.capture_post_call_action(id uuid primary key, owner_id uuid, description text,
      status text, accepted_at timestamptz, updated_at timestamptz, completed_at timestamptz);
    create table public.capture_session(id uuid primary key,actor_id uuid,state text,state_at timestamptz,started_at timestamptz);
    create table ops.notification(id uuid primary key,recipient_actor uuid,subject_type text,subject_ref text,
      severity text,reason text,deep_link text,created_at timestamptz);
    create table public.activity(id uuid primary key,email_source jsonb);
    create table public.email_proposal(id uuid primary key,kind text,owner text,activity_id uuid,version integer,
      status text,draft_subject text,created_at timestamptz,submitted_at timestamptz,result_recorded_at timestamptz);
    create table ops.notification_read(notification_id uuid,recipient_actor uuid,read_at timestamptz);
    create table ops.notification_delivery(notification_id uuid,channel text,state text,held_until timestamptz);
  `);
  const schema=readFileSync(new URL('../../db/schema.sql',import.meta.url),'utf8');
  await c.query(schema.match(/CREATE FUNCTION ops\.portfolio_writer_actor_id\(\)[\s\S]*?\n\$\$;/)[0]);
  await c.query(schema.match(/CREATE TABLE public\.tool_call \([\s\S]*?\n\);/)[0]);
  await c.query('alter table tool_call add primary key(idempotency_key)');
  await c.query('grant usage on schema ops to carr_writer,carr_reader');
  await c.query('grant select,insert on tool_call to carr_writer');
  await c.query('insert into actor(id,slug) values($1,\'joe\'),($2,\'dell\')', [joe.id,dell.id]);
  await c.query("select set_config('carr.acting_actor_slug','joe',false),set_config('carr.verified_human_actor_slug','joe',false)");
  const cadence=readFileSync(new URL('../../migrations/0617_delivery_cadence_a05.sql', import.meta.url),'utf8');
  await c.query(cadence.match(/create or replace function ops\.notification_feed_facts\(p_after timestamptz, p_limit integer\)[\s\S]*?end \$\$;/)[0]);
  await c.query(readFileSync(new URL('../../migrations/1114_home_inbox.sql', import.meta.url),'utf8'));
}

test('Home inbox is an actor-scoped bounded snapshot over canonical records', async t => {
  const db = await postgresDatabase('home-inbox-');
  const c = db.client;
  t.after(() => db.close());
  await initialize(c);
  await c.query(`insert into next_action values
    ($1,$2,'Synthetic open task','open',1,'2026-10-29','2026-10-29','2026-11-02'),
    ($3,$2,'Synthetic done task','done',2,'2026-10-28','2026-10-29',null),
    ($4,$5,'Other partner task','open',1,'2026-10-29','2026-10-29',null)`, [id(10),joe.id,id(11),id(12),dell.id]);
  const read = async (actor=joe) => {
    await c.query("select set_config('carr.acting_actor_slug',$1,false),set_config('carr.verified_human_actor_slug',$1,false)",[actor.slug]);
    let queries=0;
    const result=await executeRegisteredTool({query(...args){queries++; return c.query(...args);}},actor,'read-home-inbox',{});
    assert.equal(queries,1,'one bounded SQL query without per-item calls');
    return result;
  };
  await c.query('set role carr_writer');
  const first=await read();
  await c.query('reset role');
  assert.deepEqual(first.items.map(i=>i.source_id),[id(10),id(11)]);
  assert.equal(first.items[1].source_state,'done');
  assert.equal(first.items[1].disposition,'inbox','Done does not imply Settled');
  assert.equal(first.items[0].hold_until,'2026-11-02');
  assert.equal(new Set(first.items.map(i=>i.item_id)).size,2);
  assert.deepEqual(await read(),first);
  assert.deepEqual((await read(dell)).items.map(i=>i.source_id),[id(12)]);
  await t.test('held notifications follow the feed until their hold expires',async()=>{
    await c.query(`insert into ops.notification values
      ($1,$3,'deal','held','info','held',null,now()),
      ($2,$3,'deal','normal','info','normal',null,now())`,[id(20),id(21),joe.id]);
    await c.query("insert into ops.notification_delivery values ($1,'in_app','held_until_morning',now()+interval '1 day')",[id(20)]);
    const visible=async()=> (await read()).items.filter(i=>i.source_type==='notification').map(i=>i.title).sort();
    await read();
    const feed=async()=> (await c.query('select ops.notification_feed_facts(null,50) as result')).rows[0].result.notifications.map(n=>n.id).sort();
    assert.deepEqual(await feed(),[id(21)]);
    assert.deepEqual(await visible(),['normal']);
    await c.query("update ops.notification_delivery set held_until=now()-interval '1 second' where notification_id=$1",[id(20)]);
    assert.deepEqual(await feed(),[id(20),id(21)]);
    assert.deepEqual(await visible(),['held','normal']);
    await c.query('delete from ops.notification_delivery where notification_id=$1',[id(20)]);
    await c.query('delete from ops.notification where id in ($1,$2)',[id(20),id(21)]);
  });
  let key=100;
  const change=async(verb,items,extra={},actor=joe,requestKey=id(key++))=>{
    await c.query("select set_config('carr.acting_actor_slug',$1,false),set_config('carr.verified_human_actor_slug',$1,false)",[actor.slug]);
    await c.query('begin');
    try {
      const out=await executeRegisteredTool(c,actor,verb,{idempotency_key:requestKey,
        items:items.map(({item_id,base_version})=>({item_id,base_version})),...extra});
      await c.query('commit'); return out;
    } catch(error){await c.query('rollback');throw error;}
  };
  await t.test('settle and un-settle are reversible, versioned, idempotent and private',async()=>{
    const item=(await read()).items[0];
    const settled=await change('settle-home-inbox',[item],{},joe,id(900));
    assert.equal(settled.results[0].item.disposition,'settled');
    const retry=await change('settle-home-inbox',[item],{},joe,id(900));
    assert.deepEqual(retry,{replayed:true,...settled});
    await c.query('delete from tool_call where idempotency_key=$1',[id(900)]);
    assert.deepEqual(await change('settle-home-inbox',[item],{},joe,id(900)),settled,'durable replay survives envelope retention');
    const stale=await change('unsettle-home-inbox',[item]);
    assert.equal(stale.results[0].error,'version_conflict');
    const other=await change('settle-home-inbox',[item],{},dell);
    assert.equal(other.results[0].error,'inbox_item_not_found');
    const undone=await change('unsettle-home-inbox',[settled.results[0].item]);
    assert.equal(undone.results[0].item.disposition,'inbox');
    assert.equal(undone.results[0].item.explicit_unsettle,true);
    assert.equal((await read(dell)).items[0].disposition,'inbox');
  });
  await t.test('snooze ignores completion but wakes on a material change, reopening or expiry',async()=>{
    const item=(await read()).items[0];
    await change('snooze-home-inbox',[item],{until:'2099-11-01T12:00:00Z'});
    await c.query("update next_action set status='done',version=version+1,updated_at='2026-10-30T13:00:00Z' where id=$1",[id(10)]);
    let current=(await read()).items[0];
    assert.equal(current.source_state,'done'); assert.equal(current.disposition,'snoozed');
    await c.query("update next_action set description='New material instruction',version=version+1 where id=$1",[id(10)]);
    current=(await read()).items[0]; assert.equal(current.disposition,'inbox');
    await change('snooze-home-inbox',[current],{until:'2099-11-01T12:00:00Z'});
    await c.query("update next_action set status='open',version=version+1 where id=$1",[id(10)]);
    current=(await read()).items[0]; assert.equal(current.disposition,'inbox');
    await change('snooze-home-inbox',[current],{until:'2099-11-01T12:00:00Z'});
    await c.query("update ops.home_inbox_disposition set snoozed_until='2000-01-01T00:00:00Z' where actor_id=$1 and item_id=$2",[joe.id,current.item_id]);
    assert.equal((await read()).items[0].disposition,'inbox');
  });
  await t.test('a concurrent material edit of a done task wakes snooze or refuses stale input',async()=>{
    const editor=new pg.Client(c.connectionParameters);
    await editor.connect();
    let pending;
    try {
      const item=(await read()).items.find(i=>i.source_id===id(11));
      const pid=(await c.query('select pg_backend_pid() pid')).rows[0].pid;
      await c.query(`alter function ops.home_inbox_items(uuid) rename to home_inbox_items_observed;
        create function ops.home_inbox_items(p_actor uuid) returns setof jsonb
        language plpgsql stable set search_path=pg_catalog,pg_temp as $$
        begin
          return query select * from ops.home_inbox_items_observed(p_actor);
          perform pg_advisory_lock(617003);
          perform pg_advisory_unlock(617003);
        end $$`);
      await editor.query('select pg_advisory_lock(617003)');
      pending=change('snooze-home-inbox',[item],{until:'2099-11-01T12:00:00Z'});
      let waiting=false;
      for(let attempt=0;attempt<200;attempt++) {
        const state=await editor.query("select wait_event from pg_stat_activity where pid=$1",[pid]);
        if(state.rows[0].wait_event==='advisory') {waiting=true;break;}
        await setTimeout(10);
      }
      assert.equal(waiting,true,'command paused after observing the source version');
      await editor.query("update next_action set description='Concurrent material instruction',version=version+1 where id=$1",[id(11)]);
      await editor.query('select pg_advisory_unlock(617003)');
      const result=(await pending).results[0];
      assert.ok(result.ok || result.error==='version_conflict');
      const current=(await read()).items.find(i=>i.item_id===item.item_id);
      assert.equal(current.title,'Concurrent material instruction');
      assert.equal(current.source_state,'done');
      assert.equal(current.disposition,'inbox','the newer instruction must not be swallowed by snooze');
    } finally {
      await editor.query('select pg_advisory_unlock_all()');
      if(pending) await pending;
      await c.query(`drop function ops.home_inbox_items(uuid);
        alter function ops.home_inbox_items_observed(uuid) rename to home_inbox_items`);
      await editor.end();
    }
  });
  await t.test('new failure or request wakes snooze; existing failure and background completion do not',async()=>{
    await c.query(`insert into ops.notification values($1,$2,'deal','synthetic','failure','Synthetic failure','/home','2026-10-29')`,[id(20),joe.id]);
    let item=(await read()).items.find(i=>i.source_type==='notification');
    assert.equal(item.source_state,'failed');
    await change('snooze-home-inbox',[item],{until:'2099-11-01T12:00:00Z'});
    item=(await read()).items.find(i=>i.source_type==='notification');
    assert.equal(item.disposition,'snoozed','existing failures do not wake');
    await c.query(`insert into ops.notification values($1,$2,'deal','synthetic','completion','Synthetic completion','/home','2026-10-30')`,[id(21),joe.id]);
    item=(await read()).items.find(i=>i.source_type==='notification');
    assert.equal(item.disposition,'snoozed'); assert.equal(item.unseen,true);
    await c.query(`insert into ops.notification values($1,$2,'deal','synthetic','failure','New failure','/home','2026-10-31')`,[id(22),joe.id]);
    item=(await read()).items.find(i=>i.source_type==='notification');
    assert.equal(item.disposition,'inbox');
    await change('snooze-home-inbox',[item],{until:'2099-11-01T12:00:00Z'});
    await c.query(`insert into ops.notification values($1,$2,'deal','synthetic','action_required','New approval request','/home','2026-11-01')`,[id(23),joe.id]);
    item=(await read()).items.find(i=>i.source_type==='notification');
    assert.equal(item.disposition,'inbox');
    assert.equal((await read()).items.filter(i=>i.source_type==='notification').length,1,'one item per notification source');
    await change('settle-home-inbox',[item]);
    await c.query(`insert into ops.notification values($1,$2,'deal','synthetic','action_required','Shared source','/home','2026-11-01')`,[id(24),dell.id]);
    const shared=(await read(dell)).items.find(i=>i.item_id===item.item_id);
    assert.equal(shared.disposition,'inbox','the same source has an independent partner disposition');
    await read();
    assert.equal((await c.query('select count(*)::int n from ops.notification_read')).rows[0].n,0,'settle never acknowledges notifications');
  });
  await t.test('working is collapsed; terminal and unknown work never auto-settle in this delivery',async()=>{
    await c.query(`insert into capture_session values($1,$2,'distilling','2026-10-30','2026-10-29')`,[id(30),joe.id]);
    let snapshot=await read();
    assert.equal(snapshot.working_collapsed,true);
    let item=snapshot.items.find(i=>i.source_type==='capture_session');
    assert.equal(item.section,'working');
    await c.query("update capture_session set state='done',state_at='2026-10-31' where id=$1",[id(30)]);
    item=(await read()).items.find(i=>i.source_type==='capture_session');
    assert.equal(item.source_state,'done'); assert.equal(item.disposition,'inbox');
    await change('unsettle-home-inbox',[item]);
    await c.query("update capture_session set state='failed',state_at='2026-11-01' where id=$1",[id(30)]);
    item=(await read()).items.find(i=>i.source_type==='capture_session');
    assert.equal(item.explicit_unsettle,false,'new real activity clears the explicit override');
    assert.equal(item.disposition,'inbox');
    await change('snooze-home-inbox',[item],{until:'2099-11-01T12:00:00Z'});
    await c.query("update capture_session set state='done',state_at='2026-11-02' where id=$1",[id(30)]);
    item=(await read()).items.find(i=>i.source_type==='capture_session');
    assert.equal(item.disposition,'snoozed','recovery completion does not wake an existing failure');
    assert.equal((await read()).auto_settle_enabled,false);
  });
  await t.test('bulk reports each stale and accepted item, and refuses duplicate and reused keys',async()=>{
    const items=(await read()).items.filter(i=>i.source_type==='next_action');
    const result=await change('settle-home-inbox',[items[0],{...items[1],base_version:'0'.repeat(32)}]);
    assert.equal(result.results[0].ok,true); assert.equal(result.results[1].error,'version_conflict');
    await assert.rejects(change('settle-home-inbox',[items[0],items[0]]),e=>e.payload.error==='duplicate_inbox_item');
    await assert.rejects(change('unsettle-home-inbox',[items[0]],{},joe,id(900)),e=>e.payload.error==='key_reuse');
    assert.deepEqual((await c.query('select status,hold_until::text from next_action where id=$1',[id(10)])).rows[0],{status:'open',hold_until:'2026-11-02'});
  });
  await t.test('reply drafts use mailbox ownership and sending results, never approval as completion',async()=>{
    await c.query(`insert into activity values($1,'{"mailbox_owner":"joe"}'),($2,'{"mailbox_owner":"dell"}')`,[id(40),id(41)]);
    await c.query(`insert into email_proposal values($1,'reply_draft','joe',$2,1,'pending','Synthetic reply','2026-10-29',null,null),
      ($3,'reply_draft','joe',$4,1,'pending','Other mailbox','2026-10-29',null,null)`,[id(42),id(40),id(43),id(41)]);
    let item=(await read()).items.find(i=>i.source_type==='email_proposal');
    assert.equal(item.source_id,id(42)); assert.equal(item.source_state,'needs_attention');
    await c.query("update email_proposal set status='approved_to_send',submitted_at='2026-10-30',version=2 where id=$1",[id(42)]);
    item=(await read()).items.find(i=>i.source_type==='email_proposal');
    assert.equal(item.source_state,'working');
    await change('snooze-home-inbox',[item],{until:'2099-11-01T12:00:00Z'});
    await c.query("update email_proposal set status='sent',result_recorded_at='2026-10-31',version=3 where id=$1",[id(42)]);
    item=(await read()).items.find(i=>i.source_type==='email_proposal');
    assert.equal(item.source_state,'done'); assert.equal(item.disposition,'snoozed');
  });
  await t.test('notifications about an existing task enrich it without a duplicate card',async()=>{
    await c.query(`insert into ops.notification values($1,$2,'next_action',$3,'failure','Task failed','/home','2026-11-05')`,[id(50),joe.id,id(11)]);
    const snapshot=await read();
    assert.equal(snapshot.items.filter(i=>i.source_id===id(11)).length,1);
    const task=snapshot.items.find(i=>i.source_id===id(11));
    assert.equal(task.source_state,'failed'); assert.equal(task.unseen,true);
    assert.equal(snapshot.items.filter(i=>i.source_type==='notification').length,1,'linked notifications do not make another inbox item');
    await c.query("update next_action set status='open',version=version+1,updated_at='2026-11-06' where id=$1",[id(11)]);
    const reopened=(await read()).items.find(i=>i.source_id===id(11));
    await change('snooze-home-inbox',[reopened],{until:'2099-11-01T12:00:00Z'});
    await c.query(`insert into ops.notification values($1,$2,'next_action',$3,'completion','Background work finished','/home','2026-11-07')`,[id(51),joe.id,id(11)]);
    assert.equal((await read()).items.find(i=>i.source_id===id(11)).disposition,'snoozed',
      'a completion notification never wakes a still-open canonical task');
  });
  await t.test('reader cannot read dispositions or supply another actor to private helpers',async()=>{
    await c.query('set role carr_reader');
    await assert.rejects(c.query('select * from ops.home_inbox_disposition'),e=>e.code==='42501');
    await assert.rejects(c.query('select ops.read_home_inbox()'),e=>e.code==='42501');
    await c.query('reset role');
    await c.query('set role carr_writer');
    await assert.rejects(c.query('select * from ops.home_inbox_sources($1)',[dell.id]),e=>e.code==='42501');
    await c.query('reset role');
  });
  await t.test('overflow refuses the snapshot rather than silently truncating',async()=>{
    const remaining=1000-(await read()).items.length;
    await c.query(`insert into next_action(id,owner_id,description,status,version,created_at,updated_at)
      select ('af000000-0000-4000-8000-'||lpad(n::text,12,'0'))::uuid,$1,'Synthetic bound','open',1,'2026-10-30','2026-10-30'
      from generate_series(1,$2::integer)n`,[joe.id,remaining]);
    assert.equal((await read()).items.length,1000);
    await c.query(`insert into next_action(id,owner_id,description,status,version,created_at,updated_at)
      values($1,$2,'One over the limit','open',1,'2026-10-30','2026-10-30')`,[id(99),joe.id]);
    await assert.rejects(read(),e=>e.payload.error==='inbox_limit_exceeded');
  });

});

test('Home inbox v2 projects current partner work with context', async t => {
  const db=await postgresDatabase('home-inbox-v2-');
  const c=db.client;
  t.after(()=>db.close());
  await initialize(c);
  await c.query(`alter table next_action add subject_type text default 'deal', add subject_id uuid, add due_on date;
    alter table capture_post_call_action add deal_id uuid, add due_on date, add version integer default 1;
    create table deal(id uuid primary key,name text);
    create table loop_item(id uuid primary key,number text,title text);
    insert into deal values('${id(60)}','Synthetic lease');
    insert into loop_item values('${id(61)}','250','Synthetic orb panel');
    insert into next_action(id,owner_id,subject_id,description,status,version,created_at,updated_at,hold_until,due_on) values
    ('${id(70)}','${joe.id}','${id(60)}','Call owner','open',1,now(),now(),null,current_date),
    ('${id(71)}','${joe.id}','${id(60)}',' CALL   owner ','open',2,now()-interval '1 day',now()-interval '1 day',null,current_date),
    ('${id(72)}','${joe.id}','${id(60)}','Recent done','done',1,now(),now(),null,null),
    ('${id(73)}','${joe.id}','${id(60)}','Old done','done',1,now()-interval '8 days',now()-interval '8 days',null,null),
    ('${id(74)}','${joe.id}','${id(60)}','Dropped','dropped',1,now(),now(),null,null),
    ('${id(75)}','${joe.id}','${id(60)}','Held','open',1,now(),now(),current_date+1,null);
    insert into ops.notification values
    ('${id(80)}','${joe.id}','loop','${id(61)}','action_required','Action needed on an open loop',null,now());`);
  const read=async()=> (await c.query('select ops.read_home_inbox_v2() result')).rows[0].result;
  const before=(await c.query('select ops.read_home_inbox() result')).rows[0].result;
  console.log('Synthetic v1 seed items',before.items.length);
  if (!process.env.HOME_INBOX_TEST_V1) {
    await c.query(readFileSync(new URL('../../migrations/1136_home_inbox_projection.sql',import.meta.url),'utf8'));
  }
  const snapshot=await executeRegisteredTool(c,joe,'read-home-inbox-v2',{});
  assert.deepEqual(snapshot,await read());
  console.log('Synthetic v2 seed items',snapshot.items.length);
  await t.test('done has a seven-day shelf and dropped is hidden',()=>{
    const done=snapshot.items.find(i=>i.source_id===id(72));
    assert.equal(done.section,'done');
    assert.ok(!snapshot.items.some(i=>[id(73),id(74)].includes(i.source_id)));
  });
  await t.test('identical open tasks collapse by owner and deal with actionable context',()=>{
    const task=snapshot.items.find(i=>i.source_id===id(71));
    assert.equal(snapshot.items.filter(i=>[id(70),id(71)].includes(i.source_id)).length,1);
    assert.deepEqual(task.merged_source_ids,[id(71),id(70)]);
    assert.equal(task.context_label,'Synthetic lease');
    assert.equal(task.kind_label,'Task');
    assert.ok(task.due_on);
    assert.equal(task.open_url,`/deals?deal=${id(60)}&focus=next-step`);
  });
  await t.test('task completion uses canonical action identity and integer version',()=>{
    const task=snapshot.items.find(i=>i.source_id===id(71));
    assert.equal(task.action_id,id(71));
    assert.equal(task.action_version,2);
    assert.equal(task.deal_id,id(60));
    assert.deepEqual(homeReviewReference(task),{kind:'action',item_id:task.item_id,
      source_version:task.source_version,operations:['complete-one-action']});
  });
  await t.test('future hold belongs to Held',()=>{
    assert.equal(snapshot.items.find(i=>i.source_id===id(75)).section,'held');
  });
  await t.test('notification title names its loop',()=>{
    assert.match(snapshot.items.find(i=>i.source_type==='notification' && i.title.includes('loop')).title,/Synthetic orb panel/);
  });
  await t.test('notifications admit exactly the partner-facing subject allowlist',async()=>{
    const allowed=['deal','loop','call','email','doc_conversation_request'];
    const subjects=[...allowed,'engineering','release','next_action','capture_session','email_proposal','unknown'];
    const notificationIds=subjects.map((_,n)=>id(800+n));
    for (const [n,subject] of subjects.entries()) {
      await c.query(`insert into ops.notification values($1,$2,$3,$4,'action_required','Allowlist request',null,now())`,
        [notificationIds[n],joe.id,subject,`allowlist-${subject}`]);
    }
    const notifications=(await read()).items.filter(i=>i.source_type==='notification' && i.title.startsWith('Allowlist request'));
    assert.deepEqual(notifications.map(i=>JSON.parse(i.source_id)[0]).sort(),allowed.toSorted());
    await c.query('delete from ops.notification where id=any($1::uuid[])',[notificationIds]);
  });
  await t.test('v2 keeps completion identity across task kinds and non-deal tasks',async()=>{
    await c.query(`insert into next_action(id,owner_id,subject_type,subject_id,description,status,version,created_at,updated_at)
      values($1,$2,'client',$3,'Client task','open',7,now(),now())`,[id(90),joe.id,id(62)]);
    await c.query(`insert into capture_post_call_action(id,owner_id,deal_id,description,status,updated_at,version)
      values('${id(91)}','${joe.id}','${id(60)}','Accepted call task','open',now(),4)`);
    const items=(await read()).items;
    const client=items.find(i=>i.source_id===id(90));
    assert.equal(client.action_id,id(90));
    assert.equal(client.action_version,7);
    assert.equal(client.deal_id,null);
    const call=items.find(i=>i.source_id===id(91));
    assert.equal(call.action_id,id(91));
    assert.equal(call.action_version,4);
    assert.equal(call.deal_id,id(60));
    await c.query('delete from next_action where id=$1',[id(90)]);
    await c.query('delete from capture_post_call_action where id=$1',[id(91)]);
  });
  await t.test('v2 dispositions stay private and reject stale canonical edits',async()=>{
    const task=(await read()).items.find(i=>i.source_id===id(71));
    await c.query('set role carr_reader');
    await assert.rejects(c.query('select ops.read_home_inbox_v2()'),e=>e.code==='42501');
    await c.query('reset role');
    await c.query('set role carr_writer');
    assert.equal((await read()).ok,true);
    await c.query('reset role');
    const change=async(key,item)=> (await c.query("select ops.change_home_inbox($1,'settle',$2,null) result",
      [key,JSON.stringify([{item_id:item.item_id,base_version:item.base_version}])])).rows[0].result;
    await c.query("select set_config('carr.acting_actor_slug','dell',false),set_config('carr.verified_human_actor_slug','dell',false)");
    assert.equal((await read()).items.length,0);
    assert.equal((await change(id(93),task)).results[0].error,'inbox_item_not_found');
    await c.query("select set_config('carr.acting_actor_slug','joe',false),set_config('carr.verified_human_actor_slug','joe',false)");
    assert.equal((await change(id(94),task)).results[0].item.disposition,'settled');
    await c.query('update next_action set version=version+1 where id=$1',[id(71)]);
    assert.equal((await change(id(95),task)).results[0].error,'version_conflict');
  });
  await t.test('generated v2 contract matches the runtime declaration and projected fields',()=>{
    const contract=JSON.parse(readFileSync(new URL('../contracts/home-inbox.v2.json',import.meta.url),'utf8'));
    assert.deepEqual(contract,HOME_INBOX_CONTRACT);
    assert.equal(snapshot.schema_version,contract.schema_version);
    for(const item of snapshot.items) {
      for(const field of Object.keys(contract.item_fields)) assert.ok(Object.hasOwn(item,field),field);
      assert.ok(contract.sections.includes(item.section));
    }
  });
  await t.test('newer duplicate edits and drops retain snooze and stable identity',async()=>{
    const item=(await read()).items.find(i=>i.source_id===id(71));
    const result=(await c.query("select ops.change_home_inbox($1,'snooze',$2,'2099-01-01') result",
      [id(96),JSON.stringify([{item_id:item.item_id,base_version:item.base_version}])])).rows[0].result;
    assert.equal(result.results[0].ok,true);
    await c.query("update next_action set updated_at=now(),version=version+1 where id=$1",[id(70)]);
    let current=(await read()).items.find(i=>i.item_id===item.item_id);
    assert.equal(current.disposition,'snoozed');
    await c.query("update next_action set status='dropped',version=version+1 where id=$1",[id(70)]);
    current=(await read()).items.find(i=>i.item_id===item.item_id);
    assert.equal(current.disposition,'snoozed');
  });
  await t.test('document conversation requests remain partner work and loop numbers never resolve titles',async()=>{
    await c.query(`insert into ops.notification values
      ('${id(83)}','${joe.id}','doc_conversation_request','request','action_required','Review document',null,now()),
      ('${id(84)}','${joe.id}','loop','250','action_required','Review loop',null,now())`);
    const items=(await read()).items;
    assert.ok(items.some(i=>i.source_type==='notification' && i.source_id.includes('doc_conversation_request')));
    assert.equal(items.find(i=>i.source_type==='notification' && i.source_id.includes('250')).context_label,'loop 250');
  });
  await t.test('over-cap returns newest open work without refusing the snapshot',async()=>{
    await c.query(`insert into next_action(id,owner_id,subject_id,description,status,version,created_at,updated_at)
      select ('af000000-0000-4000-8000-'||lpad(n::text,12,'0'))::uuid,$1,$2,'Bound '||n,'open',1,
        now()+n*interval '1 second',now()+n*interval '1 second' from generate_series(1,1002)n`,[joe.id,id(60)]);
    const result=await read();
    assert.equal(result.ok,true);
    assert.equal(result.items.length,1000);
    assert.equal(result.items[0].title,'Bound 1002');
    assert.equal(result.truncated,true);
    assert.equal(result.schema_version,'home-inbox.v2');
  });
});

test('v1 snapshots and stored dispositions survive the projection expansion',async t=>{
  const db=await postgresDatabase('inbox-upgrade-'); const c=db.client; t.after(()=>db.close());
  await initialize(c);
  await c.query(`alter table next_action add subject_type text default 'deal',add subject_id uuid,add due_on date;
    alter table capture_post_call_action add deal_id uuid,add due_on date,add version integer default 1;
    create table deal(id uuid primary key,name text); create table loop_item(id uuid primary key,number text,title text);
    insert into next_action(id,owner_id,description,status,version,created_at,updated_at) values
      ('${id(70)}','${joe.id}','Snoozed task','open',1,now(),now()),
      ('${id(71)}','${joe.id}','Settled task','open',1,now(),now()),
      ('${id(72)}','${joe.id}','Done task','done',1,now(),now());`);
  const read=async()=> (await c.query('select ops.read_home_inbox() result')).rows[0].result;
  let seq=500;
  const change=async(command,item)=> (await c.query('select ops.change_home_inbox($1,$2,$3,$4) result',
    [id(seq++),command,JSON.stringify([{item_id:item.item_id,base_version:item.base_version}]),command==='snooze'?'2099-01-01':null])).rows[0].result;
  const items=(await read()).items;
  await change('snooze',items.find(i=>i.source_id===id(70)));
  await change('settle',items.find(i=>i.source_id===id(71)));
  const fixture=await read();
  await c.query(readFileSync(new URL('../../migrations/1136_home_inbox_projection.sql',import.meta.url),'utf8'));
  await t.test('v1 fixture shape and values are byte compatible',async()=>{
    assert.equal(JSON.stringify(await read()),JSON.stringify(fixture));
    for (const command of ['unsettle','snooze','settle']) {
      const result=await change(command,(await read()).items.find(i=>i.source_id===id(72)));
      assert.equal(result.schema_version,'home-inbox-command.v1');
      assert.deepEqual(Object.keys(result.results[0].item).sort(),Object.keys(fixture.items[0]).sort());
      assert.ok(['inbox','working','snoozed','settled'].includes(result.results[0].item.section));
    }
  });
  await t.test('v2 retains dispositions written under migration 1114',async()=>{
    const current=(await c.query('select ops.read_home_inbox_v2() result')).rows[0].result;
    assert.equal(current.items.find(i=>i.source_id===id(70)).disposition,'snoozed');
    assert.equal(current.items.find(i=>i.source_id===id(71)).disposition,'settled');
  });
});

test('projection read at realistic synthetic volume',{skip:!process.env.HOME_INBOX_BENCHMARK},async t=>{
  const db=await postgresDatabase('inbox-benchmark-'); const c=db.client; t.after(()=>db.close());
  await initialize(c);
  await c.query(`alter table next_action add subject_type text default 'deal',add subject_id uuid,add due_on date;
    alter table capture_post_call_action add deal_id uuid,add due_on date,add version integer default 1;
    create table deal(id uuid primary key,name text); create table loop_item(id uuid primary key,number text,title text);
    insert into deal select md5('deal'||n)::uuid,'Synthetic deal '||n from generate_series(1,300)n;
    insert into next_action(id,owner_id,subject_id,description,status,version,created_at,updated_at)
      select md5('task'||n)::uuid,'${joe.id}',md5('deal'||(1+n%300))::uuid,'Task '||n,
        case when n%5=0 then 'done' else 'open' end,1,now()-n*interval '1 minute',now()-n*interval '1 minute'
      from generate_series(1,20000)n;
    insert into ops.notification select md5('notification'||n)::uuid,'${joe.id}','deal',
      md5('deal'||(1+n%300))::uuid::text,'action_required','Synthetic request',null,now()-n*interval '1 second'
      from generate_series(1,10000)n;
    analyze next_action; analyze deal; analyze ops.notification;`);
  await c.query(readFileSync(new URL('../../migrations/1136_home_inbox_projection.sql',import.meta.url),'utf8'));
  const times=[];
  for(let n=0;n<6;n++){
    const start=performance.now();
    const result=(await c.query('select ops.read_home_inbox_v2() result')).rows[0].result;
    const elapsed=performance.now()-start;
    assert.equal(result.ok,true); assert.equal(result.items.length,1000); assert.equal(result.truncated,true);
    if(n) times.push(elapsed);
  }
  const explain=(await c.query('explain (analyze,buffers,verbose) select ops.read_home_inbox_v2()')).rows;
  const migration=readFileSync(new URL('../../migrations/1136_home_inbox_projection.sql',import.meta.url),'utf8');
  const sourceSql=migration.match(/as \$\$([\s\S]*?)\$\$;/)[1].replaceAll(/\bp_actor\b/g,()=>'$1');
  const sources=(await c.query('explain (analyze,buffers,verbose) '+sourceSql,[joe.id])).rows;
  times.sort((a,b)=>a-b);
  const report={tasks:20000,deals:300,notifications:10000,successful_reads:6,failures:0,median_ms:times[2],range_ms:[times[0],times[4]],times_ms:times,explain,sources};
  writeFileSync('/tmp/WR39.S1.J33-benchmark.json',JSON.stringify(report,null,2)+'\n');
  console.log('Synthetic projection benchmark',JSON.stringify({...report,explain:undefined,sources:undefined}));
});

test('legacy duplicate dispositions survive in v1 and carry to stable v2 cards',async t=>{
  const db=await postgresDatabase('inbox-carry-'); const c=db.client; t.after(()=>db.close());
  await initialize(c);
  await c.query(`alter table next_action add subject_type text default 'deal',add subject_id uuid,add due_on date;
    alter table capture_post_call_action add deal_id uuid,add due_on date,add version integer default 1;
    create table deal(id uuid primary key,name text); create table loop_item(id uuid primary key,number text,title text);
    insert into next_action(id,owner_id,description,status,version,created_at,updated_at) values
      ('${id(73)}','${joe.id}','Duplicate task','open',1,now()-interval '2 days',now()),
      ('${id(74)}','${joe.id}','Duplicate task','open',1,now(),now()),
      ('${id(75)}','${joe.id}','Settled duplicate','open',1,now()-interval '2 days',now()),
      ('${id(76)}','${joe.id}','Settled duplicate','open',1,now(),now())`);
  const readV1=async()=> (await c.query('select ops.read_home_inbox() result')).rows[0].result;
  const before=await readV1();
  for(const [n,command] of [[74,'snooze'],[76,'settle']]) {
    const item=before.items.find(i=>i.source_id===id(n));
    const result=(await c.query('select ops.change_home_inbox($1,$2,$3,$4) result',
      [id(500+n),command,JSON.stringify([{item_id:item.item_id,base_version:item.base_version}]),command==='snooze'?'2099-01-01':null])).rows[0].result;
    assert.equal(result.results[0].ok,true);
  }
  const fixture=await readV1();
  const legacyColumns='actor_id,item_id,disposition,snoozed_until,source_version,material_version,version,explicit_unsettle,updated_at';
  const stored=(await c.query(`select ${legacyColumns} from ops.home_inbox_disposition order by actor_id,item_id`)).rows;
  await c.query(readFileSync(new URL('../../migrations/1136_home_inbox_projection.sql',import.meta.url),'utf8'));
  assert.deepEqual(await readV1(),fixture,'both v1 duplicate pairs keep their original dispositions and versions');
  assert.deepEqual((await c.query(`select ${legacyColumns} from ops.home_inbox_disposition order by actor_id,item_id`)).rows,stored);
  for(const status of ['open','dropped']) {
    await c.query('update next_action set status=$1,version=version+1,updated_at=now() where id=any($2::uuid[])',[status,[id(74),id(76)]]);
    const after=(await c.query('select ops.read_home_inbox_v2() result')).rows[0].result;
    assert.equal(after.items.length,2);
    assert.equal(after.items.find(i=>i.source_id===id(73)).disposition,'snoozed');
    assert.equal(after.items.find(i=>i.source_id===id(75)).disposition,'settled');
  }
  const representative=(await readV1()).items.find(i=>i.source_id===id(73));
  const changed=(await c.query("select ops.change_home_inbox($1,'settle',$2,null) result",
    [id(600),JSON.stringify([{item_id:representative.item_id,base_version:representative.base_version}])])).rows[0].result;
  assert.equal(changed.results[0].ok,true);
  const after=(await c.query('select ops.read_home_inbox_v2() result')).rows[0].result;
  assert.equal(after.items.find(i=>i.source_id===id(73)).disposition,'settled','a representative command overrides inherited disposition');
});

test('Inbox v3 auto-settlement follows canonical work and preserves reversible dispositions',async t=>{
  const db=await postgresDatabase('inbox-settlement-'); const c=db.client; t.after(()=>db.close());
  await initialize(c);
  await c.query(`alter table next_action add subject_type text default 'deal',add subject_id uuid,add due_on date;
    alter table capture_post_call_action add deal_id uuid,add due_on date,add version integer default 1;
    create table deal(id uuid primary key,name text); create table loop_item(id uuid primary key,number text,title text);
    insert into activity values('${id(700)}','{"mailbox_owner":"joe"}');
    insert into email_proposal values('${id(701)}','reply_draft','joe','${id(700)}',1,'pending','Synthetic reply',now(),null,null);
    insert into next_action(id,owner_id,description,status,version,created_at,updated_at)
      values('${id(702)}','${joe.id}','Synthetic task','open',1,now(),now());`);
  await c.query(readFileSync(new URL('../../migrations/1136_home_inbox_projection.sql',import.meta.url),'utf8'));
  const schema=readFileSync(new URL('../../db/schema.sql',import.meta.url),'utf8');
  for(const name of ['fact_observation','fact_observation_batch','canonical_fact',
    'tour_share_grant','tour_share_feedback_event','tour_share_grant_revocation_receipt']) {
    await c.query(schema.match(new RegExp(`CREATE TABLE ops\\.${name} \\([\\s\\S]*?\\n\\);`))[0]);
  }
  await c.query('alter table ops.tour_share_feedback_event alter column event_order add generated always as identity');
  const reviewSql=readFileSync(new URL('../../migrations/1099_fact_review.sql',import.meta.url),'utf8');
  await c.query(reviewSql.match(/create table ops\.fact_review_decision \([\s\S]*?\n\);/)[0].replace(/ references ops\.fact_key_contract\(fact_key\)/g,'').replace(/ references ops\.fact_observation\(id\)/g,''));
  await c.query(`alter table ops.fact_observation add normalized_schedule jsonb,add region_id text;
    alter table ops.canonical_fact add normalized_schedule jsonb;
    alter table ops.fact_review_decision add extraction_job_ids uuid[] default '{}';
    create table ops.deal_extraction_job(id uuid,organization_tenant_id text,entity_ref text,recorded_at timestamptz,receipt jsonb);
    create table ops.fact_key_contract(fact_key text,canonical_unit text);
    insert into ops.fact_key_contract values('base_rent_per_sf','USD/SF/year');`);
  for(const [file,name] of [['1109_deal_schedules.sql','fact_observation_json'],['1109_deal_schedules.sql','fact_review_field'],['1103_deal_extraction.sql','list_fact_review'],['1103_deal_extraction.sql','resolve_fact_review'],['1109_deal_schedules.sql','promote_fact']]) {
    const sql=readFileSync(new URL(`../../migrations/${file}`,import.meta.url),'utf8');
    await c.query(sql.match(new RegExp(`create or replace function ops\\.${name}\\([\\s\\S]*?\\$\\$;`))[0]);
  }
  await c.query('alter table deal add owner text');
  const v2Before=(await executeRegisteredTool(c,joe,'read-home-inbox-v2',{}));
  await c.query(readFileSync(new URL('../../migrations/1140_home_inbox_resolution_adapters.sql',import.meta.url),'utf8'));
  assert.deepEqual(await executeRegisteredTool(c,joe,'read-home-inbox-v2',{}),v2Before,'shared placement preserves the v2 snapshot');
  await c.query('set role carr_writer');
  assert.equal((await executeRegisteredTool(c,joe,'read-home-inbox-v3',{})).ok,true);
  await c.query('reset role');
  const read=()=>executeRegisteredTool(c,joe,'read-home-inbox-v3',{});
  const item=async n=>(await read()).items.find(i=>i.source_id===id(n));
  const detail=current=>executeRegisteredTool(c,joe,'read-home-review',{
    item_id:current.item_id,source_version:current.source_version});
  await t.test('review statechart: projected to detail loaded reads no domain state',async()=>{
    const current=await item(702);
    assert.equal(current.review_reference.kind,'action');
    assert.deepEqual(current.review_reference.operations,['complete-one-action']);
    const before=(await c.query('select to_jsonb(t) row from next_action t order by id')).rows;
    const loaded=await detail(current);
    assert.equal(loaded.schema_version,'home-review.v1');
    assert.deepEqual(loaded.operations,[{verb:'complete-one-action',args:{action_id:id(702),base_version:1}}]);
    assert.deepEqual((await c.query('select to_jsonb(t) row from next_action t order by id')).rows,before);
    assert.deepEqual(await detail(current),loaded);
  });
  await t.test('review statechart: projected to refused on stale source',async()=>{
    const current=await item(702);
    await c.query('update next_action set version=version+1 where id=$1',[id(702)]);
    await assert.rejects(detail(current),e=>e.payload?.error==='home_review_stale');
  });
  await t.test('review statechart: another actor cannot load the card',async()=>{
    const current=await item(702);
    await c.query("select set_config('carr.acting_actor_slug','dell',false),set_config('carr.verified_human_actor_slug','dell',false)");
    try {
      await assert.rejects(executeRegisteredTool(c,dell,'read-home-review',{
        item_id:current.item_id,source_version:current.source_version}),e=>e.payload?.error==='home_review_not_found');
    } finally {
      await c.query("select set_config('carr.acting_actor_slug','joe',false),set_config('carr.verified_human_actor_slug','joe',false)");
    }
  });
  await t.test('review statechart: refused to projected after refresh',async()=>{
    const current=await item(702);
    assert.equal((await detail(current)).operations[0].args.base_version,2);
  });
  let seq=800;
  const change=async(command,current,until=null)=>executeRegisteredTool(c,joe,`${command}-home-inbox`,{
    idempotency_key:id(seq++),items:[{item_id:current.item_id,base_version:current.base_version}],...(until?{until}:{})});
  await t.test('open to working to auto-settled uses the canonical reply state',async()=>{
    assert.equal((await item(701)).section,'inbox');
    await c.query("update email_proposal set status='approved_to_send',version=version+1 where id=$1",[id(701)]);
    assert.equal((await item(701)).section,'working');
    await c.query("update email_proposal set status='sent',result_recorded_at=now(),version=version+1 where id=$1",[id(701)]);
    const sent=await item(701);
    assert.equal(sent.section,'settled'); assert.equal(sent.auto_settled,true);
    assert.equal(sent.disposition,'settled');
  });
  await t.test('open to done automatically settles a task without mutating its disposition',async()=>{
    await c.query("update next_action set status='done',updated_at=now(),version=version+1 where id=$1",[id(702)]);
    const done=await item(702);
    assert.equal(done.section,'settled'); assert.equal(done.auto_settled,true);
    assert.equal((await c.query('select count(*)::int n from ops.home_inbox_disposition')).rows[0].n,0);
  });
  await t.test('Undo an automatic settlement persists until real activity and supports exact-key replay',async()=>{
    const done=await item(702);
    const args={idempotency_key:id(seq++),items:[{item_id:done.item_id,base_version:done.base_version}]};
    const undo=await executeRegisteredTool(c,joe,'unsettle-home-inbox',args);
    assert.equal(undo.results[0].ok,true);
    const replay=await executeRegisteredTool(c,joe,'unsettle-home-inbox',args);
    assert.equal(replay.replayed,true); assert.deepEqual(replay.results,undo.results);
    for(let n=0;n<2;n++) {
      const undone=await item(702);
      assert.equal(undone.section,'inbox'); assert.equal(undone.explicit_unsettle,true); assert.equal(undone.auto_settled,false);
    }
    await c.query("update next_action set status='open',version=version+1,updated_at=now() where id=$1",[id(702)]);
    assert.equal((await item(702)).section,'inbox'); assert.equal((await item(702)).explicit_unsettle,false);
  });
  await t.test('manual settlement and its Undo keep their authority and receipt versions',async()=>{
    const current=await item(702);
    assert.equal((await change('settle',current)).results[0].ok,true);
    assert.equal((await item(702)).section,'settled'); assert.equal((await item(702)).auto_settled,false);
    assert.equal((await change('unsettle',await item(702))).results[0].ok,true);
    assert.equal((await item(702)).section,'inbox');
  });
  await t.test('snooze wins over completion until expiry, and can be undone',async()=>{
    assert.equal((await change('snooze',await item(702),'2099-01-01T00:00:00Z')).results[0].ok,true);
    await c.query("update next_action set status='done',version=version+1,updated_at=now() where id=$1",[id(702)]);
    assert.equal((await item(702)).section,'snoozed'); assert.equal((await item(702)).auto_settled,false);
    await c.query("update ops.home_inbox_disposition set snoozed_until=now()-interval '1 second' where item_id=$1",[`next_action:${id(702)}`]);
    assert.equal((await item(702)).section,'settled'); assert.equal((await item(702)).auto_settled,true);
    assert.equal((await change('unsettle',await item(702))).results[0].ok,true);
    assert.equal((await item(702)).section,'inbox');
  });
  await t.test('snooze can be undone or settled manually without completing work',async()=>{
    await c.query("update next_action set status='open',version=version+1,updated_at=now() where id=$1",[id(702)]);
    await change('snooze',await item(702),'2099-01-01T00:00:00Z');
    assert.equal((await change('unsettle',await item(702))).results[0].ok,true);
    assert.equal((await item(702)).section,'inbox');
    await change('snooze',await item(702),'2099-01-01T00:00:00Z');
    assert.equal((await change('settle',await item(702))).results[0].ok,true);
    assert.equal((await item(702)).section,'settled'); assert.equal((await item(702)).auto_settled,false);
    assert.equal((await c.query('select status from next_action where id=$1',[id(702)])).rows[0].status,'open');
  });
  await t.test('reopening an automatically settled reply returns it to Inbox',async()=>{
    await c.query("update email_proposal set status='pending',version=version+1 where id=$1",[id(701)]);
    assert.equal((await item(701)).section,'inbox'); assert.equal((await item(701)).auto_settled,false);
  });
  await t.test('unknown completion notifications never prove canonical resolution',async()=>{
    await c.query(`insert into ops.notification values('${id(703)}','${joe.id}','deal','unknown','completion','Synthetic completion',null,now())`);
    const notification=(await read()).items.find(i=>i.source_type==='notification');
    assert.equal(notification.section,'done'); assert.equal(notification.auto_settled,false);
  });
  await t.test('completed call follow-ups settle but processing completion stays Done',async()=>{
    await c.query(`insert into capture_post_call_action(id,owner_id,description,status,updated_at,completed_at,version)
      values($1,$2,'Synthetic completed follow-up','done',now(),now(),4)`,[id(705),joe.id]);
    await c.query("insert into capture_session values($1,$2,'done',now(),now())",[id(706),joe.id]);
    assert.equal((await item(705)).auto_settled,true); assert.equal((await item(705)).section,'settled');
    assert.equal((await item(706)).auto_settled,false); assert.equal((await item(706)).section,'done');
  });
  await t.test('newer canonical failure evidence prevents automatic settlement',async()=>{
    await c.query("update email_proposal set status='sent',result_recorded_at=now()-interval '1 minute',version=version+1 where id=$1",[id(701)]);
    await c.query(`insert into ops.notification values('${id(704)}','${joe.id}','email_proposal','${id(701)}','failure','Synthetic failure',null,now())`);
    const failed=await item(701);
    assert.equal(failed.source_state,'failed'); assert.equal(failed.section,'inbox'); assert.equal(failed.auto_settled,false);
  });
  const transitions=[
    ['open','inbox','resolve','settled'], ['open','inbox','settle','settled'],
    ['open','inbox','snooze','snoozed'], ['open','inbox','start','working'],
    ['done','inbox','reopen','inbox'], ['done','inbox','settle','settled'],
    ['done','inbox','snooze','snoozed'], ['done','inbox','unsettle','inbox'],
    ['open','settled','unsettle','inbox'], ['open','snoozed','expire','inbox'],
    ['open','snoozed','settle','settled'], ['open','snoozed','unsettle','inbox'],
    ['working','inbox','resolve','settled'], ['working','inbox','settle','settled'],
    ['working','inbox','snooze','snoozed'], ['done','snoozed','expire','settled'],
    ['working','snoozed','expire','working'], ['done','override','reopen','inbox'],
    ['open','override','settle','settled'], ['open','override','snooze','snoozed'],
    ['open','settled','start','settled'], ['open','snoozed','start','snoozed'],
    ['open','override','start','inbox'], ['open','settled','resolve','settled'],
    ['open','snoozed','resolve','snoozed'], ['open','override','resolve','inbox'],
    ['working','settled','resolve','settled'], ['working','snoozed','resolve','snoozed'],
    ['working','override','resolve','inbox'], ['done','override','snooze','snoozed'],
    ['working','override','snooze','snoozed'], ['done','override','settle','settled'],
    ['working','override','settle','settled'], ['done','settled','unsettle','inbox'],
    ['working','settled','unsettle','inbox'], ['done','snoozed','unsettle','inbox'],
    ['working','snoozed','unsettle','inbox'], ['done','snoozed','settle','settled'],
    ['working','snoozed','settle','settled'],
  ];
  for(const [index,[source,disposition,event,expected]] of transitions.entries()) {
    await t.test(`statechart ${index+1}: ${source} with ${disposition}, ${event} reaches ${expected}`,async()=>{
      const sourceId=id(1000+index);
      const status={open:'pending',working:'approved_to_send',done:'sent'};
      await c.query(`insert into email_proposal values($1,'reply_draft','joe',$2,1,$3,'Synthetic transition',now(),null,now())`,[sourceId,id(700),status[source]]);
      const current=async()=>(await read()).items.find(i=>i.source_id===sourceId);
      if(disposition!=='inbox') await change(disposition==='override'?'unsettle':disposition==='settled'?'settle':'snooze',await current(),disposition==='snoozed'?'2099-01-01T00:00:00Z':null);
      const before=await current();
      assert.deepEqual(await current(),before);
      if(['start','resolve','reopen'].includes(event)) {
        await c.query('update email_proposal set status=$1,version=version+1,result_recorded_at=now() where id=$2',
          [{start:'approved_to_send',resolve:'sent',reopen:'pending'}[event],sourceId]);
      } else if(event==='expire') {
        await c.query("update ops.home_inbox_disposition set snoozed_until=now()-interval '1 second' where item_id=$1",[`email_proposal:${sourceId}`]);
      } else {
        assert.equal((await change(event,await current(),event==='snooze'?'2099-01-01T00:00:00Z':null)).results[0].ok,true);
      }
      assert.equal((await current()).section,expected);
      await c.query('delete from email_proposal where id=$1',[sourceId]);
      await c.query('delete from ops.home_inbox_disposition where item_id=$1',[`email_proposal:${sourceId}`]);
    });
  }
  await t.test('fact review resolution and exact grant feedback receipt auto-settle through the same commands',async adapters=>{
    await c.query(`insert into deal(id,name,owner) values('${id(2000)}','Synthetic review deal','joe');
      insert into ops.fact_observation_batch(id,organization_tenant_id,idempotency_key,request_sha256,recorded_by_actor_id)
        values('${id(2001)}','carr-internal','${id(2002)}',repeat('a',64),'${joe.id}');
      insert into ops.fact_observation(id,organization_tenant_id,batch_id,ordinal,observation_ref,entity_ref,fact_key,
        value,unit,normalized_value,document_ref,page_start,page_end,cited_text_sha256,cited_text_expires_at,
        method,extractor_version,confidence_tier)
        values('${id(2003)}','carr-internal','${id(2001)}',1,'synthetic','${id(2000)}','base_rent_per_sf',
          '24','USD/SF/year',24,'synthetic',1,1,repeat('a',64),now()+interval '1 day','text_layer','synthetic','human_review');
      insert into ops.tour_share_grant(id,organization_tenant_id,projection_id,grant_version,token_digest,audience,
        permission_scopes,expires_at,status,created_by_actor_id)
        values('${id(2010)}','carr-internal','${id(2011)}',1,'sha256:'||repeat('a',64),'client',
          '["view_packet","comment"]',now()+interval '1 day','active','${joe.id}');`);
    const find=async type=>(await read()).items.find(i=>i.source_type===type);
    for(const type of ['fact_review','tour_feedback']) {
      assert.equal((await find(type))?.section,'inbox',`${type} unresolved stays visible`);
      assert.equal((await find(type)).auto_settled,false);
    }
    await adapters.test('fact review detail names exact field evidence and owner decisions',async()=>{
      const current=await find('fact_review');
      const loaded=await detail(current);
      assert.equal(loaded.detail.fact_key,'base_rent_per_sf');
      assert.deepEqual(loaded.operations,[{verb:'resolve-fact-review',decisions:['approve','correct','reject'],args:{
        entity_ref:id(2000),fact_key:'base_rent_per_sf',base_version:loaded.detail.canonical_version,
        review_version:loaded.detail.review_version}}]);
      assert.deepEqual(await detail(current),loaded);
      await assert.rejects(detail(await find('tour_feedback')),e=>e.payload?.error==='home_review_unsupported');
    });
    await c.query(`insert into ops.tour_share_grant(id,organization_tenant_id,projection_id,grant_version,token_digest,audience,
      permission_scopes,expires_at,status,created_by_actor_id)
      select '${id(2020)}',organization_tenant_id,projection_id,2,'sha256:'||repeat('b',64),audience,
        permission_scopes,expires_at,status,'${dell.id}' from ops.tour_share_grant where id='${id(2010)}';
      insert into ops.tour_share_feedback_event(id,organization_tenant_id,projection_id,share_grant_id,property_id,
        idempotency_key,action,shortlisted,payload_digest)
      values('${id(2021)}','carr-internal','${id(2011)}','${id(2020)}','${id(2013)}','${id(2022)}',
        'shortlist',false,'sha256:'||repeat('b',64));`);
    assert.equal((await find('tour_feedback')).section,'inbox','another grant feedback cannot settle this grant');
    const adapterTransitions=[
      ['open','inbox','resolve','settled'], ['open','inbox','snooze','snoozed'],
      ['open','inbox','settle','settled'], ['open','snoozed','resolve','snoozed'],
      ['open','snoozed','expire','inbox'], ['open','snoozed','settle','settled'],
      ['open','snoozed','unsettle','inbox'], ['open','settled','resolve','settled'],
      ['open','settled','unsettle','inbox'], ['open','override','resolve','inbox'],
      ['open','override','snooze','snoozed'], ['open','override','settle','settled'],
      ['done','inbox','snooze','snoozed'], ['done','inbox','settle','settled'],
      ['done','inbox','unsettle','inbox'], ['done','snoozed','expire','settled'],
      ['done','snoozed','settle','settled'], ['done','snoozed','unsettle','inbox'],
      ['done','settled','unsettle','inbox'], ['done','override','snooze','snoozed'],
      ['done','override','settle','settled'],
    ];
    for(const type of ['fact_review','tour_feedback']) {
      const resolution=type==='fact_review'?'ResolveFactReview':'ReceiveTourFeedback';
      const resolve=async()=>{
        if(type==='fact_review') {
          const field=(await c.query("select ops.fact_review_field('carr-internal',$1,'base_rent_per_sf') result",[id(2000)])).rows[0].result;
          assert.equal((await executeRegisteredTool(c,joe,'resolve-fact-review',{
            idempotency_key:id(seq++),entity_ref:id(2000),fact_key:'base_rent_per_sf',decision:'reject',
            base_version:field.canonical_version,review_version:field.review_version,reason:'Synthetic transition',
          })).ok,true);
        } else {
          await c.query(`insert into ops.tour_share_feedback_event(id,organization_tenant_id,projection_id,share_grant_id,
            property_id,idempotency_key,action,comment,payload_digest)
            values($1,'carr-internal',$2,$3,$4,$5,'comment','Synthetic transition','sha256:'||repeat('a',64))`,
          [id(seq++),id(2011),id(2010),id(2013),id(seq++)]);
        }
      };
      const cases=type==='fact_review'
        ? [...adapterTransitions,['done','inbox','reopen','inbox'],['done','override','reopen','inbox']]
        : adapterTransitions;
      for(const [source,disposition,event,expected] of cases) {
        await adapters.test(`${type} statechart: ${source} with ${disposition}, ${event==='resolve'?resolution:event} reaches ${expected}`,async()=>{
          await c.query('begin');
          try {
            if(source==='done') await resolve();
            if(disposition!=='inbox') assert.equal((await change(
              disposition==='override'?'unsettle':disposition==='settled'?'settle':'snooze',
              await find(type),disposition==='snoozed'?'2099-01-01T00:00:00Z':null)).results[0].ok,true);
            const before=await find(type);
            assert.equal(before.source_state,source==='done'?'done':'needs_attention');
            assert.equal(before.section,disposition==='override'?'inbox':disposition==='inbox'&&source==='done'?'settled':disposition);
            assert.deepEqual(await find(type),before);
            if(event==='resolve') await resolve();
            else if(event==='reopen') {
              await c.query(`insert into ops.fact_observation select $1::uuid,organization_tenant_id,batch_id,2,
                'new-evidence',entity_ref,fact_key,value,unit,expense_basis,normalized_value,document_ref,content_hash,
                document_family_id,derivation_chain,page_start,page_end,cited_text,cited_text_sha256,cited_text_expires_at,
                method,extractor_version,extraction_pass,extraction_model,confidence_tier,now(),normalized_schedule,region_id
                from ops.fact_observation where id=$2`,[id(seq++),id(2003)]);
            } else if(event==='expire') {
              await c.query("update ops.home_inbox_disposition set snoozed_until=now()-interval '1 second' where item_id=$1",[before.item_id]);
            } else assert.equal((await change(event,await find(type),event==='snooze'?'2099-01-01T00:00:00Z':null)).results[0].ok,true);
            const after=await find(type);
            assert.equal(after.section,expected);
            assert.equal(after.explicit_unsettle,event==='unsettle'||(disposition==='override'&&event==='resolve'));
            assert.equal(after.auto_settled,expected==='settled'&&event!=='settle'&&disposition!=='settled');
            assert.deepEqual(await find(type),after);
          } finally { await c.query('rollback'); }
        });
      }
    }
    const dispositionsBefore=(await c.query('select count(*) n from ops.home_inbox_disposition')).rows[0].n;
    const field=(await c.query("select ops.fact_review_field('carr-internal',$1,'base_rent_per_sf') result",[id(2000)])).rows[0].result;
    assert.equal((await executeRegisteredTool(c,joe,'resolve-fact-review',{
      idempotency_key:id(2004),entity_ref:id(2000),fact_key:'base_rent_per_sf',decision:'reject',
      base_version:field.canonical_version,review_version:field.review_version,reason:'Synthetic review resolution',
    })).ok,true);
    await c.query(`
      insert into ops.tour_share_feedback_event(id,organization_tenant_id,projection_id,share_grant_id,property_id,
        idempotency_key,action,comment,payload_digest)
      values('${id(2012)}','carr-internal','${id(2011)}','${id(2010)}','${id(2013)}','${id(2014)}',
        'comment','Synthetic feedback','sha256:'||repeat('a',64));`);
    assert.equal((await c.query('select count(*) n from ops.home_inbox_disposition')).rows[0].n,dispositionsBefore);
    for(const type of ['fact_review','tour_feedback']) await adapters.test(`${type} modeled resolution, Undo, replay and snooze`,async()=>{
      const settled=await find(type);
      assert.equal(settled.section,'settled',`${type} modeled resolution transition`);
      assert.equal(settled.auto_settled,true);
      const args={idempotency_key:id(seq++),items:[{item_id:settled.item_id,base_version:settled.base_version}]};
      const undo=await executeRegisteredTool(c,joe,'unsettle-home-inbox',args);
      assert.equal(undo.results[0].ok,true);
      const replay=await executeRegisteredTool(c,joe,'unsettle-home-inbox',args);
      assert.equal(replay.replayed,true); assert.deepEqual(replay.results,undo.results);
      const restored=await find(type);
      assert.equal(restored.section,'inbox'); assert.equal(restored.explicit_unsettle,true);
      assert.equal(restored.auto_settled,false); assert.deepEqual(await find(type),restored);
      assert.equal((await change('snooze',restored,'2099-01-01T00:00:00Z')).results[0].ok,true);
      assert.equal((await find(type)).section,'snoozed');
      await c.query("update ops.home_inbox_disposition set snoozed_until=now()-interval '1 second' where item_id=$1",[restored.item_id]);
      assert.equal((await find(type)).auto_settled,true);
      assert.equal((await change('settle',await find(type))).results[0].ok,true);
      assert.equal((await find(type)).auto_settled,false);
      assert.equal((await change('unsettle',await find(type))).results[0].ok,true);
    });
    const before=await find('fact_review');
    await c.query(`insert into ops.fact_observation select '${id(2005)}'::uuid,organization_tenant_id,batch_id,2,
      'new-evidence',entity_ref,fact_key,value,unit,expense_basis,normalized_value,document_ref,content_hash,
      document_family_id,derivation_chain,page_start,page_end,cited_text,cited_text_sha256,cited_text_expires_at,
      method,extractor_version,extraction_pass,extraction_model,confidence_tier,now(),normalized_schedule,region_id
      from ops.fact_observation where id='${id(2003)}'`);
    const reopened=await find('fact_review');
    assert.equal(reopened.source_state,'needs_attention'); assert.equal(reopened.section,'inbox');
    assert.equal(reopened.explicit_unsettle,false);
    assert.equal((await change('settle',before)).results[0].error,'version_conflict');
    const pending=(await c.query("select ops.fact_review_field('carr-internal',$1,'base_rent_per_sf') result",[id(2000)])).rows[0].result;
    assert.equal((await executeRegisteredTool(c,joe,'resolve-fact-review',{
      idempotency_key:id(2006),entity_ref:id(2000),fact_key:'base_rent_per_sf',decision:'approve',
      observation_id:id(2005),base_version:pending.canonical_version,review_version:pending.review_version,
      reason:'Synthetic approval of new evidence',
    })).ok,true);
    assert.equal((await find('fact_review')).auto_settled,true,'approved new evidence settles the reopened review');

    const v2=await executeRegisteredTool(c,joe,'read-home-inbox-v2',{});
    assert.ok(v2.items.every(i=>!['fact_review','tour_feedback'].includes(i.source_type)));
    await c.query("select set_config('carr.acting_actor_slug','dell',false),set_config('carr.verified_human_actor_slug','dell',false)");
    assert.ok((await executeRegisteredTool(c,dell,'read-home-inbox-v3',{})).items.every(i=>i.source_type!=='fact_review' && i.source_id!==id(2010)));
    await c.query("select set_config('carr.acting_actor_slug','joe',false),set_config('carr.verified_human_actor_slug','joe',false)");
  });
  await t.test('v3 contract matches its runtime declaration',()=>{
    assert.deepEqual(JSON.parse(readFileSync(new URL('../contracts/home-inbox.v3.json',import.meta.url),'utf8')),HOME_INBOX_SETTLEMENT_CONTRACT);
  });
  await t.test('v2 remains compatible and v3 stays actor scoped',async()=>{
    const v2=await executeRegisteredTool(c,joe,'read-home-inbox-v2',{});
    assert.equal(v2.auto_settle_enabled,false); assert.equal(v2.schema_version,'home-inbox.v2');
    assert.ok(v2.items.every(i=>!Object.hasOwn(i,'auto_settled')));
    const v3=await read(); assert.equal(v3.auto_settle_enabled,true); assert.equal(v3.schema_version,'home-inbox.v3');
    await c.query("select set_config('carr.acting_actor_slug','dell',false),set_config('carr.verified_human_actor_slug','dell',false)");
    assert.ok((await executeRegisteredTool(c,dell,'read-home-inbox-v3',{})).items.every(i=>i.source_id===id(2020)));
  });
});
