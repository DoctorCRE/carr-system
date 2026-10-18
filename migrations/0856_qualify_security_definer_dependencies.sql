-- Qualify application dependencies from the current merged disposable catalog.
-- CREATE OR REPLACE retains metadata sealed by the hardening successor.
-- rollback: forward-only — correct bindings in a later migration while retaining schema qualification.

CREATE OR REPLACE FUNCTION public.log_retrieval_query(p_query text, p_result_count integer, p_section_ids uuid[], p_score_bands jsonb, p_policy_id text, p_policy_version bigint, p_explicit_hit boolean, p_scope_ref text DEFAULT 'carr-internal'::text)
 RETURNS uuid
 LANGUAGE sql
 SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
  insert into public.retrieval_query_log
    (normalized_hash, result_count, score_bands, selected_row_ids,
     policy_id, policy_version, explicit_hit, scope_ref)
  values (
    encode(public.digest(convert_to(public.normalize_retrieval_phrase(p_query), 'UTF8'), 'sha256'), 'hex'),
    greatest(coalesce(p_result_count, 0), 0),
    coalesce(p_score_bands, jsonb_build_object('high', 0, 'medium', 0, 'low', 0)),
    coalesce(p_section_ids, '{}'),
    p_policy_id, p_policy_version,
    coalesce(p_explicit_hit, false),
    coalesce(p_scope_ref, 'carr-internal'))
  returning id
$function$;

CREATE OR REPLACE FUNCTION public.memory_item_insert_valid()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'public', 'pg_temp'
AS $function$
declare prior public.memory_item%rowtype; expected_root uuid;
begin
  -- Timestamps are server-owned; caller-supplied values are ignored.
  new.created_at := now();
  new.updated_at := new.created_at;
  if new.status <> 'candidate' or new.version <> 1
     or new.promoted_by_actor_id is not null or new.promoted_at is not null
     or new.corrected_by_actor_id is not null or new.correction_reason is not null or new.corrected_at is not null
     or new.forgotten_by_actor_id is not null or new.forget_reason is not null or new.forgotten_at is not null then
    raise exception 'new memory rows must start as clean candidate version 1';
  end if;
  if new.predecessor_id is null or new.lineage_root_id is null then
    if new.predecessor_id is not null or new.lineage_root_id is not null then
      raise exception 'new memory roots cannot carry partial lineage';
    end if;
    return new;
  end if;
  select * into prior from public.memory_item where id=new.predecessor_id;
  expected_root := coalesce(prior.lineage_root_id, prior.id);
  if not found or prior.status <> 'corrected'
     or prior.organization_tenant_id is distinct from new.organization_tenant_id
     or prior.scope is distinct from new.scope
     or prior.owner_actor_id is distinct from new.owner_actor_id
     or prior.kind is distinct from new.kind
     or prior.context is distinct from new.context
     or prior.confidence is distinct from new.confidence
     or prior.work_request_id is distinct from new.work_request_id
     or prior.work_request_version is distinct from new.work_request_version
     or prior.plan_id is distinct from new.plan_id
     or new.lineage_root_id is distinct from expected_root then
    raise exception 'memory successor lineage does not match corrected predecessor';
  end if;
  return new;
end $function$;

CREATE OR REPLACE FUNCTION ops.acquire_canonical_ownership_lease(p_work_request_id uuid, p_work_request_version integer, p_work_request_digest text, p_accepted_plan_id uuid, p_accepted_plan_digest text, p_slice_plan_id uuid, p_slice_plan_digest text, p_slice_ref text, p_contract_digest text, p_path_claims jsonb, p_resource_claims jsonb, p_dependencies jsonb, p_ttl_seconds integer DEFAULT 900)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'ops', 'public', 'pg_temp'
AS $function$
declare context jsonb:=ops.canonical_ownership_context(); claim jsonb; other jsonb;
        dep jsonb; currentness jsonb; dep_state jsonb; canonical_deps jsonb;
        submitted_deps jsonb; collision record; replaced record;
        lease_row ops.canonical_ownership_lease%rowtype; now_at timestamptz;
        refs text[]; subject_id uuid; subject_count integer;
        claim_ordinal bigint; other_ordinal bigint; dep_ordinal bigint;
        duplicate_kind text; mismatch_ordinal integer;
begin
  if not coalesce((context->>'ok')::boolean,false) then return context; end if;
  if p_work_request_id is null or p_work_request_version is null or p_work_request_version<1
     or p_work_request_digest is null or p_work_request_digest !~ '^sha256:[0-9a-f]{64}$'
     or p_accepted_plan_id is null or p_accepted_plan_digest is null
     or p_accepted_plan_digest !~ '^sha256:[0-9a-f]{64}$'
     or p_slice_plan_id is null or p_slice_plan_digest is null
     or p_slice_plan_digest !~ '^sha256:[0-9a-f]{64}$'
     or p_slice_ref is null or p_slice_ref !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'
     or p_ttl_seconds is null or p_ttl_seconds<30 or p_ttl_seconds>1800
     or jsonb_typeof(p_path_claims) is distinct from 'array'
     or jsonb_typeof(p_resource_claims) is distinct from 'array'
     or jsonb_typeof(p_dependencies) is distinct from 'array'
     or p_contract_digest is null or p_contract_digest !~ '^sha256:[0-9a-f]{64}$' then
    return ops.canonical_ownership_refusal('INPUT_INVALID','lease.input','"bounded exact A2 input"'::jsonb,'"invalid"'::jsonb);
  end if;
  for claim,claim_ordinal in
    select value,ordinality from jsonb_array_elements(p_path_claims) with ordinality
  loop
    if not coalesce(ops.engineering_receipt_exact_object(claim,array['path','mode','operation']),false)
       or claim->>'mode' not in ('file','tree') or claim->>'operation' not in ('write','rename_source','rename_destination')
       or not coalesce(ops.canonical_ownership_path_valid(claim->>'path'),false) then
      return ops.canonical_ownership_refusal('PATH_INVALID','path_claim',
        '"exact A1a path claim"'::jsonb,
        jsonb_build_object('ordinal',claim_ordinal,'field','path_claim',
          'reason','invalid','value_redacted',true));
    end if;
  end loop;
  for claim,claim_ordinal in
    select value,ordinality from jsonb_array_elements(p_path_claims) with ordinality
  loop
    for other,other_ordinal in
      select value,ordinality from jsonb_array_elements(p_path_claims) with ordinality
    loop
      if claim_ordinal<other_ordinal
         and ops.canonical_ownership_path_case_alias(claim->>'path',other->>'path') then
        return ops.canonical_ownership_refusal('PATH_CASE_ALIAS','path_claims',
          '"one canonical path case"'::jsonb,
          jsonb_build_object('left_ordinal',claim_ordinal,
            'right_ordinal',other_ordinal,'reason','case_alias','value_redacted',true));
      end if;
    end loop;
  end loop;
  for claim,claim_ordinal in
    select value,ordinality from jsonb_array_elements(p_resource_claims) with ordinality
  loop
    if not coalesce(ops.engineering_receipt_exact_object(claim,array['resource']),false)
       or not coalesce(ops.canonical_ownership_resource_valid(claim->>'resource'),false) then
      return ops.canonical_ownership_refusal('RESOURCE_INVALID','resource_claim',
        '"exact ASCII resource identifier"'::jsonb,
        jsonb_build_object('ordinal',claim_ordinal,'field','resource_claim',
          'reason','invalid','value_redacted',true));
    end if;
  end loop;
  select min(second.ordinality) into claim_ordinal
    from jsonb_array_elements(p_path_claims) with ordinality first(value,ordinality)
    join jsonb_array_elements(p_path_claims) with ordinality second(value,ordinality)
      on first.value=second.value and first.ordinality<second.ordinality;
  duplicate_kind:='path';
  if claim_ordinal is null then
    select min(second.ordinality) into claim_ordinal
      from jsonb_array_elements(p_resource_claims) with ordinality first(value,ordinality)
      join jsonb_array_elements(p_resource_claims) with ordinality second(value,ordinality)
        on first.value=second.value and first.ordinality<second.ordinality;
    duplicate_kind:='resource';
  end if;
  if claim_ordinal is not null then
    return ops.canonical_ownership_refusal('DUPLICATE_CLAIM','claims',
      '"unique claims"'::jsonb,jsonb_build_object(
        'claim_kind',duplicate_kind,'duplicate_ordinal',claim_ordinal));
  end if;
  for dep,dep_ordinal in
    select value,ordinality from jsonb_array_elements(p_dependencies) with ordinality
  loop
    if not coalesce(ops.engineering_receipt_exact_object(dep,array['slice_ref','required_state']),false)
       or dep->>'required_state' not in ('completed','independently_verified')
       or (dep->>'slice_ref') !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$' then
      return ops.canonical_ownership_refusal('INPUT_INVALID','dependency',
        '"exact dependency object"'::jsonb,
        jsonb_build_object('ordinal',dep_ordinal,'field','dependency',
          'reason','invalid','value_redacted',true));
    end if;
  end loop;
  select min(second.ordinality) into dep_ordinal
    from jsonb_array_elements(p_dependencies) with ordinality first(value,ordinality)
    join jsonb_array_elements(p_dependencies) with ordinality second(value,ordinality)
      on first.value->>'slice_ref'=second.value->>'slice_ref'
     and first.ordinality<second.ordinality;
  if dep_ordinal is not null then
    return ops.canonical_ownership_refusal('INPUT_INVALID','dependencies',
      '"unique slice_ref values"'::jsonb,
      jsonb_build_object('duplicate_ordinal',dep_ordinal));
  end if;

  perform pg_advisory_xact_lock(hashtextextended('canonical-ownership:'||(context->>'tenant'),0));
  perform 1 from ops.canonical_ownership_lease l where l.organization_tenant_id=context->>'tenant' order by l.id for update;
  canonical_deps:=ops.canonical_ownership_plan_dependencies(p_slice_plan_id,p_slice_ref);
  select array_agg(ref order by ref) into refs from (
    select p_slice_ref ref union select value->>'slice_ref'
      from jsonb_array_elements(case when coalesce((canonical_deps->>'ok')::boolean,false)
        then canonical_deps->'dependencies' else '[]'::jsonb end)
  ) q;
  perform ops.canonical_ownership_lock_lineage(
    (context->>'actor_id')::uuid,p_slice_plan_id,refs);
  perform 1 from public.actor a
   where a.id=(context->>'actor_id')::uuid
     and a.slug=context->>'actor_slug' and a.active;
  if not found then
    return ops.canonical_ownership_refusal('IDENTITY_CONTEXT_INVALID','identity_context.actor',
      '"active canonical actor"'::jsonb,
      jsonb_build_object('reason','unknown_or_inactive','value_redacted',true));
  end if;
  now_at:=clock_timestamp();
  currentness:=ops.canonical_ownership_currentness(p_work_request_id,p_work_request_version,p_work_request_digest,
    p_accepted_plan_id,p_accepted_plan_digest,p_slice_plan_id,p_slice_plan_digest,p_slice_ref);
  if not coalesce((currentness->>'ok')::boolean,false) then return currentness; end if;
  canonical_deps:=ops.canonical_ownership_plan_dependencies(p_slice_plan_id,p_slice_ref);
  if not coalesce((canonical_deps->>'ok')::boolean,false) then return canonical_deps; end if;
  select coalesce(jsonb_agg(value order by value->>'slice_ref'),'[]'::jsonb)
    into submitted_deps from jsonb_array_elements(p_dependencies);
  if submitted_deps is distinct from canonical_deps->'dependencies' then
    select min(ordinal) into mismatch_ordinal
      from generate_series(1,greatest(jsonb_array_length(submitted_deps),
           jsonb_array_length(canonical_deps->'dependencies'))) ordinal
     where submitted_deps->(ordinal-1)
           is distinct from canonical_deps->'dependencies'->(ordinal-1);
    return ops.canonical_ownership_refusal('SLICE_PLAN_BINDING_STALE','slice_plan.dependencies',
      '"exact canonical dependency snapshot"'::jsonb,
      jsonb_build_object('reason','snapshot_mismatch',
        'submitted_count',jsonb_array_length(submitted_deps),
        'canonical_count',jsonb_array_length(canonical_deps->'dependencies'),
        'first_mismatch_ordinal',mismatch_ordinal,'value_redacted',true));
  end if;
  select count(*),(array_agg(id order by id))[1] into subject_count,subject_id
    from ops.engineering_execution_envelope e
   where e.work_request_id=p_work_request_id and e.slice_plan_id=p_slice_plan_id
     and e.slice_ref=p_slice_ref
     and not exists (select 1 from ops.engineering_execution_envelope successor
                      where successor.supersedes_envelope_id=e.id);
  if subject_count<>1 then
    return ops.canonical_ownership_refusal('SLICE_PLAN_BINDING_STALE','slice_plan.subject',
      '"one current subject envelope"'::jsonb,
      jsonb_build_object('reason','subject_lineage_stale','value_redacted',true));
  end if;
  for dep in select value from jsonb_array_elements(canonical_deps->'dependencies') loop
    dep_state:=ops.canonical_ownership_dependency_state(p_work_request_id,p_slice_plan_id,dep->>'slice_ref',dep->>'required_state');
    if not coalesce((dep_state->>'ok')::boolean,false) then return dep_state; end if;
  end loop;

  select l.id lease_id,c.claim_kind,c.claim_value,c.claim_mode,c.operation,
         submitted.submitted_ordinal,
         encode(public.digest(c.claim_value,'sha256'),'hex') claim_digest into collision
    from ops.canonical_ownership_lease l
    join ops.canonical_ownership_claim c on c.lease_id=l.id
    join lateral (
      select r.ordinality submitted_ordinal
        from jsonb_array_elements(p_resource_claims) with ordinality r(value,ordinality)
       where c.claim_kind='resource' and r.value->>'resource'=c.claim_value
      union all
      select p.ordinality submitted_ordinal
        from jsonb_array_elements(p_path_claims) with ordinality p(value,ordinality)
       where c.claim_kind='path' and ops.canonical_ownership_paths_overlap(
         c.claim_value,c.claim_mode,p.value->>'path',p.value->>'mode')
    ) submitted on true
   where l.organization_tenant_id=context->>'tenant' and l.state='active' and l.expires_at>now_at
   order by l.id,c.claim_kind,c.claim_value,submitted.submitted_ordinal limit 1;
  if found then
    return ops.canonical_ownership_refusal('FOREIGN_LEASE_COLLISION','lease.collision',
      '"unclaimed scope"'::jsonb,
      jsonb_build_object('conflicting_lease_id',collision.lease_id,
        'claim_kind',collision.claim_kind,
        'submitted_ordinal',collision.submitted_ordinal,
        'claim_digest',collision.claim_digest,
        'reason','already_claimed','value_redacted',true));
  end if;

  insert into ops.canonical_ownership_lease(
    organization_tenant_id,holder_actor_id,holder_actor_slug,holder_session_ref,holder_host_ref,
    work_request_id,work_request_version,work_request_digest,accepted_plan_id,accepted_plan_digest,
    slice_plan_id,slice_plan_digest,slice_ref,subject_envelope_id,
    contract_digest,acquired_at,expires_at,created_at,updated_at)
  values(context->>'tenant',(context->>'actor_id')::uuid,context->>'actor_slug',context->>'session_ref',context->>'host_ref',
    p_work_request_id,p_work_request_version,p_work_request_digest,p_accepted_plan_id,p_accepted_plan_digest,
    p_slice_plan_id,p_slice_plan_digest,p_slice_ref,subject_id,
    p_contract_digest,now_at,now_at+make_interval(secs=>p_ttl_seconds),now_at,now_at)
  returning * into lease_row;

  for claim in select value from jsonb_array_elements(p_path_claims) loop
    insert into ops.canonical_ownership_claim values(lease_row.id,context->>'tenant','path',claim->>'path',claim->>'mode',claim->>'operation',now_at);
  end loop;
  for claim in select value from jsonb_array_elements(p_resource_claims) loop
    insert into ops.canonical_ownership_claim values(lease_row.id,context->>'tenant','resource',claim->>'resource','resource','claim',now_at);
  end loop;
  for dep in select value from jsonb_array_elements(canonical_deps->'dependencies') loop
    dep_state:=ops.canonical_ownership_dependency_state(p_work_request_id,p_slice_plan_id,dep->>'slice_ref',dep->>'required_state');
    insert into ops.canonical_ownership_dependency values(
      lease_row.id,context->>'tenant',dep->>'slice_ref',dep->>'required_state',
      (dep_state->>'envelope_id')::uuid,(dep_state->>'receipt_id')::uuid,
      nullif(dep_state->>'reviewer_fact_id','')::uuid,now_at,now_at);
  end loop;

  for replaced in
    select distinct l.organization_tenant_id,l.id,l.lease_token,l.fencing_generation,
      l.expires_at,l.state
      from ops.canonical_ownership_lease l join ops.canonical_ownership_claim c on c.lease_id=l.id
     where l.organization_tenant_id=context->>'tenant' and l.id<>lease_row.id and l.state in ('active','expired') and l.expires_at<=now_at
       and ((c.claim_kind='resource' and exists(select 1 from jsonb_array_elements(p_resource_claims) r where r->>'resource'=c.claim_value))
         or (c.claim_kind='path' and exists(select 1 from jsonb_array_elements(p_path_claims) p
               where ops.canonical_ownership_paths_overlap(c.claim_value,c.claim_mode,p->>'path',p->>'mode'))))
  loop
    update ops.canonical_ownership_lease set state='replaced',replaced_at=now_at,
      superseded_by_lease_id=lease_row.id,updated_at=now_at
     where organization_tenant_id=replaced.organization_tenant_id and id=replaced.id
       and lease_token=replaced.lease_token and fencing_generation=replaced.fencing_generation
       and expires_at=replaced.expires_at and state=replaced.state;
    if not found then raise exception 'canonical ownership predecessor fence changed under tenant lock'; end if;
    if replaced.state='active' then
      insert into ops.canonical_ownership_lease_event
        (organization_tenant_id,lease_id,event_kind,fencing_generation,actor_id,
         session_ref,host_ref,cause,occurred_at,created_at)
      values(replaced.organization_tenant_id,replaced.id,'expired',replaced.fencing_generation,
        (context->>'actor_id')::uuid,context->>'session_ref',context->>'host_ref',
        '{"reason":"observed_expired_during_reacquire"}'::jsonb,now_at,now_at);
    end if;
    insert into ops.canonical_ownership_lease_event
      (organization_tenant_id,lease_id,event_kind,fencing_generation,actor_id,
       session_ref,host_ref,cause,occurred_at,created_at)
    values(replaced.organization_tenant_id,replaced.id,'replaced',replaced.fencing_generation,
      (context->>'actor_id')::uuid,context->>'session_ref',context->>'host_ref',
      jsonb_build_object('superseded_by_lease_id',lease_row.id,
        'reason','expired_scope_reacquired'),now_at,now_at);
  end loop;
  insert into ops.canonical_ownership_lease_event
    (organization_tenant_id,lease_id,event_kind,fencing_generation,actor_id,
     session_ref,host_ref,cause,occurred_at,created_at)
  values(context->>'tenant',lease_row.id,'acquired',lease_row.fencing_generation,(context->>'actor_id')::uuid,
    context->>'session_ref',context->>'host_ref',
    jsonb_build_object('contract_digest',p_contract_digest),now_at,now_at);
  return jsonb_build_object('ok',true,'lease_id',lease_row.id,'lease_token',lease_row.lease_token,
    'fencing_generation',lease_row.fencing_generation,'expires_at',lease_row.expires_at);
end $function$;

CREATE OR REPLACE FUNCTION ops.activate_context_bundle(p_work_request text, p_plan_ref text, p_bundle jsonb, p_idempotency_key uuid)
 RETURNS TABLE(binding_id text, bundle_digest text, replayed boolean)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare
  tenant text := current_setting('carr.organization_tenant_id', true);
  work ops.work_request%rowtype;
  plan ops.sourced_work_request_plan%rowtype;
  existing ops.context_activation_binding%rowtype;
  new_id text;
  digest_text text;
  item jsonb;
  ordinal integer := 0;
begin
  if coalesce(tenant,'') = '' then raise exception 'activation requires authenticated tenant context'; end if;
  select * into work from ops.work_request where ref=p_work_request and organization_tenant_id=tenant for share;
  if not found then raise exception 'activation work request is not visible to tenant'; end if;
  select p.* into plan from ops.sourced_work_request_plan p
   join ops.sourced_work_request_plan_acceptance_receipt a on a.plan_id=p.id and a.plan_hash=p.plan_hash
   where p.work_request_id=work.id and p.plan_ref=p_plan_ref and a.result_version=work.version for share;
  if not found then raise exception 'activation requires the exact accepted current plan'; end if;
  select * into existing from ops.context_activation_binding where organization_tenant_id=tenant and idempotency_key=p_idempotency_key for share;
  if found then
    if existing.work_request_id<>work.id or existing.plan_hash<>plan.plan_hash or existing.bundle_digest<>coalesce(p_bundle->>'bundle_digest','') then raise exception 'activation idempotency key conflicts with prior binding'; end if;
    return query select existing.binding_id, existing.bundle_digest, true; return;
  end if;
  if jsonb_typeof(p_bundle)<>'object' or p_bundle->>'schema_version' <> 'context-bundle.v1' or jsonb_typeof(p_bundle->'header')<>'object' or jsonb_typeof(p_bundle->'items')<>'array' then raise exception 'activation bundle shape is invalid'; end if;
  if jsonb_array_length(p_bundle->'items') < 1 or jsonb_array_length(p_bundle->'items') > 64 then raise exception 'activation bundle exceeds bounded item count'; end if;
  if p_bundle->'header'->>'work_request_id' <> work.ref or p_bundle->'header'->>'accepted_plan_digest' <> coalesce(plan.preimage->'context_activation'->>'base_plan_digest',plan.plan_hash) then raise exception 'activation bundle is not bound to accepted Work Request and plan'; end if;
  if p_bundle->'header'->>'tenant_id' <> tenant then raise exception 'activation bundle tenant mismatch'; end if;
  if plan.preimage->'context_activation'->>'bundle_digest' is null
     or plan.preimage->'context_activation'->>'bundle_digest' <> p_bundle->>'bundle_digest' then
    raise exception 'activation bundle digest is not in the accepted plan preimage';
  end if;
  if plan.preimage->'context_activation'->'item_refs' is null
     or (select count(*) from jsonb_array_elements_text(plan.preimage->'context_activation'->'item_refs')) <> jsonb_array_length(p_bundle->'items') then
    raise exception 'activation bundle item set is not in the accepted plan preimage';
  end if;
  digest_text := ops.context_activation_bundle_digest(p_bundle);
  if p_bundle->>'bundle_digest' <> digest_text then raise exception 'activation bundle digest does not reproduce canonical body'; end if;
  new_id := 'ctx-' || encode(public.gen_random_bytes(8),'hex');
  insert into ops.context_activation_binding(idempotency_key,organization_tenant_id,work_request_id,work_request_version,plan_id,plan_hash,binding_id,bundle_digest,retrieval_policy,mode,issued_at,expires_at,compiler_ref,query_basis_digest,grounding_plan)
  values(p_idempotency_key,tenant,work.id,work.version,plan.id,plan.plan_hash,new_id,p_bundle->>'bundle_digest',jsonb_build_object('ref',coalesce(p_bundle->'header'->>'retrieval_policy','policy:unknown')),coalesce(p_bundle->'header'->>'mode','shadow'),now(),coalesce((p_bundle->'header'->>'expires_at')::timestamptz,now()+interval '1 hour'),coalesce(p_bundle->'header'->>'compiler_id','compiler:unknown'),coalesce(p_bundle->'header'->>'query_basis_digest','sha256:'||repeat('0',64)),jsonb_build_object('source_coverage',jsonb_build_object('doctrine','retrieved','standing_rules','retrieved','accepted_decisions','dependency_selected','promoted_memory','dependency_selected','skills',jsonb_build_object('state','not_available','reason','no canonical skills store'), 'architecture_constraints',jsonb_build_object('state','covered_by_doctrine_and_active_rules','reason','canonical architecture constraints are represented by the selected doctrine/rule revisions'), 'prior_failures',jsonb_build_object('state','dependency_selected','reason','selected canonical defect refs are frozen body-free in the bundle'))));
  for item in select * from jsonb_array_elements(p_bundle->'items') loop
    ordinal := ordinal + 1;
    insert into ops.context_activation_item(binding_id,ordinal,artifact_kind,canonical_ref,revision,content_digest,scope_redaction,required,trigger_ref,consumer_ref,delivery_mode,representation_kind,freshness,selection_reason,selection_rank)
    values((select b.id from ops.context_activation_binding b where b.binding_id=new_id),ordinal,coalesce(item->>'artifact_kind',item->>'kind'),item->>'canonical_ref',item->>'revision',item->>'digest',coalesce(item->>'scope_redaction',item->>'redaction_class'),'true' = lower(coalesce(item->>'required','false')),coalesce(item->>'trigger_ref',item->>'trigger'),coalesce(item->>'consumer_ref',item->>'consumer'),coalesce(item->>'delivery_mode','reference_only'),coalesce(item->>'representation_kind',item->>'kind'),jsonb_build_object('state',coalesce(item->>'freshness','unknown')),coalesce(item->>'selection_reason','bounded-retrieval'),coalesce((item->>'selection_rank')::integer,ordinal));
  end loop;
  return query select new_id, p_bundle->>'bundle_digest', false;
end $function$;

CREATE OR REPLACE FUNCTION ops.activate_guidance_registry(p_registry_id uuid, p_manifest_digest text, p_idempotency_key text, p_reason text)
 RETURNS uuid
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare
  v_batch_id uuid;
  authority_slug text;
  authority_actor uuid;
  registry_owner uuid;
  receipt_id uuid;
  event_id uuid;
  constitution_count integer;
  coverage_count integer;
  existing record;
