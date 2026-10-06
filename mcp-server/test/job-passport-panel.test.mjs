import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import crypto from "node:crypto";
import { deriveJobPassports, jobPassportStatusLabel, parseJobPassportReceipt } from "../../dealroom/js/job-passport.js";
import { activationReliabilityWire, strictAttemptReceiptShape } from "../src/evidence-activation.js";
import { requirePlan } from "../src/engineering-runtime.js";

const helperMode = process.argv.includes("--evidence-activation-joined-path");
const testCase = helperMode ? () => {} : test;

const fixture = JSON.parse(fs.readFileSync(new URL("../../control-room/contracts/fixtures/execution-fabric/codex_desktop.observatory-projection.v1.json", import.meta.url)));
const envelope = JSON.parse(fs.readFileSync(new URL("../../control-room/contracts/fixtures/execution-fabric/codex_desktop.execution-envelope.v1.json", import.meta.url)));
const portfolio = JSON.parse(fs.readFileSync(new URL("../../control-room/contracts/fixtures/execution-fabric/carr-evaluation-kernel.synthetic.v1.json", import.meta.url)));
const spatial = JSON.parse(fs.readFileSync(new URL("../../control-room/contracts/fixtures/execution-fabric/codex_desktop.spatial-surface.v1.json", import.meta.url)));
const elapsedTelemetry = JSON.parse(fs.readFileSync(new URL("../../control-room/contracts/fixtures/execution-fabric/codex_desktop.elapsed-time.telemetry-measurement.v1.json", import.meta.url)));
const unavailableCost = JSON.parse(fs.readFileSync(new URL("../../control-room/contracts/fixtures/execution-fabric/codex_desktop.billed-cost.telemetry-measurement.v1.json", import.meta.url)));
const roomSource = fs.readFileSync(new URL("../../dealroom/js/room.js", import.meta.url), "utf8");
const roomCss = fs.readFileSync(new URL("../../dealroom/css/room.css", import.meta.url), "utf8");
const evidenceActivationSource = fs.readFileSync(new URL("../src/evidence-activation.js", import.meta.url), "utf8");
const turn = (payload, seq = 1) => ({ seq: String(seq), kind: "receipt", at: "2026-08-24T12:00:06Z", body: JSON.stringify(payload) });
const wrap = (kind, payload) => ({ job_passport: { schema_version: "job-passport-wire.v1", kind, payload } });
const canonical = (value) => Array.isArray(value) ? `[${value.map(canonical).join(",")}]` : value && typeof value === "object" ? `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}` : JSON.stringify(value);
const seal = (value) => `sha256:${crypto.createHash("sha256").update(canonical(value)).digest("hex")}`;
const engineeringFor = (projection = fixture) => {
  const workRequest = { id: projection.work_request_id, state_version: projection.source_state.state_version, canonical_record_digest: projection.source_state.canonical_record_digest };
  const acceptedPlanRevision = { id: envelope.plan_revision.id, revision: envelope.plan_revision.revision, digest: projection.source_state.plan_revision_digest };
  const evidence = { ref: "evidence:baseline", redaction_class: "redacted_evidence", content_digest: "sha256:" + "a".repeat(64) };
  const slice = { slice_ref: "slice:a", ordinal: 1, objective: "Blocked synthetic slice", definition_of_done: "A typed receipt arrives", dependency_refs: [], declared_resource_refs: ["resource:worktree-a"], declared_component_refs: ["component:execution-fabric"], declared_plan_step_refs: ["step:synthetic-read"], baseline_evidence_refs: [evidence], planned_checks: [{ check_ref: "check:synthetic", failure_condition: "missing evidence", evidence_requirement: "redacted_evidence_required" }], scope_boundary: "synthetic fixture", forbidden_change_refs: ["forbidden:authority"], concurrency_posture: "parallel_safe", manual_qa_required: false, risk_class: "R1", release_requirement: "required" };
  const slicePlan = { schema_version: "engineering-slice-plan.v1", work_request: workRequest, accepted_plan_revision: acceptedPlanRevision, slices: [slice] };
  slicePlan.plan_digest = seal(slicePlan);
  const receiptEvidence = { ref: "evidence:receipt", redaction_class: "redacted_evidence", content_digest: "sha256:" + "a".repeat(64) };
  const receipt = { schema_version: "engineering-slice-receipt.v1", envelope_digest: seal(envelope), attempt_id: "attempt:a", slice_ref: "slice:a", plan_digest: slicePlan.plan_digest, attribution: { actor_ref: "actor:codex", session_ref: "session:fresh", adapter_ref: "adapter:codex" }, planned_resource_refs: ["resource:worktree-a"], actual_resource_refs: ["resource:worktree-a"], planned_component_refs: ["component:execution-fabric"], actual_component_refs: ["component:execution-fabric"], checks: [{ check_ref: "check:synthetic", state: "passed", evidence_refs: [receiptEvidence] }], outcome: "claimed_complete", artifact_refs: ["artifact:a"], evidence_refs: [receiptEvidence], deviations: [], source_evidence: { worktree_ref: "worktree:isolated", branch_ref: "branch:engineering-passport", source_sha: "0e7279b4", evidence_refs: [receiptEvidence] }, reset_reconstruction: { fresh_session: true, inherited_transcript_used: false, reconstruction_free: true, remediation_action: null }, executor_claim: { claim_state: "executor_claim", claimed_by: "actor:codex", claimed_at: "2026-08-24T12:15:00Z" }, independent_verification_required: true };
  const passport = { schema_version: "engineering-passport.v1", work_request: workRequest, accepted_plan_revision: acceptedPlanRevision, plan_digest: slicePlan.plan_digest, slice_plan: slicePlan, execution_envelopes: [structuredClone(envelope)], receipts: [receipt], reviewer_facts: [], qa_facts: [], slices: [{ slice_ref: "slice:a", ordinal: 1, dependency_refs: [], state: "claimed", planned_check_refs: ["check:synthetic"], deviation_refs: [], manual_qa_required: false, release_requirement: "required" }], operator_receipt: { what_changed: [], why: "derived from accepted plan and typed receipts", evidence_refs: [receiptEvidence], deviations: [], remaining_risk: ["slice:a"], manual_qa_items: [] }, closure: { work: { state: "unresolved", evidence_refs: [], note: "pending" }, proof: { state: "unresolved", evidence_refs: [], note: "pending" }, explanation: { state: "unresolved", evidence_refs: [], note: "pending" }, release: { state: "unresolved", evidence_refs: [], note: "pending" }, learning: { state: "unresolved", route: null, evidence_refs: [], note: "pending" } }, closure_state: "blocked", stale_conflict: { state: "none", reason: null } };
  passport.projection_digest = seal(passport);
  return passport;
};
// engineering-slice-plan.v2 fixture, derived from the server's own v2 test
// fixture (mcp-server/test/engineering-runtime.test.mjs designContract/v2Slice):
// same closed design_contract field set, mirrored here for the browser reader.
const designContractFor = (slice) => {
  const depth = ["R0", "R1", "R2", "R3"].includes(slice.risk_class) && slice.concurrency_posture === "parallel_safe"
    && slice.manual_qa_required === false && slice.release_requirement === "not_required" && slice.dependency_refs.length === 0
    && slice.declared_resource_refs.length <= 1 && slice.declared_component_refs.length <= 1 && slice.declared_plan_step_refs.length <= 1
    ? "short" : "full";
  const redaction = slice.planned_checks.some((check) => check.evidence_requirement === "redacted_evidence_required") ? "redacted_evidence" : "metadata_only";
  return {
    contract_version: "engineering-design-contract.v1",
    rationale: "the closed validator owns this behavior end to end",
    dependency_rationale: "no accepted predecessor slice is required",
    code_model_decision: { rationale: "stable enforceable behavior stays deterministic code", selection_basis: ["capability_gain", "quality_gain"], model_judgment_steps: [] },
    routing: { executor_class: "deterministic_code", adapter_ref: "adapter:codex-desktop", fresh_session_required: true },
    authority: { capability_profile: "capability:engineering-repository-write", read_only: false, environment: "rehearsal" },
    isolation: { worktree_required: true, branch_required: true, shared_resource_refs: [] },
    tests: { planned_check_refs: slice.planned_checks.map((check) => check.check_ref), verification_lanes: slice.manual_qa_required ? ["contract", "manual_qa"] : ["contract"] },
    review: { independent_review_required: true, reviewer_class: "independent_agent" },
    failure: { failure_modes: [{ failure_ref: "failure:contract-drift", detection: "the closed validator refuses the plan", compensation: "revise the accepted plan revision before admission" }] },
    evidence: { redaction_class: redaction, retention: "material_redacted", evidence_refs: [{ ref: "evidence:design", redaction_class: redaction, content_digest: "sha256:" + "a".repeat(64) }] },
    deployment: { release_requirement: slice.release_requirement, rollback_ref: slice.release_requirement === "required" ? "release:rollback-plan" : null, confirmation_required: !["R0", "R1"].includes(slice.risk_class) },
    completion: { completion_predicate: "every accepted planned check passes under independent review", verified_by: slice.manual_qa_required ? "independent_review_and_manual_qa" : "independent_review" },
    seam_decision: { mode: "extend", target_seam_ref: "seam:engineering-runtime", measurement: { basis: "complexity_reduction", note: "extending the proven validator is smaller than a new module" }, new_module_justification: null, replaced_seam_refs: [], residual_authority_refs: [] },
    full_design_refs: depth === "short" ? null : { design_interview_ref: "interview:v5-f03", authority_envelope_ref: "envelope:v5-f03", failure_model_ref: "failure-model:v5-f03", fixture_refs: ["fixture:v5-f03-boundary"], oracle_ref: "oracle:doctorcre-v5:Q035.D1" },
    short_template: depth === "full" ? null : { template_ref: "template:short-governed-v1", objective_summary: "one bounded parallel-safe change with no dependencies", verification_ref: "verification:short-governed-v1" },
  };
};
const engineeringForV2 = (projection = fixture) => {
  const workRequest = { id: projection.work_request_id, state_version: projection.source_state.state_version, canonical_record_digest: projection.source_state.canonical_record_digest };
  const acceptedPlanRevision = { id: envelope.plan_revision.id, revision: envelope.plan_revision.revision, digest: projection.source_state.plan_revision_digest };
  const evidence = { ref: "evidence:baseline", redaction_class: "redacted_evidence", content_digest: "sha256:" + "a".repeat(64) };
  const slice = { slice_ref: "slice:a", ordinal: 1, objective: "Blocked synthetic slice", definition_of_done: "A typed receipt arrives", dependency_refs: [], declared_resource_refs: ["resource:worktree-a"], declared_component_refs: ["component:execution-fabric"], declared_plan_step_refs: ["step:synthetic-read"], baseline_evidence_refs: [evidence], planned_checks: [{ check_ref: "check:synthetic", failure_condition: "missing evidence", evidence_requirement: "redacted_evidence_required" }], scope_boundary: "synthetic fixture", forbidden_change_refs: ["forbidden:authority"], concurrency_posture: "parallel_safe", manual_qa_required: false, risk_class: "R1", release_requirement: "required" };
  slice.design_contract = designContractFor(slice);
  const slicePlan = { schema_version: "engineering-slice-plan.v2", work_request: workRequest, accepted_plan_revision: acceptedPlanRevision, slices: [slice] };
  slicePlan.plan_digest = seal(slicePlan);
  const receiptEvidence = { ref: "evidence:receipt", redaction_class: "redacted_evidence", content_digest: "sha256:" + "a".repeat(64) };
  const receipt = { schema_version: "engineering-slice-receipt.v1", envelope_digest: seal(envelope), attempt_id: "attempt:a", slice_ref: "slice:a", plan_digest: slicePlan.plan_digest, attribution: { actor_ref: "actor:codex", session_ref: "session:fresh", adapter_ref: "adapter:codex" }, planned_resource_refs: ["resource:worktree-a"], actual_resource_refs: ["resource:worktree-a"], planned_component_refs: ["component:execution-fabric"], actual_component_refs: ["component:execution-fabric"], checks: [{ check_ref: "check:synthetic", state: "passed", evidence_refs: [receiptEvidence] }], outcome: "claimed_complete", artifact_refs: ["artifact:a"], evidence_refs: [receiptEvidence], deviations: [], source_evidence: { worktree_ref: "worktree:isolated", branch_ref: "branch:engineering-passport", source_sha: "0e7279b4", evidence_refs: [receiptEvidence] }, reset_reconstruction: { fresh_session: true, inherited_transcript_used: false, reconstruction_free: true, remediation_action: null }, executor_claim: { claim_state: "executor_claim", claimed_by: "actor:codex", claimed_at: "2026-08-24T12:15:00Z" }, independent_verification_required: true };
  const passport = { schema_version: "engineering-passport.v1", work_request: workRequest, accepted_plan_revision: acceptedPlanRevision, plan_digest: slicePlan.plan_digest, slice_plan: slicePlan, execution_envelopes: [structuredClone(envelope)], receipts: [receipt], reviewer_facts: [], qa_facts: [], slices: [{ slice_ref: "slice:a", ordinal: 1, dependency_refs: [], state: "claimed", planned_check_refs: ["check:synthetic"], deviation_refs: [], manual_qa_required: false, release_requirement: "required" }], operator_receipt: { what_changed: [], why: "derived from accepted plan and typed receipts", evidence_refs: [receiptEvidence], deviations: [], remaining_risk: ["slice:a"], manual_qa_items: [] }, closure: { work: { state: "unresolved", evidence_refs: [], note: "pending" }, proof: { state: "unresolved", evidence_refs: [], note: "pending" }, explanation: { state: "unresolved", evidence_refs: [], note: "pending" }, release: { state: "unresolved", evidence_refs: [], note: "pending" }, learning: { state: "unresolved", route: null, evidence_refs: [], note: "pending" } }, closure_state: "blocked", stale_conflict: { state: "none", reason: null } };
  passport.projection_digest = seal(passport);
  return passport;
};
const reseal = (typedPassport) => {
  delete typedPassport.slice_plan.plan_digest;
  typedPassport.slice_plan.plan_digest = seal(typedPassport.slice_plan);
  typedPassport.plan_digest = typedPassport.slice_plan.plan_digest;
  for (const receipt of typedPassport.receipts) receipt.plan_digest = typedPassport.plan_digest;
  delete typedPassport.projection_digest;
  typedPassport.projection_digest = seal(typedPassport);
  return typedPassport;
};

