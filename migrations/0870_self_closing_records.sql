-- rollback: forward-only — every row these objects hold is a closing receipt another record now stands on; stop writing by revoking execute on the two ops functions and insert on public.defect_resolution, and leave the rows in place.
-- lock-review: two new tables and four new functions; v_defect is replaced with the same columns plus four appended ones; ops.sourced_work_request_outcome_feedback_acceptance_receipt gains two columns with a constant default (metadata-only) and one CHECK validated over its 22 rows under a brief ACCESS EXCLUSIVE lock.
-- 0858: self-closing records (Joe, 2026-10-12, decision cbe91287: "this system
-- needs to be able to function without me micromanaging it").
--
-- Three kinds of record piled up because only a person could close them:
--
--   1. A defect had no close at all. public.defect keeps accumulating on
--      purpose (the class counts are the point), so a close is NOT a delete
--      and NOT a state on the defect row: it is one append-only resolution
--      receipt naming the fixing commit, pull request or server receipt.
--      v_defect gains the receipt's columns; v_defect_class is unchanged, so
--      a closed defect still counts toward its class.
--
--   2. An incident closes on its own evidence through close-incident (that is
--      JavaScript over rows carr_writer can already read, so nothing here).
--      A linked defect's resolution receipt is one of the remediation receipts
--      it accepts, which is why this table is readable by carr_writer.
--
--   3. A sourced Work Request reaches its done state, which is an accepted
--      outcome-feedback receipt (V5-F09: current_sourced_work_requests stops
--      offering it as work), without a human accept-outcome-feedback call, when
--      every acceptance criterion carries a bound evidence receipt the database
--      itself can resolve. The receipt says so: acceptance_basis is
--      'completed_on_bound_evidence' and bound_receipt_refs lists the receipts.
--      The human acceptance path is untouched and still stamps
--      'human_acceptance'.
--
-- WHAT COUNTS AS A RECEIPT is decided here, once, by
-- ops.work_request_evidence_receipt_kind, and nowhere else: a succeeded ops.run,
-- a complete ops.deployment, a defect resolution, or a resolved incident. A
-- caller's own words never count, which is the difference between this and the
-- outcome proposal's free-text evidence_refs.

-- ── 1. defect resolution ─────────────────────────────────────────────────────

create table public.defect_resolution (
  id uuid primary key default gen_random_uuid(),
  defect_id uuid not null references public.defect(id),
  evidence_kind text not null check (evidence_kind in ('commit', 'pull_request', 'receipt')),
  evidence_ref text not null,
  note text check (note is null or char_length(note) <= 1000),
  idempotency_key text not null unique check (btrim(idempotency_key) <> ''),
  resolved_by uuid not null references public.actor(id),
  created_at timestamptz not null default now(),
  -- One close per defect. A second close with the same evidence is the same
  -- act (close-defect returns the first receipt); different evidence is
  -- refused rather than overwriting what the defect was closed on.
  constraint defect_resolution_one_per_defect unique (defect_id),
  constraint defect_resolution_evidence_shape check (
    (evidence_kind = 'commit' and evidence_ref ~ '^[0-9a-f]{7,40}$')
    or (evidence_kind = 'pull_request' and evidence_ref ~ '^[1-9][0-9]{0,6}$')
    or (evidence_kind = 'receipt'
        and evidence_ref ~ '^[a-z][a-z0-9_.-]{0,40}:[A-Za-z0-9][A-Za-z0-9:_./-]{0,250}$'))
);

comment on table public.defect_resolution is
  'Append-only close receipt for one defect (0858, decision cbe91287): the fixing commit, pull request or server receipt, who bound it and when. The defect row itself never changes, so v_defect_class keeps counting it; v_defect shows the receipt beside it.';

create function public.defect_resolution_is_append_only() returns trigger
language plpgsql set search_path = pg_catalog, public, pg_temp as $fn$
begin
  raise exception 'defect_resolution is append-only: a closed defect keeps the evidence it was closed on';
end $fn$;

create trigger defect_resolution_append_only before update or delete
on public.defect_resolution for each row execute function public.defect_resolution_is_append_only();
create trigger defect_resolution_no_truncate before truncate
on public.defect_resolution for each statement execute function public.defect_resolution_is_append_only();
create trigger scac_reference_monitor_guard_row before insert or update or delete
on public.defect_resolution for each row execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_truncate before truncate
on public.defect_resolution for each statement execute function ops.scac_reference_monitor_guard();

revoke all on public.defect_resolution from public, carr_reader, carr_writer, carr_jobs, carr_authority;
grant select on public.defect_resolution to carr_writer, carr_exporter;
grant insert on public.defect_resolution to carr_writer;

-- carr_reader holds no grant on the base tables; v_defect is how a read
-- session sees the log, so the receipt joins it here. Same columns as before,
-- in the same order, with the four receipt columns appended.
create or replace view public.v_defect as
select d.id,
       d.occurred_on,
       d.defect_class,
       d.claimed,
       d.actual,
       d.source_unread,
       d.rule_violated,
       d.detected_by,
       d.session_key,
       d.cost_note,
       r.statement as rule_statement,
       a.slug as recorded_by,
       d.created_at,
       res.created_at as resolved_at,
       res.evidence_kind as resolution_kind,
       res.evidence_ref as resolution_ref,
       ra.slug as resolved_by
  from public.defect d
  left join public.actor a on a.id = d.created_by
  left join public.rule r on r.id::text = d.rule_violated
                          or left(r.id::text, 8) = lower(btrim(coalesce(d.rule_violated, '')))
  left join public.defect_resolution res on res.defect_id = d.id
  left join public.actor ra on ra.id = res.resolved_by;

-- ── 2. which receipts the database will stand on ─────────────────────────────

-- The kind of server record a receipt reference resolves to RIGHT NOW, or null.
-- Each branch matches the reference's exact shape before it casts, so a
-- malformed reference answers null instead of raising.
create function ops.work_request_evidence_receipt_kind(p_ref text) returns text
language plpgsql stable set search_path = pg_catalog, ops, public, pg_temp as $fn$
declare
  uuid_shape constant text := '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}';
begin
  if p_ref is null then
    return null;
  elsif p_ref ~ ('^ops\.run:' || uuid_shape || '$') then
    if exists (select 1 from ops.run r where r.id = substring(p_ref from 9)::uuid and r.state = 'succeeded') then
      return 'run';
    end if;
  elsif p_ref ~ ('^ops\.deployment:' || uuid_shape || '$') then
    if exists (select 1 from ops.deployment d where d.id = substring(p_ref from 16)::uuid and d.state = 'complete') then
      return 'deployment';
    end if;
  elsif p_ref ~ ('^defect-resolution:' || uuid_shape || '$') then
    if exists (select 1 from public.defect_resolution x where x.id = substring(p_ref from 19)::uuid) then
      return 'defect_resolution';
    end if;
  elsif p_ref ~ '^incident:INC-[0-9]{8}-(0[1-9]|[1-9][0-9]+)$' then
    if exists (select 1 from ops.incident i where i.ref = substring(p_ref from 10)
                                            and i.state in ('resolved', 'reviewed')) then
      return 'incident';
    end if;
  end if;
  return null;
end $fn$;

comment on function ops.work_request_evidence_receipt_kind(text) is
  'The one definition of a bound evidence receipt (0858): ops.run:<uuid> that succeeded, ops.deployment:<uuid> that completed, defect-resolution:<uuid>, or incident:<ref> that is resolved or reviewed. Null for anything else, including a malformed reference.';

-- THE PREDICATE. Pure: criteria are the Work Request''s acceptance_criteria
-- ([{id,text}]); bindings are [{criterion_id, receipt_ref, receipt_kind,
-- binding_id}] where receipt_kind is the CURRENT resolution (null when the
-- receipt no longer resolves). Complete only when every criterion id has a
-- binding with a non-null kind; an empty criteria list is never complete.
create function ops.work_request_bound_evidence_verdict(p_criteria jsonb, p_bindings jsonb) returns jsonb
language sql immutable set search_path = pg_catalog, pg_temp as $fn$
  with criteria as (
    select distinct c.value ->> 'id' as id
      from jsonb_array_elements(case when jsonb_typeof(p_criteria) = 'array' then p_criteria else '[]'::jsonb end) c
     where c.value ->> 'id' is not null
  ), bound as (
    select b.value ->> 'criterion_id' as id, b.value as binding
      from jsonb_array_elements(case when jsonb_typeof(p_bindings) = 'array' then p_bindings else '[]'::jsonb end) b
     where b.value ->> 'receipt_kind' is not null and b.value ->> 'criterion_id' is not null
  ), missing as (
    select criteria.id from criteria
     where not exists (select 1 from bound where bound.id = criteria.id)
  )
  select jsonb_build_object(
    'complete', exists (select 1 from criteria) and not exists (select 1 from missing),
    'missing', coalesce((select jsonb_agg(missing.id order by missing.id) from missing), '[]'::jsonb),
    'receipts', coalesce((select jsonb_agg(jsonb_build_object(
                    'criterion_id', bound.id,
                    'receipt_ref', bound.binding ->> 'receipt_ref',
                    'receipt_kind', bound.binding ->> 'receipt_kind',
                    'binding_id', bound.binding ->> 'binding_id') order by bound.id)
                  from bound where exists (select 1 from criteria where criteria.id = bound.id)), '[]'::jsonb))
$fn$;

comment on function ops.work_request_bound_evidence_verdict(jsonb, jsonb) is
  'Pure completion predicate (0858): {complete, missing, receipts}. Complete only when every acceptance criterion id carries a binding whose receipt currently resolves.';

-- ── 3. Work Request criterion evidence ───────────────────────────────────────

create table ops.work_request_criterion_evidence (
  id uuid primary key default gen_random_uuid(),
  work_request_id uuid not null references ops.work_request(id),
  criterion_id text not null check (criterion_id ~ '^[A-Z][A-Z0-9-]{1,63}$'),
  receipt_ref text not null check (char_length(receipt_ref) <= 300),
  receipt_kind text not null check (receipt_kind in ('run', 'deployment', 'defect_resolution', 'incident')),
  bound_by_actor_id uuid not null references public.actor(id),
  idempotency_key uuid not null unique,
  created_at timestamptz not null default now(),
  -- One receipt per criterion. Binding is a fact about evidence, not a vote,
  -- so a second, different receipt for the same criterion is refused.
  constraint work_request_criterion_evidence_once unique (work_request_id, criterion_id)
);

comment on table ops.work_request_criterion_evidence is
  'Append-only binding of one sourced Work Request acceptance criterion to one server-resolvable receipt (0858). Written only through ops.bind_work_request_criterion_evidence; when every criterion is bound and a criteria_met outcome proposal exists, the request completes on bound evidence.';

create function ops.work_request_criterion_evidence_is_append_only() returns trigger
language plpgsql set search_path = pg_catalog, ops, pg_temp as $fn$
begin
  raise exception 'work_request_criterion_evidence is append-only: a bound receipt is never rewritten';
end $fn$;

create trigger work_request_criterion_evidence_append_only before update or delete
on ops.work_request_criterion_evidence for each row execute function ops.work_request_criterion_evidence_is_append_only();
create trigger work_request_criterion_evidence_no_truncate before truncate
on ops.work_request_criterion_evidence for each statement execute function ops.work_request_criterion_evidence_is_append_only();
create trigger scac_reference_monitor_guard_row before insert or update or delete
on ops.work_request_criterion_evidence for each row execute function ops.scac_reference_monitor_guard();
create trigger scac_reference_monitor_guard_truncate before truncate
on ops.work_request_criterion_evidence for each statement execute function ops.scac_reference_monitor_guard();

revoke all on ops.work_request_criterion_evidence from public, carr_reader, carr_writer, carr_jobs, carr_authority;
grant select on ops.work_request_criterion_evidence to carr_reader, carr_writer;

-- ── 4. the acceptance receipt says how it was reached ────────────────────────

alter table ops.sourced_work_request_outcome_feedback_acceptance_receipt
  add column acceptance_basis text not null default 'human_acceptance',
  add column bound_receipt_refs jsonb,
  add constraint sourced_outcome_acceptance_basis_shape check (
    (acceptance_basis = 'human_acceptance' and bound_receipt_refs is null)
    or (acceptance_basis = 'completed_on_bound_evidence'
        and jsonb_typeof(bound_receipt_refs) = 'array'
        and jsonb_array_length(bound_receipt_refs) > 0));

comment on column ops.sourced_work_request_outcome_feedback_acceptance_receipt.acceptance_basis is
  'human_acceptance: a partner accepted the proposal (accept-outcome-feedback). completed_on_bound_evidence: every acceptance criterion carried a bound receipt the database resolved, and the request completed without a human call (0858); bound_receipt_refs lists them.';

-- ── 5. completion on bound evidence ──────────────────────────────────────────

-- Accept the latest criteria_met outcome proposal for the current ready plan
-- when every acceptance criterion carries a receipt that resolves NOW. Returns
-- completed=false with what is missing, and writes nothing, otherwise. It
-- re-derives everything it relies on; nothing a caller says is trusted.
create function ops.complete_sourced_work_request_on_bound_evidence(p_work_request text, p_idempotency_key uuid)
returns table(work_request_id uuid, ref text, state text, version integer, completed boolean, missing jsonb,
              receipt_id uuid, feedback_ref text, feedback_hash text, acceptance_basis text,
              bound_receipt_refs jsonb, accepted_by_actor_slug text, accepted_at timestamptz, replayed boolean)
language plpgsql security definer set search_path = pg_catalog, ops, public, pg_temp as $fn$
declare
  actor_slug text; a public.actor%rowtype; w ops.work_request%rowtype;
  p ops.sourced_work_request_plan%rowtype;
  ar ops.sourced_work_request_plan_acceptance_receipt%rowtype;
  f ops.sourced_work_request_outcome_feedback%rowtype;
  fr ops.sourced_work_request_outcome_feedback_acceptance_receipt%rowtype;
  bindings jsonb; verdict jsonb; canonical_preimage jsonb; canonical_hash text;
begin
  if coalesce(btrim(p_work_request), '') !~ '^WR-[0-9]{1,12}$' or p_idempotency_key is null then
    raise exception 'completion on bound evidence requires an exact Work Request ref and a UUID idempotency key';
  end if;
  actor_slug := nullif(btrim(current_setting('carr.acting_actor_slug', true)), '');
  select x.* into a from public.actor x where x.slug = actor_slug and x.active;
  if not found then
    raise exception 'completion on bound evidence requires a server-derived acting actor';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('program6-outcome-feedback-acceptance:' || p_idempotency_key, 0));
  select x.* into fr from ops.sourced_work_request_outcome_feedback_acceptance_receipt x
   where x.idempotency_key = p_idempotency_key;
  if found then
    select x.* into w from ops.work_request x where x.id = fr.work_request_id;
    select x.* into f from ops.sourced_work_request_outcome_feedback x where x.id = fr.feedback_id;
    if fr.acceptance_basis is distinct from 'completed_on_bound_evidence' or w.ref is distinct from p_work_request then
      raise exception 'idempotency key already names a different outcome-feedback acceptance';
    end if;
    return query select w.id, w.ref, w.state, fr.result_version, true, '[]'::jsonb, fr.id,
      f.feedback_ref, f.feedback_hash, fr.acceptance_basis, fr.bound_receipt_refs,
      (select y.slug from public.actor y where y.id = fr.accepted_by_actor_id), fr.accepted_at, true;
    return;
  end if;

  select x.* into w from ops.work_request x where x.ref = p_work_request for update;
  if not found then raise exception 'exact sourced Work Request not found'; end if;
  if w.capture_idempotency_key is null or w.organization_tenant_id is distinct from 'carr-internal'
     or w.state is distinct from 'ready' then
    return query select w.id, w.ref, w.state, w.version, false, '["ready_sourced_work_request"]'::jsonb,
      null::uuid, null::text, null::text, null::text, null::jsonb, null::text, null::timestamptz, false;
    return;
  end if;

  -- The latest proposal for the current version. An older criteria_met
  -- proposal never completes a request whose newest proposal says otherwise.
  select x.* into f from ops.sourced_work_request_outcome_feedback x
   where x.work_request_id = w.id and x.work_request_version = w.version
   order by x.feedback_version desc limit 1;
  if not found or f.outcome is distinct from 'criteria_met' or f.blocker_code is distinct from 'none' then
    return query select w.id, w.ref, w.state, w.version, false, '["criteria_met_outcome_proposal"]'::jsonb,
      null::uuid, f.feedback_ref, f.feedback_hash, null::text, null::jsonb, null::text, null::timestamptz, false;
    return;
  end if;
  if exists (select 1 from ops.sourced_work_request_outcome_feedback_acceptance_receipt x where x.feedback_id = f.id) then
    return query select w.id, w.ref, w.state, w.version, false, '["unaccepted_outcome_proposal"]'::jsonb,
      null::uuid, f.feedback_ref, f.feedback_hash, null::text, null::jsonb, null::text, null::timestamptz, false;
    return;
  end if;

  -- The same exactness accept_sourced_work_request_outcome_feedback demands.
  select x.* into p from ops.sourced_work_request_plan x where x.id = f.plan_id;
  select x.* into ar from ops.sourced_work_request_plan_acceptance_receipt x where x.id = f.plan_acceptance_receipt_id;
  if not found or ar.work_request_id is distinct from w.id or ar.plan_id is distinct from p.id
     or ar.result_version is distinct from w.version or ar.plan_hash is distinct from p.plan_hash
     or ar.accepted_by_actor_id is null
     or (w.shape_fixed_surface_ref is distinct from ar.shape_fixed_surface_ref
         and not exists (
           select 1 from ops.sourced_work_request_plan_shape_binding_receipt sb
            where sb.plan_acceptance_receipt_id = ar.id
              and sb.work_request_id = w.id
              and sb.disposition is not distinct from w.shape_disposition
              and sb.fixed_surface_ref is not distinct from w.shape_fixed_surface_ref
              and exists (select 1 from ops.sourced_work_request_shape_disposition_lineage(w.id) e
                           where (e.disposition, e.fixed_surface_ref, e.rationale, e.decided_by_actor_id, e.decided_at)
                             is not distinct from (sb.disposition, sb.fixed_surface_ref, sb.rationale, sb.decided_by_actor_id, sb.decided_at)))) then
    raise exception 'only an exact current ready sourced plan may complete on bound evidence';
  end if;
  canonical_preimage := ops.sourced_work_request_outcome_feedback_preimage(
    w.id, p.id, ar.id, f.criterion_results, f.evidence_refs, f.outcome, f.blocker_code,
    f.result_summary, f.observed_minutes, f.interaction_surface, f.heavy_session_used,
    f.manual_context_transfers);
  canonical_hash := ops.sourced_work_request_outcome_feedback_digest(canonical_preimage);
  if canonical_preimage is distinct from f.preimage or canonical_hash is distinct from f.feedback_hash then
    raise exception 'sourced outcome-feedback preimage is stale or does not match its exact hash';
  end if;

  select coalesce(jsonb_agg(jsonb_build_object(
           'criterion_id', e.criterion_id, 'receipt_ref', e.receipt_ref,
           'receipt_kind', ops.work_request_evidence_receipt_kind(e.receipt_ref),
           'binding_id', e.id::text) order by e.criterion_id), '[]'::jsonb)
    into bindings
    from ops.work_request_criterion_evidence e where e.work_request_id = w.id;
  verdict := ops.work_request_bound_evidence_verdict(w.acceptance_criteria, bindings);
  if not (verdict ->> 'complete')::boolean then
    return query select w.id, w.ref, w.state, w.version, false, verdict -> 'missing',
      null::uuid, f.feedback_ref, f.feedback_hash, null::text, null::jsonb, null::text, null::timestamptz, false;
    return;
  end if;

  insert into ops.sourced_work_request_outcome_feedback_acceptance_receipt
    (work_request_id, feedback_id, idempotency_key, base_version, feedback_hash,
     accepted_by_actor_id, result_version, acceptance_basis, bound_receipt_refs)
  values (w.id, f.id, p_idempotency_key, w.version, f.feedback_hash, a.id, w.version,
          'completed_on_bound_evidence', verdict -> 'receipts')
  returning * into fr;
  return query select w.id, w.ref, w.state, w.version, true, '[]'::jsonb, fr.id, f.feedback_ref,
    f.feedback_hash, fr.acceptance_basis, fr.bound_receipt_refs, a.slug, fr.accepted_at, false;
