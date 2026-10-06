import assert from "node:assert/strict";
import test from "node:test";
import { readSystemWorkCensus, SYSTEM_WORK_LEGS } from "../src/system-work-census.v5.js";
import {
  canonicalDigest,
  ENGINEERING_REPOSITORY_ACTIONS,
  isCurrentRepositoryWriteEnvelope,
  buildCodexEnvelope,
  validateReceiptBinding,
  runCodexSlice,
  requirePlan,
  closureProjection,
  runEngineeringWorker,
  admitEngineeringSlice,
  recordEngineeringReview,
  resolveSourceMergeAuthority,
  engineeringRuntimeTools,
  portfolioAncestorBinding,
  classifyDesignDepth,
  designDepthInputs,
  ENGINEERING_SLICE_PLAN_VERSIONS,
  ENGINEERING_DESIGN_CONTRACT_VERSION,
  ENGINEERING_DESIGN_DEPTH_PREDICATE_VERSIONS,
  ENGINEERING_SERVER_EXECUTION_BINDING,
  withTrustedCanonicalOwnershipContext,
} from "../src/engineering-runtime.js";

const digest = value => canonicalDigest(value);
const source = {
  work: {
    id: "wr:11111111-1111-4111-8111-111111111111", ref: "WR-301", version: 3,
    canonical_record_digest: `sha256:${"1".repeat(64)}`,
  },
  plan: {
    record_id: "22222222-2222-4222-8222-222222222222", plan_ref: "plan:301",
    revision: 2, digest: `sha256:${"2".repeat(64)}`,
  },
};
const slice = {
  slice_ref: "slice:one", ordinal: 1, dependency_refs: [],
  declared_resource_refs: ["resource:worktree"], declared_component_refs: ["component:runtime"],
  declared_plan_step_refs: ["step:one"], risk_class: "R1",
};
const plan = { plan_digest: `sha256:${"3".repeat(64)}`, slices: [slice] };
const actor = { id: "33333333-3333-4333-8333-333333333333", slug: "codex", sponsoring_human_slug: "joe" };
class EngineeringToolError extends Error {
  constructor(payload) { super(payload.error || "engineering tool error"); Object.assign(this, payload); }
}

function currentClaimEnvelope(sessionId = "99999999-9999-4999-8999-999999999999") {
  const issued = new Date(Date.now()).toISOString().replace(/\.\d{3}Z$/, "Z");
  const expiry = new Date(Date.parse(issued) + 29 * 60 * 1000).toISOString().replace(/\.\d{3}Z$/, "Z");
  return {
    schema_version: "execution-envelope.v1", envelope_id: "env:88888888-8888-4888-8888-888888888888", work_request_id: "wr:11111111-1111-4111-8111-111111111111", issued_at: issued, expires_at: expiry,
    agent_session: { id: `session:${sessionId}`, lease_expires_at: expiry },
    request: { job_ref: "job:66666666-6666-4666-8666-666666666666", allowed_actions: [...ENGINEERING_REPOSITORY_ACTIONS] },
    // environment is part of the documented server execution binding, so the
    // rehearsal envelope this seam actually issues states it here.
    server_binding: { authority: { read_only: false, capability_profile: "capability:engineering-repository-write", environment: ENGINEERING_SERVER_EXECUTION_BINDING.environment },
      identity: { agent_principal_id: "agent:codex", runtime_principal: "runtime:codex" }, adapter: { surface: "codex_desktop", adapter_id: "adapter:codex-desktop" } },
  };
}

function controllerPlan() {
  const item = {
    slice_ref: "slice:one", ordinal: 1, objective: "Do the bounded work", definition_of_done: "A typed receipt exists",
    dependency_refs: [], declared_resource_refs: [], declared_component_refs: [], declared_plan_step_refs: [],
    baseline_evidence_refs: [], planned_checks: [{ check_ref: "check:one", failure_condition: "missing", evidence_requirement: "metadata_only_sufficient" }],
    scope_boundary: "one bounded slice", forbidden_change_refs: [], concurrency_posture: "parallel_safe", manual_qa_required: false,
    risk_class: "R1", release_requirement: "required",
  };
  const typed = { schema_version: "engineering-slice-plan.v1", work_request: { id: source.work.id, state_version: 3, canonical_record_digest: source.work.canonical_record_digest }, accepted_plan_revision: { id: source.plan.plan_ref, revision: 2, digest: source.plan.digest }, slices: [item] };
  typed.plan_digest = digest(typed);
  return typed;
}

function engineeringSlice(sliceRef, ordinal, dependency_refs = []) {
  return {
    slice_ref: sliceRef, ordinal, objective: `Execute ${sliceRef}`, definition_of_done: "A typed receipt exists",
    dependency_refs, declared_resource_refs: [], declared_component_refs: [], declared_plan_step_refs: [],
    baseline_evidence_refs: [], planned_checks: [{ check_ref: `check:${sliceRef}`, failure_condition: "missing", evidence_requirement: "metadata_only_sufficient" }],
    scope_boundary: `one bounded ${sliceRef}`, forbidden_change_refs: [], concurrency_posture: "serial_after_dependencies", manual_qa_required: false,
    risk_class: "R1", release_requirement: "required",
  };
}

function typedEngineeringPlan(slices) {
  const typed = {
    schema_version: "engineering-slice-plan.v1",
    work_request: { id: source.work.id, state_version: 3, canonical_record_digest: source.work.canonical_record_digest },
    accepted_plan_revision: { id: source.plan.plan_ref, revision: 2, digest: source.plan.digest },
    slices,
  };
  typed.plan_digest = digest(typed);
  return typed;
}

function passportFacts(plan, { envelopes = [], receipts = [], reviewer_facts = [] } = {}) {
  return {
    source: { work_request: source.work, accepted_plan: source.plan },
    slice_plans: [{ id: "12121212-1212-4212-8212-121212121212", accepted_plan_id: source.plan.record_id, accepted_plan_hash: source.plan.digest, plan }],
    envelopes, receipts, reviewer_facts,
  };
}

function receiptRow(id, envelope_id, slice_ref, outcome, created_at, attempt_id = "attempt:1") {
  const evidence = { ref: "evidence:receipt", redaction_class: "metadata_only", content_digest: `sha256:${"a".repeat(64)}` };
  return {
    id, envelope_id, work_request_id: source.work.id.replace(/^wr:/, ""), slice_ref, attempt_id, outcome,
    executor_actor_id: actor.id, executor_actor_active: true, executor_actor_slug: actor.slug, created_at,
    receipt: {
      actual_component_refs: [], actual_resource_refs: [], artifact_refs: ["artifact:receipt"], attribution: null,
      attempt_id, checks: [{ check_ref: `check:${slice_ref}`, evidence_refs: [evidence], state: "passed" }],
      deviations: [{
        category: "scope", deviation_ref: "deviation:resolved", evidence_refs: [evidence], impact: "low",
        out_of_scope_component_refs: [], out_of_scope_resource_refs: [], plan_revision_required: false,
        reason: "fixture resolved deviation", review_state: "resolved",
      }],
      envelope_digest: null, evidence_refs: [evidence], executor_claim: { claim_state: "executor_claim", claimed_at: created_at, claimed_by: actor.slug },
      independent_verification_required: true, outcome, plan_digest: null, planned_component_refs: [], planned_resource_refs: [],
      reset_reconstruction: { fresh_session: true, inherited_transcript_used: false, reconstruction_free: true, remediation_action: null },
      schema_version: "engineering-slice-receipt.v1", slice_ref, source_evidence: { branch_ref: "branch:fixture", evidence_refs: [evidence], source_sha: "0".repeat(40), worktree_ref: "worktree:fixture" },
    },
  };
}

function envelopeRow(id, slice_ref, created_at, supersedes_envelope_id = null) {
  const job_id = id.replace(/-/g, "").slice(0, 32);
  const envelope_digest = `sha256:${"e".repeat(64)}`;
  return { id, job_id, agent_session_id: id, envelope_digest, work_request_id: source.work.id.replace(/^wr:/, ""), slice_plan_id: "12121212-1212-4212-8212-121212121212", slice_ref, created_at, issued_at: created_at, supersedes_envelope_id,
    envelope: { envelope_id: `env:${id}`, request: { job_ref: `job:${job_id}` }, agent_session: { id: `session:${id}` },
      server_binding: { identity: { agent_principal_id: "agent:codex" }, adapter: { adapter_id: "adapter:codex-desktop" } } } };
}

function bindReceiptLineage(row, plan, envelope) {
  row.receipt.plan_digest = plan.plan_digest;
  row.receipt.envelope_digest = envelope.envelope_digest;
  row.receipt.attribution = {
    actor_ref: "agent:codex", adapter_ref: "adapter:codex-desktop", session_ref: envelope.envelope.agent_session.id,
  };
  row.receipt_digest = digest(row.receipt);
  return row;
}

function reviewerRow(id, receipt_id, slice_ref, state = "passed", created_at = "2026-08-26T00:00:00Z", attempt_id = "attempt:1") {
  return {
    id, receipt_id, work_request_id: source.work.id.replace(/^wr:/, ""), slice_ref, reviewer_actor_id: "99999999-9999-4999-8999-999999999999", reviewer_actor_active: true, reviewer_actor_slug: "reviewer", contract_version: "engineering-review.v1", reviewer_session_ref: `session:reviewer:${id}`, state, created_at,
    fact: { attempt_id, slice_ref, reviewer_ref: "reviewer:reviewer", session_ref: `session:reviewer:${id}`, state, evidence_refs: state === "passed" ? [{ ref: "evidence:review", redaction_class: "metadata_only", content_digest: `sha256:${"c".repeat(64)}` }] : [], is_independent: true, reviewed_deviation_refs: ["deviation:resolved"], resolved_deviation_refs: ["deviation:resolved"] },
  };
}

test("canonical digest is stable across object key order and matches SHA-256", () => {
  assert.equal(canonicalDigest({ b: 2, a: 1 }), canonicalDigest({ a: 1, b: 2 }));
  assert.equal(canonicalDigest({ a: 1 }), "sha256:015abd7f5cc57a2dd94b7590f04ad8084273905ee33ec5cebeae62276a97f862");
});

test("passport source is readable before an initial slice plan is registered", async () => {
  const tools = engineeringRuntimeTools({ withEnvelope: async (_c, _a, _verb, _args, fn) => fn(), writeEvent: async () => {}, ToolError: EngineeringToolError });
  const sourcePayload = { work_request: source.work, accepted_plan: source.plan };
  const c = { query: async (sql, params) => {
    assert.match(sql, /ops\.engineering_admission_source/);
    assert.deepEqual(params, ["WR-301"]);
    return { rows: [{ source: sourcePayload }] };
  } };
  const result = await tools["engineering-passport-source"].handler(c, actor, { work_request: "WR-301" });
  assert.equal(result.schema_version, "engineering-passport-source.v1");
  assert.deepEqual(result.work_request, source.work);
  assert.deepEqual(result.accepted_plan_revision, source.plan);
});

test("server builds a fresh Codex envelope and receipt binding rejects wrong attempt", () => {
  const envelope = buildCodexEnvelope({ source, plan, slice, jobId: "44444444-4444-4444-8444-444444444444", sessionId: "55555555-5555-4555-8555-555555555555", actor });
  assert.equal(envelope.server_binding.adapter.surface, "codex_desktop");
  assert.equal(envelope.plan_revision.id, source.plan.plan_ref);
  assert.equal(envelope.handoff.capability_inherited, false);
  assert.equal(envelope.server_binding.authority.read_only, false);
  assert.equal(envelope.server_binding.authority.capability_profile, "capability:engineering-repository-write");
  assert.deepEqual(envelope.request.allowed_actions, [
    "repository:create-worktree", "repository:create-branch", "repository:write-declared-scope",
    "repository:run-checks", "repository:commit", "repository:push-branch", "repository:open-pr",
  ]);
  assert.ok(!envelope.request.allowed_actions.some(action => /merge|deploy|production|review/.test(action)));
  const receipt = {
    schema_version: "engineering-slice-receipt.v1", envelope_digest: digest(envelope),
    attempt_id: "attempt:1", slice_ref: slice.slice_ref, plan_digest: plan.plan_digest,
    attribution: { actor_ref: "actor:codex", session_ref: "session:fresh", adapter_ref: "adapter:codex" },
    planned_resource_refs: [], actual_resource_refs: [], planned_component_refs: [], actual_component_refs: [],
    checks: [{ check_ref: "check:one", state: "passed", evidence_refs: [] }], artifact_refs: [], evidence_refs: [], deviations: [], source_evidence: {},
    outcome: "claimed_complete", independent_verification_required: true,
    reset_reconstruction: { fresh_session: true, inherited_transcript_used: false },
    executor_claim: { claimed_by: actor.slug },
  };
  assert.doesNotThrow(() => validateReceiptBinding(receipt, { ...envelope, envelope_digest: receipt.envelope_digest }, { ...slice, plan_digest: plan.plan_digest }, actor, Error));
  assert.throws(() => validateReceiptBinding({ ...receipt, envelope_digest: `sha256:${"9".repeat(64)}` }, { ...envelope, envelope_digest: receipt.envelope_digest }, { ...slice, plan_digest: plan.plan_digest }, actor, Error));
});

test("runtime refuses malformed, expired, read-only, wrong-environment and session-lease-mismatched packets before dispatch", () => {
  const envelope = currentClaimEnvelope();
  assert.equal(envelope.server_binding.authority.environment, ENGINEERING_SERVER_EXECUTION_BINDING.environment,
    "the rehearsal fixture must carry the environment the server binding documents");
  assert.equal(isCurrentRepositoryWriteEnvelope({ envelope }), true);
  const authority = envelope.server_binding.authority;
  for (const invalid of [
    { ...envelope, expires_at: "not-a-date" },
    { ...envelope, server_binding: { ...envelope.server_binding, authority: { ...authority, read_only: true } } },
    // The documented gate includes environment: an envelope naming another one
    // is not the binding this seam issues, so it may not dispatch as if it were.
    { ...envelope, server_binding: { ...envelope.server_binding, authority: { ...authority, environment: "production" } } },
    { ...envelope, server_binding: { ...envelope.server_binding, authority: { ...authority, environment: "staging" } } },
    { ...envelope, server_binding: { ...envelope.server_binding, authority: { ...authority, environment: undefined } } },
    { ...envelope, agent_session: { ...envelope.agent_session, lease_expires_at: "2000-01-01T00:00:00Z" } },
    { ...envelope, schema_version: "other" },
  ]) assert.equal(isCurrentRepositoryWriteEnvelope({ envelope: invalid }), false);
});

test("the first adapter dispatches only fresh Codex sessions and refuses Claude", async () => {
  let options;
  const result = await runCodexSlice({
    desk: "hermes-desktop", envelope: { server_binding: { adapter: { surface: "codex_desktop" } } }, task: { slice_ref: slice.slice_ref },
    dispatchEnvelope: async (_desk, _envelope, task, dispatchOptions) => { options = dispatchOptions; return { task }; },
  });
  assert.deepEqual(result, { task: { slice_ref: slice.slice_ref } });
  assert.deepEqual(options, { fresh: true });
  await assert.rejects(() => runCodexSlice({ dispatchEnvelope: async () => ({}), desk: "hermes-desktop", envelope: { server_binding: { adapter: { surface: "claude_desktop" } } }, task: {} }), /only codex_desktop/);
});

test("the committed plan shape is validated and Passport closure is schema-shaped and sealed", () => {
  const item = {
    slice_ref: "slice:one", ordinal: 1, objective: "Do the bounded work", definition_of_done: "A typed receipt exists",
    dependency_refs: [], declared_resource_refs: [], declared_component_refs: [], declared_plan_step_refs: [],
    baseline_evidence_refs: [], planned_checks: [{ check_ref: "check:one", failure_condition: "missing", evidence_requirement: "metadata_only_sufficient" }],
    scope_boundary: "one bounded slice", forbidden_change_refs: [], concurrency_posture: "parallel_safe", manual_qa_required: false,
    risk_class: "R1", release_requirement: "required",
  };
  const typedPlan = { schema_version: "engineering-slice-plan.v1", work_request: { id: source.work.id, state_version: 3, canonical_record_digest: source.work.canonical_record_digest }, accepted_plan_revision: { id: "plan:301", revision: 2, digest: source.plan.digest }, slices: [item] };
  typedPlan.plan_digest = digest(typedPlan);
  assert.equal(requirePlan(typedPlan, Error), typedPlan);
  assert.throws(() => requirePlan({ ...typedPlan, unexpected: true }, Error));
  assert.throws(() => requirePlan({ ...typedPlan, slices: [{ ...item, planned_checks: [{ ...item.planned_checks[0], extra: true }] }] }, Error));
  assert.throws(() => requirePlan({ ...typedPlan, slices: [{ ...item, baseline_evidence_refs: [{ ref: "evidence:one" }] }] }, Error));
  assert.throws(() => requirePlan({ ...typedPlan, slices: [{ ...item, risk_class: "R9" }] }, Error));
  const facts = { source: { work_request: source.work, accepted_plan: source.plan }, slice_plans: [{ accepted_plan_id: source.plan.record_id, accepted_plan_hash: source.plan.digest, plan: typedPlan }], envelopes: [], receipts: [], reviewer_facts: [] };
  const projection = closureProjection(facts, Error);
  assert.deepEqual(Object.keys(projection).sort(), ["accepted_plan_revision", "closure", "closure_state", "current_receipts", "current_reviewer_facts", "execution_envelopes", "operator_receipt", "plan_digest", "projection_digest", "qa_facts", "receipts", "reviewer_facts", "schema_version", "slice_plan", "slices", "stale_conflict", "work_request"].sort());
  for (const key of ["slice_plan", "execution_envelopes", "receipts", "reviewer_facts", "qa_facts", "operator_receipt", "projection_digest"]) assert.ok(projection[key]);
  for (const key of ["work", "proof", "explanation", "release"]) assert.deepEqual(Object.keys(projection.closure[key]).sort(), ["evidence_refs", "note", "state"]);
  assert.deepEqual(Object.keys(projection.closure.learning).sort(), ["evidence_refs", "note", "route", "state"]);
  assert.equal(projection.projection_digest, digest({ ...projection, projection_digest: undefined }));
  assert.equal(projection.closure.work.state, "unresolved");
  assert.match(projection.projection_digest, /^sha256:[0-9a-f]{64}$/);
});