// Build unexecuted public-wire projections so plan mutations cannot be refused
// accidentally by unrelated receipt or derived-state bindings.
const unexecutedV2 = (configure = () => {}) => {
  const passport = engineeringForV2();
  configure(passport.slice_plan.slices);
  passport.receipts = [];
  passport.execution_envelopes = [];
  passport.slices = passport.slice_plan.slices.map((slice) => ({
    slice_ref: slice.slice_ref, ordinal: slice.ordinal, dependency_refs: slice.dependency_refs,
    state: slice.dependency_refs.length ? "blocked" : "eligible",
    planned_check_refs: slice.planned_checks.map((check) => check.check_ref), deviation_refs: [],
    manual_qa_required: slice.manual_qa_required, release_requirement: slice.release_requirement,
  }));
  passport.operator_receipt.evidence_refs = [];
  passport.operator_receipt.remaining_risk = passport.slices.map((slice) => slice.slice_ref);
  passport.operator_receipt.manual_qa_items = passport.slices.filter((slice) => slice.manual_qa_required).map((slice) => slice.slice_ref);
  return reseal(passport);
};
class PlanError extends Error {
  constructor(payload) { super(payload.error); Object.assign(this, payload); }
}
const assertV2Parity = (passport, accepted) => {
  if (accepted) assert.equal(requirePlan(passport.slice_plan, PlanError), passport.slice_plan);
  else assert.throws(() => requirePlan(passport.slice_plan, PlanError), PlanError);
  const model = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("engineering_passport", passport), 2)]);
  assert.equal(model.passports.length, 1);
  if (accepted) {
    assert.deepEqual(model.passports[0].engineering_passport, passport);
    assert.deepEqual(model.rejected, []);
  } else {
    assert.equal(model.passports[0].engineering_passport, null);
    assert.deepEqual(model.rejected, [{ seq: 2, reason: "invalid_engineering_passport" }]);
  }
};
const modelStep = () => ({
  step_ref: "step:synthetic-read", responsibility_class: "classification",
  input_contract_ref: "contract:input", output_contract_ref: "contract:output",
  rationale: "classify typed uncertainty", selection_basis: ["quality_gain"],
});
const modelV2 = () => unexecutedV2(([slice]) => {
  slice.design_contract.routing.executor_class = "model_assisted";
  slice.design_contract.code_model_decision.model_judgment_steps = [modelStep()];
});
const orderedV2 = (dependencies = [[], []], resources = ["resource:a", "resource:b"]) => unexecutedV2((slices) => {
  const prototype = slices[0];
  slices.splice(0, slices.length, ...dependencies.map((refs, index) => {
    const slice = structuredClone(prototype);
    slice.slice_ref = `slice:${index}`;
    slice.ordinal = index + 1;
    slice.dependency_refs = refs;
    slice.declared_resource_refs = [resources[index]];
    slice.design_contract = designContractFor(slice);
    return slice;
  }));
});
const mutateV2 = (passport, mutation) => {
  mutation(passport.slice_plan.slices);
  return reseal(passport);
};

testCase("v2 public wire refuses coercible non-string evidence digests like the server", () => {
  const passport = unexecutedV2(([slice]) => {
    const evidence = slice.design_contract.evidence.evidence_refs[0];
    evidence.content_digest = [evidence.content_digest];
  });
  assert.throws(() => requirePlan(passport.slice_plan, PlanError), { error: "engineering_design_contract_field_invalid" });
  assertV2Parity(passport, false);
});

