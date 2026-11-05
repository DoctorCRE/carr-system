// Apply email commitments through canonical task writers and retain guarded correction evidence.
import { automaticEvidenceHold } from './call-auto-apply.js';
import { suspected } from './untrusted-text.js';
import { partnerAuthoritySlugForActor } from './partner-authority.js';
import { derivedIdempotencyKey } from './derived-key.ts';

export const completeEmailBody = context => context?.body_kind === 'full'
  && context.body_completeness === 'complete' && context.body_outcome === 'complete' && !context.body_truncated;

export function emailCommitmentHold(source, row) {
  if (!completeEmailBody(source.email_context)) return 'incomplete_source';
  if (suspected(source.detail) || suspected(row.evidence_quote)) return 'suspected_instruction';
  if (!source.detail?.includes(row.evidence_quote)) return 'source_revision';
  if (!source.detail.trimStart().startsWith(row.evidence_quote.trim()) || /(?:^|\n)(?:From:|Sent:|On .+wrote:|[- ]*Forwarded message)/i.test(source.detail)) return 'uncertain_attribution';
  if (automaticEvidenceHold({}, row.evidence_quote)) return 'unclear_evidence';
  if (!row.next_date || !row.evidence_quote.includes(row.next_date)) return 'missing_literal_date';
  if (source.kind !== 'email_out' || row.owner !== source.mailbox_owner) return 'uncertain_attribution';
  if (/\b(?:not|never|cannot|might|maybe|if|unless|could|would|cancel(?:led)?|correction)\b/i.test(source.detail)) return 'conflicting_or_conditional';
  if (Number(row.confidence) < 0.95 || /\?/.test(row.evidence_quote)
      || !/^I (?:will|shall|commit to)\s/i.test(row.evidence_quote.trim())) return 'unclear_commitment';
  const literalWords = new Set(row.evidence_quote.toLowerCase().match(/[a-z]+/g));
  if ((row.task_text.toLowerCase().match(/[a-z]+/g) ?? []).some(word => !literalWords.has(word))) return 'unclear_task';
  return null;
}

export async function emailStepQueue(c, actor, args) {
  const rows = (await c.query(`select p.id as proposal_id from email_proposal p join activity a on a.id=p.activity_id
    where p.kind='next_step' and p.status='pending' and coalesce(a.email_context#>>'{lifecycle,state}','live')='live' and not(coalesce(p.correction_provenance,'{}') ? 'email_application')
      and (a.email_source->>'mailbox_owner'=$1 or (a.email_source is null and a.actor_id=$2))
    order by p.created_at,p.id limit $3`,[partnerAuthoritySlugForActor(actor),actor.id,Math.min(args.limit ?? 25,25)])).rows;
  return { proposals:rows };
}

