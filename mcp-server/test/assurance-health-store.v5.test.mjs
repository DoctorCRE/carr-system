import test from "node:test";
import assert from "node:assert/strict";

import {
  assuranceHealthStoreTools,
  validateAssuranceHealthProjection,
} from "../src/assurance-health-store.v5.js";

class ToolError extends Error {
  constructor(payload) { super(payload.error); this.payload = payload; }
}

const SCOPE = Object.freeze({
  workflow_key: "doctorcre.release",
  workflow_version: 7,
  work_request_id: "WR-700",
});
const OTHER_SCOPE = Object.freeze({
  workflow_key: "doctorcre.release",
  workflow_version: 8,
  work_request_id: "WR-800",
});
const LAYERS = Object.freeze([
  "artifact_assessment",
  "execution_assessment",
  "controller_assessment",
  "candidate_outcome_oracle",
  "activation_readback",
  "actual_business_outcome",
]);

function healthyProjection(scope = SCOPE) {
  return {
    schema_version: "assurance-health.v1",
    scope: { ...scope },
    state: "healthy",
    green: true,
    capability_stage: "act",
    owner: { kind: "record_layer", ref: "ops.assurance_health_evidence" },
    workflow_truth: {
      available: true, source: "V5-F09 workflow census", state: "operational",
      enabled: true, admissible_modes: ["shadow", "canary", "live"],
    },
    evidence: Object.fromEntries(LAYERS.map((layer, index) => [layer, {
      layer,
      state: "passing",
      status: "pass",
      scope: { ...scope },
      evidence_ref: `receipt:${layer}:${index}`,
      evidence_digest: `sha256:${String(index + 1).padStart(64, "0")}`,
      observed_at: "2026-09-25T20:00:00.000Z",
      expires_at: "2026-09-26T20:00:00.000Z",
    }])),
    impact: { scope_limited_to: { ...scope }, withdrawn_stages: [] },
    recovery: { required_evidence: [] },
  };
}

function toolsWith(projection) {
  const calls = [];
  const client = { query: async (sql, params) => {
    calls.push({ sql, params });
    return { rows: [{ projection }] };
  } };
  const tools = assuranceHealthStoreTools({ ToolError, withEnvelope: async (_c, _a, _v, _args, fn) => fn() });
  return { calls, client, handler: tools["read-assurance-health"].handler };
}

test("V5-A01: the live read returns a current evidence-traceable projection for one exact scope", async () => {
  const projection = healthyProjection();
  const { calls, client, handler } = toolsWith(projection);
  const answer = await handler(client, { slug: "dell" }, { scope: SCOPE });

  assert.deepEqual(answer, projection);
  assert.equal(calls.length, 1);
  assert.match(calls[0].sql, /ops\.read_assurance_health/);
  assert.deepEqual(calls[0].params, [SCOPE.workflow_key, SCOPE.workflow_version, SCOPE.work_request_id]);
  assert.deepEqual(new Set(Object.values(answer.evidence).map(row => row.evidence_ref)).size, LAYERS.length);
});

test("V5-A01: failure injection cannot move an unrelated exact scope", async () => {
  const affected = healthyProjection(SCOPE);
  affected.state = "degraded";
  affected.green = false;
  affected.capability_stage = "draft";
  affected.evidence.actual_business_outcome = {
    ...affected.evidence.actual_business_outcome,
    state: "failed",
    status: "fail",
  };
  affected.impact.withdrawn_stages = ["act"];
  affected.recovery.required_evidence = ["actual_business_outcome"];

  const unaffected = healthyProjection(OTHER_SCOPE);
  assert.equal(validateAssuranceHealthProjection(affected, SCOPE, ToolError).state, "degraded");
  assert.equal(validateAssuranceHealthProjection(unaffected, OTHER_SCOPE, ToolError).state, "healthy");
  assert.deepEqual(unaffected, healthyProjection(OTHER_SCOPE), "the sibling scope is byte-for-byte unchanged");
});

