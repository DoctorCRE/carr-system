// activity-occurred-at-future.test.mjs — a touch cannot have happened tomorrow.
//
// WHAT WAS FOUND, 2026-08-15, while pulling context for the vendor-level drift
// list: of 59 vendors carrying a last-touch date, FOUR were dated after today —
// 2026-08-18, two on 2026-08-26, and 2026-08-27. The nearest is three days out,
// which reads exactly like a booked meeting logged as though it had happened.
//
// WHY IT MATTERS, and it is not tidiness: last_touch is what staleness is
// measured from. A vendor whose last touch is in the future can never read as
// stale, no matter how long it has actually been since anyone spoke to them. The
// row goes quiet and no surface can tell.
//
// THE FIELD ALREADY MEANT THIS. log-activity documents occurred_at as "when it
// happened (defaults now)", past tense, and the activity table already draws
// this boundary from the other side: migration 0017 stopped a `note` moving
// last_touch because a note is annotation rather than contact. A future date
// crosses the same line in the other direction. No new ruling was needed and
// none was asked for — the doctrine store was searched for any decision on touch
// dates or scheduled contact and returned nothing.
//
// THE FUTURE ALREADY HAS ITS OWN VERBS: set-next-action for the ball you owe,
// add-critical-date for a dated deal obligation. A scheduled meeting belongs in
// those, not in the record of what has already occurred.
//
// TOLERANCE: a small skew window is allowed, because a caller's clock and the
// server's are not the same clock and a touch logged "now" can arrive a few
// seconds ahead. The window is minutes, not days — it exists for clock skew, not
// for scheduling.
//
// Run with: node --test mcp-server/test/activity-occurred-at-future.test.mjs

import { test } from "node:test";
import assert from "node:assert/strict";
import { TOOLS } from "../src/tools.js";
import { connectionRouteForTool } from "../src/mcp.js";

const ids = {
  joe: "10000000-0000-0000-0000-000000000002",
  vendor: "70000000-0000-0000-0000-000000000001",
};
const joe = { id: ids.joe, slug: "joe", display: "Joe", human: true, kind: "human",
  via: "mcp", client_id: "claude" };

const detail = (e) => JSON.stringify(e.payload ?? e.body ?? e.message ?? e);

class Fake {
  constructor() { this.inserts = []; }
  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    if (sql.startsWith("select request_hash, response")) return { rows: [] };
    if (sql.includes("v_ref_index") || sql.includes("from vendor") || sql.includes("resolve"))
      return { rows: [{ id: ids.vendor, party_id: ids.vendor, subject_type: "vendor" }] };
    if (sql.startsWith("insert into activity")) {
      this.inserts.push(params);
      return { rows: [{ id: "activity-1" }] };
    }
    if (sql.startsWith("insert into event")) return { rows: [] };
    if (sql.startsWith("update")) return { rows: [] };
    return { rows: [] };
  }
}

const iso = (msFromNow) => new Date(Date.now() + msFromNow).toISOString();
const DAY = 86_400_000;

const logIt = (fake, occurred_at, kind = "call") =>
  TOOLS["log-activity"].handler(fake, joe, {
    idempotency_key: "k-" + Math.random().toString(36).slice(2),
    ref: "V-SUP-007", kind, summary: "spoke with them", occurred_at,
  });

test("a touch dated days in the future is refused", async () => {
  const fake = new Fake();
  await assert.rejects(() => logIt(fake, iso(3 * DAY)), (e) => {
    assert.match(detail(e), /occurred_at_in_future/, "the refusal names the shape");
    return true;
  });
  assert.deepEqual(fake.inserts, [], "and nothing is written");
});

test("the refusal points at the verbs the future actually has", async () => {
  const fake = new Fake();
  await assert.rejects(() => logIt(fake, iso(12 * DAY)), (e) => {
    const body = detail(e);
    assert.match(body, /set-next-action/, "the ball you owe has its own verb");
    assert.match(body, /add-critical-date/, "and so does a dated obligation");
    return true;
  });
});

