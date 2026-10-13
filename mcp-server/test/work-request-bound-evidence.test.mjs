// work-request-bound-evidence.test.mjs — a sourced Work Request completes on
// bound evidence without a human accept-outcome-feedback call (decision
// cbe91287).
//
// The predicate itself is SQL (ops.work_request_bound_evidence_verdict, proven
// against a real PostgreSQL in self-closing-records-sql.test.mjs, and end to end
// by ops/self-closing-records-gate.py). This suite proves the ONE door that
// reaches it, bind-work-request-criterion-evidence: only a partner or a
// sponsored agent may use it, no unattended profile carries it, it reports
// exactly what the database answered (and the state the row holds), and it
// never claims a completion the database did not write. propose-outcome-feedback
// sits on the capture profile, so it proposes and completes nothing.
//
// Run with: node --test mcp-server/test/work-request-bound-evidence.test.mjs

import { test } from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError } from "../src/tools.js";
import { PROFILES, allowedIn } from "../src/mcp.js";

const KEY = "30000000-0000-4000-8000-000000000001";
const WR_ID = "40000000-0000-4000-8000-000000000001";
const RUN = "ops.run:8c9e3adf-a3d5-46e6-b134-eee5a60d1b80";
const codex = { id: "10000000-0000-0000-0000-000000000009", slug: "codex", human: false,
  via: "oauth-google", sponsoring_human_slug: "joe", native_agent_verified: true };

class Fake {
  constructor(plan = {}) { this.plan = plan; this.sql = []; this.toolCalls = new Map(); }
  async query(text, params = []) {
    const s = text.replace(/\s+/g, " ").trim();
    this.sql.push([s, params]);
    if (s.startsWith("select request_hash, response")) {
      const row = this.toolCalls.get(params[0]); return { rows: row ? [row] : [] };
    }
    if (s.startsWith("insert into tool_call")) {
      this.toolCalls.set(params[0], { request_hash: params[3], response: JSON.parse(params[4]) });
      return { rows: [] };
    }
    for (const [match, rows] of Object.entries(this.plan))
      if (s.includes(match)) return { rows: typeof rows === "function" ? rows(params) : rows };
    return { rows: [] };
  }
}

const refuse = async (fn) => {
  try { await fn(); } catch (e) {
    assert.ok(e instanceof ToolError, `expected a ToolError, got ${e}`);
    return e.payload;
  }
  throw new Error("expected a refusal and got none");
};

const binding = { binding_id: "50000000-0000-4000-8000-000000000001", work_request_id: WR_ID,
  ref: "WR-000201", criterion_id: "SHIPPED", receipt_ref: RUN, receipt_kind: "run",
  bound_by_actor_slug: "codex", created_at: "2026-10-12T12:00:00.000Z", replayed: false };
const notYet = { work_request_id: WR_ID, ref: "WR-000201", state: "ready", version: 4,
  completed: false, missing: ["OBSERVED"], receipt_id: null, feedback_ref: "OUTCOME-aaaaaaaaaaaa-v1",
  feedback_hash: `sha256:${"b".repeat(64)}`, acceptance_basis: null, bound_receipt_refs: null,
  accepted_by_actor_slug: null, accepted_at: null, replayed: false };
const receipts = [{ criterion_id: "OBSERVED", receipt_ref: "incident:INC-20260901-03",
  receipt_kind: "incident", binding_id: "50000000-0000-4000-8000-000000000002" },
  { criterion_id: "SHIPPED", receipt_ref: RUN, receipt_kind: "run", binding_id: binding.binding_id }];
const done = { ...notYet, completed: true, missing: [], receipt_id: "60000000-0000-4000-8000-000000000001",
  acceptance_basis: "completed_on_bound_evidence", bound_receipt_refs: receipts,
  accepted_by_actor_slug: "codex", accepted_at: "2026-10-12T12:00:01.000Z" };

const bind = (fake, over = {}) => TOOLS["bind-work-request-criterion-evidence"].handler(fake, codex,
  { idempotency_key: KEY, human_ref: "WR-000201", criterion_id: "SHIPPED", receipt_ref: RUN, ...over });

// ── 1. THE BIND DOOR ────────────────────────────────────────────────────────

