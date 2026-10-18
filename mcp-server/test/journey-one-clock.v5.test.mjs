import test from "node:test";
import assert from "node:assert/strict";
import { chicagoThirtyDayDeadline, DEADLINE_PLAIN, DEADLINE_GAP_SHIFTED,
  DEADLINE_OVERLAP_ORIGIN_OFFSET } from "../src/journey-one-clock.v5.js";
const HOUR = 3600000;
const ORIGIN = "2026-09-09T15:00:00.000Z";
for (const [origin, due, hours] of [
  // An ordinary spring crossing is 719 elapsed hours and an ordinary autumn one
  // is 721. Thirty calendar dates is never converted into a fixed 720.
  ["2026-02-20T16:15:00.000Z", "2026-03-22T15:15:00.000Z", 719],
  ["2026-10-10T15:15:00.000Z", "2026-11-09T16:15:00.000Z", 721],
  [ORIGIN, "2026-10-09T15:00:00.000Z", 720],
]) test(`thirty Chicago dates from ${origin} is ${hours} elapsed hours`, () => {
  const result = chicagoThirtyDayDeadline(origin);
  assert.equal(result.due_at, due);
  assert.equal(result.reason_id, DEADLINE_PLAIN);
  assert.equal((Date.parse(due) - Date.parse(origin)) / HOUR, hours);
});

test("a spring-gap target shifts forward by the gap length rather than snapping to the transition", () => {
  // 02:30 Chicago on 6 February 2026 is CST. Thirty dates later is 8 March, the
  // morning the wall clock jumps 02:00 to 03:00, so 02:30 never happens. The
  // decided convention shifts the WALL TIME forward by exactly the gap: 03:30
  // CDT, not the 03:00 transition instant.
  const result = chicagoThirtyDayDeadline("2026-02-06T08:30:00.000Z");
  assert.equal(result.status, "resolved");
  assert.equal(result.due_at, "2026-03-08T08:30:00.000Z");
  assert.equal(result.reason_id, DEADLINE_GAP_SHIFTED);
  // 03:30 CDT is -05:00, so the shifted wall time is one hour past the target
  // and NOT the 08:00Z transition instant that a snap would have produced.
  assert.notEqual(result.due_at, "2026-03-08T08:00:00.000Z");
});

test("an autumn-fold target takes the occurrence with the origin's own UTC offset", () => {
  // 01:30 Chicago on 2 October 2026 is CDT. Thirty dates later is 1 November,
  // when 01:30 happens twice. The origin ran on daylight time, so the deadline
  // is the first, daylight occurrence: 01:30 CDT, 06:30Z.
  const result = chicagoThirtyDayDeadline("2026-10-02T06:30:00.000Z");
  assert.equal(result.status, "resolved");
  assert.equal(result.due_at, "2026-11-01T06:30:00.000Z");
  assert.equal(result.reason_id, DEADLINE_OVERLAP_ORIGIN_OFFSET);
  // The standard-time occurrence an hour later is the one NOT taken.
  assert.notEqual(result.due_at, "2026-11-01T07:30:00.000Z");
});