test("V5-A01: missing one evidence layer blocks act and green", () => {
  const projection = healthyProjection();
  projection.state = "not-yet-operational";
  projection.green = false;
  projection.capability_stage = "draft";
  projection.evidence.activation_readback = {
    layer: "activation_readback",
    state: "missing",
    present: false,
    scope: { ...SCOPE },
  };
  projection.impact.withdrawn_stages = ["act"];
  projection.recovery.required_evidence = ["activation_readback"];

  assert.equal(validateAssuranceHealthProjection(projection, SCOPE, ToolError).green, false);
});

test("V5-A01 planted traceability mutant: a green label with one evidence identity erased is killed", () => {
  const mutant = healthyProjection();
  delete mutant.evidence.controller_assessment.evidence_ref;
  assert.throws(
    () => validateAssuranceHealthProjection(mutant, SCOPE, ToolError),
    error => error.payload?.error === "assurance_health_projection_invalid",
  );
});

test("V5-A01 planted scope mutant: accepting one layer from a sibling scope is killed", () => {
  const mutant = healthyProjection();
  mutant.evidence.execution_assessment.scope = { ...OTHER_SCOPE };
  assert.throws(
    () => validateAssuranceHealthProjection(mutant, SCOPE, ToolError),
    error => error.payload?.error === "assurance_health_projection_invalid",
  );
});

test("V5-A01 planted missing-layer mutant: green/act with a deleted layer is killed", () => {
  const mutant = healthyProjection();
  delete mutant.evidence.activation_readback;
  assert.throws(
    () => validateAssuranceHealthProjection(mutant, SCOPE, ToolError),
    error => error.payload?.error === "assurance_health_projection_invalid",
  );
});

test("V5-A01: the verb has a closed exact-scope contract and is a read", () => {
  const tool = assuranceHealthStoreTools({ ToolError, withEnvelope: async () => {} })["read-assurance-health"];
  assert.equal(tool.write, undefined);
  assert.equal(tool.writerConnection, true);
  assert.equal(tool.inputSchema.additionalProperties, false);
  assert.deepEqual(tool.inputSchema.required, ["scope"]);
  assert.equal(tool.inputSchema.properties.scope.additionalProperties, false);
});

test("V5-A01: evidence ingress derives attribution and cannot accept a label, green state, tenant or evaluator", async () => {
  const calls = [];
  const client = { query: async (sql, params = []) => {
    calls.push({ sql, params });
    if (sql.includes("set_config")) return { rows: [{ ok: true }] };
    return { rows: [{ receipt: { id: "evidence-row-1", replayed: false } }] };
  } };
  const envelopes = [];
  const tools = assuranceHealthStoreTools({
    ToolError,
    withEnvelope: async (_c, actor, verb, args, fn) => {
      envelopes.push({ actor, verb, args });
      return fn();
    },
  });
  const tool = tools["record-assurance-health-evidence"];
  assert.equal(tool.write, true);
  assert.equal(tool.inputSchema.additionalProperties, false);
  for (const forbidden of ["tenant", "evaluator", "state", "green", "capability_stage"]) {
    assert.equal(Object.hasOwn(tool.inputSchema.properties, forbidden), false, forbidden);
  }
  const evidence = {
    layer: "artifact_assessment",
    basis: "independent_artifact_review",
    status: "pass",
    subject_ref: "release:abc123",
    evidence_ref: "review:abc123",
    evidence_digest: `sha256:${"a".repeat(64)}`,
    observed_at: "2026-09-25T20:00:00.000Z",
    expires_at: "2026-09-26T20:00:00.000Z",
    detail: {
      repository_commit_sha: "a".repeat(40),
      repository_tree_sha: "b".repeat(40),
      reviewer_fact_id: "reviewer-fact:1",
    },
    incident_refs: [],
    recovery_refs: ["runbook:a01"],
  };
  const args = { scope: SCOPE, evidence, idempotency_key: "11111111-2222-4333-8444-555555555555" };
  const actor = { slug: "reviewer-a01" };
  assert.deepEqual(await tool.handler(client, actor, args), { id: "evidence-row-1", replayed: false });
  assert.equal(envelopes[0].actor, actor);
  assert.equal(envelopes[0].verb, "record-assurance-health-evidence");
  assert.equal(calls.length, 2);
  assert.match(calls[0].sql, /set_config\('carr\.acting_actor_slug',\$1::text,true\)/);
  assert.match(calls[1].sql, /ops\.record_assurance_health_evidence\(\$1::jsonb,\$2::jsonb,\$3::uuid\)/);
  // The acting slug is the authenticated actor's, bound as a parameter -- never
  // anything the caller put in args (subject_ref here is deliberately different).
  assert.deepEqual(calls[0].params, [actor.slug]);
  assert.notEqual(calls[0].params[0], evidence.subject_ref);
  assert.deepEqual(calls[1].params, [JSON.stringify(SCOPE), JSON.stringify(evidence), args.idempotency_key]);
});