testCase("v2 public wire and server agree on short/full depth and model-assisted success", () => {
  assertV2Parity(unexecutedV2(), true);
  assertV2Parity(unexecutedV2(([slice]) => {
    slice.release_requirement = "not_required";
    slice.design_contract = designContractFor(slice);
  }), true);
  assertV2Parity(modelV2(), true);
  assertV2Parity(unexecutedV2(([slice]) => {
    slice.manual_qa_required = true;
    slice.design_contract = designContractFor(slice);
    slice.design_contract.authority = { read_only: true, capability_profile: "capability:read", environment: "production" };
  }), true);
});

const nestedV2Failures = [
  ["full depth cannot use short template", (s) => { s.design_contract.full_design_refs = null; s.design_contract.short_template = { template_ref: "template:short", objective_summary: "bounded", verification_ref: "verification:short" }; }],
  ["short depth cannot use full template", (s) => { s.release_requirement = "not_required"; s.design_contract.deployment.release_requirement = "not_required"; s.design_contract.deployment.rollback_ref = null; }],
  ["cost alone cannot select executor", (s) => { s.design_contract.code_model_decision.selection_basis = ["cost"]; }],
  ["deterministic executor cannot own model step", (s) => { s.design_contract.code_model_decision.model_judgment_steps = [modelStep()]; }],
  ["model executor requires step", (s) => { s.design_contract.routing.executor_class = "model_assisted"; }],
  ["model cannot own validation", (s) => { s.design_contract.routing.executor_class = "model_assisted"; s.design_contract.code_model_decision.model_judgment_steps = [{ ...modelStep(), responsibility_class: "validation" }]; }],
  ["model step must be declared", (s) => { s.design_contract.routing.executor_class = "model_assisted"; s.design_contract.code_model_decision.model_judgment_steps = [{ ...modelStep(), step_ref: "step:undeclared" }]; }],
  ["model steps cannot duplicate", (s) => { s.design_contract.routing.executor_class = "model_assisted"; s.design_contract.code_model_decision.model_judgment_steps = [modelStep(), modelStep()]; }],
  ["write requires repository capability", (s) => { s.design_contract.authority.capability_profile = "capability:read"; }],
  ["authority read_only is typed", (s) => { s.design_contract.authority.read_only = "false"; }],
  ["authority environment is closed", (s) => { s.design_contract.authority.environment = "unknown"; }],
  ["worktree isolation is mandatory", (s) => { s.design_contract.isolation.worktree_required = false; }],
  ["branch isolation is mandatory", (s) => { s.design_contract.isolation.branch_required = false; }],
  ["shared resource must be declared", (s) => { s.design_contract.isolation.shared_resource_refs = ["resource:undeclared"]; }],
  ["parallel-safe cannot share a resource", (s) => { s.design_contract.isolation.shared_resource_refs = [...s.declared_resource_refs]; }],
  ["check refs must match plan", (s) => { s.design_contract.tests.planned_check_refs = ["check:other"]; }],
  ["manual QA lane must match slice", (s) => { s.design_contract.tests.verification_lanes.push("manual_qa"); }],
  ["independent review required", (s) => { s.design_contract.review.independent_review_required = false; }],
  ["failure modes required", (s) => { s.design_contract.failure.failure_modes = []; }],
  ["failure mode refs unique", (s) => { s.design_contract.failure.failure_modes.push(structuredClone(s.design_contract.failure.failure_modes[0])); }],
  ["evidence redaction must match", (s) => { s.design_contract.evidence.evidence_refs[0].redaction_class = "metadata_only"; }],
  ["release requirement must match", (s) => { s.design_contract.deployment.release_requirement = "not_required"; }],
  ["release needs rollback", (s) => { s.design_contract.deployment.rollback_ref = null; }],
  ["high risk requires confirmation", (s) => { s.risk_class = "R4"; s.design_contract.deployment.confirmation_required = false; }],
  ["completion verifier matches QA", (s) => { s.design_contract.completion.verified_by = "independent_review_and_manual_qa"; }],
  ["new module needs justification", (s) => { s.design_contract.seam_decision.mode = "new_module"; }],
  ["replacement retires predecessor", (s) => { s.design_contract.seam_decision.mode = "replace"; }],
  ["replacement leaves no residual authority", (s) => { Object.assign(s.design_contract.seam_decision, { mode: "replace", replaced_seam_refs: ["seam:old"], residual_authority_refs: ["authority:old"] }); }],
];
for (const [name, mutation] of nestedV2Failures) {
  testCase(`v2 parity rejects: ${name}`, () => assertV2Parity(unexecutedV2(([slice]) => mutation(slice)), false));
}

testCase("v2 parity refuses missing, extra and null fields inside every contract facet", () => {
  const contract = unexecutedV2().slice_plan.slices[0].design_contract;
  for (const [facet, value] of Object.entries(contract).filter(([, value]) => value && typeof value === "object")) {
    for (const field of Object.keys(value)) {
      for (const kind of ["missing", "null"]) {
        if (kind === "null" && value[field] === null) continue;
        const passport = unexecutedV2(([slice]) => {
          if (kind === "missing") delete slice.design_contract[facet][field];
          else slice.design_contract[facet][field] = null;
        });
        assertV2Parity(passport, false);
      }
    }
    assertV2Parity(unexecutedV2(([slice]) => { slice.design_contract[facet].unexpected = true; }), false);
  }
});

testCase("v2 parity validates seam ownership, retirement and replacement across slices", () => {
  const own = (slice, target) => Object.assign(slice.design_contract.seam_decision, { mode: "new_module", target_seam_ref: target, new_module_justification: "authority" });
  const replace = (slice, target, retired) => Object.assign(slice.design_contract.seam_decision, { mode: "replace", target_seam_ref: target, replaced_seam_refs: [retired] });
  assertV2Parity(mutateV2(orderedV2(), ([a, b]) => { own(a, "seam:a"); own(b, "seam:b"); }), true);
  assertV2Parity(mutateV2(orderedV2(), ([a, b]) => { own(a, "seam:a"); own(b, "seam:a"); }), false);
  assertV2Parity(mutateV2(orderedV2(), ([a, b]) => { replace(a, "seam:a", "seam:old"); replace(b, "seam:b", "seam:old"); }), false);
  for (const mode of ["reuse", "extend"]) {
    assertV2Parity(mutateV2(orderedV2(), ([a, b]) => { replace(a, "seam:a", "seam:old"); Object.assign(b.design_contract.seam_decision, { mode, target_seam_ref: "seam:old" }); }), false);
  }
  assertV2Parity(mutateV2(orderedV2(), ([a]) => replace(a, "seam:a", "seam:old")), true);
});

testCase("v2 parity enforces direct/transitive ordering for contended parallel resources", () => {
  assertV2Parity(orderedV2(), true);
  assertV2Parity(orderedV2([[], []], ["resource:shared", "resource:shared"]), false);
  assertV2Parity(orderedV2([[], ["slice:0"]], ["resource:shared", "resource:shared"]), true);
  assertV2Parity(orderedV2([["slice:1"], []], ["resource:shared", "resource:shared"]), true);
  assertV2Parity(orderedV2([[], ["slice:0"], ["slice:1"]], ["resource:shared", "resource:middle", "resource:shared"]), true);
  assertV2Parity(orderedV2([[], ["slice:0"], []], ["resource:shared", "resource:middle", "resource:shared"]), false);
  assertV2Parity(orderedV2([["slice:1"], ["slice:0"]]), false);
});

