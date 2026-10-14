-- 0881_allow_triaged_work_request_supersession.sql
-- rollback: forward-only — withdrawing this backward transition would invalidate recorded successor history.
-- expand-contract: expand — preserves captured behavior and rejects new successor lineage cycles.
-- lock-review: validating the replacement shape constraint scans ops.work_request; no table rewrite, existing row shapes remain valid.
-- WR-000221 supersede-triaged repair. No actual request rows are touched.

-- Permit a superseded triaged row to retain its recorded triage fields.
alter table ops.work_request drop constraint work_request_sourced_capture_shape;
alter table ops.work_request add constraint work_request_sourced_capture_shape check (

  ((capture_idempotency_key IS NULL) AND (organization_tenant_id IS NULL) AND (doctrine_section_id IS NULL) AND (doctrine_revision_id IS NULL) AND (sourced_capture_sequence IS NULL) AND (triage_classification IS NULL) AND (triaged_by_actor_id IS NULL) AND (triaged_at IS NULL) AND (program_key IS NULL) AND (program_ordinal IS NULL))
  OR
  ((capture_idempotency_key IS NULL) AND (NOT (organization_tenant_id IS DISTINCT FROM 'carr-internal'::text)) AND (doctrine_section_id IS NULL) AND (doctrine_revision_id IS NULL) AND (sourced_capture_sequence IS NULL) AND (triage_classification IS NULL) AND (triaged_by_actor_id IS NULL) AND (triaged_at IS NULL) AND (program_key = ANY (ARRAY['carr-ai-engineering-suite-v1'::text, 'carr-system-integrity-elimination-v1'::text])) AND (program_ordinal IS NOT NULL) AND (program_ordinal > 0) AND (NOT (requester_actor IS DISTINCT FROM 'joe'::text)) AND (NOT (owner_actor IS DISTINCT FROM 'joe'::text)))
  OR
  ((capture_idempotency_key IS NOT NULL) AND (NOT (organization_tenant_id IS DISTINCT FROM 'carr-internal'::text)) AND (doctrine_section_id IS NOT NULL) AND (doctrine_revision_id IS NOT NULL) AND (sourced_capture_sequence IS NOT NULL) AND (program_key IS NULL) AND (program_ordinal IS NULL) AND (origin_ref IS NOT NULL) AND (origin_ref ~ '^doctrine:[a-z0-9][a-z0-9-]*#[a-z0-9][a-z0-9-]*$'::text) AND (
      ((state = 'captured'::text) AND (triage_classification IS NULL) AND (triaged_by_actor_id IS NULL) AND (triaged_at IS NULL))
      OR
      ((state = ANY (ARRAY['triaged'::text, 'ready'::text])) AND (triage_classification = ANY (ARRAY['operational'::text, 'needs_judgment'::text, 'safety_review'::text])) AND (triaged_by_actor_id IS NOT NULL) AND (triaged_at IS NOT NULL))
      OR
      ((state = 'declined'::text)
        AND (triage_classification IS NULL) AND (triaged_by_actor_id IS NULL) AND (triaged_at IS NULL)
        AND (exit_reason IS NOT NULL) AND (btrim(exit_reason) <> ''::text)
        AND (closed_at IS NOT NULL) AND (superseded_by IS NULL))
      OR
      ((state = 'superseded'::text)
        AND ((triage_classification IS NULL AND triaged_by_actor_id IS NULL AND triaged_at IS NULL)
          OR (triage_classification = ANY (ARRAY['operational'::text, 'needs_judgment'::text, 'safety_review'::text])
              AND triaged_by_actor_id IS NOT NULL AND triaged_at IS NOT NULL))
        AND (exit_reason IS NOT NULL) AND (btrim(exit_reason) <> ''::text)
        AND (closed_at IS NOT NULL) AND (superseded_by IS NOT NULL))
  ))
);

CREATE OR REPLACE FUNCTION ops.sourced_work_request_is_immutable() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'ops'
    AS $$
