-- 0628_doctorcre_slice_done_marker.sql
--
-- DoctorCRE v5 slice done-record: make it fire.
--
-- DIAGNOSIS (verified 2026-09-25 against production, read-only): 0 rows in
-- ops.slice_checkable_done_registration, 0 in ops.slice_checkable_done_registry,
-- 0 in ops.slice_completion_mark, 0 cutover plans. Nothing ever called the Q153
-- doors 0602 built: no criteria were registered and no step marked a slice
-- after it shipped. Two contributing causes: (1) registering and completing
-- were partner-authority acts no process performs; (2) the only evidence kinds
-- were workflow acceptance and cutover transitions, so an infrastructure or
-- gate slice (V5-F08 restore proof, V5-J303 field allowlist) had no evidence it
-- could ever be proven by.
--
-- THIS MIGRATION (0602's guarantees are kept, not weakened):
--
--   * Criteria come from the catalog, server-side.
--     ops.register_slice_criteria_from_catalog takes NO criteria argument: it
--     reads the slice's checkable_done list out of the current revision of the
--     doctrine section `v5-reviewed-implementation-slice-catalog-and-parallel-
--     groups-2026-09-09` and registers exactly that list, verbatim, each as
--     evidence_kind='unbound'. A caller cannot invent, drop or reword one.
--
--   * Four new server-resolved evidence kinds, alongside acceptance/transition:
--       shipped_release: evidence_ref names an ops.release_slice_member row --
--         a merged commit attributed to this slice, recorded against a
--         production ops.release whose state is 'complete'.
--       live_check: evidence_ref names a success row in one allowlisted,
--         server-written receipt table (staging_restore_only_result,
--         completion_receipt by collector, job_receipt by job definition),
--         or portfolio_acceptance_effect_free: the ref is a portfolio
--         acceptance receipt and the server counts ops.job, capability-session
--         and execution-envelope rows created within 2s of that revision's
--         propose/review/accept events; zero passes.
--       accepted_record: evidence_ref names a partner acceptance receipt
--         (source portfolio_revision_acceptance, key = portfolio_ref). Passes
--         only while that revision is the portfolio's current accepted one
--         AND ops.portfolio_revision_integrity_error recomputes it intact from
--         its rows (21 nodes, four children, acyclic, every digest).
--       refusal_proof: resolves ONLY against a gate result the server itself
--         recorded, keyed by the gate key. No such server-recorded gate-result
--         source exists today, so every bind of this kind is refused
--         (refusal_proof_has_no_server_gate_source) and the resolver never
--         passes it: a refusal criterion stays unbound. The kind is kept so the
--         source can be added later without reshaping the tables.
--     The server resolves every ref itself; a caller's pass claim is never read.
--     live_check_source / live_check_key carry the source and key of
--     live_check, accepted_record and refusal_proof alike.
--
--   * An unbound criterion can be bound later, append-only, in
--     ops.slice_criterion_binding. The automation seat may bind a criterion
--     ONCE, only to a kind the server derives as allowed for that criterion's
--     wording (ops.slice_criterion_allowed_kinds), and never while a partner
--     holds the slice. A partner may rebind (or explicitly unbind) at any
--     time, and a partner binding always wins.
--
--   * A shipped_release binding names ONE release member (one PR) at bind
--     time, and only that member resolves it. The Worker holds no GitHub
--     credential, so the server cannot check that a commit is reachable from
--     main or that its PR carries the slice id; therefore an AUTOMATED
--     shipped_release binding is only a PROPOSAL: it is never the effective
--     binding until a partner confirms it (rebind to the same member). The
--     server also refuses a member whose subject does not name the slice.
--
--   * The automated seat (ops.slice_marker_seat: the local machine actors)
--     may register from the catalog, bind, record release membership, mark
--     in_progress/blocked and PROPOSE complete through its own writer doors
--     (Joe's standing maximum-automation ruling, decision b729859d). It can
--     never SET complete: there is no automation door to complete, the shared
--     complete body refuses any caller but a partner, and a CHECK on
--     ops.slice_completion_mark refuses a complete row not marked_via
--     'authority'.
--
--   * Complete is a partner act. ops.propose_slice_completion (seat) records
--     a proposal in ops.slice_completion_proposal, recomputed from
--     server-resolved evidence. ops.confirm_slice_completions (authority,
--     actor from ops.authority_actor_slug()) confirms many slices in one call:
--     per slice it re-evaluates the latest proposal's receipt NOW against each
--     criterion's effective binding and writes complete only when every
--     criterion still resolves, no partner holds the slice, and no mark was
--     written after the proposal. ops.mark_slice_completion (authority) stays
--     the direct partner door.
--
--   * Partner override. ops.set_slice_mark_hold (authority) appends a held
--     in_progress/blocked mark; while a partner mark is the latest, every
--     non-authority mark, bind and proposal is refused, and confirmation
--     reports the slice held. The partner releases the hold with the same
--     door; a proposal made before the hold is stale and must be re-proposed.
--
--   * One lock per slice. Every door that reads or writes a slice's marks,
--     bindings, proposals or registration first takes
--     pg_advisory_xact_lock(hashtextextended('slice-done:' || slice_id, 0)),
--     so a hold, a bind, a proposal and a confirmation of the same slice are
--     serialised and a later one always sees the earlier one's commit.
--
-- Nothing here marks any slice. The marker (ops/slice-done-marker.py) proposes
-- through the verbs; a partner confirms.

-- ===========================================================================
-- The automated seat
-- ===========================================================================
create table if not exists ops.slice_marker_seat (
  actor_slug text primary key check (btrim(actor_slug) <> ''),
  note text not null,
  created_at timestamptz not null default now()
);

comment on table ops.slice_marker_seat is
  'DoctorCRE v5 slice done-record: the non-human actor slugs (carr.acting_actor_slug, server-derived by the MCP layer) allowed to register slice criteria from the catalog, bind unbound criteria once, record release membership, mark progress and PROPOSE completion. Only a partner confirms complete.';

revoke all on table ops.slice_marker_seat from public, carr_writer, carr_jobs, carr_authority;
grant select on table ops.slice_marker_seat to carr_reader;

insert into ops.slice_marker_seat (actor_slug, note) values
  ('joe-local', 'Joe''s machine credential; the release pipeline and run.sh call act as it'),
  ('dell-local', 'Dell''s machine credential')
on conflict (actor_slug) do nothing;

create or replace function ops.slice_marker_seat_actor()
returns text
language plpgsql stable security definer
set search_path = pg_catalog, ops
as $$
declare
  v_actor text := nullif(btrim(coalesce(current_setting('carr.acting_actor_slug', true), '')), '');
begin
  if v_actor is null or not exists (select 1 from ops.slice_marker_seat where actor_slug = v_actor) then
    raise exception 'slice_marker_seat_required: actor % is not an automated slice-marker seat', coalesce(v_actor, '(none)');
  end if;
  return v_actor;
end;
$$;

revoke all on function ops.slice_marker_seat_actor() from public;

-- The per-slice lock every slice done-record door takes first. Transaction-
-- scoped: released at commit or rollback. Re-entrant within a transaction.
create or replace function ops.slice_done_lock(p_slice_id text)
returns void
language sql volatile
set search_path = pg_catalog
as $$
  select pg_advisory_xact_lock(hashtextextended('slice-done:' || coalesce(p_slice_id, ''), 0))
$$;

revoke all on function ops.slice_done_lock(text) from public;

-- ===========================================================================
-- The catalog, read server-side
-- ===========================================================================
create or replace function ops.slice_catalog_checkable_done(p_slice_id text)
returns table (revision_id uuid, criteria text[])
language plpgsql stable security definer
set search_path = pg_catalog, ops, public
as $$
declare
  v_revision uuid;
  v_body jsonb;
  v_slice jsonb;
begin
  select r.id, (r.body->>'text')::jsonb into v_revision, v_body
    from public.doctrine_section s
    join public.doctrine_document d on d.id = s.document_id
    join public.doctrine_revision r on r.id = s.current_revision_id
   where d.slug = 'doctorcre-v5-astra-integration-review'
     and s.section_key = 'v5-reviewed-implementation-slice-catalog-and-parallel-groups-2026-09-09'
     and s.status = 'active';
  if v_revision is null then
    raise exception 'slice_catalog_unavailable';
  end if;
  select el into v_slice
    from jsonb_array_elements(coalesce(v_body->'slices', '[]'::jsonb)) el
   where el->>'proposed_id' = p_slice_id;
  if v_slice is null then
    raise exception 'slice_not_in_catalog: %', p_slice_id;
  end if;
  if jsonb_typeof(v_slice->'checkable_done') <> 'array' or jsonb_array_length(v_slice->'checkable_done') = 0 then
    raise exception 'slice_catalog_has_no_checkable_done: %', p_slice_id;
  end if;
  return query
    select v_revision, array(select btrim(c) from jsonb_array_elements_text(v_slice->'checkable_done') c);
end;
$$;

revoke all on function ops.slice_catalog_checkable_done(text) from public;

-- ===========================================================================
-- Registry: new evidence kinds
-- ===========================================================================
alter table ops.slice_checkable_done_registry
  alter column workflow_key drop not null,
  alter column workflow_version drop not null,
  add column if not exists live_check_source text,
  add column if not exists live_check_key text,
  add column if not exists write_required_reason text;

alter table ops.slice_checkable_done_registry
  drop constraint if exists slice_checkable_done_registry_evidence_kind_check,
  drop constraint if exists slice_checkable_done_registry_check;

-- Each `is not null` precedes its disjunction: a NULL source would make the
-- disjunction NULL, which a CHECK accepts.
alter table ops.slice_checkable_done_registry
  add constraint slice_checkable_done_registry_evidence_kind_check check (
    evidence_kind in ('acceptance', 'transition', 'shipped_release', 'live_check',
                      'accepted_record', 'refusal_proof', 'unbound')),
  add constraint slice_checkable_done_registry_binding_check check (
    (evidence_kind = 'acceptance' and workflow_key is not null and workflow_version is not null
       and acceptance_mode is not null and transition_to_stage is null
       and live_check_source is null and live_check_key is null and write_required_reason is null)
    or (evidence_kind = 'transition' and workflow_key is not null and workflow_version is not null
       and transition_to_stage is not null and acceptance_mode is null
       and live_check_source is null and live_check_key is null and write_required_reason is null)
    or (evidence_kind in ('shipped_release', 'unbound') and workflow_key is null and workflow_version is null
       and acceptance_mode is null and transition_to_stage is null
       and live_check_source is null and live_check_key is null and write_required_reason is null)
    or (evidence_kind in ('live_check', 'accepted_record', 'refusal_proof')
       and workflow_key is null and workflow_version is null
       and acceptance_mode is null and transition_to_stage is null
       and live_check_source is not null
       and ((evidence_kind = 'live_check' and write_required_reason is null
             and ((live_check_source = 'staging_restore_only_result' and live_check_key is null)
               or (live_check_source in ('completion_receipt', 'job_receipt', 'portfolio_acceptance_effect_free')
                   and live_check_key is not null and btrim(live_check_key) <> '')))
         or (evidence_kind = 'accepted_record' and write_required_reason is null
             and live_check_source = 'portfolio_revision_acceptance'
             and live_check_key is not null and btrim(live_check_key) <> '')
         or (evidence_kind = 'refusal_proof' and live_check_source = 'ci_gate'
             and live_check_key is not null and btrim(live_check_key) <> ''
             and write_required_reason is not null and btrim(write_required_reason) <> '')))
  );

alter table ops.slice_checkable_done_registration
  add column if not exists registered_via text not null default 'authority'
    check (registered_via in ('authority', 'automation')),
  add column if not exists catalog_revision_id uuid;

-- ===========================================================================
-- Late, append-only bindings for criteria registered 'unbound'
-- ===========================================================================
create table if not exists ops.slice_criterion_binding (
  id uuid primary key default gen_random_uuid(),
  slice_id text not null,
  criterion text not null,
  evidence_kind text not null check (evidence_kind in (
    'shipped_release', 'live_check', 'accepted_record', 'refusal_proof', 'unbound')),
  live_check_source text,
  live_check_key text,
  write_required_reason text,
  bound_member_id uuid,
  bound_via text not null check (bound_via in ('authority', 'automation')),
  bound_by_actor_slug text not null,
  reason text not null check (btrim(reason) <> ''),
  idempotency_key uuid not null unique,
  bind_seq bigint generated always as identity unique,
  created_at timestamptz not null default now(),
  foreign key (slice_id, criterion) references ops.slice_checkable_done_registry (slice_id, criterion),
  check (
    (evidence_kind = 'shipped_release' and live_check_source is null and live_check_key is null
       and write_required_reason is null and bound_member_id is not null)
    or (evidence_kind = 'unbound' and live_check_source is null and live_check_key is null
       and write_required_reason is null and bound_member_id is null)
    -- `is not null` first: a NULL source would make the disjunction NULL,
    -- which a CHECK accepts.
    or (evidence_kind = 'live_check' and live_check_source is not null and write_required_reason is null
        and ((live_check_source = 'staging_restore_only_result' and live_check_key is null)
          or (live_check_source in ('completion_receipt', 'job_receipt', 'portfolio_acceptance_effect_free')
              and live_check_key is not null and btrim(live_check_key) <> '')))
    or (evidence_kind = 'accepted_record' and live_check_source is not null and write_required_reason is null
        and live_check_source = 'portfolio_revision_acceptance'
        and live_check_key is not null and btrim(live_check_key) <> '')
    or (evidence_kind = 'refusal_proof' and live_check_source is not null
        and live_check_source = 'ci_gate'
        and live_check_key is not null and btrim(live_check_key) <> ''
        and write_required_reason is not null and btrim(write_required_reason) <> '')
  )
);

-- The automation seat binds a criterion at most once.
create unique index if not exists slice_criterion_binding_one_automation
  on ops.slice_criterion_binding (slice_id, criterion) where bound_via = 'automation';

comment on table ops.slice_criterion_binding is
  'DoctorCRE v5: append-only evidence bindings for criteria registered evidence_kind=unbound. Effective binding: the latest authority binding, else the single automation binding, else unbound. Never updated or deleted.';

revoke all on table ops.slice_criterion_binding from public, carr_writer, carr_jobs, carr_authority;
grant select on table ops.slice_criterion_binding to carr_reader;

create or replace function ops.refuse_slice_criterion_binding_rewrite()
returns trigger language plpgsql as $$
begin
  raise exception 'slice_criterion_binding is append-only';
end $$;

create trigger slice_criterion_binding_append_only
  before update or delete on ops.slice_criterion_binding
  for each row execute function ops.refuse_slice_criterion_binding_rewrite();

-- The kinds the AUTOMATED seat may bind a criterion to, derived server-side
-- from the criterion's own catalog wording, as 'kind:source' ('kind:' when the
-- kind has no source). The seat cannot pick a weaker kind than this allows; a
-- partner rebind is not limited by it. Order matters: the first rule that
-- matches decides. The two "nothing" rules come first, so wording that names a
-- refusal or a human observation is never bound by a later, looser rule
-- ("restore into production is refused", "Joe observes zero side effects").
-- Whatever the seat binds, it can only PROPOSE complete; a partner confirms.
--   refuse / negative / bypass   -> nothing (refusal_proof needs a server-
--                                   recorded gate result, and none exists)
--   runtime / human outcomes     -> nothing (no server row can show them)
--   restore wording              -> live_check on staging_restore_only_result
--   acyclic (the portfolio graph)-> accepted_record on the portfolio acceptance
--   zero ... effects             -> live_check portfolio_acceptance_effect_free
--   anything else                -> shipped_release (a proposal until a partner
--                                   confirms it)
create or replace function ops.slice_criterion_allowed_kinds(p_criterion text)
returns text[]
language sql immutable
set search_path = pg_catalog
as $$
  select case
    when p_criterion ~* '(refus|negative|bypass)' then array[]::text[]
    when p_criterion ~* '(observ|pilot|decid|measur|partner|joe|dell|week|month|adopt|survey|feedback|interview|one-use|fresh exact)'
      then array[]::text[]
    when p_criterion ~* 'restor' then array['live_check:staging_restore_only_result']
    when p_criterion ~* 'acyclic' then array['accepted_record:portfolio_revision_acceptance']
    when p_criterion ~* 'zero' and p_criterion ~* 'effect' then array['live_check:portfolio_acceptance_effect_free']
    else array['shipped_release:']
  end
$$;

revoke all on function ops.slice_criterion_allowed_kinds(text) from public;
grant execute on function ops.slice_criterion_allowed_kinds(text) to carr_reader;

-- The one portfolio the catalog's slices belong to. An automated
-- accepted_record / effect-free binding may name only this portfolio, so the
-- seat cannot point a criterion at some other accepted portfolio.
create or replace function ops.slice_catalog_portfolio_ref()
returns text
language sql immutable
set search_path = pg_catalog
as $$ select 'DoctorCre-v5'::text $$;

revoke all on function ops.slice_catalog_portfolio_ref() from public;

-- The ONE effective binding of a registered criterion.
create or replace function ops.slice_effective_binding(p_slice_id text, p_criterion text)
returns table (
  evidence_kind text, workflow_key text, workflow_version integer,
  acceptance_mode text, transition_to_stage text,
  live_check_source text, live_check_key text, write_required_reason text,
  bound_member_id uuid, binding_source text, binding_id uuid
)
language plpgsql stable
set search_path = pg_catalog, ops
as $$
declare
  v_reg ops.slice_checkable_done_registry%rowtype;
  v_bind ops.slice_criterion_binding%rowtype;
begin
  select * into v_reg from ops.slice_checkable_done_registry r
   where r.slice_id = p_slice_id and r.criterion = p_criterion;
  if not found then
    raise exception 'slice_criterion_not_registered: % / %', p_slice_id, p_criterion;
  end if;
  if v_reg.evidence_kind <> 'unbound' then
    return query select v_reg.evidence_kind, v_reg.workflow_key, v_reg.workflow_version,
      v_reg.acceptance_mode, v_reg.transition_to_stage, v_reg.live_check_source, v_reg.live_check_key,
      v_reg.write_required_reason, null::uuid, 'registration'::text, v_reg.id;
    return;
  end if;
  select * into v_bind from ops.slice_criterion_binding b
   where b.slice_id = p_slice_id and b.criterion = p_criterion and b.bound_via = 'authority'
   order by b.bind_seq desc limit 1;
  if not found then
    -- An automated shipped_release binding is a PROPOSAL, never effective:
    -- the server cannot verify the PR, so a partner must confirm it.
    select * into v_bind from ops.slice_criterion_binding b
     where b.slice_id = p_slice_id and b.criterion = p_criterion and b.bound_via = 'automation'
       and b.evidence_kind <> 'shipped_release';
  end if;
  if v_bind.id is null then
    return query select 'unbound'::text, null::text, null::integer, null::text, null::text,
      null::text, null::text, null::text, null::uuid, 'registration'::text, v_reg.id;
    return;
  end if;
  return query select v_bind.evidence_kind, null::text, null::integer, null::text, null::text,
    v_bind.live_check_source, v_bind.live_check_key, v_bind.write_required_reason,
    v_bind.bound_member_id, ('binding:' || v_bind.bound_via)::text, v_bind.id;
end;
$$;

revoke all on function ops.slice_effective_binding(text, text) from public;

create or replace function ops.slice_bind_insert(
  p_slice_id text, p_criterion text, p_kind text, p_source text, p_key text,
  p_write_required_reason text, p_bound_member_id uuid, p_reason text, p_idempotency_key uuid,
  p_via text, p_actor text
) returns ops.slice_criterion_binding
language plpgsql
set search_path = pg_catalog, ops
as $$
declare
  v_existing ops.slice_criterion_binding%rowtype;
  v_reg ops.slice_checkable_done_registry%rowtype;
  v_row ops.slice_criterion_binding%rowtype;
begin
  perform ops.slice_done_lock(p_slice_id);
  if p_idempotency_key is null then
    raise exception 'idempotency_key_required';
  end if;
  select * into v_existing from ops.slice_criterion_binding where idempotency_key = p_idempotency_key;
  if found then
    if v_existing.slice_id is distinct from p_slice_id or v_existing.criterion is distinct from btrim(p_criterion)
       or v_existing.evidence_kind is distinct from p_kind or v_existing.bound_via is distinct from p_via then
      raise exception 'idempotency_key_reused_for_a_different_binding';
    end if;
    return v_existing;
  end if;
  select * into v_reg from ops.slice_checkable_done_registry
   where slice_id = p_slice_id and criterion = btrim(coalesce(p_criterion, ''));
  if not found then
    raise exception 'slice_criterion_not_registered: % / %', p_slice_id, p_criterion;
  end if;
  if v_reg.evidence_kind <> 'unbound' then
    raise exception 'criterion_bound_at_registration: %', v_reg.criterion;
  end if;
  if p_reason is null or btrim(p_reason) = '' then
    raise exception 'reason_required';
  end if;
  if p_kind = 'refusal_proof' then
    raise exception 'refusal_proof_has_no_server_gate_source: no server-recorded gate result exists to resolve it; the criterion stays unbound';
  end if;
  if p_via = 'automation' then
    if ops.slice_mark_held(p_slice_id) then
      raise exception 'slice_mark_held_by_partner: %', p_slice_id;
    end if;
    if not (p_kind || ':' || coalesce(p_source, '')) = any (ops.slice_criterion_allowed_kinds(v_reg.criterion)) then
      raise exception 'automation_binding_kind_not_allowed: % may be bound only to %', v_reg.criterion,
        coalesce(nullif(array_to_string(ops.slice_criterion_allowed_kinds(v_reg.criterion), ', '), ''), 'nothing (a partner decides)');
    end if;
    if p_source in ('portfolio_revision_acceptance', 'portfolio_acceptance_effect_free')
       and p_key is distinct from ops.slice_catalog_portfolio_ref() then
      raise exception 'automation_portfolio_key_not_catalog_portfolio: %', coalesce(p_key, '(none)');
    end if;
    if exists (select 1 from ops.slice_criterion_binding
                where slice_id = p_slice_id and criterion = v_reg.criterion) then
      raise exception 'criterion_already_bound: only a partner may rebind %', v_reg.criterion;
    end if;
  end if;
  if p_kind = 'shipped_release' then
    -- One specific PR, named now: a member of THIS slice in a complete
    -- production release.
    if p_bound_member_id is null or not exists (
      select 1 from ops.release_slice_member m join ops.release r on r.id = m.release_id
       where m.id = p_bound_member_id and m.slice_id = p_slice_id
         and r.environment = 'production' and r.state = 'complete' and r.git_sha = m.release_git_sha) then
      raise exception 'shipped_release_binding_requires_this_slice_member: %', coalesce(p_bound_member_id::text, '(none)');
    end if;
  elsif p_bound_member_id is not null then
    raise exception 'bound_member_only_for_shipped_release';
  end if;
  insert into ops.slice_criterion_binding (
    slice_id, criterion, evidence_kind, live_check_source, live_check_key, write_required_reason,
    bound_member_id, bound_via, bound_by_actor_slug, reason, idempotency_key
  ) values (
    p_slice_id, v_reg.criterion, p_kind, p_source, p_key, p_write_required_reason,
    p_bound_member_id, p_via, p_actor, p_reason, p_idempotency_key
  ) returning * into v_row;
  return v_row;
end;
$$;

revoke all on function ops.slice_bind_insert(text, text, text, text, text, text, uuid, text, uuid, text, text) from public;

-- Automation door: bind an unbound criterion once.
create or replace function ops.bind_slice_criterion_evidence(
  p_slice_id text, p_criterion text, p_evidence_kind text,
  p_live_check_source text, p_live_check_key text, p_write_required_reason text,
  p_bound_member_id uuid, p_reason text, p_idempotency_key uuid
) returns ops.slice_criterion_binding
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
begin
  return ops.slice_bind_insert(p_slice_id, p_criterion, p_evidence_kind, p_live_check_source,
    p_live_check_key, p_write_required_reason, p_bound_member_id, p_reason, p_idempotency_key, 'automation', ops.slice_marker_seat_actor());
end;
$$;

comment on function ops.bind_slice_criterion_evidence(text, text, text, text, text, text, uuid, text, uuid) is
  'DoctorCRE v5 automation door: bind a criterion registered unbound, once, to a kind ops.slice_criterion_allowed_kinds allows for its wording. A shipped_release binding names one release member of this slice and is only a proposal until a partner confirms it. Refused for any actor outside ops.slice_marker_seat, while a partner holds the slice, for a criterion bound at registration, and for a criterion already bound (only a partner may rebind).';

revoke all on function ops.bind_slice_criterion_evidence(text, text, text, text, text, text, uuid, text, uuid) from public;
grant execute on function ops.bind_slice_criterion_evidence(text, text, text, text, text, text, uuid, text, uuid) to carr_writer;

-- Partner door: rebind (or explicitly unbind) at any time; always wins.
create or replace function ops.rebind_slice_criterion_evidence(
  p_slice_id text, p_criterion text, p_evidence_kind text,
  p_live_check_source text, p_live_check_key text, p_write_required_reason text,
  p_bound_member_id uuid, p_reason text, p_idempotency_key uuid
) returns ops.slice_criterion_binding
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
begin
  return ops.slice_bind_insert(p_slice_id, p_criterion, p_evidence_kind, p_live_check_source,
    p_live_check_key, p_write_required_reason, p_bound_member_id, p_reason, p_idempotency_key, 'authority', ops.authority_actor_slug());
end;
$$;

comment on function ops.rebind_slice_criterion_evidence(text, text, text, text, text, text, uuid, text, uuid) is
  'DoctorCRE v5 partner authority door: bind, rebind or explicitly unbind a criterion registered unbound, or confirm an automated shipped_release proposal by rebinding it to the same member. The latest partner binding is the effective binding, whatever automation bound.';

revoke all on function ops.rebind_slice_criterion_evidence(text, text, text, text, text, text, uuid, text, uuid) from public;
grant execute on function ops.rebind_slice_criterion_evidence(text, text, text, text, text, text, uuid, text, uuid) to carr_authority;

-- ===========================================================================
-- Registration from the catalog (automation)
-- ===========================================================================
create or replace function ops.register_slice_criteria_from_catalog(
  p_slice_id text,
  p_idempotency_key uuid
) returns setof ops.slice_checkable_done_registry
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  v_actor text;
  v_existing ops.slice_checkable_done_registration%rowtype;
  v_revision uuid;
  v_criteria text[];
  v_c text;
begin
  v_actor := ops.slice_marker_seat_actor();
  perform ops.slice_done_lock(p_slice_id);
  if p_idempotency_key is null then
    raise exception 'idempotency_key_required';
  end if;
  select * into v_existing from ops.slice_checkable_done_registration where idempotency_key = p_idempotency_key;
  if found then
    if v_existing.slice_id is distinct from p_slice_id then
      raise exception 'idempotency_key_reused_for_a_different_registration';
    end if;
    return query select * from ops.slice_checkable_done_registry where slice_id = v_existing.slice_id;
    return;
  end if;
  if p_slice_id is null or btrim(p_slice_id) = '' then
    raise exception 'slice_id_required';
  end if;
  if exists (select 1 from ops.slice_checkable_done_registration where slice_id = p_slice_id) then
    raise exception 'slice_checkable_done_already_registered: %', p_slice_id;
  end if;
  select c.revision_id, c.criteria into v_revision, v_criteria from ops.slice_catalog_checkable_done(p_slice_id) c;
  if (select count(distinct x) from unnest(v_criteria) x) <> cardinality(v_criteria) then
    raise exception 'slice_catalog_criteria_not_distinct: %', p_slice_id;
  end if;

  insert into ops.slice_checkable_done_registration (
    slice_id, registered_by_actor_slug, idempotency_key, registered_via, catalog_revision_id
  ) values (p_slice_id, v_actor, p_idempotency_key, 'automation', v_revision);
  foreach v_c in array v_criteria loop
    insert into ops.slice_checkable_done_registry (slice_id, criterion, evidence_kind)
    values (p_slice_id, v_c, 'unbound');
  end loop;
  return query select * from ops.slice_checkable_done_registry where slice_id = p_slice_id;
end;
$$;

comment on function ops.register_slice_criteria_from_catalog(text, uuid) is
  'DoctorCRE v5 automation door: register a slice''s checkable_done criteria exactly as the current catalog doctrine revision states them (no caller-supplied criteria), each evidence_kind=unbound. Seat-only. Idempotent on p_idempotency_key.';

revoke all on function ops.register_slice_criteria_from_catalog(text, uuid) from public;
grant execute on function ops.register_slice_criteria_from_catalog(text, uuid) to carr_writer;

-- The partner registration door (0602) keeps its signature and accepts the new
-- kinds. Workflow kinds still require a registered workflow; live_check needs
-- an allowlisted source.
create or replace function ops.register_slice_checkable_done(
  p_slice_id text,
  p_criteria jsonb,
  p_idempotency_key uuid
) returns setof ops.slice_checkable_done_registry
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  v_authority_actor text;
  v_existing ops.slice_checkable_done_registration%rowtype;
  v_el jsonb;
  v_kind text;
  v_key text;
  v_version integer;
begin
  v_authority_actor := ops.authority_actor_slug();
  perform ops.slice_done_lock(p_slice_id);
  if p_idempotency_key is null then
    raise exception 'idempotency_key_required';
  end if;
  select * into v_existing from ops.slice_checkable_done_registration
   where idempotency_key = p_idempotency_key;
  if found then
    return query select * from ops.slice_checkable_done_registry where slice_id = v_existing.slice_id;
    return;
  end if;
  if p_slice_id is null or btrim(p_slice_id) = '' then
    raise exception 'slice_id_required';
  end if;
  if exists (select 1 from ops.slice_checkable_done_registration where slice_id = p_slice_id) then
    raise exception 'slice_checkable_done_already_registered: %', p_slice_id;
  end if;
  if p_criteria is null or jsonb_typeof(p_criteria) <> 'array' or jsonb_array_length(p_criteria) = 0 then
    raise exception 'criteria_required_nonempty_array';
  end if;
  if (select count(distinct btrim(el->>'criterion')) from jsonb_array_elements(p_criteria) el)
     <> jsonb_array_length(p_criteria) then
    raise exception 'criteria_must_be_distinct';
  end if;

  insert into ops.slice_checkable_done_registration (slice_id, registered_by_actor_slug, idempotency_key, registered_via)
  values (p_slice_id, v_authority_actor, p_idempotency_key, 'authority');

  for v_el in select * from jsonb_array_elements(p_criteria)
  loop
    if jsonb_typeof(v_el) <> 'object' or coalesce(btrim(v_el->>'criterion'), '') = '' then
      raise exception 'criterion_required';
    end if;
    v_kind := v_el->>'evidence_kind';
    if v_kind in ('acceptance', 'transition') then
      v_key := v_el->>'workflow_key';
      if coalesce(jsonb_typeof(v_el->'workflow_version'), '') <> 'number' then
        raise exception 'criterion_workflow_version_required: %', v_el->>'criterion';
      end if;
      v_version := (v_el->>'workflow_version')::integer;
      if not exists (select 1 from ops.job_definition where key = v_key and version = v_version) then
        raise exception 'criterion_workflow_not_registered: % v%', v_key, v_version;
      end if;
    else
      v_key := null;
      v_version := null;
      if v_el ? 'workflow_key' or v_el ? 'workflow_version' then
        raise exception 'criterion_binding_mixes_evidence_types: %', v_el->>'criterion';
      end if;
    end if;
    if v_kind = 'acceptance' and v_el ? 'transition_to_stage' then
      raise exception 'criterion_binding_mixes_evidence_types: %', v_el->>'criterion';
    end if;
    if v_kind = 'transition' and v_el ? 'acceptance_mode' then
      raise exception 'criterion_binding_mixes_evidence_types: %', v_el->>'criterion';
    end if;
    if v_kind = 'refusal_proof' then
      raise exception 'refusal_proof_has_no_server_gate_source: %', v_el->>'criterion';
    end if;
    -- A shipped_release criterion must name ONE release member, which a
    -- registration cannot do: register it unbound and name the member with
    -- rebind-slice-criterion-evidence. Otherwise any member row the seat
    -- recorded for this slice would resolve it.
    if v_kind = 'shipped_release' then
      raise exception 'shipped_release_registration_needs_a_named_member: register % unbound and bind its member', v_el->>'criterion';
    end if;
    insert into ops.slice_checkable_done_registry (
      slice_id, criterion, evidence_kind, workflow_key, workflow_version,
      acceptance_mode, transition_to_stage, live_check_source, live_check_key, write_required_reason
    ) values (
      p_slice_id, btrim(v_el->>'criterion'), v_kind, v_key, v_version,
      v_el->>'acceptance_mode', v_el->>'transition_to_stage',
      v_el->>'live_check_source', v_el->>'live_check_key', v_el->>'write_required_reason'
    );
  end loop;

  return query select * from ops.slice_checkable_done_registry where slice_id = p_slice_id;
end;
$$;

comment on function ops.register_slice_checkable_done(text, jsonb, uuid) is
  'DoctorCRE V5-R02 / Q153 authority write door: registers, once, the checkable_done criteria of a slice_id, each bound to one evidence type (acceptance+mode or transition+stage on a registered workflow; shipped_release; live_check, accepted_record or refusal_proof on an allowlisted source; or unbound). The actor is ops.authority_actor_slug(). Idempotent on p_idempotency_key.';

revoke all on function ops.register_slice_checkable_done(text, jsonb, uuid) from public;
grant execute on function ops.register_slice_checkable_done(text, jsonb, uuid) to carr_authority;

-- ===========================================================================
-- Release membership: merged commits attributed to a slice, per production
-- release. The database cannot compute git ancestry; the automation seat
-- records which attributed merges a complete production release contains,
-- and the evaluator only accepts a member of a production release that
-- actually reached state='complete'.
-- ===========================================================================
create table if not exists ops.release_slice_member (
  id uuid primary key default gen_random_uuid(),
  release_id uuid not null references ops.release(id),
  release_git_sha text not null,
  slice_id text not null check (btrim(slice_id) <> ''),
  commit_sha text not null check (commit_sha ~ '^[0-9a-f]{40}$'),
  pr_number integer check (pr_number is null or pr_number > 0),
  subject text not null check (btrim(subject) <> '' and length(subject) <= 400),
  attribution text not null check (attribution in ('explicit', 'bare_id')),
  recorded_by_actor_slug text not null,
  created_at timestamptz not null default now(),
  unique (release_id, slice_id, commit_sha)
);

create index if not exists release_slice_member_slice_idx on ops.release_slice_member (slice_id);

comment on table ops.release_slice_member is
  'DoctorCRE v5: one row per (production release, slice, merged commit attributed to the slice). Evidence for evidence_kind=shipped_release. Append-only.';

revoke all on table ops.release_slice_member from public, carr_writer, carr_jobs, carr_authority;
grant select on table ops.release_slice_member to carr_reader;

create or replace function ops.refuse_release_slice_member_rewrite()
returns trigger language plpgsql as $$
begin
  raise exception 'release_slice_member is append-only';
end $$;

create trigger release_slice_member_append_only
  before update or delete on ops.release_slice_member
  for each row execute function ops.refuse_release_slice_member_rewrite();

-- p_members: [{"slice_id","commit_sha","pr_number","subject","attribution"}]
create or replace function ops.slice_subject_names_slice(p_subject text, p_slice_id text)
returns boolean
language sql immutable
set search_path = pg_catalog
as $$
  select coalesce(
    p_subject ~ ('(^|[^A-Za-z0-9-])' || p_slice_id || '($|[^A-Za-z0-9-])')
    or (substr(p_slice_id, 4) ~ '^([FAS][0-9]{2}|J[0-9]{3})$'
        and p_subject ~ ('(^|[^A-Za-z0-9-])' || substr(p_slice_id, 4) || '($|[^A-Za-z0-9-])')),
    false)
$$;

revoke all on function ops.slice_subject_names_slice(text, text) from public;

create or replace function ops.record_release_slice_members(
  p_release_key text,
  p_members jsonb
) returns setof ops.release_slice_member
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  v_actor text;
  v_release ops.release%rowtype;
  v_el jsonb;
begin
  v_actor := ops.slice_marker_seat_actor();
  select * into v_release from ops.release where release_key = p_release_key;
  if not found then
    raise exception 'release_not_found: %', p_release_key;
  end if;
  if v_release.environment <> 'production' or v_release.state <> 'complete' then
    raise exception 'release_not_complete_production: % (% / %)', p_release_key, v_release.environment, v_release.state;
  end if;
  if p_members is null or jsonb_typeof(p_members) <> 'array' or jsonb_array_length(p_members) = 0 then
    raise exception 'members_required_nonempty_array';
  end if;
  for v_el in select * from jsonb_array_elements(p_members) loop
    if jsonb_typeof(v_el) <> 'object' then
      raise exception 'member_must_be_object';
    end if;
    -- Only a slice the catalog names (raises slice_not_in_catalog otherwise).
    perform 1 from ops.slice_catalog_checkable_done(v_el->>'slice_id');
    -- The subject must NAME the slice: its full id, or (attribution rule A1)
    -- a bare F/A/S NN or J NNN id as a whole word. The database cannot see git,
    -- so this does not prove the commit is in the release; that is why an
    -- automated shipped_release binding stays a proposal until a partner
    -- confirms it.
    if not ops.slice_subject_names_slice(v_el->>'subject', v_el->>'slice_id') then
      raise exception 'member_subject_does_not_name_slice: % / %', v_el->>'slice_id', left(coalesce(v_el->>'subject', ''), 120);
    end if;
    if coalesce(v_el->>'commit_sha', '') !~ '^[0-9a-fA-F]{40}$' then
      raise exception 'member_commit_sha_invalid';
    end if;
    insert into ops.release_slice_member (
      release_id, release_git_sha, slice_id, commit_sha, pr_number, subject, attribution, recorded_by_actor_slug
    ) values (
      v_release.id, v_release.git_sha, v_el->>'slice_id', lower(v_el->>'commit_sha'),
      nullif(v_el->>'pr_number', '')::integer, left(btrim(v_el->>'subject'), 400),
      v_el->>'attribution', v_actor
    ) on conflict (release_id, slice_id, commit_sha) do nothing;
  end loop;
  return query select * from ops.release_slice_member where release_id = v_release.id order by slice_id, commit_sha;
end;
$$;

comment on function ops.record_release_slice_members(text, jsonb) is
  'DoctorCRE v5 automation door: record merged commits attributed to catalog slices as members of one complete production release. Seat-only; idempotent per (release, slice, commit).';

revoke all on function ops.record_release_slice_members(text, jsonb) from public;
grant execute on function ops.record_release_slice_members(text, jsonb) to carr_writer;

create or replace function ops.list_shipped_releases(p_since timestamptz)
returns table (release_key text, git_sha text, completed_at timestamptz, member_count bigint)
language sql stable security definer
set search_path = pg_catalog, ops
as $$
  select r.release_key, r.git_sha, coalesce(r.ended_at, r.updated_at) as completed_at,
         (select count(*) from ops.release_slice_member m where m.release_id = r.id)
    from ops.release r
   where r.environment = 'production' and r.state = 'complete'
     and coalesce(r.ended_at, r.updated_at) >= coalesce(p_since, '-infinity'::timestamptz)
   order by coalesce(r.ended_at, r.updated_at), r.release_key;
$$;

revoke all on function ops.list_shipped_releases(p_since timestamp with time zone) from public;
grant execute on function ops.list_shipped_releases(p_since timestamp with time zone) to carr_reader;

-- ===========================================================================
-- Marks: who marked, and the partner hold
-- ===========================================================================
alter table ops.slice_completion_mark
  add column if not exists marked_via text
    check (marked_via is null or marked_via in
      ('authority', 'automation', 'writer', 'authority_hold', 'authority_release')),
  add column if not exists confirmed_proposal_id uuid;

-- The terminal state is a partner act, at the table: no door, present or
-- future, can store a complete mark that a partner did not write. NULL-safe
-- (a complete row with no marked_via is refused, not waved through by a NULL
-- comparison) and VALID: it is checked against every existing row when it is
-- added, so the migration refuses rather than grandfathering a complete mark
-- no partner wrote.
alter table ops.slice_completion_mark
  add constraint slice_completion_mark_complete_is_partner
    check (status <> 'complete' or marked_via is not distinct from 'authority');

-- ===========================================================================
-- Proposals: automation's last word. A proposal is NOT a mark and changes no
-- slice's state; only ops.confirm_slice_completions (a partner) turns one
-- into a complete mark, after re-evaluating it.
-- ===========================================================================
create table if not exists ops.slice_completion_proposal (
  id uuid primary key default gen_random_uuid(),
  slice_id text not null references ops.slice_checkable_done_registration (slice_id),
  -- Recomputed server-side by ops.slice_completion_evaluate at proposal time;
  -- every element passed then. Confirmation recomputes it again.
  criteria_receipt jsonb not null check (jsonb_typeof(criteria_receipt) = 'array'),
  reason text not null check (btrim(reason) <> ''),
  proposed_by_actor_slug text not null,
  -- The slice's latest mark_seq when proposed (0 when unmarked). Any mark
  -- written after it makes the proposal stale.
  basis_mark_seq bigint not null,
  idempotency_key uuid not null unique,
  proposal_seq bigint generated always as identity unique,
  created_at timestamptz not null default now()
);

create index if not exists slice_completion_proposal_slice_idx
  on ops.slice_completion_proposal (slice_id, proposal_seq desc);

comment on table ops.slice_completion_proposal is
  'DoctorCRE v5: append-only proposals, by the automated slice-marker seat, that a slice is complete. Never a mark: a partner confirms with ops.confirm_slice_completions, which re-evaluates every criterion at that moment.';

revoke all on table ops.slice_completion_proposal from public, carr_writer, carr_jobs, carr_authority;
grant select on table ops.slice_completion_proposal to carr_reader;

create or replace function ops.refuse_slice_completion_proposal_rewrite()
returns trigger language plpgsql as $$
begin
  raise exception 'slice_completion_proposal is append-only';
end $$;

create trigger slice_completion_proposal_append_only
  before update or delete on ops.slice_completion_proposal
  for each row execute function ops.refuse_slice_completion_proposal_rewrite();

-- A partner mark (authority complete, or a hold) is the latest mark: every
-- non-authority mark is refused until the partner releases it.
create or replace function ops.slice_mark_held(p_slice_id text)
returns boolean
language sql stable
set search_path = pg_catalog, ops
as $$
  select coalesce((
    select m.marked_via in ('authority', 'authority_hold')
      from ops.slice_completion_mark m
     where m.slice_id = p_slice_id
     order by m.mark_seq desc limit 1
  ), false);
$$;

revoke all on function ops.slice_mark_held(text) from public;

-- ===========================================================================
-- The evaluator: every kind resolved server-side against the EFFECTIVE binding.
-- ===========================================================================
-- ===========================================================================
-- Evidence resolution: ONE body, used by the evaluator (a submitted ref) and
-- by the done-state read (the newest candidate). Every kind is a live read.
-- ===========================================================================
create or replace function ops.slice_portfolio_acceptance_effect_count(p_receipt_id uuid)
returns integer
language sql stable
set search_path = pg_catalog, ops, public
as $$
  -- Windows: the acceptance itself, and every event recorded on the revision
  -- (propose, review, accept), each +-2s.
  with rcpt as (
    select a.portfolio_revision_id, a.accepted_at
      from ops.portfolio_revision_acceptance_receipt a where a.id = p_receipt_id
  ), anchors as (
    select accepted_at as t from rcpt
    union
    select e.occurred_at from public.event e join rcpt on e.subject_id = rcpt.portfolio_revision_id
  )
  select (
      (select count(*) from ops.job x, anchors w
        where x.created_at between w.t - interval '2 seconds' and w.t + interval '2 seconds')
    + (select count(*) from ops.capability_agent_session x, anchors w
        where x.created_at between w.t - interval '2 seconds' and w.t + interval '2 seconds')
    + (select count(*) from ops.engineering_execution_envelope x, anchors w
        where x.created_at between w.t - interval '2 seconds' and w.t + interval '2 seconds')
    + (select count(*) from ops.execution_envelope_v1 x, anchors w
        where x.created_at between w.t - interval '2 seconds' and w.t + interval '2 seconds')
  )::integer
$$;

revoke all on function ops.slice_portfolio_acceptance_effect_count(uuid) from public;

create or replace function ops.slice_portfolio_acceptance_current(p_receipt_id uuid, p_portfolio_ref text)
returns boolean
language sql stable
set search_path = pg_catalog, ops
as $$
  -- The receipt names the portfolio, its revision is still the CURRENT
  -- accepted one, and the rows recompute intact (structure, acyclicity,
  -- graph/child/accepted digests, receipt digest).
  select coalesce((
    select ops.portfolio_current_accepted_revision(p_portfolio_ref) = a.portfolio_revision_id
       and ops.portfolio_revision_integrity_error(a.portfolio_revision_id) is null
      from ops.portfolio_revision_acceptance_receipt a
     where a.id = p_receipt_id and a.portfolio_ref = p_portfolio_ref
  ), false)
$$;

revoke all on function ops.slice_portfolio_acceptance_current(uuid, text) from public;

create or replace function ops.slice_evidence_resolves(
  p_slice_id text, p_kind text, p_workflow_key text, p_workflow_version integer,
  p_acceptance_mode text, p_transition_to_stage text, p_source text, p_key text,
  p_bound_member_id uuid, p_ref uuid
) returns boolean
language plpgsql stable
set search_path = pg_catalog, ops
as $$
declare
  v boolean := false;
begin
  if p_ref is null then
    return false;
  end if;
  if p_kind = 'acceptance' then
    select exists (
      select 1 from ops.workflow_acceptance a
       where a.id = p_ref and a.status = 'accepted'
         and a.workflow_key = p_workflow_key
         and a.workflow_version = p_workflow_version
         and a.mode = p_acceptance_mode
    ) into v;
  elsif p_kind = 'transition' then
    select exists (
      select 1 from ops.workflow_cutover_stage_transition t
        join ops.workflow_cutover_plan pl on pl.id = t.plan_id
       where t.id = p_ref
         and t.to_stage = p_transition_to_stage
         and pl.workflow_key = p_workflow_key
         and pl.workflow_version = p_workflow_version
    ) into v;
  elsif p_kind = 'shipped_release' then
    -- Only the ONE member named at bind time. A shipped_release binding that
    -- names no member (none can be registered that way) never resolves.
    if p_bound_member_id is null or p_ref <> p_bound_member_id then
      return false;
    end if;
    select exists (
      select 1 from ops.release_slice_member m
        join ops.release r on r.id = m.release_id
       where m.id = p_ref
         and m.slice_id = p_slice_id
         and r.environment = 'production'
         and r.state = 'complete'
         and r.git_sha = m.release_git_sha
    ) into v;
  elsif p_kind = 'accepted_record' and p_source = 'portfolio_revision_acceptance' then
    v := ops.slice_portfolio_acceptance_current(p_ref, p_key);
  elsif p_kind = 'live_check' then
    if p_source = 'staging_restore_only_result' then
      select exists (
        select 1 from ops.staging_restore_only_result x
         where x.id = p_ref and x.status = 'succeeded'
      ) into v;
    elsif p_source = 'completion_receipt' then
      select exists (
        select 1 from ops.completion_receipt x
         where x.id = p_ref and x.outcome = 'succeeded'
           and x.collector_name = p_key
           and x.expires_at > now()
      ) into v;
    elsif p_source = 'job_receipt' then
      select exists (
        select 1 from ops.job_receipt x
          join ops.job j on j.id = x.job_id
         where x.id = p_ref and x.kind = 'completion'
           and j.definition_key = p_key
      ) into v;
    elsif p_source = 'portfolio_acceptance_effect_free' then
      v := ops.slice_portfolio_acceptance_current(p_ref, p_key)
           and ops.slice_portfolio_acceptance_effect_count(p_ref) = 0;
    end if;
  end if;
  -- 'unbound', refusal_proof (no server-recorded gate-result source exists)
  -- and any unknown kind/source never resolve.
  return coalesce(v, false);
end;
$$;

revoke all on function ops.slice_evidence_resolves(text, text, text, integer, text, text, text, text, uuid, uuid) from public;

-- The newest server-side row that WOULD resolve a binding, or null. Only
-- kinds whose rows the server can find on its own have a candidate.
create or replace function ops.slice_evidence_candidate(
  p_slice_id text, p_kind text, p_source text, p_key text, p_bound_member_id uuid
) returns uuid
language plpgsql stable
set search_path = pg_catalog, ops
as $$
declare
  v uuid;
begin
  if p_kind = 'live_check' and p_source = 'staging_restore_only_result' then
    select x.id into v from ops.staging_restore_only_result x
     where x.status = 'succeeded' order by x.observed_at desc limit 1;
  elsif p_kind = 'live_check' and p_source = 'completion_receipt' then
    select x.id into v from ops.completion_receipt x
     where x.outcome = 'succeeded' and x.collector_name = p_key and x.expires_at > now()
     order by x.finished_at desc limit 1;
  elsif p_kind = 'live_check' and p_source = 'job_receipt' then
    select x.id into v from ops.job_receipt x join ops.job j on j.id = x.job_id
     where x.kind = 'completion' and j.definition_key = p_key
     order by x.created_at desc limit 1;
  elsif (p_kind = 'live_check' and p_source = 'portfolio_acceptance_effect_free')
     or (p_kind = 'accepted_record' and p_source = 'portfolio_revision_acceptance') then
    select a.id into v from ops.portfolio_revision_acceptance_receipt a
     where a.portfolio_ref = p_key
       and a.portfolio_revision_id = ops.portfolio_current_accepted_revision(p_key)
     order by a.accepted_at desc limit 1;
  elsif p_kind = 'shipped_release' then
    v := p_bound_member_id;
  end if;
  return v;
end;
$$;

revoke all on function ops.slice_evidence_candidate(text, text, text, text, uuid) from public;

create or replace function ops.slice_completion_evaluate(
  p_slice_id text,
  p_criteria_receipt jsonb
) returns jsonb
language plpgsql stable
set search_path = pg_catalog, ops
as $$
declare
  v_registered_count integer;
  v_submitted_count integer;
  v_distinct_count integer;
  v_matched_count integer;
  v_el jsonb;
  v_criterion text;
  v_b record;
  v_ref text;
  v_resolved boolean;
  v_computed jsonb := '[]'::jsonb;
begin
  if p_slice_id is null or btrim(p_slice_id) = '' then
    raise exception 'slice_id_required';
  end if;
  select count(*) into v_registered_count
    from ops.slice_checkable_done_registry where slice_id = p_slice_id;
  if v_registered_count = 0 then
    raise exception 'slice_completion_unknown_slice_id: %', p_slice_id;
  end if;
  if p_criteria_receipt is null or jsonb_typeof(p_criteria_receipt) <> 'array'
     or jsonb_array_length(p_criteria_receipt) = 0 then
    raise exception 'criteria_receipt_required_nonempty_array';
  end if;
  if exists (
    select 1 from jsonb_array_elements(p_criteria_receipt) el
     where jsonb_typeof(el) <> 'object' or coalesce(btrim(el->>'criterion'), '') = ''
  ) then
    raise exception 'criteria_receipt_element_requires_criterion';
  end if;

  select count(*), count(distinct btrim(el->>'criterion'))
    into v_submitted_count, v_distinct_count
    from jsonb_array_elements(p_criteria_receipt) el;
  if v_distinct_count <> v_submitted_count then
    raise exception 'slice_completion_duplicate_criterion';
  end if;
  select count(*) into v_matched_count
    from (select distinct btrim(el->>'criterion') as criterion
            from jsonb_array_elements(p_criteria_receipt) el) submitted
    join ops.slice_checkable_done_registry r
      on r.slice_id = p_slice_id and r.criterion = submitted.criterion;
  if v_matched_count <> v_registered_count or v_distinct_count <> v_registered_count then
    raise exception 'slice_completion_criteria_set_mismatch: registered % submitted % matched %',
      v_registered_count, v_distinct_count, v_matched_count;
  end if;

  for v_el in
    select el from jsonb_array_elements(p_criteria_receipt) el
     order by btrim(el->>'criterion') collate "C"
  loop
    v_criterion := btrim(v_el->>'criterion');
    select * into v_b from ops.slice_effective_binding(p_slice_id, v_criterion);
    v_ref := nullif(btrim(coalesce(v_el->>'evidence_ref', '')), '');
    v_resolved := false;
    if v_ref is not null
       and v_ref ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' then
      v_resolved := ops.slice_evidence_resolves(p_slice_id, v_b.evidence_kind, v_b.workflow_key,
        v_b.workflow_version, v_b.acceptance_mode, v_b.transition_to_stage,
        v_b.live_check_source, v_b.live_check_key, v_b.bound_member_id, v_ref::uuid);
      -- evidence_kind = 'unbound' never resolves.
    end if;
    v_computed := v_computed || jsonb_build_array(jsonb_strip_nulls(jsonb_build_object(
      'criterion', v_criterion,
      'evidence_kind', v_b.evidence_kind,
      'workflow_key', v_b.workflow_key,
      'workflow_version', v_b.workflow_version,
      'live_check_source', v_b.live_check_source,
      'live_check_key', v_b.live_check_key,
      'write_required_reason', v_b.write_required_reason,
      'binding_source', v_b.binding_source
    )) || jsonb_build_object('pass', v_resolved, 'evidence_ref', v_ref));
  end loop;
  return v_computed;
end;
$$;

comment on function ops.slice_completion_evaluate(text, jsonb) is
  'DoctorCRE V5-R02 / Q153 internal evaluator (no EXECUTE grant): checks a submitted criteria receipt against the slice''s registered criteria and recomputes pass from each criterion''s EFFECTIVE binding (registration, else latest partner binding, else the automation binding): acceptance, transition, shipped_release and refusal_proof/ci_gate (member of a complete production release), accepted_record (current, intact portfolio acceptance), live_check (success row in the bound allowlisted receipt source, or a current acceptance with zero effect rows in its windows). unbound never passes.';

revoke all on function ops.slice_completion_evaluate(text, jsonb) from public;

-- Writer door (0602 signature kept): in_progress / blocked only; refused while
-- a partner mark holds the slice; records marked_via.
create or replace function ops.mark_slice_progress(
  p_slice_id text,
  p_status text,
  p_criteria_receipt jsonb,
  p_reason text,
  p_idempotency_key uuid,
  p_actor_slug text
) returns ops.slice_completion_mark
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  v_existing ops.slice_completion_mark%rowtype;
  v_row ops.slice_completion_mark%rowtype;
  v_acting text := nullif(btrim(coalesce(current_setting('carr.acting_actor_slug', true), '')), '');
  v_via text;
begin
  perform ops.slice_done_lock(p_slice_id);
  if p_idempotency_key is null then
    raise exception 'idempotency_key_required';
  end if;
  select * into v_existing from ops.slice_completion_mark where idempotency_key = p_idempotency_key;
  if found then
    if v_existing.slice_id is distinct from p_slice_id or v_existing.status is distinct from p_status then
      raise exception 'idempotency_key_reused_for_a_different_mark';
    end if;
    return v_existing;
  end if;
  if p_status is null or p_status not in ('in_progress', 'blocked') then
    raise exception 'slice_progress_status_invalid: complete is written only by ops.mark_slice_completion';
  end if;
  if p_status = 'blocked' and (p_reason is null or btrim(p_reason) = '') then
    raise exception 'slice_progress_blocked_requires_reason';
  end if;
  if ops.slice_mark_held(p_slice_id) then
    raise exception 'slice_mark_held_by_partner: %', p_slice_id;
  end if;
  -- The actor is the server-derived acting slug ONLY; p_actor_slug (kept for
  -- the 0602 signature) is never recorded.
  if v_acting is null then
    raise exception 'acting_actor_required: the server sets carr.acting_actor_slug for every writer call';
  end if;
  v_via := case when v_acting is not null and exists (select 1 from ops.slice_marker_seat where actor_slug = v_acting)
                then 'automation' else 'writer' end;

  insert into ops.slice_completion_mark (
    slice_id, status, criteria_receipt, reason, marked_by_actor_slug, idempotency_key, marked_via
  ) values (
    p_slice_id, p_status, ops.slice_completion_evaluate(p_slice_id, p_criteria_receipt),
    p_reason, v_acting, p_idempotency_key, v_via
  ) returning * into v_row;
  return v_row;
end;
$$;

comment on function ops.mark_slice_progress(text, text, jsonb, text, uuid, text) is
  'DoctorCRE V5-R02 / Q153 writer door: append an in_progress or blocked mark for a registered slice_id, with the criteria receipt recomputed by ops.slice_completion_evaluate. Never writes complete; refused while a partner mark holds the slice. The actor is the server-derived carr.acting_actor_slug when set. Idempotent on p_idempotency_key.';

revoke all on function ops.mark_slice_progress(text, text, jsonb, text, uuid, text) from public;
grant execute on function ops.mark_slice_progress(text, text, jsonb, text, uuid, text) to carr_writer;

-- The shared complete body. A partner act only: every caller that is not a
-- partner door is refused here, before anything is read.
create or replace function ops.slice_mark_complete_insert(
  p_slice_id text, p_criteria_receipt jsonb, p_reason text, p_idempotency_key uuid,
  p_actor text, p_via text
) returns ops.slice_completion_mark
language plpgsql
set search_path = pg_catalog, ops
as $$
declare
  v_existing ops.slice_completion_mark%rowtype;
  v_row ops.slice_completion_mark%rowtype;
  v_computed jsonb;
begin
  perform ops.slice_done_lock(p_slice_id);
  if p_via is distinct from 'authority' then
    raise exception 'slice_complete_is_a_partner_act: automation may only propose (ops.propose_slice_completion)';
  end if;
  if p_idempotency_key is null then
    raise exception 'idempotency_key_required';
  end if;
  select * into v_existing from ops.slice_completion_mark where idempotency_key = p_idempotency_key;
  if found then
    if v_existing.slice_id is distinct from p_slice_id or v_existing.status <> 'complete' then
      raise exception 'idempotency_key_reused_for_a_different_mark';
    end if;
    return v_existing;
  end if;

  v_computed := ops.slice_completion_evaluate(p_slice_id, p_criteria_receipt);
  if exists (select 1 from jsonb_array_elements(v_computed) el where (el->>'pass')::boolean is not true) then
    raise exception 'slice_completion_complete_requires_every_criterion_proven';
  end if;

  insert into ops.slice_completion_mark (
    slice_id, status, criteria_receipt, reason, marked_by_actor_slug, idempotency_key, marked_via
  ) values (
    p_slice_id, 'complete', v_computed, p_reason, p_actor, p_idempotency_key, 'authority'
  ) returning * into v_row;
  return v_row;
end;
$$;

revoke all on function ops.slice_mark_complete_insert(text, jsonb, text, uuid, text, text) from public;

create or replace function ops.mark_slice_completion(
  p_slice_id text,
  p_criteria_receipt jsonb,
  p_reason text,
  p_idempotency_key uuid
) returns ops.slice_completion_mark
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
begin
  return ops.slice_mark_complete_insert(p_slice_id, p_criteria_receipt, p_reason, p_idempotency_key,
    ops.authority_actor_slug(), 'authority');
end;
$$;

comment on function ops.mark_slice_completion(text, jsonb, text, uuid) is
  'DoctorCRE V5-R02 / Q153 authority write door: append status=complete for a registered slice_id only when ops.slice_completion_evaluate resolves every registered criterion to evidence of its effective binding. The actor is ops.authority_actor_slug(); the mark holds the slice against automation. Idempotent on p_idempotency_key.';

revoke all on function ops.mark_slice_completion(text, jsonb, text, uuid) from public;
grant execute on function ops.mark_slice_completion(text, jsonb, text, uuid) to carr_authority;

-- Automation's door to the terminal state: a PROPOSAL, never a mark.
create or replace function ops.propose_slice_completion(
  p_slice_id text,
  p_criteria_receipt jsonb,
  p_reason text,
  p_idempotency_key uuid
) returns ops.slice_completion_proposal
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  v_actor text;
  v_existing ops.slice_completion_proposal%rowtype;
  v_latest ops.slice_completion_mark%rowtype;
  v_row ops.slice_completion_proposal%rowtype;
  v_computed jsonb;
begin
  v_actor := ops.slice_marker_seat_actor();
  perform ops.slice_done_lock(p_slice_id);
  if p_idempotency_key is null then
    raise exception 'idempotency_key_required';
  end if;
  select * into v_existing from ops.slice_completion_proposal where idempotency_key = p_idempotency_key;
  if found then
    if v_existing.slice_id is distinct from p_slice_id then
      raise exception 'idempotency_key_reused_for_a_different_proposal';
    end if;
    return v_existing;
  end if;
  if p_reason is null or btrim(p_reason) = '' then
    raise exception 'reason_required';
  end if;
  if ops.slice_mark_held(p_slice_id) then
    raise exception 'slice_mark_held_by_partner: %', p_slice_id;
  end if;
  select * into v_latest from ops.slice_completion_mark where slice_id = p_slice_id order by mark_seq desc limit 1;
  if v_latest.status = 'complete' then
    raise exception 'slice_already_complete: %', p_slice_id;
  end if;
  v_computed := ops.slice_completion_evaluate(p_slice_id, p_criteria_receipt);
  if exists (select 1 from jsonb_array_elements(v_computed) el where (el->>'pass')::boolean is not true) then
    raise exception 'slice_completion_proposal_requires_every_criterion_proven';
  end if;
  insert into ops.slice_completion_proposal (
    slice_id, criteria_receipt, reason, proposed_by_actor_slug, basis_mark_seq, idempotency_key
  ) values (
    p_slice_id, v_computed, p_reason, v_actor, coalesce(v_latest.mark_seq, 0), p_idempotency_key
  ) returning * into v_row;
  return v_row;
end;
$$;

comment on function ops.propose_slice_completion(text, jsonb, text, uuid) is
  'DoctorCRE v5 automation door: PROPOSE that a slice is complete. Every criterion is recomputed from server-resolved evidence and must pass; the proposal is recorded in ops.slice_completion_proposal and changes no mark. Only a partner turns it into complete, with ops.confirm_slice_completions. Seat-only; refused while a partner holds the slice or once it is complete. Idempotent on p_idempotency_key.';

revoke all on function ops.propose_slice_completion(text, jsonb, text, uuid) from public;
grant execute on function ops.propose_slice_completion(text, jsonb, text, uuid) to carr_writer;

-- The complete mark a batch confirmation writes for one slice has a key
-- derived from the batch key, so a replayed batch returns the same marks.
create or replace function ops.slice_confirm_mark_key(p_batch_key uuid, p_slice_id text)
returns uuid
language sql immutable
set search_path = pg_catalog
as $$ select md5('slice-confirm:' || p_batch_key::text || ':' || p_slice_id)::uuid $$;

revoke all on function ops.slice_confirm_mark_key(uuid, text) from public;

-- Partner batch door: one act confirms many proposals. Per slice, under the
-- slice lock: refused (reported, not raised) when held, already complete,
-- with no proposal, with a proposal older than the slice's latest mark, or
-- when any criterion no longer resolves NOW.
create or replace function ops.confirm_slice_completions(
  p_slice_ids text[],
  p_reason text,
  p_idempotency_key uuid,
  p_expected_proposal_ids jsonb default null
) returns table (slice_id text, outcome text, mark_id uuid, proposal_id uuid, criteria_receipt jsonb)
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
#variable_conflict use_column
declare
  v_actor text;
  v_slice text;
  v_key uuid;
  v_existing ops.slice_completion_mark%rowtype;
  v_latest ops.slice_completion_mark%rowtype;
  v_prop ops.slice_completion_proposal%rowtype;
  v_computed jsonb;
  v_row ops.slice_completion_mark%rowtype;
  v_expected jsonb := '{}'::jsonb;
  v_expected_id uuid;
  v_k text;
  v_v jsonb;
begin
  v_actor := ops.authority_actor_slug();
  if p_idempotency_key is null then
    raise exception 'idempotency_key_required';
  end if;
  if p_slice_ids is null or cardinality(p_slice_ids) = 0 then
    raise exception 'slice_ids_required_nonempty';
  end if;
  if cardinality(p_slice_ids) > 200 then
    raise exception 'slice_ids_at_most_200';
  end if;
  if p_reason is null or btrim(p_reason) = '' then
    raise exception 'reason_required';
  end if;
  -- Optional {slice_id: proposal_id}: what the partner actually reviewed. A
  -- slice whose latest proposal is no longer that one is not confirmed.
  if p_expected_proposal_ids is not null and jsonb_typeof(p_expected_proposal_ids) <> 'null' then
    if jsonb_typeof(p_expected_proposal_ids) <> 'object' then
      raise exception 'expected_proposal_ids_must_be_an_object';
    end if;
    for v_k, v_v in select e.key, e.value from jsonb_each(p_expected_proposal_ids) e loop
      if jsonb_typeof(v_v) <> 'string'
         or (v_v #>> '{}') !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then
        raise exception 'expected_proposal_id_not_a_uuid: %', v_k;
      end if;
      if not exists (select 1 from unnest(p_slice_ids) x where btrim(x) = btrim(v_k)) then
        raise exception 'expected_proposal_id_for_unlisted_slice: %', v_k;
      end if;
      v_expected := v_expected || jsonb_build_object(btrim(v_k), lower(v_v #>> '{}'));
    end loop;
  end if;
  -- One fixed order, so two concurrent batches take slice locks alike.
  for v_slice in
    select d.s from (select distinct btrim(x) as s from unnest(p_slice_ids) x
                      where x is not null and btrim(x) <> '') d
     order by d.s collate "C"
  loop
    perform ops.slice_done_lock(v_slice);
    slice_id := v_slice; mark_id := null; proposal_id := null; criteria_receipt := null;
    v_key := ops.slice_confirm_mark_key(p_idempotency_key, v_slice);
    select * into v_existing from ops.slice_completion_mark m where m.idempotency_key = v_key;
    if found then
      if v_existing.slice_id = v_slice and v_existing.status = 'complete' and v_existing.marked_via = 'authority' then
        outcome := 'confirmed'; mark_id := v_existing.id; proposal_id := v_existing.confirmed_proposal_id;
        criteria_receipt := v_existing.criteria_receipt;
      else
        outcome := 'idempotency_key_conflict';
      end if;
      return next;
      continue;
    end if;
    if not exists (select 1 from ops.slice_checkable_done_registration r where r.slice_id = v_slice) then
      outcome := 'unknown_slice';
      return next;
      continue;
    end if;
    v_latest := null;
    select * into v_latest from ops.slice_completion_mark m where m.slice_id = v_slice order by m.mark_seq desc limit 1;
    if v_latest.status = 'complete' then
      outcome := 'already_complete'; mark_id := v_latest.id;
      return next;
      continue;
    end if;
    if ops.slice_mark_held(v_slice) then
      outcome := 'held'; mark_id := v_latest.id;
      return next;
      continue;
    end if;
    v_prop := null;
    select * into v_prop from ops.slice_completion_proposal p where p.slice_id = v_slice order by p.proposal_seq desc limit 1;
    if v_prop.id is null then
      outcome := 'no_proposal';
      return next;
      continue;
    end if;
    proposal_id := v_prop.id;
    v_expected_id := (v_expected ->> v_slice)::uuid;
    if v_expected_id is not null and v_expected_id is distinct from v_prop.id then
      outcome := 'proposal_superseded';
      return next;
      continue;
    end if;
    if coalesce(v_latest.mark_seq, 0) > v_prop.basis_mark_seq then
      outcome := 'stale_proposal'; mark_id := v_latest.id;
      return next;
      continue;
    end if;
    -- Re-evaluate NOW: every criterion's effective binding, every ref.
    begin
      v_computed := ops.slice_completion_evaluate(v_slice, v_prop.criteria_receipt);
    exception when others then
      v_computed := null;
    end;
    if v_computed is null
       or exists (select 1 from jsonb_array_elements(v_computed) el where (el->>'pass')::boolean is not true) then
      outcome := 'not_proven'; criteria_receipt := v_computed;
      return next;
      continue;
    end if;
    insert into ops.slice_completion_mark (
      slice_id, status, criteria_receipt, reason, marked_by_actor_slug, idempotency_key,
      marked_via, confirmed_proposal_id
    ) values (
      v_slice, 'complete', v_computed, p_reason, v_actor, v_key, 'authority', v_prop.id
    ) returning * into v_row;
    outcome := 'confirmed'; mark_id := v_row.id; criteria_receipt := v_computed;
    return next;
  end loop;
end;
$$;

comment on function ops.confirm_slice_completions(text[], text, uuid, jsonb) is
  'DoctorCRE v5 partner authority door: confirm many slices complete in one act. Per slice (under the slice lock) it takes the latest automated proposal, re-evaluates every criterion against its effective binding NOW, and writes a complete mark (marked_via authority, actor ops.authority_actor_slug(), confirmed_proposal_id) only when all still pass. Optional p_expected_proposal_ids {slice_id: proposal_id} names the proposal the partner reviewed; if the latest proposal of the slice is a different one it reports proposal_superseded and confirms nothing. Outcomes per slice: confirmed, held, already_complete, no_proposal, proposal_superseded, stale_proposal (a mark was written after the proposal), not_proven, unknown_slice, idempotency_key_conflict. Idempotent on p_idempotency_key.';

revoke all on function ops.confirm_slice_completions(text[], text, uuid, jsonb) from public;
grant execute on function ops.confirm_slice_completions(text[], text, uuid, jsonb) to carr_authority;

-- Read: the proposals a partner could confirm now (latest per slice, not
-- held, not complete, not stale), each with whether it still passes.
create or replace function ops.pending_slice_completion_proposals()
returns jsonb
language plpgsql stable security definer
set search_path = pg_catalog, ops
as $$
declare
  v_out jsonb := '[]'::jsonb;
  v_p record;
  v_computed jsonb;
begin
  for v_p in
    select p.*, (select max(m.mark_seq) from ops.slice_completion_mark m where m.slice_id = p.slice_id) as latest_seq
      from (select distinct on (x.slice_id) x.* from ops.slice_completion_proposal x
             order by x.slice_id, x.proposal_seq desc) p
     order by p.slice_id collate "C"
  loop
    continue when ops.slice_mark_held(v_p.slice_id);
    continue when coalesce(v_p.latest_seq, 0) > v_p.basis_mark_seq;
    begin
      v_computed := ops.slice_completion_evaluate(v_p.slice_id, v_p.criteria_receipt);
    exception when others then
      v_computed := null;
    end;
    v_out := v_out || jsonb_build_array(jsonb_build_object(
      'slice_id', v_p.slice_id, 'proposal_id', v_p.id, 'proposed_by', v_p.proposed_by_actor_slug,
      'reason', v_p.reason, 'created_at', v_p.created_at,
      'passes_now', v_computed is not null and not exists (
        select 1 from jsonb_array_elements(v_computed) el where (el->>'pass')::boolean is not true),
      'criteria_receipt', coalesce(v_computed, v_p.criteria_receipt)));
  end loop;
  return v_out;
end;
$$;

comment on function ops.pending_slice_completion_proposals() is
  'DoctorCRE v5 read door: the latest automated completion proposal of every slice a partner could confirm now (not held, not complete, no mark since), each re-evaluated on this read (passes_now). The list a partner hands to ops.confirm_slice_completions.';

revoke all on function ops.pending_slice_completion_proposals() from public;
grant execute on function ops.pending_slice_completion_proposals() to carr_reader;

-- Partner override: hold (unmark to in_progress / blocked) or release.
create or replace function ops.set_slice_mark_hold(
  p_slice_id text,
  p_action text,
  p_status text,
  p_reason text,
  p_idempotency_key uuid
) returns ops.slice_completion_mark
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  v_actor text;
  v_existing ops.slice_completion_mark%rowtype;
  v_row ops.slice_completion_mark%rowtype;
  v_via text;
  v_status text;
begin
  v_actor := ops.authority_actor_slug();
  perform ops.slice_done_lock(p_slice_id);
  if p_idempotency_key is null then
    raise exception 'idempotency_key_required';
  end if;
  select * into v_existing from ops.slice_completion_mark where idempotency_key = p_idempotency_key;
  if found then
    if v_existing.slice_id is distinct from p_slice_id
       or v_existing.marked_via not in ('authority_hold', 'authority_release') then
      raise exception 'idempotency_key_reused_for_a_different_mark';
    end if;
    return v_existing;
  end if;
  if not exists (select 1 from ops.slice_checkable_done_registration where slice_id = p_slice_id) then
    raise exception 'slice_completion_unknown_slice_id: %', p_slice_id;
  end if;
  if p_reason is null or btrim(p_reason) = '' then
    raise exception 'reason_required';
  end if;
  if p_action = 'hold' then
    if p_status is null or p_status not in ('in_progress', 'blocked') then
      raise exception 'slice_hold_status_invalid: %', p_status;
    end if;
    v_via := 'authority_hold';
    v_status := p_status;
  elsif p_action = 'release' then
    if not ops.slice_mark_held(p_slice_id) then
      raise exception 'slice_mark_not_held: %', p_slice_id;
    end if;
    v_via := 'authority_release';
    v_status := 'in_progress';
  else
    raise exception 'slice_hold_action_invalid: %', p_action;
  end if;
  insert into ops.slice_completion_mark (
    slice_id, status, criteria_receipt, reason, marked_by_actor_slug, idempotency_key, marked_via
  ) values (
    p_slice_id, v_status, '[]'::jsonb, p_reason, v_actor, p_idempotency_key, v_via
  ) returning * into v_row;
  return v_row;
end;
$$;

comment on function ops.set_slice_mark_hold(text, text, text, text, uuid) is
  'DoctorCRE v5 partner authority door: hold a slice at in_progress or blocked (overriding or unmarking any mark, including a partner complete; a pending proposal becomes stale and confirmation reports the slice held), or release the hold so automation may mark and propose again. Appends a mark; never rewrites one. Takes the slice lock.';

revoke all on function ops.set_slice_mark_hold(text, text, text, text, uuid) from public;
grant execute on function ops.set_slice_mark_hold(text, text, text, text, uuid) to carr_authority;

-- ===========================================================================
-- One read of a slice's whole done-state, for the marker and for people.
-- ===========================================================================
create or replace function ops.read_slice_done_state(p_slice_id text)
returns jsonb
language plpgsql stable security definer
set search_path = pg_catalog, ops
as $$
declare
  v_reg ops.slice_checkable_done_registration%rowtype;
  v_latest ops.slice_completion_mark%rowtype;
  v_prop ops.slice_completion_proposal%rowtype;
  v_criteria jsonb := '[]'::jsonb;
  v_r record;
  v_b record;
  v_candidate uuid;
  v_catalog jsonb;
begin
  select * into v_reg from ops.slice_checkable_done_registration where slice_id = p_slice_id;
  if v_reg.slice_id is null then
    -- Not registered yet: what the seat WOULD be allowed to bind, per catalog
    -- criterion (null when the catalog lacks the slice).
    begin
      select jsonb_object_agg(c, to_jsonb(ops.slice_criterion_allowed_kinds(c))) into v_catalog
        from ops.slice_catalog_checkable_done(p_slice_id) x, unnest(x.criteria) c;
    exception when others then
      v_catalog := null;
    end;
  end if;
  select * into v_latest from ops.slice_completion_mark where slice_id = p_slice_id order by mark_seq desc limit 1;
  select * into v_prop from ops.slice_completion_proposal where slice_id = p_slice_id order by proposal_seq desc limit 1;
  if v_reg.slice_id is not null then
    for v_r in select * from ops.slice_checkable_done_registry where slice_id = p_slice_id order by created_at, criterion loop
      select * into v_b from ops.slice_effective_binding(p_slice_id, v_r.criterion);
      v_candidate := ops.slice_evidence_candidate(p_slice_id, v_b.evidence_kind,
        v_b.live_check_source, v_b.live_check_key, v_b.bound_member_id);
      v_criteria := v_criteria || jsonb_build_array(jsonb_build_object(
        'criterion', v_r.criterion,
        'registered_kind', v_r.evidence_kind,
        'evidence_kind', v_b.evidence_kind,
        'binding_source', v_b.binding_source,
        'live_check_source', v_b.live_check_source,
        'live_check_key', v_b.live_check_key,
        'automation_bound', exists (select 1 from ops.slice_criterion_binding x
                                     where x.slice_id = p_slice_id and x.criterion = v_r.criterion
                                       and x.bound_via = 'automation'),
        'write_required_reason', v_b.write_required_reason,
        'bound_member_id', v_b.bound_member_id,
        'allowed_kinds', to_jsonb(ops.slice_criterion_allowed_kinds(v_r.criterion)),
        'proposal', (select jsonb_build_object('id', x.id, 'evidence_kind', x.evidence_kind,
                       'bound_member_id', x.bound_member_id, 'reason', x.reason, 'created_at', x.created_at)
                       from ops.slice_criterion_binding x
                      where x.slice_id = p_slice_id and x.criterion = v_r.criterion and x.bound_via = 'automation'
                        and v_b.binding_source <> 'binding:automation'
                        and not exists (select 1 from ops.slice_criterion_binding y
                                         where y.slice_id = x.slice_id and y.criterion = x.criterion
                                           and y.bound_via = 'authority')),
        'live_check_candidate', v_candidate,
        -- Recomputed now, from the live rows; never read from a mark.
        'candidate_passes', ops.slice_evidence_resolves(p_slice_id, v_b.evidence_kind, v_b.workflow_key,
          v_b.workflow_version, v_b.acceptance_mode, v_b.transition_to_stage,
          v_b.live_check_source, v_b.live_check_key, v_b.bound_member_id, v_candidate)
      ));
    end loop;
  end if;
  return jsonb_build_object(
    'slice_id', p_slice_id,
    'registered', v_reg.slice_id is not null,
    'registered_via', v_reg.registered_via,
    'catalog_revision_id', v_reg.catalog_revision_id,
    'catalog_allowed_kinds', v_catalog,
    'criteria', v_criteria,
    'held_by_partner', ops.slice_mark_held(p_slice_id),
    'latest_proposal', case when v_prop.id is null then null else jsonb_build_object(
      'id', v_prop.id, 'proposed_by', v_prop.proposed_by_actor_slug, 'reason', v_prop.reason,
      'basis_mark_seq', v_prop.basis_mark_seq, 'created_at', v_prop.created_at,
      'criteria_receipt', v_prop.criteria_receipt,
      'stale', coalesce(v_latest.mark_seq, 0) > v_prop.basis_mark_seq) end,
    -- A proposal a partner could confirm now (confirmation re-evaluates it).
    'awaiting_partner_confirmation', v_prop.id is not null
      and coalesce(v_latest.mark_seq, 0) <= v_prop.basis_mark_seq
      and not ops.slice_mark_held(p_slice_id)
      and v_latest.status is distinct from 'complete',
    'latest_mark', case when v_latest.id is null then null else jsonb_build_object(
      'id', v_latest.id, 'status', v_latest.status, 'marked_via', v_latest.marked_via,
      'marked_by', v_latest.marked_by_actor_slug, 'reason', v_latest.reason,
      'criteria_receipt', v_latest.criteria_receipt, 'created_at', v_latest.created_at) end,
    'release_members', coalesce((
      select jsonb_agg(jsonb_build_object(
               'id', m.id, 'release_key', r.release_key, 'commit_sha', m.commit_sha,
               'pr_number', m.pr_number, 'subject', m.subject, 'attribution', m.attribution)
               order by coalesce(r.ended_at, r.updated_at), m.commit_sha)
        from ops.release_slice_member m join ops.release r on r.id = m.release_id
       where m.slice_id = p_slice_id and r.environment = 'production' and r.state = 'complete'
    ), '[]'::jsonb)
  );
end;
$$;

comment on function ops.read_slice_done_state(text) is
  'DoctorCRE v5 read door: registration, each criterion with its effective binding, its newest server-found candidate and whether that candidate passes NOW (a live recompute, never read from a mark), the latest mark and partner-hold state, and the shipped release members of one slice.';

revoke all on function ops.read_slice_done_state(text) from public;
grant execute on function ops.read_slice_done_state(text) to carr_reader;
