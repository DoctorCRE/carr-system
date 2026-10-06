-- f03-plan-ownership-validator-postgres.sql
--
-- Bounded acceptance/negative fixture for the candidate whole-plan validator and
-- the two canonical-ownership replacements in
-- ops/f03-plan-ownership-validator.candidate.sql.
--
-- NOT RUN.  Nothing in this file has been executed, and no result below is
-- claimed.  It is reviewable source only.
--
-- ===========================================================================
-- SAFETY PROPERTIES
-- ===========================================================================
--
--   * The whole script is one transaction that ends in ROLLBACK.  Every insert
--     it makes -- slice plans, envelopes, job attempts, receipts, temp tables,
--     temp functions -- is undone.  It never commits.
--   * It creates no production record, no role, no grant, no Work Request, no
--     job definition and no migration.  It writes only rows that its own
--     ROLLBACK removes.
--   * It never applies either candidate.  Applying them to an isolated scratch
--     database is a separate, explicit operator step (below).
--   * Run it ONLY against an isolated scratch database.  Do not run it against
--     production, and do not run it against any database whose Engineering
--     ledgers carry real evidence.
--
-- ===========================================================================
-- PREREQUISITES
-- ===========================================================================
--
-- P0. An isolated scratch PostgreSQL database with the repository migrations
--     applied through migrations/0450_canonical_ownership_lease_kernel.sql, with
--     public.digest available (pgcrypto), and with BOTH candidates applied on
--     top of them:
--
--       ops/f03-receipt-validator.candidate.sql
--       ops/f03-plan-ownership-validator.candidate.sql
--
--     THE ORDER BETWEEN THE TWO DOES NOT MATTER, AND NEITHER FAILS AT CREATE.
--     An earlier version of this header said the wrong order "fails at CREATE
--     with function ops.engineering_receipt_design_contract_refusal(jsonb) does
--     not exist".  That claim is withdrawn: check_function_bodies only
--     SYNTAX-checks a plpgsql body, so ops.engineering_slice_plan_refusal and
--     both replaced plpgsql seams CREATE cleanly with the other candidate
--     absent, and the two LANGUAGE sql helpers the plan candidate adds call
--     nothing from the receipt candidate.  Each candidate installs successfully
--     alone.  The dependency -- which is mutual, since the receipt seam's v2
--     branch calls ops.engineering_slice_plan_refusal -- resolves at EXECUTION:
--     a v2 registration, or a v2 receipt append, raises "function ... does not
--     exist" and is refused until both are present.  Still fail-closed; just not
--     at CREATE, and not order-dependent.
--
--     WHAT EACH PART NEEDS, exactly:
--
--       Part A  needs BOTH candidates, even though it only calls plan-candidate
--               functions.  ops.engineering_slice_plan_refusal calls
--               ops.engineering_receipt_design_contract_refusal(jsonb) and
--               ops.engineering_receipt_design_self_label_free(jsonb), which the
--               RECEIPT candidate installs, for every v2 slice.  With the
--               receipt candidate absent, the plan-candidate functions exist and
--               the v1 cases still return, but every v2 case raises "function
--               ... does not exist".  The pre-flight below names both sets
--               before any case runs, so a half-installed database says so
--               instead of failing case by case.
--       Part E  needs exactly what Part A needs -- both candidates, no ledger,
--               no admission source -- because it calls
--               ops.engineering_slice_plan_refusal and nothing else.  It is
--               generated from the shared corpus rather than written here; see
--               the PART E banner above the \ir near the end of this file.
--       Parts B and C  need both candidates and a scratch admission source (P2).
--       Part D  needs both candidates and the full execution lane (P3): it
--               appends a receipt through the REPLACED receipt seam, which for
--               a v2 plan calls ops.engineering_slice_plan_refusal itself.
--
-- P1. Part A (the pure predicate cases) needs nothing else.  It reads no ledger
--     table and writes no row outside its own temp objects.
--
-- P1b. Run the script as a role that can create temp objects and, for Parts B, C
--     and D, insert directly into ops.engineering_slice_plan,
--     ops.engineering_execution_envelope and ops.job_attempt and execute the
--     revoked ownership functions -- normally the scratch database owner.  The
--     fixture deliberately does NOT grant anything to obtain those rights, and
--     carr_writer is expected to lack them.
--
-- P2. Parts B and C need one scratch ADMISSION SOURCE: an ops.work_request with
--     state='ready' plus a matching ops.sourced_work_request_plan and
--     ops.sourced_work_request_plan_acceptance_receipt, so that
--     ops.engineering_admission_source(<ref>) returns non-null.  Its accepted
--     plan must have NO ops.engineering_slice_plan yet, because
--     ops.engineering_slice_plan carries UNIQUE (accepted_plan_id)
--     (migrations/0310_engineering_execution_fabric.sql:22) and every case here
--     registers or inserts one and rolls it back.  Set lane_work_request_ref
--     below; leave the placeholder and Parts B/C skip loudly.
--
-- P3. Part D additionally needs the full scratch execution lane described in the
--     PREREQUISITES header of mcp-server/test/f03-receipt-validator-postgres.sql
--     -- the same lane, with the same requirements (a codex automation actor, a
--     capability_agent_session on a WHOLE-SECOND lease, and an ops.job in state
--     'running' with a lease token and a payload whose plan_digest is the digest
--     Part D registers its plans under).  Set the three lane_* values below;
--     leave the all-zero placeholders and Part D skips loudly.
--
--     ONE MORE REQUIREMENT FOR D02.  Part D appends a receipt, and for an
--     engineering-slice-plan.v2 plan the replaced receipt seam validates the
--     WHOLE stored plan, which includes "the plan_digest binds the canonical
--     content".  ops.engineering_envelope_currentness:167 and :179 force the
--     stored plan's digest to be the lane job payload's, so D02 can only run
--     when the lane payload plan_digest IS the canonical digest of the exact v2
--     plan body this fixture builds.  Part D does not mutate the lane to arrange
--     that: it computes the required digest, prints it, and SKIPS D02 loudly.
--     D01 (v1) is unaffected -- a v1 receipt never reaches the whole-plan check.
--
-- P4. Column lists.  Part C and Part D insert into ops.engineering_slice_plan
--     using the EXACT seven-column list that ops.engineering_register_slice_plan
--     itself inserts (0310:205-210), so the list cannot be stale while the
--     production register path works.  Part D inserts into
--     ops.engineering_execution_envelope using the column list at 0310:30; the
--     live set at db/schema.sql:29142-29167 adds only the two NULLABLE 0311
--     columns supersedes_envelope_id and supersession_reason, whose
--     travels-together CHECK is satisfied with both NULL.  Part D inserts into
--     ops.job_attempt using the exact five columns ops.engineering_claim_slice
--     inserts (0310:383).
--
-- ===========================================================================
-- WHAT A PASS DOES AND DOES NOT MEAN
-- ===========================================================================
--
--   * A pass means the database refuses the plan shapes the JS and Python
--     validators refuse, accepts the ones they accept, and that both
--     canonical-ownership functions now read an engineering-slice-plan.v2 slice
--     instead of refusing it.  For Parts A..D that first clause is a claim about
--     shapes chosen HERE; PART E is where it becomes a claim about the SAME
--     inputs, because every case in it is generated from the shared corpus the
--     JS and Python validators are themselves tested against.
--   * A pass says nothing about plans that are ALREADY STORED.  This seam binds
--     future registrations only, and neither candidate re-validates a stored
--     row.  The boundary that refuses an already-stored malformed v2 plan is the
--     receipt append, which calls the same ops.engineering_slice_plan_refusal;
--     that is covered in mcp-server/test/f03-receipt-validator-postgres.sql
--     Part B, cases B08..B10.
--   * PARTS B, C AND D do NOT independently verify canonicalization parity with
--     the JS and Python producers: they compute plan_digest with the same
--     ops.guidance_import_canonical_json expression the validator checks against,
--     so a divergence between the database and those producers is invisible in
--     them.  See the digest note in the candidate for the one narrow case
--     (numeric spelling) the receipt path does not already prove.  PART E is the
--     exception and the reason this sentence now names B, C and D rather than the
--     whole file: its plan_digest values are literals the JavaScript producer
--     computed over the corpus plans, so the database has to reach the same
--     digest independently or a positive case refuses.
--
-- COVERAGE, STATED RATHER THAN IMPLIED.  Part A's case table names every refusal
-- token ops.engineering_slice_plan_refusal can return, including the per-slice
-- shape tokens (slice_ref, text_fields, enums, identifier_arrays,
-- baseline_evidence_refs, planned_checks, the non-object slice) and every plan
-- binding token (work_request.id / .canonical_record_digest / .state_version and
-- all four accepted_plan_revision tokens).  What it does NOT do is enumerate
-- every input that can produce a given token: one case per token is a
-- discrimination test, not an exhaustive one.  The design-contract tokens the
-- per-slice loop forwards from ops.engineering_receipt_design_contract_refusal
-- are covered where that function is stated, in
-- mcp-server/test/f03-receipt-validator-postgres.sql Part A; only two of them
-- (contract_version, routing shape) are re-checked here, to pin that the token
-- is forwarded with the slice prefix intact.

\set ON_ERROR_STOP on

-- Scratch lane parameters.  The placeholders are deliberate: leave them and the
-- ledger-touching parts skip loudly instead of touching anything.
\set lane_work_request_ref 'REPLACE-WITH-SCRATCH-WORK-REQUEST-REF'
\set lane_job_id '00000000-0000-0000-0000-000000000000'
\set lane_lease_token '00000000-0000-0000-0000-000000000000'
\set lane_agent_session_id '00000000-0000-0000-0000-000000000000'

begin;

-- ===========================================================================
-- Shared builders.  These are pg_temp functions, so they disappear with this
-- transaction's ROLLBACK and with the session; they create nothing durable.
-- ===========================================================================

