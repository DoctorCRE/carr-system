-- f03-receipt-validator-postgres.sql
--
-- Bounded acceptance/negative fixture for the candidate receipt validator in
-- ops/f03-receipt-validator.candidate.sql.
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
--   * It never applies the candidate.  Applying the candidate to an isolated
--     scratch database is a separate, explicit operator step (below).
--   * Run it ONLY against an isolated scratch database.  Do not run it against
--     production, and do not run it against any database whose Engineering
--     ledgers carry real evidence.
--
-- ===========================================================================
-- PREREQUISITES (all expected to be built by the operator in the scratch DB)
-- ===========================================================================
--
-- P0. An isolated scratch PostgreSQL database with the repository migrations
--     applied, with public.digest available (pgcrypto), and with
--     ops/f03-receipt-validator.candidate.sql applied on top of them.
--
--     WHICH CANDIDATE EACH PART NEEDS, exactly:
--
--       Part A  needs ops/f03-receipt-validator.candidate.sql ONLY, on a
--               database migrated through
--               migrations/0335_engineering_controller_currentness.sql.  It
--               calls exactly four functions from it --
--               ops.engineering_receipt_design_contract_refusal(jsonb),
--               ops.engineering_receipt_design_depth(jsonb,text),
--               ops.engineering_receipt_design_binding_refusal(jsonb,jsonb) and
--               ops.engineering_receipt_design_identifier_subset(jsonb,jsonb)
--               (A63..A67) -- plus a pg_proc ACL read over the ten helpers that
--               candidate creates.  Every one of the four is created by that
--               candidate, and the subset predicate validates its superset
--               inline rather than calling ops.engineering_plan_identifier_list,
--               so Part A acquires nothing from the companion candidate: it does
--               NOT call ops.engineering_slice_plan_refusal and does not need the
--               companion.  A55 fails with "function ... does not exist" if
--               candidate 1 is absent; that is the intended fail-closed signal.
--
--       Part A2 needs exactly what Part A needs and adds nothing: it calls one
--               function, ops.engineering_receipt_design_contract_refusal, over
--               cases generated from the shared JS/Python corpus rather than
--               written here.  See the PART A2 banner above its \ir.
--
--       Part B  needs BOTH candidates for its engineering-slice-plan.v2 cases,
--               because the replaced receipt seam calls
--               ops.engineering_slice_plan_refusal(jsonb), which
--               ops/f03-plan-ownership-validator.candidate.sql installs.  The
--               order the two candidates are applied in does not matter: both
--               are plpgsql and the dependency resolves at execution, not at
--               CREATE.  Part B DETECTS whether the companion is present and
--               asserts the corresponding behavior either way, so a
--               half-installed database is a covered case rather than a broken
--               run -- see the B08..B12 block.  The v1 cases need candidate 1
--               only.
--
--     IF YOU APPLY THE COMPANION CANDIDATE, MIGRATE THROUGH 0450 FIRST.  It
--     CREATE OR REPLACEs ops.canonical_ownership_plan_dependencies and
--     ops.canonical_ownership_dependency_state, which
--     migrations/0450_canonical_ownership_lease_kernel.sql defines.  On a
--     database that stopped at 0335 those two would be CREATED rather than
--     replaced, landing lease-kernel functions with a default ACL on a database
--     that never ran 0450.  Migrate through 0450 or do not apply the companion.
--
-- P1. Part A (the pure predicate cases) needs nothing else.  It reads no table
--     and writes no row outside its own temp tables.
--
-- P1b. Run the script as a role that can create temp objects and, for Part B,
--     insert directly into ops.engineering_slice_plan,
--     ops.engineering_execution_envelope and ops.job_attempt -- normally the
--     scratch database owner.  The fixture deliberately does NOT grant anything
--     to obtain those rights, and carr_writer is expected to lack them.
--
-- P2. Part B (the seam cases) needs one scratch execution lane.  The lane is
--     NOT created here, because ops.work_request, ops.sourced_work_request_plan,
--     ops.sourced_work_request_plan_acceptance_receipt, public.actor,
--     ops.capability_agent_session and ops.job are defined by migrations
--     outside the two paths this candidate owns, and inventing their columns
--     here would be a guess.  Part B is skipped with a loud NOTICE when the
--     lane is absent.  The lane must satisfy exactly what
--     ops.engineering_envelope_currentness (0335:126) and
--     ops.engineering_record_slice_receipt require:
--
--       a. ops.work_request with state='ready', a
--          ops.sourced_work_request_plan and a matching
--          ops.sourced_work_request_plan_acceptance_receipt, so that
--          ops.engineering_admission_source(<ref>) returns non-null.
--       b. public.actor with active=true, kind='automation', slug='codex'.
--       c. ops.capability_agent_session for that Work Request with
--          executor_actor_id = the actor in (b), state in
--          ('claimed','in_progress'), scope_ref = 'slice:'||<slice_ref>,
--          worktree_ref = 'engineering:server-admission',
--          source_commit_sha = repeat('0',40), and lease_expires_at set to a
--          WHOLE SECOND (date_trunc('second', ...)) at least ~20 minutes in
--          the future.  The whole second matters: currentness compares the
--          envelope JSON instant rendered with
--          to_char(...,'YYYY-MM-DD"T"HH24:MI:SS"Z"') against the column.
--       d. ops.job with definition_key='engineering-slice', definition_version=1,
--          mode='shadow', state='running', attempt >= 1 (the receipt table
--          requires attempt_id ~ '^attempt:[1-9][0-9]*$'), a lease_token equal
--          to the \set lane_lease_token below, leased_until at least
--          960 seconds in the future, and payload
--          {"work_request":<ref>,"slice_ref":<slice_ref>,"plan_digest":<sha256>,
--           "generation":1}.  The payload plan_digest is the digest this fixture
--          registers its per-case slice plans under, so the two agree by
--          construction (ops.engineering_envelope_currentness:167 and :179
--          require payload->>'plan_digest' to equal both the registered row's
--          plan_digest column and the stored plan's own plan_digest field).
--
--          ONE MORE REQUIREMENT FOR THE v2 ACCEPT CASE (B02).  The replaced seam
--          now validates the whole stored v2 plan, which includes "the digest
--          binds the canonical content".  Because currentness forces the stored
--          plan's digest to be the lane payload's digest, B02 can only append
--          when the lane payload plan_digest IS the canonical digest of the
--          exact plan body this fixture builds.  The fixture does not mutate the
--          lane to arrange that: it computes the required digest, reports it,
--          and SKIPS B02 loudly when the lane carries a different one.  Put the
--          reported value in the lane job payload (and in the registered plan's
--          digest, which this fixture derives from the payload) to run B02.
--          No other case depends on this: every other v2 case is refused by a
--          check that precedes the digest test.
--          The job must have NO ops.engineering_execution_envelope yet, and its
--          accepted plan must have NO ops.engineering_slice_plan yet: this
--          fixture inserts one of each per case and rolls both back.
--       e. ops.job_definition ('engineering-slice',1) enabled -- migration 0310
--          already inserts this.
--
--     Fill the four \set values below with that lane.  Part B inserts the
--     ops.job_attempt row itself, using the exact five columns
--     ops.engineering_claim_slice (0335:340) inserts.
--
-- P3. Part B inserts ops.engineering_execution_envelope using the exact column
--     list declared in migrations/0310_engineering_execution_fabric.sql:30.  The
--     live column set at db/schema.sql:29142-29167 adds only the two NULLABLE
--     0311 columns supersedes_envelope_id and supersession_reason, whose
--     CHECK (engineering_envelope_supersession_travels_together) is satisfied
--     with both NULL, so no later NOT NULL column without a default exists and
--     this insert is feasible as written.  A Part B setup-error on this insert
--     should therefore be read as a real environment difference, not as an
--     expected gap.

\set ON_ERROR_STOP on

-- Scratch lane parameters.  The all-zero UUIDs are deliberate placeholders:
-- leave them and Part B skips loudly instead of touching anything.
\set lane_work_request_ref 'REPLACE-WITH-SCRATCH-WORK-REQUEST-REF'
\set lane_job_id '00000000-0000-0000-0000-000000000000'
\set lane_lease_token '00000000-0000-0000-0000-000000000000'
\set lane_agent_session_id '00000000-0000-0000-0000-000000000000'

begin;

-- ===========================================================================
-- PART A -- pure predicate cases for the new v2 surface.
--
-- These call the candidate's private predicates directly with literal JSON.
-- They read no table, take no lock and touch no ledger, so they are the part a
-- reviewer can run against a scratch database with nothing else in it.
-- ===========================================================================

create temporary table f03_base(kind text primary key, slice jsonb) on commit drop;

-- A valid FULL-depth engineering-slice-plan.v2 slice.  R4 puts it outside the
-- SHORT risk band, so the frozen Q035.D1 predicate classifies it FULL and it
-- must carry full_design_refs with short_template null.
insert into f03_base(kind, slice)
values (
  'full',
  replace($json$
{
  "slice_ref": "slice:f03-receipt-source",
  "ordinal": 1,
  "objective": "Teach the one existing receipt seam engineering-slice-plan.v2.",
  "definition_of_done": "A v1 receipt is unchanged and an exact valid v2 receipt appends.",
  "scope_boundary": "ops.engineering_record_slice_receipt and its private predicates only.",
  "dependency_refs": [],
  "declared_resource_refs": ["resource:engineering-receipt-seam"],
  "declared_component_refs": ["component:engineering-receipt-validator"],
  "declared_plan_step_refs": ["step:validate-v2-design-contract"],
  "forbidden_change_refs": ["change:migration-ordinal"],
  "baseline_evidence_refs": [],
  "planned_checks": [
    {
      "check_ref": "check:v1-receipt-unchanged",
      "failure_condition": "an engineering-slice-plan.v1 receipt is refused or accepted differently",
      "evidence_requirement": "metadata_only_sufficient"
    },
    {
      "check_ref": "check:v2-receipt-accepted",
      "failure_condition": "an exact valid engineering-slice-plan.v2 receipt cannot append",
      "evidence_requirement": "redacted_evidence_required"
    }
  ],
  "concurrency_posture": "parallel_safe",
  "manual_qa_required": false,
  "risk_class": "R4",
  "release_requirement": "not_required",
  "design_contract": {
    "contract_version": "engineering-design-contract.v1",
    "rationale": "The receipt seam is the last fail-closed gate before immutable evidence.",
    "dependency_rationale": "No slice dependency: this slice replaces one validator in place.",
    "code_model_decision": {
      "rationale": "Contract validation is deterministic; no judgment step is required.",
      "selection_basis": ["typed_uncertainty", "cost"],
      "model_judgment_steps": []
    },
    "routing": {
      "executor_class": "deterministic_code",
      "adapter_ref": "adapter:codex-desktop",
      "fresh_session_required": true
    },
    "authority": {
      "capability_profile": "capability:engineering-repository-write",
      "read_only": false,
      "environment": "rehearsal"
    },
    "isolation": {
      "worktree_required": true,
      "branch_required": true,
      "shared_resource_refs": []
    },
    "tests": {
      "planned_check_refs": ["check:v1-receipt-unchanged", "check:v2-receipt-accepted"],
      "verification_lanes": ["unit", "contract"]
    },
    "review": {
      "independent_review_required": true,
      "reviewer_class": "independent_agent"
    },
    "failure": {
      "failure_modes": [
        {
          "failure_ref": "failure:v2-receipt-cannot-append",
          "detection": "the seam raises on a registered v2 plan",
          "compensation": "the transaction rolls back and the lease remains claimable"
        }
      ]
    },
    "evidence": {
      "redaction_class": "redacted_evidence",
      "retention": "material_redacted",
      "evidence_refs": [
        {
          "ref": "evidence:f03-candidate-source",
          "redaction_class": "redacted_evidence",
          "content_digest": "@DIGEST@"
        }
      ]
    },
    "deployment": {
      "release_requirement": "not_required",
      "rollback_ref": null,
      "confirmation_required": true
    },
    "completion": {
      "completion_predicate": "an independent reviewer confirms v1 parity and v2 acceptance",
      "verified_by": "independent_review"
    },
    "seam_decision": {
      "mode": "extend",
      "target_seam_ref": "seam:engineering-record-slice-receipt",
      "measurement": {
        "basis": "complexity_reduction",
        "note": "one receipt validator keeps one slice-contract authority"
      },
      "new_module_justification": null,
      "replaced_seam_refs": [],
      "residual_authority_refs": []
    },
    "full_design_refs": {
      "design_interview_ref": "design:f03-receipt-source",
      "authority_envelope_ref": "authority:engineering-receipt-seam",
      "failure_model_ref": "failure-model:f03-receipt-source",
      "oracle_ref": "oracle:f03-receipt-source",
      "fixture_refs": ["fixture:f03-receipt-validator-postgres"]
    },
    "short_template": null
  }
}
$json$, '@DIGEST@', 'sha256:' || repeat('a', 64))::jsonb
);

-- A valid SHORT-depth slice: R1, parallel-safe, no manual QA, no release
-- requirement, zero dependencies and at most one declared resource, component
-- and plan step.  SHORT changes design-template depth only; it grants no
-- authority and waives no gate.
insert into f03_base(kind, slice)
values (
  'short',
  replace($json$
{
  "slice_ref": "slice:f03-short-example",
  "ordinal": 2,
  "objective": "Exercise the SHORT branch of the frozen design-depth predicate.",
  "definition_of_done": "The short governed template is required and the full envelope is refused.",
  "scope_boundary": "fixture only",
  "dependency_refs": [],
  "declared_resource_refs": ["resource:engineering-receipt-seam"],
  "declared_component_refs": ["component:engineering-receipt-validator"],
  "declared_plan_step_refs": ["step:classify-design-depth"],
  "forbidden_change_refs": [],
  "baseline_evidence_refs": [],
  "planned_checks": [
    {
      "check_ref": "check:short-depth-classified",
      "failure_condition": "the classifier returns full for a short slice",
      "evidence_requirement": "metadata_only_sufficient"
    }
  ],
  "concurrency_posture": "parallel_safe",
  "manual_qa_required": false,
  "risk_class": "R1",
  "release_requirement": "not_required",
  "design_contract": {
    "contract_version": "engineering-design-contract.v1",
    "rationale": "A single deterministic predicate with one declared step.",
    "dependency_rationale": "No dependency is declared.",
    "code_model_decision": {
      "rationale": "Deterministic classification only.",
      "selection_basis": ["typed_uncertainty"],
      "model_judgment_steps": []
    },
    "routing": {
      "executor_class": "deterministic_code",
      "adapter_ref": "adapter:codex-desktop",
      "fresh_session_required": true
    },
    "authority": {
      "capability_profile": "capability:engineering-repository-write",
      "read_only": false,
      "environment": "rehearsal"
    },
    "isolation": {
      "worktree_required": true,
      "branch_required": true,
      "shared_resource_refs": []
    },
    "tests": {
      "planned_check_refs": ["check:short-depth-classified"],
      "verification_lanes": ["unit"]
    },
    "review": {
      "independent_review_required": true,
      "reviewer_class": "independent_agent"
    },
    "failure": {
      "failure_modes": [
        {
          "failure_ref": "failure:short-misclassified",
          "detection": "depth disagrees with the frozen predicate",
          "compensation": "refuse the contract"
        }
      ]
    },
    "evidence": {
      "redaction_class": "metadata_only",
      "retention": "ephemeral",
      "evidence_refs": [
        {
          "ref": "evidence:f03-short-example",
          "redaction_class": "metadata_only",
          "content_digest": "@DIGEST@"
        }
      ]
    },
    "deployment": {
      "release_requirement": "not_required",
      "rollback_ref": null,
      "confirmation_required": false
    },
    "completion": {
      "completion_predicate": "the classifier returns short and the template is bound",
      "verified_by": "independent_review"
    },
    "seam_decision": {
      "mode": "reuse",
      "target_seam_ref": "seam:engineering-record-slice-receipt",
      "measurement": {
        "basis": "coverage",
        "note": "one additional covered branch"
      },
      "new_module_justification": null,
      "replaced_seam_refs": [],
      "residual_authority_refs": []
    },
    "full_design_refs": null,
    "short_template": {
      "template_ref": "template:engineering-short-design",
      "objective_summary": "Classify one accepted slice and bind its template.",
      "verification_ref": "verification:f03-short-example"
    }
  }
}
$json$, '@DIGEST@', 'sha256:' || repeat('b', 64))::jsonb
);

create temporary table f03_contract_case(
  name text primary key,
  slice jsonb not null,
  expected text
) on commit drop;

-- expected NULL means "the contract is accepted".  Every other row names the
-- exact refusal token the candidate must return.
insert into f03_contract_case(name, slice, expected)
select * from (values
  -- ---- acceptance ------------------------------------------------------
  ('A01-accept-full-depth-contract',
   (select slice from f03_base where kind='full'), null::text),
  ('A02-accept-short-depth-contract',
   (select slice from f03_base where kind='short'), null),

  -- ---- shape, version and self-labelling -------------------------------
  ('A03-refuse-missing-design-contract',
   (select slice - 'design_contract' from f03_base where kind='full'),
   'design_contract'),
  ('A04-refuse-unknown-design-contract-version',
   (select jsonb_set(slice, '{design_contract,contract_version}',
                     '"engineering-design-contract.v2"') from f03_base where kind='full'),
   'design_contract.contract_version'),
  ('A05-refuse-unknown-contract-field',
   (select jsonb_set(slice, '{design_contract,extra_field}', '"x"', true)
      from f03_base where kind='full'),
   'design_contract.shape'),
  ('A06-refuse-contract-self-label',
   (select jsonb_set(slice, '{design_contract,design_depth}', '"short"', true)
      from f03_base where kind='full'),
   'design_contract.design_depth_self_label'),
  ('A07-refuse-slice-self-label',
   (select jsonb_set(slice, '{classifier_override}', '"short"', true)
      from f03_base where kind='full'),
   'slice.design_depth_self_label'),
  ('A08-refuse-untyped-classifier-input',
   (select jsonb_set(slice, '{manual_qa_required}', '"false"') from f03_base where kind='full'),
   'design_contract.design_depth'),

  -- ---- Q029.D1 selection basis and Q016.D1 model judgment steps --------
  ('A09-refuse-cost-only-selection-basis',
   (select jsonb_set(slice, '{design_contract,code_model_decision,selection_basis}', '["cost"]')
      from f03_base where kind='full'),
   'design_contract.code_model_decision.selection_basis'),
  ('A10-refuse-reserved-responsibility-step',
   (select jsonb_set(
             jsonb_set(slice, '{design_contract,routing,executor_class}', '"model_assisted"'),
             '{design_contract,code_model_decision,model_judgment_steps}',
             '[{"step_ref":"step:validate-v2-design-contract","responsibility_class":"validation",
                "input_contract_ref":"contract:slice-plan-v2","output_contract_ref":"contract:receipt-refusal",
                "rationale":"model owns validation","selection_basis":["typed_uncertainty"]}]'::jsonb)
      from f03_base where kind='full'),
   'design_contract.code_model_decision.model_judgment_steps'),
  ('A11-refuse-undeclared-model-step',
   (select jsonb_set(
             jsonb_set(slice, '{design_contract,routing,executor_class}', '"model_assisted"'),
             '{design_contract,code_model_decision,model_judgment_steps}',
             '[{"step_ref":"step:not-declared","responsibility_class":"classification",
                "input_contract_ref":"contract:slice-plan-v2","output_contract_ref":"contract:receipt-refusal",
                "rationale":"typed uncertainty","selection_basis":["typed_uncertainty"]}]'::jsonb)
      from f03_base where kind='full'),
   'design_contract.code_model_decision.model_judgment_steps'),

  -- ---- routing and authority -------------------------------------------
  ('A12-refuse-deterministic-route-with-model-step',
   (select jsonb_set(slice, '{design_contract,code_model_decision,model_judgment_steps}',
             '[{"step_ref":"step:validate-v2-design-contract","responsibility_class":"classification",
                "input_contract_ref":"contract:slice-plan-v2","output_contract_ref":"contract:receipt-refusal",
                "rationale":"typed uncertainty","selection_basis":["typed_uncertainty"]}]'::jsonb)
      from f03_base where kind='full'),
   'design_contract.routing.executor_class'),
  ('A13-refuse-model-route-without-model-step',
   (select jsonb_set(slice, '{design_contract,routing,executor_class}', '"model_assisted"')
      from f03_base where kind='full'),
   'design_contract.routing.executor_class'),
  ('A14-refuse-inherited-session',
   (select jsonb_set(slice, '{design_contract,routing,fresh_session_required}', 'false')
      from f03_base where kind='full'),
   'design_contract.routing.fresh_session_required'),
  ('A15-refuse-write-authority-without-repository-profile',
   (select jsonb_set(slice, '{design_contract,authority,capability_profile}',
                     '"capability:engineering-read-only"') from f03_base where kind='full'),
   'design_contract.authority.capability_profile'),
  ('A16-refuse-unknown-environment',
   (select jsonb_set(slice, '{design_contract,authority,environment}', '"sandbox"')
      from f03_base where kind='full'),
   'design_contract.authority.environment'),

  -- ---- isolation --------------------------------------------------------
  ('A17-refuse-shared-resource-under-parallel-safe',
   (select jsonb_set(slice, '{design_contract,isolation,shared_resource_refs}',
                     '["resource:engineering-receipt-seam"]') from f03_base where kind='full'),
   'design_contract.isolation.shared_resource_refs'),
  ('A18-refuse-undeclared-shared-resource',
   (select jsonb_set(
             jsonb_set(slice, '{concurrency_posture}', '"serial_after_dependencies"'),
             '{design_contract,isolation,shared_resource_refs}', '["resource:never-declared"]')
      from f03_base where kind='full'),
   'design_contract.isolation.shared_resource_refs'),
  ('A19-refuse-non-isolated-worktree',
   (select jsonb_set(slice, '{design_contract,isolation,worktree_required}', 'false')
      from f03_base where kind='full'),
   'design_contract.isolation.worktree_required'),

  -- ---- tests ------------------------------------------------------------
  ('A20-refuse-reordered-planned-check-refs',
   (select jsonb_set(slice, '{design_contract,tests,planned_check_refs}',
                     '["check:v2-receipt-accepted","check:v1-receipt-unchanged"]')
      from f03_base where kind='full'),
   'design_contract.tests.planned_check_refs'),
  ('A21-refuse-missing-planned-check-ref',
   (select jsonb_set(slice, '{design_contract,tests,planned_check_refs}',
                     '["check:v1-receipt-unchanged"]') from f03_base where kind='full'),
   'design_contract.tests.planned_check_refs'),
  ('A22-refuse-manual-qa-lane-without-manual-qa',
   (select jsonb_set(slice, '{design_contract,tests,verification_lanes}',
                     '["unit","manual_qa"]') from f03_base where kind='full'),
   'design_contract.tests.verification_lanes'),
  ('A23-refuse-unknown-verification-lane',
   (select jsonb_set(slice, '{design_contract,tests,verification_lanes}', '["smoke"]')
      from f03_base where kind='full'),
   'design_contract.tests.verification_lanes'),

  -- ---- review, failure and evidence -------------------------------------
  ('A24-refuse-unsupported-reviewer-class',
   (select jsonb_set(slice, '{design_contract,review,reviewer_class}', '"independent_human"')
      from f03_base where kind='full'),
   'design_contract.review.reviewer_class'),
  ('A25-refuse-optional-independent-review',
   (select jsonb_set(slice, '{design_contract,review,independent_review_required}', 'false')
      from f03_base where kind='full'),
   'design_contract.review.independent_review_required'),
  ('A26-refuse-empty-failure-model',
   (select jsonb_set(slice, '{design_contract,failure,failure_modes}', '[]')
      from f03_base where kind='full'),
   'design_contract.failure.failure_modes'),
  ('A27-refuse-evidence-class-mismatch',
   (select jsonb_set(slice, '{design_contract,evidence,redaction_class}', '"metadata_only"')
      from f03_base where kind='full'),
   'design_contract.evidence.evidence_refs'),
  ('A28-refuse-metadata-only-when-a-check-requires-redacted-evidence',
   (select jsonb_set(
             jsonb_set(slice, '{design_contract,evidence,redaction_class}', '"metadata_only"'),
             '{design_contract,evidence,evidence_refs,0,redaction_class}', '"metadata_only"')
      from f03_base where kind='full'),
   'design_contract.evidence.redaction_class'),

  -- ---- deployment and completion ----------------------------------------
  ('A29-refuse-unconfirmed-above-r1',
   (select jsonb_set(slice, '{design_contract,deployment,confirmation_required}', 'false')
      from f03_base where kind='full'),
   'design_contract.deployment.confirmation_required'),
  ('A30-refuse-release-requirement-drift',
   (select jsonb_set(slice, '{design_contract,deployment,release_requirement}', '"required"')
      from f03_base where kind='full'),
   'design_contract.deployment.release_requirement'),
  ('A31-refuse-verifier-mismatch',
   (select jsonb_set(slice, '{design_contract,completion,verified_by}',
                     '"independent_review_and_manual_qa"') from f03_base where kind='full'),
   'design_contract.completion.verified_by'),

  -- ---- seam decision -----------------------------------------------------
  ('A32-refuse-replacement-naming-no-retired-seam',
   (select jsonb_set(slice, '{design_contract,seam_decision,mode}', '"replace"')
      from f03_base where kind='full'),
   'design_contract.seam_decision.replaced_seam_refs'),
  ('A33-refuse-seam-replacing-itself',
   (select jsonb_set(
             jsonb_set(slice, '{design_contract,seam_decision,mode}', '"replace"'),
             '{design_contract,seam_decision,replaced_seam_refs}',
             '["seam:engineering-record-slice-receipt"]') from f03_base where kind='full'),
   'design_contract.seam_decision.replaced_seam_refs'),
  ('A34-refuse-residual-authority',
   (select jsonb_set(slice, '{design_contract,seam_decision,residual_authority_refs}',
                     '["seam:legacy-receipt-writer"]') from f03_base where kind='full'),
   'design_contract.seam_decision.residual_authority_refs'),
  ('A35-refuse-new-module-without-justification',
   (select jsonb_set(slice, '{design_contract,seam_decision,mode}', '"new_module"')
      from f03_base where kind='full'),
   'design_contract.seam_decision.new_module_justification'),
  ('A36-refuse-justification-without-new-module',
   (select jsonb_set(slice, '{design_contract,seam_decision,new_module_justification}',
                     '"authority"') from f03_base where kind='full'),
   'design_contract.seam_decision.new_module_justification'),

  -- ---- Q035.D1 depth material -------------------------------------------
  ('A37-refuse-short-template-on-full-slice',
   (select jsonb_set(slice, '{design_contract,short_template}',
             '{"template_ref":"template:engineering-short-design",
               "objective_summary":"summary","verification_ref":"verification:x"}'::jsonb)
      from f03_base where kind='full'),
   'design_contract.short_template'),
  ('A38-refuse-full-slice-without-full-design-refs',
   (select jsonb_set(slice, '{design_contract,full_design_refs}', 'null')
      from f03_base where kind='full'),
   'design_contract.full_design_refs'),
  ('A39-refuse-full-design-refs-on-short-slice',
   (select jsonb_set(slice, '{design_contract,full_design_refs}',
             '{"design_interview_ref":"design:x","authority_envelope_ref":"authority:x",
               "failure_model_ref":"failure-model:x","oracle_ref":"oracle:x",
               "fixture_refs":["fixture:x"]}'::jsonb)
      from f03_base where kind='short'),
   'design_contract.full_design_refs'),
  ('A40-refuse-short-slice-without-template',
   (select jsonb_set(slice, '{design_contract,short_template}', 'null')
      from f03_base where kind='short'),
   'design_contract.short_template'),
  ('A41-refuse-empty-fixture-refs',
   (select jsonb_set(slice, '{design_contract,full_design_refs,fixture_refs}', '[]')
      from f03_base where kind='full'),
   'design_contract.full_design_refs.fixture_refs'),

  -- ---- non-object and absent input --------------------------------------
  ('A42-refuse-array-slice', '[]'::jsonb, 'slice'),
  ('A43-refuse-json-null-slice', 'null'::jsonb, 'slice'),
  ('A44-refuse-null-design-contract',
   (select jsonb_set(slice, '{design_contract}', 'null') from f03_base where kind='full'),
   'design_contract'),

  -- ---- isolation: DECLARED-REF DUPLICATE PARITY ------------------------------
  -- ops.engineering_receipt_design_identifier_subset is split on purpose: the
  -- subset (isolation.shared_resource_refs) must be unique, the superset
  -- (declared_resource_refs) may repeat.  Both source validators say exactly
  -- that -- isUniqueIdentifierArray on the shared set, a plain membership test
  -- against the declared set, and no uniqueness rule on any declared array -- and
  -- the whole-plan validator reaches this contract at PLAN REGISTRATION, where
  -- nothing else has required the declared array to be unique.  A57 and A58 are
  -- refused by a both-sides-unique predicate and accepted by this one; A59..A62
  -- pin that loosening the superset did not make the test vacuous.
  ('A57-accept-duplicate-declared-resources-with-an-empty-shared-set',
   (select jsonb_set(slice, '{declared_resource_refs}',
             '["resource:engineering-receipt-seam","resource:engineering-receipt-seam"]')
      from f03_base where kind='full'),
   null),
  ('A58-accept-duplicate-declared-resources-with-a-non-empty-shared-subset',
   (select jsonb_set(
             jsonb_set(
               jsonb_set(slice, '{concurrency_posture}', '"serial_after_dependencies"'),
               '{declared_resource_refs}',
               '["resource:engineering-receipt-seam","resource:engineering-receipt-seam","resource:second"]'),
             '{design_contract,isolation,shared_resource_refs}',
             '["resource:engineering-receipt-seam"]')
      from f03_base where kind='full'),
   null),
  ('A59-refuse-duplicate-shared-resource-refs',
   (select jsonb_set(
             jsonb_set(slice, '{concurrency_posture}', '"serial_after_dependencies"'),
             '{design_contract,isolation,shared_resource_refs}',
             '["resource:engineering-receipt-seam","resource:engineering-receipt-seam"]')
      from f03_base where kind='full'),
   'design_contract.isolation.shared_resource_refs'),
  ('A60-refuse-malformed-declared-resource-superset',
   (select jsonb_set(slice, '{declared_resource_refs}', '["not a valid identifier"]')
      from f03_base where kind='full'),
   'design_contract.isolation.shared_resource_refs'),
  ('A61-refuse-non-string-declared-resource-superset',
   (select jsonb_set(slice, '{declared_resource_refs}', '[1]')
      from f03_base where kind='full'),
   'design_contract.isolation.shared_resource_refs'),
  -- A declared_resource_refs that is not an ARRAY at all is refused earlier, by
  -- the frozen classifier's own typing gate, so its token is the depth one.  The
  -- boundary between the two refusals is pinned here rather than assumed.
  ('A62-refuse-non-array-declared-resource-superset',
   (select jsonb_set(slice, '{declared_resource_refs}', '{}')
      from f03_base where kind='full'),
   'design_contract.design_depth')
) as cases(name, slice, expected);

