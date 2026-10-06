-- 0333: outcome-feedback guards must accept the preserve-shape acceptance state.
--
-- THE DEFECT (found 2026-08-27, incident INC-20260827-04, first exposed by the
-- first plan ever accepted under a REQUIRED Work Shape, WR-000005). Migration
-- 0306's accept function unconditionally stamps the acceptance receipt's
-- shape_fixed_surface_ref with 'sourced-plan:<plan_ref>#<hash>', while its
-- preserve-shape branch deliberately leaves the work request's own
-- shape_fixed_surface_ref null and records the frozen shape state in
-- ops.sourced_work_request_plan_shape_binding_receipt instead. Both 0179
-- guards below still required receipt field == work request field, so every
-- shape-required closeout was refused with "only an exact current ready
-- sourced plan may receive outcome feedback". Rule 73381d78: one contract,
-- two readers -- 0306 changed the writer and left these readers unread.
--
-- THE FIX. The exactness test becomes: the fields match (the unshaped and
-- not_required paths, unchanged), OR the acceptance carries a shape-binding
-- receipt whose frozen (disposition, fixed_surface_ref) still matches the
-- work request's current shape state -- so post-acceptance shape drift is
-- still refused. Everything else in both functions is byte-identical to the
-- deployed bodies; this migration is generated from 0179's text with only
-- the two guard conditions replaced.

create or replace function ops.propose_sourced_work_request_outcome_feedback(
  p_work_request text, p_base_version integer, p_plan_hash text,
  p_criterion_results jsonb, p_evidence_refs jsonb, p_blocker_code text,
  p_result_summary text, p_observed_minutes integer, p_interaction_surface text,
  p_heavy_session_used boolean, p_manual_context_transfers integer,
  p_idempotency_key uuid
)
returns table (
  feedback_id uuid, feedback_ref text, feedback_hash text, work_request_id uuid,
  ref text, state text, version integer, plan_id uuid, plan_ref text,
  plan_hash text,
  outcome text, criterion_results jsonb, evidence_refs jsonb, blocker_code text,
  result_summary text, observed_minutes integer, interaction_surface text,
  heavy_session_used boolean, manual_context_transfers integer, replayed boolean
)
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  w ops.work_request%rowtype; p ops.sourced_work_request_plan%rowtype;
  ar ops.sourced_work_request_plan_acceptance_receipt%rowtype;
  f ops.sourced_work_request_outcome_feedback%rowtype;
  normalized_summary text; derived_outcome text; next_feedback_version integer;
  canonical_preimage jsonb; canonical_hash text;
