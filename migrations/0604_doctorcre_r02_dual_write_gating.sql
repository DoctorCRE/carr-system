-- 0604_doctorcre_r02_dual_write_gating.sql
--
-- DoctorCRE V5-R02 / PR #1245 review, item 5: ops.enqueue_job did not know
-- the V5-R02 workflow-cutover state machine (0602) existed at all. A
-- workflow mid-cutover -- already at single_write_authority or beyond, where
-- the NEW system is supposed to be the only writer -- could still have its
-- legacy schedule surfaces enqueue live jobs with no relationship to the
-- cutover plan's own stage or evidence ladder, defeating the whole point of
-- Q116's staged cutover. This migration re-creates ops.enqueue_job (owned by
-- migration 0498, already applied to production, so it can only be replaced
-- forward, never edited in place) with two additional gates that apply ONLY
-- when an active ops.workflow_cutover_plan exists for the exact
-- (definition_key, definition_version):
--
--   1. At stage single_write_authority or later, live-mode enqueue requires
--      proof the legacy side cannot also be writing: a
--      ops.legacy_schedule_disable_receipt for every
--      ops.legacy_schedule_surface_registry row of the workflow, or no
--      legacy surface registered at all. This is the same
--      "every legacy surface" anti-join ops.retire_workflow_cutover_plan
--      (0602, PR #1245 item 3) already uses -- single_write_authority is
--      exactly the point where that proof starts mattering, not only at
--      final retirement.
--   2. Canary mode is never gated here, and neither is any stage before
--      single_write_authority (PR #1245 re-review P1-a: gating them let
--      merely opening a plan halt a live workflow with no way to produce
--      fresh evidence). Canary FRESHNESS is not re-checked here per stage
--      either (PR #1245 re-review 2, P1-a): a per-stage requirement made
--      every advance after cutover -- monitor, recovery_ready -- halt the
--      live workflow again although the new system was already the only
--      writer. Freshness is checked once, by
--      ops.advance_workflow_cutover_stage (0602), on the moves into
--      single_write_authority and cutover: the canary evidence must have
--      been accepted since the plan entered the stage it is leaving.
--
-- A workflow with no active cutover plan (the overwhelming majority) is
-- completely unaffected: the new block is gated on `found` from the
-- ops.workflow_cutover_plan lookup, and 0498's original shadow/canary/live
-- evidence ladder and duplicate_group exclusion are reproduced verbatim
-- below, unchanged.
--
-- SCAC: this is a security-definer function replacement, so it owes a
-- registry successor. Per the coordinator's updated numbering rule (this PR,
-- 2026-09-24): there are no pre-assigned SCAC versions any more, and the
-- successor for every function changed in this PR branch -- including this
-- one -- is sealed together at PR #1245 item 8, against origin/main's actual
-- newest version at merge time, not a number guessed now.

create or replace function ops.enqueue_job(
  p_definition_key text,
  p_definition_version integer,
  p_scheduled_for timestamptz,
  p_payload jsonb,
  p_idempotency_key text,
  p_mode text default 'live'
) returns ops.job
language plpgsql security definer set search_path=ops,public,pg_temp
as $$
declare
  d ops.job_definition%rowtype;
  j ops.job%rowtype;
  canary_disabled boolean;
  duplicate_group_name text;
  conflicting_key text;
  v_plan ops.workflow_cutover_plan%rowtype;
  v_stages text[] := array['read_legacy','build_projection','shadow_compare',
    'single_write_authority','cutover','monitor','recovery_ready'];
  v_stage_idx integer;
  v_swa_idx integer;
  v_missing_surface_count integer;
begin
  select * into d from ops.job_definition
   where key=p_definition_key and version=p_definition_version and enabled;
  if not found then
    raise exception 'job definition % v% is not enabled',p_definition_key,p_definition_version;
  end if;
  if p_mode not in ('shadow','canary','live','replay') then
    raise exception 'invalid job mode %',p_mode;
  end if;
  -- A missing canary key (every cognition contract, and any deterministic
  -- contract that never named one) is not the same claim as an explicit
  -- canary.enabled=false; only the explicit false is a contractual refusal.
  canary_disabled := (d.execution_contract #>> '{canary,enabled}') = 'false';
  if p_mode='canary' then
    if canary_disabled then
      raise exception 'workflow % cannot enqueue canary mode: canary is contractually disabled for definition v%',
        p_definition_key,p_definition_version;
    end if;
    if not exists (
      select 1 from ops.workflow_acceptance
       where workflow_key=p_definition_key and workflow_version=p_definition_version
         and mode='shadow' and status='accepted'
    ) then
      raise exception 'workflow % cannot enqueue canary mode: no accepted shadow acceptance evidence for definition v%',
        p_definition_key,p_definition_version;
    end if;
  elsif p_mode='live' then
    if canary_disabled then
      if not exists (
        select 1 from ops.workflow_acceptance
         where workflow_key=p_definition_key and workflow_version=p_definition_version
           and mode='shadow' and status='accepted'
      ) then
        raise exception 'workflow % cannot enqueue live mode: canary is contractually disabled and no accepted shadow acceptance evidence exists for definition v%',
          p_definition_key,p_definition_version;
      end if;
    elsif not exists (
      select 1 from ops.workflow_acceptance
       where workflow_key=p_definition_key and workflow_version=p_definition_version
         and mode='canary' and status='accepted'
    ) then
      raise exception 'workflow % cannot enqueue live mode: no accepted canary acceptance evidence for definition v%',
        p_definition_key,p_definition_version;
    end if;
  end if;

  -- P1 fix (PR #1245 review, item 5): tie enqueue to the V5-R02 cutover
  -- plan's own stage when one is active for this exact workflow identity.
  -- PR #1245 re-review P1-a: the gate applies only to LIVE mode and only
  -- once the plan has reached single_write_authority. Before that stage an
  -- open plan changes nothing about what a live workflow may enqueue, and
  -- canary jobs are never gated here -- canary is how the acceptance
  -- evidence ops.advance_workflow_cutover_stage requires gets produced, so
  -- gating it would leave the workflow no way to recover. The only check
  -- is that every legacy surface is disabled; canary freshness is not
  -- re-checked per stage (re-review 2).
  select * into v_plan from ops.workflow_cutover_plan
   where workflow_key=p_definition_key and workflow_version=p_definition_version
     and status='active';
  v_stage_idx := array_position(v_stages, v_plan.stage);
  v_swa_idx := array_position(v_stages, 'single_write_authority');
  if v_plan.id is not null and p_mode = 'live' and v_stage_idx is not null and v_stage_idx >= v_swa_idx then
    select count(*) into v_missing_surface_count
      from ops.legacy_schedule_surface_registry s
     where s.workflow_key = p_definition_key and s.workflow_version = p_definition_version
       and not exists (
         select 1 from ops.legacy_schedule_disable_receipt r
          where r.workflow_key = s.workflow_key and r.workflow_version = s.workflow_version
            and r.surface_id = s.surface_id and r.locator = s.locator
       );
    if v_missing_surface_count > 0 then
      raise exception 'workflow % cannot enqueue live mode at cutover stage %: % legacy surface(s) still undisabled',
        p_definition_key, v_plan.stage, v_missing_surface_count;
    end if;
  end if;

  -- V5-F09: exclude two DISTINCT registered workflow identities that share one
  -- duplicate_group from both becoming executable at one canonical slot.
  select r.duplicate_group into duplicate_group_name
    from ops.legacy_schedule_surface_registry r
   where r.workflow_key=p_definition_key and r.workflow_version=p_definition_version
     and r.duplicate_group is not null
   limit 1;
  if duplicate_group_name is not null then
    -- ONE bigint key: the two-argument advisory form is (int4,int4) and would
    -- not take these hashes. The slot is rendered as epoch microseconds rather
    -- than ::text because a timestamptz cast follows the CALLER's TimeZone, and
    -- two connections in different zones must not hash one instant into two
    -- different locks -- which would silently reopen the race this closes.
    perform pg_advisory_xact_lock(hashtextextended(
      'carr.enqueue_job.duplicate_group:'||duplicate_group_name||'@'||
      (extract(epoch from p_scheduled_for)*1000000)::bigint::text,0));
    -- A cancelled job never executed; every other state either is executable or
    -- already executed, so only 'cancelled' is excluded here.
    select j2.definition_key into conflicting_key
      from ops.job j2
      join ops.legacy_schedule_surface_registry r2
        on r2.workflow_key=j2.definition_key and r2.workflow_version=j2.definition_version
     where r2.duplicate_group=duplicate_group_name
       and j2.scheduled_for=p_scheduled_for
       and j2.definition_key<>p_definition_key
       and j2.state<>'cancelled'
     order by j2.definition_key
     limit 1;
    if conflicting_key is not null then
      raise exception 'workflow % cannot enqueue: duplicate_group % already has an executable job for this canonical slot from workflow %',
        p_definition_key,duplicate_group_name,conflicting_key;
    end if;
  end if;

  insert into ops.job
    (definition_key,definition_version,idempotency_key,scheduled_for,mode,payload,
     max_attempts,timeout_seconds)
  values
    (d.key,d.version,p_idempotency_key,p_scheduled_for,p_mode,coalesce(p_payload,'{}'::jsonb),
     (d.retry_policy->>'max_attempts')::integer,
     (d.retry_policy->>'timeout_seconds')::integer)
  on conflict do nothing
  returning * into j;
  if j.id is null then
    select * into j from ops.job
     where idempotency_key=p_idempotency_key
        or (definition_key=p_definition_key
            and definition_version=p_definition_version
            and scheduled_for=p_scheduled_for)
     order by (idempotency_key=p_idempotency_key) desc
     limit 1;
    if j.id is null
       or j.definition_key <> p_definition_key
       or j.definition_version <> p_definition_version
       or j.scheduled_for <> p_scheduled_for
       or j.payload <> coalesce(p_payload,'{}'::jsonb)
       or j.mode <> p_mode then
      raise exception 'duplicate delivery conflicts with the canonical scheduled job';
    end if;
  end if;
  return j;
end $$;

comment on function ops.enqueue_job(text, integer, timestamptz, jsonb, text, text) is
  'The only admission path into ops.job. 0334''s enabled-definition gate and shadow/canary/live evidence ladder, 0498''s duplicate_group exclusion, and (0604, PR #1245 item 5) the V5-R02 workflow-cutover gate: when an active ops.workflow_cutover_plan exists for this exact workflow identity, live mode at single_write_authority or later requires a disable receipt for every registered legacy surface (canary freshness is checked once, by ops.advance_workflow_cutover_stage, not per stage here). Earlier stages and canary mode are never gated by a plan.';