do $$
declare row_case record; actual text; failures integer := 0; total integer := 0;
begin
  for row_case in select name, slice, expected from f03_contract_case order by name loop
    total := total + 1;
    actual := ops.engineering_receipt_design_contract_refusal(row_case.slice);
    if actual is distinct from row_case.expected then
      failures := failures + 1;
      raise warning 'PART A FAIL % : expected %, got %',
        row_case.name, coalesce(row_case.expected, '<accept>'), coalesce(actual, '<accept>');
    end if;
  end loop;
  if failures > 0 then
    raise exception 'PART A: % of % design-contract cases did not match', failures, total;
  end if;
  raise notice 'PART A: % design-contract cases matched', total;
end $$;

-- The frozen Q035.D1 predicate itself, including its fail-closed NULL for an
-- unsupported design-contract version.
do $$
declare failures integer := 0;
begin
  if ops.engineering_receipt_design_depth(
       (select slice from f03_base where kind='full'), 'engineering-design-contract.v1') is distinct from 'full'
  then failures := failures + 1; raise warning 'PART A FAIL A45: R4 slice must classify full'; end if;
  if ops.engineering_receipt_design_depth(
       (select slice from f03_base where kind='short'), 'engineering-design-contract.v1') is distinct from 'short'
  then failures := failures + 1; raise warning 'PART A FAIL A46: R1 isolated slice must classify short'; end if;
  -- one dependency is enough to leave the SHORT band
  if ops.engineering_receipt_design_depth(
       (select jsonb_set(slice, '{dependency_refs}', '["slice:other"]') from f03_base where kind='short'),
       'engineering-design-contract.v1') is distinct from 'full'
  then failures := failures + 1; raise warning 'PART A FAIL A47: a dependency must leave the short band'; end if;
  -- an unsupported predicate version is unclassifiable, never defaulted
  if ops.engineering_receipt_design_depth(
       (select slice from f03_base where kind='short'), 'engineering-design-contract.v2') is not null
  then failures := failures + 1; raise warning 'PART A FAIL A48: unknown contract version must not classify'; end if;
  if failures > 0 then raise exception 'PART A: % design-depth cases did not match', failures; end if;
  raise notice 'PART A: 4 design-depth cases matched';