test("the worker invokes the fresh Codex path and submits the returned typed receipt", async () => {
  const calls = [];
  const typed = controllerPlan();
  const fakeClaim = { definition_key: "engineering-slice", job_id: "66666666-6666-4666-8666-666666666666", attempt: 1, lease_token: "77777777-7777-4777-8777-777777777777", envelope_id: "88888888-8888-4888-8888-888888888888", envelope_digest: `sha256:${"a".repeat(64)}`, envelope: currentClaimEnvelope(), payload: { work_request: "WR-301", slice_ref: "slice:one", plan_digest: typed.plan_digest } };
  const c = { query: async (sql) => {
    calls.push(sql);
    if (sql.includes("ops.engineering_claim_slice")) return { rows: [fakeClaim] };
    if (sql.includes("engineering_execution_envelope")) return { rows: [{ id: fakeClaim.envelope_id, job_id: fakeClaim.job_id, work_request_id: "11111111-1111-4111-8111-111111111111", issued_at: fakeClaim.envelope.issued_at, expires_at: fakeClaim.envelope.expires_at, agent_session_id: "99999999-9999-4999-8999-999999999999", agent_session_lease_expires_at: fakeClaim.envelope.agent_session.lease_expires_at, envelope: fakeClaim.envelope, envelope_digest: `sha256:${"a".repeat(64)}`, slice_ref: "slice:one" }] };
    if (sql.includes("engineering_controller_binding")) return { rows: [{ binding: { envelope_id: fakeClaim.envelope_id, envelope_digest: fakeClaim.envelope_digest, slice_ref: "slice:one", plan_digest: typed.plan_digest, slice_plan: typed, executor_actor: { id: actor.id, slug: actor.slug }, agent_session_lease_expires_at: fakeClaim.envelope.expires_at, job_lease_expires_at: fakeClaim.envelope.expires_at } }] };
    if (sql.includes("engineering_passport_facts")) return { rows: [{ facts: passportFacts(typed) }] };
    if (sql.includes("engineering_finalize_slice_receipt")) return { rows: [{ id: "receipt:one" }] };
    return { rows: [] };
  } };
  const receipt = { schema_version: "engineering-slice-receipt.v1", envelope_digest: fakeClaim.envelope_digest, attempt_id: "attempt:1", slice_ref: "slice:one", plan_digest: typed.plan_digest, attribution: {}, planned_resource_refs: [], actual_resource_refs: [], planned_component_refs: [], actual_component_refs: [], checks: [{ check_ref: "check:one" }], artifact_refs: [], evidence_refs: [], deviations: [], source_evidence: {}, reset_reconstruction: { fresh_session: true, inherited_transcript_used: false }, executor_claim: { claimed_by: "codex" }, independent_verification_required: true, outcome: "claimed_complete" };
  const result = await runEngineeringWorker({ c, worker: "engineering-worker", desk: "hermes-desktop",
    withCanonicalOwnership: async (claim, execute) => {
      assert.equal(claim.job_id, fakeClaim.job_id);
      calls.push("ownership-acquire");
      const value = await execute();
      calls.push("ownership-release");
      return value;
    },
    dispatchEnvelope: async (_desk, _envelope, task, options) => { calls.push({ task, options }); return receipt; }, ToolError: Error });
  assert.equal(result.completed, 1);
  assert.ok(calls.findIndex(sql => typeof sql === "string" && sql.includes("ops.reap_expired_jobs")) <
    calls.findIndex(sql => typeof sql === "string" && sql.includes("ops.engineering_claim_slice")));
  assert.ok(calls.findIndex(sql => typeof sql === "string" && sql.includes("ops.engineering_retire_permanently_ineligible_jobs")) <
    calls.findIndex(sql => typeof sql === "string" && sql.includes("ops.engineering_claim_slice")));
  assert.deepEqual(calls.find(row => row.options)?.options, { fresh: true });
  assert.ok(calls.indexOf("ownership-acquire") < calls.findIndex(row => row.options));
  assert.ok(calls.indexOf("ownership-release") > calls.findIndex(row => row.options));
  assert.ok(calls.indexOf("ownership-release") <
    calls.findIndex(sql => typeof sql === "string" && sql.includes("engineering_finalize_slice_receipt")));
  assert.equal(calls.find(row => row.options)?.task.engineering_plan.plan_digest, typed.plan_digest);
  assert.equal(calls.find(row => row.options)?.task.work_request, typed.work_request.id);
  assert.equal(calls.find(row => row.options)?.task.work_request_ref, "WR-301",
    "the canonical human Work Request ref must survive beside the immutable UUID binding");
  assert.equal(calls.find(row => row.options)?.task.claim_lease_expires_at, fakeClaim.envelope.expires_at);
  assert.ok(!calls.some(sql => typeof sql === "string" && /from ops\.work_request/i.test(sql)));
  assert.equal(calls.filter(sql => typeof sql === "string" && /ops\.(?:complete_job|fail_job)/.test(sql)).length, 0,
    "runtime must leave post-receipt job finalization to the transactional database seam");
});

test("the controller fails closed without launching Codex when the claim payload has no canonical Work Request ref", async () => {
  const typed = controllerPlan();
  const claim = { definition_key: "engineering-slice", job_id: "66666666-6666-4666-8666-666666666666", attempt: 1, lease_token: "77777777-7777-4777-8777-777777777777", envelope_id: "88888888-8888-4888-8888-888888888888", envelope_digest: `sha256:${"a".repeat(64)}`, envelope: currentClaimEnvelope(), payload: { slice_ref: "slice:one", plan_digest: typed.plan_digest } };
  const calls = [];
  const c = { query: async (sql) => {
    calls.push(sql);
    if (sql.includes("ops.engineering_claim_slice")) return { rows: [claim] };
    if (sql.includes("engineering_execution_envelope")) return { rows: [{ id: claim.envelope_id, job_id: claim.job_id, work_request_id: "11111111-1111-4111-8111-111111111111", issued_at: claim.envelope.issued_at, expires_at: claim.envelope.expires_at, agent_session_id: "99999999-9999-4999-8999-999999999999", agent_session_lease_expires_at: claim.envelope.agent_session.lease_expires_at, envelope: claim.envelope, envelope_digest: claim.envelope_digest }] };
    if (sql.includes("engineering_controller_binding")) return { rows: [{ binding: { envelope_id: claim.envelope_id, envelope_digest: claim.envelope_digest, slice_ref: "slice:one", plan_digest: typed.plan_digest, slice_plan: typed, executor_actor: { id: actor.id, slug: actor.slug }, agent_session_lease_expires_at: claim.envelope.expires_at, job_lease_expires_at: claim.envelope.expires_at } }] };
    if (sql.includes("ops.engineering_fail_claim")) return { rows: [{ state: "retry_wait" }] };
    return { rows: [] };
  } };
  let launched = 0;
  const result = await runEngineeringWorker({ c, worker: "engineering-worker", desk: "engineering-codex", ToolError: EngineeringToolError,
    dispatchEnvelope: async () => { launched += 1; return {}; } });
  assert.equal(launched, 0);
  assert.equal(result.completed, 0);
  assert.deepEqual(result.results[0], { job_id: claim.job_id, state: "retry_wait", failure_class: "engineering_dispatch_failed" });
});

test("an atomically finalized receipt survives controller readback failure without a compensating scoped failure", async () => {
  const typed = controllerPlan();
  const claim = { definition_key: "engineering-slice", job_id: "66666666-6666-4666-8666-666666666666", attempt: 1, lease_token: "77777777-7777-4777-8777-777777777777", envelope_id: "88888888-8888-4888-8888-888888888888", envelope: currentClaimEnvelope(), envelope_digest: `sha256:${"a".repeat(64)}`, payload: { work_request: "WR-301", slice_ref: "slice:one", plan_digest: typed.plan_digest } };
  const calls = [];
  let receiptPersisted = false;
  const c = { query: async (sql) => {
    calls.push(sql);
    if (sql.includes("ops.engineering_claim_slice")) return { rows: [claim] };
    if (sql.includes("engineering_execution_envelope")) return { rows: [{ id: claim.envelope_id, job_id: claim.job_id, work_request_id: "11111111-1111-4111-8111-111111111111", issued_at: claim.envelope.issued_at, expires_at: claim.envelope.expires_at, agent_session_id: "99999999-9999-4999-8999-999999999999", agent_session_lease_expires_at: claim.envelope.agent_session.lease_expires_at, envelope: claim.envelope, envelope_digest: claim.envelope_digest, slice_ref: "slice:one" }] };
    if (sql.includes("engineering_controller_binding")) return { rows: [{ binding: { envelope_id: claim.envelope_id, envelope_digest: claim.envelope_digest, slice_ref: "slice:one", plan_digest: typed.plan_digest, slice_plan: typed, executor_actor: { id: actor.id, slug: actor.slug }, agent_session_lease_expires_at: claim.envelope.expires_at, job_lease_expires_at: claim.envelope.expires_at } }] };
    if (sql.includes("work_request w")) return { rows: [{ ref: "WR-301" }] };
    if (sql.includes("engineering_passport_facts")) {
      if (receiptPersisted) throw new Error("readback database unavailable");
      return { rows: [{ facts: { source: { work_request: source.work, accepted_plan: source.plan }, slice_plans: [{ accepted_plan_id: source.plan.record_id, accepted_plan_hash: source.plan.digest, plan: typed }], envelopes: [], receipts: [], reviewer_facts: [] } }] };
    }
    if (sql.includes("engineering_finalize_slice_receipt")) { receiptPersisted = true; return { rows: [{ id: "receipt:one" }] }; }
    if (sql.includes("ops.engineering_fail_claim")) return { rows: [{ state: "retry_wait" }] };
    return { rows: [] };
  } };
  const receipt = { schema_version: "engineering-slice-receipt.v1", envelope_digest: claim.envelope_digest, attempt_id: "attempt:1", slice_ref: "slice:one", plan_digest: typed.plan_digest, attribution: {}, planned_resource_refs: [], actual_resource_refs: [], planned_component_refs: [], actual_component_refs: [], checks: [{ check_ref: "check:one" }], artifact_refs: [], evidence_refs: [], deviations: [], source_evidence: {}, reset_reconstruction: { fresh_session: true, inherited_transcript_used: false }, executor_claim: { claimed_by: "codex" }, independent_verification_required: true, outcome: "claimed_complete" };
  const result = await runEngineeringWorker({ c, worker: "engineering-worker", desk: "engineering-codex", ToolError: Error,
    dispatchEnvelope: async () => receipt });
  assert.equal(result.results[0].ok, true);
  assert.deepEqual(result.results[0].operator_readback, { state: "unavailable", reason: "readback_failed" });
  assert.equal(calls.filter(sql => typeof sql === "string" && sql.includes("ops.engineering_fail_claim")).length, 0);
});

test("a failed cleanup lease is reported as cleanup_deferred without redispatch", async () => {
  const typed = controllerPlan();
  const claim = { definition_key: "engineering-slice", job_id: "66666666-6666-4666-8666-666666666666", attempt: 1, lease_token: "77777777-7777-4777-8777-777777777777", envelope_id: "88888888-8888-4888-8888-888888888888", envelope: currentClaimEnvelope(), envelope_digest: `sha256:${"a".repeat(64)}`, payload: { work_request: "WR-301", slice_ref: "slice:one", plan_digest: typed.plan_digest } };
  const calls = [];
  let dispatched = 0;
  const c = { query: async (sql) => {
    calls.push(sql);
    if (sql.includes("ops.engineering_claim_slice")) return { rows: [claim] };
    if (sql.includes("engineering_execution_envelope")) return { rows: [{ id: claim.envelope_id, job_id: claim.job_id, work_request_id: "11111111-1111-4111-8111-111111111111", issued_at: claim.envelope.issued_at, expires_at: claim.envelope.expires_at, agent_session_id: "99999999-9999-4999-8999-999999999999", agent_session_lease_expires_at: claim.envelope.agent_session.lease_expires_at, envelope: claim.envelope, envelope_digest: claim.envelope_digest }] };
    if (sql.includes("engineering_controller_binding")) return { rows: [{ binding: { envelope_id: claim.envelope_id, envelope_digest: claim.envelope_digest, slice_ref: "slice:one", plan_digest: typed.plan_digest, slice_plan: typed, executor_actor: { id: actor.id, slug: actor.slug }, agent_session_lease_expires_at: claim.envelope.expires_at, job_lease_expires_at: claim.envelope.expires_at } }] };
    if (sql.includes("ops.engineering_fail_claim")) throw new Error("lease already lost");
    return { rows: [] };
  } };
  const result = await runEngineeringWorker({ c, worker: "engineering-worker", desk: "engineering-codex", ToolError: Error,
    dispatchEnvelope: async () => { dispatched += 1; throw new Error("adapter unavailable"); } });
  assert.equal(dispatched, 1);
  assert.equal(result.results[0].job_id, claim.job_id);
  assert.equal(result.results[0].state, "cleanup_deferred");
  assert.equal(calls.filter(sql => typeof sql === "string" && sql.includes("ops.engineering_claim_slice")).length, 1);
});

test("a controller dispatch failure records the canonical retry receipt without a duplicate dispatch", async () => {
  const typed = controllerPlan();
  const claim = { definition_key: "engineering-slice", job_id: "66666666-6666-4666-8666-666666666666", attempt: 1, lease_token: "77777777-7777-4777-8777-777777777777", envelope_id: "88888888-8888-4888-8888-888888888888", envelope_digest: `sha256:${"a".repeat(64)}`, envelope: currentClaimEnvelope(), payload: { work_request: "WR-301", slice_ref: "slice:one", plan_digest: typed.plan_digest } };
  const calls = [];
  const c = { query: async (sql) => {
    calls.push(sql);
    if (sql.includes("ops.engineering_claim_slice")) return { rows: [claim] };
    if (sql.includes("engineering_execution_envelope")) return { rows: [{ id: claim.envelope_id, job_id: claim.job_id, work_request_id: "11111111-1111-4111-8111-111111111111", issued_at: claim.envelope.issued_at, expires_at: claim.envelope.expires_at, agent_session_id: "99999999-9999-4999-8999-999999999999", agent_session_lease_expires_at: claim.envelope.agent_session.lease_expires_at, envelope: claim.envelope, envelope_digest: claim.envelope_digest }] };
    if (sql.includes("engineering_controller_binding")) return { rows: [{ binding: { envelope_id: claim.envelope_id, envelope_digest: claim.envelope_digest, slice_ref: "slice:one", plan_digest: typed.plan_digest, slice_plan: typed, executor_actor: { id: actor.id, slug: actor.slug }, agent_session_lease_expires_at: claim.envelope.expires_at, job_lease_expires_at: claim.envelope.expires_at } }] };
    if (sql.includes("ops.engineering_fail_claim")) return { rows: [{ state: "retry_wait" }] };
    return { rows: [] };
  } };
  let dispatched = 0;
  const result = await runEngineeringWorker({ c, worker: "engineering-worker", desk: "engineering-codex", ToolError: Error,
    dispatchEnvelope: async () => { dispatched += 1; throw new Error("adapter unavailable"); } });
  assert.equal(dispatched, 1);
  assert.equal(result.completed, 0);
  assert.deepEqual(result.results[0], { job_id: claim.job_id, state: "retry_wait", failure_class: "engineering_dispatch_failed" });
  assert.equal(calls.filter(sql => typeof sql === "string" && sql.includes("ops.engineering_fail_claim")).length, 1);
});

test("the controller fails closed without launching Codex when post-claim currentness is invalid", async () => {
  const typed = controllerPlan();
  const envelope = { ...currentClaimEnvelope(), agent_session: { id: "session:99999999-9999-4999-8999-999999999999", lease_expires_at: "2000-01-01T00:00:00Z" } };
  const claim = { definition_key: "engineering-slice", job_id: "66666666-6666-4666-8666-666666666666", attempt: 1, lease_token: "77777777-7777-4777-8777-777777777777", envelope_id: "88888888-8888-4888-8888-888888888888", envelope_digest: `sha256:${"a".repeat(64)}`, payload: { work_request: "WR-301", slice_ref: "slice:one", plan_digest: typed.plan_digest } };
  const calls = [];
  const c = { query: async (sql) => {
    calls.push(sql);
    if (sql.includes("ops.engineering_claim_slice")) return { rows: [claim] };
    if (sql.includes("engineering_execution_envelope")) return { rows: [{ id: claim.envelope_id, agent_session_id: "99999999-9999-4999-8999-999999999999", agent_session_lease_expires_at: "2000-01-01T00:00:00Z", envelope, envelope_digest: claim.envelope_digest }] };
    if (sql.includes("ops.engineering_fail_claim")) return { rows: [{ state: "retry_wait" }] };
    return { rows: [] };
  } };
  let launched = 0;
  const result = await runEngineeringWorker({ c, worker: "engineering-worker", desk: "engineering-codex", ToolError: Error, dispatchEnvelope: async () => { launched += 1; return {}; } });
  assert.equal(launched, 0);
  assert.equal(result.completed, 0);
  assert.equal(calls.filter(sql => typeof sql === "string" && sql.includes("ops.engineering_fail_claim")).length, 1);
});

test("the controller chooses the newest immutable successor envelope for an idempotent job", async () => {
  const typed = controllerPlan();
  const claim = { definition_key: "engineering-slice", job_id: "66666666-6666-4666-8666-666666666666", attempt: 1, lease_token: "77777777-7777-4777-8777-777777777777", envelope_id: "88888888-8888-4888-8888-888888888888", envelope_digest: `sha256:${"a".repeat(64)}`, envelope: currentClaimEnvelope(), payload: { work_request: "WR-301", slice_ref: "slice:one", plan_digest: typed.plan_digest } };
  let envelopeQuery = "";
  const c = { query: async (sql) => {
    if (sql.includes("ops.engineering_claim_slice")) return { rows: [claim] };
    if (sql.includes("engineering_execution_envelope")) {
      envelopeQuery = sql;
      return { rows: [{ id: claim.envelope_id, job_id: claim.job_id, work_request_id: "11111111-1111-4111-8111-111111111111", issued_at: claim.envelope.issued_at, expires_at: claim.envelope.expires_at, agent_session_id: "99999999-9999-4999-8999-999999999999", agent_session_lease_expires_at: claim.envelope.agent_session.lease_expires_at, envelope: claim.envelope, envelope_digest: claim.envelope_digest }] };
    }
    if (sql.includes("engineering_controller_binding")) return { rows: [{ binding: { envelope_id: claim.envelope_id, envelope_digest: claim.envelope_digest, slice_ref: "slice:one", plan_digest: typed.plan_digest, slice_plan: typed, executor_actor: { id: actor.id, slug: actor.slug }, agent_session_lease_expires_at: claim.envelope.expires_at, job_lease_expires_at: claim.envelope.expires_at } }] };
    if (sql.includes("ops.engineering_fail_claim")) return { rows: [{ state: "retry_wait" }] };
    return { rows: [] };
  } };
  await runEngineeringWorker({ c, worker: "engineering-worker", desk: "engineering-codex", ToolError: Error,
    dispatchEnvelope: async () => { throw new Error("stop after controller binding"); } });
  assert.match(envelopeQuery, /order by e\.issued_at desc, e\.id desc limit 1/i);
  assert.doesNotMatch(envelopeQuery, /capability_agent_session/i);
});

