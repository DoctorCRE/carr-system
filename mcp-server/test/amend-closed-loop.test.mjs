// amend-closed-loop.test.mjs — unit coverage for the append-only correction
// door onto a CLOSED loop's outcome.
//
// THE GAP THIS CLOSES. close-loop refuses with loop_not_open on anything
// already closed, by design. That rule never had an answer for the outcome
// text itself being wrong: loop c7265238-effe-4166-bc9a-eccc5f389763 was
// closed with outcome "x" by mistake (defect a2c04ffa-92d0-4428-b175-
// 32fa3cfb0802), and nothing could fix it short of a raw table UPDATE. This
// suite proves amend-closed-loop's refusals fire on the right conditions and
// that a successful amend appends a loop_amendment row before touching the
// loop_item projection — never the reverse, and never neither.
//
// Run with: node --test mcp-server/test/amend-closed-loop.test.mjs
// (also picked up by `npm test`'s test/*.test.mjs glob).

import { test } from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError } from "../src/tools.js";

const joe = { id: "10000000-0000-0000-0000-000000000002", slug: "joe",
  display: "Joe", human: true, via: "mcp", client_id: "claude" };

const CLOSED = {
  id: "cccccccc-0000-0000-0000-000000000723",
  kind: "open_loop", number: "723", status: "done",
  marker: "none", due_on: null, close_outcome: "x",
  section: "backlog", rel_path: "00_Context/open-loops-backlog.md",
};
const OPEN = {
  id: "dddddddd-0000-0000-0000-000000000724",
  kind: "open_loop", number: "724", status: "open",
  marker: "none", due_on: null, close_outcome: null,
  section: "backlog", rel_path: "00_Context/open-loops-backlog.md",
};
const SUCCESSOR = {
  id: "eeeeeeee-0000-0000-0000-000000000725",
  kind: "open_loop", number: "725", status: "open",
  marker: "none", due_on: null, close_outcome: null,
  section: "backlog", rel_path: "00_Context/open-loops-backlog.md",
};

const GOOD_OUTCOME = "The card visual system shipped in PR #900, not the bio-header reminder.";
const GOOD_REASON = "Original close mistakenly recorded outcome 'x' — defect a2c04ffa.";

/** Answers the reads/writes amend-closed-loop makes, and records every write
 * so a test can assert ORDER (append before projection) and CONTENT. */
class Fake {
  constructor({ row = CLOSED, version = 3 } = {}) {
    this.row = row;
    this.version = version;
    this.writes = [];
  }
  async query(text, params) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.startsWith("select request_hash, response")) return { rows: [] };
    if (sql.startsWith("select version from loop_item"))
      return { rows: [{ version: this.version }] };
    if (sql.startsWith("select created_at from loop_item"))
      return { rows: [{ created_at: new Date("2026-09-01T00:00:00Z") }] };
    if (sql.startsWith("select a.slug as actor"))
      return { rows: [] };
    if (sql.startsWith("select li.id, li.kind, li.number")) {
      if (sql.includes("where li.id = $1")) {
        const id = params[0];
        const match = [CLOSED, OPEN, SUCCESSOR].find((r) => r.id === id) || this.row;
        return { rows: [match] };
      }
      if (sql.includes("where li.number = $1")) {
        const num = params[0];
        const match = [CLOSED, OPEN, SUCCESSOR].find((r) => r.number === num);
        return { rows: match ? [match] : [] };
      }
    }
    this.writes.push({ sql, params });
    if (sql.startsWith("insert into loop_amendment")) return { rows: [{ id: "amend-1" }] };
    if (sql.startsWith("update loop_item")) return { rows: [{ ...this.row, version: this.version + 1 }] };
    if (sql.startsWith("insert into")) return { rows: [{ id: "event-1" }] };
    return { rows: [] };
  }
}

function args(overrides = {}) {
  return { idempotency_key: `k-${Math.random()}`, loop_id: CLOSED.id, base_version: 3,
    outcome: GOOD_OUTCOME, reason: GOOD_REASON, ...overrides };
}

async function call(a, fake = new Fake()) {
  try { return { fake, result: await TOOLS["amend-closed-loop"].handler(fake, joe, a), error: null }; }
  catch (e) { return { fake, result: null, error: e instanceof ToolError ? e.payload : e }; }
}

test("registered as a write verb with the documented required fields", () => {
  const tool = TOOLS["amend-closed-loop"];
  assert.ok(tool, "amend-closed-loop is not registered");
  assert.equal(tool.write, true);
  assert.deepEqual(tool.inputSchema.required.slice().sort(),
    ["idempotency_key", "outcome", "reason"].sort());
});

test("a successful amend appends loop_amendment BEFORE it updates loop_item's projection", async () => {
  const { fake, error, result } = await call(args());
  assert.equal(error, null);
  assert.equal(result.ok, true);
  assert.equal(result.loop_id, CLOSED.id);
  assert.equal(result.prior_outcome, "x");
  assert.equal(result.outcome, GOOD_OUTCOME);
  assert.equal(result.status, "done", "resolution defaults to the loop's own closed status");
  const amendIdx = fake.writes.findIndex((w) => w.sql.startsWith("insert into loop_amendment"));
  const updateIdx = fake.writes.findIndex((w) => w.sql.startsWith("update loop_item"));
  assert.ok(amendIdx >= 0 && updateIdx >= 0, "both the amendment insert and the projection update must run");
  assert.ok(amendIdx < updateIdx, "append-only: the amendment row is written before the projection catches up");
});

