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

-- f03-plan-ownership-validator.candidate.sql
--
-- CANDIDATE SOURCE.  THIS IS NOT A MIGRATION.
--
--   * It deliberately carries NO migration ordinal and lives outside
--     migrations/.  Assigning an ordinal, sequencing it against the migration
--     frontier, and applying it anywhere other than an isolated scratch
--     database are root decisions this file does not make and must not imply.
--   * It is forward-only: no issued plan, receipt, envelope, lease or reviewer
--     fact is rewritten, no historical migration is edited, and no grant, role
--     or job/Work Request record is created or relaxed.
--   * It is reviewable source pending independent review, checks and
--     integration.  Nothing here has been executed.
--
-- ===========================================================================
-- INSTALLATION: BOTH FILES ARE REQUIRED, AND THE ORDER BETWEEN THEM IS FREE
-- ===========================================================================
--
--   1. migrations/ applied through 0450_canonical_ownership_lease_kernel.sql.
--   2. ops/f03-receipt-validator.candidate.sql  and  THIS FILE, in either order.
--
-- This file calls, and never redefines:
--
--     ops.engineering_receipt_design_contract_refusal(jsonb)   [receipt candidate]
--     ops.engineering_receipt_design_self_label_free(jsonb)    [receipt candidate]
--     ops.engineering_receipt_exact_object(jsonb,text[])       [0335:412]
--     ops.engineering_receipt_identifier_array(jsonb)          [0335:422]
--     ops.engineering_receipt_evidence_array(jsonb)            [0335:458]
--     ops.canonical_ownership_refusal(text,text,jsonb,jsonb)   [0450:116]
--     ops.guidance_import_canonical_json(jsonb)                [db/schema.sql:9274]
--
-- and the receipt candidate's v2 receipt branch calls, and never redefines,
-- ops.engineering_slice_plan_refusal(jsonb) from THIS file.  The dependency is
-- therefore mutual.
--
-- IT IS RESOLVED AT EXECUTION, NOT AT CREATE.  An earlier draft of this header
-- claimed that applying this file without the receipt candidate "fails
-- immediately at CREATE with function ... does not exist".  That is not how
-- plpgsql creation works and the claim is withdrawn: check_function_bodies only
-- SYNTAX-checks a plpgsql body, and the SQL statements inside it are not
-- parse-analysed until the function runs.  ops.engineering_slice_plan_refusal
-- (LANGUAGE plpgsql, below) and both replaced plpgsql seams therefore CREATE
-- cleanly with the other file absent.  The two LANGUAGE sql helpers this file
-- adds ARE analysed at creation, but they call nothing from the receipt
-- candidate, so they create cleanly too.
--
-- Both half-installed states still fail closed, at first use rather than at
-- CREATE, which is why the order is free:
--
--   * this file alone: a v2 plan REGISTRATION raises 'function
--     ops.engineering_receipt_design_contract_refusal(jsonb) does not exist' and
--     is refused.  v1 registration is unchanged.
--   * the receipt candidate alone: a v2 receipt APPEND raises 'function
--     ops.engineering_slice_plan_refusal(jsonb) does not exist' and is refused.
--     v1 append is unchanged.
--
-- The closed Q046.D1 design contract keeps exactly ONE statement, in the receipt
-- candidate, and this file reuses it rather than restating it.  Symmetrically,
-- the whole-plan contract keeps exactly ONE statement, here, and the receipt
-- seam reuses it.  Neither file is usable alone for engineering-slice-plan.v2,
-- and neither can half-open a door on its own.
--
-- ===========================================================================
-- WHAT IT FIXES
-- ===========================================================================
--
-- 1. THE REGISTER-TIME BYPASS.
--
-- ops.engineering_register_slice_plan
-- (migrations/0310_engineering_execution_fabric.sql:185) checks only the five
-- binding fields at :197-201 and p_plan->>'plan_digest' <> p_plan_digest at
-- :202.  It never inspects p_plan->'slices' and never re-derives the digest
-- from plan content.  0310:399 grants EXECUTE on it to carr_writer.
--
-- Every plan-wide engineering-slice-plan.v2 invariant therefore lives only in
-- callers: requirePlan in mcp-server/src/engineering-runtime.js and
-- validate_engineering_slice_plan in
-- tools/room-bridge/engineering_passport.py.  Both are cited by SYMBOL
-- throughout this header: those two files move, and a line number in a comment
-- that nothing executes drifts silently into a false citation.  Migration
-- ordinals are cited by line, because a committed migration never changes.
-- A carr_writer holding a direct
-- SQL connection bypasses all of them.  Until now that bypass was largely inert,
-- because 0335:550 pinned the receipt seam to engineering-slice-plan.v1 and no
-- v2 plan could ever produce a receipt.  ops/f03-receipt-validator.candidate.sql
-- teaches that seam v2 and so removes an accidental fail-closed.
--
-- This file moves those invariants into the database at the one existing
-- registration seam, as a forward-only replacement of the same function.
--
-- REGISTRATION ALONE IS NOT ENOUGH, AND NO INSTALL ORDER MAKES IT ENOUGH.  This
-- seam binds FUTURE registrations only: registration writes a new row and never
-- re-reads or re-validates a stored one, and this file deliberately does not
-- re-validate stored plans (see NO RETROACTIVE REWRITE below).  Because
-- engineering-runtime.js already accepts 'engineering-slice-plan.v2', v2 plans
-- register and dispatch today, so the exposed set is "every v2 plan already
-- stored", which nothing at this seam can reach.  That is why the receipt
-- candidate's v2 branch calls ops.engineering_slice_plan_refusal over the
-- already-locked stored plan row before it appends: the whole-plan contract is
-- enforced at BOTH existing boundaries, from this one statement.  The receipt
-- seam's own bound-slice checks structurally cannot do it -- they read exactly
-- one slice -- and this seam structurally cannot cover stored rows.  Neither
-- boundary is redundant and neither is a substitute for the other.
--
-- 2. THE TWO LATER v1-EXACT SLICE PINS.
--
-- migrations/0450_canonical_ownership_lease_kernel.sql:330
-- (ops.canonical_ownership_plan_dependencies) and :433
-- (ops.canonical_ownership_dependency_state) both call
-- ops.engineering_receipt_exact_object(slice, <the 16 v1 fields>) over the same
-- ops.engineering_slice_plan.plan->'slices' rows.  A v2 slice carries a 17th
-- key, design_contract, so both refuse it (SLICE_PLAN_BINDING_STALE /
-- malformed_dependencies), and 0450:828, :848, :1048 and :1059 reach them for
-- the SUBJECT slice, not only for its dependencies.  Replacing only the receipt
-- validator moves the wall rather than removing it.  This file carries the
-- minimal forward-only version-aware replacement of both.
--
-- ===========================================================================
-- WHAT IS PRESERVED EXACTLY
-- ===========================================================================
--
--   * ops.engineering_register_slice_plan keeps its name, argument names and
--     types, return type, LANGUAGE, SECURITY DEFINER, search_path and body.
--     Every existing statement is reproduced verbatim from 0310:185-220 --
--     the admission-source lookup, the digest-format check, the five binding
--     comparisons and their exact refusal text, the insert column list, the
--     ON CONFLICT (idempotency_key) DO NOTHING replay, and the
--     idempotency-key-reuse guard.  One block is inserted, marked V5-F03.
--     CREATE OR REPLACE retains the existing owner and ACL, so the 0310:399
--     grant to carr_writer is neither widened nor narrowed.
--   * Both 0450 functions keep their name, arguments, return type, LANGUAGE,
--     volatility, SECURITY DEFINER and search_path, and every dependency, lease,
--     lock-ordering, currentness and authority rule in them is reproduced
--     verbatim.  Exactly one expression changes in each: the hard-coded 16-field
--     array becomes the version-dependent field set.  Their 0450:1224-1225
--     revocation from every role is preserved by CREATE OR REPLACE, so the lease
--     kernel stays dark.
--   * An engineering-slice-plan.v1 plan registers exactly as before.  See the
--     v1 boundary below for the two narrow exceptions and why neither can change
--     the outcome of any plan that was ever usable.
--
-- ===========================================================================
-- THE v1 BOUNDARY (read this before assigning an ordinal)
-- ===========================================================================
--
-- ops.engineering_slice_plan_refusal returns NULL -- accept -- for
-- engineering-slice-plan.v1 as soon as it has established the plan's top-level
-- shape and version.  v1 plans are NOT put through the new whole-plan rules.
-- That is deliberate and it matches the server validator's own documented
-- divergence (the "DELIBERATE LEGACY v1 DIVERGENCES" note above
-- ENGINEERING_SLICE_PLAN_VERSIONS in engineering-runtime.js): registered plans
-- are append-only and requirePlan re-runs against the STORED row on every read
-- path (sourcePlan, closureProjection, controllerPlan), so newly
-- refusing a shape for v1 would strand an already registered plan rather than
-- correct it.  Duplicate ordinals and dependency cycles are enforced for v2
-- only in JS for exactly this reason, and this file does not go further.
--
-- Exactly two refusals are therefore newly visible to a non-v2 plan:
--
--   a. schema_version outside {engineering-slice-plan.v1,
--      engineering-slice-plan.v2} is refused, where 0310 accepted any value
--      including a missing one.
--   b. a top-level key set other than exactly {accepted_plan_revision,
--      plan_digest, schema_version, slices, work_request} is refused.
--
-- Neither can change the outcome of a plan that was ever usable.  0335:547
-- already required exactly that five-key set of the bound plan, and 0335:550
-- already pinned the version, so a plan failing (a) or (b) could never produce a
-- receipt under 0335 and can never produce one under candidate 2 either.  Both
-- refusals are new only at registration, they fail closed, and they strand
-- nothing: registration writes a new row and never re-reads a stored one.
--
-- NO RETROACTIVE REWRITE.  Nothing anywhere in these two files re-validates,
-- rewrites, reclassifies or deletes a plan, receipt, envelope, lease or reviewer
-- fact that already exists.  A v2 plan registered before this file is applied
-- keeps its stored content untouched, this file never re-reads it, and the two
-- 0450 replacements deliberately add no contract validation of their own,
-- because they read lineages that already closed.
--
-- TWO CONSEQUENCES OF THAT, STATED RATHER THAN LEFT TO BE FOUND:
--
--   i.  A stored v2 plan that was registered around the caller-side validators
--       is not rewritten, but it can no longer RECEIPT: the receipt candidate's
--       v2 branch calls ops.engineering_slice_plan_refusal over the stored plan
--       row before appending.  That refuses a new append; it strands nothing,
--       because 0335:550 pinned the receipt seam to v1 and therefore no v2
--       receipt can exist yet.  A refusal there is fail-closed on a plan that
--       could never have produced a valid receipt under 0335 either.
--   ii. Re-registering an already-stored malformed v2 plan with its ORIGINAL
--       idempotency key now raises instead of returning the stored row.  The
--       V5-F03 refusal in SECTION 3 runs before the ON CONFLICT ... DO NOTHING
--       replay, so the idempotent-replay path is reached only for a plan that
--       passes the whole-plan contract.  This is deliberate and fail-closed: the
--       stored row is untouched and still readable, and a caller that wants it
--       reads ops.engineering_slice_plan directly rather than re-asserting a
--       malformed plan as current.  No accepted plan changes behavior, because a
--       plan that passes the contract replays exactly as it did under 0310.
--
-- ===========================================================================
-- PARITY WITH THE TWO SOURCE VALIDATORS, AND THE FIVE PLACES IT IS OR WAS NOT
-- EXACT (difference 4 is now parity; the entry is kept so the withdrawal shows)
-- ===========================================================================
--
-- ops.engineering_slice_plan_refusal implements requirePlan field for field and
-- predicate for predicate, including requireDesignContract,
-- requireAcyclicDependencies, requireSeamAuthority,
-- requireParallelResourceIsolation and requirePlan's closing canonicalDigest
-- recomputation of the plan minus plan_digest.  Five differences are known and
-- stated:
--
--   1. REFUSAL ORDER.  A plan with several faults may be refused on a different
--      fault than JS reports first.  FOR engineering-slice-plan.v2 the ACCEPTED
--      SET is identical; only which token comes back can differ, and no caller
--      matches on these tokens.  The claim is scoped to v2 on purpose: for v1
--      this file is deliberately WEAKER than requirePlan, which also refuses
--      duplicate slice refs, applies every per-slice typing rule, and runs the
--      plan_digest-binds-content recomputation for EVERY version -- none of
--      which this file runs for v1, because it returns NULL as soon as it has
--      the top-level shape and version.  That gap is named, not closed: see THE
--      v1 BOUNDARY above for why, and difference 3 for the cycle/ordinal half of
--      the same divergence.
--   2. DUPLICATE DECLARED REFS.  JS uses a per-element identifier check for
--      dependency_refs / declared_resource_refs / declared_component_refs /
--      declared_plan_step_refs / forbidden_change_refs and does NOT require
--      uniqueness (requirePlan's declared-ref loop, and the comment on
--      requireParallelResourceIsolation, which de-duplicates with a Set at the
--      point of use because one slice repeating its own resource is not
--      contention).  Python agrees (_ids in engineering_passport).  This file
--      matches them exactly, via ops.engineering_plan_identifier_list.  It
--      deliberately does NOT use ops.engineering_receipt_identifier_array here,
--      which also requires uniqueness: doing so would refuse plans both source
--      validators accept.
--      THE SAME RULE HOLDS INSIDE THE DESIGN CONTRACT.
--      ops.engineering_receipt_design_identifier_subset (candidate 2) compares
--      isolation.shared_resource_refs against declared_resource_refs, and it is
--      the one place a declared-ref array is read from candidate 2 during
--      REGISTRATION.  It requires the subset to be unique and the superset only
--      to be well formed, exactly as requireDesignContract does
--      (isUniqueIdentifierArray on shared_resource_refs, a plain .includes()
--      membership test against declared_resource_refs).  Uniqueness IS still
--      required everywhere the source validators require it:
--      isolation.shared_resource_refs, tests.planned_check_refs, the seam ref
--      arrays and full_design_refs.fixture_refs.
--      CONSEQUENCE, PRE-EXISTING AND UNCHANGED FOR v1: the receipt seam DOES
--      require uniqueness on those fields (0335:579 / candidate 2's reproduced
--      slice-typing block), so a plan with a repeated declared ref registers and
--      can never receipt.  That asymmetry exists today for v1; this file neither
--      creates nor closes it, and closing it would be a new refusal the source
--      validators do not have.
--   3. v1 CYCLES AND DUPLICATE ORDINALS.  The Python validator refuses these for
--      every version (validate_engineering_slice_plan); JS refuses them for v2
--      only, and documents that as a deliberate legacy divergence.  This file
--      follows JS, because JS is the seam that actually reads stored rows.
--   4. IDENTIFIERS ARE COMPARED RAW, AND FOR v2 THAT IS NOW EXACT PARITY.  An
--      earlier version of this header said JS validates the TRIMMED result of
--      text(), so a padded ref such as " slice:a " passed requirePlan while this
--      file's untrimmed regex refused it.  That claim is STALE and is withdrawn
--      for v2: requirePlan now selects exactId for engineering-slice-plan.v2 and
--      applies the same closed ID regex to the value exactly as the producer
--      wrote it, so a padded identifier is refused there too, and refused rather
--      than normalised.  For v2 the JS, Python (_str full-matches the raw value)
--      and SQL identifier contracts are the same contract.
--      THE LEGACY v1 PATH IS UNCHANGED: requirePlan still selects id() for
--      engineering-slice-plan.v1, which validates text()'s trimmed copy while
--      the plan keeps the raw string, because a stored v1 plan is append-only
--      and must stay readable on every read path.  This file cannot diverge from
--      that: it accepts a v1 plan after its shape and version and never applies
--      an identifier regex to a v1 plan at all.  The PRE-EXISTING v1 asymmetry
--      is at the receipt seam, which has always matched the raw value, so a
--      padded v1 ref registers and can never receipt -- the same shape of
--      asymmetry as difference 2, and equally untouched here.
--      Neither this file nor candidate 2 trims: rewriting an identifier would
--      silently change the content a sealed plan_digest already binds.
--      This entry is kept, and kept numbered, precisely because it is no longer
--      a divergence: a reader who was told there was one needs to see it
--      withdrawn rather than quietly deleted.
--   5. RE-REGISTERING A STORED MALFORMED v2 PLAN.  See consequence (ii) under
--      THE v1 BOUNDARY: the idempotent replay of such a plan now raises instead
--      of returning the stored row.  requirePlan has no equivalent, because it
--      never reaches the register seam with a plan it has already refused.
--
-- ===========================================================================
-- WHAT THIS FILE IS NOT
-- ===========================================================================
--
-- It is not a new Work Request, controller program, queue, adapter, role or
-- authority.  It adds no table, no trigger, no grant and no job definition.  It
-- creates exactly three new functions --
-- ops.engineering_slice_plan_slice_fields, ops.engineering_plan_identifier_list
-- and ops.engineering_slice_plan_refusal -- all pure, all SECURITY DEFINER and
-- all explicitly revoked from public and from every application role at the end
-- of this file, and it replaces exactly three existing functions in place:
-- ops.engineering_register_slice_plan, ops.canonical_ownership_plan_dependencies
-- and ops.canonical_ownership_dependency_state.
--
-- IT IS ALSO NOT A WHOLE-PLAN CHECK IN THE OWNERSHIP KERNEL.  Neither 0450
-- replacement calls ops.engineering_slice_plan_refusal, and that is a decision,
-- not an omission.  Adding it there is not needed for correctness once the
-- registration seam and the receipt seam both call it: those are the only two
-- ways a v2 plan can become the basis of a lease, and a v2 lineage cannot reach
-- ops.canonical_ownership_dependency_state at all without a receipt, which now
-- requires a valid whole plan.  It would also be the one place where a
-- whole-plan refusal COULD strand something -- these two functions read plans
-- and lineages that already exist -- and 0450:1224-1225 leaves the entire lease
-- kernel revoked from every role, so nothing calls them today.  A minimal call
-- could be added later at 0450:303 if a v2 lease flow is ever activated; it is
-- deliberately not added now.
--
-- ===========================================================================
-- ORDINAL-TIME INTEGRATION PREREQUISITES (root decisions, not made here)
-- ===========================================================================
--
--   a. db/schema.sql is a generated dump of the applied frontier.  Whoever
--      assigns this file a migration ordinal must regenerate it by the
--      repository's existing procedure; this file does not edit it.
--   b. THE CANONICAL-OWNERSHIP PRE-0431 FINGERPRINT ASSERTION IS NOT AFFECTED BY
--      THIS FILE.  ops/local-pg-ci.py recomputes the "before" side each run from
--      a second database migrated through 0431 and compares it to the frontier;
--      there is no committed baseline to regenerate.  None of the three
--      functions this file replaces -- ops.engineering_register_slice_plan,
--      ops.canonical_ownership_plan_dependencies,
--      ops.canonical_ownership_dependency_state -- is in the gate's target
--      list, and neither are the three functions it adds, so no compared
--      definition or acl changes.  The COMPANION receipt candidate does change
--      one compared definition; see its header for the exact field and for the
--      standing instruction not to edit the gate, normalize the comparison, or
--      weaken the pre-0431 baseline to accommodate it.
-- ===========================================================================


