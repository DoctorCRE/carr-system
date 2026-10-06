-- f03-receipt-validator.candidate.sql
--
-- CANDIDATE SOURCE.  THIS IS NOT A MIGRATION.
--
--   * It deliberately carries NO migration ordinal and lives outside
--     migrations/.  Assigning an ordinal, sequencing it against the migration
--     frontier, and applying it anywhere other than an isolated scratch
--     database are root decisions this file does not make and must not imply.
--   * It is forward-only: no issued receipt, plan, envelope or reviewer fact is
--     rewritten, no historical migration is edited, and no grant, role or
--     job/Work Request record is created or relaxed.
--   * It is reviewable source pending independent review, checks and
--     integration.  Nothing here has been executed.
--
-- WHAT IT FIXES
--
-- migrations/0310_engineering_execution_fabric.sql:185 (ops.engineering_register_slice_plan)
-- accepts any plan whose bindings match the accepted source; it pins no
-- schema_version, so an engineering-slice-plan.v2 plan registers today.
--
-- migrations/0335_engineering_controller_currentness.sql:473
-- (ops.engineering_record_slice_receipt) is the latest RECEIPT validator.  At
-- :550 it pins the bound slice plan to 'engineering-slice-plan.v1' and at :578
-- it requires the exact 16-field v1 slice object.  A v2 plan therefore registers
-- and dispatches, but its receipt can never append: the seam raises
-- 'engineering receipt slice plan is malformed or not bound to the envelope'.
-- ops.engineering_finalize_slice_receipt (0335:793) delegates to that function
-- by name and signature, so it is intentionally NOT redefined here; replacing
-- the record function in place fixes the finalize door too.
--
-- IT IS NOT THE ONLY v1-EXACT SLICE PIN.  Two more live in a LATER migration and
-- read the same ops.engineering_slice_plan.plan->'slices' rows:
--
--   * migrations/0450_canonical_ownership_lease_kernel.sql:330
--     (ops.canonical_ownership_plan_dependencies)
--   * migrations/0450_canonical_ownership_lease_kernel.sql:433
--     (ops.canonical_ownership_dependency_state)
--
-- Both call ops.engineering_receipt_exact_object(slice, <the 16 v1 fields>).  A
-- v2 slice carries a 17th key, design_contract, so both refuse it
-- (SLICE_PLAN_BINDING_STALE / malformed_dependencies), and 0450:828, :848,
-- :1048 and :1059 reach them for the SUBJECT slice, not only its dependencies.
-- Applying THIS file alone therefore does not make engineering-slice-plan.v2
-- usable end to end: it clears the receipt seam and leaves the canonical
-- ownership lease kernel refusing.  Those two functions are outside this
-- candidate's two owned paths; the companion candidate
-- ops/f03-plan-ownership-validator.candidate.sql carries their forward-only
-- version-aware replacements.
--
-- THE TWO CANDIDATES DEPEND ON EACH OTHER AT RUNTIME, NEITHER AT CREATE.  This
-- file's v2 branch calls ops.engineering_slice_plan_refusal (companion file);
-- the companion file's whole-plan validator calls
-- ops.engineering_receipt_design_contract_refusal and
-- ops.engineering_receipt_design_self_label_free (this file).  Both replaced
-- seams and both new validators are LANGUAGE plpgsql, and PostgreSQL only
-- syntax-checks a plpgsql body at CREATE -- check_function_bodies does not
-- parse-analyse the SQL statements inside it -- so each file CREATEs cleanly
-- with the other absent, in either order.  The dependency resolves at
-- EXECUTION, and every half-installed state fails closed:
--
--   * this file alone: a v2 receipt append raises 'function
--     ops.engineering_slice_plan_refusal(jsonb) does not exist' and refuses.
--     v1 is untouched, and no v2 receipt exists to strand (see below).
--   * the companion alone: a v2 plan registration raises 'function
--     ops.engineering_receipt_design_contract_refusal(jsonb) does not exist' and
--     refuses.  v1 registration is untouched.
--
-- So there is no unsafe install window and no ordering obligation between the
-- two files.  Both must be applied before engineering-slice-plan.v2 is usable;
-- neither can half-open a door on its own.
--
-- V5-F03 adds engineering-slice-plan.v2 with the closed per-slice
-- design_contract (mcp-server/src/engineering-runtime.js requirePlan /
-- requireDesignContract, tools/room-bridge/engineering_passport.py
-- validate_engineering_slice_plan / _validate_design_contract).  This candidate
-- teaches the one existing receipt seam the successor version instead of
-- creating a second slice-contract authority: the same identity, lease,
-- currentness, CAS, digest, receipt and authority pipeline, the same lock
-- order, the same guards, plus exactly the v2 surface.
--
-- WHAT IS PRESERVED EXACTLY
--
--   * Every statement of ops.engineering_record_slice_receipt is reproduced
--     verbatim from 0335 except the four marked V5-F03 hunks below.
--   * The lock order is unchanged: unlocked identifier lookup, then
--     capability_agent_session FOR UPDATE, actor FOR SHARE, advisory envelope
--     lock, envelope FOR KEY SHARE, slice plan FOR KEY SHARE, Work Request FOR
--     SHARE, job FOR UPDATE, job_attempt FOR UPDATE, post-lock currentness
--     sample, and the second pre-append currentness sample.  The new v2 checks
--     are pure JSON predicates over rows already read under those locks and
--     take no lock, no snapshot and no clock sample of their own.
--   * An engineering-slice-plan.v1 receipt is accepted and refused exactly as
--     before.  The v1 slice field set, the v1 typing rules and every v1 refusal
--     are byte-identical.
--
--     ONE REFUSAL TEXT MOVES, FOR NON-v1 PLANS ONLY.  Hunk 2 evaluates the
--     schema-version test before the plan exact_object block instead of inside
--     it, so a bound plan whose schema_version is neither v1 nor v2 now raises
--     'engineering receipt slice plan schema version is not supported' where
--     0335 raised '...is malformed or not bound to the envelope'.  No v1 receipt
--     changes outcome, and nothing in mcp-server/ or migrations/ matches on
--     either string, so this is refusal text only.
--
-- WHAT IS NEW (v2 only, all fail-closed)
--
--   1. The plan schema pin becomes an explicit closed membership test over
--      {engineering-slice-plan.v1, engineering-slice-plan.v2}.  Any other value,
--      a missing value, or a non-string value refuses.  Membership is NOT
--      loosened to "any schema_version".
--   2. The bound slice's exact field set gains 'design_contract' for v2 only.
--      A v2 slice missing design_contract, or a v1 slice carrying one, refuses.
--   3. For v2 the bound slice's design_contract is validated as the closed
--      Q046.D1 contract, including the frozen Q035.D1 design-depth predicate
--      selected by the contract's own sealed contract_version.  An unknown
--      design-contract version refuses; it is never defaulted.
--   4. For v2 the accepted routing/authority facets must describe the execution
--      binding recorded in the envelope this receipt is bound to.  This compares
--      and refuses only; it never widens, reissues or derives authority.
--   5. For v2 the WHOLE bound plan must be a valid typed slice plan, checked
--      against the already-locked ops.engineering_slice_plan row before the
--      append.  See the next section.
--
-- WHY THE WHOLE PLAN IS VALIDATED HERE (hunk 4), NOT ONLY AT REGISTRATION
--
-- 0335 validated exactly one slice, and hunks 2/3 kept that.  That is not
-- sufficient once hunk 2 exists, and register-time enforcement alone cannot make
-- it sufficient:
--
--   * 0310:399 grants EXECUTE on ops.engineering_register_slice_plan to
--     carr_writer, and 0310:196-204 checks only the five binding fields and
--     p_plan->>'plan_digest' <> p_plan_digest.  It never inspects 'slices' and
--     never re-derives the digest from content, so a carr_writer with a direct
--     SQL connection bypasses every plan-wide invariant AND the
--     digest-binds-content check.
--   * mcp-server/src/engineering-runtime.js already lists
--     'engineering-slice-plan.v2' as an accepted plan version, so v2 plans
--     register and dispatch today.
--   * The register-side replacement in the companion candidate binds FUTURE
--     registrations only.  Registration writes a new row; it never re-validates
--     a stored one.  The exposed set is therefore "every v2 plan already
--     stored", not "plans registered during the install window", and no install
--     ordering of the two files can shrink it.
--
-- Before hunk 2 that whole set was inert, because 0335:550 made any v2 plan
-- un-receiptable.  Hunk 2 removes that accidental fail-closed.  Hunk 4 replaces
-- it with a deliberate one: an already-stored bypass-registered v2 plan whose
-- BOUND slice happens to be well formed cannot append a receipt while the plan
-- carries duplicate ordinals, a dependency cycle, duplicate seam authority, a
-- parallel-safe resource collision, or a plan_digest that does not bind its own
-- canonical content.
--
-- IT STRANDS NOTHING.  0335:550 pinned this seam to engineering-slice-plan.v1,
-- so no v2 receipt can exist yet; hunk 4 can only refuse a NEW append, never
-- invalidate an issued receipt or a lineage that already closed.  That is
-- exactly why the two 0450 replacements in the companion candidate deliberately
-- do NOT add contract validation of their own: they read plans and lineages that
-- already exist, and this seam does not.
--
-- COST AND DUPLICATION, STATED.  The bound slice is validated twice for v2: once
-- by the receipt-specific bindings above (which the whole-plan predicate does
-- not perform -- it has no envelope, no receipt and no locked rows) and once
-- inside the whole-plan pass.  Both are kept.  The added cost is one whole-plan
-- pass, including one canonical-JSON digest recompute, per v2 receipt append.
--
-- The two further v1-exact slice pins at 0450:330 and 0450:433 named in the
-- header above are outside these two owned paths and are carried by that
-- companion candidate.
--
-- ORDINAL-TIME INTEGRATION PREREQUISITES (root decisions, deliberately not made
-- here, named so they are not discovered late)
--
--   a. db/schema.sql is a generated dump of the applied frontier.  Whoever
--      assigns this file a migration ordinal must regenerate it by the
--      repository's existing procedure; this file does not edit it.
--   b. THE CANONICAL-OWNERSHIP PRE-0431 FINGERPRINT ASSERTION WILL TRIP ON THIS
--      FILE.  ops/local-pg-ci.py provisions a second database, migrates it
--      through migrations/0431_completion_register_schema.sql, runs
--      ops/canonical-ownership-lease-local-pg-gate.py with --fingerprint-only,
--      and passes the result as CARR_OWNERSHIP_PRE_0450_FINGERPRINT to the
--      acceptance run against the frontier database, which compares them.  There
--      is no committed baseline: the "before" side is recomputed from the 0431
--      frontier on every run, so nothing needs regenerating -- but the compared
--      'functions' block records, per target, {definition (pg_get_functiondef,
--      byte for byte), owner, security_definer, config, acl}, and
--      ops.engineering_record_slice_receipt(uuid,uuid,jsonb,text,uuid) is one of
--      the five entries in that fingerprint's function_targets list
--      (ops/canonical-ownership-lease-local-pg-gate.py, CATALOG_FINGERPRINT_SQL).
--      No migration after 0431 replaces that function
--      today, which is precisely why the assertion currently holds and why this
--      file, once it carries an ordinal, changes exactly one compared field:
--      that function's definition.  owner, security_definer and config are
--      reproduced identically, and its acl is NOT affected -- CREATE OR REPLACE
--      preserves the ACL 0335:2129-2135 left on it (that revoke names
--      ops.engineering_record_slice_receipt at 0335:2131, and the grant at
--      0335:2136-2141 deliberately omits it, so it is owner-only), and the
--      revoke at the end of this file deliberately excludes it.
--      Reconciling that assertion is an integration step outside these four
--      paths.  DO NOT edit the ownership gate, normalize the comparison, or
--      weaken the pre-0431 baseline to make it pass: this file names the
--      requirement rather than fixing it.
--
--      TWO DIFFERENT LISTS, NOT ONE.  The gate's function_targets above is the
--      FINGERPRINT list: five functions whose definition and acl are COMPARED.
--      It is not the lease kernel's DARK list -- the eighteen functions
--      migrations/0450_canonical_ownership_lease_kernel.sql:1215-1233 revokes
--      from public and all four application roles so that nothing can CALL them.
--      The companion candidate replaces
--      ops.canonical_ownership_plan_dependencies(uuid,text) (0450:1224) and
--      ops.canonical_ownership_dependency_state(uuid,uuid,text,text) (0450:1225),
--      which are on the DARK list and NOT on the fingerprint list, plus
--      ops.engineering_register_slice_plan, which is on neither.  The companion
--      is therefore fingerprint-neutral -- it changes no compared definition or
--      acl -- while CREATE OR REPLACE separately preserves the dark ACL on those
--      two, leaving the lease kernel exactly as unreachable as 0450 left it.
--      Neither file adds, widens or narrows a grant on either list.