end $$;

-- The v2 contract-versus-envelope binding comparison.  It compares and refuses
-- only; it never widens or reissues a binding.
create temporary table f03_binding_case(
  name text primary key,
  slice jsonb not null,
  envelope jsonb not null,
  expected text
) on commit drop;

insert into f03_binding_case(name, slice, envelope, expected)
select * from (values
  ('A49-accept-matching-binding',
   (select slice from f03_base where kind='full'),
   '{"server_binding":{"adapter":{"adapter_id":"adapter:codex-desktop"},
     "authority":{"environment":"rehearsal","capability_profile":"capability:engineering-repository-write",
                  "read_only":false}}}'::jsonb,
   null::text),
  ('A50-refuse-adapter-mismatch',
   (select slice from f03_base where kind='full'),
   '{"server_binding":{"adapter":{"adapter_id":"adapter:other-desktop"},
     "authority":{"environment":"rehearsal","capability_profile":"capability:engineering-repository-write",
                  "read_only":false}}}'::jsonb,
   'design_contract.routing.adapter_ref'),
  ('A51-refuse-environment-mismatch',
   (select slice from f03_base where kind='full'),
   '{"server_binding":{"adapter":{"adapter_id":"adapter:codex-desktop"},
     "authority":{"environment":"production","capability_profile":"capability:engineering-repository-write",
                  "read_only":false}}}'::jsonb,
   'design_contract.authority.environment'),
  ('A52-refuse-read-only-posture-mismatch',
   (select jsonb_set(slice, '{design_contract,authority,read_only}', 'true')
      from f03_base where kind='full'),
   '{"server_binding":{"adapter":{"adapter_id":"adapter:codex-desktop"},
     "authority":{"environment":"rehearsal","capability_profile":"capability:engineering-repository-write",
                  "read_only":false}}}'::jsonb,
   'design_contract.authority.read_only'),
  ('A53-refuse-attended-human-executor',
   (select jsonb_set(slice, '{design_contract,routing,executor_class}', '"attended_human"')
      from f03_base where kind='full'),
   '{"server_binding":{"adapter":{"adapter_id":"adapter:codex-desktop"},
     "authority":{"environment":"rehearsal","capability_profile":"capability:engineering-repository-write",
                  "read_only":false}}}'::jsonb,
   'design_contract.routing.executor_class'),
  ('A54-refuse-envelope-without-server-binding',
   (select slice from f03_base where kind='full'),
   '{}'::jsonb,
   'envelope.server_binding')
) as cases(name, slice, envelope, expected);