-- ---------------------------------------------------------------------------
-- SECTION 1 -- the two new pure predicates the rest of the file rests on.
--
-- Same convention as the 0335 helpers at 0335:412-471 and the candidate 2
-- helpers: argument-only, read no row, expose no data, substitute an empty JSON
-- container before iterating so malformed caller JSON is a false predicate
-- rather than a set-return type error that could bypass a fail-closed branch.
-- Both are revoked at the end of this file.
-- ---------------------------------------------------------------------------

-- The exact accepted slice field set for one slice-plan schema version.
--
-- NULL means "unsupported version", and every caller treats NULL as a refusal.
-- ops.engineering_receipt_exact_object is STRICT, so even a caller that forgot
-- to test for NULL gets NULL -> coalesce(...,false) -> refuse; the explicit
-- tests below are belt and braces, not the only guard.
--
-- This restates the same 16 fields that 0335:579, 0450:331-333 and 0450:434-438
-- hard-code, and the same 17th field candidate 2 appends for v2.  It is a
-- deliberate second statement rather than a shared one, because folding it back
-- into candidate 2 would widen a file that has already been reviewed line by
-- line.  It is pinned rather than trusted: the companion fixture asserts this
-- function's v1 output is set-equal to the literal 0450 array, so a future
-- divergence fails a check instead of silently accepting a wrong shape.
create or replace function ops.engineering_slice_plan_slice_fields(p_schema_version text)
returns text[] language sql immutable strict security definer set search_path=pg_catalog,ops
as $$
  select case p_schema_version
    when 'engineering-slice-plan.v1' then array[
      'baseline_evidence_refs','concurrency_posture','declared_component_refs','declared_plan_step_refs',
      'declared_resource_refs','definition_of_done','dependency_refs','forbidden_change_refs','manual_qa_required',
      'objective','ordinal','planned_checks','release_requirement','risk_class','scope_boundary','slice_ref']
    when 'engineering-slice-plan.v2' then array[
      'baseline_evidence_refs','concurrency_posture','declared_component_refs','declared_plan_step_refs',
      'declared_resource_refs','definition_of_done','dependency_refs','forbidden_change_refs','manual_qa_required',
      'objective','ordinal','planned_checks','release_requirement','risk_class','scope_boundary','slice_ref',
      'design_contract']
    else null
  end;
