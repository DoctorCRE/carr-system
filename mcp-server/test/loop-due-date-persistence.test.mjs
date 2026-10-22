// loop-due-date-persistence.test.mjs — defect e34d2b88, loop #476:
// "a write verb accepted a field and silently discarded it".
//
// WHAT HAPPENED. update-loop called with a due date but WITHOUT also setting
// marker to "dated" returned success, bumped the row version, moved updated_at,
// and stored nothing. The marker branch reads:
//
//     const marker = args.marker !== undefined ? args.marker : cur.marker;
//     set("due_on", marker === "dated" ? due : null);
//
// so a row whose current marker is "none" has the incoming date written straight
// to NULL. Every success signal short of reading the field back says the write
// worked. On 2026-08-20 this swallowed 46 consecutive writes during the
// idea-bank conversion to due-date selection, caught only because the whole
// board was read back before reporting.
//
// WHY THE SCHEMA WARNS NOBODY: add-loop documents the dependency in the
// OPPOSITE direction — the due date is required WHEN the marker is "dated" —
// which reads as a constraint on the marker, not on the date. Nothing said the
// date was inert on its own.
//
// THE FIX CHOSEN: refuse. Loop #476 offered two acceptable fixes and called
// refusal the safer one, and it is the one that matches this file's other
// guards: the date is never accepted-and-dropped, and the error names the
// missing marker. Implicitly promoting the row to "dated" was the alternative
// and was rejected because a marker change moves a row between the hot list and
// the backlog — a silent side effect on a render Joe reads is a worse cure than
// the disease.
//
// EVERY TEST HERE READS THE ROW BACK. Asserting on the verb's return value is
// the exact failure being fixed, so the fake below applies each `update
// loop_item set ...` to its stored row and the assertions look at that.
//
// Run with: node --test mcp-server/test/loop-due-date-persistence.test.mjs

import { test } from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError } from "../src/tools.js";

const ids = {
  joe: "10000000-0000-0000-0000-000000000002",
  loop: "3f7c1d90-1111-4222-8333-444455556666",
};
const joe = { id: ids.joe, slug: "joe", display: "Joe", human: true,
  via: "mcp", client_id: "claude" };

class LoopFake {
  constructor({ marker = "none", due_on = null, kind = "open_loop" } = {}) {
    this.loop = { id: ids.loop, kind, number: "476", status: "open",
      marker, due_on, marker_literal: null, close_outcome: null,
      section: "backlog", version: 1, created_at: "2026-08-20T00:00:00.000Z" };
    this.events = [];
    this.calls = new Map();
  }

  // THE READ-BACK. Apply the set-list to the stored row exactly as Postgres
  // would, so a test can ask what the record now holds rather than what the
  // handler said it did.
  #apply(sql, params) {
    const setList = sql.slice(sql.indexOf("set ") + 4, sql.lastIndexOf(" where "));
    for (const m of setList.matchAll(/([a-z_]+)=\$(\d+)/g)) {
      const [, col, n] = m;
      this.loop[col] = params[Number(n) - 1];
    }
  }

  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.startsWith("select request_hash, response")) {
      const prior = this.calls.get(params[0]);
      return { rows: prior ? [prior] : [] };
    }
    if (sql.includes("from loop_item li join loop_block lb") && sql.includes("where li.id = $1"))
      return { rows: params[0] === this.loop.id ? [{ ...this.loop }] : [] };
    if (sql.startsWith("select version from loop_item where id=$1 for update"))
      return { rows: [{ version: this.loop.version }] };
    if (sql.startsWith("select created_at from loop_item where id=$1"))
      return { rows: [{ created_at: this.loop.created_at }] };
    if (sql.includes("from event e join actor a on a.id=e.actor_id")) return { rows: [] };
    if (sql.startsWith("select id from loop_item where kind=$1 and number=$2 and status='open'"))
      return { rows: [] };
    if (sql.startsWith("select id, rel_path from loop_block")) return { rows: [] };
    if (sql.startsWith("select coalesce(max(render_seq)")) return { rows: [{ n: 0 }] };
    if (sql.startsWith("update loop_item set")) { this.#apply(sql, params); return { rows: [] }; }
    if (sql.startsWith("insert into event")) {
      this.events.push({ verb: params[2] });
      return { rows: [] };
    }
    if (sql.startsWith("insert into tool_call")) {
      // BY POSITION, from the insert's own parameter order, because that is what
      // the row coming back out of Postgres would carry. Storing only the hash
      // and the response made every identity column read as undefined on replay,
      // so a replay check comparing actor, tenant or session would judge the
      // call against a row that had forgotten who made it -- and pass, which is
      // the direction that does not announce itself.
      this.calls.set(params[0], {
        request_hash: params[3],
        response: JSON.parse(params[4]),
        actor_id: params[2],
        organization_tenant_id: params[7] ?? null,
        application_session_id: params[12] ?? null,
      });
      return { rows: [] };
    }
    throw new Error(`unhandled fake query: ${sql}`);
  }
}

