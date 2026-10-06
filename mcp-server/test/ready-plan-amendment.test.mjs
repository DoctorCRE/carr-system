import assert from "node:assert/strict";
import test from "node:test";

import { workRequestIntakeTools } from "../src/work-request-intake.js";

class ToolError extends Error { constructor(payload) { super(payload.error); this.payload = payload; } }
const ACTOR = { id: "10000000-0000-4000-8000-000000000001", slug: "codex", human: false };
const HASH = `sha256:${"a".repeat(64)}`;
const EVIDENCE = (suffix, source_class) => ({ source_ref: `safe:wr125:${suffix}`, source_class,
  locator: `https://example.com/${suffix}`, observed_at: "2026-09-20T22:00:00.000Z",
  content_digest: `sha256:${"c".repeat(64)}`, finding: `Evidence for the successor heavy-build contract's ${source_class} requirement.` });
const HEAVY_BUILD = {
  builder_session_ref: "session:builder:wr125-successor-plan",
  research_manifest: {
    primary_sources: [EVIDENCE("primary", "primary_source")],
    maintained_repositories: [EVIDENCE("repository-one", "maintained_repository"), EVIDENCE("repository-two", "maintained_repository")],
    practitioner_evidence: [EVIDENCE("practitioner", "practitioner_evidence")],
    current_baseline: [EVIDENCE("baseline", "current_baseline")],
    failure_modes: [EVIDENCE("failure", "failure_mode")],
    unresolved_contradictions: [], conclusion: "The successor is supported by current evidence and retains an explicit falsifier.",
  },
  master_plan: {
    product_goal: "Safely replace the accepted plan with a fully reviewed successor contract.",
    non_goals: ["Do not mutate the predecessor plan in place."],
    architecture: ["Immutable successor row", "Build-admission receipt", "Independent review receipt"],
    authority_boundaries: ["The database derives admission and a separate reviewer provides the passing review."],
    dependency_dag: [{ step_ref: "step:proposal", depends_on: [] }, { step_ref: "step:review", depends_on: ["step:proposal"] }],
    planned_checks: [{ artifact: "successor review receipt", comparator: "exact plan and admission hashes", failure_condition: "hash mismatch or missing fresh review" }],
    baseline_comparison: "Compare the successor against its immutable predecessor and reject unbound replacement attempts.",
    release_strategy: "Keep human acceptance as the only state-changing ready-plan action.",
    rollback_strategy: "Retain the predecessor as effective until exact successor acceptance succeeds.",
    observability_strategy: "Expose exact admission and review hashes in successor lifecycle readbacks.",
    fully_shipped_definition: "A successor is accepted only after its own admission and fresh passing review are recorded.",
    prerequisite_policy: "Record dependencies in the successor instead of broadening execution authority.",
  },
};
const PROPOSAL = {
  idempotency_key: "10000000-0000-4000-8000-000000000002", human_ref: "WR-000125", base_version: 4,
  predecessor_plan_hash: HASH, scope_summary: "Replace the accepted plan only after its exact predecessor is bound.",
  runbook_ref: "doctrine:runbook#wr125-canonical-ownership-plan-lifecycle-recovery", dependency_refs: [],
  recovery_ref: "safe:wr125:rollback", observability_ref: "safe:wr125:readback",
  caps: { max_steps: 6, max_duration_minutes: 120 }, heavy_build: HEAVY_BUILD,
};

function tools(events = []) {
  return workRequestIntakeTools({
    ToolError,
    withEnvelope: async (_c, _actor, _verb, _args, fn) => fn(),
    writeEvent: async (_c, _actor, verb, subjectType, subjectId, fields) => events.push({ verb, subjectType, subjectId, fields }),
  });
}

test("same-WR plan successor proposal is closed, predecessor-bound, and serialized", async () => {
  const events = [];
  const db = { calls: [], query: async (sql, params = []) => {
    db.calls.push({ sql, params });
    if (sql.includes("propose_ready_plan_amendment")) return { rows: [{ amendment: {
      ok: true, replayed: false, work_request: { id: "20000000-0000-4000-8000-000000000001", ref: "WR-000125", state: "ready", version: 5 },
      plan: { id: "30000000-0000-4000-8000-000000000001", ref: "PLAN-successor", hash: `sha256:${"b".repeat(64)}`, version: 2, predecessor_ref: "PLAN-old", predecessor_hash: HASH },
      build_admission: { tier: "heavy", reasons: ["successor"], admission_ref: "HBA-successor", admission_hash: `sha256:${"d".repeat(64)}`, builder_session_ref: HEAVY_BUILD.builder_session_ref },
    } }] };
    throw new Error(`unexpected query: ${sql}`);
  } };
  const out = await tools(events)["propose-ready-plan-amendment"].handler(db, ACTOR, structuredClone(PROPOSAL));
  assert.equal(out.ok, true);
  assert.equal(out.plan.predecessor_hash, HASH);
  assert.equal(db.calls[0].params[2], HASH);
  assert.deepEqual(JSON.parse(db.calls[0].params[9]), HEAVY_BUILD);
  assert.equal(db.calls[0].params[10], ACTOR.id);
  assert.equal(out.build_admission.admission_ref, "HBA-successor");
  assert.equal(events[0].verb, "propose-ready-plan-amendment");

  await assert.rejects(() => tools()["propose-ready-plan-amendment"].handler({ query: async () => { throw new Error("must not query"); } }, ACTOR,
    { ...PROPOSAL, predecessor_plan_hash: "forged" }), error => error.payload?.error === "invalid_ready_plan_amendment_predecessor");
  await assert.rejects(() => tools()["propose-ready-plan-amendment"].handler({ query: async () => { throw new Error("must not query"); } }, ACTOR,
    (() => { const proposal = structuredClone(PROPOSAL); delete proposal.heavy_build; return proposal; })()),
  error => error.payload?.error === "heavy_build_admission_required");
});

