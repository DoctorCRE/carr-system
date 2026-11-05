import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { executeRegisteredTool } from '../src/tools.js';

test('durable filing generations survive horizons and reject stale claims', async () => {
  const fixture = await acquireDisposablePostgres({ prefix: 'filing-application-', snapshot: true });
  const c = new pg.Client(fixture.connection);
  await c.connect();
  try {
    const actor = { ...(await c.query("select id,slug from actor where slug='joe'")).rows[0], human: true };
    const party = (await c.query("insert into party(kind,name,email,created_by,updated_by) values('person','Synthetic filing','fixture@example.test',$1,$1) returning id", [actor.id])).rows[0].id;
    const client = (await c.query("insert into client(party_id,created_by,updated_by) values($1,$2,$2) returning id", [party,actor.id])).rows[0].id;
    const deal = (await c.query("insert into deal(name,client_id,deal_type,phase,owner,created_by,updated_by) values('Synthetic filing',$1,'purchase','site_selection','joe',$2,$2) returning id", [client,actor.id])).rows[0].id;
    const command = async (verb,args,db=c) => {
      await db.query('begin');
      try { const r = await executeRegisteredTool(db,actor,verb,args); await db.query('commit'); return r; }
      catch(e) { await db.query('rollback'); throw e; }
    };
    const activity = (await c.query(`insert into activity(kind,summary,detail,client_id,actor_id,updated_by,occurred_at,recorded_at,email_context)
      values('email_in','Synthetic filing','Please review the proposed terms.',$1,$2,$2,now()-interval '60 days',now()-interval '60 days',
      '{"sender":{"email":"fixture@example.test"},"recipients":[]}') returning id`, [client,actor.id])).rows[0].id;
    const proposed = await command('record-email-proposal',{idempotency_key:randomUUID(),kind:'file_to_deal',activity_id:activity,deal,
      evidence_quote:'Please review the proposed terms.',confidence:0.9,sender_role:'client'});
    const pending = (await c.query('select * from email_filing_application where activity_id=$1',[activity])).rows;
    assert.equal(pending.length,1);
    assert.equal(pending[0].status,'review');
    const preview = await command('preview-email-filing',{proposal_id:proposed.proposal_id,deal});
    await command('confirm-email-filing',{idempotency_key:randomUUID(),proposal_id:proposed.proposal_id,base_version:1,deal,proposal_digest:preview.proposal_digest});
    const queue = () => command('email-router-queue',{purpose:'applications',limit:1});
    const first = (await queue()).emails[0];
    assert.equal(first.activity_id,activity);
    assert.equal(first.mailbox_owner,actor.slug);
    const mode = (mode,extra={}) => command('record-email-proposal',{idempotency_key:randomUUID(),mode,activity_id:activity,generation:first.generation,...extra});
    const competing = new pg.Client(fixture.connection);
    await competing.connect();
    let claim;
    try {
      const args = {mode:'application_claim',activity_id:activity,generation:first.generation};
      const race = await Promise.all([command('record-email-proposal',{...args,idempotency_key:randomUUID()}),
        command('record-email-proposal',{...args,idempotency_key:randomUUID()},competing)]);
      assert.equal(race.filter(r=>r.generate).length,1);
      claim = race.find(r=>r.generate);
    } finally { await competing.end(); }
    assert.equal((await mode('application_claim')).generate,false);
    await c.query('update activity set version=version+1 where id=$1',[activity]);
    assert.equal((await queue()).emails.length,0);
    await mode('application_result',{claim_id:claim.claim_id,application_status:'applied'});
    assert.equal((await queue()).emails.length,0);
    await c.query("update activity set detail=detail||' New captured evidence.' where id=$1",[activity]);
    const revised = (await queue()).emails[0];
    assert.notEqual(revised.generation,first.generation);
    await assert.rejects(mode('application_result',{claim_id:claim.claim_id,application_status:'applied'}),e=>e.payload?.error==='email_application_stale');
    // A dead worker may be reclaimed, but its old lease cannot acknowledge the new claim.
    const forGeneration = (mode,generation,extra={}) => command('record-email-proposal',
      {idempotency_key:randomUUID(),mode,activity_id:activity,generation,...extra});
    const secondClaim = await forGeneration('application_claim',revised.generation);
    await c.query("update email_filing_application set claim_until=now()-interval '1 second' where generation=$1",[revised.generation]);
    const recovery = await forGeneration('application_claim',revised.generation);
    assert.notEqual(recovery.claim_id,secondClaim.claim_id);
    await assert.rejects(forGeneration('application_result',revised.generation,
      {claim_id:secondClaim.claim_id,application_status:'applied'}),e=>e.payload?.error==='email_application_stale');
    assert.equal((await forGeneration('application_result',revised.generation,
      {claim_id:recovery.claim_id,application_status:'retry'})).status,'pending');
    const last = await forGeneration('application_claim',revised.generation);
    assert.equal((await forGeneration('application_result',revised.generation,
      {claim_id:last.claim_id,application_status:'retry'})).status,'exhausted');
    assert.equal((await queue()).emails.length,0);
    await c.query("update activity set detail=detail||' More new evidence.' where id=$1",[activity]);
    const owned = (await queue()).emails[0];
    const other = {...actor,slug:'dell',id:randomUUID()};
    assert.equal((await executeRegisteredTool(c,other,'email-router-queue',{purpose:'applications'})).emails.length,0);
    await c.query("update deal set phase='closed' where id=$1",[deal]);
    assert.equal((await queue()).emails.length,0);
    await assert.rejects(forGeneration('application_claim',owned.generation),e=>e.payload?.error==='email_application_stale');
    await c.query("update deal set phase='site_selection' where id=$1",[deal]);
    await c.query('begin');
    await c.query("select public.enqueue_email_filing($1,$2,'rolled-back','confirmed')",[activity,deal]);
    await c.query('rollback');
    assert.equal((await queue()).emails[0].generation,owned.generation);
    const inFlight = await forGeneration('application_claim',owned.generation);
    await c.query("update activity set detail=detail||' Revision during extraction.' where id=$1",[activity]);
    await assert.rejects(forGeneration('application_result',owned.generation,
      {claim_id:inFlight.claim_id,application_status:'applied'}),e=>e.payload?.error==='email_application_stale');
    await assert.rejects(command('record-email-proposal',{idempotency_key:randomUUID(),kind:'next_step',
      activity_id:activity,deal,generation:owned.generation,claim_id:inFlight.claim_id,
      evidence_quote:'Please review the proposed terms.',confidence:0.9,text:'Review terms',
      next_date:'2026-12-01',owner:'joe'}),e=>e.payload?.error==='email_application_stale');
    const replacement = (await queue()).emails[0];
    assert.notEqual(replacement.generation,owned.generation);
    assert.equal((await c.query('select status from email_filing_application where generation=$1',[owned.generation])).rows[0].status,'needs_review');
    await c.query("update activity set email_context=jsonb_set(email_context,'{lifecycle}','{\"state\":\"deleted\"}') where id=$1",[activity]);
    assert.equal((await queue()).emails.length,0);
    const many = (await c.query(`insert into activity(kind,summary,detail,client_id,deal_id,actor_id,occurred_at,recorded_at)
      select 'email_in','Synthetic page','Synthetic page evidence '||n,$1,$2,$3,
      now()-interval '60 days',now()-interval '60 days' from generate_series(1,30) n returning id`,[client,deal,actor.id])).rows;
    for (const row of many) await c.query("select public.enqueue_email_filing($1,$2,$3,'confirmed')",[row.id,deal,row.id]);
    const page = await command('email-router-queue',{purpose:'applications',limit:200});
    assert.equal(page.emails.length,25);
    assert.equal(page.more,true);
    const nextPage = await command('email-router-queue',{purpose:'applications',after:page.next_cursor,limit:200});
    assert.equal(nextPage.emails.length,5);
    assert.equal(nextPage.more,false);
    assert.equal(new Set([...page.emails,...nextPage.emails].map(e=>e.generation)).size,30);
  } finally { await c.end(); await fixture.close(); }
});
