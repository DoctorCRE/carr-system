import test from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError, executeRegisteredTool } from "../src/tools.js";
import { allowedIn, callTool } from "../src/mcp.js";

const JOE = { id: "10000000-0000-0000-0000-000000000002", slug: "joe", human: true, via: "test" };
const BOT = { ...JOE, human: false, slug: "codex" };
const PLAN_HASH = `sha256:${"a".repeat(64)}`;
const PERSISTED_PLAN_HASH = `sha256:${"d".repeat(64)}`;
const FEEDBACK_HASH = `sha256:${"b".repeat(64)}`;
const PROPOSE = {
  idempotency_key: "10000000-0000-0000-0000-000000000099", human_ref: "WR-000001", base_version: 3, plan_hash: PLAN_HASH,
  criterion_results: [{ id: "READY-PLAN", result: "met" }, { id: "ADOPTION", result: "not_observed" }],
  evidence_refs: ["safe:observation:run-1"], blocker_code: "evidence_missing", result_summary: "One criterion is not yet observed.",
  observed_minutes: 12, interaction_surface: "codex", heavy_session_used: true, manual_context_transfers: 1,
};
const ACCEPT = { idempotency_key: "20000000-0000-0000-0000-000000000099", human_ref: "WR-000001", base_version: 3, feedback_hash: FEEDBACK_HASH };
const acceptedA = {
  feedback_ref: "FEEDBACK-000001", feedback_hash: FEEDBACK_HASH, outcome: "inconclusive",
  criterion_results: PROPOSE.criterion_results, evidence_refs: PROPOSE.evidence_refs, blocker_code: "evidence_missing",
  result_summary: PROPOSE.result_summary, observed_minutes: 12, interaction_surface: "codex", heavy_session_used: true,
  manual_context_transfers: 1, accepted_by_actor_slug: "joe", accepted_at: "2026-08-16T00:00:00Z",
};
const acceptedB = { ...acceptedA, feedback_ref: "FEEDBACK-000002", feedback_hash: `sha256:${"c".repeat(64)}`,
  outcome: "criteria_met", criterion_results: [{ id: "READY-PLAN", result: "met" }, { id: "ADOPTION", result: "met" }],
  blocker_code: "none", result_summary: "Both criteria were observed.", observed_minutes: 8,
  interaction_surface: "workspace", heavy_session_used: false, manual_context_transfers: 0, accepted_at: "2026-08-16T01:00:00Z" };

async function refused(fn) { try { await fn(); assert.fail("expected refusal"); } catch (error) { assert.ok(error instanceof ToolError); return error.payload; } }

class OutcomeFake {
  constructor() { this.calls = []; this.toolCalls = new Map(); }
  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim(); this.calls.push({ sql, params });
    if (sql.startsWith("select pg_advisory_xact_lock")) return { rows: [] };
    if (sql.startsWith("select request_hash, response")) { const row = this.toolCalls.get(params[0]); return { rows: row ? [row] : [] }; }
    if (sql.includes("propose_sourced_work_request_outcome_feedback")) return { rows: [{
      feedback_id: "30000000-0000-0000-0000-000000000001", feedback_ref: acceptedA.feedback_ref, feedback_hash: FEEDBACK_HASH,
      work_request_id: "40000000-0000-0000-0000-000000000001", ref: "WR-000001", state: "ready", version: 3,
      plan_ref: "PLAN-000001", plan_hash: PERSISTED_PLAN_HASH, outcome: "inconclusive", criterion_results: PROPOSE.criterion_results, evidence_refs: PROPOSE.evidence_refs,
      blocker_code: PROPOSE.blocker_code, result_summary: PROPOSE.result_summary, observed_minutes: PROPOSE.observed_minutes,
      interaction_surface: PROPOSE.interaction_surface, heavy_session_used: PROPOSE.heavy_session_used, manual_context_transfers: PROPOSE.manual_context_transfers,
    }] };
    if (sql.includes("accept_sourced_work_request_outcome_feedback")) return { rows: [{
      work_request_id: "40000000-0000-0000-0000-000000000001", ref: "WR-000001", state: "ready", version: 3,
      feedback_id: "30000000-0000-0000-0000-000000000001", feedback_ref: acceptedA.feedback_ref, feedback_hash: FEEDBACK_HASH,
      plan_ref: "PLAN-000001", plan_hash: PERSISTED_PLAN_HASH, outcome: "inconclusive", criterion_results: PROPOSE.criterion_results,
      evidence_refs: PROPOSE.evidence_refs, blocker_code: acceptedA.blocker_code, result_summary: acceptedA.result_summary,
      observed_minutes: PROPOSE.observed_minutes, interaction_surface: PROPOSE.interaction_surface, heavy_session_used: PROPOSE.heavy_session_used,
      manual_context_transfers: PROPOSE.manual_context_transfers, accepted_by_actor_slug: "joe", accepted_at: acceptedA.accepted_at,
    }] };
    if (sql.startsWith("insert into event")) return { rows: [] };
    if (sql.startsWith("insert into tool_call")) { this.toolCalls.set(params[0], { request_hash: params[3], response: JSON.parse(params[4]),
        actor_id: params[2], organization_tenant_id: params[7] ?? null,
        application_session_id: params[12] ?? null }); return { rows: [] }; }
    throw new Error(`unexpected query: ${sql}`);
  }
}

