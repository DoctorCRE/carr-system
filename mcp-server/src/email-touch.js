// Bind email contact applications to their permanent activity-event proof.
import { organizationTenantForActor } from './identity.js';
const ROLES = {client:'client',lead:'prospect',vendor:'vendor'};
export const EMAIL_TOUCH_SCHEMA = {
  type:'object',additionalProperties:false,required:['source_id','source_version','role'],
  properties:{source_id:{type:'string',minLength:1,maxLength:500},source_version:{type:'string',minLength:1,maxLength:200},role:{enum:Object.values(ROLES)}},
};
const EFFECT_PROPERTIES = {
  ...EMAIL_TOUCH_SCHEMA.properties,original_key:{type:'string',format:'uuid'},
  target_type:{enum:Object.keys(ROLES)},target_id:{type:'string',format:'uuid'},occurred_at:{type:'string',format:'date-time'},
};
export function emailTouchBinding(args, subject, ToolError) {
  if (!args.email_touch) return null;
  if (!['email_in','email_out'].includes(args.kind) || !args.occurred_at || !Number.isFinite(Date.parse(args.occurred_at)))
    throw new ToolError({error:'email_touch_requires_email_occurrence'});
  if (!ROLES[subject.type] || ROLES[subject.type] !== args.email_touch.role)
    throw new ToolError({error:'email_touch_role_mismatch'});
  return {...args.email_touch,original_key:args.idempotency_key,target_type:subject.type,target_id:subject.id,
    occurred_at:new Date(args.occurred_at).toISOString()};
}

export async function guardEmailTouch(c, actor, binding, ToolError) {
  if (!binding) return;
  await c.query('select pg_advisory_xact_lock(hashtextextended($1,0))',[JSON.stringify(['email-touch-source',actor.id,binding.source_id,binding.target_type,binding.target_id])]);
  const identity=[actor.id,binding.source_id,binding.source_version,binding.target_type,binding.target_id];
  await c.query('select pg_advisory_xact_lock(hashtextextended($1,0))',[JSON.stringify(['email-touch',...identity])]);
  const live=await c.query(`select 1 from v_ref_index r join party p on p.id=r.party_id
    where r.subject_type=$1 and r.subject_id=$2 and not r.merged and p.deleted_at is null`,[binding.target_type,binding.target_id]);
  if (!live.rows.length) throw new ToolError({error:'email_touch_target_not_live'});
  const prior=await c.query(`select 1 from event where verb='log-activity' and actor_id=$1
    and public.digest(new_value#>>'{email_application,binding,source_id}','sha256')=public.digest($2,'sha256')
    and public.digest(new_value#>>'{email_application,binding,source_version}','sha256')=public.digest($3,'sha256')
    and new_value#>>'{email_application,binding,source_id}'=$2
    and new_value#>>'{email_application,binding,source_version}'=$3
    and new_value#>>'{email_application,binding,target_type}'=$4
    and new_value#>>'{email_application,binding,target_id}'=$5 limit 1`,identity);
  if (prior.rows.length) throw new ToolError({error:'email_touch_already_applied'});
}

