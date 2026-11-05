import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { snapshotDatabase } from './helpers/postgres-database.mjs';
import { executeRegisteredTool, TOOLS } from '../src/tools.js';

const dsn = await snapshotDatabase('subject-watches-');
test('registered watch transitions preserve isolation and initial baseline', async t => {
  const c = new pg.Client({ connectionString: dsn });
  await c.connect(); t.after(() => c.end());
  assert.equal((await c.query('select ops.scac_mutation_catalog_v118_current() as valid')).rows[0].valid,true);
  const joe = { ...(await c.query("select id,slug from actor where slug='joe'")).rows[0], human: true };
  const dell = { ...(await c.query("select id,slug from actor where slug='dell'")).rows[0], human: true };
  const party = (await c.query("insert into party(kind,name,created_by,updated_by) values('person','Synthetic watch client',$1,$1) returning id",[joe.id])).rows[0].id;
  await c.query("insert into client_status(slug,label) values('active','Synthetic active') on conflict do nothing");
  const client = (await c.query("insert into client(party_id,status,created_by,updated_by) values($1,'active',$2,$2) returning id",[party,joe.id])).rows[0].id;
  const deal = (await c.query("insert into deal(client_id,name,deal_type,phase,owner,created_by,updated_by) values($1,'Synthetic watch deal','purchase','research','joe',$2,$2) returning id",[client,joe.id])).rows[0].id;
  const lease = (await c.query("insert into lease(deal_id,client_id,created_by,updated_by) values($1,$2,$3,$3) returning id",[deal,client,joe.id])).rows[0].id;
  const call = async (verb,args={},who=joe,connection=c) => {
    await connection.query('begin');
    try {
      await connection.query("select set_config('carr.acting_actor_slug',$1,true),set_config('carr.verified_human_actor_slug',$1,true),set_config('carr.organization_tenant_id','carr-internal',true)",[who.slug]);
      await connection.query('set local role carr_writer');
      const result = await executeRegisteredTool(connection,who,verb,args);
      await connection.query('commit'); return result;
    } catch(e) { await connection.query('rollback'); throw e; }
  };
  const target = {subject_type:'deal',subject_id:deal};
  const write = (verb,args=target,who=joe,key=randomUUID()) => call(verb,{...args,idempotency_key:key},who);
  const read = (args=target,who=joe) => call('read-subject-watch-v1',args,who);
  const list = (args={},who=joe) => call('list-subject-watches-v1',args,who);
  const refuses = (promise,error) => assert.rejects(promise,e=>e.payload?.error===error);
  assert.ok(TOOLS['watch-subject-v1']);
  assert.equal((await read()).watch,null);
  const notifications = (await c.query('select count(*) from ops.notification')).rows[0].count;
  const key=randomUUID();
  const first = await write('watch-subject-v1',target,joe,key);
  assert.equal(first.schema_version,'subject-watch.v1');
  assert.equal(first.watch.baseline.version,1);
  assert.equal(first.watch.actor_id,joe.id);
  assert.equal((await write('watch-subject-v1',target,joe,key)).replayed,true);
  const concurrent = new pg.Client({connectionString:dsn});
  await concurrent.connect();
  try {
    const args={...target,idempotency_key:randomUUID()};
    const responses=await Promise.all([call('watch-subject-v1',args),call('watch-subject-v1',args,joe,concurrent)]);
    assert.equal(responses.filter(r=>r.replayed).length,1);
    assert.deepEqual(responses[0].watch,responses[1].watch);
  } finally { await concurrent.end(); }
  await refuses(write('watch-subject-v1',{subject_type:'lease',subject_id:lease},joe,key),'key_reuse');
  await refuses(write('watch-subject-v1',target,dell,key),'key_reuse');
  assert.equal((await read(target,dell)).watch,null);
  await write('watch-subject-v1',target,dell);
  await c.query("update deal set owner='dell',version=version+1 where id=$1",[deal]);
  assert.deepEqual((await read()).watch,first.watch);
  assert.deepEqual((await write('watch-subject-v1')).watch,first.watch);
  const leaseTarget={subject_type:'lease',subject_id:lease};
  await write('watch-subject-v1',leaseTarget);
  assert.equal((await list()).watches.length,2);
  const page=await list({limit:1});
  assert.equal(page.watches.length,1); assert.equal(page.has_more,true);
  const next=await list({limit:1,cursor:page.next_cursor});
  assert.equal(next.watches.length,1); assert.notEqual(next.watches[0].subject_id,page.watches[0].subject_id);
  assert.equal(next.has_more,false);
  const unwatchKey=randomUUID();
  await write('unwatch-subject-v1',target,joe,unwatchKey);
  assert.equal((await write('unwatch-subject-v1',target,joe,unwatchKey)).replayed,true);
  assert.equal((await read()).watch,null);
  assert.ok((await read(target,dell)).watch);
  assert.equal((await write('unwatch-subject-v1')).rows_changed,0);
  assert.equal((await write('watch-subject-v1')).watch.baseline.version,2);
  await c.query('update party set deleted_at=now() where id=$1',[party]);
  await refuses(read(),'watch_target_not_found');
  await refuses(write('watch-subject-v1'),'watch_target_not_found');
  await refuses(write('watch-subject-v1',target,joe,key),'watch_target_not_found');
  assert.deepEqual((await list()).watches,[]);
  await write('unwatch-subject-v1');
  await c.query('update party set deleted_at=null where id=$1',[party]);
  assert.equal((await read()).watch,null);
  assert.ok((await read(leaseTarget)).watch);
  await c.query('delete from lease where id=$1',[lease]);
  await refuses(read(leaseTarget),'watch_target_not_found');
  await write('unwatch-subject-v1',leaseTarget);
  for (const subject_type of ['deal','lease','listing'])
    await refuses(write('watch-subject-v1',{subject_type,subject_id:randomUUID()}),'watch_target_not_found');
  await assert.rejects(list({limit:101}));
  await assert.rejects(read({...target,actor_id:dell.id}));
  await c.query('begin');
  try {
    await c.query('set local role carr_reader');
    await assert.rejects(c.query('select * from ops.subject_watch'),e=>e.code==='42501');
  } finally { await c.query('rollback'); }
  await c.query('begin');
  try {
    await c.query("select set_config('carr.acting_actor_slug','joe',true),set_config('carr.verified_human_actor_slug','joe',true),set_config('carr.organization_tenant_id','other-tenant',true)");
    await c.query('set local role carr_writer');
    await assert.rejects(executeRegisteredTool(c,joe,'list-subject-watches-v1',{}));
  } finally { await c.query('rollback'); }
  assert.equal((await c.query('select count(*) from ops.notification')).rows[0].count,notifications);
});