-- A55/A56.  The ten new helpers are SECURITY DEFINER, so an unrevoked default
-- ACL would be a PUBLIC-reachable indirect call channel into the four 0335
-- helpers that migrations/0335_engineering_controller_currentness.sql:2114
-- explicitly revoked.  This reads pg_proc only: it writes nothing, and it
-- deliberately does NOT grant or revoke anything itself.
--
-- proacl is NULL for a function that has never had a grant changed, and NULL
-- means "the default", which for a function is EXECUTE to PUBLIC.  acldefault()
-- is therefore substituted so the unrevoked case is detected rather than read as
-- an empty ACL.
do $$
declare wanted text[] := array[
          'ops.engineering_receipt_design_string(jsonb)',
          'ops.engineering_receipt_design_identifier(jsonb)',
          'ops.engineering_receipt_design_enum(jsonb,text[])',
          'ops.engineering_receipt_design_identifier_subset(jsonb,jsonb)',
          'ops.engineering_receipt_design_self_label_free(jsonb)',
          'ops.engineering_receipt_design_selection_basis(jsonb)',
          'ops.engineering_receipt_design_model_steps(jsonb,jsonb)',
          'ops.engineering_receipt_design_depth(jsonb,text)',
          'ops.engineering_receipt_design_contract_refusal(jsonb)',
          'ops.engineering_receipt_design_binding_refusal(jsonb,jsonb)'];
        missing text; offenders text; present integer;
