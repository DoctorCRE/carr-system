-- 0602_doctorcre_r02_workflow_cutover_and_caller_inventory.sql
--
-- DoctorCRE V5-R02 (Workflow cutover, caller migration and retirement
-- readiness). Builds the READINESS AND CUTOVER MACHINERY only -- no live
-- production workflow is retired by this migration. The store and write
-- doors below let a future, separately-scheduled effect retire a real
-- workflow once its evidence is real; this migration cannot do that itself,
-- because ops.retire_workflow_cutover_plan (below) derives the workflow
-- census itself and refuses whenever that census is not provably available
-- (no census store exists in this schema yet; see #1244) -- Q157:
-- unavailable evidence is refused, never silently treated as done.
--
-- Q116 (recovered): "avoid uncontrolled dual writes, and migrate one
-- workflow at a time. The steps are: read legacy state, build the new
-- projection, compare outcomes in shadow, establish one write authority, cut
-- over, monitor, preserve a bounded recovery path, then retire the old
-- workflow and its instructions." ops.workflow_cutover_plan.stage is exactly
-- that eight-step sequence, enforced strictly forward by
-- ops.advance_workflow_cutover_stage (no skipping, no going back), and
-- retirement is split into its own authority-only door
-- (ops.retire_workflow_cutover_plan) composing the existing
-- ops.disable_legacy_schedule (0176/0180/0182/0184) and
-- ops.workflow_acceptance (0149) evidence rather than reinventing either.
--
-- Q153 (recovered): "V5 explicitly supersedes [v3, rev4 and stale doctrine],
-- reconciles any valid evidence, and retires contradictory active guidance.
-- Only one master graph controls execution." ops.open_workflow_cutover_plan
-- enforces exactly one ACTIVE plan per (workflow_key, workflow_version): a
-- new open automatically supersedes any prior active plan for the same
-- workflow, recording why, rather than leaving two live plans to disagree.
-- Q153 also added: "completion of a certain slice throughout this build must
-- be clearly marked so that future sessions know explicitly whats been done
-- and whats left" -- ops.slice_completion_mark is that explicit, generic,
-- append-only marker, usable by every V5 slice, not only this one.
--
-- Q157 (recovered) lists conditions that are failures despite green metrics
-- -- "the interface hides uncertainty or stale data" chief among them. Every
-- read door below refuses to report a caller or a workflow "done" from
-- absent or unavailable evidence: ops.record_workflow_caller requires a
-- non-null evidence_ref before accepting status='done', and the MCP read
-- verb (workflow-cutover-board) independently re-checks the workflow census
-- and downgrades to 'unknown' rather than trusting a stale cached figure.
--
-- ACCESS MODEL (PR #1245 re-review). Every door that can change what
-- ops.enqueue_job does for a live workflow -- open (and its supersession),
-- advance, cancel and retire -- is a human authority act: EXECUTE is granted
-- to carr_authority only and each door takes its actor from
-- ops.authority_actor_slug(), never from a caller-supplied string. A writer
-- login keeps exactly two doors here: ops.record_workflow_caller and
-- ops.mark_slice_progress. Completing a slice (ops.mark_slice_completion) and
-- registering what completion means (ops.register_slice_checkable_done) are
-- authority acts too.
--
-- No explicit transaction control: from 0339 onward tools/migrate.py runs
-- each migration inside its own single transaction.

-- ===========================================================================
-- ops.workflow_cutover_plan -- one active plan per (workflow_key, version).
-- ===========================================================================
create table if not exists ops.workflow_cutover_plan (
  id uuid primary key default gen_random_uuid(),
  workflow_key text not null check (btrim(workflow_key) <> ''),
  workflow_version integer not null check (workflow_version >= 1),
  stage text not null default 'read_legacy' check (stage in (
    'read_legacy', 'build_projection', 'shadow_compare', 'single_write_authority',
    'cutover', 'monitor', 'recovery_ready', 'retired'
  )),
  status text not null default 'active' check (status in ('active', 'superseded', 'blocked', 'retired', 'cancelled')),
  superseded_by uuid references ops.workflow_cutover_plan(id),
  supersede_reason text,
  -- Q116: "preserve a bounded recovery path" is not an afterthought at the
  -- retire step -- the plan cannot open without one stated up front.
  recovery_plan text not null check (btrim(recovery_plan) <> ''),
  opened_by_actor_slug text,
  idempotency_key uuid not null unique,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Exactly one ACTIVE plan per workflow identity -- the Q153 "only one master
-- graph" invariant, enforced by the database rather than trusted to callers.
create unique index if not exists workflow_cutover_plan_one_active_idx
  on ops.workflow_cutover_plan (workflow_key, workflow_version)
  where status = 'active';

create index if not exists workflow_cutover_plan_workflow_idx
  on ops.workflow_cutover_plan (workflow_key, workflow_version, created_at desc);

comment on table ops.workflow_cutover_plan is
  'DoctorCRE V5-R02: one workflow-migration plan per open. status=active is unique per (workflow_key,workflow_version) -- Q153 stale-plan supersession. stage follows the fixed Q116 sequence read_legacy..retired, advanced only forward by ops.advance_workflow_cutover_stage / ops.retire_workflow_cutover_plan.';

revoke all on table ops.workflow_cutover_plan from public, carr_writer, carr_jobs, carr_authority;
grant select on table ops.workflow_cutover_plan to carr_reader;

-- ===========================================================================
-- ops.workflow_cutover_stage_transition -- append-only audit of every move.
-- ===========================================================================
create table if not exists ops.workflow_cutover_stage_transition (
  id uuid primary key default gen_random_uuid(),
  plan_id uuid not null references ops.workflow_cutover_plan(id),
  from_stage text,
  to_stage text not null check (to_stage in (
    'read_legacy', 'build_projection', 'shadow_compare', 'single_write_authority',
    'cutover', 'monitor', 'recovery_ready', 'retired'
  )),
  evidence_ref text,
  reason text not null check (btrim(reason) <> ''),
  actor_slug text,
  idempotency_key uuid not null unique,
  occurred_at timestamptz not null default now()
);

create index if not exists workflow_cutover_stage_transition_plan_idx
  on ops.workflow_cutover_stage_transition (plan_id, occurred_at);

comment on table ops.workflow_cutover_stage_transition is
  'DoctorCRE V5-R02: append-only stage-transition audit for ops.workflow_cutover_plan. Never updated or deleted.';

revoke all on table ops.workflow_cutover_stage_transition from public, carr_writer, carr_jobs, carr_authority;
grant select on table ops.workflow_cutover_stage_transition to carr_reader;

-- ===========================================================================
-- ops.workflow_caller -- caller inventory: done/remaining/blocked/
-- superseded/retired per caller of a workflow identity.
-- ===========================================================================
create table if not exists ops.workflow_caller (
  id uuid primary key default gen_random_uuid(),
  workflow_key text not null check (btrim(workflow_key) <> ''),
  workflow_version integer not null check (workflow_version >= 1),
  caller_locator text not null check (btrim(caller_locator) <> ''),
  caller_kind text not null check (caller_kind in (
    'script', 'verb', 'worker_route', 'job_definition', 'external'
  )),
  status text not null default 'remaining' check (status in (
    'remaining', 'done', 'blocked', 'superseded', 'retired'
  )),
  blocked_reason text,
  -- Q157: a caller cannot read back 'done' from hidden or absent evidence.
  evidence_ref text,
  updated_by_actor_slug text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (workflow_key, workflow_version, caller_locator)
);

create index if not exists workflow_caller_workflow_idx
  on ops.workflow_caller (workflow_key, workflow_version, status);

comment on table ops.workflow_caller is
  'DoctorCRE V5-R02: caller inventory per workflow identity. status=done requires a non-null evidence_ref (enforced by ops.record_workflow_caller); a caller can never read back done from silence.';

revoke all on table ops.workflow_caller from public, carr_writer, carr_jobs, carr_authority;
grant select on table ops.workflow_caller to carr_reader;

-- ===========================================================================
-- ops.slice_completion_mark -- Q153's explicit, generic, per-slice
-- completion marker. Append-only; the latest row per slice_id is current.
-- Each row is a stable record of one slice outcome: its id, slice_id, status,
-- and the server-recomputed criteria_receipt, where every element names the
-- workflow identity and the evidence row that proved (or failed to prove)
-- one registered criterion. Later slices bind their own receipts to these
-- rows by id, so the shape is kept stable.
-- ===========================================================================
create table if not exists ops.slice_completion_mark (
  id uuid primary key default gen_random_uuid(),
  slice_id text not null check (btrim(slice_id) <> ''),
  status text not null check (status in ('in_progress', 'complete', 'blocked')),
  -- One element per registered criterion, recomputed server-side by
  -- ops.slice_completion_evaluate: {"criterion","evidence_kind",
  -- "workflow_key","workflow_version","evidence_ref","pass"}. The caller's
  -- own pass claim is never stored.
  criteria_receipt jsonb not null check (jsonb_typeof(criteria_receipt) = 'array'),
  reason text,
  marked_by_actor_slug text,
  idempotency_key uuid not null unique,
  -- Append order. "Latest mark" is decided by this, not by created_at, which
  -- is the transaction timestamp and ties for marks written in one
  -- transaction.
  mark_seq bigint generated always as identity unique,
  created_at timestamptz not null default now()
);

create index if not exists slice_completion_mark_slice_idx
  on ops.slice_completion_mark (slice_id, mark_seq desc);

comment on table ops.slice_completion_mark is
  'DoctorCRE V5-R02 / Q153: append-only explicit slice-completion marker. The latest row per slice_id (highest mark_seq) is the current state. status=complete is written only by ops.mark_slice_completion (authority) after every registered criterion resolved to its bound evidence; in_progress/blocked are written by ops.mark_slice_progress (writer).';

revoke all on table ops.slice_completion_mark from public, carr_writer, carr_jobs, carr_authority;
grant select on table ops.slice_completion_mark to carr_reader;

create or replace function ops.refuse_slice_completion_mark_rewrite()
returns trigger language plpgsql as $$
begin
  raise exception 'slice_completion_mark is append-only';
end $$;

create trigger slice_completion_mark_append_only
  before update or delete on ops.slice_completion_mark
  for each row execute function ops.refuse_slice_completion_mark_rewrite();

-- ===========================================================================
-- The checkable_done registry: WHICH criteria define "done" for a slice_id,
-- and WHAT evidence each criterion accepts. Registered once per slice_id by a
-- human authority, then immutable. Each criterion is bound to one workflow
-- identity and one evidence type:
--   evidence_kind='acceptance': an accepted ops.workflow_acceptance row for
--     exactly (workflow_key, workflow_version) in exactly acceptance_mode;
--   evidence_kind='transition': an ops.workflow_cutover_stage_transition
--     into exactly transition_to_stage on a plan for exactly that workflow.
-- Without the binding, any accepted acceptance row anywhere satisfied any
-- criterion (PR #1245 re-review P1-c).
-- ===========================================================================
create table if not exists ops.slice_checkable_done_registration (
  slice_id text primary key check (btrim(slice_id) <> ''),
  registered_by_actor_slug text not null,
  idempotency_key uuid not null unique,
  created_at timestamptz not null default now()
);

comment on table ops.slice_checkable_done_registration is
  'DoctorCRE V5-R02 / Q153: one row per slice_id whose checkable_done criteria set is registered. A slice_id is registered once; its criteria never change afterwards.';

revoke all on table ops.slice_checkable_done_registration from public, carr_writer, carr_jobs, carr_authority;
grant select on table ops.slice_checkable_done_registration to carr_reader;

create table if not exists ops.slice_checkable_done_registry (
  id uuid primary key default gen_random_uuid(),
  slice_id text not null references ops.slice_checkable_done_registration(slice_id),
  criterion text not null check (btrim(criterion) <> '' and criterion = btrim(criterion)),
  evidence_kind text not null check (evidence_kind in ('acceptance', 'transition')),
  workflow_key text not null check (btrim(workflow_key) <> ''),
  workflow_version integer not null check (workflow_version >= 1),
  acceptance_mode text check (acceptance_mode in ('shadow', 'canary')),
  transition_to_stage text check (transition_to_stage in (
    'read_legacy', 'build_projection', 'shadow_compare', 'single_write_authority',
    'cutover', 'monitor', 'recovery_ready', 'retired'
  )),
  created_at timestamptz not null default now(),
  unique (slice_id, criterion),
  check (
    (evidence_kind = 'acceptance' and acceptance_mode is not null and transition_to_stage is null)
    or (evidence_kind = 'transition' and transition_to_stage is not null and acceptance_mode is null)
  )
);

comment on table ops.slice_checkable_done_registry is
  'DoctorCRE V5-R02 / Q153: the exact checkable_done criteria of a registered slice_id, each bound to one workflow identity and one evidence type. ops.slice_completion_evaluate resolves a submitted evidence_ref only against this binding.';

revoke all on table ops.slice_checkable_done_registry from public, carr_writer, carr_jobs, carr_authority;
grant select on table ops.slice_checkable_done_registry to carr_reader;

create or replace function ops.refuse_slice_checkable_done_rewrite()
returns trigger language plpgsql as $$
begin
  raise exception 'slice checkable_done registration is immutable';
end $$;

create trigger slice_checkable_done_registration_immutable
  before update or delete on ops.slice_checkable_done_registration
  for each row execute function ops.refuse_slice_checkable_done_rewrite();

create trigger slice_checkable_done_registry_immutable
  before update or delete on ops.slice_checkable_done_registry
  for each row execute function ops.refuse_slice_checkable_done_rewrite();

-- p_criteria: a jsonb array of
--   {"criterion", "evidence_kind", "workflow_key", "workflow_version",
--    "acceptance_mode" (acceptance) | "transition_to_stage" (transition)}.
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
  -- Defining what "done" means for a slice is a human authority act.
  v_authority_actor := ops.authority_actor_slug();
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

  insert into ops.slice_checkable_done_registration (slice_id, registered_by_actor_slug, idempotency_key)
  values (p_slice_id, v_authority_actor, p_idempotency_key);

  for v_el in select * from jsonb_array_elements(p_criteria)
  loop
    if jsonb_typeof(v_el) <> 'object' or coalesce(btrim(v_el->>'criterion'), '') = '' then
      raise exception 'criterion_required';
    end if;
    v_kind := v_el->>'evidence_kind';
    v_key := v_el->>'workflow_key';
    if coalesce(jsonb_typeof(v_el->'workflow_version'), '') <> 'number' then
      raise exception 'criterion_workflow_version_required: %', v_el->>'criterion';
    end if;
    v_version := (v_el->>'workflow_version')::integer;
    if not exists (select 1 from ops.job_definition where key = v_key and version = v_version) then
      raise exception 'criterion_workflow_not_registered: % v%', v_key, v_version;
    end if;
    if v_kind = 'acceptance' and v_el ? 'transition_to_stage' then
      raise exception 'criterion_binding_mixes_evidence_types: %', v_el->>'criterion';
    end if;
    if v_kind = 'transition' and v_el ? 'acceptance_mode' then
      raise exception 'criterion_binding_mixes_evidence_types: %', v_el->>'criterion';
    end if;
    insert into ops.slice_checkable_done_registry (
      slice_id, criterion, evidence_kind, workflow_key, workflow_version,
      acceptance_mode, transition_to_stage
    ) values (
      p_slice_id, btrim(v_el->>'criterion'), v_kind, v_key, v_version,
      v_el->>'acceptance_mode', v_el->>'transition_to_stage'
    );
  end loop;

  return query select * from ops.slice_checkable_done_registry where slice_id = p_slice_id;
end;
$$;

comment on function ops.register_slice_checkable_done(text, jsonb, uuid) is
  'DoctorCRE V5-R02 / Q153 authority write door: registers, once, the checkable_done criteria of a slice_id, each bound to a registered workflow identity and one evidence type (acceptance+mode or transition+to_stage). Refuses duplicate criteria, an already-registered slice_id and an unregistered workflow. Idempotent on p_idempotency_key. The actor is ops.authority_actor_slug().';

revoke all on function ops.register_slice_checkable_done(text, jsonb, uuid) from public;
grant execute on function ops.register_slice_checkable_done(text, jsonb, uuid) to carr_authority;

-- ===========================================================================
-- ops.workflow_cutover_retire_receipt_use -- each
-- ops.legacy_schedule_disable_receipt may retire exactly one cutover plan.
-- The primary key on receipt_id enforces one-use without touching the
-- shared, already-applied 0176 table at all.
-- ===========================================================================
create table if not exists ops.workflow_cutover_retire_receipt_use (
  receipt_id uuid primary key references ops.legacy_schedule_disable_receipt(id),
  plan_id uuid not null references ops.workflow_cutover_plan(id),
  used_at timestamptz not null default now()
);

comment on table ops.workflow_cutover_retire_receipt_use is
  'DoctorCRE V5-R02: one row per legacy_schedule_disable_receipt ever consumed by ops.retire_workflow_cutover_plan. The receipt_id primary key is the one-use enforcement.';

revoke all on table ops.workflow_cutover_retire_receipt_use from public, carr_writer, carr_jobs, carr_authority;
grant select on table ops.workflow_cutover_retire_receipt_use to carr_reader;

-- ===========================================================================
-- ops.workflow_caller_history -- append-only ledger of every caller status
-- change. ops.workflow_caller (above) remains the fast-lookup CURRENT state
-- workflow-cutover-board reads; this table is what "append history instead
-- of overwriting it" (PR #1245 review) actually means -- no update or delete
-- ever reaches a caller's prior status.
-- ===========================================================================
create table if not exists ops.workflow_caller_history (
  id uuid primary key default gen_random_uuid(),
  workflow_key text not null,
  workflow_version integer not null,
  caller_locator text not null,
  caller_kind text not null,
  status text not null,
  blocked_reason text,
  evidence_ref text,
  actor_slug text,
  recorded_at timestamptz not null default now()
);

create index if not exists workflow_caller_history_caller_idx
  on ops.workflow_caller_history (workflow_key, workflow_version, caller_locator, recorded_at);

create or replace function ops.refuse_workflow_caller_history_rewrite()
returns trigger language plpgsql as $$
begin
  raise exception 'workflow_caller_history is append-only';
end $$;

create trigger workflow_caller_history_append_only
  before update or delete on ops.workflow_caller_history
  for each row execute function ops.refuse_workflow_caller_history_rewrite();

comment on table ops.workflow_caller_history is
  'DoctorCRE V5-R02: append-only history of every ops.record_workflow_caller call. Never updated or deleted -- ops.workflow_caller holds only the current row per caller.';

revoke all on table ops.workflow_caller_history from public, carr_writer, carr_jobs, carr_authority;
grant select on table ops.workflow_caller_history to carr_reader;

-- ===========================================================================
-- Write doors
-- ===========================================================================

-- ops.open_workflow_cutover_plan: Q116 step 1 ("read legacy state") opens a
-- plan at stage='read_legacy'. Q153 stale-plan supersession: any existing
-- ACTIVE plan for the same (workflow_key, workflow_version) is superseded
-- first, in the same transaction, never left to silently coexist.
-- Authority-only (PR #1245 re-review P1-a): an active plan changes what
-- ops.enqueue_job admits for the workflow once it reaches
-- single_write_authority, so opening or superseding one is a human authority
-- act, never ordinary writer traffic. The plan must name a registered
-- workflow identity.
create or replace function ops.open_workflow_cutover_plan(
  p_workflow_key text,
  p_workflow_version integer,
  p_recovery_plan text,
  p_idempotency_key uuid
) returns ops.workflow_cutover_plan
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  v_authority_actor text;
  v_existing ops.workflow_cutover_plan%rowtype;
  v_prior ops.workflow_cutover_plan%rowtype;
  v_row ops.workflow_cutover_plan%rowtype;
begin
  v_authority_actor := ops.authority_actor_slug();
  if p_idempotency_key is null then
    raise exception 'idempotency_key_required';
  end if;
  select * into v_existing from ops.workflow_cutover_plan where idempotency_key = p_idempotency_key;
  if found then
    return v_existing;
  end if;
  if p_workflow_key is null or btrim(p_workflow_key) = '' then
    raise exception 'workflow_key_required';
  end if;
  if p_workflow_version is null or p_workflow_version < 1 then
    raise exception 'workflow_version_invalid';
  end if;
  if p_recovery_plan is null or btrim(p_recovery_plan) = '' then
    raise exception 'recovery_plan_required';
  end if;
  if not exists (
    select 1 from ops.job_definition where key = p_workflow_key and version = p_workflow_version
  ) then
    raise exception 'workflow_cutover_plan_unregistered_workflow: % v%', p_workflow_key, p_workflow_version;
  end if;

  select * into v_prior from ops.workflow_cutover_plan
   where workflow_key = p_workflow_key and workflow_version = p_workflow_version and status = 'active'
   for update;
  if found then
    -- Defense in depth: after ops.retire_workflow_cutover_plan sets
    -- status='retired' (never 'active'), a retired plan can no longer be
    -- selected here at all -- but a plan is also refused explicitly by stage
    -- in case some future write path ever leaves 'retired' under status='active'.
    if v_prior.stage = 'retired' then
      raise exception 'workflow_cutover_plan_supersede_refused_retired_plan';
    end if;
    update ops.workflow_cutover_plan
       set status = 'superseded', supersede_reason = 'superseded by a new open (idempotency_key ' || p_idempotency_key || ')',
           updated_at = now()
     where id = v_prior.id;
  end if;

  insert into ops.workflow_cutover_plan (
    workflow_key, workflow_version, stage, status, recovery_plan, opened_by_actor_slug, idempotency_key
  ) values (
    p_workflow_key, p_workflow_version, 'read_legacy', 'active', p_recovery_plan, v_authority_actor, p_idempotency_key
  ) returning * into v_row;

  if v_prior.id is not null then
    update ops.workflow_cutover_plan set superseded_by = v_row.id where id = v_prior.id;
  end if;

  insert into ops.workflow_cutover_stage_transition (
    plan_id, from_stage, to_stage, evidence_ref, reason, actor_slug, idempotency_key
  ) values (
    v_row.id, null, 'read_legacy',
    case when v_prior.id is not null then 'supersedes:' || v_prior.id else null end,
    case when v_prior.id is not null
      then 'plan opened; supersedes prior active plan ' || v_prior.id
      else 'plan opened' end,
    v_authority_actor, p_idempotency_key
  );

  return v_row;
end;
$$;

comment on function ops.open_workflow_cutover_plan(text, integer, text, uuid) is
  'DoctorCRE V5-R02 authority write door (Q116 step 1 / Q153 stale-plan supersession): opens a cutover plan at stage=read_legacy for a registered workflow identity, superseding any existing active plan for it. The actor is ops.authority_actor_slug(). Idempotent on p_idempotency_key.';

revoke all on function ops.open_workflow_cutover_plan(text, integer, text, uuid) from public;
grant execute on function ops.open_workflow_cutover_plan(text, integer, text, uuid) to carr_authority;

-- ops.workflow_cutover_plan_cancel: one row per cancelled plan. A cancel is
-- the recovery door for a plan that should stop governing its workflow:
-- status leaves 'active', so ops.enqueue_job no longer consults it and the
-- one-active-plan slot frees for a later open.
create table if not exists ops.workflow_cutover_plan_cancel (
  plan_id uuid primary key references ops.workflow_cutover_plan(id),
  stage_at_cancel text not null,
  reason text not null check (btrim(reason) <> ''),
  actor_slug text not null,
  idempotency_key uuid not null unique,
  cancelled_at timestamptz not null default now()
);

comment on table ops.workflow_cutover_plan_cancel is
  'DoctorCRE V5-R02: append-only record of every ops.cancel_workflow_cutover_plan call, one per plan.';

revoke all on table ops.workflow_cutover_plan_cancel from public, carr_writer, carr_jobs, carr_authority;
grant select on table ops.workflow_cutover_plan_cancel to carr_reader;

create or replace function ops.cancel_workflow_cutover_plan(
  p_plan_id uuid,
  p_reason text,
  p_idempotency_key uuid
) returns ops.workflow_cutover_plan
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  v_authority_actor text;
  v_existing ops.workflow_cutover_plan_cancel%rowtype;
  v_plan ops.workflow_cutover_plan%rowtype;
begin
  v_authority_actor := ops.authority_actor_slug();
  if p_idempotency_key is null then
    raise exception 'idempotency_key_required';
  end if;
  select * into v_existing from ops.workflow_cutover_plan_cancel where idempotency_key = p_idempotency_key;
  if found then
    select * into v_plan from ops.workflow_cutover_plan where id = v_existing.plan_id;
    return v_plan;
  end if;
  if p_reason is null or btrim(p_reason) = '' then
    raise exception 'reason_required';
  end if;
  select * into v_plan from ops.workflow_cutover_plan where id = p_plan_id for update;
  if not found then
    raise exception 'workflow_cutover_plan_not_found';
  end if;
  if v_plan.status <> 'active' then
    raise exception 'workflow_cutover_plan_not_active';
  end if;

  update ops.workflow_cutover_plan
     set status = 'cancelled', updated_at = now()
   where id = v_plan.id
  returning * into v_plan;

  insert into ops.workflow_cutover_plan_cancel (plan_id, stage_at_cancel, reason, actor_slug, idempotency_key)
  values (v_plan.id, v_plan.stage, p_reason, v_authority_actor, p_idempotency_key);

  return v_plan;
end;
$$;

comment on function ops.cancel_workflow_cutover_plan(uuid, text, uuid) is
  'DoctorCRE V5-R02 authority write door (PR #1245 re-review P1-a): cancels an active cutover plan at any stage before retirement. The plan stops governing ops.enqueue_job at once and frees the one-active-plan slot. The actor is ops.authority_actor_slug(). Idempotent on p_idempotency_key.';

revoke all on function ops.cancel_workflow_cutover_plan(uuid, text, uuid) from public;
grant execute on function ops.cancel_workflow_cutover_plan(uuid, text, uuid) to carr_authority;

-- ops.advance_workflow_cutover_stage: moves a plan forward exactly one step
-- in the fixed Q116 sequence, up to and including 'recovery_ready'. Cannot
-- reach 'retired' -- that is ops.retire_workflow_cutover_plan's door alone.
-- shadow_compare/single_write_authority/cutover each require fresh accepted
-- ops.workflow_acceptance evidence, so a plan cannot be talked forward on
-- narrative alone -- "shadow parity and single-writer checks pass" is
-- checked here, not asserted by the caller.
create or replace function ops.advance_workflow_cutover_stage(
  p_plan_id uuid,
  p_to_stage text,
  p_evidence_ref text,
  p_reason text,
  p_idempotency_key uuid
) returns ops.workflow_cutover_plan
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  v_authority_actor text;
  v_plan ops.workflow_cutover_plan%rowtype;
  v_stages text[] := array['read_legacy','build_projection','shadow_compare',
    'single_write_authority','cutover','monitor','recovery_ready'];
  v_from_idx integer;
  v_to_idx integer;
  v_existing_transition ops.workflow_cutover_stage_transition%rowtype;
  v_stage_entered_at timestamptz;
begin
  -- Authority-only (PR #1245 re-review P1-a): reaching single_write_authority
  -- changes what ops.enqueue_job admits for a live workflow, and every plan
  -- is opened by a human authority, so every stage move is one too.
  v_authority_actor := ops.authority_actor_slug();
  if p_idempotency_key is null then
    raise exception 'idempotency_key_required';
  end if;
  select * into v_existing_transition from ops.workflow_cutover_stage_transition
   where idempotency_key = p_idempotency_key;
  if found then
    select * into v_plan from ops.workflow_cutover_plan where id = v_existing_transition.plan_id;
    return v_plan;
  end if;
  if p_to_stage = 'retired' then
    raise exception 'retired_stage_requires_retire_door';
  end if;
  if p_reason is null or btrim(p_reason) = '' then
    raise exception 'reason_required';
  end if;

  select * into v_plan from ops.workflow_cutover_plan where id = p_plan_id for update;
  if not found then
    raise exception 'workflow_cutover_plan_not_found';
  end if;
  if v_plan.status <> 'active' then
    raise exception 'workflow_cutover_plan_not_active';
  end if;

  v_from_idx := array_position(v_stages, v_plan.stage);
  v_to_idx := array_position(v_stages, p_to_stage);
  if v_to_idx is null then
    raise exception 'workflow_cutover_stage_invalid';
  end if;
  -- P1 fix (PR #1245 review): v_stages holds only read_legacy..recovery_ready
  -- (retired is reached exclusively through ops.retire_workflow_cutover_plan),
  -- so a plan already at stage='retired' makes array_position return NULL,
  -- and `NULL <> v_to_idx + 1` evaluates to NULL/falsy in plpgsql -- the
  -- sequential check below silently never fires and 'retired' was not
  -- terminal. Raise explicitly instead of relying on the comparison.
  if v_from_idx is null then
    raise exception 'workflow_cutover_plan_stage_not_advanceable: %', v_plan.stage;
  end if;
  if v_to_idx <> v_from_idx + 1 then
    raise exception 'workflow_cutover_stage_not_sequential: at % requested %', v_plan.stage, p_to_stage;
  end if;

  if p_to_stage in ('shadow_compare', 'single_write_authority', 'cutover') then
    if p_evidence_ref is null or btrim(p_evidence_ref) = '' then
      raise exception 'evidence_ref_required_for_stage: %', p_to_stage;
    end if;
    if p_to_stage = 'shadow_compare' then
      if not exists (
        select 1 from ops.workflow_acceptance
         where workflow_key = v_plan.workflow_key and workflow_version = v_plan.workflow_version
           and mode = 'shadow' and status = 'accepted' and id::text = p_evidence_ref
      ) then
        raise exception 'shadow_acceptance_evidence_not_found_or_not_accepted';
      end if;
    else
      if not exists (
        select 1 from ops.workflow_acceptance
         where workflow_key = v_plan.workflow_key and workflow_version = v_plan.workflow_version
           and mode = 'canary' and status = 'accepted' and id::text = p_evidence_ref
      ) then
        raise exception 'canary_acceptance_evidence_not_found_or_not_accepted';
      end if;
      -- Canary freshness is checked ONCE, here, on the two moves that hand
      -- write authority to the new system (PR #1245 re-review 2, P1-a): the
      -- canary must have been accepted since the plan entered the stage it
      -- is leaving, so an acceptance from an earlier stage cannot carry the
      -- plan into single_write_authority or cutover. ops.enqueue_job does
      -- not re-check freshness per stage -- after cutover the new system is
      -- already the only writer, and monitor / recovery_ready must not halt
      -- a live workflow for want of yet another canary run.
      select occurred_at into v_stage_entered_at
        from ops.workflow_cutover_stage_transition
       where plan_id = v_plan.id and to_stage = v_plan.stage
       order by occurred_at desc limit 1;
      v_stage_entered_at := coalesce(v_stage_entered_at, v_plan.created_at);
      if not exists (
        select 1 from ops.workflow_acceptance
         where id::text = p_evidence_ref and created_at >= v_stage_entered_at
      ) then
        raise exception 'canary_acceptance_evidence_predates_current_stage: % entered at %',
          v_plan.stage, v_stage_entered_at;
      end if;
    end if;
  end if;

  update ops.workflow_cutover_plan set stage = p_to_stage, updated_at = now() where id = v_plan.id
    returning * into v_plan;

  insert into ops.workflow_cutover_stage_transition (
    plan_id, from_stage, to_stage, evidence_ref, reason, actor_slug, idempotency_key
  ) values (
    v_plan.id, v_stages[v_from_idx], p_to_stage, p_evidence_ref, p_reason, v_authority_actor, p_idempotency_key
  );

  return v_plan;
end;
$$;

comment on function ops.advance_workflow_cutover_stage(uuid, text, text, text, uuid) is
  'DoctorCRE V5-R02 authority write door (Q116): advances a cutover plan exactly one step forward through read_legacy..recovery_ready. Refuses to skip stages, refuses a non-active plan, and requires accepted ops.workflow_acceptance evidence for shadow_compare/single_write_authority/cutover; the canary evidence for single_write_authority and cutover must have been accepted since the plan entered the stage it is leaving. Never reaches retired -- see ops.retire_workflow_cutover_plan.';

revoke all on function ops.advance_workflow_cutover_stage(uuid, text, text, text, uuid) from public;
grant execute on function ops.advance_workflow_cutover_stage(uuid, text, text, text, uuid) to carr_authority;

-- ops.retire_workflow_cutover_plan: the ONLY door to stage='retired'.
-- Requires the plan to already be at 'recovery_ready' and requires an actual
-- ops.legacy_schedule_disable_receipt row -- i.e. a real, already-performed
-- ops.disable_legacy_schedule call -- referenced by id. It does not perform
-- a disable itself and cannot: disabling a native scheduler already runs
-- exclusively through ops.disable_legacy_schedule (Joe-only authority,
-- migrations 0176/0180/0182/0184).
create or replace function ops.retire_workflow_cutover_plan(
  p_plan_id uuid,
  p_disable_receipt_id uuid,
  p_reason text,
  p_idempotency_key uuid
) returns ops.workflow_cutover_plan
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  v_plan ops.workflow_cutover_plan%rowtype;
  v_existing_transition ops.workflow_cutover_stage_transition%rowtype;
  v_receipt ops.legacy_schedule_disable_receipt%rowtype;
  v_authority_actor text;
  v_cutover_at timestamptz;
  v_missing_surface_count integer;
  v_census_available boolean;
begin
  -- P1 fix (PR #1245 review, item 3): the real actor is the DB connection
  -- identity, exactly like ops.disable_legacy_schedule -- never the
  -- caller-supplied p_actor_slug string. This raises for any session that is
  -- not carr_authority_joe/dell, which is the SAME check the MCP layer's
  -- authorityOnly flag intends, done again here so a misconfigured envelope
  -- cannot bypass it.
  v_authority_actor := ops.authority_actor_slug();

  if p_idempotency_key is null then
    raise exception 'idempotency_key_required';
  end if;
  select * into v_existing_transition from ops.workflow_cutover_stage_transition
   where idempotency_key = p_idempotency_key;
  if found then
    select * into v_plan from ops.workflow_cutover_plan where id = v_existing_transition.plan_id;
    return v_plan;
  end if;
  if p_reason is null or btrim(p_reason) = '' then
    raise exception 'reason_required';
  end if;
  if p_disable_receipt_id is null then
    raise exception 'disable_receipt_id_required';
  end if;

  select * into v_plan from ops.workflow_cutover_plan where id = p_plan_id for update;
  if not found then
    raise exception 'workflow_cutover_plan_not_found';
  end if;
  if v_plan.status <> 'active' then
    raise exception 'workflow_cutover_plan_not_active';
  end if;
  if v_plan.stage <> 'recovery_ready' then
    raise exception 'workflow_cutover_plan_not_recovery_ready';
  end if;

  -- P1 fix (item 3): the receipt must have been approved AFTER this exact
  -- plan actually cut over -- a receipt from before the cutover transition
  -- proves nothing about THIS plan's dual-write window.
  select occurred_at into v_cutover_at from ops.workflow_cutover_stage_transition
   where plan_id = v_plan.id and to_stage = 'cutover'
   order by occurred_at desc limit 1;
  if v_cutover_at is null then
    raise exception 'workflow_cutover_plan_missing_cutover_transition';
  end if;

  select * into v_receipt from ops.legacy_schedule_disable_receipt where id = p_disable_receipt_id;
  if not found then
    raise exception 'legacy_schedule_disable_receipt_not_found';
  end if;
  -- P1 fix (item 3): match on BOTH workflow_key and workflow_version -- the
  -- prior version-blind check would accept a receipt issued for a different
  -- version of the same workflow_key.
  if v_receipt.workflow_key is distinct from v_plan.workflow_key
     or v_receipt.workflow_version is distinct from v_plan.workflow_version then
    raise exception 'legacy_schedule_disable_receipt_workflow_mismatch';
  end if;
  if v_receipt.approved_at < v_cutover_at then
    raise exception 'legacy_schedule_disable_receipt_predates_cutover_transition';
  end if;
  -- P1 fix (item 3): each receipt retires exactly one plan. The primary key
  -- on ops.workflow_cutover_retire_receipt_use.receipt_id is the real
  -- enforcement; this check exists to raise a legible error instead of a
  -- unique_violation.
  if exists (
    select 1 from ops.workflow_cutover_retire_receipt_use where receipt_id = p_disable_receipt_id
  ) then
    raise exception 'legacy_schedule_disable_receipt_already_used';
  end if;
  -- P1 fix (item 3): a workflow can have more than one legacy surface
  -- (ops.legacy_schedule_surface_registry, 0176) -- e.g. a launchd job AND a
  -- Claude Code scheduled task for the same workflow identity. Retirement
  -- requires a disable receipt for EVERY registered surface, not just the
  -- one named here.
  select count(*) into v_missing_surface_count
    from ops.legacy_schedule_surface_registry s
   where s.workflow_key = v_plan.workflow_key and s.workflow_version = v_plan.workflow_version
     and not exists (
       select 1 from ops.legacy_schedule_disable_receipt r
        where r.workflow_key = s.workflow_key and r.workflow_version = s.workflow_version
          and r.surface_id = s.surface_id and r.locator = s.locator
     );
  if v_missing_surface_count > 0 then
    raise exception 'legacy_schedule_disable_receipt_missing_for_% legacy surface(s)', v_missing_surface_count;
  end if;

  -- P1 fix (PR #1245 review, item 6 / Q157): retirement is irreversible and
  -- the independent workflow-truth census is the one signal that isn't
  -- purely a paperwork trail (the receipt and evidence chain above all just
  -- prove a human clicked the right buttons). A census that cannot say
  -- available:true means retirement proceeds fail-open on trust alone --
  -- refuse it, ordered AFTER every other check so the more specific
  -- receipt/stage errors above still surface first when those are what's
  -- actually wrong.
  --
  -- The census answer is DERIVED HERE, never taken from the caller (PR #1245
  -- re-review P3). This schema holds no durable workflow-truth census yet --
  -- the signed census store is a separate PR (#1244) -- so the derived answer
  -- is "unavailable" and every retirement is refused at this line. When the
  -- census store lands, this assignment becomes a read of it for exactly
  -- (v_plan.workflow_key, v_plan.workflow_version); until then there is no
  -- input, from any caller, that can make it true.
  v_census_available := false;
  if not v_census_available then
    raise exception 'workflow_cutover_retire_refused_census_unknown';
  end if;

  update ops.workflow_cutover_plan
     set stage = 'retired',
         -- P1 fix (item 2): status must leave 'active' too, or the plan
         -- keeps holding the one-active-plan slot forever and the partial
         -- unique index refuses ANY future open for the same identity.
         status = 'retired',
         updated_at = now()
   where id = v_plan.id
    returning * into v_plan;

  insert into ops.workflow_cutover_stage_transition (
    plan_id, from_stage, to_stage, evidence_ref, reason, actor_slug, idempotency_key
  ) values (
    v_plan.id, 'recovery_ready', 'retired', p_disable_receipt_id::text, p_reason, v_authority_actor, p_idempotency_key
  );

  insert into ops.workflow_cutover_retire_receipt_use (receipt_id, plan_id)
  values (p_disable_receipt_id, v_plan.id);

  return v_plan;
end;
$$;

comment on function ops.retire_workflow_cutover_plan(uuid, uuid, text, uuid) is
  'DoctorCRE V5-R02 authority write door (Q116 last step): the only path to stage=retired, which also moves status to retired so the plan frees the one-active-plan slot. Requires the plan at recovery_ready; a legacy_schedule_disable_receipt matching this exact (workflow_key, workflow_version), approved at or after the cutover transition, not already used to retire another plan, and one receipt for every ops.legacy_schedule_surface_registry row of this workflow. The workflow-truth census (Q157) is derived inside this function, never supplied by a caller; with no census store in this schema yet (#1244) it derives unavailable, so retirement is refused last, after the more specific stage/receipt errors. The real actor is ops.authority_actor_slug(), never a caller-supplied string.';

revoke all on function ops.retire_workflow_cutover_plan(uuid, uuid, text, uuid) from public;
grant execute on function ops.retire_workflow_cutover_plan(uuid, uuid, text, uuid) to carr_authority;

-- ops.record_workflow_caller: caller inventory upsert. status='done' is
-- refused without evidence_ref -- Q157, never a claim from silence.
create or replace function ops.record_workflow_caller(
  p_workflow_key text,
  p_workflow_version integer,
  p_caller_locator text,
  p_caller_kind text,
  p_status text,
  p_blocked_reason text,
  p_evidence_ref text,
  p_actor_slug text
) returns ops.workflow_caller
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  v_row ops.workflow_caller%rowtype;
begin
  if p_workflow_key is null or btrim(p_workflow_key) = '' then
    raise exception 'workflow_key_required';
  end if;
  if p_workflow_version is null or p_workflow_version < 1 then
    raise exception 'workflow_version_invalid';
  end if;
  if p_caller_locator is null or btrim(p_caller_locator) = '' then
    raise exception 'caller_locator_required';
  end if;
  if p_caller_kind not in ('script', 'verb', 'worker_route', 'job_definition', 'external') then
    raise exception 'caller_kind_invalid';
  end if;
  if p_status not in ('remaining', 'done', 'blocked', 'superseded', 'retired') then
    raise exception 'caller_status_invalid';
  end if;
  if p_status = 'done' and (p_evidence_ref is null or btrim(p_evidence_ref) = '') then
    raise exception 'caller_done_requires_evidence_ref';
  end if;
  if p_status = 'blocked' and (p_blocked_reason is null or btrim(p_blocked_reason) = '') then
    raise exception 'caller_blocked_requires_reason';
  end if;
  -- P1 fix (PR #1245 review, item 6 / Q157): status='done' for a workflow
  -- identity that names no real ops.job_definition row is exactly the same
  -- "hides uncertainty" failure Q157 already treats status=done without
  -- evidence_ref as -- a caller can claim done against a workflow_key/
  -- workflow_version that was never actually registered as a real
  -- migratable workflow, and nothing before this line would have noticed.
  if p_status = 'done' and not exists (
    select 1 from ops.job_definition
     where key = p_workflow_key and version = p_workflow_version
  ) then
    raise exception 'caller_done_requires_registered_job_definition: % v%', p_workflow_key, p_workflow_version;
  end if;

  insert into ops.workflow_caller (
    workflow_key, workflow_version, caller_locator, caller_kind, status,
    blocked_reason, evidence_ref, updated_by_actor_slug
  ) values (
    p_workflow_key, p_workflow_version, p_caller_locator, p_caller_kind, p_status,
    p_blocked_reason, p_evidence_ref, p_actor_slug
  )
  on conflict (workflow_key, workflow_version, caller_locator) do update
    set caller_kind = excluded.caller_kind, status = excluded.status,
        blocked_reason = excluded.blocked_reason, evidence_ref = excluded.evidence_ref,
        updated_by_actor_slug = excluded.updated_by_actor_slug, updated_at = now()
  returning * into v_row;

  -- P1 fix (item 6): "append history instead of overwriting it" (PR #1245
  -- review) -- the upsert above is still the fast-lookup CURRENT row
  -- workflow-cutover-board reads, but every call, upsert or first-insert,
  -- now also appends its own row to the append-only
  -- ops.workflow_caller_history ledger, so a prior status is never lost to
  -- the next update the way the bare upsert above would lose it on its own.
  insert into ops.workflow_caller_history (
    workflow_key, workflow_version, caller_locator, caller_kind, status,
    blocked_reason, evidence_ref, actor_slug
  ) values (
    p_workflow_key, p_workflow_version, p_caller_locator, p_caller_kind, p_status,
    p_blocked_reason, p_evidence_ref, p_actor_slug
  );

  return v_row;
end;
$$;

comment on function ops.record_workflow_caller is
  'DoctorCRE V5-R02 write door: upserts one caller row for one workflow identity into ops.workflow_caller (current state) and appends one row to ops.workflow_caller_history (full history, never overwritten). status=done is refused without a non-null evidence_ref and without a real ops.job_definition row for the exact workflow identity; status=blocked is refused without blocked_reason -- Q157, an absent-evidence or unregistered caller cannot read back done.';

revoke all on function ops.record_workflow_caller(text, integer, text, text, text, text, text, text) from public;
grant execute on function ops.record_workflow_caller(text, integer, text, text, text, text, text, text) to carr_writer;

-- ops.workflow_cutover_board: read projection. Reports the active plan (or
-- none), its stage history, and caller counts by status. Never claims a
-- workflow-level census state -- that independent, fail-closed check is
-- performed by the MCP verb layer (workflow-cutover-board), which calls
-- lib/control_plane_workflow_truth_reader.py itself and merges the result
-- in, never trusting a caller-supplied claim.
create or replace function ops.workflow_cutover_board(
  p_workflow_key text,
  p_workflow_version integer
) returns jsonb
language sql
stable
set search_path = pg_catalog, ops
as $$
  select jsonb_build_object(
    'schema', 'doctorcre-v5-r02-workflow-cutover-board.v1',
    'workflow_key', p_workflow_key,
    'workflow_version', p_workflow_version,
    'plan', (
      select jsonb_build_object(
        'id', p.id, 'stage', p.stage, 'status', p.status,
        'recovery_plan', p.recovery_plan, 'superseded_by', p.superseded_by,
        'created_at', p.created_at, 'updated_at', p.updated_at
      )
      from ops.workflow_cutover_plan p
      where p.workflow_key = p_workflow_key and p.workflow_version = p_workflow_version and p.status = 'active'
    ),
    'stage_history', coalesce((
      select jsonb_agg(jsonb_build_object(
        'from_stage', t.from_stage, 'to_stage', t.to_stage, 'evidence_ref', t.evidence_ref,
        'reason', t.reason, 'occurred_at', t.occurred_at
      ) order by t.occurred_at)
      from ops.workflow_cutover_stage_transition t
      join ops.workflow_cutover_plan p on p.id = t.plan_id
      where p.workflow_key = p_workflow_key and p.workflow_version = p_workflow_version
    ), '[]'::jsonb),
    'caller_counts', coalesce((
      select jsonb_object_agg(status, ct) from (
        select status, count(*) ct from ops.workflow_caller
         where workflow_key = p_workflow_key and workflow_version = p_workflow_version
         group by status
      ) counted
    ), '{}'::jsonb),
    'callers', coalesce((
      select jsonb_agg(jsonb_build_object(
        'caller_locator', c.caller_locator, 'caller_kind', c.caller_kind, 'status', c.status,
        'blocked_reason', c.blocked_reason, 'evidence_ref', c.evidence_ref, 'updated_at', c.updated_at
      ) order by c.caller_locator)
      from ops.workflow_caller c
      where c.workflow_key = p_workflow_key and c.workflow_version = p_workflow_version
    ), '[]'::jsonb)
  );
$$;

comment on function ops.workflow_cutover_board is
  'DoctorCRE V5-R02 read door: active plan, stage history and caller counts for one workflow identity. Carries no census claim -- the MCP verb layer merges an independently fail-closed census read in on top of this.';

revoke all on function ops.workflow_cutover_board(text, integer) from public;
grant execute on function ops.workflow_cutover_board(text, integer) to carr_reader;

-- ops.slice_completion_evaluate: the ONE place a submitted criteria receipt
-- is checked and recomputed. Not a door: no role holds EXECUTE; the two mark
-- doors below call it. p_criteria_receipt is a jsonb array of
-- {"criterion", "evidence_ref"} (evidence_ref may be null for a criterion
-- not yet proven). Rules:
--   * the slice_id must be registered;
--   * each criterion appears once -- [A, A] is refused, it never satisfies
--     {A, B} (PR #1245 re-review P1-c);
--   * the DISTINCT submitted set must equal the registered set exactly;
--   * pass is recomputed from the criterion's registered binding: the
--     evidence_ref must name an evidence row of the bound type, for the bound
--     workflow identity, in the bound mode or stage. The caller's own pass
--     claim is ignored.
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
  v_binding ops.slice_checkable_done_registry%rowtype;
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
    select * into v_binding from ops.slice_checkable_done_registry
     where slice_id = p_slice_id and criterion = btrim(v_el->>'criterion');
    v_ref := nullif(btrim(coalesce(v_el->>'evidence_ref', '')), '');
    v_resolved := false;
    if v_ref is not null
       and v_ref ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' then
      if v_binding.evidence_kind = 'acceptance' then
        select exists (
          select 1 from ops.workflow_acceptance a
           where a.id = v_ref::uuid and a.status = 'accepted'
             and a.workflow_key = v_binding.workflow_key
             and a.workflow_version = v_binding.workflow_version
             and a.mode = v_binding.acceptance_mode
        ) into v_resolved;
      else
        select exists (
          select 1 from ops.workflow_cutover_stage_transition t
            join ops.workflow_cutover_plan p on p.id = t.plan_id
           where t.id = v_ref::uuid
             and t.to_stage = v_binding.transition_to_stage
             and p.workflow_key = v_binding.workflow_key
             and p.workflow_version = v_binding.workflow_version
        ) into v_resolved;
      end if;
    end if;
    v_computed := v_computed || jsonb_build_array(jsonb_build_object(
      'criterion', v_binding.criterion,
      'evidence_kind', v_binding.evidence_kind,
      'workflow_key', v_binding.workflow_key,
      'workflow_version', v_binding.workflow_version,
      'evidence_ref', v_ref,
      'pass', v_resolved
    ));
  end loop;
  return v_computed;
end;
$$;

comment on function ops.slice_completion_evaluate(text, jsonb) is
  'DoctorCRE V5-R02 / Q153 internal evaluator (no EXECUTE grant): checks a submitted criteria receipt against the slice''s registered criteria (registered slice, no duplicate criterion, distinct set equal to the registered set) and recomputes pass from each criterion''s registered workflow and evidence binding.';

revoke all on function ops.slice_completion_evaluate(text, jsonb) from public;

-- ops.mark_slice_progress: writer door for in_progress / blocked. It can
-- never write status=complete.
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
begin
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

  insert into ops.slice_completion_mark (
    slice_id, status, criteria_receipt, reason, marked_by_actor_slug, idempotency_key
  ) values (
    p_slice_id, p_status, ops.slice_completion_evaluate(p_slice_id, p_criteria_receipt),
    p_reason, p_actor_slug, p_idempotency_key
  ) returning * into v_row;
  return v_row;
end;
$$;

comment on function ops.mark_slice_progress(text, text, jsonb, text, uuid, text) is
  'DoctorCRE V5-R02 / Q153 writer door: append an in_progress or blocked mark for a registered slice_id, with the criteria receipt recomputed by ops.slice_completion_evaluate. Never writes complete. Idempotent on p_idempotency_key.';

revoke all on function ops.mark_slice_progress(text, text, jsonb, text, uuid, text) from public;
grant execute on function ops.mark_slice_progress(text, text, jsonb, text, uuid, text) to carr_writer;

-- ops.mark_slice_completion: Q153's explicit completion mark, a human
-- authority act. Refused unless every registered criterion resolved to its
-- bound evidence.
create or replace function ops.mark_slice_completion(
  p_slice_id text,
  p_criteria_receipt jsonb,
  p_reason text,
  p_idempotency_key uuid
) returns ops.slice_completion_mark
language plpgsql security definer
set search_path = pg_catalog, ops
as $$
declare
  v_authority_actor text;
  v_existing ops.slice_completion_mark%rowtype;
  v_row ops.slice_completion_mark%rowtype;
  v_computed jsonb;
begin
  v_authority_actor := ops.authority_actor_slug();
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
    slice_id, status, criteria_receipt, reason, marked_by_actor_slug, idempotency_key
  ) values (
    p_slice_id, 'complete', v_computed, p_reason, v_authority_actor, p_idempotency_key
  ) returning * into v_row;
  return v_row;
end;
$$;

comment on function ops.mark_slice_completion(text, jsonb, text, uuid) is
  'DoctorCRE V5-R02 / Q153 authority write door: append status=complete for a registered slice_id only when ops.slice_completion_evaluate resolves every registered criterion to evidence of its bound type, workflow and mode/stage. The actor is ops.authority_actor_slug(). Idempotent on p_idempotency_key.';

revoke all on function ops.mark_slice_completion(text, jsonb, text, uuid) from public;
grant execute on function ops.mark_slice_completion(text, jsonb, text, uuid) to carr_authority;

-- ops.read_slice_completion: latest mark per slice_id.
create or replace function ops.read_slice_completion(p_slice_id text)
returns ops.slice_completion_mark
language sql
stable
set search_path = pg_catalog, ops
as $$
  select * from ops.slice_completion_mark
   where slice_id = p_slice_id
   order by mark_seq desc
   limit 1;
$$;

comment on function ops.read_slice_completion is
  'DoctorCRE V5-R02 / Q153 read door: the current (most recent) completion mark for one slice_id, or no row when the slice has never been marked.';

revoke all on function ops.read_slice_completion(text) from public;
grant execute on function ops.read_slice_completion(text) to carr_reader;
