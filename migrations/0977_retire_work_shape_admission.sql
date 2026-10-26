-- rollback: forward-only; restore implementation-shape admission only in a new migration.
-- expand-contract: contract; retire Work Shape ceremony while preserving historical evidence.
-- lock-review: replace three existing functions; no table rewrite or data deletion.
SET LOCAL lock_timeout = '5s';

CREATE OR REPLACE FUNCTION ops.work_request_shape_gate() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
begin
  if tg_op = 'INSERT' then
    if new.state in ('claimed','in_progress','verification','awaiting_release','released','confirmed_closed') then
      raise exception 'new Work Request cannot enter implementation directly';
    end if;
    return new;
  end if;

  if (old.state not in ('captured','triaged','ready')
      or new.state in ('claimed','in_progress','verification','awaiting_release','released','confirmed_closed'))
     and (new.shape_disposition,
          new.shape_fixed_surface_ref,
          new.shape_rationale,
          new.shape_decided_by_actor_id,
          new.shape_decided_at)
         is distinct from
         (old.shape_disposition,
          old.shape_fixed_surface_ref,
          old.shape_rationale,
          old.shape_decided_by_actor_id,
          old.shape_decided_at) then
    raise exception 'implementation shape disposition is frozen after claim';
  end if;

  return new;
end;
$$;

CREATE OR REPLACE FUNCTION ops.propose_sourced_work_request_plan(p_work_request text, p_base_version integer, p_scope_summary text, p_runbook_ref text, p_dependency_refs jsonb, p_recovery_ref text, p_observability_ref text, p_caps jsonb, p_idempotency_key uuid) RETURNS TABLE(plan_id uuid, plan_ref text, plan_hash text, work_request_id uuid, ref text, state text, version integer, runbook_ref text, runbook_revision_id uuid, runbook_content_hash text, scope_summary text, replayed boolean)
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'ops', 'pg_temp'
    AS $_$
declare
  w ops.work_request%rowtype; p ops.sourced_work_request_plan%rowtype;
  rb_section_id uuid; rb_revision_id uuid; rb_content_hash text;
  normalized_scope text; normalized_runbook text; normalized_recovery text; normalized_observability text;
  next_plan_version integer; canonical_preimage jsonb; canonical_hash text;
