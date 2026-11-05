import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { executeRegisteredTool } from '../src/tools.js';

test('email critical dates apply once, hold conflicts and undo only current values', async () => {
  const fixture = await acquireDisposablePostgres({ prefix:'email-critical-',snapshot:true });
  const c = new pg.Client(fixture.connection); await c.connect();
  try {
    const actor = {...(await c.query("select id,slug from actor where slug='joe'")).rows[0],human:true};
    const party = (await c.query("insert into party(kind,name,created_by,updated_by) values('person','Synthetic date',$1,$1) returning id",[actor.id])).rows[0].id;
    const client = (await c.query('insert into client(party_id,created_by,updated_by) values($1,$2,$2) returning id',[party,actor.id])).rows[0].id;
    const deal = (await c.query("insert into deal(name,client_id,deal_type,phase,owner,created_by,updated_by) values('Synthetic date',$1,'lease','legal','joe',$2,$2) returning id",[client,actor.id])).rows[0].id;
    const invoke = async (verb,args,who=actor) => {
      await c.query('begin');
      await c.query(verb==='email-router-queue'?'set local role carr_reader':'set local role carr_writer');
      try {const r=await executeRegisteredTool(c,who,verb,args);await c.query('commit');return r;}
      catch(e){await c.query('rollback');throw e;}
    };
    const command = (args,who) => invoke('record-email-proposal',{idempotency_key:randomUUID(),...args},who);
    const make = async (body,context={}) => {
      const activity_id=(await c.query(`insert into activity(kind,summary,detail,deal_id,client_id,actor_id,updated_by,occurred_at,email_context)
        values('email_in','Synthetic date',$1,$2,$3,$4,$4,now(),$5) returning id`,[body,deal,client,actor.id,
        {body_kind:'full',body_completeness:'complete',body_outcome:'complete',body_truncated:false,attachment_count:0,...context}])).rows[0].id;
      await c.query("select enqueue_email_filing($1,$2,'synthetic','confirmed')",[activity_id,deal]);
      const generation=(await c.query('select generation from email_filing_application where activity_id=$1',[activity_id])).rows[0].generation;
      const claim=await command({mode:'application_claim',activity_id,generation});
      return {mode:'critical_date',activity_id,generation,claim_id:claim.claim_id,deal,obligation_kind:'loi_expiry',evidence_quote:body};
    };
    const first=await make('The agreed LOI expiration date is 2026-11-01.');
    const result=await command(first);
    assert.equal(result.status,'applied');
    assert.equal((await command(first)).effect_id,result.effect_id);
    assert.equal((await c.query('select due_on::text,status,source from critical_date where id=$1',[result.critical_date_id])).rows[0].due_on,'2026-11-01');
    assert.equal((await c.query('select count(*)::int n from critical_date where deal_id=$1',[deal])).rows[0].n,1);
    const room=await invoke('get-deal-room',{deal});
    assert.equal(room.critical_dates.find(d=>d.id===result.critical_date_id).due_on,'2026-11-01');
    const same=await make(first.evidence_quote);
    assert.equal((await command(same)).status,'duplicate');
    await c.query("update critical_date set kind='LOI expiration' where id=$1",[result.critical_date_id]);
    assert.equal((await command(await make(first.evidence_quote))).status,'duplicate');
    const conflict=await make('The agreed LOI expiration date is 2026-11-02.');
    const held=await command(conflict);
    assert.equal(held.reason,'existing_obligation_conflict');
    const version=(await c.query('select version from critical_date where id=$1',[result.critical_date_id])).rows[0].version;
    const correction={mode:'critical_date_review',effect_id:held.effect_id,decision:'apply',due_on:'2026-11-02',
      critical_date_id:result.critical_date_id,base_version:version,reason:'Reviewed explicit correction'};
    await assert.rejects(command(correction,{...actor,human:false}),e=>e.payload?.error==='human_confirmation_required');
    await assert.rejects(command({...correction,base_version:version+1}),e=>e.payload?.error==='version_conflict');
    assert.equal((await command(correction)).status,'applied');
    assert.equal((await command({mode:'critical_date_undo',effect_id:held.effect_id})).status,'undone');
    assert.equal((await c.query('select due_on::text as due_on from critical_date where id=$1',[result.critical_date_id])).rows[0].due_on,'2026-11-01');
    for (const [body,context,reason] of [
      ['The LOI expiration date is next Friday.',{},'absolute_agreement_required'],
      ['The agreed LOI expiration date is 2026-11-01 at 5pm.',{},'timezone_review_required'],
      ['The agreed LOI expiration date is 2026-11-01.',{body_kind:'preview'},'incomplete_source'],
      ['The agreed LOI expiration date is 2026-11-01.',{attachments:[{id:'synthetic'}]},'attachment_review_required'],
      ['The LOI expiration is cancelled.',{},'agreement_uncertain'],
    ]) {
      const r=await command(await make(body,context));assert.equal(r.status,'review');assert.equal(r.reason,reason);
    }
    const undo={mode:'critical_date_undo',effect_id:result.effect_id};
    await c.query("update critical_date set note='Partner edit' where id=$1",[result.critical_date_id]);
    await assert.rejects(command(undo),e=>e.payload?.error==='version_conflict');
    // A second obligation tests the other DST boundary and a successful undo.
    const spring=await make('The agreed lease commencement date is 2027-03-14.');
    spring.obligation_kind='lease_commencement';
    const applied=await command(spring);assert.equal(applied.status,'applied');
    assert.equal((await command({mode:'critical_date_undo',effect_id:applied.effect_id})).status,'undone');
    assert.equal((await c.query('select status from critical_date where id=$1',[applied.critical_date_id])).rows[0].status,'dropped');
    const cancel=await make('The LOI expiration is cancelled.');
    const cancellation=await command(cancel);
    const currentVersion=(await c.query('select version from critical_date where id=$1',[result.critical_date_id])).rows[0].version;
    assert.equal((await command({mode:'critical_date_review',effect_id:cancellation.effect_id,decision:'cancel',
      critical_date_id:result.critical_date_id,base_version:currentVersion,reason:'Reviewed cancellation'})).status,'applied');
    assert.equal((await c.query('select status from critical_date where id=$1',[result.critical_date_id])).rows[0].status,'dropped');
    await command({mode:'critical_date_undo',effect_id:cancellation.effect_id});
    assert.equal((await c.query('select status from critical_date where id=$1',[result.critical_date_id])).rows[0].status,'open');
    const crash=await make('The agreed lease expiration date is 2027-11-07.');crash.obligation_kind='lease_expiration';
    const crashArgs={idempotency_key:randomUUID(),...crash};
    await c.query('begin');
    await executeRegisteredTool(c,actor,'record-email-proposal',crashArgs);
    await c.query('rollback');
    assert.equal((await c.query("select count(*)::int n from critical_date where deal_id=$1 and kind='lease_expiration'",[deal])).rows[0].n,0);
    const recovered=await invoke('record-email-proposal',crashArgs);
    assert.equal(recovered.status,'applied');
    assert.equal((await command(crash)).effect_id,recovered.effect_id);
    const stale=await make('The agreed lease expiration date is 2027-11-07.');stale.obligation_kind='lease_expiration';
    await c.query("update activity set detail=detail||' Revised.' where id=$1",[stale.activity_id]);
    await assert.rejects(command(stale),e=>e.payload?.error==='email_application_stale');
    const review=await invoke('email-router-queue',{purpose:'critical_dates'});
    assert.ok(review.effects.filter(e=>e.status==='review').length>=5);
    assert.ok((await c.query("select count(*)::int n from event where verb='record-email-proposal' and new_value->>'effect_id'=$1",[result.effect_id])).rows[0].n>=1);
  } finally {await c.end();await fixture.close();}
});
