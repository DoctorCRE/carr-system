-- 0721_a02_rule_enforcement_coverage.sql
--
-- V5-A02's live rule-coverage guard.  Coverage is derived from current active
-- rule, approval, installed-control, binding, verification and fallback rows.
-- Nothing in this migration chooses a fallback for an existing rule: that is a
-- decision recorded on Joe's authority connection through the append-only
-- function below.  The receipt proves which authority connection wrote it; it
-- does not by itself prove that a human (rather than an agent holding that
-- connection) chose the fallback.
--
-- Refusals are NAMED: every exception this migration raises carries a stable
-- snake_case message that the MCP verb maps to a ToolError of the same name.

create table ops.rule_enforcement_fallback_receipt (
  id                uuid primary key default gen_random_uuid(),
  idempotency_key   text not null unique check (btrim(idempotency_key) <> ''),
  rule_id           uuid not null references public.rule(id) on delete restrict,
  rule_version      integer not null check (rule_version > 0),
  statement_hash    text not null check (statement_hash ~ '^[0-9a-f]{64}$'),
  fallback_kind     text not null check (fallback_kind in (
    'degraded_read_only',
    'documented_manual_procedure',
    'escalate_to_verified_partner',
    'refuse_closed'
  )),
  procedure_ref     text not null check (btrim(procedure_ref) <> ''),
  reason            text not null check (btrim(reason) <> ''),
  recorded_by       uuid not null references public.actor(id) on delete restrict,
  recorded_by_slug  text not null check (recorded_by_slug = 'joe'),
  created_at        timestamptz not null default now(),
  constraint rule_enforcement_fallback_receipt_exact_rule_version_key
    unique (rule_id, rule_version, statement_hash)
);

comment on table ops.rule_enforcement_fallback_receipt is
  'Append-only fallback selection for one exact rule version and statement, '
  'written only on Joe''s authority connection. V5-A02 coverage accepts only a '
  'current exact receipt; this migration deliberately seeds none.';

-- Append-only is a property of the TABLE (the 0700/0704/0706 idiom): UPDATE
-- and DELETE are refused per row and TRUNCATE per statement, for every role
-- including the owner, independent of whatever a later grant says.
create or replace function ops.refuse_rule_enforcement_fallback_receipt_rewrite()
returns trigger language plpgsql as $fn$
begin
  raise exception using
    message = 'rule_enforcement_fallback_receipts_append_only',
    detail = format('%s on ops.rule_enforcement_fallback_receipt is refused; '
                    'a later rule version needs a new receipt', tg_op);
end
$fn$;

create trigger rule_enforcement_fallback_receipt_append_only
  before update or delete on ops.rule_enforcement_fallback_receipt
  for each row execute function ops.refuse_rule_enforcement_fallback_receipt_rewrite();

create trigger rule_enforcement_fallback_receipt_no_truncate
  before truncate on ops.rule_enforcement_fallback_receipt
  for each statement execute function ops.refuse_rule_enforcement_fallback_receipt_rewrite();

create or replace function ops.record_rule_enforcement_fallback(
  p_rule_id uuid,
  p_fallback_kind text,
  p_procedure_ref text,
  p_idempotency_key text,
  p_reason text
) returns jsonb
language plpgsql security definer set search_path=ops,public,pg_temp
as $fn$
declare
  v_actor_slug text;
  v_actor_id uuid;
  v_rule public.rule%rowtype;
  v_statement_hash text;
  v_existing ops.rule_enforcement_fallback_receipt%rowtype;
  v_receipt ops.rule_enforcement_fallback_receipt%rowtype;
  v_constraint text;