test('watch statechart on canonical event ingress', async t => {
  const c=new pg.Client({connectionString:dsn});await c.connect();t.after(()=>c.end());
  const joe=(await c.query("select id from actor where slug='joe'")).rows[0].id;
  const dell=(await c.query("select id from actor where slug='dell'")).rows[0].id;
  const party=(await c.query("insert into party(kind,name,created_by,updated_by) values('person','Synthetic event client',$1,$1) returning id",[joe])).rows[0].id;
  await c.query("insert into client_status(slug,label) values('active','Active') on conflict do nothing");
  const client=(await c.query("insert into client(party_id,status,created_by,updated_by) values($1,'active',$2,$2) returning id",[party,joe])).rows[0].id;
  const deal=(await c.query("insert into deal(client_id,name,deal_type,phase,owner,created_by,updated_by) values($1,'Synthetic event deal','purchase','research','joe',$2,$2) returning id",[client,joe])).rows[0].id;
  const watch=async(kind,id,slug='joe',on=true)=>{
    await c.query('begin');
    try {
      await c.query("select set_config('carr.acting_actor_slug',$1,true),set_config('carr.verified_human_actor_slug',$1,true),set_config('carr.organization_tenant_id','carr-internal',true)",[slug]);
      const r=(await c.query('select ops.set_subject_watch($1,$2,$3) as result',[kind,id,on])).rows[0].result;
      await c.query('commit');return r;
    }catch(e){await c.query('rollback');throw e;}
  };
  const event=async(verb='update-deal',value={},subject=deal)=> (await c.query("insert into event(occurred_at,actor_id,verb,subject_type,subject_id,new_value,cause) values(clock_timestamp(),$1,$2,'deal',$3,$4,'automation_job') returning id",[joe,verb,subject,value])).rows[0].id;
  const count=async(who=joe,kind='deal')=>Number((await c.query("select count(*) from ops.notification where recipient_actor=$1 and subject_type=$2 and dedupe_key like 'subject-watch:%'",[who,kind])).rows[0].count);
  await watch('deal',deal);await event();assert.equal(await count(),0,'quiet baseline');
  await c.query('update deal set version=version+1 where id=$1',[deal]);
  await c.query("insert into event(occurred_at,actor_id,verb,subject_type,subject_id,cause,organization_tenant_id) values(now(),$1,'update-deal','deal',$2,'automation_job','other-tenant')",[joe,deal]);
  assert.equal(await count(),0,'cross-tenant events cannot wake partners');
  const e=await event();assert.equal(await count(),1);
  await c.query('insert into ops.notification_read(notification_id,recipient_actor) select id,$1 from ops.notification where recipient_actor=$1 and dedupe_key like \'subject-watch:%\' on conflict do nothing',[joe]);
  await event();assert.equal(await count(),1,'duplicate source version');
  for(const link of ['https://example.test','//example.test','/deals?deal=bad','/clients?record=bad','/deals?deal='+deal+'&redirect=evil']){
    await c.query('begin');
    try{await assert.rejects(c.query("select ops.mint_notification('event',$1,'deal',$2,'Synthetic rejection','action_required',$3,$3,'joe',false,false)",[e,deal,link]),x=>x.code==='23514');}
    finally{await c.query('rollback');}
  }
  await c.query('select ops.consume_subject_watch_event($1)',[e]);assert.equal(await count(),1,'lost commit acknowledgement');
  assert.equal(await count(dell),0);
  await watch('deal',deal,'dell');
  await c.query("update deal set owner='dell',version=version+1 where id=$1",[deal]);
  await event();assert.equal(await count(),2);assert.equal(await count(dell),1);
  const activity=(await c.query("insert into activity(kind,occurred_at,deal_id,actor_id,summary,email_source) values('email_in',now(),$1,$2,'Synthetic inbound','{\"mailbox_owner\":\"joe\",\"sender_email\":\"synthetic@example.test\"}') returning id",[deal,joe])).rows[0].id;
  await event('confirm-email-filing',{activity_id:activity});assert.equal(await count(),3);assert.equal(await count(dell),1,'cross-mailbox reply');
  await event('inherit-email-thread',{activity_id:activity});assert.equal(await count(),3,'reply identity deduplicates');
  await c.query('update party set deleted_at=now() where id=$1',[party]);
  await c.query('update deal set version=version+1 where id=$1',[deal]);await event();assert.equal(await count(),3,'deleted source');
  await c.query('update party set deleted_at=null where id=$1',[party]);
  await watch('deal',deal,'joe',false);await event();assert.equal(await count(),3,'unsubscribe');
  await watch('deal',deal);await event();assert.equal(await count(),3,'rewatch starts from a quiet fresh version');
  const unwatched=(await c.query("insert into deal(client_id,name,deal_type,phase,owner,version,created_by,updated_by) values($1,'Synthetic unwatched deal','purchase','research','joe',2,$2,$2) returning id",[client,joe])).rows[0].id;
  await event('update-deal',{},unwatched);assert.equal(await count(),3,'unwatched deal stays quiet');
  const links=(await c.query("select deep_link from ops.notification where subject_type='deal' and dedupe_key like 'subject-watch:%'")).rows;
  assert.ok(links.every(r=>r.deep_link===`/deals?deal=${deal}`));
  await c.query('begin');
  try{await c.query('set local role carr_writer');await assert.rejects(c.query('select ops.consume_subject_watch_event($1)',[e]),x=>x.code==='42501');}
  finally{await c.query('rollback');}

  const leases=[];
  for(let n=0;n<5;n++){
    const l=(await c.query("insert into lease(client_id,created_by,updated_by,expiration_on) values($1,$2,$2,current_date+interval '30 months') returning id",[client,joe])).rows[0].id;
    leases.push(l);await watch('lease',l);
  }
  await c.query("insert into ops.notification_preference(actor,device_opt_in,quiet_hours_start,quiet_hours_end,timezone) values($1,true,'00:00','23:59:59.999999','UTC') on conflict(actor) do update set device_opt_in=true,quiet_hours_start='00:00',quiet_hours_end='23:59:59.999999',timezone='UTC'",[joe]);
  await c.query("update lease set expiration_on=current_date+interval '12 months' where id=any($1::uuid[])",[leases]);
  for(let n=0;n<3;n++)await c.query('select ops.scan_subject_watch_leases($1,2)',[e]);
  assert.equal(await count(joe,'lease'),5,'bounded batches skip no lease');
  const rows=(await c.query("select id,deep_link from ops.notification where subject_type='lease' and dedupe_key like 'subject-watch:%'")).rows;
  assert.ok(rows.every(r=>r.deep_link===`/clients?record=${client}`));
  assert.equal(Number((await c.query("select count(*) from ops.notification_delivery where notification_id=any($1::uuid[]) and channel='device' and state='suppressed_quiet_hours'",[rows.map(r=>r.id)])).rows[0].count),5);
  await c.query("update ops.notification_delivery set state='delivered',settled_at=now() where notification_id=any($1::uuid[]) and channel='in_app'",[rows.map(r=>r.id)]);
  await event();await event();assert.equal(await count(joe,'lease'),5,'delivery acknowledgement cannot replay');
  await watch('lease',leases[0],'joe',false);
  await c.query("update lease set expiration_on=current_date+interval '6 months' where id=any($1::uuid[])",[leases]);
  await event();assert.equal(await count(joe,'lease'),9,'unsubscribed lease stays quiet');
  await c.query('delete from lease where id=$1',[leases[1]]);await event();assert.equal(await count(joe,'lease'),9,'deleted lease');
  await c.query('begin');
  try{
    await c.query("create function ops.watch_test_fail() returns trigger language plpgsql as $$ begin raise exception 'source unavailable'; end $$");
    await c.query('create trigger watch_test_fail before insert on ops.notification for each row execute function ops.watch_test_fail()');
    await c.query("update lease set expiration_on=current_date+interval '3 months' where id=$1",[leases[2]]);
    await assert.rejects(event(),/source unavailable/);
  }finally{await c.query('rollback');}
  assert.equal(await count(joe,'lease'),9,'source failure rolls back cursor and notification');
  const cursorBefore=(await c.query('select watch_id from ops.subject_watch_scan_cursor')).rows;
  await c.query('begin');
  try{
    await c.query("create or replace function ops.lease_radar_window() returns table(starts_on date,ends_on date) language plpgsql stable set search_path=pg_catalog,pg_temp as $$ begin raise exception 'lease source unavailable'; end $$");
    await assert.rejects(event(),/lease source unavailable/);
  }finally{await c.query('rollback');}
  assert.deepEqual((await c.query('select watch_id from ops.subject_watch_scan_cursor')).rows,cursorBefore);
  assert.equal(await count(joe,'lease'),9);
  const inside=(await c.query("insert into lease(client_id,created_by,updated_by,expiration_on) values($1,$2,$2,current_date+interval '12 months') returning id",[client,joe])).rows[0].id;
  await watch('lease',inside);await event();assert.equal(await count(joe,'lease'),9,'in-window subscription takes a quiet baseline');
  await c.query("update lease set expiration_on=current_date+interval '30 months' where id=$1",[inside]);await event();
  await c.query('begin');
  try{
    await c.query("create or replace function ops.lease_radar_window() returns table(starts_on date,ends_on date) language sql stable set search_path=pg_catalog,pg_temp as $$ select current_date,(current_date+interval '36 months')::date $$");
    const window=(await c.query('select * from ops.lease_radar_window()')).rows[0];
    assert.deepEqual((await c.query('select starts_on,ends_on from public.v_client_lease_radar')).rows[0],window,'radar and producer share policy');
    await event();assert.equal(await count(joe,'lease'),10,'clock threshold needs no lease write');
    await event();assert.equal(await count(joe,'lease'),10);
  }finally{await c.query('rollback');}
});
