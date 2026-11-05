import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { executeRegisteredTool } from '../src/tools.js';

test('email milestones apply once and undo only while current', async () => {
  const fixture = await acquireDisposablePostgres({prefix:'email-phase-',snapshot:true});
  const c = new pg.Client(fixture.connection); await c.connect();
  try {
    const actor = {...(await c.query("select id,slug from actor where slug='joe'")).rows[0],human:true};
    const party=(await c.query("insert into party(kind,name,created_by,updated_by) values('person','Synthetic phase',$1,$1) returning id",[actor.id])).rows[0].id;
    const client=(await c.query('insert into client(party_id,created_by,updated_by) values($1,$2,$2) returning id',[party,actor.id])).rows[0].id;
    const deal=(await c.query("insert into deal(name,client_id,deal_type,phase,owner,created_by,updated_by) values('Synthetic phase',$1,'lease','site_selection','joe',$2,$2) returning id",[client,actor.id])).rows[0].id;
    const command=async(verb,args)=>{await c.query('begin');try {const r=await executeRegisteredTool(c,actor,verb,args);await c.query('commit');return r;}catch(e){await c.query('rollback');throw e;}};
    const complete={body_kind:'full',body_completeness:'complete',body_outcome:'complete',body_truncated:false};
    const quote='We received the LOI from the landlord.';
    const source=async(body=quote,context=complete,identity=null)=> (await c.query(`insert into activity(kind,summary,detail,client_id,deal_id,actor_id,occurred_at,email_context,email_identity)
      values('email_in','Synthetic phase',$1,$2,$3,$4,now(),$5::jsonb,$6::jsonb) returning id`,[body,client,deal,actor.id,JSON.stringify(context),identity ? JSON.stringify(identity) : null])).rows[0].id;
    const version=async()=> (await c.query('select version from deal where id=$1',[deal])).rows[0].version;
    const claims=new Map();
    const record=async(activity_id,evidence_quote=quote,extra={})=>{
      if(!claims.has(activity_id)) {
        await c.query("select public.enqueue_email_filing($1,$2,'synthetic','confirmed')",[activity_id,deal]);
        const generation=(await c.query('select generation from email_filing_application where activity_id=$1',[activity_id])).rows[0].generation;
        const claim=await command('record-email-proposal',{mode:'application_claim',activity_id,generation,idempotency_key:randomUUID()});
        claims.set(activity_id,{generation,claim_id:claim.claim_id});
      }
      return command('record-email-proposal',{idempotency_key:randomUUID(),kind:'phase_move',deal,activity_id,evidence_quote,confidence:0.99,phase_base_version:await version(),...claims.get(activity_id),...extra});
    };
    const id=await source();
    const result=await record(id);
    assert.equal(result.phase_application.status,'applied');
    const receipt=result.phase_application;
    assert.ok(receipt.event_id);
    assert.equal((await c.query('select phase from deal where id=$1',[deal])).rows[0].phase,'negotiation');
    assert.equal((await record(id)).phase_application.event_id,receipt.event_id);
    await command('record-email-proposal',{mode:'phase_undo',proposal_id:result.proposal_id,phase_base_version:await version(),idempotency_key:randomUUID()});
    assert.equal((await record(id)).phase_application.event_id,receipt.event_id);
    assert.equal((await c.query('select phase from deal where id=$1',[deal])).rows[0].phase,'site_selection');
    const identity={mailbox_owner:'joe',mailbox_account:'synthetic@example.test',item_id:randomUUID()};
    const original=await source(quote,complete,{...identity,subject:'one'});
    const duplicate=await source(quote,complete,{...identity,subject:'two'});
    const first=await record(original);
    const again=await record(duplicate);
    assert.equal(again.phase_application.event_id,first.phase_application.event_id);
    assert.equal(again.phase_application.replayed,true);
    await c.query("update deal set phase='site_selection' where id=$1",[deal]);
    const crash=await source();
    await c.query("select public.enqueue_email_filing($1,$2,'crash','confirmed')",[crash,deal]);
    const crashGeneration=(await c.query('select generation from email_filing_application where activity_id=$1',[crash])).rows[0].generation;
    const crashClaim=await command('record-email-proposal',{mode:'application_claim',activity_id:crash,generation:crashGeneration,idempotency_key:randomUUID()});
    const crashArgs={kind:'phase_move',activity_id:crash,deal,generation:crashGeneration,claim_id:crashClaim.claim_id,
      evidence_quote:quote,confidence:0.99,phase_base_version:await version(),idempotency_key:randomUUID()};
    await c.query('begin');
    assert.equal((await executeRegisteredTool(c,actor,'record-email-proposal',crashArgs)).phase_application.status,'applied');
    await c.query('rollback');
    assert.equal((await c.query('select phase from deal where id=$1',[deal])).rows[0].phase,'site_selection');
    assert.equal((await c.query('select count(*)::int as n from email_proposal where activity_id=$1',[crash])).rows[0].n,0);
    assert.equal((await command('record-email-proposal',crashArgs)).phase_application.status,'applied');
    await c.query("update deal set phase='site_selection' where id=$1",[deal]);
    for(const context of [{},{...complete,body_truncated:true},{...complete,body_completeness:'partial'}]) {
      assert.equal((await record(await source(quote,context))).phase_application.status,'held');
    }
    for(const body of ['We have not received the LOI.','Please send the LOI tomorrow.','The draft LOI was received.']) {
      const activity=await source(body);
      const review=await record(activity,body);
      assert.equal(review.phase_application.status,'review');
      assert.equal((await record(activity,body)).proposal_id,review.proposal_id);
    }
    assert.equal((await record(await source(),quote,{phase_base_version:(await version())-1})).phase_application.status,'review');
    const newer=await record(await source());
    await c.query("update activity set detail=detail||' New revision.' where id=$1",[newer.phase_application.activity_id]);
    await assert.rejects(command('record-email-proposal',{mode:'phase_undo',proposal_id:newer.proposal_id,
      phase_base_version:await version(),idempotency_key:randomUUID()}),e=>e.payload?.error==='email_deal_proposal_stale');
    await command('update-deal',{idempotency_key:randomUUID(),deal,base_version:await version(),fields:{phase:'legal'}});
    await assert.rejects(command('revert-deal-field',{idempotency_key:randomUUID(),event_id:newer.phase_application.event_id}),e=>e.payload?.error==='newer_change_exists');
    for (const [phase,body,target] of [
      ['research','The tour is confirmed for Tuesday.','site_selection'],
      ['negotiation','The LOI has been signed by both parties.','legal'],
      ['negotiation','The lease has been sent for legal review.','legal'],
      ['legal','The lease has been executed by both parties.','closing'],
    ]) {
      await c.query('update deal set phase=$2 where id=$1',[deal,phase]);
      const applied=await record(await source(body),body);
      assert.equal(applied.phase_application.to_phase,target);
    }
    await c.query("update deal set phase='legal',deal_type='purchase' where id=$1",[deal]);
    const unsupported='The lease has been executed by both parties.';
    assert.equal((await record(await source(unsupported),unsupported)).phase_application.status,'review');
    await c.query("update deal set phase='site_selection',deal_type='lease' where id=$1",[deal]);
    const old=await source();
    await c.query("select public.enqueue_email_filing($1,$2,'synthetic','confirmed')",[old,deal]);
    const generation=(await c.query('select generation from email_filing_application where activity_id=$1',[old])).rows[0].generation;
    const claim=await command('record-email-proposal',{mode:'application_claim',activity_id:old,generation,idempotency_key:randomUUID()});
    await c.query("update activity set detail=detail||' Source changed.' where id=$1",[old]);
    await assert.rejects(command('record-email-proposal',{kind:'phase_move',activity_id:old,deal,generation,
      claim_id:claim.claim_id,evidence_quote:quote,confidence:0.99,phase_base_version:await version(),idempotency_key:randomUUID()}),e=>e.payload?.error==='email_application_stale');
    assert.equal((await c.query('select phase from deal where id=$1',[deal])).rows[0].phase,'site_selection');
  } finally {await c.end();await fixture.close();}
});
