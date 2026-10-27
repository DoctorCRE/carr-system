import { NOW } from "./helpers/fixed-clock.mjs";
// Test engineering runtime envelopes, admission, plans, receipts, and closure.
import assert from "node:assert/strict";
import test from "node:test";
import { readSystemWorkCensus, SYSTEM_WORK_LEGS } from "../src/system-work-census.v5.js";
import {
  canonicalDigest,
  ENGINEERING_REPOSITORY_ACTIONS,
  isCurrentRepositoryWriteEnvelope,
  buildCodexEnvelope,
  validateReceiptBinding,

  requirePlan,
  closureProjection,




  engineeringRuntimeTools,

  classifyDesignDepth,
  designDepthInputs,
  ENGINEERING_SLICE_PLAN_VERSIONS,
  ENGINEERING_DESIGN_CONTRACT_VERSION,
  ENGINEERING_DESIGN_DEPTH_PREDICATE_VERSIONS,
  ENGINEERING_SERVER_EXECUTION_BINDING,

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
  const issued = new Date(NOW).toISOString().replace(/\.\d{3}Z$/, "Z");
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

function progressFacts() {
  const plan = typedEngineeringPlan([
    engineeringSlice("slice:one", 1), engineeringSlice("slice:two", 2), engineeringSlice("slice:three", 3),
  ]);
  const envelope = envelopeRow("11111111-1111-4111-8111-111111111111", "slice:one", "2026-08-26T00:00:01Z");
  const receipt = bindReceiptLineage(receiptRow("22222222-2222-4222-8222-222222222222", envelope.id, "slice:one", "claimed_complete", "2026-08-26T00:00:02Z"), plan, envelope);
  const review = reviewerRow("33333333-3333-4333-8333-333333333333", receipt.id, "slice:one", "passed", "2026-08-26T00:00:03Z");
  const facts = passportFacts(plan, { envelopes: [envelope], receipts: [receipt], reviewer_facts: [review] });
  Object.assign(facts.slice_plans[0], { work_request_id: source.work.id.replace(/^wr:/, ""), work_request_version: 3, plan_digest: plan.plan_digest });
  return facts;
}

async function readProgress(facts) {
  const tools = engineeringRuntimeTools({ withEnvelope: () => assert.fail("progress must not write"), writeEvent: () => assert.fail("progress must not write"), ToolError: EngineeringToolError });
  const c = { query: async (sql, params) => {
    assert.equal(sql, "select ops.engineering_passport_facts($1::text) as facts");
    assert.deepEqual(params, ["WR-301"]);
    return { rows: [{ facts }] };
  } };
  return tools["engineering-progress"].handler(c, actor, { work_request: "WR-301" });
}

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
        lease_expires_at: new Date(NOW + 30 * 60 * 1000).toISOString() }] };
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
