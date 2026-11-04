import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { executeRegisteredTool } from '../src/tools.js';

test('valid_pushed_card_beyond_home_snapshot_still_resolves_only_for_recipient', async t => {
  const f = await acquireDisposablePostgres({prefix:'partner-needed-',snapshot:true});
  const c = new pg.Client(f.connection); await c.connect();
  t.after(async () => { await c.end(); await f.close(); });
  const [who, other] = (await c.query("select id,slug from actor where slug in ('joe','dell') order by slug")).rows;
  await c.query("update actor set email=slug||'@example.test' where slug in ('joe','dell')");
  const run = async (sql, args=[], actor=who) => {
    await c.query('begin');
    try {
      await c.query("select set_config('carr.acting_actor_slug',$1,true),set_config('carr.verified_human_actor_slug',$1,true),set_config('carr.organization_tenant_id','carr-internal',true)",[actor.slug]);
      await c.query('set local role carr_writer');
      const result = sql === 'read-partner-needed-card'
        ? await executeRegisteredTool(c,{...actor,human:true},sql,{event_id:args[0]})
        : sql === 'read-home-review' ? await executeRegisteredTool(c,{...actor,human:true},sql,args)
          : (await c.query(sql,args)).rows[0]?.result;
      await c.query('commit'); return result;
    } catch(e) { await c.query('rollback'); throw e; }
  };
  const draft = async () => {
    const id=randomUUID(), activity=randomUUID();
    await c.query(`insert into activity(id,occurred_at,actor_id,kind,summary,detail,email_source)
      values($1,now(),$2,'email_in','Synthetic','Synthetic',jsonb_build_object('mailbox_owner',$3::text,'sender_email','sender@example.test'))`,[activity,who.id,who.slug]);
    await c.query(`insert into email_proposal(id,kind,activity_id,evidence_quote,owner,confidence,created_by,draft_subject,draft_body)
      values($1,'reply_draft',$2,'Synthetic',$3,0.95,$4,'Synthetic','Synthetic')`,[id,activity,who.slug,who.id]);
    return id;
  };
  await draft();
  await c.query(`insert into ops.web_push_subscription(organization_tenant_id,actor_id,installation_id,generation,endpoint,endpoint_hash,p256dh,auth,key_generation)
    values('carr-internal',$1,$2,1,'https://push.example.test/synthetic','synthetic','synthetic','synthetic','synthetic')`,[who.id,randomUUID()]);
  const drain = () => c.query("select ops.enqueue_partner_needed('carr-internal')");
  await drain();
  assert.equal((await c.query('select count(*)::int n from ops.partner_needed_event')).rows[0].n,0);
  const id=await draft(); await drain();
  const event=(await c.query('select * from ops.partner_needed_event')).rows[0];
  assert.equal(event.card_item_id,`email_proposal:${id}`);
  await c.query(`insert into next_action(subject_type,subject_id,owner_id,description,created_by)
    select 'client',gen_random_uuid(),$1,'Synthetic task '||n,$1 from generate_series(1,1001) n`,[who.id]);
  const read = actor => run('read-partner-needed-card',[event.event_id],actor);
  assert.equal((await run('select ops.read_home_inbox_v3() result')).items.some(i=>i.item_id===event.card_item_id),false);
  assert.equal((await read(who)).state,'actionable');
  assert.equal((await read(who)).item.review_reference.kind,'reply_draft');
  const exact=(await read(who)).item;
  const detail=await run('read-home-review',{item_id:exact.item_id,source_version:exact.source_version});
  assert.equal(detail.detail.reply_draft.body,'Synthetic');
  assert.equal((await read(other)).state,'unavailable');
  await t.test('snoozed and settled exact targets resolve without a send',async () => {
    await c.query(`insert into ops.home_inbox_disposition(actor_id,item_id,disposition,snoozed_until,source_version,material_version)
      values($1,$2,'snoozed',now()+interval '1 day','synthetic','synthetic')`,[who.id,event.card_item_id]);
    assert.equal((await read(who)).state,'resolved');
    await c.query("update ops.home_inbox_disposition set disposition='settled',snoozed_until=null where actor_id=$1 and item_id=$2",[who.id,event.card_item_id]);
    assert.equal((await read(who)).state,'resolved');
    await c.query('delete from ops.home_inbox_disposition where actor_id=$1 and item_id=$2',[who.id,event.card_item_id]);
    assert.equal((await read(who)).state,'actionable');
  });
  await t.test('only_three_actionable_topics_create_one_recipient_event_per_revision',async () => {
    await drain(); await drain();
    assert.equal((await c.query('select count(*)::int n from ops.partner_needed_delivery')).rows[0].n,1);
    await c.query("update email_proposal set draft_subject='Edited',version=version+1 where id=$1",[id]);
    await drain();
    assert.equal((await c.query('select count(*)::int n from ops.partner_needed_event')).rows[0].n,1);
  });
  await c.query("update email_proposal set status='dismissed',resolved_at=now(),resolved_by=$2,resolution_reason='Synthetic' where id=$1",[id,who.id]);
  assert.equal((await read(who)).state,'resolved');
  await t.test('processing done is not review waiting; pending candidate selects recorder then review resolves',async () => {
    const session=randomUUID();
    await c.query(`insert into capture_session(id,nonce,device_id,actor_id,mode,started_at,consent_announced_at,session_token_hash,expires_at,state,state_at,post_call)
      values($1::uuid,$1::uuid::text,'synthetic',$2,'meeting',now(),now(),'synthetic',now()+interval '1 day','done',now(),true)`,[session,who.id]);
    await drain();
    assert.equal((await c.query("select count(*)::int n from ops.partner_needed_event where topic='post_call_review_waiting'")).rows[0].n,0);
    await c.query(`insert into capture_candidate(session_id,idempotency_key,batch_hash,item_index,kind,payload,evidence_quote,confidence)
      values($1,'synthetic','synthetic',0,'activity','{}','Synthetic',0.9)`,[session]);
    await drain();
    const callEvent=(await c.query("select * from ops.partner_needed_event where topic='post_call_review_waiting'")).rows[0];
    assert.equal(callEvent.recipient_actor,who.id);
    const legacy=(await run('select ops.read_home_inbox_v3() result')).items;
    assert.equal(legacy.some(i=>i.source_type==='post_call_review'),false);
    const current=(await run('select ops.read_home_inbox_v4() result')).items;
    assert.equal(current.some(i=>i.item_id===callEvent.card_item_id),true);
    assert.equal((await run('select ops.read_partner_needed_card($1) result',[callEvent.event_id])).item.review_target.session_id,session);
    assert.equal((await run('select ops.read_partner_needed_card($1) result',[callEvent.event_id])).state,'actionable');
    assert.equal((await run('select ops.acknowledge_partner_review($1,$2) result',[callEvent.event_id,randomUUID()])).ok,false);
    await c.query('update capture_session set outcome_reviewed_at=now(),outcome_reviewed_by=$2 where id=$1',[session,who.id]);
    assert.equal((await run('select ops.read_partner_needed_card($1) result',[callEvent.event_id])).state,'resolved');
  });
  await t.test('feedback is grant-specific, revision-bound and acknowledged only by its recipient',async () => {
    const party=randomUUID(),client=randomUUID(),deal=randomUUID(),tour=randomUUID(),projection=randomUUID(),grant=randomUUID(),property=randomUUID();
    await c.query('set session_replication_role=replica');
    try {
      await c.query(`insert into party(id,kind,name,created_by,updated_by) values($1,'person','Synthetic',$2,$2)`,[party,who.id]);
      await c.query(`insert into client(id,party_id,created_by,updated_by) values($1,$2,$3,$3)`,[client,party,who.id]);
      await c.query(`insert into deal(id,name,owner,client_id,deal_type,phase,created_by,updated_by) values($1,'Synthetic',$2,$3,'lease','prospect',$4,$4)`,[deal,who.slug,client,who.id]);
      await c.query(`insert into ops.tour(id,organization_tenant_id,tour_name,tour_status,route_version,canonical_dataset_version,deal_id,subject_type,subject_id,subject_bound_at)
        values($1,'carr-internal','Synthetic','active',1,'synthetic',$2::uuid,'deal',$2::uuid::text,now())`,[tour,deal]);
      await c.query(`insert into ops.tour_public_projection(id,organization_tenant_id,tour_id,projection_version,route_version,as_of,facts_only,projection_digest,status)
        values($1,'carr-internal',$2,1,1,now(),true,'sha256:'||repeat('a',64),'published')`,[projection,tour]);
      await c.query(`insert into ops.tour_share_grant(id,organization_tenant_id,projection_id,grant_version,token_digest,audience,permission_scopes,expires_at,status,created_by_actor_id)
        values($1,'carr-internal',$2,1,'sha256:'||repeat('a',64),'client','["view_packet","comment"]',now()+interval '1 day','active',$3)`,[grant,projection,who.id]);
      await c.query("insert into ops.tour_property(id,organization_tenant_id,property_status) values($1,'carr-internal','active')",[property]);
    } finally { await c.query('set session_replication_role=origin'); }
    const feedback=()=>c.query(`insert into ops.tour_share_feedback_event(organization_tenant_id,projection_id,share_grant_id,property_id,idempotency_key,action,comment,payload_digest)
      values('carr-internal',$1,$2,$3,$4,'comment','Synthetic','sha256:'||repeat('a',64))`,[projection,grant,property,randomUUID()]);
    await feedback(); await drain();
    const first=(await c.query("select * from ops.partner_needed_event where topic='tour_feedback_received'")).rows[0];
    assert.equal(first.source_id,grant);
    const ackKey=randomUUID();
    assert.equal((await run('select ops.acknowledge_partner_review($1,$2) result',[first.event_id,ackKey],other)).ok,false);
    assert.equal((await run('select ops.acknowledge_partner_review($1,$2) result',[first.event_id,ackKey])).ok,true);
    assert.equal((await run('select ops.acknowledge_partner_review($1,$2) result',[first.event_id,ackKey])).ok,true);
    assert.equal((await run('select ops.read_partner_needed_card($1) result',[first.event_id])).state,'resolved');
    await feedback(); await drain();
    const events=(await c.query("select * from ops.partner_needed_event where topic='tour_feedback_received' order by created_at,event_id")).rows;
    assert.equal(events.length,2);
    assert.notEqual(events[0].card_item_id,events[1].card_item_id);
    await c.query('begin');
    await c.query('set local session_replication_role=replica');
    await c.query('delete from ops.tour_public_projection where id=$1',[projection]);
    assert.equal((await c.query("select count(*)::int n from ops.partner_needed_events('carr-internal',$1) where topic='tour_feedback_received'",[who.id])).rows[0].n,0);
    await c.query('rollback');
    await c.query('begin');
    await c.query("update deal set owner=$2 where id=$1",[deal,other.slug]);
    assert.equal((await c.query("select count(*)::int n from ops.partner_needed_events('carr-internal',$1) where topic='tour_feedback_received'",[who.id])).rows[0].n,0);
    await c.query('rollback');
  });
  await t.test('producer rollback and bounded batches retain durable progress without a history burst',async () => {
    const before=(await c.query('select count(*)::int n from ops.partner_needed_event')).rows[0].n;
    for(let n=0;n<25;n++) await draft();
    await c.query('begin'); await drain(); await c.query('rollback');
    assert.equal((await c.query('select count(*)::int n from ops.partner_needed_event')).rows[0].n,before);
    await drain();
    assert.equal((await c.query('select count(*)::int n from ops.partner_needed_event')).rows[0].n,before+20);
    await drain();
    assert.equal((await c.query('select count(*)::int n from ops.partner_needed_event')).rows[0].n,before+25);
    await c.query(`insert into ops.web_push_subscription(organization_tenant_id,actor_id,installation_id,generation,endpoint,endpoint_hash,p256dh,auth,key_generation)
      values('carr-internal',$1,$2,1,'https://push.example.test/new','new','synthetic','synthetic','synthetic')`,[who.id,randomUUID()]);
    const deliveries=(await c.query('select count(*)::int n from ops.partner_needed_delivery')).rows[0].n;
    await drain();
    assert.equal((await c.query('select count(*)::int n from ops.partner_needed_delivery')).rows[0].n,deliveries);
  });
});