-- One fully valid engineering-slice-plan.v2 slice.
--
-- risk_class is R4 in every generated slice on purpose: R4 is outside the SHORT
-- band, so the frozen Q035.D1 predicate classifies every slice here FULL
-- regardless of its dependency and resource counts.  That keeps the depth
-- material constant while the plan-wide shape under test varies, and it is why
-- deployment.confirmation_required is true (the explicit confirmation gate above
-- R1 is retained, not waived).
create function pg_temp.f03p_slice(
  p_slice_ref text, p_name text, p_ordinal integer,
  p_dependency_refs jsonb default '[]'::jsonb,
  p_resource_refs jsonb default null,
  p_posture text default 'parallel_safe',
  p_seam_mode text default 'reuse',
  p_target_seam_ref text default null,
  p_replaced_seam_refs jsonb default '[]'::jsonb,
  p_with_contract boolean default true
) returns jsonb language plpgsql as $slice$
declare v_slice jsonb; v_contract jsonb;
        v_resources jsonb := coalesce(p_resource_refs, jsonb_build_array('resource:' || p_name));
        v_target text := coalesce(p_target_seam_ref, 'seam:' || p_name);
begin
  v_slice := jsonb_build_object(
    'slice_ref', p_slice_ref,
    'ordinal', p_ordinal,
    'objective', 'exercise the whole-plan validator for ' || p_name,
    'definition_of_done', 'the validator accepts or refuses exactly as the case expects',
    'scope_boundary', 'fixture only',
    'dependency_refs', p_dependency_refs,
    'declared_resource_refs', v_resources,
    'declared_component_refs', jsonb_build_array('component:' || p_name),
    'declared_plan_step_refs', jsonb_build_array('step:' || p_name),
    'forbidden_change_refs', '[]'::jsonb,
    'baseline_evidence_refs', '[]'::jsonb,
    'planned_checks', jsonb_build_array(jsonb_build_object(
      'check_ref', 'check:' || p_name,
      'failure_condition', 'the validator does not behave as the case expects',
      'evidence_requirement', 'metadata_only_sufficient')),
    'concurrency_posture', p_posture,
    'manual_qa_required', false,
    'risk_class', 'R4',
    'release_requirement', 'not_required');
  if not p_with_contract then return v_slice; end if;

  v_contract := jsonb_build_object(
    'contract_version', 'engineering-design-contract.v1',
    'rationale', 'a deterministic predicate replaces a caller-side assertion',
    'dependency_rationale', 'the declared dependency set is exactly what this slice waits on',
    'code_model_decision', jsonb_build_object(
      'rationale', 'plan validation is deterministic; no judgment step is required',
      'selection_basis', jsonb_build_array('typed_uncertainty'),
      'model_judgment_steps', '[]'::jsonb),
    'routing', jsonb_build_object(
      'executor_class', 'deterministic_code',
      'adapter_ref', 'adapter:codex-desktop',
      'fresh_session_required', true),
    'authority', jsonb_build_object(
      'capability_profile', 'capability:engineering-repository-write',
      'read_only', false,
      'environment', 'rehearsal'),
    'isolation', jsonb_build_object(
      'worktree_required', true, 'branch_required', true,
      'shared_resource_refs', '[]'::jsonb),
    'tests', jsonb_build_object(
      'planned_check_refs', jsonb_build_array('check:' || p_name),
      'verification_lanes', jsonb_build_array('contract')),
    'review', jsonb_build_object(
      'independent_review_required', true, 'reviewer_class', 'independent_agent'),
    'failure', jsonb_build_object('failure_modes', jsonb_build_array(jsonb_build_object(
      'failure_ref', 'failure:' || p_name,
      'detection', 'the registration seam refuses the plan',
      'compensation', 'the transaction rolls back and nothing is registered'))),
    'evidence', jsonb_build_object(
      'redaction_class', 'metadata_only', 'retention', 'ephemeral',
      'evidence_refs', jsonb_build_array(jsonb_build_object(
        'ref', 'evidence:' || p_name, 'redaction_class', 'metadata_only',
        'content_digest', 'sha256:' || repeat('a', 64)))),
    'deployment', jsonb_build_object(
      'release_requirement', 'not_required', 'rollback_ref', null::jsonb,
      'confirmation_required', true),
    'completion', jsonb_build_object(
      'completion_predicate', 'an independent reviewer confirms the refusal set',
      'verified_by', 'independent_review'),
    'seam_decision', jsonb_build_object(
      'mode', p_seam_mode, 'target_seam_ref', v_target,
      'measurement', jsonb_build_object('basis', 'coverage', 'note', 'one covered validator branch'),
      'new_module_justification',
        case when p_seam_mode = 'new_module' then to_jsonb('authority'::text) else null::jsonb end,
      'replaced_seam_refs', p_replaced_seam_refs,
      'residual_authority_refs', '[]'::jsonb),
    'full_design_refs', jsonb_build_object(
      'design_interview_ref', 'design:' || p_name,
      'authority_envelope_ref', 'authority:' || p_name,
      'failure_model_ref', 'failure-model:' || p_name,
      'oracle_ref', 'oracle:' || p_name,
      'fixture_refs', jsonb_build_array('fixture:f03-plan-ownership-validator-postgres')),
    'short_template', null::jsonb);

  return jsonb_set(v_slice, '{design_contract}', v_contract, true);
end $slice$;

-- Seal a plan: build the body, then append the plan_digest the validator will
-- recompute.  This uses the SAME ops.guidance_import_canonical_json expression
-- the validator uses, which is why a pass here does not prove canonicalization
-- parity with the JS and Python producers.
create function pg_temp.f03p_seal(p_body jsonb) returns jsonb language sql as $$
  select p_body || jsonb_build_object('plan_digest',
    'sha256:' || encode(public.digest(ops.guidance_import_canonical_json(p_body), 'sha256'), 'hex'));
$$;

-- A sealed plan bound to placeholder work-request/accepted-plan values.  Part A
-- never compares those against a real admission source, so placeholders are
-- exact for the pure predicate; Parts B, C and D build the same shape from
-- ops.engineering_admission_source instead.
create function pg_temp.f03p_plan(p_schema_version text, p_slices jsonb)
returns jsonb language sql as $$
  select pg_temp.f03p_seal(jsonb_build_object(
    'schema_version', p_schema_version,
    'work_request', jsonb_build_object(
      'id', 'wr:00000000-0000-4000-8000-000000000001',
      'state_version', 1,
      'canonical_record_digest', 'sha256:' || repeat('b', 64)),
    'accepted_plan_revision', jsonb_build_object(
      'id', 'plan:f03-plan-ownership',
      'revision', 1,
      'digest', 'sha256:' || repeat('c', 64)),
    'slices', p_slices));
$$;

-- ===========================================================================
-- PART A -- the pure whole-plan validator and the two new predicates.
--
-- These call ops.engineering_slice_plan_refusal and its helpers directly with
-- constructed JSON.  They read no ledger table, take no lock and touch no
-- ledger, so this is the part a reviewer can run against a scratch database
-- with nothing in it but the migrations and the two candidates.
-- ===========================================================================

-- PRE-FLIGHT.  Which candidate is missing, said once and plainly, before any
-- case runs.  Part A calls only this candidate's functions, but
-- ops.engineering_slice_plan_refusal calls the receipt candidate's design
-- predicates for every engineering-slice-plan.v2 slice, so both sets must be
-- present for the v2 cases to mean anything.  Neither file fails at CREATE
-- without the other -- see PREREQUISITE P0 -- so this is where a half-installed
-- database is detected, and it reads pg_proc only.
do $$
declare missing_plan text; missing_receipt text;
begin
  select string_agg(sig, ', ' order by sig) into missing_plan
    from unnest(array[
      'ops.engineering_slice_plan_slice_fields(text)',
      'ops.engineering_plan_identifier_list(jsonb)',
      'ops.engineering_slice_plan_refusal(jsonb)']) as sigs(sig)
   where to_regprocedure(sig) is null;
  select string_agg(sig, ', ' order by sig) into missing_receipt
    from unnest(array[
      'ops.engineering_receipt_design_contract_refusal(jsonb)',
      'ops.engineering_receipt_design_self_label_free(jsonb)']) as sigs(sig)
   where to_regprocedure(sig) is null;
  if missing_plan is not null then
    raise exception
      'PART A PRE-FLIGHT: ops/f03-plan-ownership-validator.candidate.sql is not applied; absent: %',
      missing_plan;
  end if;
  if missing_receipt is not null then
    raise exception
      'PART A PRE-FLIGHT: ops/f03-receipt-validator.candidate.sql is not applied, so every v2 case would raise from inside ops.engineering_slice_plan_refusal; absent: %',
      missing_receipt;
  end if;
  raise notice 'PART A PRE-FLIGHT: both candidates are applied';
end $$;

create temporary table f03p_case(
  name text primary key,
  plan jsonb not null,
  expected text
) on commit drop;

