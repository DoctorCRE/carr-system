-- rollback: forward-only; declined triaged rows retain intake history that the previous check rejects
-- lock-review: replace one validated CHECK on the small ops.work_request table; brief ACCESS EXCLUSIVE lock
-- 0910_decline_triaged_work_request.sql
-- Joe authorized declining captured or triaged requests, including answered work.
-- Preserve downstream history; supersession keeps its separate evidence rules.

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
      ((state = ANY (ARRAY['declined'::text, 'superseded'::text]))
        AND (((triage_classification IS NULL) AND (triaged_by_actor_id IS NULL) AND (triaged_at IS NULL))
             OR ((state = ANY (ARRAY['declined'::text, 'superseded'::text]))
                 AND (triage_classification = ANY (ARRAY['operational'::text, 'needs_judgment'::text, 'safety_review'::text]))
                 AND (triaged_by_actor_id IS NOT NULL) AND (triaged_at IS NOT NULL)))
        AND (exit_reason IS NOT NULL) AND (btrim(exit_reason) <> ''::text)
        AND (closed_at IS NOT NULL)
        AND (((state = 'declined'::text) AND (superseded_by IS NULL))
             OR ((state = 'superseded'::text) AND (superseded_by IS NOT NULL))))
  ))
);

do $work_request_decline$
declare
  v_definition text;
  v_marker constant text := $marker$  raise exception 'sourced Program 6 Work Requests permit only receipt-backed captured-to-triaged, triaged shape-disposition, triaged-to-ready, captured-to-withdrawn, or triaged-to-superseded transitions';$marker$;
  v_branch constant text := $branch$  if old.state = 'triaged'
     and new.state = 'declined'
     and new.version = old.version + 1
     and (to_jsonb(new) - array['state','exit_reason','closed_at','version','updated_at'])
           is not distinct from
         (to_jsonb(old) - array['state','exit_reason','closed_at','version','updated_at'])
     and exists (
       select 1 from ops.work_request_withdrawal_receipt r
        where r.work_request_id = old.id and r.base_version = old.version
          and r.result_version = new.version and r.final_state = 'declined'
          and r.exit_reason = new.exit_reason and r.superseded_by is null
     ) then
    return new;
  end if;

  raise exception 'sourced Program 6 Work Requests permit only receipt-backed captured-to-triaged, triaged shape-disposition, triaged-to-ready, captured-to-withdrawn, or triaged-to-withdrawn transitions';$branch$;
begin
  select pg_get_functiondef('ops.sourced_work_request_is_immutable()'::regprocedure)
    into v_definition;
  if (length(v_definition)-length(replace(v_definition,v_marker,'')))/length(v_marker)<>1 then
    raise exception '0910 refused: sourced Work Request lifecycle predecessor shape drifted';
  end if;
  execute replace(v_definition,v_marker,v_branch);
end $work_request_decline$;

create or replace function ops.decline_sourced_work_request(
  p_work_request text, p_base_version integer, p_exit_reason text,
  p_actor_slug text, p_idempotency_key uuid)
returns table(ref text, state text, version integer, exit_reason text, closed_at timestamp with time zone)
language plpgsql security definer
set search_path to 'pg_catalog', 'ops', 'pg_temp' as $fn$
declare
  v_id uuid;
  v_actor public.actor%rowtype;
  v_state text;
  v_version integer;
  v_sourced boolean;
  v_now timestamp with time zone := now();
begin
  if p_exit_reason is null or btrim(p_exit_reason) = '' then
    raise exception 'withdrawal_reason_missing: a withdrawal must record why';
  end if;
  select a.* into v_actor from public.actor a where a.slug = p_actor_slug;
  if v_actor.id is null then
    raise exception 'unknown_actor: unknown actor %', p_actor_slug;
  end if;
  if not coalesce(v_actor.active and (
    v_actor.kind = 'human'
    or (v_actor.kind = 'automation'
        and nullif(btrim(current_setting('carr.sponsoring_human_slug', true)), '') in ('joe', 'dell'))
  ), false) then
    raise exception 'sponsored_actor_required: declining a Work Request requires an active human actor or a sponsored agent'
      using errcode = '42501';
  end if;

  select w.id, w.state, w.version, w.capture_idempotency_key is not null
    into v_id, v_state, v_version, v_sourced from ops.work_request w
   where w.ref = p_work_request for update;
  if v_id is null then
    raise exception 'work_request_not_found: no Work Request %', p_work_request;
  end if;
  if v_state not in ('captured','triaged') or not v_sourced then
    raise exception 'work_request_not_declinable: % is in state %; only captured or triaged sourced requests may be declined',
      p_work_request, v_state;
  end if;
  if v_version <> p_base_version then
    raise exception 'work_request_version_conflict: % is in state % at version %, not version %',
      p_work_request, v_state, v_version, p_base_version;
  end if;

  insert into ops.work_request_withdrawal_receipt
    (work_request_id, idempotency_key, base_version, result_version, final_state,
     exit_reason, superseded_by, withdrawn_by_actor_id, withdrawn_at)
  values (v_id, p_idempotency_key, p_base_version, p_base_version + 1, 'declined',
          p_exit_reason, null, v_actor.id, v_now)
  on conflict (idempotency_key) do nothing;

  update ops.work_request w
     set state = 'declined', exit_reason = p_exit_reason, closed_at = v_now,
         version = w.version + 1, updated_at = v_now
   where w.id = v_id;

  return query
    select w.ref, w.state, w.version, w.exit_reason, w.closed_at
      from ops.work_request w where w.id = v_id;
end;
$fn$;