begin
  authority_slug := ops.authority_actor_slug();
  select id into authority_actor from public.actor
   where slug=authority_slug and kind='human' and active;
  if authority_actor is null then
    raise exception 'guidance registry activation requires an admitted human authority actor';
  end if;
  if p_manifest_digest !~ '^[0-9a-f]{64}$' or coalesce(btrim(p_idempotency_key),'')=''
     or coalesce(btrim(p_reason),'')='' then
    raise exception 'activation requires a sha256 manifest digest, idempotency key and reason';
  end if;
  select created_by into registry_owner from ops.guidance_registry where id=p_registry_id;
  if registry_owner is null then
    raise exception 'unknown guidance registry %',p_registry_id;
  end if;
  if registry_owner <> authority_actor then
    raise exception 'guidance registry activation requires its accountable human authority actor';
  end if;
  select b.id into v_batch_id from ops.guidance_import_batch b
   where b.manifest_digest=p_manifest_digest
     and exists (select 1 from ops.guidance_import_apply_event a where a.batch_id=b.id)
     and exists (select 1 from ops.guidance_import_decision_event d
                  where d.batch_id=b.id and d.manifest_digest=p_manifest_digest and d.state='active');
  if v_batch_id is null then
    raise exception 'registry activation requires an applied, human-approved exact guidance import manifest';
  end if;
  perform ops.assert_guidance_import_inventory(v_batch_id);
  perform ops.assert_guidance_import_materialization(v_batch_id);
  perform pg_advisory_xact_lock(
    hashtextextended('guidance-registry-activation:' || p_idempotency_key,0));
  select ar.id,ar.kind,ar.subject_type,ar.subject_id,ar.actor_id,ar.decision,
         ar.contract_hash,ge.id as event_id,ge.manifest_digest,ge.reason
    into existing
    from ops.authority_receipt ar
    left join ops.guidance_registry_event ge on ge.authority_receipt_id=ar.id
   where ar.idempotency_key=p_idempotency_key;
  if existing.id is not null then
    if existing.kind<>'activation' or existing.subject_type<>'guidance'
       or existing.subject_id<>p_registry_id or existing.actor_id<>authority_actor
       or existing.decision<>'approved' or existing.contract_hash<>p_manifest_digest
       or existing.event_id is null or existing.manifest_digest<>p_manifest_digest
       or existing.reason<>p_reason then
      raise exception 'idempotency key already names a different or incomplete guidance registry activation';
    end if;
    return existing.event_id;
  end if;
  select count(*) into constitution_count
    from ops.v_guidance_materialized_current where is_constitution;
  if constitution_count not between 5 and 10 then
    raise exception 'guidance constitution must contain between 5 and 10 active items';
  end if;
  select count(*) into coverage_count from ops.assert_guidance_registry_coverage();
  if coverage_count <> 0 then
    raise exception 'guidance registry has % coverage failure(s)',coverage_count;
  end if;
  insert into ops.authority_receipt
    (idempotency_key,kind,subject_type,subject_id,actor_id,decision,
     contract_hash,evidence_refs)
  values
    (p_idempotency_key,'activation','guidance',p_registry_id,authority_actor,
     'approved',p_manifest_digest,array[p_registry_id::text])
  returning id into receipt_id;
  insert into ops.guidance_registry_event
    (registry_id,state,authority_receipt_id,manifest_digest,reason)
  values (p_registry_id,'active',receipt_id,p_manifest_digest,p_reason)
  returning id into event_id;
  return event_id;
end $function$;

CREATE OR REPLACE FUNCTION ops.activate_guidance_situation_mapping(p_proposed_mapping_id uuid, p_authority_binding_id uuid, p_reason text)
 RETURNS uuid
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare
  authority_slug text;
  authority_actor uuid;
  mapping_id uuid;
  proposal ops.guidance_situation_mapping%rowtype;
begin
  authority_slug := ops.authority_actor_slug();
  select id into authority_actor from public.actor
   where slug=authority_slug and kind='human';
  if coalesce(btrim(p_reason),'')='' then
    raise exception 'situation mapping activation requires a reason';
  end if;
  select * into proposal from ops.guidance_situation_mapping
   where id=p_proposed_mapping_id and state='proposed';
  if proposal.id is null then
    raise exception 'unknown proposed situation mapping %',p_proposed_mapping_id;
  end if;
  if not exists (
    select 1
      from ops.guidance_authority_binding b
      join ops.authority_receipt ar on ar.id=b.authority_receipt_id
     where b.id=p_authority_binding_id
       and b.guidance_revision_id=proposal.guidance_revision_id
       and ar.actor_id=authority_actor
       and ar.decision='approved') then
    raise exception 'mapping activation requires this authority session approval for the exact doctrine revision';
  end if;
  if not exists (
    select 1 from public.doctrine_concept_mapping
     where concept_id=proposal.concept_id
       and section_id=proposal.doctrine_section_id
       and status='approved') then
    raise exception 'mapping activation requires an approved WR-AI-006 doctrine bridge';
  end if;
  insert into ops.guidance_situation_mapping
    (guidance_revision_id,concept_id,doctrine_section_id,state,
     authority_binding_id,supersedes_mapping_id,reason)
  values
    (proposal.guidance_revision_id,proposal.concept_id,proposal.doctrine_section_id,
     'active',p_authority_binding_id,proposal.id,p_reason)
  returning id into mapping_id;
  return mapping_id;
end $function$;

CREATE OR REPLACE FUNCTION ops.amend_rule_statement(p_rule_id uuid, p_new_statement text, p_idempotency_key text, p_reason text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare
  v_actor_slug text;
  v_actor_id uuid;
  v_rule public.rule%rowtype;
  v_prior ops.rule_amendment_receipt%rowtype;
  v_receipt ops.rule_amendment_receipt%rowtype;
  v_prior_hash text;
  v_new_hash text;
  v_new_statement text;
  v_legacy_note text;
  v_contract jsonb;
  v_contract_hash text;
  v_amended_at timestamptz;
begin
  v_actor_slug := ops.authority_actor_slug();
  if v_actor_slug <> 'joe' then
    raise exception 'system rule amendment requires Joe authority; % may teach and participate but cannot replace Joe approval',
      v_actor_slug;
  end if;
  select id into v_actor_id from public.actor
   where slug=v_actor_slug and kind='human' and active;
  if v_actor_id is null then
    raise exception 'authority actor % is not an active human',v_actor_slug;
  end if;
  if btrim(coalesce(p_idempotency_key,''))='' or btrim(coalesce(p_reason,''))='' then
    raise exception 'amendment idempotency key and rationale are required';
  end if;
  v_new_statement := btrim(coalesce(p_new_statement,''));
  if v_new_statement='' then
    raise exception 'a rule cannot be amended to empty text';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('rule-amendment:'||p_idempotency_key,0));
  select * into v_prior from ops.rule_amendment_receipt
   where idempotency_key=p_idempotency_key;
  if found then
    if v_prior.rule_id is distinct from p_rule_id
       or v_prior.new_statement is distinct from v_new_statement
       or v_prior.rationale is distinct from btrim(p_reason)
       or v_prior.amended_by is distinct from v_actor_id then
      raise exception 'rule amendment idempotency key was reused with different input';
    end if;
    select * into v_rule from public.rule where id=p_rule_id for update;
    if not found
       or v_rule.version is distinct from v_prior.rule_version_after
       or v_rule.statement is distinct from v_prior.new_statement then
      raise exception 'rule amendment replay refused: current rule no longer matches the immutable amendment';
    end if;
    return jsonb_build_object('ok',true,'replayed',true,'rule_id',p_rule_id,
      'rule_version_before',v_prior.rule_version_before,
      'rule_version_after',v_prior.rule_version_after,
      'amendment_receipt_id',v_prior.id,'legacy_admission',v_prior.legacy_admission);
  end if;

  select * into v_rule from public.rule where id=p_rule_id for update;
  if not found then raise exception 'rule % not found',p_rule_id; end if;
  if v_rule.status='retired' then
    raise exception 'rule % is retired; a withdrawn rule stays as written',p_rule_id;
  end if;
  if v_rule.status not in ('proposed','active') then
    raise exception 'rule % is %, expected proposed or active',p_rule_id,v_rule.status;
  end if;

  v_prior_hash := encode(public.digest(v_rule.statement,'sha256'),'hex');
  v_new_hash   := encode(public.digest(v_new_statement,'sha256'),'hex');
  if v_prior_hash = v_new_hash then
    raise exception 'rule % amendment is a no-op: the new statement hashes identically to the current one',p_rule_id;
  end if;

  -- (0351) Purely descriptive here: unlike ops.retire_rule, nothing below
  -- branches on v_legacy_note -- it is recorded whenever it applies, never
  -- required.
  v_legacy_note := ops.legacy_rule_admission_note(v_rule.id,v_rule.status,v_rule.activated_at);

  v_amended_at := now();
  v_contract := jsonb_build_object(
    'rule_id',v_rule.id,'rule_version_before',v_rule.version,'rule_version_after',v_rule.version+1,
    'prior_statement_hash',v_prior_hash,'new_statement_hash',v_new_hash,
    'actor_id',v_actor_id,'rationale',btrim(p_reason),'legacy_admission',v_legacy_note,'amended_at',v_amended_at);
  v_contract_hash := encode(public.digest(v_contract::text,'sha256'),'hex');

  insert into ops.rule_amendment_receipt
    (idempotency_key,rule_id,rule_version_before,rule_version_after,prior_statement_hash,
     new_statement,new_statement_hash,amended_by,rationale,legacy_admission,contract_hash,amended_at)
  values (p_idempotency_key,v_rule.id,v_rule.version,v_rule.version+1,v_prior_hash,
          v_new_statement,v_new_hash,v_actor_id,btrim(p_reason),v_legacy_note,v_contract_hash,v_amended_at)
  returning * into v_receipt;

  insert into ops.authority_receipt
    (idempotency_key,kind,subject_type,subject_id,actor_id,decision,contract_hash,evidence_refs)
  values ('amendment:'||p_idempotency_key,'amendment','rule',v_rule.id,v_actor_id,
          'statement amended by Joe authority: '||btrim(p_reason),v_contract_hash,'{}'::text[]);

  update public.rule set statement=v_new_statement where id=v_rule.id and version=v_rule.version;
  if not found then raise exception 'rule % amendment raced',v_rule.id; end if;

  return jsonb_build_object('ok',true,'replayed',false,'rule_id',v_rule.id,
    'rule_version_before',v_rule.version,'rule_version_after',v_rule.version+1,
    'amendment_receipt_id',v_receipt.id,'legacy_admission',v_receipt.legacy_admission);
end $function$;

CREATE OR REPLACE FUNCTION ops.applicable_rules(p_workflow text DEFAULT NULL::text, p_surface text DEFAULT NULL::text, p_tier text DEFAULT NULL::text)
 RETURNS TABLE(rule_id uuid, statement text, enforcement_class text, binding_moment text, applicability jsonb)
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
  select r.id,r.statement,a.enforcement_class,a.binding_moment,a.applicability
    from public.rule r
    join ops.rule_admission a on a.rule_id=r.id
    join ops.rule_approval_receipt ar
      on ar.rule_id=r.id and ar.actor_id=r.activated_by
     and (
       (
         (ar.rule_version=r.version or exists (
           select 1 from ops.rule_approval_lifecycle_anchor legacy
            where legacy.approval_receipt_id=ar.id and legacy.rule_id=r.id
              and legacy.rule_version_after=r.version
              and legacy.statement_hash=ar.statement_hash))
         and ar.statement_hash=encode(public.digest(r.statement,'sha256'),'hex')
       )
       -- (0349) An amended active rule's VERSION and STATEMENT both moved
       -- together, so both the version-match and the hash-match above are
       -- expected to fail for it -- rule_amendment_reaches() proves the two
       -- moved together through a genuine, tamper-evident chain rather than
       -- checking either number in isolation.
       or ops.rule_amendment_reaches(r.id,ar.rule_version,ar.statement_hash,
                                      r.version,encode(public.digest(r.statement,'sha256'),'hex'))
     )
     and ar.policy_kind=a.enforcement_class
     and ar.enforcement_status=a.enforcement_status
     and ar.normalized_contract->>'binding_moment'=a.binding_moment
     and ar.normalized_contract->'applicability'=a.applicability
     and ar.normalized_contract->'projection'=a.projection
     and ar.normalized_contract->'reachability'=a.reachability
     and ar.normalized_contract->'input_contract'=a.input_contract
     and ar.evidence_refs=a.fixture_refs
   where r.status='active' and a.state='admitted' and a.admitted_by=ar.actor_id
     and exists (
       select 1 from ops.authority_receipt auth
        where auth.idempotency_key='approval:'||ar.idempotency_key
          and auth.kind='activation' and auth.subject_type='rule'
          and auth.subject_id=r.id and auth.actor_id=ar.actor_id
          and auth.contract_hash=ar.contract_hash)
     and not exists (
       select 1 from unnest(ar.requested_control_keys) requested(control_key)
        where not exists (
          select 1 from ops.rule_enforcement_point ep
          join ops.enforcement_control_catalog c using (control_key)
          join ops.rule_control_binding b
            on b.rule_id=ep.rule_id and b.control_key=ep.control_key
         where ep.rule_id=r.id and ep.control_key=requested.control_key
           and ep.installed and c.installed and c.verified_at is not null
           and b.statement_hash=ar.statement_hash
           and c.enforcement_class in ('deny_gate','stop_gate','schema','transactional_schema')))
     and (p_workflow is null or not (a.applicability ? 'workflows')
          or a.applicability->'workflows' ? '*'
          or a.applicability->'workflows' ? p_workflow)
     and (p_surface is null or not (a.applicability ? 'surfaces')
          or a.applicability->'surfaces' ? '*'
          or a.applicability->'surfaces' ? p_surface)
     and (p_tier is null or not (a.applicability ? 'tiers')
          or a.applicability->'tiers' ? '*'
          or a.applicability->'tiers' ? p_tier)
   order by r.created_at,r.id
$function$;

CREATE OR REPLACE FUNCTION ops.approve_rule_receipt_activation_v1(p_rule_id uuid, p_policy_kind text, p_control_keys text[], p_idempotency_key text, p_reason text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare
  v_actor_slug text;
  v_actor_id uuid;
  v_rule public.rule%rowtype;
  v_intake_id uuid;
  v_requested text[];
  v_installed text[];
  v_missing text[];
  v_evidence text[];
  v_status text;
  v_contract jsonb;
  v_contract_hash text;
  v_receipt ops.rule_approval_receipt%rowtype;
  v_prior ops.rule_approval_receipt%rowtype;
begin
  v_actor_slug := ops.authority_actor_slug();
  if v_actor_slug <> 'joe' then
    raise exception 'system rule approval requires Joe authority; % may teach and participate but cannot replace Joe approval',
      v_actor_slug;
  end if;
  select id into v_actor_id from public.actor
   where slug=v_actor_slug and kind='human' and active;
  if v_actor_id is null then
    raise exception 'authority actor % is not an active human',v_actor_slug;
  end if;
  if p_policy_kind='judgment_advisory' then
    raise exception 'advisory guidance is not an unbreakable rule; build a mechanical control before approval';
  end if;
  if p_policy_kind not in ('machine_enforceable','human_only') then
    raise exception 'unsupported policy kind %',p_policy_kind;
  end if;
  if btrim(coalesce(p_idempotency_key,''))='' or btrim(coalesce(p_reason,''))='' then
    raise exception 'idempotency key and approval reason are required';
  end if;

  v_requested := array(
    select distinct btrim(u.control_key)
      from unnest(coalesce(p_control_keys,'{}'::text[])) as u(control_key)
     where btrim(u.control_key)<>'' order by btrim(u.control_key));
  if p_policy_kind='human_only'
     and not ('human_authority_runtime'=any(v_requested)) then
    v_requested := array_append(v_requested,'human_authority_runtime');
    select array_agg(u.control_key order by u.control_key) into v_requested
      from unnest(v_requested) as u(control_key);
  end if;
  if cardinality(v_requested)=0 then
    raise exception 'exact registered controls must be implemented before approval';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('rule-approval:'||p_idempotency_key,0));
  select * into v_prior from ops.rule_approval_receipt
   where idempotency_key=p_idempotency_key;
  if found then
    if v_prior.rule_id is distinct from p_rule_id
       or v_prior.policy_kind is distinct from p_policy_kind
       or v_prior.requested_control_keys is distinct from v_requested
       or v_prior.reason is distinct from btrim(p_reason) then
      raise exception 'rule approval idempotency key was reused with different input';
    end if;
    select * into v_rule from public.rule where id=p_rule_id for update;
    if not found
       or v_rule.status is distinct from 'active'
       or not (v_rule.version=v_prior.rule_version or exists (
         select 1 from ops.rule_approval_lifecycle_anchor legacy
          where legacy.approval_receipt_id=v_prior.id and legacy.rule_id=v_rule.id
            and legacy.rule_version_after=v_rule.version
            and legacy.statement_hash=v_prior.statement_hash))
       or encode(public.digest(v_rule.statement,'sha256'),'hex') is distinct from v_prior.statement_hash
       or v_rule.activated_by is distinct from v_prior.actor_id then
      raise exception 'rule approval replay refused: current active rule no longer matches the immutable approval';
    end if;
    if not exists (
      select 1 from ops.rule_admission a
       where a.rule_id=v_rule.id and a.state='admitted'
         and a.enforcement_status=v_prior.enforcement_status
         and a.enforcement_class=v_prior.policy_kind
         and a.admitted_by=v_prior.actor_id
         and a.binding_moment=v_prior.normalized_contract->>'binding_moment'
         and a.applicability=v_prior.normalized_contract->'applicability'
         and a.projection=v_prior.normalized_contract->'projection'
         and a.reachability=v_prior.normalized_contract->'reachability'
         and a.input_contract=v_prior.normalized_contract->'input_contract'
         and a.fixture_refs=v_prior.evidence_refs
    ) or exists (
      select 1 from unnest(v_prior.requested_control_keys) requested(control_key)
       where not exists (
         select 1
           from ops.rule_enforcement_point ep
           join ops.enforcement_control_catalog c using (control_key)
           join ops.rule_control_binding b
             on b.rule_id=ep.rule_id and b.control_key=ep.control_key
          where ep.rule_id=v_rule.id
            and ep.control_key=requested.control_key
            and ep.installed and c.installed and c.verified_at is not null
            and b.statement_hash=v_prior.statement_hash
            and c.enforcement_class in ('deny_gate','stop_gate','schema','transactional_schema')
       )
    ) or not exists (
      select 1 from ops.authority_receipt ar
       where ar.idempotency_key='approval:'||v_prior.idempotency_key
         and ar.kind='activation' and ar.subject_type='rule'
         and ar.subject_id=v_rule.id and ar.actor_id=v_prior.actor_id
         and ar.contract_hash=v_prior.contract_hash
    ) then
      raise exception 'rule approval replay refused: exact installed enforcement or authority evidence is stale';
    end if;
    return jsonb_build_object(
      'ok',true,'replayed',true,'rule_id',v_prior.rule_id,
      'policy_status','active','enforcement_status',v_prior.enforcement_status,
      'installed_controls',v_prior.installed_control_keys,
      'pending_controls','{}'::text[],'approval_receipt_id',v_prior.id);
  end if;

  select * into v_rule from public.rule where id=p_rule_id for update;
  if not found then raise exception 'rule % not found',p_rule_id; end if;
  if v_rule.status<>'proposed' then
    raise exception 'rule % is %, only a proposed rule can be approved',p_rule_id,v_rule.status;
  end if;

  select coalesce(array_agg(c.control_key order by c.control_key),'{}'::text[]),
         coalesce(array_agg(c.test_ref order by c.control_key),'{}'::text[])
    into v_installed,v_evidence
    from ops.enforcement_control_catalog c
    join ops.rule_control_binding b using (control_key)
   where c.installed and c.verified_at is not null
     and c.enforcement_class in ('deny_gate','stop_gate','schema','transactional_schema')
     and b.rule_id=p_rule_id
     and b.statement_hash=encode(public.digest(v_rule.statement,'sha256'),'hex')
     and c.control_key=any(v_requested);
  v_missing := array(
    select requested.control_key from unnest(v_requested) as requested(control_key)
     where not (requested.control_key=any(v_installed)) order by requested.control_key);
  if cardinality(v_missing)>0 or cardinality(v_installed)<>cardinality(v_requested) then
    raise exception 'rule approval refused: exact enforcement is not installed; missing %',v_missing;
  end if;
  v_status := case when p_policy_kind='human_only'
                   then 'authority_enforced' else 'hard_enforced' end;

  v_contract := jsonb_build_object(
    'rule_id',p_rule_id,
    'rule_version',v_rule.version,
    'statement_hash',encode(public.digest(v_rule.statement,'sha256'),'hex'),
    'enforcement_class',p_policy_kind,
    'enforcement_status',v_status,
    'binding_moment','when the approved rule applies',
    'applicability',case when v_rule.scope='{}'::jsonb
      then '{"workflows":["*"],"surfaces":["*"],"tiers":["*"]}'::jsonb
      else v_rule.scope end,
    'projection',jsonb_build_object('targets',jsonb_build_array(
      'standing-context','applicable-rules','rule-enforcement-status')),
    'reachability',jsonb_build_object('paths',jsonb_build_array(
      'record-layer','session-boot','registered-controls')),
    'input_contract','{"type":"object","required":["workflow","surface","tier"]}'::jsonb,
    'requested_controls',v_requested);
  v_contract_hash := encode(public.digest(v_contract::text,'sha256'),'hex');

  select id into v_intake_id from ops.guidance_intake
   where lane='rule' and source_ref='rule:'||p_rule_id::text
   order by captured_at limit 1;
  if v_intake_id is null then
    insert into ops.guidance_intake
      (lane,source_kind,source_ref,statement,state,normalized_contract,captured_by)
    values ('rule','human','rule:'||p_rule_id::text,v_rule.statement,'admitted',
            v_contract,v_actor_id) returning id into v_intake_id;
  else
    update ops.guidance_intake
       set state='admitted',normalized_contract=v_contract,updated_at=now(),version=version+1
     where id=v_intake_id;
  end if;

  insert into ops.rule_admission
    (rule_id,guidance_intake_id,enforcement_class,enforcement_status,binding_moment,
     applicability,projection,reachability,input_contract,fixture_refs,state,
     admitted_by,admitted_at,reason,coverage_detail)
  values
    (p_rule_id,v_intake_id,p_policy_kind,v_status,'when the approved rule applies',
     v_contract->'applicability',v_contract->'projection',v_contract->'reachability',
     v_contract->'input_contract',v_evidence,'admitted',v_actor_id,now(),btrim(p_reason),
     jsonb_build_object('requested',v_requested,'installed',v_installed,'missing','{}'::text[]))
  on conflict (rule_id) do update set
    guidance_intake_id=excluded.guidance_intake_id,
    enforcement_class=excluded.enforcement_class,
    enforcement_status=excluded.enforcement_status,
    binding_moment=excluded.binding_moment,
    applicability=excluded.applicability,
    projection=excluded.projection,
    reachability=excluded.reachability,
    input_contract=excluded.input_contract,
    fixture_refs=excluded.fixture_refs,
    state='admitted',admitted_by=excluded.admitted_by,admitted_at=excluded.admitted_at,
    reason=excluded.reason,coverage_detail=excluded.coverage_detail,
    version=ops.rule_admission.version+1,updated_at=now();

  update ops.rule_enforcement_point set installed=false,verified_at=null
   where rule_id=p_rule_id and not (control_key=any(v_installed));
  insert into ops.rule_enforcement_point
    (rule_id,control_key,implementation_ref,test_ref,enforcement_class,installed,verified_at)
  select p_rule_id,control_key,implementation_ref,test_ref,enforcement_class,true,verified_at
    from ops.enforcement_control_catalog
   where control_key=any(v_installed)
  on conflict (rule_id,control_key) do update set
    implementation_ref=excluded.implementation_ref,test_ref=excluded.test_ref,
    enforcement_class=excluded.enforcement_class,installed=true,
    verified_at=excluded.verified_at;

  insert into ops.rule_approval_receipt
    (idempotency_key,rule_id,rule_version,statement_hash,actor_id,policy_kind,
     enforcement_status,requested_control_keys,installed_control_keys,reason,
     normalized_contract,contract_hash,evidence_refs)
  -- The activation UPDATE below is the one permitted active transition and
  -- trg_touch_row increments the rule version in that same statement. Store
  -- the post-activation version so replay can prove no later mutation occurred.
  values (p_idempotency_key,p_rule_id,v_rule.version+1,
          encode(public.digest(v_rule.statement,'sha256'),'hex'),v_actor_id,p_policy_kind,v_status,
          v_requested,v_installed,btrim(p_reason),v_contract,v_contract_hash,v_evidence)
  returning * into v_receipt;

  insert into ops.authority_receipt
    (idempotency_key,kind,subject_type,subject_id,actor_id,decision,contract_hash,evidence_refs)
  values ('approval:'||p_idempotency_key,'activation','rule',p_rule_id,v_actor_id,
          'approved, enforced and activated atomically',v_contract_hash,v_evidence);

  update public.rule
     set status='active',activated_by=v_actor_id,activated_at=now(),
         enforcement=case when v_status='hard_enforced' then 'gate' else 'constraint' end
   where id=p_rule_id and status='proposed';
  if not found then raise exception 'rule % did not activate',p_rule_id; end if;

  return jsonb_build_object(
    'ok',true,'replayed',false,'rule_id',p_rule_id,'policy_status','active',
    'enforcement_status',v_status,'installed_controls',v_installed,
    'pending_controls','{}'::text[],'approval_receipt_id',v_receipt.id);
end $function$;

CREATE OR REPLACE FUNCTION ops.assert_guidance_import_inventory(p_batch_id uuid)
 RETURNS void
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
begin
  if not exists (select 1 from ops.guidance_import_batch where id=p_batch_id) then
    raise exception 'unknown guidance import batch %',p_batch_id;
  end if;
  if exists (
    (select id from public.rule
      where status='active' and coalesce(scope->>'kind','') <> 'intro_politics')
    except
    (select distinct source_rule_id from ops.guidance_import_entry where batch_id=p_batch_id)
  ) or exists (
    (select distinct source_rule_id from ops.guidance_import_entry where batch_id=p_batch_id)
    except
    (select id from public.rule
      where status='active' and coalesce(scope->>'kind','') <> 'intro_politics')
  ) then
    raise exception 'guidance import batch source inventory no longer exactly matches standing-context active rules';
  end if;
end $function$;

CREATE OR REPLACE FUNCTION ops.assert_guidance_registry_coverage()
 RETURNS TABLE(source_rule_id uuid, issue text)
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
  with active_rules as (
    select id from public.rule
     where status='active' and coalesce(scope->>'kind','') <> 'intro_politics'
  ), primary_counts as (
    select ar.id,
           count(g.*) filter (where g.is_primary) as primary_count
      from active_rules ar
      left join ops.v_guidance_materialized_current g on g.source_rule_id=ar.id
     group by ar.id
  )
  select id,
         case when primary_count=0 then 'missing active primary guidance'
              else 'multiple active primary guidance records' end
    from primary_counts where primary_count <> 1
  union all
  select g.source_rule_id,'constraint lacks admitted installed enforcement projection'
    from ops.v_guidance_materialized_current g
   where g.is_primary and g.guidance_type='constraint'
     and not exists (
       select 1
         from ops.rule_admission a
         join ops.rule_enforcement_point ep
           on ep.rule_id=a.rule_id and ep.installed
        where a.rule_id=g.source_rule_id and a.state='admitted')
  union all
  select g.source_rule_id,'doctrine lacks active WR-AI-006 situation bridge'
    from ops.v_guidance_materialized_current g
   where g.is_primary and g.guidance_type='doctrine'
     and not exists (
       select 1
         from ops.v_guidance_materialized_situation_mapping_current m
         join public.retrieval_concept c on c.id=m.concept_id and c.status='approved'
         join public.doctrine_section s on s.id=m.doctrine_section_id and s.status='active'
         join public.doctrine_concept_mapping dcm
           on dcm.concept_id=m.concept_id
          and dcm.section_id=m.doctrine_section_id
          and dcm.status='approved'
        where m.guidance_revision_id=g.guidance_revision_id and m.state='active')
$function$;

CREATE OR REPLACE FUNCTION ops.assign_execution_profile(p_work_request text, p_profile_key text, p_environment text, p_policy_ref text, p_policy_digest text, p_idempotency_key uuid)
 RETURNS TABLE(assignment_id uuid, replayed boolean)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare tenant text := current_setting('carr.organization_tenant_id', true); work ops.work_request%rowtype;
  profile public.agent_profile%rowtype; sponsor public.actor%rowtype; existing ops.work_request_execution_assignment%rowtype;
