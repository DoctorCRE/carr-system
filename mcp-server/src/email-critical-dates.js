import { lockFilingApplication } from './email-filing-application.js';
import { partnerAuthoritySlugForActor } from './partner-authority.js';
import { suspected } from './untrusted-text.js';
import { authorizationClassForActor } from './identity.js';

const labels = {
  loi_expiry: 'LOI (?:expiration|expiry)', lease_commencement: 'lease commencement',
  lease_expiration: 'lease (?:expiration|expiry)', contingency_deadline: 'contingency deadline',
};
const identities = {
  loi_expiry:['loi_expiry','loi_expiration'], lease_commencement:['lease_commencement','lease_commencement_date'],
  lease_expiration:['lease_expiration','lease_expiry'], contingency_deadline:['contingency_deadline'],
};
const snapshot = r => r ? {id:r.id,kind:r.kind,due_on:r.due_on,status:r.status,note:r.note,source:r.source,version:r.version} : null;
const day = value => /^\d{4}-\d{2}-\d{2}$/.test(value ?? '') &&
  !Number.isNaN(Date.parse(value)) && new Date(value).toISOString().slice(0,10) === value;

export function dateEvidence(source, kind, quote) {
  const context = source.email_context ?? {};
  if (context.body_kind!=='full' || context.body_completeness!=='complete' ||
      context.body_outcome!=='complete' || context.body_truncated) return {reason:'incomplete_source'};
  if (!labels[kind] || !source.detail?.includes(quote)) return {reason:'ungrounded_obligation'};
  if (suspected(source.detail)) return {reason:'suspected_instruction'};
  if (context.attachment_count!==0 || context.attachments?.length || context.attachment_conflict || context.body_conflict)
    return {reason:'attachment_review_required'};
  if (/\b\d{1,2}:\d{2}|\b\d{1,2}\s*(?:am|pm)\b|\b(?:UTC|CST|CDT|timezone)\b/i.test(source.detail))
    return {reason:'timezone_review_required'};
  // Whole-body uncertainty prevents a clean quote from hiding contradictory context.
  if (/\b(?:propos\w*|draft|tentative|if|unless|not|cancel\w*|contradict\w*|conflict\w*|amend\w*|correct\w*|revis\w*|supersed\w*)\b/i.test(source.detail))
    return {reason:'agreement_uncertain'};
  const match = new RegExp('^The agreed '+labels[kind]+' date is (\\d{4}-\\d{2}-\\d{2})\\.?$', 'i').exec(quote.trim());
  if (!match || !day(match[1])) return {reason:'absolute_agreement_required'};
  const dates = source.detail.match(/\b\d{4}-\d{2}-\d{2}\b/g) ?? [];
  if (dates.some(d=>d!==match[1])) return {reason:'contradictory_dates'};
  return {due_on:match[1],reason:null};
}

export async function emailCriticalDateQueue(c,actor,args) {
  const owner=partnerAuthoritySlugForActor(actor), limit=Math.min(args.limit??25,200);
  const rows=(await c.query(`select e.*,e.source_hash=public.email_filing_source_hash(a)
      and a.deal_id=e.deal_id and coalesce(a.email_context#>>'{lifecycle,state}','live')='live' as current_source
    from email_critical_date_effect e join activity a on a.id=e.activity_id
    where (a.email_source->>'mailbox_owner'=$1 or (a.email_source is null and a.actor_id=$2))
      and ($3::uuid is null or e.id>$3) order by e.id limit $4`,[owner,actor.id,args.after??null,limit+1])).rows;
  return {schema_version:'email-critical-dates.v1',effects:rows.slice(0,limit),more:rows.length>limit,
    next_cursor:rows.length>limit?rows[limit-1].id:null};
}

