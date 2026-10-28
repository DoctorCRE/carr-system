// loop-renumber.test.mjs — update-loop's `number` field, the verb half of
// loop #306 (2026-08-13). Migration 0112 is the database half.
//
// THE DEFECT. Two OPEN rows of the same kind could carry the same number, and
// two pairs did (#95, #88). Every verb that resolves by number — update-loop,
// close-loop, read-loop — refuses on an ambiguous one. That refusal is correct
// and still a failure: a human saying "close 95" is saying something the system
// cannot act on, and it surfaces as a refusal, so it reads like a broken verb
// rather than broken data. Anyone routing around it with loop_id silently picks
// whichever row they happened to look up, which is how the wrong loop closes.
// update-loop's whitelist covered title, body, owner, unblocks, source_note,
// domain, blocker and marker — everything except the one column that was wrong.
//
// What these tests pin down:
//   1. a renumber actually writes `number` and reports from/to,
//   2. it REFUSES without renumber_reason (rule 7105955b: a renumbered row is
//      not an abandoned one, and the note has to say so, because the old number
//      survives in other rows' prose and in every render),
//   3. it refuses a number another OPEN row of the same kind holds, returning
//      the colliding id rather than a constraint name,
//   4. the same number on a DIFFERENT kind is not a collision — open_loop #4 and
//      idea #4 are separate series, which is why 0112's index is scoped by kind,
//   5. a non-numeric number is refused, since the renders sort on this text,
//   6. the event carries the old number, so the change is traceable.
//
// Run with: node --test mcp-server/test/loop-renumber.test.mjs

import { test } from "node:test";
import assert from "node:assert/strict";
import { TOOLS, ToolError } from "../src/tools.js";
import { derivedIdempotencyKey } from "../src/derived-key.ts";

const ids = {
  joe: "10000000-0000-0000-0000-000000000002",
  keep: "f7b6b750-0e2c-4124-b7a8-38a97da1eceb",
  other: "1904e222-cd18-47f3-9b45-7e17147a9283",
};
const joe = { id: ids.joe, slug: "joe", display: "Joe", human: true,
  via: "mcp", client_id: "claude" };

