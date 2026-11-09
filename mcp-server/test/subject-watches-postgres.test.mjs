import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import pg from 'pg';
import { snapshotDatabase } from './helpers/postgres-database.mjs';
import { executeRegisteredTool, TOOLS } from '../src/tools.js';

const dsn = await snapshotDatabase('subject-watches-');
test('watch Home projection preserves email lifecycle visibility', async t => {
  const c=new pg.Client({connectionString:dsn}); await c.connect(); t.after(()=>c.end());
  const joe={...(await c.query("select id,slug from actor where slug='joe'")).rows[0],human:true};
  const read=async version=>{
    await c.query("select set_config('carr.acting_actor_slug','joe',false),set_config('carr.verified_human_actor_slug','joe',false)");
    return executeRegisteredTool(c,joe,`read-home-inbox-v${version}`,{});
  };
  for(const state of ['deleted','recalled','superseded']) await t.test(state,async()=>{
    await c.query('begin');
    try {
      const mail=(await c.query("insert into activity(kind,occurred_at,actor_id,summary,email_source) values('email_in',now(),$1,'Synthetic lifecycle source','{\"mailbox_owner\":\"joe\",\"sender_email\":\"synthetic@example.test\"}') returning id",[joe.id])).rows[0].id;
      const notice=(await c.query(`insert into ops.notification(subject_type,subject_ref,event_ref,event_source,recipient_actor,reason,severity,deep_link,dedupe_key)
        values('email',$1,$2,'event',$3,'Synthetic lifecycle notice','action_required','/',$4) returning id`,[mail,randomUUID(),joe.id,randomUUID()])).rows[0].id;
      const draft=(await c.query(`insert into email_proposal(kind,activity_id,owner,draft_subject,draft_body,status,evidence_quote,confidence,created_by)
        values('reply_draft',$1,'joe','Synthetic lifecycle draft','Synthetic body','pending','Synthetic quote',1,$2) returning id`,[mail,joe.id])).rows[0].id;
      for(const version of [2,3,4,5]) {
        const items=(await read(version)).items;
        assert.ok(items.some(i=>i.source_id===draft),`v${version} includes live draft`);
        assert.ok(items.some(i=>i.merged_source_ids.includes(notice)),`v${version} includes live email notification`);
      }
      await c.query("update activity set email_context=jsonb_build_object('lifecycle',jsonb_build_object('state',$2::text)) where id=$1",[mail,state]);
      for(const version of [2,3,4,5]) {
        const items=(await read(version)).items;
        assert.ok(!items.some(i=>i.source_id===draft),`v${version} excludes ${state} draft`);
        assert.ok(!items.some(i=>i.merged_source_ids.includes(notice)),`v${version} excludes ${state} email notification`);
      }
    } finally { await c.query('rollback'); }
  });
});

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
  const event=async(verb='update-deal',value={},subject=deal,scope='none')=> (await c.query("insert into event(occurred_at,actor_id,verb,subject_type,subject_id,new_value,cause,personal_scope) values(clock_timestamp(),$1,$2,'deal',$3,$4,'automation_job',$5) returning id",[joe,verb,subject,value,scope])).rows[0].id;
  const count=async(who=joe,kind='deal')=>Number((await c.query("select count(*) from ops.notification where recipient_actor=$1 and subject_type=$2 and dedupe_key like 'subject-watch:%'",[who,kind])).rows[0].count);
  await watch('deal',deal);await event();assert.equal(await count(),0,'quiet baseline');
  await c.query('update deal set version=version+1 where id=$1',[deal]);
  await c.query("insert into event(occurred_at,actor_id,verb,subject_type,subject_id,cause,organization_tenant_id,personal_scope) values(now(),$1,'update-deal','deal',$2,'automation_job','other-tenant','joe-personal')",[joe,deal]);
  assert.equal(await count(),0,'cross-tenant events cannot wake partners');
  await c.query('begin');
  try {
    const original=readFileSync(new URL('../../migrations/1156_subject_watch_events.sql',import.meta.url),'utf8');
    const consumer=original.slice(original.indexOf('create function ops.consume_subject_watch_event'),original.indexOf('create function ops.subject_watch_on_event'));
    const schema=readFileSync(new URL('../../db/schema.sql',import.meta.url),'utf8');
    const minterStart=schema.indexOf('CREATE FUNCTION ops.mint_notification(');
    const originalMinter=schema.slice(minterStart,schema.indexOf('end $$;',minterStart)+8);
    await c.query(originalMinter.replace('CREATE FUNCTION','CREATE OR REPLACE FUNCTION'));
    await c.query(consumer.replace('create function','create or replace function'));
    await event();assert.equal(await count(),1,'original consumer reproduced a version-only notification');
  } finally {await c.query('rollback');}
  const e=await event();assert.equal(await count(),0);
  const legacy=(await c.query("select ops.mint_notification('event',$1,'deal',$2,'Legacy direct Watch','action_required','/deals?deal='||$2,'subject-watch:legacy-direct','joe',false,false) result",[e,deal])).rows[0].result;
  assert.equal(legacy.minted,false,'legacy direct delivery cannot bypass scheduled receipt emission');
  assert.equal(legacy.reason_id,'watch_scheduled_delivery_required');

  await c.query('insert into ops.notification_read(notification_id,recipient_actor) select id,$1 from ops.notification where recipient_actor=$1 and dedupe_key like \'subject-watch:%\' on conflict do nothing',[joe]);
  await event();assert.equal(await count(),0,'duplicate source version');
  for(const link of ['https://example.test','//example.test','/deals?deal=bad','/clients?record=bad','/deals?deal='+deal+'&redirect=evil']){
    await c.query('begin');
    try{await assert.rejects(c.query("select ops.mint_notification('event',$1,'deal',$2,'Synthetic rejection','action_required',$3,$3,'joe',false,false)",[e,deal,link]),x=>x.code==='23514');}
    finally{await c.query('rollback');}
  }
  await c.query('select ops.consume_subject_watch_event($1)',[e]);assert.equal(await count(),0,'lost commit acknowledgement');
  assert.equal(await count(dell),0);
  await watch('deal',deal,'dell');
  await c.query('begin');
  try {
    const version=(await c.query('select version from deal where id=$1',[deal])).rows[0].version;
    await c.query("select set_config('carr.acting_actor_slug','joe',true),set_config('carr.verified_human_actor_slug','joe',true)");
    await c.query(TOOLS['set-lead'].authorityOnly?'set local session authorization carr_authority_joe':'set local role carr_writer');
    await executeRegisteredTool(c,{id:joe,slug:'joe',human:true},'set-lead',{deal,new_lead:'dell',base_version:version,idempotency_key:randomUUID()});
    await c.query('commit');
  } catch(error) {await c.query('rollback');throw error;}
  await event();assert.equal(await count(),0);assert.equal(await count(dell),0);
  assert.equal(Number((await c.query("select count(*) from ops.subject_watch_change where subject_id=$1 and category='material_changed'",[deal])).rows[0].count),2,'material owner change captures both partners');
  const activity=(await c.query("insert into activity(kind,occurred_at,deal_id,actor_id,summary,email_source) values('email_in',now(),$1,$2,'Synthetic inbound','{\"mailbox_owner\":\"joe\",\"sender_email\":\"synthetic@example.test\"}') returning id",[deal,joe])).rows[0].id;
  await event('confirm-email-filing',{activity_id:activity},deal,'joe-personal');
  await event('inherit-email-thread',{activity_id:activity},deal,'joe-personal');
  assert.equal(await count(),0,'capture does not emit');
  await c.query('select ops.sweep_subject_watches(clock_timestamp(),50)');
  assert.equal(await count(),1,'material and reply coalesce');
  assert.equal(await count(dell),1,'shared material change reaches Dell');
  assert.equal(Number((await c.query("select count(*) from ops.subject_watch_change where subject_id=$1 and category='reply_arrived'",[deal])).rows[0].count),1,'mailbox reply identity remains private and deduplicated');
  await watch('deal',deal,'joe',false);
  await event(); await c.query("select ops.sweep_subject_watches(clock_timestamp()+interval '5 minutes',50)");
  assert.equal(await count(),1,'unwatch fences emission');

});