export function joined_activation_reliability_wire_and_model_room_path({ db_projection, activation_read_projection, admitted_receipt, observatory_projection }) {
  const canonicalBinding = db_projection?.canonical_binding;
  assert.ok(canonicalBinding, "DB reliability projection has a canonical binding");
  assert.equal(activation_read_projection?.evidence_register?.work_request_ref, canonicalBinding.work_request_id);
  assert.equal(activation_read_projection?.evidence_register?.admission_ref, `activation:${canonicalBinding.activation_binding_ref}`);
  const readAttempt = activation_read_projection?.attempt_receipts?.find((row) => row.attempt_id === canonicalBinding.attempt_id);
  assert.ok(readAttempt, "one-WR read projection contains the admitted attempt");
  assert.equal(activation_read_projection.learning?.length, 1);
  assert.equal(activation_read_projection.learning[0].source_ref, `attempt:${canonicalBinding.attempt_id}`);
  assert.equal(activation_read_projection.learning[0].lifecycle, "accepted");

  const projection = structuredClone(observatory_projection);
  projection.work_request_id = canonicalBinding.work_request_id;
  projection.source_state.state_version = canonicalBinding.work_request_version;
  projection.source_state.canonical_record_digest = canonicalBinding.accepted_plan_digest;
  projection.source_state.plan_revision_digest = canonicalBinding.accepted_plan_digest;
  projection.attempt_lane.attempt_id = canonicalBinding.attempt_id;
  delete projection.projection_digest;
  projection.projection_digest = seal(projection);

  const receipt = structuredClone(admitted_receipt);
  receipt.attempt_id = canonicalBinding.attempt_id;
  receipt.envelope_digest = canonicalBinding.envelope_digest;
  receipt.result = { ...receipt.result, job_ref: `job:${canonicalBinding.work_request_id}` };
  receipt.knowledge_activation.canonical_binding = {
    work_request_id: canonicalBinding.work_request_id,
    work_request_version: canonicalBinding.work_request_version,
    accepted_plan_digest: canonicalBinding.accepted_plan_digest,
    envelope_digest: canonicalBinding.envelope_digest,
    activation_binding_ref: canonicalBinding.activation_binding_ref,
  };

  const wire = activationReliabilityWire(db_projection);
  const parsed = parseJobPassportReceipt(JSON.stringify(wire));
  assert.deepEqual(parsed, { ok: true, kind: "activation_reliability_projection", payload: db_projection });
  const model = deriveJobPassports([
    turn(wrap("observatory_projection", projection), 1),
    turn(wrap("attempt_receipt", receipt), 2),
    turn(wire, 3),
  ]);
  assert.equal(model.enabled, true);
  assert.equal(model.passports.length, 1);
  const passport = model.passports[0];
  assert.equal(passport.work_request_id, canonicalBinding.work_request_id);
  assert.equal(passport.attempt_lane.attempt_id, canonicalBinding.attempt_id);
  assert.deepEqual(passport.activation_reliability.canonical, db_projection);
  assert.deepEqual(passport.activation_reliability.knowledge_activation.canonical_binding, receipt.knowledge_activation.canonical_binding);
  assert.equal(passport.activation_reliability.reliability.closure.state, receipt.reliability.closure.state);
  assert.equal(passport.activation_reliability.canonical.reliability.state, db_projection.reliability.state);
  return {
    ok: true,
    work_request_id: canonicalBinding.work_request_id,
    attempt_id: canonicalBinding.attempt_id,
    activation_binding_ref: canonicalBinding.activation_binding_ref,
    learning_lifecycle: db_projection.learning.lifecycle,
    reliability_state: db_projection.reliability.state,
  };
}

if (process.argv.includes("--evidence-activation-joined-path")) {
  let input = "";
  for await (const chunk of process.stdin) input += chunk;
  try {
    process.stdout.write(JSON.stringify(joined_activation_reliability_wire_and_model_room_path(JSON.parse(input))));
  } catch (error) {
    process.stderr.write(`${error.stack || error}\n`);
    process.exitCode = 1;
  }
}

testCase("Job Passport parses only strict typed wire wrappers", () => {
  assert.equal(parseJobPassportReceipt("not json").ok, false);
  assert.equal(parseJobPassportReceipt('{"job_passport":{"schema_version":"job-passport-wire.v1","kind":"nope","payload":{}}}').ok, false);
  assert.deepEqual(parseJobPassportReceipt(JSON.stringify(wrap("progress_event", { schema_version: "progress-event.v1" }))),
    { ok: true, kind: "progress_event", payload: { schema_version: "progress-event.v1" } });
});

testCase("the deterministic panel keeps profile identity separate from model staffing", () => {
  const model = deriveJobPassports([turn(wrap("observatory_projection", fixture))], { now: Date.parse("2026-08-24T12:00:10Z") });
  assert.equal(model.enabled, true);
  assert.equal(model.passports[0].attempt_lane.persistent_profile.display_label, "Doc");
  assert.equal(model.passports[0].attempt_lane.actual_staffing.model_id, "model:codex-synthetic");
  assert.equal(model.passports[0].status, "verified_complete");
  assert.equal(jobPassportStatusLabel(model.passports[0].status), "Independently verified complete");
});

testCase("newer canonical state wins, stale receipts do not overwrite it, and same-version conflicts stay visible", () => {
  const newer = structuredClone(fixture);
  newer.source_state.state_version = 2;
  newer.source_state.canonical_record_digest = "sha256:" + "9".repeat(64);
  newer.generated_at = "2026-08-24T12:00:08Z";
  const stale = structuredClone(fixture);
  const conflict = structuredClone(newer);
  conflict.source_state.canonical_record_digest = "sha256:" + "8".repeat(64);
  const model = deriveJobPassports([
    turn(wrap("observatory_projection", fixture), 1), turn(wrap("observatory_projection", newer), 2),
    turn(wrap("observatory_projection", stale), 3), turn(wrap("observatory_projection", conflict), 4),
  ]);
  assert.equal(model.passports[0].source_state.state_version, 2);
  assert.equal(model.passports[0].status, "unknown_partial", "a conflicting same-version receipt cannot look certain");
  assert.ok(model.rejected.some((row) => row.reason === "stale_projection"));
  assert.ok(model.rejected.some((row) => row.reason === "same_version_conflict"));
});

testCase("status distinctions do not invent deviation from quiet or filesystem-only observation", () => {
  const cases = {
    aligned: ["active", [], "unknown"], deviation_candidate: ["active", [{ candidate_id: "candidate:one" }], "unknown"],
    blocked: ["blocked", [], "unknown"], quiet: ["quiet", [], "filesystem_only"], stale: ["stale", [], "stale_signal"],
    failed: ["failed", [], "unknown"], unknown_partial: ["unknown", [], "unknown"],
  };
  for (const [expected, [progress, candidates, uncertainty]] of Object.entries(cases)) {
    const value = structuredClone(fixture);
    value.state.progress = progress; value.observed_movement.progress_state = progress;
    value.observed_movement.deviation_candidates = candidates; value.observed_movement.uncertainty = uncertainty;
    if (expected === "failed") value.state.lifecycle = "failed";
    const result = deriveJobPassports([turn(wrap("observatory_projection", value))]);
    assert.equal(result.passports[0].status, expected, expected);
  }
});

testCase("typed facts count on the wire but cannot create a visual job from transcript-like data", () => {
  const model = deriveJobPassports([
    turn(wrap("execution_envelope", { schema_version: "execution-envelope.v1" })),
    turn(wrap("progress_event", { schema_version: "progress-event.v1" })),
    turn(wrap("attempt_receipt", { schema_version: "attempt-receipt.v1" })),
  ]);
  assert.equal(model.enabled, false);
  assert.deepEqual(model.typedCounts, { execution_envelope: 1, progress_event: 1, attempt_receipt: 1, activation_reliability_projection: 0, observatory_projection: 0, evaluation_kernel: 0, eval_portfolio: 0, spatial_surface: 0, telemetry_measurement: 0 });
});

