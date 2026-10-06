import test from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError, executeRegisteredTool } from "../src/tools.js";
import { PROFILES, allowedIn, callTool } from "../src/mcp.js";

const JOE = { id: "10000000-0000-0000-0000-000000000002", slug: "joe", human: true, via: "test" };
const BOT = { ...JOE, human: false, slug: "codex" };
// A server-verified sponsored agent holding Joe's derived partner authority --
// the actor class the generic humanOnly dispatch gate (tools.js) ADMITS for
// most humanOnly verbs, and the one THIS verb's handler must refuse on its own.
const CLAUDE_FOR_JOE = { id: "10000000-0000-0000-0000-000000000011", slug: "claude", human: false,
  sponsoring_human_slug: "joe", native_agent_verified: true, via: "oauth-agent", client_id: "claude-client" };
const ARGS = { idempotency_key: "10000000-0000-0000-0000-000000000099", human_ref: "WR-000001",
  base_version: 1, answer_text: "Go with option B.", scope_confirmed: true };

async function rejected(fn) { try { await fn(); assert.fail("expected refusal"); } catch (e) { assert.ok(e instanceof ToolError); return e.payload; } }

// Mirrors ops.answer_work_request_for_joe (migration 0575): every refusal
// branch RAISES, it never returns an empty row set, so this fake raises real
// Error objects with the real exception text and the handler's own catch is
// what has to map them -- not a `!row` check that could never fire against
// the real function.
class JoeAnswerFake {
  constructor(opts = {}) {
    this.calls = []; this.toolCalls = new Map(); this.receipts = new Map();
    this.version = opts.version ?? 1;
    this.state = opts.state ?? "needs_joe";
    this.acceptanceCriteria = "acceptanceCriteria" in opts ? opts.acceptanceCriteria : ["AC-1"];
  }
  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim(); this.calls.push({ sql, params });
    if (sql.startsWith("select pg_advisory_xact_lock")) return { rows: [] };
    if (sql.startsWith("select request_hash, response")) { const row = this.toolCalls.get(params[0]); return { rows: row ? [row] : [] }; }
    if (sql.includes("answer_work_request_for_joe")) {
      const [humanRef, baseVersion, answerText, scopeConfirmed, evidenceRef, idempotencyKey] = params;
      const existing = this.receipts.get(idempotencyKey);
      if (existing) {
        if (existing.baseVersion !== baseVersion || existing.answerText !== answerText ||
            existing.scopeConfirmed !== scopeConfirmed || (existing.evidenceRef ?? null) !== (evidenceRef ?? null))
          throw new Error("idempotency key already names a different answer to Joe");
        return { rows: [{ id: existing.id, ref: humanRef, state: "triaged", version: existing.resultVersion,
          answer_text: existing.answerText, scope_confirmed: existing.scopeConfirmed,
          evidence_ref: existing.evidenceRef ?? null, acceptance_criteria_digest: existing.digest,
          answered_by_actor_slug: "joe", answered_at: "2026-09-24T00:00:00Z", replayed: true }] };
      }
      if (this.state !== "needs_joe" || this.version !== baseVersion)
        throw new Error("only the exact current needs_joe Work Request may be answered");
      if (!Array.isArray(this.acceptanceCriteria) || this.acceptanceCriteria.length === 0)
        throw new Error("acceptance_criteria_missing: this Work Request carries no acceptance criteria to revalidate");
      this.state = "triaged"; this.version += 1;
      const digest = "sha256:" + "a".repeat(64);
      const receipt = { id: "20000000-0000-0000-0000-000000000001", baseVersion, answerText, scopeConfirmed,
        evidenceRef: evidenceRef ?? null, resultVersion: this.version, digest };
      this.receipts.set(idempotencyKey, receipt);
      return { rows: [{ id: receipt.id, ref: humanRef, state: this.state, version: this.version,
        answer_text: answerText, scope_confirmed: scopeConfirmed, evidence_ref: evidenceRef ?? null,
        acceptance_criteria_digest: digest, answered_by_actor_slug: "joe", answered_at: "2026-09-24T00:00:00Z", replayed: false }] };
    }
    if (sql.startsWith("insert into event")) return { rows: [] };
    if (sql.startsWith("insert into tool_call")) { this.toolCalls.set(params[0], { request_hash: params[3], response: JSON.parse(params[4]),
        actor_id: params[2], organization_tenant_id: params[7] ?? null,
        application_session_id: params[12] ?? null }); return { rows: [] }; }
    throw new Error(`unexpected query: ${sql}`);
  }
}