begin
  if session_user !~ '^carr_authority_' then raise exception 'execution profile assignment requires the authoritative policy gateway'; end if;
  select * into work from ops.work_request where ref=p_work_request and organization_tenant_id=tenant for share;
  if not found then raise exception 'execution profile assignment work request is not visible'; end if;
  select * into profile from public.agent_profile where profile_key=p_profile_key and status='active' and current_model is not null and current_desk is not null for share;
  if not found or p_environment not in ('local','rehearsal','staging','production') or p_policy_ref !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$' or p_policy_digest !~ '^sha256:[0-9a-f]{64}$' then
    raise exception 'execution profile assignment requires active profile and exact policy binding';
  end if;
  select * into sponsor from public.actor where slug=regexp_replace(session_user,'^carr_authority_','') and kind='human' and active;
  if not found then raise exception 'execution profile assignment cannot derive sponsoring human'; end if;
  select * into existing from ops.work_request_execution_assignment where work_request_id=work.id;
  if found then
    if existing.profile_id<>profile.id or existing.sponsoring_human_id<>sponsor.id or existing.environment<>p_environment or existing.policy_ref<>p_policy_ref or existing.policy_digest<>p_policy_digest then raise exception 'execution profile assignment conflicts with immutable existing lane'; end if;
    return query select existing.id,true; return;
  end if;
  insert into ops.work_request_execution_assignment(work_request_id,profile_id,sponsoring_human_id,environment,policy_ref,policy_digest,idempotency_key)
  values(work.id,profile.id,sponsor.id,p_environment,p_policy_ref,p_policy_digest,p_idempotency_key) returning id into assignment_id;
  return query select assignment_id,false;
end $function$;

CREATE OR REPLACE FUNCTION ops.attest_attempt_receipt_evaluation(p_attempt_id text, p_evaluator_kind text, p_check_ref text, p_dimension_refs jsonb, p_status text, p_independent boolean, p_evidence_refs jsonb, p_evaluation_metadata jsonb, p_outcome_feedback_ref text, p_outcome_feedback_hash text, p_idempotency_key uuid)
 RETURNS TABLE(attestation_id uuid, replayed boolean)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare tenant text := current_setting('carr.organization_tenant_id',true); receipt_row ops.attempt_receipt%rowtype;
  envelope_row ops.execution_envelope_v1%rowtype; authority_actor public.actor%rowtype; existing ops.attempt_receipt_evaluation_attestation%rowtype;
  derived_horizon text; accepted_feedback boolean;
