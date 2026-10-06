// A Work Request captured in error could only ever move FORWARD. Migration 0426
// adds the terminal half; these tests cover the verb half of it and the card that
// has to keep reading a withdrawn record afterwards.
//
// THE FAILURE THIS EXISTS TO CATCH is not "the write did not happen". It is the
// card quietly reporting a closed record as queued and asking a human to triage
// it, which is the queue pollution the two verbs were written to end.
import test from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError, executeRegisteredTool } from "../src/tools.js";

const JOE = { id: "10000000-0000-0000-0000-000000000002", slug: "joe", human: true, via: "test" };
const BOT = { ...JOE, human: false, slug: "claude" };
const KEY = "10000000-0000-0000-0000-000000000099";
const DECLINE = { idempotency_key: KEY, human_ref: "WR-000032", base_version: 1,
  exit_reason: "captured by mistake from a schema probe; nothing to build" };
const SUPERSEDE = { ...DECLINE, superseded_by: "WR-000034" };
const ID = "20000000-0000-0000-0000-000000000032";

async function rejected(fn) { try { await fn(); assert.fail("expected refusal"); } catch (e) { assert.ok(e instanceof ToolError); return e.payload; } }

class WithdrawFake {
  constructor() { this.calls = []; this.toolCalls = new Map(); this.state = "captured"; this.version = 1; }
  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim(); this.calls.push({ sql, params });
    if (sql.startsWith("select pg_advisory_xact_lock")) return { rows: [] };
    if (sql.startsWith("select request_hash, response")) { const row = this.toolCalls.get(params[0]); return { rows: row ? [row] : [] }; }
    // The functions select "for update" on state='captured' and version=
    // p_base_version and raise when nothing matches; a stale version reaching the
    // handler as zero rows is the shape the handler has to turn into a conflict.
    if (sql.includes("decline_sourced_work_request")) {
      if (params[1] !== 1) return { rows: [] };
      this.state = "declined"; this.version = 2;
      return { rows: [{ ref: "WR-000032", state: this.state, version: this.version,
        exit_reason: params[2], closed_at: "2026-08-28T00:00:00Z" }] };
    }
    if (sql.includes("supersede_sourced_work_request")) {
      if (params[1] !== 1) return { rows: [] };
      this.state = "superseded"; this.version = 2;
      return { rows: [{ ref: "WR-000032", state: this.state, version: this.version,
        exit_reason: params[2], closed_at: "2026-08-28T00:00:00Z", superseded_by_ref: params[3] }] };
    }
    if (sql.includes("work-request-intake:withdrawn-subject")) return { rows: [{ id: ID }] };
    if (sql.startsWith("insert into event")) return { rows: [] };
    if (sql.startsWith("insert into tool_call")) { this.toolCalls.set(params[0], { request_hash: params[3], response: JSON.parse(params[4]),
        actor_id: params[2], organization_tenant_id: params[7] ?? null,
        application_session_id: params[12] ?? null }); return { rows: [] }; }
    throw new Error(`unexpected query: ${sql}`);
  }
}

test("withdrawal is TWO closed versioned writes, never one with an optional successor", () => {
  // state-machines.v1.json is phase0_frozen and declares "* -> declined" and
  // "* -> superseded" as separate transitions with different guards. A single
  // verb inferring the state from whether a successor was passed would turn a
  // forgotten argument into a silent decline.
  for (const name of ["decline-work-request", "supersede-work-request"]) {
    const tool = TOOLS[name];
    assert.equal(tool.write, true);
    assert.equal(tool.inputSchema.additionalProperties, false);
    assert.equal(tool.inputSchema.properties.base_version.minimum, 1);
  }
  assert.deepEqual(TOOLS["decline-work-request"].inputSchema.required,
    ["idempotency_key", "human_ref", "base_version", "exit_reason"]);
  assert.deepEqual(TOOLS["supersede-work-request"].inputSchema.required,
    ["idempotency_key", "human_ref", "base_version", "exit_reason", "superseded_by"]);
  // A decline cannot NAME a successor and a supersession cannot omit one; that
  // separation is the whole reason there are two verbs.
  assert.equal(TOOLS["decline-work-request"].inputSchema.properties.superseded_by, undefined);
  // NOT authorityOnly: 0426 grants execute to carr_writer only, and an authority
  // principal holding carr_writer is refused by the runtime preflight, so routing
  // these down the authority DSN would be a permission-denied on every call.
  for (const name of ["decline-work-request", "supersede-work-request"])
    assert.notEqual(TOOLS[name].authorityOnly, true);
});