-- expected NULL means "the plan is accepted".  Every other row names the exact
-- refusal token the candidate must return.
insert into f03p_case(name, plan, expected)
select * from (values
  -- ---- acceptance --------------------------------------------------------
  ('A01-accept-single-slice-v2',
   pg_temp.f03p_plan('engineering-slice-plan.v2',
     jsonb_build_array(pg_temp.f03p_slice('slice:a','a',1))),
   null::text),
  ('A02-accept-ordered-multi-slice-v2',
   pg_temp.f03p_plan('engineering-slice-plan.v2', jsonb_build_array(
     pg_temp.f03p_slice('slice:a','a',1),
     pg_temp.f03p_slice('slice:b','b',2,jsonb_build_array('slice:a')))),
   null),
  -- Two parallel_safe slices may own one declared resource when a dependency
  -- edge orders them: the contradiction the rule refuses is gone.
  ('A03-accept-shared-resource-when-ordered-by-a-dependency',
   pg_temp.f03p_plan('engineering-slice-plan.v2', jsonb_build_array(
     pg_temp.f03p_slice('slice:a','a',1,'[]'::jsonb,jsonb_build_array('resource:shared')),
     pg_temp.f03p_slice('slice:b','b',2,jsonb_build_array('slice:a'),
       jsonb_build_array('resource:shared')))),
   null),
  -- Contention declared with a non-parallel posture is exactly how the rule is
  -- meant to be satisfied, and is untouched.
  ('A04-accept-shared-resource-under-a-serial-posture',
   pg_temp.f03p_plan('engineering-slice-plan.v2', jsonb_build_array(
     pg_temp.f03p_slice('slice:a','a',1,'[]'::jsonb,jsonb_build_array('resource:shared')),
     pg_temp.f03p_slice('slice:b','b',2,'[]'::jsonb,jsonb_build_array('resource:shared'),
       'serial_after_dependencies'))),
   null),

  -- ---- the v1 boundary ----------------------------------------------------
  -- A v1 plan is accepted after its top-level shape and version only.  These two
  -- cases pin the deliberate divergence documented in the candidate header: v1
  -- keeps its exact previous behavior and is NOT retroactively upgraded.
  ('A05-accept-v1-plan-without-a-design-contract',
   pg_temp.f03p_plan('engineering-slice-plan.v1',
     jsonb_build_array(pg_temp.f03p_slice('slice:a','a',1,'[]'::jsonb,null,
       'parallel_safe','reuse',null,'[]'::jsonb,false))),
   null),
  ('A06-accept-v1-plan-with-duplicate-ordinals-and-a-cycle',
   pg_temp.f03p_plan('engineering-slice-plan.v1', jsonb_build_array(
     pg_temp.f03p_slice('slice:a','a',1,jsonb_build_array('slice:b'),null,
       'parallel_safe','reuse',null,'[]'::jsonb,false),
     pg_temp.f03p_slice('slice:b','b',1,jsonb_build_array('slice:a'),null,
       'parallel_safe','reuse',null,'[]'::jsonb,false))),
   null),

  -- ---- unknown types and versions -----------------------------------------
  ('A07-refuse-unknown-plan-schema-version',
   pg_temp.f03p_plan('engineering-slice-plan.v3',
     jsonb_build_array(pg_temp.f03p_slice('slice:a','a',1))),
   'plan.schema_version'),
  ('A08-refuse-missing-plan-schema-version',
   (pg_temp.f03p_plan('engineering-slice-plan.v2',
      jsonb_build_array(pg_temp.f03p_slice('slice:a','a',1))) - 'schema_version'),
   'plan.shape'),
  ('A09-refuse-unknown-top-level-field',
   (pg_temp.f03p_plan('engineering-slice-plan.v2',
      jsonb_build_array(pg_temp.f03p_slice('slice:a','a',1)))
    || '{"extra_field":"x"}'::jsonb),
   'plan.shape'),
  ('A10-refuse-array-plan', '[]'::jsonb, 'plan'),
  ('A11-refuse-json-null-plan', 'null'::jsonb, 'plan'),
  ('A12-refuse-unknown-design-contract-version',
   pg_temp.f03p_plan('engineering-slice-plan.v2', jsonb_build_array(jsonb_set(
     pg_temp.f03p_slice('slice:a','a',1),
     '{design_contract,contract_version}', '"engineering-design-contract.v2"'))),
   'slices[slice:a].design_contract.contract_version'),

  -- ---- malformed nesting ---------------------------------------------------
  ('A13-refuse-malformed-work-request-binding',
   pg_temp.f03p_seal(
     (pg_temp.f03p_plan('engineering-slice-plan.v2',
        jsonb_build_array(pg_temp.f03p_slice('slice:a','a',1))) - 'plan_digest')
     || jsonb_build_object('work_request',
          '{"id":"wr:00000000-0000-4000-8000-000000000001"}'::jsonb)),
   'plan.work_request'),
  ('A14-refuse-non-integral-state-version',
   pg_temp.f03p_seal(
     (pg_temp.f03p_plan('engineering-slice-plan.v2',
        jsonb_build_array(pg_temp.f03p_slice('slice:a','a',1))) - 'plan_digest')
     || jsonb_build_object('work_request', jsonb_build_object(
          'id','wr:00000000-0000-4000-8000-000000000001',
          'state_version','1',
          'canonical_record_digest','sha256:' || repeat('b',64)))),
   'plan.work_request.state_version'),
  ('A15-refuse-v2-slice-without-a-design-contract',
   pg_temp.f03p_plan('engineering-slice-plan.v2',
     jsonb_build_array(pg_temp.f03p_slice('slice:a','a',1,'[]'::jsonb,null,
       'parallel_safe','reuse',null,'[]'::jsonb,false))),
   'slices[slice:a].shape'),
  ('A16-refuse-nested-contract-shape',
   pg_temp.f03p_plan('engineering-slice-plan.v2', jsonb_build_array(jsonb_set(
     pg_temp.f03p_slice('slice:a','a',1),
     '{design_contract,routing,extra}', '"x"', true))),
   'slices[slice:a].design_contract.routing'),
  ('A17-refuse-slice-self-label',
   pg_temp.f03p_plan('engineering-slice-plan.v2', jsonb_build_array(jsonb_set(
     pg_temp.f03p_slice('slice:a','a',1), '{classifier_override}', '"short"', true))),
   'slices[slice:a].design_depth_self_label'),
  ('A18-refuse-empty-slices',
   pg_temp.f03p_plan('engineering-slice-plan.v2', '[]'::jsonb),
   'plan.slices'),
  ('A19-refuse-unknown-dependency-ref',
   pg_temp.f03p_plan('engineering-slice-plan.v2', jsonb_build_array(
     pg_temp.f03p_slice('slice:a','a',1,jsonb_build_array('slice:never-declared')))),
   'slices[slice:a].dependency_refs_unknown'),

  -- ---- cycles --------------------------------------------------------------
  ('A20-refuse-two-slice-dependency-cycle',
   pg_temp.f03p_plan('engineering-slice-plan.v2', jsonb_build_array(
     pg_temp.f03p_slice('slice:a','a',1,jsonb_build_array('slice:b')),
     pg_temp.f03p_slice('slice:b','b',2,jsonb_build_array('slice:a')))),
   'plan.dependency_cycle[slice:a,slice:b]'),
  ('A21-refuse-self-dependency',
   pg_temp.f03p_plan('engineering-slice-plan.v2', jsonb_build_array(
     pg_temp.f03p_slice('slice:a','a',1,jsonb_build_array('slice:a')))),
   'plan.dependency_cycle[slice:a]'),

  -- ---- ordinals and slice refs --------------------------------------------
  ('A22-refuse-duplicate-ordinal',
   pg_temp.f03p_plan('engineering-slice-plan.v2', jsonb_build_array(
     pg_temp.f03p_slice('slice:a','a',1),
     pg_temp.f03p_slice('slice:b','b',1))),
   'plan.duplicate_ordinal[1]'),
  ('A23-refuse-duplicate-slice-ref',
   pg_temp.f03p_plan('engineering-slice-plan.v2', jsonb_build_array(
     pg_temp.f03p_slice('slice:a','a',1),
     pg_temp.f03p_slice('slice:a','a2',2))),
   'plan.duplicate_slice_ref[slice:a]'),
  ('A24-refuse-zero-ordinal',
   pg_temp.f03p_plan('engineering-slice-plan.v2', jsonb_build_array(
     pg_temp.f03p_slice('slice:a','a',0))),
   'slices[slice:a].ordinal'),

  -- ---- seam authority ------------------------------------------------------
  ('A25-refuse-two-slices-owning-one-seam',
   pg_temp.f03p_plan('engineering-slice-plan.v2', jsonb_build_array(
     pg_temp.f03p_slice('slice:a','a',1,'[]'::jsonb,null,'parallel_safe',
       'new_module','seam:shared'),
     pg_temp.f03p_slice('slice:b','b',2,'[]'::jsonb,null,'parallel_safe',
       'new_module','seam:shared'))),
   'plan.seam_duplicate_authority[seam:shared]'),
  ('A26-refuse-one-seam-retired-twice',
   pg_temp.f03p_plan('engineering-slice-plan.v2', jsonb_build_array(
     pg_temp.f03p_slice('slice:a','a',1,'[]'::jsonb,null,'parallel_safe',
       'replace','seam:a-target',jsonb_build_array('seam:legacy')),
     pg_temp.f03p_slice('slice:b','b',2,'[]'::jsonb,null,'parallel_safe',
       'replace','seam:b-target',jsonb_build_array('seam:legacy')))),
   'plan.seam_retired_twice[seam:legacy]'),
  ('A27-refuse-half-replacement',
   pg_temp.f03p_plan('engineering-slice-plan.v2', jsonb_build_array(
     pg_temp.f03p_slice('slice:a','a',1,'[]'::jsonb,null,'parallel_safe',
       'replace','seam:a-target',jsonb_build_array('seam:legacy')),
     pg_temp.f03p_slice('slice:b','b',2,'[]'::jsonb,null,'parallel_safe',
       'reuse','seam:legacy'))),
   'plan.seam_half_replacement[seam:legacy]'),

  -- ---- parallel-safe resource isolation ------------------------------------
  ('A28-refuse-parallel-resource-collision',
   pg_temp.f03p_plan('engineering-slice-plan.v2', jsonb_build_array(
     pg_temp.f03p_slice('slice:a','a',1,'[]'::jsonb,jsonb_build_array('resource:shared')),
     pg_temp.f03p_slice('slice:b','b',2,'[]'::jsonb,jsonb_build_array('resource:shared')))),
   'plan.parallel_resource_conflict[resource:shared:slice:a,slice:b]'),

  -- ---- the digest must bind the content ------------------------------------
  ('A29-refuse-digest-that-does-not-bind-content',
   jsonb_set(pg_temp.f03p_plan('engineering-slice-plan.v2',
     jsonb_build_array(pg_temp.f03p_slice('slice:a','a',1))),
     '{plan_digest}', to_jsonb('sha256:' || repeat('d', 64))),
   'plan.plan_digest_does_not_bind_content'),
  ('A30-refuse-content-changed-after-sealing',
   jsonb_set(pg_temp.f03p_plan('engineering-slice-plan.v2',
     jsonb_build_array(pg_temp.f03p_slice('slice:a','a',1))),
     '{slices,0,objective}', '"a different objective than the one that was sealed"'),
   'plan.plan_digest_does_not_bind_content'),
  ('A31-refuse-malformed-plan-digest',
   jsonb_set(pg_temp.f03p_plan('engineering-slice-plan.v2',
     jsonb_build_array(pg_temp.f03p_slice('slice:a','a',1))),
     '{plan_digest}', '"not-a-digest"'),
   'plan.plan_digest'),

  -- ---- declared-ref duplicate parity ---------------------------------------
  -- Both source validators accept a slice that repeats a declared ref: requirePlan's
  -- declared-ref loop and _ids apply a per-element identifier check and no
  -- uniqueness rule, and requireParallelResourceIsolation de-duplicates with a
  -- Set at the point of use because one slice repeating its own resource is not
  -- contention with anyone.  This file must accept them too, or registration
  -- refuses plans the two validators that produce plans accept.  A41 is refused
  -- by a design-contract subset test that requires BOTH sides to be unique and
  -- accepted by the split one; A42 does the same with a non-empty shared subset,
  -- which needs a non-parallel posture to be declarable at all.
  ('A41-accept-duplicate-declared-resource-refs',
   pg_temp.f03p_plan('engineering-slice-plan.v2', jsonb_build_array(
     pg_temp.f03p_slice('slice:a','a',1,'[]'::jsonb,
       jsonb_build_array('resource:shared','resource:shared')))),
   null),
  ('A42-accept-duplicate-declared-resources-with-a-non-empty-shared-subset',
   pg_temp.f03p_plan('engineering-slice-plan.v2', jsonb_build_array(jsonb_set(
     pg_temp.f03p_slice('slice:a','a',1,'[]'::jsonb,
       jsonb_build_array('resource:shared','resource:shared'),'serial_after_dependencies'),
     '{design_contract,isolation,shared_resource_refs}', '["resource:shared"]'))),
   null),

  -- ---- the per-slice shape checks, one case per refusal token ---------------
  -- A15/A16/A17 cover the closed field set, a nested contract shape and the
  -- self-label refusal.  These cover the typed per-slice tokens between them,
  -- so every branch of the per-slice loop is discriminated by a case rather than
  -- assumed to be exercised by the contract cases above.
  ('A43-refuse-malformed-slice-ref',
   pg_temp.f03p_plan('engineering-slice-plan.v2', jsonb_build_array(
     pg_temp.f03p_slice('ab','a',1))),
   'slices[ab].slice_ref'),
  ('A44-refuse-empty-slice-text-field',
   pg_temp.f03p_plan('engineering-slice-plan.v2', jsonb_build_array(jsonb_set(
     pg_temp.f03p_slice('slice:a','a',1), '{objective}', '"   "'))),
   'slices[slice:a].text_fields'),
  ('A45-refuse-unknown-slice-enum-value',
   pg_temp.f03p_plan('engineering-slice-plan.v2', jsonb_build_array(jsonb_set(
     pg_temp.f03p_slice('slice:a','a',1), '{risk_class}', '"R9"'))),
   'slices[slice:a].enums'),
  ('A46-refuse-malformed-declared-ref-array',
   pg_temp.f03p_plan('engineering-slice-plan.v2', jsonb_build_array(jsonb_set(
     pg_temp.f03p_slice('slice:a','a',1), '{declared_component_refs}',
     '["not an identifier"]'))),
   'slices[slice:a].identifier_arrays'),
  ('A47-refuse-untyped-baseline-evidence-ref',
   pg_temp.f03p_plan('engineering-slice-plan.v2', jsonb_build_array(jsonb_set(
     pg_temp.f03p_slice('slice:a','a',1), '{baseline_evidence_refs}',
     '["evidence:a"]'))),
   'slices[slice:a].baseline_evidence_refs'),
  ('A48-refuse-empty-planned-checks',
   pg_temp.f03p_plan('engineering-slice-plan.v2', jsonb_build_array(jsonb_set(
     pg_temp.f03p_slice('slice:a','a',1), '{planned_checks}', '[]'))),
   'slices[slice:a].planned_checks'),
  ('A49-refuse-non-object-slice',
   pg_temp.f03p_plan('engineering-slice-plan.v2', '[1]'::jsonb),
   'slices[<unnamed>].shape'),

  -- ---- the plan bindings, one case per refusal token ------------------------
  -- A13 covers the work_request shape and A14 its state_version.  These cover
  -- the remaining binding tokens.  Each is resealed after the edit, so the
  -- digest still binds the content and the binding refusal is what is measured.
  ('A50-refuse-malformed-work-request-id',
   pg_temp.f03p_seal(
     (pg_temp.f03p_plan('engineering-slice-plan.v2',
        jsonb_build_array(pg_temp.f03p_slice('slice:a','a',1))) - 'plan_digest')
     || jsonb_build_object('work_request', jsonb_build_object(
          'id','wr','state_version',1,
          'canonical_record_digest','sha256:' || repeat('b',64)))),
   'plan.work_request.id'),
  ('A51-refuse-malformed-work-request-digest',
   pg_temp.f03p_seal(
     (pg_temp.f03p_plan('engineering-slice-plan.v2',
        jsonb_build_array(pg_temp.f03p_slice('slice:a','a',1))) - 'plan_digest')
     || jsonb_build_object('work_request', jsonb_build_object(
          'id','wr:00000000-0000-4000-8000-000000000001','state_version',1,
          'canonical_record_digest','not-a-digest'))),
   'plan.work_request.canonical_record_digest'),
  ('A52-refuse-malformed-accepted-plan-revision-shape',
   pg_temp.f03p_seal(
     (pg_temp.f03p_plan('engineering-slice-plan.v2',
        jsonb_build_array(pg_temp.f03p_slice('slice:a','a',1))) - 'plan_digest')
     || jsonb_build_object('accepted_plan_revision',
          '{"id":"plan:f03-plan-ownership","revision":1}'::jsonb)),
   'plan.accepted_plan_revision'),
  ('A53-refuse-malformed-accepted-plan-revision-id',
   pg_temp.f03p_seal(
     (pg_temp.f03p_plan('engineering-slice-plan.v2',
        jsonb_build_array(pg_temp.f03p_slice('slice:a','a',1))) - 'plan_digest')
     || jsonb_build_object('accepted_plan_revision', jsonb_build_object(
          'id','p','revision',1,'digest','sha256:' || repeat('c',64)))),
   'plan.accepted_plan_revision.id'),
  ('A54-refuse-malformed-accepted-plan-revision-digest',
   pg_temp.f03p_seal(
     (pg_temp.f03p_plan('engineering-slice-plan.v2',
        jsonb_build_array(pg_temp.f03p_slice('slice:a','a',1))) - 'plan_digest')
     || jsonb_build_object('accepted_plan_revision', jsonb_build_object(
          'id','plan:f03-plan-ownership','revision',1,'digest','not-a-digest'))),
   'plan.accepted_plan_revision.digest'),
  ('A55-refuse-non-integral-accepted-plan-revision',
   pg_temp.f03p_seal(
     (pg_temp.f03p_plan('engineering-slice-plan.v2',
        jsonb_build_array(pg_temp.f03p_slice('slice:a','a',1))) - 'plan_digest')
     || jsonb_build_object('accepted_plan_revision', jsonb_build_object(
          'id','plan:f03-plan-ownership','revision','1',
          'digest','sha256:' || repeat('c',64)))),
   'plan.accepted_plan_revision.revision')
) as cases(name, plan, expected);

