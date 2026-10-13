-- rollback: forward-only — a request superseded from triaged keeps its triage fields, which the restored 0426 shape check would reject; recover with a new migration
-- lock-review: drops and re-adds one CHECK on ops.work_request (tens of rows, validated in milliseconds) and adds a row trigger to ops.work_request_triage_receipt (one row per triaged request); both take brief ACCESS EXCLUSIVE locks on small tables
-- 0883_supersede_triaged_work_request.sql
--
-- SUPERSEDE A TRIAGED WORK REQUEST WHEN NOTHING DOWNSTREAM WOULD BE STRANDED.
--
-- WHY. Joe, 2026-10-13: retire the old V2 plan WR-000221 (triaged, version 3)
-- in favour of WR-000288, which carries every WR-000221 criterion plus three
-- more. supersede-work-request refused with an unhandled error, "no captured
-- sourced Work Request WR-000221 at version 3": 0426 admitted withdrawal from
-- 'captured' only.
--
-- 0426 WITHDREW AN EARLIER 'triaged' REVISION ON TWO FALSE PREMISES. This
-- migration replaces both rather than widening the state check alone.
--
--   (a) A count read off a query ending in "limit 20". Replaced by a census
--       that cannot be truncated: ops.work_request_supersession_strands() reads
--       every foreign key that references ops.work_request from pg_constraint
--       at call time and checks each one for rows naming this request. A table
--       that gains a foreign key later is refused by default, not forgotten.
--   (b) The triage receipt being append-only, which no trigger enforced. It is
--       enforced below, mirroring ops.work_request_withdrawal_receipt_append_only.
--
-- THE FOREIGN-KEY ENUMERATION, read from pg_constraint on a disposable
-- PostgreSQL 18 rebuild of db/schema.sql at 0879 (33 constraints, 32 tables).
--
--   Stay attached to the retired row, readable through superseded_by. These are
--   receipts of the request's own intake, which a supersession does not undo:
--     ops.work_request_triage_receipt.work_request_id
--     ops.work_request_joe_answer_receipt.work_request_id
--     ops.work_request_withdrawal_receipt.work_request_id     (this act's own)
--     ops.work_request_withdrawal_receipt.superseded_by       (a predecessor's)
--
--   Refuse a triaged supersession when any row names the request. Each is work
--   or evidence bound to THIS request's id and version, which a successor with
--   a different id and version cannot inherit:
--     plans        ops.sourced_work_request_plan
--                  ops.sourced_work_request_plan_acceptance_receipt
--                  ops.sourced_work_request_plan_shape_binding_receipt
--                  ops.ready_plan_amendment
--                  ops.ready_plan_amendment_acceptance_receipt
--                  ops.source_merge_plan_scope
--     admission    ops.heavy_build_admission_revision
--     shape        ops.work_shape_revision
--                  ops.sourced_work_request_shape_disposition_receipt
--                  ops.sourced_work_request_shape_disposition_correction_receipt
--     outcome      ops.sourced_work_request_outcome_feedback
--                  ops.sourced_work_request_outcome_feedback_acceptance_receipt
--                  ops.work_request_criterion_evidence
--     execution    ops.engineering_slice_plan
--                  ops.engineering_slice_receipt
--                  ops.engineering_execution_envelope
--                  ops.engineering_reviewer_fact
--                  ops.execution_envelope_v1
--                  ops.work_request_execution_assignment
--                  ops.attempt_receipt
--                  ops.assurance_execution_manifest
--                  ops.context_activation_binding
--                  ops.siep_package_contract
--                  ops.capability_agent_session
--                  ops.capability_verification
--                  ops.canonical_ownership_lease
--                  ops.canonical_ownership_runtime_session
--     memory       public.memory_item
--     lineage      ops.work_request.superseded_by  (a predecessor already
--                  superseded INTO this request; retiring it would leave that
--                  pointer naming a dead row, which 0426 refuses on the way in)
--
--   Four columns name a Work Request with no foreign key, found by column name
--   on the same rebuild. They are checked by name, because the census above
--   cannot see them:
--     public.room_dispatch_link.work_request_id      (uuid)
--     ops.assurance_health_evidence.work_request_id  (WR- ref)
--     ops.release.work_request_ref                   (WR- ref)
--     ops.engineering_stale_contract_fence.work_request_ref (WR- ref)
--
-- THE REFUSALS, each named so the verb can surface it rather than report an
-- unhandled failure:
--   withdrawal_reason_missing                  blank exit_reason
--   unknown_actor                              p_actor_slug names no actor
--   work_request_not_supersedable              not a sourced request in
--                                              captured or triaged at that version
--   successor_not_found, successor_is_self, successor_withdrawn,
--   successor_not_sourced, supersession_cycle  the four 0426 pointer refusals
--   triaged_work_request_has_downstream_evidence  the census above found rows
--
-- THE DECLINE TWIN STAYS CAPTURED-ONLY. It shares the state check but not the
-- reasoning: the test here is what a successor can inherit, and a decline has
-- no successor. Declining a triaged request is a different disposition under
-- the frozen state machine's "authorized disposition recorded" guard, and
-- nobody has asked for it.

do $preflight$
begin
  if (select md5(prosrc) from pg_proc
       where oid = 'ops.sourced_work_request_is_immutable()'::regprocedure)
     is distinct from '516bf0b7b0a8f8d22e83359268f85af5' then
    raise exception '0883 refused: ops.sourced_work_request_is_immutable drifted from its 0532a shape';
  end if;
  if (select md5(prosrc) from pg_proc
       where oid = 'ops.supersede_sourced_work_request(text,integer,text,text,text,uuid)'::regprocedure)
     is distinct from '3be1e92a543cd60bb0407bee2ce5dc0d' then
    raise exception '0883 refused: ops.supersede_sourced_work_request drifted from its 0426 shape';
  end if;
end
$preflight$;

-- ------------------------------------------- (b) the triage receipt, enforced
-- Not security definer: a raise needs no privilege, and an invoker function
-- adds no row to the sealed SCAC execute-grant catalog.
create function ops.work_request_triage_receipt_append_only()
returns trigger language plpgsql
set search_path to 'pg_catalog', 'ops', 'pg_temp' as $fn$
begin
  raise exception 'ops.work_request_triage_receipt is append-only';
end;
$fn$;

create trigger work_request_triage_receipt_append_only
  before delete or update on ops.work_request_triage_receipt
  for each row execute function ops.work_request_triage_receipt_append_only();

comment on table ops.work_request_triage_receipt is
  'Receipt for triaging a sourced Work Request. Append-only, enforced by trigger since 0883. '
  'Stays attached to a request superseded from triaged, readable through superseded_by.';

-- ---------------------------------------------------------- (a) the census
-- Returns every place that names the request outside its own intake receipts,
-- as 'schema.table.column'; empty when a successor would strand nothing.
create function ops.work_request_supersession_strands(p_work_request_id uuid)
returns text[] language plpgsql stable
set search_path to 'pg_catalog', 'ops', 'pg_temp' as $fn$
declare
  v_ref text;
  v_fk record;
  v_hit boolean;
  v_found text[] := array[]::text[];
begin
  select w.ref into v_ref from ops.work_request w where w.id = p_work_request_id;

  for v_fk in
    select format('%I.%I', n.nspname, r.relname) as rel, a.attname::text as col
      from pg_constraint c
      join pg_class r on r.oid = c.conrelid
      join pg_namespace n on n.oid = r.relnamespace
      join pg_attribute a on a.attrelid = c.conrelid and a.attnum = c.conkey[1]
     where c.contype = 'f' and c.confrelid = 'ops.work_request'::regclass
       and cardinality(c.conkey) = 1
       and (n.nspname || '.' || r.relname, a.attname::text) not in (
         ('ops.work_request_triage_receipt', 'work_request_id'),
         ('ops.work_request_joe_answer_receipt', 'work_request_id'),
         ('ops.work_request_withdrawal_receipt', 'work_request_id'),
         ('ops.work_request_withdrawal_receipt', 'superseded_by'))
     order by 1, 2
  loop
    execute format('select exists (select 1 from %s where %I = $1)', v_fk.rel, v_fk.col)
      into v_hit using p_work_request_id;
    if v_hit then
      v_found := v_found || (v_fk.rel || '.' || v_fk.col);
    end if;
  end loop;

  -- A multi-column key to ops.work_request would escape the loop above; there
  -- is none today, and one appearing must be read before it is trusted.
  if exists (select 1 from pg_constraint c
              where c.contype = 'f' and c.confrelid = 'ops.work_request'::regclass
                and cardinality(c.conkey) <> 1) then
    v_found := v_found || 'unreadable multi-column foreign key'::text;
  end if;

  if exists (select 1 from public.room_dispatch_link x where x.work_request_id = p_work_request_id) then
    v_found := v_found || 'public.room_dispatch_link.work_request_id'::text;
  end if;
  if exists (select 1 from ops.assurance_health_evidence x where x.work_request_id = v_ref) then
    v_found := v_found || 'ops.assurance_health_evidence.work_request_id'::text;
  end if;
  if exists (select 1 from ops.release x where x.work_request_ref = v_ref) then
    v_found := v_found || 'ops.release.work_request_ref'::text;
  end if;
  if exists (select 1 from ops.engineering_stale_contract_fence x where x.work_request_ref = v_ref) then
    v_found := v_found || 'ops.engineering_stale_contract_fence.work_request_ref'::text;
  end if;

  return v_found;
end;
$fn$;

revoke all on function ops.work_request_supersession_strands(uuid) from public;

comment on function ops.work_request_supersession_strands(uuid) is
  'Every column outside the intake receipts that names this Work Request. A triaged request '
  'may be superseded only when this is empty. Reads foreign keys from pg_constraint at call time.';

-- ------------------------------------------------------ the shape check
-- One change to branch 3's terminal sub-arm: a SUPERSEDED row may carry the
-- complete triage triple it was triaged with. DECLINED still requires all
-- three null, which keeps decline captured-only at the constraint as well.
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
             OR ((state = 'superseded'::text)
                 AND (triage_classification = ANY (ARRAY['operational'::text, 'needs_judgment'::text, 'safety_review'::text]))
                 AND (triaged_by_actor_id IS NOT NULL) AND (triaged_at IS NOT NULL)))
        AND (exit_reason IS NOT NULL) AND (btrim(exit_reason) <> ''::text)
        AND (closed_at IS NOT NULL)
        AND (((state = 'declined'::text) AND (superseded_by IS NULL))
             OR ((state = 'superseded'::text) AND (superseded_by IS NOT NULL))))
  ))
);

