import assert from "node:assert/strict";
import test from "node:test";

import { TOOLS, ToolError, executeRegisteredTool } from "../src/tools.js";

const JOE = { id: "10000000-0000-0000-0000-000000000002", slug: "joe", display: "Joe", human: true, via: "test" };
const AGENT = { id: "10000000-0000-0000-0000-000000000099", slug: "codex", display: "Codex", human: false, via: "test" };
const BATCH_ID = "20000000-0000-0000-0000-000000000001";
const REGISTRY_ID = "30000000-0000-0000-0000-000000000001";
const DIGEST = "a".repeat(64);

class AuthorityDb {
  constructor() { this.calls = []; }
  async query(sql, params = []) {
    this.calls.push({ sql, params });
    if (sql.startsWith("select request_hash, response")) return { rows: [] };
    if (sql.includes("ops.decide_guidance_import_batch")) return { rows: [{ id: "decision-event" }] };
    if (sql.includes("ops.deactivate_guidance_registry")) return { rows: [{ id: "deactivation-event" }] };
    if (sql.startsWith("insert into event")) return { rows: [] };
    if (sql.startsWith("insert into tool_call")) return { rows: [] };
    throw new Error(`unexpected query: ${sql}`);
  }
}

async function rejected(fn) {
  try {
    await fn();
    assert.fail("expected ToolError");
  } catch (error) {
    assert.ok(error instanceof ToolError);
    return error.payload;
  }
}

test("guidance import decision and registry deactivation are human authority operations", () => {
  // humanOnly LABEL RETIRED, authorityOnly UNCHANGED (WR-000019 slice S1,
  // 2026-08-27) — see control-plane-authority-boundary.test.mjs for the same
  // retirement note.
  for (const name of ["decide-guidance-import-batch", "deactivate-guidance-registry"]) {
    assert.equal(TOOLS[name].write, true, name);
    assert.equal(TOOLS[name].humanOnly, undefined, name);
    assert.equal(TOOLS[name].authorityOnly, true, name);
    assert.ok(TOOLS[name].inputSchema.required.includes("idempotency_key"), name);
    assert.ok(TOOLS[name].inputSchema.required.includes("manifest_digest"), name);
    assert.ok(TOOLS[name].inputSchema.required.includes("reason"), name);
  }
});

test("a non-human actor cannot decide or deactivate the typed guidance registry", async () => {
  for (const [name, args] of [
    ["decide-guidance-import-batch", { idempotency_key: "decision-denied", batch_id: BATCH_ID, manifest_digest: DIGEST, reason: "fixture" }],
    ["deactivate-guidance-registry", { idempotency_key: "deactivation-denied", registry_id: REGISTRY_ID, manifest_digest: DIGEST, reason: "fixture" }],
  ]) {
    const db = new AuthorityDb();
    // INVERTED (Joe's ruling 2026-08-26, decision dc57f62d): authority no
    // longer refuses these, so the agent must now REACH the handler. Whatever
    // stops it after that is fixture shape, never human_only.
    let out;
    try { await executeRegisteredTool(db, AGENT, name, args); }
    catch (error) { out = error.payload; }
    assert.notEqual(out?.error, "human_only", name);
  }
});

test("authority verbs bind exactly the batch or registry and reviewed digest", async () => {
  const decisionDb = new AuthorityDb();
  const decision = await executeRegisteredTool(decisionDb, JOE, "decide-guidance-import-batch", {
    idempotency_key: "decision-allowed", batch_id: BATCH_ID, manifest_digest: DIGEST, reason: "reviewed manifest approved",
  });
  assert.deepEqual(decision, { ok: true, batch_id: BATCH_ID, decision_event_id: "decision-event", manifest_digest: DIGEST, state: "active" });
  const decisionCall = decisionDb.calls.find(call => call.sql.includes("ops.decide_guidance_import_batch"));
  assert.deepEqual(decisionCall.params, [BATCH_ID, DIGEST, "active", "decision-allowed", "reviewed manifest approved"]);

  const deactivateDb = new AuthorityDb();
  const deactivation = await executeRegisteredTool(deactivateDb, JOE, "deactivate-guidance-registry", {
    idempotency_key: "deactivation-allowed", registry_id: REGISTRY_ID, manifest_digest: DIGEST, reason: "fixture rollback",
  });
  assert.deepEqual(deactivation, { ok: true, registry_id: REGISTRY_ID, registry_event_id: "deactivation-event", manifest_digest: DIGEST, state: "inactive" });
  const deactivationCall = deactivateDb.calls.find(call => call.sql.includes("ops.deactivate_guidance_registry"));
  assert.deepEqual(deactivationCall.params, [REGISTRY_ID, DIGEST, "deactivation-allowed", "fixture rollback"]);
});
