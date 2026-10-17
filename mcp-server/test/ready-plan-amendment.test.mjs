import assert from "node:assert/strict";
import test from "node:test";

import { workRequestIntakeTools } from "../src/work-request-intake.js";

class ToolError extends Error { constructor(payload) { super(payload.error); this.payload = payload; } }
const ACTOR = { id: "10000000-0000-4000-8000-000000000001", slug: "codex", human: false };
const HASH = `sha256:${"a".repeat(64)}`;
const PROPOSAL = {
  idempotency_key: "10000000-0000-4000-8000-000000000002", human_ref: "WR-000125", base_version: 4,
  predecessor_plan_hash: HASH, scope_summary: "Replace the accepted plan only after its exact predecessor is bound.",
  runbook_ref: "doctrine:runbook#wr125-canonical-ownership-plan-lifecycle-recovery", dependency_refs: [],
  recovery_ref: "safe:wr125:rollback", observability_ref: "safe:wr125:readback",
  caps: { max_steps: 6, max_duration_minutes: 120 },
};

function tools(events = []) {
  return workRequestIntakeTools({
    ToolError,
    withEnvelope: async (_c, _actor, _verb, _args, fn) => fn(),
    writeEvent: async (_c, _actor, verb, subjectType, subjectId, fields) => events.push({ verb, subjectType, subjectId, fields }),
  });
}

test("same-WR plan successor proposal is closed, predecessor-bound, and serialized", async () => {
  assert.equal(tools()["propose-ready-plan-amendment"].inputSchema.required.includes("heavy_build"), false);
  const events = [];
  const db = { calls: [], query: async (sql, params = []) => {
    db.calls.push({ sql, params });
    if (sql.includes("propose_ready_plan_amendment")) return { rows: [{ amendment: {
      ok: true, replayed: false, work_request: { id: "20000000-0000-4000-8000-000000000001", ref: "WR-000125", state: "ready", version: 5 },
      plan: { id: "30000000-0000-4000-8000-000000000001", ref: "PLAN-successor", hash: `sha256:${"b".repeat(64)}`, version: 2, predecessor_ref: "PLAN-old", predecessor_hash: HASH },
    } }] };
    throw new Error(`unexpected query: ${sql}`);
  } };
  const out = await tools(events)["propose-ready-plan-amendment"].handler(db, ACTOR, structuredClone(PROPOSAL));
  assert.equal(out.ok, true);
  assert.equal(out.plan.predecessor_hash, HASH);
  assert.equal(db.calls[0].params[2], HASH);
  assert.equal(db.calls[0].params.length, 11);
  assert.equal(db.calls[0].params[9], ACTOR.id);
  assert.equal("build_admission" in out, false);
  assert.equal(events[0].verb, "propose-ready-plan-amendment");

  await assert.rejects(() => tools()["propose-ready-plan-amendment"].handler({ query: async () => { throw new Error("must not query"); } }, ACTOR,
    { ...PROPOSAL, predecessor_plan_hash: "forged" }), error => error.payload?.error === "invalid_ready_plan_amendment_predecessor");
  await assert.rejects(() => tools()["propose-ready-plan-amendment"].handler({ query: async () => { throw new Error("must not query"); } }, ACTOR,
    { ...PROPOSAL, heavy_build: {} }), error => error.payload?.error === "invalid_ready_plan_amendment_fields");
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