begin;

-- ---------------------------------------------------------------------------
-- Private predicate helpers for the SECURITY DEFINER receipt seam.
--
-- These follow the exact convention of the 0335 helpers at
-- migrations/0335_engineering_controller_currentness.sql:412-471: pure,
-- argument-only predicates that read no row and expose no data, each
-- substituting an empty JSON container before iterating so malformed caller
-- JSON is a false predicate rather than a set-return type error that could
-- bypass a fail-closed branch.
--
-- ACL.  The 0335 siblings are NOT left at PostgreSQL's default ACL: 0335:2114
-- strips EXECUTE from public and from all four application roles, and
-- migrations/0450_canonical_ownership_lease_kernel.sql:1215 applies the same
-- convention to every helper it adds.  CREATE OR REPLACE on a function that
-- does not yet exist lands it with the default EXECUTE to PUBLIC, and because
-- these ten are SECURITY DEFINER that would also be a PUBLIC-reachable indirect
-- call channel into the four 0335 helpers that were explicitly revoked.  The
-- matching `revoke all on function ... from public,carr_reader,carr_writer,
-- carr_jobs,carr_authority` for exactly these ten new functions is therefore at
-- the end of this file, immediately before COMMIT.  It restricts only the new
-- helpers: no existing grant is added, widened or narrowed anywhere here.
-- ---------------------------------------------------------------------------