testCase("attempt receipt activation facts are redacted and forged nested content is withheld", () => {
  const activationReceipt = {
    schema_version: "attempt-receipt.v1", attempt_id: fixture.attempt_lane.attempt_id,
    result: { job_ref: `job:${fixture.work_request_id}` },
    knowledge_activation: {
      bundle_digest: "sha256:" + "1".repeat(64), mode: "canary",
      canonical_binding: { work_request_id: fixture.work_request_id, work_request_version: fixture.source_state.state_version, accepted_plan_digest: fixture.source_state.plan_revision_digest, envelope_digest: "sha256:" + "0".repeat(64), activation_binding_ref: "ctx:synthetic" },
      item_dispositions: [{ item_ref: "rule:scope", disposition: "applied", evidence_refs: ["evidence:rule"], stage_ref: "stage:retrieve", tool_ref: "tool:context", reason_ref: "reason:bound" }],
      closure: { state: "closed", unresolved_required_item_refs: [], derived_by: "server" },
    },
  };
  const accepted = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("attempt_receipt", activationReceipt), 2)]);
  assert.equal(accepted.passports[0].activation_reliability.knowledge_activation.bundle_digest, activationReceipt.knowledge_activation.bundle_digest);
  const fullReliabilityReceipt = structuredClone(activationReceipt);
  fullReliabilityReceipt.reliability = {
    route_digest: "sha256:" + "1".repeat(64), topology_digest: "sha256:" + "2".repeat(64), evaluation_plan_digest: "sha256:" + "3".repeat(64),
    environment_binding_digest: "sha256:" + "9".repeat(64),
    environment_evidence: { binding_digest: "sha256:" + "9".repeat(64), session_ref: "environment-session:one", lease_state: "released", operation_count: 1, policy_refusal_refs: [], security_event_refs: [], cleanup_state: "verified", cleanup_evidence_refs: ["evidence:cleanup"], side_effect_state: "none", resource_usage: { cpu_ms: 1, memory_peak_mb: 1, disk_peak_mb: 0, network_egress_bytes: 0 }, evidence_refs: ["evidence:environment"] },
    grounding_sufficiency: { state: "sufficient", evidence_refs: ["evidence:grounding"], required_supplied: ["rule:scope"], required_used: ["rule:scope"], required_missing: [], advisory_supplied: [], advisory_used: [], freshness_failures: [], retrieval_failures: [] },
    deterministic_checks: [{ check_id: "check:one", state: "passed", critical: true, evidence_refs: ["evidence:one"] }], model_judgement: { state: "pass", judge_ref: "actor:judge", evidence_refs: ["evidence:judge"] }, human_acceptance: { state: "absent", actor_ref: "actor:human", evidence_refs: [], outcome_feedback_ref: null, outcome_feedback_hash: null },
    trajectory: [{ sequence: 1, stage_ref: "stage:one", parent_event_ref: null, decision_class: "decision:one", tool_class: "tool:one", result_state: "succeeded", fallback_state: "not_used", guardrail_state: "clear", latency_ms: 1, evidence_refs: ["evidence:trace"] }],
    evaluator_results: [{ kind: "deterministic", evaluator_ref: "evaluator:deterministic", rubric_ref: "rubric:one", evaluator_version: "v1", evaluator_digest: "sha256:" + "4".repeat(64), status: "passed", confidence: "high", critical: true, independence_state: "not_independent", held_out_case_count: 1, check_refs: ["check:one"], dimension_refs: ["dimension:correctness"], evidence_refs: ["evidence:one"], judge_provenance: "provenance:deterministic", calibration_evidence_refs: [] }],
    corrections: [], defects: [], incidents: [], downstream_outcome: { state: "unknown", brokerage_ref: "deal:pending", evidence_refs: [], outcome_feedback_ref: null, outcome_feedback_hash: null }, outcome_horizon: { state: "immature", ends_at: "2026-08-24T12:00:00Z", as_of: "2026-08-24T12:00:00Z", evidence_refs: [] }, process_metrics: { latency_ms: 1, cost_usd: 0, input_tokens: 1, output_tokens: 1, cached_input_tokens: 0, retry_count: 0, recovery_count: 0, context_reconstruction_ms: 0, human_intervention_count: 0, security_event_refs: [] }, eval_candidates: [], shadow_comparisons: [], learning_disposition: "none", telemetry: [], closure: { state: "insufficient_evidence", reasons: ["reason:authority-evidence"], derived_by: "server" },
  };
  const fullReliability = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("attempt_receipt", fullReliabilityReceipt), 2)]);
  assert.ok(fullReliability.passports[0].activation_reliability.reliability, "browser accepts a receipt evaluator with exact check/dimension bindings");
  // This is the complete activated evaluation_plan emitted by the SQL issuer,
  // exercised through the envelope parser rather than a reduced browser-only
  // surrogate shape.
  const exactDbIssuedPlan = { ref: "evaluation-plan:independent-risk-v1", digest: "sha256:" + "e".repeat(64), lane_ref: "lane:governed-work", risk_class: "R2", rubric_digest: "sha256:" + "a".repeat(64), case_set_digest: "sha256:" + "b".repeat(64), evaluator_policy_digest: "sha256:" + "c".repeat(64), evaluator_ref: "evaluator:authority-independent-v1", rubric_ref: "rubric:independent-risk-v1", evaluator_version: "version:v1", evaluator_digest: "sha256:" + "d".repeat(64), required_rungs: ["rung:smoke", "rung:regression"], required_deterministic_check_refs: ["check:activation-binding", "check:critical-security"], critical_dimensions: ["dimension:correctness", "dimension:security"], human_acceptance_required: true, outcome_horizon_ref: "outcome-horizon:r2-seven-day", outcome_horizon_not_before: "2026-08-31T12:00:00Z", requirements: { required_evaluator_kinds: ["deterministic", "judge", "human_acceptance"], minimum_held_out_case_count: 1, minimum_calibration_ref_count: 1, maximum_critical_failure_count: 0, maximum_critical_failure_rate: 0, confidence_posture: "lower_bound_required", drift_tolerance: "no_critical_regression", independent_review_required: true, human_acceptance_required: true, outcome_horizon_required: true } };
  const exactDbEnvelopePassport = engineeringFor(fixture);
  const exactDbEnvelope = exactDbEnvelopePassport.execution_envelopes[0];
  exactDbEnvelope.activation_binding = { bundle_digest: "sha256:" + "b".repeat(64), item_refs: ["rule:scope"], mode: "canary", retrieval_policy_version: "v1" };
  exactDbEnvelope.reliability_policy_binding = { policy_ref: "policy:execution-lane-v1", policy_digest: "sha256:" + "c".repeat(64), risk_class: "R2", mode: "canary" };
  exactDbEnvelope.context_activation_ref = "ctx:synthetic";
  exactDbEnvelope.runtime_profile = { ref: "runtime-profile:builder:v1", digest: "sha256:" + "1".repeat(64), profile_key: "builder", profile_version: 1, provider_id: "provider:openai", model_id: "model:gpt-5", desk: "desk:build", policy_ref: "policy:execution-lane-v1", policy_digest: "sha256:" + "c".repeat(64), modality: "modality:text", reasoning_effort_ref: "reasoning-effort:governed-default", sampling_profile_ref: "sampling:governed-default", context_budget: 8192, cache_policy_ref: "cache:governed-default", knowledge_cutoff_posture: "knowledge-cutoff:provider-declared", tool_calling_mode: "tool-calling:metadata-only", environment_provider_ref: "environment-provider:hermes-local:v1", environment_provider_version: 1, environment_provider_digest: "sha256:" + "4".repeat(64), environment_requirement_digest: "sha256:" + "5".repeat(64), environment_configuration_digest: "sha256:" + "6".repeat(64), environment_backend_kind: "local", environment_source_class: "built_in", environment_isolation_class: "host_process", environment_capability_refs: ["environment:exec", "environment:filesystem"], environment_conformance_ref: "conformance-run:hermes-local-v1", environment_conformance_digest: "sha256:" + "7".repeat(64), environment_binding_digest: "sha256:" + "8".repeat(64) };
  exactDbEnvelope.execution_topology = { ref: "execution-topology:single-governed-attempt-v1", digest: "sha256:" + "2".repeat(64), kind: "single_agent_loop", harness_digest: "sha256:" + "3".repeat(64), parallelism: "sequential", code_model_step_refs: ["step:model-governed"], fallback_policy_ref: "fallback:stop-and-escalate", stop_condition_refs: ["stop:capability-expired", "stop:critical-failure"], context_refresh_policy_ref: "context-refresh:bound-revisions-only", memory_policy_ref: "memory:context-never-authority", sandbox_ref: "environment-provider:hermes-local:v1", guardrail_ref: "guardrail:governed-default", threat_model_ref: "threat-model:governed-default" };
  exactDbEnvelope.evaluation_plan = exactDbIssuedPlan;
  exactDbEnvelopePassport.receipts[0].envelope_digest = seal(exactDbEnvelope);
  delete exactDbEnvelopePassport.projection_digest; exactDbEnvelopePassport.projection_digest = seal(exactDbEnvelopePassport);
  const exactDbEnvelopeModel = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("engineering_passport", exactDbEnvelopePassport), 2)]);
  assert.deepEqual(exactDbEnvelopeModel.passports[0].engineering_passport, exactDbEnvelopePassport, "browser attaches the full SQL-issued activated envelope");
  assert.equal(exactDbEnvelopeModel.passports[0].engineering_passport.execution_envelopes[0].runtime_profile.environment_provider_ref, "environment-provider:hermes-local:v1");
  assert.ok(!exactDbEnvelopeModel.rejected.some((row) => row.reason === "invalid_engineering_passport"));
  const malformedActivatedEnvelopePassport = structuredClone(exactDbEnvelopePassport);
  delete malformedActivatedEnvelopePassport.execution_envelopes[0].evaluation_plan.required_deterministic_check_refs;
  delete malformedActivatedEnvelopePassport.projection_digest; malformedActivatedEnvelopePassport.projection_digest = seal(malformedActivatedEnvelopePassport);
  const malformedActivatedEnvelopeModel = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("engineering_passport", malformedActivatedEnvelopePassport), 2)]);
  assert.equal(malformedActivatedEnvelopeModel.passports[0].engineering_passport, null);
  assert.ok(malformedActivatedEnvelopeModel.rejected.some((row) => row.reason === "invalid_engineering_passport"));
  const forgedEnvironmentReceipt = structuredClone(fullReliabilityReceipt);
  forgedEnvironmentReceipt.reliability.environment_evidence.binding_digest = "sha256:" + "0".repeat(64);
  const forgedEnvironmentModel = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("attempt_receipt", forgedEnvironmentReceipt), 2)]);
  assert.equal(forgedEnvironmentModel.passports[0].activation_reliability, null);
  const foreignBinding = structuredClone(activationReceipt); foreignBinding.knowledge_activation.canonical_binding.work_request_id = "wr-foreign";
  const crossWr = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("attempt_receipt", foreignBinding), 2)]);
  assert.equal(crossWr.passports[0].activation_reliability, null);
  // This is the exact payload shape returned by the DB query; the MCP helper
  // supplies the sole real wire wrapper consumed by the browser parser.
  const dbProjection = { canonical_binding: { ...activationReceipt.knowledge_activation.canonical_binding, attempt_id: activationReceipt.attempt_id }, canonical_revision: { authority_fact_count: 0, learning_event_count: 0, outcome_horizon_mature: false }, learning: { lifecycle: "proposed", candidate_refs: ["candidate:synthetic"] }, telemetry: [], reliability: { state: "insufficient_evidence", reasons: ["reason:canonical-coverage"], derived_by: "canonical_authority_evaluation", outcome_horizon_state: "immature", outcome_horizon_not_before: "2026-08-31T12:00:00Z" } };
  const mcpWire = activationReliabilityWire(dbProjection);
  assert.deepEqual(parseJobPassportReceipt(JSON.stringify(mcpWire)), { ok: true, kind: "activation_reliability_projection", payload: dbProjection });
  const withCanonical = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("attempt_receipt", activationReceipt), 2), turn(mcpWire, 3)]);
  assert.equal(withCanonical.passports[0].activation_reliability.canonical.learning.lifecycle, "proposed");
  const malformedCanonical = structuredClone(dbProjection); malformedCanonical.learning.candidate_refs = ["raw transcript body"];
  const withheldCanonical = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("attempt_receipt", activationReceipt), 2), turn(wrap("activation_reliability_projection", malformedCanonical), 3)]);
  assert.equal(withheldCanonical.passports[0].activation_reliability.canonical, null);
  const rawCanonicalReason = structuredClone(dbProjection); rawCanonicalReason.reliability.reasons = ["the operator typed a raw sentence"];
  const withheldRawReason = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("attempt_receipt", activationReceipt), 2), turn(activationReliabilityWire(rawCanonicalReason), 3)]);
  assert.equal(withheldRawReason.passports[0].activation_reliability.canonical, null);
  assert.ok(withheldRawReason.rejected.some((row) => row.reason === "invalid_canonical_activation_reliability"));
  const conflictingCanonical = structuredClone(dbProjection); conflictingCanonical.reliability.state = "blocked";
  const duplicateConflict = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("attempt_receipt", activationReceipt), 2), turn(activationReliabilityWire(dbProjection), 3), turn(activationReliabilityWire(conflictingCanonical), 4)]);
  assert.equal(duplicateConflict.passports[0].activation_reliability.canonical, null);
  assert.ok(duplicateConflict.rejected.some((row) => row.reason === "conflicting_canonical_activation_reliability"));
  const persistentConflict = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("attempt_receipt", activationReceipt), 2), turn(activationReliabilityWire(dbProjection), 3), turn(activationReliabilityWire(conflictingCanonical), 4), turn(activationReliabilityWire(dbProjection), 5)]);
  assert.equal(persistentConflict.passports[0].activation_reliability.canonical, null);
  assert.equal(persistentConflict.rejected.filter((row) => row.reason === "conflicting_canonical_activation_reliability").length, 2);
  const eligibleCanonical = structuredClone(dbProjection);
  eligibleCanonical.canonical_revision.authority_fact_count = 4;
  eligibleCanonical.canonical_revision.outcome_horizon_mature = true;
  eligibleCanonical.reliability = { ...eligibleCanonical.reliability, state: "eligible_for_human_review", reasons: [], outcome_horizon_state: "mature" };
  const newerCanonical = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("attempt_receipt", activationReceipt), 2), turn(activationReliabilityWire(dbProjection), 3), turn(activationReliabilityWire(eligibleCanonical), 4)]);
  assert.equal(newerCanonical.passports[0].activation_reliability.canonical.reliability.state, "eligible_for_human_review");
  const staleCanonical = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("attempt_receipt", activationReceipt), 2), turn(activationReliabilityWire(eligibleCanonical), 3), turn(activationReliabilityWire(dbProjection), 4)]);
  assert.equal(staleCanonical.passports[0].activation_reliability.canonical.reliability.state, "eligible_for_human_review");
  assert.ok(staleCanonical.rejected.some((row) => row.reason === "stale_canonical_activation_reliability"));
  const forged = structuredClone(activationReceipt); forged.raw_transcript = "never";
  const withheld = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("attempt_receipt", forged), 2)]);
  assert.equal(withheld.passports[0].activation_reliability, null);
  assert.ok(withheld.rejected.some((row) => row.reason === "invalid_activation_reliability"));
  assert.match(roomSource, /Knowledge & Grounding/);
  assert.match(roomSource, /Route & Agent Topology/);
  assert.match(roomSource, /Evaluation & Outcome/);
  assert.match(roomSource, /All remain human-gated and unpromoted/);
});