test('registered partner and sponsored writes wake shared deal watchers', async t => {
  const c = new pg.Client({ connectionString: dsn });
  await c.connect(); t.after(() => c.end());
  const actors = (await c.query("select id,slug from actor where slug in ('joe','dell','codex')")).rows;
  const joe = { ...actors.find(a=>a.slug==='joe'), human:true };
  const dell = { ...actors.find(a=>a.slug==='dell'), human:true };
  const agent = { ...actors.find(a=>a.slug==='codex'), human:false, native_agent_verified:true, sponsoring_human_slug:'joe' };
  const call = async (who,verb,args) => {
    await c.query('begin');
    try {
      await c.query("select set_config('carr.acting_actor_slug',$1,true),set_config('carr.verified_human_actor_slug',$2,true),set_config('carr.organization_tenant_id','carr-internal',true)",[who.slug,who.human?who.slug:'']);
      await c.query('set local role carr_writer');
      const result=await executeRegisteredTool(c,who,verb,args);
      await c.query('commit');return result;
    } catch(e) { await c.query('rollback');throw e; }
  };
  for (const who of [joe,dell,agent]) await t.test(who.slug,async ()=>{
    const party=(await c.query("insert into party(kind,name,created_by,updated_by) values('person','Synthetic writer regression',$1,$1) returning id",[joe.id])).rows[0].id;
    const client=(await c.query("insert into client(party_id,status,created_by,updated_by) values($1,'active',$2,$2) returning id",[party,joe.id])).rows[0].id;
    const deal=(await c.query("insert into deal(client_id,name,deal_type,phase,owner,created_by,updated_by) values($1,'Synthetic registered writer deal','purchase','research','joe',$2,$2) returning id",[client,joe.id])).rows[0].id;
    for(const partner of [joe,dell]) await call(partner,'watch-subject-v1',{subject_type:'deal',subject_id:deal,idempotency_key:randomUUID()});
    const control=(await c.query("insert into deal(client_id,name,deal_type,phase,owner,created_by,updated_by) values($1,'Synthetic proof control','purchase','research','joe',$2,$2) returning id",[client,joe.id])).rows[0].id;
    const args={deal,base_version:1,fields:{closed_on:'2027-01-01'},idempotency_key:randomUUID()};
    await call(who,'update-deal',args);
    const counts=async()=> (await c.query("select actor_id,count(*)::integer n from ops.subject_watch_change where subject_id=$1 group by actor_id order by actor_id",[deal])).rows;
    const expected=[joe,dell].map(a=>({actor_id:a.id,n:1})).sort((a,b)=>a.actor_id.localeCompare(b.actor_id));
    assert.deepEqual(await counts(),expected,'shared material changes capture both partners despite personal provenance');
    await call(who,'update-deal',args);
    assert.deepEqual(await counts(),expected,'registered writer replay captures zero');
    await call(who,'update-deal',{...args,base_version:2,fields:{city:'Synthetic changed city'},idempotency_key:randomUUID()});
    assert.deepEqual(await counts(),expected,'immaterial write captures zero');
    await call(who,'update-deal',{...args,deal:control,idempotency_key:randomUUID()});
    assert.equal((await c.query('select count(*)::integer n from ops.subject_watch_change where subject_id=$1',[control])).rows[0].n,0);
    await call(joe,'unwatch-subject-v1',{subject_type:'deal',subject_id:deal,idempotency_key:randomUUID()});
    await call(who,'update-deal',{...args,base_version:3,fields:{closed_on:null},idempotency_key:randomUUID()});
    const after=await counts();
    assert.equal(after.find(a=>a.actor_id===joe.id).n,1,'unwatch fences capture');
    assert.equal(after.find(a=>a.actor_id===dell.id).n,2);
  });
});