begin
  normalized_scope := btrim(p_scope_summary); normalized_runbook := btrim(p_runbook_ref);
  normalized_recovery := btrim(p_recovery_ref); normalized_observability := btrim(p_observability_ref);
  if p_idempotency_key is null or p_base_version is null or p_base_version < 1
     or coalesce(normalized_scope,'') = '' or char_length(normalized_scope) > 1000
     or coalesce(normalized_runbook,'') !~ '^doctrine:runbook#[a-z0-9][a-z0-9-]*$'
     or coalesce(normalized_recovery,'') !~ '^safe:[a-z0-9][a-z0-9:_./-]*$' or char_length(normalized_recovery) > 300
     or coalesce(normalized_observability,'') !~ '^safe:[a-z0-9][a-z0-9:_./-]*$' or char_length(normalized_observability) > 300 then
    raise exception 'invalid bounded sourced plan';
  end if;
  if jsonb_typeof(p_dependency_refs) is distinct from 'array' or jsonb_array_length(p_dependency_refs) > 12
     or exists (select 1 from jsonb_array_elements(p_dependency_refs) v where jsonb_typeof(v) <> 'string' or v #>> '{}' !~ '^safe:[a-z0-9][a-z0-9:_./-]*$' or char_length(v #>> '{}') > 300)
     or exists (select v #>> '{}' from jsonb_array_elements(p_dependency_refs) v group by v #>> '{}' having count(*) > 1) then
    raise exception 'invalid bounded sourced plan dependency references';
  end if;
  if jsonb_typeof(p_caps) is distinct from 'object'
     or ((select array_agg(k order by k) from jsonb_object_keys(p_caps) k)
           is distinct from array['max_duration_minutes','max_steps']::text[]
         and (select array_agg(k order by k) from jsonb_object_keys(p_caps) k)
           is distinct from array['max_duration_minutes','max_steps','source_merge']::text[])
     or coalesce(p_caps->>'max_steps','') !~ '^[0-9]+$' or coalesce(p_caps->>'max_duration_minutes','') !~ '^[0-9]+$'
     or (p_caps->>'max_steps')::integer not between 1 and 20 or (p_caps->>'max_duration_minutes')::integer not between 1 and 120
     or (p_caps ? 'source_merge' and not coalesce(ops.source_merge_scope_valid(p_caps->'source_merge'),false)) then
    raise exception 'invalid bounded sourced plan caps';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('program6-plan-proposal:' || p_idempotency_key, 0));
  select x.* into p from ops.sourced_work_request_plan x where x.idempotency_key=p_idempotency_key for share;
  if found then
    select x.* into w from ops.work_request x where x.id=p.work_request_id for share;
    if not found or w.ref is distinct from p_work_request or p.work_request_version is distinct from p_base_version
       or p.scope_summary is distinct from normalized_scope or p.runbook_ref is distinct from normalized_runbook
       or p.dependency_refs is distinct from p_dependency_refs or p.recovery_ref is distinct from normalized_recovery
       or p.observability_ref is distinct from normalized_observability or p.caps is distinct from p_caps then
      raise exception 'idempotency key already names a different sourced plan proposal';
    end if;
    return query select p.id,p.plan_ref,p.plan_hash,w.id,w.ref,'triaged'::text,p.work_request_version,
      p.runbook_ref,p.runbook_revision_id,'sha256:' || p.runbook_content_hash,p.scope_summary,true;
    return;
  end if;

  select x.* into w from ops.work_request x where x.ref=p_work_request for update;
  if not found or w.state is distinct from 'triaged' or w.version is distinct from p_base_version
     or w.capture_idempotency_key is null or w.organization_tenant_id is distinct from 'carr-internal' then
    raise exception 'exact current triaged sourced Work Request required';
  end if;
  perform 1 from public.doctrine_document source_document join public.doctrine_section source_section on source_section.document_id=source_document.id
    join public.doctrine_revision source_revision on source_revision.id=source_section.current_revision_id and source_revision.section_id=source_section.id
   where source_document.visibility='shared' and source_section.status='active' and source_section.id=w.doctrine_section_id
     and source_revision.id=w.doctrine_revision_id and source_revision.content_hash ~ '^[0-9a-f]{64}$'
     and encode(public.digest(source_revision.plain_text,'sha256'),'hex')=source_revision.content_hash
     and source_revision.body=jsonb_build_object('text',source_revision.plain_text)
   for share of source_document,source_section,source_revision;
  if not found then raise exception 'sourced Work Request evidence is no longer exact, current, active, and shared'; end if;
  select s.id,r.id,r.content_hash into rb_section_id,rb_revision_id,rb_content_hash
    from public.doctrine_document d join public.doctrine_section s on s.document_id=d.id
    join public.doctrine_revision r on r.id=s.current_revision_id and r.section_id=s.id
   where d.slug='runbook' and d.visibility='shared' and s.status='active'
     and ('doctrine:' || d.slug || '#' || s.section_key)=normalized_runbook and r.content_hash ~ '^[0-9a-f]{64}$'
     and encode(public.digest(r.plain_text,'sha256'),'hex')=r.content_hash and r.body=jsonb_build_object('text',r.plain_text)
   for share of d,s,r;
  if not found then raise exception 'runbook must be an exact current active shared doctrine revision'; end if;
  select coalesce(max(x.plan_version),0)+1 into next_plan_version from ops.sourced_work_request_plan x where x.work_request_id=w.id;
  canonical_preimage := ops.sourced_work_request_plan_preimage(w.id,normalized_scope,normalized_runbook,rb_section_id,rb_revision_id,rb_content_hash,p_dependency_refs,normalized_recovery,normalized_observability,p_caps);
  canonical_hash := ops.sourced_work_request_plan_digest(canonical_preimage);
  if exists (select 1 from ops.sourced_work_request_plan x where x.work_request_id=w.id and x.plan_hash=canonical_hash) then
    raise exception 'the exact sourced plan already exists under a different idempotency key';
  end if;
  insert into ops.sourced_work_request_plan (work_request_id,plan_version,idempotency_key,work_request_version,preimage,scope_summary,runbook_ref,runbook_section_id,runbook_revision_id,runbook_content_hash,dependency_refs,recovery_ref,observability_ref,caps,plan_hash,plan_ref)
  values (w.id,next_plan_version,p_idempotency_key,w.version,canonical_preimage,normalized_scope,normalized_runbook,rb_section_id,rb_revision_id,rb_content_hash,p_dependency_refs,normalized_recovery,normalized_observability,p_caps,canonical_hash,'PLAN-' || substr(canonical_hash,8,12) || '-v' || next_plan_version)
  returning * into p;
  return query select p.id,p.plan_ref,p.plan_hash,w.id,w.ref,w.state,w.version,p.runbook_ref,p.runbook_revision_id,'sha256:' || p.runbook_content_hash,p.scope_summary,false;
end;
$_$;

CREATE OR REPLACE FUNCTION ops.accept_sourced_work_request_plan(p_work_request text, p_base_version integer, p_plan_hash text, p_idempotency_key uuid) RETURNS TABLE(work_request_id uuid, ref text, state text, version integer, plan_id uuid, plan_ref text, plan_hash text, accepted_by_actor_slug text, accepted_at timestamp with time zone, shape_disposition text, shape_fixed_surface_ref text, replayed boolean)
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'ops', 'pg_temp'
    AS $_$
declare
  actor_slug text; a public.actor%rowtype; w ops.work_request%rowtype;
  p ops.sourced_work_request_plan%rowtype;
  ar ops.sourced_work_request_plan_acceptance_receipt%rowtype;
  canonical_preimage jsonb; canonical_hash text; fixed_surface text; rationale text;
  preserve_shape boolean := false;
  applied_disposition text; applied_fixed_surface text; applied_rationale text;
  applied_actor_id uuid; applied_at timestamptz;
begin
  if p_idempotency_key is null or p_base_version is null or p_base_version < 1
     or coalesce(p_plan_hash,'') !~ '^sha256:[0-9a-f]{64}$' then
    raise exception 'exact sourced plan acceptance inputs required';
  end if;
  actor_slug := ops.authority_actor_slug();
  select x.* into a from public.actor x
   where x.slug = actor_slug and x.active and x.kind = 'human' for share;
  if not found then raise exception 'authority session user is not an active human actor'; end if;

  perform pg_advisory_xact_lock(hashtextextended('program6-plan-acceptance:' || p_idempotency_key, 0));
  select x.* into ar from ops.sourced_work_request_plan_acceptance_receipt x
   where x.idempotency_key = p_idempotency_key for share;
  if found then
    select x.* into p from ops.sourced_work_request_plan x where x.id = ar.plan_id for share;
    select x.* into w from ops.work_request x where x.id = ar.work_request_id for share;
    if not found or w.ref is distinct from p_work_request
       or ar.base_version is distinct from p_base_version or ar.plan_hash is distinct from p_plan_hash
       or ar.accepted_by_actor_id is distinct from a.id or p.id is distinct from ar.plan_id
       or p.plan_hash is distinct from ar.plan_hash or w.state is distinct from 'ready'
       or w.version is distinct from ar.result_version
       or not exists (
         select 1 from ops.sourced_work_request_plan_shape_binding_receipt b
          where b.plan_acceptance_receipt_id = ar.id and b.work_request_id = w.id
            and (w.shape_disposition,w.shape_fixed_surface_ref,w.shape_rationale,w.shape_decided_by_actor_id,w.shape_decided_at)
                is not distinct from (b.disposition,b.fixed_surface_ref,b.rationale,b.decided_by_actor_id,b.decided_at)
         union all
         select 1 where not exists (select 1 from ops.sourced_work_request_plan_shape_binding_receipt b where b.plan_acceptance_receipt_id = ar.id)
           and w.shape_disposition = 'not_required' and w.shape_fixed_surface_ref is not distinct from ar.shape_fixed_surface_ref
       ) then
      raise exception 'idempotency key already names a different sourced plan acceptance';
    end if;
    return query select w.id,w.ref,w.state,w.version,p.id,p.plan_ref,p.plan_hash,
      a.slug,ar.accepted_at,w.shape_disposition,w.shape_fixed_surface_ref,true;
    return;
  end if;

  select x.* into w from ops.work_request x where x.ref = p_work_request for update;
  if not found then raise exception 'exact sourced Work Request not found'; end if;
  select x.* into p from ops.sourced_work_request_plan x
   where x.work_request_id = w.id and x.plan_hash = p_plan_hash for share;
  if not found or w.state is distinct from 'triaged' or w.version is distinct from p_base_version
     or p.work_request_version is distinct from p_base_version or w.capture_idempotency_key is null then
    raise exception 'exact current triaged sourced plan/version required';
  end if;

  preserve_shape := w.shape_disposition is not null;
  if preserve_shape and not exists (
    select 1 from ops.sourced_work_request_shape_disposition_lineage(w.id) e
     where (w.shape_disposition,w.shape_fixed_surface_ref,w.shape_rationale,w.shape_decided_by_actor_id,w.shape_decided_at)
           is not distinct from (e.disposition,e.fixed_surface_ref,e.rationale,e.decided_by_actor_id,e.decided_at)
  ) then
    raise exception 'sourced ready-plan shape metadata must match its historical receipt';
  end if;

  perform 1 from public.doctrine_document d join public.doctrine_section s on s.document_id = d.id
    join public.doctrine_revision r on r.id = s.current_revision_id and r.section_id = s.id
   where d.slug = 'runbook' and d.visibility = 'shared' and s.status = 'active'
     and s.id = p.runbook_section_id and r.id = p.runbook_revision_id and r.content_hash = p.runbook_content_hash
     and ('doctrine:' || d.slug || '#' || s.section_key) = p.runbook_ref
     and encode(public.digest(r.plain_text,'sha256'),'hex') = r.content_hash and r.body = jsonb_build_object('text',r.plain_text)
   for share of d,s,r;
  if not found then raise exception 'accepted sourced plan runbook is no longer current'; end if;
  perform 1 from public.doctrine_document source_document join public.doctrine_section source_section on source_section.document_id = source_document.id
    join public.doctrine_revision source_revision on source_revision.id = source_section.current_revision_id and source_revision.section_id = source_section.id
   where source_document.visibility = 'shared' and source_section.status = 'active'
     and source_section.id = w.doctrine_section_id and source_revision.id = w.doctrine_revision_id
     and source_revision.content_hash ~ '^[0-9a-f]{64}$'
     and encode(public.digest(source_revision.plain_text,'sha256'),'hex') = source_revision.content_hash
     and source_revision.body = jsonb_build_object('text',source_revision.plain_text)
   for share of source_document,source_section,source_revision;
  if not found then raise exception 'accepted sourced plan source evidence is no longer current'; end if;

  canonical_preimage := ops.sourced_work_request_plan_preimage(w.id,p.scope_summary,p.runbook_ref,p.runbook_section_id,p.runbook_revision_id,p.runbook_content_hash,p.dependency_refs,p.recovery_ref,p.observability_ref,p.caps);
  canonical_hash := ops.sourced_work_request_plan_digest(canonical_preimage);
  if canonical_preimage is distinct from p.preimage or canonical_hash is distinct from p.plan_hash or canonical_hash is distinct from p_plan_hash then
    raise exception 'sourced plan preimage is stale or does not match its exact hash';
  end if;

  fixed_surface := 'sourced-plan:' || p.plan_ref || '#' || p.plan_hash;
  rationale := 'Accepted immutable plan ' || p.plan_ref || ' for ' || p.runbook_ref || ' at sha256:' || p.runbook_content_hash;
  insert into ops.sourced_work_request_plan_acceptance_receipt
    (work_request_id,plan_id,idempotency_key,base_version,plan_hash,accepted_by_actor_id,result_version,shape_fixed_surface_ref,shape_rationale)
  values (w.id,p.id,p_idempotency_key,p_base_version,p_plan_hash,a.id,w.version + 1,fixed_surface,rationale)
  returning * into ar;

  if preserve_shape then
    insert into ops.sourced_work_request_plan_shape_binding_receipt
      (plan_acceptance_receipt_id,work_request_id,disposition,fixed_surface_ref,rationale,decided_by_actor_id,decided_at)
    values (ar.id,w.id,w.shape_disposition,w.shape_fixed_surface_ref,w.shape_rationale,w.shape_decided_by_actor_id,w.shape_decided_at);
    applied_disposition := w.shape_disposition; applied_fixed_surface := w.shape_fixed_surface_ref;
    applied_rationale := w.shape_rationale; applied_actor_id := w.shape_decided_by_actor_id; applied_at := w.shape_decided_at;
  else
    applied_disposition := 'not_required'; applied_fixed_surface := ar.shape_fixed_surface_ref;
    applied_rationale := ar.shape_rationale; applied_actor_id := a.id; applied_at := ar.accepted_at;
  end if;

  update ops.work_request x set state='ready',version=ar.result_version,updated_at=now(),
    shape_disposition=applied_disposition,shape_fixed_surface_ref=applied_fixed_surface,
    shape_rationale=applied_rationale,shape_decided_by_actor_id=applied_actor_id,shape_decided_at=applied_at
   where x.id=w.id;
  select x.* into w from ops.work_request x where x.id=w.id;
  return query select w.id,w.ref,w.state,w.version,p.id,p.plan_ref,p.plan_hash,
    a.slug,ar.accepted_at,w.shape_disposition,w.shape_fixed_surface_ref,false;
end;
$_$;

COMMENT ON COLUMN ops.work_request.shape_disposition IS 'Historical implementation-shape metadata. Retained for receipt lineage and Work Inventory reads; no shape decision or analysis revision is required for readiness or implementation.';