const update = (db, args) => TOOLS["update-loop"].handler(db, joe, args);

// ── THE DEFECT ITSELF ──────────────────────────────────────────────────────

test("update-loop: THE BUG — a due date with no marker is REFUSED, not accepted and dropped", async () => {
  const db = new LoopFake({ marker: "none", due_on: null });
  await assert.rejects(
    () => update(db, { idempotency_key: "due-1", loop_id: ids.loop, base_version: 1,
                       due_on: "2026-09-01" }),
    (err) => {
      assert.ok(err instanceof ToolError, "must be a typed refusal, not a bare 500");
      assert.equal(err.payload.error, "due_date_needs_dated_marker");
      assert.equal(err.payload.marker, "none", "the refusal names the marker actually on the row");
      return true;
    });
  // READ THE ROW BACK — the whole point. Nothing may have moved.
  assert.equal(db.loop.due_on, null, "a refused call must not have written a date");
  assert.equal(db.loop.marker, "none", "a refused call must not have changed the marker");
});

test("update-loop: the refusal fires for bell and decision markers too, not only 'none'", async () => {
  for (const marker of ["bell", "decision"]) {
    const db = new LoopFake({ marker });
    await assert.rejects(
      () => update(db, { idempotency_key: `due-${marker}`, loop_id: ids.loop,
                         base_version: 1, due_on: "2026-09-01" }),
      (err) => err.payload?.error === "due_date_needs_dated_marker");
    assert.equal(db.loop.due_on, null, `${marker}: no date may be stored`);
  }
});

test("update-loop: a date explicitly cleared alongside a marker change is not the bug", async () => {
  // Passing marker 'none' on a dated row SHOULD null the date. That is a
  // deliberate demotion, and the refusal must not block it.
  const db = new LoopFake({ marker: "dated", due_on: "2026-09-01" });
  await update(db, { idempotency_key: "demote-1", loop_id: ids.loop, base_version: 1,
                     marker: "none" });
  assert.equal(db.loop.marker, "none");
  assert.equal(db.loop.due_on, null, "demoting off 'dated' correctly clears the date");
});

// ── THE PATHS THAT MUST KEEP WORKING ───────────────────────────────────────

test("update-loop: marker + date together stores the date", async () => {
  const db = new LoopFake({ marker: "none" });
  await update(db, { idempotency_key: "due-ok", loop_id: ids.loop, base_version: 1,
                     marker: "dated", due_on: "2026-09-01" });
  assert.equal(db.loop.marker, "dated");
  assert.equal(db.loop.due_on, "2026-09-01");
  assert.equal(db.loop.marker_literal, "🗓2026-09-01");
});

test("update-loop: RESCHEDULING an already-dated row needs no marker repeated", async () => {
  // This is the shape that must NOT be caught by the new refusal: the row is
  // already 'dated', so a bare due_on is unambiguous and has always worked.
  const db = new LoopFake({ marker: "dated", due_on: "2026-09-01" });
  await update(db, { idempotency_key: "resched-1", loop_id: ids.loop, base_version: 1,
                     due_on: "2026-10-15" });
  assert.equal(db.loop.marker, "dated");
  assert.equal(db.loop.due_on, "2026-10-15", "the new date must actually be stored");
});

test("update-loop: a 'dated' marker with no date anywhere is still refused", async () => {
  const db = new LoopFake({ marker: "none", due_on: null });
  await assert.rejects(
    () => update(db, { idempotency_key: "dated-nodate", loop_id: ids.loop,
                       base_version: 1, marker: "dated" }),
    (err) => err.payload?.error === "dated_marker_needs_date");
  assert.equal(db.loop.marker, "none");
});

// ── THE MIRROR CASES IN add-loop ───────────────────────────────────────────
// Loop #476 asked for two things beyond the update-loop fix: the mirror case on
// the creation verb, and a sweep for any OTHER field whose persistence silently
// depends on a second field. Both landed here.
//
// SWEPT AND CLEARED, so nobody re-derives it: patch-deal-field's
// `value.state === "parked" ? value.reason : null` looks like the same defect
// and is not — assertDealRoomField already throws
// `active_deal_has_no_parking_reason` before that ternary can drop anything, so
// those lines are unreachable redundancy rather than a silent discard.
//
// FOUND AND FIXED: add-loop had two.
//   1. `blockerGated ? args.blocker : null` — a blocker passed on a team_loop,
//      action_required or idea row was accepted and thrown away, because the
//      blocker gate only applies to open_loop.
//   2. an explicit non-dated marker alongside a due date STORED the date while
//      leaving the row on the other marker, so the date was inert: no render
//      reads due_on except on a dated row. Not dropped, but not honoured
//      either, which is the same lie in a different shape.

