-- 0426_withdraw_a_work_request_captured_in_error.sql
--
-- A work request captured in error could only be moved FORWARD. Every intake verb
-- advances one: report-problem captures, review-and-triage classifies, then the
-- plan and outcome-feedback pairs. Nothing withdrew one, so a record created by
-- mistake stayed in the queue looking like work, and the only way to clear it was
-- to triage it forward as though it were real. WR-000032 and WR-000033 are the
-- standing example: captured duplicates of each other, one carrying the wrong
-- situation text from a schema probe.
--
-- NOTHING HERE IS NEW MACHINERY. ops.work_request already admits 'declined' and
-- 'superseded'; side_exits_record_a_reason already forces exit_reason on both;
-- superseded_names_its_successor already forces a successor; terminal_rows_are_
-- closed already forces closed_at; superseded_by already exists with a self-
-- referencing foreign key. The ONLY thing keeping a SOURCED request out of those
-- states was branch 3 of work_request_sourced_capture_shape. Five independent
-- reviews of the plan for this change established that, each by reading the
-- deployed schema rather than the prose around it.
--
-- CAPTURED ONLY. A captured sourced request holds no triage receipt, no shape
-- revision, no plan and no admission — propose_sourced_work_request_plan and
-- set_sourced_work_request_shape_disposition both require state='triaged' — so
-- withdrawing one strands no evidence. An earlier revision allowed withdrawal
-- from 'triaged' too, on two premises that were false: a count read off a query
-- ending in "limit 20", and an append-only property of the triage receipt that
-- NO TRIGGER ENFORCES (filed separately; it is a comment and nothing more).
--
-- THE INSERT SIDE IS COVERED, and it was not before. sourced_work_request_is_
-- immutable is BEFORE UPDATE only, so nothing stopped a row being INSERTed
-- already withdrawn — no receipt, no version history, no pointer check. Every
-- other INSERT trigger returns early for these states. That hole is closed here.

begin;

-- ---------------------------------------------------------------- the receipt
-- Shaped after ops.work_request_triage_receipt, with the append-only trigger
-- that table was supposed to have and does not. A receipt that can be edited is
-- not evidence, and this one is written to be evidence from the first row.
create table ops.work_request_withdrawal_receipt (
  id uuid primary key default gen_random_uuid(),
  work_request_id uuid not null references ops.work_request(id),
  idempotency_key uuid not null unique,
  base_version integer not null check (base_version > 0),
  result_version integer not null check (result_version > 0),
  final_state text not null check (final_state in ('declined','superseded')),
  exit_reason text not null check (btrim(exit_reason) <> ''),
  superseded_by uuid references ops.work_request(id),
  withdrawn_by_actor_id uuid not null references public.actor(id),
  withdrawn_at timestamp with time zone not null default now(),
  constraint withdrawal_receipt_successor_matches_state
    check ((final_state = 'superseded') = (superseded_by is not null))
);

create unique index work_request_withdrawal_receipt_one_per_request
  on ops.work_request_withdrawal_receipt (work_request_id);

create or replace function ops.work_request_withdrawal_receipt_append_only()
returns trigger language plpgsql security definer
set search_path to 'pg_catalog', 'ops' as $fn$
begin
  raise exception 'ops.work_request_withdrawal_receipt is append-only';
end;
$fn$;

create trigger work_request_withdrawal_receipt_append_only
  before delete or update on ops.work_request_withdrawal_receipt
  for each row execute function ops.work_request_withdrawal_receipt_append_only();

grant select on table ops.work_request_withdrawal_receipt to carr_reader;

comment on table ops.work_request_withdrawal_receipt is
  'Receipt for withdrawing a sourced Work Request captured in error. Append-only, '
  'enforced by trigger rather than by this comment. One row per request.';

-- ------------------------------------------------- branch 3 of the shape check
-- One new sub-arm, for one shape: the CAPTURED shape gone terminal. All three
-- triage fields stay null, which is exactly what makes this captured-only —
-- a row withdrawn from 'triaged' would carry them and could not satisfy it.
-- Branches 1 and 2 are untouched on purpose: neither imposes a state predicate,
-- so widening the shared list instead of this branch would make the terminal
-- states legal for every non-sourced and program row with nothing required.
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
        AND (triage_classification IS NULL) AND (triaged_by_actor_id IS NULL) AND (triaged_at IS NULL)
        AND (exit_reason IS NOT NULL) AND (btrim(exit_reason) <> ''::text)
        AND (closed_at IS NOT NULL)
        AND (((state = 'declined'::text) AND (superseded_by IS NULL))
             OR ((state = 'superseded'::text) AND (superseded_by IS NOT NULL))))
  ))
);

