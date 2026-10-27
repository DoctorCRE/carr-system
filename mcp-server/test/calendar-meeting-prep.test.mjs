import { NOW } from "./helpers/fixed-clock.mjs";
// Tests the authenticated meeting preparation verb with synthetic calendar and deal records.
import test from "node:test";
import assert from "node:assert/strict";
import { executeRegisteredTool } from "../src/tools.js";

const JOE = { slug: "joe", human: true, via: "oauth-google" };
const DEAL = "11111111-1111-4111-8111-111111111111";
const start = new Date(NOW + 3_600_000).toISOString();
const end = new Date(NOW + 7_200_000).toISOString();
const snapshot = () => ({ schema: "carr-calendar-agenda.v1", observed_at: new Date(NOW).toISOString(),
  warnings: [], items: [{ title: "Synthetic lease review", start, end,
    organizer: { name: "Test Client", email: "client@example.test" }, attendees: [] }] });

function client({ calendar = snapshot(), matches = [{ email: "client@example.test", id: DEAL,
  name: "Synthetic lease", client_ref: "C-TEST" }], prep = [{ id: DEAL, phase: "negotiation",
  next_step: "Review the landlord's counteroffer", next_date: "2026-11-02", last_touch: "2026-10-25", market: "Test City" }], fail = null } = {}) {
  const queries = [];
  return { queries, query: async (sql, params = []) => {
    queries.push({ sql, params });
    if (fail && sql.includes(fail)) throw new Error("private fixture failure");
    if (sql.includes("from board_snapshot")) return { rows: calendar === null ? [] : [{ snapshot_json: calendar }] };
    if (sql.includes("calendar_deal_matches")) return { rows: matches };
    if (sql.includes("calendar_meeting_prep")) return { rows: prep };
    if (/^(savepoint|release savepoint|rollback to savepoint)/.test(sql)) return { rows: [] };
    throw new Error(`Unexpected query ${sql}`);
  } };
}

const read = (c, actor = JOE, args = {}) => executeRegisteredTool(c, actor, "calendar-meeting-prep", args);

test("upcoming meetings carry possible deal preparation from the canonical open deal board", async () => {
  const result = await read(client());
  assert.equal(result.state, "ready");
  assert.equal(result.match_basis, "attendee_email_possible_match");
  assert.deepEqual(result.items[0].possible_deals[0].prep, { state: "ready", phase: "negotiation",
    next_step: "Review the landlord's counteroffer", next_date: "2026-11-02",
    last_touch: "2026-10-25", market: "Test City" });
});

test("the authenticated sponsor selects both the calendar and owned deals", async () => {
  const c = client();
  await read(c, { slug: "codex", human: false, via: "oauth-google", sponsoring_human_slug: "joe" });
  assert.deepEqual(c.queries.find(q => q.sql.includes("board_snapshot")).params, ["carr-internal", "joe", "calendar-agenda"]);
  assert.deepEqual(c.queries.find(q => q.sql.includes("calendar_deal_matches")).params, [["client@example.test"], "joe"]);
  const prep = c.queries.find(q => q.sql.includes("calendar_meeting_prep"));
  assert.deepEqual(prep.params, [[DEAL], "joe"]);
  assert.match(prep.sql, /lower\(owner\)=\$2/);
});

test("unsponsored calls and caller-selected context fail before reading a calendar", async () => {
  const c = client();
  await assert.rejects(read(c, { slug: "codex", human: false, via: "agent-token" }), /calendar_prep_requires_partner_scope/);
  await assert.rejects(read(c, JOE, { audience: "other" }), /caller_authority_field_forbidden/);
  assert.equal(c.queries.length, 0);
});

test("multiple possible deals retain their own preparation without confirming a match", async () => {
  const second = "22222222-2222-4222-8222-222222222222";
  const result = await read(client({ matches: [
    { email: "client@example.test", id: DEAL, name: "First lease", client_ref: "C-TEST" },
    { email: "client@example.test", id: second, name: "Second lease", client_ref: "C-TEST" },
  ], prep: [{ id: DEAL, phase: "research", next_step: null, next_date: null, last_touch: null, market: null },
    { id: second, phase: "legal", next_step: "Review lease", next_date: "2026-11-03", last_touch: null, market: null }] }));
  assert.equal(result.items[0].possible_deals.length, 2);
  assert.equal(result.items[0].possible_deals[0].prep.phase, "research");
  assert.equal(result.items[0].possible_deals[0].prep.next_step, null);
  assert.equal(result.items[0].possible_deals[1].prep.phase, "legal");
  assert.equal(result.match_basis, "attendee_email_possible_match");
});

test("a failed preparation query preserves the meeting and rolls back its read savepoint", async () => {
  const c = client({ fail: "from v_open_deal_room_board" });
  c.inTransaction = true;
  const result = await read(c);
  assert.equal(result.state, "partial");
  assert.equal(result.items[0].title, "Synthetic lease review");
  assert.deepEqual(result.items[0].possible_deals[0].prep, { state: "unavailable" });
  assert.deepEqual(result.sources.meeting_prep, { state: "unavailable" });
  assert.equal(JSON.stringify(result).includes("private fixture failure"), false);
  assert.ok(c.queries.some(q => q.sql === "rollback to savepoint calendar_meeting_prep"));
  assert.equal(c.queries.at(-1).sql, "release savepoint calendar_meeting_prep");
});

test("a possible deal absent from the open board has unavailable preparation", async () => {
  const result = await read(client({ prep: [] }));
  assert.equal(result.state, "partial");
  assert.deepEqual(result.sources.meeting_prep, { state: "partial" });
  assert.deepEqual(result.items[0].possible_deals[0].prep, { state: "unavailable" });
});

test("missing and failed snapshots are unavailable while a current empty calendar is empty", async () => {
  assert.equal((await read(client({ calendar: null }))).state, "unavailable");
  const c = client({ fail: "from board_snapshot" });
  c.inTransaction = true;
  assert.equal((await read(c)).state, "unavailable");
  assert.ok(c.queries.some(q => q.sql === "rollback to savepoint calendar_prep_snapshot"));
  const empty = await read(client({ calendar: { ...snapshot(), items: [] } }));
  assert.equal(empty.state, "empty");
  assert.deepEqual(empty.items, []);
});

test("stale and attendance warnings remain visible after preparation succeeds", async () => {
  const result = await read(client({ calendar: { ...snapshot(), observed_at: new Date(NOW - 19 * 3_600_000).toISOString(),
    warnings: [{ code: "responses_unverified" }] } }));
  assert.equal(result.state, "partial");
  assert.equal(result.sources.outlook_calendar.state, "stale");
  assert.ok(result.notes.some(note => note.includes("declined")));
  assert.ok(result.notes.some(note => note.includes("18 hours")));
  assert.equal(result.items[0].possible_deals[0].prep.state, "ready");
});

test("failed suggestions preserve the meeting without inventing preparation", async () => {
  const result = await read(client({ fail: "calendar_deal_matches" }));
  assert.equal(result.state, "partial");
  assert.deepEqual(result.sources.deal_suggestions, { state: "unavailable" });
  assert.deepEqual(result.items[0].possible_deals, []);
});