begin
  select string_agg(sig, ', ' order by sig) into missing
    from unnest(wanted) as sigs(sig) where to_regprocedure(sig) is null;
  if missing is not null then
    raise exception 'PART A FAIL A55: the candidate is not applied, these functions are absent: %', missing;
  end if;
  select count(*) into present from unnest(wanted) as sigs(sig);
  select string_agg(format('%s->%s', sig, grantee), '; ' order by sig, grantee) into offenders
    from (
      select sigs.sig,
             case when acl.grantee = 0 then 'PUBLIC' else acl.grantee::regrole::text end as grantee
        from unnest(wanted) as sigs(sig)
        join pg_catalog.pg_proc p on p.oid = to_regprocedure(sigs.sig)
        cross join lateral aclexplode(
          coalesce(p.proacl, acldefault('f', p.proowner))) acl
       where acl.privilege_type = 'EXECUTE'
         and (acl.grantee = 0
              or acl.grantee::regrole::text = any(array[
                   'carr_reader','carr_writer','carr_jobs','carr_authority']))
    ) q;
  if offenders is not null then
    raise exception
      'PART A FAIL A56: new SECURITY DEFINER helpers still carry EXECUTE for public or an application role: %',
      offenders;
  end if;
  raise notice 'PART A: % new helpers exist and carry no EXECUTE for public or any application role', present;
end $$;

do $$
declare row_case record; actual text; failures integer := 0; total integer := 0;
begin
  for row_case in select name, slice, envelope, expected from f03_binding_case order by name loop
    total := total + 1;
    actual := ops.engineering_receipt_design_binding_refusal(row_case.slice, row_case.envelope);
    if actual is distinct from row_case.expected then
      failures := failures + 1;
      raise warning 'PART A FAIL % : expected %, got %',
        row_case.name, coalesce(row_case.expected, '<accept>'), coalesce(actual, '<accept>');
    end if;
  end loop;
  if failures > 0 then
    raise exception 'PART A: % of % binding cases did not match', failures, total;
  end if;
  raise notice 'PART A: % binding cases matched', total;
end $$;

-- A63-A67.  The split subset predicate itself, called directly.  A57..A62 reach
-- it through the whole contract; these pin the predicate's own boundary, so a
-- future "tidy-up" that makes both sides unique -- or that drops the superset's
-- typing to buy the duplicate acceptance -- fails a check instead of silently
-- changing what registers.  The receipt seam separately requires
-- declared_resource_refs to be a UNIQUE identifier array before a bound slice
-- reaches the design contract, so the asymmetry is observable only at plan
-- registration, which is where both source validators accept a repeated ref.
do $$
declare failures integer := 0;
begin
  if ops.engineering_receipt_design_identifier_subset(
       '[]'::jsonb, '["resource:a","resource:a"]'::jsonb) is not true then
    failures := failures + 1;
    raise warning 'PART A FAIL A63: a duplicate superset with an empty subset must be accepted';
  end if;
  if ops.engineering_receipt_design_identifier_subset(
       '["resource:a"]'::jsonb, '["resource:a","resource:a","resource:b"]'::jsonb) is not true then
    failures := failures + 1;
    raise warning 'PART A FAIL A64: a duplicate superset with a non-empty subset must be accepted';
  end if;
  if ops.engineering_receipt_design_identifier_subset(
       '["resource:a","resource:a"]'::jsonb, '["resource:a"]'::jsonb) is not false then
    failures := failures + 1;
    raise warning 'PART A FAIL A65: the subset must still be required to be unique';
  end if;
  if ops.engineering_receipt_design_identifier_subset('[]'::jsonb, '["not an identifier"]'::jsonb) is not false
     or ops.engineering_receipt_design_identifier_subset('[]'::jsonb, '[1]'::jsonb) is not false
     or ops.engineering_receipt_design_identifier_subset('[]'::jsonb, '{}'::jsonb) is not false
     or ops.engineering_receipt_design_identifier_subset('[]'::jsonb, 'null'::jsonb) is not false then
    failures := failures + 1;
    raise warning 'PART A FAIL A66: a malformed superset must never make an empty subset vacuously true';
  end if;
  if ops.engineering_receipt_design_identifier_subset(
       '["resource:b"]'::jsonb, '["resource:a","resource:a"]'::jsonb) is not false then
    failures := failures + 1;
    raise warning 'PART A FAIL A67: containment must still be required';
  end if;
  if failures > 0 then raise exception 'PART A: % subset predicate cases did not match', failures; end if;
  raise notice 'PART A: 5 subset predicate cases matched';
end $$;

-- ===========================================================================
-- PART A2 -- the SHARED corpus, generated, not hand-written.
--
-- Every case above was written here, which is the one thing they cannot prove:
-- that this validator agrees with the two it must agree with.  A hand-written
-- case table stays in step with the JS and Python corpus only for as long as
-- somebody keeps it there, so on the day this fixture is finally run against a
-- database it would have proved the candidate against a DIFFERENT set of cases
-- than the validators it mirrors.
--
-- Part A2 is that missing binding.  Every case in the included file is derived
-- from mcp-server/test/fixtures/f03-design-contract-parity.v1.json -- the same
-- corpus f03-design-contract-parity.test.mjs replays through requirePlan and
-- validate_engineering_slice_plan -- by
-- mcp-server/test/f03-sql-corpus-generator.mjs.  A vector added to the corpus
-- for JS and Python is automatically a case this leg must satisfy, with nothing
-- to keep up to date by hand.  The included file is GENERATED and byte-checked
-- against the corpus by that same JS suite, so editing it, or changing the
-- corpus without regenerating, is caught in CI long before a database.
--
-- IT KEEPS PART A's SINGLE-CANDIDATE PROPERTY.  It calls exactly one function,
-- ops.engineering_receipt_design_contract_refusal, which
-- ops/f03-receipt-validator.candidate.sql installs.  It needs no companion
-- candidate, no ledger table, no admission source and no execution lane.
--
-- WHAT IT DELIBERATELY DOES NOT ASSERT.  The corpus states its verdicts for a
-- WHOLE PLAN and this seam validates ONE SLICE, so a corpus rejection is
-- asserted here only where the rejection is structurally incapable of being a
-- plan-level one.  The generated header lists the exact test, and every vector
-- it excludes is named with its reason in f03_corpus_slice_excluded, counted at
-- run time, and asserted in full against the whole-plan seam in
-- mcp-server/test/f03-plan-ownership-validator-postgres.sql Part E.  Nothing is
-- dropped; it moves to the seam that owns it.
-- ===========================================================================
\ir f03-design-contract-corpus-slice-postgres.generated.sql

-- ===========================================================================
-- PART B -- the seam itself, against a scratch execution lane.
--
-- Each case inserts one slice plan and one envelope, calls
-- ops.engineering_record_slice_receipt directly (the finalize wrapper is
-- unchanged and delegates to it), records what the seam did, and then rolls the
-- whole case back through a deliberate sentinel exception.  Nothing a case
-- writes survives the case, and nothing survives the script.
--
-- Part B calls the record function rather than the finalize wrapper on purpose:
-- the wrapper transitions the queue lease, and this fixture must not consume a
-- scratch lane's lease between cases.
--
-- The plan row is INSERTED rather than registered on purpose too.  A directly
-- inserted row is exactly what a carr_writer bypassing
-- ops.engineering_register_slice_plan leaves behind, and it is exactly what an
-- engineering-slice-plan.v2 plan stored before either candidate was applied
-- looks like.  That is the population B08..B10 are about: the register-side
-- replacement in the companion candidate binds future registrations only and
-- cannot reach a stored row, so the whole-plan check at this seam is the only
-- thing standing between an already-stored malformed v2 plan and an immutable
-- receipt.  Registration-time refusals belong to
-- mcp-server/test/f03-plan-ownership-validator-postgres.sql and are not
-- re-tested here.
-- ===========================================================================

create temporary table f03_lane(
  work_request_ref text not null,
  job_id uuid not null,
  lease_token uuid not null,
  agent_session_id uuid not null
) on commit drop;

insert into f03_lane(work_request_ref, job_id, lease_token, agent_session_id)
values (:'lane_work_request_ref', :'lane_job_id'::uuid,
        :'lane_lease_token'::uuid, :'lane_agent_session_id'::uuid);

