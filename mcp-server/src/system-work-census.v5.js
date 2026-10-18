// System design/build projection of the existing work inventory federation.
import { canonicalJson } from './artifact-trust.js';
import { createHash } from 'node:crypto';
import { organizationTenantForActor, personalScopeForActor } from './identity.js';
import { closureProjection } from './engineering-runtime.js';

const base = (source, kind, sql, note = null) => ({ source, kind, sql, note });
// Every SELECT has the same typed columns. Source states are preserved, never
// equated with a completion claim merely because a row disappeared from a queue.
export const SYSTEM_WORK_LEGS = [
  base('ops.work_request', 'work_request', `select w.ref as id, w.title, w.state, w.captured_at as opened_at,
    w.updated_at as last_activity_at, w.owner_actor as owner, w.version::text as version,
    w.state='confirmed_closed' as completed, w.state in ('declined','superseded') as cancelled,
    jsonb_build_object('human_ref',w.ref) as identity from ops.work_request w
    where w.organization_tenant_id=$1`),
  base('ops.portfolio_node', 'portfolio_node', `select n.node_ref as id,n.node_ref as title,
    coalesce(m.status,'unmarked') as state,n.created_at as opened_at,
    coalesce(m.created_at,n.created_at) as last_activity_at,null::text as owner,r.revision_version::text as version,
    coalesce(m.status='complete',false) as completed,false as cancelled,
    jsonb_build_object('slice_id',n.node_ref) as identity from ops.portfolio_node n
    join ops.portfolio_revision r on r.id=n.portfolio_revision_id
    left join lateral (select status,created_at from ops.slice_completion_mark where slice_id=n.node_ref order by mark_seq desc limit 1) m on true
    where not exists (select 1 from ops.portfolio_revision newer where newer.portfolio_ref=r.portfolio_ref and newer.revision_version>r.revision_version)`),
  base('public.loop_item', 'loop', `select l.id::text as id,l.title,l.status as state,
    case when l.since_text ~ '^\\d{4}-\\d{2}-\\d{2}' and pg_input_is_valid(left(l.since_text,10),'date') then left(l.since_text,10)::date::timestamptz else l.created_at end as opened_at,
    l.updated_at as last_activity_at,l.owner,l.version::text as version,l.status='done' as completed,l.status='dropped' as cancelled,
    jsonb_build_object('loop_id',l.id,'kind',l.kind) as identity from public.loop_item l
    where l.kind in ('open_loop','idea','team_loop','action_required') and l.domain='system'
    and (l.tier='shared' or (l.tier='personal' and l.personal_to=(select id from ops.system_work_actor_scope where slug=$2)))
    and not exists (select 1 from jsonb_path_query(l.extra_cells,'$.**') s
      where s ?| array['deal_id','lead_id','client_id','vendor_id','party_id',
        'deal_ref','lead_ref','client_ref','vendor_ref','party_ref']
      or (jsonb_typeof(s)='string' and lower(trim(both '"' from s::text)) ~ '^(deal|lead|client|vendor|party)(:|/)')
      or exists(select 1 from jsonb_each(case when jsonb_typeof(s)='object' then s else '{}'::jsonb end) e
        where e.key in ('subject','subject_type','entity_type','record_type','type')
        and lower(trim(both '"' from e.value::text)) in ('deal','lead','client','vendor','party')))`),
  base('ops.work_shape_revision', 'work_shape', `select s.id::text as id,w.title,w.state,s.created_at as opened_at,
    s.created_at as last_activity_at,w.owner_actor as owner,s.version::text as version,
    w.state='confirmed_closed' as completed,w.state in ('declined','superseded') as cancelled,
    jsonb_build_object('work_request',w.ref) as identity from ops.work_shape_revision s join ops.work_request w on w.id=s.work_request_id
    where w.organization_tenant_id=$1 and not exists(select 1 from ops.work_shape_revision newer where newer.work_request_id=s.work_request_id and newer.version>s.version)`),
  base('ops.engineering_slice_plan', 'slice_plan', `select p.id::text as id,w.title,
    case when m.completed then 'complete' else w.state end as state,p.created_at as opened_at,
    coalesce(m.last_activity_at,p.created_at) as last_activity_at,w.owner_actor as owner,p.work_request_version::text as version,
    (w.state='confirmed_closed' or m.completed) as completed,w.state in ('declined','superseded') as cancelled,
    jsonb_build_object('work_request',w.ref) as identity,p.plan_digest,p.accepted_plan_id,p.accepted_plan_hash
    from ops.engineering_slice_plan p join ops.work_request w on w.id=p.work_request_id
    left join lateral(select count(*)>0 and bool_and(coalesce(mark.status='complete',false)) as completed,
      max(mark.created_at) as last_activity_at from jsonb_array_elements(p.plan->'slices') member
      left join lateral(select status,created_at from ops.slice_completion_mark
        where slice_id=member->>'slice_ref' order by mark_seq desc limit 1) mark on true) m on true
    where w.organization_tenant_id=$1 and not exists(select 1 from ops.engineering_slice_plan newer
      where newer.work_request_id=p.work_request_id and
       ((newer.plan->'accepted_plan_revision'->>'revision')::int > (p.plan->'accepted_plan_revision'->>'revision')::int
        or (newer.plan->'accepted_plan_revision'->>'revision'=p.plan->'accepted_plan_revision'->>'revision'
         and (newer.created_at,newer.id)>(p.created_at,p.id))))`),
  base('ops.rule_admission','governance_item', `select a.rule_id::text as id,left(coalesce(a.reason,a.binding_moment),160) as title,
    a.state,a.updated_at as opened_at,a.updated_at as last_activity_at,null::text as owner,a.version::text as version,
    a.state='admitted' as completed,a.state='rejected' as cancelled,jsonb_build_object('rule_id',a.rule_id) as identity
    from ops.rule_admission a join ops.system_work_rule_scope r on r.id=a.rule_id
    where r.personal_to is null or r.personal_to=(select id from ops.system_work_actor_scope where slug=$2)`),
  base('ops.capability_agent_session','capability_session', `select s.id::text as id,w.title,s.state,s.created_at as opened_at,
    s.updated_at as last_activity_at,w.owner_actor as owner,w.version::text as version,s.state='completed' as completed,
    s.state='cancelled' as cancelled,jsonb_build_object('sequence',w.program_ordinal,'capability_agent_session_id',s.id) as identity
    from ops.capability_agent_session s join ops.work_request w on w.id=s.work_request_id where w.organization_tenant_id=$1`),
  base('ops.slice_completion_proposal','slice_proposal', `select p.id::text as id,p.slice_id as title,
    case when m.status='complete' then 'complete' else 'pending' end as state,p.created_at as opened_at,
    coalesce(m.created_at,p.created_at) as last_activity_at,p.proposed_by_actor_slug as owner,null::text as version,
    coalesce(m.status='complete',false) as completed,coalesce(m.mark_seq>p.basis_mark_seq and m.status<>'complete',false) as cancelled,
    jsonb_build_object('slice_ids',jsonb_build_array(p.slice_id),'expected_proposal_ids',jsonb_build_object(p.slice_id,p.id)) as identity
    from ops.slice_completion_proposal p left join lateral(select status,created_at,mark_seq from ops.slice_completion_mark
      where slice_id=p.slice_id order by mark_seq desc limit 1) m on true
    where not exists(select 1 from ops.slice_completion_proposal newer where newer.slice_id=p.slice_id and newer.proposal_seq>p.proposal_seq)`),
  base('public.investigation_run','investigation', `select id::text as id,objective as title,status as state,opened_at,
    coalesce(closed_at,opened_at) as last_activity_at,$2::text as owner,null::text as version,
    status='completed' as completed,status='abandoned' as cancelled,jsonb_build_object('run_id',id) as identity
    from public.investigation_run where owner_actor_id=(select id from ops.system_work_actor_scope where slug=$2)`),
  base('ops.incident','incident', `select ref as id,title,state,detected_at as opened_at,
    coalesce(reviewed_at,resolved_at,last_seen_at,observed_at) as last_activity_at,owner_actor as owner,null::text as version,
    state in ('resolved','reviewed') as completed,false as cancelled,jsonb_build_object('ref',ref) as identity from ops.incident`),
  base('ops.workflow_cutover_plan','cutover_plan', `select id::text as id,workflow_key as title,status as state,created_at as opened_at,
    updated_at as last_activity_at,opened_by_actor_slug as owner,null::text as version,status='retired' as completed,
    status in ('superseded','cancelled') as cancelled,jsonb_build_object('plan_id',id,'stage',stage) as identity from ops.workflow_cutover_plan`),
  base('public.retrieval_proposal','retrieval_proposal', `select id::text as id,reason as title,status as state,created_at as opened_at,
    coalesce(reviewed_at,created_at) as last_activity_at,null::text as owner,version::text as version,status='approved' as completed,
    status in ('rejected','superseded') as cancelled,jsonb_build_object('proposal_id',id) as identity from public.retrieval_proposal`),
  base('ops.ready_plan_amendment','ready_plan_amendment', `select a.plan_id::text as id,w.title,
    case when r.id is null then 'pending' else 'accepted' end as state,a.proposed_at as opened_at,
    coalesce(r.accepted_at,a.proposed_at) as last_activity_at,w.owner_actor as owner,w.version::text as version,
    r.id is not null as completed,w.state in ('declined','superseded') as cancelled,
    jsonb_build_object('human_ref',w.ref,'plan_hash',p.plan_hash) as identity from ops.ready_plan_amendment a
    join ops.work_request w on w.id=a.work_request_id join ops.system_work_plan_scope p on p.id=a.plan_id left join ops.ready_plan_amendment_acceptance_receipt r on r.successor_plan_id=a.plan_id
    where w.organization_tenant_id=$1`),
  base('public.defect','defect', `select id::text as id,defect_class||': '||claimed as title,'fix_untracked'::text as state,
    occurred_on::timestamptz as opened_at,created_at as last_activity_at,null::text as owner,null::text as version,
    false as completed,false as cancelled,jsonb_build_object('defect_id',id) as identity from public.defect`, 'fix_status_not_recorded'),
  base('ops.work_shape_revision.builder_brief','builder_brief', `select s.id::text as id,w.title,w.state,s.created_at as opened_at,
    s.created_at as last_activity_at,w.owner_actor as owner,s.version::text as version,w.state='confirmed_closed' as completed,
    w.state in ('declined','superseded') as cancelled,jsonb_build_object('work_request',w.ref) as identity
    from ops.work_shape_revision s join ops.work_request w on w.id=s.work_request_id
    where w.organization_tenant_id=$1 and s.builder_brief<>'{}'::jsonb
    and not exists(select 1 from ops.work_shape_revision newer where newer.work_request_id=s.work_request_id and newer.version>s.version)
    and not exists(select 1 from public.board_snapshot b
      where b.organization_tenant_id=$1 and b.sponsoring_human_slug=$2 and b.board_id='carr-v5'
      and b.snapshot_json->'external_inventory'->'pr_work_refs' ? w.ref)`),
  base('public.board_snapshot.tasks','progress_task', `select b.board_id||':'||t.key as id,coalesce(t.value->>'title',t.key) as title,
    coalesce(t.value->>'status','queued') as state,
    coalesce(nullif(t.value->>'created_at','')::timestamptz,nullif(t.value->>'updated_at','')::timestamptz,b.updated_at) as opened_at,
    coalesce(nullif(t.value->>'updated_at','')::timestamptz,b.updated_at) as last_activity_at,
    t.value->>'executor' as owner,b.version::text as version,
    coalesce((t.value->>'stage' in ('live','measured') and coalesce(t.value->>'evidence','')<>''),false) as completed,
    false as cancelled,jsonb_build_object('board_id',b.board_id,'task_id',t.key) as identity
    from public.board_snapshot b,jsonb_each(coalesce(b.snapshot_json->'tasks','{}'::jsonb)) t
    where b.organization_tenant_id=$1 and b.sponsoring_human_slug=$2
    and t.value->>'repo' in ('jbookout/carr-system','jbookout/doctorcre-app','jbookout/software-factory')
    and t.value->>'domain'='system'
    and (t.value->>'status' is distinct from 'done' or t.value->>'stage' in ('live','measured'))`),
];
export const EXTERNAL_WORK_KINDS = ['pull_request','remote_branch','builder_brief_file'];
export const SYSTEM_WORK_KINDS = [...SYSTEM_WORK_LEGS.map(x=>x.kind), ...EXTERNAL_WORK_KINDS];
const err = code => Object.assign(new Error(code),{code});
const iso = value => { const d=new Date(value);return value && Number.isFinite(+d)?d.toISOString():null; };
const compareText=(a,b)=>a===b?0:a<b?-1:1;
const cmp = (a,b,live) => (live ? compareText(b.last_activity_at,a.last_activity_at) : compareText(a.opened_at,b.opened_at)) || compareText(a.kind,b.kind) || compareText(a.id,b.id);