-- -------------------------------------------------- the immutability trigger
-- One new arm, spliced in front of the final refusal as 0532a spliced its
-- ready-plan arm: TRIAGED -> SUPERSEDED, receipt-backed like captured, and
-- re-checking the census so a forged receipt cannot strand anything either.
do $work_request_successor$
declare
  v_definition text;
  v_marker constant text := $marker$  raise exception 'sourced Program 6 Work Requests permit only receipt-backed captured-to-triaged, triaged shape-disposition, triaged-to-ready, or captured-to-withdrawn transitions';$marker$;
  v_branch constant text := $branch$  -- TRIAGED -> SUPERSEDED (0883). The triage fields stay on the row and the
  -- triage receipt stays attached; nothing else may name the request.
  if old.state = 'triaged'
     and new.state = 'superseded'
     and new.version = old.version + 1
     and (to_jsonb(new) - array['state','exit_reason','closed_at','superseded_by','version','updated_at'])
           is not distinct from
         (to_jsonb(old) - array['state','exit_reason','closed_at','superseded_by','version','updated_at'])
     and exists (
       select 1 from ops.work_request_withdrawal_receipt r
        where r.work_request_id = old.id and r.base_version = old.version
          and r.result_version = new.version and r.final_state = 'superseded'
          and r.exit_reason = new.exit_reason
          and r.superseded_by is not distinct from new.superseded_by
     )
     and cardinality(ops.work_request_supersession_strands(old.id)) = 0 then
    return new;
  end if;

  raise exception 'sourced Program 6 Work Requests permit only receipt-backed captured-to-triaged, triaged shape-disposition, triaged-to-ready, captured-to-withdrawn, or triaged-to-superseded transitions';$branch$;