test("V5-A01 M8: the evaluator slug is actor.slug for every actor, never a field of args", async () => {
  for (const slug of ["reviewer-a01", "dell", "sol-model-room"]) {
    const calls = [];
    const client = { query: async (sql, params = []) => {
      calls.push({ sql, params });
      return { rows: [{ receipt: { id: "row", replayed: false } }] };
    } };
    const tool = assuranceHealthStoreTools({ ToolError, withEnvelope: async (_c, _a, _v, _args, fn) => fn() })[
      "record-assurance-health-evidence"];
    const evidence = {
      layer: "execution_assessment", basis: "attempt_receipt_execution_evidence", status: "pass",
      subject_ref: "attempt:subject", evidence_ref: `exec:${slug}`, evidence_digest: `sha256:${"c".repeat(64)}`,
      observed_at: "2026-09-25T20:00:00.000Z", expires_at: "2026-09-26T20:00:00.000Z",
      detail: { attempt_id: "attempt:1", envelope_digest: "e", plan_hash: "p" },
      incident_refs: [], recovery_refs: [],
    };
    await tool.handler(client, { slug }, {
      scope: { workflow_key: "doctorcre.release", workflow_version: 7 },
      evidence, idempotency_key: "11111111-2222-4333-8444-555555555555",
    });
    assert.equal(calls.length, 2);
    assert.deepEqual(calls[0].params, [slug]);
  }
});

test("V5-A01: an actor without a slug cannot record evidence, and no query is issued", async () => {
  const calls = [];
  const client = { query: async (sql, params) => { calls.push({ sql, params }); return { rows: [] }; } };
  const tool = assuranceHealthStoreTools({ ToolError, withEnvelope: async (_c, _a, _v, _args, fn) => fn() })[
    "record-assurance-health-evidence"];
  for (const actor of [null, {}, { slug: "" }, { slug: 7 }]) {
    await assert.rejects(tool.handler(client, actor, {
      scope: { workflow_key: "doctorcre.release", workflow_version: 7 },
      evidence: { layer: "artifact_assessment", basis: "independent_artifact_review", subject_ref: "joe" },
      idempotency_key: "11111111-2222-4333-8444-555555555555",
    }), error => error.payload?.error === "assurance_health_actor_required");
  }
  assert.equal(calls.length, 0);
});

test("V5-A01: the read binds a scope without a Work Request as an explicit null parameter", async () => {
  const scope = { workflow_key: "doctorcre.release", workflow_version: 7 };
  const projection = unavailableTruthProjection(scope);
  const { calls, client, handler } = toolsWith(projection);
  await handler(client, { slug: "dell" }, { scope });
  assert.deepEqual(calls[0].params, ["doctorcre.release", 7, null]);
});

function unavailableTruthProjection(scope = SCOPE) {
  const projection = healthyProjection(scope);
  projection.state = "unknown";
  projection.green = false;
  projection.capability_stage = "unavailable";
  projection.workflow_truth = { available: false, source: "V5-F09 workflow census", reason: "unreadable" };
  projection.impact.withdrawn_stages = ["act", "draft", "read"];
  return projection;
}