export async function emailCriticalDateWrite(c,actor,args,fail,writeEvent) {
  const event=async (effect,oldValue,newValue) => writeEvent(c,actor,'record-email-proposal','deal',effect.deal_id,
    {old:oldValue,new:{effect_id:effect.id,activity_id:effect.activity_id,generation:effect.generation,
      source_hash:effect.source_hash,critical_date_id:effect.critical_date_id,...newValue},cause:'ingest_email',idempotency_key:args.idempotency_key});
  if (args.mode==='critical_date') {
    if (!args.generation || !args.claim_id || !labels[args.obligation_kind] || !args.evidence_quote?.trim()) fail('email_proposal_fields_invalid');
    const work=await lockFilingApplication(c,actor,args,fail,true);
    if (!work.claim_active || work.claim_id!==args.claim_id || work.deal_id!==args.deal) fail('email_application_stale');
    const previous=(await c.query('select * from email_critical_date_effect where generation=$1 and obligation_kind=$2',[args.generation,args.obligation_kind])).rows[0];
    if (previous) return {ok:true,effect_id:previous.id,status:previous.status,reason:previous.reason,critical_date_id:previous.critical_date_id};
    const source=(await c.query('select detail,email_context from activity where id=$1',[work.activity_id])).rows[0];
    const evidence=dateEvidence(source,args.obligation_kind,args.evidence_quote);
    const dates=(await c.query(`select *,due_on::text as due_on from critical_date where deal_id=$1
      and lower(regexp_replace(kind,'[ -]+','_','g'))=any($2::text[]) limit 2 for update`,[work.deal_id,identities[args.obligation_kind]])).rows;
    let status='review', reason=evidence.reason, target=null;
    if (!reason && dates.length) {
      if (dates.length===1 && dates[0].status==='open' && dates[0].due_on===evidence.due_on) {status='duplicate';target=dates[0];}
      else reason='existing_obligation_conflict';
    } else if (!reason) {
      status='applied';
      target=(await c.query(`insert into critical_date(deal_id,kind,due_on,note,source,created_by)
        values($1,$2,$3,$4,$5,$6) returning *,due_on::text as due_on`,[work.deal_id,args.obligation_kind,evidence.due_on,
        args.evidence_quote,`activity:${work.activity_id}; generation:${work.generation}`,actor.id])).rows[0];
    }
    const effect=(await c.query(`insert into email_critical_date_effect(generation,activity_id,deal_id,obligation_kind,source_hash,evidence_quote,due_on,status,reason,critical_date_id,after_value)
      values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) returning *`,[work.generation,work.activity_id,work.deal_id,args.obligation_kind,work.source_hash,args.evidence_quote,
      evidence.due_on??null,status,reason,target?.id??null,status==='applied'?snapshot(target):null])).rows[0];
    await event(effect,null,{status,reason,after_value:snapshot(target)});
    return {ok:true,effect_id:effect.id,status,reason,critical_date_id:target?.id??null};
  }
  const found=(await c.query('select activity_id,deal_id from email_critical_date_effect where id=$1',[args.effect_id])).rows[0];
  if (!found) fail('email_proposal_not_found');
  await c.query('select id from activity where id=$1 for update',[found.activity_id]);
  await c.query('select id from deal where id=$1 for update',[found.deal_id]);
  const effect=(await c.query(`select e.*,g.source_hash=public.email_filing_source_hash(a)
      and a.deal_id=e.deal_id and d.phase<>'closed' and d.invoiced_on is null
      and coalesce(a.email_context#>>'{lifecycle,state}','live')='live' as current_source
    from email_critical_date_effect e join activity a on a.id=e.activity_id
    join deal d on d.id=e.deal_id
    join email_filing_application g on g.generation=e.generation where e.id=$1
      and (a.email_source->>'mailbox_owner'=$2 or (a.email_source is null and a.actor_id=$3)) for update of e`,
  [args.effect_id,partnerAuthoritySlugForActor(actor),actor.id])).rows[0];
  if (!effect) fail('email_proposal_not_found');
  if (args.mode==='critical_date_undo') {
    if (effect.status==='undone') return {ok:true,status:'undone'};
    if (effect.status!=='applied') fail('email_proposal_not_pending');
    const target=(await c.query('select *,due_on::text as due_on from critical_date where id=$1 for update',[effect.critical_date_id])).rows[0];
    if (!target || Object.entries(effect.after_value).some(([k,v])=>snapshot(target)[k]!==v)) fail('version_conflict');
    const before=effect.before_value;
    await c.query(`update critical_date set due_on=$2,status=$3,note=$4,source=$5,updated_by=$6 where id=$1`,
      [target.id,before?.due_on??target.due_on,before?.status??'dropped',before?before.note:target.note,before?before.source:target.source,actor.id]);
    await c.query("update email_critical_date_effect set status='undone' where id=$1",[effect.id]);
    await event(effect,effect.after_value,{status:'undone',restored:before});
    return {ok:true,status:'undone'};
  }
  if (authorizationClassForActor(actor)!=='verified_partner') fail('human_confirmation_required');
  if (effect.status!=='review' || !effect.current_source) fail('email_application_stale');
  if (!args.reason?.trim()) fail('email_proposal_reason_required');
  if (args.decision==='dismiss') {
    await c.query("update email_critical_date_effect set status='dismissed',reason=$2 where id=$1",[effect.id,args.reason]);
    await event(effect,null,{status:'dismissed'});return {ok:true,status:'dismissed'};
  }
  if (!['apply','cancel'].includes(args.decision) || (args.decision==='apply' && !day(args.due_on))) fail('email_proposal_fields_invalid');
  const dates=(await c.query(`select *,due_on::text as due_on from critical_date where deal_id=$1
    and lower(regexp_replace(kind,'[ -]+','_','g'))=any($2::text[])
    order by (id=$3) desc nulls last limit 2 for update`,[effect.deal_id,identities[effect.obligation_kind],args.critical_date_id??null])).rows;
  const target=dates.find(d=>d.id===args.critical_date_id);
  if (dates.length && (!target || target.version!==args.base_version)) fail('version_conflict');
  if (!dates.length && (args.critical_date_id || args.base_version || args.decision==='cancel')) fail('version_conflict');
  const before=snapshot(target);
  const saved=target ? (await c.query(`update critical_date set due_on=$2,status=$3,note=$4,source=$5,updated_by=$6 where id=$1 returning *,due_on::text as due_on`,
    [target.id,args.decision==='cancel'?target.due_on:args.due_on,args.decision==='cancel'?'dropped':'open',effect.evidence_quote,`activity:${effect.activity_id}; effect:${effect.id}`,actor.id])).rows[0]
    : (await c.query(`insert into critical_date(deal_id,kind,due_on,note,source,created_by) values($1,$2,$3,$4,$5,$6) returning *,due_on::text as due_on`,
      [effect.deal_id,effect.obligation_kind,args.due_on,effect.evidence_quote,`activity:${effect.activity_id}; effect:${effect.id}`,actor.id])).rows[0];
  await c.query("update email_critical_date_effect set status='applied',reason=$2,critical_date_id=$3,before_value=$4,after_value=$5 where id=$1",
    [effect.id,args.reason,saved.id,before,snapshot(saved)]);
  await event({...effect,critical_date_id:saved.id},before,{status:'applied',after_value:snapshot(saved),reviewed:true});
  return {ok:true,status:'applied',critical_date_id:saved.id};
}