test("dependency admission binds the pass to the newest exact receipt and does not open a write path on refusal", async () => {
  const plan = typedEngineeringPlan([engineeringSlice("slice:one", 1), engineeringSlice("slice:two", 2, ["slice:one"])]);
  const oldEnvelope = envelopeRow("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "slice:one", "2026-08-26T00:00:01Z");
  const newEnvelope = envelopeRow("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "slice:one", "2026-08-26T00:00:02Z", oldEnvelope.id);
  const oldReceipt = bindReceiptLineage(receiptRow("cccccccc-cccc-4ccc-8ccc-cccccccccccc", oldEnvelope.id, "slice:one", "failed", "2026-08-26T00:00:03Z"), plan, oldEnvelope);
  const newReceipt = bindReceiptLineage(receiptRow("dddddddd-dddd-4ddd-8ddd-dddddddddddd", newEnvelope.id, "slice:one", "claimed_complete", "2026-08-26T00:00:04Z"), plan, newEnvelope);
  const facts = passportFacts(plan, {
    envelopes: [oldEnvelope, newEnvelope], receipts: [oldReceipt, newReceipt],
    reviewer_facts: [reviewerRow("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", oldReceipt.id, "slice:one")],
  });
  const calls = [];
  const c = { query: async (sql, params = []) => {
    calls.push({ sql, params });
    if (sql.includes("engineering_passport_facts")) return { rows: [{ facts }] };
    return { rows: [] };
  } };
  await assert.rejects(
    () => admitEngineeringSlice(c, actor, { idempotency_key: "99999999-9999-4999-8999-999999999999", work_request: source.work.ref, slice_ref: "slice:two" }, EngineeringToolError, async () => {}),
    error => error.error === "engineering_dependencies_not_verified",
  );
  assert.equal(calls.some(call => /pg_advisory_xact_lock|engineering_enqueue_slice_job|insert into ops\./i.test(call.sql)), false,
    "unreviewed successor must be refused before advisory lock or enqueue/write paths");

  facts.reviewer_facts.push(reviewerRow("ffffffff-ffff-4fff-8fff-ffffffffffff", newReceipt.id, "slice:one", "passed", "2026-08-26T00:00:05Z"));
  const writes = [];
  const success = { query: async (sql, params = []) => {
    writes.push({ sql, params });
    if (sql.includes("engineering_passport_facts")) return { rows: [{ facts }] };
    if (sql.includes("from actor")) return { rows: [{ id: actor.id, slug: actor.slug }] };
    if (sql.trimStart().startsWith("select id from ops.engineering_slice_plan")) return { rows: [{ id: "12121212-1212-4212-8212-121212121212" }] };
    if (sql.includes("select id from ops.work_request")) return { rows: [{ id: source.work.id.replace(/^wr:/, "") }] };
    if (sql.includes("insert into ops.capability_agent_session")) return { rows: [{ id: "22222222-2222-4222-8222-222222222222" }] };
    if (sql.includes("capability_agent_session")) return { rows: [] };
    if (sql.includes("engineering_enqueue_slice_job")) return { rows: [{ id: "11111111-1111-4111-8111-111111111111" }] };
    if (sql.includes("insert into ops.engineering_execution_envelope")) return { rows: [{ id: "33333333-3333-4333-8333-333333333333" }] };
    return { rows: [] };
  } };
  const admitted = await admitEngineeringSlice(success, actor, {
    idempotency_key: "44444444-4444-4444-8444-444444444444", work_request: source.work.ref, slice_ref: "slice:two",
  }, EngineeringToolError, async () => {});
  assert.equal(admitted.envelope_id, "33333333-3333-4333-8333-333333333333");
  assert.ok(writes.some(call => call.sql.includes("engineering_enqueue_slice_job")));
});

test("closure projection is generation-aware: exact review completes, unreviewed successor blocks, later failure reopens", () => {
  const plan = typedEngineeringPlan([engineeringSlice("slice:one", 1)]);
  const e1 = envelopeRow("11111111-1111-4111-8111-111111111111", "slice:one", "2026-08-26T00:00:01Z");
  const e2 = envelopeRow("22222222-2222-4222-8222-222222222222", "slice:one", "2026-08-26T00:00:03Z", e1.id);
  const e3 = envelopeRow("33333333-3333-4333-8333-333333333333", "slice:one", "2026-08-26T00:00:05Z", e2.id);
  const failed = bindReceiptLineage(receiptRow("44444444-4444-4444-8444-444444444444", e1.id, "slice:one", "failed", "2026-08-26T00:00:02Z"), plan, e1);
  const success = bindReceiptLineage(receiptRow("55555555-5555-4555-8555-555555555555", e2.id, "slice:one", "claimed_complete", "2026-08-26T00:00:04Z"), plan, e2);
  const laterFailed = bindReceiptLineage(receiptRow("66666666-6666-4666-8666-666666666666", e3.id, "slice:one", "failed", "2026-08-26T00:00:06Z"), plan, e3);
  const oldPass = reviewerRow("77777777-7777-4777-8777-777777777777", failed.id, "slice:one", "passed", "2026-08-26T00:00:02Z");
  const exactPass = reviewerRow("88888888-8888-4888-8888-888888888888", success.id, "slice:one", "passed", "2026-08-26T00:00:04Z");
  const base = passportFacts(plan, { envelopes: [e1, e2, e3], receipts: [failed, success, laterFailed], reviewer_facts: [oldPass, exactPass] });
  const verified = closureProjection(base, Error);
  assert.equal(verified.slices[0].state, "reopened", "the later failed generation must override the earlier verified success");
  assert.equal(verified.closure_state, "blocked");
  assert.deepEqual(verified.receipts, [failed.receipt, success.receipt, laterFailed.receipt]);
  assert.deepEqual(verified.reviewer_facts, [oldPass.fact, exactPass.fact]);

  const throughSuccess = passportFacts(plan, { envelopes: [e1, e2], receipts: [failed, success], reviewer_facts: [oldPass] });
  const unreviewed = closureProjection(throughSuccess, Error);
  assert.equal(unreviewed.slices[0].state, "claimed");
  assert.equal(unreviewed.closure_state, "blocked");
  assert.deepEqual(unreviewed.receipts, [failed.receipt, success.receipt]);
  assert.deepEqual(unreviewed.reviewer_facts, [oldPass.fact]);

  const exact = closureProjection({ ...throughSuccess, reviewer_facts: [oldPass, exactPass] }, Error);
  assert.equal(exact.slices[0].state, "verified_complete");
  assert.equal(exact.closure_state, "complete");
  assert.deepEqual(exact.receipts, [failed.receipt, success.receipt]);
  assert.deepEqual(exact.reviewer_facts, [oldPass.fact, exactPass.fact]);
  assert.deepEqual(exact.current_receipts, [success.receipt]);
  assert.deepEqual(exact.current_reviewer_facts, [exactPass.fact]);

  const staleFacts = structuredClone({ ...throughSuccess, reviewer_facts: [oldPass, exactPass] });
  staleFacts.source.work_request.version += 1;
  staleFacts.source.work_request.canonical_record_digest = `sha256:${"f".repeat(64)}`;
  const stale = closureProjection(staleFacts, Error);
  assert.deepEqual(stale.work_request, plan.work_request, "a stale projection must remain bound to its registered plan");
  assert.equal(stale.slices[0].state, "claimed");
  assert.equal(stale.closure_state, "blocked");
  assert.equal(stale.closure.release.state, "unresolved");
  assert.equal(stale.stale_conflict.state, "stale");
  assert.match(stale.stale_conflict.reason, /no longer matches/);

  const noReceiptSuccessor = closureProjection({ ...base, receipts: [failed, success], reviewer_facts: [oldPass, exactPass] }, Error);
  assert.equal(noReceiptSuccessor.slices[0].state, "eligible", "an unsuperseded leaf without a receipt must fence an older reviewed pass");
  assert.equal(noReceiptSuccessor.closure_state, "blocked");
});

test("system census honors exact Passport completion before library filtering, counts and pagination", async () => {
  const plan = typedEngineeringPlan([engineeringSlice("slice:one", 1)]);
  const envelope = envelopeRow("11111111-1111-4111-8111-111111111111", "slice:one", "2026-08-26T00:00:01Z");
  const receipt = bindReceiptLineage(receiptRow("22222222-2222-4222-8222-222222222222", envelope.id, "slice:one", "claimed_complete", "2026-08-26T00:00:02Z"), plan, envelope);
  const review = reviewerRow("33333333-3333-4333-8333-333333333333", receipt.id, "slice:one", "passed", "2026-08-26T00:00:03Z");
  const facts = passportFacts(plan, { envelopes: [envelope], receipts: [receipt], reviewer_facts: [review] });
  assert.equal(closureProjection(facts, Error).closure_state, "complete");
  const row = { id: facts.slice_plans[0].id, title: "Synthetic complete engineering plan", state: "in_progress",
    opened_at: "2026-08-01T00:00:00Z", last_activity_at: "2026-08-26T00:00:00Z", version: "3",
    completed: false, cancelled: false, identity: { work_request: source.work.ref },
    plan_digest: plan.plan_digest, accepted_plan_id: source.plan.record_id, accepted_plan_hash: source.plan.digest };
  const client = { query: async (sql, params) => {
    if (sql.includes("as cache")) return { rows: [] };
    if (sql.includes("ops.engineering_passport_facts")) {
      assert.equal(params[0], source.work.ref); return { rows: [{ facts }] };
    }
    assert.ok(sql.includes(SYSTEM_WORK_LEGS.find(l => l.kind === "slice_plan").sql));
    assert.deepEqual(params, ["carr-internal"], "the raw slice-plan statement binds only its tenant parameter");
    const rows = sql.includes("completed=$3") && params[2] !== row.completed ? [] : [row];
    return { rows: sql.includes("count(*) as count") ? [{ count: rows.length }] : rows };
  } };
  const read = live_library => readSystemWorkCensus({ client, actor: { slug: "joe", human: true }, kinds: "slice_plan", live_library, limit: 1 });
  let unfinished = await read(false), live = await read(true);
  assert.equal(unfinished.items.length, 0);
  assert.equal(unfinished.coverage[0].count_total, 0);
  assert.equal(live.items[0]?.id, row.id);
  assert.equal(live.coverage[0].count_total, 1);
  assert.equal(live.items[0].state, "complete");
  assert.equal(live.next_cursor, null);
  // A legacy completion mark cannot hide a current unreviewed or stale generation.
  row.completed = true;
  facts.reviewer_facts = [];
  unfinished = await read(false); live = await read(true);
  assert.equal(unfinished.items[0]?.id, row.id); assert.equal(live.items.length, 0);
  facts.reviewer_facts = [review];
  facts.source.work_request = { ...facts.source.work_request, version: 4 };
  assert.equal((await read(false)).items[0]?.id, row.id);
  assert.equal((await read(true)).items.length, 0);
  facts.source.work_request = source.work;
  const successor = envelopeRow("44444444-4444-4444-8444-444444444444", "slice:one", "2026-08-27T00:00:00Z", envelope.id);
  facts.envelopes.push(successor);
  facts.receipts.push(bindReceiptLineage(receiptRow("55555555-5555-4555-8555-555555555555", successor.id, "slice:one", "failed", "2026-08-27T00:01:00Z"), plan, successor));
  assert.equal((await read(false)).items[0]?.id, row.id);
  assert.equal((await read(true)).items.length, 0);
  facts.slice_plans[0].id = "66666666-6666-4666-8666-666666666666";
  const mismatched = await read(true);
  assert.equal(mismatched.items.length, 0);
  assert.equal(mismatched.coverage[0].state, "unavailable", "a different registered plan cannot fall back to a legacy completion mark");
});

test("source merge authority comes from one reader-safe projection, never direct runtime table reads", async () => {
  const head = "a".repeat(40);
  const plan = typedEngineeringPlan([engineeringSlice("slice:one", 1)]);
  const envelope = envelopeRow("11111111-1111-4111-8111-111111111111", "slice:one", "2026-08-26T00:00:01Z");
  const receipt = receiptRow("22222222-2222-4222-8222-222222222222", envelope.id, "slice:one", "claimed_complete", "2026-08-26T00:00:02Z");
  receipt.receipt.source_evidence.source_sha = head;
  bindReceiptLineage(receipt, plan, envelope);
  const review = reviewerRow("33333333-3333-4333-8333-333333333333", receipt.id, "slice:one", "passed", "2026-08-26T00:00:03Z");
  const facts = passportFacts(plan, { envelopes: [envelope], receipts: [receipt], reviewer_facts: [review] });
  facts.source.accepted_plan.accepted_by_actor_id = actor.id;
  const calls = [];
  const c = { query: async (sql, params) => {
    calls.push({ sql, params });
    if (sql.includes("source_merge_authority_projection")) return { rows: [{ authority: {
      ok: true,
      passport_facts: facts,
      authority: {
      schema_version: "source-merge-authority.v1",
      derived_by: "source-merge-authority-projection",
      decision: {
        decision_ref: "decision:4eaae0e1-f3b0-4e5d-af93-c44f39adc687",
        event_ref: "event:44444444-4444-4444-8444-444444444444",
        title: "Routine authorized green PRs merge without asking Joe for ceremonial approval",
        sponsoring_human_slug: "joe",
      },
      source_merge_only: true,
      allowed_actions: ["repository:merge-pr"],
      scope_ref: "source-merge-scope:99999999-9999-4999-8999-999999999999",
      scope_digest: `sha256:${"6".repeat(64)}`,
      authorized_path_claims: [{ lease_ref: "canonical-ownership-lease:99999999-9999-4999-8999-999999999999", path: "mcp-server/src/source-merge-policy.js", mode: "file", operation: "write", claim_path: "mcp-server/src/source-merge-policy.js", claim_mode: "file", claim_operation: "write" }],
      assurance_bindings: [{
        slice_ref: "slice:one", attempt_id: receipt.attempt_id,
        evidence_manifest_ref: "assurance-manifest:55555555-5555-4555-8555-555555555555",
        review_manifest_ref: "assurance-manifest:66666666-6666-4666-8666-666666666666",
        evidence_ref: "assurance-evidence:77777777-7777-4777-8777-777777777777",
        reviewer_fact_ref: "engineering-review:33333333-3333-4333-8333-333333333333",
        review_extension_ref: "assurance-review:88888888-8888-4888-8888-888888888888",
        reviewer_state: "passed", evidence_digest: `sha256:${"4".repeat(64)}`,
        review_digest: `sha256:${"5".repeat(64)}`, repository_commit_sha: head,
        repository_tree_sha: "b".repeat(40), snapshot_valid_until: new Date(Date.now() + 60_000).toISOString(),
      }],
      exact_head_sha: head, pr_number: 42,
      currentness_evaluated_at: new Date().toISOString(),
    } } }] };
    return { rows: [] };
  } };
  const authority = await resolveSourceMergeAuthority(c, {
    decision_id: "4eaae0e1-f3b0-4e5d-af93-c44f39adc687",
    work_request: source.work.ref, pr_number: 42, head_sha: head,
  }, EngineeringToolError);
  assert.equal(authority.derived_by, "source-merge-authority-projection");
  assert.equal(authority.decision.sponsoring_human_slug, "joe");
  assert.equal(authority.passport.closure_state, "complete");
  assert.equal(authority.authorized_path_claims[0].path, "mcp-server/src/source-merge-policy.js");
  assert.match(authority.scope_ref, /^source-merge-scope:[0-9a-f-]{36}$/);
  assert.equal(calls.length, 1);
  assert.match(calls[0].sql, /source_merge_authority_projection/);
  assert.doesNotMatch(calls[0].sql, /canonical_ownership_claim|assurance_evidence_extension|from event/i);

  facts.source.execution_authorized = false;
  facts.source.execution_refusal_reason = "stale_wr122_contract";
  await assert.rejects(() => resolveSourceMergeAuthority(c, {
    decision_id: "4eaae0e1-f3b0-4e5d-af93-c44f39adc687",
    work_request: source.work.ref, pr_number: 42, head_sha: head,
  }, EngineeringToolError), error => error.error === "engineering_execution_contract_retired" &&
    error.reason === "stale_wr122_contract");
});

test("dependency preflight and closure fail closed on malformed latest receipt or reviewer lineage", async () => {
  const plan = typedEngineeringPlan([engineeringSlice("slice:one", 1), engineeringSlice("slice:two", 2, ["slice:one"])]);
  const envelope = envelopeRow("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "slice:one", "2026-08-26T00:00:01Z");
  const receipt = bindReceiptLineage(receiptRow("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", envelope.id, "slice:one", "claimed_complete", "2026-08-26T00:00:02Z"), plan, envelope);
  const review = reviewerRow("cccccccc-cccc-4ccc-8ccc-cccccccccccc", receipt.id, "slice:one");
  const cases = [
    ["receipt digest mismatch", facts => { facts.receipts[0].receipt_digest = `sha256:${"f".repeat(64)}`; }],
    ["receipt schema mismatch", facts => { facts.receipts[0].receipt.schema_version = "legacy.receipt"; }],
    ["receipt extra top-level field", facts => { facts.receipts[0].receipt.unexpected = true; }],
    ["empty artifacts", facts => { facts.receipts[0].receipt.artifact_refs = []; }],
    ["malformed evidence", facts => { facts.receipts[0].receipt.evidence_refs = [{}]; }],
    ["malformed checks", facts => { facts.receipts[0].receipt.checks = [{}]; }],
    ["bogus planned check ref", facts => { facts.receipts[0].receipt.checks[0].check_ref = "check:bogus"; }],
    ["missing planned check ref", facts => { delete facts.receipts[0].receipt.checks[0].check_ref; }],
    ["wrong evidence redaction class", facts => { facts.receipts[0].receipt.checks[0].evidence_refs[0].redaction_class = "redacted_evidence"; }],
    ["attribution actor mismatch", facts => { facts.receipts[0].receipt.attribution.actor_ref = "agent:wrong"; }],
    ["attribution adapter mismatch", facts => { facts.receipts[0].receipt.attribution.adapter_ref = "adapter:wrong"; }],
    ["attribution session mismatch", facts => { facts.receipts[0].receipt.attribution.session_ref = "session:wrong"; }],
    ["deviations object instead of array", facts => { facts.receipts[0].receipt.deviations = {}; }],
    ["malformed deviation", facts => { facts.receipts[0].receipt.deviations = [{ deviation_ref: "deviation:resolved", review_state: "unreviewed", plan_revision_required: true }]; }],
    ["duplicate receipt deviation", facts => { facts.receipts[0].receipt.deviations.push({ ...facts.receipts[0].receipt.deviations[0] }); }],
    ["inactive reviewer", facts => { facts.reviewer_facts[0].reviewer_actor_active = false; }],
    ["reviewer actor slug mismatch", facts => { facts.reviewer_facts[0].reviewer_actor_slug = "other"; }],
    ["reviewer ref mismatch", facts => { facts.reviewer_facts[0].fact.reviewer_ref = "reviewer:other"; }],
    ["malformed reviewer session", facts => { facts.reviewer_facts[0].reviewer_session_ref = ""; facts.reviewer_facts[0].fact.session_ref = ""; }],
    ["duplicate reviewed deviation refs", facts => { facts.reviewer_facts[0].fact.reviewed_deviation_refs.push("deviation:resolved"); }],
    ["duplicate resolved deviation refs", facts => { facts.reviewer_facts[0].fact.resolved_deviation_refs.push("deviation:resolved"); }],
  ];
  const isolatedCases = cases.map(([label, mutate]) => [label, label === "receipt digest mismatch" ? mutate : facts => {
    mutate(facts);
    facts.receipts[0].receipt_digest = digest(facts.receipts[0].receipt);
  }]);
  for (const [label, mutate] of isolatedCases) {
    const facts = passportFacts(plan, {
      envelopes: [structuredClone(envelope)], receipts: [structuredClone(receipt)], reviewer_facts: [structuredClone(review)],
    });
    mutate(facts);
    const calls = [];
    const c = { query: async sql => { calls.push(sql); if (sql.includes("engineering_passport_facts")) return { rows: [{ facts }] }; return { rows: [] }; } };
    await assert.rejects(
      () => admitEngineeringSlice(c, actor, { idempotency_key: "dddddddd-dddd-4ddd-8ddd-dddddddddddd", work_request: source.work.ref, slice_ref: "slice:two" }, EngineeringToolError, async () => {}),
      error => error.error === "engineering_dependencies_not_verified",
      label,
    );
    assert.equal(calls.some(sql => /pg_advisory_xact_lock|engineering_enqueue_slice_job|insert into ops\./i.test(sql)), false, `${label} opened a write path`);
    const projection = closureProjection(facts, EngineeringToolError);
    assert.notEqual(projection.slices.find(row => row.slice_ref === "slice:one")?.state, "verified_complete", label);
    assert.equal(projection.closure_state, "blocked", label);
  }
});

test("malformed newer relational receipt cannot fall back to an older verified generation", async () => {
  const plan = typedEngineeringPlan([engineeringSlice("slice:one", 1), engineeringSlice("slice:two", 2, ["slice:one"])]);
  const oldEnvelope = envelopeRow("11111111-1111-4111-8111-111111111111", "slice:one", "2026-08-26T00:00:01Z");
  const newEnvelope = envelopeRow("22222222-2222-4222-8222-222222222222", "slice:one", "2026-08-26T00:00:03Z", oldEnvelope.id);
  const oldReceipt = bindReceiptLineage(receiptRow("33333333-3333-4333-8333-333333333333", oldEnvelope.id, "slice:one", "claimed_complete", "2026-08-26T00:00:02Z"), plan, oldEnvelope);
  const malformedNewReceipt = bindReceiptLineage(receiptRow("44444444-4444-4444-8444-444444444444", newEnvelope.id, "slice:one", "claimed_complete", "2026-08-26T00:00:04Z"), plan, newEnvelope);
  malformedNewReceipt.receipt.attribution.session_ref = "session:wrong";
  malformedNewReceipt.receipt_digest = digest(malformedNewReceipt.receipt);
  const oldReview = reviewerRow("55555555-5555-4555-8555-555555555555", oldReceipt.id, "slice:one");
  const facts = passportFacts(plan, { envelopes: [oldEnvelope, newEnvelope], receipts: [oldReceipt, malformedNewReceipt], reviewer_facts: [oldReview] });
  const calls = [];
  const c = { query: async sql => { calls.push(sql); if (sql.includes("engineering_passport_facts")) return { rows: [{ facts }] }; return { rows: [] }; } };
  await assert.rejects(
    () => admitEngineeringSlice(c, actor, { idempotency_key: "66666666-6666-4666-8666-666666666666", work_request: source.work.ref, slice_ref: "slice:two" }, EngineeringToolError, async () => {}),
    error => error.error === "engineering_dependencies_not_verified",
  );
  assert.equal(calls.some(sql => /pg_advisory_xact_lock|engineering_enqueue_slice_job|insert into ops\./i.test(sql)), false);
  const projection = closureProjection(facts, EngineeringToolError);
  assert.notEqual(projection.slices.find(row => row.slice_ref === "slice:one")?.state, "verified_complete");
  assert.equal(projection.closure_state, "blocked");
  assert.deepEqual(projection.reviewer_facts, [oldReview.fact]);
});

test("admission fails closed when a DAG dependency lacks a passed independent review", async () => {
  const first = {
    slice_ref: "slice:one", ordinal: 1, objective: "First", definition_of_done: "A receipt exists",
    dependency_refs: [], declared_resource_refs: [], declared_component_refs: [], declared_plan_step_refs: [],
    baseline_evidence_refs: [], planned_checks: [{ check_ref: "check:one", failure_condition: "missing", evidence_requirement: "metadata_only_sufficient" }],
    scope_boundary: "one slice", forbidden_change_refs: [], concurrency_posture: "parallel_safe", manual_qa_required: false,
    risk_class: "R1", release_requirement: "required",
  };
  const second = { ...first, slice_ref: "slice:two", ordinal: 2, objective: "Second", dependency_refs: ["slice:one"], planned_checks: [{ ...first.planned_checks[0], check_ref: "check:two" }] };
  const typedPlan = { schema_version: "engineering-slice-plan.v1", work_request: { id: source.work.id, state_version: 3, canonical_record_digest: source.work.canonical_record_digest }, accepted_plan_revision: { id: source.plan.plan_ref, revision: 2, digest: source.plan.digest }, slices: [first, second] };
  typedPlan.plan_digest = digest(typedPlan);
  const c = { query: async () => ({ rows: [{ facts: { source: { work_request: source.work, accepted_plan: source.plan }, slice_plans: [{ accepted_plan_id: source.plan.record_id, accepted_plan_hash: source.plan.digest, plan: typedPlan }], envelopes: [], receipts: [], reviewer_facts: [] } }] }) };
  await assert.rejects(() => admitEngineeringSlice(c, actor, { idempotency_key: "99999999-9999-4999-8999-999999999999", work_request: source.work.ref, slice_ref: "slice:two" }, Error));
});

test("successful admission persists its event through the injected writer", async () => {
  const item = {
    slice_ref: "slice:one", ordinal: 1, objective: "Do the bounded work", definition_of_done: "A typed receipt exists",
    dependency_refs: [], declared_resource_refs: [], declared_component_refs: [], declared_plan_step_refs: [],
    baseline_evidence_refs: [], planned_checks: [{ check_ref: "check:one", failure_condition: "missing", evidence_requirement: "metadata_only_sufficient" }],
    scope_boundary: "one bounded slice", forbidden_change_refs: [], concurrency_posture: "parallel_safe", manual_qa_required: false,
    risk_class: "R1", release_requirement: "required",
  };
  const typedPlan = { schema_version: "engineering-slice-plan.v1", work_request: { id: source.work.id, state_version: 3, canonical_record_digest: source.work.canonical_record_digest }, accepted_plan_revision: { id: source.plan.plan_ref, revision: 2, digest: source.plan.digest }, slices: [item] };
  typedPlan.plan_digest = digest(typedPlan);
  const facts = { source: { work_request: source.work, accepted_plan: source.plan }, slice_plans: [{ accepted_plan_id: source.plan.record_id, accepted_plan_hash: source.plan.digest, plan: typedPlan }], envelopes: [], receipts: [], reviewer_facts: [] };
  const sessionId = "44444444-4444-4444-8444-444444444444";
  const jobId = "55555555-5555-4555-8555-555555555555";
  const envelopeId = "66666666-6666-4666-8666-666666666666";
  const calls = [];
  const c = { query: async (sql) => {
    calls.push(sql);
    if (sql.includes("engineering_passport_facts")) return { rows: [{ facts }] };
    if (sql.includes("capability_agent_session")) return { rows: [{ id: sessionId, executor_actor_id: actor.id, state: "claimed", lease_expires_at: new Date(Date.now() + 30 * 60 * 1000).toISOString(), scope_ref: "slice:slice:one", worktree_ref: "engineering:server-admission", source_commit_sha: "0".repeat(40) }] };
    if (sql.includes("from actor")) return { rows: [{ id: actor.id, slug: actor.slug }] };
    if (sql.trimStart().startsWith("select id from ops.engineering_slice_plan")) return { rows: [{ id: "12121212-1212-4212-8212-121212121212" }] };
    if (sql.includes("select id from ops.work_request")) return { rows: [{ id: source.work.id.replace(/^wr:/, "") }] };
    if (sql.includes("engineering_enqueue_slice_job")) return { rows: [{ id: jobId }] };
    if (sql.includes("insert into ops.engineering_execution_envelope")) return { rows: [{ id: envelopeId }] };
    return { rows: [] };
  } };
  const events = [];
  const result = await admitEngineeringSlice(c, actor, {
    idempotency_key: "77777777-7777-4777-8777-777777777777",
    work_request: source.work.ref,
    slice_ref: item.slice_ref,
  }, Error, async (...event) => events.push(event));
  assert.equal(result.envelope_id, envelopeId);
  assert.equal(events.length, 1);
  assert.equal(events[0][2], "admit-engineering-slice");
  const planRead = calls.find(sql => sql.trimStart().startsWith("select id from ops.engineering_slice_plan"));
  assert.ok(planRead, "admission must verify the registered immutable slice plan");
  assert.doesNotMatch(planRead, /for\s+(?:key\s+)?share|for\s+update/i,
    "carr_writer has SELECT-only plan authority, so admission must not request a row lock");
});

test("admission refuses source drift after serialization before any write", async () => {
  const plan = typedEngineeringPlan([engineeringSlice("slice:one", 1)]);
  const currentFacts = passportFacts(plan);
  const staleFacts = structuredClone(currentFacts);
  staleFacts.source.work_request.version += 1;
  staleFacts.source.work_request.canonical_record_digest = `sha256:${"f".repeat(64)}`;
  const calls = [];
  let passportReads = 0;
  const c = { query: async (sql, params = []) => {
    calls.push({ sql, params });
    if (sql.includes("engineering_passport_facts"))
      return { rows: [{ facts: passportReads++ === 0 ? currentFacts : staleFacts }] };
    if (sql.includes("capability_agent_session")) return { rows: [] };
    if (sql.includes("from actor")) return { rows: [{ id: actor.id, slug: actor.slug }] };
    if (sql.includes("pg_advisory_xact_lock")) return { rows: [] };
    return { rows: [] };
  } };
  await assert.rejects(
    () => admitEngineeringSlice(c, actor, {
      idempotency_key: "89898989-8989-4989-8989-898989898989",
      work_request: source.work.ref,
      slice_ref: "slice:one",
    }, EngineeringToolError, async () => {}),
    error => error.error === "engineering_admission_serialization_restart",
  );
  assert.equal(calls.some(call => /engineering_enqueue_slice_job|insert into ops\.|update ops\./i.test(call.sql)), false);
  assert.ok(calls.findIndex(call => call.sql.includes("from actor")) <
            calls.findIndex(call => call.sql.includes("pg_advisory_xact_lock")));
});

function priorSessionAdmissionFixture({
  sessionState = "completed",
  sessionScope = "slice:slice:one",
  sessionExecutorId = actor.id,
  actorRow = { id: actor.id, slug: actor.slug },
  bindingSessionId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  currentness = { eligible: false, dispatch_runway_sufficient: false },
  jobState = "completed",
} = {}) {
  const typedPlan = typedEngineeringPlan([engineeringSlice("slice:one", 1)]);
  const priorId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  const priorSession = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
  const priorJob = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
  const nextSession = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
  const nextJob = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee";
  const nextEnvelope = "ffffffff-ffff-4fff-8fff-ffffffffffff";
  const prior = buildCodexEnvelope({ source, plan: typedPlan, slice: typedPlan.slices[0],
    jobId: priorJob, sessionId: priorSession, actor });
  const facts = passportFacts(typedPlan, { envelopes: [{
    id: priorId, job_id: priorJob, agent_session_id: priorSession,
    accepted_plan_id: source.plan.record_id,
    slice_plan_id: "12121212-1212-4212-8212-121212121212",
    slice_ref: "slice:one", created_at: prior.issued_at, envelope: prior,
  }] });
  const counts = { cancellation: 0, session: 0, job: 0, envelope: 0, currentness: 0 };
  const c = { query: async (sql) => {
    if (sql.includes("engineering_passport_facts")) return { rows: [{ facts }] };
    if (sql.includes("from ops.engineering_execution_envelope e")) return { rows: [{
      id: priorId, job_id: priorJob, work_request_id: source.work.id.replace(/^wr:/, ""),
      accepted_plan_id: source.plan.record_id,
      slice_plan_id: "12121212-1212-4212-8212-121212121212", slice_ref: "slice:one",
      agent_session_id: bindingSessionId, envelope: prior, job_state: jobState,
    }] };
    if (sql.includes("where id=$1::uuid for update")) return { rows: [{
      id: priorSession, work_request_id: source.work.id.replace(/^wr:/, ""),
      executor_actor_id: sessionExecutorId, state: sessionState,
      lease_expires_at: prior.agent_session.lease_expires_at, scope_ref: sessionScope,
      worktree_ref: "engineering:server-admission", source_commit_sha: "0".repeat(40),
    }] };
    if (sql.includes("from actor")) return { rows: actorRow ? [actorRow] : [] };
    if (sql.includes("engineering_envelope_currentness")) {
      counts.currentness += 1;
      return { rows: [{ currentness }] };
    }
    if (sql.includes("update ops.capability_agent_session")) {
      counts.cancellation += 1;
      return { rows: [] };
    }
    if (sql.includes("insert into ops.capability_agent_session")) {
      counts.session += 1;
      return { rows: [{ id: nextSession, executor_actor_id: actor.id, state: "claimed",
        lease_expires_at: new Date(Date.now() + 30 * 60 * 1000).toISOString() }] };
    }
    if (sql.trimStart().startsWith("select id from ops.engineering_slice_plan"))
      return { rows: [{ id: "12121212-1212-4212-8212-121212121212" }] };
    if (sql.includes("select id from ops.work_request"))
      return { rows: [{ id: source.work.id.replace(/^wr:/, "") }] };
    if (sql.includes("engineering_enqueue_slice_job")) {
      counts.job += 1;
      return { rows: [{ id: nextJob }] };
    }
    if (sql.includes("insert into ops.engineering_execution_envelope")) {
      counts.envelope += 1;
      return { rows: [{ id: nextEnvelope }] };
    }
    return { rows: [] };
  } };
  return { c, counts, priorId, priorJob, priorSession, nextSession, nextJob, nextEnvelope };
}

test("admission creates one successor after a correctly bound completed or cancelled session without mutating the predecessor", async t => {
  for (const sessionState of ["completed", "cancelled"]) await t.test(sessionState, async () => {
    const fixture = priorSessionAdmissionFixture({ sessionState });
    const result = await admitEngineeringSlice(fixture.c, actor, {
      idempotency_key: "12121212-1212-4212-8212-121212121212",
      work_request: source.work.ref,
      slice_ref: "slice:one",
    }, EngineeringToolError, async () => {});
    assert.equal(result.replayed, false);
    assert.equal(result.supersedes_envelope_id, fixture.priorId);
    assert.equal(result.agent_session_id, fixture.nextSession);
    assert.equal(result.job_id, fixture.nextJob);
    assert.equal(result.envelope_id, fixture.nextEnvelope);
    assert.deepEqual(fixture.counts, { cancellation: 0, session: 1, job: 1, envelope: 1, currentness: 0 });
  });
});

test("admission replays an active current predecessor only with sufficient dispatch runway and creates no duplicate records", async () => {
  const fixture = priorSessionAdmissionFixture({
    sessionState: "in_progress", jobState: "running",
    currentness: { eligible: true, dispatch_runway_sufficient: true },
  });
  const result = await admitEngineeringSlice(fixture.c, actor, {
    idempotency_key: "23232323-2323-4232-8232-232323232323",
    work_request: source.work.ref,
    slice_ref: "slice:one",
  }, EngineeringToolError, async () => {});
  assert.equal(result.replayed, true);
  assert.equal(result.envelope_id, fixture.priorId);
  assert.deepEqual(fixture.counts, { cancellation: 0, session: 0, job: 0, envelope: 0, currentness: 1 });
});

test("admission refuses active replay with insufficient runway before successor writes", async () => {
  const fixture = priorSessionAdmissionFixture({
    sessionState: "claimed", jobState: "queued",
    currentness: { eligible: true, dispatch_runway_sufficient: false },
  });
  await assert.rejects(
    () => admitEngineeringSlice(fixture.c, actor, {
      idempotency_key: "24242424-2424-4242-8242-242424242424",
      work_request: source.work.ref,
      slice_ref: "slice:one",
    }, EngineeringToolError, async () => {}),
    error => error.error === "engineering_envelope_insufficient_runway",
  );
  assert.deepEqual(fixture.counts, { cancellation: 0, session: 0, job: 0, envelope: 0, currentness: 1 });
});

test("admission refuses mismatched predecessor session, scope, or executor before successor writes", async t => {
  const cases = [
    ["session", { bindingSessionId: "abababab-abab-4aba-8aba-abababababab" }, "engineering_session_conflict"],
    ["scope", { sessionScope: "slice:other" }, "engineering_session_conflict"],
    ["executor", {
      sessionExecutorId: "56565656-5656-4565-8565-565656565656",
      actorRow: { id: actor.id, slug: actor.slug },
    }, "engineering_codex_actor_not_provisioned"],
  ];
  for (const [name, overrides, expected] of cases) await t.test(name, async () => {
    const fixture = priorSessionAdmissionFixture(overrides);
    await assert.rejects(
      () => admitEngineeringSlice(fixture.c, actor, {
        idempotency_key: "34343434-3434-4343-8343-343434343434",
        work_request: source.work.ref,
        slice_ref: "slice:one",
      }, EngineeringToolError, async () => {}),
      error => error.error === expected,
    );
    assert.equal(fixture.counts.cancellation, 0);
    assert.equal(fixture.counts.session, 0);
    assert.equal(fixture.counts.job, 0);
    assert.equal(fixture.counts.envelope, 0);
  });
});

test("admission replaces a stale read-only envelope whose prior job is terminal with a new immutable generation", async () => {
  const item = {
    slice_ref: "slice:one", ordinal: 1, objective: "Do the bounded work", definition_of_done: "A typed receipt exists",
    dependency_refs: [], declared_resource_refs: [], declared_component_refs: [], declared_plan_step_refs: [],
    baseline_evidence_refs: [], planned_checks: [{ check_ref: "check:one", failure_condition: "missing", evidence_requirement: "metadata_only_sufficient" }],
    scope_boundary: "one bounded slice", forbidden_change_refs: [], concurrency_posture: "parallel_safe", manual_qa_required: false,
    risk_class: "R1", release_requirement: "required",
  };
  const typedPlan = { schema_version: "engineering-slice-plan.v1", work_request: { id: source.work.id, state_version: 3, canonical_record_digest: source.work.canonical_record_digest }, accepted_plan_revision: { id: source.plan.plan_ref, revision: 2, digest: source.plan.digest }, slices: [item] };
  typedPlan.plan_digest = digest(typedPlan);
  const priorId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  const priorSession = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
  const legacy = buildCodexEnvelope({ source, plan: typedPlan, slice: item,
    jobId: "cccccccc-cccc-4ccc-8ccc-cccccccccccc", sessionId: priorSession, actor });
  legacy.request.allowed_actions = [];
  legacy.server_binding.authority.capability_profile = "capability:engineering-read-only";
  legacy.server_binding.authority.capability_grant_ref = "grant:engineering-read-only-v1";
  legacy.server_binding.authority.read_only = true;
  legacy.expires_at = "2026-08-24T00:00:00Z";
  legacy.agent_session.lease_expires_at = legacy.expires_at;
  const facts = {
    source: { work_request: source.work, accepted_plan: source.plan },
    slice_plans: [{ accepted_plan_id: source.plan.record_id, accepted_plan_hash: source.plan.digest, plan: typedPlan }],
    envelopes: [{ id: priorId, job_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      accepted_plan_id: source.plan.record_id, slice_plan_id: "12121212-1212-4212-8212-121212121212", slice_ref: item.slice_ref,
      created_at: "2026-08-24T00:00:00Z", envelope: legacy }],
    receipts: [], reviewer_facts: [],
  };
  const newSession = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
  const newJob = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee";
  const newEnvelopeId = "ffffffff-ffff-4fff-8fff-ffffffffffff";
  let insertParams;
  let enqueueParams;
  const lockOrder = [];
  const c = { query: async (sql, params = []) => {
    if (sql.includes("engineering_passport_facts")) return { rows: [{ facts }] };
    if (sql.includes("from ops.engineering_execution_envelope e")) {
      lockOrder.push("prior-binding");
      return { rows: [{ id: priorId, job_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc", work_request_id: source.work.id.replace(/^wr:/, ""), accepted_plan_id: source.plan.record_id, slice_plan_id: "12121212-1212-4212-8212-121212121212", slice_ref: item.slice_ref, agent_session_id: priorSession, envelope: legacy, job_state: "dead_lettered" }] };
    }
    if (sql.includes("pg_advisory_xact_lock")) {
      lockOrder.push(String(params[0]).startsWith("engineering-slice:") ? "admission-advisory" : "lineage-advisory");
      return { rows: [] };
    }
    if (sql.includes("where id=$1::uuid for update")) {
      lockOrder.push("session-lock");
      return { rows: [{ id: priorSession, work_request_id: source.work.id.replace(/^wr:/, ""), executor_actor_id: actor.id,
        state: "claimed", scope_ref: `slice:${item.slice_ref}`, worktree_ref: "engineering:server-admission",
        source_commit_sha: "0".repeat(40), executor_slug: "codex" }] };
    }
    if (sql.includes("engineering_envelope_currentness")) { lockOrder.push("currentness"); return { rows: [{ currentness: { eligible: false, dispatch_runway_sufficient: false } }] }; }
    if (sql.includes("update ops.capability_agent_session")) { lockOrder.push("session-cancellation"); return { rows: [] }; }
    if (sql.includes("insert into ops.capability_agent_session"))
      return { rows: [{ id: newSession, executor_actor_id: actor.id, state: "active" }] };
    if (sql.includes("select id, executor_actor_id")) return { rows: [] };
    if (sql.includes("from actor")) { lockOrder.push("actor-lock"); return { rows: [{ id: actor.id, slug: actor.slug }] }; }
    if (sql.trimStart().startsWith("select id from ops.engineering_slice_plan")) return { rows: [{ id: "12121212-1212-4212-8212-121212121212" }] };
    if (sql.includes("select id from ops.work_request")) return { rows: [{ id: source.work.id.replace(/^wr:/, "") }] };
    if (sql.includes("engineering_enqueue_slice_job")) { enqueueParams = params; return { rows: [{ id: newJob }] }; }
    if (sql.includes("insert into ops.engineering_execution_envelope")) {
      insertParams = params;
      return { rows: [{ id: newEnvelopeId }] };
    }
    return { rows: [] };
  } };
  const result = await admitEngineeringSlice(c, actor, {
    idempotency_key: "12121212-1212-4212-8212-121212121212",
    work_request: source.work.ref,
    slice_ref: item.slice_ref,
  }, Error, async () => {});
  assert.equal(result.replayed, false);
  assert.equal(result.supersedes_envelope_id, priorId);
  assert.equal(enqueueParams[4], 2);
  assert.equal(insertParams[12], priorId);
  const sessionIndex = lockOrder.indexOf("session-lock");
  const actorIndex = lockOrder.indexOf("actor-lock");
  const admissionIndex = lockOrder.indexOf("admission-advisory");
  const lineageIndex = lockOrder.indexOf("lineage-advisory");
  assert.ok(sessionIndex > lockOrder.indexOf("prior-binding"), lockOrder.join(","));
  assert.ok(actorIndex > sessionIndex, lockOrder.join(","));
  assert.ok(admissionIndex > actorIndex, lockOrder.join(","));
  assert.ok(lineageIndex > admissionIndex, lockOrder.join(","));
  assert.ok(lineageIndex < lockOrder.indexOf("currentness"), lockOrder.join(","));
  assert.ok(lineageIndex < lockOrder.indexOf("session-cancellation"), lockOrder.join(","));
  const replacement = JSON.parse(insertParams[9]);
  assert.equal(replacement.handoff.mode, "replacement");
  assert.equal(replacement.handoff.replaces_agent_session_id, `session:${priorSession}`);
  assert.equal(replacement.handoff.capability_inherited, false);
  assert.equal(replacement.server_binding.authority.read_only, false);
});

test("admission rejects a stale read-only envelope bound to a verification session before any replacement writes", async () => {
  const item = {
    slice_ref: "slice:one", ordinal: 1, objective: "Do the bounded work", definition_of_done: "A typed receipt exists",
    dependency_refs: [], declared_resource_refs: [], declared_component_refs: [], declared_plan_step_refs: [],
    baseline_evidence_refs: [], planned_checks: [{ check_ref: "check:one", failure_condition: "missing", evidence_requirement: "metadata_only_sufficient" }],
    scope_boundary: "one bounded slice", forbidden_change_refs: [], concurrency_posture: "parallel_safe", manual_qa_required: false,
    risk_class: "R1", release_requirement: "required",
  };
  const typedPlan = { schema_version: "engineering-slice-plan.v1", work_request: { id: source.work.id, state_version: 3, canonical_record_digest: source.work.canonical_record_digest }, accepted_plan_revision: { id: source.plan.plan_ref, revision: 2, digest: source.plan.digest }, slices: [item] };
  typedPlan.plan_digest = digest(typedPlan);
  const priorId = "abababab-abab-4aba-8aba-abababababab";
  const priorSession = "bcbcbcbc-bcbc-4bcb-8bcb-bcbcbcbcbcbc";
  const priorJob = "cdcdcdcd-cdcd-4cdc-8cdc-cdcdcdcdcdcd";
  const legacy = buildCodexEnvelope({ source, plan: typedPlan, slice: item, jobId: priorJob, sessionId: priorSession, actor });
  legacy.server_binding.authority.read_only = true;
  legacy.server_binding.authority.capability_profile = "capability:engineering-read-only";
  legacy.expires_at = "2026-08-24T00:00:00Z";
  legacy.agent_session.lease_expires_at = legacy.expires_at;
  const facts = {
    source: { work_request: source.work, accepted_plan: source.plan },
    slice_plans: [{ accepted_plan_id: source.plan.record_id, accepted_plan_hash: source.plan.digest, plan: typedPlan }],
    envelopes: [{ id: priorId, job_id: priorJob, accepted_plan_id: source.plan.record_id, slice_plan_id: "12121212-1212-4212-8212-121212121212", slice_ref: item.slice_ref, created_at: "2026-08-24T00:00:00Z", envelope: legacy }],
    receipts: [], reviewer_facts: [],
  };
  const calls = [];
  let provenanceParams;
  const c = { query: async (sql, params = []) => {
    calls.push(sql);
    if (sql.includes("engineering_passport_facts")) return { rows: [{ facts }] };
    if (sql.includes("from ops.engineering_execution_envelope e"))
      return { rows: [{ id: priorId, job_id: priorJob, work_request_id: source.work.id.replace(/^wr:/, ""), accepted_plan_id: source.plan.record_id, slice_plan_id: "12121212-1212-4212-8212-121212121212", slice_ref: item.slice_ref, agent_session_id: priorSession, envelope: legacy, job_state: "dead_lettered" }] };
    if (sql.includes("where id=$1::uuid for update")) {
      provenanceParams = params;
      return { rows: [{ id: priorSession, work_request_id: source.work.id.replace(/^wr:/, ""), executor_actor_id: actor.id,
        state: "claimed", scope_ref: "verification:read-only-review", worktree_ref: "engineering:server-admission",
        source_commit_sha: "f".repeat(40), executor_slug: "codex" }] };
    }
    return { rows: [] };
  } };
  class ToolError extends Error {
    constructor(payload) { super(payload.error); Object.assign(this, payload); }
  }
  let typedError;
  await assert.rejects(
    () => admitEngineeringSlice(c, actor, {
      idempotency_key: "dededede-dede-4ded-8ded-dededededede", work_request: source.work.ref, slice_ref: item.slice_ref,
    }, ToolError, async () => {}),
    error => { typedError = error; return error.error === "engineering_session_conflict" && error.envelope_id === priorId; },
  );
  assert.equal(typedError.error, "engineering_session_conflict");
  assert.deepEqual(provenanceParams, [priorSession]);
  assert.ok(calls.some(sql => sql.includes("where id=$1::uuid for update")));
  assert.equal(calls.filter(sql => sql.includes("engineering_envelope_currentness")).length, 0);
  assert.equal(calls.filter(sql => sql.includes("update ops.capability_agent_session")).length, 0);
  assert.equal(calls.filter(sql => sql.includes("insert into ops.capability_agent_session")).length, 0);
  assert.equal(calls.filter(sql => sql.includes("engineering_enqueue_slice_job")).length, 0);
  assert.equal(calls.filter(sql => sql.includes("insert into ops.engineering_execution_envelope")).length, 0);
});

test("successful independent review persists its event through the injected writer", async () => {
  const receiptId = "88888888-8888-4888-8888-888888888888";
  const reviewer = { id: "99999999-9999-4999-8999-999999999999", slug: "reviewer" };
  const receiptRow = {
    id: receiptId,
    work_request_id: "11111111-1111-4111-8111-111111111111",
    slice_ref: "slice:one",
    attempt_id: "attempt:1",
    executor_actor_id: actor.id,
    outcome: "claimed_complete",
    receipt: { outcome: "claimed_complete", deviations: [] },
  };
  const c = { query: async sql => {
    if (sql.includes("from ops.engineering_slice_receipt")) return { rows: [receiptRow] };
    if (sql.includes("insert into ops.engineering_reviewer_fact")) return { rows: [{ id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", state: "passed" }] };
    return { rows: [] };
  } };
  const events = [];
  const result = await recordEngineeringReview(c, reviewer, {
    idempotency_key: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    receipt_id: receiptId,
    fact: {
      slice_ref: "slice:one", attempt_id: "attempt:1", reviewer_ref: "reviewer:reviewer", session_ref: "session:reviewer",
      state: "passed", evidence_refs: [{ ref: "evidence:review", redaction_class: "metadata_only", content_digest: `sha256:${"c".repeat(64)}` }],
      is_independent: true, reviewed_deviation_refs: [], resolved_deviation_refs: [],
    },
  }, EngineeringToolError, async (...event) => events.push(event));
  assert.equal(result.state, "passed");
  assert.equal(events.length, 1);
  assert.equal(events[0][2], "review-engineering-slice");
});

test("review admission refuses lineage, independence, evidence, deviation, and noncomplete violations before writing", async () => {
  const receiptId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  const reviewer = { id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", slug: "reviewer" };
  const completeReceipt = {
    id: receiptId, work_request_id: source.work.id.replace(/^wr:/, ""), slice_ref: "slice:one", attempt_id: "attempt:1", executor_actor_id: actor.id, outcome: "claimed_complete",
    receipt: { outcome: "claimed_complete", slice_ref: "slice:one", attempt_id: "attempt:1", deviations: [], attribution: { session_ref: "session:executor" } },
  };
  const validFact = {
    attempt_id: "attempt:1", evidence_refs: [{ ref: "evidence:review", redaction_class: "metadata_only", content_digest: `sha256:${"c".repeat(64)}` }],
    is_independent: true, resolved_deviation_refs: [], reviewed_deviation_refs: [], reviewer_ref: "reviewer:reviewer", session_ref: "session:reviewer", slice_ref: "slice:one", state: "passed",
  };
  const cases = [
    ["slice mismatch", { fact: { ...validFact, slice_ref: "slice:wrong" } }],
    ["attempt mismatch", { fact: { ...validFact, attempt_id: "attempt:2" } }],
    ["self review", { actor, fact: validFact }],
    ["reviewer session equals executor", { fact: { ...validFact, session_ref: "session:executor" } }],
    ["empty evidence", { fact: { ...validFact, evidence_refs: [] } }],
    ["malformed evidence", { fact: { ...validFact, evidence_refs: [{ ref: "evidence:review" }] } }],
    ["passed noncomplete", { fact: validFact, receipt: { ...completeReceipt, outcome: "failed", receipt: { ...completeReceipt.receipt, outcome: "failed" } } }],
    ["unresolved deviation", { fact: validFact, receipt: { ...completeReceipt, receipt: { ...completeReceipt.receipt, deviations: [{ deviation_ref: "deviation:one", review_state: "unreviewed" }] } } }],
  ];
  for (const [label, override] of cases) {
    const writes = [];
    const targetReceipt = override.receipt || completeReceipt;
    const c = { query: async (sql, params = []) => {
      if (sql.includes("from ops.engineering_slice_receipt")) return { rows: [targetReceipt] };
      if (sql.includes("insert into ops.engineering_reviewer_fact")) { writes.push({ sql, params }); return { rows: [{ id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc", state: "passed" }] }; }
      return { rows: [] };
    } };
    const targetActor = override.actor || reviewer;
    await assert.rejects(
      () => recordEngineeringReview(c, targetActor, { idempotency_key: "dddddddd-dddd-4ddd-8ddd-dddddddddddd", receipt_id: receiptId, fact: override.fact }, EngineeringToolError, async () => {}),
      error => Boolean(error?.error),
      label,
    );
    assert.equal(writes.length, 0, `${label} must leave no immutable reviewer row`);
  }
});


// --- portfolio ancestor binding at the existing admission -------------------
// Governance is decided by trusted stored source. There is no caller argument
// that turns it on or off, so these tests drive the stored answer instead.

/** The runtime calls `new ToolError(payload)`, so the test's error class has to
 *  keep the payload rather than stringify it into a message. */
class BindingToolError extends Error {
  constructor(payload) { super(payload?.error || "tool_error"); Object.assign(this, payload); }
}

/** A stub connection returning one exact ops.portfolio_descendant_binding row. */
function bindingConnection(binding) {
  return { query: async () => ({ rows: [{ binding }] }) };
}

const GOVERNED_BINDING = Object.freeze({
  governed: true,
  portfolio_ref: "WR-000062",
  portfolio_revision_id: "11111111-1111-4111-8111-111111111111",
  accepted_digest: `sha256:${"a".repeat(64)}`,
  child_ref: "foundation-and-control-plane",
  child_version: 1,
  child_digest: `sha256:${"b".repeat(64)}`,
  node_ref: "step:j1-kernel-production-outcome",
  authority_class: "synthetic_authority",
  effect_class: "synthetic_no_effect",
  data_class: "synthetic_record_layer",
  budget_identity: "synthetic:budget-9",
  budget_ceiling: 9000,
  model_floor: { provider: "synthetic", model: "synthetic", version: "1", effort: "high" },
  recovery_ref: "recovery:synthetic-9",
  terminal_predicate: "synthetic accepted outcome present",
  // The current admitted plan in this fixture. A child bound to anything else
  // authorizes different work that merely shares a name.
  child_accepted_plan_ref: source.plan.plan_ref,
  predecessors: [{ node_ref: "step:foundation-assurance-minimum-receipt",
    child_ref: "foundation-and-control-plane",
    accepted_plan_ref: source.plan.plan_ref }],
});

test("ordinary attended source work is not portfolio-governed", async () => {
  const connection = bindingConnection({ governed: false });
  assert.equal(await portfolioAncestorBinding(connection, {}, source, plan, "slice:ordinary-work",
    BindingToolError), null);
});

test("a governed slice with no predecessors binds its accepted ancestor", async () => {
  const binding = await portfolioAncestorBinding(
    bindingConnection({ ...GOVERNED_BINDING, predecessors: [] }), {}, source, plan,
    "step:j1-kernel-production-outcome", BindingToolError);
  assert.equal(binding.portfolio_ref, "WR-000062");
  assert.equal(binding.child_ref, "foundation-and-control-plane");
  assert.equal(binding.child_version, 1);
  assert.equal(binding.child_digest, GOVERNED_BINDING.child_digest);
  assert.equal(binding.child_accepted_plan_ref, source.plan.plan_ref);
  assert.deepEqual(binding.predecessors, []);
  assert.deepEqual(binding.verified_predecessors, []);
});

test("a child bound to a DIFFERENT accepted plan refuses admission", () => {
  // Root reproduced this exactly: a child bound to PLAN-accepted-child-v1 while
  // the admitted source plan was PLAN-different-current-v1 previously returned
  // the binding unchanged, so one accepted plan silently governed another's work.
  return assert.rejects(
    () => portfolioAncestorBinding(bindingConnection({
      ...GOVERNED_BINDING, predecessors: [],
      child_accepted_plan_ref: "PLAN-different-current-v1",
    }), {}, source, plan, "step:j1-kernel-production-outcome", BindingToolError),
    error => {
      assert.equal(error.error, "engineering_portfolio_child_plan_mismatch");
      assert.equal(error.child_accepted_plan_ref, "PLAN-different-current-v1");
      assert.equal(error.admitted_plan_ref, source.plan.plan_ref);
      return true;
    });
});

test("a child with NO accepted plan binding refuses admission", () => {
  // A proposal may describe a child whose source binding is not accepted yet.
  // That child may exist; it may not admit anything.
  return assert.rejects(
    () => portfolioAncestorBinding(bindingConnection({
      ...GOVERNED_BINDING, predecessors: [], child_accepted_plan_ref: null,
    }), {}, source, plan, "step:j1-kernel-production-outcome", BindingToolError),
    error => {
      assert.equal(error.error, "engineering_portfolio_child_plan_mismatch");
      assert.equal(error.child_accepted_plan_ref, null);
      return true;
    });
});

test("a predecessor bound to another plan is unmet, not satisfied by a same-named proof", () => {
  // The decisive cross-plan case: the predecessor's own accepted plan is not the
  // plan being admitted, so this transaction's facts cannot speak to it at all.
  // Matching on the node name alone would let one plan's proof admit another's.
  return assert.rejects(
    () => portfolioAncestorBinding(bindingConnection({
      ...GOVERNED_BINDING,
      predecessors: [{ node_ref: "step:foundation-assurance-minimum-receipt",
        child_ref: "assurance-fabric", accepted_plan_ref: "PLAN-some-other-plan-v1" }],
    }), {}, source, plan, "step:j1-kernel-production-outcome", BindingToolError),
    error => {
      assert.equal(error.error, "engineering_portfolio_predecessor_proof_missing");
      assert.equal(error.unmet_predecessors[0].reason, "predecessor_plan_not_admitted");
      assert.equal(error.unmet_predecessors[0].accepted_plan_ref, "PLAN-some-other-plan-v1");
      return true;
    });
});

test("a declared predecessor with no independent proof REFUSES admission", async () => {
  // The accepted portfolio names a predecessor. Nothing maps it to a passed
  // Engineering proof, so admission refuses rather than admitting the work and
  // calling the obligation satisfied in the same breath.
  await assert.rejects(
    () => portfolioAncestorBinding(bindingConnection(GOVERNED_BINDING), {}, source, plan,
      "step:j1-kernel-production-outcome", BindingToolError),
    error => {
      assert.equal(error.error, "engineering_portfolio_predecessor_proof_missing");
      assert.equal(error.unmet_predecessors[0].node_ref, "step:foundation-assurance-minimum-receipt");
      assert.equal(error.unmet_predecessors[0].reason, "no_passed_independent_proof");
      return true;
    });
});

test("the predecessor refusal happens before any job, session or envelope write", async () => {
  // Every statement this connection sees is recorded. A refusal that had
  // already written something would show the write here.
  const statements = [];
  const connection = { query: async (sql) => {
    statements.push(sql);
    return { rows: [{ binding: GOVERNED_BINDING }] };
  } };
  await assert.rejects(() => portfolioAncestorBinding(connection, {}, source, plan,
    "step:j1-kernel-production-outcome", BindingToolError));
  assert.equal(statements.length, 1, "only the read-only binding lookup may run");
  assert.match(statements[0], /portfolio_descendant_binding/);
  for (const forbidden of [/insert\s+into/i, /update\s+/i, /ops\.job/i,
    /engineering_execution_envelope/i, /capability_agent_session/i]) {
    assert.ok(!forbidden.test(statements[0]), `no write may precede the refusal: ${forbidden}`);
  }
});

test("an incomplete accepted ancestor refuses rather than admitting", async () => {
  for (const field of ["accepted_digest", "child_digest", "child_version",
    "authority_class", "budget_ceiling", "model_floor", "terminal_predicate"]) {
    const partial = { ...GOVERNED_BINDING, predecessors: [] };
    delete partial[field];
    await assert.rejects(
      () => portfolioAncestorBinding(bindingConnection(partial), {}, source, plan, "step:x",
        BindingToolError),
      error => {
        assert.equal(error.error, "engineering_portfolio_ancestor_incomplete");
        assert.equal(error.missing_field, field);
        return true;
      },
      `a binding missing ${field} must refuse`);
  }
});

test("an ungoverned envelope keeps its exact previous shape", () => {
  const ungoverned = buildCodexEnvelope({ source, plan, slice, jobId: "44444444-4444-4444-8444-444444444444",
    sessionId: "55555555-5555-4555-8555-555555555555", actor,
    envelopeId: "66666666-6666-4666-8666-666666666666",
    expiresAt: "2099-01-01T00:00:00Z" });
  assert.ok(!("portfolio_binding" in ungoverned),
    "an ungoverned envelope must not gain a field, or every existing digest moves");

  const governed = buildCodexEnvelope({ source, plan, slice, jobId: "44444444-4444-4444-8444-444444444444",
    sessionId: "55555555-5555-4555-8555-555555555555", actor,
    envelopeId: "66666666-6666-4666-8666-666666666666",
    expiresAt: "2099-01-01T00:00:00Z", portfolioBinding: GOVERNED_BINDING });
  assert.equal(governed.portfolio_binding.portfolio_ref, "WR-000062");
  assert.notEqual(JSON.stringify(governed), JSON.stringify(ungoverned));
});

// --- V5-F03 deep-module execution contract ------------------------------------
//
// These fixtures and case tables exercise the SERVER validator.  They are the
// same shape as the ones in tools/room-bridge/test_engineering_passport_unit.py,
// but that resemblance is no longer what carries the parity claim: two
// hand-maintained parallel tables drift the moment one side is edited alone,
// which is exactly what they cannot detect.  Parity is now proven by one shared,
// versioned corpus -- test/fixtures/f03-design-contract-parity.v1.json, driven
// through BOTH validators by test/f03-design-contract-parity.test.mjs, which
// compares the two verdict-for-verdict.  So a case added here does not have to
// be mirrored by hand over there, and a rule that exists in only one validator
// is caught by the corpus rather than by two tables agreeing on paper.
//
// Parity is claimed for engineering-slice-plan.v2, not universally.  THREE
// legacy v1 families are the documented divergence -- duplicate ordinals,
// dependency cycles, and whitespace-padded identifiers.  The portable validator
// has always refused all three, and this server validator has always accepted
// them, so refusing them here now would strand already registered append-only
// plans on every read path; the gate is the declared plan version, so a plan
// newly registered as v1 keeps that permissive path too (see the header of
// src/engineering-runtime.js).  The corpus asserts both halves of each
// divergence exactly, in both languages, and the cases below state the server
// half rather than asserting a parity that does not hold.

const DESIGN_CONTRACT_FIELD_NAMES = [
  "authority", "code_model_decision", "completion", "contract_version", "dependency_rationale",
  "deployment", "evidence", "failure", "full_design_refs", "isolation", "rationale", "review",
  "routing", "seam_decision", "short_template", "tests",
];
const RESERVED_CODE_RESPONSIBILITIES = [
  "execution", "identity", "idempotency", "permissions", "policy", "state", "validation",
];

function modelStep(overrides = {}) {
  return {
    step_ref: "step:synthetic-read", responsibility_class: "classification",
    input_contract_ref: "contract:step-input", output_contract_ref: "contract:step-output",
    rationale: "the candidate label is genuinely uncertain and code would reduce quality",
    selection_basis: ["typed_uncertainty", "quality_gain"], ...overrides,
  };
}

function designContract(row) {
  const depth = classifyDesignDepth(row, EngineeringToolError);
  const redaction = row.planned_checks.some(check => check.evidence_requirement === "redacted_evidence_required")
    ? "redacted_evidence" : "metadata_only";
  return {
    contract_version: ENGINEERING_DESIGN_CONTRACT_VERSION,
    rationale: "the closed validator owns this behavior end to end",
    dependency_rationale: "no accepted predecessor slice is required",
    code_model_decision: {
      rationale: "stable enforceable behavior stays deterministic code",
      selection_basis: ["capability_gain", "quality_gain"],
      model_judgment_steps: [],
    },
    routing: { executor_class: "deterministic_code", adapter_ref: "adapter:codex-desktop", fresh_session_required: true },
    authority: { capability_profile: "capability:engineering-repository-write", read_only: false, environment: "rehearsal" },
    isolation: { worktree_required: true, branch_required: true, shared_resource_refs: [] },
    tests: {
      planned_check_refs: row.planned_checks.map(check => check.check_ref),
      verification_lanes: row.manual_qa_required ? ["contract", "manual_qa"] : ["contract"],
    },
    review: { independent_review_required: true, reviewer_class: "independent_agent" },
    failure: { failure_modes: [{
      failure_ref: "failure:contract-drift", detection: "the closed validator refuses the plan",
      compensation: "revise the accepted plan revision before admission",
    }] },
    evidence: {
      redaction_class: redaction, retention: "material_redacted",
      evidence_refs: [{ ref: "evidence:design", redaction_class: redaction, content_digest: `sha256:${"a".repeat(64)}` }],
    },
    deployment: {
      release_requirement: row.release_requirement,
      rollback_ref: row.release_requirement === "required" ? "release:rollback-plan" : null,
      confirmation_required: !["R0", "R1"].includes(row.risk_class),
    },
    completion: {
      completion_predicate: "every accepted planned check passes under independent review",
      verified_by: row.manual_qa_required ? "independent_review_and_manual_qa" : "independent_review",
    },
    seam_decision: {
      mode: "extend", target_seam_ref: "seam:engineering-runtime",
      measurement: { basis: "complexity_reduction", note: "extending the proven validator is smaller than a new module" },
      new_module_justification: null, replaced_seam_refs: [], residual_authority_refs: [],
    },
    full_design_refs: depth === "short" ? null : {
      design_interview_ref: "interview:v5-f03", authority_envelope_ref: "envelope:v5-f03",
      failure_model_ref: "failure-model:v5-f03", fixture_refs: ["fixture:v5-f03-boundary"],
      oracle_ref: "oracle:doctorcre-v5:Q035.D1",
    },
    short_template: depth === "full" ? null : {
      template_ref: "template:short-governed-v1",
      objective_summary: "one bounded parallel-safe change with no dependencies",
      verification_ref: "verification:short-governed-v1",
    },
  };
}

function v2Slice(sliceRef = "slice:short", ordinal = 1, overrides = {}) {
  const row = {
    slice_ref: sliceRef, ordinal, objective: "Deepen the accepted slice contract",
    definition_of_done: "Both validators agree on one closed contract",
    dependency_refs: [], declared_resource_refs: ["resource:worktree-a"],
    declared_component_refs: ["component:execution-fabric"], declared_plan_step_refs: ["step:synthetic-read"],
    baseline_evidence_refs: [], planned_checks: [{
      check_ref: "check:contract", failure_condition: "an unknown field is accepted",
      evidence_requirement: "redacted_evidence_required",
    }],
    scope_boundary: "the two existing slice-plan validators", forbidden_change_refs: ["forbidden:new-authority"],
    concurrency_posture: "parallel_safe", manual_qa_required: false,
    risk_class: "R1", release_requirement: "not_required", ...overrides,
  };
  row.design_contract = designContract(row);
  return row;
}

function withoutDigest(typedPlan) {
  return Object.fromEntries(Object.entries(typedPlan).filter(([key]) => key !== "plan_digest"));
}

function reseal(typedPlan) {
  typedPlan.plan_digest = canonicalDigest(withoutDigest(typedPlan));
  return typedPlan;
}

function v2Plan(slices) {
  return reseal({
    schema_version: "engineering-slice-plan.v2",
    work_request: { id: source.work.id, state_version: 3, canonical_record_digest: source.work.canonical_record_digest },
    accepted_plan_revision: { id: source.plan.plan_ref, revision: 2, digest: source.plan.digest },
    slices, plan_digest: null,
  });
}

function refusesPlan(typedPlan, note) {
  assert.throws(() => requirePlan(typedPlan, EngineeringToolError), EngineeringToolError,
    `plan should have refused: ${note}`);
}

test("the design depth classifier matches the approved SHORT predicate exactly", () => {
  const base = v2Slice();
  assert.equal(classifyDesignDepth(base, EngineeringToolError), "short");
  for (const [field, value] of [
    ["concurrency_posture", "serial_after_dependencies"],
    ["concurrency_posture", "exclusive_resource"],
    ["manual_qa_required", true],
    ["release_requirement", "required"],
    ["dependency_refs", ["slice:other"]],
    ["declared_resource_refs", ["resource:a", "resource:b"]],
    ["declared_component_refs", ["component:a", "component:b"]],
    ["declared_plan_step_refs", ["step:a", "step:b"]],
  ]) {
    assert.equal(classifyDesignDepth({ ...base, [field]: value }, EngineeringToolError), "full",
      `${field}=${JSON.stringify(value)} must take the full path`);
  }
  for (const risk of ["R0", "R1", "R2", "R3"])
    assert.equal(classifyDesignDepth({ ...base, risk_class: risk }, EngineeringToolError), "short", risk);
  for (const risk of ["R4", "R5", "R6"])
    assert.equal(classifyDesignDepth({ ...base, risk_class: risk }, EngineeringToolError), "full", risk);
  assert.equal(classifyDesignDepth({
    ...base, declared_resource_refs: [], declared_component_refs: [], declared_plan_step_refs: [],
  }, EngineeringToolError), "short");
});

test("the planned check count is never a classifier input and every check stays mandatory", () => {
  const base = v2Slice();
  const inputs = designDepthInputs(base, EngineeringToolError);
  assert.ok(!JSON.stringify(inputs).includes("planned_check"));
  const many = structuredClone(base);
  many.planned_checks = [0, 1, 2, 3].map(index => ({
    check_ref: `check:extra-${index}`, failure_condition: "verification is missing",
    evidence_requirement: "redacted_evidence_required",
  }));
  assert.deepEqual(designDepthInputs(many, EngineeringToolError), inputs);
  assert.equal(classifyDesignDepth(many, EngineeringToolError), "short");
  many.design_contract = designContract(many);
  assert.doesNotThrow(() => requirePlan(v2Plan([structuredClone(many)]), EngineeringToolError));
  const dropped = structuredClone(many);
  dropped.design_contract.tests.planned_check_refs = ["check:extra-0"];
  refusesPlan(v2Plan([dropped]), "tests may not drop a planned check");
  // The portable table refuses this case too: verification is never optional,
  // so a slice cannot plan zero checks and take the SHORT template anyway.
  const emptied = structuredClone(many);
  emptied.planned_checks = [];
  assert.throws(() => requirePlan(v2Plan([emptied]), EngineeringToolError),
    error => error.error === "engineering_slice_checks_invalid",
    "a slice must plan at least one check");
});

test("an agent cannot self-label design depth or a bypass", () => {
  for (const label of ["design_depth", "simple", "complexity", "classifier_override", "bypass"]) {
    const row = v2Slice();
    row[label] = "short";
    assert.throws(() => classifyDesignDepth(row, EngineeringToolError),
      error => error.error === "engineering_design_depth_self_label_forbidden",
      `the classifier consumed a self-label: ${label}`);
    refusesPlan(v2Plan([row]), `slice self-label ${label}`);
    const contracted = v2Slice();
    contracted.design_contract[label] = "short";
    refusesPlan(v2Plan([contracted]), `contract self-label ${label}`);
  }
});

test("every Q046 design contract field is bound and changes the canonical digest", () => {
  const typed = v2Plan([v2Slice()]);
  assert.equal(requirePlan(typed, EngineeringToolError).plan_digest, typed.plan_digest);
  for (const field of DESIGN_CONTRACT_FIELD_NAMES) {
    const missing = structuredClone(typed);
    delete missing.slices[0].design_contract[field];
    refusesPlan(reseal(missing), `missing ${field}`);
  }
  const extra = structuredClone(typed);
  extra.slices[0].design_contract.extra_field = "x";
  refusesPlan(reseal(extra), "unknown design contract field");
  const stale = structuredClone(typed);
  stale.slices[0].design_contract.rationale = "a different rationale";
  assert.notEqual(canonicalDigest(withoutDigest(stale)), typed.plan_digest);
  refusesPlan(stale, "stale digest after a design contract change");
  for (const [facet, key, value] of [
    ["routing", "fresh_session_required", false],
    ["authority", "capability_profile", "capability:read-only"],
    ["isolation", "worktree_required", false],
    ["review", "independent_review_required", false],
    ["evidence", "redaction_class", "metadata_only"],
    ["deployment", "release_requirement", "required"],
    ["completion", "verified_by", "independent_review_and_manual_qa"],
  ]) {
    const row = structuredClone(typed);
    row.slices[0].design_contract[facet][key] = value;
    refusesPlan(reseal(row), `${facet}.${key}=${value}`);
  }
  const emptyFailure = structuredClone(typed);
  emptyFailure.slices[0].design_contract.failure.failure_modes = [];
  refusesPlan(reseal(emptyFailure), "a slice must model at least one failure mode");
});

test("reserved deterministic responsibilities refuse model judgment", () => {
  for (const reserved of RESERVED_CODE_RESPONSIBILITIES) {
    const row = v2Slice();
    row.design_contract.routing.executor_class = "model_assisted";
    row.design_contract.code_model_decision.model_judgment_steps = [modelStep({ responsibility_class: reserved })];
    assert.throws(() => requirePlan(v2Plan([row]), EngineeringToolError),
      error => error.error === "engineering_design_model_step_reserved_responsibility" &&
        error.responsibility_class === reserved,
      `model judgment claimed ${reserved}`);
  }
});

test("a typed model step needs contracts, rationale and more than cost", () => {
  const accepted = v2Slice();
  accepted.design_contract.routing.executor_class = "model_assisted";
  accepted.design_contract.code_model_decision.model_judgment_steps = [modelStep()];
  assert.doesNotThrow(() => requirePlan(v2Plan([structuredClone(accepted)]), EngineeringToolError));
  for (const override of [
    { input_contract_ref: "" }, { output_contract_ref: "" }, { rationale: "  " },
    { selection_basis: ["cost"] }, { selection_basis: [] }, { selection_basis: ["invented"] },
    { step_ref: "step:never-declared" }, { responsibility_class: "vibes" },
  ]) {
    const row = v2Slice();
    row.design_contract.routing.executor_class = "model_assisted";
    row.design_contract.code_model_decision.model_judgment_steps = [modelStep(override)];
    refusesPlan(v2Plan([row]), `model step ${JSON.stringify(override)}`);
  }
  const deterministic = v2Slice();
  deterministic.design_contract.code_model_decision.model_judgment_steps = [modelStep()];
  refusesPlan(v2Plan([deterministic]), "a deterministic_code route cannot carry model judgment");
  const unstaffed = v2Slice();
  unstaffed.design_contract.routing.executor_class = "model_assisted";
  refusesPlan(v2Plan([unstaffed]), "a model_assisted route needs a typed model step");
  const costOnly = v2Slice();
  costOnly.design_contract.code_model_decision.selection_basis = ["cost"];
  assert.throws(() => requirePlan(v2Plan([costOnly]), EngineeringToolError),
    error => error.error === "engineering_design_cost_only_selection",
    "cost alone selected the code/model choice");
  const measured = v2Slice();
  measured.design_contract.code_model_decision.selection_basis = ["cost", "capability_gain"];
  assert.doesNotThrow(() => requirePlan(v2Plan([measured]), EngineeringToolError));
});

// The oracle/fixture interface is a DECLARATION and not a binding, and that is
// a decision stated in engineering-runtime.js beside FULL_DESIGN_REF_FIELDS,
// not an oversight.  It is asserted here for the same reason the documented v1
// divergences are asserted rather than described: a non-goal nothing tests is
// indistinguishable from a gap, and the next reader has no way to tell which
// one they are looking at.  What the contract buys instead of resolution is
// exactness and permanence -- the names are pinned to the identifier regex, the
// fixture list may not repeat or be empty, and every one of them is sealed into
// plan_digest, so an accepted plan cannot be quietly re-pointed at a friendlier
// oracle afterwards.
test("a FULL slice's oracle and fixture refs are declared and sealed, never resolved", () => {
  const unresolvable = v2Slice("slice:short", 1, { risk_class: "R4" });
  assert.equal(classifyDesignDepth(unresolvable, EngineeringToolError), "full");
  unresolvable.design_contract.full_design_refs.oracle_ref = "oracle:no-such-oracle-exists-anywhere";
  unresolvable.design_contract.full_design_refs.fixture_refs = [
    "fixture:no-such-fixture", "fixture:nor-this-one",
  ];
  assert.doesNotThrow(() => requirePlan(v2Plan([structuredClone(unresolvable)]), EngineeringToolError),
    "nothing resolves an oracle_ref or a fixture_ref: naming one is the whole contract");

  // Sealed, so the declaration is at least permanent.  Two plans that differ
  // only in the oracle they name are two different plans.
  const named = v2Plan([structuredClone(unresolvable)]);
  const renamed = structuredClone(named);
  renamed.slices[0].design_contract.full_design_refs.oracle_ref = "oracle:a-different-oracle";
  refusesPlan(renamed, "re-pointing the oracle broke the seal it was accepted under");
  assert.notEqual(reseal(renamed).plan_digest, named.plan_digest,
    "the oracle a slice names is part of what its digest binds");

  // Exact, so the declaration is checkable even though it is not resolvable.
  for (const badOracle of ["not an id", " oracle:padded ", "", 7, null]) {
    const row = structuredClone(unresolvable);
    row.design_contract.full_design_refs.oracle_ref = badOracle;
    refusesPlan(v2Plan([row]), `oracle_ref ${JSON.stringify(badOracle)}`);
  }
  for (const badFixtures of [["fixture:one", "fixture:one"], ["fixture:one", ""], "fixture:one", [7]]) {
    const row = structuredClone(unresolvable);
    row.design_contract.full_design_refs.fixture_refs = badFixtures;
    refusesPlan(v2Plan([row]), `fixture_refs ${JSON.stringify(badFixtures)}`);
  }
});

test("full depth requires the whole design envelope and short work stays governed", () => {
  const short = v2Slice();
  assert.doesNotThrow(() => requirePlan(v2Plan([structuredClone(short)]), EngineeringToolError));
  const complexRow = v2Slice("slice:short", 1, { risk_class: "R4" });
  assert.equal(classifyDesignDepth(complexRow, EngineeringToolError), "full");
  assert.doesNotThrow(() => requirePlan(v2Plan([structuredClone(complexRow)]), EngineeringToolError));
  const smuggled = structuredClone(complexRow);
  smuggled.design_contract.full_design_refs = null;
  smuggled.design_contract.short_template = structuredClone(short.design_contract.short_template);
  refusesPlan(v2Plan([smuggled]), "high-risk work took the shorter template");
  for (const field of ["design_interview_ref", "authority_envelope_ref", "failure_model_ref", "oracle_ref"]) {
    const row = structuredClone(complexRow);
    row.design_contract.full_design_refs[field] = "";
    refusesPlan(v2Plan([row]), `full_design_refs ${field}`);
  }
  const noFixtures = structuredClone(complexRow);
  noFixtures.design_contract.full_design_refs.fixture_refs = [];
  refusesPlan(v2Plan([noFixtures]), "full depth requires fixtures");
  const overreaching = structuredClone(short);
  overreaching.design_contract.full_design_refs = structuredClone(complexRow.design_contract.full_design_refs);
  refusesPlan(v2Plan([overreaching]), "the SHORT shape is exact");
  for (const field of ["review", "failure", "evidence", "deployment", "completion", "seam_decision", "tests"]) {
    const row = structuredClone(short);
    delete row.design_contract[field];
    refusesPlan(v2Plan([row]), `SHORT dropped ${field}`);
  }
  const ungoverned = structuredClone(short);
  ungoverned.design_contract.review.independent_review_required = false;
  refusesPlan(v2Plan([ungoverned]), "SHORT waived independent review");
  const material = v2Slice("slice:short", 1, { risk_class: "R3" });
  assert.equal(classifyDesignDepth(material, EngineeringToolError), "short");
  const unconfirmed = structuredClone(material);
  unconfirmed.design_contract.deployment.confirmation_required = false;
  refusesPlan(v2Plan([unconfirmed]), "R3 dropped its explicit confirmation gate");
});

test("seam decisions refuse duplicate authority and half-replacement", () => {
  assert.doesNotThrow(() => requirePlan(v2Plan([v2Slice()]), EngineeringToolError));
  const unjustified = v2Slice();
  Object.assign(unjustified.design_contract.seam_decision, { mode: "new_module", new_module_justification: null });
  refusesPlan(v2Plan([unjustified]), "a new module needs a real seam");
  const invented = v2Slice();
  Object.assign(invented.design_contract.seam_decision, { mode: "new_module", new_module_justification: "convenience" });
  refusesPlan(v2Plan([invented]), "convenience is not an accepted module justification");
  const justified = v2Slice();
  Object.assign(justified.design_contract.seam_decision, { mode: "new_module", new_module_justification: "lifecycle" });
  assert.doesNotThrow(() => requirePlan(v2Plan([justified]), EngineeringToolError));
  const unmeasured = v2Slice();
  unmeasured.design_contract.seam_decision.measurement = { basis: "gut_feel", note: "it felt simpler" };
  refusesPlan(v2Plan([unmeasured]), "reuse/extend/replace must be measured");
  const half = v2Slice();
  Object.assign(half.design_contract.seam_decision, {
    mode: "replace", replaced_seam_refs: ["seam:legacy"], residual_authority_refs: ["seam:legacy-residual"],
  });
  assert.throws(() => requirePlan(v2Plan([half]), EngineeringToolError),
    error => error.error === "engineering_design_seam_half_replacement",
    "a replacement that leaves residual authority is a half-fix");
  const itself = v2Slice();
  Object.assign(itself.design_contract.seam_decision, {
    mode: "replace", replaced_seam_refs: ["seam:engineering-runtime"],
  });
  refusesPlan(v2Plan([itself]), "a seam cannot replace itself");
  const first = v2Slice("slice:one", 1);
  Object.assign(first.design_contract.seam_decision, {
    mode: "new_module", target_seam_ref: "seam:new-authority", new_module_justification: "authority",
  });
  const second = v2Slice("slice:two", 2);
  Object.assign(second.design_contract.seam_decision, {
    mode: "replace", target_seam_ref: "seam:new-authority", replaced_seam_refs: ["seam:old-authority"],
  });
  assert.throws(() => requirePlan(v2Plan([first, second]), EngineeringToolError),
    error => error.error === "engineering_design_seam_duplicate_authority" &&
      error.seam_ref === "seam:new-authority",
    "two slices claimed one seam");
  const retiring = v2Slice("slice:one", 1);
  Object.assign(retiring.design_contract.seam_decision, {
    mode: "replace", target_seam_ref: "seam:successor", replaced_seam_refs: ["seam:engineering-runtime"],
  });
  assert.throws(() => requirePlan(v2Plan([retiring, v2Slice("slice:two", 2)]), EngineeringToolError),
    error => error.error === "engineering_design_seam_half_replacement",
    "one slice extended a seam another retires");
});

test("engineering-slice-plan.v1 stays exactly compatible and unknown versions fail explicitly", () => {
  assert.deepEqual([...ENGINEERING_SLICE_PLAN_VERSIONS],
    ["engineering-slice-plan.v1", "engineering-slice-plan.v2"]);
  const v1 = controllerPlan();
  assert.equal(requirePlan(v1, EngineeringToolError).schema_version, "engineering-slice-plan.v1");
  const smuggled = structuredClone(v1);
  smuggled.slices[0].design_contract = designContract(smuggled.slices[0]);
  refusesPlan(reseal(smuggled), "v1 remains closed against the successor field");
  const typed = v2Plan([v2Slice()]);
  const downgraded = structuredClone(typed);
  downgraded.schema_version = "engineering-slice-plan.v1";
  refusesPlan(reseal(downgraded), "a v2 slice is not silently reinterpreted as v1");
  for (const unknown of ["engineering-slice-plan.v3", "engineering-slice-plan", "", null]) {
    const row = structuredClone(typed);
    row.schema_version = unknown;
    assert.throws(() => requirePlan(reseal(row), EngineeringToolError),
      error => error.error === "engineering_slice_plan_schema_invalid",
      `unknown schema_version ${JSON.stringify(unknown)}`);
  }
});

// --- V5-F03 review corrections ------------------------------------------------
//
// Each case below is a contradiction the accepted contract could previously
// state and the runtime would then ignore.  The remedy is always a refusal
// against the binding that already exists; nothing here grants new authority.

test("admission refuses an accepted contract that contradicts the binding it issues", async () => {
  const contradictions = [
    ["authority.read_only", row => { row.design_contract.authority.read_only = true; }],
    ["authority.environment", row => { row.design_contract.authority.environment = "production"; }],
    ["authority.capability_profile", row => {
      Object.assign(row.design_contract.authority, {
        read_only: true, capability_profile: "capability:engineering-read-only",
      });
    }],
    ["routing.adapter_ref", row => { row.design_contract.routing.adapter_ref = "adapter:human-desk"; }],
    ["routing.executor_class", row => { row.design_contract.routing.executor_class = "attended_human"; }],
  ];
  for (const [field, contradict] of contradictions) {
    const row = v2Slice();
    contradict(row);
    const typed = v2Plan([row]);
    assert.doesNotThrow(() => requirePlan(typed, EngineeringToolError),
      `${field} is still accepted by the sealed contract itself`);
    const calls = [];
    const c = { query: async sql => {
      calls.push(sql);
      if (sql.includes("engineering_passport_facts")) return { rows: [{ facts: passportFacts(typed) }] };
      return { rows: [] };
    } };
    await assert.rejects(() => admitEngineeringSlice(c, actor, {
      idempotency_key: "77777777-7777-4777-8777-777777777777",
      work_request: source.work.ref, slice_ref: "slice:short",
    }, EngineeringToolError, async () => {}),
      error => error.error === "engineering_design_contract_binding_mismatch" && error.field === field,
      field);
    assert.equal(calls.some(sql => /pg_advisory_xact_lock|engineering_enqueue_slice_job|insert into ops\.|update ops\./i.test(sql)),
      false, `${field} must refuse before any lock, job, session or envelope`);
    // The refusal belongs to admission alone: an already sealed plan stays
    // readable, because refusing on the read path would strand a closed
    // passport that nothing can amend.
    assert.equal(closureProjection(passportFacts(typed), EngineeringToolError).closure_state, "blocked", field);
  }
});

test("a v2 slice whose contract matches the issued binding still admits", async () => {
  const typed = v2Plan([v2Slice()]);
  assert.equal(typed.slices[0].design_contract.routing.adapter_ref, ENGINEERING_SERVER_EXECUTION_BINDING.adapter_ref);
  const facts = passportFacts(typed);
  const envelopeId = "66666666-6666-4666-8666-666666666666";
  const c = { query: async sql => {
    if (sql.includes("engineering_passport_facts")) return { rows: [{ facts }] };
    if (sql.includes("from actor")) return { rows: [{ id: actor.id, slug: actor.slug }] };
    if (sql.trimStart().startsWith("select id from ops.engineering_slice_plan")) return { rows: [{ id: "12121212-1212-4212-8212-121212121212" }] };
    if (sql.includes("select id from ops.work_request")) return { rows: [{ id: source.work.id.replace(/^wr:/, "") }] };
    if (sql.includes("insert into ops.capability_agent_session")) return { rows: [{
      id: "44444444-4444-4444-8444-444444444444", executor_actor_id: actor.id, state: "claimed",
      lease_expires_at: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
    }] };
    if (sql.includes("capability_agent_session")) return { rows: [] };
    if (sql.includes("engineering_enqueue_slice_job")) return { rows: [{ id: "55555555-5555-4555-8555-555555555555" }] };
    if (sql.includes("insert into ops.engineering_execution_envelope")) return { rows: [{ id: envelopeId }] };
    return { rows: [] };
  } };
  const admitted = await admitEngineeringSlice(c, actor, {
    idempotency_key: "88888888-8888-4888-8888-888888888888",
    work_request: source.work.ref, slice_ref: "slice:short",
  }, EngineeringToolError, async () => {});
  assert.equal(admitted.replayed, false);
  assert.equal(admitted.envelope_id, envelopeId);
});

test("an unsupported reviewer class is refused rather than sealed into a plan", () => {
  const accepted = v2Slice();
  assert.equal(accepted.design_contract.review.reviewer_class, "independent_agent");
  assert.doesNotThrow(() => requirePlan(v2Plan([accepted]), EngineeringToolError));
  for (const unsupported of ["independent_human", "self_review", ""]) {
    const row = v2Slice();
    row.design_contract.review.reviewer_class = unsupported;
    refusesPlan(v2Plan([row]), `reviewer_class ${unsupported}`);
  }
});

test("the SHORT predicate is frozen to the contract version that sealed it", () => {
  assert.deepEqual([...ENGINEERING_DESIGN_DEPTH_PREDICATE_VERSIONS], [ENGINEERING_DESIGN_CONTRACT_VERSION]);
  const base = v2Slice();
  assert.equal(classifyDesignDepth(base, EngineeringToolError, ENGINEERING_DESIGN_CONTRACT_VERSION), "short");
  for (const unknown of ["engineering-design-contract.v2", "", null]) {
    assert.throws(() => classifyDesignDepth(base, EngineeringToolError, unknown),
      error => error.error === "engineering_design_depth_predicate_unsupported",
      `no predicate is frozen for ${JSON.stringify(unknown)}`);
  }
  const typed = v2Plan([v2Slice()]);
  assert.doesNotThrow(() => requirePlan(typed, EngineeringToolError));
  const successor = structuredClone(typed);
  successor.slices[0].design_contract.contract_version = "engineering-design-contract.v2";
  refusesPlan(reseal(successor), "a changed predicate has to ship as an explicit successor contract version");
});

test("two parallel-safe slices cannot both own one declared resource", () => {
  assert.throws(() => requirePlan(v2Plan([v2Slice("slice:one", 1), v2Slice("slice:two", 2)]), EngineeringToolError),
    error => error.error === "engineering_design_parallel_resource_conflict" &&
      error.resource_ref === "resource:worktree-a" && error.conflicting_slice_ref === "slice:one",
    "both slices declared one resource while each stated nothing it touches is shared");
  assert.doesNotThrow(() => requirePlan(v2Plan([
    v2Slice("slice:one", 1),
    v2Slice("slice:two", 2, { declared_resource_refs: ["resource:worktree-b"] }),
  ]), EngineeringToolError), "distinct resources stay parallel");
  assert.doesNotThrow(() => requirePlan(v2Plan([
    v2Slice("slice:one", 1),
    v2Slice("slice:two", 2, { dependency_refs: ["slice:one"] }),
  ]), EngineeringToolError), "a dependency edge means the two can never run at once");
  assert.doesNotThrow(() => requirePlan(v2Plan([
    v2Slice("slice:one", 1),
    v2Slice("slice:two", 2, { concurrency_posture: "exclusive_resource" }),
  ]), EngineeringToolError), "declaring the contention is the supported route");
  assert.doesNotThrow(() => requirePlan(v2Plan([
    v2Slice("slice:one", 1, { declared_resource_refs: ["resource:worktree-a", "resource:worktree-a"] }),
  ]), EngineeringToolError), "one slice repeating its own resource is not contention with anyone");
  const v1 = typedEngineeringPlan([
    { ...engineeringSlice("slice:one", 1), concurrency_posture: "parallel_safe", declared_resource_refs: ["resource:worktree-a"] },
    { ...engineeringSlice("slice:two", 2), concurrency_posture: "parallel_safe", declared_resource_refs: ["resource:worktree-a"] },
  ]);
  assert.doesNotThrow(() => requirePlan(v1, EngineeringToolError),
    "engineering-slice-plan.v1 keeps its exact accepted behavior");
});

test("duplicate ordinals and dependency cycles refuse in v2 while legacy v1 keeps its exact read behavior", () => {
  // Both refusals are NEW on the server side.  requirePlan re-runs against the
  // stored append-only plan row on every read path, so enforcing them for v1
  // would not repair an already registered plan -- it would strand one, leaving
  // a sealed passport unreadable with nothing able to amend the row.  They
  // therefore bind engineering-slice-plan.v2 only.  The portable validator has
  // always refused both for every version; that divergence is confined to
  // legacy v1 and documented in engineering-runtime.js, not claimed as parity.
  const duplicateV1 = typedEngineeringPlan([engineeringSlice("slice:one", 1), engineeringSlice("slice:two", 1)]);
  assert.doesNotThrow(() => requirePlan(duplicateV1, EngineeringToolError),
    "a pre-existing v1 plan with duplicate ordinals must stay readable");
  const selfDependentV1 = typedEngineeringPlan([engineeringSlice("slice:one", 1, ["slice:one"])]);
  assert.doesNotThrow(() => requirePlan(selfDependentV1, EngineeringToolError),
    "a pre-existing v1 self-dependent plan must stay readable");
  const cycleV1 = typedEngineeringPlan([
    engineeringSlice("slice:one", 1, ["slice:two"]), engineeringSlice("slice:two", 2, ["slice:one"]),
  ]);
  assert.doesNotThrow(() => requirePlan(cycleV1, EngineeringToolError),
    "a pre-existing v1 cycle must stay readable, and stays blocked exactly as before");
  // Read paths keep working on that legacy plan: it projects, and every slice on
  // the cycle stays blocked, which is the previous behavior unchanged.
  const legacyProjection = closureProjection(passportFacts(cycleV1), EngineeringToolError);
  assert.deepEqual(legacyProjection.slices.map(row => row.state), ["blocked", "blocked"]);
  assert.equal(legacyProjection.closure_state, "blocked");

  const duplicateV2 = v2Plan([
    v2Slice("slice:one", 1),
    v2Slice("slice:two", 1, { declared_resource_refs: ["resource:worktree-b"] }),
  ]);
  assert.throws(() => requirePlan(duplicateV2, EngineeringToolError),
    error => error.error === "engineering_slice_ordinal_duplicate" && error.ordinal === 1,
    "two v2 slices claimed ordinal 1");
  const selfDependentV2 = v2Plan([v2Slice("slice:one", 1, { dependency_refs: ["slice:one"] })]);
  assert.throws(() => requirePlan(selfDependentV2, EngineeringToolError),
    error => error.error === "engineering_slice_dependency_cycle" && error.slice_ref === "slice:one",
    "a v2 slice cannot depend on itself");
  const v2Cycle = v2Plan([
    v2Slice("slice:one", 1, { dependency_refs: ["slice:two"] }),
    v2Slice("slice:two", 2, { dependency_refs: ["slice:one"] }),
  ]);
  assert.throws(() => requirePlan(v2Cycle, EngineeringToolError),
    error => error.error === "engineering_slice_dependency_cycle",
    "a two-slice v2 cycle can never satisfy either dependency");
});

// --- V5-F03 exact identifier admission ----------------------------------------
//
// requirePlan matched the closed identifier regex against text()'s TRIMMED copy
// while every call site discarded that copy and the plan kept, sealed and
// compared the raw string.  " slice:one " therefore registered here and was
// refused by tools/room-bridge/engineering_passport.py (_str with
// identifier=True) and by the SQL validators, all three of which match the raw
// value.  engineering-slice-plan.v2 now matches the raw value too, with the
// same regex and the same contract version: a bug correction inside the
// existing identifier rule, not a new policy or a new version.  A padded
// identifier is refused, never trimmed into an accepted identity, because
// rewriting one would silently change the content plan_digest seals.
//
// Written from character codes so this file stays printable ASCII: 9 tab,
// 10 newline, 13 carriage return.
const TAB = String.fromCharCode(9);
const NEWLINE = String.fromCharCode(10);
const RETURN = String.fromCharCode(13);
const IDENTIFIER_PADDINGS = [
  [" ", ""], ["", " "], [" ", " "], ["  ", "  "],
  [TAB, ""], ["", NEWLINE], ["", RETURN], [`${TAB} `, ` ${RETURN}${NEWLINE}`],
];

test("engineering-slice-plan.v2 admits identifiers exactly as written and refuses padded ones", () => {
  const wrap = (value, [before, after]) => `${before}${value}${after}`;
  const paddedFields = [
    ["work_request.id", (typed, pad) => { typed.work_request.id = wrap(typed.work_request.id, pad); }],
    ["accepted_plan_revision.id", (typed, pad) => {
      typed.accepted_plan_revision.id = wrap(typed.accepted_plan_revision.id, pad);
    }],
    ["slice_ref", (typed, pad) => { typed.slices[0].slice_ref = wrap(typed.slices[0].slice_ref, pad); }],
    ["declared_resource_refs", (typed, pad) => {
      typed.slices[0].declared_resource_refs[0] = wrap(typed.slices[0].declared_resource_refs[0], pad);
    }],
    ["declared_component_refs", (typed, pad) => {
      typed.slices[0].declared_component_refs[0] = wrap(typed.slices[0].declared_component_refs[0], pad);
    }],
    ["declared_plan_step_refs", (typed, pad) => {
      typed.slices[0].declared_plan_step_refs[0] = wrap(typed.slices[0].declared_plan_step_refs[0], pad);
    }],
    ["forbidden_change_refs", (typed, pad) => {
      typed.slices[0].forbidden_change_refs[0] = wrap(typed.slices[0].forbidden_change_refs[0], pad);
    }],
    ["planned_checks.check_ref", (typed, pad) => {
      typed.slices[0].planned_checks[0].check_ref = wrap(typed.slices[0].planned_checks[0].check_ref, pad);
      typed.slices[0].design_contract.tests.planned_check_refs =
        typed.slices[0].planned_checks.map(check => check.check_ref);
    }],
    ["baseline_evidence_refs.ref", (typed, pad) => {
      typed.slices[0].baseline_evidence_refs = [{
        ref: wrap("evidence:baseline", pad), redaction_class: "redacted_evidence",
        content_digest: `sha256:${"a".repeat(64)}`,
      }];
    }],
  ];
  for (const [field, mutate] of paddedFields)
    for (const pad of IDENTIFIER_PADDINGS) {
      const typed = v2Plan([v2Slice()]);
      mutate(typed, pad);
      assert.throws(() => requirePlan(reseal(typed), EngineeringToolError),
        error => error.error === "engineering_identifier_invalid",
        `${field} padded with ${JSON.stringify(pad)} was accepted`);
    }
  for (const blank of ["   ", TAB, NEWLINE]) {
    const typed = v2Plan([v2Slice()]);
    typed.slices[0].slice_ref = blank;
    assert.throws(() => requirePlan(reseal(typed), EngineeringToolError),
      error => error.error === "engineering_field_required",
      "a whitespace-only identifier is a missing field, never an empty accepted identity");
  }
  // The nested design-contract facets already matched raw identifiers; these
  // pin that they still do, so the rule is one rule at every identifier.
  for (const [facet, mutate] of [
    ["routing.adapter_ref", row => { row.design_contract.routing.adapter_ref = "adapter:codex-desktop "; }],
    ["authority.capability_profile", row => {
      row.design_contract.authority.capability_profile = " capability:engineering-repository-write";
    }],
    ["seam_decision.target_seam_ref", row => {
      row.design_contract.seam_decision.target_seam_ref = `seam:engineering-runtime${TAB}`;
    }],
    ["evidence.evidence_refs.ref", row => { row.design_contract.evidence.evidence_refs[0].ref = "evidence:design "; }],
    ["short_template.template_ref", row => {
      row.design_contract.short_template.template_ref = "template:short-governed-v1 ";
    }],
  ]) {
    const row = v2Slice();
    mutate(row);
    refusesPlan(v2Plan([row]), `padded ${facet}`);
  }
  // Nothing narrowed and nothing widened: an exact identifier using the whole
  // accepted character set is still accepted, and prose keeps its exact
  // previous whitespace behavior, so the rule never reaches free text.
  assert.doesNotThrow(() => requirePlan(v2Plan([v2Slice("slice:short.v2_1-a", 1)]), EngineeringToolError),
    "an exact identifier using the whole accepted character set must stay accepted");
  const prose = v2Plan([v2Slice("slice:short", 1, { objective: "  Deepen the accepted slice contract  " })]);
  assert.doesNotThrow(() => requirePlan(prose, EngineeringToolError),
    "an objective is prose, not an identifier");
  assert.equal(prose.slices[0].objective, "  Deepen the accepted slice contract  ",
    "prose must come back exactly as written");
  // The refusal never rewrites the caller's payload, so no reader can observe a
  // trimmed identity that was never registered and never sealed.
  const padded = v2Plan([v2Slice()]);
  padded.slices[0].slice_ref = " slice:short ";
  reseal(padded);
  const before = JSON.stringify(padded);
  assert.throws(() => requirePlan(padded, EngineeringToolError), EngineeringToolError);
  assert.equal(JSON.stringify(padded), before, "a refused plan must come back exactly as it was handed in");
  assert.equal(padded.slices[0].slice_ref, " slice:short ");
});

test("engineering-slice-plan.v1 keeps its exact previous identifier acceptance", () => {
  // DOCUMENTED LEGACY DIVERGENCE, asserted rather than equalized.  The portable
  // and SQL validators have always refused a padded identifier; this server
  // validator has always accepted one, and requirePlan re-runs against the
  // stored append-only plan row on every read path, so refusing it for v1 now
  // would strand an already registered passport instead of repairing it.
  const legacy = () => typedEngineeringPlan([engineeringSlice("slice:one", 1)]);
  for (const [field, mutate] of [
    ["work_request.id", typed => { typed.work_request.id = ` ${typed.work_request.id} `; }],
    ["accepted_plan_revision.id", typed => {
      typed.accepted_plan_revision.id = `${typed.accepted_plan_revision.id}${TAB}`;
    }],
    ["slice_ref", typed => { typed.slices[0].slice_ref = " slice:one "; }],
    ["declared_resource_refs", typed => { typed.slices[0].declared_resource_refs = [" resource:one "]; }],
    ["declared_component_refs", typed => { typed.slices[0].declared_component_refs = [`component:one${NEWLINE}`]; }],
    ["declared_plan_step_refs", typed => { typed.slices[0].declared_plan_step_refs = [`${RETURN}step:one`]; }],
    ["forbidden_change_refs", typed => { typed.slices[0].forbidden_change_refs = ["forbidden:one "]; }],
    ["planned_checks.check_ref", typed => {
      typed.slices[0].planned_checks[0].check_ref = `${typed.slices[0].planned_checks[0].check_ref} `;
    }],
    ["baseline_evidence_refs.ref", typed => {
      typed.slices[0].baseline_evidence_refs = [{
        ref: " evidence:one ", redaction_class: "metadata_only", content_digest: `sha256:${"a".repeat(64)}`,
      }];
    }],
  ]) {
    const typed = legacy();
    mutate(typed);
    assert.doesNotThrow(() => requirePlan(reseal(typed), EngineeringToolError),
      `a pre-existing v1 plan with a padded ${field} must stay readable`);
  }
  // A producer that wants the stricter boundary registers the successor
  // version, which is what the successor version is for.
  const successor = v2Plan([v2Slice()]);
  successor.slices[0].slice_ref = " slice:short ";
  assert.throws(() => requirePlan(reseal(successor), EngineeringToolError),
    error => error.error === "engineering_identifier_invalid",
    "the successor version refuses what legacy v1 keeps accepting");
  // The stored identity comes back exactly as registered, and the read path
  // still projects it, which is the previous behavior unchanged.
  const padded = legacy();
  padded.slices[0].slice_ref = " slice:one ";
  reseal(padded);
  assert.equal(requirePlan(padded, EngineeringToolError).slices[0].slice_ref, " slice:one ",
    "a legacy identity must never be trimmed on the way back out");
  const projection = closureProjection(passportFacts(padded), EngineeringToolError);
  assert.equal(projection.slices[0].slice_ref, " slice:one ");
  assert.equal(projection.slices[0].state, "eligible");
  assert.equal(projection.closure_state, "blocked");
  // The legacy divergence is confined to leading and trailing whitespace.
  const interior = legacy();
  interior.slices[0].slice_ref = "slice: one";
  assert.throws(() => requirePlan(reseal(interior), EngineeringToolError),
    error => error.error === "engineering_identifier_invalid",
    "interior whitespace was never accepted by either validator, in any version");
  const dangling = typedEngineeringPlan([
    engineeringSlice("slice:one", 1), engineeringSlice("slice:two", 2, [" slice:one "]),
  ]);
  assert.throws(() => requirePlan(dangling, EngineeringToolError),
    error => error.error === "engineering_slice_dependency_unknown",
    "a padded dependency reference names no slice, so legacy v1 refuses it too");
});

test("a v2 plan still admits and projects through the existing runtime seams", () => {
  const typed = v2Plan([v2Slice()]);
  const accepted = requirePlan(typed, EngineeringToolError);
  const envelope = buildCodexEnvelope({
    source, plan: accepted, slice: accepted.slices[0],
    jobId: "44444444-4444-4444-8444-444444444444", sessionId: "55555555-5555-4555-8555-555555555555",
    actor, envelopeId: "66666666-6666-4666-8666-666666666666", expiresAt: "2099-01-01T00:00:00Z",
  });
  assert.ok(!JSON.stringify(envelope).includes("design_contract"),
    "the design contract governs admission, not the execution envelope");
  assert.deepEqual(envelope.request.declared_expectations.plan_step_refs, ["step:synthetic-read"]);
  const projection = closureProjection(passportFacts(accepted), EngineeringToolError);
  assert.equal(projection.slice_plan.schema_version, "engineering-slice-plan.v2");
  assert.equal(projection.slices[0].state, "eligible");
  assert.equal(projection.closure_state, "blocked");
});

test("canonical ownership context is minted from sealed runtime bindings and read back on one connection", async () => {
  const calls = [];
  const ownedActor = { ...actor, execution_host_id: "cloudflare-workers:build-126" };
  const db = { query: async (sql, params = []) => {
    calls.push({ sql, params });
    if (sql.includes("issuer-capability")) return { rows: [] };
    if (sql.includes("mint_canonical_ownership_runtime_session")) return { rows: [{ binding: {
      ok: true, ownership_session_ref: "ownership:88888888-8888-4888-8888-888888888888",
      organization_tenant_id: "carr-internal", acting_actor_slug: "codex", execution_host_ref: "cloudflare-workers:build-126",
    } }] };
    if (sql.includes("canonical_ownership_trusted_context")) return { rows: [{ context: {
      ok: true, ownership_session_ref: "ownership:88888888-8888-4888-8888-888888888888",
      organization_tenant_id: "carr-internal", acting_actor_slug: "codex", execution_host_ref: "cloudflare-workers:build-126",
    } }] };
    if (sql.includes("set_config")) return { rows: [] };
    throw new Error(`unexpected query: ${sql}`);
  } };
  const result = await withTrustedCanonicalOwnershipContext(db, ownedActor, {
    work_request_id: "11111111-1111-4111-8111-111111111111", accepted_plan_id: "22222222-2222-4222-8222-222222222222",
    agent_session_id: "55555555-5555-4555-8555-555555555555",
    envelope_id: "33333333-3333-4333-8333-333333333333", attempt: 1, expires_at: "2099-01-01T00:00:00Z",
    idempotency_key: "44444444-4444-4444-8444-444444444444", job_lease_token: "66666666-6666-4666-8666-666666666666",
  }, EngineeringToolError, context => ({ session: context.ownership_session_ref }));
  assert.equal(result.session, "ownership:88888888-8888-4888-8888-888888888888");
  assert.equal(calls.filter(call => call.sql.includes("mint_canonical_ownership_runtime_session")).length, 1);
  assert.equal(calls.filter(call => call.sql.includes("canonical_ownership_trusted_context")).length, 1);
  assert.match(calls.find(call => call.sql.includes("ownership_session_id")).sql, /ownership_session_id/);
  assert.deepEqual(calls.find(call => call.sql.includes("canonical-ownership-server-binding")).params,
    ["carr-internal", "codex", "session:55555555-5555-4555-8555-555555555555",
      "cloudflare-workers:build-126", "66666666-6666-4666-8666-666666666666"]);
  await assert.rejects(() => withTrustedCanonicalOwnershipContext(db, { ...ownedActor, execution_host_id: "forged-host" }, {
    work_request_id: "11111111-1111-4111-8111-111111111111", accepted_plan_id: "22222222-2222-4222-8222-222222222222",
    agent_session_id: "55555555-5555-4555-8555-555555555555",
    envelope_id: "33333333-3333-4333-8333-333333333333", attempt: 1, expires_at: "2099-01-01T00:00:00Z",
    idempotency_key: "77777777-7777-4777-8777-777777777777", job_lease_token: "66666666-6666-4666-8666-666666666666",
  }, EngineeringToolError, () => {}), error => error.error === "canonical_ownership_server_context_unavailable");
});