-- --------------------------------------------------- the immutability trigger
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

  -- CAPTURED -> DECLINED or SUPERSEDED, receipt-backed like every other arm.
  -- Only from captured: a triaged row carries a triage receipt and may carry a
  -- shape disposition, and withdrawing it would strand both.
  if old.state = 'captured'
     and new.state in ('declined','superseded')
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

  raise exception 'sourced Program 6 Work Requests permit only receipt-backed captured-to-triaged, triaged shape-disposition, triaged-to-ready, or captured-to-withdrawn transitions';
end;
$$;

-- The trigger itself has to fire on INSERT for the guard above to run at all.
drop trigger sourced_work_request_is_immutable on ops.work_request;
create trigger sourced_work_request_is_immutable
  before insert or update on ops.work_request
  for each row execute function ops.sourced_work_request_is_immutable();


-- ------------------------------------------------------------------- the card
-- THREE SITES, not one. The handler guard alone is dead code: this function's own
-- body filtered state in (captured,triaged,ready), so a withdrawn request came
-- back as zero rows and the verb raised work_request_not_found. And the signature
-- carried nowhere to say WHY it was withdrawn. A capability that erases the record
-- it exists to write is worse than no capability.
--
-- RETURNS TABLE changes, so this is a DROP and re-create, which drops the grants
-- with it — they are re-issued below. Precedent: 0177 and 0179 do the same.
drop function ops.work_request_card(text, text);