testCase("MCP receipt admission refuses forged nested facts before database persistence", () => {
  const receipt = {
    schema_version: "attempt-receipt.v1", attempt_id: "attempt:strict", envelope_digest: "sha256:" + "a".repeat(64),
    attempt_ordinal: 1, adapter: {}, lifecycle: {}, result: {}, attestation: {}, negative_knowledge: [], telemetry: [], tool_event_summaries: [], observation: {}, interventions: [], handoff_proposal: {}, visual_artifacts: [], evaluation_binding: {},
    knowledge_activation: { bundle_digest: "sha256:" + "b".repeat(64), mode: "shadow", canonical_binding: { work_request_id: "WR-1", work_request_version: 1, accepted_plan_digest: "sha256:" + "a".repeat(64), envelope_digest: "sha256:" + "a".repeat(64), activation_binding_ref: "ctx:strict" }, item_dispositions: [{ item_ref: "rule:scope", disposition: "applied", evidence_refs: ["evidence:rule"], stage_ref: "stage:one", reason_ref: "reason:applied" }], closure: { state: "not_activated", unresolved_required_item_refs: [], derived_by: "server" } },
    reliability: { route_digest: "sha256:" + "1".repeat(64), topology_digest: "sha256:" + "2".repeat(64), evaluation_plan_digest: "sha256:" + "3".repeat(64), grounding_sufficiency: { state: "sufficient", evidence_refs: ["evidence:grounding"], required_supplied: ["rule:scope"], required_used: ["rule:scope"], required_missing: [], advisory_supplied: [], advisory_used: [], freshness_failures: [], retrieval_failures: [] }, deterministic_checks: [{ check_id: "check:one", state: "passed", critical: true, evidence_refs: ["evidence:one"] }], model_judgement: { state: "pass", judge_ref: "actor:judge", evidence_refs: ["evidence:judge"] }, human_acceptance: { state: "accepted", actor_ref: "actor:joe", evidence_refs: ["evidence:human"], outcome_feedback_ref: "OUTCOME-abcdef123456-v1", outcome_feedback_hash: "sha256:" + "f".repeat(64) }, trajectory: [{ sequence: 1, stage_ref: "stage:one", parent_event_ref: null, decision_class: "decision:one", tool_class: "tool:one", result_state: "succeeded", fallback_state: "not_used", guardrail_state: "clear", latency_ms: 1, evidence_refs: ["evidence:trace"] }], evaluator_results: [{ kind: "deterministic", evaluator_ref: "evaluator:deterministic", rubric_ref: "rubric:one", evaluator_version: "v1", evaluator_digest: "sha256:" + "4".repeat(64), status: "passed", confidence: "high", critical: true, independence_state: "not_independent", held_out_case_count: 1, check_refs: ["check:one"], dimension_refs: ["dimension:correctness"], evidence_refs: ["evidence:one"], judge_provenance: "provenance:deterministic", calibration_evidence_refs: [] }], corrections: [], defects: [], incidents: [], downstream_outcome: { state: "unknown", brokerage_ref: "deal:pending", evidence_refs: [], outcome_feedback_ref: null, outcome_feedback_hash: null }, outcome_horizon: { state: "mature", ends_at: "2026-08-24T12:00:00Z", as_of: "2026-08-24T12:00:00Z", evidence_refs: ["evidence:horizon"] }, process_metrics: { latency_ms: 1, cost_usd: 0, input_tokens: 1, output_tokens: 1, cached_input_tokens: 0, retry_count: 0, recovery_count: 0, context_reconstruction_ms: 0, human_intervention_count: 0, security_event_refs: [] }, eval_candidates: [], shadow_comparisons: [], learning_disposition: "none", telemetry: [], closure: { state: "insufficient_evidence", reasons: ["reason:authority-evidence"], derived_by: "server" } },
  };
  assert.equal(strictAttemptReceiptShape(receipt), true);
  const raw = structuredClone(receipt); raw.reliability.trajectory = [{ sequence: 1, raw_transcript: "secret" }];
  assert.equal(strictAttemptReceiptShape(raw), false);
  const forgedClosure = structuredClone(receipt); forgedClosure.knowledge_activation.closure.derived_by = "caller";
  assert.equal(strictAttemptReceiptShape(forgedClosure), false);
  const missingStage = structuredClone(receipt); delete missingStage.knowledge_activation.item_dispositions[0].stage_ref;
  assert.equal(strictAttemptReceiptShape(missingStage), false);
  const heldoutLeak = structuredClone(receipt); heldoutLeak.reliability.evaluator_results[0].expected_answer = "not for executor or UI";
  assert.equal(strictAttemptReceiptShape(heldoutLeak), false);
  const rawDisposition = structuredClone(receipt); rawDisposition.knowledge_activation.item_dispositions[0].reason_ref = "the operator typed a raw sentence";
  const rawIntervention = structuredClone(receipt); rawIntervention.interventions = [{ kind: "human", occurred_at: "2026-08-24T12:00:00Z", summary: "the operator typed a raw sentence" }];
  const rawHandoff = structuredClone(receipt); rawHandoff.handoff_proposal = { proposed: true, reason: "the operator typed a raw sentence", replacement_session_ref: "session:replacement", checkpoint_ref: "checkpoint:one", requires_independent_verification: true };
  const rawEvidence = structuredClone(receipt); rawEvidence.reliability.deterministic_checks[0].evidence_refs = ["the operator typed a raw sentence"];
  const rawCalibration = structuredClone(receipt); rawCalibration.reliability.evaluator_results[0].calibration_evidence_refs = ["the operator typed a raw sentence"];
  for (const attack of [rawDisposition, rawIntervention, rawHandoff, rawEvidence, rawCalibration]) assert.equal(strictAttemptReceiptShape(attack), false);
  const callerCandidate = structuredClone(receipt); callerCandidate.reliability.eval_candidates = [{ candidate_id: "eval-candidate:caller" }];
  assert.equal(strictAttemptReceiptShape(callerCandidate), false);
  const executorShadow = structuredClone(receipt); executorShadow.reliability.shadow_comparisons = [{ promotion_state: "active", side_effect_ref: "effect:forged" }];
  assert.equal(strictAttemptReceiptShape(executorShadow), false);
  const forgedHuman = structuredClone(receipt); forgedHuman.reliability.human_acceptance.outcome_feedback_hash = null;
  assert.equal(strictAttemptReceiptShape(forgedHuman), false);
});

