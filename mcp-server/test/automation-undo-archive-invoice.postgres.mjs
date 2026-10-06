// Synthetic fixtures only; loopback disposable database; transaction rolls back.
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import pg from 'pg';
import {TOOLS} from '../src/tools.js';
const url=process.env.CARR_CI_DATABASE_URL||process.env.DATABASE_URL;
if(!url || !['localhost','127.0.0.1'].includes(new URL(url).hostname))throw Error('disposable_loopback_database_required');
const c=new pg.Client({connectionString:url});await c.connect();let checks=0;
const equal=(a,b)=>{assert.deepEqual(a,b);checks++;};
const actor={id:randomUUID(),slug:'joe',human:true,via:'synthetic-test'};
const writeEvent=async(c,a,verb,type,id,change)=>c.query(`insert into event(occurred_at,recorded_at,actor_id,verb,subject_type,subject_id,field,old_value,new_value,cause)
  values(now(),clock_timestamp(),$1,$2,$3,$4,$5,$6,$7,$8)`,[a.id,verb,type,id,change.field||null,change.old||null,change.new||null,change.cause||'automation_job']);
const call=async(name,args={})=>TOOLS[name].handler(c,actor,{idempotency_key:randomUUID(),...args});
const callInvoice=call;
await c.query('begin');
try {
  await c.query("insert into actor(id,slug,kind,display_name)values($1,$2,'human','Synthetic Partner')",[actor.id,'synthetic-'+actor.id]);
  const party=randomUUID(),lead=randomUUID(),ref='L-SYNTH-'+randomUUID();
  await c.query("insert into party(id,kind,name,email,created_by,updated_by)values($1,'person','Synthetic Practice','contact@example.test',$2,$2)",[party,actor.id]);
  await c.query(`insert into lead(id,registry_ref,party_id,stage,event_confidence,est_lease_event,created_at,created_by,updated_by)
    values($1,$2,$3,'new','high',current_date+90,now()-interval '7 days',$4,$4)`,[lead,ref,party,actor.id]);
  const now=(await c.query('select now() as now')).rows[0].now;
  const contact={lead:ref,native_ref:'local-mail:synthetic-reply',counterparty_address:'contact@example.test',kind:'email_in',occurred_at:new Date(now-3600000).toISOString(),automated:false};
  await call('record-lead-contact',contact);const result=await call('advance-leads');const move=result.moves.find(m=>m.lead_id===lead);
  assert.match(move.reason,/Reply received \d{4}-\d{2}-\d{2}/);checks++;
  const board=await TOOLS['lead-board'].handler(c);const read=board.leads.find(l=>l.id===lead);
  equal(read.stage_moves[0].reason,move.reason);equal(read.stage_moves[0].evidence_ref,contact.native_ref);
  await assert.rejects(()=>call('undo-lead-move',{move_id:move.move_id,base_version:read.base_version-1}),/stale/);checks++;
  const undoArgs={idempotency_key:randomUUID(),move_id:move.move_id,base_version:read.base_version};
  const undone=await call('undo-lead-move',undoArgs);
  equal((await call('undo-lead-move',undoArgs)).replayed,true);equal(undone.stage,'new');equal(undone.undone_by,actor.id);
  equal((await c.query("select new_value->>'undone_by' as who from event where subject_id=$1 and verb='undo-lead-move'",[lead])).rows[0].who,actor.id);
  equal((await call('advance-leads')).moves.filter(m=>m.lead_id===lead),[]);
  await assert.rejects(()=>call('undo-lead-move',{move_id:move.move_id,base_version:read.base_version}),/stale/);checks++;
  // A partner's later stage work, including a same-value edit, protects the row.
  const fresh=await call('record-lead-contact',{...contact,native_ref:'local-mail:synthetic-fresh',occurred_at:now.toISOString()});
  const freshMove=(await call('advance-leads')).moves.find(m=>m.activity_id===fresh.activity_id);
  await writeEvent(c,actor,'update-lead','lead',lead,{field:'stage',new:{stage:'qualified'},old:{stage:'qualified'}});
  const version=(await c.query('select version from lead where id=$1',[lead])).rows[0].version;
  await assert.rejects(()=>call('undo-lead-move',{move_id:freshMove.move_id,base_version:version}),/newer_stage/);checks++;
  await c.query("update lead set stage='nurture_drip' where id=$1",[lead]);
  for(const archive_reason of ['retired','sold_to_platform','another_broker'])await call('record-lead-contact',{...contact,native_ref:'local-mail:synthetic-'+archive_reason,lead_stage_signal:'archived',archive_reason});
  const before=await TOOLS['lead-board'].handler(c);
  const dry=await call('lead-stage-preview');const archive=dry.moves.find(m=>m.lead_id===lead);equal([archive.to_stage,archive.status],['archived','proposed']);
  await call('advance-leads');equal((await c.query('select stage from lead where id=$1',[lead])).rows[0].stage,'nurture_drip');
  const proposal=(await call('lead-approval-queue')).moves.find(m=>m.lead_id===lead);
  await assert.rejects(()=>TOOLS['approve-lead-move'].handler(c,{...actor,human:false},{idempotency_key:randomUUID(),move_id:proposal.id,base_version:proposal.base_version}),/human_approval/);checks++;
  await call('approve-lead-move',{move_id:proposal.id,base_version:proposal.base_version});
  const after=await TOOLS['lead-board'].handler(c);equal(after.metrics.nurture_count,before.metrics.nurture_count-1);equal(after.metrics.conversion_denominator,before.metrics.conversion_denominator-1);
  equal(after.leads.find(l=>l.id===lead).stage,'archived');
  await assert.rejects(()=>TOOLS['update-lead'].handler(c,{...actor,human:false,slug:'synthetic-agent'},{idempotency_key:randomUUID(),lead:lead,base_version:after.leads.find(l=>l.id===lead).base_version,fields:{stage:'archived'}}),/archive_requires_partner/);checks++;
  await call('undo-lead-move',{move_id:proposal.id,base_version:after.leads.find(l=>l.id===lead).base_version});equal((await TOOLS['lead-board'].handler(c)).metrics.nurture_count,before.metrics.nurture_count);
  // Two-field invoice match uses the existing update-deal verb and restores both fields.
  const client=randomUUID(),deal=randomUUID(),clientRef='C-SYNTH-'+randomUUID();
  await c.query("insert into client(id,party_id,roster_ref,status,created_by,updated_by)values($1,$2,$3,'active_deal',$4,$4)",[client,party,clientRef,actor.id]);
  await c.query("insert into deal(id,client_id,name,deal_type,phase,created_by,updated_by)values($1,$2,'Synthetic Lease','other','legal',$3,$3)",[deal,client,actor.id]);
  const args={native_ref:'local-mail:synthetic-invoice',from_address:'invoices@example.test',deal_name:'Synthetic Lease',client_name:'Synthetic Practice',occurred_at:new Date(now-3600000).toISOString()};
  const captured=await callInvoice('record-deal-invoice',args);const preview=await call('lead-stage-preview');equal(preview.invoice_closes.find(m=>m.invoice_id===captured.invoice_id).status,'applied');
  equal((await c.query('select phase from deal where id=$1',[deal])).rows[0].phase,'legal');
  const close=(await call('advance-leads')).invoice_closes.find(m=>m.invoice_id===captured.invoice_id);equal(close.deal_id,deal);
  equal((await c.query('select phase,to_char(invoiced_on,\'YYYY-MM-DD\') as date from deal where id=$1',[deal])).rows[0],{phase:'closed',date:args.occurred_at.slice(0,10)});
  equal((await c.query("select count(*)::int n from event where subject_id=$1 and verb='update-deal' and field in ('phase','invoiced_on')",[deal])).rows[0].n,2);
  equal((await callInvoice('invoice-close-queue')).moves[0].reason,close.reason);
  const dv=(await c.query('select version from deal where id=$1',[deal])).rows[0].version;
  await assert.rejects(()=>callInvoice('undo-invoice-close',{invoice_id:captured.invoice_id,base_version:dv-1}),/newer_invoice/);checks++;
  const restoreArgs={idempotency_key:randomUUID(),invoice_id:captured.invoice_id,base_version:dv};
  const restored=await callInvoice('undo-invoice-close',restoreArgs);equal((await callInvoice('undo-invoice-close',restoreArgs)).replayed,true);equal(restored.phase,'legal');equal(restored.invoiced_on,null);equal(restored.undone_by,actor.id);
  equal((await call('advance-leads')).invoice_closes,[]);
  const unmatched=await callInvoice('record-deal-invoice',{...args,native_ref:'local-mail:synthetic-name-only',client_name:undefined});
  equal((await call('lead-stage-preview')).invoice_closes.find(m=>m.invoice_id===unmatched.invoice_id).status,'proposed');
  equal((await callInvoice('invoice-close-queue')).proposals.length,1);
  equal((await call('advance-leads')).invoice_closes[0].status,'proposed');
  equal((await c.query('select phase from deal where id=$1',[deal])).rows[0].phase,'legal');
  // A later invoice for the same deal cannot overwrite the first date in one batch.
  const first=await callInvoice('record-deal-invoice',{...args,native_ref:'local-mail:synthetic-batch-first',occurred_at:new Date(now-172800000).toISOString()});
  const second=await callInvoice('record-deal-invoice',{...args,native_ref:'local-mail:synthetic-batch-second',occurred_at:new Date(now-86400000).toISOString()});
  const batch=(await call('advance-leads')).invoice_closes;
  equal(batch.find(m=>m.invoice_id===first.invoice_id).status,'applied');
  equal(batch.find(m=>m.invoice_id===second.invoice_id).status,'proposed');
  equal(batch.find(m=>m.invoice_id===second.invoice_id).needs_confirmation,'Deal has a different invoice date');
  equal((await c.query("select to_char(invoiced_on,'YYYY-MM-DD') date from deal where id=$1",[deal])).rows[0].date,new Date(now-172800000).toISOString().slice(0,10));
  const lastVersion=(await c.query('select version from deal where id=$1',[deal])).rows[0].version;
  await TOOLS['update-deal'].handler(c,actor,{idempotency_key:randomUUID(),deal,base_version:lastVersion,fields:{invoiced_on:new Date(now-172800000).toISOString().slice(0,10)}});
  const changedVersion=(await c.query('select version from deal where id=$1',[deal])).rows[0].version;
  await assert.rejects(()=>callInvoice('undo-invoice-close',{invoice_id:first.invoice_id,base_version:changedVersion}),/newer_invoice/);checks++;
  console.log(`db-gate-proof: W3c — ${checks} synthetic assertions; readable reasons, guarded undo, archive partner decision/counts, two-field invoice match, existing deal verbs and dry-run`);
}finally{await c.query('rollback');await c.end();}
