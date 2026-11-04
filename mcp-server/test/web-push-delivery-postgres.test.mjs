import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { postgresDeliveryStore, drainWebPush } from '../src/web-push-delivery.ts';

const config={WEB_PUSH_ENABLED:'true',WEB_PUSH_VAPID_PUBLIC_KEY:'B'+'A'.repeat(86),WEB_PUSH_VAPID_PRIVATE_KEY:'A'.repeat(43),WEB_PUSH_VAPID_SUBJECT:'mailto:test@example.test',WEB_PUSH_KEY_GENERATION:'test'};

test('durable_delivery_statechart',async t=>{
  const f=await acquireDisposablePostgres({prefix:'web-push-delivery-',snapshot:true});
  const db=new pg.Client(f.connection);await db.connect();
  const other=new pg.Client(f.connection);await other.connect();
  t.after(async()=>{await other.end();await db.end();await f.close();});
  const actor=(await db.query("select id,slug from actor where slug='joe'")).rows[0];
  await db.query("insert into ops.notification_preference(actor,device_opt_in) values($1,true) on conflict(actor) do update set device_opt_in=true,quiet_hours_start=null,quiet_hours_end=null",[actor.id]);
  const subscription=randomUUID();
  await db.query(`insert into ops.web_push_subscription(subscription_id,organization_tenant_id,actor_id,installation_id,generation,endpoint,endpoint_hash,p256dh,auth,key_generation)
    values($1,'carr-internal',$2,$3,1,'https://fcm.googleapis.com/fcm/send/synthetic','synthetic','synthetic','synthetic','test')`,[subscription,actor.id,randomUUID()]);
  const make=async()=>{
    const activity=randomUUID(),proposal=randomUUID();
    await db.query(`insert into activity(id,occurred_at,actor_id,kind,summary,detail,email_source)
      values($1,now(),$2,'email_in','Synthetic','Synthetic',jsonb_build_object('mailbox_owner','joe','sender_email','sender@example.test'))`,[activity,actor.id]);
    await db.query(`insert into email_proposal(id,kind,activity_id,evidence_quote,owner,confidence,created_by,draft_subject,draft_body)
      values($1,'reply_draft',$2,'Synthetic','joe',0.95,$3,'Synthetic','Synthetic')`,[proposal,activity,actor.id]);
    await db.query("select ops.enqueue_web_push()");
    return (await db.query('select * from ops.partner_needed_event where source_id=$1',[proposal])).rows[0];
  };
  const store=postgresDeliveryStore(db),second=postgresDeliveryStore(other);
  const delivery=async e=>(await db.query('select * from ops.partner_needed_delivery where event_id=$1',[e.event_id])).rows[0];
  const reset=async()=>{
    await db.query('delete from ops.partner_needed_delivery');await db.query('delete from ops.web_push_budget');await db.query('delete from ops.web_push_breaker');
    await db.query('update ops.web_push_subscription set revoked_at=null');
    await db.query('update ops.notification_preference set device_opt_in=true,quiet_hours_start=null,quiet_hours_end=null');
  };
  const due=async e=>db.query("update ops.partner_needed_delivery set next_attempt_at=now()-interval '1 minute' where event_id=$1",[e.event_id]);
  await t.test('quiet_or_resolved_card_is_never_sent_even_when_outbox_row_is_pending',async()=>{
    const e=await make();
    await db.query("update ops.notification_preference set quiet_hours_start=(now() at time zone 'UTC'-interval '1 hour')::time,quiet_hours_end=(now() at time zone 'UTC'+interval '1 hour')::time,timezone='UTC'");
    let sent=0;const send=async()=>{sent++;return new Response(null,{status:201});};
    await drainWebPush({config,store,send});assert.equal(sent,0);assert.equal((await delivery(e)).state,'retry_due');assert.equal((await delivery(e)).attempt_count,0);
    await db.query('update ops.notification_preference set quiet_hours_start=null,quiet_hours_end=null');await due(e);
    await db.query(`insert into ops.home_inbox_disposition(actor_id,item_id,disposition,source_version,material_version)
      values($1,$2,'settled','synthetic','synthetic')`,[actor.id,e.card_item_id]);
    await drainWebPush({config,store,send});assert.equal(sent,0);assert.equal((await delivery(e)).state,'suppressed');
  });
  await reset();
  await t.test('two_workers_never_own_one_live_claim_and_stale_completion_is_refused',async()=>{
    const e=await make();const [a,b]=await Promise.all([store.claim(),second.claim()]);
    assert.equal(a.length+b.length,1);const first=[...a,...b][0];assert.equal(await store.authorize(first,'test'),true);
    await db.query("update ops.partner_needed_delivery set claim_until=now()-interval '1 second' where event_id=$1",[e.event_id]);
    const [next]=await second.claim();assert.notEqual(next.claim_token,first.claim_token);
    assert.equal(await store.finish(first,201,0),false);assert.equal(await second.finish(next,201,0),false);
    assert.equal(await second.authorize(next,'test'),true);assert.equal(await second.authorize(next,'test'),false);
    assert.equal(await second.finish(next,201,0),true);assert.equal((await delivery(e)).state,'accepted_by_provider');
    assert.equal((await delivery(e)).attempt_count,2);assert.equal((await store.claim()).length,0);
  });
  await reset();
  await t.test('opt_out_racing_with_claim_suppresses_before_send',async()=>{
    const e=await make();const [claim]=await store.claim();
    await db.query('update ops.notification_preference set device_opt_in=false');
    assert.equal(await store.authorize(claim,'test'),false);assert.equal((await delivery(e)).state,'suppressed');
  });
  await reset();
  await t.test('three_attempt_stop_one_minute_then_five_minute_backoff',async()=>{
    const e=await make();
    for(let n=1;n<=3;n++){
      const [claim]=await store.claim();assert.ok(claim);assert.equal(await store.authorize(claim,'test'),true);
      await store.finish(claim,500,0);const d=await delivery(e);
      assert.equal(d.attempt_count,n);assert.equal(d.state,n===3?'failed':'retry_due');
      const wait=Date.parse(d.next_attempt_at)-Date.now();assert.ok(wait>=(n===1?59000:299000));await due(e);
    }
    assert.equal((await store.claim()).length,0);
  });
  await reset();
  await t.test('provider_404_and_410_revoke_endpoint',async()=>{
    for(const status of [404,410]){
      await db.query('update ops.web_push_subscription set revoked_at=null');const e=await make();const [claim]=await store.claim();
      assert.equal(await store.authorize(claim,'test'),true);await store.finish(claim,status,0);
      assert.equal((await delivery(e)).state,'failed');assert.ok((await db.query('select revoked_at from ops.web_push_subscription')).rows[0].revoked_at);
    }
  });
  await reset();
  await t.test('429_honors_retry_after_within_expiry',async()=>{
    const e=await make();const [claim]=await store.claim();assert.equal(await store.authorize(claim,'test'),true);
    await store.finish(claim,429,600);assert.ok(Date.parse((await delivery(e)).next_attempt_at)-Date.now()>599000);
    await due(e);const [again]=await store.claim();assert.equal(await store.authorize(again,'test'),true);
    await store.finish(again,429,86400);assert.equal((await delivery(e)).state,'expired');
  });
  await reset();
  await t.test('fake_clock_expiry_enforces_twenty_four_hours_even_with_later_expires_at',async()=>{
    const e=await make();await db.query("update ops.partner_needed_event set created_at=now()-interval '24 hours',expires_at=now()+interval '1 day' where event_id=$1",[e.event_id]);
    const [claim]=await store.claim();assert.equal(await store.authorize(claim,'test'),false);assert.equal((await delivery(e)).state,'expired');
  });
  await reset();
  await t.test('hundred_per_actor_day_boundary_is_atomic',async()=>{
    const a=await make(),b=await make();
    await db.query("insert into ops.web_push_budget(actor_id,day,attempts) values($1,(now() at time zone 'UTC')::date,99)",[actor.id]);
    const claims=await store.claim();const allowed=await Promise.all(claims.map((c,i)=>(i?second:store).authorize(c,'test')));
    assert.equal(allowed.filter(Boolean).length,1);assert.equal((await db.query('select attempts from ops.web_push_budget')).rows[0].attempts,100);
    assert.equal([await delivery(a),await delivery(b)].filter(d=>d.state==='retry_due').length,1);
  });
  await reset();
  await t.test('breaker_five_failures_open_five_minutes_and_one_half_open_probe',async()=>{
    const events=[];for(let i=0;i<7;i++)events.push(await make());
    for(let i=0;i<5;i++){
      const claims=await store.claim();const claim=claims.find(c=>c.event_id===events[i].event_id);assert.ok(claim);
      assert.equal(await store.authorize(claim,'test'),true);await store.finish(claim,500,0);
      await db.query("update ops.partner_needed_delivery set state='queued' where state='claimed'");
    }
    const b=(await db.query('select * from ops.web_push_breaker')).rows[0];assert.equal(b.failures,5);assert.ok(Date.parse(b.open_until)-Date.now()>299000);
    let claims=await store.claim();assert.equal(await store.authorize(claims[0],'test'),false);
    await db.query("update ops.web_push_breaker set open_until=now()-interval '1 second'");
    await db.query("update ops.partner_needed_delivery set next_attempt_at=now() where terminal_reason='deferred'");
    await db.query("update ops.partner_needed_delivery set state='queued' where state='claimed'");
    claims=await store.claim();const allowed=await Promise.all(claims.map((c,i)=>(i%2?second:store).authorize(c,'test')));
    assert.equal(allowed.filter(Boolean).length,1);
    await store.finish(claims[allowed.indexOf(true)],201,0);
    const closed=(await db.query('select * from ops.web_push_breaker')).rows[0];assert.equal(closed.failures,0);assert.equal(closed.open_until,null);
  });
  await reset();
  await t.test('revoked_installation_and_key_rotation_suppress_current_claim',async()=>{
    for(const change of ["update ops.web_push_subscription set revoked_at=now()","update ops.web_push_subscription set key_generation='rotated'"]){
      await db.query("update ops.web_push_subscription set revoked_at=null,key_generation='test'");
      const e=await make();const [claim]=await store.claim();await db.query(change);
      assert.equal(await store.authorize(claim,'test'),false);assert.equal((await delivery(e)).state,'suppressed');
    }
    await db.query("update ops.web_push_subscription set key_generation='test'");
  });
  await reset();
  await t.test('quiet_deferral_past_expiry_expires_without_attempt',async()=>{
    const e=await make();await db.query("update ops.partner_needed_event set expires_at=now()+interval '5 minutes' where event_id=$1",[e.event_id]);
    await db.query("update ops.notification_preference set quiet_hours_start=(now() at time zone 'UTC'-interval '1 hour')::time,quiet_hours_end=(now() at time zone 'UTC'+interval '1 hour')::time,timezone='UTC'");
    const [claim]=await store.claim();assert.equal(await store.authorize(claim,'test'),false);
    assert.equal((await delivery(e)).state,'expired');assert.equal((await delivery(e)).attempt_count,0);
  });
  await reset();
  await t.test('three_installations_per_actor_and_foreign_subscription_suppression',async()=>{
    for(let i=0;i<3;i++) await db.query(`insert into ops.web_push_subscription(subscription_id,organization_tenant_id,actor_id,installation_id,generation,endpoint,endpoint_hash,p256dh,auth,key_generation,created_at)
      values($1,'carr-internal',$2,$3,1,'https://fcm.googleapis.com/fcm/send/synthetic',$4,'synthetic','synthetic','test',now()+make_interval(secs=>$5))`,[randomUUID(),actor.id,randomUUID(),'synthetic-'+i,i+1]);
    const e=await make();const claims=await store.claim();assert.equal(claims.length,4);
    const allowed=[];for(const claim of claims){const admitted=await store.authorize(claim,'test');allowed.push(admitted);if(admitted)await store.finish(claim,201,0);}
    assert.equal(allowed.filter(Boolean).length,3);
    assert.equal((await db.query("select count(*)::int n from ops.partner_needed_delivery where state='suppressed'")).rows[0].n,1);
    await db.query('delete from ops.partner_needed_delivery');await db.query('delete from ops.partner_needed_seen where subscription_id<>$1',[subscription]);
    await db.query('delete from ops.partner_needed_activation where subscription_id<>$1',[subscription]);
    await db.query('delete from ops.web_push_subscription where subscription_id<>$1',[subscription]);
  });
  await reset();
  await t.test('half_open_failed_probe_reopens_and_probe_crash_allows_one_replacement',async()=>{
    await make();await make();
    await db.query("insert into ops.web_push_breaker(origin,failures,open_until) values('fcm.googleapis.com',5,now()-interval '1 second')");
    const claims=await store.claim();assert.equal(await store.authorize(claims[0],'test'),true);
    assert.equal(await second.authorize(claims[1],'test'),false);
    await store.finish(claims[0],0,0);assert.ok((await db.query('select open_until>now() opened from ops.web_push_breaker')).rows[0].opened);
    await db.query("update ops.web_push_breaker set open_until=now()-interval '1 second'");
    await db.query("update ops.partner_needed_delivery set next_attempt_at=now()");
    const next=await store.claim();assert.equal(await store.authorize(next[0],'test'),true);
    await db.query("update ops.web_push_breaker set probe_until=now()-interval '1 second'");
    assert.equal(await second.authorize(next[1],'test'),true);
    assert.equal(await store.finish(next[0],201,0),true);
    assert.ok((await db.query('select probe_token from ops.web_push_breaker')).rows[0].probe_token);
    await second.finish(next[1],201,0);assert.equal((await db.query('select open_until from ops.web_push_breaker')).rows[0].open_until,null);
  });
  await reset();
  await t.test('sixty_deliveries_claim_twenty_and_backlog_advances',async()=>{
    for(let i=0;i<60;i++)await make();
    const seen=new Set();
    for(let n=0;n<3;n++){
      const claims=await store.claim();assert.equal(claims.length,20);
      for(const claim of claims){assert.equal(seen.has(claim.event_id),false);seen.add(claim.event_id);
        assert.equal(await store.authorize(claim,'test'),true);await store.finish(claim,201,0);}
    }
    assert.equal(seen.size,60);assert.equal((await store.claim()).length,0);
  });
  await reset();
  await t.test('constant_query_count_for_twenty_deliveries_source_to_outbox_to_fake_provider_contract',async()=>{
    for(let i=0;i<20;i++)await make();
    let queries=0,sends=0;
    const counted=postgresDeliveryStore({query:async(...args)=>{queries++;return db.query(...args);}});
    const result=await drainWebPush({config,store:counted,send:async()=>{sends++;return new Response(null,{status:201});}});
    assert.equal(sends,20);assert.equal(result.accepted,20);assert.ok(queries<=42);
    assert.equal((await db.query("select count(*)::int n from ops.partner_needed_delivery where state='accepted_by_provider'")).rows[0].n,20);
  });
  await reset();
  await t.test('resolved_source_suppressed_at_send_and_provider_outage_keeps_home_card',async()=>{
    const e=await make();let [claim]=await store.claim();
    assert.equal(await store.authorize(claim,'test'),true);await store.finish(claim,0,0);
    assert.ok((await db.query("select 1 from ops.partner_needed_events('carr-internal',$1) where item_id=$2",[actor.id,e.card_item_id])).rows.length);
    await due(e);[claim]=await store.claim();
    await db.query("update email_proposal set status='dismissed',resolved_at=now(),resolved_by=$2,resolution_reason='Synthetic' where id=$1",[e.source_id,actor.id]);
    assert.equal(await store.authorize(claim,'test'),false);assert.equal((await delivery(e)).state,'suppressed');
  });
  await reset();
  await t.test('overlapping_workers_share_two_durable_provider_slots',async()=>{
    for(let i=0;i<3;i++)await make();const claims=await store.claim();
    const results=await Promise.all(claims.map((claim,i)=>(i%2?second:store).authorize(claim,'test')));
    assert.equal(results.filter(Boolean).length,2);
    for(let i=0;i<claims.length;i++)if(results[i])await store.finish(claims[i],201,0);
    const deferred=claims[results.indexOf(false)];await due(deferred);
    const [next]=await store.claim();assert.equal(await store.authorize(next,'test'),true);await store.finish(next,201,0);
  });
  await t.test('jobs_functions_are_registered_and_partner_roles_cannot_execute',async()=>{
    assert.equal((await db.query("select has_function_privilege('carr_jobs','ops.claim_web_push()','execute') ok")).rows[0].ok,true);
    assert.equal((await db.query("select has_function_privilege('carr_writer','ops.claim_web_push()','execute') ok")).rows[0].ok,false);
    const contract=(await db.query('select ops.scac_current_grant_contract() c')).rows[0].c;
    assert.ok(contract.secdef_execute.some(e=>e.grantee==='carr_jobs' && e.signature==='ops.claim_web_push()'));
    await db.query('begin');await db.query('set local role carr_jobs');
    await store.enqueue();assert.deepEqual(await store.claim(),[]);await db.query('rollback');
    await db.query('begin');await db.query('set local role carr_writer');
    await assert.rejects(store.claim(),error=>error.code==='42501');await db.query('rollback');
  });
});