test("binding one criterion while another is unbound does not complete the request", async () => {
  const fake = new Fake({
    "ops.bind_work_request_criterion_evidence": [binding],
    "ops.complete_sourced_work_request_on_bound_evidence": [notYet],
  });
  const out = await bind(fake);
  assert.equal(out.bound, true);
  assert.equal(out.completion.completed, false);
  assert.deepEqual(out.completion.missing, ["OBSERVED"]);
  assert.equal(out.state, "ready", "the request is not done while a criterion is unbound");
  assert.ok(!fake.sql.some(([s, p]) => s.startsWith("insert into event") &&
    p.includes("outcome_feedback_completed_on_bound_evidence")),
    "no completion event for a completion that did not happen");
});

test("binding the last criterion completes the request, and the receipt names every receipt id", async () => {
  const fake = new Fake({
    "ops.bind_work_request_criterion_evidence": [binding],
    "ops.complete_sourced_work_request_on_bound_evidence": [done],
  });
  const out = await bind(fake);
  assert.equal(out.completion.completed, true);
  assert.equal(out.completion.acceptance_basis, "completed_on_bound_evidence");
  assert.match(out.completion.summary, /completed on bound evidence/);
  assert.deepEqual(out.completion.bound_receipt_refs.map(r => r.receipt_ref),
    ["incident:INC-20260901-03", RUN]);
  assert.equal(out.state, "ready",
    "the state the row holds: completion writes an acceptance receipt, never a state the row cannot take");
  assert.equal(out.completion.state, "ready");
  assert.ok(fake.sql.some(([s, p]) => s.startsWith("insert into event") &&
    p.includes("outcome_feedback_completed_on_bound_evidence")),
    "the completion is an event a reader can find, not only a row");
});

test("a receipt the database cannot resolve is refused, and the refusal says what counts", async () => {
  const fake = { async query(text) {
    if (String(text).includes("ops.bind_work_request_criterion_evidence"))
      throw Object.assign(new Error("receipt fixed it does not resolve to a succeeded run, a complete deployment, a defect resolution or a resolved incident"), { code: "P0001" });
    return { rows: [] };
  } };
  const payload = await refuse(() => bind(fake, { receipt_ref: "fixed it" }));
  assert.equal(payload.error, "criterion_evidence_refused");
  assert.match(payload.detail, /does not resolve/);
});

test("an unrelated or reused receipt is refused, and the refusal names the mismatch", async () => {
  for (const [reason, message] of [
    ["receipt_not_evidence_for_work_request", "receipt_not_evidence_for_work_request: run_not_linked_to_work_request: " +
      `${RUN} belongs to no release recorded for WR-000201 and names WR-000201 in none of run_key, source_ref, evidence_ref`],
    ["receipt_already_bound", `receipt_already_bound: ${RUN} is already evidence for WR-000188 criterion SHIPPED`],
  ]) {
    const fake = { async query(text) {
      if (String(text).includes("ops.bind_work_request_criterion_evidence"))
        throw Object.assign(new Error(message), { code: "P0001" });
      return { rows: [] };
    } };
    const payload = await refuse(() => bind(fake));
    assert.equal(payload.error, "criterion_evidence_refused");
    assert.equal(payload.reason, reason);
    assert.match(payload.detail, reason === "receipt_already_bound" ? /WR-000188/ : /run_not_linked_to_work_request/);
  }
});

test("an unsponsored runtime is refused before the database is asked", async () => {
  for (const actor of [
    { id: "10000000-0000-0000-0000-000000000011", slug: "grok", human: false, via: "agent-token" },
    { id: "10000000-0000-0000-0000-000000000008", slug: "smoke-probe", human: false, probe: true, via: "probe-token" },
  ]) {
    const fake = new Fake({ "ops.bind_work_request_criterion_evidence": [binding],
      "ops.complete_sourced_work_request_on_bound_evidence": [done] });
    const payload = await refuse(() => TOOLS["bind-work-request-criterion-evidence"].handler(fake, actor,
      { idempotency_key: KEY, human_ref: "WR-000201", criterion_id: "SHIPPED", receipt_ref: RUN }));
    assert.equal(payload.error, "sponsored_actor_required", actor.slug);
    assert.ok(!fake.sql.some(([s]) => s.includes("bind_work_request_criterion_evidence") ||
      s.includes("complete_sourced_work_request_on_bound_evidence")), "nothing reached the database");
  }
});