begin
  -- INSERT WAS NEVER COVERED. This trigger was BEFORE UPDATE only, so a sourced
  -- row could be INSERTed already terminal: no receipt, no version history, no
  -- pointer check, nothing. Every other INSERT trigger on this table returns
  -- early for these states, so the constraint alone stood between a fabricated
  -- withdrawal and the table. A sourced request is born captured or not at all.
  if tg_op = 'INSERT' then
    if new.capture_idempotency_key is not null and new.state <> 'captured' then
      raise exception 'a sourced Work Request must be INSERTed in state captured, not %', new.state;
    end if;
    return new;
  end if;

  if old.capture_idempotency_key is null then
    return new;
  end if;

  if old.state = 'captured'
     and new.state = 'triaged'
     and new.version = old.version + 1
     and (to_jsonb(new) - array['state','triage_classification','triaged_by_actor_id','triaged_at','version','updated_at'])
           is not distinct from
         (to_jsonb(old) - array['state','triage_classification','triaged_by_actor_id','triaged_at','version','updated_at'])
     and exists (
       select 1 from ops.work_request_triage_receipt r
        where r.work_request_id = old.id and r.base_version = old.version
          and r.result_version = new.version and r.classification = new.triage_classification
          and r.triaged_by_actor_id = new.triaged_by_actor_id and r.triaged_at = new.triaged_at
     ) then
    return new;
  end if;

  if old.state = 'triaged'
     and new.state = 'triaged'
     and new.version = old.version + 1
     and (to_jsonb(new) - array['shape_disposition','shape_fixed_surface_ref','shape_rationale','shape_decided_by_actor_id','shape_decided_at','version','updated_at'])
           is not distinct from
         (to_jsonb(old) - array['shape_disposition','shape_fixed_surface_ref','shape_rationale','shape_decided_by_actor_id','shape_decided_at','version','updated_at'])
     and exists (
       select 1 from ops.sourced_work_request_shape_disposition_receipt r
        where r.work_request_id = old.id and r.base_version = old.version and r.result_version = new.version
          and (new.shape_disposition,new.shape_fixed_surface_ref,new.shape_rationale,new.shape_decided_by_actor_id,new.shape_decided_at)
             is not distinct from
             (r.disposition,r.fixed_surface_ref,r.rationale,r.decided_by_actor_id,r.decided_at)
     ) then
    return new;
  end if;

  if old.state = 'triaged'
     and new.state = 'ready'
     and new.version = old.version + 1
     and (to_jsonb(new) - array['state','version','updated_at','shape_disposition','shape_fixed_surface_ref','shape_rationale','shape_decided_by_actor_id','shape_decided_at'])
           is not distinct from
         (to_jsonb(old) - array['state','version','updated_at','shape_disposition','shape_fixed_surface_ref','shape_rationale','shape_decided_by_actor_id','shape_decided_at'])
     and exists (
       select 1
         from ops.sourced_work_request_plan_acceptance_receipt ar
         join ops.sourced_work_request_plan p on p.id = ar.plan_id
         left join ops.sourced_work_request_plan_shape_binding_receipt b
           on b.plan_acceptance_receipt_id = ar.id
        where ar.work_request_id = old.id and ar.base_version = old.version
          and ar.result_version = new.version and ar.plan_hash = p.plan_hash
          and (
            (b.id is null
             and new.shape_disposition = 'not_required'
             and new.shape_fixed_surface_ref = ar.shape_fixed_surface_ref
             and new.shape_fixed_surface_ref = ('sourced-plan:' || p.plan_ref || '#' || p.plan_hash)
             and new.shape_rationale = ar.shape_rationale
             and new.shape_rationale = ('Accepted immutable plan ' || p.plan_ref || ' for ' || p.runbook_ref || ' at sha256:' || p.runbook_content_hash)
             and new.shape_decided_by_actor_id = ar.accepted_by_actor_id
             and new.shape_decided_at = ar.accepted_at)
            or
            (b.id is not null
             and b.work_request_id = old.id
             and (new.shape_disposition,new.shape_fixed_surface_ref,new.shape_rationale,new.shape_decided_by_actor_id,new.shape_decided_at)
                 is not distinct from
                 (b.disposition,b.fixed_surface_ref,b.rationale,b.decided_by_actor_id,b.decided_at))
          )
     ) then
    return new;
  end if;

  -- Do not strand an existing predecessor by declining its successor.
  -- Supersession locks both rows before changing the source, so this check sees
  -- either the committed incoming edge or the successor's earlier decline.
  if old.state = 'captured' and new.state = 'declined'
     and exists (select 1 from ops.work_request predecessor
                  where predecessor.superseded_by = old.id) then
    raise exception 'a Work Request already named as a successor cannot be declined';
  end if;

  -- Captured rows may be declined or superseded; triaged rows may be
  -- superseded while preserving their triage and shape evidence.
  if ((old.state = 'captured' and new.state in ('declined','superseded'))
      or (old.state = 'triaged' and new.state = 'superseded'))
     and new.version = old.version + 1
     and (to_jsonb(new) - array['state','exit_reason','closed_at','superseded_by','version','updated_at'])
           is not distinct from
         (to_jsonb(old) - array['state','exit_reason','closed_at','superseded_by','version','updated_at'])
     and exists (
       select 1 from ops.work_request_withdrawal_receipt r
        where r.work_request_id = old.id and r.base_version = old.version
          and r.result_version = new.version and r.final_state = new.state
          and r.exit_reason = new.exit_reason
          and r.superseded_by is not distinct from new.superseded_by
     ) then
    return new;
  end if;

  raise exception 'sourced Program 6 Work Requests permit only receipt-backed captured-to-triaged, triaged shape-disposition, triaged-to-ready, captured-to-withdrawn, or triaged-to-superseded transitions';
end;
$$;

drop function ops.supersede_sourced_work_request(text,integer,text,text,text,uuid);

create function ops.supersede_sourced_work_request(
  p_work_request text, p_base_version integer, p_exit_reason text,
  p_superseded_by text, p_actor_slug text, p_idempotency_key uuid)
returns table(ref text, state text, version integer, exit_reason text,
              closed_at timestamp with time zone, superseded_by_ref text, previous_state text)