end $fn$;

comment on function ops.complete_sourced_work_request_on_bound_evidence(text, uuid) is
  'Completion on bound evidence (0858): writes an outcome-feedback acceptance receipt with acceptance_basis completed_on_bound_evidence when the latest proposal for the current ready plan is criteria_met and every acceptance criterion carries a receipt that resolves now. Otherwise writes nothing and returns what is missing.';

-- Bind one criterion of a ready sourced Work Request to one receipt, then try
-- to complete. Idempotent on the key; the same receipt twice is the same act.
create function ops.bind_work_request_criterion_evidence(p_work_request text, p_criterion_id text,
                                                         p_receipt_ref text, p_idempotency_key uuid)
returns table(binding_id uuid, work_request_id uuid, ref text, criterion_id text, receipt_ref text,
              receipt_kind text, bound_by_actor_slug text, created_at timestamptz, replayed boolean)
language plpgsql security definer set search_path = pg_catalog, ops, public, pg_temp as $fn$
declare
  actor_slug text; a public.actor%rowtype; w ops.work_request%rowtype;
  e ops.work_request_criterion_evidence%rowtype; kind text;
begin
  if coalesce(btrim(p_work_request), '') !~ '^WR-[0-9]{1,12}$'
     or coalesce(p_criterion_id, '') !~ '^[A-Z][A-Z0-9-]{1,63}$'
     or coalesce(p_receipt_ref, '') = '' or char_length(p_receipt_ref) > 300
     or p_idempotency_key is null then
    raise exception 'criterion evidence binding requires an exact Work Request ref, criterion id, receipt reference and UUID idempotency key';
  end if;
  actor_slug := nullif(btrim(current_setting('carr.acting_actor_slug', true)), '');
  select x.* into a from public.actor x where x.slug = actor_slug and x.active;
  if not found then
    raise exception 'criterion evidence binding requires a server-derived acting actor';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('work-request-criterion-evidence:' || p_idempotency_key, 0));
  select x.* into e from ops.work_request_criterion_evidence x where x.idempotency_key = p_idempotency_key;
  if found then
    select x.* into w from ops.work_request x where x.id = e.work_request_id;
    if w.ref is distinct from p_work_request or e.criterion_id is distinct from p_criterion_id
       or e.receipt_ref is distinct from p_receipt_ref then
      raise exception 'idempotency key already names a different criterion evidence binding';
    end if;
    return query select e.id, w.id, w.ref, e.criterion_id, e.receipt_ref, e.receipt_kind,
      (select y.slug from public.actor y where y.id = e.bound_by_actor_id), e.created_at, true;
    return;
  end if;

  select x.* into w from ops.work_request x where x.ref = p_work_request for update;
  if not found then raise exception 'exact sourced Work Request not found'; end if;
  if w.capture_idempotency_key is null or w.organization_tenant_id is distinct from 'carr-internal'
     or w.state is distinct from 'ready' then
    raise exception 'criterion evidence binds only to a ready sourced Work Request';
  end if;
  if not exists (select 1 from jsonb_array_elements(w.acceptance_criteria) c where c.value ->> 'id' = p_criterion_id) then
    raise exception 'criterion % is not one of this Work Request''s acceptance criteria', p_criterion_id;
  end if;
  kind := ops.work_request_evidence_receipt_kind(p_receipt_ref);
  if kind is null then
    raise exception 'receipt % does not resolve to a succeeded run, a complete deployment, a defect resolution or a resolved incident', p_receipt_ref;
  end if;

  select x.* into e from ops.work_request_criterion_evidence x
   where x.work_request_id = w.id and x.criterion_id = p_criterion_id;
  if found then
    if e.receipt_ref is distinct from p_receipt_ref then
      raise exception 'criterion % is already bound to %', p_criterion_id, e.receipt_ref;
    end if;
    return query select e.id, w.id, w.ref, e.criterion_id, e.receipt_ref, e.receipt_kind,
      (select y.slug from public.actor y where y.id = e.bound_by_actor_id), e.created_at, true;
    return;
  end if;

  insert into ops.work_request_criterion_evidence
    (work_request_id, criterion_id, receipt_ref, receipt_kind, bound_by_actor_id, idempotency_key)
  values (w.id, p_criterion_id, p_receipt_ref, kind, a.id, p_idempotency_key)
  returning * into e;
  return query select e.id, w.id, w.ref, e.criterion_id, e.receipt_ref, e.receipt_kind, a.slug, e.created_at, false;
end $fn$;

comment on function ops.bind_work_request_criterion_evidence(text, text, text, uuid) is
  'Bind one acceptance criterion of a ready sourced Work Request to one receipt that resolves through ops.work_request_evidence_receipt_kind (0858). Append-only; a different receipt for a bound criterion is refused.';

revoke all on function ops.work_request_evidence_receipt_kind(text) from public;
revoke all on function ops.work_request_bound_evidence_verdict(jsonb, jsonb) from public;
revoke all on function ops.complete_sourced_work_request_on_bound_evidence(text, uuid) from public;
revoke all on function ops.bind_work_request_criterion_evidence(text, text, text, uuid) from public;
grant execute on function ops.work_request_evidence_receipt_kind(text) to carr_writer, carr_reader;
grant execute on function ops.work_request_bound_evidence_verdict(jsonb, jsonb) to carr_writer, carr_reader;
grant execute on function ops.complete_sourced_work_request_on_bound_evidence(text, uuid) to carr_writer;
grant execute on function ops.bind_work_request_criterion_evidence(text, text, text, uuid) to carr_writer;
