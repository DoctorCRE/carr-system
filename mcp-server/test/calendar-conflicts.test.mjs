import test from "node:test";
import assert from "node:assert/strict";
import { executeRegisteredTool } from "../src/tools.js";

const JOE = { slug: "joe", human: true, via: "oauth-google" };
const base = Date.now() + 3_600_000;
const at = minutes => new Date(base + minutes * 60_000).toISOString();
const meeting = (title, start, end, extra = {}) => ({ title, start: at(start), end: at(end), ...extra });

async function read(items, extra = {}) {
  const snapshot = { schema: "carr-calendar-agenda.v1", observed_at: new Date().toISOString(),
    warnings: [], items, ...extra };
  const c = { query: async sql => {
    if (sql.includes("from board_snapshot")) return { rows: [{ snapshot_json: snapshot }] };
    throw new Error(`Unexpected query ${sql}`);
  } };
  return executeRegisteredTool(c, JOE, "calendar-meeting-prep", {});
}

test("overlapping meetings identify each other and the exact competing interval", async () => {
  const result = await read([meeting("Synthetic lease review", 0, 60), meeting("Synthetic site call", 30, 90)]);
  assert.deepEqual(result.items[0].schedule_conflicts, [{ title: "Synthetic site call",
    start: at(30), end: at(90), overlap_start: at(30), overlap_end: at(60) }]);
  assert.deepEqual(result.items[1].schedule_conflicts, [{ title: "Synthetic lease review",
    start: at(0), end: at(60), overlap_start: at(30), overlap_end: at(60) }]);
  assert.ok(result.items.every(item => item.schedule_conflict && item.schedule_conflicts_omitted === 0));
});

test("contained and same-start meetings report only their intersecting interval", async () => {
  const result = await read([meeting("Synthetic long review", 0, 90),
    meeting("Synthetic brief call", 0, 15), meeting("Synthetic discussion", 30, 45)]);
  const long = result.items.find(item => item.title === "Synthetic long review");
  assert.deepEqual(long.schedule_conflicts, [
    { title: "Synthetic brief call", start: at(0), end: at(15), overlap_start: at(0), overlap_end: at(15) },
    { title: "Synthetic discussion", start: at(30), end: at(45), overlap_start: at(30), overlap_end: at(45) },
  ]);
  assert.equal(result.items.find(item => item.title === "Synthetic brief call").schedule_conflicts.length, 1);
});

test("adjacent, all-day and zero-duration entries do not compete for meeting time", async () => {
  const result = await read([meeting("Synthetic first call", 0, 30), meeting("Synthetic second call", 30, 60),
    meeting("Synthetic reminder", 10, 10), meeting("Synthetic holiday", 0, 1440, { all_day: true })]);
  for (const item of result.items) {
    assert.equal(item.schedule_conflict, false);
    assert.deepEqual(item.schedule_conflicts, []);
    assert.equal(item.schedule_conflicts_omitted, 0);
  }
});

test("busy meetings retain five chronological details and count every omitted conflict", async () => {
  const result = await read([meeting("Synthetic long review", 0, 120),
    ...Array.from({ length: 7 }, (_, i) => meeting(`Synthetic call ${i + 1}`, (i + 1) * 10, (i + 1) * 10 + 5))]);
  assert.deepEqual(result.items[0].schedule_conflicts.map(item => item.title),
    ["Synthetic call 1", "Synthetic call 2", "Synthetic call 3", "Synthetic call 4", "Synthetic call 5"]);
  assert.equal(result.items[0].schedule_conflicts_omitted, 2);
  assert.equal(result.items[0].schedule_conflict, true);
  assert.equal(result.items.at(-1).schedule_conflicts[0].title, "Synthetic long review");
});

test("a visible meeting explains a conflict with a meeting beyond the agenda display limit", async () => {
  const result = await read([
    ...Array.from({ length: 99 }, (_, i) => meeting(`Synthetic reminder ${i}`, 0, 0)),
    meeting("Synthetic visible review", 30, 90), meeting("Synthetic omitted call", 60, 120),
  ]);
  assert.equal(result.items.length, 100);
  assert.deepEqual(result.items.at(-1).schedule_conflicts, [{ title: "Synthetic omitted call",
    start: at(60), end: at(120), overlap_start: at(60), overlap_end: at(90) }]);
  assert.equal(result.items.at(-1).schedule_conflict, true);
  assert.ok(result.notes.includes("Only the first 100 meetings are shown."));
});

test("conflict details preserve stale-source and attendance uncertainty warnings", async () => {
  const result = await read([meeting("Synthetic review", 0, 60), meeting("Synthetic call", 30, 90)],
    { observed_at: new Date(Date.now() - 19 * 3_600_000).toISOString(),
      warnings: [{ code: "responses_unverified" }, { code: "time_changed" }] });
  assert.equal(result.state, "partial");
  assert.equal(result.sources.outlook_calendar.state, "stale");
  assert.match(result.notes.join(" "), /declined.*cancelled or deleted/);
  assert.match(result.notes.join(" "), /more than one stored time/);
  assert.equal(result.items[0].schedule_conflicts.length, 1);
});
