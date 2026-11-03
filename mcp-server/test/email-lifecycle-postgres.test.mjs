import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import pg from 'pg';
import { acquireDisposablePostgres } from './helpers/disposable-postgres.mjs';
import { executeRegisteredTool } from '../src/tools.js';

test('terminal mail transitions, replay, action refusal, scope and notes', async t => {
  const fixture = await acquireDisposablePostgres({ prefix: 'mail-lifecycle-', snapshot: true });
  const c = new pg.Client(fixture.connection);
  await c.connect();
  t.after(async () => { await c.end(); await fixture.close(); });
  const actor = { ...(await c.query("select id,slug from actor where slug='joe'")).rows[0], human: true };
  await c.query("update actor set email='joe@example.test' where id=$1", [actor.id]);
  await c.query("insert into client_status(slug,label) values('active','Fixture') on conflict do nothing");
  const party = (await c.query("insert into party(kind,name,created_by,updated_by) values('person','Lifecycle fixture',$1,$1) returning id", [actor.id])).rows[0].id;
  const client=(await c.query("insert into client(party_id,status,created_by,updated_by,roster_ref) values($1,'active',$2,$2,'C-999442') returning id", [party,actor.id])).rows[0].id;
  const deal=(await c.query("insert into deal(name,client_id,deal_type,phase,owner,created_by,updated_by) values('Lifecycle deal',$1,'purchase','site_selection','joe',$2,$2) returning id",[client,actor.id])).rows[0].id;
  await c.query("select set_config('carr.acting_actor_slug','joe',false),set_config('carr.verified_human_actor_slug','joe',false)");
  const call = async (verb,args,who=actor,role=null) => {
    await c.query('begin');
    try { if(role) await c.query(`set local role ${role}`); const result=await executeRegisteredTool(c,who,verb,args); await c.query('commit'); return result; }
    catch(e) { await c.query('rollback'); throw e; }
  };
  for (const state of ['deleted','recalled','superseded']) await t.test(state,async () => {
    const identity={mailbox_owner:'joe',mailbox_account:'joe@example.test',item_id:randomUUID()};
    const source=await call('log-activity',{idempotency_key:randomUUID(),ref:'C-999442',kind:'email_in',summary:'Synthetic mail',detail:'Please call.',email_source:{sender_email:'sender@example.test',mailbox_owner:'joe'},email_identity:identity});
    await c.query('update activity set deal_id=$2 where id=$1',[source.activity_id,deal]);
    assert.ok((await call('email-router-queue',{purpose:'links'})).emails.some(e=>e.activity_id===source.activity_id));
    const draft=(await c.query("insert into email_proposal(kind,activity_id,owner,draft_subject,draft_body,status,evidence_quote,confidence,created_by) values('reply_draft',$1,'joe','Reply','Please call.','pending','Please call.',1,$2) returning id",[source.activity_id,actor.id])).rows[0].id;
    assert.ok((await call('read-home-inbox-v3',{})).items.some(i=>i.source_id===draft));
    assert.ok((await call('email-router-queue',{purpose:'summaries',pending_only:false})).emails.some(e=>e.activity_id===source.activity_id));
    if (state==='superseded') {
      await c.query(`update email_proposal set status='approved_to_send',resolved_by=$2,resolved_at=now(),
        submitted_at=now(),submitted_email=$3::jsonb where id=$1`,[draft,actor.id,JSON.stringify({
          subject:'Reply',body:'Please call.',sha256:'sha256:'+'a'.repeat(64),to:['sender@example.test'],cc:['dell@example.test']})]);
      assert.ok((await call('outbox-for-sender',{})).drafts.some(p=>p.proposal_id===draft));
    }
    const proposal=(await c.query("insert into email_proposal(kind,activity_id,owner,task_text,next_date,status,evidence_quote,confidence,created_by,resolved_by,resolved_at) values('next_step',$1,'joe','Call','2026-01-01','confirmed','Please call me.',1,$2,$2,now()) returning id",[source.activity_id,actor.id])).rows[0].id;
    const args={idempotency_key:randomUUID(),email_identity:identity,base_version:0,state,
      observation_id:randomUUID(),observed_at:'2026-01-01T12:00:00Z',method:'outlook_tombstone'};
    const result=await call('record-email-lifecycle',args,actor,'carr_writer');
    assert.equal(result.state,state);
    assert.equal(result.version,1);
    assert.ok(result.action_notes.some(n=>n.proposal_id===proposal));
    assert.equal((await call('record-email-lifecycle',args)).replayed,true);
    assert.equal((await call('record-email-lifecycle',{...args,idempotency_key:randomUUID()})).replayed,true);
    await assert.rejects(call('record-email-lifecycle',{...args,idempotency_key:randomUUID(),observation_id:randomUUID()}),e=>e.payload?.error==='version_conflict');
    await assert.rejects(call('record-email-lifecycle',{...args,idempotency_key:randomUUID(),base_version:1,observation_id:randomUUID()}),e=>e.payload?.error==='email_lifecycle_terminal');
    await assert.rejects(call('log-activity',{idempotency_key:randomUUID(),ref:'C-999442',kind:'email_in',summary:'Retry',email_identity:identity}),e=>e.payload?.error==='email_not_live');
    await assert.rejects(call('preview-email-next-step',{deal:randomUUID(),evidence_quote:'Please call.',activity_id:source.activity_id,text:'Call',next_date:'2026-01-02',owner:'joe'}),e=>e.payload?.error==='email_not_live');
    const stored=(await c.query('select email_context from activity where id=$1',[source.activity_id])).rows[0].email_context.lifecycle;
    assert.equal(stored.state,state);
    assert.equal(stored.observation_id,args.observation_id);
    assert.equal(stored.actor_id,actor.id);
    assert.equal(stored.organization_tenant_id,'carr-internal');
    assert.ok(stored.recorded_at);
    await assert.rejects(call('record-email-lifecycle',{...args,idempotency_key:randomUUID()}, {...actor,slug:'dell'}),e=>e.payload?.error==='email_identity_owner_mismatch');
    await assert.rejects(call('record-email-lifecycle',{...args,idempotency_key:randomUUID(),email_identity:{...identity,mailbox_account:'other@example.test'}}),e=>e.payload?.error==='email_identity_account_mismatch');
    for (const version of ['','-v2','-v3']) assert.ok(!(await call('read-home-inbox'+version,{})).items.some(i=>i.source_id===draft));
    assert.ok(!(await call('outbox-for-sender',{})).drafts.some(p=>p.proposal_id===draft));
    assert.ok(!(await call('email-router-queue',{purpose:'summaries',pending_only:false})).emails.some(e=>e.activity_id===source.activity_id));
    assert.ok(!(await call('list-email-proposals',{status:'confirmed',include_non_deal:true})).proposals.some(p=>p.activity_id===source.activity_id));
    await assert.rejects(c.query('update email_proposal set version=version+1 where id=$1',[proposal]), /email_not_live/);
    await assert.rejects(c.query("insert into email_message_summary(activity_id,source_hash,generator_version) values($1,$2,'fixture')",[source.activity_id,'sha256:'+'a'.repeat(64)]),/email_not_live/);
    await assert.rejects(call('record-email-lifecycle',{...args,idempotency_key:randomUUID(),organization_tenant_id:'other'}),e=>e.payload?.error==='caller_authority_field_forbidden');
    const queue=await call('email-router-queue',{purpose:'links'});
    assert.ok(!queue.emails.some(e=>e.activity_id===source.activity_id));
  });
  await t.test('reader and action enforcement independently rejects terminal source metadata',async st => {
    const source=await call('log-activity',{idempotency_key:randomUUID(),ref:'C-999442',kind:'email_in',summary:'Terminal fixture',detail:'Please call me.'});
    await c.query(`update activity set deal_id=$2,email_context='{"lifecycle":{"state":"deleted"}}' where id=$1`,[source.activity_id,deal]);
    await st.test('link queue excludes terminal sources',async()=>{
      assert.ok(!(await call('email-router-queue',{purpose:'links'})).emails.some(e=>e.activity_id===source.activity_id));
    });
    await st.test('summary queue excludes terminal sources',async()=>{
      assert.ok(!(await call('email-router-queue',{purpose:'summaries',pending_only:false})).emails.some(e=>e.activity_id===source.activity_id));
    });
    await st.test('follow-up preview refuses terminal sources',async()=>{
      await assert.rejects(call('preview-email-next-step',{deal,activity_id:source.activity_id,evidence_quote:'Please call me.',text:'Call',next_date:'2026-01-02',owner:'joe'}),e=>e.payload?.error==='email_not_live');
    });
  });
  await t.test('same item ID in another mailbox remains live; concurrent observation replay converges',async () => {
    const dell={...(await c.query("select id,slug from actor where slug='dell'")).rows[0],human:true};
    await c.query("update actor set email='dell@example.test' where id=$1",[dell.id]);
    const item_id=randomUUID();
    const identity={mailbox_owner:'joe',mailbox_account:'joe@example.test',item_id};
    const source=await call('log-activity',{idempotency_key:randomUUID(),ref:'C-999442',kind:'email_in',summary:'Concurrent source',email_identity:identity});
    const otherIdentity={mailbox_owner:'dell',mailbox_account:'dell@example.test',item_id};
    const other=await call('log-activity',{idempotency_key:randomUUID(),ref:'C-999442',kind:'email_in',summary:'Other mailbox',email_identity:otherIdentity},dell);
    const args={idempotency_key:randomUUID(),email_identity:identity,base_version:0,state:'deleted',observation_id:randomUUID(),observed_at:'2026-01-01T12:00:00Z',method:'outlook_tombstone'};
    const second=new pg.Client(fixture.connection);
    await second.connect();
    try {
      await c.query('begin');
      const first=await executeRegisteredTool(c,actor,'record-email-lifecycle',args);
      await second.query('begin');
      const replay=executeRegisteredTool(second,actor,'record-email-lifecycle',{...args,idempotency_key:randomUUID()});
      await c.query('commit');
      assert.equal((await replay).replayed,true);
      await second.query('commit');
      assert.equal(first.version,1);
      assert.equal((await c.query("select count(*)::int as n from event where verb='record-email-lifecycle' and subject_id=$1",[source.activity_id])).rows[0].n,1);
      assert.equal((await c.query('select email_context from activity where id=$1',[other.activity_id])).rows[0].email_context,null);
      assert.equal((await call('log-activity',{idempotency_key:randomUUID(),ref:'C-999442',kind:'email_in',summary:'Other retry',email_identity:otherIdentity},dell)).activity_id,other.activity_id);
    } finally { await c.query('rollback'); await second.query('rollback'); await second.end(); }
  });
});