begin
  normalized_summary := btrim(p_result_summary);
  if coalesce(btrim(p_work_request),'') !~ '^WR-[0-9]{1,12}$'
     or p_base_version is null or p_base_version < 1
     or coalesce(p_plan_hash,'') !~ '^sha256:[0-9a-f]{64}$'
     or p_idempotency_key is null
     or coalesce(normalized_summary,'')='' or char_length(normalized_summary)>500
     or p_observed_minutes is null or p_observed_minutes not between 1 and 1440
     or p_interaction_surface not in ('workspace','control_room','mcp','codex','claude_code','other')
     or p_heavy_session_used is null
     or p_manual_context_transfers is null or p_manual_context_transfers not between 0 and 100
     or p_blocker_code not in ('none','evidence_missing','criterion_not_met','external_dependency','system_error')
     or jsonb_typeof(p_criterion_results) is distinct from 'array'
     or jsonb_typeof(p_evidence_refs) is distinct from 'array'
     or jsonb_array_length(p_evidence_refs) not between 1 and 12
     or exists (select 1 from jsonb_array_elements(p_evidence_refs) v
                 where jsonb_typeof(v) <> 'string'
                    or v #>> '{}' !~ '^safe:[a-z0-9][a-z0-9:_./-]*$'
                    or char_length(v #>> '{}') > 300)
     or exists (select v #>> '{}' from jsonb_array_elements(p_evidence_refs) v
                 group by v #>> '{}' having count(*) > 1)
     or exists (select 1 from jsonb_array_elements(p_criterion_results) v
                 where jsonb_typeof(v) <> 'object'
                    or (select array_agg(k order by k) from jsonb_object_keys(v) k)
                         is distinct from array['id','result']::text[]
                    or coalesce(v->>'id','') !~ '^[A-Z][A-Z0-9-]{1,63}$'
                    or v->>'result' not in ('met','not_met','not_observed'))
     or exists (select v->>'id' from jsonb_array_elements(p_criterion_results) v
                 group by v->>'id' having count(*) > 1) then
    raise exception 'outcome feedback requires exact ready plan/version, full criterion results, safe evidence references, bounded observed facts, blocker, summary, and UUID idempotency key';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('program6-outcome-feedback-proposal:' || p_idempotency_key,0));
  select x.* into f from ops.sourced_work_request_outcome_feedback x
   where x.idempotency_key=p_idempotency_key for share;
  if found then
    select x.* into w from ops.work_request x where x.id=f.work_request_id for share;
    select x.* into p from ops.sourced_work_request_plan x where x.id=f.plan_id for share;
    if not found or w.ref is distinct from p_work_request
       or f.work_request_version is distinct from p_base_version
       or p.plan_hash is distinct from p_plan_hash or f.criterion_results is distinct from p_criterion_results
       or f.evidence_refs is distinct from p_evidence_refs or f.blocker_code is distinct from p_blocker_code
       or f.result_summary is distinct from normalized_summary or f.observed_minutes is distinct from p_observed_minutes
       or f.interaction_surface is distinct from p_interaction_surface or f.heavy_session_used is distinct from p_heavy_session_used
       or f.manual_context_transfers is distinct from p_manual_context_transfers then
      raise exception 'idempotency key already names different sourced outcome feedback proposal';
    end if;
    return query select f.id,f.feedback_ref,f.feedback_hash,w.id,w.ref,'ready'::text,f.work_request_version,
      p.id,p.plan_ref,p.plan_hash,f.outcome,f.criterion_results,f.evidence_refs,f.blocker_code,
      f.result_summary,f.observed_minutes,f.interaction_surface,f.heavy_session_used,
      f.manual_context_transfers,true;
    return;
  end if;

  select x.* into w from ops.work_request x where x.ref=p_work_request for update;
  if not found then raise exception 'exact sourced Work Request not found'; end if;
  select x.* into p from ops.sourced_work_request_plan x
   join ops.sourced_work_request_plan_acceptance_receipt ar0 on ar0.plan_id=x.id
   where x.work_request_id=w.id and x.plan_hash=p_plan_hash for share of x;
  select x.* into ar from ops.sourced_work_request_plan_acceptance_receipt x
   where x.work_request_id=w.id and x.plan_id=p.id for share;
  if not found or w.capture_idempotency_key is null
     or w.organization_tenant_id is distinct from 'carr-internal'
     or w.state is distinct from 'ready' or w.version is distinct from p_base_version
     or p.work_request_version >= w.version or ar.result_version is distinct from w.version
     or ar.plan_hash is distinct from p.plan_hash
     or (w.shape_fixed_surface_ref is distinct from ar.shape_fixed_surface_ref
         and not exists (
           select 1 from ops.sourced_work_request_plan_shape_binding_receipt sb
            where sb.plan_acceptance_receipt_id = ar.id
              and sb.work_request_id = w.id
              and sb.disposition is not distinct from w.shape_disposition
              and sb.fixed_surface_ref is not distinct from w.shape_fixed_surface_ref)) then
    raise exception 'only an exact current ready sourced plan may receive outcome feedback';
  end if;
  if (select array_agg(v->>'id' order by v->>'id') from jsonb_array_elements(p_criterion_results) v)
       is distinct from
     (select array_agg(v->>'id' order by v->>'id') from jsonb_array_elements(w.acceptance_criteria) v) then
    raise exception 'outcome feedback criterion results must name the exact full Work Request acceptance-criteria ID set';
  end if;
  if exists (select 1 from jsonb_array_elements(p_criterion_results) v where v->>'result'='not_met') then
    derived_outcome := 'criteria_not_met';
  elsif exists (select 1 from jsonb_array_elements(p_criterion_results) v where v->>'result'='not_observed') then
    derived_outcome := 'inconclusive';
  else
    derived_outcome := 'criteria_met';
  end if;
  if (derived_outcome='criteria_met' and p_blocker_code <> 'none')
     or (derived_outcome='criteria_not_met' and p_blocker_code='none')
     or (derived_outcome='inconclusive' and p_blocker_code not in ('evidence_missing','external_dependency','system_error')) then
    raise exception 'outcome feedback blocker is inconsistent with its derived criterion outcome';
  end if;

  select coalesce(max(x.feedback_version),0)+1 into next_feedback_version
    from ops.sourced_work_request_outcome_feedback x where x.work_request_id=w.id;
  canonical_preimage := ops.sourced_work_request_outcome_feedback_preimage(
    w.id,p.id,ar.id,p_criterion_results,p_evidence_refs,derived_outcome,p_blocker_code,
    normalized_summary,p_observed_minutes,p_interaction_surface,p_heavy_session_used,
    p_manual_context_transfers);
  canonical_hash := ops.sourced_work_request_outcome_feedback_digest(canonical_preimage);
  if canonical_preimage is null or canonical_hash is null then
    raise exception 'exact ready-plan outcome-feedback preimage is unavailable';
  end if;
  if exists (select 1 from ops.sourced_work_request_outcome_feedback x
              where x.work_request_id=w.id and x.feedback_hash=canonical_hash) then
    raise exception 'the exact sourced outcome feedback already exists under a different idempotency key';
  end if;
  insert into ops.sourced_work_request_outcome_feedback
    (work_request_id,feedback_version,idempotency_key,work_request_version,plan_id,
     plan_acceptance_receipt_id,preimage,criterion_results,evidence_refs,outcome,
     blocker_code,result_summary,observed_minutes,interaction_surface,heavy_session_used,
     manual_context_transfers,feedback_hash,feedback_ref)
  values
    (w.id,next_feedback_version,p_idempotency_key,w.version,p.id,ar.id,canonical_preimage,
     p_criterion_results,p_evidence_refs,derived_outcome,p_blocker_code,normalized_summary,
     p_observed_minutes,p_interaction_surface,p_heavy_session_used,p_manual_context_transfers,
     canonical_hash,'OUTCOME-' || substr(canonical_hash,8,12) || '-v' || next_feedback_version)
  returning * into f;
  return query select f.id,f.feedback_ref,f.feedback_hash,w.id,w.ref,w.state,w.version,
    p.id,p.plan_ref,p.plan_hash,f.outcome,f.criterion_results,f.evidence_refs,f.blocker_code,
    f.result_summary,f.observed_minutes,f.interaction_surface,f.heavy_session_used,
    f.manual_context_transfers,false;
end;
$$;


create or replace function ops.accept_sourced_work_request_outcome_feedback(
  p_work_request text, p_base_version integer, p_feedback_hash text,
  p_idempotency_key uuid
)
returns table (
  work_request_id uuid, ref text, state text, version integer,
  feedback_id uuid, feedback_ref text, feedback_hash text, plan_ref text, plan_hash text,
  outcome text, criterion_results jsonb, evidence_refs jsonb, blocker_code text,
  result_summary text, observed_minutes integer, interaction_surface text,
  heavy_session_used boolean, manual_context_transfers integer,
  accepted_by_actor_slug text, accepted_at timestamptz, replayed boolean
)
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  actor_slug text; a public.actor%rowtype; w ops.work_request%rowtype;
  p ops.sourced_work_request_plan%rowtype;
  ar ops.sourced_work_request_plan_acceptance_receipt%rowtype;
  f ops.sourced_work_request_outcome_feedback%rowtype;
  fr ops.sourced_work_request_outcome_feedback_acceptance_receipt%rowtype;
  canonical_preimage jsonb; canonical_hash text;
begin
  if coalesce(btrim(p_work_request),'') !~ '^WR-[0-9]{1,12}$'
     or p_base_version is null or p_base_version < 1
     or coalesce(p_feedback_hash,'') !~ '^sha256:[0-9a-f]{64}$'
     or p_idempotency_key is null then
    raise exception 'independent outcome-feedback acceptance requires exact ready Work Request/version, feedback hash, and UUID idempotency key';
  end if;
  actor_slug := ops.authority_actor_slug();
  select x.* into a from public.actor x where x.slug=actor_slug and x.active and x.kind='human' for share;
  if not found then raise exception 'authority session user is not an active human actor'; end if;

  perform pg_advisory_xact_lock(hashtextextended('program6-outcome-feedback-acceptance:' || p_idempotency_key,0));
  select x.* into fr from ops.sourced_work_request_outcome_feedback_acceptance_receipt x
   where x.idempotency_key=p_idempotency_key for share;
  if found then
    select x.* into f from ops.sourced_work_request_outcome_feedback x where x.id=fr.feedback_id for share;
    select x.* into w from ops.work_request x where x.id=fr.work_request_id for share;
    select x.* into p from ops.sourced_work_request_plan x where x.id=f.plan_id for share;
    if not found or w.ref is distinct from p_work_request
       or fr.base_version is distinct from p_base_version
       or fr.result_version is distinct from p_base_version or f.feedback_hash is distinct from p_feedback_hash
       or fr.feedback_hash is distinct from p_feedback_hash or fr.accepted_by_actor_id is distinct from a.id then
      raise exception 'idempotency key already names different sourced outcome feedback acceptance';
    end if;
    return query select w.id,w.ref,'ready'::text,fr.result_version,f.id,f.feedback_ref,f.feedback_hash,
      p.plan_ref,p.plan_hash,f.outcome,f.criterion_results,f.evidence_refs,f.blocker_code,f.result_summary,
      f.observed_minutes,f.interaction_surface,f.heavy_session_used,f.manual_context_transfers,
      a.slug,fr.accepted_at,true;
    return;
  end if;

  select x.* into w from ops.work_request x where x.ref=p_work_request for update;
  if not found then raise exception 'exact sourced Work Request not found'; end if;
  select x.* into f from ops.sourced_work_request_outcome_feedback x
   where x.work_request_id=w.id and x.feedback_hash=p_feedback_hash for share;
  if not found then raise exception 'exact sourced outcome feedback proposal not found'; end if;
  select x.* into p from ops.sourced_work_request_plan x where x.id=f.plan_id for share;
  select x.* into ar from ops.sourced_work_request_plan_acceptance_receipt x
   where x.id=f.plan_acceptance_receipt_id for share;
  if not found or w.capture_idempotency_key is null
     or w.organization_tenant_id is distinct from 'carr-internal'
     or w.state is distinct from 'ready' or w.version is distinct from p_base_version
     or f.work_request_version is distinct from p_base_version
     or ar.work_request_id is distinct from w.id or ar.plan_id is distinct from p.id
     or ar.result_version is distinct from w.version or ar.plan_hash is distinct from p.plan_hash
     or ar.accepted_by_actor_id is null
     or (w.shape_fixed_surface_ref is distinct from ar.shape_fixed_surface_ref
         and not exists (
           select 1 from ops.sourced_work_request_plan_shape_binding_receipt sb
            where sb.plan_acceptance_receipt_id = ar.id
              and sb.work_request_id = w.id
              and sb.disposition is not distinct from w.shape_disposition
              and sb.fixed_surface_ref is not distinct from w.shape_fixed_surface_ref)) then
    raise exception 'only an exact current ready sourced plan may receive human outcome-feedback acceptance';
  end if;
  canonical_preimage := ops.sourced_work_request_outcome_feedback_preimage(
    w.id,p.id,ar.id,f.criterion_results,f.evidence_refs,f.outcome,f.blocker_code,
    f.result_summary,f.observed_minutes,f.interaction_surface,f.heavy_session_used,
    f.manual_context_transfers);
  canonical_hash := ops.sourced_work_request_outcome_feedback_digest(canonical_preimage);
  if canonical_preimage is distinct from f.preimage
     or canonical_hash is distinct from f.feedback_hash
     or canonical_hash is distinct from p_feedback_hash then
    raise exception 'sourced outcome-feedback preimage is stale or does not match its exact hash';
  end if;
  if exists (select 1 from ops.sourced_work_request_outcome_feedback_acceptance_receipt x where x.feedback_id=f.id) then
    raise exception 'the sourced outcome-feedback proposal already has an immutable acceptance receipt';
  end if;
  insert into ops.sourced_work_request_outcome_feedback_acceptance_receipt
    (work_request_id,feedback_id,idempotency_key,base_version,feedback_hash,
     accepted_by_actor_id,result_version)
  values (w.id,f.id,p_idempotency_key,p_base_version,p_feedback_hash,a.id,w.version)
  returning * into fr;
  -- No Work Request UPDATE: this accepts a measurement, not an execution result.
  return query select w.id,w.ref,w.state,w.version,f.id,f.feedback_ref,f.feedback_hash,
    p.plan_ref,p.plan_hash,f.outcome,f.criterion_results,f.evidence_refs,f.blocker_code,f.result_summary,
    f.observed_minutes,f.interaction_surface,f.heavy_session_used,f.manual_context_transfers,
    a.slug,fr.accepted_at,false;
end;
$$;