begin
  select pg_get_functiondef('ops.sourced_work_request_is_immutable()'::regprocedure)
    into v_definition;
  if (length(v_definition)-length(replace(v_definition,v_marker,'')))/length(v_marker)<>1 then
    raise exception '0883 refused: sourced Work Request lifecycle predecessor shape drifted';
  end if;
  execute replace(v_definition,v_marker,v_branch);
end $work_request_successor$;

-- ---------------------------------------------------- the supersede verb
-- Same signature, return shape, owner and grants (create or replace keeps the
-- ACL). Every refusal now carries a name the verb can map.
create or replace function ops.supersede_sourced_work_request(
  p_work_request text, p_base_version integer, p_exit_reason text,
  p_superseded_by text, p_actor_slug text, p_idempotency_key uuid)
returns table(ref text, state text, version integer, exit_reason text,
              closed_at timestamp with time zone, superseded_by_ref text)
language plpgsql security definer
set search_path to 'pg_catalog', 'ops', 'pg_temp' as $fn$
declare
  v_id uuid;
  v_state text;
  v_succ uuid;
  v_succ_state text;
  v_succ_sourced boolean;
  v_actor uuid;
  v_strands text[];
  v_now timestamp with time zone := now();
begin
  if p_exit_reason is null or btrim(p_exit_reason) = '' then
    raise exception 'withdrawal_reason_missing: a withdrawal must record why';
  end if;
  select a.id into v_actor from public.actor a where a.slug = p_actor_slug;
  if v_actor is null then
    raise exception 'unknown_actor: unknown actor %', p_actor_slug;
  end if;

  select w.id, w.state into v_id, v_state from ops.work_request w
   where w.ref = p_work_request and w.capture_idempotency_key is not null
     and w.state in ('captured','triaged') and w.version = p_base_version
   for update;
  if v_id is null then
    raise exception 'work_request_not_supersedable: no captured or triaged sourced Work Request % at version %',
      p_work_request, p_base_version;
  end if;

  select w.id, w.state, (w.capture_idempotency_key is not null)
    into v_succ, v_succ_state, v_succ_sourced
    from ops.work_request w where w.ref = p_superseded_by;
  if v_succ is null then
    raise exception 'successor_not_found: no Work Request % to supersede it', p_superseded_by;
  end if;
  -- The four refusals the foreign key does not give.
  if v_succ = v_id then
    raise exception 'successor_is_self: a Work Request cannot supersede itself';
  end if;
  if v_succ_state in ('declined','superseded') then
    raise exception 'successor_withdrawn: the successor % is itself withdrawn; superseding into a dead row loses the trail', p_superseded_by;
  end if;
  if not v_succ_sourced then
    raise exception 'successor_not_sourced: the successor % is not a sourced Work Request', p_superseded_by;
  end if;
  if exists (select 1 from ops.work_request w
              where w.id = v_succ and w.superseded_by = v_id) then
    raise exception 'supersession_cycle: that would make a two-row supersession cycle';
  end if;

  if v_state = 'triaged' then
    v_strands := ops.work_request_supersession_strands(v_id);
    if cardinality(v_strands) > 0 then
      raise exception 'triaged_work_request_has_downstream_evidence: % is named by % and a successor cannot inherit it',
        p_work_request, array_to_string(v_strands, ', ');
    end if;
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
    select w.ref, w.state, w.version, w.exit_reason, w.closed_at, s.ref
      from ops.work_request w
      left join ops.work_request s on s.id = w.superseded_by
     where w.id = v_id;
end;
$fn$;

-- --------------------------------------------------------------------- proofs
do $verify$
begin
  if not exists (select 1 from pg_trigger t
                  where t.tgrelid = 'ops.work_request_triage_receipt'::regclass
                    and t.tgname = 'work_request_triage_receipt_append_only'
                    and t.tgtype::integer & 8 = 8 and t.tgtype::integer & 16 = 16) then
    raise exception '0883 FAILED: the triage receipt has no UPDATE and DELETE append-only trigger';
  end if;
  if position('triaged-to-superseded' in (select prosrc from pg_proc
       where oid = 'ops.sourced_work_request_is_immutable()'::regprocedure)) = 0 then
    raise exception '0883 FAILED: the lifecycle trigger has no triaged-to-superseded arm';
  end if;
  if not has_function_privilege('carr_writer',
       'ops.supersede_sourced_work_request(text,integer,text,text,text,uuid)', 'execute') then
    raise exception '0883 FAILED: carr_writer lost execute on supersede_sourced_work_request';
  end if;
  if has_function_privilege('carr_writer', 'ops.work_request_supersession_strands(uuid)', 'execute') then
    raise exception '0883 FAILED: the census is callable directly by carr_writer';
  end if;
end
$verify$;
