// Claim and acknowledge durable extraction work for existing-deal email filings.
import { partnerAuthoritySlugForActor } from './partner-authority.js';

const current = `a.kind in ('email_in','email_out') and a.detail is not null
  and a.occurred_at<=now() and coalesce(a.email_context#>>'{lifecycle,state}','live')='live'
  and (a.email_source->>'mailbox_owner'=$2 or (a.email_source is null and a.actor_id=$3::uuid))
  and (a.kind<>'email_out' or a.actor_id=$3::uuid)
  and a.deal_id=g.deal_id and d.phase<>'closed' and d.invoiced_on is null
  and g.source_hash=public.email_filing_source_hash(a)
  and g.authority in ('confirmed','inherited')
  and not exists(select 1 from email_filing_application newer where newer.activity_id=g.activity_id
    and newer.authority<>'review' and (newer.created_at,newer.generation)>(g.created_at,g.generation))`;

export async function filingApplicationQueue(c, actor, args) {
  const limit = Math.min(args.limit ?? 25,25);
  const rows = (await c.query(`select g.generation,g.source_hash,a.id as activity_id,a.kind,
      a.summary,a.detail as body,a.email_context,coalesce(a.email_source->>'mailbox_owner',$2) as mailbox_owner,
      d.id as deal,d.name as deal_name,d.phase,d.deal_type
    from email_filing_application g join activity a on a.id=g.activity_id join deal d on d.id=g.deal_id
    where ($1::uuid is null or g.generation>$1) and ${current}
      and g.status in ('pending','claimed') and g.reason is distinct from 'incomplete_source'
      and (g.claim_until is null or g.claim_until<=now())
    order by g.generation limit $4`, [args.after ?? null,partnerAuthoritySlugForActor(actor),actor.id,limit+1])).rows;
  return { schema_version:'email-filing-application.v1',emails:rows.slice(0,limit),more:rows.length>limit,
    next_cursor:rows.length>limit ? rows[limit-1].generation : null };
}

export async function lockFilingApplication(c, actor, args, fail) {
  await c.query('select id from activity where id=$1 for update',[args.activity_id]);
  await c.query('select id from deal where id=(select deal_id from activity where id=$1) for share',[args.activity_id]);
  const row = (await c.query(`select g.*,(${current}) as current,g.claim_until>now() as claim_active
    from email_filing_application g join activity a on a.id=g.activity_id left join deal d on d.id=g.deal_id
    where g.generation=$1 and g.activity_id=$4 for update of g`,
  [args.generation,partnerAuthoritySlugForActor(actor),actor.id,args.activity_id])).rows[0];
  if (!row?.current || !['pending','claimed'].includes(row.status)) fail('email_application_stale');
  return row;
}

export async function filingApplicationWrite(c, actor, args, fail) {
  const row = await lockFilingApplication(c,actor,args,fail);
  if (args.mode==='application_claim') {
    if (row.reason==='incomplete_source') return {ok:true,generate:false,status:'held'};
    if (row.claim_active) return {ok:true,generate:false};
    if (row.attempts>=3) {
      await c.query("update email_filing_application set status='exhausted',reason='retry_exhausted',claim_id=null,claim_until=null where generation=$1",[row.generation]);
      return {ok:true,generate:false,status:'exhausted'};
    }
    const claimed = (await c.query(`update email_filing_application set status='claimed',attempts=attempts+1,
      claim_id=gen_random_uuid(),claim_until=now()+interval '5 minutes'
      where generation=$1 returning claim_id,attempts`,[row.generation])).rows[0];
    return {ok:true,generate:true,...claimed};
  }
  if (!row.claim_active || row.claim_id!==args.claim_id) fail('email_application_stale');
  if (!['applied','retry','held'].includes(args.application_status)) fail('email_proposal_fields_invalid');
  const status = args.application_status==='held' ? 'pending' : args.application_status==='applied' ? 'applied' : row.attempts>=3 ? 'exhausted' : 'pending';
  await c.query(`update email_filing_application set status=$2,reason=$3,claim_id=null,claim_until=null
    where generation=$1`,[row.generation,status,args.application_status==='held' ? 'incomplete_source' : status==='exhausted' ? 'retry_exhausted' : null]);
  return {ok:true,status,generation:row.generation};
}