create temporary table f03_seam_case(
  name text primary key,
  ordinal integer not null,
  plan_schema_version text not null,
  design_contract_mode text not null,
  adapter_ref text not null,
  plan_shape text not null,
  needs_binding_digest boolean not null,
  needs_companion boolean not null,
  expected text
) on commit drop;

-- expected NULL means "the receipt appends".  Every other row names a substring
-- the raised message must contain.
--
-- design_contract_mode:
--   'absent'      -- slice carries no design_contract (the v1 shape)
--   'valid'       -- slice carries the valid v2 contract
--   'self_label'  -- slice carries a v2 contract with a forbidden self-label
--
-- plan_shape -- what the STORED plan looks like around the bound slice.  This is
-- how the already-registered-and-malformed cases are expressed: every one of
-- them is inserted directly into ops.engineering_slice_plan, which is exactly
-- what a carr_writer bypassing the registration seam leaves behind, and exactly
-- what the whole-plan check at the receipt seam exists to refuse.
--   'bound_slice_only'  -- one slice, the bound one
--   'digest_drift'      -- the bound slice's content is not what plan_digest binds
--   'duplicate_ordinal' -- a second declared slice reuses the bound slice's ordinal
--   'dependency_cycle'  -- two further slices depend on each other
--
-- needs_binding_digest -- the case can only ACCEPT when the lane job payload's
-- plan_digest is the canonical digest of the plan body this fixture builds (see
-- PREREQUISITE P2d).  Such a case is SKIPPED loudly, with the required digest
-- printed, rather than failed.
--
-- needs_companion -- the case reaches ops.engineering_slice_plan_refusal, so its
-- expectation holds only when ops/f03-plan-ownership-validator.candidate.sql is
-- also applied.  When it is absent the loop replaces the expectation with the
-- fail-closed 'function ops.engineering_slice_plan_refusal(jsonb) does not
-- exist'.  That is the half-installed / wrong-order behavior, asserted rather
-- than assumed.
insert into f03_seam_case(name, ordinal, plan_schema_version, design_contract_mode, adapter_ref,
                          plan_shape, needs_binding_digest, needs_companion, expected)
values
  ('B01-accept-v1-plan-unchanged', 1, 'engineering-slice-plan.v1', 'absent',
   'adapter:codex-desktop', 'bound_slice_only', false, false, null),
  ('B02-accept-v2-plan-with-valid-contract', 2, 'engineering-slice-plan.v2', 'valid',
   'adapter:codex-desktop', 'bound_slice_only', true, true, null),
  ('B03-refuse-unknown-plan-schema-version', 3, 'engineering-slice-plan.v3', 'absent',
   'adapter:codex-desktop', 'bound_slice_only', false, false,
   'slice plan schema version is not supported'),
  ('B04-refuse-v2-plan-missing-design-contract', 4, 'engineering-slice-plan.v2', 'absent',
   'adapter:codex-desktop', 'bound_slice_only', false, false,
   'bound slice plan is not fully typed'),
  ('B05-refuse-v1-plan-carrying-design-contract', 5, 'engineering-slice-plan.v1', 'valid',
   'adapter:codex-desktop', 'bound_slice_only', false, false,
   'bound slice plan is not fully typed'),
  ('B06-refuse-v2-plan-with-invalid-contract', 6, 'engineering-slice-plan.v2', 'self_label',
   'adapter:codex-desktop', 'bound_slice_only', false, false, 'design contract is invalid'),
  ('B07-refuse-v2-contract-contradicting-the-envelope', 7, 'engineering-slice-plan.v2', 'valid',
   'adapter:some-other-desktop', 'bound_slice_only', false, false,
   'contradicts the server execution binding'),

  -- ---- already-stored malformed v2 whole plans ----------------------------
  -- In every one of these the BOUND slice is well formed and would have passed
  -- 0335's one-slice validation.  Under hunks 2/3 alone they would append.  The
  -- whole-plan check is the only thing that refuses them, and the expected
  -- prefix pins the seam's own message, not just the token.
  ('B08-refuse-stored-v2-plan-whose-digest-does-not-bind-content', 8,
   'engineering-slice-plan.v2', 'valid', 'adapter:codex-desktop', 'digest_drift', false, true,
   'not a valid typed slice plan: plan.plan_digest_does_not_bind_content'),
  ('B09-refuse-stored-v2-plan-with-duplicate-ordinals', 9,
   'engineering-slice-plan.v2', 'valid', 'adapter:codex-desktop', 'duplicate_ordinal', false, true,
   'not a valid typed slice plan: plan.duplicate_ordinal'),
  ('B10-refuse-stored-v2-plan-with-a-dependency-cycle', 10,
   'engineering-slice-plan.v2', 'valid', 'adapter:codex-desktop', 'dependency_cycle', false, true,
   'not a valid typed slice plan: plan.dependency_cycle'),

  -- ---- the v1 branch is untouched by any of this --------------------------
  -- The same plan-wide fault as B09, on a v1 plan, must still append: the
  -- whole-plan call is inside the v2 branch, so a v1 receipt never reaches it --
  -- including when the companion candidate is absent, which is what makes this
  -- case discriminating rather than decorative.
  ('B11-accept-v1-plan-with-a-plan-wide-fault', 11,
   'engineering-slice-plan.v1', 'absent', 'adapter:codex-desktop', 'duplicate_ordinal',
   false, false, null);

-- A further declared slice of the stored plan, used only to give the whole-plan
-- cases something to be wrong about.  It is never the bound slice, so it never
-- changes what the receipt seam's one-slice checks see.
--
-- risk_class is R4 on purpose: R4 is outside the SHORT band, so the frozen
-- Q035.D1 predicate classifies it FULL whatever its dependency count, which is
-- why it carries full_design_refs with short_template null and
-- deployment.confirmation_required true (the explicit confirmation gate above R1
-- is retained, not waived).  Each slice names its own resource and its own seam,
-- so the only plan-wide fault present is the one the case is about.
create function pg_temp.f03_extra_slice(
  p_slice_ref text, p_name text, p_ordinal integer,
  p_dependency_refs jsonb, p_with_contract boolean
) returns jsonb language plpgsql as $extra$
declare v_slice jsonb;
begin
  v_slice := jsonb_build_object(
    'slice_ref', p_slice_ref,
    'ordinal', p_ordinal,
    'objective', 'a further declared slice of the stored plan: ' || p_name,
    'definition_of_done', 'the whole-plan check behaves exactly as the case expects',
    'scope_boundary', 'scratch lane only',
    'dependency_refs', p_dependency_refs,
    'declared_resource_refs', jsonb_build_array('resource:' || p_name),
    'declared_component_refs', jsonb_build_array('component:' || p_name),
    'declared_plan_step_refs', jsonb_build_array('step:' || p_name),
    'forbidden_change_refs', '[]'::jsonb,
    'baseline_evidence_refs', '[]'::jsonb,
    'planned_checks', jsonb_build_array(jsonb_build_object(
      'check_ref', 'check:' || p_name,
      'failure_condition', 'the whole-plan check does not behave as the case expects',
      'evidence_requirement', 'metadata_only_sufficient')),
    'concurrency_posture', 'parallel_safe',
    'manual_qa_required', false,
    'risk_class', 'R4',
    'release_requirement', 'not_required');
  if not p_with_contract then return v_slice; end if;
  return jsonb_set(v_slice, '{design_contract}', jsonb_build_object(
    'contract_version', 'engineering-design-contract.v1',
    'rationale', 'a further declared slice of the same stored plan',
    'dependency_rationale', 'the declared dependency set is exactly what this slice waits on',
    'code_model_decision', jsonb_build_object(
      'rationale', 'deterministic validation only',
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
      'detection', 'the seam refuses the receipt',
      'compensation', 'the transaction rolls back'))),
    'evidence', jsonb_build_object(
      'redaction_class', 'metadata_only', 'retention', 'ephemeral',
      'evidence_refs', jsonb_build_array(jsonb_build_object(
        'ref', 'evidence:' || p_name, 'redaction_class', 'metadata_only',
        'content_digest', 'sha256:' || repeat('2', 64)))),
    'deployment', jsonb_build_object(
      'release_requirement', 'not_required', 'rollback_ref', null::jsonb,
      'confirmation_required', true),
    'completion', jsonb_build_object(
      'completion_predicate', 'the whole-plan check reaches its verdict',
      'verified_by', 'independent_review'),
    'seam_decision', jsonb_build_object(
      'mode', 'reuse', 'target_seam_ref', 'seam:' || p_name,
      'measurement', jsonb_build_object('basis', 'coverage', 'note', 'one covered plan-wide branch'),
      'new_module_justification', null::jsonb,
      'replaced_seam_refs', '[]'::jsonb, 'residual_authority_refs', '[]'::jsonb),
    'full_design_refs', jsonb_build_object(
      'design_interview_ref', 'design:' || p_name,
      'authority_envelope_ref', 'authority:' || p_name,
      'failure_model_ref', 'failure-model:' || p_name,
      'oracle_ref', 'oracle:' || p_name,
      'fixture_refs', jsonb_build_array('fixture:f03-receipt-validator-postgres')),
    'short_template', null::jsonb), true);
end $extra$;