test("answer-work-request-for-joe is a closed human-only authority-only versioned write", () => {
  const tool = TOOLS["answer-work-request-for-joe"];
  assert.equal(tool.write, true); assert.equal(tool.humanOnly, true); assert.equal(tool.authorityOnly, true);
  assert.equal(tool.inputSchema.additionalProperties, false);
  assert.deepEqual(tool.inputSchema.required, ["idempotency_key", "human_ref", "base_version", "answer_text", "scope_confirmed"]);
  assert.deepEqual(tool.inputSchema.properties.scope_confirmed.enum, [true]);
  for (const profile of ["capture", "hermes", "probe", "reviewer"]) assert.equal(allowedIn(profile, "answer-work-request-for-joe", tool), false, `${profile} must not receive the Joe answer`);
});

test("answer-work-request-for-joe performs only needs_joe-to-triaged and writes one audit event", async () => {
  const db = new JoeAnswerFake(); const out = await executeRegisteredTool(db, JOE, "answer-work-request-for-joe", structuredClone(ARGS));
  assert.deepEqual(out, { ok: true, human_ref: "WR-000001", state: "triaged", version: 2, answer_text: "Go with option B.",
    scope_confirmed: true, evidence_ref: null, acceptance_criteria_digest: `sha256:${"a".repeat(64)}`,
    answered_by_actor_slug: "joe", answered_at: "2026-09-24T00:00:00Z" });
  assert.equal(db.calls.filter(x => x.sql.includes("answer_work_request_for_joe")).length, 1);
  const event = db.calls.find(x => x.sql.startsWith("insert into event"));
  assert.equal(event.params[1], JOE.id, "audit actor is server-derived");
  const newValue = JSON.parse(event.params[7]);
  assert.equal(newValue.answer_text, "Go with option B.");
  assert.equal(newValue.scope_confirmed, true);
  assert.equal(JSON.parse(event.params[6]).state, "needs_joe");
});

test("answer-work-request-for-joe carries evidence_ref through to the audit event and the response", async () => {
  const db = new JoeAnswerFake();
  const out = await executeRegisteredTool(db, JOE, "answer-work-request-for-joe",
    { ...structuredClone(ARGS), evidence_ref: "doc-conversation:abc123#turn-4" });
  assert.equal(out.evidence_ref, "doc-conversation:abc123#turn-4");
  const event = db.calls.find(x => x.sql.startsWith("insert into event"));
  assert.equal(JSON.parse(event.params[7]).evidence_ref, "doc-conversation:abc123#turn-4");
});

test("answer-work-request-for-joe refuses a machine actor, and still refuses extras, stale versions, and same-key mutation", async () => {
  const db = new JoeAnswerFake();
  const machine = await rejected(() => executeRegisteredTool(db, BOT, "answer-work-request-for-joe", structuredClone(ARGS)));
  assert.equal(machine.error, "human_only_verb_requires_verified_partner");
  assert.equal(machine.verb, "answer-work-request-for-joe");
  assert.equal(machine.actor_class, "unsponsored_agent");
  assert.equal(db.calls.length, 0, "an agent must not reach the database on a human-only verb");
  for (const extra of [{ state: "ready" }, { executor: "codex" }, { approval: "yes" }]) {
    const db = new JoeAnswerFake(); const out = await rejected(() => executeRegisteredTool(db, JOE, "answer-work-request-for-joe", { ...ARGS, ...extra }));
    assert.ok(["caller_authority_field_forbidden", "invalid_answer_work_request_for_joe_fields", "unregistered_operation_fields"].includes(out.error)); assert.equal(db.calls.length, 0);
  }
  const stale = await rejected(() => executeRegisteredTool(new JoeAnswerFake({ version: 1 }), JOE, "answer-work-request-for-joe", { ...ARGS, base_version: 99 }));
  assert.equal(stale.error, "version_conflict");
  assert.equal(stale.human_ref, "WR-000001");
});

test("answer-work-request-for-joe refuses a sponsored partner-authority agent even though the dispatch gate admits the actor class", async () => {
  // The generic humanOnly gate (tools.js) admits CLAUDE_FOR_JOE for most
  // humanOnly verbs, because most of them record a partner's decision made
  // THROUGH a verified sponsored agent. This verb must still refuse it: the
  // answer has to be the human's own, not an agent's on his behalf.
  const db = new JoeAnswerFake();
  const out = await rejected(() => executeRegisteredTool(db, CLAUDE_FOR_JOE, "answer-work-request-for-joe", structuredClone(ARGS)));
  assert.equal(out.error, "human_only_verb_requires_direct_human_actor");
  assert.equal(out.verb, "answer-work-request-for-joe");
  assert.equal(db.calls.length, 0, "a sponsored agent must not reach the database on this verb");
});

test("answer-work-request-for-joe refuses a Work Request that is not in needs_joe", async () => {
  const db = new JoeAnswerFake({ state: "triaged" });
  const out = await rejected(() => executeRegisteredTool(db, JOE, "answer-work-request-for-joe", structuredClone(ARGS)));
  assert.equal(out.error, "version_conflict");
});

