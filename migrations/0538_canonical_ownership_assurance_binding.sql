-- WR-000130: an activated A2 lease binds its accepted source scope; an A3a
-- compiler input binds its own assurance preimage.  These are distinct values.
do $wr130_manifest_binding$
declare
  target regprocedure := to_regprocedure(
    'ops.record_assurance_execution_manifest(uuid,uuid,bigint,text,jsonb,jsonb,jsonb,jsonb,uuid)');
  before_proc pg_catalog.pg_proc%rowtype;
  after_proc pg_catalog.pg_proc%rowtype;
  definition text;
  declaration_marker constant text := $marker$
declare live jsonb; context jsonb; l ops.canonical_ownership_lease%rowtype;
$marker$;
  declaration_replacement constant text := $replacement$
declare live jsonb; context jsonb; l ops.canonical_ownership_lease%rowtype;
        activated_runtime boolean; runtime_for_lease boolean; accepted_scope_digest text;
$replacement$;
  predecessor_guard constant text := $guard$
  if p_compiler_input#>>'{assurance_slice,ownership_contract_digest}'
       is distinct from l.contract_digest then
    return ops.assurance_refusal('ASSURANCE_DIGEST_MISMATCH',
      'compiler_input.assurance_slice.ownership_contract_digest',
      to_jsonb(l.contract_digest),
      p_compiler_input#>'{assurance_slice,ownership_contract_digest}');
  end if;$guard$;
  activated_guard constant text := $activated$
  -- An activated lease is identified by its ownership-session runtime, never
  -- merely by the presence of a source scope.  Direct legacy leases can share
  -- that plan and scope.
  select exists(
    select 1 from ops.canonical_ownership_runtime_session rs
     where rs.organization_tenant_id=l.organization_tenant_id
       and rs.subject_envelope_id=l.subject_envelope_id
       and rs.ownership_session_ref=l.holder_session_ref)
    into runtime_for_lease;
  if runtime_for_lease then
    select exists(
      select 1
        from ops.canonical_ownership_runtime_session rs
        join ops.canonical_ownership_issuer_generation g
          on g.principal=session_user and g.state in ('active','draining')
       where rs.organization_tenant_id=l.organization_tenant_id
         and rs.subject_envelope_id=l.subject_envelope_id
         and rs.ownership_session_ref=l.holder_session_ref
         and rs.issuer_principal=session_user
         and rs.state='active' and rs.expires_at>clock_timestamp())
      into activated_runtime;
    if not activated_runtime then
      return ops.assurance_refusal('IDENTITY_CONTEXT_INVALID','manifest.issuer_runtime',
        '"current issuer runtime for this lease subject"'::jsonb,
        '"missing_or_stale"'::jsonb);
    end if;
    select scope.scope_digest into accepted_scope_digest
      from ops.source_merge_plan_scope scope
      join ops.sourced_work_request_plan_acceptance_receipt acceptance
        on acceptance.id=scope.acceptance_receipt_id
     where scope.organization_tenant_id=l.organization_tenant_id
       and scope.work_request_id=l.work_request_id
       and scope.accepted_plan_id=l.accepted_plan_id
       and acceptance.result_version=l.work_request_version;
    if not found or l.contract_digest is distinct from accepted_scope_digest then
      return ops.assurance_refusal('ASSURANCE_BINDING_STALE','lease.contract_digest',
        to_jsonb(accepted_scope_digest),to_jsonb(l.contract_digest));
    end if;
  elsif p_compiler_input#>>'{assurance_slice,ownership_contract_digest}'
          is distinct from l.contract_digest then
    -- Legacy leases retain the historic compiler-to-lease equality rule.
    return ops.assurance_refusal('ASSURANCE_DIGEST_MISMATCH',
      'compiler_input.assurance_slice.ownership_contract_digest',
      to_jsonb(l.contract_digest),
      p_compiler_input#>'{assurance_slice,ownership_contract_digest}');
  end if;$activated$;
  lineage_guard constant text := $lineage$
     or p_manifest#>>'{input_bindings,assurance_slice_ownership_contract_digest}' is distinct from l.contract_digest$lineage$;
  lineage_replacement constant text := $lineage_replacement$
     or p_manifest#>>'{input_bindings,assurance_slice_ownership_contract_digest}'
          is distinct from p_compiler_input#>>'{assurance_slice,ownership_contract_digest}'$lineage_replacement$;
begin
  if target is null then
    raise exception 'WR130 manifest entrypoint is absent';
  end if;
  select * into strict before_proc from pg_catalog.pg_proc where oid=target::oid;
  definition := pg_catalog.pg_get_functiondef(target::oid);
  if (length(definition)-length(replace(definition,declaration_marker,'')))/length(declaration_marker) <> 1
     or (length(definition)-length(replace(definition,predecessor_guard,'')))/length(predecessor_guard) <> 1
     or (length(definition)-length(replace(definition,lineage_guard,'')))/length(lineage_guard) <> 1 then
    raise exception 'WR130 predecessor manifest body drifted';
  end if;
  definition := replace(definition,declaration_marker,declaration_replacement);
  definition := replace(definition,predecessor_guard,activated_guard);
  definition := replace(definition,lineage_guard,lineage_replacement);
  execute definition;
  select * into strict after_proc from pg_catalog.pg_proc where oid=target::oid;
  if (after_proc.proowner,after_proc.prosecdef,after_proc.proconfig,after_proc.proargtypes,
      after_proc.prorettype,after_proc.provolatile,after_proc.proparallel) is distinct from
     (before_proc.proowner,before_proc.prosecdef,before_proc.proconfig,before_proc.proargtypes,
      before_proc.prorettype,before_proc.provolatile,before_proc.proparallel) then
    raise exception 'WR130 manifest replacement changed security posture';
  end if;
end $wr130_manifest_binding$;

-- The issuer gets one guarded persistence door and its non-authorizing
-- currentness read.  Both require the same live issuer/runtime identity.
-- This migration adds no lifecycle, kernel, or PUBLIC execution grant.
revoke all on function ops.record_assurance_execution_manifest(
  uuid,uuid,bigint,text,jsonb,jsonb,jsonb,jsonb,uuid) from public;
grant execute on function ops.record_assurance_execution_manifest(
  uuid,uuid,bigint,text,jsonb,jsonb,jsonb,jsonb,uuid) to carr_ownership_issuer;
revoke all on function ops.assurance_manifest_currentness(
  uuid,text,text,text,text,text,uuid) from public;
grant execute on function ops.assurance_manifest_currentness(
  uuid,text,text,text,text,text,uuid) to carr_ownership_issuer;

-- The issuer lifecycle derives activated A2 claims from the accepted source
-- scope and registered slice resources.  The compiler contract has path claims
-- only, so validate those exact paths and independently require the exact
-- registered resource set.  Legacy leases keep the original no-nonpath rule.
do $wr130_activated_claims$
declare
  target regprocedure := to_regprocedure(
    'ops.assurance_validate_compiler_input(uuid,jsonb,jsonb)');
  before_proc pg_catalog.pg_proc%rowtype;
  after_proc pg_catalog.pg_proc%rowtype;
  definition text;
  declaration_marker constant text := $marker$
        planned_check jsonb; expected_claims jsonb; expected_dependencies jsonb;
$marker$;
  declaration_replacement constant text := $replacement$
        planned_check jsonb; expected_claims jsonb; expected_dependencies jsonb;
        expected_resources jsonb; expected_declared_resources jsonb;
        activated_runtime boolean;
$replacement$;
  dependencies_marker constant text := $marker$
  select coalesce(jsonb_agg(value order by ops.assurance_digest(value)),'[]'::jsonb)
    into expected_coord_dependencies from (
      select jsonb_build_object(
        'slice_ref',d.dependency_slice_ref,'state',d.required_state,
        'evidence_digest',case when d.required_state='independently_verified'
          then ops.assurance_digest(f.fact) else r.receipt_digest end) value
        from ops.canonical_ownership_dependency d
        join ops.engineering_slice_receipt r on r.id=d.observed_receipt_id
        left join ops.engineering_reviewer_fact f on f.id=d.observed_reviewer_fact_id
       where d.lease_id=l.id) q;
$marker$;
  dependencies_replacement constant text := $replacement$
  select coalesce(jsonb_agg(value order by ops.assurance_digest(value)),'[]'::jsonb)
    into expected_coord_dependencies from (
      select jsonb_build_object(
        'slice_ref',d.dependency_slice_ref,'state',d.required_state,
        'evidence_digest',case when d.required_state='independently_verified'
          then ops.assurance_digest(f.fact) else r.receipt_digest end) value
        from ops.canonical_ownership_dependency d
        join ops.engineering_slice_receipt r on r.id=d.observed_receipt_id
        left join ops.engineering_reviewer_fact f on f.id=d.observed_reviewer_fact_id
       where d.lease_id=l.id) q;
  select exists(
    select 1 from ops.canonical_ownership_runtime_session rs
     where rs.organization_tenant_id=l.organization_tenant_id
       and rs.subject_envelope_id=l.subject_envelope_id
       and rs.ownership_session_ref=l.holder_session_ref)
    into activated_runtime;
  select coalesce(jsonb_agg(value order by ops.assurance_digest(value)),'[]'::jsonb)
    into expected_resources from (
      select jsonb_build_object('resource',claim_value) value
        from ops.canonical_ownership_claim
       where lease_id=l.id and claim_kind='resource') q;
  select coalesce(jsonb_agg(jsonb_build_object('resource',ref)
      order by ops.assurance_digest(jsonb_build_object('resource',ref))),'[]'::jsonb)
    into expected_declared_resources from (
      select distinct value ref from jsonb_array_elements_text(
        (selected->'declared_resource_refs')||(selected->'declared_component_refs'))) q;
$replacement$;
  nonpath_guard constant text := $guard$
     or exists(select 1 from ops.canonical_ownership_claim
                where lease_id=l.id and claim_kind<>'path')
$guard$;
  activated_nonpath_guard constant text := $replacement$
     or (not activated_runtime and exists(
          select 1 from ops.canonical_ownership_claim
           where lease_id=l.id and claim_kind<>'path'))
     or (activated_runtime and (
          expected_resources is distinct from expected_declared_resources
          or exists(select 1 from ops.canonical_ownership_claim
                     where lease_id=l.id and claim_kind not in ('path','resource'))))
$replacement$;
begin
  if target is null then raise exception 'WR130 compiler validator is absent'; end if;
  select * into strict before_proc from pg_catalog.pg_proc where oid=target::oid;
  definition:=pg_catalog.pg_get_functiondef(target::oid);
  if (length(definition)-length(replace(definition,declaration_marker,'')))/length(declaration_marker)<>1
     or (length(definition)-length(replace(definition,dependencies_marker,'')))/length(dependencies_marker)<>1
     or (length(definition)-length(replace(definition,nonpath_guard,'')))/length(nonpath_guard)<>1 then
    raise exception 'WR130 compiler claim predecessor body drifted';
  end if;
  definition:=replace(definition,declaration_marker,declaration_replacement);
  definition:=replace(definition,dependencies_marker,dependencies_replacement);
  definition:=replace(definition,nonpath_guard,activated_nonpath_guard);
  execute definition;
  select * into strict after_proc from pg_catalog.pg_proc where oid=target::oid;
  if (after_proc.proowner,after_proc.prosecdef,after_proc.proconfig,after_proc.proargtypes,
      after_proc.prorettype,after_proc.provolatile,after_proc.proparallel) is distinct from
     (before_proc.proowner,before_proc.prosecdef,before_proc.proconfig,before_proc.proargtypes,
      before_proc.prorettype,before_proc.provolatile,before_proc.proparallel) then
    raise exception 'WR130 compiler claim replacement changed security posture';
  end if;
end $wr130_activated_claims$;

-- 0532a deliberately releases the A2 lease before it writes the terminal
-- claimed_complete receipt.  Assurance append records therefore need a narrow
-- proof for that one terminal transition; they must not treat a released lease
-- as live authority to create another manifest.
create or replace function ops.assurance_terminal_receipt_lineage_current(
  p_lease_id uuid,
  p_receipt_id uuid,
  p_now timestamptz
) returns jsonb
language plpgsql security definer
set search_path=pg_catalog,ops,public
as $$
declare
  l ops.canonical_ownership_lease%rowtype;
  r ops.engineering_slice_receipt%rowtype;
  e ops.engineering_execution_envelope%rowtype;
  j ops.job%rowtype;
  a ops.job_attempt%rowtype;
  rs ops.canonical_ownership_runtime_session%rowtype;
  issuer_generation ops.canonical_ownership_issuer_generation%rowtype;
  terminal_event ops.canonical_ownership_lease_event%rowtype;
begin
  if p_lease_id is null or p_receipt_id is null or p_now is null then
    return ops.assurance_refusal('ASSURANCE_BINDING_STALE','lease.terminal_lineage',
      '"released lease and claimed-complete receipt"'::jsonb,'"missing"'::jsonb);
  end if;
  select * into l from ops.canonical_ownership_lease where id=p_lease_id for key share;
  if l.id is null then
    return ops.assurance_refusal('ASSURANCE_BINDING_STALE','lease.terminal_lineage',
      '"existing released lease"'::jsonb,'"missing"'::jsonb);
  end if;
  perform pg_advisory_xact_lock(hashtextextended('canonical-ownership:'||l.organization_tenant_id,0));
  lock table ops.canonical_ownership_lease in share mode;
  select * into l from ops.canonical_ownership_lease where id=p_lease_id for key share;
  select * into r from ops.engineering_slice_receipt where id=p_receipt_id for key share;
  select * into e from ops.engineering_execution_envelope where id=r.envelope_id for key share;
  select * into j from ops.job where id=e.job_id for key share;
  select * into a from ops.job_attempt where id=r.job_attempt_id for key share;
  select * into rs from ops.canonical_ownership_runtime_session
   where organization_tenant_id=l.organization_tenant_id
     and subject_envelope_id=l.subject_envelope_id
     and ownership_session_ref=l.holder_session_ref
   for key share;
  select * into issuer_generation from ops.canonical_ownership_issuer_generation
   where principal=rs.issuer_principal and state in ('active','draining')
   for key share;
  select * into terminal_event from ops.canonical_ownership_lease_event
   where lease_id=l.id and event_kind='released'
     and cause='{"reason":"release_before_terminal"}'::jsonb
     and occurred_at=l.released_at
   order by id desc limit 1 for key share;
  if l.state is distinct from 'released' or l.released_at is null
     or l.expires_at<=l.released_at or terminal_event.id is null
     or terminal_event.organization_tenant_id is distinct from l.organization_tenant_id
     or terminal_event.fencing_generation is distinct from l.fencing_generation
     or terminal_event.actor_id is distinct from l.holder_actor_id
     or terminal_event.session_ref is distinct from l.holder_session_ref
     or terminal_event.host_ref is distinct from l.holder_host_ref
     or r.id is null or r.outcome is distinct from 'claimed_complete'
     or e.id is null or e.id is distinct from l.subject_envelope_id
     or r.work_request_id is distinct from l.work_request_id
     or r.slice_ref is distinct from l.slice_ref
     or r.executor_actor_id is distinct from l.holder_actor_id
     or e.work_request_id is distinct from l.work_request_id
     or e.accepted_plan_id is distinct from l.accepted_plan_id
     or e.slice_plan_id is distinct from l.slice_plan_id
     or e.slice_ref is distinct from l.slice_ref
     or j.id is null or j.definition_key is distinct from 'engineering-slice'
     or j.state is distinct from 'succeeded' or j.attempt is distinct from rs.attempt
     or a.id is null or a.job_id is distinct from j.id or a.attempt is distinct from j.attempt
     or a.state is distinct from 'succeeded' or a.ended_at is null
     or rs.capability_token_hash is distinct from encode(public.digest(a.lease_token::text,'sha256'),'hex')
     or rs.id is null or rs.state is distinct from 'expired' or rs.phase is distinct from 'build'
     or rs.actor_id is distinct from l.holder_actor_id
     or rs.actor_slug is distinct from l.holder_actor_slug
     or rs.work_request_id is distinct from l.work_request_id
     or rs.accepted_plan_id is distinct from l.accepted_plan_id
     or rs.runtime_session_ref is distinct from e.envelope#>>'{agent_session,id}'
     or rs.execution_host_ref is distinct from l.holder_host_ref
     or issuer_generation.principal is null then
    return ops.assurance_refusal('ASSURANCE_BINDING_STALE','lease.terminal_lineage',
      '"0532a release-before-terminal receipt lineage"'::jsonb,'"mismatch"'::jsonb);
  end if;
  return jsonb_build_object('ok',true,'lease_id',l.id,'receipt_id',r.id,
    'released_at',l.released_at,'evaluated_at',p_now);
end $$;

create or replace function ops.assurance_terminal_evidence_lineage_current(
  p_lease_id uuid,
  p_receipt_id uuid,
  p_now timestamptz,
  p_lease_token uuid,
  p_fencing_generation bigint
) returns jsonb
language plpgsql security definer
set search_path=pg_catalog,ops,public
as $$
declare live jsonb; l ops.canonical_ownership_lease%rowtype;
        rs ops.canonical_ownership_runtime_session%rowtype;
begin
  live:=ops.assurance_terminal_receipt_lineage_current(p_lease_id,p_receipt_id,p_now);
  if not coalesce((live->>'ok')::boolean,false) then return live; end if;
  select * into l from ops.canonical_ownership_lease where id=p_lease_id for key share;
  select * into rs from ops.canonical_ownership_runtime_session
   where organization_tenant_id=l.organization_tenant_id
     and subject_envelope_id=l.subject_envelope_id
     and ownership_session_ref=l.holder_session_ref for key share;
  if p_lease_token is distinct from l.lease_token
     or p_fencing_generation is distinct from l.fencing_generation then
    return ops.assurance_refusal('ASSURANCE_BINDING_STALE','lease.terminal_authorization',
      '"exact released lease token and fence"'::jsonb,'"mismatch"'::jsonb);
  end if;
  return live;
end $$;

create or replace function ops.assurance_append_lineage_current(
  p_lease_id uuid,
  p_receipt_id uuid,
  p_now timestamptz,
  p_lease_token uuid default null,
  p_fencing_generation bigint default null
) returns jsonb
language plpgsql security definer
set search_path=pg_catalog,ops,public
as $$
declare state text;
begin
  select l.state into state from ops.canonical_ownership_lease l where l.id=p_lease_id;
  if state='active' then
    if p_lease_token is not null or p_fencing_generation is not null then
      return ops.canonical_ownership_validate_live(
        p_lease_id,p_lease_token,p_fencing_generation,true);
    end if;
    return ops.assurance_lease_lineage_current(p_lease_id,p_now);
  end if;
  if state='released' then
    if p_lease_token is null or p_fencing_generation is null then
      return ops.assurance_terminal_receipt_lineage_current(p_lease_id,p_receipt_id,p_now);
    end if;
    return ops.assurance_terminal_evidence_lineage_current(
      p_lease_id,p_receipt_id,p_now,p_lease_token,p_fencing_generation);
  end if;
  return ops.assurance_refusal('ASSURANCE_BINDING_STALE','lease.currentness',
    '"active lease or 0532a terminal release"'::jsonb,to_jsonb(state));
end $$;

revoke all on function ops.assurance_terminal_receipt_lineage_current(uuid,uuid,timestamp with time zone) from public;
revoke all on function ops.assurance_terminal_evidence_lineage_current(uuid,uuid,timestamp with time zone,uuid,bigint) from public;
revoke all on function ops.assurance_append_lineage_current(uuid,uuid,timestamp with time zone,uuid,bigint) from public;

do $wr130_terminal_append$
declare
  target regprocedure;
  before_proc pg_catalog.pg_proc%rowtype;
  after_proc pg_catalog.pg_proc%rowtype;
  definition text;
  predecessor text;
  replacement text;
begin
  foreach target in array array[
    'ops.record_assurance_evidence_extension(uuid,uuid,uuid,jsonb,text,uuid)'::regprocedure,
    'ops.record_assurance_review_extension(uuid,uuid,uuid,jsonb,text,uuid)'::regprocedure,
    'ops.record_assurance_owner_acceptance(uuid,uuid,text,jsonb,text,uuid)'::regprocedure
  ] loop
    select * into strict before_proc from pg_catalog.pg_proc where oid=target::oid;
    definition:=pg_catalog.pg_get_functiondef(target::oid);
    if target::text like 'ops.record_assurance_evidence_extension%' then
      predecessor:=$old$
  live:=ops.canonical_ownership_validate_live(m.lease_id,p_lease_token,m.fencing_generation,true);
  if not coalesce((live->>'ok')::boolean,false) then return live; end if;
  lock table ops.canonical_ownership_lease in share mode;
  live:=ops.canonical_ownership_validate_live(m.lease_id,p_lease_token,m.fencing_generation,true);
  if not coalesce((live->>'ok')::boolean,false) then return live; end if;$old$;
      replacement:=$new$
  live:=ops.assurance_append_lineage_current(m.lease_id,p_receipt_id,clock_timestamp(),p_lease_token,m.fencing_generation);
  if not coalesce((live->>'ok')::boolean,false) then return live; end if;
  lock table ops.canonical_ownership_lease in share mode;
  live:=ops.assurance_append_lineage_current(m.lease_id,p_receipt_id,clock_timestamp(),p_lease_token,m.fencing_generation);
  if not coalesce((live->>'ok')::boolean,false) then return live; end if;$new$;
    else
      -- Human review and owner acceptance are separate owner-only append
      -- doors.  Their context is the persisted reviewer/authority identity,
      -- not the builder's runtime, which expired at the 0532a transition.
      if (length(definition)-length(replace(definition,
          'context:=ops.canonical_ownership_context();','')))
         /length('context:=ops.canonical_ownership_context();') <> 1 then
        raise exception 'WR130 human append context predecessor drifted: %',target;
      end if;
      definition:=replace(definition,
        'context:=ops.canonical_ownership_context();',
        'context:=ops.canonical_ownership_context_v1();');
      predecessor:=$old$
  select lease_id into lease_probe from ops.assurance_execution_manifest
   where id=p_review_manifest_id;
  if lease_probe is not null then
    perform pg_advisory_xact_lock(hashtextextended('assurance-lease-scan',0));
    lineage_current:=ops.assurance_lease_lineage_current(lease_probe,clock_timestamp());
    if not coalesce((lineage_current->>'ok')::boolean,false) then return lineage_current; end if;
    lock table ops.canonical_ownership_lease in share mode;
    lineage_current:=ops.assurance_lease_lineage_current(lease_probe,clock_timestamp());
    if not coalesce((lineage_current->>'ok')::boolean,false) then return lineage_current; end if;
  end if;$old$;
      replacement:=$new$
  select lease_id into lease_probe from ops.assurance_execution_manifest
   where id=p_review_manifest_id;
  if lease_probe is not null and exists (
    select 1 from ops.assurance_evidence_extension where id=p_evidence_id) then
    perform pg_advisory_xact_lock(hashtextextended('assurance-lease-scan',0));
    lineage_current:=ops.assurance_append_lineage_current(lease_probe,
      (select receipt_id from ops.assurance_evidence_extension where id=p_evidence_id),clock_timestamp());
    if not coalesce((lineage_current->>'ok')::boolean,false) then return lineage_current; end if;
    lock table ops.canonical_ownership_lease in share mode;
    lineage_current:=ops.assurance_append_lineage_current(lease_probe,
      (select receipt_id from ops.assurance_evidence_extension where id=p_evidence_id),clock_timestamp());
    if not coalesce((lineage_current->>'ok')::boolean,false) then return lineage_current; end if;
  end if;$new$;
    end if;
    if (length(definition)-length(replace(definition,predecessor,'')))/length(predecessor) <> 1 then
      raise exception 'WR130 terminal append predecessor body drifted: %',target;
    end if;
    definition:=replace(definition,predecessor,replacement);
    if target::text like 'ops.record_assurance_evidence_extension%' then
      if (length(definition)-length(replace(definition,
          'or e.envelope#>>''{agent_session,id}'' is distinct from l.holder_session_ref','')))
         /length('or e.envelope#>>''{agent_session,id}'' is distinct from l.holder_session_ref') <> 1 then
        raise exception 'WR130 evidence runtime lineage predecessor drifted';
      end if;
      definition:=replace(definition,
        'or e.envelope#>>''{agent_session,id}'' is distinct from l.holder_session_ref',
        'or (e.envelope#>>''{agent_session,id}'' is distinct from l.holder_session_ref' || chr(10) ||
        '     and not exists (select 1 from ops.canonical_ownership_runtime_session rs' || chr(10) ||
        '       where rs.organization_tenant_id=l.organization_tenant_id' || chr(10) ||
        '         and rs.subject_envelope_id=e.id' || chr(10) ||
        '         and rs.ownership_session_ref=l.holder_session_ref' || chr(10) ||
        '         and rs.runtime_session_ref=e.envelope#>>''{agent_session,id}''))');
      definition:=replace(definition,
        'if now_at>=m.snapshot_valid_until or l.expires_at<=now_at',
        'if now_at>=m.snapshot_valid_until or (l.state=''active'' and l.expires_at<=now_at) or (l.state=''released'' and (l.released_at is null or finished_at>l.released_at))');
      definition:=replace(definition,
        'if l.expires_at<=now_at then' || chr(10) || '    return ops.assurance_refusal(''ASSURANCE_BINDING_STALE'',''lease.currentness'',',
        'if l.state=''active'' and l.expires_at<=now_at then' || chr(10) || '    return ops.assurance_refusal(''ASSURANCE_BINDING_STALE'',''lease.currentness'',');
    else
      definition:=replace(definition,
        'or l.state is distinct from ''active''' || chr(10) || '     or l.expires_at<=now_at then',
        'or (l.state=''active'' and l.expires_at<=now_at)' || chr(10) || '     or (l.state=''released'' and l.released_at is null) then');
      definition:=replace(definition,
        'if l.expires_at<=now_at then' || chr(10) || '    return ops.assurance_refusal(''ASSURANCE_BINDING_STALE'',''lease.currentness'',',
        'if l.state=''active'' and l.expires_at<=now_at then' || chr(10) || '    return ops.assurance_refusal(''ASSURANCE_BINDING_STALE'',''lease.currentness'',');
    end if;
    execute definition;
    select * into strict after_proc from pg_catalog.pg_proc where oid=target::oid;
    if (after_proc.proowner,after_proc.prosecdef,after_proc.proconfig,after_proc.proargtypes,
        after_proc.prorettype,after_proc.provolatile,after_proc.proparallel) is distinct from
       (before_proc.proowner,before_proc.prosecdef,before_proc.proconfig,before_proc.proargtypes,
        before_proc.prorettype,before_proc.provolatile,before_proc.proparallel) then
      raise exception 'WR130 terminal append replacement changed security posture: %',target;
    end if;
  end loop;
end $wr130_terminal_append$;