-- One case runner.  It is a pg_temp function so it disappears with the session
-- and with this transaction's ROLLBACK; it creates nothing durable.
create function pg_temp.f03_run_seam_case(
  p_plan_schema_version text, p_design_contract_mode text, p_adapter_ref text,
  p_plan_shape text, p_needs_binding_digest boolean
) returns text
language plpgsql
as $runner$
declare lane record; lane_job record; lane_session record; v_source jsonb;
        v_work_request_id uuid; v_accepted_plan_id uuid; v_slice_ref text; v_plan_digest text;
        v_actor_id uuid; v_actor_slug text; v_revision jsonb; v_slice jsonb; v_plan jsonb;
        v_slices jsonb; v_canonical_digest text;
        v_slice_plan_id uuid;
        v_envelope_id uuid; v_envelope jsonb; v_envelope_digest text;
        v_issued_at timestamptz; v_expires_at timestamptz;
        v_receipt jsonb; v_receipt_digest text;
        v_contract jsonb;
begin
  select * into lane from f03_lane;
  select * into lane_job from ops.job where id = lane.job_id;
  if not found then return 'setup-error: lane job row not found'; end if;
  select * into lane_session from ops.capability_agent_session where id = lane.agent_session_id;
  if not found then return 'setup-error: lane capability_agent_session row not found'; end if;
  if lane_session.lease_expires_at is null then
    return 'setup-error: lane capability_agent_session has no lease_expires_at';
  end if;
  v_source := ops.engineering_admission_source(lane.work_request_ref);
  if v_source is null then
    return 'setup-error: ops.engineering_admission_source returned null for the lane Work Request';
  end if;
  select a.id, a.slug into v_actor_id, v_actor_slug
    from public.actor a where a.id = lane_session.executor_actor_id;
  if not found then return 'setup-error: lane executor actor row not found'; end if;

  v_work_request_id := regexp_replace(v_source->'work_request'->>'id', '^wr:', '')::uuid;
  v_accepted_plan_id := (v_source->'accepted_plan'->>'record_id')::uuid;
  v_slice_ref := lane_job.payload->>'slice_ref';
  v_plan_digest := lane_job.payload->>'plan_digest';
  if v_slice_ref is null or v_plan_digest is null then
    return 'setup-error: lane job payload is missing slice_ref or plan_digest';
  end if;
  v_revision := jsonb_build_object(
    'id', v_source->'accepted_plan'->>'plan_ref',
    'revision', (v_source->'accepted_plan'->>'revision')::integer,
    'digest', v_source->'accepted_plan'->>'digest');

  -- The bound slice.  Its 16 v1 fields are identical in every case; only the
  -- presence and content of design_contract and the schema version vary.
  v_contract := jsonb_build_object(
    'contract_version', 'engineering-design-contract.v1',
    'rationale', 'scratch lane contract',
    'dependency_rationale', 'no dependency is declared',
    'code_model_decision', jsonb_build_object(
      'rationale', 'deterministic validation only',
      'selection_basis', jsonb_build_array('typed_uncertainty'),
      'model_judgment_steps', '[]'::jsonb),
    'routing', jsonb_build_object(
      'executor_class', 'deterministic_code',
      'adapter_ref', p_adapter_ref,
      'fresh_session_required', true),
    'authority', jsonb_build_object(
      'capability_profile', 'capability:engineering-repository-write',
      'read_only', false,
      'environment', 'rehearsal'),
    'isolation', jsonb_build_object(
      'worktree_required', true, 'branch_required', true,
      'shared_resource_refs', '[]'::jsonb),
    'tests', jsonb_build_object(
      'planned_check_refs', jsonb_build_array('check:f03-scratch'),
      'verification_lanes', jsonb_build_array('contract')),
    'review', jsonb_build_object(
      'independent_review_required', true, 'reviewer_class', 'independent_agent'),
    'failure', jsonb_build_object('failure_modes', jsonb_build_array(jsonb_build_object(
      'failure_ref', 'failure:f03-scratch',
      'detection', 'the seam refuses the receipt',
      'compensation', 'the transaction rolls back'))),
    'evidence', jsonb_build_object(
      'redaction_class', 'metadata_only', 'retention', 'ephemeral',
      'evidence_refs', jsonb_build_array(jsonb_build_object(
        'ref', 'evidence:f03-scratch', 'redaction_class', 'metadata_only',
        'content_digest', 'sha256:' || repeat('c', 64)))),
    'deployment', jsonb_build_object(
      'release_requirement', 'not_required', 'rollback_ref', null::jsonb,
      'confirmation_required', false),
    'completion', jsonb_build_object(
      'completion_predicate', 'the scratch receipt appends',
      'verified_by', 'independent_review'),
    'seam_decision', jsonb_build_object(
      'mode', 'reuse', 'target_seam_ref', 'seam:engineering-record-slice-receipt',
      'measurement', jsonb_build_object('basis', 'coverage', 'note', 'one covered seam branch'),
      'new_module_justification', null::jsonb,
      'replaced_seam_refs', '[]'::jsonb, 'residual_authority_refs', '[]'::jsonb),
    'full_design_refs', null::jsonb,
    'short_template', jsonb_build_object(
      'template_ref', 'template:engineering-short-design',
      'objective_summary', 'scratch lane slice',
      'verification_ref', 'verification:f03-scratch'));
  if p_design_contract_mode = 'self_label' then
    v_contract := jsonb_set(v_contract, '{design_depth}', '"short"', true);
  end if;

  -- R1, parallel-safe, no manual QA, no release requirement, no dependency and
  -- one declared resource/component/plan step: the frozen predicate classifies
  -- this SHORT, so the contract above carries short_template and no full refs.
  v_slice := jsonb_build_object(
    'slice_ref', v_slice_ref,
    'ordinal', 1,
    'objective', 'scratch lane slice',
    'definition_of_done', 'the scratch receipt appends or is refused as expected',
    'scope_boundary', 'scratch lane only',
    'dependency_refs', '[]'::jsonb,
    'declared_resource_refs', jsonb_build_array('resource:f03-scratch'),
    'declared_component_refs', jsonb_build_array('component:f03-scratch'),
    'declared_plan_step_refs', jsonb_build_array('step:f03-scratch'),
    'forbidden_change_refs', '[]'::jsonb,
    'baseline_evidence_refs', '[]'::jsonb,
    'planned_checks', jsonb_build_array(jsonb_build_object(
      'check_ref', 'check:f03-scratch',
      'failure_condition', 'the seam does not behave as the case expects',
      'evidence_requirement', 'metadata_only_sufficient')),
    'concurrency_posture', 'parallel_safe',
    'manual_qa_required', false,
    'risk_class', 'R1',
    'release_requirement', 'not_required');
  if p_design_contract_mode <> 'absent' then
    v_slice := jsonb_set(v_slice, '{design_contract}', v_contract, true);
  end if;

  -- The rest of the stored plan around the bound slice.  The bound slice is
  -- always slices[0] and is never altered except by 'digest_drift', so every
  -- one-slice check at the seam sees the same thing in every case.
  v_slices := jsonb_build_array(v_slice);
  if p_plan_shape = 'duplicate_ordinal' then
    -- ordinal 1 is the bound slice's ordinal, so the plan declares it twice.
    v_slices := v_slices || jsonb_build_array(pg_temp.f03_extra_slice(
      'slice:f03-extra-ordinal', 'f03-extra-ordinal', 1, '[]'::jsonb,
      p_design_contract_mode <> 'absent'));
  elsif p_plan_shape = 'dependency_cycle' then
    v_slices := v_slices || jsonb_build_array(
      pg_temp.f03_extra_slice('slice:f03-cycle-x', 'f03-cycle-x', 2,
        jsonb_build_array('slice:f03-cycle-y'), p_design_contract_mode <> 'absent'),
      pg_temp.f03_extra_slice('slice:f03-cycle-y', 'f03-cycle-y', 3,
        jsonb_build_array('slice:f03-cycle-x'), p_design_contract_mode <> 'absent'));
  elsif p_plan_shape = 'digest_drift' then
    -- Every field stays valid; only the CONTENT moves away from whatever the
    -- stored plan_digest binds.  Currentness forces that digest to be the lane
    -- payload's, so this body cannot be the one the operator sealed for B02.
    v_slices := jsonb_set(v_slices, '{0,objective}',
      '"content the stored plan digest does not cover"');
  end if;

  v_plan := jsonb_build_object(
    'schema_version', p_plan_schema_version,
    'plan_digest', v_plan_digest,
    'work_request', jsonb_build_object(
      'id', 'wr:' || v_work_request_id::text,
      'state_version', (v_source->'work_request'->>'version')::integer,
      'canonical_record_digest', v_source->'work_request'->>'canonical_record_digest'),
    'accepted_plan_revision', v_revision,
    'slices', v_slices);

  -- Before anything is written: the whole-plan check requires the stored
  -- plan_digest to bind the canonical content, and currentness (0335:167, :179)
  -- forces that digest to be the lane job payload's.  A case that expects an
  -- APPEND therefore cannot run unless the lane was configured with the digest
  -- of this exact body.  Report the required value and skip rather than fail,
  -- and mutate nothing to arrange it.
  v_canonical_digest := 'sha256:' || encode(public.digest(
    ops.guidance_import_canonical_json(v_plan - 'plan_digest'), 'sha256'), 'hex');
  if p_needs_binding_digest and v_canonical_digest is distinct from v_plan_digest then
    return 'lane-digest-mismatch: set the lane job payload plan_digest to ' || v_canonical_digest;
  end if;

  insert into ops.engineering_slice_plan
    (work_request_id, accepted_plan_id, accepted_plan_hash, work_request_version,
     plan_digest, plan, idempotency_key)
  values (v_work_request_id, v_accepted_plan_id, v_source->'accepted_plan'->>'digest',
          (v_source->'work_request'->>'version')::integer, v_plan_digest, v_plan, gen_random_uuid())
  returning id into v_slice_plan_id;

  -- The envelope this fixture issues is the exact shape
  -- ops.engineering_envelope_currentness requires, with the instants aligned to
  -- the lane session's whole-second lease.
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
        'plan_step_refs', jsonb_build_array('step:f03-scratch'),
        'component_refs', jsonb_build_array('component:f03-scratch'),
        'resource_refs', jsonb_build_array('resource:f03-scratch'),
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

  -- The claimed attempt.  If the lane already carries one for this attempt and
  -- lease token, it is used as is; otherwise the fixture inserts exactly the
  -- five columns ops.engineering_claim_slice (0335:340) inserts.
  perform 1 from ops.job_attempt
   where job_id = lane_job.id and attempt = lane_job.attempt
     and lease_token = lane.lease_token and state = 'running';
  if not found then
    insert into ops.job_attempt(job_id, attempt, lease_owner, lease_token, state)
    values (lane_job.id, lane_job.attempt,
            coalesce(lane_job.lease_owner, 'f03-fixture'), lane.lease_token, 'running');
  end if;

  v_receipt := jsonb_build_object(
    'schema_version', 'engineering-slice-receipt.v1',
    'plan_digest', v_plan_digest,
    'envelope_digest', v_envelope_digest,
    'slice_ref', v_slice_ref,
    'attempt_id', 'attempt:' || lane_job.attempt,
    'outcome', 'claimed_complete',
    'planned_resource_refs', jsonb_build_array('resource:f03-scratch'),
    'actual_resource_refs', jsonb_build_array('resource:f03-scratch'),
    'planned_component_refs', jsonb_build_array('component:f03-scratch'),
    'actual_component_refs', jsonb_build_array('component:f03-scratch'),
    'artifact_refs', jsonb_build_array('artifact:f03-candidate-source'),
    'evidence_refs', jsonb_build_array(jsonb_build_object(
      'ref', 'evidence:f03-scratch-receipt', 'redaction_class', 'metadata_only',
      'content_digest', 'sha256:' || repeat('f', 64))),
    'checks', jsonb_build_array(jsonb_build_object(
      'check_ref', 'check:f03-scratch', 'state', 'passed',
      'evidence_refs', jsonb_build_array(jsonb_build_object(
        'ref', 'evidence:f03-scratch-check', 'redaction_class', 'metadata_only',
        'content_digest', 'sha256:' || repeat('1', 64))))),
    'deviations', '[]'::jsonb,
    'source_evidence', jsonb_build_object(
      'worktree_ref', 'worktree:f03-scratch', 'branch_ref', 'branch:f03-scratch',
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
    return 'accepted';
  exception when others then
    return 'refused: ' || sqlerrm;
  end;
end $runner$;

do $$
declare row_case record; result text; failures integer := 0; total integer := 0;
        skipped integer := 0; case_expected text; companion boolean;
        lane record; job_exists boolean;
begin
  select * into lane from f03_lane;
  select exists(select 1 from ops.job where id = lane.job_id) into job_exists;
  if not job_exists then
    raise notice 'PART B SKIPPED: no scratch lane job % -- see the PREREQUISITES header. No seam case ran.',
      lane.job_id;
    return;
  end if;

  -- Which half of the pair is installed.  Both halves are asserted: with the
  -- companion present the whole-plan verdicts below are expected; with it absent
  -- every case that reaches the call must instead fail closed on the missing
  -- function, in whichever order the two candidates were applied.
  companion := to_regprocedure('ops.engineering_slice_plan_refusal(jsonb)') is not null;
  if companion then
    raise notice 'PART B: ops.engineering_slice_plan_refusal is present -- asserting whole-plan verdicts';
  else
    raise notice 'PART B: ops.engineering_slice_plan_refusal is ABSENT -- asserting the fail-closed half-installed behavior';
  end if;

  for row_case in select * from f03_seam_case order by ordinal loop
    total := total + 1;
    -- The full message is 'function ops.engineering_slice_plan_refusal(jsonb)
    -- does not exist'; only the substring has to appear in sqlerrm.
    case_expected := case when row_case.needs_companion and not companion
                          then 'does not exist' else row_case.expected end;
    -- Each case runs inside a subtransaction that is always rolled back through
    -- the sentinel below, so its slice plan, envelope, attempt and any receipt
    -- disappear before the next case runs.  plpgsql variables assigned before
    -- the sentinel survive the rollback, which is how the result escapes.
    begin
      result := pg_temp.f03_run_seam_case(
        row_case.plan_schema_version, row_case.design_contract_mode, row_case.adapter_ref,
        row_case.plan_shape, row_case.needs_binding_digest and companion);
      raise exception '__f03_case_rollback__';
    exception when others then
      if sqlerrm <> '__f03_case_rollback__' then
        result := 'setup-error: ' || sqlerrm;
      end if;
    end;

    if result is not null and position('lane-digest-mismatch' in result) = 1 then
      -- Not a failure and not a pass: the lane cannot express this case.  See
      -- PREREQUISITE P2d; the message carries the exact digest to configure.
      skipped := skipped + 1;
      raise notice 'PART B SKIPPED % : %', row_case.name, result;
    elsif case_expected is null then
      if result <> 'accepted' then
        failures := failures + 1;
        raise warning 'PART B FAIL % : expected the receipt to append, got %', row_case.name, result;
      end if;
    elsif result is null or position(case_expected in result) = 0 then
      failures := failures + 1;
      raise warning 'PART B FAIL % : expected a refusal containing "%", got %',
        row_case.name, case_expected, coalesce(result, '<null>');
    end if;
  end loop;

  if failures > 0 then
    raise exception 'PART B: % of % seam cases did not match', failures, total;
  end if;
  raise notice 'PART B: % of % seam cases matched, % skipped', total - skipped, total, skipped;
end $$;

-- ===========================================================================
-- Nothing here is kept.  This ROLLBACK is the point of the fixture.
-- ===========================================================================
rollback;

-- ===========================================================================
-- WHAT THIS FIXTURE DOES NOT COVER
-- ===========================================================================
--
--   * It does not verify the canonical receipt digest independently: it
--     computes the digest with the same ops.guidance_import_canonical_json
--     expression the seam checks against, so a canonicalisation divergence
--     between the database and the JS/Python producers is invisible here.
--   * It covers the plan-wide engineering-slice-plan.v2 invariants only as the
--     receipt seam sees them, and only three of them: the digest-binds-content
--     check (B08), duplicate ordinals (B09) and a dependency cycle (B10).  It
--     does not re-test seam-authority uniqueness or parallel-safe resource
--     isolation here, because the seam calls one function whose per-fault
--     behavior is covered directly, case by case, in
--     mcp-server/test/f03-plan-ownership-validator-postgres.sql Part A.  What
--     this fixture pins is that the seam CALLS it, over the stored plan, before
--     the append, and fails closed when it is absent.
--   * It does not cover registration-time refusal at all.  That is
--     ops.engineering_register_slice_plan, in the companion candidate and the
--     companion fixture.
--   * It does not cover the two further v1-exact slice pins at
--     migrations/0450_canonical_ownership_lease_kernel.sql:330
--     (ops.canonical_ownership_plan_dependencies) and :433
--     (ops.canonical_ownership_dependency_state).  A PASS here therefore does
--     NOT mean an engineering-slice-plan.v2 plan is usable end to end: it means
--     the receipt seam accepts it.  Those two pins are covered by the companion
--     fixture named above.
--   * The declared-ref duplicate parity in A57..A67 is covered at the PREDICATE
--     and CONTRACT level only, and deliberately not at the seam.  The seam's
--     reproduced 0335 slice-typing block independently requires
--     declared_resource_refs to be a UNIQUE identifier array before the bound
--     slice reaches the design contract, so no Part B case can append a receipt
--     for a slice with a repeated declared ref -- that is the pre-existing v1
--     asymmetry named as difference 2 in
--     ops/f03-plan-ownership-validator.candidate.sql, and this candidate neither
--     creates nor closes it.  Where the parity matters is plan registration, and
--     that is exercised end to end by A41/A42 of the companion fixture.
--   * It does not cover ops.engineering_finalize_slice_receipt, the queue
--     transition, reviewer facts or closure projection.  The finalize wrapper is
--     unchanged and delegates to the replaced record function by name and
--     signature.
--   * It does not cover lease, currentness, CAS or authority expiry paths.
--     Those guards are reproduced verbatim from 0335 and are unchanged by this
--     candidate.
--   * Part B exercises one scratch lane with one bound slice_ref and one plan
--     digest.  It does not exercise multi-slice plans, superseded envelope
--     lineage, or concurrent claims.