test("the amendment row carries the prior outcome, the new outcome, the reason and the SERVER actor", async () => {
  const { fake } = await call(args());
  const insert = fake.writes.find((w) => w.sql.startsWith("insert into loop_amendment"));
  // loop_id, prior_outcome, new_outcome, prior_resolution, new_resolution, reason, actor_id, idempotency_key
  assert.equal(insert.params[0], CLOSED.id);
  assert.equal(insert.params[1], "x");
  assert.equal(insert.params[2], GOOD_OUTCOME);
  assert.equal(insert.params[3], "done");
  assert.equal(insert.params[5], GOOD_REASON);
  assert.equal(insert.params[6], joe.id, "the actor column is always the server-authenticated actor");
});

test("a caller-supplied actor field is ignored — the actor is server-derived, never a caller field", async () => {
  const { fake, error } = await call(args({ actor: "someone-else", actor_id: "11111111-0000-0000-0000-000000000099" }));
  assert.equal(error, null);
  const insert = fake.writes.find((w) => w.sql.startsWith("insert into loop_amendment"));
  assert.equal(insert.params[6], joe.id, "no caller-supplied field ever reaches the actor column");
  const event = fake.writes.find((w) => w.sql.startsWith("insert into event"));
  assert.equal(event.params[1], joe.id, "the event's actor_id is also always the server actor");
});

test("outcome_required: empty or whitespace-only outcome is refused before any read", async () => {
  for (const outcome of ["", "   "]) {
    const { fake, error } = await call(args({ outcome }));
    assert.equal(error?.error, "outcome_required");
    assert.equal(fake.writes.length, 0, "nothing is written on refusal");
  }
});

test("outcome_too_short: a placeholder correction under ~10 characters is refused (defect a2c04ffa's exact shape)", async () => {
  for (const outcome of ["x", "fixed", "n/a", "done."]) {
    const { fake, error } = await call(args({ outcome }));
    assert.equal(error?.error, "outcome_too_short");
    assert.equal(fake.writes.length, 0);
  }
});

test("reason_required: a missing or blank reason is refused, independent of a fine outcome", async () => {
  for (const reason of [undefined, "", "  "]) {
    const { fake, error } = await call(args({ reason }));
    assert.equal(error?.error, "reason_required");
    assert.equal(fake.writes.length, 0);
  }
});

test("loop_open: an OPEN loop is refused and points to update-loop / close-loop", async () => {
  const fake = new Fake({ row: OPEN, version: 1 });
  const { error } = await call({ ...args(), loop_id: OPEN.id, base_version: 1 }, fake);
  assert.equal(error?.error, "loop_open");
  assert.match(error.hint, /update-loop/);
  assert.match(error.hint, /close-loop/);
  assert.equal(fake.writes.length, 0);
});

test("version_conflict: a stale base_version is refused and never auto-retried", async () => {
  const fake = new Fake({ row: CLOSED, version: 5 }); // live version is 5
  const { error } = await call({ ...args(), base_version: 2 }, fake); // caller read version 2
  assert.equal(error?.error, "version_conflict");
  assert.equal(fake.writes.length, 0);
});

test("a bare number resolves a CLOSED row too — unlike every other loop verb's number lookup", async () => {
  const { error, result } = await call({ ...args(), loop_id: undefined, number: CLOSED.number, base_version: 3 });
  assert.equal(error, null);
  assert.equal(result.loop_id, CLOSED.id);
});

test("bookkeeping correction: RENUMBERED/SUPERSEDED wording requires resolution 'dropped' and a successor", async () => {
  const bookkeepingOutcome = "Superseded by #725, carried forward verbatim as the real fix.";
  const { error: noResolution } = await call(args({ outcome: bookkeepingOutcome }));
  assert.equal(noResolution?.error, "bookkeeping_close_is_dropped");

  const { error: noSuccessor } = await call(args({ outcome: bookkeepingOutcome, resolution: "dropped" }));
  assert.equal(noSuccessor?.error, "successor_loop_required");

  const { error: ok, result } = await call(args({
    outcome: bookkeepingOutcome, resolution: "dropped", successor_loop: "725" }));
  assert.equal(ok, null);
  assert.equal(result.successor_loop.id, SUCCESSOR.id);
});

test("successor_loop_not_open: the named successor must be a different, currently open loop", async () => {
  const bookkeepingOutcome = "Superseded by #723, carried forward.";
  const { error } = await call(args({
    outcome: bookkeepingOutcome, resolution: "dropped", successor_loop: "723" })); // names itself, closed
  assert.equal(error?.error, "successor_loop_not_open");
});

test("closed_at / closed_by are never touched — this corrects what was said, not when it was closed", async () => {
  const { fake } = await call(args());
  const update = fake.writes.find((w) => w.sql.startsWith("update loop_item"));
  assert.doesNotMatch(update.sql, /closed_at/, "closed_at must stay untouched by an amendment");
  assert.doesNotMatch(update.sql, /closed_by/, "closed_by must stay untouched by an amendment");
});