export async function emailStepApplication(c, actor, args, { fail, preview, write, undoPlan, writeEvent }) {
  if (!args.proposal_id || Object.keys(args).some(key => !['mode','proposal_id','idempotency_key',
    ...(args.mode === 'undo_next_step' ? ['base_version'] : [])].includes(key))) fail('email_proposal_fields_invalid');
  // Source before proposal is also the filing consumer's lock order.
  const id = (await c.query('select activity_id from email_proposal where id=$1',[args.proposal_id])).rows[0]?.activity_id;
  if (!id) fail('email_proposal_not_pending');
  const source = (await c.query(`select a.*,public.email_filing_source_hash(a) as source_hash,
    coalesce(a.email_source->>'mailbox_owner',o.slug) as mailbox_owner
    from activity a join actor o on o.id=a.actor_id where a.id=$1 for update of a`,[id])).rows[0];
  if (source.mailbox_owner !== partnerAuthoritySlugForActor(actor)) fail('email_proposal_source_missing');
  const row = (await c.query('select *,next_date::text as next_date from email_proposal where id=$1 for update',[args.proposal_id])).rows[0];
  if (row.kind !== 'next_step') fail('email_proposal_kind_invalid');
  const saved = row.correction_provenance?.email_application;
  const subjectType = saved?.subject_type ?? (row.deal_id ? 'deal' : null);
  const save = async receipt => {
    receipt = {...receipt, source_version:source.version, generation:row.correction_provenance?.generation ?? null};
    await c.query(`update email_proposal set correction_provenance=coalesce(correction_provenance,'{}') ||
      jsonb_build_object('email_application',$2::jsonb),version=version+1 where id=$1`,[row.id,JSON.stringify(receipt)]);
    await writeEvent(c,actor,'record-email-proposal',receipt.subject_type ?? 'activity',receipt.subject_id ?? id,
      {field:'email_application',new:{proposal_id:row.id,activity_id:id,...receipt},cause:'observed',idempotency_key:args.idempotency_key});
    return {ok:true,proposal_id:row.id,email_application:receipt};
  };
  if (args.mode === 'undo_next_step') {
    if (!['joe','dell'].includes(actor.slug) || actor.human !== true) fail('partner_required');
    if (!saved || saved.state !== 'applied') fail('not_applied_automatically');
    if (subjectType === 'deal') {
      const deal = (await c.query('select version from deal where id=$1 for update',[saved.subject_id])).rows[0];
      if (deal?.version !== args.base_version || deal.version !== saved.subject_version) fail('newer_change_exists');
    }
    const action = (await c.query('select * from next_action where id=$1 for update',[saved.action_id])).rows[0];
    if (!action || action.status !== 'open' || action.version !== saved.action_version) fail('newer_change_exists');
    if (saved.writer === 'set-next-step') {
      await undoPlan(c,actor,saved.subject_id,{id:row.id,payload:{text:row.task_text,next_date:row.next_date}},[saved.write_key],args,'record-email-proposal');
    } else {
      await c.query("update next_action set status='dropped',updated_by=$2,updated_at=clock_timestamp() where id=$1",[saved.action_id,actor.id]);
      if (subjectType === 'deal') await c.query('update deal set next_date=$2,updated_by=$3 where id=$1',[saved.subject_id,saved.prior_date,actor.id]);
    }
    return save({...saved,state:'undone'});
  }
  if (saved) return {ok:true,proposal_id:row.id,email_application:saved,duplicate_suppressed:true};
  if (row.status !== 'pending') fail('email_proposal_not_pending');
  const hold = reason => save({state:'review',reason,source_hash:source.source_hash});
  if (row.correction_provenance?.email_source_hash !== source.source_hash) return hold('source_revision');
  if (source.actor_id !== actor.id) return hold('uncertain_attribution');
  const evidenceHold = emailCommitmentHold(source,row);
  if (evidenceHold) return hold(evidenceHold);
  if (row.deal_id !== source.deal_id) return hold('linkage_changed');
  if (row.deal_id) {
    const authority = (await c.query(`select 1 from email_filing_application where activity_id=$1 and deal_id=$2
      and source_hash=$3 and authority in ('confirmed','inherited') and status not in ('suppressed','needs_review') limit 1`,
    [id,row.deal_id,source.source_hash])).rows.length;
    if (!authority) return hold('unverified_filing');
  }
  let current;
  try {
    current = await preview(c,actor,'preview-email-next-step',{activity_id:id,deal:row.deal_id,
      text:row.task_text,next_date:row.next_date,owner:row.owner,evidence_quote:row.evidence_quote});
  } catch (error) {
    if (['email_next_step_evidence_not_found','email_next_step_input_invalid','email_next_step_quote_invalid'].includes(error.payload?.error))
      return hold(error.payload.error);
    throw error;
  }
  const type = current.subject_type ?? 'deal', subject = current.subject_id ?? current.deal;
  // Locks match canonical writers. Automatic evidence never replaces an existing ordinary ball.
  if (type === 'deal') await c.query('select id from deal where id=$1 for update',[subject]);
  else await c.query(`select id from ${type} where id=$1 for update`,[subject]);
  const open = (await c.query(`select 1 from next_action where subject_type=$1 and subject_id=$2 and status='open'
    and item_title is null union all select 1 from capture_post_call_action where $1='deal' and deal_id=$2
    and status='open' and item_title is null limit 1`,[type,subject])).rows.length;
  if (open || current.current_next_step || current.current_next_date) return hold('existing_plan');
  const newer = (await c.query(`select 1 from event where subject_type=$1 and subject_id=$2
    and recorded_at>$3 and (field in ('next_step','next_date','next_action')
      or verb in ('set-next-action','complete-action','complete-one-action')) limit 1`,[type,subject,source.occurred_at])).rows.length;
  if (newer) return hold('newer_partner_work');
  const writer = type === 'deal' && row.owner === 'joe' ? 'set-next-step' : 'set-next-action';
  const key = derivedIdempotencyKey('email-commitment',row.id);
  const result = await write(c,actor,writer,writer === 'set-next-step'
    ? {idempotency_key:key,deal:subject,base_version:current.base_version,text:row.task_text,next_date:row.next_date}
    : {idempotency_key:key,ref:subject,owner:row.owner,description:row.task_text,due_on:row.next_date,
      ...(type === 'deal' ? {} : {expected_action:null})});
  const action = (await c.query('select version from next_action where id=$1',[result.next_action_id])).rows[0];
  const version = type === 'deal' ? (await c.query('select version from deal where id=$1',[subject])).rows[0].version : null;
  await c.query("update email_proposal set status='confirmed',resolved_at=now(),resolved_by=$2 where id=$1",[row.id,actor.id]);
  return save({state:'applied',source_hash:source.source_hash,subject_type:type,subject_id:subject,
    owner:row.owner,due_on:row.next_date,writer,write_key:key,action_id:result.next_action_id,
    action_version:action.version,subject_version:version,prior_date:current.current_next_date ?? null});
}
