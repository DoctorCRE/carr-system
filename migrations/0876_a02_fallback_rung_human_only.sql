-- 0876_a02_fallback_rung_human_only.sql
-- rollback: re-run the create or replace from migrations/0721_a02_rule_enforcement_coverage.sql (the fallback rung then binds every rule again); no data is written here
--
-- V5-A02: THE FALLBACK RUNG BINDS HUMAN-ONLY RULES, NOT HARD-ENFORCED ONES
-- WHOSE CONTROLS ALREADY REPORT THEIR OWN UNAVAILABILITY.
--
-- ops.v5_a02_rule_enforcement_coverage() (0721) demanded a Joe-authority
-- fallback receipt for EVERY active rule, after its approved controls passed.
-- For a human_only rule that is right: its only control is the human
-- authority runtime, and the receipt names what a session does when that is
-- unavailable. For a machine_enforceable rule whose current exact approval is
-- hard_enforced it is a second Joe act per rule on top of an installed,
-- verified, test-bound control, and decision
-- cbe91287-10a2-48d0-bda7-3b1784b4bb93 (Joe, 2026-10-12) removes his approval
-- from everything the system can do itself. On 2026-10-12 production read
-- 219 active rules, 0 covered, and the two hard_enforced rules with receipts
-- (1fcaa63a, 6cfb67f5) failed on this rung alone.
--
-- WHAT CHANGES. One new fact, fallback_required: false only when the rule's
-- CURRENT exact approval (same version and statement hash) is
-- machine_enforceable with enforcement_status hard_enforced AND every control
-- that approval names is of a kind whose unavailability the system already
-- reports by machine (below). The active_rule_fallback_absent rung fires only
-- when it is required. Fail-safe by construction: a rule with no approval, an
-- approval for an earlier version, a human_only approval, or an approval that
-- names any other kind of control still needs its receipt. Every earlier rung
-- (control mapped, all controls installed, tests recorded, passing, not
-- future-dated, unchanged since approval) is untouched, so a hard_enforced
-- rule is covered only when its controls hold. The returned shape and
-- schema_version are unchanged; fallback_required joins the per-rule facts,
-- so the evidence-digest preimage gains that one key.
--
-- WHAT THIS GIVES UP, AND WHY THAT IS ACCEPTABLE. For an exempt rule the
-- system no longer holds a receipt saying what it does when the installed
-- control is unavailable: a disabled hook, the Worker down, catalog drift.
-- The exemption is limited to the two kinds where a machine check already
-- answers that question:
--   * Hook controls: every implementation_ref entry is a hooks/*.py file.
--     hooks/gate-integrity.py runs at every SessionStart, hashes every
--     hooks/*.py against ops/config/gate-baseline.json (changed, missing and
--     unblessed hooks are each named) and compares the live settings hooks
--     block with ops/config/hooks.json (a hook that is no longer invoked is
--     named); the `machine config` row of ./run.sh health repeats the wiring
--     comparison. A missing, changed or disabled gate is therefore named the
--     next time any session boots, which is the receipt's job.
--   * transactional_schema controls: the refusal runs inside the write's own
--     transaction or request, so when the database or the Worker is
--     unavailable the write does not happen at all. That is refuse_closed,
--     the receipt's own answer, made structural.
-- Catalog drift is caught for both by this function's own earlier rungs (an
-- uninstalled control, or a verified_at that moved since approval, is a named
-- gap). For those kinds the rung duplicated a check the system already makes.
-- The claim is FALSE for the other kinds, so they keep the rung: githook
-- deny gates (commit_claims, index_upkeep, path_hygiene, staged_gate_bless),
-- CI and script deny gates (eval_receipt, source_merge_eligibility,
-- calendar_intake, client_asset_packet, delegation_names_model_and_effort,
-- control-plane-workflow-manifest, platform_metering_pre_dispatch,
-- close_before_open), schema-class checks run as scripts (glanceable_lead,
-- stale_count, trigger_grant), and any control whose implementation names a
-- file outside hooks/. gate-integrity does not hash those files, so nothing
-- names their unavailability at boot; the existing fallback receipt stays the
-- record for them. On 2026-10-12 that keeps 6cfb67f5 (control
-- delegation_names_model_and_effort, tools/room-bridge/dispatch.py) on the
-- rung.

create or replace function ops.v5_a02_rule_enforcement_coverage()
returns jsonb
language plpgsql stable security definer set search_path=ops,public,pg_temp
as $fn$
declare
  v_active integer;
  v_covered integer;
  v_gap_count integer;
  v_gaps jsonb;
  v_facts jsonb;
  v_complete boolean;
  v_state text;
  v_observed_at timestamptz := clock_timestamp();
  v_evidence_digest text;
begin
  with active_rule as (
    select r.id as rule_id, r.version,
           encode(public.digest(r.statement, 'sha256'), 'hex') as statement_hash
      from public.rule r
     where r.status = 'active'
  ), approved_control as (
    -- One row per control named by each current exact approval, whether or
    -- not that control is installed: a missing control must be visible.
    select a.rule_id, ar.id as approval_id,
           coalesce(ep.installed and c.installed
                    and b.statement_hash = a.statement_hash, false) as mapped,
           coalesce(ep.installed and c.installed
                    and b.statement_hash = a.statement_hash
                    and ep.verified_at is not null
                    and c.verified_at is not null
                    and btrim(ep.test_ref) <> '' and btrim(c.test_ref) <> '', false) as recorded,
           coalesce(ep.verified_at > v_observed_at
                    or c.verified_at > v_observed_at, false) as future_dated,
           c.verified_at is distinct from ep.verified_at as changed_since_approval,
           -- 0876: a control whose unavailability the system already names by
           -- machine (see the header): transactional_schema, or implemented
           -- only by hooks/*.py files that gate-integrity hashes and wires.
           -- A missing catalog row is never self-alarming.
           coalesce(c.control_key is not null
                    and (c.enforcement_class = 'transactional_schema'
                         or not exists (
                           select 1
                             from regexp_split_to_table(btrim(c.implementation_ref), '\s*;\s*') ref(path)
                            where ref.path !~ '^hooks/[A-Za-z0-9_.-]+[.]py$')), false) as self_alarming
      from active_rule a
      join ops.rule_approval_receipt ar
        on ar.rule_id = a.rule_id
       and ar.rule_version = a.version
       and ar.statement_hash = a.statement_hash
      join lateral unnest(ar.installed_control_keys) approved(control_key) on true
      left join ops.rule_enforcement_point ep
        on ep.rule_id = ar.rule_id and ep.control_key = approved.control_key
      left join ops.enforcement_control_catalog c
        on c.control_key = approved.control_key
      left join ops.rule_control_binding b
        on b.rule_id = ar.rule_id and b.control_key = approved.control_key
  ), approval as (
    -- Per approval, ALL of its controls must hold, never just one.
    select rule_id, approval_id,
           bool_or(mapped) as any_mapped,
           bool_and(mapped) as all_mapped,
           bool_and(recorded) as all_recorded,
           bool_or(recorded and future_dated) as any_future_dated,
           bool_and(recorded and not future_dated and not changed_since_approval) as all_passing,
           bool_and(self_alarming) as all_self_alarming
      from approved_control
     group by rule_id, approval_id
  ), facts as (
    select a.rule_id,
           exists (select 1 from approval p
                    where p.rule_id = a.rule_id and p.any_mapped) as control_mapped,
           exists (select 1 from approval p
                    where p.rule_id = a.rule_id and p.all_mapped) as all_controls_mapped,
           exists (
             select 1 from ops.rule_approval_receipt ar
              where ar.rule_id = a.rule_id
                and (ar.rule_version <> a.version
                     or ar.statement_hash <> a.statement_hash)
           ) as approved_earlier_version,
           exists (select 1 from approval p
                    where p.rule_id = a.rule_id and p.all_mapped
                      and p.all_recorded) as tests_recorded,
           exists (select 1 from approval p
                    where p.rule_id = a.rule_id and p.all_mapped
                      and p.all_passing) as tests_passing,
           exists (select 1 from approval p
                    where p.rule_id = a.rule_id and p.all_mapped
                      and p.all_recorded and p.any_future_dated) as tests_future_dated,
           exists (
             select 1
               from ops.rule_enforcement_fallback_receipt fr
              where fr.rule_id = a.rule_id
                and fr.rule_version = a.version
                and fr.statement_hash = a.statement_hash
           ) as fallback_recorded,
           -- 0876: the fallback rung binds human_only rules, any rule without
           -- a current exact hard_enforced approval, and any approval naming a
           -- control whose unavailability nothing names by machine. Only a
           -- machine_enforceable/hard_enforced approval whose EVERY control is
           -- self-alarming (header) is exempt.
           not exists (
             select 1 from ops.rule_approval_receipt ar
               join approval p on p.approval_id = ar.id and p.rule_id = ar.rule_id
              where ar.rule_id = a.rule_id
                and ar.rule_version = a.version
                and ar.statement_hash = a.statement_hash
                and ar.policy_kind = 'machine_enforceable'
                and ar.enforcement_status = 'hard_enforced'
                and p.all_self_alarming
           ) as fallback_required
      from active_rule a
  ), classified as (
    select f.*,
           case
             when not control_mapped and approved_earlier_version
               then 'active_rule_amended_needs_reapproval'
             when not control_mapped then 'active_rule_control_unmapped'
             when not all_controls_mapped
               then 'active_rule_approved_control_not_installed'
             when not tests_recorded then 'rule_tests_not_passing'
             when not tests_passing and tests_future_dated
               then 'rule_test_evidence_future_dated'
             when not tests_passing then 'rule_test_evidence_changed_since_approval'
             when fallback_required and not fallback_recorded then 'active_rule_fallback_absent'
             else null
           end as reason_id
      from facts f
  ), explained as (
    select c.*,
           case c.reason_id
             when 'active_rule_amended_needs_reapproval' then
               'the rule was amended after its control was approved; approve the current version and statement again'
             when 'active_rule_control_unmapped' then
               'no current approval-bound installed control matches this rule version and statement'
             when 'active_rule_approved_control_not_installed' then
               'the current approval names a control that is not installed and bound to this statement; every approved control must hold'
             when 'rule_tests_not_passing' then
               'an approved control has no recorded verified test evidence'
             when 'rule_test_evidence_future_dated' then
               'an approved control''s test evidence is dated after this read, so it cannot be current evidence'
             when 'rule_test_evidence_changed_since_approval' then
               'the control''s current verified_at is not the value its approval captured on the enforcement point'
             when 'active_rule_fallback_absent' then
               'no Joe-authority fallback receipt matches this rule version and statement'
             else null
           end as detail
      from classified c
  )
  select count(*)::integer,
         count(*) filter (where reason_id is null)::integer,
         count(*) filter (where reason_id is not null)::integer,
         coalesce(jsonb_agg(jsonb_build_object(
           'rule_id', rule_id, 'reason_id', reason_id, 'detail', detail)
           order by rule_id) filter (where reason_id is not null), '[]'::jsonb),
         coalesce(jsonb_agg(jsonb_build_object(
           'rule_id', rule_id, 'control_mapped', control_mapped,
           'all_controls_mapped', all_controls_mapped,
           'approved_earlier_version', approved_earlier_version,
           'tests_recorded', tests_recorded, 'tests_passing', tests_passing,
           'fallback_recorded', fallback_recorded,
           'fallback_required', fallback_required)
           order by rule_id), '[]'::jsonb)
    into v_active, v_covered, v_gap_count, v_gaps, v_facts
    from explained;

  v_state := case
    when v_active = 0 then 'empty'
    when v_gap_count > 0 then 'gaps'
    else 'complete'
  end;
  v_complete := v_state = 'complete';
  if (v_complete and (v_active = 0 or v_gap_count <> 0 or jsonb_array_length(v_gaps) <> 0))
     or v_covered + v_gap_count <> v_active then
    raise exception using message = 'rule_coverage_false_green';
  end if;

  v_evidence_digest := 'sha256:' || encode(public.digest(convert_to(
    jsonb_build_object(
      'schema_version', 'doctorcre-v5-a02-rule-enforcement-coverage.v2',
      'active_rule_count', v_active,
      'covered_rule_count', v_covered,
      'gap_count', v_gap_count,
      'coverage_state', v_state,
      'coverage_complete', v_complete,
      'gaps', v_gaps,
      'facts', v_facts
    )::text, 'UTF8'), 'sha256'), 'hex');

  return jsonb_build_object(
    'schema_version', 'doctorcre-v5-a02-rule-enforcement-coverage.v2',
    'observed_at', v_observed_at,
    'active_rule_count', v_active,
    'covered_rule_count', v_covered,
    'gap_count', v_gap_count,
    'coverage_state', v_state,
    'coverage_complete', v_complete,
    'gaps', v_gaps,
    'evidence_digest', v_evidence_digest
  );
end
$fn$;

comment on function ops.v5_a02_rule_enforcement_coverage() is
  'Universal V5-A02 read: every active rule must have a current exact approval '
  'whose EVERY named control is installed and bound, with recorded test evidence '
  'that is not future-dated and whose catalog verified_at still equals the value '
  'the approval captured on the enforcement point. A rule also needs an exact '
  'Joe-authority-connection fallback receipt unless its current exact approval is '
  'machine_enforceable/hard_enforced and every approved control is transactional_schema '
  'or implemented only by hooks/*.py, whose unavailability gate-integrity names at '
  'every session start (0876). Missing '
  'evidence is a named gap, and zero active rules is coverage_state empty, never complete.';

do $v5_a02_0876$
begin
  if position('fallback_required and not fallback_recorded' in
       pg_get_functiondef('ops.v5_a02_rule_enforcement_coverage()'::regprocedure)) = 0 then
    raise exception '0876 FAILED: the fallback rung is not conditioned on fallback_required';
  end if;
  if position('p.all_self_alarming' in
       pg_get_functiondef('ops.v5_a02_rule_enforcement_coverage()'::regprocedure)) = 0 then
    raise exception '0876 FAILED: the fallback exemption is not limited to self-alarming controls';
  end if;
  if not has_function_privilege('carr_reader',
       'ops.v5_a02_rule_enforcement_coverage()'::regprocedure, 'execute')
     or has_function_privilege('carr_writer',
       'ops.v5_a02_rule_enforcement_coverage()'::regprocedure, 'execute') then
    raise exception '0876 FAILED: V5-A02 coverage grants changed';
  end if;
end
$v5_a02_0876$;