class RenumberFake {
  constructor({ siblings = [] } = {}) {
    this.loop = { id: ids.keep, kind: "open_loop", number: "95", status: "open",
      marker: "none", due_on: null, close_outcome: null, section: "hot",
      version: 3, created_at: "2026-08-01T00:00:00.000Z" };
    // other OPEN rows, for the collision check: {id, kind, number}
    this.siblings = siblings;
    this.events = [];
    this.calls = new Map();
    this.updates = [];
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
    if (sql.startsWith("select id from loop_item where kind=$1 and number=$2 and status='open'")) {
      const [kind, number, notId] = params;
      return { rows: this.siblings
        .filter(s => s.kind === kind && s.number === number && s.id !== notId)
        .map(s => ({ id: s.id })) };
    }
    if (sql.startsWith("select id, rel_path from loop_block")) return { rows: [] };
    if (sql.startsWith("select coalesce(max(render_seq)")) return { rows: [{ n: 0 }] };
    if (sql.startsWith("update loop_item set")) {
      this.updates.push({ sql, params });
      return { rows: [] };
    }
    if (sql.startsWith("insert into event")) {
      this.events.push({ verb: params[2],
        old_value: params[6] ? JSON.parse(params[6]) : null,
        new_value: params[7] ? JSON.parse(params[7]) : null });
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

const call = (db, args) => TOOLS["update-loop"].handler(db, joe, args);

test("update-loop: a renumber writes the column and reports from/to", async () => {
  const db = new RenumberFake();
  const res = await call(db, { idempotency_key: "00000571-0000-4000-8000-000000000190", loop_id: ids.keep, base_version: 3,
    number: "363", renumber_reason: "RENUMBERED, NOT ABANDONED — #95 now names the newsletter row only." });
  assert.equal(res.ok, true);
  assert.equal(res.number, "363");
  assert.deepEqual(res.renumbered.from, "95");
  assert.deepEqual(res.renumbered.to, "363");
  assert.equal(db.updates.length, 1);
  assert.ok(db.updates[0].sql.includes("number=$"), "the update must actually set number");
  assert.ok(db.updates[0].params.includes("363"));
});

test("update-loop: the event carries the OLD number, so the change is traceable", async () => {
  const db = new RenumberFake();
  await call(db, { idempotency_key: "00000571-0000-4000-8000-000000000191", loop_id: ids.keep, base_version: 3,
    number: "363", renumber_reason: "RENUMBERED, NOT ABANDONED — bookkeeping only." });
  const ev = db.events.at(-1);
  assert.equal(ev.old_value.number, "95");
  assert.equal(ev.new_value.renumbered.to, "363");
  assert.match(ev.new_value.renumbered.reason, /^RENUMBERED, NOT ABANDONED/);
});

test("update-loop: RULE 7105955b — a renumber without a reason is refused", async () => {
  const db = new RenumberFake();
  await assert.rejects(
    () => call(db, { idempotency_key: "00000571-0000-4000-8000-000000000192", loop_id: ids.keep, base_version: 3, number: "363" }),
    (err) => {
      assert.ok(err instanceof ToolError);
      assert.equal(err.payload.error, "renumber_reason_required");
      return true;
    });
  assert.equal(db.updates.length, 0, "nothing may be written when the reason is missing");
});

test("update-loop: a number another OPEN row of the same kind holds is refused, with the id", async () => {
  const db = new RenumberFake({ siblings: [
    { id: ids.other, kind: "open_loop", number: "88" },
  ] });
  await assert.rejects(
    () => call(db, { idempotency_key: "00000571-0000-4000-8000-000000000193", loop_id: ids.keep, base_version: 3,
      number: "88", renumber_reason: "RENUMBERED, NOT ABANDONED — trying to collide." }),
    (err) => {
      assert.ok(err instanceof ToolError);
      assert.equal(err.payload.error, "number_taken");
      assert.deepEqual(err.payload.held_by, [ids.other]);
      return true;
    });
  assert.equal(db.updates.length, 0);
});

test("update-loop: the same number on a DIFFERENT kind is not a collision", async () => {
  // open_loop #4 and idea #4 are separate series — the idea bank numbers its own
  // rows from 1. This is why migration 0112's index is (kind, number), not number.
  const db = new RenumberFake({ siblings: [
    { id: ids.other, kind: "idea", number: "363" },
  ] });
  const res = await call(db, { idempotency_key: "00000571-0000-4000-8000-000000000194", loop_id: ids.keep, base_version: 3,
    number: "363", renumber_reason: "RENUMBERED, NOT ABANDONED — idea #363 is a different series." });
  assert.equal(res.number, "363");
});

test("update-loop: a non-numeric number is refused — the renders sort on this text", async () => {
  const db = new RenumberFake();
  for (const bad of ["95a", "ninety-five", "#", "12.5"]) {
    await assert.rejects(
      () => call(db, { idempotency_key: derivedIdempotencyKey("test", `rn-bad-${bad}`), loop_id: ids.keep, base_version: 3,
        number: bad, renumber_reason: "RENUMBERED, NOT ABANDONED — should not land." }),
      (err) => {
        assert.ok(err instanceof ToolError);
        assert.equal(err.payload.error, "bad_number");
        return true;
      });
  }
  assert.equal(db.updates.length, 0);
});

test("update-loop: a leading # is tolerated, because that is how a human writes it", async () => {
  const db = new RenumberFake();
  const res = await call(db, { idempotency_key: "00000571-0000-4000-8000-000000000195", loop_id: ids.keep, base_version: 3,
    number: "#363", renumber_reason: "RENUMBERED, NOT ABANDONED — hash form." });
  assert.equal(res.number, "363");
});

test("update-loop: renumbering to the number it already has is a no-op, not a false collision", async () => {
  const db = new RenumberFake();
  await assert.rejects(
    () => call(db, { idempotency_key: "00000571-0000-4000-8000-000000000196", loop_id: ids.keep, base_version: 3,
      number: "95", renumber_reason: "RENUMBERED, NOT ABANDONED — same value." }),
    (err) => {
      assert.ok(err instanceof ToolError);
      // no other field passed, so there is genuinely nothing to update
      assert.equal(err.payload.error, "nothing_to_update");
      return true;
    });
  assert.equal(db.updates.length, 0);
});
