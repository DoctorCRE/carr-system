import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

import { canonicalDigest, closureProjection } from "../src/engineering-runtime.js";

const contract = JSON.parse(fs.readFileSync(
  new URL("../../workspace/contracts/siep-01-heavy-build-session-reproduction.v1.json", import.meta.url),
  "utf8",
));
const fixture = JSON.parse(fs.readFileSync(
  new URL("./fixtures/siep-01-heavy-build-session-reproduction.v1.json", import.meta.url),
  "utf8",
));
const gate = fs.readFileSync(
  new URL("../../ops/siep-01-heavy-build-session-reproduction-local-pg-gate.py", import.meta.url),
  "utf8",
);
const migrationNames = fs.readdirSync(new URL("../../migrations/", import.meta.url));

class CapturedToolError extends Error {
  constructor(payload) { super(payload.error); Object.assign(this, payload); }
}

const exactKeys = (value, keys, label) =>
  assert.deepEqual(Object.keys(value).sort(), [...keys].sort(), label);

function observationPreimage(value) {
  return Object.fromEntries(Object.entries(value).filter(([key]) => key !== "observation_digest"));
}

function slice(sliceRef, ordinal) {
  return {
    baseline_evidence_refs: [{ ref: `evidence:siep01:${ordinal}`, content_digest: `sha256:${String(ordinal).repeat(64)}`, redaction_class: "metadata_only" }],
    concurrency_posture: "parallel_safe",
    declared_component_refs: ["component:engineering-passport"],
    declared_plan_step_refs: [`step:siep01:${ordinal}`],
    declared_resource_refs: ["resource:canonical-ledgers"],
    definition_of_done: "The exact canonical evidence state is read back.",
    dependency_refs: [],
    forbidden_change_refs: ["forbidden:production-mutation"],
    manual_qa_required: false,
    objective: "Reproduce one bounded execution history.",
    ordinal,
    planned_checks: [{ check_ref: `check:siep01:${ordinal}`, evidence_requirement: "metadata_only_sufficient", failure_condition: "Canonical evidence does not match the observation." }],
    release_requirement: "not_required",
    risk_class: "R1",
    scope_boundary: "Disposable source-test evidence only.",
    slice_ref: sliceRef,
  };
}

function passportFacts() {
  const binding = fixture.shared_binding;
  const slices = fixture.observations.map((item, index) => slice(item.slice_ref, index + 1));
  const preimage = {
    accepted_plan_revision: { id: binding.accepted_plan.ref, revision: binding.accepted_plan.revision, digest: binding.accepted_plan.hash },
    schema_version: "engineering-slice-plan.v1",
    slices,
    work_request: { id: `wr:${binding.work_request.id}`, state_version: binding.work_request.version, canonical_record_digest: binding.work_request.canonical_digest },
  };
  const plan = { ...preimage, plan_digest: canonicalDigest(preimage) };
  const good = fixture.observations.find(item => item.case === "fully_observed");
  const slicePlanId = binding.engineering_slice_plan.id;
  const workRequestId = binding.work_request.id;
  const createdAt = "2026-08-26T00:00:00Z";
  const receiptEvidence = {
    ref: "evidence:siep01:receipt",
    content_digest: `sha256:${"4".repeat(64)}`,
    redaction_class: "metadata_only",
  };
  const reviewEvidence = {
    ref: "evidence:siep01:review",
    content_digest: `sha256:${"5".repeat(64)}`,
    redaction_class: "metadata_only",
  };
  const envelope = {
    id: good.envelope.id,
    job_id: good.job.id,
    agent_session_id: good.capability_session_id,
    envelope_digest: good.envelope.digest,
    work_request_id: workRequestId,
    slice_plan_id: slicePlanId,
    slice_ref: good.slice_ref,
    created_at: createdAt,
    issued_at: createdAt,
    supersedes_envelope_id: null,
    envelope: {
      envelope_id: `env:${good.envelope.id}`,
      request: { job_ref: `job:${good.job.id}` },
      agent_session: { id: `session:${good.capability_session_id}` },
      server_binding: {
        identity: { agent_principal_id: "agent:siep01-executor" },
        adapter: { adapter_id: "adapter:siep01" },
      },
    },
  };
  const receipt = {
    actual_component_refs: ["component:engineering-passport"],
    actual_resource_refs: ["resource:canonical-ledgers"],
    artifact_refs: ["artifact:siep01:good"],
    attribution: {
      actor_ref: "agent:siep01-executor",
      adapter_ref: "adapter:siep01",
      session_ref: `session:${good.capability_session_id}`,
    },
    attempt_id: "attempt:1",
    checks: [{ check_ref: "check:siep01:1", evidence_refs: [receiptEvidence], state: "passed" }],
    deviations: [],
    envelope_digest: good.envelope.digest,
    evidence_refs: [receiptEvidence],
    executor_claim: { claim_state: "executor_claim", claimed_at: createdAt, claimed_by: "siep01-executor" },
    independent_verification_required: true,
    outcome: "claimed_complete",
    plan_digest: plan.plan_digest,
    planned_component_refs: ["component:engineering-passport"],
    planned_resource_refs: ["resource:canonical-ledgers"],
    reset_reconstruction: {
      fresh_session: true,
      inherited_transcript_used: false,
      reconstruction_free: true,
      remediation_action: null,
    },
    schema_version: "engineering-slice-receipt.v1",
    slice_ref: good.slice_ref,
    source_evidence: {
      branch_ref: "branch:siep01-fixture",
      evidence_refs: [receiptEvidence],
      source_sha: fixture.source_commit_sha,
      worktree_ref: "worktree:siep01-fixture",
    },
  };
  const receiptRow = {
    id: good.engineering_receipt.id,
    envelope_id: good.envelope.id,
    work_request_id: workRequestId,
    slice_ref: good.slice_ref,
    attempt_id: "attempt:1",
    outcome: "claimed_complete",
    executor_actor_id: good.executor_actor_id,
    executor_actor_active: true,
    executor_actor_slug: "siep01-executor",
    created_at: createdAt,
    receipt,
    receipt_digest: canonicalDigest(receipt),
  };
  const reviewerSessionRef = "session:siep01-reviewer";
  return {
    source: {
      work_request: { id: `wr:${binding.work_request.id}`, version: binding.work_request.version, canonical_record_digest: binding.work_request.canonical_digest },
      accepted_plan: { record_id: binding.accepted_plan.record_id, plan_ref: binding.accepted_plan.ref, revision: binding.accepted_plan.revision, digest: binding.accepted_plan.hash },
    },
    slice_plans: [{ id: slicePlanId, accepted_plan_id: binding.accepted_plan.record_id, accepted_plan_hash: binding.accepted_plan.hash, plan }],
    envelopes: [envelope],
    receipts: [receiptRow],
    reviewer_facts: [{
      id: good.reviewer_fact.id,
      receipt_id: good.engineering_receipt.id,
      work_request_id: workRequestId,
      slice_ref: good.slice_ref,
      reviewer_actor_id: good.reviewer_fact.reviewer_actor_id,
      reviewer_actor_active: true,
      reviewer_actor_slug: "siep01-reviewer",
      contract_version: "engineering-review.v1",
      reviewer_session_ref: reviewerSessionRef,
      state: "passed",
      created_at: createdAt,
      fact: {
        attempt_id: "attempt:1",
        evidence_refs: [reviewEvidence],
        is_independent: true,
        resolved_deviation_refs: [],
        reviewed_deviation_refs: [],
        reviewer_ref: "reviewer:siep01-reviewer",
        session_ref: reviewerSessionRef,
        slice_ref: good.slice_ref,
        state: "passed",
      },
    }],
  };
}