test("V5-A01 B1: without workflow truth, six passing layers are accepted only as unknown / unavailable / not green", () => {
  const projection = unavailableTruthProjection();
  assert.equal(validateAssuranceHealthProjection(projection, SCOPE, ToolError).state, "unknown");
  for (const [field, value] of [["state", "healthy"], ["green", true], ["capability_stage", "act"],
    ["capability_stage", "read"], ["state", "not-yet-operational"], ["state", "degraded"]]) {
    const mutant = structuredClone(projection);
    mutant[field] = value;
    if (field === "state" && value === "healthy") mutant.green = true;
    assert.throws(() => validateAssuranceHealthProjection(mutant, SCOPE, ToolError),
      error => error.payload?.error === "assurance_health_projection_invalid", `${field}=${value}`);
  }
});

test("V5-A01 planted projection mutants are each killed by a guard", () => {
  const mutants = {
    schema_version: p => { p.schema_version = "assurance-health.v0"; },
    workflow_truth_missing: p => { delete p.workflow_truth; },
    workflow_truth_not_boolean: p => { p.workflow_truth.available = "yes"; },
    green_without_healthy: p => { p.state = "degraded"; },
    healthy_without_green: p => { p.green = false; },
    act_without_live: p => { p.workflow_truth.admissible_modes = ["shadow"]; },
    disabled_without_truth: p => { p.state = "disabled"; p.green = false; },
    owner_missing: p => { delete p.owner; },
    stage_outside_vocabulary: p => { p.capability_stage = "admin"; },
    evidence_state_outside_vocabulary: p => { p.evidence.artifact_assessment.state = "fine"; },
    passing_without_pass_status: p => { p.evidence.artifact_assessment.status = "fail"; },
    passing_with_bad_digest: p => { p.evidence.artifact_assessment.evidence_digest = "sha256:xyz"; },
    passing_without_expiry: p => { delete p.evidence.artifact_assessment.expires_at; },
    reused_identity: p => {
      p.evidence.execution_assessment.evidence_ref = p.evidence.artifact_assessment.evidence_ref;
      p.evidence.execution_assessment.evidence_digest = p.evidence.artifact_assessment.evidence_digest;
    },
    layer_label_swapped: p => { p.evidence.artifact_assessment.layer = "execution_assessment"; },
    extra_layer: p => { p.evidence.telemetry = { layer: "telemetry", state: "passing", scope: { ...SCOPE } }; },
    act_with_nonpassing_layer: p => { p.evidence.controller_assessment.state = "stale"; },
    impact_escapes_scope: p => { p.impact.scope_limited_to = { ...OTHER_SCOPE }; },
    impact_not_array: p => { p.impact.withdrawn_stages = "none"; },
    recovery_unknown_layer: p => { p.recovery.required_evidence = ["telemetry"]; },
    projection_scope_mismatch: p => { p.scope = { ...OTHER_SCOPE }; },
  };
  for (const [name, plant] of Object.entries(mutants)) {
    const mutant = healthyProjection();
    plant(mutant);
    assert.throws(() => validateAssuranceHealthProjection(mutant, SCOPE, ToolError),
      error => error.payload?.error === "assurance_health_projection_invalid", name);
  }
  const degraded = healthyProjection();
  degraded.state = "degraded"; degraded.green = false; degraded.capability_stage = "draft";
  degraded.evidence.activation_readback.state = "failed";
  degraded.evidence.activation_readback.status = "fail";
  degraded.impact.withdrawn_stages = ["act"];
  assert.throws(() => validateAssuranceHealthProjection(degraded, SCOPE, ToolError),
    error => error.payload?.error === "assurance_health_projection_invalid", "recovery omits a non-passing layer");
  degraded.recovery.required_evidence = ["activation_readback"];
  assert.equal(validateAssuranceHealthProjection(degraded, SCOPE, ToolError).state, "degraded");
  // Distinctness is enforced on every projection, not only a healthy one: two
  // passing layers sharing one receipt identity are refused even when degraded.
  const shared = structuredClone(degraded);
  shared.evidence.execution_assessment.evidence_ref = shared.evidence.artifact_assessment.evidence_ref;
  shared.evidence.execution_assessment.evidence_digest = shared.evidence.artifact_assessment.evidence_digest;
  assert.throws(() => validateAssuranceHealthProjection(shared, SCOPE, ToolError),
    error => error.payload?.error === "assurance_health_projection_invalid", "reused identity while degraded");
});