begin
  -- Authority first, before any row is read or any CHECK could fire: a Dell
  -- authority session is refused here by name, not by the table's
  -- recorded_by_slug CHECK.
  v_actor_slug := ops.authority_actor_slug();
  if v_actor_slug is distinct from 'joe' then
    raise exception using
      message = 'rule_enforcement_fallback_requires_joe_authority',
      detail = format('authority session %s may not select a rule fallback', v_actor_slug);
  end if;
  if p_fallback_kind is null or p_fallback_kind not in (
      'degraded_read_only', 'documented_manual_procedure',
      'escalate_to_verified_partner', 'refuse_closed') then
    raise exception using
      message = 'rule_enforcement_fallback_kind_unknown',
      detail = format('unknown fallback kind %s', p_fallback_kind);
  end if;
  if btrim(coalesce(p_procedure_ref, '')) = ''
     or btrim(coalesce(p_reason, '')) = ''
     or btrim(coalesce(p_idempotency_key, '')) = '' then
    raise exception using
      message = 'rule_enforcement_fallback_fields_required',
      detail = 'procedure reference, reason and idempotency key are required';
  end if;

  select * into v_rule from public.rule where id = p_rule_id;
  if not found then
    raise exception using
      message = 'rule_enforcement_fallback_rule_not_found',
      detail = format('rule %s does not exist', p_rule_id);
  end if;
  v_statement_hash := encode(public.digest(v_rule.statement, 'sha256'), 'hex');
  select id into v_actor_id from public.actor where slug = v_actor_slug;
  if v_actor_id is null then
    raise exception using
      message = 'rule_enforcement_fallback_actor_unregistered',
      detail = format('authority actor %s is not registered', v_actor_slug);
  end if;

  select * into v_existing
    from ops.rule_enforcement_fallback_receipt
   where idempotency_key = p_idempotency_key;
  if found then
    if v_existing.rule_id is distinct from p_rule_id
       or v_existing.rule_version is distinct from v_rule.version
       or v_existing.statement_hash is distinct from v_statement_hash
       or v_existing.fallback_kind is distinct from p_fallback_kind
       or v_existing.procedure_ref is distinct from btrim(p_procedure_ref)
       or v_existing.reason is distinct from btrim(p_reason) then
      raise exception using
        message = 'rule_enforcement_fallback_idempotency_conflict',
        detail = 'this idempotency key was already used for a different request';
    end if;
    v_receipt := v_existing;
  else
    begin
      insert into ops.rule_enforcement_fallback_receipt
        (idempotency_key, rule_id, rule_version, statement_hash, fallback_kind,
         procedure_ref, reason, recorded_by, recorded_by_slug)
      values
        (p_idempotency_key, p_rule_id, v_rule.version, v_statement_hash,
         p_fallback_kind, btrim(p_procedure_ref), btrim(p_reason), v_actor_id,
         v_actor_slug)
      returning * into v_receipt;
    exception when unique_violation then
      get stacked diagnostics v_constraint = constraint_name;
      if v_constraint = 'rule_enforcement_fallback_receipt_exact_rule_version_key' then
        raise exception using
          message = 'rule_enforcement_fallback_already_recorded',
          detail = format('rule %s version %s already has a fallback receipt; '
                          'receipts are never rewritten', p_rule_id, v_rule.version);
      end if;
      raise exception using
        message = 'rule_enforcement_fallback_idempotency_conflict',
        detail = 'a concurrent request claimed this idempotency key';
    end;
  end if;

  return jsonb_build_object(
    'schema_version', 'rule-enforcement-fallback-receipt.v1',
    'receipt_id', v_receipt.id,
    'rule_id', v_receipt.rule_id,
    'rule_version', v_receipt.rule_version,
    'fallback_kind', v_receipt.fallback_kind,
    'procedure_ref', v_receipt.procedure_ref,
    'recorded_by', v_receipt.recorded_by_slug,
    'created_at', v_receipt.created_at
  );
end
$fn$;

comment on function ops.record_rule_enforcement_fallback(uuid,text,text,text,text) is
  'Joe-authority-connection-only append of one explicit V5-A02 fallback receipt. '
  'Actor, current rule version and statement hash are server-derived; no '
  'default is inferred. Refusals are named; a second receipt for the same rule '
  'version is rule_enforcement_fallback_already_recorded.';