test("no unattended profile carries the completion door; capture keeps only the proposal", () => {
  const tool = TOOLS["bind-work-request-criterion-evidence"];
  for (const profile of Object.keys(PROFILES).filter(p => p !== "full" && p !== "doc"))
    assert.equal(allowedIn(profile, "bind-work-request-criterion-evidence", tool), false, profile);
  assert.equal(allowedIn("full", "bind-work-request-criterion-evidence", tool), true);
  assert.equal(allowedIn("capture", "propose-outcome-feedback", TOOLS["propose-outcome-feedback"]), true,
    "capture may still propose, which is why proposing must complete nothing");
});

test("the bind door's arguments are closed and checked before the database", async () => {
  const schema = TOOLS["bind-work-request-criterion-evidence"].inputSchema;
  assert.equal(schema.additionalProperties, false);
  assert.deepEqual([...schema.required].sort(), ["criterion_id", "human_ref", "idempotency_key", "receipt_ref"]);
  assert.equal(TOOLS["bind-work-request-criterion-evidence"].write, true);
  assert.ok(!TOOLS["bind-work-request-criterion-evidence"].humanOnly);
  for (const over of [{ idempotency_key: "not-a-uuid" }, { human_ref: "201" },
                      { criterion_id: "shipped" }, { receipt_ref: "" }]) {
    const fake = new Fake();
    const payload = await refuse(() => bind(fake, over));
    assert.equal(payload.error, "invalid_criterion_evidence", JSON.stringify(over));
    assert.ok(!fake.sql.some(([s]) => s.includes("bind_work_request_criterion_evidence")));
  }
});

// ── 2. THE PROPOSAL DOOR ────────────────────────────────────────────────────

const PROPOSE = { idempotency_key: "10000000-0000-4000-8000-000000000099", human_ref: "WR-000201",
  base_version: 4, plan_hash: `sha256:${"a".repeat(64)}`,
  criterion_results: [{ id: "OBSERVED", result: "met" }, { id: "SHIPPED", result: "met" }],
  evidence_refs: ["safe:observation:run-1"], blocker_code: "none", result_summary: "Both observed.",
  observed_minutes: 12, interaction_surface: "codex", heavy_session_used: false, manual_context_transfers: 0 };
const proposed = { feedback_id: "70000000-0000-4000-8000-000000000001", feedback_ref: notYet.feedback_ref,
  feedback_hash: notYet.feedback_hash, work_request_id: WR_ID, ref: "WR-000201", state: "ready", version: 4,
  plan_ref: "PLAN-aaaaaaaaaaaa-v1", plan_hash: PROPOSE.plan_hash, outcome: "criteria_met",
  criterion_results: PROPOSE.criterion_results, evidence_refs: PROPOSE.evidence_refs, blocker_code: "none",
  result_summary: PROPOSE.result_summary, observed_minutes: 12, interaction_surface: "codex",
  heavy_session_used: false, manual_context_transfers: 0, replayed: false };

test("a criteria_met proposal with every criterion bound still completes nothing: it proposes", async () => {
  const fake = new Fake({
    "propose_sourced_work_request_outcome_feedback": [proposed],
    "ops.complete_sourced_work_request_on_bound_evidence": [done],
  });
  const out = await TOOLS["propose-outcome-feedback"].handler(fake, codex, PROPOSE);
  assert.equal(out.status, "pending_human_acceptance");
  assert.equal(Object.hasOwn(out, "completion"), false);
  assert.ok(!fake.sql.some(([s]) => s.includes("complete_sourced_work_request_on_bound_evidence")),
    "the capture-profile verb never asks the database to complete");
  assert.ok(!fake.sql.some(([s, p]) => s.startsWith("insert into event") &&
    p.includes("outcome_feedback_completed_on_bound_evidence")));
  assert.match(TOOLS["propose-outcome-feedback"].description, /^[^.]*completes nothing/,
    "the registry's 'does' (the first sentence) records that it completes nothing");
});