export function systemWorkActions(item) {
  if(item.completed || item.cancelled) return [];
  const action=(action,verb,args,fields,versioned=false)=>({action,verb,args,fields,versioned});
  const reason=[{name:'reason',label:'Reason',required:true}];
  switch(item.kind) {
    case 'loop': return [action('cancel','close-loop',{...item.identity,resolution:'dropped'},[{name:'outcome',label:'Why cancel?',required:true}],true),
      action('progress','update-loop',item.identity,[{name:'body',label:'Progress and next step',required:true}],true),
      action('redesign','update-loop',item.identity,[{name:'title',label:'Revised title',required:true},{name:'body',label:'Revised design',required:true}],true)];
    case 'work_request': return item.state==='captured' ? [action('cancel','decline-work-request',item.identity,[{name:'exit_reason',label:'Why cancel?',required:true}],true),
      ...(item.state==='captured'?[action('progress','review-and-triage',item.identity,[{name:'classification',label:'Review lane',choices:['operational','needs_judgment','safety_review'],required:true}],true)]:[])] : [];
    case 'capability_session': return item.identity.sequence ? [action('cancel','cancel-capability-session',item.identity,reason,true)] : [];
    case 'investigation': return [action('cancel','close-investigation',{...item.identity,status:'abandoned'},[
      {name:'conclusion',label:'Conclusion',required:true},{name:'confidence',label:'Confidence (0 to 1)',type:'number',min:0,max:1,required:true},
      {name:'strongest_alternative',label:'Strongest alternative',required:true},{name:'alternative_disposition',label:'Alternative disposition',required:true},
      {name:'termination_reason',label:'Termination reason',choices:['budget_exhausted','insufficient_evidence','signal_invalid','superseded'],required:true}])];
    case 'ready_plan_amendment': return [action('progress','accept-ready-plan-amendment',item.identity,[],true)];
    case 'retrieval_proposal': return [action('progress','approve-retrieval-proposals',{proposal_ids:[item.id]},[{name:'golden_suite_digest',label:'Verified golden suite digest',required:true}],true)];
    case 'incident': return item.state==='detected' ? [action('progress','triage-incident',item.identity,[{name:'next_action',label:'Next investigation step',required:true},{name:'impact_assessment',label:'Impact assessment',required:true}]),
      action('redesign','triage-incident',item.identity,[{name:'next_action',label:'Revised investigation',required:true},{name:'impact_assessment',label:'Revised impact assessment',required:true}])] : [];
    case 'cutover_plan': { const {stage,...args}=item.identity; const stages=['read_legacy','build_projection','shadow_compare','single_write_authority','cutover','monitor','recovery_ready']; const next=stages[stages.indexOf(stage)+1];
      return [action('cancel','cancel-workflow-cutover-plan',args,reason),...(next?[action('progress','advance-workflow-cutover-stage',{...args,to_stage:next},[...reason,{name:'evidence_ref',label:'Accepted workflow evidence',required:['shadow_compare','single_write_authority','cutover'].includes(next)}])]:[])]; }
    case 'slice_proposal': return [action('progress','confirm-slice-completions',item.identity,reason)];
    default:return [];
  }
}
const navigationFor=(row,kind)=>{
 if(['work_request','work_shape','slice_plan','builder_brief','ready_plan_amendment'].includes(kind)) {
  const ref=row.identity?.human_ref??row.identity?.work_request;
  if(typeof ref==='string' && /^WR-[0-9]{1,12}$/.test(ref))
   return {state:'available',link:`/system-work.html?work_request=${encodeURIComponent(ref)}`,identity:{work_request:ref}};
 }
 if(kind==='progress_task' && row.identity?.board_id)
  return {state:'available',link:`/progress-board.html?board=${encodeURIComponent(row.identity.board_id)}`,identity:row.identity};
 if(['pull_request','remote_branch'].includes(kind) && typeof row.link==='string' && /^https:\/\/github\.com\/jbookout\/(carr-system|doctorcre-app)\//.test(row.link))
  return {state:'available',link:row.link,identity:{id:row.id}};
 return {state:'unavailable',link:null,reason:'owning_workflow_has_no_supported_record_navigation',identity:row.identity??{}};
};
const normalized=(row,leg,now)=>{
 const opened=iso(row.opened_at),activity=iso(row.last_activity_at);if(!opened||!activity||!row.id) return null;
 const navigation=navigationFor(row,leg.kind);
 const item={...row,id:String(row.id),source:leg.source,kind:leg.kind,opened_at:opened,last_activity_at:activity,
   age:Math.max(0,Math.floor((+now-Date.parse(opened))/86400000)),link:navigation.link,navigation,
   status:row.state,updated_at:activity,source_ref:leg.source,open:navigation.link,related:[],unlinked:true};
 item.source_workflow=leg.kind==='incident'?{read:{verb:'get-incident',args:item.identity}}:
  leg.kind==='work_request'?{read:{verb:'work-request-card',args:{work_request:item.identity.human_ref}}}:null;
 item.available_triage_actions=systemWorkActions(item);return item;
};

export async function readSystemWorkCensus({client,actor,correlationId,now=()=>new Date(),...filters}) {
 const scope=personalScopeForActor(actor);if(scope.status!=='personal') throw err('AUTHORIZATION_REFUSED');
 const tenant=organizationTenantForActor(actor),sponsor=scope.sponsor,at=now();
 const live=filters.live_library===true||filters.live_library==='true';
 const limit=Math.min(500,Number(filters.limit??100));
 const age=Number(filters.age??0),text=String(filters.text??'').trim();
 if(!Number.isInteger(limit)||limit<1||!Number.isFinite(age)||age<0||text.length>200)throw err('AUTHORIZATION_REFUSED');
 const kinds=filters.kinds ? String(filters.kinds).split(',') : SYSTEM_WORK_KINDS;
 if(kinds.some(k=>!SYSTEM_WORK_KINDS.includes(k)))throw err('AUTHORIZATION_REFUSED');
 const sources=filters.source?String(filters.source).split(','):null;
 if(sources?.some(s=>![...SYSTEM_WORK_LEGS.map(l=>l.source),'github','builder_files'].includes(s)))throw err('AUTHORIZATION_REFUSED');
 const signature=JSON.stringify([kinds,sources,age,text,live,sponsor,filters.id??null]);
 let cursor=null;if(filters.cursor) {try{cursor=JSON.parse(Buffer.from(filters.cursor,'base64url').toString());}catch{throw err('AUTHORIZATION_REFUSED');}
  if(!cursor || typeof cursor!=='object' || Array.isArray(cursor) || cursor.signature!==signature||!iso(cursor.date)||!kinds.includes(cursor.kind)||typeof cursor.id!=='string')throw err('AUTHORIZATION_REFUSED');}
 const coverage=[],items=[];
 const cacheRead=await client.query(`select snapshot_json->'external_inventory' as cache from public.board_snapshot
   where organization_tenant_id=$1 and sponsoring_human_slug=$2 and board_id='carr-v5'`,[tenant,sponsor]).catch(()=>({rows:[]}));
 let cache=cacheRead.rows[0]?.cache;
 let pagesMissing=false;
 if(cache?.schema==='system-work-external.v2') {
  const pages=cache.pages;
  const validManifest=Array.isArray(pages) && pages.length<=10000 && pages.every(p=>p &&
   typeof p.board_id==='string' && /^carr-v5-external-[a-zA-Z0-9-]+$/.test(p.board_id) && Number.isSafeInteger(p.version) && p.version>0 && Number.isSafeInteger(p.count) && p.count>=0);
  const loaded=[];
  if(validManifest) {
   const response=await client.query(`select board_id,version,snapshot_json from public.board_snapshot
    where organization_tenant_id=$1 and sponsoring_human_slug=$2 and board_id=any($3::text[])`,[tenant,sponsor,pages.map(p=>p.board_id)]).catch(()=>({rows:[]}));
   for(const page of pages) {
    const stored=response.rows.find(r=>r.board_id===page.board_id);
    const rows=stored?.snapshot_json?.items;
    if(!stored || Number(stored.version)!==page.version || !Array.isArray(rows) || rows.length!==page.count ||
       (page.digest && createHash('sha256').update(canonicalJson(stored.snapshot_json)).digest('hex')!==page.digest)) {pagesMissing=true;continue;}
    loaded.push(...rows);
   }
  } else pagesMissing=true;
  if(loaded.length!==cache.item_count)pagesMissing=true;
  cache={...cache,items:loaded};
 }
 const cacheShape=cache && typeof cache==='object' && Array.isArray(cache.items) && cache.items.every(r=>r && typeof r==='object' && !Array.isArray(r));
 const cacheFresh=cacheShape&&['system-work-external.v1','system-work-external.v2'].includes(cache?.schema)&&iso(cache.observed_at)&&(+at-Date.parse(cache.observed_at)<3600000)&&cache.complete===true&&+at>=Date.parse(cache.observed_at);
 for(const leg of SYSTEM_WORK_LEGS.filter(l=>kinds.includes(l.kind)&&(!sources||sources.includes(l.source)))){
  try{
   if(leg.kind==='slice_plan') {
    // Completion belongs to the Passport. Resolve it before selecting either
    // library, counting, or paginating; legacy marks cannot override lineage.
    const result=await client.query(leg.sql,[tenant]);
    const rows=[];let missing=0;
    for(const raw of result.rows) {
     const row={...raw};
     const facts=(await client.query('select ops.engineering_passport_facts($1::text) as facts',[row.identity.work_request])).rows[0]?.facts;
     const registered=facts?.slice_plans?.find(p=>p.id===row.id);
     if(facts?.source && !registered) throw err('engineering_census_plan_binding_mismatch');
     if(registered) {
      if(registered.plan?.plan_digest!==row.plan_digest || registered.accepted_plan_id!==row.accepted_plan_id || registered.accepted_plan_hash!==row.accepted_plan_hash)
       throw err('engineering_census_plan_binding_mismatch');
      const passport=closureProjection(facts,Error);
      if(passport.plan_digest!==row.plan_digest) throw err('engineering_census_plan_binding_mismatch');
      row.completed=passport.closure_state==='complete';
      row.state=row.completed?'complete':passport.stale_conflict.state==='stale'?'stale':passport.slices.some(s=>s.state==='reopened')?'reopened':passport.closure_state;
      const dates=[row.last_activity_at,...(facts.receipts??[]).map(r=>r.created_at),...(facts.reviewer_facts??[]).map(r=>r.created_at)].map(iso).filter(Boolean).sort();
      row.last_activity_at=dates.at(-1);
     }
     const item=normalized(row,leg,at);
     if(!item){missing++;continue;}
     if(item.completed===live && (live||!item.cancelled) && Date.parse(item.opened_at)<=+at-age*86400000 &&
       (!filters.id||item.id===filters.id) && (!text||`${item.title} ${item.id}`.toLowerCase().includes(text.toLowerCase()))) rows.push(item);
    }
    coverage.push({kind:leg.kind,source_ref:leg.source,state:missing?'partial':'complete',count_total:rows.length,reason:missing?'rows_missing_dates':null});
    items.push(...rows.filter(r=>!cursor||cmp({kind:cursor.kind,id:cursor.id,opened_at:cursor.date,last_activity_at:cursor.date},r,live)<0).sort((a,b)=>cmp(a,b,live)).slice(0,limit+1));
    continue;
   }
   const order=live?'last_activity_at':'opened_at',direction=live?'desc':'asc',op=live?'<':'>';
   // Parameters bind search, dates and authority. Values never become SQL.
   const projection=`select id,title,state,date_trunc('milliseconds',opened_at) as opened_at,
     date_trunc('milliseconds',last_activity_at) as last_activity_at,owner,version,completed,cancelled,identity from (${leg.sql}) canonical`;
   const where=`where $1::text is not null and $2::text is not null and completed=$3 and ($3 or not cancelled) and opened_at <= $4::timestamptz
     and ($5='' or title ilike $5 escape '\\' or id ilike $5 escape '\\')
     and ($6::text is null or id=$6)`;
   const tie=cursor?` and (${order} ${op} $7::timestamptz or (${order}=$7::timestamptz and
      ('${leg.kind}' collate "C",$8::text,id collate "C") > ($9::text collate "C",$8::text,$10::text collate "C")))`:'';
   const values=[tenant,sponsor,live,new Date(+at-age*86400000).toISOString(),text?`%${text.replace(/[\\%_]/g,'\\$&')}%`:'',filters.id??null];
   const params=cursor?[...values,cursor.date,leg.source,cursor.kind,cursor.id,limit+1]:[...values,limit+1];
   const rows=await client.query(`select * from (${projection}) census ${where}${tie} order by ${order} ${direction},id collate "C" asc limit $${params.length}::int`,params);
   const count=await client.query(`select count(*) as count from (${projection}) census ${where}`,values);
   let missing=0;for(const row of rows.rows){const item=normalized(row,leg,at);if(item)items.push(item);else missing++;}
   const reason=[leg.note,missing?'rows_missing_dates':null,leg.kind==='builder_brief'&&!cacheFresh?'github_cache_unavailable':null].filter(Boolean).join(';')||null;
   coverage.push({kind:leg.kind,source_ref:leg.source,state:reason?'partial':'complete',count_total:Number(count.rows[0]?.count??NaN),reason});
  }catch(e){coverage.push({kind:leg.kind,source_ref:leg.source,state:'unavailable',count_total:null,reason:e.code==='42501'?'DEPENDENCY_UNAVAILABLE':'source_read_failed'});}
 }
 for(const kind of EXTERNAL_WORK_KINDS.filter(k=>kinds.includes(k)&&(!sources||sources.includes(k==='builder_brief_file'?'builder_files':'github')))){
  const source=kind==='builder_brief_file'?'builder_files':'github';
  const all=(cacheShape?cache.items:[]).filter(r=>r.kind===kind && (!r.personal_to||r.personal_to===sponsor));
  const shapeValid=r=>typeof r.id==='string' && r.id.trim()!=='' && typeof r.title==='string' &&
   typeof r.opened_at==='string' && typeof r.last_activity_at==='string' &&
   typeof r.completed==='boolean' && typeof r.cancelled==='boolean' && r.identity && typeof r.identity==='object' && !Array.isArray(r.identity);
  const rejected=all.filter(r=>!shapeValid(r)||!iso(r.opened_at)||!iso(r.last_activity_at)).length;
  const valid=all.filter(shapeValid).map(r=>normalized(r,{source,kind},at)).filter(Boolean).filter(r=>
   r.completed===live && !r.cancelled && (!filters.id||r.id===filters.id) &&
   (!text||`${r.title} ${r.id}`.toLowerCase().includes(text.toLowerCase())) && r.age>=age);
  items.push(...valid.filter(r=>!cursor||cmp({kind:cursor.kind,id:cursor.id,opened_at:cursor.date,last_activity_at:cursor.date},r,live)<0));
  const historyMissing=live&&kind==='pull_request'&&cache?.completed_pr_history!==true;
  coverage.push({kind,source_ref:source,state:!cacheFresh?'unavailable':kind==='builder_brief_file'||historyMissing||rejected||pagesMissing?'partial':'complete',reason:!cacheFresh?'github_cache_missing_stale_or_incomplete':kind==='builder_brief_file'?'unstructured_brief_pr_relationships':historyMissing?'cache_contains_open_github_work_only':pagesMissing?'external_history_pages_missing_or_invalid':rejected?'rows_rejected_invalid_dates_or_identity':null,count_rejected:rejected,count_total:cacheFresh?valid.length+rejected:null,observed_at:cache?.observed_at??null});
 }
 items.sort((a,b)=>cmp(a,b,live));const page=items.slice(0,limit);
 for(const c of coverage){c.count_returned=page.filter(r=>r.kind===c.kind).length;if(!Number.isFinite(c.count_total)){c.count_total=null;if(c.state==='complete'){c.state='partial';c.reason='count_unavailable';}}}
 const last=page.at(-1),complete=coverage.every(c=>c.state==='complete');
 return {ok:true,schema:'unfinished-work.v1',viewer:sponsor,tenant,items:page,coverage,census_complete:complete,limit,
  next_cursor:items.length>limit?Buffer.from(JSON.stringify({signature,date:live?last.last_activity_at:last.opened_at,kind:last.kind,id:last.id})).toString('base64url'):null,
  source:{source:'work_inventory_census',observed_at:at.toISOString(),freshness:complete?'fresh':'unknown',correlation_id:correlationId,
    safe_explanation:complete?'All selected sources read.':'Incomplete census: inspect source coverage.'}};
}

export function systemWorkTools(){return {'unfinished-work':{write:false,description:'Every unfinished system design and build item, or search the complete Live library. Source and personal authority stay with their owning collections.',
 inputSchema:{type:'object',additionalProperties:false,properties:{source:{type:'string'},kinds:{type:'string'},age:{type:'number',minimum:0},text:{type:'string',maxLength:200},live_library:{type:'boolean'},cursor:{type:'string'},id:{type:'string'},limit:{type:'integer',minimum:1,maximum:500}}},
 handler:(client,actor,args)=>readSystemWorkCensus({client,actor,correlationId:'unfinished-work',...args})}};}