test("decline records the reason, the closing time and one audit event", async () => {
  const db = new WithdrawFake();
  const out = await executeRegisteredTool(db, JOE, "decline-work-request", structuredClone(DECLINE));
  assert.deepEqual(out, { ok: true, human_ref: "WR-000032", state: "declined", version: 2,
    exit_reason: DECLINE.exit_reason, closed_at: "2026-08-28T00:00:00Z" });
  const call = db.calls.find(x => x.sql.includes("decline_sourced_work_request"));
  assert.equal(call.params[2], DECLINE.exit_reason);
  const event = db.calls.find(x => x.sql.startsWith("insert into event"));
  assert.equal(event.params[1], JOE.id, "audit actor is server-derived");
  assert.equal(event.params[4], ID, "the event names the withdrawn row, not a null subject");
  assert.equal(JSON.parse(event.params[7]).state, "declined");
  assert.equal(JSON.parse(event.params[6]).state, "captured");
});

test("supersede carries the successor through to the result and the event", async () => {
  const db = new WithdrawFake();
  const out = await executeRegisteredTool(db, JOE, "supersede-work-request", structuredClone(SUPERSEDE));
  assert.equal(out.state, "superseded");
  assert.equal(out.superseded_by_ref, "WR-000034");
  const call = db.calls.find(x => x.sql.includes("supersede_sourced_work_request"));
  assert.equal(call.params[3], "WR-000034");
  const event = db.calls.find(x => x.sql.startsWith("insert into event"));
  assert.equal(JSON.parse(event.params[7]).superseded_by_ref, "WR-000034");
});

test("the acting actor's own slug is passed, not a flattened partner name", async () => {
  // ops.authority_actor_slug() can only ever answer 'joe' or 'dell', which is why
  // every other receipt in this family stamps a human whoever acted. These
  // functions take the slug instead, so an agent's act is recorded as the agent's.
  const db = new WithdrawFake();
  await executeRegisteredTool(db, BOT, "decline-work-request", structuredClone(DECLINE));
  const call = db.calls.find(x => x.sql.includes("decline_sourced_work_request"));
  assert.equal(call.params[3], "claude");
});

test("a stale version is a question for a human, never a silent second withdrawal", async () => {
  for (const [verb, args] of [["decline-work-request", DECLINE], ["supersede-work-request", SUPERSEDE]]) {
    const out = await rejected(() => executeRegisteredTool(new WithdrawFake(), JOE, verb, { ...structuredClone(args), base_version: 99 }));
    assert.equal(out.error, "version_conflict");
    assert.equal(out.human_ref, "WR-000032");
  }
});

test("an empty reason, an unknown field and a self-supersession refuse before any query", async () => {
  const cases = [
    ["decline-work-request", { ...DECLINE, exit_reason: "   " }, "invalid_decline_work_request"],
    ["decline-work-request", { ...DECLINE, idempotency_key: "not-a-uuid" }, "invalid_decline_work_request"],
    // SIEP's reviewed operation contract rejects undeclared top-level fields at
    // the shared dispatch choke point, before the verb-specific validator.
    ["decline-work-request", { ...DECLINE, superseded_by: "WR-000034" }, "unregistered_operation_fields"],
    // A literally empty reason never reaches the validator: assertRequiredArgs
    // counts "" as missing at the shared choke point. Whitespace does reach it,
    // and is where "a withdrawal must record why" is actually enforced twice —
    // here and in the function.
    ["supersede-work-request", { ...SUPERSEDE, exit_reason: "" }, "missing_required"],
    ["supersede-work-request", { ...SUPERSEDE, exit_reason: " \t " }, "invalid_supersede_work_request"],
    ["supersede-work-request", { ...SUPERSEDE, superseded_by: "WR-000032" }, "invalid_supersede_work_request"],
    ["supersede-work-request", { ...SUPERSEDE, superseded_by: "not-a-ref" }, "invalid_supersede_work_request"],
  ];
  for (const [verb, args, error] of cases) {
    const db = new WithdrawFake();
    const out = await rejected(() => executeRegisteredTool(db, JOE, verb, args));
    assert.equal(out.error, error, `${verb} ${JSON.stringify(args)}`);
    assert.equal(db.calls.length, 0, "an invalid withdrawal never reaches the database");
  }
});