test('watch alerts reach named Home cards and read, settle, snooze stay independent', async t => {
  const c=new pg.Client({connectionString:dsn}); await c.connect(); t.after(()=>c.end());
  const joe={...(await c.query("select id,slug from actor where slug='joe'")).rows[0],human:true};
  let isolated=false;
  const call=async(verb,args={})=>{
    if(!isolated) await c.query('begin');
    try {
      await c.query("select set_config('carr.acting_actor_slug','joe',true),set_config('carr.verified_human_actor_slug','joe',true),set_config('carr.organization_tenant_id','carr-internal',true)");
      await c.query('set local role carr_writer');
      const result=await executeRegisteredTool(c,joe,verb,args); if(!isolated) await c.query('commit'); return result;
    } catch(e) { if(!isolated) await c.query('rollback'); throw e; }
  };
  await c.query("insert into client_status(slug,label) values('active','Active') on conflict do nothing");
  const party=(await c.query("insert into party(kind,name,created_by,updated_by) values('person','Synthetic inbox client',$1,$1) returning id",[joe.id])).rows[0].id;
  const client=(await c.query("insert into client(party_id,status,created_by,updated_by) values($1,'active',$2,$2) returning id",[party,joe.id])).rows[0].id;
  const deal=(await c.query("insert into deal(client_id,name,deal_type,phase,owner,created_by,updated_by) values($1,'Synthetic inbox deal','purchase','research','joe',$2,$2) returning id",[client,joe.id])).rows[0].id;
  const lease=(await c.query("insert into lease(client_id,created_by,updated_by,expiration_on) values($1,$2,$2,current_date+interval '30 months') returning id",[client,joe.id])).rows[0].id;
  for(const [subject_type,subject_id] of [['deal',deal],['lease',lease]])
    await call('watch-subject-v1',{subject_type,subject_id,idempotency_key:randomUUID()});
  await c.query("update lease set expiration_on=current_date+interval '6 months' where id=$1",[lease]);
  const mail=(await c.query("insert into activity(kind,occurred_at,deal_id,actor_id,summary,email_source) values('email_in',now(),$1,$2,'Synthetic inbox reply','{\"mailbox_owner\":\"joe\",\"sender_email\":\"synthetic@example.test\"}') returning id",[deal,joe.id])).rows[0].id;
  await c.query("insert into event(occurred_at,actor_id,verb,subject_type,subject_id,new_value,cause) values(now(),$1,'confirm-email-filing','deal',$2,$3,'automation_job')",[joe.id,deal,{activity_id:mail}]);
  await c.query('select ops.sweep_subject_watches(clock_timestamp(),50)');
  const canonical=async()=> (await c.query('select to_jsonb(d) deal,to_jsonb(l) lease from deal d cross join lease l where d.id=$1 and l.id=$2',[deal,lease])).rows[0];
  const original=await canonical();
  const feed=await call('notification-feed',{limit:200});
  const home=()=>call('read-home-inbox-v5');
  for(const [kind,ref,url,name] of [['deal',deal,`/deals?deal=${deal}`,'Synthetic inbox deal'],['lease',lease,`/clients?record=${client}`,'Synthetic inbox client']]) {
    const notice=feed.notifications.find(n=>n.subject_type===kind&&n.subject_ref===ref);
    assert.ok(notice); assert.equal(notice.deep_link,url);
    const find=async()=> (await home()).items.find(i=>i.merged_source_ids.includes(notice.id));
    let item=await find(); assert.ok(item,`${kind} has a Home card`);
    const context=kind==='lease'?`${name} · Lease expires ${original.lease.expiration_on}`:name;
    assert.equal(item.open_url,url); assert.equal(item.title,`${notice.reason} · ${context}`);
    assert.equal(item.context_label,context);
    assert.equal(item.section,'inbox'); assert.equal(item.unseen,true);
    for(const seen of [false,true]) for(const placement of ['inbox','settled','snoozed']) {
      const operations=placement==='inbox'?['acknowledge-notification','settle-home-inbox','snooze-home-inbox']:['acknowledge-notification','unsettle-home-inbox'];
      for(const operation of operations) await t.test(`${kind}: ${seen?'read':'unread'} ${placement} ${operation}`,async()=>{
        await c.query('begin'); isolated=true;
        try {
        if(seen) await call('acknowledge-notification',{notification_id:notice.id,idempotency_key:randomUUID()});
        let current=await find();
        const mutate=async verb=>call(verb,{idempotency_key:randomUUID(),items:[{item_id:current.item_id,base_version:current.base_version}],...(verb==='snooze-home-inbox'?{until:'2099-01-01T00:00:00Z'}:{})});
        if(placement!=='inbox') { await mutate(placement==='settled'?'settle-home-inbox':'snooze-home-inbox'); current=await find(); }
        if(operation==='acknowledge-notification') await call(operation,{notification_id:notice.id,idempotency_key:randomUUID()});
        else await mutate(operation);
        const after=await find();
        assert.equal(after.section,operation==='acknowledge-notification'?placement:operation==='settle-home-inbox'?'settled':operation==='snooze-home-inbox'?'snoozed':'inbox');
        assert.equal(after.unseen,operation==='acknowledge-notification'?false:!seen);
        assert.equal(after.source_state,'needs_attention');
        assert.ok((await call('read-subject-watch-v1',{subject_type:kind,subject_id:ref})).watch,'inbox actions preserve watch');
        } finally { await c.query('rollback'); isolated=false; }
      });
    }
    const key=randomUUID();
    await call('acknowledge-notification',{notification_id:notice.id,idempotency_key:key});
    item=await find(); assert.equal(item.unseen,false); assert.equal(item.section,'inbox');
    assert.equal(item.source_state,'needs_attention');
    await call('acknowledge-notification',{notification_id:notice.id,idempotency_key:key});
    const change=async verb=>call(verb,{idempotency_key:randomUUID(),items:[{item_id:item.item_id,base_version:item.base_version}],...(verb==='snooze-home-inbox'?{until:'2099-01-01T00:00:00Z'}:{})});
    await change('settle-home-inbox'); item=await find();
    assert.equal(item.section,'settled'); assert.equal(item.source_state,'needs_attention');
    await change('unsettle-home-inbox'); item=await find(); assert.equal(item.section,'inbox');
    await change('snooze-home-inbox'); item=await find(); assert.equal(item.section,'snoozed');
    assert.equal(item.unseen,false); assert.equal(item.source_state,'needs_attention');
    assert.ok((await call('notification-feed',{limit:200})).notifications.find(n=>n.id===notice.id).read_at);
    await change('unsettle-home-inbox'); item=await find(); assert.equal(item.section,'inbox');
    assert.deepEqual(await canonical(),original);
  }
});