language plpgsql security definer
set search_path to 'pg_catalog', 'ops' as $fn$
declare
  v_id uuid;
  v_succ uuid;
  v_succ_state text;
  v_succ_sourced boolean;
  v_succ_tenant text;
  v_previous_state text;
  v_request_tenant text;
  v_locked_tenant text;
  v_locked_version integer;
  v_has_cycle boolean;
  v_actor uuid;
  v_now timestamp with time zone := now();
begin
  if p_exit_reason is null or btrim(p_exit_reason) = '' then
    raise exception 'a withdrawal must record why';
  end if;
  select a.id into v_actor from public.actor a where a.slug = p_actor_slug;
  if v_actor is null then
    raise exception 'unknown actor %', p_actor_slug;
  end if;

  -- Identify the tenant without taking a row lock, then serialize graph edits
  -- for that tenant before taking any row locks. A write-volume-independent lock
  -- is needed because disjoint endpoint pairs can still complete a longer cycle.
  select w.id, w.organization_tenant_id into v_id, v_request_tenant
    from ops.work_request w
   where w.ref = p_work_request and w.capture_idempotency_key is not null;
  if v_id is null then
    raise exception 'no sourced Work Request %', p_work_request;
  end if;
  perform pg_advisory_xact_lock(hashtextextended(
    'ops.work_request_supersession_graph:' || v_request_tenant, 0));

  select w.id into v_succ from ops.work_request w where w.ref = p_superseded_by;
  if v_succ is null then
    raise exception 'no Work Request % to supersede it', p_superseded_by;
  end if;

  -- Lock both endpoints in a stable UUID order. This also serializes a target's
  -- concurrent decline; the trigger refuses that decline if this edge commits.
  perform 1 from ops.work_request w
   where w.id in (v_id, v_succ)
   order by w.id
   for update;

  -- Recheck source CAS and target properties only after both locks are held.
  select w.state, w.version, w.organization_tenant_id
    into v_previous_state, v_locked_version, v_locked_tenant
    from ops.work_request w where w.id = v_id;
  if v_previous_state not in ('captured','triaged')
     or v_locked_version is distinct from p_base_version
     or v_locked_tenant is distinct from v_request_tenant then
    raise exception 'no captured or triaged sourced Work Request % at version %', p_work_request, p_base_version;
  end if;

  select w.state, (w.capture_idempotency_key is not null), w.organization_tenant_id
    into v_succ_state, v_succ_sourced, v_succ_tenant
    from ops.work_request w where w.id = v_succ;
  if v_succ_state is null then
    raise exception 'no Work Request % to supersede it', p_superseded_by;
  end if;
  -- Successor guards the foreign key does not give.
  if v_succ = v_id then
    raise exception 'a Work Request cannot supersede itself';
  end if;
  if v_succ_state in ('declined','superseded') then
    raise exception 'the successor % is itself withdrawn; superseding into a dead row loses the trail', p_superseded_by;
  end if;
  if v_succ_tenant is distinct from v_request_tenant then
    raise exception 'the successor % is not in the same organization tenant', p_superseded_by;
  end if;
  if not v_succ_sourced then
    raise exception 'the successor % is not a sourced Work Request', p_superseded_by;
  end if;

  -- The superseded_by relation is a lineage graph, so adding an edge must not
  -- create a cycle of any length. The tenant advisory lock makes this traversal
  -- stable against other supersede operations in the graph.
  with recursive successor_chain(id, successor_id, path, cycle) as (
    select w.id, w.superseded_by, array[w.id], false
      from ops.work_request w where w.id = v_succ
    union all
    select next.id, next.superseded_by, chain.path || next.id,
           next.id = any(chain.path)
      from successor_chain chain
      join ops.work_request next on next.id = chain.successor_id
     where chain.successor_id is not null and not chain.cycle
       and next.organization_tenant_id = v_request_tenant
  )
  select exists (select 1 from successor_chain where id = v_id or cycle)
    into v_has_cycle;
  if v_has_cycle then
    raise exception 'that would make a supersession cycle';
  end if;

  insert into ops.work_request_withdrawal_receipt
    (work_request_id, idempotency_key, base_version, result_version, final_state,
     exit_reason, superseded_by, withdrawn_by_actor_id, withdrawn_at)
  values (v_id, p_idempotency_key, p_base_version, p_base_version + 1, 'superseded',
          p_exit_reason, v_succ, v_actor, v_now)
  on conflict (idempotency_key) do nothing;

  update ops.work_request w
     set state = 'superseded', exit_reason = p_exit_reason, closed_at = v_now,
         superseded_by = v_succ, version = w.version + 1, updated_at = v_now
   where w.id = v_id;

  return query
    select w.ref, w.state, w.version, w.exit_reason, w.closed_at, s.ref, v_previous_state
      from ops.work_request w
      left join ops.work_request s on s.id = w.superseded_by
     where w.id = v_id;
end;
$fn$;

revoke all on function ops.supersede_sourced_work_request(text,integer,text,text,text,uuid) from public;
grant execute on function ops.supersede_sourced_work_request(text,integer,text,text,text,uuid) to carr_writer;