-- One reason per active rule, first failing leg wins, in this order:
--   active_rule_amended_needs_reapproval       an approval exists, but only for an
--                                              earlier version or statement
--   active_rule_control_unmapped               no current exact approval has any
--                                              installed, currently bound control
--   active_rule_approved_control_not_installed the current exact approval names a
--                                              control that is not installed and
--                                              bound; EVERY approved control counts
--   rule_tests_not_passing                     an approved control has no recorded
--                                              verified test evidence
--   rule_test_evidence_future_dated            an approved control's verified_at is
--                                              after this read
--   rule_test_evidence_changed_since_approval  the catalog control's verified_at is
--                                              not the value the approval captured
--   active_rule_fallback_absent                no exact fallback receipt
-- Zero active rules is coverage_state 'empty', never 'complete'.
--
-- WHAT "THE VALUE THE APPROVAL CAPTURED" IS. ops.rule_approval_receipt does not
-- record verified_at. The approval writer (0228 ops.approve_rule) copies
-- the catalog control's verified_at into ops.rule_enforcement_point in the same
-- transaction that inserts the receipt, and approved_rule_enforcement_point_
-- immutable (0228) freezes that row once any approval exists for the rule. So
-- rule_enforcement_point.verified_at is the recorded binding closest to the
-- approval, and evidence is current only while the catalog still carries that
-- exact value. Evidence older than the approval is normal and is NOT a gap.
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
           c.verified_at is distinct from ep.verified_at as changed_since_approval
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
           bool_and(recorded and not future_dated and not changed_since_approval) as all_passing
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
           ) as fallback_recorded
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
             when not fallback_recorded then 'active_rule_fallback_absent'
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
           'fallback_recorded', fallback_recorded)
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
  'the approval captured on the enforcement point, plus an exact '
  'Joe-authority-connection fallback receipt. Missing evidence is a named gap, '
  'and zero active rules is coverage_state empty, never complete.';

revoke all on table ops.rule_enforcement_fallback_receipt
  from public, carr_reader, carr_writer, carr_jobs, carr_authority;
revoke all on function ops.record_rule_enforcement_fallback(uuid,text,text,text,text)
  from public, carr_reader, carr_writer, carr_jobs;
revoke all on function ops.v5_a02_rule_enforcement_coverage()
  from public, carr_writer, carr_jobs, carr_authority;
grant execute on function ops.record_rule_enforcement_fallback(uuid,text,text,text,text)
  to carr_authority;
grant execute on function ops.v5_a02_rule_enforcement_coverage() to carr_reader;

do $v5_a02_0721$
begin
  if to_regclass('ops.rule_enforcement_fallback_receipt') is null
     or to_regprocedure('ops.record_rule_enforcement_fallback(uuid,text,text,text,text)') is null
     or to_regprocedure('ops.v5_a02_rule_enforcement_coverage()') is null then
    raise exception '0721 FAILED: V5-A02 rule coverage contract is incomplete';
  end if;
  if has_function_privilege('carr_writer',
       'ops.record_rule_enforcement_fallback(uuid,text,text,text,text)'::regprocedure,
       'execute')
     or not has_function_privilege('carr_authority',
       'ops.record_rule_enforcement_fallback(uuid,text,text,text,text)'::regprocedure,
       'execute')
     or not has_function_privilege('carr_reader',
       'ops.v5_a02_rule_enforcement_coverage()'::regprocedure, 'execute') then
    raise exception '0721 FAILED: V5-A02 grants are not closed';
  end if;
  if (select count(*) from pg_trigger
       where tgrelid = 'ops.rule_enforcement_fallback_receipt'::regclass
         and tgname in ('rule_enforcement_fallback_receipt_append_only',
                        'rule_enforcement_fallback_receipt_no_truncate')
         and not tgisinternal) <> 2 then
    raise exception '0721 FAILED: fallback receipt append-only or truncate guard missing';
  end if;
end
$v5_a02_0721$;