do $$
declare row_case record; actual text; failures integer := 0; total integer := 0;
begin
  for row_case in select name, plan, expected from f03p_case order by name loop
    total := total + 1;
    actual := ops.engineering_slice_plan_refusal(row_case.plan);
    if actual is distinct from row_case.expected then
      failures := failures + 1;
      raise warning 'PART A FAIL % : expected %, got %',
        row_case.name, coalesce(row_case.expected, '<accept>'), coalesce(actual, '<accept>');
    end if;
  end loop;
  if failures > 0 then
    raise exception 'PART A: % of % whole-plan cases did not match', failures, total;
  end if;
  raise notice 'PART A: % whole-plan cases matched', total;
end $$;

-- A32-A35.  The version-dependent field set, pinned against the literal arrays
-- the two 0450 functions hard-code today.  If this function and those literals
-- ever diverge, this fails rather than silently accepting a wrong slice shape.
do $$
declare v1_fields text[] := ops.engineering_slice_plan_slice_fields('engineering-slice-plan.v1');
        v2_fields text[] := ops.engineering_slice_plan_slice_fields('engineering-slice-plan.v2');
        literal_0450 text[] := array[
          'baseline_evidence_refs','concurrency_posture','declared_component_refs',
          'declared_plan_step_refs','declared_resource_refs','definition_of_done',
          'dependency_refs','forbidden_change_refs','manual_qa_required','objective',
          'ordinal','planned_checks','release_requirement','risk_class',
          'scope_boundary','slice_ref'];
        failures integer := 0;
begin
  if (select array_agg(f order by f) from unnest(v1_fields) f)
     is distinct from (select array_agg(f order by f) from unnest(literal_0450) f) then
    failures := failures + 1;
    raise warning 'PART A FAIL A32: the v1 field set no longer matches the literal array at 0450:331-333';
  end if;
  if (select array_agg(f order by f) from unnest(v2_fields) f)
     is distinct from (select array_agg(f order by f)
                         from unnest(literal_0450 || 'design_contract'::text) f) then
    failures := failures + 1;
    raise warning 'PART A FAIL A33: the v2 field set is not the v1 set plus design_contract';
  end if;
  if ops.engineering_slice_plan_slice_fields('engineering-slice-plan.v3') is not null then
    failures := failures + 1;
    raise warning 'PART A FAIL A34: an unsupported slice-plan version must not resolve to a field set';
  end if;
  if ops.engineering_slice_plan_slice_fields(null) is not null then
    failures := failures + 1;
    raise warning 'PART A FAIL A35: an absent slice-plan version must not resolve to a field set';
  end if;
  if failures > 0 then raise exception 'PART A: % field-set pins did not match', failures; end if;
  raise notice 'PART A: 4 field-set pins matched';
end $$;