test("the refusal explains the staleness consequence, not just the rule", async () => {
  const fake = new Fake();
  await assert.rejects(() => logIt(fake, iso(2 * DAY)), (e) => {
    assert.match(detail(e).toLowerCase(), /stale/,
      "a future last touch means the record can never read as stale");
    return true;
  });
});

test("a touch dated now is fine", async () => {
  const fake = new Fake();
  const out = await logIt(fake, new Date().toISOString());
  assert.equal(out.ok, true);
});

test("a touch dated in the past is fine — backfilling is normal work", async () => {
  const fake = new Fake();
  const out = await logIt(fake, iso(-30 * DAY));
  assert.equal(out.ok, true, "logging last month's meeting today must keep working");
});

test("omitting the date entirely is fine — it defaults to now", async () => {
  const fake = new Fake();
  const out = await TOOLS["log-activity"].handler(fake, joe, {
    idempotency_key: "k-none", ref: "V-SUP-007", kind: "call", summary: "called them",
  });
  assert.equal(out.ok, true);
});

test("a few seconds ahead is tolerated, because two clocks are not one clock", async () => {
  const fake = new Fake();
  const out = await logIt(fake, iso(30_000));
  assert.equal(out.ok, true,
    "the window exists for clock skew between caller and server, not for scheduling");
});

test("the tolerance is minutes, not hours — it cannot be used to schedule", async () => {
  const fake = new Fake();
  await assert.rejects(() => logIt(fake, iso(6 * 3_600_000)),
    /occurred_at_in_future/,
    "six hours ahead is a plan, not clock skew");
});

test("stamp-touch is covered too, since it routes through the same handler", async () => {
  const fake = new Fake();
  await assert.rejects(() => TOOLS["stamp-touch"].handler(fake, joe, {
    idempotency_key: "k-stamp", ref: "V-SUP-007", summary: "called",
    occurred_at: iso(5 * DAY),
  }), /occurred_at_in_future/,
    "the truck-shorthand verb must not be the way around the guard");
});

test("an unparseable date is left alone, not guessed at", async () => {
  const fake = new Fake();
  const out = await logIt(fake, "sometime last week");
  assert.equal(out.ok, true,
    "this guard judges FUTURE dates; malformed input is a different problem and not this one to invent a verdict on");
});

// Upgrade reconciliation reads canonical capture evidence from the existing record reader.
test("record history includes complete calendar replay evidence", async () => {
  const calls = [];
  const history = [{ key: "calcap-person@example.test-2026-09-25",
    summary: "Meeting: Original", activity_id: "legacy-activity" }];
  const c = { query: async (sql, params) => {
    calls.push({ sql, params });
    if (sql.includes("v_ref_index")) return { rows: [{ id: ids.vendor, subject_id: ids.vendor, subject_type: "vendor" }] };
    if (sql.includes("from tool_call")) return { rows: history };
    return { rows: [] };
  }};
  const result = await TOOLS["catch-me-up"].handler(c, joe, { ref: "V-SUP-007" });
  assert.deepEqual(result.calendar_history, history);
  assert.match(calls.at(-1).sql, /actor_id/);
  assert.deepEqual(calls.at(-1).params, [joe.id, ids.vendor]);
  assert.equal(TOOLS["catch-me-up"].write, false);
});
test("truncated calendar history refuses upgrade evidence", async () => {
  const c = { query: async sql => sql.includes("v_ref_index")
    ? { rows: [{ id: ids.vendor, subject_id: ids.vendor, subject_type: "vendor" }] }
    : { rows: sql.includes("from tool_call") ? Array(5001).fill({}) : [] } };
  await assert.rejects(() => TOOLS["catch-me-up"].handler(c, joe, { ref: "V-SUP-007" }));
});

test("calendar reconciliation uses the authenticated read-only writer route", () => {
  assert.equal(connectionRouteForTool(TOOLS["catch-me-up"]), "writer_read_only");
  assert.equal(TOOLS["catch-me-up"].write, false);
});

test("timeline compositions retain the authenticated read-only calendar-history route", () => {
  for (const name of ["find-and-catch-up", "prepare-conversation"]) {
    assert.equal(connectionRouteForTool(TOOLS[name]), "writer_read_only", name);
    assert.equal(TOOLS[name].write, false);
  }
});
