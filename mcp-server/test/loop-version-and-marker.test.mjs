// loop-version-and-marker.test.mjs — coverage for the two Phase 1 defects
// found live 2026-08-13 (decision 7026246b):
//
//   1. close-loop returned version_conflict when closing a loop with
//      base_version === current_version (loop #350: both 1), citing the
//      loop's OWN add-loop creation event as the "intervening" conflict.
//      Root cause: versionGuard's comparison was strict `!==` with no type
//      coercion, and MCP tool-call arguments are never validated against
//      inputSchema server-side (mcp.js's callTool passes
//      `rpc.params?.arguments` straight through) — a caller that sent
//      base_version as the JSON string "1" instead of the number 1 hit
//      `1 !== "1"` => true despite both being "1" to a human reading the
//      error. Fixed by coercing both sides to Number before comparing
//      (compareVersion, tools.js), and separately, the intervening-events
//      query now excludes the record's own creation event on any genuine
//      conflict (never useful information: a caller holding base_version>=1
//      already knows the record exists).
//
//   2. add-loop returned a bare {"error":"internal error"} for an illegal
//      marker ("wrench" — not in bell/dated/decision/none). Root cause: the
//      value reached loop_item's CHECK constraint raw, and the DB error
//      was never translated into a ToolError. Fixed with (a) up-front
//      validation in add-loop naming the allowed values, and (b) a generic
//      backstop in executeRegisteredTool that translates any UNCAUGHT
//      Postgres class-23 (integrity_constraint_violation) error into a
//      clean ToolError, so a future enum/FK nobody remembered to validate
//      fails the same honest way instead of a bare 500.
//
// Run with: node --test mcp-server/test/loop-version-and-marker.test.mjs
// (also picked up by `npm test`'s test/*.test.mjs glob).

import { test } from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError, executeRegisteredTool, compareVersion, pgConstraintError } from "../src/tools.js";

// ────────────────────────────────────────────────────────────────────────
// compareVersion — pure logic, no DB. This is the actual fix for defect 1:
// isolated so the type-coercion behavior is provable without a connection.
// ────────────────────────────────────────────────────────────────────────

test("compareVersion: equal numbers agree", () => {
  assert.deepEqual(compareVersion(1, 1), { ok: true });
  assert.deepEqual(compareVersion(4, 4), { ok: true });
});

test("compareVersion: THE BUG — equal value, mismatched JS type, must still agree", () => {
  // This is loop #350 exactly: current_version 1 (a real DB number) against
  // a base_version that arrived as the JSON string "1". The old code's
  // strict `current !== baseVersion` treated this as a conflict.
  assert.deepEqual(compareVersion(1, "1"), { ok: true });
  assert.deepEqual(compareVersion("1", 1), { ok: true });
  assert.deepEqual(compareVersion("4", "4"), { ok: true });
});

test("compareVersion: a REAL mismatch still conflicts after coercion", () => {
  assert.deepEqual(compareVersion(4, 1), { ok: false, kind: "conflict" });
  assert.deepEqual(compareVersion(1, "4"), { ok: false, kind: "conflict" });
});

test("compareVersion: missing base_version is refused as missing, not silently matched", () => {
  assert.deepEqual(compareVersion(1, undefined), { ok: false, kind: "missing_base_version" });
  assert.deepEqual(compareVersion(1, null), { ok: false, kind: "missing_base_version" });
});

test("compareVersion: a non-numeric base_version is refused by name, not treated as a conflict-shaped guess", () => {
  assert.deepEqual(compareVersion(1, "abc"), { ok: false, kind: "invalid_base_version" });
  assert.deepEqual(compareVersion(1, {}), { ok: false, kind: "invalid_base_version" });
});

// ────────────────────────────────────────────────────────────────────────
// close-loop end to end, through a fake DB client — same pattern as
// loop-owner-repair.test.mjs's CloseLoopFake. Two scenarios: the exact
// false-conflict this defect produced (must now succeed), and a genuine
// conflict (must still refuse, and must not blame the loop's own creation).
// ────────────────────────────────────────────────────────────────────────

const ids = {
  joe: "10000000-0000-0000-0000-000000000002",
  loop: "20000000-0000-0000-0000-000000000350",
  successor: "20000000-0000-0000-0000-000000000351",
};
const joe = { id: ids.joe, slug: "joe", display: "Joe", human: true,
  via: "mcp", client_id: "claude" };