create or replace function ops.engineering_receipt_design_string(p_value jsonb)
returns boolean language sql immutable strict security definer set search_path=pg_catalog,ops
as $$
  select jsonb_typeof(p_value)='string' and coalesce(btrim(p_value#>>'{}')<>'',false);
$$;

create or replace function ops.engineering_receipt_design_identifier(p_value jsonb)
returns boolean language sql immutable strict security definer set search_path=pg_catalog,ops
as $$
  select jsonb_typeof(p_value)='string'
     and coalesce((p_value#>>'{}') ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$',false);
$$;

create or replace function ops.engineering_receipt_design_enum(p_value jsonb,p_allowed text[])
returns boolean language sql immutable strict security definer set search_path=pg_catalog,ops
as $$
  select jsonb_typeof(p_value)='string'
     and coalesce((p_value#>>'{}')=any(p_allowed),false);
$$;

-- Every element of p_subset must appear in p_superset.  Both sides must already
-- be well-formed identifier arrays, so a malformed declaration can never widen a
-- subset test into a vacuous truth.  THE TWO SIDES ARE NOT THE SAME PREDICATE:
--
--   * p_subset -- the accepted isolation.shared_resource_refs -- must be UNIQUE.
--     Both source validators require exactly that of it
--     (isUniqueIdentifierArray in requireDesignContract, and the identical
--     uniqueness test in _validate_design_contract).
--   * p_superset -- the slice's declared_resource_refs -- allows DUPLICATES.
--     Both source validators check that array element by element and
--     deliberately do not require uniqueness (the declared-ref loop in
--     requirePlan, _ids in engineering_passport); see difference 2 in the header
--     of ops/f03-plan-ownership-validator.candidate.sql.  Requiring uniqueness
--     here would refuse AT PLAN REGISTRATION a plan both source validators
--     accept, because the whole-plan validator reaches this predicate through
--     ops.engineering_receipt_design_contract_refusal for every v2 slice.
--
-- The per-element superset test is written inline against
-- ops.engineering_receipt_design_identifier above -- the same closed regex
-- ops.engineering_plan_identifier_list applies -- rather than by calling that
-- companion-file helper.  This function is LANGUAGE sql and so IS parse-analysed
-- at CREATE, so calling across files here would make this file un-appliable
-- alone and would add a prerequisite to Part A of
-- mcp-server/test/f03-receipt-validator-postgres.sql.  It acquires no new
-- dependency.
--
-- THE RECEIPT SEAM IS UNAFFECTED.  It independently requires
-- declared_resource_refs to be a UNIQUE identifier array before the bound slice
-- ever reaches the design contract, and that guard is untouched here; this
-- changes acceptance only where the design contract is evaluated without it,
-- which is plan registration.
create or replace function ops.engineering_receipt_design_identifier_subset(p_subset jsonb,p_superset jsonb)
returns boolean language sql immutable strict security definer set search_path=pg_catalog,ops
as $$
  select coalesce(ops.engineering_receipt_identifier_array(p_subset),false)
     and jsonb_typeof(p_superset)='array'
     and not exists (
       select 1
         from jsonb_array_elements(case when jsonb_typeof(p_superset)='array' then p_superset else '[]'::jsonb end) value
        where not coalesce(ops.engineering_receipt_design_identifier(value),false)
     )
     and not exists (
       (select value#>>'{}'
          from jsonb_array_elements(case when jsonb_typeof(p_subset)='array' then p_subset else '[]'::jsonb end) value)
       except
       (select value#>>'{}'
          from jsonb_array_elements(case when jsonb_typeof(p_superset)='array' then p_superset else '[]'::jsonb end) value)
     );
$$;

-- The agent may never hand the design-depth classifier its own answer.  The
-- closed field sets already refuse unknown keys; this is the named refusal, and
-- it mirrors refuseSelfLabel / _refuse_self_label in the two source validators.
create or replace function ops.engineering_receipt_design_self_label_free(p_value jsonb)
returns boolean language sql immutable strict security definer set search_path=pg_catalog,ops
as $$
  select jsonb_typeof(p_value)='object'
     and not exists (
       select 1
         from jsonb_object_keys(case when jsonb_typeof(p_value)='object' then p_value else '{}'::jsonb end) as keys(key)
        where key=any(array['design_depth','depth','template','template_kind','complexity','complexity_class',
                            'simple','is_simple','classification','classifier_override','bypass'])
     );
$$;

-- Q029.D1: a non-empty unique set of accepted reasons, and cost may appear
-- alongside a capability reason but never alone.
create or replace function ops.engineering_receipt_design_selection_basis(p_value jsonb)
returns boolean language sql immutable strict security definer set search_path=pg_catalog,ops
as $$
  select jsonb_typeof(p_value)='array'
     and coalesce(case when jsonb_typeof(p_value)='array'
                       then jsonb_array_length(p_value)>0 else false end,false)
     and not exists (
       select 1
         from jsonb_array_elements(case when jsonb_typeof(p_value)='array' then p_value else '[]'::jsonb end) value
        where jsonb_typeof(value)<>'string'
           or not coalesce((value#>>'{}')=any(array[
                'typed_uncertainty','capability_gain','quality_gain','adaptability_gain','cost']),false)
     )
     and (select count(*)=count(distinct (value#>>'{}'))
            from jsonb_array_elements(case when jsonb_typeof(p_value)='array'
                                           then p_value else '[]'::jsonb end) value)
     and p_value<>'["cost"]'::jsonb;
$$;

-- Q016.D1: deterministic code owns identity, policy, permissions, state,
-- validation, idempotency and execution outright.  A model judgment step that
-- claims one of them is refused rather than reviewed, and every step must name a
-- plan step this slice actually declared.
create or replace function ops.engineering_receipt_design_model_steps(p_slice jsonb,p_steps jsonb)
returns boolean language sql immutable strict security definer set search_path=pg_catalog,ops
as $$
  select jsonb_typeof(p_steps)='array'
     and not exists (
       select 1
         from jsonb_array_elements(case when jsonb_typeof(p_steps)='array' then p_steps else '[]'::jsonb end) step
        where not coalesce(ops.engineering_receipt_exact_object(step,array[
                'input_contract_ref','output_contract_ref','rationale','responsibility_class',
                'selection_basis','step_ref']),false)
           or not coalesce(ops.engineering_receipt_design_identifier(step->'step_ref'),false)
           or not exists (
                select 1
                  from jsonb_array_elements(case when jsonb_typeof(p_slice->'declared_plan_step_refs')='array'
                                                 then p_slice->'declared_plan_step_refs' else '[]'::jsonb end) declared
                 where declared=step->'step_ref')
           or coalesce((step->>'responsibility_class')=any(array[
                'identity','policy','permissions','state','validation','idempotency','execution']),false)
           or not coalesce(ops.engineering_receipt_design_enum(step->'responsibility_class',array[
                'classification','extraction','summarization','ranking','drafting','disambiguation']),false)
           or not coalesce(ops.engineering_receipt_design_identifier(step->'input_contract_ref'),false)
           or not coalesce(ops.engineering_receipt_design_identifier(step->'output_contract_ref'),false)
           or not coalesce(ops.engineering_receipt_design_string(step->'rationale'),false)
           or not coalesce(ops.engineering_receipt_design_selection_basis(step->'selection_basis'),false)
     )
     and (select count(*)=count(distinct (step->>'step_ref'))
            from jsonb_array_elements(case when jsonb_typeof(p_steps)='array'
                                           then p_steps else '[]'::jsonb end) step);
$$;

-- The deterministic Q035.D1 design-depth classifier, frozen to the design
-- contract version that sealed it.
--
-- SHORT requires every accepted condition: R0-R3, parallel-safe, no manual QA,
-- no release requirement, zero dependencies, and at most one declared resource,
-- component and plan step.  Every other valid combination is FULL.
--
-- SHORT changes design-template depth only.  It grants no action authority,
-- waives no R0-R6 operating gate, reduces no verification and activates no
-- effect.
--
-- NULL means "not classifiable": an unsupported contract version, or a slice
-- whose classifier inputs are not exactly typed.  The caller treats NULL as a
-- refusal, so a future boundary must ship as an explicit successor contract
-- version with its own branch here rather than silently reclassifying an
-- already sealed, append-only plan.
create or replace function ops.engineering_receipt_design_depth(p_slice jsonb,p_contract_version text)
returns text language sql immutable strict security definer set search_path=pg_catalog,ops
as $$
  select case
    when p_contract_version<>'engineering-design-contract.v1' then null
    when not (
           coalesce(ops.engineering_receipt_design_enum(p_slice->'risk_class',
             array['R0','R1','R2','R3','R4','R5','R6']),false)
       and coalesce(ops.engineering_receipt_design_enum(p_slice->'concurrency_posture',
             array['parallel_safe','serial_after_dependencies','exclusive_resource']),false)
       and jsonb_typeof(p_slice->'manual_qa_required')='boolean'
       and coalesce(ops.engineering_receipt_design_enum(p_slice->'release_requirement',
             array['required','not_required']),false)
       and coalesce((select bool_and(jsonb_typeof(p_slice->field)='array')
                       from unnest(array['dependency_refs','declared_resource_refs',
                                         'declared_component_refs','declared_plan_step_refs']) field),false)
         ) then null
    when (p_slice->>'risk_class')=any(array['R0','R1','R2','R3'])
     and (p_slice->>'concurrency_posture')='parallel_safe'
     and p_slice->'manual_qa_required'='false'::jsonb
     and (p_slice->>'release_requirement')='not_required'
     and jsonb_array_length(p_slice->'dependency_refs')=0
     and jsonb_array_length(p_slice->'declared_resource_refs')<=1
     and jsonb_array_length(p_slice->'declared_component_refs')<=1
     and jsonb_array_length(p_slice->'declared_plan_step_refs')<=1
      then 'short'
    else 'full'
  end;
$$;

-- The closed Q046.D1 slice contract for one accepted engineering-slice-plan.v2
-- slice.  Returns NULL when the contract is valid, otherwise a stable refusal
-- token naming the exact facet that failed.  Every non-object, absent or
-- untyped input returns a token: there is no accepting default anywhere.
create or replace function ops.engineering_receipt_design_contract_refusal(p_slice jsonb)
returns text language plpgsql immutable security definer set search_path=pg_catalog,ops
as $$
declare contract jsonb; decision jsonb; steps jsonb; routing jsonb; authority jsonb;
        isolation jsonb; tests jsonb; review jsonb; failure jsonb; evidence jsonb;
        deployment jsonb; completion jsonb; seam jsonb; measurement jsonb;
        refs jsonb; template jsonb; depth text;
begin
  if jsonb_typeof(p_slice) is distinct from 'object' then return 'slice'; end if;
  if not coalesce(ops.engineering_receipt_design_self_label_free(p_slice),false) then
    return 'slice.design_depth_self_label';
  end if;
  contract := p_slice->'design_contract';
  if jsonb_typeof(contract) is distinct from 'object' then return 'design_contract'; end if;
  if not coalesce(ops.engineering_receipt_design_self_label_free(contract),false) then
    return 'design_contract.design_depth_self_label';
  end if;
  if not coalesce(ops.engineering_receipt_exact_object(contract,array[
       'authority','code_model_decision','completion','contract_version','dependency_rationale',
       'deployment','evidence','failure','full_design_refs','isolation','rationale','review',
       'routing','seam_decision','short_template','tests']),false) then
    return 'design_contract.shape';
  end if;
  -- An unknown design-contract version is refused, never defaulted: the frozen
  -- depth predicate is selected by the contract's own sealed version.
  if contract->>'contract_version' is distinct from 'engineering-design-contract.v1' then
    return 'design_contract.contract_version';
  end if;
  depth := ops.engineering_receipt_design_depth(p_slice,contract->>'contract_version');
  if depth is null then return 'design_contract.design_depth'; end if;
  if not coalesce(ops.engineering_receipt_design_string(contract->'rationale'),false) then
    return 'design_contract.rationale';
  end if;
  if not coalesce(ops.engineering_receipt_design_string(contract->'dependency_rationale'),false) then
    return 'design_contract.dependency_rationale';
  end if;

  decision := contract->'code_model_decision';
  if not coalesce(ops.engineering_receipt_exact_object(decision,array[
       'model_judgment_steps','rationale','selection_basis']),false) then
    return 'design_contract.code_model_decision';
  end if;
  if not coalesce(ops.engineering_receipt_design_string(decision->'rationale'),false) then
    return 'design_contract.code_model_decision.rationale';
  end if;
  if not coalesce(ops.engineering_receipt_design_selection_basis(decision->'selection_basis'),false) then
    return 'design_contract.code_model_decision.selection_basis';
  end if;
  steps := decision->'model_judgment_steps';
  if not coalesce(ops.engineering_receipt_design_model_steps(p_slice,steps),false) then
    return 'design_contract.code_model_decision.model_judgment_steps';
  end if;

  routing := contract->'routing';
  if not coalesce(ops.engineering_receipt_exact_object(routing,array[
       'adapter_ref','executor_class','fresh_session_required']),false) then
    return 'design_contract.routing';
  end if;
  if not coalesce(ops.engineering_receipt_design_enum(routing->'executor_class',array[
       'deterministic_code','attended_human','model_assisted']),false) then
    return 'design_contract.routing.executor_class';
  end if;
  if not coalesce(ops.engineering_receipt_design_identifier(routing->'adapter_ref'),false) then
    return 'design_contract.routing.adapter_ref';
  end if;
  if routing->'fresh_session_required' is distinct from 'true'::jsonb then
    return 'design_contract.routing.fresh_session_required';
  end if;
  if (routing->>'executor_class')='deterministic_code' and jsonb_array_length(steps)>0 then
    return 'design_contract.routing.executor_class';
  end if;
  if (routing->>'executor_class')='model_assisted' and jsonb_array_length(steps)=0 then
    return 'design_contract.routing.executor_class';
  end if;

  authority := contract->'authority';
  if not coalesce(ops.engineering_receipt_exact_object(authority,array[
       'capability_profile','environment','read_only']),false) then
    return 'design_contract.authority';
  end if;
  if not coalesce(ops.engineering_receipt_design_identifier(authority->'capability_profile'),false) then
    return 'design_contract.authority.capability_profile';
  end if;
  if jsonb_typeof(authority->'read_only') is distinct from 'boolean' then
    return 'design_contract.authority.read_only';
  end if;
  if not coalesce(ops.engineering_receipt_design_enum(authority->'environment',array[
       'local','rehearsal','staging','production']),false) then
    return 'design_contract.authority.environment';
  end if;
  if authority->'read_only'='false'::jsonb
     and (authority->>'capability_profile') is distinct from 'capability:engineering-repository-write' then
    return 'design_contract.authority.capability_profile';
  end if;

  isolation := contract->'isolation';
  if not coalesce(ops.engineering_receipt_exact_object(isolation,array[
       'branch_required','shared_resource_refs','worktree_required']),false) then
    return 'design_contract.isolation';
  end if;
  if isolation->'worktree_required' is distinct from 'true'::jsonb
     or isolation->'branch_required' is distinct from 'true'::jsonb then
    return 'design_contract.isolation.worktree_required';
  end if;
  if not coalesce(ops.engineering_receipt_design_identifier_subset(
       isolation->'shared_resource_refs',p_slice->'declared_resource_refs'),false) then
    return 'design_contract.isolation.shared_resource_refs';
  end if;
  if jsonb_array_length(isolation->'shared_resource_refs')>0
     and (p_slice->>'concurrency_posture')='parallel_safe' then
    return 'design_contract.isolation.shared_resource_refs';
  end if;

  tests := contract->'tests';
  if not coalesce(ops.engineering_receipt_exact_object(tests,array[
       'planned_check_refs','verification_lanes']),false) then
    return 'design_contract.tests';
  end if;
  if not coalesce(ops.engineering_receipt_identifier_array(tests->'planned_check_refs'),false) then
    return 'design_contract.tests.planned_check_refs';
  end if;
  -- Ordered equality, exactly as both source validators compare them: the
  -- contract binds the accepted planned checks in their accepted order.
  if (select array_agg(value#>>'{}' order by ord)
        from jsonb_array_elements(case when jsonb_typeof(tests->'planned_check_refs')='array'
                                       then tests->'planned_check_refs' else '[]'::jsonb end)
             with ordinality as bound_refs(value,ord))
     is distinct from
     (select array_agg(check_row->>'check_ref' order by ord)
        from jsonb_array_elements(case when jsonb_typeof(p_slice->'planned_checks')='array'
                                       then p_slice->'planned_checks' else '[]'::jsonb end)
             with ordinality as planned(check_row,ord)) then
    return 'design_contract.tests.planned_check_refs';
  end if;
  if jsonb_typeof(tests->'verification_lanes') is distinct from 'array'
     or coalesce(case when jsonb_typeof(tests->'verification_lanes')='array'
                      then jsonb_array_length(tests->'verification_lanes') else null end,0)=0
     or exists (
       select 1
         from jsonb_array_elements(case when jsonb_typeof(tests->'verification_lanes')='array'
                                        then tests->'verification_lanes' else '[]'::jsonb end) lane
        where jsonb_typeof(lane)<>'string'
           or not coalesce((lane#>>'{}')=any(array['unit','contract','integration','manual_qa']),false))
     or (select count(*)<>count(distinct (lane#>>'{}'))
           from jsonb_array_elements(case when jsonb_typeof(tests->'verification_lanes')='array'
                                          then tests->'verification_lanes' else '[]'::jsonb end) lane) then
    return 'design_contract.tests.verification_lanes';
  end if;
  if (tests->'verification_lanes' @> '["manual_qa"]'::jsonb)
     is distinct from (p_slice->'manual_qa_required'='true'::jsonb) then
    return 'design_contract.tests.verification_lanes';
  end if;

  review := contract->'review';
  if not coalesce(ops.engineering_receipt_exact_object(review,array[
       'independent_review_required','reviewer_class']),false) then
    return 'design_contract.review';
  end if;
  if review->'independent_review_required' is distinct from 'true'::jsonb then
    return 'design_contract.review.independent_review_required';
  end if;
  -- review-engineering-slice is the only reviewer provider this seam has and it
  -- records one independent automation actor's typed fact.  independent_human
  -- would seal a requirement into an immutable plan that no code can satisfy or
  -- refuse, so it stays refused until a human-review provider exists.
  if not coalesce(ops.engineering_receipt_design_enum(review->'reviewer_class',
       array['independent_agent']),false) then
    return 'design_contract.review.reviewer_class';
  end if;

  failure := contract->'failure';
  if not coalesce(ops.engineering_receipt_exact_object(failure,array['failure_modes']),false) then
    return 'design_contract.failure';
  end if;
  if jsonb_typeof(failure->'failure_modes') is distinct from 'array'
     or coalesce(case when jsonb_typeof(failure->'failure_modes')='array'
                      then jsonb_array_length(failure->'failure_modes') else null end,0)=0
     or exists (
       select 1
         from jsonb_array_elements(case when jsonb_typeof(failure->'failure_modes')='array'
                                        then failure->'failure_modes' else '[]'::jsonb end) mode
        where not coalesce(ops.engineering_receipt_exact_object(mode,array[
                'compensation','detection','failure_ref']),false)
           or not coalesce(ops.engineering_receipt_design_identifier(mode->'failure_ref'),false)
           or not coalesce(ops.engineering_receipt_design_string(mode->'detection'),false)
           or not coalesce(ops.engineering_receipt_design_string(mode->'compensation'),false))
     or (select count(*)<>count(distinct (mode->>'failure_ref'))
           from jsonb_array_elements(case when jsonb_typeof(failure->'failure_modes')='array'
                                          then failure->'failure_modes' else '[]'::jsonb end) mode) then
    return 'design_contract.failure.failure_modes';
  end if;

  evidence := contract->'evidence';
  if not coalesce(ops.engineering_receipt_exact_object(evidence,array[
       'evidence_refs','redaction_class','retention']),false) then
    return 'design_contract.evidence';
  end if;
  if not coalesce(ops.engineering_receipt_design_enum(evidence->'redaction_class',array[
       'metadata_only','redacted_evidence']),false) then
    return 'design_contract.evidence.redaction_class';
  end if;
  if not coalesce(ops.engineering_receipt_design_enum(evidence->'retention',array[
       'ephemeral','material_redacted']),false) then
    return 'design_contract.evidence.retention';
  end if;
  if not coalesce(ops.engineering_receipt_evidence_array(evidence->'evidence_refs'),false) then
    return 'design_contract.evidence.evidence_refs';
  end if;
  if exists (
       select 1
         from jsonb_array_elements(case when jsonb_typeof(evidence->'evidence_refs')='array'
                                        then evidence->'evidence_refs' else '[]'::jsonb end) item
        where (item->>'redaction_class') is distinct from (evidence->>'redaction_class')) then
    return 'design_contract.evidence.evidence_refs';
  end if;
  if exists (
       select 1
         from jsonb_array_elements(case when jsonb_typeof(p_slice->'planned_checks')='array'
                                        then p_slice->'planned_checks' else '[]'::jsonb end) planned_check
        where (planned_check->>'evidence_requirement')='redacted_evidence_required')
     and (evidence->>'redaction_class') is distinct from 'redacted_evidence' then
    return 'design_contract.evidence.redaction_class';
  end if;

  deployment := contract->'deployment';
  if not coalesce(ops.engineering_receipt_exact_object(deployment,array[
       'confirmation_required','release_requirement','rollback_ref']),false) then
    return 'design_contract.deployment';
  end if;
  if (deployment->>'release_requirement') is distinct from (p_slice->>'release_requirement') then
    return 'design_contract.deployment.release_requirement';
  end if;
  if (deployment->>'release_requirement')='required' then
    if not coalesce(ops.engineering_receipt_design_identifier(deployment->'rollback_ref'),false) then
      return 'design_contract.deployment.rollback_ref';
    end if;
  elsif deployment->'rollback_ref' is distinct from 'null'::jsonb
        and not coalesce(ops.engineering_receipt_design_identifier(deployment->'rollback_ref'),false) then
    return 'design_contract.deployment.rollback_ref';
  end if;
  if jsonb_typeof(deployment->'confirmation_required') is distinct from 'boolean' then
    return 'design_contract.deployment.confirmation_required';
  end if;
  -- The canonical explicit confirmation gate above R1 is retained, not waived.
  if not coalesce((p_slice->>'risk_class')=any(array['R0','R1']),false)
     and deployment->'confirmation_required' is distinct from 'true'::jsonb then
    return 'design_contract.deployment.confirmation_required';
  end if;

  completion := contract->'completion';
  if not coalesce(ops.engineering_receipt_exact_object(completion,array[
       'completion_predicate','verified_by']),false) then
    return 'design_contract.completion';
  end if;
  if not coalesce(ops.engineering_receipt_design_string(completion->'completion_predicate'),false) then
    return 'design_contract.completion.completion_predicate';
  end if;
  if not coalesce(ops.engineering_receipt_design_enum(completion->'verified_by',array[
       'independent_review','independent_review_and_manual_qa']),false) then
    return 'design_contract.completion.verified_by';
  end if;
  if (completion->>'verified_by') is distinct from
     (case when p_slice->'manual_qa_required'='true'::jsonb
           then 'independent_review_and_manual_qa' else 'independent_review' end) then
    return 'design_contract.completion.verified_by';
  end if;

  -- Q063.D1 / Q122.D1: extend a proven deep module or replace it cleanly; a new
  -- module needs a real seam, and a replacement may not leave residual
  -- authority behind.
  seam := contract->'seam_decision';
  if not coalesce(ops.engineering_receipt_exact_object(seam,array[
       'measurement','mode','new_module_justification','replaced_seam_refs',
       'residual_authority_refs','target_seam_ref']),false) then
    return 'design_contract.seam_decision';
  end if;
  if not coalesce(ops.engineering_receipt_design_enum(seam->'mode',array[
       'reuse','extend','replace','new_module']),false) then
    return 'design_contract.seam_decision.mode';
  end if;
  if not coalesce(ops.engineering_receipt_design_identifier(seam->'target_seam_ref'),false) then
    return 'design_contract.seam_decision.target_seam_ref';
  end if;
  measurement := seam->'measurement';
  if not coalesce(ops.engineering_receipt_exact_object(measurement,array['basis','note']),false)
     or not coalesce(ops.engineering_receipt_design_enum(measurement->'basis',array[
          'complexity_reduction','defect_rate','coverage','latency','operator_effort']),false)
     or not coalesce(ops.engineering_receipt_design_string(measurement->'note'),false) then
    return 'design_contract.seam_decision.measurement';
  end if;
  if not coalesce(ops.engineering_receipt_identifier_array(seam->'replaced_seam_refs'),false) then
    return 'design_contract.seam_decision.replaced_seam_refs';
  end if;
  if not coalesce(ops.engineering_receipt_identifier_array(seam->'residual_authority_refs'),false) then
    return 'design_contract.seam_decision.residual_authority_refs';
  end if;
  if (seam->>'mode')='new_module' then
    if not coalesce(ops.engineering_receipt_design_enum(seam->'new_module_justification',array[
         'authority','lifecycle','failure_isolation','multi_adapter']),false) then
      return 'design_contract.seam_decision.new_module_justification';
    end if;
  elsif seam->'new_module_justification' is distinct from 'null'::jsonb then
    return 'design_contract.seam_decision.new_module_justification';
  end if;
  if (seam->>'mode')='replace' then
    if jsonb_array_length(seam->'replaced_seam_refs')=0
       or seam->'replaced_seam_refs' @> jsonb_build_array(seam->'target_seam_ref') then
      return 'design_contract.seam_decision.replaced_seam_refs';
    end if;
    if jsonb_array_length(seam->'residual_authority_refs')>0 then
      return 'design_contract.seam_decision.residual_authority_refs';
    end if;
  else
    if jsonb_array_length(seam->'replaced_seam_refs')>0 then
      return 'design_contract.seam_decision.replaced_seam_refs';
    end if;
    if jsonb_array_length(seam->'residual_authority_refs')>0 then
      return 'design_contract.seam_decision.residual_authority_refs';
    end if;
  end if;

  -- Q035.D1 depth material.  The classifier decides which template the accepted
  -- slice must carry; the slice never decides for itself.
  if depth='full' then
    if contract->'short_template' is distinct from 'null'::jsonb then
      return 'design_contract.short_template';
    end if;
    refs := contract->'full_design_refs';
    if not coalesce(ops.engineering_receipt_exact_object(refs,array[
         'authority_envelope_ref','design_interview_ref','failure_model_ref','fixture_refs','oracle_ref']),false) then
      return 'design_contract.full_design_refs';
    end if;
    if exists (
         select 1
           from unnest(array['design_interview_ref','authority_envelope_ref','failure_model_ref','oracle_ref']) field
          where not coalesce(ops.engineering_receipt_design_identifier(refs->field),false)) then
      return 'design_contract.full_design_refs';
    end if;
    if not coalesce(ops.engineering_receipt_identifier_array(refs->'fixture_refs'),false)
       or coalesce(case when jsonb_typeof(refs->'fixture_refs')='array'
                        then jsonb_array_length(refs->'fixture_refs') else null end,0)=0 then
      return 'design_contract.full_design_refs.fixture_refs';
    end if;
  else
    if contract->'full_design_refs' is distinct from 'null'::jsonb then
      return 'design_contract.full_design_refs';
    end if;
    template := contract->'short_template';
    if not coalesce(ops.engineering_receipt_exact_object(template,array[
         'objective_summary','template_ref','verification_ref']),false) then
      return 'design_contract.short_template';
    end if;
    if not coalesce(ops.engineering_receipt_design_identifier(template->'template_ref'),false)
       or not coalesce(ops.engineering_receipt_design_identifier(template->'verification_ref'),false) then
      return 'design_contract.short_template';
    end if;
    if not coalesce(ops.engineering_receipt_design_string(template->'objective_summary'),false) then
      return 'design_contract.short_template.objective_summary';
    end if;
  end if;

  return null;
end $$;

-- The accepted v2 routing/authority facets must describe the execution binding
-- recorded in the exact envelope this receipt is bound to.  routing and
-- authority are sealed inside plan_digest at registration, and nothing at this
-- seam previously read them: a slice whose accepted contract said read-only
-- production work on a human desk could produce a receipt against a
-- write-capable rehearsal automation envelope with both statements recorded as
-- true.
--
-- This compares and refuses only.  It never selects an executor, never widens,
-- reissues or derives an envelope to match a contract, and grants nothing.  It
-- reads only the envelope row already loaded under the seam's existing locks.
-- Returns NULL when the contract describes the binding, else a refusal token.
create or replace function ops.engineering_receipt_design_binding_refusal(p_slice jsonb,p_envelope jsonb)
returns text language plpgsql immutable security definer set search_path=pg_catalog,ops
as $$
declare routing jsonb; authority jsonb; binding jsonb; mismatch_field text;
begin
  if jsonb_typeof(p_slice) is distinct from 'object'
     or jsonb_typeof(p_envelope) is distinct from 'object' then return 'envelope'; end if;
  routing := p_slice#>'{design_contract,routing}';
  authority := p_slice#>'{design_contract,authority}';
  binding := p_envelope->'server_binding';
  if jsonb_typeof(routing) is distinct from 'object'
     or jsonb_typeof(authority) is distinct from 'object'
     or jsonb_typeof(binding) is distinct from 'object'
     or jsonb_typeof(binding->'adapter') is distinct from 'object'
     or jsonb_typeof(binding->'authority') is distinct from 'object' then
    return 'envelope.server_binding';
  end if;
  -- Codex is the only executable adapter at this seam, so an accepted contract
  -- routed to an attended human names an executor no receipt here can come from.
  if not coalesce((routing->>'executor_class')=any(array['deterministic_code','model_assisted']),false) then
    return 'design_contract.routing.executor_class';
  end if;
  if binding#>>'{adapter,adapter_id}' is null
     or (routing->>'adapter_ref') is distinct from (binding#>>'{adapter,adapter_id}') then
    return 'design_contract.routing.adapter_ref';
  end if;
  -- environment, capability_profile and read_only are compared as jsonb, so a
  -- boolean false and the string "false" are not interchangeable.
  select fields.name into mismatch_field
    from unnest(array['environment','capability_profile','read_only']) with ordinality as fields(name,ord)
   where binding->'authority'->fields.name is null
      or authority->fields.name is distinct from binding->'authority'->fields.name
   order by fields.ord
   limit 1;
  if mismatch_field is not null then
    return 'design_contract.authority.'||mismatch_field;
  end if;
  return null;
end $$;

-- ---------------------------------------------------------------------------
-- The replaced receipt seam.
--
-- Reproduced verbatim from migrations/0335_engineering_controller_currentness.sql:473
-- except the four hunks marked "V5-F03".  Same name, same argument names and
-- types, same return type, so ops.engineering_finalize_slice_receipt (0335:793)
-- keeps resolving to it unchanged and a refused transition still rolls back the
-- receipt insert.  CREATE OR REPLACE retains the existing owner and ACL; no
-- grant is added, widened or narrowed.
-- ---------------------------------------------------------------------------

create or replace function ops.engineering_record_slice_receipt(p_envelope_id uuid,p_lease_token uuid,p_receipt jsonb,p_receipt_digest text,p_executor_actor_id uuid)
returns ops.engineering_slice_receipt language plpgsql security definer set search_path=pg_catalog,ops,public
as $$
declare e ops.engineering_execution_envelope%rowtype; s ops.capability_agent_session%rowtype;
        j ops.job%rowtype; a ops.job_attempt%rowtype; v_checked_at timestamptz; v_append_at timestamptz;
        session_executor uuid; session_slug text; receipt_plan_digest text; receipt_plan jsonb; receipt_slice jsonb;
        receipt_outcome text; slice_count integer;
        -- V5-F03 (1/4): the bound plan's schema version, the version-dependent
        -- slice field set, the design-contract refusal token, and the whole-plan
        -- refusal token (NULL when the bound plan itself is a valid typed plan).
        plan_schema_version text; slice_fields text[]; design_refusal text;
        plan_refusal text;
        row ops.engineering_slice_receipt%rowtype;
begin
  -- One atomic seam: lineage, session, job, receipt append, and terminal job
  -- transition all succeed together or roll back together.
  select * into e from ops.engineering_execution_envelope where id=p_envelope_id;
  if not found then raise exception 'engineering envelope not found'; end if;
  if p_lease_token is null then raise exception 'engineering claim or lease is not current'; end if;
  if jsonb_typeof(p_receipt) is distinct from 'object'
     or p_receipt->>'schema_version' is distinct from 'engineering-slice-receipt.v1'
     or p_receipt_digest is null or p_receipt_digest !~ '^sha256:[0-9a-f]{64}$'
     or p_receipt_digest is distinct from
        ('sha256:'||encode(public.digest(ops.guidance_import_canonical_json(p_receipt),'sha256'),'hex'))
     or not coalesce(ops.engineering_receipt_exact_object(p_receipt,array[
       'actual_component_refs','actual_resource_refs','artifact_refs','attribution','attempt_id','checks',
       'deviations','envelope_digest','evidence_refs','executor_claim','independent_verification_required',
       'outcome','plan_digest','planned_component_refs','planned_resource_refs','reset_reconstruction',
       'schema_version','slice_ref','source_evidence'
     ]),false) then
    raise exception 'engineering receipt is malformed';
  end if;
  -- The identifier lookup above is intentionally unlocked.  From here through
  -- append we retain the global session -> actor -> lineage lock order.
  select * into s from ops.capability_agent_session where id=e.agent_session_id for update;
  if not found then raise exception 'engineering agent session is not current'; end if;
  session_executor := s.executor_actor_id;
  select actor.slug into session_slug
    from public.actor actor
   where actor.id=s.executor_actor_id and actor.active and actor.kind='automation' and actor.slug='codex'
   order by actor.id for share;
  if not found then raise exception 'engineering executor actor is not current'; end if;
  perform pg_advisory_xact_lock(hashtextextended('engineering-envelope:' || e.slice_plan_id::text || ':' || e.slice_ref,0));
  select * into e from ops.engineering_execution_envelope where id=p_envelope_id for key share;
  if not found or e.agent_session_id is distinct from s.id then
    raise exception 'engineering envelope or agent session binding changed';
  end if;
  select plan_digest,plan into receipt_plan_digest,receipt_plan
    from ops.engineering_slice_plan where id=e.slice_plan_id for key share;
  if not found then raise exception 'engineering receipt slice plan is not current'; end if;
  perform 1 from ops.work_request where id=e.work_request_id for share;
  if not found then raise exception 'engineering Work Request is not current'; end if;
  select * into j from ops.job where id=e.job_id for update;
  if not found then raise exception 'engineering claim or lease is not current'; end if;
  select * into a from ops.job_attempt
   where job_id=j.id and attempt=j.attempt and lease_token is not distinct from p_lease_token
     and state is not distinct from 'running'
   for update;
  if not found then raise exception 'engineering claim or lease is not current'; end if;
  -- statement_timestamp() is fixed at function entry and can be stale after a
  -- session/actor/lineage/job lock wait.  Sample only after all ordered locks.
  v_checked_at := clock_timestamp();
  if j.state is distinct from 'running' or j.lease_token is distinct from p_lease_token
     or j.leased_until is null or j.leased_until<v_checked_at
     or e.expires_at<=v_checked_at or s.lease_expires_at is null or s.lease_expires_at<=v_checked_at
     or (s.state is distinct from 'claimed' and s.state is distinct from 'in_progress') then
    raise exception 'engineering claim, envelope, or agent-session lease is not current';
  end if;
  if not ops.engineering_envelope_is_executable(e.id,e.job_id) then raise exception 'engineering envelope is no longer executable'; end if;
  if session_executor is null or p_executor_actor_id is distinct from session_executor then raise exception 'engineering receipt executor is not the server-bound agent session'; end if;
  if not found or p_receipt->>'plan_digest' is distinct from receipt_plan_digest
     or p_receipt->>'envelope_digest' is distinct from e.envelope_digest
     or p_receipt->>'slice_ref' is distinct from e.slice_ref
     or p_receipt->>'attempt_id' is distinct from ('attempt:'||a.attempt) then
    raise exception 'engineering receipt is not bound to the claimed envelope';
  end if;
  -- V5-F03 (2/4): 0335 pinned the bound plan to engineering-slice-plan.v1 here,
  -- which made a registered v2 plan un-receiptable.  The pin becomes an explicit
  -- closed membership test over the two accepted slice-plan versions.  A missing,
  -- non-string or unknown schema_version refuses; membership is not loosened, and
  -- the rest of the plan surface below is unchanged for both versions.
  --
  -- DELETION, outside this marker.  The single line
  --   receipt_plan->>'schema_version' is distinct from 'engineering-slice-plan.v1'
  -- at 0335:550 is removed from inside the plan exact_object block reproduced
  -- further down, which begins after this marker ends.  That is the only
  -- statement removed from 0335 anywhere in this file; nothing else in that
  -- block changes.
  plan_schema_version := receipt_plan->>'schema_version';
  if not coalesce(plan_schema_version=any(array[
       'engineering-slice-plan.v1','engineering-slice-plan.v2']),false) then
    raise exception 'engineering receipt slice plan schema version is not supported';
  end if;
  slice_fields := array[
    'baseline_evidence_refs','concurrency_posture','declared_component_refs','declared_plan_step_refs',
    'declared_resource_refs','definition_of_done','dependency_refs','forbidden_change_refs','manual_qa_required',
    'objective','ordinal','planned_checks','release_requirement','risk_class','scope_boundary','slice_ref'];
  if plan_schema_version='engineering-slice-plan.v2' then
    slice_fields := slice_fields||'design_contract'::text;
  end if;
  -- The immutable slice-plan projection is the only declaration of what the
  -- direct receipt seam may claim.  Re-validate the narrow plan surface here
  -- rather than trusting a caller-side Passport validator.
  if not coalesce(ops.engineering_receipt_exact_object(receipt_plan,array[
       'accepted_plan_revision','plan_digest','schema_version','slices','work_request'
     ]),false)
     or receipt_plan->>'plan_digest' is distinct from receipt_plan_digest
     or not coalesce(ops.engineering_receipt_exact_object(receipt_plan->'work_request',array[
       'canonical_record_digest','id','state_version'
     ]),false)
     or receipt_plan->'work_request' is distinct from jsonb_build_object(
       'id','wr:'||e.work_request_id::text,
       'state_version',e.state_version,
       'canonical_record_digest',e.canonical_record_digest)
     or not coalesce(ops.engineering_receipt_exact_object(receipt_plan->'accepted_plan_revision',array[
       'digest','id','revision'
     ]),false)
     or receipt_plan->'accepted_plan_revision' is distinct from e.envelope->'plan_revision'
     or jsonb_typeof(receipt_plan->'slices') is distinct from 'array'
     or not coalesce(case when jsonb_typeof(receipt_plan->'slices')='array'
                          then jsonb_array_length(receipt_plan->'slices')>0 else false end,false) then
    raise exception 'engineering receipt slice plan is malformed or not bound to the envelope';
  end if;
  select count(*) into slice_count
    from jsonb_array_elements(case when jsonb_typeof(receipt_plan->'slices')='array'
                                   then receipt_plan->'slices' else '[]'::jsonb end) candidate
   where candidate->>'slice_ref'=e.slice_ref;
  if slice_count<>1 then
    raise exception 'engineering receipt slice plan does not name exactly one bound slice';
  end if;
  select candidate into receipt_slice
    from jsonb_array_elements(receipt_plan->'slices') candidate
   where candidate->>'slice_ref'=e.slice_ref;
  -- V5-F03 (3/4): the exact slice field set is now version-dependent.  For v1 it
  -- is byte-identical to 0335; for v2 it is the same 16 fields plus
  -- design_contract, so a v2 slice missing the contract and a v1 slice carrying
  -- one both refuse.  Every typing rule below is unchanged.
  if not coalesce(ops.engineering_receipt_exact_object(receipt_slice,slice_fields),false)
     or receipt_slice->>'slice_ref' is distinct from e.slice_ref
     or jsonb_typeof(receipt_slice->'ordinal') is distinct from 'number'
     or not coalesce((receipt_slice->>'ordinal') ~ '^[1-9][0-9]*$',false)
     or exists (select 1 from unnest(array['objective','definition_of_done','scope_boundary']) field
                 where jsonb_typeof(receipt_slice->field) is distinct from 'string'
                    or not coalesce(btrim(receipt_slice->>field)<>'',false))
     or not coalesce(ops.engineering_receipt_identifier_array(receipt_slice->'dependency_refs'),false)
     or not coalesce(ops.engineering_receipt_identifier_array(receipt_slice->'declared_resource_refs'),false)
     or not coalesce(ops.engineering_receipt_identifier_array(receipt_slice->'declared_component_refs'),false)
     or not coalesce(ops.engineering_receipt_identifier_array(receipt_slice->'declared_plan_step_refs'),false)
     or not coalesce(ops.engineering_receipt_identifier_array(receipt_slice->'forbidden_change_refs'),false)
     or not coalesce(ops.engineering_receipt_evidence_array(receipt_slice->'baseline_evidence_refs'),false)
     or jsonb_typeof(receipt_slice->'planned_checks') is distinct from 'array'
     or not coalesce(case when jsonb_typeof(receipt_slice->'planned_checks')='array'
                          then jsonb_array_length(receipt_slice->'planned_checks')>0 else false end,false)
     or exists (
       select 1
         from jsonb_array_elements(case when jsonb_typeof(receipt_slice->'planned_checks')='array'
                                        then receipt_slice->'planned_checks' else '[]'::jsonb end) planned_check
        where not coalesce(ops.engineering_receipt_exact_object(planned_check,array[
                'check_ref','evidence_requirement','failure_condition'
              ]),false)
           or not coalesce((planned_check->>'check_ref') ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$',false)
           or jsonb_typeof(planned_check->'failure_condition') is distinct from 'string'
           or not coalesce(btrim(planned_check->>'failure_condition')<>'',false)
           or not coalesce(planned_check->>'evidence_requirement'=any(array['redacted_evidence_required','metadata_only_sufficient']),false)
     )
     or exists (
       select 1 from jsonb_array_elements(receipt_slice->'planned_checks') planned_check
       group by planned_check->>'check_ref' having count(*)>1
     )
     or not coalesce(receipt_slice->>'concurrency_posture'=any(array['parallel_safe','serial_after_dependencies','exclusive_resource']),false)
     or jsonb_typeof(receipt_slice->'manual_qa_required') is distinct from 'boolean'
     or not coalesce(receipt_slice->>'risk_class'=any(array['R0','R1','R2','R3','R4','R5','R6']),false)
     or not coalesce(receipt_slice->>'release_requirement'=any(array['required','not_required']),false) then
    raise exception 'engineering receipt bound slice plan is not fully typed';
  end if;
  -- V5-F03 (4/4): the successor version's additional surface at the existing
  -- seam.  All three predicates are pure JSON over rows already read under the
  -- locks taken above; none takes a lock, samples a clock, reads another table,
  -- or writes anything.  A v1 receipt never reaches this branch.
  --
  -- The first two validate the ONE bound slice, exactly as 0335 validated the
  -- one bound slice.  The third validates the WHOLE stored plan, and it is here
  -- because the bound-slice checks structurally cannot reach a plan-wide fault:
  -- see WHY THE WHOLE PLAN IS VALIDATED HERE in the header.  receipt_plan is the
  -- row already read at the slice-plan FOR KEY SHARE above, so no additional row
  -- is read and the lock order is unchanged.
  if plan_schema_version='engineering-slice-plan.v2' then
    design_refusal := ops.engineering_receipt_design_contract_refusal(receipt_slice);
    if design_refusal is not null then
      raise exception 'engineering receipt bound slice design contract is invalid: %',design_refusal;
    end if;
    design_refusal := ops.engineering_receipt_design_binding_refusal(receipt_slice,e.envelope);
    if design_refusal is not null then
      raise exception 'engineering receipt bound slice design contract contradicts the server execution binding: %',design_refusal;
    end if;
    -- The whole-plan predicate lives in ops/f03-plan-ownership-validator.candidate.sql
    -- and is the SAME single statement the registration seam uses; it is called
    -- here, never restated.  If that file has not been applied this raises
    -- 'function ops.engineering_slice_plan_refusal(jsonb) does not exist' and the
    -- append is refused -- the half-installed state fails closed in either
    -- install order.  The revoke at the end of that file does not block this
    -- call: ops.engineering_record_slice_receipt is SECURITY DEFINER, so the
    -- call is made as ITS owner, and that owner is the same migration role that
    -- creates the whole-plan validator when both files are applied normally --
    -- an owner's own EXECUTE is not what a revoke from public and the four
    -- application roles removes.  Applying the two files as DIFFERENT roles
    -- would need an explicit grant and is not an intended installation.
    plan_refusal := ops.engineering_slice_plan_refusal(receipt_plan);
    if plan_refusal is not null then
      raise exception 'engineering receipt bound slice plan is not a valid typed slice plan: %',plan_refusal;
    end if;
  end if;
  receipt_outcome := p_receipt->>'outcome';
  if receipt_outcome is null or not coalesce(receipt_outcome=any(array['claimed_complete','failed','blocked','reopened']),false) then
    raise exception 'engineering receipt outcome is invalid';
  end if;
  if not coalesce(ops.engineering_receipt_identifier_array(p_receipt->'planned_resource_refs'),false)
     or not coalesce(ops.engineering_receipt_identifier_array(p_receipt->'actual_resource_refs'),false)
     or not coalesce(ops.engineering_receipt_identifier_array(p_receipt->'planned_component_refs'),false)
     or not coalesce(ops.engineering_receipt_identifier_array(p_receipt->'actual_component_refs'),false)
     or not coalesce(ops.engineering_receipt_identifier_array(p_receipt->'artifact_refs'),false)
     or not coalesce(ops.engineering_receipt_evidence_array(p_receipt->'evidence_refs'),false)
     or jsonb_typeof(p_receipt->'checks') is distinct from 'array'
     or not coalesce(case when jsonb_typeof(p_receipt->'checks')='array'
                          then jsonb_array_length(p_receipt->'checks')>0 else false end,false)
     or exists (
       select 1
         from jsonb_array_elements(case when jsonb_typeof(p_receipt->'checks')='array'
                                        then p_receipt->'checks' else '[]'::jsonb end) receipt_check
        where not coalesce(ops.engineering_receipt_exact_object(receipt_check,array[
                'check_ref','evidence_refs','state'
              ]),false)
           or not coalesce((receipt_check->>'check_ref') ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$',false)
           or not coalesce(receipt_check->>'state'=any(array['passed','failed','blocked','not_run']),false)
           or not coalesce(ops.engineering_receipt_evidence_array(receipt_check->'evidence_refs'),false)
     )
     or exists (
       select 1 from jsonb_array_elements(p_receipt->'checks') receipt_check
       group by receipt_check->>'check_ref' having count(*)>1
     )
     or exists (
       select 1 from jsonb_array_elements(p_receipt->'checks') receipt_check
        where not exists (
          select 1 from jsonb_array_elements(receipt_slice->'planned_checks') planned_check
           where planned_check->>'check_ref'=receipt_check->>'check_ref'
        )
     )
     or exists (
       select 1 from jsonb_array_elements(receipt_slice->'planned_checks') planned_check
        where not exists (
          select 1 from jsonb_array_elements(p_receipt->'checks') receipt_check
           where receipt_check->>'check_ref'=planned_check->>'check_ref'
        )
     )
     or exists (
       select 1
         from jsonb_array_elements(p_receipt->'checks') receipt_check
         join jsonb_array_elements(receipt_slice->'planned_checks') planned_check
           on planned_check->>'check_ref'=receipt_check->>'check_ref'
        where receipt_check->>'state'='passed'
          and (jsonb_array_length(receipt_check->'evidence_refs')=0
               or not exists (
                 select 1 from jsonb_array_elements(receipt_check->'evidence_refs') evidence
                  where evidence->>'redaction_class'=case planned_check->>'evidence_requirement'
                    when 'redacted_evidence_required' then 'redacted_evidence' else 'metadata_only' end
               ))
     )
     or jsonb_typeof(p_receipt->'deviations') is distinct from 'array'
     or exists (
       select 1
         from jsonb_array_elements(case when jsonb_typeof(p_receipt->'deviations')='array'
                                        then p_receipt->'deviations' else '[]'::jsonb end) deviation
        where not coalesce(ops.engineering_receipt_exact_object(deviation,array[
                'category','deviation_ref','evidence_refs','impact','out_of_scope_component_refs',
                'out_of_scope_resource_refs','plan_revision_required','reason','review_state'
              ]),false)
           or not coalesce((deviation->>'deviation_ref') ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$',false)
           or exists (select 1 from unnest(array['category','reason','impact']) field
                       where jsonb_typeof(deviation->field) is distinct from 'string'
                          or not coalesce(btrim(deviation->>field)<>'',false))
           or jsonb_typeof(deviation->'plan_revision_required') is distinct from 'boolean'
           or not coalesce(ops.engineering_receipt_evidence_array(deviation->'evidence_refs'),false)
           or not coalesce(ops.engineering_receipt_identifier_array(deviation->'out_of_scope_resource_refs'),false)
           or not coalesce(ops.engineering_receipt_identifier_array(deviation->'out_of_scope_component_refs'),false)
           or not coalesce(deviation->>'review_state'=any(array['unreviewed','reviewed','resolved']),false)
     )
     or exists (
       select 1 from jsonb_array_elements(p_receipt->'deviations') deviation
       group by deviation->>'deviation_ref' having count(*)>1
     )
     or not coalesce(ops.engineering_receipt_identifier_sets_equal(
          p_receipt->'planned_resource_refs',receipt_slice->'declared_resource_refs'),false)
     or not coalesce(ops.engineering_receipt_identifier_sets_equal(
          p_receipt->'planned_component_refs',receipt_slice->'declared_component_refs'),false)
     or exists (
       select 1 from jsonb_array_elements(p_receipt->'actual_resource_refs') actual_ref
        where not exists (
          select 1 from jsonb_array_elements(receipt_slice->'declared_resource_refs') declared_ref
           where declared_ref=actual_ref
        ) and not exists (
          select 1 from jsonb_array_elements(p_receipt->'deviations') deviation,
                        jsonb_array_elements(deviation->'out_of_scope_resource_refs') approved_ref
           where deviation->>'review_state'='resolved' and approved_ref=actual_ref
        )
     )
     or exists (
       select 1 from jsonb_array_elements(p_receipt->'actual_component_refs') actual_ref
        where not exists (
          select 1 from jsonb_array_elements(receipt_slice->'declared_component_refs') declared_ref
           where declared_ref=actual_ref
        ) and not exists (
          select 1 from jsonb_array_elements(p_receipt->'deviations') deviation,
                        jsonb_array_elements(deviation->'out_of_scope_component_refs') approved_ref
           where deviation->>'review_state'='resolved' and approved_ref=actual_ref
        )
     )
     or (receipt_outcome='claimed_complete' and (
       jsonb_array_length(p_receipt->'artifact_refs')=0
       or jsonb_array_length(p_receipt->'evidence_refs')=0
       or exists (select 1 from jsonb_array_elements(p_receipt->'checks') receipt_check
                   where receipt_check->>'state' is distinct from 'passed')
     ))
     or not coalesce(ops.engineering_receipt_exact_object(p_receipt->'source_evidence',array[
          'branch_ref','evidence_refs','source_sha','worktree_ref'
        ]),false)
     or exists (select 1 from unnest(array['worktree_ref','branch_ref','source_sha']) field
                 where jsonb_typeof(p_receipt->'source_evidence'->field) is distinct from 'string'
                    or not coalesce(btrim(p_receipt->'source_evidence'->>field)<>'',false)
                    or (field<>'source_sha' and not coalesce(
                         (p_receipt->'source_evidence'->>field) ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$',false)))
     or not coalesce(ops.engineering_receipt_evidence_array(p_receipt->'source_evidence'->'evidence_refs'),false)
     or not coalesce(ops.engineering_receipt_exact_object(p_receipt->'reset_reconstruction',array[
          'fresh_session','inherited_transcript_used','reconstruction_free','remediation_action'
        ]),false)
     or p_receipt->'reset_reconstruction'->'fresh_session' is distinct from 'true'::jsonb
     or p_receipt->'reset_reconstruction'->'inherited_transcript_used' is distinct from 'false'::jsonb
     or jsonb_typeof(p_receipt->'reset_reconstruction'->'reconstruction_free') is distinct from 'boolean'
     or (p_receipt->'reset_reconstruction'->'reconstruction_free'='false'::jsonb and
         (jsonb_typeof(p_receipt->'reset_reconstruction'->'remediation_action') is distinct from 'string'
          or not coalesce(btrim(p_receipt->'reset_reconstruction'->>'remediation_action')<>'',false)))
     or (p_receipt->'reset_reconstruction'->'reconstruction_free'='true'::jsonb and
         p_receipt->'reset_reconstruction'->'remediation_action' is distinct from 'null'::jsonb
         and (jsonb_typeof(p_receipt->'reset_reconstruction'->'remediation_action') is distinct from 'string'
              or not coalesce(btrim(p_receipt->'reset_reconstruction'->>'remediation_action')<>'',false)))
     or not coalesce(ops.engineering_receipt_exact_object(p_receipt->'executor_claim',array[
          'claim_state','claimed_at','claimed_by'
        ]),false)
     or p_receipt->'executor_claim'->>'claim_state' is distinct from 'executor_claim'
     or not coalesce((p_receipt->'executor_claim'->>'claimed_by') ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$',false)
     or jsonb_typeof(p_receipt->'executor_claim'->'claimed_at') is distinct from 'string'
     or not coalesce(btrim(p_receipt->'executor_claim'->>'claimed_at')<>'',false)
     or p_receipt->'independent_verification_required' is distinct from 'true'::jsonb
     or not coalesce(ops.engineering_receipt_exact_object(p_receipt->'attribution',array[
          'actor_ref','adapter_ref','session_ref'
        ]),false)
     or exists (select 1 from unnest(array['actor_ref','session_ref','adapter_ref']) field
                 where not coalesce((p_receipt->'attribution'->>field) ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$',false))
     or session_slug is null or p_receipt->'executor_claim'->>'claimed_by' is distinct from session_slug
     or e.envelope#>>'{server_binding,identity,agent_principal_id}' is null
     or e.envelope#>>'{agent_session,id}' is null
     or e.envelope#>>'{server_binding,adapter,adapter_id}' is null
     or p_receipt->'attribution'->>'actor_ref' is distinct from e.envelope#>>'{server_binding,identity,agent_principal_id}'
     or p_receipt->'attribution'->>'session_ref' is distinct from e.envelope#>>'{agent_session,id}'
     or p_receipt->'attribution'->>'adapter_ref' is distinct from e.envelope#>>'{server_binding,adapter,adapter_id}' then
    raise exception 'engineering receipt typed contract is invalid';
  end if;
  -- No receipt may cross the append boundary on authority sampled only before
  -- JSON validation.  All rows remain locked from the first ordered check.
  v_append_at := clock_timestamp();
  if j.state is distinct from 'running' or j.lease_token is distinct from p_lease_token
     or j.leased_until is null or j.leased_until<v_append_at
     or e.expires_at<=v_append_at or s.lease_expires_at is null or s.lease_expires_at<=v_append_at
     or (s.state is distinct from 'claimed' and s.state is distinct from 'in_progress') then
    raise exception 'engineering claim, envelope, or agent-session lease is not current at receipt append';
  end if;
  if not ops.engineering_envelope_is_executable(e.id,e.job_id) then
    raise exception 'engineering envelope is no longer executable at receipt append';
  end if;
  insert into ops.engineering_slice_receipt(job_attempt_id,envelope_id,work_request_id,slice_ref,attempt_id,executor_actor_id,receipt_digest,outcome,receipt) values(a.id,e.id,e.work_request_id,e.slice_ref,p_receipt->>'attempt_id',session_executor,p_receipt_digest,p_receipt->>'outcome',p_receipt) returning * into row;
  return row;
end $$;

comment on function ops.engineering_record_slice_receipt(uuid,uuid,jsonb,text,uuid) is
  'Lease-bound typed engineering slice receipt append. Accepts engineering-slice-plan.v1 '
  'exactly as before and engineering-slice-plan.v2 with its closed per-slice design_contract. '
  'For v2 the whole bound slice plan must also be a valid typed plan under '
  'ops.engineering_slice_plan_refusal, so a plan registered around the caller-side '
  'validators cannot become receiptable. Unknown slice-plan or design-contract versions are '
  'refused, never defaulted.';

-- ---------------------------------------------------------------------------
-- Strip the default PUBLIC EXECUTE from the ten new SECURITY DEFINER helpers,
-- exactly as migrations/0335_engineering_controller_currentness.sql:2114 does
-- for ops.engineering_receipt_exact_object / _identifier_array /
-- _identifier_sets_equal / _evidence_array, and as
-- migrations/0450_canonical_ownership_lease_kernel.sql:1215 does for its own
-- helpers.
--
-- This names ONLY functions this file creates.  It touches no pre-existing
-- function, no table, no sequence and no role, and it widens nothing: every
-- listed function goes from PostgreSQL's implicit default (EXECUTE to PUBLIC)
-- to no EXECUTE for anyone but the owner.  ops.engineering_record_slice_receipt
-- is deliberately absent: it already exists, CREATE OR REPLACE preserved its
-- owner and the owner-only ACL the revoke at 0335:2129-2135 left on it (it is
-- named at 0335:2131 and is absent from the grant at 0335:2136-2141), and
-- re-revoking it here would be a change to an existing grant.
-- ---------------------------------------------------------------------------
revoke all on function
  ops.engineering_receipt_design_string(jsonb),
  ops.engineering_receipt_design_identifier(jsonb),
  ops.engineering_receipt_design_enum(jsonb,text[]),
  ops.engineering_receipt_design_identifier_subset(jsonb,jsonb),
  ops.engineering_receipt_design_self_label_free(jsonb),
  ops.engineering_receipt_design_selection_basis(jsonb),
  ops.engineering_receipt_design_model_steps(jsonb,jsonb),
  ops.engineering_receipt_design_depth(jsonb,text),
  ops.engineering_receipt_design_contract_refusal(jsonb),
  ops.engineering_receipt_design_binding_refusal(jsonb,jsonb)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;

commit;