testCase("receipt and activation reads use tenant-scoped server seams, never raw binding SELECT", () => {
  assert.match(evidenceActivationSource, /set_config\('carr\.organization_tenant_id'/);
  assert.match(evidenceActivationSource, /ops\.context_activation_receipt_binding/);
  assert.doesNotMatch(evidenceActivationSource, /from ops\.context_activation_binding where binding_id=\$1/);
  assert.match(evidenceActivationSource, /activation_not_found/);
  assert.match(evidenceActivationSource, /propose-evaluation-case/);
  assert.match(evidenceActivationSource, /transition-evaluation-case/);
  assert.match(evidenceActivationSource, /humanOnly: true, authorityOnly: true/);
});

testCase("spatial Home Zone binds exact visual state, preserves list parity, and withholds stale/conflicting views", () => {
  const model = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("spatial_surface", spatial), 2)]);
  const view = model.passports[0].spatial_surface;
  assert.equal(view.home_zone.return_label, "Return to Job Passport Home");
  assert.deepEqual(view.list_order, view.nodes.map((node) => node.node_id));
  assert.equal(view.nodes.find((node) => node.node_type === "attempt_lane").resource_refs[0], "native:codex-thread-1");
  const stale = structuredClone(spatial); stale.canonical_binding.state_version = 0;
  const mismatch = structuredClone(spatial); mismatch.canonical_binding.source_projection_digest = "sha256:" + "0".repeat(64);
  const withheld = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("spatial_surface", stale), 2), turn(wrap("spatial_surface", mismatch), 3)]);
  assert.equal(withheld.passports[0].spatial_surface, null);
  assert.ok(withheld.rejected.some((row) => row.reason === "mismatched_spatial_surface"));
  assert.match(roomSource, /Job Passport Home Zone/);
  assert.match(roomSource, /Telemetry: unavailable/);
  assert.match(roomCss, /passport-spatial-list/);
});

testCase("typed telemetry remains attributed, unavailable, and never manufactures a quota or cost zero", () => {
  const model = deriveJobPassports([
    turn(wrap("observatory_projection", fixture), 1), turn(wrap("telemetry_measurement", elapsedTelemetry), 2), turn(wrap("telemetry_measurement", unavailableCost), 3),
  ]);
  const telemetry = model.passports[0].telemetry_measurements;
  assert.equal(telemetry.length, 2);
  assert.equal(telemetry.find((row) => row.metric_kind === "elapsed_time").value.amount, 5000);
  assert.equal(telemetry.find((row) => row.metric_kind === "billed_cost").value.kind, "unavailable");
  const malformed = structuredClone(unavailableCost); malformed.value.amount = 0;
  const withheld = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("telemetry_measurement", malformed), 2)]);
  assert.equal(withheld.passports[0].telemetry_measurements.length, 0);
  assert.ok(withheld.rejected.some((row) => row.reason === "invalid_telemetry_measurement"));
  assert.match(roomSource, /unavailable — \$\{measurement\.value\.unavailable_reason\}/);
});

testCase("eval ladder binds to the exact visual projection and keeps critical regression visible without a score", () => {
  const model = deriveJobPassports([
    turn(wrap("observatory_projection", fixture), 1), turn(wrap("evaluation_kernel", portfolio), 2),
  ]);
  assert.equal(model.passports[0].eval_portfolio.portfolio_id, "portfolio:carr-synthetic-read-only");
  assert.deepEqual([...new Set(model.passports[0].eval_portfolio.cases.map((row) => row.rung))], ["smoke", "regression", "hill_climb", "launch"]);
  const candidate = model.passports[0].eval_portfolio.results.find((row) => row.result_id === "result:cheaper-candidate");
  assert.equal(candidate.dimension_results.find((row) => row.dimension_id === "visual_accessibility").direction_vs_baseline, "regressed");
  const mismatched = structuredClone(portfolio); mismatched.binding.projection_digest = "sha256:" + "0".repeat(64);
  const withheld = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("evaluation_kernel", mismatched), 2)]);
  assert.equal(withheld.passports[0].eval_portfolio, null);
  assert.ok(withheld.rejected.some((row) => row.reason === "mismatched_eval_portfolio"));
  assert.match(roomSource, /no aggregate score/);
  assert.match(roomCss, /passport-eval-matrix/);
  assert.match(roomSource, /Execution environment:/);
  assert.match(roomSource, /Environment session:/);
});

testCase("a malformed projection digest is visibly withheld before it can paint", () => {
  const malformed = structuredClone(fixture);
  malformed.projection_digest = "not-a-digest";
  const model = deriveJobPassports([turn(wrap("observatory_projection", malformed))]);
  assert.equal(model.enabled, false);
  assert.deepEqual(model.rejected, [{ seq: 1, reason: "invalid_projection" }]);
});

testCase("Engineering Passport binds to exact Observatory state and withholds stale/conflicting facts", () => {
  const matching = engineeringFor(fixture);
  const model = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("engineering_passport", matching), 2)]);
  assert.deepEqual(model.passports[0].engineering_passport, matching);
  const stale = engineeringFor(fixture); const newerProjection = structuredClone(fixture); newerProjection.source_state.state_version = 2; newerProjection.source_state.canonical_record_digest = "sha256:" + "9".repeat(64);
  const staleModel = deriveJobPassports([turn(wrap("observatory_projection", newerProjection), 1), turn(wrap("engineering_passport", stale), 2)]);
  assert.equal(staleModel.passports[0].engineering_passport, null);
  assert.ok(staleModel.rejected.some((row) => row.reason === "stale_engineering_passport"));
  const conflict = engineeringFor(fixture); conflict.work_request.canonical_record_digest = "sha256:" + "d".repeat(64); conflict.slice_plan.work_request.canonical_record_digest = conflict.work_request.canonical_record_digest; conflict.execution_envelopes[0].state_binding.canonical_record_digest = conflict.work_request.canonical_record_digest; conflict.receipts[0].envelope_digest = seal(conflict.execution_envelopes[0]); conflict.slice_plan.plan_digest = seal({ ...conflict.slice_plan, plan_digest: undefined }); delete conflict.slice_plan.plan_digest; conflict.slice_plan.plan_digest = seal(conflict.slice_plan); conflict.plan_digest = conflict.slice_plan.plan_digest; conflict.receipts[0].plan_digest = conflict.plan_digest; delete conflict.projection_digest; conflict.projection_digest = seal(conflict);
  const conflictModel = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("engineering_passport", matching), 2), turn(wrap("engineering_passport", conflict), 3)]);
  assert.equal(conflictModel.passports[0].engineering_passport, null);
  assert.ok(conflictModel.rejected.some((row) => row.reason === "conflicting_engineering_passport"));
});

testCase("engineering-slice-plan.v1 plans are still accepted exactly as before", () => {
  const matching = engineeringFor(fixture);
  const model = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("engineering_passport", matching), 2)]);
  assert.deepEqual(model.passports[0].engineering_passport, matching);
});