class AddLoopFake {
  constructor() {
    this.inserted = null;
    this.calls = new Map();
  }
  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.startsWith("select request_hash, response")) return { rows: [] };
    if (sql.startsWith("select id, rel_path, col_order from loop_block") ||
        sql.startsWith("select id, rel_path, renders_closed from loop_block") ||
        sql.startsWith("select id, rel_path from loop_block"))
      // Echo back whichever section add-loop asked for — the block it picks is
      // kind-dependent ('parked' for an idea, 'open' for a team_loop) and a fake
      // that answers only one of them tests the fake, not the verb.
      return { rows: [{ id: "block-1", rel_path: "00_Context/idea-bank.md",
                        block_key: params[1], col_order: null, renders_closed: false }] };
    if (sql.startsWith("select coalesce(max(render_seq)")) return { rows: [{ n: 0 }] };
    if (sql.includes("from loop_item") && sql.includes("max(")) return { rows: [{ n: 500 }] };
    if (sql.startsWith("insert into loop_item")) {
      this.inserted = params;
      return { rows: [{ id: "11111111-2222-3333-4444-555555555555" }] };
    }
    if (sql.startsWith("insert into event")) return { rows: [] };
    if (sql.startsWith("insert into tool_call")) return { rows: [] };
    return { rows: [] };
  }
}

const addLoop = (db, args) => TOOLS["add-loop"].handler(db, joe, args);

test("add-loop: a blocker on a kind that does not carry one is REFUSED, not dropped", async () => {
  const db = new AddLoopFake();
  await assert.rejects(
    () => addLoop(db, { idempotency_key: "al-1", kind: "idea", owner: "joe",
                        title: "a parked idea", blocker: "other_lane",
                        blocker_detail: "some other lane picks this up later" }),
    (err) => {
      assert.ok(err instanceof ToolError);
      assert.equal(err.payload.error, "blocker_not_carried_by_kind");
      assert.equal(err.payload.kind, "idea");
      return true;
    });
  assert.equal(db.inserted, null, "a refused call must not have inserted a row");
});

test("add-loop: an explicit non-dated marker alongside a due date is REFUSED", async () => {
  const db = new AddLoopFake();
  await assert.rejects(
    () => addLoop(db, { idempotency_key: "al-2", kind: "idea", owner: "joe",
                        title: "bell and a date", marker: "bell", due_on: "2026-09-01" }),
    (err) => err.payload?.error === "due_date_needs_dated_marker");
  assert.equal(db.inserted, null, "a refused call must not have inserted a row");
});

test("add-loop: a due date with NO marker still infers 'dated' — that path is unchanged", async () => {
  const db = new AddLoopFake();
  const res = await addLoop(db, { idempotency_key: "al-3", kind: "idea", owner: "joe",
                                  title: "dated by inference", due_on: "2026-09-01" });
  assert.equal(res.ok, true);
  assert.ok(db.inserted.includes("dated"), "marker must have been inferred as dated");
  assert.ok(db.inserted.includes("2026-09-01"), "the date must actually be stored");
});

for (const owner of ["joe", "dell"]) {
  test(`an agent can add and reassign a loop to ${owner}`, async () => {
    const agent = { id: "10000000-0000-0000-0000-000000000009", slug: "codex", human: false, via: "agent-token" };
    const added = new AddLoopFake();
    const result = await TOOLS["add-loop"].handler(added, agent, {
      idempotency_key: `agent-loop-${owner}`, kind: "open_loop", owner, number: "701",
      body: "Example partner follow-up", blocker: "other_lane", blocker_detail: "Waiting for example.test reply",
    });
    assert.equal(result.ok, true);
    assert.equal(added.inserted[6], owner);
    assert.equal(added.inserted[16], agent.id);
    const updated = new LoopFake();
    await TOOLS["update-loop"].handler(updated, agent, {
      idempotency_key: `agent-reassign-${owner}`, loop_id: ids.loop, base_version: 1, owner,
    });
    assert.equal(updated.loop.owner, owner);
    assert.equal(updated.loop.updated_by, agent.id);
  });
}
