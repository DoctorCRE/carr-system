-- rollback: forward-only; restore retired callable interfaces in a new migration if needed; historical receipts remain immutable
-- expand-contract: contract; remove retired call paths and rename receipt tables only after their callers are removed; preserve all receipt rows
-- lock-review: brief catalog/function locks and metadata-only renames on two receipt tables; no receipt rows are rewritten or deleted
-- Heavy-build protocol retired by Joe on 2026-10-17, receipt 09de9da4.
-- Keep the completed admission/review history, with no callable admission path.
DROP TRIGGER work_request_heavy_build_ready_plan_gate ON ops.work_request;

CREATE OR REPLACE FUNCTION ops.accept_ready_plan_amendment(p_work_request text, p_base_version integer, p_plan_hash text, p_idempotency_key uuid) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'ops', 'public', 'pg_temp'
    AS $_$
declare v_slug text; v_actor uuid; v_work ops.work_request%rowtype;
        v_plan ops.sourced_work_request_plan%rowtype; v_prior ops.sourced_work_request_plan%rowtype;
        v_amend ops.ready_plan_amendment%rowtype; v_accept ops.ready_plan_amendment_acceptance_receipt%rowtype;
        v_plan_accept ops.sourced_work_request_plan_acceptance_receipt%rowtype;
        v_census jsonb; v_notice_count integer; v_expected_slices integer; v_mapped_slices integer;
begin
  v_slug:=ops.authority_actor_slug();
  select id into v_actor from public.actor where slug=v_slug and active and kind='human' for share;
  if v_actor is null then return jsonb_build_object('ok',false,'reason_id','ready_plan_amendment_authority_required'); end if;
  if p_idempotency_key is null or p_base_version is null or p_base_version<1
     or coalesce(p_plan_hash,'')!~'^sha256:[0-9a-f]{64}$' then
    return jsonb_build_object('ok',false,'reason_id','ready_plan_amendment_accept_input_invalid');
  end if;
  perform pg_advisory_xact_lock(hashtextextended('ready-plan-amendment-accept:'||p_work_request,0));
  select * into v_accept from ops.ready_plan_amendment_acceptance_receipt where idempotency_key=p_idempotency_key;
  if found then
    select * into v_work from ops.work_request where id=v_accept.work_request_id;
    select * into v_plan from ops.sourced_work_request_plan where id=v_accept.successor_plan_id;
    select * into v_prior from ops.sourced_work_request_plan where id=v_accept.predecessor_plan_id;
    if v_work.ref is distinct from p_work_request or v_accept.base_version<>p_base_version or v_plan.plan_hash<>p_plan_hash then
      return jsonb_build_object('ok',false,'reason_id','ready_plan_amendment_accept_idempotency_conflict');
    end if;
    return jsonb_build_object('ok',true,'replayed',true,
      'work_request',jsonb_build_object('id',v_work.id,'ref',v_work.ref,'state','ready','version',v_accept.result_version),
      'prior_plan',jsonb_build_object('ref',v_prior.plan_ref,'hash',v_prior.plan_hash),
      'successor_plan',jsonb_build_object('ref',v_plan.plan_ref,'hash',v_plan.plan_hash));
  end if;
  select * into v_work from ops.work_request where ref=p_work_request for update;
  select p.* into v_plan from ops.sourced_work_request_plan p
   where p.work_request_id=v_work.id and p.plan_hash=p_plan_hash for share of p;
  select a.* into v_amend from ops.ready_plan_amendment a where a.plan_id=v_plan.id for share of a;
  select * into v_prior from ops.sourced_work_request_plan where id=v_amend.predecessor_plan_id for share;
  if v_work.id is null or v_work.state<>'ready' or v_work.version<>p_base_version
     or v_plan.id is null or v_amend.base_version<>v_work.version
     or v_prior.plan_hash<>v_amend.predecessor_plan_hash
     or not exists(select 1 from ops.sourced_work_request_plan_acceptance_receipt ar
       where ar.work_request_id=v_work.id and ar.plan_id=v_prior.id and ar.result_version=v_work.version)
     or ops.engineering_contract_stale(v_plan.plan_ref,v_plan.plan_hash,null) then
    return jsonb_build_object('ok',false,'reason_id','ready_plan_amendment_accept_stale');
  end if;
  insert into ops.ready_plan_amendment_slice_compatibility(
    successor_plan_id,predecessor_slice_plan_id,predecessor_plan_digest,
    slice_ref,predecessor_slice_digest)
  select v_plan.id,sp.id,sp.plan_digest,slice->>'slice_ref',
    'sha256:'||encode(public.digest(
      ops.guidance_import_canonical_json(slice),'sha256'),'hex')
    from ops.engineering_slice_plan sp
    cross join lateral jsonb_array_elements(sp.plan->'slices') slice
   where sp.work_request_id=v_work.id and sp.accepted_plan_id=v_prior.id
     and jsonb_typeof(sp.plan->'slices')='array'
  on conflict do nothing;
  select coalesce(sum(jsonb_array_length(sp.plan->'slices')),0) into v_expected_slices
    from ops.engineering_slice_plan sp
   where sp.work_request_id=v_work.id and sp.accepted_plan_id=v_prior.id
     and jsonb_typeof(sp.plan->'slices')='array';
  select count(*) into v_mapped_slices
    from ops.ready_plan_amendment_slice_compatibility
   where successor_plan_id=v_plan.id;
  if v_mapped_slices<>v_expected_slices then
    return jsonb_build_object('ok',false,'reason_id','ready_plan_amendment_compatibility_incomplete',
      'expected_slices',v_expected_slices,'mapped_slices',v_mapped_slices);
  end if;
  v_census:=ops.ready_plan_amendment_census(v_work.id,v_prior.id);
  if coalesce((v_census->>'safe')::boolean,false) is not true then
    return jsonb_build_object('ok',false,'reason_id','ready_plan_amendment_active_work','census',v_census);
  end if;
  insert into ops.sourced_work_request_plan_acceptance_receipt(work_request_id,plan_id,idempotency_key,
    base_version,plan_hash,accepted_by_actor_id,result_version,shape_fixed_surface_ref,shape_rationale)
  values(v_work.id,v_plan.id,p_idempotency_key,v_work.version,v_plan.plan_hash,v_actor,v_work.version+1,
    v_work.shape_fixed_surface_ref,v_work.shape_rationale) returning * into v_plan_accept;
  insert into ops.sourced_work_request_plan_shape_binding_receipt(plan_acceptance_receipt_id,work_request_id,
    disposition,fixed_surface_ref,rationale,decided_by_actor_id,decided_at)
  values(v_plan_accept.id,v_work.id,v_work.shape_disposition,v_work.shape_fixed_surface_ref,
    v_work.shape_rationale,v_work.shape_decided_by_actor_id,v_work.shape_decided_at);
  insert into ops.ready_plan_amendment_acceptance_receipt(work_request_id,predecessor_plan_id,
    successor_plan_id,plan_acceptance_receipt_id,idempotency_key,base_version,result_version,
    accepted_by_actor_id,accepted_at,census)
  values(v_work.id,v_prior.id,v_plan.id,v_plan_accept.id,p_idempotency_key,v_work.version,
    v_work.version+1,v_actor,v_plan_accept.accepted_at,v_census) returning * into v_accept;
  update ops.work_request set version=v_accept.result_version,updated_at=now() where id=v_work.id;
  insert into ops.ready_plan_amendment_notice(acceptance_receipt_id,actor_id)
    select v_accept.id,a.id from public.actor a where a.active on conflict do nothing;
  get diagnostics v_notice_count=row_count;
  return jsonb_build_object('ok',true,'replayed',false,
    'work_request',jsonb_build_object('id',v_work.id,'ref',v_work.ref,'state',v_work.state,'version',v_accept.result_version),
    'prior_plan',jsonb_build_object('ref',v_prior.plan_ref,'hash',v_prior.plan_hash),
    'successor_plan',jsonb_build_object('ref',v_plan.plan_ref,'hash',v_plan.plan_hash),
    'notice_count',v_notice_count,'census',v_census);
