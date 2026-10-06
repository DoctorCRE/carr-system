import test from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError, executeRegisteredTool } from "../src/tools.js";
import { PROFILES, allowedIn, callTool } from "../src/mcp.js";

const JOE = { id: "10000000-0000-0000-0000-000000000002", slug: "joe", human: true, via: "test" };
const BOT = { ...JOE, human: false, slug: "codex" };
const ARGS = { idempotency_key: "10000000-0000-0000-0000-000000000099", human_ref: "WR-000001", base_version: 1, classification: "operational" };

async function rejected(fn) { try { await fn(); assert.fail("expected refusal"); } catch (e) { assert.ok(e instanceof ToolError); return e.payload; } }

class TriageFake {
  constructor() { this.calls = []; this.toolCalls = new Map(); this.version = 1; this.state = "captured"; }
  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim(); this.calls.push({ sql, params });
    if (sql.startsWith("select pg_advisory_xact_lock")) return { rows: [] };
    if (sql.startsWith("select request_hash, response")) { const row = this.toolCalls.get(params[0]); return { rows: row ? [row] : [] }; }
    if (sql.includes("triage_sourced_work_request")) {
      if (params.includes(99)) return { rows: [] };
      this.state = "triaged"; this.version += 1;
      return { rows: [{ ref: "WR-000001", state: this.state, version: this.version, classification: "operational",
        triaged_by_actor_slug: "joe", triaged_at: "2026-08-16T00:00:00Z" }] };
    }
    if (sql.startsWith("insert into event")) return { rows: [] };
    if (sql.startsWith("insert into tool_call")) { this.toolCalls.set(params[0], { request_hash: params[3], response: JSON.parse(params[4]),
        actor_id: params[2], organization_tenant_id: params[7] ?? null,
        application_session_id: params[12] ?? null }); return { rows: [] }; }
    throw new Error(`unexpected query: ${sql}`);
  }
}

test("review-and-triage is a closed human-only authority-only versioned write", () => {
  const tool = TOOLS["review-and-triage"];
  assert.equal(tool.write, true); assert.equal(tool.humanOnly, true); assert.equal(tool.authorityOnly, true);
  assert.equal(tool.inputSchema.additionalProperties, false);
  assert.deepEqual(tool.inputSchema.required, ["idempotency_key", "human_ref", "base_version", "classification"]);
  for (const profile of ["capture", "hermes", "probe", "reviewer"]) assert.equal(allowedIn(profile, "review-and-triage", tool), false, `${profile} must not receive triage`);
});

test("review-and-triage performs only captured-to-triaged and writes one audit event", async () => {
  const db = new TriageFake(); const out = await executeRegisteredTool(db, JOE, "review-and-triage", structuredClone(ARGS));
  assert.deepEqual(out, { ok: true, human_ref: "WR-000001", state: "triaged", version: 2, classification: "operational",
    triaged_by_actor_slug: "joe", triaged_at: "2026-08-16T00:00:00Z" });
  assert.equal(db.calls.filter(x => x.sql.includes("triage_sourced_work_request")).length, 1);
  const event = db.calls.find(x => x.sql.startsWith("insert into event"));
  assert.equal(event.params[1], JOE.id, "audit actor is server-derived");
  assert.equal(JSON.parse(event.params[7]).classification, "operational");
});

test("review-and-triage refuses a machine actor, and still refuses extras, stale versions, and same-key mutation", async () => {
  // RE-INVERTED 2026-09-11 (WR-000021). This assertion has now been flipped
  // twice, and that is the point of it: on 2026-08-26 Joe's ruling dc57f62d
  // retired the humanOnly refusal and the assertion became "admits", written
  // that way rather than deleted so that restoring the gate would fail here
  // loudly instead of passing quietly. Restoring the gate is exactly what
  // happened — the release review of 5b5f5ff8 found that a sponsored agent
  // could accept the DoctorCRE v5 constitution through its sponsor's authority
  // connection and the ledger would name the sponsor — so it fires, and it is
  // flipped back rather than deleted for the same reason as before.
  //
  // WHAT THE 2026-08-26 RULING KEEPS: it was about a verb answering differently
  // through two doors, and partner-authority.js still fixes that. Nothing here
  // narrows authorityOnly, and the 220 verbs without the flag are untouched.
  // Everything else this test pins is unchanged: the caller-authority,
  // field-validation and optimistic-concurrency refusals are NOT authority
  // checks and must keep firing for every actor.
  const db = new TriageFake();
  const machine = await rejected(() => executeRegisteredTool(db, BOT, "review-and-triage", structuredClone(ARGS)));
  assert.equal(machine.error, "human_only_verb_requires_verified_partner");
  assert.equal(machine.verb, "review-and-triage");
  assert.equal(machine.actor_class, "unsponsored_agent");
  assert.equal(db.calls.length, 0, "an agent must not reach the database on a human-only verb");
  for (const extra of [{ state: "ready" }, { executor: "codex" }, { approval: "yes" }]) {
    const db = new TriageFake(); const out = await rejected(() => executeRegisteredTool(db, JOE, "review-and-triage", { ...ARGS, ...extra }));
    assert.ok(["caller_authority_field_forbidden", "invalid_triage_fields", "unregistered_operation_fields"].includes(out.error)); assert.equal(db.calls.length, 0);
  }
  const stale = await rejected(() => executeRegisteredTool(new TriageFake(), JOE, "review-and-triage", { ...ARGS, base_version: 99 }));
  assert.equal(stale.error, "version_conflict");
});

test("review-and-triage replay never makes a second transition", async () => {
  const db = new TriageFake(); await executeRegisteredTool(db, JOE, "review-and-triage", structuredClone(ARGS));
  const out = await executeRegisteredTool(db, JOE, "review-and-triage", structuredClone(ARGS));
  assert.equal(out.replayed, true); assert.equal(db.calls.filter(x => x.sql.includes("triage_sourced_work_request")).length, 1);
});

test("review-and-triage replay key is bound to the authenticated human", async () => {
  const db = new TriageFake(); await executeRegisteredTool(db, JOE, "review-and-triage", structuredClone(ARGS));
  const dell = { ...JOE, id: "10000000-0000-0000-0000-000000000003", slug: "dell" };
  const out = await rejected(() => executeRegisteredTool(db, dell, "review-and-triage", structuredClone(ARGS)));
  assert.equal(out.error, "key_reuse");
  assert.equal(db.calls.filter(x => x.sql.includes("triage_sourced_work_request")).length, 1);
});

test("review-and-triage has no routine-writer fallback when the authority connection is absent", async () => {
  const out = await rejected(() => callTool({}, JOE, "review-and-triage", structuredClone(ARGS), "full"));
  assert.equal(out.error, "authority_connection_unavailable");
});