class CardFake {
  constructor(mode = "a") { this.mode = mode; }
  async query(text) {
    if (String(text).includes("pending_sourced_work_request_outcome_feedback")) return { rows: this.mode === "pending" ? [{
      ...acceptedB, outcome: acceptedB.outcome, proposed_at: "2026-08-16T02:00:00Z",
    }] : [] };
    // The card also asks who actually performed each authority act, joining the
    // receipts to public.tool_call by idempotency key. No acts in this fixture.
    if (String(text).includes("acting-identity")) return { rows: [] };
    if (!String(text).includes("work_request_card")) throw new Error(`unexpected query: ${text}`);
    const history = this.mode === "b" ? [acceptedA, acceptedB] : [acceptedA];
    return { rows: [{ ref: "WR-000001", title: "Sourced routine", desired_outcome: "Routine is usable", acceptance_criteria: [],
      state: "ready", origin_ref: "doctrine:runbook#safe-plan", source_current: true, source_provenance: {},
      triage_classification: "operational", triaged_by_actor_slug: "joe", triaged_at: acceptedA.accepted_at,
      plan_ref: "PLAN-000001", plan_hash: PLAN_HASH, outcome_feedback: history.at(-1), outcome_feedback_history: history,
      accepted_feedback_count: history.length, shape_disposition: "not_required", shape_fixed_surface_ref: "sourced-plan:PLAN-000001" }] };
  }
}

test("outcome feedback schemas are closed; routine writers may propose but only human authority may accept", () => {
  assert.equal(TOOLS["propose-outcome-feedback"].inputSchema.additionalProperties, false);
  assert.equal(TOOLS["accept-outcome-feedback"].inputSchema.additionalProperties, false);
  assert.equal(TOOLS["accept-outcome-feedback"].humanOnly, true);
  assert.equal(TOOLS["accept-outcome-feedback"].authorityOnly, true);
  assert.equal(allowedIn("capture", "propose-outcome-feedback", TOOLS["propose-outcome-feedback"]), true);
  assert.equal(allowedIn("capture", "accept-outcome-feedback", TOOLS["accept-outcome-feedback"]), false);
});

test("proposal is explicit pending feedback, not a self-attested outcome", async () => {
  const db = new OutcomeFake(); const out = await executeRegisteredTool(db, JOE, "propose-outcome-feedback", structuredClone(PROPOSE));
  assert.deepEqual(out, { ok: true, human_ref: "WR-000001", state: "ready", version: 3, plan_ref: "PLAN-000001", plan_hash: PERSISTED_PLAN_HASH,
    feedback_ref: acceptedA.feedback_ref, feedback_hash: FEEDBACK_HASH, status: "pending_human_acceptance", proposed_outcome: "inconclusive",
    criterion_results: PROPOSE.criterion_results, evidence_refs: PROPOSE.evidence_refs, blocker_code: "evidence_missing", result_summary: PROPOSE.result_summary,
    observed_minutes: 12, interaction_surface: "codex", heavy_session_used: true, manual_context_transfers: 1 });
  assert.equal(Object.hasOwn(out, "raw_payload"), false); assert.equal(Object.hasOwn(out, "prompt"), false);
  const call = db.calls.find(x => x.sql.includes("propose_sourced_work_request_outcome_feedback"));
  assert.equal(call.params.length, 12); assert.equal(call.params[5], "evidence_missing"); assert.equal(call.params[7], 12);
  const event = db.calls.find(x => x.sql.startsWith("insert into event"));
  assert.equal(event.params[5], "outcome_feedback_proposed");
});

test("human acceptance reads back every accepted material field but never transitions or completes the Work Request", async () => {
  const db = new OutcomeFake(); const out = await executeRegisteredTool(db, JOE, "accept-outcome-feedback", structuredClone(ACCEPT));
  assert.deepEqual(out, { ok: true, human_ref: "WR-000001", state: "ready", version: 3, plan_ref: "PLAN-000001", plan_hash: PERSISTED_PLAN_HASH,
    feedback_ref: acceptedA.feedback_ref, feedback_hash: FEEDBACK_HASH, outcome: "inconclusive", criterion_results: PROPOSE.criterion_results,
    evidence_refs: PROPOSE.evidence_refs, blocker_code: "evidence_missing", result_summary: PROPOSE.result_summary, observed_minutes: 12,
    interaction_surface: "codex", heavy_session_used: true, manual_context_transfers: 1, accepted_by_actor_slug: "joe", accepted_at: acceptedA.accepted_at });
  const event = db.calls.find(x => x.sql.startsWith("insert into event"));
  assert.equal(event.params[5], "outcome_feedback_accepted"); assert.equal(JSON.parse(event.params[7]).state, undefined);
});