testCase("engineering-slice-plan.v2 plans are accepted with a valid design_contract", () => {
  const matching = engineeringForV2(fixture);
  const model = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("engineering_passport", matching), 2)]);
  assert.deepEqual(model.passports[0].engineering_passport, matching);
});

testCase("engineering-slice-plan.v2 refuses a design_contract with an extra or a missing key", () => {
  const extra = engineeringForV2(fixture);
  extra.slice_plan.slices[0].design_contract.unexpected_field = "nope";
  reseal(extra);
  const extraModel = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("engineering_passport", extra), 2)]);
  assert.equal(extraModel.passports[0].engineering_passport, null);
  assert.ok(extraModel.rejected.some((row) => row.reason === "invalid_engineering_passport"));

  const missing = engineeringForV2(fixture);
  delete missing.slice_plan.slices[0].design_contract.seam_decision;
  reseal(missing);
  const missingModel = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("engineering_passport", missing), 2)]);
  assert.equal(missingModel.passports[0].engineering_passport, null);
  assert.ok(missingModel.rejected.some((row) => row.reason === "invalid_engineering_passport"));
});

testCase("an unknown engineering-slice-plan schema_version is refused", () => {
  const unknown = engineeringForV2(fixture);
  unknown.slice_plan.schema_version = "engineering-slice-plan.v3";
  reseal(unknown);
  const unknownModel = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("engineering_passport", unknown), 2)]);
  assert.equal(unknownModel.passports[0].engineering_passport, null);
  assert.ok(unknownModel.rejected.some((row) => row.reason === "invalid_engineering_passport"));
});

testCase("browser refuses forged or malformed Engineering Passports before paint", () => {
  const forged = engineeringFor(fixture);
  forged.closure_state = "complete"; forged.slices[0].state = "verified_complete"; forged.projection_digest = seal(forged);
  const forgedModel = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("engineering_passport", forged), 2)]);
  assert.equal(forgedModel.passports[0].engineering_passport, null);
  assert.ok(forgedModel.rejected.some((row) => row.reason === "invalid_engineering_passport"));

  const malformed = engineeringFor(fixture); malformed.slice_plan = { schema_version: "engineering-slice-plan.v1" }; malformed.projection_digest = seal(malformed);
  const malformedModel = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("engineering_passport", malformed), 2)]);
  assert.equal(malformedModel.passports[0].engineering_passport, null);

  const duplicate = engineeringFor(fixture); duplicate.slices.push(structuredClone(duplicate.slices[0])); duplicate.projection_digest = seal(duplicate);
  const duplicateModel = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("engineering_passport", duplicate), 2)]);
  assert.equal(duplicateModel.passports[0].engineering_passport, null);

  const badDigest = engineeringFor(fixture); badDigest.projection_digest = "sha256:" + "0".repeat(64);
  const digestModel = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("engineering_passport", badDigest), 2)]);
  assert.equal(digestModel.passports[0].engineering_passport, null);
  assert.ok(digestModel.passports[0].attempt_lane, "invalid engineering fact must not remove the prior observatory card");

  const duplicateCheck = engineeringFor(fixture); duplicateCheck.slice_plan.slices[0].planned_checks.push(structuredClone(duplicateCheck.slice_plan.slices[0].planned_checks[0])); delete duplicateCheck.slice_plan.plan_digest; duplicateCheck.slice_plan.plan_digest = seal(duplicateCheck.slice_plan); duplicateCheck.plan_digest = duplicateCheck.slice_plan.plan_digest; delete duplicateCheck.projection_digest; duplicateCheck.projection_digest = seal(duplicateCheck);
  const duplicateCheckModel = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("engineering_passport", duplicateCheck), 2)]);
  assert.equal(duplicateCheckModel.passports[0].engineering_passport, null);

  const wrongEnvelope = engineeringFor(fixture); wrongEnvelope.execution_envelopes[0].work_request_id = "wr-other"; delete wrongEnvelope.projection_digest; wrongEnvelope.projection_digest = seal(wrongEnvelope);
  const wrongEnvelopeModel = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("engineering_passport", wrongEnvelope), 2)]);
  assert.equal(wrongEnvelopeModel.passports[0].engineering_passport, null);

  const unreferencedEnvelope = engineeringFor(fixture); const extraEnvelope = structuredClone(unreferencedEnvelope.execution_envelopes[0]); extraEnvelope.envelope_id = "env-synthetic-extra"; unreferencedEnvelope.execution_envelopes.push(extraEnvelope); unreferencedEnvelope.projection_digest = seal(unreferencedEnvelope);
  const unreferencedModel = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("engineering_passport", unreferencedEnvelope), 2)]);
  assert.equal(unreferencedModel.passports[0].engineering_passport, null);
});

testCase("browser mirrors strict ExecutionEnvelope authority invariants without throwing", () => {
  const mutations = [
    (passport) => { passport.execution_envelopes[0].server_binding.authority.read_only = false; },
    (passport) => { passport.execution_envelopes[0].server_binding.identity.client_mutable = true; },
    (passport) => { passport.execution_envelopes[0].server_binding.authority.derived_by = "client_claim"; },
    (passport) => { passport.execution_envelopes[0].request.allowed_actions = ["write_file"]; },
    (passport) => { passport.execution_envelopes[0].handoff.capability_inherited = true; },
    (passport) => { passport.execution_envelopes[0].state_binding.compare_and_swap_required = false; },
    (passport) => { passport.execution_envelopes[0].phase_binding.switch_conditions = ["phase_boundary"]; },
    (passport) => { passport.execution_envelopes[0].evaluation_context.experiment_arm = "forged_arm"; },
  ];
  for (const mutate of mutations) {
    const forged = engineeringFor(fixture); mutate(forged); forged.projection_digest = seal(forged);
    assert.doesNotThrow(() => {
      const model = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("engineering_passport", forged), 2)]);
      assert.equal(model.passports[0].engineering_passport, null);
    });
  }
});

testCase("browser rejects Python-invalid whitespace and identifier values", () => {
  const mutations = [
    (passport) => { passport.execution_envelopes[0].state_binding.accepted_resource_revisions[0].resource_ref = "   "; },
    (passport) => { passport.execution_envelopes[0].state_binding.accepted_resource_revisions[0].revision_ref = "revision with spaces"; },
    (passport) => { passport.execution_envelopes[0].server_binding.adapter.adapter_version = "   "; },
    (passport) => { passport.receipts[0].source_evidence.source_sha = "   "; },
  ];
  for (const mutate of mutations) {
    const forged = engineeringFor(fixture); mutate(forged); forged.projection_digest = seal(forged);
    assert.doesNotThrow(() => {
      const model = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("engineering_passport", forged), 2)]);
      assert.equal(model.passports[0].engineering_passport, null);
    });
  }
});

testCase("browser accepts reordered binding keys but rejects semantic mismatches", () => {
  const reordered = engineeringFor(fixture);
  reordered.work_request = { canonical_record_digest: reordered.work_request.canonical_record_digest, id: reordered.work_request.id, state_version: reordered.work_request.state_version };
  reordered.slice_plan.work_request = { state_version: reordered.work_request.state_version, id: reordered.work_request.id, canonical_record_digest: reordered.work_request.canonical_record_digest };
  reordered.accepted_plan_revision = { digest: reordered.accepted_plan_revision.digest, revision: reordered.accepted_plan_revision.revision, id: reordered.accepted_plan_revision.id };
  reordered.slice_plan.accepted_plan_revision = { revision: reordered.accepted_plan_revision.revision, id: reordered.accepted_plan_revision.id, digest: reordered.accepted_plan_revision.digest };
  reordered.execution_envelopes[0].plan_revision = { digest: reordered.execution_envelopes[0].plan_revision.digest, id: reordered.execution_envelopes[0].plan_revision.id, revision: reordered.execution_envelopes[0].plan_revision.revision };
  delete reordered.projection_digest; reordered.projection_digest = seal(reordered);
  const accepted = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("engineering_passport", reordered), 2)]);
  assert.ok(accepted.passports[0].engineering_passport);
  const mismatch = structuredClone(reordered); mismatch.slice_plan.work_request.id = "wr-other"; delete mismatch.projection_digest; mismatch.projection_digest = seal(mismatch);
  const refused = deriveJobPassports([turn(wrap("observatory_projection", fixture), 1), turn(wrap("engineering_passport", mismatch), 2)]);
  assert.equal(refused.passports[0].engineering_passport, null);
});

testCase("regression guards preserve keyboard drilldown across a poll and a real mobile grid row", () => {
  assert.match(roomSource, /card\.dataset\.detailOpen = "true"/);
  assert.match(roomSource, /detail\.open = card\.dataset\.detailOpen === "true"/);
  assert.match(roomSource, /event\.key === "Enter" \|\| event\.key === " "/);
  assert.match(roomCss, /minmax\(280px, max-content\)/);
  assert.match(roomCss, /\.job-passport \{ overflow: visible; min-height: max-content; \}/);
});