end $_$;

CREATE OR REPLACE FUNCTION ops.set_sourced_work_request_shape_disposition(p_work_request text, p_base_version integer, p_disposition text, p_fixed_surface_ref text, p_rationale text, p_decided_by_actor_id uuid, p_idempotency_key uuid) RETURNS TABLE(work_request_id uuid, ref text, state text, version integer, shape_disposition text, shape_fixed_surface_ref text, shape_rationale text, shape_decided_by_actor_id uuid, shape_decided_at timestamp with time zone, replayed boolean)
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'ops', 'pg_temp'
    AS $_$
declare
  w ops.work_request%rowtype;
  original ops.sourced_work_request_shape_disposition_receipt%rowtype;
  correction ops.sourced_work_request_shape_disposition_correction_receipt%rowtype;
  actor public.actor%rowtype;
  normalized_fixed_surface text := nullif(btrim(coalesce(p_fixed_surface_ref,'')), '');
  normalized_rationale text := nullif(btrim(coalesce(p_rationale,'')), '');
begin
  if coalesce(btrim(p_work_request),'') !~ '^WR-[0-9]{1,12}$'
     or p_base_version is null or p_base_version < 1
     or p_disposition not in ('required','not_required')
     or normalized_rationale is null
     or p_decided_by_actor_id is null
     or p_idempotency_key is null
     or (p_disposition = 'required' and normalized_fixed_surface is not null)
     or (p_disposition = 'not_required' and normalized_fixed_surface is null) then
    raise exception 'sourced shape disposition requires exact Work Request/base version, closed disposition, exact fixed surface rule, rationale, active actor, and UUID idempotency key';
  end if;

  select a.* into actor from public.actor a
   where a.id = p_decided_by_actor_id and a.active
   for share;
  if not found then
    raise exception 'sourced shape disposition actor is not active';
  end if;

  -- One advisory lock on the caller key precedes BOTH receipt lookups, so two
  -- first calls with the same key serialize before either can observe an
  -- empty table, and a key can never be reused across the two tables.
  perform pg_advisory_xact_lock(hashtextextended('program6-sourced-shape-disposition:' || p_idempotency_key, 0));
  select r.* into original
    from ops.sourced_work_request_shape_disposition_receipt r
   where r.idempotency_key = p_idempotency_key
   for share;
  if found then
    select x.* into w from ops.work_request x where x.id = original.work_request_id for share;
    if not found
       or w.ref is distinct from p_work_request
       or original.base_version is distinct from p_base_version
       or original.disposition is distinct from p_disposition
       or original.fixed_surface_ref is distinct from normalized_fixed_surface
       or original.rationale is distinct from normalized_rationale
       or original.decided_by_actor_id is distinct from p_decided_by_actor_id
       or w.state is distinct from 'triaged'
       or w.version is distinct from original.result_version
       or (w.shape_disposition,w.shape_fixed_surface_ref,w.shape_rationale,w.shape_decided_by_actor_id,w.shape_decided_at)
          is distinct from
          (original.disposition,original.fixed_surface_ref,original.rationale,original.decided_by_actor_id,original.decided_at) then
      raise exception 'idempotency key already names a different sourced shape disposition';
    end if;
    return query select w.id,w.ref,w.state,w.version,w.shape_disposition,w.shape_fixed_surface_ref,
      w.shape_rationale,w.shape_decided_by_actor_id,w.shape_decided_at,true;
    return;
  end if;
  select c.* into correction
    from ops.sourced_work_request_shape_disposition_correction_receipt c
   where c.idempotency_key = p_idempotency_key
   for share;
  if found then
    select x.* into w from ops.work_request x where x.id = correction.work_request_id for share;
    if not found
       or w.ref is distinct from p_work_request
       or correction.base_version is distinct from p_base_version
       or p_disposition is distinct from 'required'
       or normalized_fixed_surface is not null
       or correction.rationale is distinct from normalized_rationale
       or correction.decided_by_actor_id is distinct from p_decided_by_actor_id
       or w.state is distinct from 'triaged'
       or w.version is distinct from correction.result_version
       or (w.shape_disposition,w.shape_fixed_surface_ref,w.shape_rationale,w.shape_decided_by_actor_id,w.shape_decided_at)
          is distinct from
          (correction.disposition,correction.fixed_surface_ref,correction.rationale,correction.decided_by_actor_id,correction.decided_at) then
      raise exception 'idempotency key already names a different sourced shape correction';
    end if;
    return query select w.id,w.ref,w.state,w.version,w.shape_disposition,w.shape_fixed_surface_ref,
      w.shape_rationale,w.shape_decided_by_actor_id,w.shape_decided_at,true;
    return;
  end if;

  select x.* into w from ops.work_request x
   where x.ref = p_work_request
   for update;
  if not found
     or w.capture_idempotency_key is null
     or w.organization_tenant_id is distinct from 'carr-internal'
     or w.state is distinct from 'triaged'
     or w.version is distinct from p_base_version
     or w.program_key is not null or w.program_ordinal is not null then
    raise exception 'exact current triaged sourced Work Request required';
  end if;

  select r.* into original
    from ops.sourced_work_request_shape_disposition_receipt r
   where r.work_request_id = w.id
   for share;
  if found then
    -- Forward correction: exactly one monotonic not_required -> required on
    -- the exact current receipt-backed row, before any Shape revision, with
    -- no prior correction.
    if p_disposition is distinct from 'required'
       or normalized_fixed_surface is not null
       or original.disposition is distinct from 'not_required'
       or original.result_version is distinct from w.version
       or (w.shape_disposition,w.shape_fixed_surface_ref,w.shape_rationale,w.shape_decided_by_actor_id,w.shape_decided_at)
          is distinct from
          (original.disposition,original.fixed_surface_ref,original.rationale,original.decided_by_actor_id,original.decided_at)
       or exists (select 1 from ops.work_shape_revision sr where sr.work_request_id = w.id)
       or exists (select 1 from ops.sourced_work_request_shape_disposition_correction_receipt c
                   where c.work_request_id = w.id or c.original_receipt_id = original.id) then
      raise exception 'only an exact current sourced not_required receipt may receive one required correction before Shape';
    end if;
    insert into ops.sourced_work_request_shape_disposition_correction_receipt
      (work_request_id,idempotency_key,original_receipt_id,base_version,result_version,disposition,fixed_surface_ref,rationale,decided_by_actor_id)
    values
      (w.id,p_idempotency_key,original.id,w.version,w.version + 1,'required',null,normalized_rationale,p_decided_by_actor_id)
    returning * into correction;
    update ops.work_request x
       set shape_disposition = correction.disposition,
           shape_fixed_surface_ref = correction.fixed_surface_ref,
           shape_rationale = correction.rationale,
           shape_decided_by_actor_id = correction.decided_by_actor_id,
           shape_decided_at = correction.decided_at,
           version = correction.result_version,
           updated_at = now()
     where x.id = w.id;
  else
    if (w.shape_disposition,w.shape_fixed_surface_ref,w.shape_rationale,w.shape_decided_by_actor_id,w.shape_decided_at)
         is distinct from (null::text,null::text,null::text,null::uuid,null::timestamptz)
       or exists (select 1 from ops.work_shape_revision sr where sr.work_request_id = w.id) then
      raise exception 'only the exact current unshaped triaged sourced Work Request may record a shape disposition';
    end if;
    insert into ops.sourced_work_request_shape_disposition_receipt
      (work_request_id,idempotency_key,base_version,result_version,disposition,fixed_surface_ref,rationale,decided_by_actor_id)
    values
      (w.id,p_idempotency_key,p_base_version,w.version + 1,p_disposition,normalized_fixed_surface,normalized_rationale,p_decided_by_actor_id)
    returning * into original;
    update ops.work_request x
       set shape_disposition = original.disposition,
           shape_fixed_surface_ref = original.fixed_surface_ref,
           shape_rationale = original.rationale,
           shape_decided_by_actor_id = original.decided_by_actor_id,
           shape_decided_at = original.decided_at,
           version = original.result_version,
           updated_at = now()
     where x.id = w.id;
  end if;
  select x.* into w from ops.work_request x where x.id = w.id;
  return query select w.id,w.ref,w.state,w.version,w.shape_disposition,w.shape_fixed_surface_ref,
    w.shape_rationale,w.shape_decided_by_actor_id,w.shape_decided_at,false;