-- A36-A38.  The declared-ref predicate allows duplicates, exactly as the JS and
-- Python validators do, and is deliberately NOT the unique variant.  This pins
-- difference 2 in the candidate header so a future "tidy-up" that swaps one for
-- the other fails a check instead of silently refusing plans both source
-- validators accept.
--
-- The other half of difference 2 lives in the design contract: a v2 slice also
-- reaches ops.engineering_receipt_design_identifier_subset, which requires the
-- SUBSET (isolation.shared_resource_refs) to be unique and asks only
-- well-formedness of the SUPERSET (declared_resource_refs).  That predicate is
-- pinned directly in mcp-server/test/f03-receipt-validator-postgres.sql
-- (A63..A67); A41 and A42 above reach it end to end through this whole-plan
-- validator, which is the boundary where a both-sides-unique test would refuse a
-- plan both source validators accept.
do $$
declare failures integer := 0;
begin
  if ops.engineering_plan_identifier_list('["resource:a","resource:a"]'::jsonb) is not true then
    failures := failures + 1;
    raise warning 'PART A FAIL A36: the plan declared-ref predicate must allow duplicates';
  end if;
  if ops.engineering_receipt_identifier_array('["resource:a","resource:a"]'::jsonb) is not false then
    failures := failures + 1;
    raise warning 'PART A FAIL A37: the receipt predicate must still require uniqueness';
  end if;
  if ops.engineering_plan_identifier_list('["not a valid identifier"]'::jsonb) is not false
     or ops.engineering_plan_identifier_list('{}'::jsonb) is not false
     or ops.engineering_plan_identifier_list('[1]'::jsonb) is not false then
    failures := failures + 1;
    raise warning 'PART A FAIL A38: the plan declared-ref predicate must still be fully typed';
  end if;
  if failures > 0 then raise exception 'PART A: % declared-ref predicate cases did not match', failures; end if;
  raise notice 'PART A: 3 declared-ref predicate cases matched';
end $$;

-- A39/A40.  The three new functions are SECURITY DEFINER, so an unrevoked
-- default ACL would be a PUBLIC-reachable surface.  This reads pg_proc only.
-- proacl is NULL for a function whose grants have never been changed, and NULL
-- means the default, which for a function is EXECUTE to PUBLIC; acldefault() is
-- substituted so the unrevoked case is detected rather than read as empty.
do $$
declare wanted text[] := array[
          'ops.engineering_slice_plan_slice_fields(text)',
          'ops.engineering_plan_identifier_list(jsonb)',
          'ops.engineering_slice_plan_refusal(jsonb)'];
        missing text; offenders text;
begin
  select string_agg(sig, ', ' order by sig) into missing
    from unnest(wanted) as sigs(sig) where to_regprocedure(sig) is null;
  if missing is not null then
    raise exception 'PART A FAIL A39: the candidate is not applied, these functions are absent: %', missing;
  end if;
  select string_agg(format('%s->%s', sig, grantee), '; ' order by sig, grantee) into offenders
    from (
      select sigs.sig,
             case when acl.grantee = 0 then 'PUBLIC' else acl.grantee::regrole::text end as grantee
        from unnest(wanted) as sigs(sig)
        join pg_catalog.pg_proc p on p.oid = to_regprocedure(sigs.sig)
        cross join lateral aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) acl
       where acl.privilege_type = 'EXECUTE'
         and (acl.grantee = 0
              or acl.grantee::regrole::text = any(array[
                   'carr_reader','carr_writer','carr_jobs','carr_authority']))
    ) q;
  if offenders is not null then
    raise exception
      'PART A FAIL A40: new SECURITY DEFINER functions still carry EXECUTE for public or an application role: %',
      offenders;
  end if;
  raise notice 'PART A: 3 new functions exist and carry no EXECUTE for public or any application role';
end $$;

-- ===========================================================================
-- PART B -- the replaced registration seam, against a scratch admission source.
--
-- Each case builds a plan bound to the exact current accepted Work Request and
-- plan, calls ops.engineering_register_slice_plan, records what it did, and then
-- rolls the case back through a deliberate sentinel exception.  Nothing a case
-- writes survives the case, and nothing survives the script.
--
-- This is the seam a carr_writer with a direct SQL connection can reach without
-- going through requirePlan, which is the whole point of the replacement.
-- ===========================================================================

create temporary table f03p_lane(
  work_request_ref text not null,
  job_id uuid not null,
  lease_token uuid not null,
  agent_session_id uuid not null
) on commit drop;

insert into f03p_lane(work_request_ref, job_id, lease_token, agent_session_id)
values (:'lane_work_request_ref', :'lane_job_id'::uuid,
        :'lane_lease_token'::uuid, :'lane_agent_session_id'::uuid);

-- Build a plan bound to the live admission source, sealed with the digest the
-- validator will recompute.
create function pg_temp.f03p_bound_plan(p_schema_version text, p_slices jsonb)
returns jsonb language plpgsql as $$
declare v_source jsonb; v_lane record;
begin
  select * into v_lane from f03p_lane;
  v_source := ops.engineering_admission_source(v_lane.work_request_ref);
  if v_source is null then return null; end if;
  return pg_temp.f03p_seal(jsonb_build_object(
    'schema_version', p_schema_version,
    'work_request', jsonb_build_object(
      'id', v_source->'work_request'->>'id',
      'state_version', (v_source->'work_request'->>'version')::integer,
      'canonical_record_digest', v_source->'work_request'->>'canonical_record_digest'),
    'accepted_plan_revision', jsonb_build_object(
      'id', v_source->'accepted_plan'->>'plan_ref',
      'revision', (v_source->'accepted_plan'->>'revision')::integer,
      'digest', v_source->'accepted_plan'->>'digest'),
    'slices', p_slices));
end $$;

create function pg_temp.f03p_register(p_plan jsonb) returns text
language plpgsql as $$
declare v_lane record; v_row ops.engineering_slice_plan%rowtype;
begin
  select * into v_lane from f03p_lane;
  if p_plan is null then return 'setup-error: no admission source for the lane Work Request'; end if;
  begin
    select * into v_row from ops.engineering_register_slice_plan(
      v_lane.work_request_ref, p_plan, p_plan->>'plan_digest', gen_random_uuid());
    if v_row.id is null then return 'refused: no row returned'; end if;
    return 'registered';
  exception when others then
    return 'refused: ' || sqlerrm;
  end;
end $$;

create temporary table f03p_register_case(
  name text primary key,
  ordinal integer not null,
  plan_kind text not null,
  expected text
) on commit drop;