$$;

-- An array of well-formed identifier strings, duplicates ALLOWED.
--
-- This is not ops.engineering_receipt_identifier_array with the uniqueness test
-- removed by accident: it is the exact predicate both source validators apply to
-- the five declared-ref arrays of a slice (requirePlan's declared-ref loop in
-- engineering-runtime.js, _ids in engineering_passport.py), which do not require
-- uniqueness.  See difference 2 in the header.  Uniqueness IS still required
-- everywhere the source validators require it -- isolation.shared_resource_refs,
-- tests.planned_check_refs, the seam ref arrays and full_design_refs.fixture_refs
-- all go through the candidate 2 predicates, which use the unique variant.
-- ops.engineering_receipt_design_identifier_subset is split for that reason: it
-- requires the SUBSET (shared_resource_refs) to be unique and asks only
-- well-formedness of the SUPERSET (declared_resource_refs), which is the array
-- this predicate governs.
create or replace function ops.engineering_plan_identifier_list(p_value jsonb)
returns boolean language sql immutable strict security definer set search_path=pg_catalog,ops
as $$
  select jsonb_typeof(p_value)='array'
     and not exists (
       select 1
         from jsonb_array_elements(case when jsonb_typeof(p_value)='array' then p_value else '[]'::jsonb end) value
        where jsonb_typeof(value)<>'string'
           or not coalesce((value#>>'{}') ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$',false)
     );
$$;

-- ---------------------------------------------------------------------------
-- SECTION 2 -- the whole-plan validator.
--
-- Returns NULL when the plan is accepted, otherwise a stable refusal token
-- naming the exact facet that failed.  Every non-object, absent or untyped input
-- returns a token: there is no accepting default anywhere, and no branch can
-- evaluate to SQL NULL and be read as "true" by plpgsql, because every scalar
-- test is wrapped in coalesce(...) or uses IS DISTINCT FROM.
--
-- It is pure: it reads no table, takes no lock, samples no clock and writes
-- nothing.  It is safe to call inside the registration transaction without
-- affecting the lock order there, safe to call inside the receipt seam's
-- transaction over the ops.engineering_slice_plan row that seam already holds
-- FOR KEY SHARE, and safe to call directly from a fixture.
--
-- TWO CALLERS, ONE STATEMENT.  ops.engineering_register_slice_plan (SECTION 3)
-- calls it so a plan cannot be stored around the caller-side validators;
-- ops.engineering_record_slice_receipt (the companion candidate) calls it so an
-- already-stored plan that got around them cannot become receiptable.  The rules
-- are stated once, here.
-- ---------------------------------------------------------------------------
create or replace function ops.engineering_slice_plan_refusal(p_plan jsonb)
returns text language plpgsql immutable security definer set search_path=pg_catalog,ops,public
as $$
declare v_schema_version text; v_slice_fields text[]; v_slice jsonb; v_slice_ref text;
        v_design_refusal text; v_token text; v_remaining text[]; v_peeled text[];
begin
  if jsonb_typeof(p_plan) is distinct from 'object' then return 'plan'; end if;
  if not coalesce(ops.engineering_receipt_exact_object(p_plan,array[
       'accepted_plan_revision','plan_digest','schema_version','slices','work_request']),false) then
    return 'plan.shape';
  end if;
  v_schema_version := p_plan->>'schema_version';
  if not coalesce(v_schema_version=any(array[
       'engineering-slice-plan.v1','engineering-slice-plan.v2']),false) then
    return 'plan.schema_version';
  end if;
  v_slice_fields := ops.engineering_slice_plan_slice_fields(v_schema_version);
  if v_slice_fields is null then return 'plan.schema_version'; end if;
  -- THE v1 BOUNDARY.  See the header: an accepted v1 plan is not put through the
  -- successor version's whole-plan rules, so a v1 registration keeps its exact
  -- 0310 behavior from here on.
  if v_schema_version is distinct from 'engineering-slice-plan.v2' then
    return null;
  end if;

  -- ---- plan bindings ------------------------------------------------------
  if not coalesce(ops.engineering_receipt_exact_object(p_plan->'work_request',array[
       'canonical_record_digest','id','state_version']),false) then
    return 'plan.work_request';
  end if;
  if not coalesce((p_plan#>>'{work_request,id}') ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$',false) then
    return 'plan.work_request.id';
  end if;
  if not coalesce((p_plan#>>'{work_request,canonical_record_digest}') ~ '^sha256:[0-9a-f]{64}$',false) then
    return 'plan.work_request.canonical_record_digest';
  end if;
  -- The regex is how 0335:583 already spells "a positive integer": it refuses 0,
  -- a negative, and a non-integral spelling such as 1.0 that jsonb would keep.
  if jsonb_typeof(p_plan#>'{work_request,state_version}') is distinct from 'number'
     or not coalesce((p_plan#>>'{work_request,state_version}') ~ '^[1-9][0-9]*$',false) then
    return 'plan.work_request.state_version';
  end if;
  if not coalesce(ops.engineering_receipt_exact_object(p_plan->'accepted_plan_revision',array[
       'digest','id','revision']),false) then
    return 'plan.accepted_plan_revision';
  end if;
  if not coalesce((p_plan#>>'{accepted_plan_revision,id}') ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$',false) then
    return 'plan.accepted_plan_revision.id';
  end if;
  if not coalesce((p_plan#>>'{accepted_plan_revision,digest}') ~ '^sha256:[0-9a-f]{64}$',false) then
    return 'plan.accepted_plan_revision.digest';
  end if;
  if jsonb_typeof(p_plan#>'{accepted_plan_revision,revision}') is distinct from 'number'
     or not coalesce((p_plan#>>'{accepted_plan_revision,revision}') ~ '^[1-9][0-9]*$',false) then
    return 'plan.accepted_plan_revision.revision';
  end if;
  if not coalesce((p_plan->>'plan_digest') ~ '^sha256:[0-9a-f]{64}$',false) then
    return 'plan.plan_digest';
  end if;
  if jsonb_typeof(p_plan->'slices') is distinct from 'array'
     or not coalesce(case when jsonb_typeof(p_plan->'slices')='array'
                          then jsonb_array_length(p_plan->'slices')>0 else false end,false) then
    return 'plan.slices';
  end if;

  -- ---- every slice, closed and fully typed --------------------------------
  for v_slice in select value from jsonb_array_elements(p_plan->'slices') loop
    v_slice_ref := coalesce(v_slice->>'slice_ref','<unnamed>');
    if jsonb_typeof(v_slice) is distinct from 'object' then
      return 'slices['||v_slice_ref||'].shape';
    end if;
    -- The agent may never hand the design-depth classifier its own answer.  The
    -- closed field set below already refuses unknown keys; this is the named
    -- refusal, and it is tested before the shape exactly as requirePlan tests
    -- refuseSelfLabel before its own closed-field-set comparison.
    if not coalesce(ops.engineering_receipt_design_self_label_free(v_slice),false) then
      return 'slices['||v_slice_ref||'].design_depth_self_label';
    end if;
    if not coalesce(ops.engineering_receipt_exact_object(v_slice,v_slice_fields),false) then
      return 'slices['||v_slice_ref||'].shape';
    end if;
    if not coalesce(v_slice_ref ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$',false) then
      return 'slices['||v_slice_ref||'].slice_ref';
    end if;
    if jsonb_typeof(v_slice->'ordinal') is distinct from 'number'
       or not coalesce((v_slice->>'ordinal') ~ '^[1-9][0-9]*$',false) then
      return 'slices['||v_slice_ref||'].ordinal';
    end if;
    if exists (select 1 from unnest(array['objective','definition_of_done','scope_boundary']) field
                where jsonb_typeof(v_slice->field) is distinct from 'string'
                   or not coalesce(btrim(v_slice->>field)<>'',false)) then
      return 'slices['||v_slice_ref||'].text_fields';
    end if;
    if not coalesce((v_slice->>'concurrency_posture')=any(array[
         'parallel_safe','serial_after_dependencies','exclusive_resource']),false)
       or not coalesce((v_slice->>'risk_class')=any(array['R0','R1','R2','R3','R4','R5','R6']),false)
       or not coalesce((v_slice->>'release_requirement')=any(array['required','not_required']),false)
       or jsonb_typeof(v_slice->'manual_qa_required') is distinct from 'boolean' then
      return 'slices['||v_slice_ref||'].enums';
    end if;
    if exists (select 1 from unnest(array['dependency_refs','declared_resource_refs',
                                          'declared_component_refs','declared_plan_step_refs',
                                          'forbidden_change_refs']) field
                where not coalesce(ops.engineering_plan_identifier_list(v_slice->field),false)) then
      return 'slices['||v_slice_ref||'].identifier_arrays';
    end if;
    if not coalesce(ops.engineering_receipt_evidence_array(v_slice->'baseline_evidence_refs'),false) then
      return 'slices['||v_slice_ref||'].baseline_evidence_refs';
    end if;
    if jsonb_typeof(v_slice->'planned_checks') is distinct from 'array'
       or not coalesce(case when jsonb_typeof(v_slice->'planned_checks')='array'
                            then jsonb_array_length(v_slice->'planned_checks')>0 else false end,false)
       or exists (
         select 1
           from jsonb_array_elements(case when jsonb_typeof(v_slice->'planned_checks')='array'
                                          then v_slice->'planned_checks' else '[]'::jsonb end) planned_check
          where not coalesce(ops.engineering_receipt_exact_object(planned_check,array[
                  'check_ref','evidence_requirement','failure_condition']),false)
             or not coalesce((planned_check->>'check_ref') ~ '^[A-Za-z][A-Za-z0-9._:-]{2,127}$',false)
             or jsonb_typeof(planned_check->'failure_condition') is distinct from 'string'
             or not coalesce(btrim(planned_check->>'failure_condition')<>'',false)
             or not coalesce((planned_check->>'evidence_requirement')=any(array[
                  'redacted_evidence_required','metadata_only_sufficient']),false))
       or exists (
         select 1
           from jsonb_array_elements(case when jsonb_typeof(v_slice->'planned_checks')='array'
                                          then v_slice->'planned_checks' else '[]'::jsonb end) planned_check
          group by planned_check->>'check_ref' having count(*)>1) then
      return 'slices['||v_slice_ref||'].planned_checks';
    end if;
    -- Every dependency must name a slice this same plan declares.  Direction is
    -- not constrained here; a self-edge is caught by the cycle test below.
    if exists (
         select 1
           from jsonb_array_elements_text(case when jsonb_typeof(v_slice->'dependency_refs')='array'
                                               then v_slice->'dependency_refs' else '[]'::jsonb end) dep
          where not exists (select 1 from jsonb_array_elements(p_plan->'slices') other
                             where other->>'slice_ref'=dep)) then
      return 'slices['||v_slice_ref||'].dependency_refs_unknown';
    end if;
    -- The closed Q046.D1 contract, from its single authority in candidate 2.
    -- The envelope-binding comparison is deliberately NOT run here: there is no
    -- envelope at registration time, and admission is where that comparison
    -- belongs in both source validators.
    v_design_refusal := ops.engineering_receipt_design_contract_refusal(v_slice);
    if v_design_refusal is not null then
      return 'slices['||v_slice_ref||'].'||v_design_refusal;
    end if;
  end loop;

  -- ---- plan-wide uniqueness -----------------------------------------------
  -- EVERY interpolated value in the plan-wide tokens below is wrapped in
  -- coalesce.  Each of them is already known to be a non-NULL typed identifier
  -- or number by the time these run, because the per-slice loop above ran
  -- ops.engineering_receipt_design_contract_refusal and the closed field/typing
  -- tests first.  That makes the guard POSITIONAL: an SQL NULL anywhere in a
  -- token would make the whole concatenation NULL, and a NULL v_token is read as
  -- "accept".  coalesce makes the fail-closed local instead, so a future
  -- reordering of this function cannot turn a refusal into a silent acceptance.
  select 'plan.duplicate_slice_ref['||coalesce(candidate->>'slice_ref','<unnamed>')||']' into v_token
    from jsonb_array_elements(p_plan->'slices') candidate
   group by candidate->>'slice_ref' having count(*)>1
   order by 1 limit 1;
  if v_token is not null then return v_token; end if;

  select 'plan.duplicate_ordinal['||coalesce(min(candidate->>'ordinal'),'<unnamed>')||']' into v_token
    from jsonb_array_elements(p_plan->'slices') candidate
   group by candidate->'ordinal' having count(*)>1
   order by 1 limit 1;
  if v_token is not null then return v_token; end if;

  -- ---- acyclic dependencies ------------------------------------------------
  -- Kahn's peel: repeatedly drop every slice that no longer waits on a slice
  -- still in the set.  A set that stops shrinking is exactly a cycle, and a
  -- self-edge is a one-member cycle.  Every dependency ref is already known to
  -- name a declared slice, so nothing outside the set can hold a member.  The
  -- loop is bounded by the slice count: each pass either shrinks the set or
  -- exits.
  select array_agg(candidate->>'slice_ref' order by candidate->>'slice_ref') into v_remaining
    from jsonb_array_elements(p_plan->'slices') candidate;
  loop
    select coalesce(array_agg(ref order by ref),'{}'::text[]) into v_peeled
      from unnest(v_remaining) ref
     where exists (
       select 1
         from jsonb_array_elements(p_plan->'slices') candidate,
              jsonb_array_elements_text(case when jsonb_typeof(candidate->'dependency_refs')='array'
                                             then candidate->'dependency_refs' else '[]'::jsonb end) dep
        where candidate->>'slice_ref'=ref and dep=any(v_remaining));
    exit when coalesce(array_length(v_peeled,1),0)=0;
    exit when array_length(v_peeled,1)=array_length(v_remaining,1);
    v_remaining := v_peeled;
  end loop;
  if coalesce(array_length(v_peeled,1),0)>0 then
    return 'plan.dependency_cycle['||array_to_string(v_peeled,',')||']';
  end if;

  -- ---- seam authority (Q063.D1 / Q122.D1) ---------------------------------
  -- One seam may have at most one owning slice, may be retired at most once, and
  -- may not be built on by one slice while another retires it.
  select 'plan.seam_duplicate_authority['
         ||coalesce(candidate#>>'{design_contract,seam_decision,target_seam_ref}','<unnamed>')||']'
    into v_token
    from jsonb_array_elements(p_plan->'slices') candidate
   where coalesce((candidate#>>'{design_contract,seam_decision,mode}')=any(array['new_module','replace']),false)
   group by candidate#>>'{design_contract,seam_decision,target_seam_ref}'
   having count(*)>1
   order by 1 limit 1;
  if v_token is not null then return v_token; end if;

  select 'plan.seam_retired_twice['||coalesce(retired_ref,'<unnamed>')||']' into v_token
    from jsonb_array_elements(p_plan->'slices') candidate,
         jsonb_array_elements_text(
           case when jsonb_typeof(candidate#>'{design_contract,seam_decision,replaced_seam_refs}')='array'
                then candidate#>'{design_contract,seam_decision,replaced_seam_refs}'
                else '[]'::jsonb end) retired_ref
   group by retired_ref having count(*)>1
   order by 1 limit 1;
  if v_token is not null then return v_token; end if;

  select 'plan.seam_half_replacement['
         ||coalesce(candidate#>>'{design_contract,seam_decision,target_seam_ref}','<unnamed>')||']'
    into v_token
    from jsonb_array_elements(p_plan->'slices') candidate
   where coalesce((candidate#>>'{design_contract,seam_decision,mode}')=any(array['reuse','extend']),false)
     and exists (
       select 1
         from jsonb_array_elements(p_plan->'slices') other,
              jsonb_array_elements_text(
                case when jsonb_typeof(other#>'{design_contract,seam_decision,replaced_seam_refs}')='array'
                     then other#>'{design_contract,seam_decision,replaced_seam_refs}'
                     else '[]'::jsonb end) retired_ref
        where retired_ref = candidate#>>'{design_contract,seam_decision,target_seam_ref}')
   order by 1 limit 1;
  if v_token is not null then return v_token; end if;

  -- ---- parallel-safe resource isolation -----------------------------------
  -- Two parallel_safe slices that both declare one resource are two
  -- contradictory statements sealed in one plan -- each has already had to state
  -- isolation.shared_resource_refs as empty -- and both become admissible at
  -- once.  A dependency edge in either direction removes the contradiction, so
  -- the transitive closure is consulted before refusing.  Nothing here restricts
  -- a serial_after_dependencies or exclusive_resource posture, which is how
  -- contention is meant to be declared.
  --
  -- The recursive closure is only reached after the cycle test above returned
  -- clean, so the edge graph is a DAG and the UNION recursion terminates.
  select q.refusal into v_token from (
    with recursive edge(src,dst) as (
      select candidate->>'slice_ref', dep
        from jsonb_array_elements(p_plan->'slices') candidate,
             jsonb_array_elements_text(case when jsonb_typeof(candidate->'dependency_refs')='array'
                                            then candidate->'dependency_refs' else '[]'::jsonb end) dep
    ), closure(src,dst) as (
      select src,dst from edge
      union
      select c.src,e.dst from closure c join edge e on e.src=c.dst
    ), owner_row(slice_ref,resource_ref) as (
      select distinct candidate->>'slice_ref', res
        from jsonb_array_elements(p_plan->'slices') candidate,
             jsonb_array_elements_text(case when jsonb_typeof(candidate->'declared_resource_refs')='array'
                                            then candidate->'declared_resource_refs' else '[]'::jsonb end) res
       where candidate->>'concurrency_posture'='parallel_safe'
    )
    select 'plan.parallel_resource_conflict['||coalesce(a.resource_ref,'<unnamed>')||':'
             ||coalesce(a.slice_ref,'<unnamed>')||','||coalesce(b.slice_ref,'<unnamed>')||']'
             as refusal
      from owner_row a
      join owner_row b on b.resource_ref=a.resource_ref and a.slice_ref<b.slice_ref
     where not exists (select 1 from closure k where k.src=a.slice_ref and k.dst=b.slice_ref)
       and not exists (select 1 from closure k where k.src=b.slice_ref and k.dst=a.slice_ref)
     order by 1
     limit 1
  ) q;
  if v_token is not null then return v_token; end if;

  -- ---- the digest must bind the content ------------------------------------
  -- This is the check the database has never had.  It reuses the EXISTING
  -- canonicalization, ops.guidance_import_canonical_json (db/schema.sql:9274),
  -- which is the same function the receipt seam already digests receipts with at
  -- 0335:491 -- no second canonicalization authority is created here.  It sorts
  -- object keys with collate "C" and emits jsonb's own scalar spellings, which is
  -- what canonicalDigest (engineering-runtime.js) and base.canonical_digest do.
  --
  -- KNOWN NARROW EXPOSURE, stated rather than assumed.  The receipt path proves
  -- this equivalence for strings, booleans, nulls, objects and arrays, because
  -- every appended receipt has passed 0335:490.  It does NOT prove it for
  -- NUMBERS: a receipt carries none, and a plan carries three (ordinal,
  -- work_request.state_version, accepted_plan_revision.revision).  jsonb
  -- preserves numeric scale, so a producer emitting 1.0 rather than 1 would be
  -- refused here where JSON.stringify would have written 1.  Every one of those
  -- three fields is separately pinned above to ^[1-9][0-9]*$, so a plan that
  -- could hit that divergence is already refused by the time this runs, and the
  -- companion fixture registers a positive v2 plan whose acceptance depends on
  -- this equivalence holding.
  if ('sha256:'||encode(public.digest(
        ops.guidance_import_canonical_json(p_plan - 'plan_digest'),'sha256'),'hex'))
     is distinct from (p_plan->>'plan_digest') then
    return 'plan.plan_digest_does_not_bind_content';
  end if;

  return null;
end $$;

-- ---------------------------------------------------------------------------
-- SECTION 3 -- the replaced registration seam.
--
-- Reproduced verbatim from migrations/0310_engineering_execution_fabric.sql:185
-- except the one hunk marked V5-F03.  Same name, argument names and types,
-- return type, LANGUAGE, SECURITY DEFINER and search_path, so every existing
-- caller keeps resolving to it unchanged.  CREATE OR REPLACE retains the
-- existing owner and ACL: the 0310:399 EXECUTE grant to carr_writer is
-- preserved exactly, neither widened nor narrowed.
-- ---------------------------------------------------------------------------

create or replace function ops.engineering_register_slice_plan(
  p_work_request text, p_plan jsonb, p_plan_digest text, p_idempotency_key uuid
)
returns ops.engineering_slice_plan
language plpgsql security definer
set search_path = pg_catalog, ops, public
as $$
declare source jsonb; row ops.engineering_slice_plan%rowtype;
        -- V5-F03: the whole-plan refusal token, NULL when the plan is accepted.
        plan_refusal text;
begin
  source := ops.engineering_admission_source(p_work_request);
  if source is null then raise exception 'engineering admission requires a current accepted ready plan'; end if;
  if p_plan_digest !~ '^sha256:[0-9a-f]{64}$' then raise exception 'invalid engineering slice plan digest'; end if;
  if p_plan->'work_request'->>'id' <> source->'work_request'->>'id'
     or (p_plan->'work_request'->>'state_version')::integer <> (source->'work_request'->>'version')::integer
     or p_plan->'accepted_plan_revision'->>'id' <> source->'accepted_plan'->>'plan_ref'
     or p_plan->'accepted_plan_revision'->>'digest' <> source->'accepted_plan'->>'digest'
     or (p_plan->'accepted_plan_revision'->>'revision')::integer <> (source->'accepted_plan'->>'revision')::integer
     or p_plan->>'plan_digest' <> p_plan_digest then
    raise exception 'engineering slice plan is not bound to the exact accepted Work Request and plan';
  end if;
  -- V5-F03: the whole-plan check the database has never had.  It runs AFTER the
  -- five binding comparisons above, so every existing refusal keeps its exact
  -- text and its exact precedence, and it runs BEFORE the insert, so a refused
  -- plan is never written.  It is a pure JSON predicate: it takes no lock,
  -- samples no clock and reads no table, so the transaction's behavior around
  -- it is unchanged.
  --
  -- For engineering-slice-plan.v1 this returns NULL as soon as it has checked
  -- the top-level shape and version, so a v1 registration is unchanged; see THE
  -- v1 BOUNDARY in the header for the two narrow exceptions.  For
  -- engineering-slice-plan.v2 it is the full requirePlan predicate, including
  -- the plan_digest-binds-content check, which is what closes the direct
  -- carr_writer bypass of the caller-side validators for plans stored FROM NOW
  -- ON.  Plans already stored are covered at the receipt seam by the same
  -- function; see REGISTRATION ALONE IS NOT ENOUGH in the header.
  --
  -- It runs before the ON CONFLICT ... DO NOTHING replay below, so re-asserting
  -- an already-stored malformed v2 plan under its original idempotency key
  -- raises rather than replaying.  See consequence (ii) in the header.
  plan_refusal := ops.engineering_slice_plan_refusal(p_plan);
  if plan_refusal is not null then
    raise exception 'engineering slice plan is not a valid typed slice plan: %', plan_refusal;
  end if;
  insert into ops.engineering_slice_plan
    (work_request_id,accepted_plan_id,accepted_plan_hash,work_request_version,plan_digest,plan,idempotency_key)
  values (regexp_replace(source->'work_request'->>'id', '^wr:', '')::uuid,
          (source->'accepted_plan'->>'record_id')::uuid,
          source->'accepted_plan'->>'digest',(source->'work_request'->>'version')::integer,
          p_plan_digest,p_plan,p_idempotency_key)
  on conflict (idempotency_key) do nothing
  returning * into row;
  if row.id is null then
    select * into row from ops.engineering_slice_plan where idempotency_key=p_idempotency_key;
    if row.plan_digest <> p_plan_digest or row.plan <> p_plan then
      raise exception 'engineering slice plan idempotency key was reused with different content';
    end if;
  end if;
  return row;
end $$;

-- ---------------------------------------------------------------------------
-- SECTION 4 -- ops.canonical_ownership_plan_dependencies, version-aware.
--
-- Reproduced verbatim from
-- migrations/0450_canonical_ownership_lease_kernel.sql:310-346 except the one
-- hunk marked V5-F03.  Same signature, volatility, SECURITY DEFINER and
-- search_path, so 0450:303, :828, :848, :1048 and :1059 keep resolving to it,
-- and its 0450:1224 revocation from every role is retained by CREATE OR REPLACE:
-- the lease kernel stays dark.
--
-- The ONLY change is that the hard-coded 16-field array becomes the
-- version-dependent field set.  Every dependency, refusal code, causal object
-- and returned shape is unchanged, and it still fails closed on a malformed v2
-- slice: a v2 slice missing design_contract, or carrying an unknown key, fails
-- the exact-object test exactly as before and returns the same
-- SLICE_PLAN_BINDING_STALE / malformed_dependencies refusal.
--
-- It deliberately does NOT validate the v2 design contract or the whole plan
-- here.  That would retroactively hold a plan registered before this file to
-- rules it was not registered under, and this function reads plans and lineages
-- that already exist.  Those rules are enforced at the two seams that admit new
-- state instead: registration (SECTION 3) and the receipt append (companion
-- candidate).  See IT IS ALSO NOT A WHOLE-PLAN CHECK IN THE OWNERSHIP KERNEL in
-- the header for why adding one here is not needed for correctness.
-- ---------------------------------------------------------------------------

create or replace function ops.canonical_ownership_plan_dependencies(
  p_slice_plan_id uuid,p_slice_ref text
) returns jsonb language plpgsql volatile security definer set search_path=pg_catalog,ops,public
as $$
declare plan_row ops.engineering_slice_plan%rowtype; slice jsonb; slice_count integer;
        dependencies jsonb;
        -- V5-F03: the accepted slice field set for this plan's own version.
        slice_fields text[];
begin
  select * into plan_row from ops.engineering_slice_plan where id=p_slice_plan_id;
  if not found then
    return ops.canonical_ownership_refusal('SLICE_PLAN_NOT_FOUND','slice_plan',
      '"canonical slice plan"'::jsonb,'"absent"'::jsonb);
  end if;
  if jsonb_typeof(plan_row.plan->'slices') is distinct from 'array' then
    return ops.canonical_ownership_refusal('SLICE_PLAN_BINDING_STALE','slice_plan.dependencies',
      '"one typed canonical dependency set"'::jsonb,
      jsonb_build_object('reason','malformed_plan','value_redacted',true));
  end if;
  -- V5-F03: an unsupported or absent slice-plan schema version is refused here
  -- rather than defaulted to the v1 shape.
  slice_fields := ops.engineering_slice_plan_slice_fields(plan_row.plan->>'schema_version');
  if slice_fields is null then
    return ops.canonical_ownership_refusal('SLICE_PLAN_BINDING_STALE','slice_plan.dependencies',
      '"one typed canonical dependency set"'::jsonb,
      jsonb_build_object('reason','unsupported_plan_schema_version','value_redacted',true));
  end if;
  select count(*),min(value::text)::jsonb into slice_count,slice
    from jsonb_array_elements(plan_row.plan->'slices')
   where value->>'slice_ref'=p_slice_ref;
  if slice_count<>1 or not coalesce(ops.engineering_receipt_exact_object(slice,slice_fields),false)
     or not coalesce(ops.engineering_receipt_identifier_array(slice->'dependency_refs'),false) then
    return ops.canonical_ownership_refusal('SLICE_PLAN_BINDING_STALE','slice_plan.dependencies',
      '"one typed canonical dependency set"'::jsonb,
      jsonb_build_object('reason','malformed_dependencies','value_redacted',true));
  end if;
  select coalesce(jsonb_agg(jsonb_build_object(
           'slice_ref',dependency_ref,'required_state','independently_verified')
           order by dependency_ref),'[]'::jsonb)
    into dependencies
    from jsonb_array_elements_text(slice->'dependency_refs') dependency_ref;
  return jsonb_build_object('ok',true,'dependencies',dependencies);
end $$;

-- ---------------------------------------------------------------------------
-- SECTION 5 -- ops.canonical_ownership_dependency_state, version-aware.
--
-- Reproduced verbatim from
-- migrations/0450_canonical_ownership_lease_kernel.sql:348-716 except the one
-- hunk marked V5-F03.  Same signature, volatility, SECURITY DEFINER and
-- search_path; its 0450:1225 revocation from every role is retained.
--
-- The ONLY change is that the hard-coded 16-field array at 0450:433-439 becomes
-- the version-dependent field set.  Every envelope-leaf, receipt, attempt,
-- executor identity, attribution, planned-check, deviation, source-evidence,
-- reset-reconstruction, executor-claim and reviewer-fact rule below is
-- reproduced unchanged, both refusal codes are unchanged, and it still fails
-- closed on a malformed v2 slice.
-- ---------------------------------------------------------------------------

create or replace function ops.canonical_ownership_dependency_state(
  p_work_request_id uuid,p_slice_plan_id uuid,p_slice_ref text,p_required_state text
) returns jsonb language plpgsql volatile security definer set search_path=pg_catalog,ops,public
as $$
declare leaf_count integer; leaf ops.engineering_execution_envelope%rowtype;
        receipt ops.engineering_slice_receipt%rowtype; review ops.engineering_reviewer_fact%rowtype;
        plan_row ops.engineering_slice_plan%rowtype; receipt_slice jsonb;
        receipt_slice_count integer; receipt_attempt integer;
        session_executor uuid; session_slug text;
        reviewer_slug text; executor_session text; deviation_refs text[];
        -- V5-F03: the accepted slice field set for this plan's own version.
        slice_fields text[];
begin
  select count(*) into leaf_count from ops.engineering_execution_envelope e
   where e.work_request_id=p_work_request_id and e.slice_plan_id=p_slice_plan_id and e.slice_ref=p_slice_ref
     and not exists (select 1 from ops.engineering_execution_envelope successor
                      where successor.supersedes_envelope_id=e.id);
  if leaf_count<>1 then
    return ops.canonical_ownership_refusal('DEPENDENCY_MISSING','dependency',
      '"exactly one unsuperseded envelope leaf"'::jsonb,
      jsonb_build_object('slice_ref',p_slice_ref,'leaf_count',leaf_count));
  end if;
  select e.* into leaf from ops.engineering_execution_envelope e
   where e.work_request_id=p_work_request_id and e.slice_plan_id=p_slice_plan_id and e.slice_ref=p_slice_ref
     and not exists (select 1 from ops.engineering_execution_envelope successor
                      where successor.supersedes_envelope_id=e.id);
  select r.* into receipt from ops.engineering_slice_receipt r where r.envelope_id=leaf.id
   order by r.created_at desc,r.id desc limit 1;
  select sp.* into plan_row from ops.engineering_slice_plan sp
   where sp.id=p_slice_plan_id;
  select count(*),min(value::text)::jsonb
    into receipt_slice_count,receipt_slice
    from jsonb_array_elements(case
      when jsonb_typeof(plan_row.plan->'slices')='array'
      then plan_row.plan->'slices' else '[]'::jsonb end)
   where value->>'slice_ref'=p_slice_ref;
  -- V5-F03: the accepted slice field set is now selected by the plan's own
  -- sealed schema_version.  NULL means an unsupported version; the condition
  -- below refuses on it explicitly, and ops.engineering_receipt_exact_object is
  -- STRICT so it would refuse on it anyway.
  slice_fields := ops.engineering_slice_plan_slice_fields(plan_row.plan->>'schema_version');
  select ja.attempt into receipt_attempt from ops.job_attempt ja
   where ja.id=receipt.job_attempt_id and ja.job_id=leaf.job_id;
  select s.executor_actor_id,a.slug into session_executor,session_slug
    from ops.capability_agent_session s
    join public.actor a on a.id=s.executor_actor_id and a.active
   where s.id=leaf.agent_session_id and s.work_request_id=p_work_request_id;
  if receipt.id is null or receipt.outcome is distinct from 'claimed_complete'
     or receipt.work_request_id is distinct from p_work_request_id
     or receipt.envelope_id is distinct from leaf.id
     or receipt.slice_ref is distinct from p_slice_ref
     or plan_row.id is null
     or plan_row.work_request_id is distinct from p_work_request_id
     or leaf.slice_plan_id is distinct from plan_row.id
     or slice_fields is null
     or leaf.envelope->>'envelope_id'
          is distinct from 'env:'||leaf.id::text
     or leaf.envelope#>>'{request,job_ref}'
          is distinct from 'job:'||leaf.job_id::text
     or leaf.envelope#>>'{agent_session,id}'
          is distinct from 'session:'||leaf.agent_session_id::text
     or receipt.executor_actor_id is distinct from session_executor
     or session_slug is null
     or receipt_attempt is null
     or receipt.attempt_id is distinct from ('attempt:'||receipt_attempt)
     or receipt_slice_count<>1
     or not coalesce(ops.engineering_receipt_exact_object(receipt.receipt,array[
          'actual_component_refs','actual_resource_refs','artifact_refs','attribution','attempt_id','checks',
          'deviations','envelope_digest','evidence_refs','executor_claim','independent_verification_required',
          'outcome','plan_digest','planned_component_refs','planned_resource_refs','reset_reconstruction',
          'schema_version','slice_ref','source_evidence'
        ]),false)
     or receipt.receipt->>'schema_version' is distinct from 'engineering-slice-receipt.v1'
     or receipt.receipt_digest is distinct from
        ('sha256:'||encode(public.digest(
          ops.guidance_import_canonical_json(receipt.receipt),'sha256'),'hex'))
     or receipt.receipt->>'outcome' is distinct from 'claimed_complete'
     or receipt.receipt->>'slice_ref' is distinct from p_slice_ref
     or receipt.receipt->>'attempt_id' is distinct from receipt.attempt_id
     or receipt.receipt->>'envelope_digest' is distinct from leaf.envelope_digest
     or receipt.receipt->>'plan_digest' is distinct from plan_row.plan_digest
     or receipt.receipt->'independent_verification_required' is distinct from 'true'::jsonb
     or not coalesce(ops.engineering_receipt_exact_object(
          receipt.receipt->'attribution',array['actor_ref','adapter_ref','session_ref']),false)
     or leaf.envelope#>>'{server_binding,identity,agent_principal_id}' is null
     or leaf.envelope#>>'{agent_session,id}' is null
     or leaf.envelope#>>'{server_binding,adapter,adapter_id}' is null
     or receipt.receipt#>>'{attribution,actor_ref}' is distinct from
        leaf.envelope#>>'{server_binding,identity,agent_principal_id}'
     or receipt.receipt#>>'{attribution,session_ref}' is distinct from
        leaf.envelope#>>'{agent_session,id}'
     or receipt.receipt#>>'{attribution,adapter_ref}' is distinct from
        leaf.envelope#>>'{server_binding,adapter,adapter_id}'
     -- V5-F03: the one replaced expression.  0450:433-439 hard-coded the 16 v1
     -- fields here, which refused every engineering-slice-plan.v2 slice.
     or not coalesce(ops.engineering_receipt_exact_object(receipt_slice,slice_fields),false)
     or receipt_slice->>'slice_ref' is distinct from p_slice_ref
     or not coalesce(ops.engineering_receipt_identifier_array(
          receipt_slice->'declared_resource_refs'),false)
     or not coalesce(ops.engineering_receipt_identifier_array(
          receipt_slice->'declared_component_refs'),false)
     or jsonb_typeof(receipt_slice->'planned_checks') is distinct from 'array'
     or not coalesce(case when jsonb_typeof(receipt_slice->'planned_checks')='array'
                          then jsonb_array_length(receipt_slice->'planned_checks')>0
                          else false end,false)
     or exists (
       select 1 from jsonb_array_elements(case
         when jsonb_typeof(receipt_slice->'planned_checks')='array'
         then receipt_slice->'planned_checks' else '[]'::jsonb end) planned_check
        where not coalesce(ops.engineering_receipt_exact_object(
                planned_check,array['check_ref','evidence_requirement',
                                    'failure_condition']),false)
           or not coalesce((planned_check->>'check_ref') ~
                '^[A-Za-z][A-Za-z0-9._:-]{2,127}$',false)
           or not coalesce(planned_check->>'evidence_requirement'=any(array[
                'redacted_evidence_required','metadata_only_sufficient']),false)
           or jsonb_typeof(planned_check->'failure_condition') is distinct from 'string'
           or not coalesce(btrim(planned_check->>'failure_condition')<>'',false)
     )
     or exists (
       select 1 from jsonb_array_elements(case
         when jsonb_typeof(receipt_slice->'planned_checks')='array'
         then receipt_slice->'planned_checks' else '[]'::jsonb end) planned_check
       group by planned_check->>'check_ref' having count(*)>1
     )
     or not coalesce(ops.engineering_receipt_identifier_array(
          receipt.receipt->'planned_resource_refs'),false)
     or not coalesce(ops.engineering_receipt_identifier_array(
          receipt.receipt->'actual_resource_refs'),false)
     or not coalesce(ops.engineering_receipt_identifier_array(
          receipt.receipt->'planned_component_refs'),false)
     or not coalesce(ops.engineering_receipt_identifier_array(
          receipt.receipt->'actual_component_refs'),false)
     or not coalesce(ops.engineering_receipt_identifier_array(
          receipt.receipt->'artifact_refs'),false)
     or not coalesce(ops.engineering_receipt_evidence_array(
          receipt.receipt->'evidence_refs'),false)
     or not coalesce(case
          when jsonb_typeof(receipt.receipt->'artifact_refs')='array'
          then jsonb_array_length(receipt.receipt->'artifact_refs')>0
          else false end,false)
     or not coalesce(case
          when jsonb_typeof(receipt.receipt->'evidence_refs')='array'
          then jsonb_array_length(receipt.receipt->'evidence_refs')>0
          else false end,false)
     or not coalesce(ops.engineering_receipt_identifier_sets_equal(
          receipt.receipt->'planned_resource_refs',
          receipt_slice->'declared_resource_refs'),false)
     or not coalesce(ops.engineering_receipt_identifier_sets_equal(
          receipt.receipt->'planned_component_refs',
          receipt_slice->'declared_component_refs'),false)
     or jsonb_typeof(receipt.receipt->'checks') is distinct from 'array'
     or not coalesce(case when jsonb_typeof(receipt.receipt->'checks')='array'
                          then jsonb_array_length(receipt.receipt->'checks')>0
                          else false end,false)
     or exists (
       select 1 from jsonb_array_elements(case
         when jsonb_typeof(receipt.receipt->'checks')='array'
         then receipt.receipt->'checks' else '[]'::jsonb end) receipt_check
        where not coalesce(ops.engineering_receipt_exact_object(
                receipt_check,array['check_ref','evidence_refs','state']),false)
           or not coalesce((receipt_check->>'check_ref') ~
                '^[A-Za-z][A-Za-z0-9._:-]{2,127}$',false)
           or receipt_check->>'state' is distinct from 'passed'
           or not coalesce(ops.engineering_receipt_evidence_array(
                receipt_check->'evidence_refs'),false)
           or not coalesce(case
                when jsonb_typeof(receipt_check->'evidence_refs')='array'
                then jsonb_array_length(receipt_check->'evidence_refs')>0
                else false end,false)
     )
     or exists (
       select 1 from jsonb_array_elements(case
         when jsonb_typeof(receipt.receipt->'checks')='array'
         then receipt.receipt->'checks' else '[]'::jsonb end) receipt_check
       group by receipt_check->>'check_ref' having count(*)>1
     )
     or exists (
       select 1 from jsonb_array_elements(case
         when jsonb_typeof(receipt.receipt->'checks')='array'
         then receipt.receipt->'checks' else '[]'::jsonb end) receipt_check
        where not exists (
          select 1 from jsonb_array_elements(case
            when jsonb_typeof(receipt_slice->'planned_checks')='array'
            then receipt_slice->'planned_checks' else '[]'::jsonb end) planned_check
           where planned_check->>'check_ref'=receipt_check->>'check_ref')
     )
     or exists (
       select 1 from jsonb_array_elements(case
         when jsonb_typeof(receipt_slice->'planned_checks')='array'
         then receipt_slice->'planned_checks' else '[]'::jsonb end) planned_check
        where not exists (
          select 1 from jsonb_array_elements(case
            when jsonb_typeof(receipt.receipt->'checks')='array'
            then receipt.receipt->'checks' else '[]'::jsonb end) receipt_check
           where receipt_check->>'check_ref'=planned_check->>'check_ref')
     )
     or exists (
       select 1 from jsonb_array_elements(case
         when jsonb_typeof(receipt.receipt->'checks')='array'
         then receipt.receipt->'checks' else '[]'::jsonb end) receipt_check
       join jsonb_array_elements(case
         when jsonb_typeof(receipt_slice->'planned_checks')='array'
         then receipt_slice->'planned_checks' else '[]'::jsonb end) planned_check
         on planned_check->>'check_ref'=receipt_check->>'check_ref'
        where not exists (
          select 1 from jsonb_array_elements(case
            when jsonb_typeof(receipt_check->'evidence_refs')='array'
            then receipt_check->'evidence_refs' else '[]'::jsonb end) evidence
           where evidence->>'redaction_class'=case
             when planned_check->>'evidence_requirement'='redacted_evidence_required'
             then 'redacted_evidence' else 'metadata_only' end)
     )
     or jsonb_typeof(receipt.receipt->'deviations') is distinct from 'array'
     or exists (
       select 1 from jsonb_array_elements(case
         when jsonb_typeof(receipt.receipt->'deviations')='array'
         then receipt.receipt->'deviations' else '[]'::jsonb end) deviation
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
           or deviation->>'review_state' is distinct from 'resolved'
           or deviation->'plan_revision_required' is distinct from 'false'::jsonb
     )
     or exists (
       select 1 from jsonb_array_elements(case
         when jsonb_typeof(receipt.receipt->'deviations')='array'
         then receipt.receipt->'deviations' else '[]'::jsonb end) deviation
       group by deviation->>'deviation_ref' having count(*)>1
     )
     or exists (
       select 1 from jsonb_array_elements(case
         when jsonb_typeof(receipt.receipt->'actual_resource_refs')='array'
         then receipt.receipt->'actual_resource_refs' else '[]'::jsonb end) actual_ref
        where not exists (
          select 1 from jsonb_array_elements(case
            when jsonb_typeof(receipt_slice->'declared_resource_refs')='array'
            then receipt_slice->'declared_resource_refs' else '[]'::jsonb end) declared_ref
           where declared_ref=actual_ref)
          and not exists (
            select 1 from jsonb_array_elements(case
              when jsonb_typeof(receipt.receipt->'deviations')='array'
              then receipt.receipt->'deviations' else '[]'::jsonb end) deviation,
              jsonb_array_elements(case
                when jsonb_typeof(deviation->'out_of_scope_resource_refs')='array'
                then deviation->'out_of_scope_resource_refs' else '[]'::jsonb end) approved_ref
             where deviation->>'review_state'='resolved' and approved_ref=actual_ref)
     )
     or exists (
       select 1 from jsonb_array_elements(case
         when jsonb_typeof(receipt.receipt->'actual_component_refs')='array'
         then receipt.receipt->'actual_component_refs' else '[]'::jsonb end) actual_ref
        where not exists (
          select 1 from jsonb_array_elements(case
            when jsonb_typeof(receipt_slice->'declared_component_refs')='array'
            then receipt_slice->'declared_component_refs' else '[]'::jsonb end) declared_ref
           where declared_ref=actual_ref)
          and not exists (
            select 1 from jsonb_array_elements(case
              when jsonb_typeof(receipt.receipt->'deviations')='array'
              then receipt.receipt->'deviations' else '[]'::jsonb end) deviation,
              jsonb_array_elements(case
                when jsonb_typeof(deviation->'out_of_scope_component_refs')='array'
                then deviation->'out_of_scope_component_refs' else '[]'::jsonb end) approved_ref
             where deviation->>'review_state'='resolved' and approved_ref=actual_ref)
     )
     or not coalesce(ops.engineering_receipt_exact_object(
          receipt.receipt->'source_evidence',
          array['branch_ref','evidence_refs','source_sha','worktree_ref']),false)
     or exists (
       select 1 from unnest(array['worktree_ref','branch_ref','source_sha']) field
        where jsonb_typeof(receipt.receipt->'source_evidence'->field)
                is distinct from 'string'
           or not coalesce(btrim(
                receipt.receipt->'source_evidence'->>field)<>'',false)
           or (field<>'source_sha' and not coalesce(
                (receipt.receipt->'source_evidence'->>field) ~
                '^[A-Za-z][A-Za-z0-9._:-]{2,127}$',false))
     )
     or not coalesce(ops.engineering_receipt_evidence_array(
          receipt.receipt->'source_evidence'->'evidence_refs'),false)
     or not coalesce(ops.engineering_receipt_exact_object(
          receipt.receipt->'reset_reconstruction',array[
            'fresh_session','inherited_transcript_used',
            'reconstruction_free','remediation_action']),false)
     or receipt.receipt->'reset_reconstruction'->'fresh_session'
          is distinct from 'true'::jsonb
     or receipt.receipt->'reset_reconstruction'->'inherited_transcript_used'
          is distinct from 'false'::jsonb
     or jsonb_typeof(receipt.receipt->'reset_reconstruction'
          ->'reconstruction_free') is distinct from 'boolean'
     or (receipt.receipt->'reset_reconstruction'->'reconstruction_free'='false'::jsonb
         and (jsonb_typeof(receipt.receipt->'reset_reconstruction'
                ->'remediation_action') is distinct from 'string'
              or not coalesce(btrim(receipt.receipt->'reset_reconstruction'
                ->>'remediation_action')<>'',false)))
     or (receipt.receipt->'reset_reconstruction'->'reconstruction_free'='true'::jsonb
         and receipt.receipt->'reset_reconstruction'->'remediation_action'
                is distinct from 'null'::jsonb
         and (jsonb_typeof(receipt.receipt->'reset_reconstruction'
                ->'remediation_action') is distinct from 'string'
              or not coalesce(btrim(receipt.receipt->'reset_reconstruction'
                ->>'remediation_action')<>'',false)))
     or not coalesce(ops.engineering_receipt_exact_object(
          receipt.receipt->'executor_claim',
          array['claim_state','claimed_at','claimed_by']),false)
     or receipt.receipt->'executor_claim'->>'claim_state'
          is distinct from 'executor_claim'
     or receipt.receipt->'executor_claim'->>'claimed_by'
          is distinct from session_slug
     or jsonb_typeof(receipt.receipt->'executor_claim'->'claimed_at')
          is distinct from 'string'
     or not coalesce(btrim(receipt.receipt->'executor_claim'
          ->>'claimed_at')<>'',false) then
    return ops.canonical_ownership_refusal('DEPENDENCY_UNSATISFIED','dependency',
      to_jsonb(p_required_state),jsonb_build_object(
        'slice_ref',p_slice_ref,'receipt_id',receipt.id,'outcome',receipt.outcome));
  end if;
  if p_required_state='completed' then
    return jsonb_build_object('ok',true,'envelope_id',leaf.id,'receipt_id',receipt.id,'reviewer_fact_id',null);
  end if;
  select f.* into review from ops.engineering_reviewer_fact f where f.receipt_id=receipt.id
   order by f.created_at desc,f.id desc limit 1;
  select a.slug into reviewer_slug from public.actor a
   where a.id=review.reviewer_actor_id and a.active;
  executor_session:=receipt.receipt#>>'{attribution,session_ref}';
  select coalesce(array_agg(d->>'deviation_ref' order by d->>'deviation_ref'),'{}'::text[])
    into deviation_refs from jsonb_array_elements(receipt.receipt->'deviations') d;
  if review.id is null or review.state is distinct from 'passed'
     or review.contract_version is distinct from 'engineering-review.v1'
     or review.work_request_id is distinct from receipt.work_request_id
     or review.slice_ref is distinct from receipt.slice_ref
     or reviewer_slug is null
     or not coalesce(ops.engineering_receipt_exact_object(review.fact,array[
          'attempt_id','evidence_refs','is_independent','resolved_deviation_refs',
          'reviewed_deviation_refs','reviewer_ref','session_ref','slice_ref','state'
        ]),false)
     or review.reviewer_actor_id=receipt.executor_actor_id
     or review.fact->>'state' is distinct from 'passed' or review.fact->'is_independent' is distinct from 'true'::jsonb
     or review.fact->>'attempt_id' is distinct from receipt.attempt_id or review.fact->>'slice_ref' is distinct from p_slice_ref
     or not coalesce(review.fact->>'reviewer_ref'=any(array[
          reviewer_slug,'actor:'||reviewer_slug,'reviewer:'||reviewer_slug
        ]),false)
     or review.reviewer_session_ref is distinct from review.fact->>'session_ref'
     or review.reviewer_session_ref=executor_session
     or not coalesce(ops.engineering_receipt_evidence_array(review.fact->'evidence_refs'),false)
     or not coalesce(case when jsonb_typeof(review.fact->'evidence_refs')='array'
                          then jsonb_array_length(review.fact->'evidence_refs')>0
                          else false end,false)
     or not coalesce(ops.engineering_receipt_identifier_array(
          review.fact->'reviewed_deviation_refs'),false)
     or not coalesce(ops.engineering_receipt_identifier_array(
          review.fact->'resolved_deviation_refs'),false)
     or not coalesce(ops.engineering_receipt_identifier_sets_equal(
          review.fact->'reviewed_deviation_refs',to_jsonb(deviation_refs)),false)
     or not coalesce(ops.engineering_receipt_identifier_sets_equal(
          review.fact->'resolved_deviation_refs',to_jsonb(deviation_refs)),false) then
    return ops.canonical_ownership_refusal('DEPENDENCY_UNSATISFIED','dependency',
      '"independently_verified"'::jsonb,jsonb_build_object(
        'slice_ref',p_slice_ref,'receipt_id',receipt.id,
        'reviewer_fact_id',review.id,'review_state',review.state));
  end if;
  return jsonb_build_object('ok',true,'envelope_id',leaf.id,'receipt_id',receipt.id,'reviewer_fact_id',review.id);
end $$;

-- ---------------------------------------------------------------------------
-- SECTION 6 -- comments and the ACL for the new functions.
-- ---------------------------------------------------------------------------

comment on function ops.engineering_slice_plan_slice_fields(text) is
  'The exact accepted slice field set for one engineering-slice-plan schema version. '
  'NULL means the version is unsupported; every caller treats NULL as a refusal.';

comment on function ops.engineering_plan_identifier_list(jsonb) is
  'An array of well-formed identifier strings, duplicates allowed. This is the exact '
  'predicate the JS and Python plan validators apply to a slice declared-ref array; '
  'ops.engineering_receipt_identifier_array additionally requires uniqueness and is '
  'used everywhere the source validators require it.';

comment on function ops.engineering_slice_plan_refusal(jsonb) is
  'Whole-plan validator for a typed engineering slice plan. Returns NULL when the plan '
  'is accepted, otherwise a stable refusal token. engineering-slice-plan.v1 is accepted '
  'after its top-level shape and version only, so registered v1 plans are not '
  'retroactively upgraded; engineering-slice-plan.v2 is held to the full closed '
  'plan/slice/design-contract rules, unique ordinals, acyclic dependencies, unique seam '
  'authority, parallel-safe resource isolation, and a plan_digest that binds its own '
  'canonical content. Unknown slice-plan and design-contract versions are refused, never '
  'defaulted. It reads no table, takes no lock and writes nothing, and it is the single '
  'statement of those rules for both callers: ops.engineering_register_slice_plan (new '
  'plans) and ops.engineering_record_slice_receipt (already-stored plans, before append).';

comment on function ops.engineering_register_slice_plan(text,jsonb,text,uuid) is
  'Register one typed Engineering Slice Plan as an immutable projection of the exact '
  'accepted sourced plan. Every 0310 binding, idempotency and refusal behavior is '
  'unchanged; an engineering-slice-plan.v2 plan is additionally held to the whole-plan '
  'contract by ops.engineering_slice_plan_refusal, which closes the direct carr_writer '
  'bypass of the caller-side validators.';

comment on function ops.canonical_ownership_plan_dependencies(uuid,text) is
  'Canonical dependency snapshot for one slice of one slice plan. The accepted slice '
  'field set is selected by the plan''s own sealed schema_version, so an '
  'engineering-slice-plan.v2 slice is read rather than refused; an unsupported version '
  'and a malformed slice both still fail closed.';

comment on function ops.canonical_ownership_dependency_state(uuid,uuid,text,text) is
  'Current Engineering lineage state for one dependency slice. The accepted slice field '
  'set is selected by the plan''s own sealed schema_version; every dependency, lease, '
  'currentness, identity and reviewer rule is unchanged, and an unsupported version or a '
  'malformed slice still fails closed.';

-- Strip the default PUBLIC EXECUTE from the new SECURITY DEFINER functions, the
-- convention of 0335:2114 and 0450:1215.
--
-- This names ONLY functions this file creates.  The three functions this file
-- REPLACES are deliberately absent: CREATE OR REPLACE preserved their owner and
-- their existing ACLs -- the 0310:399 grant to carr_writer on the registration
-- seam, and the 0450:1224-1225 revocation from every role on the two ownership
-- functions -- and touching either here would be a change to an existing grant.
revoke all on function
  ops.engineering_slice_plan_slice_fields(text),
  ops.engineering_plan_identifier_list(jsonb),
  ops.engineering_slice_plan_refusal(jsonb)
  from public,carr_reader,carr_writer,carr_jobs,carr_authority;