CREATE FUNCTION ops.work_request_card(p_work_request text, p_organization_tenant_id text) RETURNS TABLE(ref text, title text, state text, version integer, origin_ref text, desired_outcome text, acceptance_criteria jsonb, doctrine_section_id uuid, doctrine_revision_id uuid, doctrine_source_label text, source_current boolean, triage_classification text, triaged_by_actor_slug text, triaged_at timestamp with time zone, plan_ref text, plan_hash text, scope_summary text, runbook_ref text, runbook_revision_id uuid, runbook_content_hash text, plan_caps jsonb, dependency_refs jsonb, recovery_ref text, observability_ref text, accepted_by_actor_slug text, accepted_at timestamp with time zone, shape_disposition text, shape_fixed_surface_ref text, outcome_feedback jsonb, outcome_feedback_history jsonb, accepted_feedback_count bigint, exit_reason text, closed_at timestamp with time zone, superseded_by_ref text)
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'ops'
    AS $$
  select w.ref,w.title,w.state,w.version,w.origin_ref,w.desired_outcome,
         w.acceptance_criteria,w.doctrine_section_id,w.doctrine_revision_id,
         coalesce(s.title,s.section_key),
         (s.status='active' and s.current_revision_id=w.doctrine_revision_id),
         w.triage_classification,ta.slug,w.triaged_at,
         p.plan_ref,p.plan_hash,p.scope_summary,p.runbook_ref,p.runbook_revision_id,
         case when p.runbook_content_hash is null then null else 'sha256:' || p.runbook_content_hash end,
         p.caps,p.dependency_refs,p.recovery_ref,p.observability_ref,aa.slug,ar.accepted_at,
         w.shape_disposition,w.shape_fixed_surface_ref,
         latest.feedback,coalesce(history.feedback_history,'[]'::jsonb),coalesce(counted.accepted_count,0),
         w.exit_reason,w.closed_at,succ.ref
    from ops.work_request w
    join public.doctrine_section s on s.id=w.doctrine_section_id
    join public.doctrine_document d on d.id=s.document_id
    left join public.actor ta on ta.id=w.triaged_by_actor_id
    left join ops.work_request succ on succ.id=w.superseded_by
    left join lateral (
      select x.* from ops.sourced_work_request_plan x
      left join ops.sourced_work_request_plan_acceptance_receipt accepted on accepted.plan_id=x.id
       where x.work_request_id=w.id
       order by (accepted.id is not null) desc,x.plan_version desc limit 1
    ) p on true
    left join ops.sourced_work_request_plan_acceptance_receipt ar on ar.plan_id=p.id
    left join public.actor aa on aa.id=ar.accepted_by_actor_id
    left join lateral (
      select jsonb_build_object(
        'feedback_ref',f.feedback_ref,'feedback_hash',f.feedback_hash,'outcome',f.outcome,
        'criterion_results',f.criterion_results,'evidence_refs',f.evidence_refs,
        'blocker_code',f.blocker_code,'result_summary',f.result_summary,
        'observed_minutes',f.observed_minutes,'interaction_surface',f.interaction_surface,
        'heavy_session_used',f.heavy_session_used,'manual_context_transfers',f.manual_context_transfers,
        'accepted_by_actor_slug',fa.slug,'accepted_at',fr.accepted_at) as feedback
        from ops.sourced_work_request_outcome_feedback f
        join ops.sourced_work_request_outcome_feedback_acceptance_receipt fr on fr.feedback_id=f.id
        join public.actor fa on fa.id=fr.accepted_by_actor_id
       where f.work_request_id=w.id
       order by fr.accepted_at desc,fr.id desc limit 1
    ) latest on true
    left join lateral (
      select jsonb_agg(h.feedback order by h.accepted_at,h.acceptance_id) as feedback_history
        from (
          select fr.accepted_at,fr.id as acceptance_id,jsonb_build_object(
            'feedback_ref',f.feedback_ref,'feedback_hash',f.feedback_hash,'outcome',f.outcome,
            'criterion_results',f.criterion_results,'evidence_refs',f.evidence_refs,
            'blocker_code',f.blocker_code,'result_summary',f.result_summary,
            'observed_minutes',f.observed_minutes,'interaction_surface',f.interaction_surface,
            'heavy_session_used',f.heavy_session_used,'manual_context_transfers',f.manual_context_transfers,
            'accepted_by_actor_slug',fa.slug,'accepted_at',fr.accepted_at) as feedback
            from ops.sourced_work_request_outcome_feedback f
            join ops.sourced_work_request_outcome_feedback_acceptance_receipt fr on fr.feedback_id=f.id
            join public.actor fa on fa.id=fr.accepted_by_actor_id
           where f.work_request_id=w.id
           order by fr.accepted_at desc,fr.id desc limit 20
        ) h
    ) history on true
    left join lateral (
      select count(*)::bigint as accepted_count
        from ops.sourced_work_request_outcome_feedback f
        join ops.sourced_work_request_outcome_feedback_acceptance_receipt fr on fr.feedback_id=f.id
       where f.work_request_id=w.id
    ) counted on true
   where p_organization_tenant_id='carr-internal'
     and w.organization_tenant_id='carr-internal' and w.ref=p_work_request
     and w.state in ('captured','triaged','ready','declined','superseded')
     and d.visibility='shared';
$$;

grant execute on function ops.work_request_card(p_work_request text, p_organization_tenant_id text) to carr_reader;
grant execute on function ops.work_request_card(p_work_request text, p_organization_tenant_id text) to carr_writer;

-- ------------------------------------------------------- the withdrawal verbs
-- TWO functions, not one taking an optional successor. state-machines.v1.json is
-- phase0_frozen and declares "* -> declined" and "* -> superseded" as SEPARATE
-- transitions with different guards ("authorized disposition recorded" and
-- "replacement request linked"), and work-request-projection.v1.json rests a
-- declared judgment call on the canonical record keeping them apart. One entry
-- point inferring the state from whether an argument was passed collapses exactly
-- the distinction the frozen contract says must survive.
--
-- POINTER INTEGRITY lives here rather than in a CHECK because three of the four
-- refusals read a SECOND row, which a CHECK cannot do. The self-referencing
-- foreign key gives none of them: it permits superseded_by = id, a successor that
-- is itself withdrawn, and a sourced row pointing at a program row.

create or replace function ops.decline_sourced_work_request(
  p_work_request text, p_base_version integer, p_exit_reason text,
  p_actor_slug text, p_idempotency_key uuid)
