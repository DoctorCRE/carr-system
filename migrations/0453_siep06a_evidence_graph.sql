-- 0453 / SIEP-06A: a deterministic, redacted evidence graph projected from the
-- canonical Work Request, SIEP evidence-link, job, engineering, and decision
-- ledgers.  This migration creates no graph, task, finding, or evidence store
-- and activates no Production behavior.

create or replace function ops.siep_evidence_node_digest(p_row jsonb)
returns text language sql immutable security definer
set search_path=pg_catalog,ops,public
as $$
  select 'sha256:'||encode(public.digest(
    ops.guidance_import_canonical_json(p_row),'sha256'),'hex')
$$;

create or replace function ops.siep_read_evidence_graph(p_component text default null)
returns jsonb
language plpgsql stable security definer
set search_path=pg_catalog,ops,public
as $$
declare
  k text;
  graph_body jsonb;
begin
  if p_component is not null then
    k:=ops.siep_resolve_package(p_component);
    if k is null then
      raise exception 'known SIEP package or component alias is required';
    end if;
  end if;

  with recursive
  selected_package as (
    select c.package_key
      from ops.siep_package_contract c
     where k is null or c.package_key=k
  ),
  graph_package(package_key) as (
    select package_key from selected_package
    union
    select d.depends_on_package_key
      from ops.siep_program_dependency d
      join graph_package g on g.package_key=d.package_key
  ),
  package_row as (
    select c.*,w.ref work_request_ref,w.title work_request_title,w.state work_request_state,
           w.version work_request_version,w.program_ordinal,w.executor_actor,
           w.captured_at,w.claimed_at,w.closed_at,
           ops.siep_evidence_node_digest(to_jsonb(c)) package_node_digest,
           ops.siep_evidence_node_digest(to_jsonb(w)) work_request_node_digest
      from ops.siep_package_contract c
      join graph_package g on g.package_key=c.package_key
      join ops.work_request w on w.id=c.work_request_id
  ),
  evidence_row as (
    select e.*,w.version current_work_request_version,w.state work_request_state,
           ops.siep_evidence_node_digest(to_jsonb(e)) evidence_link_node_digest,
           ops.siep_current_evidence_digest(e.ledger_kind,e.ledger_id) current_digest,
           case e.ledger_kind
             when 'job_receipt' then jr.created_at
             when 'decision_event' then de.occurred_at
           end canonical_observed_at,
           b.package_key binding_package_key,b.work_request_version binding_work_request_version,
           b.manifest_digest binding_manifest_digest,b.evidence_kind binding_evidence_kind,
           b.job_id binding_job_id,jr.evidence->>'cycle' audit_cycle,
           case when e.ledger_kind='decision_event' then
             ops.siep_current_approval(e.package_key,e.work_request_version,e.evidence_kind)
             else true end authority_current,
           case when e.ledger_kind='job_receipt' then
             jr.id is not null and j.id is not null and ja.id is not null and b.job_id is not null
             and env.id is not null and sp.id is not null and er.id is not null and rf.id is not null
             and jr.kind='completion' and j.state='succeeded' and ja.state='succeeded'
             and j.attempt=jr.attempt and j.definition_key='engineering-slice'
             and j.definition_version=1 and j.payload->>'work_request'=w.ref
             and j.payload->>'manifest_digest'=b.manifest_digest
             and b.package_key=e.package_key and b.work_request_version=e.work_request_version
             and b.manifest_digest=e.manifest_digest and b.evidence_kind=e.evidence_kind
             and env.state_version=b.work_request_version
             and sp.work_request_version=b.work_request_version
             and env.accepted_plan_id=sp.accepted_plan_id
             and er.job_attempt_id=ja.id and er.outcome='claimed_complete'
             and er.slice_ref=env.slice_ref and rf.slice_ref=er.slice_ref
             and rf.state='passed' and rf.reviewer_actor_id=b.bound_by_actor_id
             and rf.reviewer_actor_id<>er.executor_actor_id
             and jr.evidence->>'status'='pass'
             and jr.evidence->>'operation'=case when e.evidence_kind='two_clean_audit_cycles'
               then 'clean_audit_cycle' else e.evidence_kind end
             and case e.evidence_kind
               when 'source' then coalesce(jr.evidence->>'commit_sha','') ~ '^[0-9a-f]{40,64}$'
               when 'tests' then coalesce(jr.evidence->>'result_digest','') ~ '^sha256:[0-9a-f]{64}$'
               when 'readback' then coalesce(jr.evidence->>'target_ref','') ~ '^safe:[a-z0-9][a-z0-9:_./-]*$'
               when 'live_readback' then coalesce(jr.evidence->>'target_ref','') ~ '^safe:[a-z0-9][a-z0-9:_./-]*$'
               when 'rollback' then coalesce(jr.evidence->>'recovery_ref','') ~ '^safe:[a-z0-9][a-z0-9:_./-]*$'
               when 'independent_review' then coalesce(jr.evidence->>'reviewed_artifact_digest','') ~ '^sha256:[0-9a-f]{64}$'
               when 'zero_unresolved_findings' then jr.evidence->>'count'='0'
                 and coalesce(jr.evidence->>'baseline_digest','') ~ '^sha256:[0-9a-f]{64}$'
               when 'zero_blockers' then jr.evidence->>'count'='0'
                 and coalesce(jr.evidence->>'baseline_digest','') ~ '^sha256:[0-9a-f]{64}$'
               when 'material_fix' then coalesce(jr.evidence->>'commit_sha','') ~ '^[0-9a-f]{40,64}$'
               when 'two_clean_audit_cycles' then jr.evidence->>'cycle' in ('1','2')
                 and jr.evidence->>'unresolved_count'='0' and jr.evidence->>'blocker_count'='0'
                 and coalesce(jr.evidence->>'baseline_digest','') ~ '^sha256:[0-9a-f]{64}$'
                 and coalesce(jr.evidence->>'run_id','') ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
               else true end
           when e.ledger_kind='decision_event' then
             de.id is not null and de.verb='siep-joe-decision'
             and de.subject_type='work_request' and de.subject_id=w.id
             and de.actor_id=e.linked_actor_id
             and e.attested_session_principal='carr_authority_joe'
             and de.new_value->>'program_key'='carr-system-integrity-elimination-v1'
             and de.new_value->>'package_key'=e.package_key
             and de.new_value->>'gate'=e.evidence_kind
             and pc.approval_gate=e.evidence_kind
             and de.new_value->>'manifest_digest'=e.manifest_digest
             and de.new_value->>'work_request_version'=e.work_request_version::text
             and de.new_value->>'decision'=case when e.evidence_kind='joe_go_no_go' then 'go' else 'approved' end
           else false end purpose_current
      from ops.siep_evidence_link e
      join graph_package s on s.package_key=e.package_key
      join ops.siep_package_contract pc on pc.package_key=e.package_key
      join ops.work_request w on w.id=pc.work_request_id
      left join ops.job_receipt jr on e.ledger_kind='job_receipt' and jr.id=e.ledger_id
      left join ops.job j on j.id=jr.job_id
      left join ops.job_attempt ja on ja.job_id=j.id and ja.attempt=jr.attempt
      left join ops.siep_job_evidence_binding b on b.job_id=j.id
      left join ops.engineering_execution_envelope env
        on env.job_id=j.id and env.work_request_id=w.id
      left join ops.engineering_slice_plan sp
        on sp.id=env.slice_plan_id and sp.work_request_id=w.id
      left join ops.engineering_slice_receipt er
        on er.envelope_id=env.id and er.job_attempt_id=ja.id and er.work_request_id=w.id
      left join ops.engineering_reviewer_fact rf
        on rf.receipt_id=er.id and rf.work_request_id=w.id
      left join public.event de on e.ledger_kind='decision_event' and de.id=e.ledger_id
  ),
  evidence_health as (
    select e.*,
      e.current_digest is not null
      and e.evidence_digest=e.current_digest
      and e.manifest_digest=ops.siep_manifest_digest()
      and (e.evidence_kind in ('joe_approval','joe_go_no_go') or
        case
          when e.work_request_state='released' then
            e.work_request_version between e.current_work_request_version-1 and e.current_work_request_version
          when e.work_request_state='confirmed_closed' then
            e.work_request_version between e.current_work_request_version-2 and e.current_work_request_version
          else e.work_request_version=e.current_work_request_version
        end)
      and e.canonical_observed_at=e.source_observed_at
      and e.canonical_observed_at<=now()+interval '1 minute'
      and e.purpose_current
      and e.authority_current as is_current,
      array_remove(array[
        case when e.current_digest is null then 'missing_source' end,
        case when e.current_digest is not null and e.evidence_digest<>e.current_digest then 'digest_mismatch' end,
        case when e.manifest_digest<>ops.siep_manifest_digest() then 'manifest_mismatch' end,
        case when e.evidence_kind not in ('joe_approval','joe_go_no_go') and not case
          when e.work_request_state='released' then
            e.work_request_version between e.current_work_request_version-1 and e.current_work_request_version
          when e.work_request_state='confirmed_closed' then
            e.work_request_version between e.current_work_request_version-2 and e.current_work_request_version
          else e.work_request_version=e.current_work_request_version
        end then 'stale_work_request_version' end,
        case when e.canonical_observed_at is distinct from e.source_observed_at then 'observed_at_mismatch' end,
        case when e.canonical_observed_at>now()+interval '1 minute' then 'future_source' end,
        case when not e.purpose_current then 'purpose_or_lineage_mismatch' end,
        case when not e.authority_current then 'superseded_authority' end
      ],null)::text[] health_reasons
    from evidence_row e
  ),
  job_source as (
    select h.id evidence_link_id,h.evidence_kind bound_evidence_kind,jr.*,j.state job_state,j.mode job_mode,
           j.definition_key,j.definition_version,j.attempt job_attempt,
           ja.id job_attempt_id,ja.state attempt_state,
           b.idempotency_key binding_idempotency_key,b.bound_by_actor_id,b.bound_at,
           env.id envelope_id,env.slice_plan_id,env.accepted_plan_id,env.agent_session_id,
           env.state_version,env.canonical_record_digest,env.envelope_digest,
           env.issued_at,env.expires_at,
           sp.plan_digest,sp.accepted_plan_hash,sp.work_request_version slice_work_request_version,
           ap.plan_ref,ap.plan_hash,ap.plan_version,
           er.id engineering_receipt_id,er.executor_actor_id,er.receipt_digest,
           er.outcome engineering_outcome,
           rf.id reviewer_fact_id,rf.reviewer_actor_id,rf.state reviewer_state,
           ops.siep_evidence_node_digest(to_jsonb(jr)) job_receipt_node_digest,
           ops.siep_evidence_node_digest(to_jsonb(j)) job_node_digest,
           ops.siep_evidence_node_digest(to_jsonb(ja)) job_attempt_node_digest,
           ops.siep_evidence_node_digest(to_jsonb(b)) binding_node_digest,
           ops.siep_evidence_node_digest(to_jsonb(env)) envelope_node_digest,
           ops.siep_evidence_node_digest(to_jsonb(sp)) slice_plan_node_digest,
           ops.siep_evidence_node_digest(to_jsonb(ap)) accepted_plan_node_digest,
           ops.siep_evidence_node_digest(to_jsonb(er)) engineering_receipt_node_digest,
           ops.siep_evidence_node_digest(to_jsonb(rf)) reviewer_fact_node_digest
      from evidence_health h
      join ops.job_receipt jr on h.ledger_kind='job_receipt' and jr.id=h.ledger_id
      join ops.job j on j.id=jr.job_id
      join ops.job_attempt ja on ja.job_id=j.id and ja.attempt=jr.attempt
      join ops.siep_job_evidence_binding b on b.job_id=j.id
      left join ops.engineering_execution_envelope env on env.job_id=j.id
      left join ops.engineering_slice_plan sp on sp.id=env.slice_plan_id
      left join ops.sourced_work_request_plan ap on ap.id=env.accepted_plan_id
      left join ops.engineering_slice_receipt er
        on er.envelope_id=env.id and er.job_attempt_id=ja.id
      left join ops.engineering_reviewer_fact rf on rf.receipt_id=er.id
  ),
  authoritative_decision as (
    select d.*,a.slug actor_slug,a.active actor_active,
           ops.siep_evidence_node_digest(to_jsonb(d)) decision_node_digest,
           row_number() over(partition by d.new_value->>'package_key',d.new_value->>'gate'
             order by d.occurred_at,d.id) decision_ordinal,
           lag(d.id) over(partition by d.new_value->>'package_key',d.new_value->>'gate'
             order by d.occurred_at,d.id) prior_decision_id,
           true authoritative,d.new_value->>'package_key' projected_package_key
      from public.event d
      join public.actor a on a.id=d.actor_id
      join graph_package s on s.package_key=d.new_value->>'package_key'
      join ops.siep_package_contract pc on pc.package_key=s.package_key
      join ops.work_request w on w.id=pc.work_request_id and w.id=d.subject_id
     where d.verb='siep-joe-decision'
       and d.subject_type='work_request'
       and a.slug='joe' and a.active
       and d.new_value->>'program_key'='carr-system-integrity-elimination-v1'
       and d.new_value->>'gate'=pc.approval_gate
       and pc.approval_gate in ('joe_approval','joe_go_no_go')
       and d.new_value->>'manifest_digest'=ops.siep_manifest_digest()
       and coalesce(d.new_value->>'work_request_version','') ~ '^[1-9][0-9]*$'
       and case pc.approval_gate
         when 'joe_approval' then d.new_value->>'decision' in ('approved','rejected','revoked')
         when 'joe_go_no_go' then d.new_value->>'decision' in ('go','no_go','revoked')
         else false end
  ),
  attached_decision as (
    select d.*,a.slug actor_slug,a.active actor_active,
           ops.siep_evidence_node_digest(to_jsonb(d)) decision_node_digest,
           null::bigint decision_ordinal,null::uuid prior_decision_id,
           false authoritative,h.package_key projected_package_key
      from evidence_health h
      join public.event d on h.ledger_kind='decision_event' and d.id=h.ledger_id
      join public.actor a on a.id=d.actor_id
  ),
  decision_source as (
    select distinct on (id) * from (
      select * from authoritative_decision
      union all
      select * from attached_decision
    ) decision_union
    order by id,authoritative desc
  ),
  node as (
    select 'package:'||p.package_key node_key,'package' node_type,
      jsonb_build_object('package_key',p.package_key,'lane_key',p.lane_key,
        'minimum_executor_tier',p.minimum_executor_tier,'approval_gate',p.approval_gate,
        'required_evidence_kinds',to_jsonb(p.required_evidence_kinds)) attributes,
      p.package_node_digest node_digest
      from package_row p
    union all
    select 'work_request:'||p.work_request_id,'work_request',
      jsonb_build_object('ref',p.work_request_ref,'title',p.work_request_title,
        'state',p.work_request_state,'version',p.work_request_version,
        'program_ordinal',p.program_ordinal,'executor_actor',p.executor_actor,
        'captured_at',p.captured_at,'claimed_at',p.claimed_at,'closed_at',p.closed_at),
      p.work_request_node_digest
      from package_row p
    union all
    select 'evidence_link:'||h.id,'evidence_link',
      jsonb_build_object('package_key',h.package_key,'evidence_kind',h.evidence_kind,
        'ledger_kind',h.ledger_kind,'work_request_version',h.work_request_version,
        'version_relation',case when h.work_request_version=h.current_work_request_version then 'current' else 'historical' end,
        'manifest_digest',h.manifest_digest,'evidence_digest',h.evidence_digest,
        'current_digest',h.current_digest,'source_observed_at',h.source_observed_at,
        'linked_at',h.linked_at,'note',h.note,'current',h.is_current,
        'health_reasons',to_jsonb(h.health_reasons)),h.evidence_link_node_digest
      from evidence_health h
    union all
    select 'job_receipt:'||j.id,'job_receipt',
      jsonb_build_object('kind',j.kind,'created_at',j.created_at,
        'bound_evidence_kind',j.bound_evidence_kind,
        'status_pass',j.evidence->>'status'='pass',
        'operation_matches',j.evidence->>'operation'=case
          when j.bound_evidence_kind='two_clean_audit_cycles' then 'clean_audit_cycle'
          else j.bound_evidence_kind end)
      ||case
        when j.bound_evidence_kind in ('source','material_fix')
          and coalesce(j.evidence->>'commit_sha','') ~ '^[0-9a-f]{40,64}$'
          then jsonb_build_object('commit_sha',j.evidence->>'commit_sha')
        when j.bound_evidence_kind='tests'
          and coalesce(j.evidence->>'result_digest','') ~ '^sha256:[0-9a-f]{64}$'
          then jsonb_build_object('result_digest',j.evidence->>'result_digest')
        when j.bound_evidence_kind in ('readback','live_readback')
          and coalesce(j.evidence->>'target_ref','') ~ '^safe:[a-z0-9][a-z0-9:_./-]*$'
          then jsonb_build_object('target_ref',j.evidence->>'target_ref')
        when j.bound_evidence_kind='rollback'
          and coalesce(j.evidence->>'recovery_ref','') ~ '^safe:[a-z0-9][a-z0-9:_./-]*$'
          then jsonb_build_object('recovery_ref',j.evidence->>'recovery_ref')
        when j.bound_evidence_kind='independent_review'
          and coalesce(j.evidence->>'reviewed_artifact_digest','') ~ '^sha256:[0-9a-f]{64}$'
          then jsonb_build_object('reviewed_artifact_digest',j.evidence->>'reviewed_artifact_digest')
        when j.bound_evidence_kind in ('zero_unresolved_findings','zero_blockers')
          and j.evidence->>'count'='0'
          and coalesce(j.evidence->>'baseline_digest','') ~ '^sha256:[0-9a-f]{64}$'
          then jsonb_build_object('count',0,'baseline_digest',j.evidence->>'baseline_digest')
        when j.bound_evidence_kind='two_clean_audit_cycles'
          and j.evidence->>'cycle' in ('1','2')
          and coalesce(j.evidence->>'baseline_digest','') ~ '^sha256:[0-9a-f]{64}$'
          and coalesce(j.evidence->>'run_id','') ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
          then jsonb_build_object('cycle',(j.evidence->>'cycle')::integer,
            'run_id',j.evidence->>'run_id','baseline_digest',j.evidence->>'baseline_digest',
            'unresolved_count',case when j.evidence->>'unresolved_count'='0' then 0 end,
            'blocker_count',case when j.evidence->>'blocker_count'='0' then 0 end)
        else '{}'::jsonb end,
      j.job_receipt_node_digest from job_source j
    union all
    select 'job:'||j.job_id,'job',jsonb_build_object('definition_key',j.definition_key,
        'definition_version',j.definition_version,'mode',j.job_mode,'state',j.job_state,
        'attempt',j.job_attempt),j.job_node_digest from job_source j
    union all
    select 'job_attempt:'||j.job_attempt_id,'job_attempt',jsonb_build_object(
        'attempt',j.attempt,'state',j.attempt_state),j.job_attempt_node_digest from job_source j
    union all
    select 'evidence_binding:'||j.job_id,'evidence_binding',jsonb_build_object(
        'package_key',h.package_key,'evidence_kind',h.evidence_kind,
        'work_request_version',h.binding_work_request_version,
        'manifest_digest',h.binding_manifest_digest,'bound_at',j.bound_at),j.binding_node_digest
      from job_source j join evidence_health h on h.id=j.evidence_link_id
    union all
    select 'engineering_envelope:'||j.envelope_id,'engineering_envelope',jsonb_build_object(
        'state_version',j.state_version,'canonical_record_digest',j.canonical_record_digest,
        'envelope_digest',j.envelope_digest,'issued_at',j.issued_at,'expires_at',j.expires_at),
      j.envelope_node_digest
      from job_source j where j.envelope_id is not null
    union all
    select 'engineering_slice_plan:'||j.slice_plan_id,'engineering_slice_plan',jsonb_build_object(
        'work_request_version',j.slice_work_request_version,'plan_digest',j.plan_digest,
        'accepted_plan_hash',j.accepted_plan_hash),j.slice_plan_node_digest
      from job_source j where j.slice_plan_id is not null
    union all
    select 'accepted_plan:'||j.accepted_plan_id,'accepted_plan',jsonb_build_object(
        'plan_ref',j.plan_ref,'plan_hash',j.plan_hash,'plan_version',j.plan_version),
      j.accepted_plan_node_digest
      from job_source j where j.accepted_plan_id is not null
    union all
    select 'engineering_receipt:'||j.engineering_receipt_id,'engineering_receipt',jsonb_build_object(
        'outcome',j.engineering_outcome,'receipt_digest',j.receipt_digest),
      j.engineering_receipt_node_digest
      from job_source j where j.engineering_receipt_id is not null
    union all
    select 'reviewer_fact:'||j.reviewer_fact_id,'reviewer_fact',jsonb_build_object(
        'state',j.reviewer_state,'actor_separated',j.reviewer_actor_id<>j.executor_actor_id,
        'session_independence','deferred_to_siep_03'),j.reviewer_fact_node_digest
      from job_source j where j.reviewer_fact_id is not null
    union all
    select 'decision:'||d.id,'decision',jsonb_build_object(
        'gate',case when d.new_value->>'gate' in ('joe_approval','joe_go_no_go')
          then d.new_value->>'gate' end,
        'decision',case when d.new_value->>'decision' in ('approved','rejected','go','no_go','revoked')
          then d.new_value->>'decision' end,
        'work_request_version',case when coalesce(d.new_value->>'work_request_version','') ~ '^[1-9][0-9]*$'
          then (d.new_value->>'work_request_version')::integer end,
        'manifest_digest',case when coalesce(d.new_value->>'manifest_digest','') ~ '^sha256:[0-9a-f]{64}$'
          then d.new_value->>'manifest_digest' end,
        'occurred_at',d.occurred_at,'authoritative',d.authoritative,
        'decision_ordinal',d.decision_ordinal),d.decision_node_digest
      from decision_source d
    union all
    select distinct 'actor:'||a.id,'actor',jsonb_build_object('slug',a.slug,'active',a.active),
      ops.siep_evidence_node_digest(to_jsonb(a))
      from public.actor a
     where a.id in (
       select linked_actor_id from evidence_health
       union select executor_actor_id from job_source where executor_actor_id is not null
       union select reviewer_actor_id from job_source where reviewer_actor_id is not null
       union select actor_id from decision_source
     )
  ),
  edge as (
    select 'package:'||p.package_key source_key,'authorizes' relation,
           'work_request:'||p.work_request_id target_key,'package_contract:'||p.package_key basis_ref from package_row p
    union all
    select 'package:'||d.package_key,'depends_on','package:'||d.depends_on_package_key,
           'dependency:'||d.package_key||':'||d.depends_on_package_key
      from ops.siep_program_dependency d join graph_package s on s.package_key=d.package_key
    union all
    select 'package:'||h.package_key,'has_evidence','evidence_link:'||h.id,'evidence_link:'||h.id from evidence_health h
    union all
    select 'evidence_link:'||h.id,'attests',
           case h.ledger_kind when 'job_receipt' then 'job_receipt:' else 'decision:' end||h.ledger_id,
           'evidence_link:'||h.id
      from evidence_health h
    union all
    select 'evidence_link:'||h.id,'linked_by','actor:'||h.linked_actor_id,'evidence_link:'||h.id from evidence_health h
    union all
    select 'job_receipt:'||j.id,'receipt_for','job:'||j.job_id,'job_receipt:'||j.id from job_source j
    union all
    select 'job_receipt:'||j.id,'records_attempt','job_attempt:'||j.job_attempt_id,'job_receipt:'||j.id from job_source j
    union all
    select 'job:'||j.job_id,'purpose_bound_by','evidence_binding:'||j.job_id,'evidence_binding:'||j.job_id from job_source j
    union all
    select 'job:'||j.job_id,'authorized_by','engineering_envelope:'||j.envelope_id,'engineering_envelope:'||j.envelope_id
      from job_source j where j.envelope_id is not null
    union all
    select 'engineering_envelope:'||j.envelope_id,'projects','engineering_slice_plan:'||j.slice_plan_id,'engineering_envelope:'||j.envelope_id
      from job_source j where j.envelope_id is not null and j.slice_plan_id is not null
    union all
    select 'engineering_slice_plan:'||j.slice_plan_id,'derives_from','accepted_plan:'||j.accepted_plan_id,'engineering_slice_plan:'||j.slice_plan_id
      from job_source j where j.slice_plan_id is not null and j.accepted_plan_id is not null
    union all
    select 'engineering_receipt:'||j.engineering_receipt_id,'closes','engineering_envelope:'||j.envelope_id,'engineering_receipt:'||j.engineering_receipt_id
      from job_source j where j.engineering_receipt_id is not null
    union all
    select 'engineering_receipt:'||j.engineering_receipt_id,'records_attempt','job_attempt:'||j.job_attempt_id,'engineering_receipt:'||j.engineering_receipt_id
      from job_source j where j.engineering_receipt_id is not null
    union all
    select 'reviewer_fact:'||j.reviewer_fact_id,'reviews','engineering_receipt:'||j.engineering_receipt_id,'reviewer_fact:'||j.reviewer_fact_id
      from job_source j where j.reviewer_fact_id is not null
    union all
    select 'engineering_receipt:'||j.engineering_receipt_id,'executed_by','actor:'||j.executor_actor_id,'engineering_receipt:'||j.engineering_receipt_id
      from job_source j where j.engineering_receipt_id is not null
    union all
    select 'reviewer_fact:'||j.reviewer_fact_id,'reviewed_by','actor:'||j.reviewer_actor_id,'reviewer_fact:'||j.reviewer_fact_id
      from job_source j where j.reviewer_fact_id is not null
    union all
    select 'decision:'||d.id,'decided_by','actor:'||d.actor_id,'decision:'||d.id from decision_source d
    union all
    select 'decision:'||d.id,'decides','package:'||d.projected_package_key,'decision:'||d.id
      from decision_source d
    union all
    select 'decision:'||d.id,'supersedes','decision:'||d.prior_decision_id,'decision:'||d.id
      from decision_source d where d.authoritative and d.prior_decision_id is not null
  ),
  unique_node as (
    select node_key,min(node_type) node_type,min(attributes::text)::jsonb attributes,
           min(node_digest) node_digest
      from node group by node_key
  ),
  unique_edge as (
    select distinct source_key,relation,target_key,basis_ref from edge
  ),
  required_kind as (
    select p.package_key,kind evidence_kind
      from package_row p
      cross join lateral unnest(p.required_evidence_kinds||array['independent_review']) kind
    group by p.package_key,kind
  ),
  coverage as (
    select r.package_key,r.evidence_kind,
      case when r.evidence_kind='two_clean_audit_cycles' then
        (select count(distinct a.audit_cycle) from evidence_health a
          where a.package_key=r.package_key and a.evidence_kind=r.evidence_kind
            and a.audit_cycle in ('1','2') and a.is_current)=2
      else coalesce((select bool_or(a.is_current) from evidence_health a
        where a.package_key=r.package_key and a.evidence_kind=r.evidence_kind
          ),false) end covered
      from required_kind r
  ),
  integrity as (
    select count(*) link_count,
           count(*) filter(where is_current) current_link_count,
           count(*) filter(where not is_current) noncurrent_link_count,
           count(*) filter(where current_digest is null
             or (current_digest is not null and evidence_digest<>current_digest)
             or canonical_observed_at is distinct from source_observed_at
             or canonical_observed_at>now()+interval '1 minute'
             or not coalesce(purpose_current,false)) structural_invalid_link_count,
           count(*) filter(where current_digest is null) missing_source_count,
           count(*) filter(where current_digest is not null and evidence_digest<>current_digest) digest_mismatch_count,
           count(*) filter(where manifest_digest<>ops.siep_manifest_digest()) manifest_mismatch_count,
           count(*) filter(where canonical_observed_at is distinct from source_observed_at) observed_at_mismatch_count,
           count(*) filter(where canonical_observed_at>now()+interval '1 minute') future_source_count,
           count(*) filter(where evidence_kind not in ('joe_approval','joe_go_no_go') and not case
             when work_request_state='released' then
               work_request_version between current_work_request_version-1 and current_work_request_version
             when work_request_state='confirmed_closed' then
               work_request_version between current_work_request_version-2 and current_work_request_version
             else work_request_version=current_work_request_version end) stale_version_count,
           count(*) filter(where not purpose_current) purpose_or_lineage_mismatch_count,
           count(*) filter(where not authority_current) superseded_authority_count
      from evidence_health
  )
  select jsonb_build_object(
    'schema_version','siep-evidence-graph.v1',
    'program_key','carr-system-integrity-elimination-v1',
    'scope',case when k is null then 'program' else 'package:'||k end,
    'manifest_digest',ops.siep_manifest_digest(),
    'nodes',coalesce((select jsonb_agg(jsonb_build_object('key',node_key,'type',node_type,
      'attributes',attributes,'node_digest',node_digest) order by node_key) from unique_node),'[]'::jsonb),
    'edges',coalesce((select jsonb_agg(jsonb_build_object('from',source_key,'relation',relation,
      'to',target_key,'basis_ref',basis_ref,'edge_digest','sha256:'||encode(public.digest(
        ops.guidance_import_canonical_json(jsonb_build_object('from',source_key,'relation',relation,
          'to',target_key,'basis_ref',basis_ref)),'sha256'),'hex'))
      order by source_key,relation,target_key,basis_ref) from unique_edge),'[]'::jsonb),
    'attachments',coalesce((select jsonb_agg(jsonb_build_object(
      'ref','evidence_link:'||id,'package_key',package_key,'evidence_kind',evidence_kind,
      'ledger_ref',case ledger_kind when 'job_receipt' then 'job_receipt:' else 'decision:' end||ledger_id,
      'current',is_current,'status',case when is_current then 'current'
        when current_digest is null then 'missing_target'
        when evidence_digest<>current_digest then 'source_digest_mismatch'
        when manifest_digest<>ops.siep_manifest_digest() then 'stale_manifest'
        when 'stale_work_request_version'=any(health_reasons) then 'stale_work_request_version'
        when not purpose_current then 'incomplete_engineering_chain'
        when not authority_current then 'superseded_or_negative_decision'
        when canonical_observed_at>now()+interval '1 minute' then 'future_source'
        else 'invalid' end,
      'reasons',to_jsonb(health_reasons),
      'baseline_membership',case when evidence_kind in ('zero_unresolved_findings','zero_blockers')
        then 'attested_not_independently_recomputable_until_siep_40' else 'not_applicable' end
      ) order by package_key,evidence_kind,id) from evidence_health),'[]'::jsonb),
    'current_coverage',coalesce((select jsonb_agg(jsonb_build_object('package_key',package_key,
      'evidence_kind',evidence_kind,'covered',covered) order by package_key,evidence_kind)
      from coverage),'[]'::jsonb),
    'integrity',(select jsonb_build_object(
      'valid',structural_invalid_link_count=0 and not exists(select 1 from coverage where not covered),
      'immutable_integrity_valid',structural_invalid_link_count=0,
      'current_coverage_complete',not exists(select 1 from coverage where not covered),
      'link_count',link_count,'current_link_count',current_link_count,
      'noncurrent_link_count',noncurrent_link_count,
      'structural_invalid_link_count',structural_invalid_link_count,
      'missing_current_requirement_count',(select count(*) from coverage where not covered),
      'missing_source_count',missing_source_count,
      'digest_mismatch_count',digest_mismatch_count,'manifest_mismatch_count',manifest_mismatch_count,
      'observed_at_mismatch_count',observed_at_mismatch_count,'future_source_count',future_source_count,
      'stale_work_request_version_count',stale_version_count,
      'purpose_or_lineage_mismatch_count',purpose_or_lineage_mismatch_count,
      'superseded_authority_count',superseded_authority_count,
      'session_independence','deferred_to_siep_03','terminal_authority','siep_06b') from integrity)
  ) into graph_body;

  return graph_body||jsonb_build_object('read_at',now(),
    'graph_digest','sha256:'||encode(public.digest(
      ops.guidance_import_canonical_json(graph_body),'sha256'),'hex'));
end $$;

comment on function ops.siep_read_evidence_graph(text) is
  'SIEP-06A deterministic redacted projection over canonical ledgers. It stores no graph state, grants no execution authority, and does not certify terminal closure.';

revoke all on function ops.siep_evidence_node_digest(jsonb)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
revoke all on function ops.siep_read_evidence_graph(text)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;
grant execute on function ops.siep_read_evidence_graph(text)
  to carr_reader,carr_writer,carr_jobs,carr_authority;

do $$
begin
  if to_regclass('ops.siep_evidence_graph') is not null then
    raise exception 'SIEP-06A must not create a physical evidence graph';
  end if;
  if has_function_privilege('public','ops.siep_read_evidence_graph(text)','execute') then
    raise exception 'public retained SIEP evidence graph execution';
  end if;
  if has_function_privilege('public','ops.siep_evidence_node_digest(jsonb)','execute') then
    raise exception 'public retained SIEP evidence digest helper execution';
  end if;
end $$;
