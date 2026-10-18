export const JOURNEY_ONE_DEADLINE_CONTRACT = Object.freeze({
  timezone: "America/Chicago", calendar_days: 30,
  clock_origin_gate_id: "foundation-assurance-minimum-accepted",
  clock_origin_rule: "observed_at of the first current passing foundation-assurance-minimum receipt that makes Journey 1 admissible",
  // Hours, not days: the pause budget is actual elapsed time, and a day is the
  // one unit this file exists to stop anybody from assuming is 24 hours long.
  maximum_external_blocker_pause_hours: 120,
  reset_policy: "never_reset_or_rebase_elapsed_history",
  amendment_policy: "verified_partner_exact_hash_amendment_preserves_original_origin_and_elapsed_history",
  clock_terminus_gate_id: "journey-one-kernel-production-accepted",
  kernel_obligation_decision_ids: Object.freeze(["Q002.D1", "Q014.D1", "Q123.D1"]),
  miss_consequence: "mark_deadline_missed_require_replan_preserve_origin_and_elapsed_continue_safe_construction_without_claiming_deadline_success",
});
const HOUR = 3600000;
const freeze = Object.freeze;
function stamp(value) {
  const m = typeof value === "string" && /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,3}))?(Z|[+-]\d{2}:\d{2})$/.exec(value);
  if (!m) { throw new RangeError("invalid_timestamp"); }
  const [y, mo, d, h, mi, s] = m.slice(1, 7).map(Number);
  const date = new Date(0); date.setUTCFullYear(y, mo - 1, d); date.setUTCHours(h, mi, s, 0);
  if (date.getUTCFullYear() !== y || date.getUTCMonth() !== mo - 1 || date.getUTCDate() !== d || h > 23 || mi > 59 || s > 59 ||
      (m[8] !== "Z" && (Number(m[8].slice(1, 3)) > 23 || Number(m[8].slice(4)) > 59))) { throw new RangeError("invalid_timestamp"); }
  const ms = Date.parse(value); if (!Number.isSafeInteger(ms)) { throw new RangeError("invalid_timestamp"); }
  return ms;
}
const iso = ms => new Date(ms).toISOString();
const format = new Intl.DateTimeFormat("en-US", { timeZone: "America/Chicago", hourCycle: "h23",
  year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
function wall(ms) {
  const p = Object.fromEntries(format.formatToParts(ms).map(x => [x.type, x.value]));
  const date = new Date(0);
  date.setUTCFullYear(Number(p.year), Number(p.month) - 1, Number(p.day));
  date.setUTCHours(Number(p.hour), Number(p.minute), Number(p.second), ((ms % 1000) + 1000) % 1000);
  return date.getTime();
}
/** Deadline resolutions, recorded in state so the three cases stay distinguishable. */
export const DEADLINE_PLAIN = "same_chicago_wall_time_after_30_dates";
export const DEADLINE_GAP_SHIFTED = "chicago_wall_time_gap_shifted_forward_by_gap_length";
export const DEADLINE_OVERLAP_ORIGIN_OFFSET = "chicago_wall_time_overlap_resolved_to_origin_utc_offset";
export const DEADLINE_UNSUPPORTED = "unsupported_chicago_calendar_case";

/** Every Chicago UTC offset in force within two days of a wall-clock target. */
function offsetsAround(target) {
  const offsets = new Set();
  for (let h = -48; h <= 48; h += 6) { const probe = target + h * HOUR; offsets.add(wall(probe) - probe); }
  return [...offsets];
}
/** The instants whose Chicago wall time is exactly `target`: 0 in a gap, 2 in a fold. */
function instantsAt(target) {
  return offsetsAround(target).map(offset => target - offset)
    .filter(ms => wall(ms) === target).sort((a, b) => a - b);
}
/**
 * The same Chicago wall time 30 CALENDAR DATES later — never 720 fixed hours: an
 * ordinary DST crossing makes the interval 719 or 721 hours and both are correct.
 *
 * The two boundary cases are DECIDED, not inferred, and the decision is recorded
 * so a reader can tell which one produced a deadline (JOURNEY_ONE_DST_RULE_REF):
 *
 *   FOLD (the target wall time happens twice, autumn). Take the occurrence whose
 *   UTC offset equals the ORIGIN's — for a clock started on daylight time that is
 *   the first, daylight occurrence.
 *   GAP (the target wall time never happens, spring). Shift the wall time forward
 *   by exactly the gap length, so 02:30 becomes 03:30 rather than snapping to the
 *   03:00 transition instant.
 *
 * Anything those two rules do not settle FAILS CLOSED as an unresolved deadline
 * that requires a replan; it is never left as a deadline that can quietly never
 * be missed. No America/Chicago instant reaches that branch under current tzdata
 * (the zone has exactly two offsets, so a fold always contains the origin's and a
 * gap always resolves one date forward), and it is kept because a calendar this
 * kernel cannot resolve must stop the clock rather than silence it.
 */
export function chicagoThirtyDayDeadline(origin) {
  const start = stamp(origin);
  const targetDate = new Date(wall(start)); targetDate.setUTCDate(targetDate.getUTCDate() + 30);
  const target = targetDate.getTime();
  const unsupported = () => freeze({ status: "unresolved_deadline", due_at: null, reason_id: DEADLINE_UNSUPPORTED });
  const resolved = (ms, reason) => freeze({ status: "resolved", due_at: iso(ms), reason_id: reason });

  const candidates = instantsAt(target);
  if (candidates.length === 1) return resolved(candidates[0], DEADLINE_PLAIN);
  if (candidates.length > 1) {
    const originOffset = wall(start) - start;
    const preferred = candidates.filter(ms => wall(ms) - ms === originOffset);
    return preferred.length === 1 ? resolved(preferred[0], DEADLINE_OVERLAP_ORIGIN_OFFSET) : unsupported();
  }
  // A gap. Reading the target under the PRE-transition offset lands after the
  // transition and reports a wall time exactly one gap later, which is the gap
  // length without assuming it is an hour.
  const gaps = offsetsAround(target).map(offset => wall(target - offset) - target).filter(delta => delta > 0);
  if (!gaps.length) return unsupported();
  const shifted = instantsAt(target + Math.min(...gaps));
  return shifted.length === 1 ? resolved(shifted[0], DEADLINE_GAP_SHIFTED) : unsupported();
}