begin
  if session_user !~ '^carr_authority_' then raise exception 'evaluation attestation requires human authority'; end if;
  select * into authority_actor from public.actor where slug=regexp_replace(session_user,'^carr_authority_','') and kind='human' and active;
  select * into receipt_row from ops.attempt_receipt where organization_tenant_id=tenant and attempt_id=p_attempt_id for share;
  select * into envelope_row from ops.execution_envelope_v1 where id=receipt_row.execution_envelope_id and organization_tenant_id=tenant for share;
  if authority_actor.id is null or receipt_row.id is null or envelope_row.id is null
     or jsonb_typeof(p_dimension_refs)<>'array' or jsonb_array_length(p_dimension_refs)=0
     or jsonb_typeof(p_evidence_refs)<>'array' or jsonb_array_length(p_evidence_refs)=0
     or jsonb_typeof(p_evaluation_metadata)<>'object'
     or not (p_evaluation_metadata ?& array['evaluator_ref','rubric_ref','evaluator_version','evaluator_digest','confidence','held_out_case_count','calibration_refs','lower_bound_ref'])
     or exists (select 1 from jsonb_object_keys(p_evaluation_metadata) k where k <> all(array['evaluator_ref','rubric_ref','evaluator_version','evaluator_digest','confidence','held_out_case_count','calibration_refs','lower_bound_ref']))
     or p_evaluation_metadata->>'evaluator_ref' <> envelope_row.evaluation_plan->>'evaluator_ref'
     or p_evaluation_metadata->>'rubric_ref' <> envelope_row.evaluation_plan->>'rubric_ref'
     or p_evaluation_metadata->>'evaluator_version' <> envelope_row.evaluation_plan->>'evaluator_version'
     or p_evaluation_metadata->>'evaluator_digest' <> envelope_row.evaluation_plan->>'evaluator_digest'
     or p_evaluation_metadata->>'confidence' not in ('high','medium','low','unknown')
     or jsonb_typeof(p_evaluation_metadata->'held_out_case_count') <> 'number' or (p_evaluation_metadata->>'held_out_case_count')::integer < 0
     or jsonb_typeof(p_evaluation_metadata->'calibration_refs') <> 'array'
     or ops.attempt_receipt_contains_raw_content(p_evidence_refs) or ops.attempt_receipt_contains_raw_content(p_evaluation_metadata)
     or p_evaluator_kind not in ('deterministic','judge','human_acceptance','outcome_horizon')
     or p_status not in ('passed','failed','blocked','unknown','not_run','mature','immature')
     or p_dimension_refs is distinct from envelope_row.evaluation_plan->'critical_dimensions' then
    raise exception 'evaluation attestation lacks exact canonical binding';
  end if;
  if (p_evaluator_kind='deterministic' and (p_check_ref='' or not ((envelope_row.evaluation_plan->'required_deterministic_check_refs') ? p_check_ref) or p_status not in ('passed','failed','blocked','unknown','not_run')))
     or (p_evaluator_kind<>'deterministic' and p_check_ref<>'')
     or (p_evaluator_kind='judge' and p_status not in ('passed','failed','blocked','unknown','not_run'))
     or (p_evaluator_kind='human_acceptance' and p_status not in ('passed','failed','blocked','unknown','not_run'))
     or (p_evaluator_kind='outcome_horizon' and p_status not in ('mature','immature'))
     or (p_evaluator_kind='judge' and not p_independent)
     or (p_evaluator_kind<>'judge' and p_independent) then
    raise exception 'evaluation attestation kind/check/status/independence is invalid';
  end if;
  select exists (
    select 1 from ops.sourced_work_request_outcome_feedback f join ops.sourced_work_request_outcome_feedback_acceptance_receipt accepted on accepted.feedback_id=f.id
      where f.work_request_id=receipt_row.work_request_id and f.plan_id=(select plan_id from ops.context_activation_binding where id=receipt_row.activation_binding_id)
        and f.feedback_ref=p_outcome_feedback_ref and f.feedback_hash=p_outcome_feedback_hash
  ) into accepted_feedback;
  if p_evaluator_kind in ('human_acceptance','outcome_horizon') and (not accepted_feedback or p_outcome_feedback_ref !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$' or p_outcome_feedback_hash !~ '^sha256:[0-9a-f]{64}$') then
    raise exception 'authority attestation requires exact accepted Program 6 outcome feedback';
  elsif p_evaluator_kind not in ('human_acceptance','outcome_horizon') and (p_outcome_feedback_ref is not null or p_outcome_feedback_hash is not null) then
    raise exception 'only outcome-bound authority facts may name Program 6 feedback';
  end if;
  derived_horizon := case when clock_timestamp() >= (envelope_row.evaluation_plan->>'outcome_horizon_not_before')::timestamptz then 'mature' else 'immature' end;
  if p_evaluator_kind='outcome_horizon' and p_status <> derived_horizon then
    raise exception 'outcome horizon is derived from the issued policy and database clock';
  end if;
  select * into existing from ops.attempt_receipt_evaluation_attestation where idempotency_key=p_idempotency_key for share;
  if found then
    if existing.attempt_receipt_id<>receipt_row.id or existing.evaluator_kind<>p_evaluator_kind or existing.check_ref<>p_check_ref or existing.dimension_refs is distinct from p_dimension_refs or existing.status<>p_status or existing.independent<>p_independent or existing.evidence_refs is distinct from p_evidence_refs or existing.evaluator_ref<>p_evaluation_metadata->>'evaluator_ref' or existing.rubric_ref<>p_evaluation_metadata->>'rubric_ref' or existing.evaluator_version<>p_evaluation_metadata->>'evaluator_version' or existing.evaluator_digest<>p_evaluation_metadata->>'evaluator_digest' or existing.confidence<>p_evaluation_metadata->>'confidence' or existing.held_out_case_count<>(p_evaluation_metadata->>'held_out_case_count')::integer or existing.calibration_refs is distinct from p_evaluation_metadata->'calibration_refs' or existing.lower_bound_ref is distinct from nullif(p_evaluation_metadata->>'lower_bound_ref','') or existing.outcome_feedback_ref is distinct from p_outcome_feedback_ref or existing.outcome_feedback_hash is distinct from p_outcome_feedback_hash then raise exception 'evaluation attestation idempotency conflict'; end if;
    return query select existing.id,true; return;
  end if;
  insert into ops.attempt_receipt_evaluation_attestation(organization_tenant_id,attempt_receipt_id,evaluator_kind,check_ref,dimension_refs,evaluator_policy_digest,evaluator_ref,rubric_ref,evaluator_version,evaluator_digest,confidence,held_out_case_count,calibration_refs,lower_bound_ref,outcome_feedback_ref,outcome_feedback_hash,status,independent,evidence_refs,attested_by_actor_id,idempotency_key)
  values(tenant,receipt_row.id,p_evaluator_kind,p_check_ref,p_dimension_refs,envelope_row.evaluation_plan->>'evaluator_policy_digest',p_evaluation_metadata->>'evaluator_ref',p_evaluation_metadata->>'rubric_ref',p_evaluation_metadata->>'evaluator_version',p_evaluation_metadata->>'evaluator_digest',p_evaluation_metadata->>'confidence',(p_evaluation_metadata->>'held_out_case_count')::integer,p_evaluation_metadata->'calibration_refs',nullif(p_evaluation_metadata->>'lower_bound_ref',''),p_outcome_feedback_ref,p_outcome_feedback_hash,p_status,p_independent,p_evidence_refs,authority_actor.id,p_idempotency_key)
  returning id into attestation_id;
  return query select attestation_id,false;
end $function$;

CREATE OR REPLACE FUNCTION ops.attest_execution_environment_conformance(p_provider_ref text, p_observation jsonb, p_idempotency_key uuid)
 RETURNS TABLE(conformance_id uuid, replayed boolean)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare actor_row public.actor%rowtype; provider ops.execution_environment_provider%rowtype; existing ops.execution_environment_conformance%rowtype;
  allowed text[] := array['schema_version','provider_ref','manifest_digest','implementation_digest','package_digest','package_revision_ref','configuration_schema_digest','contract_ref','contract_digest','run_ref','status','check_results','version_ref','backend_kind','evidence_refs','contains_secrets','run_digest','observed_at'];
  derived_run_digest text; observed_at_value timestamptz;
begin
  if session_user !~ '^carr_authority_' then raise exception 'environment conformance attestation requires human authority'; end if;
  select * into actor_row from public.actor where slug=regexp_replace(session_user,'^carr_authority_','') and kind='human' and active;
  select * into provider from ops.execution_environment_provider p where p_provider_ref='environment-provider:'||p.provider_key||':v'||p.provider_version for share;
  begin observed_at_value := (p_observation->>'observed_at')::timestamptz; exception when others then raise exception 'environment conformance observed_at is invalid'; end;
  derived_run_digest := 'sha256:'||encode(public.digest(ops.guidance_import_canonical_json(p_observation-'run_digest'-'observed_at'),'sha256'),'hex');
  if actor_row.id is null or provider.id is null or jsonb_typeof(p_observation)<>'object'
     or not (p_observation ?& allowed) or exists(select 1 from jsonb_object_keys(p_observation) k where k<>all(allowed))
     or p_observation->>'schema_version'<>'execution-environment-conformance.v1'
     or p_observation->>'provider_ref'<>p_provider_ref
     or p_observation->>'manifest_digest'<>provider.manifest_digest
     or p_observation->>'implementation_digest' !~ '^sha256:[0-9a-f]{64}$'
     or (p_observation->>'implementation_digest'<>provider.manifest->>'implementation_digest' and not (p_observation->>'status'='failed' and p_observation->'check_results'->'check:implementation-digest-exact'='false'::jsonb))
     or p_observation->>'package_digest' !~ '^sha256:[0-9a-f]{64}$'
     or (p_observation->>'package_digest'<>provider.manifest->'package_provenance'->>'package_digest' and not (p_observation->>'status'='failed' and p_observation->'check_results'->'check:package-provenance-exact'='false'::jsonb))
     or p_observation->>'package_revision_ref' !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'
     or p_observation->>'configuration_schema_digest'<>provider.manifest->>'configuration_schema_digest'
     or p_observation->>'contract_ref'<>provider.manifest->>'conformance_contract_ref'
     or p_observation->>'contract_digest'<>provider.manifest->>'conformance_contract_digest'
     or p_observation->>'run_ref' !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'
     or p_observation->>'run_digest' is distinct from derived_run_digest
     or p_observation->>'status' not in ('passed','failed')
     or p_observation->>'backend_kind' not in ('none','local','container','remote','cloud','unknown')
     or (p_observation->>'backend_kind'<>provider.backend_kind and not (p_observation->>'status'='failed' and p_observation->'check_results'->'check:terminal-backend-local'='false'::jsonb))
     or p_observation->>'version_ref' !~ '^[^[:cntrl:]]{1,160}$'
     or p_observation->>'contains_secrets' not in ('true','false')
     or (p_observation->>'contains_secrets'='true' and not (p_observation->>'status'='failed' and p_observation->'check_results'->'check:source-secret-scan'='false'::jsonb))
     or jsonb_typeof(p_observation->'check_results')<>'object' or p_observation->'check_results'='{}'::jsonb
     or exists(select 1 from jsonb_each(p_observation->'check_results') c where c.key !~ '^check:[a-z0-9-]+$' or jsonb_typeof(c.value)<>'boolean')
     or (p_observation->>'status'='passed' and exists(select 1 from jsonb_each(p_observation->'check_results') c where c.value<>'true'::jsonb))
     or (p_observation->>'status'='failed' and not exists(select 1 from jsonb_each(p_observation->'check_results') c where c.value='false'::jsonb))
     or jsonb_typeof(p_observation->'evidence_refs')<>'array' or jsonb_array_length(p_observation->'evidence_refs')=0
     or exists(select 1 from jsonb_array_elements_text(p_observation->'evidence_refs') value where value !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$')
     or observed_at_value>clock_timestamp() then
    raise exception 'environment conformance attestation is invalid';
  end if;
  select * into existing from ops.execution_environment_conformance where idempotency_key=p_idempotency_key for share;
  if found then
    if existing.provider_id<>provider.id or existing.observation is distinct from p_observation then raise exception 'environment conformance idempotency conflict'; end if;
    return query select existing.id,true; return;
  end if;
  insert into ops.execution_environment_conformance(provider_id,contract_ref,contract_digest,run_ref,run_digest,manifest_digest,implementation_digest,package_digest,configuration_schema_digest,status,check_refs,evidence_refs,observation,observed_at,recorded_by_actor_id,idempotency_key)
  values(provider.id,provider.manifest->>'conformance_contract_ref',provider.manifest->>'conformance_contract_digest',p_observation->>'run_ref',derived_run_digest,provider.manifest_digest,p_observation->>'implementation_digest',p_observation->>'package_digest',p_observation->>'configuration_schema_digest',p_observation->>'status',to_jsonb(array(select key from jsonb_each(p_observation->'check_results') order by key)),p_observation->'evidence_refs',p_observation,observed_at_value,actor_row.id,p_idempotency_key)
  returning id into conformance_id;
  return query select conformance_id,false;
end $function$;

CREATE OR REPLACE FUNCTION ops.bind_rule_controls(p_rule_id uuid, p_control_keys text[], p_reason text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare
  v_actor_slug text;
  v_rule public.rule%rowtype;
  v_requested text[];
  v_available text[];
  v_missing text[];
  v_statement_hash text;
begin
  v_actor_slug := ops.authority_actor_slug();
  if v_actor_slug <> 'joe' then
    raise exception 'rule-control binding requires Joe authority; % cannot bind system enforcement',
      v_actor_slug;
  end if;
  if btrim(coalesce(p_reason,''))='' then
    raise exception 'rule-control binding reason is required';
  end if;

  v_requested := array(
    select distinct btrim(u.control_key)
      from unnest(coalesce(p_control_keys,'{}'::text[])) as u(control_key)
     where btrim(u.control_key)<>''
     order by btrim(u.control_key));
  if cardinality(v_requested)=0 then
    raise exception 'at least one registered control key is required';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('rule-control-binding:'||p_rule_id::text,0));
  select * into v_rule from public.rule where id=p_rule_id for update;
  if not found then raise exception 'rule % not found',p_rule_id; end if;
  if v_rule.status not in ('proposed','active') then
    raise exception 'rule % is %, only proposed rules may be bound and active rules may only be verified',
      p_rule_id,v_rule.status;
  end if;
  v_statement_hash := encode(public.digest(v_rule.statement,'sha256'),'hex');

  select coalesce(array_agg(c.control_key order by c.control_key),'{}'::text[])
    into v_available
    from ops.enforcement_control_catalog c
   where c.control_key=any(v_requested)
     and c.installed and c.verified_at is not null
     and c.enforcement_class in ('deny_gate','stop_gate','schema','transactional_schema');
  v_missing := array(
    select requested.control_key
      from unnest(v_requested) as requested(control_key)
     where not (requested.control_key=any(v_available))
     order by requested.control_key);
  if cardinality(v_missing)>0 or cardinality(v_available)<>cardinality(v_requested) then
    raise exception 'rule-control binding refused: registered installed controls are missing %',v_missing;
  end if;

  -- An approved contract is immutable. Approval replay reaches this function,
  -- so an ACTIVE rule is accepted only as an exact no-write verification.
  if v_rule.status='active' then
    if exists (
      select 1 from unnest(v_requested) requested(control_key)
       where not exists (
         select 1 from ops.rule_control_binding b
          where b.rule_id=p_rule_id
            and b.control_key=requested.control_key
            and b.statement_hash=v_statement_hash)
    ) then
      raise exception 'active rule % lacks its immutable exact control binding',p_rule_id;
    end if;
    return jsonb_build_object(
      'ok',true,'replayed',true,'rule_id',p_rule_id,
      'bound_controls',v_available,'statement_hash',v_statement_hash);
  end if;

  insert into ops.rule_control_binding
    (rule_id,control_key,statement_hash,binding_contract)
  select p_rule_id,c.control_key,v_statement_hash,
         jsonb_build_object(
           'source','ops.bind_rule_controls',
           'rule_id',p_rule_id,
           'rule_version',v_rule.version,
           'statement_hash',v_statement_hash,
           'control_key',c.control_key,
           'implementation_ref',c.implementation_ref,
           'test_ref',c.test_ref,
           'binding_reason',btrim(p_reason),
           'bound_by',v_actor_slug)
    from ops.enforcement_control_catalog c
   where c.control_key=any(v_available)
  on conflict (rule_id,control_key) do update set
    statement_hash=excluded.statement_hash,
    binding_contract=excluded.binding_contract,
    bound_at=now();

  return jsonb_build_object(
    'ok',true,'replayed',false,'rule_id',p_rule_id,
    'bound_controls',v_available,'statement_hash',v_statement_hash);
end $function$;

CREATE OR REPLACE FUNCTION ops.bind_rule_delivery(p_rule_id uuid, p_reason text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare
  v_actor_slug text;
  v_rule public.rule%rowtype;
  v_admission ops.rule_admission%rowtype;
  v_delivery jsonb;
  v_load_layer text;
  v_packs text[];
  v_scope text;
  v_why text;
  v_digest text;
  v_existing ops.rule_load_layer%rowtype;
begin
  v_actor_slug := ops.authority_actor_slug();
  if v_actor_slug <> 'joe' then
    raise exception 'rule-delivery binding requires Joe authority; % cannot bind system delivery',
      v_actor_slug;
  end if;
  if btrim(coalesce(p_reason,''))='' then
    raise exception 'rule-delivery binding reason is required';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('rule-delivery-binding:'||p_rule_id::text,0));
  select * into v_rule from public.rule where id=p_rule_id for update;
  if not found then raise exception 'rule % not found',p_rule_id; end if;
  if v_rule.status not in ('proposed','active') then
    raise exception 'rule % is %, only proposed rules may be bound and active rules may only be verified',
      p_rule_id,v_rule.status;
  end if;

  select coalesce(owner.slug,'shared') into v_scope
    from public.rule r left join public.actor owner on owner.id=r.personal_to
   where r.id=p_rule_id;
  if v_scope not in ('shared','joe','dell') then
    raise exception 'rule % has unsupported delivery scope %',p_rule_id,v_scope;
  end if;

  -- A durable row can predate this writer (migrations and existing acceptance
  -- fixtures used the owner-only table directly). Verify that legacy prebind
  -- instead of demanding it be rewritten through a newer admission shape.
  -- Active approval replay uses the same exact verification path.
  select * into v_existing from ops.rule_load_layer where rule_id=p_rule_id;
  if found then
    if v_existing.short_id<>left(p_rule_id::text,8)
       or v_existing.scope<>v_scope
       or v_existing.load_layer not in ('layer0','control','pack')
       or (v_existing.load_layer='layer0' and
           (cardinality(v_existing.packs)<>0 or nullif(btrim(coalesce(v_existing.why,'')),'') is null))
       or (v_existing.load_layer='pack' and cardinality(v_existing.packs)=0)
       or exists (select 1 from unnest(v_existing.packs) pack where pack='' or pack='*') then
      raise exception 'rule % delivery binding no longer matches its durable identity or activation contract',
        p_rule_id;
    end if;
    return jsonb_build_object(
      'ok',true,'replayed',true,'rule_id',p_rule_id,
      'load_layer',v_existing.load_layer,'packs',v_existing.packs,'scope',v_existing.scope);
  end if;
  if v_rule.status='active' then
    if v_existing.rule_id is null then
      raise exception 'active rule % lacks its delivery binding',p_rule_id;
    end if;
  end if;

  select * into v_admission from ops.rule_admission
   where rule_id=p_rule_id and state='admitted';
  if not found then
    raise exception 'rule % cannot bind delivery: admitted rule contract is missing',p_rule_id;
  end if;
  v_delivery := v_admission.projection->'delivery';
  if v_delivery is null or jsonb_typeof(v_delivery)<>'object' then
    raise exception 'rule % delivery projection is not activation-safe: projection.delivery is required',
      p_rule_id;
  end if;
  v_load_layer := btrim(coalesce(v_delivery->>'load_layer',''));
  if v_load_layer not in ('layer0','control','pack') then
    raise exception 'rule % delivery projection has invalid load_layer %',p_rule_id,v_load_layer;
  end if;
  if jsonb_typeof(v_delivery->'packs') is distinct from 'array'
     or exists (select 1 from jsonb_array_elements(v_delivery->'packs') item
                 where jsonb_typeof(item)<>'string') then
    raise exception 'rule % delivery projection packs must be an array of names',p_rule_id;
  end if;
  select coalesce(array_agg(pack order by pack),'{}'::text[]) into v_packs
    from (select distinct btrim(value) as pack
            from jsonb_array_elements_text(v_delivery->'packs') item(value)) named;
  if exists (select 1 from unnest(v_packs) pack where pack='' or pack='*') then
    raise exception 'rule % delivery projection contains an empty or wildcard pack',p_rule_id;
  end if;
  v_why := nullif(btrim(coalesce(v_delivery->>'why','')),'');
  if v_load_layer='layer0' and (cardinality(v_packs)<>0 or v_why is null) then
    raise exception 'rule % layer0 delivery must be unconditional and explain why',p_rule_id;
  end if;
  if v_load_layer='pack' and cardinality(v_packs)=0 then
    raise exception 'rule % pack delivery names no pack',p_rule_id;
  end if;
  v_digest := encode(public.digest(v_delivery::text,'sha256'),'hex');

  insert into ops.rule_load_layer
    (rule_id,short_id,load_layer,packs,scope,why,source,map_digest)
  values
    (p_rule_id,left(p_rule_id::text,8),v_load_layer,v_packs,v_scope,v_why,
     'ops.bind_rule_delivery',v_digest)
  on conflict (rule_id) do update set
    short_id=excluded.short_id,
    load_layer=excluded.load_layer,
    packs=excluded.packs,
    scope=excluded.scope,
    why=excluded.why,
    source=excluded.source,
    map_digest=excluded.map_digest,
    updated_at=now();

  return jsonb_build_object(
    'ok',true,'replayed',false,'rule_id',p_rule_id,
    'load_layer',v_load_layer,'packs',v_packs,'scope',v_scope,'map_digest',v_digest);
end $function$;

CREATE OR REPLACE FUNCTION ops.calendar_prebrief_canonical_event_digest(p_events jsonb)
 RETURNS TABLE(event_count integer, canonical_event_digest text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare v_events jsonb;
begin
  if p_events is null or pg_column_size(p_events)>262144 or jsonb_typeof(p_events)<>'array' then
    raise exception using errcode='22023',message='calendar prebrief verified envelope requires a bounded event array';
  end if;
  if jsonb_array_length(p_events)>128 or exists(
      select 1 from jsonb_array_elements(p_events) e(value)
       where pg_column_size(e.value)>4096 or jsonb_typeof(e.value)<>'object'
          or jsonb_typeof(e.value->'participant_refs')<>'array') then
    raise exception using errcode='22023',message='calendar prebrief verified envelope has invalid bounded events';
  end if;
  select coalesce(jsonb_agg(event order by event->>'occurrence_key'),'[]'::jsonb) into v_events
    from (select jsonb_set(e.value,'{participant_refs}',coalesce((select jsonb_agg(ref order by ref)
             from jsonb_array_elements_text(e.value->'participant_refs') refs(ref)),'[]'::jsonb)) event
            from jsonb_array_elements(p_events) e(value)) normalized;
  event_count:=jsonb_array_length(p_events);
  canonical_event_digest:=encode(public.digest(convert_to(v_events::text,'UTF8'),'sha256'),'hex');
  return next;
end $function$;

CREATE OR REPLACE FUNCTION ops.context_activation_bundle_body(p_tenant text, p_work_request_ref text, p_plan_revision_ref text, p_plan_revision integer, p_base_plan_digest text, p_issued_at timestamp with time zone, p_item_ref text, p_revision_ref text, p_item_digest text)
 RETURNS jsonb
 LANGUAGE sql
 IMMUTABLE STRICT SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'ops', 'public', 'pg_temp'
AS $function$
  select jsonb_build_object(
    'schema_version','context-bundle.v1',
    'header',jsonb_build_object(
      'tenant_id',p_tenant,
      'work_request_id',p_work_request_ref,
      'accepted_plan_revision_id',p_plan_revision_ref,
      'accepted_plan_revision',p_plan_revision,
      'accepted_plan_digest',p_base_plan_digest,
      'issued_at',to_char(p_issued_at at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"'),
      'mode','shadow',
      'retrieval_policy','policy:bounded-doctrine-v1',
      'retrieval_policy_version','v1','compiler_id','compiler:context-activation-v1',
      'compiler_version','v1','compiler_digest','sha256:'||encode(public.digest('compiler:context-activation-v1','sha256'),'hex'),
      'query_basis_digest','sha256:'||encode(public.digest(p_tenant||':'||p_work_request_ref||':'||p_plan_revision_ref,'sha256'),'hex'),
      'grounding_plan',jsonb_build_object('inline_budget',64,'retrieval_policy','bounded-doctrine','cache_segment','plan-bound','modalities',jsonb_build_array('metadata_only'),'freshness_sla','accepted-plan-bound')
    ),
    'items',jsonb_build_array(jsonb_build_object(
      'kind','doctrine',
      'canonical_ref',p_item_ref,
      'revision',p_revision_ref,
      'digest',p_item_digest,
      'required',true,
      'trigger','work-request-admission',
      'consumer','hermes-profile-brief',
      'enforcement','must-apply',
      'redaction_class','metadata_only','artifact_kind','doctrine','scope_redaction','metadata_only',
      'trigger_ref','work-request-admission','consumer_ref','hermes-profile-brief','delivery_mode','inline','representation_kind','doctrine','freshness_sla','accepted-plan-bound','selection_reason','canonical-doctrine-binding','selection_rank',0,'requirement_class','required',
      'freshness','fresh'
    ))
  );
$function$;

CREATE OR REPLACE FUNCTION ops.context_activation_bundle_digest(p_bundle jsonb)
 RETURNS text
 LANGUAGE sql
 IMMUTABLE STRICT SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'public', 'pg_temp'
AS $function$
  select 'sha256:' || encode(public.digest(ops.guidance_import_canonical_json(
    jsonb_build_object(
      'schema_version', p_bundle->'schema_version',
      'header', coalesce(p_bundle->'header','{}'::jsonb)
                  - 'issued_at' - 'expires_at' - 'binding_id',
      'items', p_bundle->'items'
    )), 'sha256'), 'hex')
$function$;

CREATE OR REPLACE FUNCTION ops.context_activation_bundle_from_items(p_tenant text, p_work_request_ref text, p_plan_revision_ref text, p_plan_revision integer, p_base_plan_digest text, p_issued_at timestamp with time zone, p_items jsonb)
 RETURNS jsonb
 LANGUAGE sql
 IMMUTABLE STRICT SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'ops', 'public', 'pg_temp'
AS $function$
  select jsonb_build_object(
    'schema_version','context-bundle.v1',
    'header',jsonb_build_object(
      'tenant_id',p_tenant, 'work_request_id',p_work_request_ref,
      'accepted_plan_revision_id',p_plan_revision_ref,
      'accepted_plan_revision',p_plan_revision,
      'accepted_plan_digest',p_base_plan_digest,
      'issued_at',to_char(p_issued_at at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"'),
      'mode','shadow', 'retrieval_policy','policy:bounded-doctrine-v1',
      'retrieval_policy_version','v1','compiler_id','compiler:context-activation-v1','compiler_version','v1',
      'compiler_digest','sha256:'||encode(public.digest('compiler:context-activation-v1','sha256'),'hex'),
      'query_basis_digest','sha256:'||encode(public.digest(p_tenant||':'||p_work_request_ref||':'||p_plan_revision_ref,'sha256'),'hex'),
      'grounding_plan',jsonb_build_object('inline_budget',64,'retrieval_policy','bounded-doctrine','cache_segment','plan-bound','modalities',jsonb_build_array('metadata_only'),'freshness_sla','accepted-plan-bound')
    ), 'items',(select jsonb_agg(item || jsonb_build_object('artifact_kind',item->>'kind','scope_redaction',item->>'redaction_class','trigger_ref',item->>'trigger','consumer_ref',item->>'consumer','delivery_mode',case when (item->>'required')::boolean and item->>'kind' in ('doctrine','rule','decision') then 'inline' when item->>'kind'='memory' then 'on_demand_tool' else 'reference_only' end,'representation_kind',item->>'kind','freshness_sla','accepted-plan-bound','selection_reason',coalesce(item->>'selection_reason','canonical-compiler'),'selection_rank',coalesce((item->>'selection_rank')::int,ordinal),'requirement_class',case when (item->>'required')::boolean then 'required' else 'advisory' end) order by ordinal) from jsonb_array_elements(p_items) with ordinality x(item,ordinal))
  )
$function$;

CREATE OR REPLACE FUNCTION ops.create_calendar_canary_source_snapshot(p_job_id uuid, p_lease uuid)
 RETURNS TABLE(id uuid, snapshot_digest text, contact_count integer, snapshot_text text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare j ops.job%rowtype;s jsonb;r ops.calendar_canary_source_snapshot%rowtype;
begin
 j:=ops.calendar_canary_live_job(p_job_id,p_lease);
 select coalesce(jsonb_agg(x order by source_rank,x->>'email',x->>'ref'),'[]') into s from(
  select jsonb_build_object('email',lower(btrim("Email")),'ref',"Client ID",'name',"Name",'org',"Practice / Entity") x,1 source_rank from public.v_export_clients where "Email" is not null and position('@' in btrim("Email"))>1
  union all select jsonb_build_object('email',lower(btrim("Email")),'ref',"Lead ID",'name',"Contact Name",'org',"Practice"),2 from public.v_export_leads where "Email" is not null and position('@' in btrim("Email"))>1)q;
 if jsonb_array_length(s)=0 then raise exception using errcode='22023',message='calendar canary source snapshot is empty'; end if;
 insert into ops.calendar_canary_source_snapshot(job_id,attempt,workflow_version,snapshot,snapshot_digest,contact_count)
 values(j.id,j.attempt,5,s,encode(public.digest(convert_to(s::text,'UTF8'),'sha256'),'hex'),jsonb_array_length(s)) on conflict(job_id,attempt) do nothing returning * into r;
 if not found then
  select * into r from ops.calendar_canary_source_snapshot where job_id=j.id and attempt=j.attempt;
  if r.snapshot_digest<>encode(public.digest(convert_to(s::text,'UTF8'),'sha256'),'hex') or r.contact_count<>jsonb_array_length(s) then raise exception using errcode='23505',message='calendar canary source snapshot replay conflicts with canonical contacts'; end if;
 end if;
 return query select r.id,r.snapshot_digest,r.contact_count,r.snapshot::text;
end $function$;

CREATE OR REPLACE FUNCTION ops.create_nightly_availability_canary_source_snapshot(p_job_id uuid, p_lease uuid)
 RETURNS TABLE(id uuid, snapshot_digest text, availability_count integer, open_search_count integer, snapshot_text text)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare j ops.job%rowtype;s jsonb;r ops.nightly_availability_canary_source_snapshot%rowtype;
begin
 j:=ops.nightly_availability_canary_live_job(p_job_id,p_lease);
 select jsonb_build_object(
   'availabilities',coalesce((select jsonb_agg(x order by x->>'id') from (
      select jsonb_build_object('id',av.id::text,'status',av.status,'rate_norm',av.rate_norm_sf_yr,
        'owed',av.norm_owed,'available_on',av.available_on,'observed',av.observed_at::date,
        'source',av.source,'area',sp.area_amount,'suite',sp.suite,'city',b.city,'state',b.state,
        'sub_type',b.sub_type,'address',b.address,'bname',b.name) x
        from (select distinct on (av.space_id) av.* from public.availability av
               order by av.space_id,av.observed_at desc,av.id desc) av
        join public.space sp on sp.id=av.space_id join public.building b on b.id=sp.building_id)q),'[]'::jsonb),
   'searches',coalesce((select jsonb_agg(x order by x->>'ref',x->>'id') from (
      select jsonb_build_object('id',s.id::text,'spec',s.spec,'ref',coalesce(c.roster_ref,''),'name',p.name) x
        from public.space_search s join public.client c on c.id=s.client_id join public.party p on p.id=c.party_id where s.status='open')q),'[]'::jsonb)) into s;
 if jsonb_array_length(s->'availabilities')=0 or jsonb_array_length(s->'searches')=0 then
   raise exception using errcode='22023',message='nightly availability canary source must contain availability and open-search evidence';
 end if;
 insert into ops.nightly_availability_canary_source_snapshot(job_id,attempt,workflow_version,snapshot,snapshot_digest,availability_count,open_search_count)
 values(j.id,j.attempt,3,s,encode(public.digest(convert_to(s::text,'UTF8'),'sha256'),'hex'),jsonb_array_length(s->'availabilities'),jsonb_array_length(s->'searches'))
 on conflict(job_id,attempt) do nothing returning * into r;
 if not found then
  select * into r from ops.nightly_availability_canary_source_snapshot where job_id=j.id and attempt=j.attempt;
  if r.snapshot_digest<>encode(public.digest(convert_to(s::text,'UTF8'),'sha256'),'hex') or r.availability_count<>jsonb_array_length(s->'availabilities') or r.open_search_count<>jsonb_array_length(s->'searches') then raise exception using errcode='23505',message='nightly availability canary source replay conflicts with canonical snapshot'; end if;
 end if;
 return query select r.id,r.snapshot_digest,r.availability_count,r.open_search_count,r.snapshot::text;
end $function$;

CREATE OR REPLACE FUNCTION ops.deactivate_guidance_registry(p_registry_id uuid, p_manifest_digest text, p_idempotency_key text, p_reason text)
 RETURNS uuid
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare
  v_authority_slug text;
  v_authority_actor uuid;
  v_registry_owner uuid;
  v_active_digest text;
  v_existing record;
  v_receipt_id uuid;
  v_event_id uuid;
begin
  v_authority_slug := ops.authority_actor_slug();
  select id into v_authority_actor from public.actor where slug=v_authority_slug and kind='human' and active;
  select created_by into v_registry_owner from ops.guidance_registry where id=p_registry_id;
  if v_authority_actor is null or v_registry_owner is null or v_authority_actor<>v_registry_owner then
    raise exception 'guidance registry deactivation requires its accountable human authority actor';
  end if;
  if p_manifest_digest !~ '^[0-9a-f]{64}$' or coalesce(btrim(p_idempotency_key),'')=''
     or coalesce(btrim(p_reason),'')='' then
    raise exception 'deactivation requires a sha256 manifest digest, idempotency key and reason';
  end if;
  perform pg_advisory_xact_lock(hashtextextended('guidance-registry-deactivation:' || p_idempotency_key,0));
  select ar.id,ar.kind,ar.subject_type,ar.subject_id,ar.actor_id,ar.decision,
         ar.contract_hash,ge.id as event_id,ge.manifest_digest,ge.reason
    into v_existing from ops.authority_receipt ar
    left join ops.guidance_registry_event ge on ge.authority_receipt_id=ar.id
   where ar.idempotency_key=p_idempotency_key;
  if v_existing.id is not null then
    if v_existing.kind<>'rejection' or v_existing.subject_type<>'guidance'
       or v_existing.subject_id<>p_registry_id or v_existing.actor_id<>v_authority_actor
       or v_existing.decision<>'retired' or v_existing.contract_hash<>p_manifest_digest
       or v_existing.event_id is null or v_existing.manifest_digest<>p_manifest_digest
       or v_existing.reason<>p_reason then
      raise exception 'idempotency key already names a different or incomplete guidance registry deactivation';
    end if;
    return v_existing.event_id;
  end if;
  select manifest_digest into v_active_digest from ops.v_guidance_registry_state
   where registry_id=p_registry_id and state='active';
  if v_active_digest is null or v_active_digest<>p_manifest_digest then
    raise exception 'guidance registry deactivation requires the exact currently active manifest digest';
  end if;
  insert into ops.authority_receipt
    (idempotency_key,kind,subject_type,subject_id,actor_id,decision,contract_hash,evidence_refs)
  values (p_idempotency_key,'rejection','guidance',p_registry_id,v_authority_actor,
          'retired',p_manifest_digest,array[p_registry_id::text])
  returning id into v_receipt_id;
  insert into ops.guidance_registry_event
    (registry_id,state,authority_receipt_id,manifest_digest,reason)
  values (p_registry_id,'inactive',v_receipt_id,p_manifest_digest,p_reason)
  returning id into v_event_id;
  return v_event_id;
end $function$;

CREATE OR REPLACE FUNCTION ops.decide_guidance_import_batch(p_batch_id uuid, p_manifest_digest text, p_state text, p_idempotency_key text, p_reason text)
 RETURNS uuid
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare
  v_batch ops.guidance_import_batch%rowtype;
  v_authority_slug text;
  v_authority_actor uuid;
  v_registry_owner uuid;
  v_existing record;
  v_decision_id uuid;
  v_entry ops.guidance_import_entry%rowtype;
  v_item_id uuid;
  v_revision_id uuid;
  v_lifecycle_id uuid;
  v_binding_id uuid;
  v_mapping ops.guidance_import_mapping_execution%rowtype;
  v_active_mapping_id uuid;
begin
  v_authority_slug := ops.authority_actor_slug();
  select id into v_authority_actor from public.actor where slug=v_authority_slug and kind='human' and active;
  select created_by into v_registry_owner from ops.guidance_registry where singleton;
  if v_authority_actor is null or v_authority_actor<>v_registry_owner then
    raise exception 'guidance import batch decision requires the accountable registry human authority';
  end if;
  if p_state <> 'active' or p_manifest_digest !~ '^[0-9a-f]{64}$'
     or coalesce(btrim(p_idempotency_key),'')='' or coalesce(btrim(p_reason),'')='' then
    raise exception 'guidance import decision requires state, digest, idempotency key and reason';
  end if;
  select * into v_batch from ops.guidance_import_batch where id=p_batch_id;
  if v_batch.id is null or v_batch.manifest_digest<>p_manifest_digest
     or not exists (select 1 from ops.guidance_import_apply_event where batch_id=p_batch_id) then
    raise exception 'guidance import batch must be staged, exact-digest matched and applied before a decision';
  end if;
  perform ops.assert_guidance_import_inventory(p_batch_id);
  select * into v_existing from ops.guidance_import_decision_event where idempotency_key=p_idempotency_key;
  if v_existing.id is not null then
    if v_existing.batch_id<>p_batch_id or v_existing.manifest_digest<>p_manifest_digest
       or v_existing.state<>p_state or v_existing.authority_actor_id<>v_authority_actor
       or v_existing.reason<>p_reason then
      raise exception 'idempotency key already names a different guidance import decision';
    end if;
    return v_existing.id;
  end if;
  insert into ops.guidance_import_decision_event
    (batch_id,manifest_digest,state,idempotency_key,authority_actor_id,reason)
  values (p_batch_id,p_manifest_digest,p_state,p_idempotency_key,v_authority_actor,p_reason)
  returning id into v_decision_id;
  for v_entry in select * from ops.guidance_import_entry where batch_id=p_batch_id order by ordinal loop
    select i.id,r.id into v_item_id,v_revision_id from ops.guidance_item i
      join ops.guidance_revision r on r.guidance_item_id=i.id and r.version=1
     where i.source_rule_id=v_entry.source_rule_id and i.source_clause=v_entry.source_clause;
    if v_revision_id is null then
      raise exception 'applied import entry % has no exact revision',v_entry.guidance_id;
    end if;
    v_lifecycle_id := ops.record_guidance_decision(
      v_revision_id,p_state,
      encode(public.digest(convert_to(p_idempotency_key || ':revision:' || v_revision_id::text,'UTF8'),'sha256'),'hex'),
      p_reason);
    if p_state='active' then
      select authority_binding_id into v_binding_id from ops.guidance_lifecycle_event where id=v_lifecycle_id;
      for v_mapping in select * from ops.guidance_import_mapping_execution
          where batch_id=p_batch_id and entry_id=v_entry.id
            and active_mapping_id is null order by ordinal loop
        if exists (select 1 from ops.guidance_import_mapping_execution prior
                    where prior.proposed_mapping_id=v_mapping.proposed_mapping_id
                      and prior.active_mapping_id is not null) then
          continue;
        end if;
        v_active_mapping_id := ops.activate_guidance_situation_mapping(
          v_mapping.proposed_mapping_id,v_binding_id,p_reason);
        -- Mapping execution is append-only; record the activated counterpart
        -- in a second immutable row rather than rewriting the proposal row.
        insert into ops.guidance_import_mapping_execution
          (batch_id,entry_id,ordinal,concept_id,doctrine_section_id,proposed_mapping_id,active_mapping_id)
        values (p_batch_id,v_entry.id,v_mapping.ordinal + 1000000,
                v_mapping.concept_id,v_mapping.doctrine_section_id,
                v_mapping.proposed_mapping_id,v_active_mapping_id);
      end loop;
    end if;
  end loop;
  return v_decision_id;
end $function$;

CREATE OR REPLACE FUNCTION ops.hermes_runtime_admission_for_brief_v1(p_runtime_slug text, p_profile_key text, p_sponsor_slug text, p_work_request text, p_binding_id text)
 RETURNS jsonb
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'ops', 'public', 'pg_temp'
AS $function$
declare
  tenant text := current_setting('carr.organization_tenant_id', true);
  runtime_actor public.actor%rowtype;
  sponsor public.actor%rowtype;
  envelope_row ops.execution_envelope_v1%rowtype;
  expected_state_version integer;
  expected_plan_hash text;
  expected_runtime text := 'runtime:' || p_profile_key;
  expected_agent text := 'agent:' || p_profile_key;
  actual_envelope_digest text;
begin
  -- The caller cannot choose any of these values in the tool payload: the
  -- Worker passes the authenticated actor and sponsor, while the rest is the
  -- exact Bot-Brief request. Unknown actors and missing tenant context refuse
  -- without revealing another tenant's registration.
  if p_runtime_slug is distinct from 'hermes-pilot' then
    return jsonb_build_object('status','not_registered','authorized',false,'reason','runtime_identity_not_registered');
  end if;
  select * into runtime_actor from public.actor
   where slug='hermes-pilot' and kind='automation' and active;
  if runtime_actor.id is null or coalesce(tenant,'')='' or p_work_request is null or p_binding_id is null then
    return jsonb_build_object('status','not_registered','authorized',false,'reason','runtime_or_activation_missing');
  end if;
  select * into sponsor from public.actor where slug=p_sponsor_slug and kind='human' and active;
  if sponsor.id is null then
    return jsonb_build_object('status','stale','authorized',false,'reason','sponsoring_human_unavailable');
  end if;

  select e.* into envelope_row
    from ops.execution_envelope_v1 e
    join ops.context_activation_binding b on b.id=e.activation_binding_id
    join ops.work_request w on w.id=b.work_request_id
    join ops.sourced_work_request_plan plan on plan.id=b.plan_id
    join ops.sourced_work_request_plan_acceptance_receipt ar
      on ar.work_request_id=w.id and ar.plan_id=plan.id
    join ops.work_request_execution_assignment a on a.work_request_id=w.id
      and a.sponsoring_human_id=sponsor.id
    join public.agent_profile p on p.id=a.profile_id
   where e.organization_tenant_id=tenant and b.organization_tenant_id=tenant
     and w.organization_tenant_id=tenant and w.ref=p_work_request
     and b.binding_id=p_binding_id and e.activation_binding_id=b.id
     and b.expires_at > now() and e.expires_at > now()
     and w.version=b.work_request_version and plan.work_request_id=w.id
     and plan.plan_hash=b.plan_hash and ar.plan_hash=b.plan_hash
     and ar.result_version=w.version and p.profile_key=p_profile_key
     and e.work_request_id=w.id and e.plan_hash=b.plan_hash
     and p.status='active' and p.current_model is not null and p.current_desk is not null;
  if not found then
    return jsonb_build_object('status','stale','authorized',false,'reason','activation_or_envelope_not_exact');
  end if;

  select b.work_request_version, b.plan_hash
    into expected_state_version, expected_plan_hash
    from ops.context_activation_binding b
   where b.id=envelope_row.activation_binding_id;

  actual_envelope_digest := 'sha256:' || encode(
    public.digest(ops.guidance_import_canonical_json(envelope_row.envelope),'sha256'),'hex');

  if envelope_row.envelope->'server_binding'->'identity'->>'runtime_principal' is distinct from expected_runtime
     or envelope_row.envelope->'server_binding'->'identity'->>'agent_principal_id' is distinct from expected_agent
     or envelope_row.envelope->'server_binding'->'identity'->>'sponsoring_human_id' is distinct from ('human:' || p_sponsor_slug)
     or envelope_row.envelope->'server_binding'->'identity'->>'organization_tenant_id' is distinct from tenant
     or envelope_row.envelope->'server_binding'->'identity'->>'client_mutable' is distinct from 'false'
     or envelope_row.envelope->'server_binding'->'authority'->>'read_only' is distinct from 'true'
     or envelope_row.envelope->'server_binding'->'authority'->>'client_mutable' is distinct from 'false'
     or envelope_row.envelope->'server_binding'->'authority'->>'capability_profile' is distinct from 'capability:metadata-only'
     or envelope_row.envelope->'server_binding'->'adapter'->>'surface' is distinct from 'hermes_desktop'
     or envelope_row.envelope->'server_binding'->'adapter'->>'adapter_id' is distinct from 'adapter:hermes-desktop'
     or envelope_row.envelope->'server_binding'->'adapter'->>'adapter_version' is distinct from 'v1'
     or envelope_row.envelope->'server_binding'->'adapter'->>'native_session_ref' is distinct from ('native:profile-' || p_profile_key)
     or envelope_row.envelope->'server_binding'->'adapter'->>'configuration_fingerprint' is distinct from envelope_row.configuration_digest
     or envelope_row.runtime_profile->>'profile_key' is distinct from p_profile_key
     or envelope_row.runtime_profile->>'profile_version' is distinct from (
       select p.version::text from public.agent_profile p where p.profile_key=p_profile_key)
     or envelope_row.runtime_profile->>'model_id' is distinct from (
       select 'model:' || p.current_model from public.agent_profile p where p.profile_key=p_profile_key)
     or envelope_row.runtime_profile->>'desk' is distinct from (
       select p.current_desk from public.agent_profile p where p.profile_key=p_profile_key)
     or envelope_row.envelope->'server_binding'->'adapter'->>'provider_id' is distinct from envelope_row.runtime_profile->>'provider_id'
     or envelope_row.envelope->'server_binding'->'adapter'->>'model_id' is distinct from envelope_row.runtime_profile->>'model_id'
     or envelope_row.envelope->'state_binding'->>'state_version' is distinct from expected_state_version::text
     or envelope_row.envelope->'state_binding'->>'canonical_record_digest' is distinct from expected_plan_hash
     or envelope_row.envelope->>'work_request_id' is distinct from p_work_request
     or envelope_row.envelope->>'context_activation_ref' is distinct from p_binding_id
     or envelope_row.envelope_digest is distinct from actual_envelope_digest then
    return jsonb_build_object('status','stale','authorized',false,'reason','server_envelope_identity_mismatch');
  end if;

  return jsonb_build_object(
    'status','registered','authorized',true,'reason','exact_server_envelope',
    'registration_scope','execution_envelope','grants_authority',false,
    'runtime_registration_id','envelope:' || envelope_row.id::text,
    'runtime_principal',envelope_row.envelope->'server_binding'->'identity'->>'runtime_principal',
    'agent_principal_id',envelope_row.envelope->'server_binding'->'identity'->>'agent_principal_id',
    'organization_tenant_id',tenant,'sponsoring_human_slug',p_sponsor_slug,
    'work_request',p_work_request,
    'profile_version',(envelope_row.runtime_profile->>'profile_version')::integer,
    'native_session_ref',envelope_row.envelope->'server_binding'->'adapter'->>'native_session_ref',
    'surface',envelope_row.envelope->'server_binding'->'adapter'->>'surface',
    'adapter_id',envelope_row.envelope->'server_binding'->'adapter'->>'adapter_id',
    'adapter_version',envelope_row.envelope->'server_binding'->'adapter'->>'adapter_version',
    'provider_id',envelope_row.envelope->'server_binding'->'adapter'->>'provider_id',
    'model_id',envelope_row.envelope->'server_binding'->'adapter'->>'model_id',
    'configuration_fingerprint',envelope_row.envelope->'server_binding'->'adapter'->>'configuration_fingerprint',
    'capability_profile',envelope_row.envelope->'server_binding'->'authority'->>'capability_profile',
    'read_only',true,'envelope_digest',envelope_row.envelope_digest,
    'activation_binding_id',p_binding_id,'expires_at',envelope_row.expires_at,
    'device_binding_status','not_asserted',
    'operator_surface','job-passport:context-activation',
    'telemetry_ref','observatory:activation-reliability:' || p_binding_id
  );
end $function$;

CREATE OR REPLACE FUNCTION ops.ingest_calendar_prebrief_projection(p_job_id uuid, p_lease uuid, p_observed_calendar_keys text[], p_events jsonb)
 RETURNS ops.calendar_prebrief_projection_receipt
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare
  v_job ops.job%rowtype;
  v_sponsor text;
  v_session_sponsor text;
  v_execution_kind text;
  v_allowed text[];
  v_allowlist_digest text;
  v_allowlist_revision_id uuid;
  v_observed text[];
  v_event jsonb;
  v_event_id uuid;
  v_calendar_key text;
  v_event_key text;
  v_occurrence_key text;
  v_starts_at timestamptz;
  v_ends_at timestamptz;
  v_title text;
  v_location text;
  v_participant_ref text;
  v_party_id uuid;
  v_subject_type text;
  v_subject_id uuid;
  v_match_count integer;
  v_event_participant_count integer;
  v_event_count integer := 0;
  v_participant_count integer := 0;
  v_canonical_events jsonb;
  v_event_digest text;
  v_snapshot_digest text;
  v_receipt ops.calendar_prebrief_projection_receipt%rowtype;
  v_source ops.calendar_prebrief_source_attestation_receipt%rowtype;
begin
  case session_user
    when 'carr_calendar_prebrief_joe' then v_session_sponsor := 'joe';
    when 'carr_calendar_prebrief_dell' then v_session_sponsor := 'dell';
    else raise exception using errcode='42501',message='calendar prebrief projection requires its named externally provisioned execution identity';
  end case;
  if not pg_has_role(session_user,'carr_calendar_prebrief_jobs','member') then
    raise exception using errcode='42501',message='calendar prebrief projection execution identity lacks its capability bundle';
  end if;
  if p_events is null or pg_column_size(p_events)>262144 or jsonb_typeof(p_events)<>'array' then
    raise exception using errcode='22023',message='calendar prebrief projection requires a bounded event array';
  end if;
  if jsonb_array_length(p_events)>128 then
    raise exception using errcode='22023',message='calendar prebrief projection event count exceeds its bound';
  end if;

  select * into v_job from ops.job where id=p_job_id for update;
  if not found or v_job.state<>'running' or v_job.lease_token is distinct from p_lease
     or v_job.leased_until is null or v_job.leased_until<now() then
    raise exception using errcode='55000',message='calendar prebrief projection requires current live job lease';
  end if;
  if v_job.scheduled_for < now()-interval '30 minutes' or v_job.scheduled_for > now()+interval '5 minutes' then
    raise exception using errcode='22023',message='calendar prebrief projection refuses job scheduled outside its DB-clock window';
  end if;
  select owner_actor,execution_kind into v_sponsor,v_execution_kind
    from ops.job_definition where key=v_job.definition_key and version=v_job.definition_version for update;
  if not found or v_job.definition_key not in ('calendar-prebrief-projection-joe-daily','calendar-prebrief-projection-dell-daily')
     or v_job.definition_version<>1 or v_job.mode<>'live' or v_execution_kind<>'deterministic'
     or (v_job.definition_key='calendar-prebrief-projection-joe-daily' and v_sponsor<>'joe')
     or (v_job.definition_key='calendar-prebrief-projection-dell-daily' and v_sponsor<>'dell')
     or v_sponsor<>v_session_sponsor then
    raise exception using errcode='42501',message='calendar prebrief projection execution identity does not match the static job owner';
  end if;
  perform pg_advisory_xact_lock(hashtextextended('calendar-prebrief-projection:' || v_sponsor,0));
  select calendar_keys,configuration_digest,active_revision_id into v_allowed,v_allowlist_digest,v_allowlist_revision_id from ops.calendar_prebrief_allowed_calendar
   where sponsor=v_sponsor for update;
  if not found or coalesce(cardinality(v_allowed),0)=0
     or array_position(v_allowed,null) is not null
     or v_allowlist_digest !~ '^[0-9a-f]{64}$' or v_allowlist_revision_id is null
     or exists(select 1 from unnest(v_allowed) keys(k) where k !~ '^[0-9a-f]{64}$')
     or cardinality(v_allowed)<>(select count(distinct k) from unnest(v_allowed) keys(k))
     or not exists(select 1 from ops.calendar_prebrief_allowlist_receipt ar
                   where ar.id=v_allowlist_revision_id and ar.sponsor=v_sponsor
                     and ar.configuration_digest=v_allowlist_digest and ar.calendar_keys=v_allowed) then
    raise exception using errcode='22023',message='calendar prebrief projection requires a valid DB-owned sponsor allowlist';
  end if;
  select array_agg(k order by k) into v_observed from unnest(coalesce(p_observed_calendar_keys,'{}'::text[])) keys(k);
  if coalesce(cardinality(v_observed),0)=0 or array_position(v_observed,null) is not null
     or exists(select 1 from unnest(v_observed) keys(k) where k !~ '^[0-9a-f]{64}$')
     or cardinality(v_observed)<>(select count(distinct k) from unnest(v_observed) keys(k))
     or v_observed is distinct from v_allowed then
    raise exception using errcode='22023',message='calendar prebrief observed calendars must have exact DB allowlist coverage';
  end if;

  -- Prevalidate every event and every participant before deleting a current
  -- projection.  A bad event cannot partially replace a good one.
  for v_event in select value from jsonb_array_elements(p_events) loop
    if pg_column_size(v_event)>4096 or jsonb_typeof(v_event)<>'object'
       or exists(select 1 from jsonb_object_keys(v_event) keys(key)
                 where key not in ('calendar_key','event_key','occurrence_key','starts_at','ends_at','title','location','participant_refs'))
       or exists(select key from unnest(array['calendar_key','event_key','occurrence_key','starts_at','ends_at','title','location','participant_refs']) required(key)
                 except select key from jsonb_object_keys(v_event) actual(key)) then
      raise exception using errcode='22023',message='calendar prebrief event has fields outside its bounded contract';
    end if;
    if jsonb_typeof(v_event->'calendar_key')<>'string' or jsonb_typeof(v_event->'event_key')<>'string'
       or jsonb_typeof(v_event->'occurrence_key')<>'string' or jsonb_typeof(v_event->'starts_at')<>'string'
       or jsonb_typeof(v_event->'ends_at')<>'string' or jsonb_typeof(v_event->'title')<>'string'
       or jsonb_typeof(v_event->'participant_refs')<>'array'
       or (v_event->'location' is not null and jsonb_typeof(v_event->'location') not in ('string','null')) then
      raise exception using errcode='22023',message='calendar prebrief event has invalid bounded fields';
    end if;
    v_event_participant_count:=jsonb_array_length(v_event->'participant_refs');
    if v_event_participant_count>16 or exists(select 1 from jsonb_array_elements(v_event->'participant_refs') refs(value) where jsonb_typeof(refs.value)<>'string')
       or v_event_participant_count<>(select count(distinct value#>>'{}') from jsonb_array_elements(v_event->'participant_refs') refs(value)) then
      raise exception using errcode='22023',message='calendar prebrief event has invalid or duplicate participant refs';
    end if;
    v_participant_count:=v_participant_count+v_event_participant_count;
    if v_participant_count>256 then
      raise exception using errcode='22023',message='calendar prebrief projection participant count exceeds its bound';
    end if;
    v_calendar_key:=v_event->>'calendar_key'; v_event_key:=v_event->>'event_key';
    v_occurrence_key:=v_event->>'occurrence_key'; v_title:=v_event->>'title'; v_location:=v_event->>'location';
    if v_calendar_key!~'^[0-9a-f]{64}$' or v_event_key!~'^[0-9a-f]{64}$' or v_occurrence_key!~'^[0-9a-f]{64}$'
       or length(v_title)>240 or coalesce(length(v_location),0)>240 or btrim(v_title)=''
       or v_title~'[[:alnum:]._%+-]+@[[:alnum:].-]+\.[[:alpha:]]{2,}'
       or coalesce(v_location,'')~'[[:alnum:]._%+-]+@[[:alnum:].-]+\.[[:alpha:]]{2,}'
       or v_title~*'(^|[^[:alnum:]])[a-z][a-z0-9+.-]*:[^[:space:]]' or coalesce(v_location,'')~*'(^|[^[:alnum:]])[a-z][a-z0-9+.-]*:[^[:space:]]'
       or v_title~*'www\.' or coalesce(v_location,'')~*'www\.'
       or v_title~*'\m(join|meeting|conference)[[:space:]]*(id|code|link)?[[:space:]]*[:#=]?[[:space:]]*[a-z0-9_-]{6,}\M'
       or coalesce(v_location,'')~*'\m(join|meeting|conference)[[:space:]]*(id|code|link)?[[:space:]]*[:#=]?[[:space:]]*[a-z0-9_-]{6,}\M' then
      raise exception using errcode='22023',message='calendar prebrief bounded fields must not contain email, URI, www, or join locator';
    end if;
    v_starts_at:=(v_event->>'starts_at')::timestamptz; v_ends_at:=(v_event->>'ends_at')::timestamptz;
    if v_ends_at<=v_starts_at or v_starts_at<v_job.scheduled_for-interval '7 days'
       or v_starts_at>v_job.scheduled_for+interval '45 days' or v_ends_at<v_job.scheduled_for-interval '7 days'
       or v_ends_at>v_job.scheduled_for+interval '45 days' then
      raise exception using errcode='22023',message='calendar prebrief event is outside its bounded snapshot window';
    end if;
    if not (v_calendar_key=any(v_allowed)) then
      raise exception using errcode='22023',message='calendar prebrief event calendar is outside the DB allowlist';
    end if;
    for v_participant_ref in select value from jsonb_array_elements_text(v_event->'participant_refs') loop
      if length(v_participant_ref)>128 or v_participant_ref!~'^[A-Za-z0-9][A-Za-z0-9._:-]*$' then
        raise exception using errcode='22023',message='calendar prebrief participant ref must be a bounded canonical ref';
      end if;
      select count(*) into v_match_count from public.v_ref_index r where r.ref=v_participant_ref and not r.merged and r.party_id is not null;
      if v_match_count<>1 then
        raise exception using errcode='22023',message='calendar prebrief participant ref does not resolve uniquely to one live unmerged party';
      end if;
    end loop;
  end loop;
  if (select count(*) from jsonb_array_elements(p_events) element(value))
     <> (select count(distinct value->>'occurrence_key') from jsonb_array_elements(p_events) element(value)) then
    raise exception using errcode='22023',message='calendar prebrief snapshot has duplicate occurrence keys';
  end if;

  select coalesce(jsonb_agg(event order by event->>'occurrence_key'),'[]'::jsonb) into v_canonical_events
  from (select jsonb_set(element.value,'{participant_refs}',coalesce((select jsonb_agg(ref order by ref)
          from jsonb_array_elements_text(element.value->'participant_refs') refs(ref)),'[]'::jsonb)) event
        from jsonb_array_elements(p_events) element(value)) normalized;
  v_event_digest:=encode(public.digest(convert_to(v_canonical_events::text,'UTF8'),'sha256'),'hex');
  v_snapshot_digest:=encode(public.digest(convert_to(jsonb_build_object(
    'allowlist_revision_id',v_allowlist_revision_id,'allowlist_digest',v_allowlist_digest,'observed_calendar_keys',to_jsonb(v_observed),
    'events',v_canonical_events,'snapshot_at',v_job.scheduled_for)::text,'UTF8'),'sha256'),'hex');
  select * into v_source from ops.calendar_prebrief_source_attestation_receipt
   where job_id=v_job.id and attempt=v_job.attempt;
  if not found or v_source.lease_token<>p_lease or v_source.sponsor<>v_sponsor
     or v_source.mode<>'live' or v_source.destination<>'live'
     or v_source.snapshot_at<>v_job.scheduled_for or v_source.allowlist_revision_id<>v_allowlist_revision_id
     or v_source.allowlist_digest<>v_allowlist_digest or v_source.observed_calendar_keys is distinct from v_observed
     or v_source.event_count<>jsonb_array_length(p_events) or v_source.canonical_event_digest<>v_event_digest then
    raise exception using errcode='55000',message='calendar prebrief projection requires an exact immutable verified source envelope';
  end if;
  select * into v_receipt from ops.calendar_prebrief_projection_receipt where job_id=v_job.id and attempt=v_job.attempt;
  if found then
    if v_receipt.snapshot_digest<>v_snapshot_digest then raise exception using errcode='23505',message='calendar prebrief job attempt conflicts with immutable snapshot'; end if;
    return v_receipt;
  end if;
  select * into v_receipt from ops.calendar_prebrief_projection_receipt where sponsor=v_sponsor and snapshot_at=v_job.scheduled_for;
  if found then
    if v_receipt.snapshot_digest<>v_snapshot_digest then raise exception using errcode='23505',message='calendar prebrief equal snapshot timestamp conflicts with immutable digest'; end if;
    return v_receipt;
  end if;
  if exists(select 1 from ops.calendar_prebrief_projection_receipt where sponsor=v_sponsor and snapshot_at>v_job.scheduled_for) then
    raise exception using errcode='22023',message='calendar prebrief projection refuses stale snapshot';
  end if;

  -- The second check is deliberately adjacent to the destructive replacement.
  if not exists(select 1 from ops.job where id=v_job.id and state='running' and lease_token=p_lease
                and leased_until is not null and leased_until>=now()) then
    raise exception using errcode='55000',message='calendar prebrief projection lease expired before current projection replacement';
  end if;
  delete from ops.calendar_prebrief_projection_event where sponsor=v_sponsor;
  v_participant_count:=0;
  for v_event in select value from jsonb_array_elements(p_events) loop
    insert into ops.calendar_prebrief_projection_event
      (sponsor,calendar_key,event_key,occurrence_key,starts_at,ends_at,title,location,snapshot_at,allowlist_revision_id)
    values(v_sponsor,v_event->>'calendar_key',v_event->>'event_key',v_event->>'occurrence_key',
      (v_event->>'starts_at')::timestamptz,(v_event->>'ends_at')::timestamptz,v_event->>'title',v_event->>'location',v_job.scheduled_for,v_allowlist_revision_id)
    returning id into v_event_id;
    v_event_count:=v_event_count+1;
    for v_participant_ref in select value from jsonb_array_elements_text(v_event->'participant_refs') loop
      select r.party_id,r.subject_type,r.subject_id into v_party_id,v_subject_type,v_subject_id from public.v_ref_index r
       where r.ref=v_participant_ref and not r.merged and r.party_id is not null;
      insert into ops.calendar_prebrief_projection_participant(event_id,party_id,subject_type,subject_id,participant_ref)
      values(v_event_id,v_party_id,v_subject_type,v_subject_id,v_participant_ref);
      v_participant_count:=v_participant_count+1;
    end loop;
  end loop;
  insert into ops.calendar_prebrief_projection_receipt(job_id,attempt,sponsor,snapshot_at,allowlist_revision_id,allowlist_digest,source_attestation_id,snapshot_digest,event_count,participant_count)
  values(v_job.id,v_job.attempt,v_sponsor,v_job.scheduled_for,v_allowlist_revision_id,v_allowlist_digest,v_source.id,v_snapshot_digest,v_event_count,v_participant_count)
  returning * into v_receipt;
  return v_receipt;
end $function$;

CREATE OR REPLACE FUNCTION ops.ingest_renewal_signed_snapshot(p_job_id uuid, p_lease uuid, p_snapshot_id uuid, p_provider text, p_key_fingerprint text, p_source_observed_at timestamp with time zone, p_payload_sha256 text, p_signature_sha256 text, p_rows jsonb)
 RETURNS TABLE(source_run_id uuid, row_count integer)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare v_job ops.job%rowtype; v_snapshot ops.renewal_source_snapshot%rowtype; v_row jsonb; v_candidate_id uuid; v_run ops.renewal_decision_source_run%rowtype; v_count integer; v_seq integer:=0; v_key text;
begin
 if session_user<>'carr_renewal_source_attestor' or not pg_has_role(session_user,'carr_renewal_source_attestors','member') then raise exception using errcode='42501',message='renewal signed ingress requires the exact renewal source attestor capability'; end if;
 if p_provider is null or btrim(p_provider)='' or octet_length(p_provider)>256 or p_key_fingerprint !~ '^[0-9a-f]{64}$' or p_payload_sha256 !~ '^[0-9a-f]{64}$' or p_signature_sha256 !~ '^[0-9a-f]{64}$' then raise exception using errcode='22023',message='renewal signed ingress provenance is malformed'; end if;
 if jsonb_typeof(p_rows)<>'array' or jsonb_array_length(p_rows)>10000 or octet_length(p_rows::text)>8388608 then raise exception using errcode='22023',message='renewal signed ingress source rows are not bounded'; end if;
 if exists(select 1 from jsonb_array_elements(p_rows) x(value) where jsonb_typeof(value)<>'object'
           or (select array_agg(key order by key) from jsonb_object_keys(value) key) is distinct from array['address','city','county','email','est_basis','est_lease_event','name','org_name','phone','segment','source_key','source_row','state','vertical']
           or nullif(btrim(value->>'source_key'),'') is null or nullif(btrim(value->>'name'),'') is null or octet_length(value->>'source_key')>512 or octet_length(value->>'name')>512 or octet_length((value->'source_row')::text)>65536 or octet_length(value::text)>131072 or jsonb_typeof(value->'source_row')<>'object') then
   raise exception using errcode='22023',message='renewal signed ingress source row shape is malformed'; end if;
 if exists(select 1 from jsonb_array_elements(p_rows) x(value) group by value->>'source_key' having count(*)<>1) then raise exception using errcode='23505',message='renewal signed ingress source key is duplicated'; end if;
 select * into v_job from ops.job where id=p_job_id for update;
 if not found or v_job.definition_key<>'renewal-radar-source-daily' or v_job.definition_version<>1 or v_job.mode<>'live' or v_job.state<>'running' or v_job.lease_token is distinct from p_lease or v_job.leased_until is null or v_job.leased_until<now() then raise exception using errcode='55000',message='renewal signed ingress requires its exact current live job lease'; end if;
 if p_source_observed_at < now()-interval '36 hours' or p_source_observed_at > now()+interval '5 minutes' then raise exception using errcode='22023',message='renewal signed ingress source observation is outside its DB-clock window'; end if;
 insert into ops.renewal_source_snapshot(id,job_id,attempt,provider,key_fingerprint,source_observed_at,payload_sha256,signature_sha256,row_count)
 values(p_snapshot_id,v_job.id,v_job.attempt,p_provider,p_key_fingerprint,p_source_observed_at,p_payload_sha256,p_signature_sha256,jsonb_array_length(p_rows)) on conflict(id) do nothing;
 select * into v_snapshot from ops.renewal_source_snapshot where id=p_snapshot_id for update;
 if not found or v_snapshot.job_id<>v_job.id or v_snapshot.attempt<>v_job.attempt or v_snapshot.provider<>p_provider or v_snapshot.key_fingerprint<>p_key_fingerprint or v_snapshot.source_observed_at<>p_source_observed_at or v_snapshot.payload_sha256<>p_payload_sha256 or v_snapshot.signature_sha256<>p_signature_sha256 or v_snapshot.row_count<>jsonb_array_length(p_rows) then raise exception using errcode='23505',message='renewal signed ingress snapshot identity conflicts with immutable receipt'; end if;
 select count(*) into v_count from ops.renewal_source_snapshot_member where snapshot_id=p_snapshot_id;
 if v_count>0 then
   if v_count<>jsonb_array_length(p_rows) or exists((select value->>'source_key' from jsonb_array_elements(p_rows) x(value)) except (select source_key from ops.renewal_source_snapshot_member where snapshot_id=p_snapshot_id)) or exists((select source_key from ops.renewal_source_snapshot_member where snapshot_id=p_snapshot_id) except (select value->>'source_key' from jsonb_array_elements(p_rows) x(value))) then raise exception using errcode='23505',message='renewal signed ingress replay conflicts with immutable source membership'; end if;
 else
   for v_row in select value from jsonb_array_elements(p_rows) x(value) loop
     v_seq:=v_seq+1; v_key:=btrim(v_row->>'source_key');
     insert into public.ingest_inbox(source,external_id,payload,status,triage_note) values('renewal-radar-signed',p_snapshot_id::text||':'||v_key,jsonb_build_object('provider',p_provider,'snapshot_id',p_snapshot_id,'observed_at',p_source_observed_at,'row',v_row),'new','signed renewal source ingress; payload is untrusted source data') on conflict(source,external_id) do nothing;
     insert into public.candidate_pool(source,source_key,source_seq,source_row,name,org_name,vertical,address,city,county,state,email,phone,segment,score,score_basis,est_lease_event,est_basis,status,created_by,updated_by)
       select 'renewal-radar',v_key,v_seq,v_row->'source_row',btrim(v_row->>'name'),nullif(btrim(v_row->>'org_name'),''),nullif(btrim(v_row->>'vertical'),''),nullif(btrim(v_row->>'address'),''),nullif(btrim(v_row->>'city'),''),nullif(btrim(v_row->>'county'),''),nullif(btrim(v_row->>'state'),''),nullif(btrim(v_row->>'email'),''),nullif(btrim(v_row->>'phone'),''),nullif(btrim(v_row->>'segment'),''),null,'unscored signed renewal source snapshot',nullif(v_row->>'est_lease_event','')::date,nullif(btrim(v_row->>'est_basis'),''),'pool',a.id,a.id from public.actor a where a.slug='system'
       on conflict(source,source_key) do update set source_seq=excluded.source_seq,source_row=excluded.source_row,name=excluded.name,org_name=excluded.org_name,vertical=excluded.vertical,address=excluded.address,city=excluded.city,county=excluded.county,state=excluded.state,email=excluded.email,phone=excluded.phone,segment=excluded.segment,score=null,score_basis='unscored signed renewal source snapshot',est_lease_event=excluded.est_lease_event,est_basis=excluded.est_basis,updated_by=excluded.updated_by where candidate_pool.status='pool' returning id into v_candidate_id;
     if v_candidate_id is null then raise exception using errcode='23505',message='renewal signed ingress source key conflicts with a non-pool candidate'; end if;
     insert into ops.renewal_source_snapshot_member(snapshot_id,source_key,candidate_id) values(p_snapshot_id,v_key,v_candidate_id);
     update public.ingest_inbox set status='filed',filed_refs=jsonb_build_object('candidate_pool',v_candidate_id::text) where source='renewal-radar-signed' and external_id=p_snapshot_id::text||':'||v_key and status='new';
   end loop;
 end if;
 select * into v_run from ops.seal_renewal_decision_source_run(p_job_id,p_lease,p_snapshot_id);
 source_run_id:=v_run.id; row_count:=v_snapshot.row_count; return next;
end $function$;

CREATE OR REPLACE FUNCTION ops.issue_execution_envelope_v1_without_environment_gate(p_work_request text, p_binding_id text, p_idempotency_key uuid)
 RETURNS TABLE(envelope_id uuid, envelope_digest text, envelope jsonb, replayed boolean)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare tenant text := current_setting('carr.organization_tenant_id', true); binding ops.context_activation_binding%rowtype;
  work ops.work_request%rowtype; plan ops.sourced_work_request_plan%rowtype; assignment ops.work_request_execution_assignment%rowtype; profile public.agent_profile%rowtype; sponsor public.actor%rowtype; existing ops.execution_envelope_v1%rowtype; issued timestamptz := now(); expires timestamptz;
  runtime jsonb; topology jsonb; evaluation jsonb; configuration text; body jsonb; digest_text text;
begin
  if coalesce(tenant,'')='' then raise exception 'execution envelope requires authenticated tenant context'; end if;
  select b.* into binding from ops.context_activation_binding b join ops.work_request w on w.id=b.work_request_id
   where b.binding_id=p_binding_id and b.organization_tenant_id=tenant and w.ref=p_work_request
     and b.expires_at>=now() and w.version=b.work_request_version for share;
  if not found then raise exception 'execution envelope binding is not visible to tenant'; end if;
  select * into work from ops.work_request where id=binding.work_request_id for share;
  select * into plan from ops.sourced_work_request_plan where id=binding.plan_id for share;
  if plan.id is null or plan.work_request_id<>work.id or plan.plan_hash<>binding.plan_hash
     or not exists (select 1 from ops.sourced_work_request_plan_acceptance_receipt ar where ar.work_request_id=work.id and ar.plan_id=plan.id and ar.plan_hash=binding.plan_hash and ar.result_version=work.version) then
    raise exception 'execution envelope binding is stale or no longer the exact accepted plan';
  end if;
  select * into assignment from ops.work_request_execution_assignment where work_request_id=work.id for share;
  if not found then raise exception 'execution envelope requires a preassigned server-owned profile lane'; end if;
  select * into profile from public.agent_profile where id=assignment.profile_id and status='active' and current_model is not null and current_desk is not null for share;
  select * into sponsor from public.actor where id=assignment.sponsoring_human_id and kind='human' and active for share;
  if profile.id is null or sponsor.id is null then raise exception 'execution envelope assignment has no active durable runtime profile/sponsor'; end if;
  select * into existing from ops.execution_envelope_v1 where organization_tenant_id=tenant and idempotency_key=p_idempotency_key for share;
  if found then
    if existing.work_request_id<>work.id or existing.activation_binding_id<>binding.id then raise exception 'execution envelope idempotency conflict'; end if;
    return query select existing.id,existing.envelope_digest,existing.envelope,true; return;
  end if;
  -- A binding has one server-issued envelope.  A later request cannot cause a
  -- timestamp/profile snapshot to fork the same governed attempt.
  select * into existing from ops.execution_envelope_v1
   where organization_tenant_id=tenant and activation_binding_id=binding.id for share;
  if found then
    return query select existing.id,existing.envelope_digest,existing.envelope,true; return;
  end if;
  -- These bounded versioned metadata refs are server-issued configuration,
  -- never caller authority/provider/model selections. They contain no secret
  -- values and are retained with the immutable envelope for audit.
  runtime := jsonb_build_object('ref','runtime-profile:'||profile.profile_key||':v'||profile.version::text,'profile_key',profile.profile_key,'profile_version',profile.version,'provider_id','provider:'||split_part(profile.current_model,'/',1),'model_id','model:'||profile.current_model,'desk',profile.current_desk,'policy_ref',assignment.policy_ref,'policy_digest',assignment.policy_digest,'modality','modality:text','reasoning_effort_ref','reasoning-effort:governed-default','sampling_profile_ref','sampling:governed-default','context_budget',8192,'cache_policy_ref','cache:governed-default','knowledge_cutoff_posture','knowledge-cutoff:provider-declared','tool_calling_mode','tool-calling:metadata-only');
  runtime := runtime || jsonb_build_object('digest','sha256:'||encode(public.digest(ops.guidance_import_canonical_json(runtime),'sha256'),'hex'));
  topology := jsonb_build_object('ref','execution-topology:single-governed-attempt-v1','kind','single_agent_loop','harness_digest','sha256:'||encode(public.digest(ops.guidance_import_canonical_json(jsonb_build_object('harness','postgres-governed-attempt-v1')),'sha256'),'hex'),'parallelism','sequential','code_model_step_refs',jsonb_build_array('step:model-governed'),'fallback_policy_ref','fallback:stop-and-escalate','stop_condition_refs',jsonb_build_array('stop:capability-expired','stop:critical-failure'),'context_refresh_policy_ref','context-refresh:bound-revisions-only','memory_policy_ref','memory:context-never-authority','sandbox_ref','sandbox:metadata-only','guardrail_ref','guardrail:governed-default','threat_model_ref','threat-model:governed-default');
  topology := topology || jsonb_build_object('digest','sha256:'||encode(public.digest(ops.guidance_import_canonical_json(topology),'sha256'),'hex'));
  evaluation := jsonb_build_object('ref','evaluation-plan:independent-risk-v1','lane_ref','lane:governed-work','risk_class','R2','rubric_digest','sha256:'||encode(public.digest('rubric:independent-risk-v1','sha256'),'hex'),'case_set_digest',binding.bundle_digest,'evaluator_policy_digest','sha256:'||encode(public.digest('evaluator-policy:r2-v1','sha256'),'hex'),'evaluator_ref','evaluator:authority-independent-v1','rubric_ref','rubric:independent-risk-v1','evaluator_version','version:v1','evaluator_digest','sha256:'||encode(public.digest('evaluator:authority-independent-v1:version:v1','sha256'),'hex'),'required_rungs',jsonb_build_array('rung:smoke','rung:regression'),'required_deterministic_check_refs',jsonb_build_array('check:activation-binding','check:critical-security'),'critical_dimensions',jsonb_build_array('dimension:correctness','dimension:security'),'human_acceptance_required',true,'outcome_horizon_ref',case when assignment.environment='rehearsal' then 'outcome-horizon:synthetic-fixture-zero' else 'outcome-horizon:r2-seven-day' end,'outcome_horizon_not_before',to_char((issued + case when assignment.environment='rehearsal' then interval '0' else interval '7 days' end) at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"'),'requirements',jsonb_build_object('required_evaluator_kinds',jsonb_build_array('deterministic','judge','human_acceptance'),'minimum_held_out_case_count',1,'minimum_calibration_ref_count',1,'maximum_critical_failure_count',0,'maximum_critical_failure_rate',0,'confidence_posture','lower_bound_required','drift_tolerance','no_critical_regression','independent_review_required',true,'human_acceptance_required',true,'outcome_horizon_required',true));
  evaluation := evaluation || jsonb_build_object('digest','sha256:'||encode(public.digest(ops.guidance_import_canonical_json(evaluation),'sha256'),'hex'));
  configuration := 'sha256:'||encode(public.digest(ops.guidance_import_canonical_json(runtime||topology||evaluation),'sha256'),'hex');
  expires := least(binding.expires_at, issued + interval '1 hour');
  body := jsonb_build_object(
    'schema_version','execution-envelope.v1','envelope_id','env:'||binding.binding_id,
    'work_request_id',work.ref,
    'plan_revision',jsonb_build_object('id','plan:'||binding.plan_id::text,'revision',plan.plan_version,'digest',binding.plan_hash),
    'agent_session',jsonb_build_object('id','session:'||binding.binding_id,'lease_expires_at',to_char(expires at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"')),
    'issued_at',to_char(issued at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"'),'expires_at',to_char(expires at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    'state_binding',jsonb_build_object('state_version',work.version,'canonical_record_digest',binding.plan_hash,'accepted_resource_revisions','[]'::jsonb,'compare_and_swap_required',true),
    'phase_binding',jsonb_build_object('phase_id','phase:governed-execution','session_affinity','same_native_session_preferred','switch_conditions',jsonb_build_array('verified_checkpoint','phase_boundary'),'native_session_transfer','semantic_state_only'),
    'evaluation_context',jsonb_build_object('experiment_arm','same_pair_audited_state','auditor_mode','diverse_read_only_auditor','evaluation_kernel_ref',evaluation->>'ref','workflow_rubric_digest',evaluation->>'rubric_digest','case_set_digest',binding.bundle_digest),
    'request',jsonb_build_object('job_ref','job:'||work.ref,'input_digest',binding.bundle_digest,'data_class','metadata_only','allowed_actions','[]'::jsonb,'declared_expectations',jsonb_build_object('plan_step_refs','[]'::jsonb,'component_refs','[]'::jsonb,'component_dependencies','[]'::jsonb,'resource_refs','[]'::jsonb)),
    'server_binding',jsonb_build_object('identity',jsonb_build_object('organization_tenant_id',tenant,'sponsoring_human_id','human:'||sponsor.slug,'agent_principal_id','agent:'||profile.profile_key,'runtime_principal','runtime:'||profile.profile_key,'personal_brain_scope','none','personal_brain_version','none','personal_rule_count',0,'derived_by','server_identity_resolution','client_mutable',false),'authority',jsonb_build_object('environment',assignment.environment,'risk_class','R2','capability_profile','capability:metadata-only','capability_grant_ref','grant:'||assignment.id::text,'read_only',true,'derived_by','server_capability_resolution','client_mutable',false),'adapter',jsonb_build_object('surface','hermes_desktop','adapter_id','adapter:hermes-desktop','adapter_version','v1','harness_id','harness:postgres','harness_version','v1','provider_id','provider:'||split_part(profile.current_model,'/',1),'model_id','model:'||profile.current_model,'native_session_ref','native:profile-'||profile.profile_key,'configuration_fingerprint',configuration)),
    'handoff',jsonb_build_object('mode','original','replaces_agent_session_id',null,'capability_inherited',false,'checkpoint_ref',null,'native_session_transfer','semantic_state_only'),
    'activation_binding',jsonb_build_object('bundle_digest',binding.bundle_digest,'item_refs',(select coalesce(jsonb_agg(canonical_ref order by ordinal),'[]'::jsonb) from ops.context_activation_item where binding_id=binding.id),'mode',binding.mode,'retrieval_policy_version','v1'),
    'reliability_policy_binding',jsonb_build_object('policy_ref',assignment.policy_ref,'policy_digest',assignment.policy_digest,'risk_class','R2','mode',binding.mode),
    'context_activation_ref',binding.binding_id,'runtime_profile',runtime,'execution_topology',topology,'evaluation_plan',evaluation
  );
  digest_text := 'sha256:'||encode(public.digest(ops.guidance_import_canonical_json(body),'sha256'),'hex');
  insert into ops.execution_envelope_v1(idempotency_key,organization_tenant_id,work_request_id,plan_hash,activation_binding_id,envelope_digest,envelope,runtime_profile,execution_topology,evaluation_plan,configuration_digest,issued_at,expires_at)
  values(p_idempotency_key,tenant,work.id,binding.plan_hash,binding.id,digest_text,body,runtime,topology,evaluation,configuration,issued,expires)
  returning id,ops.execution_envelope_v1.envelope_digest,ops.execution_envelope_v1.envelope into envelope_id,envelope_digest,envelope;
  return query select envelope_id,envelope_digest,envelope,false;
end $function$;

CREATE OR REPLACE FUNCTION ops.portfolio_accepted_digest(p_revision_id uuid)
 RETURNS text
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'ops', 'public', 'pg_temp'
AS $function$
  select 'sha256:' || encode(public.digest(convert_to(
    ops.portfolio_canonical_json(ops.portfolio_accepted_preimage(p_revision_id)),
    'UTF8'), 'sha256'), 'hex')
$function$;

CREATE OR REPLACE FUNCTION ops.portfolio_child_digest(p_revision_id uuid, p_child_ref text)
 RETURNS text
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'ops', 'public', 'pg_temp'
AS $function$
  select 'sha256:' || encode(public.digest(convert_to(
    ops.portfolio_canonical_json(ops.portfolio_child_preimage(p_revision_id, p_child_ref)),
    'UTF8'), 'sha256'), 'hex')
$function$;

CREATE OR REPLACE FUNCTION ops.portfolio_graph_digest(p_revision_id uuid)
 RETURNS text
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'ops', 'public', 'pg_temp'
AS $function$
  select 'sha256:' || encode(public.digest(convert_to(
    ops.portfolio_canonical_json(ops.portfolio_graph_preimage(p_revision_id)),
    'UTF8'), 'sha256'), 'hex')
$function$;

CREATE OR REPLACE FUNCTION ops.read_governance_queue()
 RETURNS jsonb
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
  select jsonb_build_object(
    'pending_rule_approvals', coalesce((
      select jsonb_agg(jsonb_build_object(
        'rule_id', r.id, 'statement', r.statement, 'human_quote', r.human_quote,
        'scope', r.scope, 'taught_at', r.created_at,
        'enforcement_class', ra.enforcement_class, 'binding_moment', ra.binding_moment,
        'admission_reason', ra.reason, 'enforcement_status', ra.enforcement_status,
        'fixture_refs', ra.fixture_refs, 'admitted_at', ra.admitted_at
      ) order by ra.admitted_at asc, r.id)
      from public.rule r join ops.rule_admission ra on ra.rule_id = r.id
      where r.status = 'proposed' and ra.state = 'admitted'
    ), '[]'::jsonb),
    'pending_guidance_import_batches', coalesce((
      select jsonb_agg(jsonb_build_object(
        'batch_id', b.id, 'manifest_digest', b.manifest_digest, 'reason', b.reason,
        'staging_key', b.staging_key, 'staged_at', b.created_at,
        'entry_count', (select count(*) from ops.guidance_import_entry e where e.batch_id = b.id)
      ) order by b.created_at asc, b.id)
      from ops.guidance_import_batch b
      where not exists (select 1 from ops.guidance_import_decision_event d where d.batch_id = b.id)
    ), '[]'::jsonb),
    'pending_retrieval_proposals', coalesce((
      select jsonb_agg(jsonb_build_object(
        'proposal_id', p.id, 'proposal_type', p.proposal_type, 'payload', p.payload,
        'reason', p.reason, 'proposer_actor_id', p.proposer_id, 'version', p.version,
        'proposed_at', p.created_at
      ) order by p.created_at asc, p.id)
      from public.retrieval_proposal p
      where p.status = 'pending'
    ), '[]'::jsonb)
  )
$function$;

CREATE OR REPLACE FUNCTION ops.reclassify_legacy_rule_admission(p_rule_id uuid, p_new_class text, p_idempotency_key uuid, p_reason text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare
  v_actor_slug text;
  v_actor_id uuid;
  v_rule public.rule%rowtype;
  v_admission ops.rule_admission%rowtype;
  v_prior ops.rule_admission_reclassification_receipt%rowtype;
  v_receipt ops.rule_admission_reclassification_receipt%rowtype;
  v_legacy_note text;
  v_contract jsonb;
  v_contract_hash text;
begin
  v_actor_slug := ops.authority_actor_slug();
  if v_actor_slug<>'joe' then
    raise exception 'legacy admission reclassification requires Joe authority';
  end if;
  select id into v_actor_id from public.actor
   where slug=v_actor_slug and kind='human' and active;
  if v_actor_id is null then raise exception 'Joe authority actor is not active'; end if;
  if btrim(coalesce(p_reason,''))='' or p_idempotency_key is null then
    raise exception 'reclassification reason and idempotency key are required';
  end if;
  if p_new_class not in ('machine_enforceable','judgment_advisory','human_only') then
    raise exception 'unknown enforcement class %',p_new_class;
  end if;

  perform pg_advisory_xact_lock(hashtextextended('rule-reclassification:'||p_idempotency_key::text,0));
  select * into v_prior from ops.rule_admission_reclassification_receipt
   where idempotency_key=p_idempotency_key;
  if found then
    if v_prior.rule_id is distinct from p_rule_id
       or v_prior.enforcement_class_after is distinct from p_new_class
       or v_prior.reason is distinct from btrim(p_reason)
       or v_prior.actor_id is distinct from v_actor_id then
      raise exception 'reclassification idempotency key was reused with different input';
    end if;
    return jsonb_build_object('ok',true,'replayed',true,'rule_id',p_rule_id,
      'enforcement_class',v_prior.enforcement_class_after,
      'reclassification_receipt_id',v_prior.id);
  end if;

  select * into v_rule from public.rule where id=p_rule_id for update;
  if not found then raise exception 'rule % not found',p_rule_id; end if;
  if v_rule.status<>'active' then
    raise exception 'rule % is %, only an active rule''s admission is reclassified here',p_rule_id,v_rule.status;
  end if;
  if exists (select 1 from ops.rule_approval_receipt where rule_id=v_rule.id) then
    raise exception 'rule % carries an approval receipt; its class is bound to the receipt and does not move through the legacy path',p_rule_id;
  end if;
  v_legacy_note := ops.legacy_rule_admission_note(v_rule.id,v_rule.status,v_rule.activated_at);
  if v_legacy_note is null then
    raise exception 'rule % is not a legacy admission; reclassification refused',p_rule_id;
  end if;

  select * into v_admission from ops.rule_admission where rule_id=v_rule.id for update;
  if not found then raise exception 'rule % has no admission row',p_rule_id; end if;
  if v_admission.enforcement_class=p_new_class then
    raise exception 'rule % is already classified %; a no-op reclassification is refused',p_rule_id,p_new_class;
  end if;

  v_contract := jsonb_build_object(
    'rule_id',v_rule.id,'enforcement_class_before',v_admission.enforcement_class,
    'enforcement_class_after',p_new_class,'actor_id',v_actor_id,
    'reason',btrim(p_reason),'legacy_admission',v_legacy_note);
  v_contract_hash := encode(public.digest(v_contract::text,'sha256'),'hex');

  insert into ops.rule_admission_reclassification_receipt
    (idempotency_key,rule_id,enforcement_class_before,enforcement_class_after,
     actor_id,reason,legacy_admission,contract_hash)
  values (p_idempotency_key,v_rule.id,v_admission.enforcement_class,p_new_class,
          v_actor_id,btrim(p_reason),v_legacy_note,v_contract_hash)
  returning * into v_receipt;

  update ops.rule_admission set enforcement_class=p_new_class
   where rule_id=v_rule.id;

  return jsonb_build_object('ok',true,'replayed',false,'rule_id',v_rule.id,
    'enforcement_class_before',v_receipt.enforcement_class_before,
    'enforcement_class',p_new_class,
    'reclassification_receipt_id',v_receipt.id,
    'legacy_admission',v_receipt.legacy_admission);
end $function$;

CREATE OR REPLACE FUNCTION ops.record_executed_lease(p_deal text, p_base_version integer, p_executed_on date, p_commencement_on date, p_expiration_on date, p_term_months integer, p_evidence_kind text, p_evidence_ref text, p_source text)
 RETURNS TABLE(lease_id uuid, version integer, superseded_lease_id uuid, deal_id uuid, client_id uuid)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare
  v_actor_slug text;
  v_actor_id uuid;
  v_deal_ids uuid[];
  v_deal_id uuid;
  v_client_id uuid;
  v_owner_id uuid;
  v_current_id uuid;
  v_current_version integer;
  v_new_id uuid;
  v_new_version integer;
begin
  v_actor_slug:=ops.authority_actor_slug();
  select id into v_actor_id from public.actor where slug=v_actor_slug and active;
  if v_actor_id is null then raise exception 'lease authority actor is unavailable'; end if;
  if nullif(btrim(coalesce(p_deal,'')),'') is null then raise exception 'deal is required'; end if;
  if p_deal ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then
    select array_agg(id) into v_deal_ids from public.deal where id=p_deal::uuid;
  else
    select array_agg(id order by id) into v_deal_ids from public.deal where lower(name)=lower(btrim(p_deal));
  end if;
  if coalesce(array_length(v_deal_ids,1),0)=0 then raise exception 'lease deal was not found'; end if;
  if array_length(v_deal_ids,1)<>1 then raise exception 'lease deal needs exact disambiguation'; end if;
  v_deal_id:=v_deal_ids[1];

  perform pg_advisory_xact_lock(hashtextextended('executed-lease:'||v_deal_id::text,0));
  select d.client_id,
         coalesce((select dp.actor_id from public.deal_participant dp
                    where dp.deal_id=d.id and dp.role='lead' and dp.to_at is null limit 1),
                  c.created_by)
    into v_client_id,v_owner_id
    from public.deal d join public.client c on c.id=d.client_id
   where d.id=v_deal_id;
  if v_owner_id is distinct from v_actor_id then
    raise exception 'lease authority does not own the current deal';
  end if;
  if p_executed_on is null or p_expiration_on is null then
    raise exception 'executed_on and expiration_on are required';
  end if;
  if p_commencement_on is not null and p_expiration_on<=p_commencement_on then
    raise exception 'lease expiration must follow commencement';
  end if;
  if p_term_months is not null and (p_term_months<1 or p_term_months>480) then
    raise exception 'lease term_months is outside 1..480';
  end if;
  if p_evidence_kind not in ('executed_lease','lease_amendment','lease_abstract') then
    raise exception 'lease evidence kind is not admitted';
  end if;
  if nullif(btrim(coalesce(p_evidence_ref,'')),'') is null or length(p_evidence_ref)>1000 then
    raise exception 'lease evidence reference is required and bounded';
  end if;
  if nullif(btrim(coalesce(p_source,'')),'') is null or length(p_source)>500 then
    raise exception 'lease source is required and bounded';
  end if;

  select l.id,l.version into v_current_id,v_current_version
    from public.lease l where l.deal_id=v_deal_id and l.status='current' for update;
  if v_current_id is not null and p_base_version is distinct from v_current_version then
    raise exception 'lease version conflict: expected %',v_current_version;
  end if;
  if v_current_id is null and p_base_version is not null then
    raise exception 'no current lease exists for supplied base version';
  end if;
  if v_current_id is not null then
    update public.lease as held set status='superseded',superseded_at=now(),updated_by=v_actor_id
     where held.id=v_current_id and held.version=v_current_version and held.status='current';
    if not found then raise exception 'lease version conflict during replacement'; end if;
  end if;
  insert into public.lease
    (deal_id,client_id,owner_id,executed_on,commencement_on,expiration_on,term_months,
     evidence_kind,evidence_ref,source,created_by,updated_by,status,supersedes_lease_id)
  values
    (v_deal_id,v_client_id,v_actor_id,p_executed_on,p_commencement_on,p_expiration_on,p_term_months,
     p_evidence_kind,btrim(p_evidence_ref),btrim(p_source),v_actor_id,v_actor_id,'current',v_current_id)
  returning id,lease.version into v_new_id,v_new_version;
  lease_id:=v_new_id; version:=v_new_version; superseded_lease_id:=v_current_id;
  deal_id:=v_deal_id; client_id:=v_client_id;
  return next;
end $function$;

CREATE OR REPLACE FUNCTION ops.record_guidance_decision(p_revision_id uuid, p_state text, p_idempotency_key text, p_reason text)
 RETURNS uuid
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare
  authority_slug text;
  authority_actor uuid;
  item_id uuid;
  revision_hash text;
  receipt_id uuid;
  binding_id uuid;
  event_id uuid;
  receipt_kind text;
  receipt_decision text;
  existing record;
begin
  authority_slug := ops.authority_actor_slug();
  select id into authority_actor from public.actor
   where slug=authority_slug and kind='human';
  if authority_actor is null then
    raise exception 'guidance decision requires an admitted human authority actor';
  end if;
  if p_state not in ('active','retired','superseded') then
    raise exception 'unsupported guidance lifecycle decision %',p_state;
  end if;
  if coalesce(btrim(p_idempotency_key),'')='' or coalesce(btrim(p_reason),'')='' then
    raise exception 'guidance decision requires idempotency key and reason';
  end if;
  select guidance_item_id,ops.guidance_revision_contract_hash(id)
    into item_id,revision_hash
    from ops.guidance_revision where id=p_revision_id;
  if item_id is null or revision_hash is null then
    raise exception 'unknown guidance revision %',p_revision_id;
  end if;
  receipt_kind := case p_state
    when 'active' then 'activation'
    when 'retired' then 'rejection'
    else 'amendment' end;
  receipt_decision := case p_state
    when 'active' then 'approved'
    when 'retired' then 'retired'
    else 'superseded' end;

  select ar.id,ar.kind,ar.subject_type,ar.subject_id,ar.actor_id,ar.decision,
         ar.contract_hash,le.id as event_id
    into existing
    from ops.authority_receipt ar
    left join ops.guidance_authority_binding b on b.authority_receipt_id=ar.id
    left join ops.guidance_lifecycle_event le
      on le.authority_binding_id=b.id and le.state=p_state
   where ar.idempotency_key=p_idempotency_key;
  if existing.id is not null then
    if existing.kind<>receipt_kind or existing.subject_type<>'guidance'
       or existing.subject_id<>item_id or existing.actor_id<>authority_actor
       or existing.decision<>receipt_decision
       or existing.contract_hash is distinct from revision_hash
       or existing.event_id is null then
      raise exception 'idempotency key already names a different or incomplete guidance decision';
    end if;
    return existing.event_id;
  end if;

  insert into ops.authority_receipt
    (idempotency_key,kind,subject_type,subject_id,actor_id,decision,
     contract_hash,evidence_refs)
  values
    (p_idempotency_key,receipt_kind,'guidance',item_id,authority_actor,
     receipt_decision,revision_hash,array[p_revision_id::text])
  returning id into receipt_id;
  insert into ops.guidance_authority_binding
    (guidance_revision_id,authority_receipt_id,contract_hash)
  values (p_revision_id,receipt_id,revision_hash)
  returning id into binding_id;
  insert into ops.guidance_lifecycle_event
    (guidance_revision_id,state,authority_binding_id,reason)
  values (p_revision_id,p_state,binding_id,p_reason)
  returning id into event_id;
  return event_id;
end $function$;

CREATE OR REPLACE FUNCTION ops.record_workflow_acceptance(p_workflow_key text, p_mode text, p_status text, p_receipt_ref text, p_actor text)
 RETURNS uuid
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare v integer; rid uuid; human_actor public.actor%rowtype;
begin
  select version into v from ops.job_definition
   where key=p_workflow_key order by version desc limit 1;
  if v is null then raise exception 'unknown workflow %',p_workflow_key; end if;
  if p_status='accepted' then
    select * into human_actor from public.actor
     where slug=p_actor and kind='human' and active;
    if not found then
      raise exception 'accepted workflow evidence requires an active human actor';
    end if;
    if not exists (
      select 1
        from ops.job j join ops.job_receipt r on r.job_id=j.id
       where j.definition_key=p_workflow_key and j.definition_version=v
         and j.mode=p_mode and r.kind='completion' and r.receipt_ref=p_receipt_ref
    ) then
      raise exception 'accepted workflow evidence must name a completion receipt from the matching workflow and mode';
    end if;
  end if;
  insert into ops.workflow_acceptance
    (workflow_key,workflow_version,mode,status,receipt_ref,accepted_by)
  values(p_workflow_key,v,p_mode,p_status,p_receipt_ref,
         case when p_status='accepted' then human_actor.slug else null end)
  returning id into rid;
  return rid;
end $function$;

CREATE OR REPLACE FUNCTION ops.register_execution_environment_provider(p_manifest jsonb, p_idempotency_key uuid)
 RETURNS TABLE(provider_ref text, manifest_digest text, state text, replayed boolean)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare actor_row public.actor%rowtype; existing ops.execution_environment_provider%rowtype;
  row_out ops.execution_environment_provider%rowtype; digest_value text; v_actor_slug text; allowed text[] := array[
    'schema_version','provider_key','provider_version','display_name','source_class','backend_kind',
    'implementation_ref','implementation_digest','capability_refs','operation_refs','isolation_class',
    'egress_policy_ref','secret_policy_ref','persistence_mode','resource_policy_ref','cleanup_policy_ref',
    'threat_model_ref','conformance_contract_ref','conformance_contract_digest','configuration_schema_digest',
    'package_provenance','collision_policy','contains_secrets','manifest_digest'];
begin
  if session_user ~ '^carr_authority_' then
    v_actor_slug := regexp_replace(session_user,'^carr_authority_','');
  elsif ops.login_bundle_principal(session_user) = 'carr_writer' then
    v_actor_slug := nullif(btrim(current_setting('carr.acting_actor_slug', true)), '');
  else
    raise exception 'provider registration requires the authority connection or a sponsored writer session';
  end if;
  select * into actor_row from public.actor where slug=v_actor_slug and kind in ('human','automation') and active;
  if actor_row.id is null or jsonb_typeof(p_manifest)<>'object'
     or not (p_manifest ?& allowed)
     or exists(select 1 from jsonb_object_keys(p_manifest) k where k<>all(allowed))
     or p_manifest->>'schema_version'<>'execution-environment-provider.v1'
     or p_manifest->>'source_class'<>'plugin' or p_manifest->>'collision_policy'<>'digest_pinned'
     or p_manifest->>'contains_secrets'<>'false'
     or p_manifest->>'provider_key' !~ '^[a-z][a-z0-9]*(-[a-z0-9]+)*$'
     or p_manifest->>'provider_key'=any(array['hermes-local','hermes-docker','hermes-ssh','hermes-singularity','hermes-modal','hermes-daytona','hermes-vercel-sandbox'])
     or coalesce((p_manifest->>'provider_version')::integer,0)<1
     or p_manifest->>'backend_kind' not in ('none','local','container','remote','cloud')
     or p_manifest->>'isolation_class' not in ('none','host_process','container','microvm','remote_host')
     or jsonb_typeof(p_manifest->'capability_refs')<>'array' or jsonb_array_length(p_manifest->'capability_refs')=0
     or jsonb_typeof(p_manifest->'operation_refs')<>'array'
     or not (p_manifest->'operation_refs' ?& array['operation:create','operation:exec','operation:cancel','operation:destroy','operation:health'])
     or jsonb_typeof(p_manifest->'package_provenance')<>'object'
     or not (p_manifest->'package_provenance' ?& array['package_ref','package_digest','signature_ref','sbom_ref'])
     or exists(select 1 from jsonb_object_keys(p_manifest->'package_provenance') k where k<>all(array['package_ref','package_digest','signature_ref','sbom_ref']))
     or p_manifest->>'display_name' !~ '^.{1,80}$'
     or p_manifest->>'display_name' ~ '[[:cntrl:]]'
     or p_manifest->>'implementation_ref' !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'
     or p_manifest->>'implementation_digest' !~ '^sha256:[0-9a-f]{64}$'
     or p_manifest->>'conformance_contract_ref' !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'
     or p_manifest->>'conformance_contract_digest' !~ '^sha256:[0-9a-f]{64}$'
     or p_manifest->>'configuration_schema_digest' !~ '^sha256:[0-9a-f]{64}$'
     or p_manifest->'package_provenance'->>'package_ref' !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'
     or p_manifest->'package_provenance'->>'package_digest' !~ '^sha256:[0-9a-f]{64}$'
     or p_manifest->'package_provenance'->>'signature_ref' !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'
     or p_manifest->'package_provenance'->>'sbom_ref' !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$'
     or p_manifest->>'persistence_mode' not in ('none','command_scoped','session_scoped','durable_workspace')
     or exists(select 1 from unnest(array['egress_policy_ref','secret_policy_ref','resource_policy_ref','cleanup_policy_ref','threat_model_ref']) field where p_manifest->>field !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$')
     or exists(select 1 from jsonb_array_elements_text(p_manifest->'operation_refs') op where op !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$')
     or (select count(*) from jsonb_array_elements_text(p_manifest->'operation_refs'))<>(select count(distinct op) from jsonb_array_elements_text(p_manifest->'operation_refs') op)
     or (select count(*) from jsonb_array_elements_text(p_manifest->'capability_refs'))<>(select count(distinct cap) from jsonb_array_elements_text(p_manifest->'capability_refs') cap)
     or exists(select 1 from jsonb_array_elements_text(p_manifest->'capability_refs') c where c not in ('environment:none','environment:exec','environment:filesystem','environment:process','environment:network-governed','environment:snapshot','environment:transfer','environment:persistent-workspace')) then
    raise exception 'execution environment plugin manifest is not closed, safe, or complete';
  end if;
  digest_value := 'sha256:'||encode(public.digest(ops.guidance_import_canonical_json(p_manifest-'manifest_digest'),'sha256'),'hex');
  if p_manifest->>'manifest_digest' is distinct from digest_value then raise exception 'execution environment plugin manifest digest mismatch'; end if;
  select * into existing from ops.execution_environment_provider where idempotency_key=p_idempotency_key for share;
  if found then
    if existing.manifest is distinct from p_manifest then raise exception 'execution environment provider idempotency conflict'; end if;
    return query select 'environment-provider:'||existing.provider_key||':v'||existing.provider_version,existing.manifest_digest,ops.execution_environment_provider_current_state(existing.id),true; return;
  end if;
  if exists(select 1 from ops.execution_environment_provider p where p.provider_key=p_manifest->>'provider_key' and (p.protected_builtin or p.provider_version>=(p_manifest->>'provider_version')::integer)) then
    raise exception 'execution environment provider key/version is protected, stale, or already registered';
  end if;
  insert into ops.execution_environment_provider(provider_key,provider_version,source_class,backend_kind,manifest_digest,manifest,protected_builtin,created_by_actor_id,idempotency_key)
  values(p_manifest->>'provider_key',(p_manifest->>'provider_version')::integer,'plugin',p_manifest->>'backend_kind',digest_value,p_manifest,false,actor_row.id,p_idempotency_key) returning * into row_out;
  insert into ops.execution_environment_provider_event(provider_id,from_state,to_state,evidence_refs,ruled_by_actor_id,idempotency_key)
  values(row_out.id,null,'discovered',jsonb_build_array('evidence:human-provider-registration'),actor_row.id,p_idempotency_key);
  return query select 'environment-provider:'||row_out.provider_key||':v'||row_out.provider_version,row_out.manifest_digest,'discovered',false;
end $function$;

CREATE OR REPLACE FUNCTION ops.renewal_decision_candidate_digest(p_candidate candidate_pool)
 RETURNS text
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
  select encode(public.digest(convert_to((to_jsonb(p_candidate) - array['updated_at','updated_by'])::text,'UTF8'),'sha256'),'hex')
$function$;

CREATE OR REPLACE FUNCTION ops.replace_calendar_prebrief_allowlist(p_calendar_keys text[])
 RETURNS ops.calendar_prebrief_allowlist_receipt
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare
  v_sponsor text;
  v_keys text[];
  v_configuration_digest text;
  v_receipt ops.calendar_prebrief_allowlist_receipt%rowtype;
begin
  v_sponsor := ops.authority_actor_slug();
  select array_agg(k order by k) into v_keys from unnest(coalesce(p_calendar_keys,'{}'::text[])) keys(k);
  if coalesce(cardinality(v_keys),0)=0
     or array_position(v_keys,null) is not null
     or exists (select 1 from unnest(v_keys) keys(k) where k !~ '^[0-9a-f]{64}$')
     or cardinality(v_keys) <> (select count(distinct k) from unnest(v_keys) keys(k)) then
    raise exception using errcode='22023',message='calendar prebrief allowlist requires nonempty distinct opaque 64-hex calendar keys';
  end if;
  perform pg_advisory_xact_lock(hashtextextended('calendar-prebrief-allowlist:' || v_sponsor,0));
  v_configuration_digest:=encode(public.digest(convert_to(jsonb_build_object(
      'sponsor',v_sponsor,'calendar_keys',to_jsonb(v_keys))::text,'UTF8'),'sha256'),'hex');
  insert into ops.calendar_prebrief_allowlist_receipt
    (sponsor,calendar_keys,configuration_digest,configured_by)
  values(v_sponsor,v_keys,v_configuration_digest,v_sponsor)
  returning * into v_receipt;
  insert into ops.calendar_prebrief_allowed_calendar(sponsor,calendar_keys,configuration_digest,active_revision_id,configured_at,configured_by)
  values(v_sponsor,v_keys,v_configuration_digest,v_receipt.id,now(),v_sponsor)
  on conflict(sponsor) do update set calendar_keys=excluded.calendar_keys,
    configuration_digest=excluded.configuration_digest,active_revision_id=excluded.active_revision_id,
    configured_at=excluded.configured_at,configured_by=excluded.configured_by;
  return v_receipt;
end $function$;

CREATE OR REPLACE FUNCTION ops.require_rule_approval_lifecycle_anchor()
 RETURNS trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
begin
  -- An anchor is not an override and has no caller-supplied trust boundary.
  -- It can only certify the exact old-0194 pre-activation receipt against the
  -- current active rule, Joe authority, admitted contract and installed gates.
  if not exists (
    select 1
      from ops.rule_approval_receipt ar
      join public.rule r on r.id=ar.rule_id and r.status='active'
      join public.actor joe on joe.id=ar.actor_id
       and joe.slug='joe' and joe.kind='human' and joe.active
      join ops.rule_admission a on a.rule_id=r.id
     where ar.id=new.approval_receipt_id
       and ar.rule_id=new.rule_id
       and ar.rule_version+1=new.rule_version_after
       and new.rule_version_after=r.version
       and new.statement_hash=ar.statement_hash
       and ar.statement_hash=encode(public.digest(r.statement,'sha256'),'hex')
       and r.activated_by=ar.actor_id
       and r.enforcement=(case when ar.enforcement_status='hard_enforced'
                               then 'gate' else 'constraint' end)
       and a.state='admitted' and a.admitted_by=ar.actor_id
       and ar.policy_kind=a.enforcement_class
       and ar.enforcement_status=a.enforcement_status
       and ar.normalized_contract->>'binding_moment'=a.binding_moment
       and ar.normalized_contract->'applicability'=a.applicability
       and ar.normalized_contract->'projection'=a.projection
       and ar.normalized_contract->'reachability'=a.reachability
       and ar.normalized_contract->'input_contract'=a.input_contract
       and ar.evidence_refs=a.fixture_refs
       and exists (
         select 1 from ops.authority_receipt auth
          where auth.idempotency_key='approval:'||ar.idempotency_key
            and auth.kind='activation' and auth.subject_type='rule'
            and auth.subject_id=r.id and auth.actor_id=ar.actor_id
            and auth.contract_hash=ar.contract_hash)
       and not exists (
         select 1 from unnest(ar.requested_control_keys) requested(control_key)
          where not exists (
            select 1 from ops.rule_enforcement_point ep
            join ops.enforcement_control_catalog c using (control_key)
            join ops.rule_control_binding b
              on b.rule_id=ep.rule_id and b.control_key=ep.control_key
             where ep.rule_id=r.id and ep.control_key=requested.control_key
               and ep.installed and c.installed and c.verified_at is not null
               and b.statement_hash=ar.statement_hash
               and c.enforcement_class in ('deny_gate','stop_gate','schema','transactional_schema')))
  ) then
    raise exception 'legacy approval anchor requires an exact active Joe-approved 0194 receipt chain';
  end if;
  return new;
end $function$;

CREATE OR REPLACE FUNCTION ops.resolve_calendar_prebrief_email_ref(p_email text)
 RETURNS text
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare v_parties integer; v_live_parties integer; v_live integer; v_ref text;
begin
  perform ops.calendar_prebrief_resolver_sponsor();
  if p_email is null or length(p_email)>320
     or lower(btrim(p_email)) !~ '^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$' then
    raise exception using errcode='42501',message='calendar prebrief email resolver requires one bounded exact email';
  end if;
  select count(*),count(*) filter (where p.deleted_at is null and p.merged_into is null)
    into v_parties,v_live_parties
    from public.party p
   where lower(btrim(p.email))=lower(btrim(p_email));
  if v_parties=0 then
    return null;
  end if;
  -- Only refs under a LIVE party count, so the one ref returned is always the
  -- one live party's own (a tombstone's live role row can never answer).
  select count(distinct r.ref),min(r.ref) into v_live,v_ref
    from public.party p join public.v_ref_index r on r.party_id=p.id and not r.merged
   where lower(btrim(p.email))=lower(btrim(p_email))
     and p.deleted_at is null and p.merged_into is null;
  if v_live_parties<>1 or v_live<>1 then
    raise exception using errcode='22023',message='calendar prebrief email resolver requires exactly one live unmerged canonical ref';
  end if;
  return v_ref;
end $function$;

CREATE OR REPLACE FUNCTION ops.retire_rule(p_rule_id uuid, p_reason text, p_superseded_by uuid, p_idempotency_key text)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare
  v_actor_slug text;
  v_actor_id uuid;
  v_rule public.rule%rowtype;
  v_prior ops.rule_retirement_receipt%rowtype;
  v_receipt ops.rule_retirement_receipt%rowtype;
  v_approval_id uuid;
  v_legacy_note text;
  v_contract jsonb;
  v_contract_hash text;
  v_retired_at timestamptz;
begin
  v_actor_slug := ops.authority_actor_slug();
  if v_actor_slug<>'joe' then
    raise exception 'system rule retirement requires Joe authority';
  end if;
  select id into v_actor_id from public.actor
   where slug=v_actor_slug and kind='human' and active;
  if v_actor_id is null then raise exception 'Joe authority actor is not active'; end if;
  if btrim(coalesce(p_reason,''))='' or btrim(coalesce(p_idempotency_key,''))='' then
    raise exception 'retirement reason and idempotency key are required';
  end if;
  if p_superseded_by is not null and p_superseded_by=p_rule_id then
    raise exception 'a rule cannot supersede itself';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('rule-retirement:'||p_idempotency_key,0));
  select * into v_prior from ops.rule_retirement_receipt
   where idempotency_key=p_idempotency_key;
  if found then
    if v_prior.rule_id is distinct from p_rule_id
       or v_prior.reason is distinct from btrim(p_reason)
       or v_prior.superseded_by is distinct from p_superseded_by
       or v_prior.actor_id is distinct from v_actor_id then
      raise exception 'rule retirement idempotency key was reused with different input';
    end if;
    select * into v_rule from public.rule where id=p_rule_id for update;
    if not found
       or v_rule.status is distinct from 'retired'
       or v_rule.version is distinct from v_prior.rule_version_after
       or encode(public.digest(v_rule.statement,'sha256'),'hex') is distinct from v_prior.statement_hash
       or v_rule.retired_by is distinct from v_prior.actor_id
       or v_rule.retired_at is distinct from v_prior.retired_at then
      raise exception 'rule retirement replay refused: current retired rule no longer matches the immutable retirement';
    end if;
    return jsonb_build_object('ok',true,'replayed',true,'rule_id',p_rule_id,
      'previous_status',v_prior.previous_status,'status','retired',
      'retirement_receipt_id',v_prior.id,'legacy_admission',v_prior.legacy_admission);
  end if;

  select * into v_rule from public.rule where id=p_rule_id for update;
  if not found then raise exception 'rule % not found',p_rule_id; end if;
  if v_rule.status not in ('proposed','active') then
    raise exception 'rule % is %, expected proposed or active',p_rule_id,v_rule.status;
  end if;
  if p_superseded_by is not null and not exists (select 1 from public.rule where id=p_superseded_by) then
    raise exception 'superseding rule % does not exist',p_superseded_by;
  end if;
  if v_rule.status='active' then
    select id into v_approval_id from ops.rule_approval_receipt
     where rule_id=v_rule.id
       and (rule_version=v_rule.version or exists (
         select 1 from ops.rule_approval_lifecycle_anchor legacy
          where legacy.approval_receipt_id=ops.rule_approval_receipt.id
            and legacy.rule_id=v_rule.id and legacy.rule_version_after=v_rule.version
            and legacy.statement_hash=ops.rule_approval_receipt.statement_hash))
       and statement_hash=encode(public.digest(v_rule.statement,'sha256'),'hex')
     order by created_at desc limit 1;
    if v_approval_id is null then
      -- (0351) Not every receiptless active rule is a defect: 217 of 219
      -- were activated before the receipt system existed at all. Fall
      -- through to the shared legacy predicate before refusing outright.
      v_legacy_note := ops.legacy_rule_admission_note(v_rule.id,v_rule.status,v_rule.activated_at);
      if v_legacy_note is null then
        raise exception 'active rule % lacks its exact approval receipt',v_rule.id;
      end if;
    end if;
  end if;

  v_retired_at := now();

  v_contract := jsonb_build_object(
    'rule_id',v_rule.id,'rule_version_before',v_rule.version,
    'rule_version_after',v_rule.version+1,
    'statement_hash',encode(public.digest(v_rule.statement,'sha256'),'hex'),
    'previous_status',v_rule.status,'actor_id',v_actor_id,
    'reason',btrim(p_reason),'superseded_by',p_superseded_by,
    'approval_receipt_id',v_approval_id,'legacy_admission',v_legacy_note,'retired_at',v_retired_at);
  v_contract_hash := encode(public.digest(v_contract::text,'sha256'),'hex');
  insert into ops.rule_retirement_receipt
    (idempotency_key,rule_id,rule_version_before,rule_version_after,statement_hash,previous_status,
     actor_id,reason,superseded_by,approval_receipt_id,legacy_admission,contract_hash,retired_at)
  values (p_idempotency_key,v_rule.id,v_rule.version,v_rule.version+1,
          encode(public.digest(v_rule.statement,'sha256'),'hex'),v_rule.status,
          v_actor_id,btrim(p_reason),p_superseded_by,v_approval_id,v_legacy_note,v_contract_hash,v_retired_at)
  returning * into v_receipt;
  insert into ops.authority_receipt
    (idempotency_key,kind,subject_type,subject_id,actor_id,decision,contract_hash,evidence_refs)
  values ('retirement:'||p_idempotency_key,'override','rule',v_rule.id,v_actor_id,
          'retired by Joe authority: '||btrim(p_reason),v_contract_hash,
          case when v_approval_id is null then '{}'::text[]
               else array[v_approval_id::text] end);
  update public.rule set status='retired',retired_by=v_actor_id,retired_at=v_retired_at
   where id=v_rule.id and status=v_rule.status;
  if not found then raise exception 'rule % retirement raced',v_rule.id; end if;
  return jsonb_build_object('ok',true,'replayed',false,'rule_id',v_rule.id,
    'previous_status',v_rule.status,'status','retired',
    'retirement_receipt_id',v_receipt.id,'legacy_admission',v_receipt.legacy_admission);
end $function$;

CREATE OR REPLACE FUNCTION ops.scac_issue_pop_challenge(p_principal_digest text, p_device_ref text, p_workload_digest text, p_ingress_key text, p_operation_manifest_digest text, p_idempotency_digest text, p_ttl_seconds integer, p_issue_idempotency_key uuid)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'public', 'ops', 'pg_temp'
AS $function$
declare e ops.scac_device_enrollment%rowtype; pe ops.scac_policy_epoch%rowtype;
        re ops.scac_mutation_registry_entry%rowtype; prior ops.scac_pop_challenge%rowtype;
        control jsonb; fingerprint text; challenge_uuid uuid; nonce bytea;
        v_issued timestamptz; v_expires timestamptz; issued_text text; expires_text text;
        nonce_text text; challenge_hash text;
begin
  if session_user<>'carr_jobs' then raise exception 'SIEP-17 challenge issuer role refused'; end if;
  if coalesce(p_principal_digest,'')!~'^sha256:[0-9a-f]{64}$'
     or coalesce(p_operation_manifest_digest,'')!~'^sha256:[0-9a-f]{64}$'
     or coalesce(p_idempotency_digest,'')!~'^sha256:[0-9a-f]{64}$'
     or (p_workload_digest is not null and p_workload_digest!~'^sha256:[0-9a-f]{64}$')
     or coalesce(p_device_ref,'')!~'^[a-z0-9][a-z0-9._-]{2,127}$'
     or coalesce(p_ingress_key,'')!~'^[a-z][a-z0-9_-]+:' or p_ingress_key~E'[\n\r\t]'
     or char_length(p_ingress_key)>1000 or p_ttl_seconds not between 1 and 300
     or p_issue_idempotency_key is null then raise exception 'SIEP-17 challenge input malformed'; end if;
  fingerprint:=ops.scac_token_sha256_text(ops.scac_canonical_json(jsonb_build_object(
    'schema_version','scac-pop-challenge-request.v1','principal_digest',p_principal_digest,
    'device_ref',p_device_ref,'workload_digest',p_workload_digest,'ingress_key',p_ingress_key,
    'operation_manifest_digest',p_operation_manifest_digest,
    'idempotency_digest',p_idempotency_digest,'ttl_seconds',p_ttl_seconds)));
  perform pg_advisory_xact_lock(hashtextextended('carr-siep17-token-control',0));
  control:=ops.scac_token_control_snapshot();
  if control->>'kill_switch_state'<>'inactive' then
    raise exception 'scac.refusal.kill_switch: SIEP-17 challenge issuance unavailable';
  end if;
  select * into prior from ops.scac_pop_challenge where issue_idempotency_key=p_issue_idempotency_key;
  if prior.challenge_id is not null then
    if prior.request_fingerprint is distinct from fingerprint then
      raise exception 'SIEP-17 challenge idempotency binding mismatch';
    end if;
  end if;
  select * into e from ops.scac_device_enrollment where device_ref=p_device_ref for key share;
  if e.device_ref is null or e.lifecycle_state<>'registered_pending_siep16_pop' or
     e.routing_eligible or e.privileges_active or e.production_enforcement_active then
    raise exception 'scac.refusal.revoked: SIEP-17 enrolled device unavailable';
  end if;
  select * into pe from ops.scac_policy_epoch order by epoch desc limit 1 for key share;
  if pe.epoch is null or pe.epoch<>e.policy_epoch or pe.epoch_digest<>e.policy_epoch_digest then
    raise exception 'scac.refusal.token_invalid: SIEP-17 current policy epoch mismatch';
  end if;
  select * into re from ops.scac_mutation_registry_entry
    where registry_version=pe.registry_version and ingress_key=p_ingress_key for key share;
  if re.ingress_key is null or re.effect_class='read_only' or
     coalesce((re.contract->>'classification_authorizing')::boolean,true) then
    raise exception 'scac.refusal.token_invalid: SIEP-17 registered mutation ingress unavailable';
  end if;
  if exists(select 1 from ops.scac_token_revocation_event r where
       (r.subject_kind='device' and r.subject_digest=ops.scac_token_sha256_text(e.device_ref)) or
       (r.subject_kind='device_key' and r.subject_digest=e.device_key_digest) or
       (r.subject_kind='facts' and r.subject_digest=e.facts_digest) or
       (p_workload_digest is not null and r.subject_kind='workload' and r.subject_digest=p_workload_digest)) then
    raise exception 'scac.refusal.revoked: SIEP-17 challenge subject revoked';
  end if;
  if prior.challenge_id is not null then
    if prior.device_key_digest is distinct from e.device_key_digest
       or prior.facts_digest is distinct from e.facts_digest
       or prior.policy_epoch is distinct from pe.epoch
       or prior.policy_epoch_digest is distinct from pe.epoch_digest
       or prior.registry_version is distinct from pe.registry_version
       or prior.registry_digest is distinct from pe.registry_digest
       or prior.expires_at<=clock_timestamp()
       or exists(select 1 from ops.scac_pop_challenge_consumption x where x.challenge_id=prior.challenge_id)
       or exists(select 1 from ops.scac_token_revocation_event r
            where r.subject_kind='challenge' and r.subject_digest=prior.challenge_digest) then
      raise exception 'scac.refusal.token_invalid: SIEP-17 prior challenge is no longer issuable';
    end if;
    return jsonb_build_object('schema_version',prior.schema_version,'challenge_id',prior.challenge_id::text,
      'device_ref',prior.device_ref,'device_key_digest',prior.device_key_digest,
      'facts_digest',prior.facts_digest,'policy_epoch',prior.policy_epoch,
      'policy_epoch_digest',prior.policy_epoch_digest,
      'operation_manifest_digest',prior.operation_manifest_digest,'nonce',encode(prior.nonce_bytes,'base64'),
      'issued_at',to_char(prior.issued_at at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
      'expires_at',to_char(prior.expires_at at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'));
  end if;
  challenge_uuid:=gen_random_uuid(); nonce:=public.gen_random_bytes(32); v_issued:=clock_timestamp();
  v_expires:=v_issued+make_interval(secs=>p_ttl_seconds);
  issued_text:=to_char(v_issued at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"');
  expires_text:=to_char(v_expires at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"');
  nonce_text:=encode(nonce,'base64');
  challenge_hash:=ops.scac_pop_challenge_digest(challenge_uuid,e.device_ref,e.device_key_digest,
    e.facts_digest,pe.epoch,pe.epoch_digest,p_operation_manifest_digest,nonce_text,issued_text,expires_text);
  insert into ops.scac_pop_challenge
    (challenge_id,schema_version,tenant_scope,environment,principal_digest,device_ref,
     device_key_digest,facts_digest,workload_digest,policy_epoch,policy_epoch_digest,
     registry_version,registry_digest,ingress_key,mutation_kind,target_surface,
     operation_manifest_digest,request_digest,idempotency_digest,nonce_bytes,nonce_digest,
     issued_at,expires_at,challenge_digest,request_fingerprint,issue_idempotency_key)
  values (challenge_uuid,'scac-pop-challenge.v1','carr-internal','source-test',p_principal_digest,
    e.device_ref,e.device_key_digest,e.facts_digest,p_workload_digest,pe.epoch,pe.epoch_digest,
    pe.registry_version,pe.registry_digest,re.ingress_key,re.contract->>'mutation_kind',
    re.contract->>'target_surface',p_operation_manifest_digest,p_operation_manifest_digest,
    p_idempotency_digest,nonce,'sha256:'||encode(public.digest(nonce,'sha256'),'hex'),v_issued,v_expires,
    challenge_hash,fingerprint,p_issue_idempotency_key);
  return jsonb_build_object('schema_version','scac-pop-challenge.v1',
    'challenge_id',challenge_uuid::text,'device_ref',e.device_ref,
    'device_key_digest',e.device_key_digest,'facts_digest',e.facts_digest,
    'policy_epoch',pe.epoch,'policy_epoch_digest',pe.epoch_digest,
    'operation_manifest_digest',p_operation_manifest_digest,'nonce',nonce_text,
    'issued_at',issued_text,'expires_at',expires_text);
end $function$;

CREATE OR REPLACE FUNCTION ops.seal_renewal_decision_source_run(p_job_id uuid, p_lease uuid)
 RETURNS ops.renewal_decision_source_run
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare
  v_job ops.job%rowtype;
  v_run ops.renewal_decision_source_run%rowtype;
  v_count integer;
begin
  if not pg_has_role(session_user,'carr_jobs','member') then
    raise exception using errcode='42501',message='renewal source-run sealing requires the jobs capability';
  end if;
  select * into v_job from ops.job where id=p_job_id for update;
  if not found or v_job.definition_key<>'renewal-radar-source-daily' or v_job.definition_version<>1
     or v_job.mode<>'live' or v_job.state<>'running' or v_job.lease_token is distinct from p_lease
     or v_job.leased_until is null or v_job.leased_until<now() then
    raise exception using errcode='55000',message='renewal source-run sealing requires its current static job lease';
  end if;
  if v_job.scheduled_for < now()-interval '36 hours' or v_job.scheduled_for > now()+interval '5 minutes' then
    raise exception using errcode='22023',message='renewal source-run sealing refuses a job outside its DB-clock window';
  end if;
  perform pg_advisory_xact_lock(hashtextextended('renewal-decision-source-run',0));
  select * into v_run from ops.renewal_decision_source_run where job_id=v_job.id and attempt=v_job.attempt;
  if found then
    if v_run.member_count <> (select count(*) from public.candidate_pool where source='renewal-radar' and status='pool')
       or exists (
          (select candidate_id,row_digest from ops.renewal_decision_source_run_member where source_run_id=v_run.id)
          except
          (select cp.id,ops.renewal_decision_candidate_digest(cp) from public.candidate_pool cp where cp.source='renewal-radar' and cp.status='pool')
       ) or exists (
          (select cp.id,ops.renewal_decision_candidate_digest(cp) from public.candidate_pool cp where cp.source='renewal-radar' and cp.status='pool')
          except
          (select candidate_id,row_digest from ops.renewal_decision_source_run_member where source_run_id=v_run.id)
       ) then
      raise exception using errcode='23505',message='renewal source-run replay conflicts with immutable source membership';
    end if;
    return v_run;
  end if;
  select count(*) into v_count from public.candidate_pool where source='renewal-radar' and status='pool';
  insert into ops.renewal_decision_source_run(job_id,attempt,snapshot_at,member_count)
  values(v_job.id,v_job.attempt,v_job.scheduled_for,v_count) returning * into v_run;
  insert into ops.renewal_decision_source_run_member(source_run_id,candidate_id,row_digest)
    select v_run.id,cp.id,ops.renewal_decision_candidate_digest(cp)
      from public.candidate_pool cp where cp.source='renewal-radar' and cp.status='pool';
  return v_run;
end $function$;

CREATE OR REPLACE FUNCTION ops.seal_renewal_decision_source_run(p_job_id uuid, p_lease uuid, p_snapshot_id uuid)
 RETURNS ops.renewal_decision_source_run
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare v_job ops.job%rowtype; v_snapshot ops.renewal_source_snapshot%rowtype; v_run ops.renewal_decision_source_run%rowtype; v_count integer;
begin
 if session_user<>'carr_renewal_source_attestor' or not pg_has_role(session_user,'carr_renewal_source_attestors','member') then raise exception using errcode='42501',message='renewal source-run sealing requires the exact renewal source attestor capability'; end if;
 select * into v_job from ops.job where id=p_job_id for update;
 if not found or v_job.definition_key<>'renewal-radar-source-daily' or v_job.definition_version<>1 or v_job.mode<>'live' or v_job.state<>'running' or v_job.lease_token is distinct from p_lease or v_job.leased_until is null or v_job.leased_until<now() then raise exception using errcode='55000',message='renewal source-run sealing requires its current static job lease'; end if;
 if v_job.scheduled_for < now()-interval '36 hours' or v_job.scheduled_for > now()+interval '5 minutes' then raise exception using errcode='22023',message='renewal source-run sealing refuses a job outside its DB-clock window'; end if;
 select * into v_snapshot from ops.renewal_source_snapshot where id=p_snapshot_id and job_id=v_job.id and attempt=v_job.attempt for update;
 if not found then raise exception using errcode='22023',message='renewal source-run sealing requires the exact leased signed source snapshot'; end if;
 if v_snapshot.row_count<>(select count(*) from ops.renewal_source_snapshot_member where snapshot_id=v_snapshot.id)
    or exists(select 1 from ops.renewal_source_snapshot_member sm left join public.candidate_pool cp on cp.id=sm.candidate_id where sm.snapshot_id=v_snapshot.id and (cp.id is null or cp.source<>'renewal-radar' or cp.status<>'pool')) then
   raise exception using errcode='23505',message='renewal source snapshot members are not a current mutable projection'; end if;
 perform pg_advisory_xact_lock(hashtextextended('renewal-decision-source-run',0));
 select * into v_run from ops.renewal_decision_source_run where job_id=v_job.id and attempt=v_job.attempt;
 if found then
   if v_run.source_snapshot_id is distinct from v_snapshot.id or v_run.member_count<>v_snapshot.row_count
      or exists((select sm.candidate_id,ops.renewal_decision_candidate_digest(cp) from ops.renewal_source_snapshot_member sm join public.candidate_pool cp on cp.id=sm.candidate_id where sm.snapshot_id=v_snapshot.id)
                except (select candidate_id,row_digest from ops.renewal_decision_source_run_member where source_run_id=v_run.id))
      or exists((select candidate_id,row_digest from ops.renewal_decision_source_run_member where source_run_id=v_run.id)
                except (select sm.candidate_id,ops.renewal_decision_candidate_digest(cp) from ops.renewal_source_snapshot_member sm join public.candidate_pool cp on cp.id=sm.candidate_id where sm.snapshot_id=v_snapshot.id)) then
     raise exception using errcode='23505',message='renewal source-run replay conflicts with immutable signed source membership';
   end if;
   return v_run;
 end if;
 select count(*) into v_count from ops.renewal_source_snapshot_member where snapshot_id=v_snapshot.id;
 insert into ops.renewal_decision_source_run(job_id,attempt,snapshot_at,member_count,source_snapshot_id) values(v_job.id,v_job.attempt,v_job.scheduled_for,v_count,v_snapshot.id) returning * into v_run;
 insert into ops.renewal_decision_source_run_member(source_run_id,candidate_id,row_digest)
 select v_run.id,sm.candidate_id,ops.renewal_decision_candidate_digest(cp) from ops.renewal_source_snapshot_member sm join public.candidate_pool cp on cp.id=sm.candidate_id where sm.snapshot_id=v_snapshot.id;
 return v_run;
end $function$;

CREATE OR REPLACE FUNCTION ops.stage_guidance_import_batch(p_manifest_digest text, p_canonical_manifest_text text, p_classifier_actor_id uuid, p_idempotency_key text, p_reason text)
 RETURNS uuid
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare
  v_batch_id uuid;
  v_source_digest text;
  v_manifest jsonb;
  v_existing record;
  v_entry jsonb;
  v_ordinal integer := 0;
begin
  if p_manifest_digest !~ '^[0-9a-f]{64}$'
     or coalesce(btrim(p_idempotency_key),'')=''
     or coalesce(btrim(p_reason),'')='' then
    raise exception 'guidance import staging requires digest, idempotency key and reason';
  end if;
  if coalesce(p_canonical_manifest_text,'')='' or right(p_canonical_manifest_text,1) <> E'\n'
     or position(E'\r' in p_canonical_manifest_text) <> 0 then
    raise exception 'guidance import manifest must be the declared newline-terminated UTF-8 canonical artifact';
  end if;
  begin
    v_manifest := p_canonical_manifest_text::jsonb;
  exception when others then
    raise exception 'guidance import manifest is not valid JSON';
  end;
  perform ops.validate_guidance_import_manifest(v_manifest);
  if coalesce(v_manifest->>'canonicalization','') <> 'utf8-json-sort-keys-compact-newline/v1' then
    raise exception 'guidance import manifest has an unsupported canonicalization identifier';
  end if;
  if current_setting('server_encoding') <> 'UTF8'
     or p_canonical_manifest_text <> ops.guidance_import_canonical_json(v_manifest) || E'\n' then
    raise exception 'guidance import manifest bytes do not match utf8-json-sort-keys-compact-newline/v1';
  end if;
  if p_manifest_digest is distinct from ops.guidance_import_manifest_digest(p_canonical_manifest_text) then
    raise exception 'guidance import digest does not match the canonical manifest';
  end if;
  select id,manifest_digest,canonical_manifest_text,manifest_json,source_manifest_digest,classifier_actor_id,reason
    into v_existing from ops.guidance_import_batch where staging_key=p_idempotency_key;
  if v_existing.id is not null then
    if v_existing.manifest_digest<>p_manifest_digest
       or v_existing.canonical_manifest_text is distinct from p_canonical_manifest_text
       or v_existing.manifest_json is distinct from v_manifest
       or v_existing.classifier_actor_id<>p_classifier_actor_id
       or v_existing.reason<>p_reason then
      raise exception 'idempotency key already names a different guidance import stage';
    end if;
    return v_existing.id;
  end if;
  if exists (select 1 from ops.guidance_import_batch where manifest_digest=p_manifest_digest) then
    raise exception 'guidance import digest is already staged under another idempotency key';
  end if;
  if not exists (select 1 from public.actor where id=p_classifier_actor_id
                 and kind in ('automation','system') and active) then
    raise exception 'guidance import staging requires an active non-human classifier actor';
  end if;
  v_source_digest := v_manifest->'source_manifest'->>'sha256';
  insert into ops.guidance_import_batch
    (manifest_digest,canonical_manifest_text,manifest_json,source_manifest_digest,classifier_actor_id,staging_key,reason)
  values (p_manifest_digest,p_canonical_manifest_text,v_manifest,v_source_digest,p_classifier_actor_id,p_idempotency_key,p_reason)
  returning id into v_batch_id;
  for v_entry in select value from jsonb_array_elements(v_manifest->'entries') loop
    v_ordinal := v_ordinal + 1;
    insert into ops.guidance_import_entry
      (batch_id,ordinal,guidance_id,source_rule_id,source_clause,is_primary,split_group_key,
       guidance_type,scope,activation,consumer,verification,provenance,delivery,
       is_constitution,revision_reason,situation_mappings)
    values
      (v_batch_id,v_ordinal,v_entry->>'guidance_id',(v_entry->>'source_rule_id')::uuid,
       v_entry->>'source_clause',(v_entry->>'is_primary')::boolean,
       nullif(v_entry->>'split_group_key',''),v_entry->>'guidance_type',v_entry->'scope',
       v_entry->'activation',v_entry->>'consumer',v_entry->'verification',v_entry->'provenance',
       v_entry->'delivery',(v_entry->>'is_constitution')::boolean,v_entry->>'reason',
       coalesce(v_entry->'activation'->'situation_mappings','[]'::jsonb));
  end loop;
  perform ops.assert_guidance_import_inventory(v_batch_id);
  return v_batch_id;
end $function$;

CREATE OR REPLACE FUNCTION ops.sync_system_rule_control_bindings()
 RETURNS integer
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare
  v_expected record;
  v_rule public.rule%rowtype;
  v_rows integer;
  v_inserted integer := 0;
begin
  for v_expected in
    select * from (values
      ('ae44e0c0-e773-456c-a85b-2dc4cf4dd49e'::uuid,
       '9e02f7eee01220fd604ba97d605830ea903d3266f95b626a5ca5d9a73567c8f9',
       '{}'::jsonb,
       '4a0e59ce-728a-49b5-a055-116156e9470e'::uuid,
       '1fe7c57e-c23f-4fb0-9cff-36f6d3cfcf08'::uuid,
       'Joe is the sole required authority for system development and high-level system decisions',
       $q$One thing I need to make sure of, I do not want this system to become dependent on dell’s approval for changes. He is not involved in system development at all. He is basically just a user of the system who may train a new work flow here and there but he will not be involved in building the system or making high level decisions about the way the system functions. He’s relying on me for that. Don’t block him from any of those decisions but don’t require his approval either$q$,
       'human_authority_runtime',
       'Joe-approved sole system authority'),
      ('a57d981a-8f6d-4c18-95ee-0e63a5a90b89'::uuid,
       'c6fd62eb91d3f03b21a6098a6fd6b2848b902a45b8c0430b1717edf4e143f668',
       $scope${"domain":"system","applies_to":["github","neon","cloudflare","anthropic","openai","google","healthchecks","blotato","make"]}$scope$::jsonb,
       '8b31938a-e2f2-4b8f-9c29-187efa5c1650'::uuid,
       'f7ea060c-268b-47f1-8a17-7168841b77e0'::uuid,
       'Make cost discipline permanent; expire only the temporary emergency restriction',
       $q$But also, we want a budget rule in affect going forward not just expiring in September. We need to operate the system with cost in mind. Not to the point where it limits the system but just to the point where excessive spending is avoided$q$,
       'platform_metering_pre_dispatch',
       'Joe-approved permanent platform cost policy')
    ) as expected(rule_id,statement_hash,rule_scope,decision_id,decision_event_id,
                  decision_title,human_quote,control_key,source)
  loop
    select * into v_rule from public.rule where id=v_expected.rule_id;
    if not found then continue; end if;
    if v_rule.id='a57d981a-8f6d-4c18-95ee-0e63a5a90b89'::uuid
       and exists (select 1 from public.event
                     where id='34f34e23-225b-4d0f-946f-478b59fbce63'::uuid) then
      -- The legacy cost restriction was truthfully retired before 0228 reached
      -- Production.  This is an exact one-row tombstone, not a general escape
      -- hatch for retired rules; any drift remains a hard refusal.
      if not exists (
        select 1
          from public.rule r
          join public.actor taught_by on taught_by.id=r.taught_by
          join public.event e on e.id='34f34e23-225b-4d0f-946f-478b59fbce63'::uuid
          join public.actor event_actor on event_actor.id=e.actor_id
         where r.id='a57d981a-8f6d-4c18-95ee-0e63a5a90b89'::uuid
           and r.status='retired' and r.version=2
           and encode(public.digest(r.statement,'sha256'),'hex')='c6fd62eb91d3f03b21a6098a6fd6b2848b902a45b8c0430b1717edf4e143f668'
           and r.human_quote=$q$Also, how does this budgeting plan become impossible to overlook? If it’s just prose the system won’t remember it$q$
           and r.scope='{"domain":"system","applies_to":["github","neon","cloudflare","anthropic","openai","google","healthchecks","blotato","make"]}'::jsonb
           and r.personal_to is null and r.enforcement='prose'
           and r.activated_by is null and r.activated_at is null
           and r.supersedes is null
           and r.created_at='2026-08-17T08:29:06.905178Z'::timestamptz
           and r.updated_at='2026-08-21T21:38:29.049309Z'::timestamptz
           and taught_by.id='b6c38b27-d006-4fad-9c38-49edf3130a07'::uuid
           and taught_by.slug='joe' and taught_by.kind='human' and taught_by.active
           and not exists (select 1 from public.rule successor where successor.supersedes=r.id)
           and e.actor_id='b6c38b27-d006-4fad-9c38-49edf3130a07'::uuid
           and event_actor.slug='joe' and event_actor.kind='human' and event_actor.active
           and e.verb='retire-rule' and e.subject_type='rule' and e.subject_id=r.id
           and e.field='status'
           and e.old_value='{"status":"proposed"}'::jsonb
           and e.new_value='{"status":"retired"}'::jsonb
           and e.cause='automation_job'
           and e.human_quote is null
           and e.occurred_at='2026-08-21T21:38:29.049309Z'::timestamptz
           and e.recorded_at='2026-08-21T21:38:29.049309Z'::timestamptz
           and e.via='oauth-google'
           and e.client_id='https://claude.ai/oauth/mcp-oauth-client-metadata'
           and e.sponsoring_human_slug='joe'
           and e.personal_scope='joe-personal'
           and e.authorization_class='verified_partner'
           and e.organization_tenant_id='carr-internal'
           and e.correlation_id='6923f7f8-4ae6-4db0-93ab-9424d5aea0f1'::uuid
           and e.idempotency_key='c4ad90f7-d8dd-4bf3-8785-659bae3d3f27'
           and encode(public.digest(coalesce(e.agent_rationale,''),'sha256'),'hex')='82cf84d571cbe49eb61bf9570e2c8f86a114fa216e9ab1b3799181045c881137'
      ) then
        raise exception 'legacy retired system cost rule does not match its exact retirement tombstone preimage';
      end if;
      continue;
    end if;
    if v_rule.status not in ('proposed','active') then
      raise exception 'system rule % is %, expected proposed or active',v_rule.id,v_rule.status;
    end if;
    if v_rule.personal_to is not null
       or v_rule.scope is distinct from v_expected.rule_scope then
      raise exception 'system rule % does not match its exact approved shared scope',v_rule.id;
    end if;
    if encode(public.digest(v_rule.statement,'sha256'),'hex') is distinct from v_expected.statement_hash then
      raise exception 'system rule % statement does not match Joe-approved preimage',v_rule.id;
    end if;
    if not exists (
      select 1 from public.v_decision_entry d
       where d.decision_id=v_expected.decision_id
         and d.event_id=v_expected.decision_event_id
         and d.title=v_expected.decision_title
         and d.human_quote=v_expected.human_quote
    ) then
      raise exception 'system rule % lacks its exact Joe decision evidence',v_rule.id;
    end if;
    if not exists (
      select 1 from ops.enforcement_control_catalog c
       where c.control_key=v_expected.control_key
         and c.installed and c.verified_at is not null
    ) then
      raise exception 'system rule % control % is not installed',v_rule.id,v_expected.control_key;
    end if;

    if not exists (
      select 1 from ops.rule_control_binding
       where rule_id=v_rule.id and control_key=v_expected.control_key
    ) then
      insert into ops.rule_control_binding
        (rule_id,control_key,statement_hash,binding_contract)
      select v_rule.id,v_expected.control_key,v_expected.statement_hash,
             jsonb_build_object(
               'source',v_expected.source,
               'durable_decision_ref',v_expected.decision_id,
               'decision_event_ref',v_expected.decision_event_id,
               'rule_id',v_rule.id,
               'rule_version',v_rule.version,
               'implementation_ref',c.implementation_ref,
               'test_ref',c.test_ref)
        from ops.enforcement_control_catalog c
       where c.control_key=v_expected.control_key;
      get diagnostics v_rows = row_count;
    else
      v_rows := 0;
    end if;
    v_inserted := v_inserted + v_rows;

    if not exists (
      select 1 from ops.rule_control_binding b
       where b.rule_id=v_rule.id and b.control_key=v_expected.control_key
         and b.statement_hash=v_expected.statement_hash
         and b.binding_contract->>'durable_decision_ref'=v_expected.decision_id::text
         and b.binding_contract->>'decision_event_ref'=v_expected.decision_event_id::text
    ) then
      raise exception 'system rule % has a stale or conflicting control binding',v_rule.id;
    end if;
  end loop;
  return v_inserted;
end $function$;

CREATE OR REPLACE FUNCTION ops.transition_execution_environment_provider(p_provider_ref text, p_expected_state text, p_target_state text, p_evidence_refs jsonb, p_idempotency_key uuid)
 RETURNS TABLE(provider_ref text, state text, replayed boolean)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare actor_row public.actor%rowtype; provider ops.execution_environment_provider%rowtype; current_state text; existing ops.execution_environment_provider_event%rowtype;
begin
  if session_user !~ '^carr_authority_' then raise exception 'environment provider transition requires human authority'; end if;
  select * into actor_row from public.actor where slug=regexp_replace(session_user,'^carr_authority_','') and kind='human' and active;
  -- The immutable provider row is the lifecycle stream's serialization head.
  -- The second concurrent CAS caller cannot inspect state until the first
  -- commits, so it must then fail the expected-state comparison below.
  select * into provider from ops.execution_environment_provider p where p_provider_ref='environment-provider:'||p.provider_key||':v'||p.provider_version for update;
  if actor_row.id is null or provider.id is null or jsonb_typeof(p_evidence_refs)<>'array' or jsonb_array_length(p_evidence_refs)=0
     or exists(select 1 from jsonb_array_elements_text(p_evidence_refs) value where value !~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$') then
    raise exception 'environment provider transition lacks valid authority, provider, or evidence';
  end if;
  select * into existing from ops.execution_environment_provider_event where idempotency_key=p_idempotency_key for share;
  if found then
    if existing.provider_id<>provider.id or existing.from_state is distinct from p_expected_state or existing.to_state<>p_target_state or existing.evidence_refs is distinct from p_evidence_refs then raise exception 'environment provider transition idempotency conflict'; end if;
    return query select p_provider_ref,existing.to_state,true; return;
  end if;
  current_state := ops.execution_environment_provider_current_state(provider.id);
  if current_state is distinct from p_expected_state
     or not ((p_expected_state='discovered' and p_target_state='quarantined')
       or (p_expected_state='quarantined' and p_target_state='conformance_passed')
       or (p_expected_state='conformance_passed' and p_target_state='shadow')
       or (p_expected_state='shadow' and p_target_state='canary')
       or (p_expected_state='canary' and p_target_state='active')
       or (p_expected_state='active' and p_target_state='disabled')
       or (p_expected_state='disabled' and p_target_state='canary')
       or (p_expected_state<>'retired' and p_target_state='retired')) then
    raise exception 'environment provider transition is stale or forbidden';
  end if;
  if p_target_state in ('conformance_passed','shadow','canary','active') and coalesce((
    select c.status from ops.execution_environment_conformance c where c.provider_id=provider.id order by c.observed_at desc,c.id desc limit 1),'unavailable')<>'passed' then
    raise exception 'environment provider cannot advance without passed conformance';
  end if;
  insert into ops.execution_environment_provider_event(provider_id,from_state,to_state,evidence_refs,ruled_by_actor_id,idempotency_key)
  values(provider.id,p_expected_state,p_target_state,p_evidence_refs,actor_row.id,p_idempotency_key);
  return query select p_provider_ref,p_target_state,false;
end $function$;

CREATE OR REPLACE FUNCTION ops.transition_proposed_eval_candidate(p_work_request text, p_candidate_ref text, p_next_state text, p_decision_basis jsonb, p_idempotency_key uuid)
 RETURNS TABLE(candidate_id uuid, lifecycle text, golden_member boolean)
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'ops', 'public', 'pg_temp'
AS $function$
declare tenant text := current_setting('carr.organization_tenant_id', true); candidate ops.proposed_eval_candidate%rowtype; actor_row public.actor%rowtype;
  current_state text; replay_event ops.proposed_eval_candidate_event%rowtype; v_actor_slug text;
begin
  if session_user ~ '^carr_authority_' then
    v_actor_slug := regexp_replace(session_user,'^carr_authority_','');
  elsif ops.login_bundle_principal(session_user) = 'carr_writer' then
    v_actor_slug := nullif(btrim(current_setting('carr.acting_actor_slug', true)), '');
  else
    raise exception 'eval candidate transition requires the authority connection or a sponsored writer session';
  end if;
  select * into actor_row from public.actor where slug=v_actor_slug and kind in ('human','automation') and active;
  select p.* into candidate from ops.proposed_eval_candidate p join ops.attempt_receipt r on r.id=p.attempt_receipt_id join ops.work_request w on w.id=r.work_request_id
   where p.organization_tenant_id=tenant and p.candidate_ref=p_candidate_ref and w.ref=p_work_request and w.organization_tenant_id=tenant for update of p;
  if actor_row.id is null or candidate.id is null or jsonb_typeof(p_decision_basis)<>'object' then raise exception 'eval candidate transition lacks visible authority/candidate/basis'; end if;
  if ops.attempt_receipt_contains_raw_content(p_decision_basis) then raise exception 'eval candidate decision basis must be metadata-only'; end if;
  select * into replay_event from ops.proposed_eval_candidate_event where idempotency_key=p_idempotency_key for share;
  if found then
    if replay_event.candidate_id<>candidate.id or replay_event.event_kind<>p_next_state or replay_event.decision_basis is distinct from p_decision_basis then raise exception 'eval candidate transition idempotency conflict'; end if;
    select pe.event_kind into current_state from ops.proposed_eval_candidate_event pe where pe.candidate_id=candidate.id order by pe.created_at desc,pe.id desc limit 1;
    return query select candidate.id,current_state,current_state='accepted'; return;
  end if;
  select pe.event_kind into current_state from ops.proposed_eval_candidate_event pe where pe.candidate_id=candidate.id order by pe.created_at desc,pe.id desc limit 1;
  if p_next_state not in ('triaged','accepted','retired')
     or (current_state='proposed' and p_next_state<>'triaged')
     or (current_state='triaged' and p_next_state<>'accepted')
     or (current_state='accepted' and p_next_state<>'retired')
     or current_state not in ('proposed','triaged','accepted') then
    raise exception 'invalid append-only eval candidate lifecycle transition';
  end if;
  insert into ops.proposed_eval_candidate_event(candidate_id,event_kind,decided_by_actor_id,decision_basis,idempotency_key)
  values(candidate.id,p_next_state,actor_row.id,p_decision_basis,p_idempotency_key);
  if p_next_state='accepted' then
    insert into ops.accepted_eval_golden_membership(candidate_id,target_golden_set_ref,accepted_by_actor_id)
    values(candidate.id,candidate.target_golden_set_ref,actor_row.id);
  end if;
  return query select candidate.id,p_next_state,(p_next_state='accepted');
end $function$;

CREATE OR REPLACE FUNCTION ops.v5_a05_assurance_cadence_batch(p_recipient_slug text)
 RETURNS jsonb
 LANGUAGE plpgsql
 STABLE SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'ops', 'public', 'pg_temp'
AS $function$
declare v_result jsonb; v_recipient uuid;
begin
  if p_recipient_slug is null or p_recipient_slug not in ('joe', 'dell') then
    raise exception using errcode = '42501',
      message = 'the V5-A05 morning batch is readable only for a partner recipient';
  end if;
  select id into v_recipient from public.actor
   where slug = p_recipient_slug and kind = 'human' and active;
  if v_recipient is null then
    raise exception using errcode = '42501',
      message = 'the V5-A05 morning batch is readable only for an active partner recipient';
  end if;

  select coalesce(jsonb_agg(row_to_json(batch) order by batch.created_at desc), '[]'::jsonb)
    into v_result
  from (
    select n.id as notification_id, n.reason, n.severity, n.subject_type, n.subject_ref,
           n.deep_link, n.created_at, s.signal_kind as reason_id
      from ops.notification n
      join public.signal_event s on s.id = n.event_ref and n.event_source = 'signal_event'
      left join ops.notification_read r
        on r.notification_id = n.id and r.recipient_actor = n.recipient_actor
     where n.recipient_actor = v_recipient
       and s.producer = 'v5-a05-delivery-cadence'
       and r.notification_id is null
       and not exists (select 1 from ops.notification_delivery h
                        where h.notification_id = n.id and h.state = 'held_until_morning'
                          and h.held_until > now())
     order by n.created_at desc
     limit 50
  ) batch;
  return v_result;
end;
$function$;

CREATE OR REPLACE FUNCTION public.retrieval_visibility_actor_id(p_sponsor_slug text)
 RETURNS uuid
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
  select id from public.actor
   where slug = p_sponsor_slug and kind = 'human' and active = true
$function$;

CREATE OR REPLACE FUNCTION public.search_doctrine_situations(p_query text, p_actor_id uuid, p_content_classes text[] DEFAULT NULL::text[], p_limit integer DEFAULT 20, p_policy_id text DEFAULT NULL::text, p_allow_fallback boolean DEFAULT false)
 RETURNS TABLE(section_id uuid, section_key text, title text, doc_slug text, content_class text, rank double precision, snippet text, lexical_score double precision, concept_score double precision, final_score double precision, provenance jsonb)
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
with
  normalized as materialized (
    select public.normalize_retrieval_phrase(p_query) as q
  ),
  policy as materialized (
    select * from public.retrieval_ranking_policy
     where policy_id = coalesce(
       p_policy_id,
       (select policy_id from public.retrieval_ranking_policy where is_default and status='active'))
       and status in ('candidate','active')
  ),
  query_terms as materialized (
    select websearch_to_tsquery('english', q) as tsq from normalized
  ),
  current_set as materialized (
    select * from public.current_retrievable_doctrine(p_actor_id, p_content_classes)
  ),
  lexical_raw as (
    select c.section_id,
           ts_rank_cd(
             setweight(c.section_title_vector, 'A') ||
             setweight(c.document_title_vector, 'A') ||
             setweight(c.body_search_vector, 'B'), q.tsq) as raw_score,
           ts_headline('english', c.plain_text, q.tsq, 'MaxWords=25, MinWords=10') as snippet
      from current_set c cross join query_terms q
     where c.section_title_vector @@ q.tsq
        or c.document_title_vector @@ q.tsq
        or c.body_search_vector @@ q.tsq
  ),
  phrase_match as (
    select p.id as phrase_id, p.concept_id, p.weight as phrase_weight,
           case p.match_mode
             when 'exact' then case when p.normalized_phrase = n.q then 1.0 else 0.0 end
             when 'fts' then case
               when to_tsvector('english', p.normalized_phrase) @@ websearch_to_tsquery('english', n.q)
               then least(1.0, ts_rank_cd(to_tsvector('english', p.normalized_phrase),
                                         websearch_to_tsquery('english', n.q))::double precision)
               else 0.0 end
             when 'trgm' then case when public.similarity(p.normalized_phrase, n.q) >= p.min_similarity
               then public.similarity(p.normalized_phrase, n.q) else 0.0 end
           end::double precision as phrase_strength
      from public.retrieval_phrase p cross join normalized n
     where p.status = 'approved'
  ),
  concept_evidence as (
    select m.section_id, pm.phrase_id, pm.concept_id, m.id as mapping_id,
           pm.phrase_strength, pm.phrase_weight::double precision,
           m.weight::double precision as mapping_weight,
           (pm.phrase_strength * pm.phrase_weight * m.weight)::double precision as contribution
      from phrase_match pm
      join public.retrieval_concept c on c.id = pm.concept_id and c.status = 'approved'
      join public.doctrine_concept_mapping m on m.concept_id = c.id and m.status = 'approved'
      join current_set visible on visible.section_id = m.section_id
     where pm.phrase_strength > 0
  ),
  concept_by_section as (
    select section_id, max(contribution)::double precision as concept_score,
           array_agg(distinct phrase_id order by phrase_id) as phrase_ids,
           array_agg(distinct concept_id order by concept_id) as concept_ids,
           array_agg(distinct mapping_id order by mapping_id) as mapping_ids
      from concept_evidence group by section_id
  ),
  unioned as materialized (
    -- Coalesce FIRST, cap second: least(1.0, NULL) is 1.0 in PostgreSQL
    -- (nulls are ignored), which is the whole defect this migration removes.
    select c.*, least(1.0, coalesce(l.raw_score, 0))::double precision as lexical_score,
           least(1.0, coalesce(ce.concept_score, 0))::double precision as concept_score,
           l.snippet, coalesce(ce.phrase_ids, '{}') as phrase_ids,
           coalesce(ce.concept_ids, '{}') as concept_ids,
           coalesce(ce.mapping_ids, '{}') as mapping_ids,
           false as used_fallback
      from current_set c
      left join lexical_raw l on l.section_id = c.section_id
      left join concept_by_section ce on ce.section_id = c.section_id
     where l.section_id is not null or ce.section_id is not null
  ),
  fallback_terms as materialized (
    select websearch_to_tsquery('english', regexp_replace(q, ' ', ' OR ', 'g')) as tsq
      from normalized
  ),
  fallback_raw as (
    select c.section_id,
           ts_rank_cd(
             setweight(c.section_title_vector, 'A') ||
             setweight(c.document_title_vector, 'A') ||
             setweight(c.body_search_vector, 'B'), q.tsq) as raw_score,
           ts_headline('english', c.plain_text, q.tsq, 'MaxWords=25, MinWords=10') as snippet
      from current_set c cross join fallback_terms q
     where p_allow_fallback
       and not exists (select 1 from unioned)
       and (c.section_title_vector @@ q.tsq
         or c.document_title_vector @@ q.tsq
         or c.body_search_vector @@ q.tsq)
  ),
  fallback_unioned as (
    select c.*, least(1.0, coalesce(f.raw_score, 0))::double precision as lexical_score,
           0::double precision as concept_score,
           f.snippet, '{}'::uuid[] as phrase_ids,
           '{}'::uuid[] as concept_ids,
           '{}'::uuid[] as mapping_ids,
           true as used_fallback
      from current_set c
      join fallback_raw f on f.section_id = c.section_id
  ),
  combined as (
    select * from unioned
    union all
    select * from fallback_unioned
  ),
  maxima as (
    select greatest(max(lexical_score), 0) as max_lexical,
           greatest(max(concept_score), 0) as max_concept from combined
  ),
  scored as (
    select u.*,
           case p.formula
             when 'weighted_sum' then
               ((p.config->>'lexical_weight')::double precision * u.lexical_score +
                (p.config->>'concept_weight')::double precision *
                  case when coalesce((p.config->>'concept_enabled')::boolean,true)
                       then u.concept_score else 0 end)
             when 'coequal_normalized' then
               (case when x.max_lexical > 0 then u.lexical_score / x.max_lexical else 0 end) +
               (case when coalesce((p.config->>'concept_enabled')::boolean,true) and x.max_concept > 0
                     then u.concept_score / x.max_concept else 0 end) +
               (case when coalesce((p.config->>'concept_enabled')::boolean,true)
                           and u.lexical_score > 0 and u.concept_score > 0
                     then (p.config->>'dual_evidence_bonus')::double precision else 0 end)
           end::double precision as final_score,
           p.policy_id, p.version as policy_version
      from combined u cross join maxima x cross join policy p
  ),
  limited as materialized (
    select * from scored
     where final_score > 0
     order by final_score desc, concept_score desc, lexical_score desc, section_key asc
     limit greatest(1, least(coalesce(p_limit, 20), 100))
  )
select l.section_id, l.section_key, l.section_title, l.doc_slug, l.content_class,
       l.final_score as rank,
       coalesce(l.snippet, left(l.plain_text, 240)) as snippet,
       l.lexical_score, l.concept_score, l.final_score,
       jsonb_build_object(
         'complete', true, 'policy_id', l.policy_id, 'policy_version', l.policy_version,
         'lexical_score', l.lexical_score, 'concept_score', l.concept_score,
         'final_score', l.final_score, 'phrase_ids', to_jsonb(l.phrase_ids),
         'concept_ids', to_jsonb(l.concept_ids), 'mapping_ids', to_jsonb(l.mapping_ids))
       || case when l.used_fallback then jsonb_build_object('fallback', true)
               else '{}'::jsonb end
  from limited l
 order by l.final_score desc, l.concept_score desc, l.lexical_score desc, l.section_key asc
$function$;
