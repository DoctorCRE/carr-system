// Synthetic fixtures only. Run against the disposable migration database.
import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import pg from 'pg';
import { leadAutomationTools, nextMorning } from '../src/lead-automation.js';
const url = process.env.CARR_CI_DATABASE_URL || process.env.DATABASE_URL;
if (!url || !['localhost','127.0.0.1'].includes(new URL(url).hostname)) throw new Error('disposable_loopback_database_required');
const c = new pg.Client({connectionString:url});
await c.connect();
let checks=0;
const equal=(a,b)=>{assert.deepEqual(a,b);checks++;};
const events=[];
const tools=leadAutomationTools({invoices:{preview:async()=>[],apply:async()=>[]},withEnvelope:async(_c,_a,_v,_args,f)=>f(),
  writeEvent:async(db,actor,verb,type,id,change)=>{
    events.push(change);
    await db.query(`insert into event(occurred_at,actor_id,verb,subject_type,subject_id,field,old_value,new_value,cause)
      values(now(),$1,$2,$3,$4,$5,$6,$7,$8)`,[actor.id,verb,type,id,change.field||null,change.old||null,change.new||null,change.cause||'human_stated']);
  },ToolError:class extends Error{constructor(v){super(v.error);}}});
await c.query('begin');
try {
  equal((await c.query(`select count(*)::int n from pg_trigger where tgrelid in ('public.lead_stage_move'::regclass,'public.lead_contact_draft'::regclass) and not tgisinternal and tgfoid='ops.scac_reference_monitor_guard()'::regprocedure`)).rows[0].n,4);
  const actor={id:randomUUID(),human:true};
  const party=randomUUID(),lead=randomUUID(),ref='L-SYNTH-'+randomUUID();
  await c.query("insert into actor(id,slug,kind,display_name)values($1,$2,'human','Synthetic Reviewer')",[actor.id,'synthetic-'+actor.id]);
  await c.query("insert into party(id,kind,name,email,created_by,updated_by)values($1,'person','Synthetic Contact','contact@example.test',$2,$2)",[party,actor.id]);
  await c.query(`insert into lead(id,registry_ref,party_id,stage,event_confidence,est_lease_event,score,created_at,created_by,updated_by)
    values($1,$2,$3,'new','high',current_date+90,80,now()-interval '7 days',$4,$4)`,[lead,ref,party,actor.id]);
  const now=(await c.query('select now() as now')).rows[0].now;
  const contact={lead:ref,native_ref:'local-mail:synthetic-'+randomUUID(),counterparty_address:'contact@example.test',kind:'email_in',occurred_at:new Date(now-3600000).toISOString(),automated:false};
  equal((await tools['record-lead-contact'].handler(c,actor,contact)).match,'exact');
  const before=(await c.query('select stage,version from lead where id=$1',[lead])).rows[0];
  equal((await tools['lead-stage-preview'].handler(c)).moves.find(m=>m.lead_id===lead).status,'applied');
  equal((await c.query('select stage,version from lead where id=$1',[lead])).rows[0],before);
  await tools['advance-leads'].handler(c,actor,{time_zone:'America/Chicago'});
  equal((await c.query('select stage from lead where id=$1',[lead])).rows[0].stage,'qualified');
  const draft=(await c.query('select * from lead_contact_draft where lead_id=$1',[lead])).rows[0];
  equal(draft.scheduled_for.toISOString(),nextMorning(now,'America/Chicago'));
  equal([draft.requires_human_send,draft.dispatchable],[true,false]);
  await tools['advance-leads'].handler(c,actor,{});
  equal((await c.query('select count(*)::int n from lead_contact_draft where lead_id=$1',[lead])).rows[0].n,1);
  equal((await tools['lead-approval-queue'].handler(c)).drafts.find(d=>d.id===draft.id).party_id,party);
  await assert.rejects(()=>tools['approve-lead-draft'].handler(c,{...actor,human:false},{draft_id:draft.id}),/human_approval/);checks++;
  equal((await tools['approve-lead-draft'].handler(c,actor,{draft_id:draft.id})).sent,false);
  equal((await c.query('select stage from lead where id=$1',[lead])).rows[0].stage,'qualified');
  // The clock is fixed by this transaction. Make the already-approved synthetic
  // draft due, then capture its exact outgoing content. No provider is invoked.
  await c.query("update lead_contact_draft set scheduled_for=now()-interval '1 minute' where id=$1",[draft.id]);
  await tools['record-lead-contact'].handler(c,actor,{...contact,native_ref:'local-mail:synthetic-sent-'+randomUUID(),kind:'email_out',occurred_at:now.toISOString(),first_contact_draft_id:draft.id,draft_body_sha256:createHash('sha256').update(draft.body).digest('hex')});
  await tools['advance-leads'].handler(c,actor,{});
  equal((await c.query('select stage from lead where id=$1',[lead])).rows[0].stage,'outreach_active');
  equal((await c.query("select count(*)::int n from event where subject_id=$1 and field='stage' and new_value ? 'evidence_ref'",[lead])).rows[0].n,2);
  // A whole captured batch must advance over repeated jobs using occurrence
  // ordering, even though every stage event is recorded later by this job.
  const batchLead=randomUUID(),batchRef='L-SYNTH-'+randomUUID();
  await c.query(`insert into lead(id,registry_ref,party_id,stage,event_confidence,est_lease_event,created_at,created_by,updated_by)
    values($1,$2,$3,'qualified','high',current_date+90,now()-interval '7 days',$4,$4)`,[batchLead,batchRef,party,actor.id]);
  const batchDraft=(await c.query(`insert into lead_contact_draft(lead_id,party_id,subject,body,scheduled_for,time_zone,created_by,approved_by,approved_at)
    values($1,$2,'Synthetic','Synthetic batch body',now()-interval '4 hours','America/Chicago',$3,$3,now()-interval '4 hours') returning *`,[batchLead,party,actor.id])).rows[0];
  await tools['record-lead-contact'].handler(c,actor,{...contact,lead:batchRef,native_ref:'local-mail:batch-sent',kind:'email_out',occurred_at:new Date(now-10800000).toISOString(),first_contact_draft_id:batchDraft.id,draft_body_sha256:createHash('sha256').update(batchDraft.body).digest('hex')});
  await tools['record-lead-contact'].handler(c,actor,{...contact,lead:batchRef,native_ref:'local-mail:batch-reply',occurred_at:new Date(now-7200000).toISOString()});
  await tools['record-lead-contact'].handler(c,actor,{...contact,lead:batchRef,native_ref:'local-calendar:batch-tour',kind:'tour',occurred_at:new Date(now-3600000).toISOString(),ended_at:new Date(now-1800000).toISOString(),attended:true,lead_stage_signal:'opportunity'});
  for(const stage of ['outreach_active','engaged','opportunity']) {
    await tools['advance-leads'].handler(c,actor,{});
    equal((await c.query('select stage from lead where id=$1',[batchLead])).rows[0].stage,stage);
  }
  // A zero-row update cannot create applied provenance or a returned effect.
  const rejectedLead=randomUUID(),rejectedRef='L-SYNTH-'+randomUUID();
  await c.query(`insert into lead(id,registry_ref,party_id,stage,event_confidence,est_lease_event,created_at,created_by,updated_by)
    values($1,$2,$3,'new','high',current_date+90,now()-interval '7 days',$4,$4)`,[rejectedLead,rejectedRef,party,actor.id]);
  await tools['record-lead-contact'].handler(c,actor,{...contact,lead:rejectedRef,native_ref:'local-mail:rejected-stage'});
  await c.query(`create function pg_temp.reject_stage() returns trigger language plpgsql as $$begin return null; end$$`);
  await c.query(`create trigger synthetic_reject_stage before update on lead for each row when (old.id='${rejectedLead}'::uuid) execute function pg_temp.reject_stage()`);
  const rejected=await tools['advance-leads'].handler(c,actor,{});
  equal(rejected.moves.filter(m=>m.lead_id===rejectedLead),[]);
  equal((await c.query("select count(*)::int n from lead_stage_move where lead_id=$1 and status='applied'",[rejectedLead])).rows[0].n,0);
  equal((await c.query("select count(*)::int n from event where subject_id=$1 and field='stage'",[rejectedLead])).rows[0].n,0);
  await c.query('drop trigger synthetic_reject_stage on lead');
  await c.query('update lead set suppressed=true where id=$1',[rejectedLead]);
  // Human reset: the previously applied evidence must not claim a second move.
  await c.query("update lead set stage='new' where id=$1",[lead]);
  const conflict=await tools['advance-leads'].handler(c,actor,{});
  equal(conflict.moves.filter(m=>m.lead_id===lead),[]);
  equal((await c.query('select stage from lead where id=$1',[lead])).rows[0].stage,'new');
  // A later exact reply must still qualify a reset lead. The oldest reply has
  // already supplied this transition and must not starve the fresh candidate.
  const fresh=await tools['record-lead-contact'].handler(c,actor,{...contact,
    native_ref:'local-mail:reset-fresh-reply',occurred_at:now.toISOString()});
  const freshPreview=(await tools['lead-stage-preview'].handler(c)).moves.filter(m=>m.lead_id===lead);
  equal(freshPreview.map(m=>m.activity_id),[fresh.activity_id]);
  const freshDryRun=await tools['advance-leads'].handler(c,actor,{dry_run:true});
  equal(freshDryRun.moves.filter(m=>m.lead_id===lead),freshPreview);
  equal((await c.query('select stage from lead where id=$1',[lead])).rows[0].stage,'new');
  const freshAdvance=await tools['advance-leads'].handler(c,actor,{});
  equal(freshAdvance.moves.filter(m=>m.lead_id===lead).map(({move_id,...m})=>m),freshPreview);
  equal((await c.query('select stage from lead where id=$1',[lead])).rows[0].stage,'qualified');
  equal((await c.query("select count(*)::int n from lead_stage_move where lead_id=$1 and from_stage='new' and to_stage='qualified' and status='applied'",[lead])).rows[0].n,2);
  await c.query("update lead set stage='new' where id=$1",[lead]);
  equal((await tools['advance-leads'].handler(c,actor,{})).moves.filter(m=>m.lead_id===lead),[]);
  equal((await c.query('select stage from lead where id=$1',[lead])).rows[0].stage,'new');
  // A weak proposal must not consume the evidence forever. The same local
  // contact becomes sufficient once the lease event is verified.
  const weakLead=randomUUID(),weakRef='L-SYNTH-'+randomUUID();
  await c.query(`insert into lead(id,registry_ref,party_id,stage,event_confidence,est_lease_event,created_at,created_by,updated_by)
    values($1,$2,$3,'new','low',current_date+90,now()-interval '7 days',$4,$4)`,[weakLead,weakRef,party,actor.id]);
  await tools['record-lead-contact'].handler(c,actor,{...contact,lead:weakRef,native_ref:'local-mail:synthetic-weak-'+randomUUID()});
  await tools['advance-leads'].handler(c,actor,{});
  equal((await c.query('select stage from lead where id=$1',[weakLead])).rows[0].stage,'new');
  equal((await c.query('select status from lead_stage_move where lead_id=$1',[weakLead])).rows[0].status,'proposed');
  await c.query("update lead set event_confidence='high' where id=$1",[weakLead]);
  await tools['advance-leads'].handler(c,actor,{});
  equal((await c.query('select stage from lead where id=$1',[weakLead])).rows[0].stage,'qualified');
  equal((await c.query('select count(*)::int n from lead_stage_move where lead_id=$1',[weakLead])).rows[0].n,1);
  // Existing outbound history and suppressed party state each prevent a draft.
  await c.query("update lead set stage='qualified' where id=$1",[lead]);
  const second=randomUUID();
  await c.query("insert into lead(id,registry_ref,party_id,stage,created_by,updated_by) values($1,$2,$3,'qualified',$4,$4)",[second,'L-SYNTH-'+randomUUID(),party,actor.id]);
  await c.query("update party set contact_state='do_not_contact' where id=$1",[party]);
  await tools['advance-leads'].handler(c,actor,{});
  equal((await c.query('select count(*)::int n from lead_contact_draft where lead_id=$1',[second])).rows[0].n,0);
  equal((await tools['lead-approval-queue'].handler(c)).drafts.some(d=>d.lead_id===second),false);
  // Exercise the union against real ops tables, including the never-run state.
  const search=await tools['last-new-lead-search'].handler(c);
  equal(search.contract,'lead-automation.v1');
  // Database constraints deny turning the stored draft into a send request.
  await c.query('savepoint invalid_dispatch');
  await assert.rejects(()=>c.query('update lead_contact_draft set dispatchable=true where id=$1',[draft.id]),/check constraint/);checks++;
  await c.query('rollback to savepoint invalid_dispatch');
  console.log(`db-gate-proof: lead automation — ${checks} synthetic assertions; stage provenance, draft-only approval, replay, suppression, dry-run and search SQL`);
} finally {await c.query('rollback');await c.end();}
await import('./lead-automation-concurrency.postgres.mjs');

await import('./automation-undo-archive-invoice.postgres.mjs');
await import('./invoice-review-regressions.postgres.mjs');