class CloseLoopFake {
  constructor({ version, baseVersionSentAsString }) {
    // open_loop, exactly like loop #350: no Done table (renders_closed is
    // false for every open_loop block), so `done.rows` is always empty here.
    this.loop = { id: ids.loop, kind: "open_loop", number: "350", status: "open",
      marker: "bell", due_on: null, close_outcome: null, section: "hot",
      version, created_at: "2026-08-13T16:09:51.846158+00:00" };
    this.successor = { id: ids.successor, kind: "open_loop", number: "351", status: "open",
      marker: "none", due_on: null, close_outcome: null, section: "backlog",
      version: 1, created_at: "2026-08-13T16:10:00.000000+00:00" };
    this.events = [
      // The loop's own creation event — recorded_at equal to created_at,
      // exactly as the same transaction guarantees in production.
      { actor: "joe", verb: "add-loop", field: null, old_value: null, new_value: {},
        recorded_at: this.loop.created_at },
    ];
    this.calls = new Map();
    this.baseVersionSentAsString = baseVersionSentAsString;
  }

  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.startsWith("select request_hash, response")) {
      const prior = this.calls.get(params[0]);
      return { rows: prior ? [prior] : [] };
    }
    if (sql.includes("from loop_item li join loop_block lb") && sql.includes("where li.id = $1"))
      return { rows: params[0] === this.loop.id ? [{ ...this.loop }]
        : params[0] === this.successor.id ? [{ ...this.successor }] : [] };
    if (sql.startsWith("select version from loop_item where id=$1 for update"))
      return { rows: params[0] === this.loop.id ? [{ version: this.loop.version }] : [] };
    if (sql.startsWith("select created_at from loop_item where id=$1"))
      return { rows: params[0] === this.loop.id ? [{ created_at: this.loop.created_at }] : [] };
    if (sql.includes("from event e join actor a on a.id=e.actor_id") && sql.includes("e.recorded_at > $2")) {
      const [subjectId, after] = params;
      if (subjectId !== this.loop.id) return { rows: [] };
      const rows = this.events
        .filter(e => new Date(e.recorded_at).getTime() > new Date(after).getTime())
        .map(e => ({ actor: e.actor, verb: e.verb, field: e.field,
                      old_value: e.old_value, new_value: e.new_value, recorded_at: e.recorded_at }));
      return { rows };
    }
    if (sql.startsWith("select id, rel_path, block_key from loop_block"))
      return { rows: [] }; // open_loop keeps no Done table
    if (sql.startsWith("select coalesce(max(render_seq)")) return { rows: [{ n: 0 }] };
    if (sql.startsWith("update loop_item set")) {
      assert.equal(params.at(-1), this.loop.id);
      this.loop.status = params[0];
      this.loop.close_outcome = params[1];
      return { rows: [] };
    }
    if (sql.startsWith("insert into event")) {
      // occurred_at, actor_id, verb, subject_type, subject_id, field, ...
      this.events.push({ actor: "joe", verb: params[2], field: params[5] || null,
        old_value: params[6] ? JSON.parse(params[6]) : null,
        new_value: params[7] ? JSON.parse(params[7]) : null,
        recorded_at: new Date(Date.now() + 60_000).toISOString() }); // strictly after creation
      return { rows: [] };
    }
    if (sql.startsWith("insert into tool_call")) {
      this.calls.set(params[0], { request_hash: params[3], response: JSON.parse(params[4]),
        actor_id: params[2], organization_tenant_id: params[7] ?? null,
        application_session_id: params[12] ?? null });
      return { rows: [] };
    }
    throw new Error(`unhandled fake query: ${sql}`);
  }
}

test("close-loop: SAME-SESSION CREATE-THEN-CLOSE now succeeds (defect 1) even when base_version arrives as a string", async () => {
  // The exact shape of loop #350: current_version 1, base_version "1" (a
  // JSON string, the plausible real cause — see compareVersion's tests
  // above). Before the fix this threw version_conflict citing the loop's
  // own creation as the "intervening event".
  const db = new CloseLoopFake({ version: 1, baseVersionSentAsString: true });
  const result = await TOOLS["close-loop"].handler(db, joe, {
    idempotency_key: "close-350-self-test",
    loop_id: ids.loop,
    base_version: "1", // string on purpose — reproduces the reported defect
    outcome: "Verified — this is what the fix makes possible.",
  });
  assert.deepEqual(result, {
    ok: true, loop_id: ids.loop, number: "350", status: "done",
    moved_to_done_table_in: null, closed_rows_render_in: null,
  });
  assert.equal(db.loop.status, "done");
});