test("acceptance requires the exact successor hash and never treats acknowledgement as authority", async () => {
  const events = [];
  const db = { query: async (sql, params = []) => {
    if (sql.includes("accept_ready_plan_amendment")) return { rows: [{ amendment: {
      ok: true, work_request: { id: "20000000-0000-4000-8000-000000000001", ref: "WR-000125", state: "ready", version: 6 },
      prior_plan: { ref: "PLAN-old", hash: HASH }, successor_plan: { ref: "PLAN-successor", hash: `sha256:${"b".repeat(64)}` }, notice_count: 2,
    } }] };
    if (sql.includes("acknowledge_ready_plan_amendment")) return { rows: [{ acknowledgement: {
      ok: true, notice_id: Number(params[0]), work_request_ref: "WR-000125", plan_ref: "PLAN-successor", acknowledged_at: "2026-09-20T22:00:00Z", replayed: false,
    } }] };
    if (sql.includes("ready-plan-amendment-ack-subject")) return { rows: [{ id: "20000000-0000-4000-8000-000000000001" }] };
    throw new Error(`unexpected query: ${sql}`);
  } };
  const accepted = await tools(events)["accept-ready-plan-amendment"].handler(db, ACTOR, {
    idempotency_key: "10000000-0000-4000-8000-000000000003", human_ref: "WR-000125", base_version: 5, plan_hash: `sha256:${"b".repeat(64)}`,
  });
  assert.equal(accepted.successor_plan.ref, "PLAN-successor");
  const acknowledgement = await tools(events)["acknowledge-ready-plan-amendment"].handler(db, ACTOR,
    { idempotency_key: "10000000-0000-4000-8000-000000000004", notice_id: 42 });
  assert.equal(acknowledgement.notice_id, 42);
  assert.equal(events.at(-1).fields.new.plan_ref, "PLAN-successor");
  assert.equal("authority" in acknowledgement, false);
});

test("the existing heavy review seam binds the successor admission and refuses its builder session", async () => {
  const successorHash = `sha256:${"b".repeat(64)}`;
  const admissionHash = `sha256:${"d".repeat(64)}`;
  const review = {
    idempotency_key: "10000000-0000-4000-8000-000000000005", human_ref: "WR-000125",
    plan_hash: successorHash, admission_hash: admissionHash, verdict: "pass",
    reviewer_session_ref: "session:reviewer:wr125-successor", review_summary: "Fresh review checked the exact successor heavy-build contract and immutable admission.",
    evidence_refs: ["safe:wr125:successor-review"], gaps: [],
  };
  const db = { calls: [], query: async (sql, params = []) => {
    db.calls.push({ sql, params });
    if (sql.includes("sourced_heavy_build_review_target")) return { rows: [{
      work_request_id: "20000000-0000-4000-8000-000000000001", ref: "WR-000125", builder_session_ref: HEAVY_BUILD.builder_session_ref,
    }] };
    if (sql.includes("review_sourced_heavy_build_plan")) return { rows: [{
      work_request_id: "20000000-0000-4000-8000-000000000001", ref: "WR-000125", review_ref: "HBR-successor", review_hash: `sha256:${"e".repeat(64)}`,
      admission_ref: "HBA-successor", admission_hash: admissionHash, verdict: params[4], reviewer_session_ref: params[5],
    }] };
    throw new Error(`unexpected query: ${sql}`);
  } };
  const out = await tools()["review-heavy-build-plan"].handler(db, ACTOR, review);
  assert.equal(out.status, "ready_for_human_plan_acceptance");
  assert.deepEqual(db.calls[0].params, ["WR-000125", successorHash, admissionHash]);

  await assert.rejects(() => tools()["review-heavy-build-plan"].handler(db, ACTOR,
    { ...review, idempotency_key: "10000000-0000-4000-8000-000000000006", reviewer_session_ref: HEAVY_BUILD.builder_session_ref }),
  error => error.payload?.error === "heavy_build_review_context_not_fresh");
});

test("effective-plan and actor-owned discovery are bounded readbacks", async () => {
  assert.equal(tools()["effective-ready-plan"].writerConnection, true,
    "tenant-scoped effective-plan reads require the actor-context transaction path");
  const db = { query: async (sql, params = []) => {
    if (sql.includes("effective_ready_plan")) return { rows: [{ plan: {
      ok: true, work_request: { ref: "WR-000125" }, current_plan: { ref: "PLAN-successor" }, lineage: [{ ref: "PLAN-old" }, { ref: "PLAN-successor" }],
    } }] };
    if (sql.includes("discover_ready_plan_amendments")) return { rows: [{ discovery: {
      ok: true, actor: "codex", items: [], next_after_notice_id: null, has_more: false,
    } }] };
    throw new Error(`unexpected query: ${sql} / ${params}`);
  } };
  assert.equal((await tools()["effective-ready-plan"].handler(db, ACTOR, { work_request: "WR-000125" })).current_plan.ref, "PLAN-successor");
  const discovered = await tools()["ready-plan-amendment-discovery"].handler(db, ACTOR, { after_notice_id: 0, limit: 25 });
  assert.equal(discovered.actor, "codex");
  assert.equal(discovered.has_more, false);
});