returns table(ref text, state text, version integer, exit_reason text, closed_at timestamp with time zone)
language plpgsql security definer
set search_path to 'pg_catalog', 'ops' as $fn$
declare
  v_id uuid;
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

  select w.id into v_id from ops.work_request w
   where w.ref = p_work_request and w.capture_idempotency_key is not null
     and w.state = 'captured' and w.version = p_base_version
   for update;
  if v_id is null then
    raise exception 'no captured sourced Work Request % at version %', p_work_request, p_base_version;
  end if;

  insert into ops.work_request_withdrawal_receipt
    (work_request_id, idempotency_key, base_version, result_version, final_state,
     exit_reason, superseded_by, withdrawn_by_actor_id, withdrawn_at)
  values (v_id, p_idempotency_key, p_base_version, p_base_version + 1, 'declined',
          p_exit_reason, null, v_actor, v_now)
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

create or replace function ops.supersede_sourced_work_request(
  p_work_request text, p_base_version integer, p_exit_reason text,
  p_superseded_by text, p_actor_slug text, p_idempotency_key uuid)
returns table(ref text, state text, version integer, exit_reason text,
              closed_at timestamp with time zone, superseded_by_ref text)
language plpgsql security definer
set search_path to 'pg_catalog', 'ops' as $fn$
declare
  v_id uuid;
  v_succ uuid;
  v_succ_state text;
  v_succ_sourced boolean;
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

  select w.id into v_id from ops.work_request w
   where w.ref = p_work_request and w.capture_idempotency_key is not null
     and w.state = 'captured' and w.version = p_base_version
   for update;
  if v_id is null then
    raise exception 'no captured sourced Work Request % at version %', p_work_request, p_base_version;
  end if;

  select w.id, w.state, (w.capture_idempotency_key is not null)
    into v_succ, v_succ_state, v_succ_sourced
    from ops.work_request w where w.ref = p_superseded_by;
  if v_succ is null then
    raise exception 'no Work Request % to supersede it', p_superseded_by;
  end if;
  -- The four refusals the foreign key does not give.
  if v_succ = v_id then
    raise exception 'a Work Request cannot supersede itself';
  end if;
  if v_succ_state in ('declined','superseded') then
    raise exception 'the successor % is itself withdrawn; superseding into a dead row loses the trail', p_superseded_by;
  end if;
  if not v_succ_sourced then
    raise exception 'the successor % is not a sourced Work Request', p_superseded_by;
  end if;
  if exists (select 1 from ops.work_request w
              where w.id = v_succ and w.superseded_by = v_id) then
    raise exception 'that would make a two-row supersession cycle';
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

revoke all on function ops.decline_sourced_work_request(text,integer,text,text,uuid) from public;
revoke all on function ops.supersede_sourced_work_request(text,integer,text,text,text,uuid) from public;
grant execute on function ops.decline_sourced_work_request(text,integer,text,text,uuid) to carr_writer;
grant execute on function ops.supersede_sourced_work_request(text,integer,text,text,text,uuid) to carr_writer;


-- --------------------------------------------------------------------- proofs
do $verify$
declare
  v_ok boolean;
begin
  -- the receipt is append-only, by trigger and not by comment
  if not exists (select 1 from pg_trigger t
                  join pg_class c on c.oid = t.tgrelid
                  join pg_namespace n on n.oid = c.relnamespace
                 where n.nspname='ops' and c.relname='work_request_withdrawal_receipt'
                   and not t.tgisinternal) then
    raise exception '0426 FAILED: the withdrawal receipt has no append-only trigger';
  end if;

  -- the immutability trigger now fires on INSERT as well as UPDATE
  select bool_or(t.tgtype::integer & 4 = 4) into v_ok
    from pg_trigger t join pg_class c on c.oid = t.tgrelid
    join pg_namespace n on n.oid = c.relnamespace
   where n.nspname='ops' and c.relname='work_request' and t.tgname='sourced_work_request_is_immutable';
  if not coalesce(v_ok,false) then
    raise exception '0426 FAILED: sourced_work_request_is_immutable still does not fire on INSERT';
  end if;

  -- the card can return a withdrawn row and can say why
  if not exists (select 1 from pg_proc p join pg_namespace n on n.oid=p.pronamespace
                  where n.nspname='ops' and p.proname='work_request_card'
                    and 'exit_reason' = any(p.proargnames)
                    and 'superseded_by_ref' = any(p.proargnames)) then
    raise exception '0426 FAILED: work_request_card cannot report a withdrawal';
  end if;

  -- and the reader kept its execute grant across the drop
  if not has_function_privilege('carr_reader','ops.work_request_card(text,text)','execute') then
    raise exception '0426 FAILED: carr_reader lost execute on work_request_card';
  end if;
end
$verify$;

commit;