end;
$_$;

DROP FUNCTION ops.propose_ready_plan_amendment(text, integer, text, text, text, jsonb, text, text, jsonb, jsonb, uuid, uuid);

CREATE OR REPLACE FUNCTION ops.propose_ready_plan_amendment(p_work_request text, p_base_version integer, p_predecessor_plan_hash text, p_scope_summary text, p_runbook_ref text, p_dependency_refs jsonb, p_recovery_ref text, p_observability_ref text, p_caps jsonb, p_proposed_by_actor_id uuid, p_idempotency_key uuid) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'ops', 'public', 'pg_temp'
    AS $_$
declare v_actor uuid; v_work ops.work_request%rowtype; v_prior ops.sourced_work_request_plan%rowtype;
        v_plan ops.sourced_work_request_plan%rowtype; v_existing ops.sourced_work_request_plan%rowtype;
        v_section uuid; v_revision uuid; v_hash text; v_preimage jsonb;
        v_plan_hash text; v_version integer; v_amendment_hash text;
        v_amend ops.ready_plan_amendment%rowtype;
begin
  v_actor:=ops.portfolio_writer_actor_id();
  if coalesce(p_work_request,'')!~'^WR-[0-9]{1,12}$'
     or p_idempotency_key is null or p_base_version is null or p_base_version<1
     or coalesce(p_predecessor_plan_hash,'')!~'^sha256:[0-9a-f]{64}$'
     or coalesce(btrim(p_scope_summary),'')='' or char_length(btrim(p_scope_summary))>1000
     or coalesce(btrim(p_runbook_ref),'')!~'^doctrine:runbook#[a-z0-9][a-z0-9-]*$'
     or coalesce(btrim(p_recovery_ref),'')!~'^safe:[a-z0-9][a-z0-9:_./-]*$'
     or char_length(btrim(p_recovery_ref))>300
     or coalesce(btrim(p_observability_ref),'')!~'^safe:[a-z0-9][a-z0-9:_./-]*$'
     or char_length(btrim(p_observability_ref))>300
     or jsonb_typeof(p_dependency_refs) is distinct from 'array'
     or jsonb_array_length(p_dependency_refs)>12
     or exists(select 1 from jsonb_array_elements(p_dependency_refs) dep
       where jsonb_typeof(dep)<>'string' or dep#>>'{}'!~'^safe:[a-z0-9][a-z0-9:_./-]*$'
         or char_length(dep#>>'{}')>300)
     or exists(select dep#>>'{}' from jsonb_array_elements(p_dependency_refs) dep
       group by dep#>>'{}' having count(*)>1)
     or jsonb_typeof(p_caps) is distinct from 'object'
     or ((select array_agg(key order by key) from jsonb_object_keys(p_caps) key)
          is distinct from array['max_duration_minutes','max_steps']::text[]
       and (select array_agg(key order by key) from jsonb_object_keys(p_caps) key)
          is distinct from array['max_duration_minutes','max_steps','source_merge']::text[])
     or coalesce(p_caps->>'max_steps','')!~'^[0-9]+$'
     or coalesce(p_caps->>'max_duration_minutes','')!~'^[0-9]+$'
     or (p_caps->>'max_steps')::integer not between 1 and 20
     or (p_caps->>'max_duration_minutes')::integer not between 1 and 120
     or (p_caps?'source_merge' and not coalesce(ops.source_merge_scope_valid(p_caps->'source_merge'),false))
     or p_proposed_by_actor_id is null or p_proposed_by_actor_id<>v_actor then
    return jsonb_build_object('ok',false,'reason_id','ready_plan_amendment_input_invalid');
  end if;
  perform pg_advisory_xact_lock(hashtextextended('ready-plan-amendment:'||p_work_request,0));
  select * into v_existing from ops.sourced_work_request_plan where idempotency_key=p_idempotency_key;
  if found then
    select * into v_work from ops.work_request where id=v_existing.work_request_id;
    select * into v_prior from ops.sourced_work_request_plan where id=(select predecessor_plan_id from ops.ready_plan_amendment where plan_id=v_existing.id);
    select * into v_amend from ops.ready_plan_amendment where plan_id=v_existing.id;
    if v_work.ref is distinct from p_work_request or v_prior.plan_hash is distinct from p_predecessor_plan_hash
       or v_existing.work_request_version is distinct from p_base_version
       or v_existing.scope_summary is distinct from btrim(p_scope_summary)
       or v_existing.runbook_ref is distinct from btrim(p_runbook_ref)
       or v_existing.dependency_refs is distinct from p_dependency_refs
       or v_existing.recovery_ref is distinct from btrim(p_recovery_ref)
       or v_existing.observability_ref is distinct from btrim(p_observability_ref)
       or v_existing.caps is distinct from p_caps
       or v_amend.proposed_by_actor_id is distinct from p_proposed_by_actor_id then
      return jsonb_build_object('ok',false,'reason_id','ready_plan_amendment_idempotency_conflict');
    end if;
    return jsonb_build_object('ok',true,'replayed',true,
      'work_request',jsonb_build_object('id',v_work.id,'ref',v_work.ref,'state','ready','version',v_existing.work_request_version),
      'plan',jsonb_build_object('id',v_existing.id,'ref',v_existing.plan_ref,'hash',v_existing.plan_hash,'version',v_existing.plan_version,
        'predecessor_ref',v_prior.plan_ref,'predecessor_hash',v_prior.plan_hash));
  end if;
  select * into v_work from ops.work_request where ref=p_work_request for update;
  if not found or v_work.state<>'ready' or v_work.version<>p_base_version then
    return jsonb_build_object('ok',false,'reason_id','ready_plan_amendment_base_stale');
  end if;
  select p.* into v_prior from ops.sourced_work_request_plan_acceptance_receipt ar
    join ops.sourced_work_request_plan p on p.id=ar.plan_id
   where ar.work_request_id=v_work.id and ar.result_version=v_work.version for share of p,ar;
  if not found or v_prior.plan_hash<>p_predecessor_plan_hash
     or exists(select 1 from ops.engineering_stale_contract_fence f
       where (f.plan_ref=v_prior.plan_ref or f.plan_hash=v_prior.plan_hash)
         and not f.successor_allowed) then
    return jsonb_build_object('ok',false,'reason_id','ready_plan_amendment_predecessor_stale');
  end if;
  if exists(select 1 from ops.ready_plan_amendment a
    where a.work_request_id=v_work.id and a.predecessor_plan_id=v_prior.id) then
    return jsonb_build_object('ok',false,'reason_id','ready_plan_amendment_fork_exists');
  end if;
  select s.id,r.id,r.content_hash into v_section,v_revision,v_hash
    from public.doctrine_document d join public.doctrine_section s on s.document_id=d.id
    join public.doctrine_revision r on r.id=s.current_revision_id
   where d.slug='runbook' and s.status='active'
     and 'doctrine:'||d.slug||'#'||s.section_key=btrim(p_runbook_ref)
     and r.section_id=s.id and encode(public.digest(r.plain_text,'sha256'),'hex')=r.content_hash
   for share of d,s,r;
  if not found then return jsonb_build_object('ok',false,'reason_id','ready_plan_amendment_runbook_stale'); end if;
  select coalesce(max(plan_version),0)+1 into v_version from ops.sourced_work_request_plan where work_request_id=v_work.id;
  v_preimage:=ops.sourced_work_request_plan_preimage(v_work.id,btrim(p_scope_summary),btrim(p_runbook_ref),
    v_section,v_revision,v_hash,p_dependency_refs,btrim(p_recovery_ref),btrim(p_observability_ref),p_caps)
    ||jsonb_build_object('successor_of',jsonb_build_object('plan_id',v_prior.id,'plan_ref',v_prior.plan_ref,'plan_hash',v_prior.plan_hash));
  v_plan_hash:=ops.sourced_work_request_plan_digest(v_preimage);
  if ops.engineering_contract_stale(null,v_plan_hash,null) then
    return jsonb_build_object('ok',false,'reason_id','ready_plan_amendment_plan_stale');
  end if;
  insert into ops.sourced_work_request_plan(work_request_id,plan_version,idempotency_key,work_request_version,
    preimage,scope_summary,runbook_ref,runbook_section_id,runbook_revision_id,runbook_content_hash,
    dependency_refs,recovery_ref,observability_ref,caps,plan_hash,plan_ref)
  values(v_work.id,v_version,p_idempotency_key,v_work.version,v_preimage,btrim(p_scope_summary),btrim(p_runbook_ref),
    v_section,v_revision,v_hash,p_dependency_refs,btrim(p_recovery_ref),btrim(p_observability_ref),p_caps,
    v_plan_hash,'PLAN-'||substr(v_plan_hash,8,12)||'-v'||v_version)
  returning * into v_plan;
  v_amendment_hash:='sha256:'||encode(public.digest(jsonb_build_object('contract','carr-ready-plan-amendment/v1',
    'work_request_id',v_work.id,'base_version',v_work.version,'predecessor_plan_hash',v_prior.plan_hash,
    'successor_plan_hash',v_plan.plan_hash)::text,'sha256'),'hex');
  insert into ops.ready_plan_amendment(plan_id,work_request_id,predecessor_plan_id,
    predecessor_plan_hash,base_version,amendment_hash,proposed_by_actor_id)
  values(v_plan.id,v_work.id,v_prior.id,v_prior.plan_hash,v_work.version,v_amendment_hash,v_actor);
  return jsonb_build_object('ok',true,'replayed',false,
    'work_request',jsonb_build_object('id',v_work.id,'ref',v_work.ref,'state',v_work.state,'version',v_work.version),
    'plan',jsonb_build_object('id',v_plan.id,'ref',v_plan.plan_ref,'hash',v_plan.plan_hash,'version',v_plan.plan_version,
      'predecessor_ref',v_prior.plan_ref,'predecessor_hash',v_prior.plan_hash));
end $_$;

DROP FUNCTION ops.classify_sourced_work_request_build(text, integer, text, jsonb, jsonb);

DROP FUNCTION ops.heavy_build_classification(uuid, text, jsonb, jsonb);

DROP FUNCTION ops.heavy_build_digest(jsonb);

DROP FUNCTION ops.heavy_build_jsonb_has_exact_keys(jsonb, text[]);

DROP FUNCTION ops.heavy_build_ready_plan_gate();

DROP FUNCTION ops.record_ready_plan_amendment_admission_internal(uuid, text, integer, jsonb, jsonb, uuid, uuid);

DROP FUNCTION ops.record_sourced_heavy_build_admission(uuid, text, integer, jsonb, jsonb, uuid, uuid);

DROP FUNCTION ops.record_sourced_heavy_build_admission_v1(uuid, text, integer, jsonb, jsonb, uuid, uuid);

DROP FUNCTION ops.review_ready_plan_amendment_internal(text, text, text, uuid, text, text, text, jsonb, jsonb, uuid);

DROP FUNCTION ops.review_sourced_heavy_build_plan(text, text, text, uuid, text, text, text, jsonb, jsonb, uuid);

DROP FUNCTION ops.review_sourced_heavy_build_plan_v1(text, text, text, uuid, text, text, text, jsonb, jsonb, uuid);

DROP FUNCTION ops.sourced_heavy_build_review_target(text, text, text);

DROP FUNCTION ops.sourced_heavy_build_review_target_v1(text, text, text);

ALTER FUNCTION ops.heavy_build_admission_rows_immutable() RENAME TO retired_build_receipts_are_immutable;
ALTER TABLE ops.heavy_build_admission_revision RENAME TO retired_build_admission_revision;
ALTER TABLE ops.heavy_build_plan_review RENAME TO retired_build_plan_review;
DO $archive_names$
DECLARE item record; rel regclass;
BEGIN
  FOREACH rel IN ARRAY ARRAY['ops.retired_build_admission_revision'::regclass,'ops.retired_build_plan_review'::regclass] LOOP
    FOR item IN SELECT conname FROM pg_constraint WHERE conrelid=rel AND conname LIKE 'heavy_build_%' LOOP
      EXECUTE format('ALTER TABLE %s RENAME CONSTRAINT %I TO %I',rel,item.conname,replace(item.conname,'heavy_build_','retired_build_'));
    END LOOP;
    FOR item IN SELECT tgname FROM pg_trigger WHERE tgrelid=rel AND NOT tgisinternal AND tgname LIKE 'heavy_build_%' LOOP
      EXECUTE format('ALTER TRIGGER %I ON %s RENAME TO %I',item.tgname,rel,replace(item.tgname,'heavy_build_','retired_build_'));
    END LOOP;
  END LOOP;
END $archive_names$;

CREATE OR REPLACE FUNCTION ops.retired_build_receipts_are_immutable() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'ops'
    AS $$
begin
  raise exception 'retired build receipts are append-only';
end;
$$;

REVOKE ALL ON FUNCTION ops.propose_ready_plan_amendment(text,integer,text,text,text,jsonb,text,text,jsonb,uuid,uuid) FROM PUBLIC,carr_reader,carr_jobs,carr_authority;
GRANT EXECUTE ON FUNCTION ops.propose_ready_plan_amendment(text,integer,text,text,text,jsonb,text,text,jsonb,uuid,uuid) TO carr_writer;

CREATE OR REPLACE FUNCTION ops.scac_current_grant_contract() RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $fn$ SELECT $current_grants${
  "registry_version": "scac-mutation-registry.v118",
  "registry_digest": "sha256:560e16ad4c5383414b36b74da507481d9e840d8c00e45ced6eed6bce2ac3447d",
  "secdef_execute": [
    {
      "config": [
        "search_path=ops, public, pg_temp"
      ],
      "grantee": "carr_authority",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.amend_rule_scope(p_rule_id uuid, p_new_scope jsonb, p_new_statement text, p_base_version integer, p_idempotency_key text, p_reason text)",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.amend_rule_scope(p_rule_id uuid, p_new_scope jsonb, p_new_statement text, p_base_version integer, p_idempotency_key text, p_reason text):carr_authority:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    },
    {
      "config": [
        "search_path=pg_catalog, ops, public, pg_temp"
      ],
      "grantee": "carr_writer",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.propose_ready_plan_amendment(p_work_request text, p_base_version integer, p_predecessor_plan_hash text, p_scope_summary text, p_runbook_ref text, p_dependency_refs jsonb, p_recovery_ref text, p_observability_ref text, p_caps jsonb, p_proposed_by_actor_id uuid, p_idempotency_key uuid)",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.propose_ready_plan_amendment(p_work_request text, p_base_version integer, p_predecessor_plan_hash text, p_scope_summary text, p_runbook_ref text, p_dependency_refs jsonb, p_recovery_ref text, p_observability_ref text, p_caps jsonb, p_proposed_by_actor_id uuid, p_idempotency_key uuid):carr_writer:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    }
  ],
  "retired_secdef_execute": [
    {
      "config": [
        "search_path=pg_catalog, ops, pg_temp"
      ],
      "grantee": "carr_writer",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.classify_sourced_work_request_build(p_work_request text, p_base_version integer, p_scope_summary text, p_dependency_refs jsonb, p_caps jsonb)",
      "volatility": "s",
      "ingress_key": "db-function-acl:ops.classify_sourced_work_request_build(p_work_request text, p_base_version integer, p_scope_summary text, p_dependency_refs jsonb, p_caps jsonb):carr_writer:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    },
    {
      "config": [
        "search_path=pg_catalog, ops, pg_temp"
      ],
      "grantee": "public",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.heavy_build_ready_plan_gate()",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.heavy_build_ready_plan_gate():public:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    },
    {
      "config": [
        "search_path=pg_catalog, ops, public, pg_temp"
      ],
      "grantee": "carr_writer",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.propose_ready_plan_amendment(p_work_request text, p_base_version integer, p_predecessor_plan_hash text, p_scope_summary text, p_runbook_ref text, p_dependency_refs jsonb, p_recovery_ref text, p_observability_ref text, p_caps jsonb, p_heavy_build jsonb, p_proposed_by_actor_id uuid, p_idempotency_key uuid)",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.propose_ready_plan_amendment(p_work_request text, p_base_version integer, p_predecessor_plan_hash text, p_scope_summary text, p_runbook_ref text, p_dependency_refs jsonb, p_recovery_ref text, p_observability_ref text, p_caps jsonb, p_heavy_build jsonb, p_proposed_by_actor_id uuid, p_idempotency_key uuid):carr_writer:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    },
    {
      "config": [
        "search_path=pg_catalog, ops, pg_temp"
      ],
      "grantee": "carr_writer",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.record_sourced_heavy_build_admission(p_plan_id uuid, p_work_request text, p_base_version integer, p_classifier_reasons jsonb, p_contract jsonb, p_proposed_by_actor_id uuid, p_idempotency_key uuid)",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.record_sourced_heavy_build_admission(p_plan_id uuid, p_work_request text, p_base_version integer, p_classifier_reasons jsonb, p_contract jsonb, p_proposed_by_actor_id uuid, p_idempotency_key uuid):carr_writer:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    },
    {
      "config": [
        "search_path=pg_catalog, ops, pg_temp"
      ],
      "grantee": "carr_writer",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.review_sourced_heavy_build_plan(p_work_request text, p_plan_hash text, p_admission_hash text, p_reviewer_actor_id uuid, p_verdict text, p_reviewer_session_ref text, p_review_summary text, p_evidence_refs jsonb, p_gaps jsonb, p_idempotency_key uuid)",
      "volatility": "v",
      "ingress_key": "db-function-acl:ops.review_sourced_heavy_build_plan(p_work_request text, p_plan_hash text, p_admission_hash text, p_reviewer_actor_id uuid, p_verdict text, p_reviewer_session_ref text, p_review_summary text, p_evidence_refs jsonb, p_gaps jsonb, p_idempotency_key uuid):carr_writer:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    },
    {
      "config": [
        "search_path=pg_catalog, ops, pg_temp"
      ],
      "grantee": "carr_writer",
      "parallel": "u",
      "grantable": false,
      "privilege": "execute",
      "signature": "ops.sourced_heavy_build_review_target(p_work_request text, p_plan_hash text, p_admission_hash text)",
      "volatility": "s",
      "ingress_key": "db-function-acl:ops.sourced_heavy_build_review_target(p_work_request text, p_plan_hash text, p_admission_hash text):carr_writer:execute",
      "ingress_kind": "db_function_acl",
      "function_kind": "f",
      "security_definer": true
    }
  ]
}$current_grants$::jsonb $fn$;

create or replace function ops.scac_mutation_catalog_v118_current() RETURNS boolean
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public', 'ops', 'pg_temp'
    as $fn$
declare observed_count integer; observed_digest text; registered_count integer;
  current_grants jsonb := ops.scac_current_grant_contract();
begin
  if not exists (
    select 1 from ops.scac_mutation_registry_version
     where registry_version=current_grants->>'registry_version'
       and registry_digest=current_grants->>'registry_digest'
  ) then return false; end if;
  with recursive connected(oid) as (select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' union select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname<>'carr_ci' and not other.rolsuper),
  runtime_roles as (select r.oid,r.rolname from pg_roles r where r.oid in(select oid from connected) and not r.rolsuper),
  functions as (select p.oid,n.nspname,p.proname,pg_get_function_identity_arguments(p.oid) args,p.prosecdef,p.prokind,p.provolatile,p.proparallel,p.proconfig,p.proacl,p.proowner from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname not in ('pg_catalog','information_schema') and p.prokind in ('f','p')),
  capabilities as (select f.*,acl.grantee,acl.privilege_type,acl.is_grantable from functions f cross join lateral aclexplode(coalesce(f.proacl,acldefault('f',f.proowner))) acl),
  live_observed as (select 'db-function-acl:'||nspname||'.'||proname||'('||args||'):'||coalesce(r.rolname,'public')||':execute' ingress_key,jsonb_build_object('ingress_key','db-function-acl:'||nspname||'.'||proname||'('||args||'):'||coalesce(r.rolname,'public')||':execute','ingress_kind','db_function_acl','signature',nspname||'.'||proname||'('||args||')','security_definer',prosecdef,'function_kind',prokind,'volatility',provolatile,'parallel',proparallel,'config',coalesce(to_jsonb(proconfig),'[]'::jsonb),'grantee',coalesce(r.rolname,'public'),'privilege','execute','grantable',is_grantable) row from capabilities c left join pg_roles r on r.oid=c.grantee where prosecdef and privilege_type='EXECUTE' and grantee<>proowner and (grantee=0 or r.oid in(select oid from runtime_roles))),
  observed as (select * from live_observed union all
    select value->>'ingress_key',value from jsonb_array_elements(coalesce(current_grants->'retired_secdef_execute','[]'::jsonb)))
  select count(*) filter (where row not in (select value from jsonb_array_elements(current_grants->'secdef_execute'))),
    'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(
      jsonb_agg(row order by ingress_key collate "C", ops.scac_canonical_json(row) collate "C")
        filter (where row not in (select value from jsonb_array_elements(current_grants->'secdef_execute'))),
      '[]'::jsonb)),'UTF8'),'sha256'),'hex'),
    count(*) filter (where row in (select value from jsonb_array_elements(current_grants->'secdef_execute')))
    into observed_count,observed_digest,registered_count from observed;
  if registered_count<>jsonb_array_length(current_grants->'secdef_execute') then return false; end if;
  if observed_count<>1267 or observed_digest<>'sha256:587e762e80c6691d4a07c5b35e48a259c7b8bdf885d8595ffd0c14928c657efa' then return false; end if;
  with recursive connected(oid) as (select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' union select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname<>'carr_ci' and not other.rolsuper), runtime_roles as (select r.oid,r.rolname from pg_roles r where r.oid in(select oid from connected) and not r.rolsuper), capabilities as (select n.nspname,c.relname,c.relkind,c.relowner,acl.grantee,acl.privilege_type,acl.is_grantable from pg_class c join pg_namespace n on n.oid=c.relnamespace cross join lateral aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) acl where n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','v','m','f')), observed as (select 'db-relation-acl:'||nspname||'.'||relname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type) ingress_key,jsonb_build_object('ingress_key','db-relation-acl:'||nspname||'.'||relname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type),'ingress_kind','db_relation_acl','relation',nspname||'.'||relname,'relation_kind',relkind,'grantee',coalesce(r.rolname,'public'),'privilege',lower(privilege_type),'grantable',is_grantable) row from capabilities c left join pg_roles r on r.oid=c.grantee where privilege_type in ('INSERT','UPDATE','DELETE','TRUNCATE') and grantee<>relowner and (grantee=0 or r.oid in(select oid from runtime_roles)))
  select count(*),'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(jsonb_agg(row order by ingress_key collate "C", ops.scac_canonical_json(row) collate "C"),'[]'::jsonb)),'UTF8'),'sha256'),'hex') into observed_count,observed_digest from observed;
  if observed_count<>321 or observed_digest<>'sha256:2ece3984f1389d78749dd3055ecf72516f02f97e33d260192e66daff062292e0' then return false; end if;
  with recursive connected(oid) as (select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' union select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname<>'carr_ci' and not other.rolsuper), runtime_roles as (select r.oid,r.rolname from pg_roles r where r.oid in(select oid from connected) and not r.rolsuper), capabilities as (select n.nspname,c.relname,c.relkind,c.relowner,a.attname,acl.grantee,acl.privilege_type,acl.is_grantable from pg_attribute a join pg_class c on c.oid=a.attrelid join pg_namespace n on n.oid=c.relnamespace cross join lateral aclexplode(a.attacl) acl where a.attnum>0 and not a.attisdropped and a.attacl is not null and cardinality(a.attacl)>0 and n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','v','m','f')), observed as (select 'db-column-acl:'||nspname||'.'||relname||'.'||attname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type) ingress_key,jsonb_build_object('ingress_key','db-column-acl:'||nspname||'.'||relname||'.'||attname||':'||coalesce(r.rolname,'public')||':'||lower(privilege_type),'ingress_kind','db_column_acl','relation',nspname||'.'||relname,'relation_kind',relkind,'column',attname,'grantee',coalesce(r.rolname,'public'),'privilege',lower(privilege_type),'grantable',is_grantable) row from capabilities c left join pg_roles r on r.oid=c.grantee where privilege_type in ('INSERT','UPDATE') and grantee<>relowner and (grantee=0 or r.oid in(select oid from runtime_roles)))
  select count(*),'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(jsonb_agg(row order by ingress_key collate "C", ops.scac_canonical_json(row) collate "C"),'[]'::jsonb)),'UTF8'),'sha256'),'hex') into observed_count,observed_digest from observed;
  if observed_count<>15 or observed_digest<>'sha256:f7a33d14ce431ba588cc07dff07fc9fd91a0dcdb41112de6fecd390fb42dc098' then return false; end if;
  with recursive connected(oid) as (
    select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' and not rolcanlogin and not rolsuper union
    select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname~'^carr_' and other.rolname<>'carr_ci' and not other.rolcanlogin and not other.rolsuper
  ), role_rows as (
    select 'db-role:'||r.rolname ingress_key,jsonb_build_object('ingress_key','db-role:'||r.rolname,'row_kind','role','role',r.rolname,'login',r.rolcanlogin,'inherit',r.rolinherit,'superuser',r.rolsuper,'create_role',r.rolcreaterole,'create_db',r.rolcreatedb,'replication',r.rolreplication,'bypass_rls',r.rolbypassrls) row from pg_roles r where r.oid in(select oid from connected)
  ), membership_rows as (
    select 'db-role-membership:'||role.rolname||':'||member.rolname ingress_key,jsonb_build_object('ingress_key','db-role-membership:'||role.rolname||':'||member.rolname,'row_kind','membership','role',role.rolname,'member',member.rolname,'admin_option',m.admin_option,'inherit_option',m.inherit_option,'set_option',m.set_option) row from pg_auth_members m join pg_roles role on role.oid=m.roleid join pg_roles member on member.oid=m.member where m.roleid in(select oid from connected) and m.member in(select oid from connected)
  ), ownership_rows as (
    select 'db-function-owner:'||n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||'):'||owner.rolname ingress_key,jsonb_build_object('ingress_key','db-function-owner:'||n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||'):'||owner.rolname,'row_kind','function_owner','signature',n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||')','owner',owner.rolname) row from pg_proc p join pg_namespace n on n.oid=p.pronamespace join pg_roles owner on owner.oid=p.proowner where n.nspname not in ('pg_catalog','information_schema') and p.prokind in ('f','p') and owner.oid in(select oid from connected) and not owner.rolsuper and owner.rolname<>'neondb_owner' union all
    select 'db-relation-owner:'||n.nspname||'.'||c.relname||':'||owner.rolname,jsonb_build_object('ingress_key','db-relation-owner:'||n.nspname||'.'||c.relname||':'||owner.rolname,'row_kind','relation_owner','relation',n.nspname||'.'||c.relname,'relation_kind',c.relkind,'owner',owner.rolname) row from pg_class c join pg_namespace n on n.oid=c.relnamespace join pg_roles owner on owner.oid=c.relowner where n.nspname not in ('pg_catalog','information_schema') and c.relkind in ('r','p','v','m','f') and owner.oid in(select oid from connected) and not owner.rolsuper and owner.rolname<>'neondb_owner'
  ), observed as (select * from role_rows union all select * from membership_rows union all select * from ownership_rows)
  select count(*),'sha256:'||encode(public.digest(convert_to(ops.scac_canonical_json(coalesce(jsonb_agg(row order by ingress_key collate "C", ops.scac_canonical_json(row) collate "C"),'[]'::jsonb)),'UTF8'),'sha256'),'hex') into observed_count,observed_digest from observed;
  if exists (select 1 from pg_auth_members m join pg_roles g on g.oid=m.roleid join pg_roles mem on mem.oid=m.member where mem.rolname~'^carr_' and (g.rolsuper or g.rolname~'^(neon_|pg_)')) then return false; end if;
  return observed_count=13 and observed_digest='sha256:93724fe71ed216afac9b4bf48eee500e693de057cae0b59f59ff8e68cc6a2bc0';
end $fn$;