-- expected NULL means "the plan registers".  Every other row names a substring
-- the raised message must contain.
insert into f03p_register_case(name, ordinal, plan_kind, expected)
values
  -- 0616 retired NEW v1 registrations at this seam.  A stored v1 row still reads
  -- (C01 below; the receipt fixture's D01) because the refusal lives only here,
  -- never in ops.engineering_slice_plan_refusal (A05 still accepts v1).
  ('B01-refuse-new-v1-registration', 1, 'v1', 'version is not registrable: engineering-slice-plan.v1'),
  ('B02-register-valid-v2-plan', 2, 'v2', null),
  ('B03-refuse-v2-plan-with-a-digest-that-does-not-bind-content', 3, 'v2_bad_digest',
   'plan.plan_digest_does_not_bind_content'),
  ('B04-refuse-v2-plan-with-a-dependency-cycle', 4, 'v2_cycle', 'plan.dependency_cycle'),
  ('B05-refuse-v2-plan-with-duplicate-ordinals', 5, 'v2_ordinal_dup', 'plan.duplicate_ordinal'),
  ('B06-refuse-v2-plan-with-shared-seam-authority', 6, 'v2_seam_dup',
   'plan.seam_duplicate_authority'),
  ('B07-refuse-v2-plan-with-a-parallel-resource-collision', 7, 'v2_resource_collision',
   'plan.parallel_resource_conflict'),
  ('B08-refuse-v2-plan-without-a-design-contract', 8, 'v2_no_contract', 'shape'),
  ('B09-refuse-unknown-plan-schema-version', 9, 'v3', 'plan.schema_version'),
  -- The pre-existing 0310 binding refusal must keep its exact text and its
  -- precedence over the new whole-plan check.
  ('B10-refuse-plan-not-bound-to-the-accepted-work-request', 10, 'v2_unbound',
   'not bound to the exact accepted Work Request and plan');

create function pg_temp.f03p_case_plan(p_kind text) returns jsonb language plpgsql as $$
declare v_plan jsonb;
begin
  if p_kind = 'v1' then
    return pg_temp.f03p_bound_plan('engineering-slice-plan.v1', jsonb_build_array(
      pg_temp.f03p_slice('slice:a','a',1,'[]'::jsonb,null,'parallel_safe','reuse',null,'[]'::jsonb,false)));
  elsif p_kind = 'v2' then
    return pg_temp.f03p_bound_plan('engineering-slice-plan.v2', jsonb_build_array(
      pg_temp.f03p_slice('slice:a','a',1),
      pg_temp.f03p_slice('slice:b','b',2,jsonb_build_array('slice:a'))));
  elsif p_kind = 'v2_bad_digest' then
    v_plan := pg_temp.f03p_bound_plan('engineering-slice-plan.v2',
      jsonb_build_array(pg_temp.f03p_slice('slice:a','a',1)));
    -- Change the content, keep the sealed digest: this is exactly what a caller
    -- bypassing requirePlan can send today, and 0310 accepted it.
    return case when v_plan is null then null
      else jsonb_set(v_plan, '{slices,0,objective}', '"content the sealed digest does not cover"') end;
  elsif p_kind = 'v2_cycle' then
    return pg_temp.f03p_bound_plan('engineering-slice-plan.v2', jsonb_build_array(
      pg_temp.f03p_slice('slice:a','a',1,jsonb_build_array('slice:b')),
      pg_temp.f03p_slice('slice:b','b',2,jsonb_build_array('slice:a'))));
  elsif p_kind = 'v2_ordinal_dup' then
    return pg_temp.f03p_bound_plan('engineering-slice-plan.v2', jsonb_build_array(
      pg_temp.f03p_slice('slice:a','a',1),
      pg_temp.f03p_slice('slice:b','b',1)));
  elsif p_kind = 'v2_seam_dup' then
    return pg_temp.f03p_bound_plan('engineering-slice-plan.v2', jsonb_build_array(
      pg_temp.f03p_slice('slice:a','a',1,'[]'::jsonb,null,'parallel_safe','new_module','seam:shared'),
      pg_temp.f03p_slice('slice:b','b',2,'[]'::jsonb,null,'parallel_safe','new_module','seam:shared')));
  elsif p_kind = 'v2_resource_collision' then
    return pg_temp.f03p_bound_plan('engineering-slice-plan.v2', jsonb_build_array(
      pg_temp.f03p_slice('slice:a','a',1,'[]'::jsonb,jsonb_build_array('resource:shared')),
      pg_temp.f03p_slice('slice:b','b',2,'[]'::jsonb,jsonb_build_array('resource:shared'))));
  elsif p_kind = 'v2_no_contract' then
    return pg_temp.f03p_bound_plan('engineering-slice-plan.v2', jsonb_build_array(
      pg_temp.f03p_slice('slice:a','a',1,'[]'::jsonb,null,'parallel_safe','reuse',null,'[]'::jsonb,false)));
  elsif p_kind = 'v3' then
    return pg_temp.f03p_bound_plan('engineering-slice-plan.v3',
      jsonb_build_array(pg_temp.f03p_slice('slice:a','a',1)));
  elsif p_kind = 'v2_unbound' then
    v_plan := pg_temp.f03p_bound_plan('engineering-slice-plan.v2',
      jsonb_build_array(pg_temp.f03p_slice('slice:a','a',1)));
    return case when v_plan is null then null
      else pg_temp.f03p_seal((v_plan - 'plan_digest')
        || jsonb_build_object('accepted_plan_revision', jsonb_build_object(
             'id','plan:not-the-accepted-one','revision',1,
             'digest','sha256:' || repeat('e',64)))) end;
  end if;
  return null;
end $$;

do $$
declare row_case record; result text; failures integer := 0; total integer := 0;
        lane record; source_present boolean;
begin
  select * into lane from f03p_lane;
  select ops.engineering_admission_source(lane.work_request_ref) is not null into source_present;
  if not source_present then
    raise notice 'PART B SKIPPED: ops.engineering_admission_source(%) is null -- see PREREQUISITE P2. No register case ran.',
      lane.work_request_ref;
    return;
  end if;

  for row_case in select * from f03p_register_case order by ordinal loop
    total := total + 1;
    -- Each case runs inside a subtransaction that is always rolled back through
    -- the sentinel below, so its slice plan disappears before the next case runs
    -- and the UNIQUE (accepted_plan_id) constraint is never contended.  plpgsql
    -- variables assigned before the sentinel survive the rollback, which is how
    -- the result escapes.
    begin
      result := pg_temp.f03p_register(pg_temp.f03p_case_plan(row_case.plan_kind));
      raise exception '__f03p_case_rollback__';
    exception when others then
      if sqlerrm <> '__f03p_case_rollback__' then
        result := 'setup-error: ' || sqlerrm;
      end if;
    end;

    if row_case.expected is null then
      if result <> 'registered' then
        failures := failures + 1;
        raise warning 'PART B FAIL % : expected the plan to register, got %', row_case.name, result;
      end if;
    elsif result is null or position(row_case.expected in result) = 0 then
      failures := failures + 1;
      raise warning 'PART B FAIL % : expected a refusal containing "%", got %',
        row_case.name, row_case.expected, coalesce(result, '<null>');
    end if;
  end loop;

  if failures > 0 then
    raise exception 'PART B: % of % register cases did not match', failures, total;
  end if;
  raise notice 'PART B: % register cases matched', total;
end $$;

-- ===========================================================================
-- PART C -- ops.canonical_ownership_plan_dependencies, the first 0450 pin.
--
-- This is the decisive test for that pin: the function reads only
-- ops.engineering_slice_plan, so one directly inserted plan row per case is
-- enough, and a directly inserted row is also exactly what a caller bypassing
-- the registration seam would leave behind.
-- ===========================================================================

create function pg_temp.f03p_plan_dependencies(p_plan jsonb, p_slice_ref text)
returns jsonb language plpgsql as $$
declare v_lane record; v_source jsonb; v_plan_id uuid;
begin
  select * into v_lane from f03p_lane;
  v_source := ops.engineering_admission_source(v_lane.work_request_ref);
  if v_source is null then return null; end if;
  insert into ops.engineering_slice_plan
    (work_request_id,accepted_plan_id,accepted_plan_hash,work_request_version,plan_digest,plan,idempotency_key)
  values (regexp_replace(v_source->'work_request'->>'id','^wr:','')::uuid,
          (v_source->'accepted_plan'->>'record_id')::uuid,
          v_source->'accepted_plan'->>'digest',
          (v_source->'work_request'->>'version')::integer,
          p_plan->>'plan_digest', p_plan, gen_random_uuid())
  returning id into v_plan_id;
  return ops.canonical_ownership_plan_dependencies(v_plan_id, p_slice_ref);
end $$;

create temporary table f03p_ownership_case(
  name text primary key,
  ordinal integer not null,
  plan jsonb not null,
  slice_ref text not null,
  expect_ok boolean not null,
  expected_reason text,
  expected_dependencies jsonb
) on commit drop;

insert into f03p_ownership_case(name, ordinal, plan, slice_ref, expect_ok, expected_reason, expected_dependencies)
values
  -- Regression: a v1 plan is read exactly as 0450 read it.
  ('C01-v1-slice-still-resolves', 1,
   pg_temp.f03p_plan('engineering-slice-plan.v1', jsonb_build_array(
     pg_temp.f03p_slice('slice:a','a',1,'[]'::jsonb,null,'parallel_safe','reuse',null,'[]'::jsonb,false))),
   'slice:a', true, null, '[]'::jsonb),
  -- THE FIX: 0450:330 as shipped refuses this slice outright, because it carries
  -- a 17th key.  With the replacement it resolves.
  ('C02-v2-slice-resolves-instead-of-refusing', 2,
   pg_temp.f03p_plan('engineering-slice-plan.v2', jsonb_build_array(
     pg_temp.f03p_slice('slice:a','a',1))),
   'slice:a', true, null, '[]'::jsonb),
  ('C03-v2-slice-dependencies-are-projected', 3,
   pg_temp.f03p_plan('engineering-slice-plan.v2', jsonb_build_array(
     pg_temp.f03p_slice('slice:a','a',1),
     pg_temp.f03p_slice('slice:b','b',2,jsonb_build_array('slice:a')))),
   'slice:b', true, null,
   '[{"slice_ref":"slice:a","required_state":"independently_verified"}]'::jsonb),
  -- Fail-closed on a malformed v2 slice: the 17-field set is exact, not a
  -- superset test, so a missing design_contract still refuses.
  ('C04-v2-slice-missing-design-contract-fails-closed', 4,
   pg_temp.f03p_plan('engineering-slice-plan.v2', jsonb_build_array(
     pg_temp.f03p_slice('slice:a','a',1,'[]'::jsonb,null,'parallel_safe','reuse',null,'[]'::jsonb,false))),
   'slice:a', false, 'malformed_dependencies', null),
  -- And an unknown key in an otherwise valid v2 slice.
  ('C05-v2-slice-with-an-unknown-key-fails-closed', 5,
   pg_temp.f03p_plan('engineering-slice-plan.v2', jsonb_build_array(
     jsonb_set(pg_temp.f03p_slice('slice:a','a',1), '{unexpected}', '"x"', true))),
   'slice:a', false, 'malformed_dependencies', null),
  -- An unsupported plan version is refused rather than defaulted to v1.
  ('C06-unsupported-plan-version-fails-closed', 6,
   pg_temp.f03p_plan('engineering-slice-plan.v3', jsonb_build_array(
     pg_temp.f03p_slice('slice:a','a',1))),
   'slice:a', false, 'unsupported_plan_schema_version', null),
  ('C07-unknown-slice-ref-fails-closed', 7,
   pg_temp.f03p_plan('engineering-slice-plan.v2', jsonb_build_array(
     pg_temp.f03p_slice('slice:a','a',1))),
   'slice:never-declared', false, 'malformed_dependencies', null);

do $$
declare row_case record; result jsonb; failures integer := 0; total integer := 0;
        lane record; source_present boolean; ok boolean; reason text;
begin
  select * into lane from f03p_lane;
  select ops.engineering_admission_source(lane.work_request_ref) is not null into source_present;
  if not source_present then
    raise notice 'PART C SKIPPED: ops.engineering_admission_source(%) is null -- see PREREQUISITE P2. No ownership case ran.',
      lane.work_request_ref;
    return;
  end if;

  for row_case in select * from f03p_ownership_case order by ordinal loop
    total := total + 1;
    result := null;
    begin
      result := pg_temp.f03p_plan_dependencies(row_case.plan, row_case.slice_ref);
      raise exception '__f03p_case_rollback__';
    exception when others then
      if sqlerrm <> '__f03p_case_rollback__' then
        raise warning 'PART C FAIL % : setup-error %', row_case.name, sqlerrm;
        failures := failures + 1;
        result := null;
      end if;
    end;

    if result is null then
      failures := failures + 1;
      raise warning 'PART C FAIL % : no result', row_case.name;
      continue;
    end if;
    ok := coalesce((result->>'ok')::boolean, false);
    reason := result#>>'{refusal,actual,reason}';
    if ok is distinct from row_case.expect_ok then
      failures := failures + 1;
      raise warning 'PART C FAIL % : expected ok=%, got %', row_case.name, row_case.expect_ok, result;
    elsif row_case.expect_ok
      and (result->'dependencies') is distinct from row_case.expected_dependencies then
      failures := failures + 1;
      raise warning 'PART C FAIL % : expected dependencies %, got %',
        row_case.name, row_case.expected_dependencies, result->'dependencies';
    elsif not row_case.expect_ok
      and (reason is null or reason is distinct from row_case.expected_reason) then
      failures := failures + 1;
      raise warning 'PART C FAIL % : expected refusal reason "%", got %',
        row_case.name, row_case.expected_reason, result;
    end if;
  end loop;

  if failures > 0 then
    raise exception 'PART C: % of % plan-dependency cases did not match', failures, total;
  end if;
  raise notice 'PART C: % plan-dependency cases matched', total;
end $$;

-- ===========================================================================
-- PART D -- ops.canonical_ownership_dependency_state, the second 0450 pin.
--
-- This one cannot be reached with a plan row alone: it requires exactly one
-- unsuperseded envelope leaf and a persisted receipt before it will read the
-- slice at all.  Each case therefore builds a whole lineage -- slice plan,
-- envelope, claimed attempt, appended receipt -- calls the function with
-- p_required_state='completed' (which returns without needing a reviewer fact),
-- and rolls the entire case back.
--
-- The decisive assertion is D02: with 0450 as shipped, a v2 slice fails the
-- 16-field exact_object at :433 and the whole call returns
-- DEPENDENCY_UNSATISFIED even though the lineage is perfect.
--
-- Appending the receipt requires the RECEIPT candidate to be applied, so a D02
-- failure reading "slice plan is malformed" means that candidate is missing, not
-- that this replacement is wrong.  The Part A pre-flight catches that case
-- first.
--
-- D02 also depends on the lane payload digest binding the plan content, because
-- the replaced receipt seam validates the whole stored v2 plan before appending;
-- it is SKIPPED with the required digest printed, not failed, when it does not.
-- See PREREQUISITE P3.  A D02 failure reading "not a valid typed slice plan"
-- means the plan body itself is wrong, which would be a defect in this fixture
-- or in ops.engineering_slice_plan_refusal, not a lane misconfiguration.
-- ===========================================================================

create function pg_temp.f03p_dependency_state(p_schema_version text, p_with_contract boolean)
returns jsonb language plpgsql as $$
declare lane record; lane_job record; lane_session record; v_source jsonb;
        v_work_request_id uuid; v_accepted_plan_id uuid; v_slice_ref text; v_plan_digest text;
        v_actor_id uuid; v_actor_slug text; v_revision jsonb; v_slice jsonb; v_plan jsonb;
        v_slice_plan_id uuid; v_envelope_id uuid; v_envelope jsonb; v_envelope_digest text;
        v_issued_at timestamptz; v_expires_at timestamptz;
        v_receipt jsonb; v_receipt_digest text; v_canonical_digest text;
begin
  select * into lane from f03p_lane;
  select * into lane_job from ops.job where id = lane.job_id;
  if not found then return jsonb_build_object('setup_error','lane job row not found'); end if;
  select * into lane_session from ops.capability_agent_session where id = lane.agent_session_id;
  if not found then return jsonb_build_object('setup_error','lane capability_agent_session row not found'); end if;
  if lane_session.lease_expires_at is null then
    return jsonb_build_object('setup_error','lane capability_agent_session has no lease_expires_at');
  end if;
  v_source := ops.engineering_admission_source(lane.work_request_ref);
  if v_source is null then
    return jsonb_build_object('setup_error','ops.engineering_admission_source returned null');
  end if;
  select a.id, a.slug into v_actor_id, v_actor_slug
    from public.actor a where a.id = lane_session.executor_actor_id;
  if not found then return jsonb_build_object('setup_error','lane executor actor row not found'); end if;

  v_work_request_id := regexp_replace(v_source->'work_request'->>'id', '^wr:', '')::uuid;
  v_accepted_plan_id := (v_source->'accepted_plan'->>'record_id')::uuid;
  v_slice_ref := lane_job.payload->>'slice_ref';
  v_plan_digest := lane_job.payload->>'plan_digest';
  if v_slice_ref is null or v_plan_digest is null then
    return jsonb_build_object('setup_error','lane job payload is missing slice_ref or plan_digest');
  end if;
  v_revision := jsonb_build_object(
    'id', v_source->'accepted_plan'->>'plan_ref',
    'revision', (v_source->'accepted_plan'->>'revision')::integer,
    'digest', v_source->'accepted_plan'->>'digest');

  -- The bound slice.  The plan_digest is the lane job payload's digest, because
  -- ops.engineering_envelope_currentness:167 requires payload->>'plan_digest' to
  -- equal the registered plan's digest.  This plan is therefore inserted
  -- directly rather than registered: Part B covers the registration seam, and
  -- this part is about what the ownership kernel reads afterwards.
  v_slice := pg_temp.f03p_slice(v_slice_ref, 'lane', 1, '[]'::jsonb, null,
    'parallel_safe', 'reuse', null, '[]'::jsonb, p_with_contract);
  v_plan := jsonb_build_object(
    'schema_version', p_schema_version,
    'plan_digest', v_plan_digest,
    'work_request', jsonb_build_object(
      'id', 'wr:' || v_work_request_id::text,
      'state_version', (v_source->'work_request'->>'version')::integer,
      'canonical_record_digest', v_source->'work_request'->>'canonical_record_digest'),
    'accepted_plan_revision', v_revision,
    'slices', jsonb_build_array(v_slice));

  -- Before anything is written.  This part appends a receipt, and for a v2 plan
  -- the replaced receipt seam validates the whole stored plan -- including that
  -- plan_digest binds the canonical content.  Currentness (0335:167, :179)
  -- forces the stored digest to be the lane payload's, so this case can only run
  -- when the lane was configured with the digest of this exact body.  Report the
  -- required value and skip; mutate nothing to arrange it.  See PREREQUISITE P3.
  if p_schema_version = 'engineering-slice-plan.v2' then
    v_canonical_digest := 'sha256:' || encode(public.digest(
      ops.guidance_import_canonical_json(v_plan - 'plan_digest'), 'sha256'), 'hex');
    if v_canonical_digest is distinct from v_plan_digest then
      return jsonb_build_object('lane_digest_mismatch', v_canonical_digest);
    end if;
  end if;

  insert into ops.engineering_slice_plan
    (work_request_id, accepted_plan_id, accepted_plan_hash, work_request_version,
     plan_digest, plan, idempotency_key)
  values (v_work_request_id, v_accepted_plan_id, v_source->'accepted_plan'->>'digest',
          (v_source->'work_request'->>'version')::integer, v_plan_digest, v_plan, gen_random_uuid())
  returning id into v_slice_plan_id;

  -- The envelope is the exact shape ops.engineering_envelope_currentness
  -- (0335:126) requires, with the instants aligned to the lane session's
  -- whole-second lease.
  v_envelope_id := gen_random_uuid();
  v_expires_at := lane_session.lease_expires_at;
  v_issued_at := v_expires_at - interval '30 minutes';
  v_envelope := jsonb_build_object(
    'schema_version', 'execution-envelope.v1',
    'envelope_id', 'env:' || v_envelope_id::text,
    'work_request_id', 'wr:' || v_work_request_id::text,
    'plan_revision', v_revision,
    'agent_session', jsonb_build_object(
      'id', 'session:' || lane_session.id::text,
      'lease_expires_at', to_char(v_expires_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')),
    'issued_at', to_char(v_issued_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    'expires_at', to_char(v_expires_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    'state_binding', jsonb_build_object(
      'state_version', (v_source->'work_request'->>'version')::integer,
      'canonical_record_digest', v_source->'work_request'->>'canonical_record_digest',
      'accepted_resource_revisions', '[]'::jsonb,
      'compare_and_swap_required', true),
    'phase_binding', jsonb_build_object(
      'phase_id', 'phase:' || v_slice_ref,
      'session_affinity', 'fresh_native_session_required',
      'switch_conditions', jsonb_build_array('verified_checkpoint', 'phase_boundary'),
      'native_session_transfer', 'semantic_state_only'),
    'evaluation_context', jsonb_build_object(
      'experiment_arm', 'audited_state_routed_executors',
      'auditor_mode', 'diverse_read_only_auditor',
      'evaluation_kernel_ref', 'kernel:engineering-passport-v1',
      'workflow_rubric_digest', v_plan_digest,
      'case_set_digest', 'sha256:' || repeat('d', 64)),
    'request', jsonb_build_object(
      'job_ref', 'job:' || lane_job.id::text,
      'input_digest', 'sha256:' || repeat('e', 64),
      'data_class', 'metadata_only',
      'allowed_actions', '["repository:create-worktree","repository:create-branch",
        "repository:write-declared-scope","repository:run-checks","repository:commit",
        "repository:push-branch","repository:open-pr"]'::jsonb,
      'declared_expectations', jsonb_build_object(
        'plan_step_refs', jsonb_build_array('step:lane'),
        'component_refs', jsonb_build_array('component:lane'),
        'resource_refs', jsonb_build_array('resource:lane'),
        'component_dependencies', '[]'::jsonb)),
    'server_binding', jsonb_build_object(
      'identity', jsonb_build_object(
        'agent_principal_id', 'agent:codex', 'runtime_principal', 'runtime:codex'),
      'authority', jsonb_build_object(
        'environment', 'rehearsal',
        'capability_profile', 'capability:engineering-repository-write',
        'read_only', false),
      'adapter', jsonb_build_object(
        'surface', 'codex_desktop', 'adapter_id', 'adapter:codex-desktop')),
    'handoff', jsonb_build_object(
      'mode', 'original', 'replaces_agent_session_id', null::jsonb,
      'capability_inherited', false, 'checkpoint_ref', null::jsonb,
      'native_session_transfer', 'semantic_state_only'));
  v_envelope_digest := 'sha256:' || encode(public.digest(v_envelope::text, 'sha256'), 'hex');

  insert into ops.engineering_execution_envelope
    (id, job_id, work_request_id, accepted_plan_id, slice_plan_id, slice_ref, agent_session_id,
     state_version, canonical_record_digest, envelope_digest, envelope, issued_at, expires_at)
  values (v_envelope_id, lane_job.id, v_work_request_id, v_accepted_plan_id, v_slice_plan_id,
          v_slice_ref, lane_session.id,
          (v_source->'work_request'->>'version')::integer,
          v_source->'work_request'->>'canonical_record_digest',
          v_envelope_digest, v_envelope, v_issued_at, v_expires_at);

  perform 1 from ops.job_attempt
   where job_id = lane_job.id and attempt = lane_job.attempt
     and lease_token = lane.lease_token and state = 'running';
  if not found then
    insert into ops.job_attempt(job_id, attempt, lease_owner, lease_token, state)
    values (lane_job.id, lane_job.attempt,
            coalesce(lane_job.lease_owner, 'f03p-fixture'), lane.lease_token, 'running');
  end if;

  v_receipt := jsonb_build_object(
    'schema_version', 'engineering-slice-receipt.v1',
    'plan_digest', v_plan_digest,
    'envelope_digest', v_envelope_digest,
    'slice_ref', v_slice_ref,
    'attempt_id', 'attempt:' || lane_job.attempt,
    'outcome', 'claimed_complete',
    'planned_resource_refs', jsonb_build_array('resource:lane'),
    'actual_resource_refs', jsonb_build_array('resource:lane'),
    'planned_component_refs', jsonb_build_array('component:lane'),
    'actual_component_refs', jsonb_build_array('component:lane'),
    'artifact_refs', jsonb_build_array('artifact:f03-plan-ownership-candidate'),
    'evidence_refs', jsonb_build_array(jsonb_build_object(
      'ref', 'evidence:f03p-receipt', 'redaction_class', 'metadata_only',
      'content_digest', 'sha256:' || repeat('f', 64))),
    'checks', jsonb_build_array(jsonb_build_object(
      'check_ref', 'check:lane', 'state', 'passed',
      'evidence_refs', jsonb_build_array(jsonb_build_object(
        'ref', 'evidence:f03p-check', 'redaction_class', 'metadata_only',
        'content_digest', 'sha256:' || repeat('1', 64))))),
    'deviations', '[]'::jsonb,
    'source_evidence', jsonb_build_object(
      'worktree_ref', 'worktree:f03p-scratch', 'branch_ref', 'branch:f03p-scratch',
      'source_sha', repeat('0', 40), 'evidence_refs', '[]'::jsonb),
    'reset_reconstruction', jsonb_build_object(
      'fresh_session', true, 'inherited_transcript_used', false,
      'reconstruction_free', true, 'remediation_action', null::jsonb),
    'executor_claim', jsonb_build_object(
      'claim_state', 'executor_claim', 'claimed_by', v_actor_slug,
      'claimed_at', to_char(clock_timestamp() at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')),
    'independent_verification_required', true,
    'attribution', jsonb_build_object(
      'actor_ref', 'agent:codex',
      'session_ref', 'session:' || lane_session.id::text,
      'adapter_ref', 'adapter:codex-desktop'));
  v_receipt_digest := 'sha256:' ||
    encode(public.digest(ops.guidance_import_canonical_json(v_receipt), 'sha256'), 'hex');

  begin
    perform ops.engineering_record_slice_receipt(
      v_envelope_id, lane.lease_token, v_receipt, v_receipt_digest, v_actor_id);
  exception when others then
    return jsonb_build_object('setup_error', 'receipt did not append: ' || sqlerrm);
  end;

  return ops.canonical_ownership_dependency_state(
    v_work_request_id, v_slice_plan_id, v_slice_ref, 'completed');
end $$;

create temporary table f03p_state_case(
  name text primary key,
  ordinal integer not null,
  plan_schema_version text not null,
  with_contract boolean not null
) on commit drop;

insert into f03p_state_case(name, ordinal, plan_schema_version, with_contract)
values
  ('D01-v1-lineage-still-resolves', 1, 'engineering-slice-plan.v1', false),
  ('D02-v2-lineage-resolves-instead-of-refusing', 2, 'engineering-slice-plan.v2', true);

do $$
declare row_case record; result jsonb; failures integer := 0; total integer := 0;
        skipped integer := 0; lane record; job_exists boolean;
begin
  select * into lane from f03p_lane;
  select exists(select 1 from ops.job where id = lane.job_id) into job_exists;
  if not job_exists then
    raise notice 'PART D SKIPPED: no scratch lane job % -- see PREREQUISITE P3. No dependency-state case ran.',
      lane.job_id;
    return;
  end if;

  for row_case in select * from f03p_state_case order by ordinal loop
    total := total + 1;
    result := null;
    begin
      result := pg_temp.f03p_dependency_state(row_case.plan_schema_version, row_case.with_contract);
      raise exception '__f03p_case_rollback__';
    exception when others then
      if sqlerrm <> '__f03p_case_rollback__' then
        raise warning 'PART D FAIL % : setup-error %', row_case.name, sqlerrm;
        failures := failures + 1;
        result := null;
      end if;
    end;

    if result is null then
      failures := failures + 1;
      raise warning 'PART D FAIL % : no result', row_case.name;
    elsif result ? 'lane_digest_mismatch' then
      -- Not a failure and not a pass: the lane cannot express this case.
      skipped := skipped + 1;
      raise notice 'PART D SKIPPED % : set the lane job payload plan_digest to % -- see PREREQUISITE P3',
        row_case.name, result->>'lane_digest_mismatch';
    elsif result ? 'setup_error' then
      failures := failures + 1;
      raise warning 'PART D FAIL % : %', row_case.name, result->>'setup_error';
    elsif coalesce((result->>'ok')::boolean, false) is not true then
      failures := failures + 1;
      raise warning 'PART D FAIL % : expected ok=true, got %', row_case.name, result;
    end if;
  end loop;

  if failures > 0 then
    raise exception 'PART D: % of % dependency-state cases did not match', failures, total;
  end if;
  raise notice 'PART D: % of % dependency-state cases matched, % skipped',
    total - skipped, total, skipped;
end $$;

-- ===========================================================================
-- PART E -- the SHARED corpus, generated, not hand-written.
--
-- Parts A through D are this file's own cases, and they exist to discriminate
-- the database's refusal TOKENS, which no other validator produces.  What they
-- could never do is prove that this validator agrees with the two it must agree
-- with, because they were written here: a hand-written case table can only be
-- kept in step with the JS and Python corpus by somebody remembering to, and on
-- the day this fixture is finally run it would have proved the database against
-- a DIFFERENT set of cases than the validators it mirrors.
--
-- Part E is that missing binding.  Every case in the included file is derived
-- from mcp-server/test/fixtures/f03-design-contract-parity.v1.json -- the same
-- 218 vectors f03-design-contract-parity.test.mjs replays through requirePlan
-- and validate_engineering_slice_plan -- by
-- mcp-server/test/f03-sql-corpus-generator.mjs.  A vector added to the corpus
-- for JS and Python is automatically a vector this leg must satisfy, and there
-- is nothing here for anyone to keep up to date by hand.  The included file is
-- GENERATED and byte-checked against the corpus by that same JS suite, so an
-- edit to it, or a corpus change without a regenerate, is caught in CI long
-- before anyone reaches a database.
--
-- It needs the same two candidates Part A needs and adds no prerequisite: it
-- calls ops.engineering_slice_plan_refusal and nothing else.
--
-- The included header states the two things a pass here would and would not
-- mean, including the ONE canonicalization claim this file's own "WHAT THIS
-- FIXTURE DOES NOT COVER" note disclaims: Part E's plan_digest values are
-- literals computed by the JavaScript producer, not recomputed with the same
-- SQL expression the validator checks against, so a canonicalization divergence
-- between the database and the JS/Python producers surfaces there as a refused
-- positive instead of staying invisible.
-- ===========================================================================
\ir f03-design-contract-corpus-plan-postgres.generated.sql

-- ===========================================================================
-- Nothing here is kept.  This ROLLBACK is the point of the fixture.
-- ===========================================================================
rollback;

-- ===========================================================================
-- WHAT THIS FIXTURE DOES NOT COVER
-- ===========================================================================
--
--   * PARTS A THROUGH D do not verify canonicalization parity with the JS and
--     Python producers.  Every digest in them is computed with the same
--     ops.guidance_import_canonical_json expression the validator checks
--     against, so a divergence between the database and those producers is
--     invisible there.  The one case the receipt path does not already prove --
--     numeric spelling -- is discussed in the candidate; the three numeric
--     plan fields are separately pinned to ^[1-9][0-9]*$ before the digest check
--     runs.  PART E DOES cover it, and is the reason this bullet is now scoped
--     to A..D: every plan_digest in the generated corpus part is a literal the
--     JavaScript producer computed, so a divergence there refuses a positive
--     rather than passing unseen.  That covers the shapes the corpus carries;
--     it is still not a proof over arbitrary content.
--   * It does not cover the malformed-v2 fail-closed path of
--     ops.canonical_ownership_dependency_state end to end.  That function will
--     not read the slice at all until a valid receipt exists, and a malformed v2
--     slice cannot produce one, so the refusal would be indistinguishable from
--     "no receipt".  The shape authority it uses is
--     ops.engineering_slice_plan_slice_fields, which Part A pins directly and
--     Part C exercises decisively through the sibling function.
--   * It does not cover the lease kernel above these two functions --
--     ops.acquire_canonical_ownership_lease, canonical_ownership_validate_live,
--     renew/release/expire -- or any tenant, path-claim, fencing or
--     identity-context rule in 0450.  Those are unchanged and remain revoked
--     from every role.
--   * It does not cover ops.engineering_record_slice_receipt itself, the
--     finalize wrapper, reviewer facts or closure projection.  Those belong to
--     mcp-server/test/f03-receipt-validator-postgres.sql.  Part D exercises the
--     receipt seam only incidentally, as the way a lineage is built; in
--     particular it does not test the seam's whole-plan call on a MALFORMED
--     stored plan, which is B08..B10 of that fixture.
--   * Part A names every refusal token ops.engineering_slice_plan_refusal can
--     return, but one case per token is a DISCRIMINATION test, not an exhaustive
--     one: it does not enumerate every input that yields a given token, and the
--     design-contract facets behind the forwarded
--     slices[<ref>].design_contract.* tokens are stated once in
--     ops/f03-receipt-validator.candidate.sql and covered in Part A of
--     mcp-server/test/f03-receipt-validator-postgres.sql, not re-enumerated here.
--   * It does not cover the v1 side of difference 1 in the candidate header
--     beyond A05/A06.  requirePlan runs its plan_digest-binds-content check for
--     every version and this file runs it for v2 only, so a v1 plan whose digest
--     does not bind its content registers here; no case asserts that, because it
--     is a stated gap rather than a rule.
--   * It does not cover concurrency: no case exercises two registrations racing
--     on one accepted plan, and the idempotency replay path of
--     ops.engineering_register_slice_plan is reproduced verbatim rather than
--     re-tested here.
--   * Parts B, C and D exercise one scratch admission source.  They do not
--     exercise multiple Work Requests, superseded envelope lineage, or a plan
--     whose accepted revision changes underneath it.
