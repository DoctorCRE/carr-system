import { emailPhaseTransitions } from './email-deal-progress.ts';
import { automaticEvidenceHold } from './call-auto-apply.js';
import { suspected } from './untrusted-text.js';
import { derivedIdempotencyKey } from './derived-key.ts';

const uncertain = /\b(?:not|never|no|draft|if|unless|will|would|could|should|please|request|intend|plan|hope|expect|may|might|cancelled|canceled|withdrawn|correction|incorrect|disregard)\b/i;
const affirmative = new Map([
  ['research', /\btour\b[^.!?\n]{0,80}\b(?:is|was|has been) (?:scheduled|confirmed|set)\b/i],
  ['site_selection', /\b(?:received|receipt of)\b[^.!?\n]{0,60}\b(?:LOI|letter of intent)\b|\b(?:LOI|letter of intent)\b[^.!?\n]{0,60}\b(?:was|has been) received\b/i],
  ['negotiation', /\b(?:LOI|letter of intent)\b[^.!?\n]{0,60}\b(?:is|was|has been) (?:signed|executed)\b|\blease\b[^.!?\n]{0,60}\b(?:was|has been) (?:sent|issued|circulated)\b/i],
  ['legal', /\blease\b[^.!?\n]{0,60}\b(?:is|was|has been) (?:signed|executed)\b/i],
]);

export async function applyEmailPhase(c, actor, args, proposal, {lockDealField, updateDeal, writeEvent}) {
  const saved = (await c.query('select correction_provenance from email_proposal where id=$1',[proposal.id])).rows[0];
  if (saved?.correction_provenance?.email_phase?.event_id) return {...saved.correction_provenance.email_phase,replayed:true};
  const result = (status,reason) => ({status,reason,activity_id:args.activity_id,generation:args.generation ?? null});
  const source = (await c.query(`select a.*,public.email_filing_source_hash(a) as source_hash
    from activity a where a.id=$1 for update`,[args.activity_id])).rows[0];
  const context=source?.email_context;
  if (!context || context.body_kind!=='full' || context.body_completeness!=='complete'
      || context.body_outcome!=='complete' || context.body_truncated !== false
      || (context.lifecycle?.state && context.lifecycle.state!=='live')) return result('held','incomplete_or_recalled_source');
  if (source.kind!=='email_in' || source.deal_id!==args.deal || !source.detail?.includes(proposal.evidence_quote))
    return result('held','source_binding_changed');
  if (!args.generation) return result('review','filing_generation_required');
  const identity=source.email_identity;
  const sourceKey=identity ? JSON.stringify([identity.mailbox_owner,identity.mailbox_account,identity.item_id]) : source.id;
  await c.query('select pg_advisory_xact_lock(hashtextextended($1,0))',['email-phase:'+sourceKey]);
  const prior=(await c.query(`select deal_id,correction_provenance->'email_phase' as receipt from email_proposal
    where kind='phase_move' and correction_provenance#>>'{email_phase,source_key}'=$1
      and correction_provenance#>>'{email_phase,status}' in ('applied','undone') limit 1`,[sourceKey])).rows[0];
  if (prior) {
    if (prior.deal_id!==args.deal) return result('review','source_deal_conflict');
    await c.query(`update email_proposal set status='confirmed',resolved_at=now(),resolved_by=$2,
      resolution_reason='duplicate_email_milestone',version=version+1 where id=$1 and status='pending'`,[proposal.id,actor.id]);
    return {...prior.receipt,replayed:true};
  }
  for (const field of ['next_date','next_step','phase']) await lockDealField(c,args.deal,field);
  const deal=(await c.query('select phase,version,deal_type,invoiced_on from deal where id=$1 for update',[args.deal])).rows[0];
  if (!deal || deal.invoiced_on || deal.version!==args.phase_base_version) return result('review','deal_version_changed');
  const transition=emailPhaseTransitions.get(deal.phase);
  const quote=proposal.evidence_quote;
  if (proposal.status!=='pending' || args.evidence_quote!==quote || args.confidence<0.95 || automaticEvidenceHold({},quote)
      || suspected(source.detail) || uncertain.test(source.detail) || !affirmative.get(deal.phase)?.test(quote)
      || !transition?.rules.some(rule => rule.instrument.test(quote) && !rule.excludedInstrument?.test(quote)
        && (!rule.dealTypes || rule.dealTypes.includes(deal.deal_type))
        && rule.additionalEvidence.every(pattern=>pattern.test(quote)))) return result('review','milestone_unclear');
  const key=derivedIdempotencyKey('email-phase',proposal.id);
  await updateDeal(c,actor,{deal:args.deal,base_version:deal.version,fields:{phase:transition.toPhase},idempotency_key:key});
  const event=(await c.query(`select id from event where subject_id=$1 and field='phase' and idempotency_key=$2`,[args.deal,key])).rows[0];
  if (!event) throw new Error('email_phase_event_missing');
  const appliedVersion=(await c.query('select version from deal where id=$1',[args.deal])).rows[0].version;
  const receipt={status:'applied',source_key:sourceKey,source_hash:source.source_hash,generation:args.generation,
    activity_id:source.id,evidence_quote:quote,from_phase:deal.phase,to_phase:transition.toPhase,event_id:event.id,
    applied_version:appliedVersion};
  await c.query(`update email_proposal set status='confirmed',resolved_at=now(),resolved_by=$2,
    resolution_reason='automatic_email_milestone',version=version+1,
    correction_provenance=coalesce(correction_provenance,'{}'::jsonb)||jsonb_build_object('email_phase',$3::jsonb) where id=$1`,
    [proposal.id,actor.id,JSON.stringify(receipt)]);
  await writeEvent(c,actor,'record-email-proposal','deal',args.deal,{new:receipt,cause:'observed',idempotency_key:args.idempotency_key});
  return receipt;
}

export async function undoEmailPhase(c,actor,args,{lockDealField,revertDeal,fail}) {
  const initial=(await c.query('select activity_id from email_proposal where id=$1',[args.proposal_id])).rows[0];
  if (!initial) fail('email_deal_application_not_found');
  await c.query('select id from activity where id=$1 for update',[initial.activity_id]);
  const proposal=(await c.query('select * from email_proposal where id=$1 for update',[args.proposal_id])).rows[0];
  const receipt=proposal?.correction_provenance?.email_phase;
  if (!receipt?.event_id) fail('email_deal_application_not_found');
  const source=(await c.query(`select public.email_filing_source_hash(a) as source_hash from activity a
    where a.id=$1 and coalesce(a.email_context#>>'{lifecycle,state}','live')='live' for update`,[proposal.activity_id])).rows[0];
  if (source?.source_hash!==receipt.source_hash) fail('email_deal_proposal_stale');
  for (const field of ['next_date','next_step','phase']) await lockDealField(c,proposal.deal_id,field);
  const deal=(await c.query('select version from deal where id=$1 for update',[proposal.deal_id])).rows[0];
  if (deal?.version!==args.phase_base_version) fail('email_deal_proposal_stale');
  if (receipt.status==='undone') return receipt;
  await revertDeal(c,actor,{event_id:receipt.event_id,idempotency_key:derivedIdempotencyKey('email-phase-undo',args.idempotency_key)});
  const undone={...receipt,status:'undone'};
  await c.query(`update email_proposal set correction_provenance=jsonb_set(correction_provenance,'{email_phase}',$2::jsonb),
    version=version+1 where id=$1`,[proposal.id,JSON.stringify(undone)]);
  return undone;
}