test("close-loop: also succeeds when base_version arrives as a proper number (the ordinary case, unregressed)", async () => {
  const db = new CloseLoopFake({ version: 1 });
  const result = await TOOLS["close-loop"].handler(db, joe, {
    idempotency_key: "close-350-ordinary",
    loop_id: ids.loop,
    base_version: 1,
    outcome: "Ordinary close, numeric base_version.",
  });
  assert.equal(result.ok, true);
  assert.equal(result.status, "done");
});

test("close-loop: a REAL version conflict still refuses, and never blames the loop's own creation event", async () => {
  const db = new CloseLoopFake({ version: 3 }); // someone else edited it twice since
  // A real intervening edit, strictly after creation.
  db.events.push({ actor: "dell", verb: "update-loop", field: "marker",
    old_value: { marker: "none" }, new_value: { marker: "bell" },
    recorded_at: "2026-08-13T17:00:00.000000+00:00" });

  await assert.rejects(
    () => TOOLS["close-loop"].handler(db, joe, {
      idempotency_key: "close-350-real-conflict",
      loop_id: ids.loop,
      base_version: 1,
      outcome: "Should not land.",
    }),
    (err) => {
      assert.ok(err instanceof ToolError);
      assert.equal(err.payload.error, "version_conflict");
      assert.equal(err.payload.current_version, 3);
      // The creation event (verb add-loop) must be excluded; the real
      // subsequent edit (verb update-loop) must be present.
      const verbs = err.payload.intervening_events.map(e => e.verb);
      assert.ok(!verbs.includes("add-loop"),
        "the loop's own creation event must never be reported as an intervening conflict");
      assert.ok(verbs.includes("update-loop"),
        "a genuine subsequent edit must still be reported");
      return true;
    });
});

test("close-loop: a bookkeeping close must name an open successor and say so first", async () => {
  const db = new CloseLoopFake({ version: 1 });
  await assert.rejects(() => TOOLS["close-loop"].handler(db, joe, {
    idempotency_key: "close-bookkeeping-missing", loop_id: ids.loop, base_version: 1,
    resolution: "dropped", outcome: "Superseded by the replacement loop.",
  }), e => e instanceof ToolError && e.payload.error === "successor_loop_required");
  const result = await TOOLS["close-loop"].handler(db, joe, {
    idempotency_key: "close-bookkeeping-valid", loop_id: ids.loop, base_version: 1,
    resolution: "dropped", outcome: "Superseded, not abandoned: #351 carries the work forward.", successor_loop: ids.successor,
  });
  assert.deepEqual(result.successor_loop, { id: ids.successor, number: "351" });
  assert.match(JSON.stringify(db.events.at(-1).new_value), /successor_loop/);
});

// ────────────────────────────────────────────────────────────────────────
// add-loop marker validation (defect 2, half a) — a clean, named refusal
// instead of a bare internal error, and it must fire before any DB write.
// ────────────────────────────────────────────────────────────────────────

class AddLoopValidationFake {
  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.startsWith("select request_hash, response"))
      return { rows: [] };
    throw new Error(`unhandled fake query (marker validation should refuse before this): ${sql}`);
  }
}

test("add-loop: an illegal marker is refused with a clean, named error listing the allowed values (defect 2a)", async () => {
  const db = new AddLoopValidationFake();
  await assert.rejects(
    () => TOOLS["add-loop"].handler(db, joe, {
      idempotency_key: "add-loop-wrench-self-test",
      kind: "open_loop", owner: "joe", body: "verb-fix self-test, close immediately",
      marker: "wrench",
      blocker: "capability", blocker_detail: "reproducing the marker defect on purpose",
    }),
    (err) => {
      assert.ok(err instanceof ToolError, "must be a structured ToolError, never a bare/raw error");
      assert.equal(err.payload.error, "unknown_marker");
      assert.equal(err.payload.got, "wrench");
      assert.deepEqual(err.payload.allowed, ["bell", "dated", "decision", "none"]);
      return true;
    });
});