test("proposal and acceptance replays bind the authenticated actor and cannot repeat writes", async () => {
  const proposal = new OutcomeFake(); await executeRegisteredTool(proposal, JOE, "propose-outcome-feedback", structuredClone(PROPOSE));
  const replay = await executeRegisteredTool(proposal, JOE, "propose-outcome-feedback", structuredClone(PROPOSE));
  assert.equal(replay.replayed, true); assert.equal(proposal.calls.filter(x => x.sql.includes("propose_sourced_work_request_outcome_feedback")).length, 1);
  const foreign = await refused(() => executeRegisteredTool(proposal, { ...JOE, id: "10000000-0000-0000-0000-000000000003", slug: "dell" }, "propose-outcome-feedback", structuredClone(PROPOSE)));
  assert.equal(foreign.error, "key_reuse");
  const accepted = new OutcomeFake(); await executeRegisteredTool(accepted, JOE, "accept-outcome-feedback", structuredClone(ACCEPT));
  const acceptedReplay = await executeRegisteredTool(accepted, JOE, "accept-outcome-feedback", structuredClone(ACCEPT));
  assert.equal(acceptedReplay.replayed, true); assert.equal(accepted.calls.filter(x => x.sql.includes("accept_sourced_work_request_outcome_feedback")).length, 1);
});

test("closed evidence, measurement, and outcome consistency refuse before database I/O", async () => {
  const noDb = { query: async () => { throw new Error("database must not be called"); } };
  for (const args of [
    { ...PROPOSE, evidence_refs: ["not-safe"] },
    { ...PROPOSE, criterion_results: [{ id: "READY-PLAN", result: "met" }, { id: "READY-PLAN", result: "met" }] },
    { ...PROPOSE, criterion_results: [{ id: "READY-PLAN", result: "met" }], blocker_code: "evidence_missing" },
    { ...PROPOSE, observed_minutes: 0 },
    { ...PROPOSE, interaction_surface: "terminal" },
    { ...PROPOSE, manual_context_transfers: 101 },
    { ...PROPOSE, extra: true },
  ]) assert.ok((await refused(() => executeRegisteredTool(noDb, JOE, "propose-outcome-feedback", args))).error);
  // RE-INVERTED 2026-09-11 (WR-000021): the humanOnly refusal is a gate again,
  // enforced at the tools.js dispatcher for every verb carrying the flag. The
  // 2026-08-26 inversion was written to fail loudly if the gate ever came back
  // rather than to disappear, and it did exactly that. Acceptance is a human
  // act: an agent reaching this verb would write the sponsor's name onto a
  // decision the sponsor never made.
  const machine = await refused(() => executeRegisteredTool(new OutcomeFake(), BOT, "accept-outcome-feedback", structuredClone(ACCEPT)));
  assert.equal(machine.error, "human_only_verb_requires_verified_partner");
  assert.equal(machine.verb, "accept-outcome-feedback");
  assert.equal((await refused(() => callTool({}, JOE, "accept-outcome-feedback", structuredClone(ACCEPT), "full"))).error, "authority_connection_unavailable");
});

test("card exposes pending separately and reads accepted history A then B without execution controls", async () => {
  const pending = await executeRegisteredTool(new CardFake("pending"), JOE, "work-request-card", { work_request: "WR-000001" });
  assert.equal(pending.outcome_feedback.feedback_ref, acceptedA.feedback_ref);
  assert.equal(pending.pending_outcome_feedback.feedback_ref, acceptedB.feedback_ref);
  assert.equal(pending.pending_outcome_feedback.status, "pending_human_acceptance");
  assert.deepEqual(pending.outcome_feedback_history.map(x => x.feedback_ref), [acceptedA.feedback_ref]);
  assert.equal(pending.accepted_feedback_count, 1); assert.equal(pending.state, "ready"); assert.equal(pending.projection_state, "queued"); assert.deepEqual(pending.actions, []);
  const accepted = await executeRegisteredTool(new CardFake("b"), JOE, "work-request-card", { work_request: "WR-000001" });
  assert.deepEqual(accepted.outcome_feedback_history.map(x => x.feedback_ref), [acceptedA.feedback_ref, acceptedB.feedback_ref]);
  assert.equal(accepted.outcome_feedback.feedback_ref, acceptedB.feedback_ref); assert.equal(accepted.accepted_feedback_count, 2);
  assert.equal(accepted.pending_outcome_feedback, null);
  assert.deepEqual(accepted.next_human_action, { label: "Outcome feedback accepted", effect: "none" });
});