test("a replayed key returns the stored withdrawal and makes no second transition", async () => {
  const db = new WithdrawFake();
  const first = await executeRegisteredTool(db, JOE, "decline-work-request", structuredClone(DECLINE));
  const replay = await executeRegisteredTool(db, JOE, "decline-work-request", structuredClone(DECLINE));
  assert.equal(replay.replayed, true);
  assert.equal(replay.human_ref, first.human_ref);
  assert.equal(db.calls.filter(x => x.sql.includes("decline_sourced_work_request")).length, 1);
});

// ------------------------------------------------------------------- the card
// A card fake, separate from the write fake: the read path answers a different
// set of queries and nothing about it should depend on a write having happened
// in the same object.
class CardFake {
  constructor(row) { this.row = row; this.calls = []; }
  async query(text) {
    const sql = text.replace(/\s+/g, " ").trim(); this.calls.push(sql);
    if (sql.includes("work_request_card")) return { rows: this.row ? [this.row] : [] };
    if (sql.includes("acting-identity")) return { rows: [] };
    throw new Error(`unexpected query: ${sql}`);
  }
}

const BASE = { ref: "WR-000032", title: "Junk", desired_outcome: "nothing", acceptance_criteria: [],
  version: 2, source_current: true, doctrine_source_label: "control-room#evidence",
  doctrine_section_id: "30000000-0000-0000-0000-000000000001",
  doctrine_revision_id: "40000000-0000-0000-0000-000000000001",
  exit_reason: "duplicate of the record that replaced it", closed_at: "2026-08-28T00:00:00Z" };

test("a superseded request is still readable, projects as declined, and names its successor", async () => {
  const db = new CardFake({ ...BASE, state: "superseded", superseded_by_ref: "WR-000034" });
  const card = await executeRegisteredTool(db, JOE, "work-request-card", { work_request: "WR-000032" });
  assert.equal(card.state, "superseded");
  // NOT queued. The crosswalk maps both terminals onto declined; a closed record
  // reported as queued tells a requester their withdrawn request is in line.
  assert.equal(card.projection_state, "declined");
  assert.deepEqual(card.withdrawal, { exit_reason: BASE.exit_reason, closed_at: BASE.closed_at,
    superseded_by_ref: "WR-000034" });
  // And it asks nothing of anybody.
  assert.deepEqual(card.next_human_action, { label: "Superseded", effect: "none" });
  assert.deepEqual(card.actions, []);
  // Withdrawal is captured-only, so there is no triage, plan or shape to report
  // and the card must not invent one.
  assert.equal(card.triage, null);
  assert.equal(card.plan, null);
  assert.equal(card.shape, null);
  // A terminal row is never asked for pending outcome feedback: that read is
  // scoped to 'ready', and CardFake would throw if it were attempted.
  assert.equal(card.pending_outcome_feedback, null);
});

test("a declined request reads the same way and carries no successor", async () => {
  const db = new CardFake({ ...BASE, state: "declined", superseded_by_ref: null });
  const card = await executeRegisteredTool(db, JOE, "work-request-card", { work_request: "WR-000032" });
  assert.equal(card.projection_state, "declined");
  assert.deepEqual(card.next_human_action, { label: "Declined", effect: "none" });
  assert.equal(card.withdrawal.superseded_by_ref, null);
  assert.ok(card.withdrawal.exit_reason, "a withdrawal without a reason is not a record");
});