// Full happy-path fake, so a legal marker (and a legal domain) can be proven
// to reach a genuine `ok:true` rather than merely "didn't throw
// unknown_marker" — the same shape as the live self-test in the report.
class AddLoopHappyFake {
  constructor() {
    this.blockId = "30000000-0000-0000-0000-000000000350";
    this.inserted = null;
    this.events = [];
    this.calls = new Map();
  }
  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.startsWith("select request_hash, response")) {
      const prior = this.calls.get(params[0]);
      return { rows: prior ? [prior] : [] };
    }
    if (sql.startsWith("select slug from loop_domain where slug=$1"))
      return { rows: params[0] === "system" ? [{ slug: "system" }] : [] };
    if (sql.startsWith("select id, rel_path, col_order from loop_block"))
      return params[0] === "open_loop" && params[1] === "hot"
        ? { rows: [{ id: this.blockId, rel_path: "00_Context/open-loops.md", col_order: null }] }
        : { rows: [] };
    if (sql.includes("from loop_item where kind = $1")) return { rows: [{ m: 350 }] }; // nextLoopNumber
    if (sql.startsWith("select coalesce(max(render_seq)")) return { rows: [{ n: 1 }] };
    if (sql.startsWith("insert into loop_item")) {
      this.inserted = params;
      return { rows: [{ id: "20000000-0000-0000-0000-000000000351" }] };
    }
    if (sql.startsWith("insert into event")) {
      this.events.push({ verb: params[2] }); return { rows: [] };
    }
    if (sql.startsWith("insert into tool_call")) {
      this.calls.set(params[0], { request_hash: params[3], response: JSON.parse(params[4]),
        actor_id: params[2], organization_tenant_id: params[7] ?? null,
        application_session_id: params[12] ?? null });
      return { rows: [] };
    }
    throw new Error(`unhandled fake query: ${sql}`);
  }
}

test("add-loop: a legal marker and a legal domain reach a genuine ok:true (defect 2a does not over-refuse)", async () => {
  const db = new AddLoopHappyFake();
  const result = await TOOLS["add-loop"].handler(db, joe, {
    idempotency_key: "add-loop-happy-path",
    kind: "open_loop", owner: "joe", body: "verb-fix self-test, close immediately",
    marker: "bell", domain: "system",
    blocker: "capability", blocker_detail: "coverage only, not a real block",
  });
  assert.equal(result.ok, true);
  assert.equal(result.number, "351");
  assert.equal(db.events.length, 1);
  assert.equal(db.events[0].verb, "add-loop");
});

// ────────────────────────────────────────────────────────────────────────
// pgConstraintError (defect 2, half b) — the generic backstop for any
// enum/FK this file has not (yet) learned to validate up front.
// ────────────────────────────────────────────────────────────────────────

test("pgConstraintError: translates a recognized Postgres class-23 violation into a clean ToolError", () => {
  const dbErr = Object.assign(new Error('new row for relation "loop_item" violates check constraint "loop_item_marker_check"'),
    { code: "23514", constraint: "loop_item_marker_check", table: "loop_item", column: null,
      detail: "Failing row contains (..., wrench, ...)." });
  const translated = pgConstraintError(dbErr);
  assert.ok(translated instanceof ToolError);
  assert.equal(translated.payload.error, "invalid_field_value");
  assert.equal(translated.payload.violation, "check_violation");
  assert.equal(translated.payload.constraint, "loop_item_marker_check");
});

test("pgConstraintError: leaves anything outside class 23 alone (not a catch-all)", () => {
  const notAConstraintError = Object.assign(new Error("connection terminated unexpectedly"), { code: "57P01" });
  assert.equal(pgConstraintError(notAConstraintError), null);
  assert.equal(pgConstraintError(new Error("plain JS error, no .code at all")), null);
});

test("pgConstraintError: redacts a connection string if one ever showed up in a driver error's detail", () => {
  const dbErr = Object.assign(new Error("integrity violation"), { code: "23503",
    detail: "leaked postgres://user:pw@host/db by accident" });
  const translated = pgConstraintError(dbErr);
  assert.ok(!translated.payload.detail.includes("postgres://"),
    "a connection string must never reach the caller, even defensively");
});

test("the assembled tool registry refuses unreviewed runtime handler injection", () => {
  assert.equal(Object.isFrozen(TOOLS), true);
  assert.throws(() => { TOOLS.__test_only_constraint_violator__ = { write: true, handler: async () => null }; }, TypeError);
  assert.equal(TOOLS.__test_only_constraint_violator__, undefined);
});