export function emailTouchReader(ToolError) {
  return {
    write:false,
    writerConnection:true,
    description:'Counts-only reconciliation of up to 500 exact expected email-touch effects for the authenticated actor. Complete describes only the submitted cohort. Occurrence uses [start,end); optional recorded bounds use [recorded_start,recorded_end). Refused counts are caller-reported refusals without application evidence. No mailbox census or permanent refusal history is inferred.',
    inputSchema:{type:'object',additionalProperties:false,required:['expected','start','end'],properties:{
      start:{type:'string',format:'date-time'},end:{type:'string',format:'date-time'},
      recorded_start:{type:'string',format:'date-time'},recorded_end:{type:'string',format:'date-time'},
      expected:{type:'array',minItems:1,maxItems:500,items:{type:'object',additionalProperties:false,
        required:Object.keys(EFFECT_PROPERTIES),properties:{...EFFECT_PROPERTIES,refused:{type:'boolean'}}}},
    }},
    handler:async(c,actor,args)=>{
      if (!(Date.parse(args.start)<Date.parse(args.end)) ||
          (!!args.recorded_start !== !!args.recorded_end) ||
          (args.recorded_start && !(Date.parse(args.recorded_start)<Date.parse(args.recorded_end))))
        throw new ToolError({error:'email_touch_window_invalid'});
      const keys=new Set(args.expected.map(e=>e.original_key));
      const effects=new Set(args.expected.map(e=>JSON.stringify([e.source_id,e.source_version,e.target_type,e.target_id])));
      if(keys.size!==args.expected.length || effects.size!==args.expected.length)
        throw new ToolError({error:'email_touch_duplicate_expectation'});
      const normalized=args.expected.map(e=>({...e,occurred_at:new Date(e.occurred_at).toISOString()}));
      const rows=(await c.query(`with expected as (
        select value as effect from jsonb_array_elements($1::jsonb)
      ), evidence as (
        select x.effect,e.recorded_at,e.new_value#>'{email_application,binding}' as binding,
          a.id as activity_id,a.occurred_at as activity_occurrence,a.kind,a.touch_correction_key,
          a.email_context->>'source_activity_id' as source_reference,source.id as source_activity,
          case x.effect->>'target_type' when 'client' then a.client_id when 'lead' then a.lead_id when 'vendor' then a.vendor_id end as activity_target,
          coalesce(source.email_context,a.email_context) as email_context,
          exists(select 1 from v_ref_index r join party p on p.id=r.party_id where r.subject_type=x.effect->>'target_type'
            and r.subject_id::text=x.effect->>'target_id' and not r.merged and p.deleted_at is null) as target_live,
          exists(select 1 from v_ref_index r where r.subject_type=x.effect->>'target_type'
            and r.subject_id::text=x.effect->>'target_id') as target_exists
        from expected x left join event e on e.idempotency_key=(x.effect->>'original_key')
          and e.verb='log-activity' and e.actor_id=$2 and e.organization_tenant_id=$3
          and e.new_value ? 'email_application'
        left join activity a on a.id::text=e.new_value->>'activity'
        left join activity source on source.id::text=a.email_context->>'source_activity_id'
      ) select case
        when (effect->>'occurred_at')::timestamptz < $4::timestamptz or (effect->>'occurred_at')::timestamptz >= $5::timestamptz
          or (binding is not null and $6::timestamptz is not null and (recorded_at < $6::timestamptz or recorded_at >= $7::timestamptz)) then 'out_of_window'
        when binding is null then case when target_exists and not target_live then 'tombstoned'
          when coalesce((effect->>'refused')::boolean,false) then 'refused' else 'missing' end
        when binding <> (effect-'refused') then 'conflicting'
        when activity_id is null or not target_exists or (source_reference is not null and source_activity is null) then 'dangling'
        when activity_target::text is distinct from effect->>'target_id'
          or activity_occurrence is distinct from (effect->>'occurred_at')::timestamptz
          or kind not in ('email_in','email_out') then 'conflicting'
        when touch_correction_key is not null then 'corrected'
        when not target_live or coalesce(email_context#>>'{lifecycle,state}','live') <> 'live' then 'tombstoned'
        else 'applied' end as disposition,effect->>'target_type' as target_type,
          effect->>'source_version' as source_version,count(*)::int as n
        from evidence group by 1,2,3`,[JSON.stringify(normalized),actor.id,organizationTenantForActor(actor),args.start,args.end,args.recorded_start||null,args.recorded_end||null])).rows;
      const emptyCounts=()=>({expected:0,applied:0,missing:0,refused:0,dangling:0,tombstoned:0,out_of_window:0,conflicting:0,corrected:0});
      const counts={...emptyCounts(),expected:args.expected.length};
      const groups = new Map();
      for(const row of rows) {
        counts[row.disposition]+=row.n;
        const key=JSON.stringify([row.target_type,row.source_version]);
        if (!groups.has(key)) groups.set(key,{target_type:row.target_type,source_version:row.source_version,
          counts:emptyCounts()});
        const group=groups.get(key);
        group.counts.expected+=row.n;
        group.counts[row.disposition]+=row.n;
      }
      return {schema_version:'email-touch-reconciliation.v1',complete:true,scope:'submitted_cohort',counts,
        groups:[...groups.values()].sort((a,b)=>JSON.stringify([a.target_type,a.source_version]).localeCompare(JSON.stringify([b.target_type,b.source_version])))};
    },
  };
}