test("answer-work-request-for-joe refuses a Work Request with empty acceptance criteria", async () => {
  const db = new JoeAnswerFake({ acceptanceCriteria: [] });
  const out = await rejected(() => executeRegisteredTool(db, JOE, "answer-work-request-for-joe", structuredClone(ARGS)));
  assert.equal(out.error, "acceptance_criteria_missing");
  assert.equal(out.human_ref, "WR-000001");
});

test("answer-work-request-for-joe refuses scope_confirmed false with a real refusal payload", async () => {
  // The verb's own closed vocabulary (enum: [true]) is enforced generically
  // by assertDeclaredVocabularies before the handler ever runs.
  const out = await rejected(() => executeRegisteredTool(new JoeAnswerFake(), JOE, "answer-work-request-for-joe", { ...structuredClone(ARGS), scope_confirmed: false }));
  assert.equal(out.error, "value_not_in_declared_vocabulary");
  assert.equal(out.field, "scope_confirmed");
});

test("answer-work-request-for-joe refuses scope_confirmed omitted entirely with a real refusal payload", async () => {
  const args = structuredClone(ARGS); delete args.scope_confirmed;
  const out = await rejected(() => executeRegisteredTool(new JoeAnswerFake(), JOE, "answer-work-request-for-joe", args));
  assert.equal(out.error, "missing_required");
  assert.ok(out.missing.includes("scope_confirmed"));
});

test("answer-work-request-for-joe replay never makes a second transition", async () => {
  const db = new JoeAnswerFake(); await executeRegisteredTool(db, JOE, "answer-work-request-for-joe", structuredClone(ARGS));
  const out = await executeRegisteredTool(db, JOE, "answer-work-request-for-joe", structuredClone(ARGS));
  assert.equal(out.replayed, true); assert.equal(db.calls.filter(x => x.sql.includes("answer_work_request_for_joe")).length, 1);
});

test("answer-work-request-for-joe refuses the same idempotency key reused with a different answer_text", async () => {
  // withEnvelope's own key_reuse guard hashes the FULL args, including
  // answer_text, so a genuinely different second call under the same key
  // never even reaches ops.answer_work_request_for_joe.
  const db = new JoeAnswerFake(); await executeRegisteredTool(db, JOE, "answer-work-request-for-joe", structuredClone(ARGS));
  const out = await rejected(() => executeRegisteredTool(db, JOE, "answer-work-request-for-joe", { ...structuredClone(ARGS), answer_text: "Go with option C instead." }));
  assert.equal(out.error, "key_reuse");
  assert.equal(db.calls.filter(x => x.sql.includes("answer_work_request_for_joe")).length, 1, "the mismatched replay must not reach the function");
});

test("answer-work-request-for-joe replay key is bound to the authenticated human", async () => {
  const db = new JoeAnswerFake(); await executeRegisteredTool(db, JOE, "answer-work-request-for-joe", structuredClone(ARGS));
  const dell = { ...JOE, id: "10000000-0000-0000-0000-000000000003", slug: "dell" };
  const out = await rejected(() => executeRegisteredTool(db, dell, "answer-work-request-for-joe", structuredClone(ARGS)));
  assert.equal(out.error, "key_reuse");
  assert.equal(db.calls.filter(x => x.sql.includes("answer_work_request_for_joe")).length, 1);
});

test("answer-work-request-for-joe has no routine-writer fallback when the authority connection is absent", async () => {
  const out = await rejected(() => callTool({}, JOE, "answer-work-request-for-joe", structuredClone(ARGS), "full"));
  assert.equal(out.error, "authority_connection_unavailable");
});

test("answer-work-request-for-joe refuses an empty or oversize answer", async () => {
  const db1 = new JoeAnswerFake();
  const blank = await rejected(() => executeRegisteredTool(db1, JOE, "answer-work-request-for-joe", { ...ARGS, answer_text: "   " }));
  assert.equal(blank.error, "invalid_answer_work_request_for_joe");
  const db2 = new JoeAnswerFake();
  const long = await rejected(() => executeRegisteredTool(db2, JOE, "answer-work-request-for-joe", { ...ARGS, answer_text: "x".repeat(501) }));
  assert.equal(long.error, "invalid_answer_work_request_for_joe");
});

test("answer-work-request-for-joe refuses an oversize evidence_ref", async () => {
  const out = await rejected(() => executeRegisteredTool(new JoeAnswerFake(), JOE, "answer-work-request-for-joe",
    { ...structuredClone(ARGS), evidence_ref: "x".repeat(501) }));
  assert.equal(out.error, "invalid_answer_work_request_for_joe");
});