test("a live request keeps every one of its previous answers", async () => {
  const db = new CardFake({ ...BASE, state: "captured", version: 1, exit_reason: null, closed_at: null });
  const card = await executeRegisteredTool(db, JOE, "work-request-card", { work_request: "WR-000032" });
  assert.equal(card.projection_state, "queued");
  assert.equal(card.withdrawal, null);
  assert.deepEqual(card.next_human_action, { label: "Review and triage", effect: "none" });
});

test("a state the card function cannot return is still not found", async () => {
  for (const row of [null, { ...BASE, state: "claimed" }]) {
    const out = await rejected(() => executeRegisteredTool(new CardFake(row), JOE, "work-request-card", { work_request: "WR-000032" }));
    assert.equal(out.error, "work_request_not_found");
  }
});

// A needs_joe row is a GENERAL row (ops.work_request_sourced_capture_shape's
// general branch requires doctrine_section_id, doctrine_revision_id and
// organization_tenant_id all null), never a sourced one -- see 0575's own
// header. So its fixture, unlike BASE above, carries no doctrine linkage.
const NEEDS_JOE = { ref: "WR-000067", title: "Decide the routing", desired_outcome: "A named human decision",
  acceptance_criteria: [{ id: "JOE-ANSWER", text: "A human decision is recorded" }],
  version: 3, state: "needs_joe", source_current: null, doctrine_source_label: null,
  doctrine_section_id: null, doctrine_revision_id: null,
  exit_reason: null, closed_at: null, superseded_by_ref: null };

test("a needs_joe request is readable and carries exactly what answer-work-request-for-joe needs to revalidate", async () => {
  const db = new CardFake({ ...NEEDS_JOE });
  const card = await executeRegisteredTool(db, JOE, "work-request-card", { work_request: "WR-000067" });
  assert.equal(card.state, "needs_joe");
  // The 0575 verb's base_version compare-and-swap and its acceptance_criteria
  // digest are both taken from exactly these two fields.
  assert.equal(card.version, 3);
  assert.deepEqual(card.acceptance_criteria, NEEDS_JOE.acceptance_criteria);
  // work-request-projection.v1.json's crosswalk, not invented: needs_joe maps
  // to needs_answer ("a named human decision is outstanding").
  assert.equal(card.projection_state, "needs_answer");
  assert.deepEqual(card.next_human_action, { label: "Answer for Joe", effect: "none" });
  assert.deepEqual(card.actions, []);
  // A general row was never sourced, triaged, planned, or shaped, and it is
  // not a withdrawal -- every one of those blocks must read null rather than
  // invent a value or leak a sourced-row's INNER-JOIN assumption.
  assert.equal(card.triage, null);
  assert.equal(card.plan, null);
  assert.equal(card.shape, null);
  assert.equal(card.withdrawal, null);
  assert.equal(card.source, null);
  assert.equal(card.pending_outcome_feedback, null);
});

test("the other card states this function returns are unchanged by admitting needs_joe", async () => {
  // captured/triaged/ready/declined/superseded keep exactly the projections
  // asserted above and in the earlier tests in this file; this is a targeted
  // re-check that adding needs_joe touched none of them.
  const captured = await executeRegisteredTool(new CardFake({ ...BASE, state: "captured", version: 1,
    exit_reason: null, closed_at: null }), JOE, "work-request-card", { work_request: "WR-000032" });
  assert.equal(captured.projection_state, "queued");
  assert.deepEqual(captured.next_human_action, { label: "Review and triage", effect: "none" });
  const declined = await executeRegisteredTool(new CardFake({ ...BASE, state: "declined", superseded_by_ref: null }),
    JOE, "work-request-card", { work_request: "WR-000032" });
  assert.equal(declined.projection_state, "declined");
  assert.deepEqual(declined.next_human_action, { label: "Declined", effect: "none" });
});