test("SIEP-01 fixture is closed, synthetic, and digest-bound", () => {
  assert.equal(contract.schema_version, "siep-heavy-build-session-reproduction.v1");
  assert.equal(contract.status, "source_test_only_no_production_enforcement_claim");
  exactKeys(fixture, contract.closed_top_level_fields, "fixture top-level fields drifted");
  exactKeys(fixture.shared_binding, contract.closed_shared_binding_fields, "shared binding fields drifted");
  assert.equal(fixture.synthetic, true);
  assert.match(fixture.source_commit_sha, /^[0-9a-f]{40}$/);
  assert.equal(fixture.observation_digest, canonicalDigest(observationPreimage(fixture)));
  assert.deepEqual(fixture.observations.map(item => item.case), contract.cases);
  for (const item of fixture.observations)
    exactKeys(item, contract.closed_observation_fields, `${item.case} fields drifted`);
});

test("generic success cannot make the half-executed sibling complete", () => {
  const [good, half] = fixture.observations;
  assert.equal(good.job.state, "succeeded");
  assert.equal(half.job.state, "succeeded");
  assert.equal(good.job_attempt.state, "succeeded");
  assert.equal(half.job_attempt.state, "succeeded");
  assert.equal(good.capability_session_id, half.capability_session_id);
  assert.equal(good.executor_actor_id, half.executor_actor_id);
  assert.equal(good.engineering_receipt.outcome, "claimed_complete");
  assert.equal(good.reviewer_fact.state, "passed");
  assert.notEqual(good.reviewer_fact.reviewer_actor_id, good.executor_actor_id);
  assert.equal(half.engineering_receipt, null);
  assert.equal(half.reviewer_fact, null);

  const passport = closureProjection(passportFacts(), CapturedToolError);
  assert.deepEqual(passport.slices.map(item => [item.slice_ref, item.state]), [
    [good.slice_ref, "verified_complete"],
    [half.slice_ref, "eligible"],
  ]);
  assert.equal(passport.closure_state, "blocked");
  assert.deepEqual(passport.closure.work.state, "unresolved");
  assert.deepEqual(passport.operator_receipt.remaining_risk, [half.slice_ref]);
});

test("the database gate uses existing ledgers and rolls every fixture back", () => {
  for (const name of [
    "record_sourced_heavy_build_admission",
    "review_sourced_heavy_build_plan",
    "engineering_register_slice_plan",
    "engineering_enqueue_slice_job",
    "engineering_claim_slice",
    "engineering_finalize_slice_receipt",
    "complete_job",
    "engineering_passport_facts",
  ]) assert.match(gate, new RegExp(`ops\\.${name}`, "i"));
  assert.match(gate, /rollback_only_connection/);
  assert.match(gate, /set_local_role\(cur, "carr_jobs"\)/);
  assert.match(gate, /set_local_role\(cur, "carr_writer"\)/);
  assert.match(gate, /rule_delivery_activation_target/);
  assert.match(gate, /caller-labelled-complete/);
  assert.doesNotMatch(gate, /engineering_heavy_ready_(?:containment|reconciliation|recontain)/i);
});

test("SIEP-01 owns no migration or runtime enforcement surface", () => {
  assert.equal(migrationNames.some(name => /siep.*heavy.*session.*reproduction/i.test(name)), false);
  assert.equal(contract.delivery_state.migration, "not_required_source_only_reproduction");
  assert.equal(contract.delivery_state.deploy, "not_applied");
  assert.equal(contract.delivery_state.production_enforcement, "not_activated");
});
