// Verify actor-scoped inbox reads and disposition commands on PostgreSQL.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { postgresDatabase } from './helpers/postgres-database.mjs';
import { executeRegisteredTool } from '../src/tools.js';

const id = n => `ae000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const joe = { id: id(1), slug: 'joe', human: true };
const dell = { id: id(2), slug: 'dell', human: true };


test('Home inbox is an actor-scoped bounded snapshot over canonical records', async t => {
  const db = await postgresDatabase('home-inbox-');
  const c = db.client;
  t.after(() => db.close());
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
  `);
  const schema=readFileSync(new URL('../../db/schema.sql',import.meta.url),'utf8');
  await c.query(schema.match(/CREATE FUNCTION ops\.portfolio_writer_actor_id\(\)[\s\S]*?\n\$\$;/)[0]);
  await c.query(schema.match(/CREATE TABLE public\.tool_call \([\s\S]*?\n\);/)[0]);
  await c.query('alter table tool_call add primary key(idempotency_key)');
  await c.query('grant usage on schema ops to carr_writer,carr_reader');
  await c.query('grant select,insert on tool_call to carr_writer');
  await c.query('insert into actor(id,slug) values($1,\'joe\'),($2,\'dell\')', [joe.id,dell.id]);
  await c.query("select set_config('carr.acting_actor_slug','joe',false),set_config('carr.verified_human_actor_slug','joe',false)");
  await c.query(readFileSync(new URL('../../migrations/1110_home_inbox.sql', import.meta.url),'utf8'));
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
