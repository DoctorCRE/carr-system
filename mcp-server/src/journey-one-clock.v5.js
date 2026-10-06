// V5-M01 pure clock kernel for decision 66427d3b (Q008.D1). This is NOT a
// receipt issuer, gateway, clock, persistence layer, or live acceptance path. A
// separately installed trusted verifier authenticates the complete envelope,
// authoritative history/inventory, exact benchmark acceptance, identities, and
// policy-derived bindings. Ordinary request JSON cannot install that verifier or
// assert verification with a flag. The projection below is an internal adapter
// seam, not a new approval policy: a projection is evidence that the verifier
// read the record, never evidence that a raw producer admitted anything.
//
// WHAT REMAINS INTEGRATION WORK, stated rather than implied:
//   * Raw-schema admission, authentication and durable append/CAS adapters.
//     Returning a history does not claim it was persisted; the caller owns the
//     durable append, which is why every result carries
//     durable_history_write_required.
//   * ANTI-ROLLBACK LIVES IN THAT ADAPTER, not here. readHistory below proves a
//     history is internally consistent and self-bound; every digest in it is
//     computable by whoever supplies it, so an OLDER GENUINE history replays
//     unless the durable store compare-and-swaps on the exact prior
//     history_digest and refuses a write whose prior does not match the stored
//     one. Sticky miss_at, the recorded amendment set,
//     origin_benchmark_manifest_digest, and the completion seals —
//     completion_receipt_ttl_policy_ms, completion_artifact_digest and
//     completion_fixture_set_digest — are each defended by that CAS alone.
//     Sealing a value into the history below binds it to THAT history and
//     nothing more: an older genuine history that never carried the seal still
//     replays unless the external store refuses the write, and this file
//     fabricates no protection it cannot provide.
//     readHistory is exported NARROWLY as readJourneyOneClockHistory so that
//     adapter can ask this kernel — rather than a second, weaker validator of
//     its own — whether a stored history is still readable. That export adds no
//     clause and softens none, and a pass from it is not an anti-rollback
//     claim: see the note on the function itself.
//     For the same reason each result carries `verified_binding` BESIDE the
//     state: the state says nothing about the accepted scope it was judged
//     under, so a store had no way to derive the scope it is writing for from
//     the computation itself. It is a read-only restatement of facts already
//     enforced here, outside the hashed state, and it authenticates nothing.
//     Its v2 `authenticated_projection_digest` answers the SECOND question a
//     consumer holding its own projection has to ask — WHICH snapshot was
//     judged — because the accepted scope alone is shared by every snapshot
//     built for one program, and two valid ones differing only in their
//     completion, amendments, history or pause instants produce the same scope
//     and different clocks. It says which, never whether: this kernel hashes
//     whatever the installed verifier returned.
//   * The terminus accepts exactly one rollout-component-receipt.v1 and checks
//     `all_current_exact_distinct_pass` as an exact contract string rather than
//     re-implementing it, so "distinct" does no work at arity one.
//   * An amendment applies to a clock that already has history. The first
//     evaluation must present the pre-origin benchmark, because there is no
//     recorded original for an amendment to preserve yet.
//   * The two receipt shapes below are r7-EXACT and carry no schema_version:
//     consumer-gate-receipt.v1 is twenty-one closed fields and
//     rollout-component-receipt.v1 is twenty-two, and neither declares one. The
//     kernel discriminates on the producer step ref, which is required on both
//     and disjoint between them; a receipt carrying an added schema_version is
//     refused as `closed_shape` exactly as any other extra field is. That makes
//     the minimum receipt ONE artifact with ONE digest across the A00 seam:
//     digest(A00's proposed_receipt) is the origin_receipt_digest recorded here.
//   * `completion_expectation` and the two TTL maxima on `binding` are TRUSTED
//     PROJECTION FACTS, not caller assertions and not values read back off the
//     receipt they judge. The installed verifier must derive them from the
//     accepted kernel scope and the accepted policy exactly as it derives
//     `authority_class`; a projection that copies artifact_digest or
//     fixture_set_digest out of the completion receipt turns the binding below
//     into the tautology it exists to replace. The kernel cannot check that from
//     inside, which is why it is stated here rather than implied.
//     `completion_expectation` is nullable BECAUSE the exact kernel artifact is
//     not knowable at the origin: it is required only when a completion is
//     presented, so knowing the future artifact is never a prerequisite for
//     starting the clock.
//     THE FIRST COMPLETION SEALS BOTH OF THEM. The completion TTL policy that
//     admitted the terminus receipt and the exact artifact and fixture set it
//     was judged against are written into the history the moment a completion is
//     recorded, and a later projection that changes either one is refused BY NAME
//     rather than re-judging a receipt that was already accepted under them.
//     Before that first completion neither is sealed, so changing either one
//     while the clock is still running is an ordinary policy change.
// A SEALED HISTORY IS NEVER REWRITTEN HERE. v1 of the state schema sealed its
// origin_receipt_digest over a 22-field minimum; this file reads the r7-exact
// twenty-one, so a v1 history names an origin digest v2 cannot recompute.
// Silently re-deriving it would rebase a sealed origin, so a v1 history is
// refused BY NAME (`legacy_history_migration_required`) and moving it forward is
// an explicit migration owned by whoever owns the durable store.
// No live clock is read anywhere: every instant comes from the verified `as_of`.
// NO CLOCK HAS BEEN STARTED. Nothing in this repository has yet produced a
// minimum receipt, so there is no live origin — that is an absence of evidence
// here, not a proof of absence about the record as a whole.
// Chicago wall time requires a full-ICU Node build.
import { digest } from "./artifact-trust.js";
import { ORGANIZATION_TENANT_ID, isKnownPartner } from "./identity.js";
import { V5_NO_EFFECTS } from "./global-boundaries.v5.js";

export const JOURNEY_ONE_CLOCK_SCHEMA = "doctorcre-v5-journey-one-clock.v2";
/**
 * State schemas this kernel can no longer read, and refuses by name rather than
 * reinterpreting. v1 sealed origin_receipt_digest over a 22-field minimum (the
 * r7 twenty-one plus an added schema_version) and carried neither the TTL policy
 * that selected its origin nor the deadline resolution that produced its base
 * deadline. Re-deriving any of those from a v1 record would rebase a sealed
 * origin or silently reset a policy, so migration is explicit and external.
 * v2 additionally seals, on the first completion, the completion TTL policy that
 * admitted the terminus receipt and the exact accepted kernel scope it was judged
 * against; a migration of a v1 record that already carries a completion has to
 * supply those from the policy and scope that actually judged it, and never from
 * the recorded receipt's own values.
 */
export const JOURNEY_ONE_CLOCK_LEGACY_SCHEMAS = Object.freeze(["doctorcre-v5-journey-one-clock.v1"]);
export const JOURNEY_ONE_CLOCK_PROJECTION = "doctorcre-v5-journey-one-clock-projection.v2";
/**
 * THE VERIFIED BINDING PROJECTION, RETURNED BESIDE THE STATE AND NEVER INSIDE IT.
 *
 * A durable store has to know WHICH accepted scope the computation it is filing
 * was judged under, and the v2 state carries none of it: the state is about one
 * clock's history, not about the binding that clock was validated against. So a
 * store could previously only COMPARE a scope it was handed at construction.
 * This is that binding, read out of the projection evaluate() already
 * authenticated and enforced — the three digests every receipt and the accepted
 * benchmark had to match, the tenant, and the two gate ids the accepted deadline
 * contract names.
 *
 * IT IS A PROJECTION, NOT A NEW POWER. It admits nothing, decides nothing and
 * adds no clause; it restates facts this evaluation already refused to proceed
 * without. It is deliberately OUTSIDE the hashed state — a field added to the
 * state would change every history_digest and rebase every stored clock — and it
 * is frozen with the rest of the result, so a reader cannot edit the binding it
 * was just told was verified. A pass is not evidence about the RECORD: it says
 * the installed verifier read these values, exactly as the header says.
 *
 * v2 ADDS `authenticated_projection_digest`: WHICH PROJECTION WAS JUDGED, EXACTLY.
 *
 * The five values above are the ACCEPTED SCOPE, and a scope is shared by every
 * projection built for one program. Two different valid snapshots for the same
 * subject, candidate, policy, tenant and origin — one carrying a terminus
 * completion and one not, or two pauses with one id and one end but different
 * start instants — produce the same five values and DIFFERENT clocks. A consumer
 * holding a projection of its own therefore could not tell, from the binding
 * alone, whether the computation in its hand was made from that projection or
 * from another one; a field-by-field comparison of the parts the binding exposes
 * cannot answer it either, because the parts it exposes are precisely the ones
 * the two snapshots agree on. This is that answer: the canonical digest of the
 * WHOLE snapshot the installed verifier returned, computed here, over the exact
 * value every clause below then reads.
 *
 * WHAT IT IS NOT, and the distinction is the entire trust boundary. It says WHICH
 * projection was judged. It says NOTHING about whether that projection was
 * authentic, and it cannot: verifySnapshot is trusted server code, and this
 * kernel hashes whatever it returned. A consumer comparing this against its own
 * composed projection learns that the two are the same object; it does not learn
 * that either one is true. It is also NOT an anti-rollback fact and not an
 * identity for the clock — the clock's identity is still derived from its origin.
 *
 * IT IS NOT IN THE HASHED STATE, for the same reason nothing else here is: the
 * v2 state gained no field, no history_digest moved, and no stored clock is
 * rebased by this. The projection is an INPUT to one evaluation and the state is
 * the clock's whole history, so a per-evaluation input has no business in it.
 */
export const JOURNEY_ONE_CLOCK_VERIFIED_BINDING = "doctorcre-v5-journey-one-clock-verified-binding.v2";
/**
 * The closed field set of that projection, so a consumer can check it exactly.
 * The tag is versioned rather than silently widened, because a consumer that
 * closed-shape checks this set would refuse a v1 binding and be refused by a v2
 * one; adding a field to a published closed shape is a new shape.
 */
export const JOURNEY_ONE_CLOCK_VERIFIED_BINDING_FIELDS = Object.freeze([
  "authenticated_projection_digest", "candidate_digest", "clock_origin_gate_id",
  "clock_terminus_gate_id", "policy_digest", "schema_version", "subject_digest", "tenant",
]);
export const JOURNEY_ONE_CLOCK_RULE_REF =
  "native-task:01a0869f-fe0d-7493-bda3-ab8b3c0d6683:user-turn:01a086d1-2f70-7a73-b0ea-14e68da841ca";
/** The Chicago DST resolution convention below is decided, not inferred. */
export const JOURNEY_ONE_DST_RULE_REF =
  "native-task:01a0869f-fe0d-7493-bda3-ab8b3c0d6683:user-turn:01a08809-a9f2-73f3-beb6-4ee831e30ee5";
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
const CAP = JOURNEY_ONE_DEADLINE_CONTRACT.maximum_external_blocker_pause_hours * HOUR;
const IDENTITY = ["actor_id", "session_ref", "authority_class"];
// r7-EXACT, both of them. Neither schema declares schema_version and both are
// additional_properties:false, so the field is absent here and an added one is
// refused as an extra field. The producer step ref discriminates the two.
const COMMON_RECEIPT = ["subject_digest", "candidate_digest", "policy_digest",
  "subject_environment", "evidence_scope", "subject_maker_identity", "producer_identity",
  "evaluator_identity", "producer_role", "independent_oracle_ref", "oracle_version",
  "evidence_ref", "fixture_set_digest", "observed_at", "ttl_expires_at", "status",
  "comparator", "negative_admission_result"];
/** consumer-gate-receipt.v1: twenty-one required fields. */
const MINIMUM = [...COMMON_RECEIPT, "gate_id", "receipt_producer_step_ref", "environment_manifest_digest"];
/** rollout-component-receipt.v1: twenty-two required fields. */
const COMPLETION = [...COMMON_RECEIPT, "receipt_ref", "producer_step_ref", "rollout_environment_manifest_digest", "artifact_digest"];
const COMPLETION_EXPECTATION = ["artifact_digest", "fixture_set_digest"];
const STATE = ["schema_version", "origin_receipt_digest", "origin_at",
  "origin_benchmark_manifest_digest", "current_benchmark_manifest_digest",
  "origin_receipt_ttl_policy_ms", "base_deadline_at", "base_deadline_resolution",
  "due_at", "paused_ms", "status", "miss_at", "completion_receipt_digest",
  "completion_observed_at", "completion_receipt_ttl_policy_ms", "completion_artifact_digest",
  "completion_fixture_set_digest", "evaluated_at", "pause_intervals", "events", "history_digest"];
/**
 * The recorded completion and its seals are ONE fact. All four are null before a
 * completion is recorded and all four are present after it, alongside
 * completion_receipt_digest: the instant it was observed, the per-receipt TTL
 * policy that admitted the terminus, and the exact accepted kernel scope it was
 * judged against.
 */
const SEALED_ON_COMPLETION = ["completion_observed_at", "completion_receipt_ttl_policy_ms",
  "completion_artifact_digest", "completion_fixture_set_digest"];
const EVENT_KEYS = ["type", "at", "recorded_at", "evidence_digest", "previous_event_digest", "event_digest"];
const EVENT_TYPES = ["clock_started", "pause_approved", "amendment_recorded", "deadline_missed", "completion_observed"];
/**
 * Q008.D1 forbids CLAIMING DEADLINE SUCCESS once a miss is durably recorded, and
 * it never licenses rewriting when a completion was actually observed. After a
 * miss those two facts stop fitting in one word, so the completed cases are
 * three rather than two: `completed_after_recorded_miss` is a completion whose
 * own observation instant falls at or before the CURRENT deadline while a
 * recorded miss stands. It is not `completed_on_time`, which would claim the
 * success the decision forbids, and it is not `completed_late`, which would
 * misreport an observation that was not late. The observational fact travels
 * beside it as `completion_observed_within_deadline`.
 */
const STATUSES = ["running", "missed", "completed_on_time", "completed_late",
  "completed_after_recorded_miss", "unresolved_deadline", "completed_unresolved_deadline"];
/**
 * A durably recorded miss requires replan, and so does a deadline this kernel
 * cannot resolve. Every status here is reachable only with a recorded miss or an
 * unresolved deadline, and the returned verdict ORs this against miss_at itself
 * so the obligation follows the recorded fact rather than a status spelling.
 */
const REPLAN_STATUSES = ["missed", "completed_late", "completed_after_recorded_miss",
  "unresolved_deadline", "completed_unresolved_deadline"];
const PAUSE_KEYS = ["pause_id", "clock_origin_digest", "blocker_ref", "starts_at", "ends_at",
  "approved_at", "approved_by_identity", "approval_digest"];
const AMENDMENT_KEYS = ["amendment_ref", "clock_origin_digest", "benchmark_manifest_digest",
  "description_digest", "accepted_at", "accepted_by_identity", "amendment_digest"];
// A recorded attempt that simply did not pass, or was no longer current when it
// was admitted, is an ordinary fact of the ledger: it can never become the
// origin, and it must not make the clock permanently uncomputable. Every other
// refusal below stays fatal, because a misbound or malformed receipt in an
// authoritative inventory means the projection itself is wrong.
//
// `receipt_ttl_policy_exceeded` is deliberately NOT in this set. An overlong
// window is a misissued receipt or a misbound policy, not a non-pass: skipping
// it would silently pass over a genuine first passing minimum and hand the
// origin to a later one, and the same history re-read under a different policy
// would then select a different origin. It is fatal, by name.
const INADMISSIBLE = new Set(["nonpassing_receipt", "receipt_not_current"]);

export class JourneyOneClockError extends Error {
  constructor(code, detail) { super(code); this.name = "JourneyOneClockError"; this.code = code; this.detail = detail; }
}
function fail(code, detail) { throw new JourneyOneClockError(code, detail); }
function json(value, path = "input") {
  if (value === null || typeof value === "boolean") return;
  if (typeof value === "string") {
    if (/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value)) fail("invalid_unicode", path);
    return;
  }
  if (typeof value === "number" && Number.isFinite(value)) return;
  if (typeof value !== "object" || value === null) fail("not_json", path);
  // Arrays are checked against Array.prototype for the same reason objects are
  // checked against Object.prototype: copy() is JSON.stringify, which honours an
  // inherited toJSON, so an exotic prototype could hand back a value no clause
  // here ever read. Unreachable from wire JSON, like every other clause.
  const proto = Object.getPrototypeOf(value);
  if (Array.isArray(value) ? proto !== Array.prototype : ![Object.prototype, null].includes(proto)) fail("invalid_object", path);
  if (Object.getOwnPropertySymbols(value).length) fail("hidden_key", path);
  for (const key of Object.getOwnPropertyNames(value)) {
    if (Array.isArray(value) && key === "length") continue;
    const d = Object.getOwnPropertyDescriptor(value, key);
    if (d.get || d.set || !d.enumerable || key === "__proto__") fail("hidden_key", path);
    json(key, path); json(d.value, `${path}.${key}`);
  }
  if (Array.isArray(value) && Object.keys(value).length !== value.length) fail("sparse_array", path);
}
function closed(value, keys, path) {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail("invalid_object", path);
  if (Object.keys(value).length !== keys.length || keys.some(k => !Object.hasOwn(value, k))) fail("closed_shape", path);
  return value;
}
function ref(value, prefix = "safe:") {
  if (typeof value !== "string" || !value.startsWith(prefix) || !/^[a-zA-Z0-9:._/-]{3,300}$/.test(value)) fail("invalid_reference", value);
}
function hash(value) { if (typeof value !== "string" || !/^sha256:[a-f0-9]{64}$/.test(value)) fail("invalid_digest", value); }
function freeze(v) { if (v && typeof v === "object") { Object.values(v).forEach(freeze); Object.freeze(v); } return v; }
function copy(v) { return JSON.parse(JSON.stringify(v)); }
function same(a, b, code) { if (digest(a) !== digest(b)) fail(code); }
function order(a, b) { return a < b ? -1 : a > b ? 1 : 0; }
function stamp(value) {
  const m = typeof value === "string" && /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,3}))?(Z|[+-]\d{2}:\d{2})$/.exec(value);
  if (!m) fail("invalid_timestamp", value);
  const [y, mo, d, h, mi, s] = m.slice(1, 7).map(Number);
  const date = new Date(0); date.setUTCFullYear(y, mo - 1, d); date.setUTCHours(h, mi, s, 0);
  if (date.getUTCFullYear() !== y || date.getUTCMonth() !== mo - 1 || date.getUTCDate() !== d || h > 23 || mi > 59 || s > 59 ||
      (m[8] !== "Z" && (Number(m[8].slice(1, 3)) > 23 || Number(m[8].slice(4)) > 59))) fail("invalid_timestamp", value);
  const ms = Date.parse(value); if (!Number.isSafeInteger(ms)) fail("invalid_timestamp", value);
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
const DEADLINE_RESOLUTIONS = Object.freeze([
  DEADLINE_PLAIN, DEADLINE_GAP_SHIFTED, DEADLINE_OVERLAP_ORIGIN_OFFSET, DEADLINE_UNSUPPORTED]);

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
/**
 * `actor_id` is an identity.js actor SLUG — the exact strings isKnownPartner is
 * keyed on, e.g. "joe" — never an email, display name or record id.
 * `authority_class` is NOT a self-asserted string either: the trusted verifier
 * must set it from identity.js's authorizationClassForActor over the LIVE
 * actor, exactly as global-boundaries.v5.js derives it, and must never read it
 * back out of a stored record. A pure kernel cannot derive either field from
 * {actor_id, session_ref, authority_class}, so the partner test below is only
 * ever as strong as that seam: a projection that copies a stored class string
 * turns this check into the caller boolean the header forbids.
 */
function identity(value, partner = false) {
  closed(value, IDENTITY, "identity"); ref(value.session_ref, "session:");
  if (typeof value.actor_id !== "string" || !value.actor_id || typeof value.authority_class !== "string" || !value.authority_class) fail("invalid_identity");
  if (partner && (!isKnownPartner(value.actor_id) || value.authority_class !== "verified_partner")) fail("verified_partner_required");
}
/**
 * One receipt, read against the exact producer contract it claims, as of `at`.
 * `at` is the instant the receipt has to have been current for: the historical
 * admission instant for a minimum, the evaluation instant for the terminus.
 */
function receipt(r, completion, binding, at) {
  // The r7-exact field set IS the schema discriminator: the two shapes are
  // closed and their required fields are disjoint, so neither carries — nor
  // tolerates — a schema_version. The producer step ref then names the producer.
  closed(r, completion ? COMPLETION : MINIMUM, "receipt");
  const step = completion ? "step:j1-kernel-production-outcome" : "step:foundation-assurance-minimum-receipt";
  if ((completion ? r.producer_step_ref : r.receipt_producer_step_ref) !== step) fail("wrong_producer_or_schema");
  if (completion) {
    if (r.receipt_ref !== "safe:receipt:journey-one-kernel-production") fail("wrong_terminus_receipt");
    hash(r.artifact_digest);
  } else if (r.gate_id !== JOURNEY_ONE_DEADLINE_CONTRACT.clock_origin_gate_id) fail("wrong_origin_gate");
  for (const k of ["subject_digest", "candidate_digest", "policy_digest"]) {
    hash(r[k]); if (r[k] !== binding[k]) fail("receipt_binding_mismatch", k);
  }
  const environment = completion ? r.rollout_environment_manifest_digest : r.environment_manifest_digest;
  if (environment !== binding[completion ? "production_environment_manifest_digest" : "minimum_environment_manifest_digest"]) fail("receipt_environment_mismatch");
  hash(r.fixture_set_digest); ref(r.evidence_ref);
  if (r.evidence_scope !== (completion ? "production" : "candidate-and-test") ||
      r.subject_environment !== (completion ? "production" : "candidate")) fail("receipt_scope_mismatch");
  if (r.producer_role !== (completion ? "independent_journey_one_kernel_outcome_oracle" : "independent_foundation_assurance_minimum_oracle") ||
      r.independent_oracle_ref !== (completion ? "oracle:rollout-component:journey-one-kernel-production" : "oracle:gate-producer:foundation-assurance-minimum") ||
      r.oracle_version !== "1.0.0") fail("receipt_oracle_mismatch");
  for (const k of ["subject_maker_identity", "producer_identity", "evaluator_identity"]) identity(r[k]);
  // Independence is against the SUBJECT MAKER, and both of the other seats are
  // checked: a producer that is the maker attests to its own work exactly as
  // much as an evaluator that is.
  for (const k of ["producer_identity", "evaluator_identity"]) {
    if (r.subject_maker_identity.actor_id === r[k].actor_id ||
        r.subject_maker_identity.session_ref === r[k].session_ref) fail("self_attestation");
  }
  if (r.status !== "pass" || r.negative_admission_result !== "all_required_denials_observed") fail("nonpassing_receipt");
  if (typeof r.comparator !== "string" || r.comparator.length < 5 || r.comparator.length > 300) fail("invalid_comparator");
  const observed = stamp(r.observed_at), expires = stamp(r.ttl_expires_at);
  if (observed > at) fail("receipt_observed_after_reference");
  if (expires <= observed) fail("invalid_receipt_window");
  // TWO MAXIMA, ONE PER RECEIPT KIND. r7 says "within the accepted per-receipt
  // maximum" and the two kinds are issued by different producers under different
  // policies, so one number applied to both would refuse a legitimate terminus
  // for being longer-lived than a minimum. Neither is derived from the other.
  const maximum = completion ? binding.maximum_completion_receipt_ttl_ms : binding.maximum_minimum_receipt_ttl_ms;
  if (expires - observed > maximum) fail("receipt_ttl_policy_exceeded", completion ? "completion" : "minimum");
  if (expires <= at) fail("receipt_not_current");
  return observed;
}
/**
 * Append one event. `at` is when the fact happened; `recorded_at` is when this
 * kernel first saw it. The chain is ordered on recorded_at, never on `at`, so a
 * legitimately earlier approval reported late appends honestly instead of
 * deadlocking every later evaluation of the ledger.
 */
function event(state, type, at, recordedAt, evidence) {
  if (state.events.some(e => e.type === type && e.evidence_digest === evidence)) return;
  if (at > recordedAt) fail("event_recorded_before_it_happened", type);
  const previous = state.events.at(-1);
  if (previous && recordedAt < stamp(previous.recorded_at)) fail("history_time_reversed");
  const data = { type, at: iso(at), recorded_at: iso(recordedAt), evidence_digest: evidence,
    previous_event_digest: previous?.event_digest ?? null };
  state.events.push({ ...data, event_digest: digest(data) });
}
function readHistory(history, now) {
  if (history === null) return null;
  // A sealed history this kernel cannot recompute is refused BY NAME, before any
  // structural reading, so nothing here can be mistaken for a rebase or a reset.
  // Migrating a v1 record is the durable store's explicit act, not this file's.
  if (typeof history === "object" && history !== null && JOURNEY_ONE_CLOCK_LEGACY_SCHEMAS.includes(history.schema_version)) {
    fail("legacy_history_migration_required", { history_schema_version: history.schema_version,
      current_schema_version: JOURNEY_ONE_CLOCK_SCHEMA,
      reason: "origin_receipt_digest_basis_changed_to_r7_exact_minimum_and_state_gained_policy_and_resolution" });
  }
  closed(history, STATE, "history");
  const { history_digest, ...body } = history;
  if (history.schema_version !== JOURNEY_ONE_CLOCK_SCHEMA || digest(body) !== history_digest) fail("corrupt_history");
  hash(history.origin_receipt_digest);
  hash(history.origin_benchmark_manifest_digest); hash(history.current_benchmark_manifest_digest);
  if (!Number.isSafeInteger(history.origin_receipt_ttl_policy_ms) || history.origin_receipt_ttl_policy_ms <= 0) fail("corrupt_history");
  if (!DEADLINE_RESOLUTIONS.includes(history.base_deadline_resolution)) fail("corrupt_history");
  if ((history.base_deadline_at === null) !== (history.base_deadline_resolution === DEADLINE_UNSUPPORTED)) fail("corrupt_history");
  const origin = stamp(history.origin_at), evaluated = stamp(history.evaluated_at);
  if (origin > now || evaluated > now || evaluated < origin) fail("history_time_reversed");
  if ((history.base_deadline_at === null) !== (history.due_at === null)) fail("corrupt_history");
  if (history.base_deadline_at !== null && stamp(history.due_at) < stamp(history.base_deadline_at)) fail("corrupt_history");
  if (!Number.isSafeInteger(history.paused_ms) || history.paused_ms < 0 || history.paused_ms > CAP) fail("corrupt_history");
  if (!STATUSES.includes(history.status)) fail("corrupt_history");
  if (history.miss_at !== null) stamp(history.miss_at);
  // A recorded miss and a claim of deadline success cannot coexist in a record
  // this kernel will read. Q008.D1 forbids the claim outright, so a history
  // carrying both is refused BY NAME rather than quietly recomputed into
  // something truthful: the two contradict each other, and reading past that
  // would make this the place a forbidden claim went unnoticed.
  if (history.miss_at !== null && history.status === "completed_on_time") fail("deadline_success_claimed_after_recorded_miss");
  if (history.status === "completed_after_recorded_miss" &&
      (history.miss_at === null || history.completion_receipt_digest === null)) fail("corrupt_history");
  // A history can neither carry a completion seal it never earned nor drop one it
  // did: a completed clock always names the policy and the accepted scope that
  // judged its terminus, and a running one names neither.
  const completed = history.completion_receipt_digest !== null;
  for (const key of SEALED_ON_COMPLETION) if ((history[key] === null) === completed) fail("corrupt_history");
  if (completed) {
    hash(history.completion_receipt_digest);
    hash(history.completion_artifact_digest); hash(history.completion_fixture_set_digest);
    if (!Number.isSafeInteger(history.completion_receipt_ttl_policy_ms) ||
        history.completion_receipt_ttl_policy_ms <= 0) fail("corrupt_history");
    if (stamp(history.completion_observed_at) < origin) fail("corrupt_history");
  }
  if (!Array.isArray(history.pause_intervals)) fail("corrupt_history");
  const pauseIds = new Set();
  for (const interval of history.pause_intervals) {
    closed(interval, ["pause_id", "ends_at"], "history.pause_interval");
    ref(interval.pause_id);
    if (interval.ends_at !== null) stamp(interval.ends_at);
    if (pauseIds.has(interval.pause_id)) fail("corrupt_history"); pauseIds.add(interval.pause_id);
  }
  if (!Array.isArray(history.events) || !history.events.length) fail("corrupt_history");
  let previous = null, lastRecorded = -Infinity;
  for (const e of history.events) {
    closed(e, EVENT_KEYS, "history.event");
    const { event_digest, ...data } = e; hash(e.evidence_digest);
    const at = stamp(e.at), recorded = stamp(e.recorded_at);
    if (!EVENT_TYPES.includes(e.type) || data.previous_event_digest !== previous ||
        digest(data) !== event_digest || at > recorded || recorded < lastRecorded || recorded > now) fail("corrupt_history");
    previous = event_digest; lastRecorded = recorded;
  }
  if (history.events[0].type !== "clock_started" || history.events[0].evidence_digest !== history.origin_receipt_digest ||
      stamp(history.events[0].at) !== origin) fail("corrupt_history");
  // A miss instant is bound to its own event exactly, as a completion is: a
  // history may not echo a miss_at that its recorded event does not attest.
  // The binding is against miss_at and not due_at on purpose — due_at is
  // recomputed every evaluation, miss_at is the historical fact.
  const recordedMiss = history.events.filter(e => e.type === "deadline_missed");
  if ((history.miss_at !== null) !== (recordedMiss.length > 0)) fail("erased_miss_history");
  if (history.miss_at !== null && (recordedMiss.length !== 1 || recordedMiss[0].at !== history.miss_at ||
      recordedMiss[0].evidence_digest !== digest(["deadline_missed", history.miss_at, history.origin_receipt_digest]))) fail("erased_miss_history");
  const recordedCompletion = history.events.find(e => e.type === "completion_observed") ?? null;
  if ((history.completion_receipt_digest !== null) !== (recordedCompletion !== null)) fail("erased_completion_history");
  if (recordedCompletion && (recordedCompletion.evidence_digest !== history.completion_receipt_digest ||
      recordedCompletion.at !== history.completion_observed_at)) fail("erased_completion_history");
  return copy(history);
}
/**
 * READ ONE HISTORY ON ITS OWN, ON EXACTLY THE TERMS evaluate() READS IT.
 *
 * A durable store has to know whether the thing it is about to file — or the
 * thing it just read back off a disk — is a history this kernel can still read.
 * Before this export the only way to ask was to build a whole authenticated
 * projection and call evaluate(), so a store either owned a second, weaker
 * validator of its own or asked nothing at all. This is that question, asked
 * directly. It is a NARROW READ-ONLY SEAM and nothing else:
 *
 *   * It is the SAME FUNCTION. readHistory stays private and this wrapper adds
 *     no clause, drops none, and softens none. The v1 refusal by name, the
 *     recomputed history_digest, the closed shape, the instant grammar, the
 *     completion-seal pairing, the sticky miss and its event binding, the event
 *     chain and the one Q008.D1 contradiction are all exactly what evaluate()
 *     applies to the history it was handed.
 *   * IT DECIDES NOTHING. It computes no deadline, selects no origin, judges no
 *     receipt, admits nothing and returns no verdict — a history is not a clock
 *     evaluation. It reports readability and hands back a frozen copy.
 *   * IT PROVES NOTHING ABOUT ROLLBACK. An OLDER GENUINE history passes here
 *     perfectly, exactly as the header says: every digest in a history is
 *     computable by whoever supplies it. Anti-rollback remains the durable
 *     store's exact-prior compare-and-swap and is not weakened, replaced or
 *     implied by a pass here.
 *   * `now` IS EXPLICIT AND IS NEVER DEFAULTED. No live clock is read anywhere
 *     in this file, and a validator that quietly reached for the system clock
 *     would make one stored history readable or unreadable depending on when it
 *     was asked. It takes the kernel's own instant grammar — the same strings
 *     `as_of` is written in — and a caller that has only a stored history
 *     passes that history's own `evaluated_at`, which asks the self-consistent
 *     question "was this readable at the instant it was computed".
 *
 * The input is JSON-checked and SNAPSHOTTED before a clause reads it, on the
 * same terms a verified projection is, because the read walks the value more
 * than once and copy() honours an inherited toJSON: a getter or an exotic
 * prototype could otherwise hand two clauses two different answers. The result
 * is a deep-frozen copy, so a reader cannot mutate the thing it was just told
 * is well formed. evaluate() deliberately keeps calling the private readHistory
 * instead of this: the state it gets back is the object it then appends to, and
 * a frozen one could not be advanced.
 *
 * @param {object|null} history a doctorcre-v5-journey-one-clock.v2 state, or null
 * @param {string} now the instant to read it as of, in the kernel's grammar
 * @returns {object|null} a frozen copy of the history, or null when it was null
 */
export function readJourneyOneClockHistory(history, now) {
  const at = stamp(now);
  json(history, "history");
  return freeze(readHistory(history === null ? null : copy(history), at));
}
/** Dependency installation is trusted server code, never a caller tool argument. */
export function createJourneyOneClock({ verifySnapshot } = {}) {
  if (typeof verifySnapshot !== "function") fail("authenticated_verifier_required");
  return Object.freeze({ evaluate(envelope) {
    json(envelope);
    // A PLAIN JSON OBJECT, before anything is copied or hashed, exactly as the
    // A00 gate refuses one at the same point. artifact-trust.js hashes a
    // top-level string as its own raw bytes, so the string `{"a":1}` and the
    // object {a:1} produce ONE envelope_digest; json() cannot catch that,
    // because a top-level string is perfectly JSON-safe. The verification
    // binding below is only as strong as this digest's injectivity, so a string
    // envelope would otherwise borrow an authenticated object's digest.
    if (!envelope || typeof envelope !== "object" || Array.isArray(envelope)) fail("invalid_object", "envelope");
    const input = freeze(copy(envelope));
    const verified = verifySnapshot(input);
    json(verified);
    closed(verified, ["envelope_digest", "snapshot"], "verification");
    if (verified.envelope_digest !== digest(input)) fail("verification_binding_mismatch");
    const p = copy(verified.snapshot);
    // WHICH PROJECTION THIS EVALUATION IS ABOUT, hashed BEFORE a clause reads it
    // and over the SNAPSHOT COPY rather than the verifier's own object, so the
    // value hashed is exactly the value every clause below goes on to read. It
    // is reported beside the state (see JOURNEY_ONE_CLOCK_VERIFIED_BINDING) and
    // is not consulted by anything here: this kernel judges the projection, it
    // does not judge its digest.
    const authenticatedProjectionDigest = digest(p);
    closed(p, ["schema_version", "tenant", "as_of", "binding", "benchmark", "minimum_history", "completion", "completion_expectation", "pauses", "amendments", "history"], "snapshot");
    if (p.schema_version !== JOURNEY_ONE_CLOCK_PROJECTION || p.tenant !== ORGANIZATION_TENANT_ID) fail("wrong_projection_or_tenant");
    const now = stamp(p.as_of), b = p.binding;
    closed(b, ["subject_digest", "candidate_digest", "policy_digest", "minimum_environment_manifest_digest",
      "production_environment_manifest_digest", "maximum_minimum_receipt_ttl_ms", "maximum_completion_receipt_ttl_ms"], "binding");
    for (const [key, value] of Object.entries(b)) {
      if (key.endsWith("_ttl_ms")) { if (!Number.isSafeInteger(value) || value <= 0) fail("invalid_receipt_ttl_policy", key); }
      else hash(value);
    }
    // The expected kernel scope. Absent until the kernel exists, which is why it
    // is nullable here and required only where a completion is actually read.
    if (p.completion_expectation !== null) {
      closed(p.completion_expectation, COMPLETION_EXPECTATION, "completion_expectation");
      hash(p.completion_expectation.artifact_digest); hash(p.completion_expectation.fixture_set_digest);
    }
    const benchmark = p.benchmark;
    closed(benchmark, ["manifest_digest", "subject_digest", "candidate_digest", "policy_digest", "deadline_contract", "accepted_at", "accepted_by_identity"], "benchmark");
    hash(benchmark.manifest_digest); identity(benchmark.accepted_by_identity, true);
    same(benchmark.deadline_contract, JOURNEY_ONE_DEADLINE_CONTRACT, "wrong_deadline_contract");
    for (const k of ["subject_digest", "candidate_digest", "policy_digest"]) if (benchmark[k] !== b[k]) fail("benchmark_binding_mismatch");

    // ORIGIN. The FIRST current passing minimum that made Journey 1 admissible,
    // selected by the instant it was ADMITTED to the authoritative ledger and
    // then read for its own observed_at — not by inventory order, and not by
    // whether its TTL has since lapsed.
    // Admission order, not observation order, is what makes the origin
    // structurally immutable. An append-only inventory can only ever gain LATER
    // admissions, so a receipt observed earlier but admitted later — produced
    // yesterday afternoon, admitted this morning inside its TTL — is an ordinary
    // fact that appends honestly. Selecting on observed_at instead would let
    // that ordinary admission rebase a running clock, which is not a repairable
    // state: the inventory is authoritative and cannot shed it, and an amendment
    // preserves the origin by definition, so every later evaluation would refuse
    // and the deadline being tracked would become unreadable rather than wrong.
    // Ties break on the receipt digest so two receipts sharing an admitted_at
    // cannot make the answer depend on array order.
    // The history is read BEFORE the origin is selected, because the policy that
    // selected the recorded origin is part of that history. A projection that
    // arrives under a different minimum-receipt TTL maximum would admit or skip
    // a different set of attempts and could name a different first pass; that
    // refuses here, by its own name, instead of surfacing later as a rebase.
    const old = readHistory(p.history, now);
    if (old && old.origin_receipt_ttl_policy_ms !== b.maximum_minimum_receipt_ttl_ms) {
      fail("origin_ttl_policy_changed", { recorded: old.origin_receipt_ttl_policy_ms, supplied: b.maximum_minimum_receipt_ttl_ms });
    }
    // THE COMPLETION SEALS, read on the same terms and for the same reason, and
    // BEFORE any receipt is validated. A clock that already recorded a completion
    // also recorded which per-receipt TTL policy admitted that terminus and which
    // artifact and fixture set the trusted projection accepted it as. A later
    // projection that tightens the policy or names a different accepted scope
    // refuses HERE, by name. Without this the change surfaces as
    // `receipt_ttl_policy_exceeded` or `completion_artifact_mismatch` against the
    // exact receipt this clock already judged: a generic verdict that reports a
    // recorded fact as defective, makes a sealed history unreadable, and
    // re-interprets a completion under a policy that never judged it instead of
    // naming the thing that actually changed. Neither seal exists before the first
    // completion, so changing either one while the clock is still running is an
    // ordinary policy change and stays allowed.
    // A NULL expectation is not a change but the ordinary "not knowable yet" case
    // the header describes, and the seal never stands in for it: a completion
    // presented without an expectation still refuses below, so the trusted
    // projection remains the only thing that can supply that binding.
    if (old && old.completion_receipt_digest !== null) {
      if (old.completion_receipt_ttl_policy_ms !== b.maximum_completion_receipt_ttl_ms) {
        fail("completion_ttl_policy_changed", { recorded: old.completion_receipt_ttl_policy_ms,
          supplied: b.maximum_completion_receipt_ttl_ms });
      }
      if (p.completion_expectation !== null &&
          (p.completion_expectation.artifact_digest !== old.completion_artifact_digest ||
           p.completion_expectation.fixture_set_digest !== old.completion_fixture_set_digest)) {
        fail("completion_expectation_changed", {
          recorded: { artifact_digest: old.completion_artifact_digest,
            fixture_set_digest: old.completion_fixture_set_digest },
          supplied: copy(p.completion_expectation) });
      }
    }
    if (!Array.isArray(p.minimum_history) || !p.minimum_history.length) fail("origin_unavailable");
    let first = null, inadmissible = 0; const seen = new Set();
    for (const admission of p.minimum_history) {
      closed(admission, ["admitted_at", "receipt"], "minimum_admission");
      const admitted = stamp(admission.admitted_at);
      if (admitted > now) fail("future_admission");
      const receiptDigest = digest(admission.receipt);
      if (seen.has(receiptDigest)) fail("duplicate_minimum"); seen.add(receiptDigest);
      let observed;
      try { observed = receipt(admission.receipt, false, b, admitted); }
      catch (e) {
        if (e instanceof JourneyOneClockError && INADMISSIBLE.has(e.code)) { inadmissible += 1; continue; }
        throw e;
      }
      if (!first || admitted < first.admitted ||
          (admitted === first.admitted && receiptDigest < first.receiptDigest)) first = { observed, admitted, receiptDigest };
    }
    if (!first) fail("origin_unavailable", { inadmissible_admissions: inadmissible });

    const base = chicagoThirtyDayDeadline(iso(first.observed));
    if (old && (old.origin_receipt_digest !== first.receiptDigest || stamp(old.origin_at) !== first.observed ||
        old.base_deadline_at !== base.due_at || old.base_deadline_resolution !== base.reason_id)) fail("origin_reset_or_rebase");
    const state = old ?? { schema_version: JOURNEY_ONE_CLOCK_SCHEMA,
      origin_receipt_digest: first.receiptDigest, origin_at: iso(first.observed),
      origin_benchmark_manifest_digest: benchmark.manifest_digest,
      current_benchmark_manifest_digest: benchmark.manifest_digest,
      origin_receipt_ttl_policy_ms: b.maximum_minimum_receipt_ttl_ms,
      base_deadline_at: base.due_at, base_deadline_resolution: base.reason_id,
      due_at: base.due_at, paused_ms: 0, status: "running", miss_at: null,
      completion_receipt_digest: null, completion_observed_at: null,
      completion_receipt_ttl_policy_ms: null, completion_artifact_digest: null,
      completion_fixture_set_digest: null,
      evaluated_at: iso(now), pause_intervals: [], events: [] };
    if (!old) event(state, "clock_started", first.observed, now, first.receiptDigest);
    const pending = [];
    if (!Array.isArray(p.pauses) || !Array.isArray(p.amendments)) fail("invalid_inventory");

    // AMENDMENTS. An amendment records a partner-signed change and may authorize
    // one exact replacement benchmark manifest. It never moves the origin, the
    // base deadline or a recorded event; that is the whole of its power here.
    const amendedManifests = new Map(), amendmentRefs = new Set();
    for (const amendment of p.amendments) {
      closed(amendment, AMENDMENT_KEYS, "amendment");
      ref(amendment.amendment_ref); hash(amendment.description_digest); identity(amendment.accepted_by_identity, true);
      if (amendment.benchmark_manifest_digest !== null) hash(amendment.benchmark_manifest_digest);
      const { amendment_digest, ...payload } = amendment;
      if (amendment_digest !== digest(["doctorcre:j1-clock-amendment:v1", payload])) fail("amendment_digest_mismatch");
      if (amendmentRefs.has(amendment.amendment_ref)) fail("duplicate_amendment"); amendmentRefs.add(amendment.amendment_ref);
      const accepted = stamp(amendment.accepted_at);
      if (amendment.clock_origin_digest !== first.receiptDigest || accepted < first.observed || accepted > now) fail("amendment_reset_or_time");
      if (amendment.benchmark_manifest_digest !== null) amendedManifests.set(amendment.benchmark_manifest_digest, amendment_digest);
      pending.push(["amendment_recorded", accepted, amendment_digest]);
    }
    // BENCHMARK ACCEPTANCE. The originating benchmark must have been accepted by
    // an exact verified partner strictly BEFORE the origin pass. Any other
    // manifest is a replacement, and a replacement is legitimate only when an
    // exact-hash partner amendment names it.
    // Future-dating is refused FIRST and the order is load-bearing. The origin
    // never postdates `as_of`, so on the originating-manifest path every
    // future-dated acceptance is also at-or-after the origin; testing lateness
    // first would report the weaker refusal and leave the future-dated case
    // unreachable there. A replacement reaches the future check on its own.
    // Replacing the CURRENT manifest is what needs the amendment, and returning
    // to the originating manifest after a recorded amendment is a replacement
    // too. A partner-signed amendment is evidence for the change it names, never
    // evidence for un-making it, so a rollback needs its own exact amendment
    // naming the manifest it returns to. Without that, a caller could silently
    // revert a signed amendment with no counter-evidence at all, and the result
    // would carry an amendment_recorded event naming a manifest it simultaneously
    // reported as not current.
    const originManifest = old ? old.origin_benchmark_manifest_digest : benchmark.manifest_digest;
    const currentManifest = old ? old.current_benchmark_manifest_digest : benchmark.manifest_digest;
    const acceptedAt = stamp(benchmark.accepted_at);
    if (acceptedAt > now) fail("benchmark_accepted_in_the_future");
    if (benchmark.manifest_digest === originManifest && acceptedAt >= first.observed) fail("benchmark_not_accepted_before_origin");
    if (benchmark.manifest_digest !== currentManifest && !amendedManifests.has(benchmark.manifest_digest)) fail("unamended_benchmark_replacement");
    state.current_benchmark_manifest_digest = benchmark.manifest_digest;

    const intervals = [], pauseIds = new Set();
    for (const pause of p.pauses) {
      closed(pause, PAUSE_KEYS, "pause");
      ref(pause.pause_id); ref(pause.blocker_ref, "safe:external-blocker:"); identity(pause.approved_by_identity, true);
      const { approval_digest, ends_at, ...payload } = pause;
      if (approval_digest !== digest(["doctorcre:j1-clock-pause:v1", payload])) fail("pause_approval_digest_mismatch");
      if (pauseIds.has(pause.pause_id)) fail("duplicate_pause"); pauseIds.add(pause.pause_id);
      if (pause.clock_origin_digest !== state.origin_receipt_digest) fail("pause_origin_mismatch");
      const start = stamp(pause.starts_at), approved = stamp(pause.approved_at);
      const end = pause.ends_at === null ? now : stamp(pause.ends_at);
      // Approval is by Joe or Dell, strictly before the pause starts, never
      // backdated before the clock itself, and never dated ahead of the record.
      if (approved >= start || approved > now || approved < first.observed || start < first.observed ||
          (pause.ends_at !== null && end < start)) fail("pause_backdating_or_order");
      // An end is immutable ONCE KNOWN, and only then. A blocker that actually
      // ended twelve hours into a pause the ledger last read as ongoing is an
      // honest late report of a fact, not a rewrite: the settled rule counts
      // ACTUAL elapsed hours, and refusing an end earlier than the last
      // evaluation would leave the only admissible report — the full
      // twenty-four — the false one. Ends are compared as INSTANTS, so the same
      // instant spelled `Z` and `+00:00` is the same end rather than a rewrite.
      const priorPause = state.pause_intervals.find(x => x.pause_id === pause.pause_id);
      if (priorPause && priorPause.ends_at !== null &&
          (pause.ends_at === null || stamp(pause.ends_at) !== stamp(priorPause.ends_at))) fail("pause_history_rewritten");
      intervals.push([start, Math.min(end, now)]);
      pending.push(["pause_approved", approved, approval_digest]);
    }
    // Complete inventories are verifier-owned. Previously recorded approvals
    // cannot disappear or mutate when an untrusted caller supplies a new view.
    for (const e of state.events.filter(e => ["pause_approved", "amendment_recorded"].includes(e.type))) {
      if (!pending.some(([type, , d]) => type === e.type && d === e.evidence_digest)) fail("erased_approval_history");
    }
    let completion = null;
    if (p.completion !== null) {
      closed(p.completion, ["gate_id", "combiner", "obligation_decision_ids", "receipts"], "completion");
      if (p.completion.gate_id !== JOURNEY_ONE_DEADLINE_CONTRACT.clock_terminus_gate_id || p.completion.combiner !== "all_current_exact_distinct_pass") fail("wrong_terminus_gate");
      same(p.completion.obligation_decision_ids, JOURNEY_ONE_DEADLINE_CONTRACT.kernel_obligation_decision_ids, "wrong_kernel_obligations");
      if (!Array.isArray(p.completion.receipts) || p.completion.receipts.length !== 1) fail("missing_or_duplicate_kernel_receipt");
      const r = p.completion.receipts[0], receiptDigest = digest(r);
      // A completion this clock already recorded is a historical fact, and the
      // truthful inventory keeps carrying its terminus receipt after the TTL
      // lapses. Reading that honest inventory must not make the recorded
      // completion unreadable — the alternative is a verifier forced to withhold
      // evidence it holds, which is the opposite of the completeness this file
      // demands of pauses and amendments. The fact survives; only its current
      // usability lapses, exactly as it does for a lapsed minimum. A receipt this
      // clock never recorded still refuses when it is not current, and every
      // other refusal stays fatal.
      // CURRENTNESS IS THE ONLY REFUSAL SOFTENED, and `receipt_ttl_policy_exceeded`
      // deliberately is not one of them. The policy that admitted a recorded
      // completion is sealed above, so the exact recorded receipt can never be
      // overlong under the policy this evaluation is reading — a tightened policy
      // already refused by name, and a widened one cannot make a short window
      // long. An overlong receipt this clock never recorded stays fatal, and no
      // tolerance here can admit a new one under a policy that is not the
      // supplied one.
      let observed = null;
      try { observed = receipt(r, true, b, now); }
      catch (e) {
        if (!(state.completion_receipt_digest === receiptDigest &&
            e instanceof JourneyOneClockError && e.code === "receipt_not_current")) throw e;
      }
      // THE EXACT KERNEL SCOPE. r7's rollout pass rule makes the artifact and
      // fixture digests exact, and format-checking them proves only that two
      // strings are sha256-shaped: two completions differing only in
      // artifact_digest would both pass. The expectation is a trusted projection
      // fact (see the header) — never the receipt's own value read back, and
      // never a decision-id label on the wrapper, which the A00 catalog
      // explicitly excludes as evidence. It is required here and not at the
      // origin, because the artifact does not exist when the clock starts.
      // These two are ordinary receipt refusals about a receipt being judged for
      // the FIRST time. Once a completion has been recorded, the expectation
      // reaching this point has already been checked against the sealed scope
      // above, so a changed one was named there and can never arrive here to
      // report a recorded terminus as the wrong artifact.
      if (p.completion_expectation === null) fail("completion_expectation_unavailable");
      if (r.artifact_digest !== p.completion_expectation.artifact_digest) fail("completion_artifact_mismatch");
      if (r.fixture_set_digest !== p.completion_expectation.fixture_set_digest) fail("completion_fixture_mismatch");
      if (observed !== null) {
        if (observed < first.observed) fail("completion_before_origin");
        completion = { observed, receiptDigest };
      }
      if (state.completion_receipt_digest !== null && state.completion_receipt_digest !== receiptDigest) fail("completion_history_replacement");
    }
    // Completion is a FACT and is recorded whether or not the deadline resolved.
    // Only the on-time judgement depends on a resolvable deadline.
    if (completion && state.completion_receipt_digest === null) {
      state.completion_receipt_digest = completion.receiptDigest;
      state.completion_observed_at = iso(completion.observed);
      // Sealed from the TRUSTED PROJECTION's own facts and never read back off
      // the receipt they judged: pinning `r.artifact_digest` here would record
      // whatever the receipt claimed and make every later comparison the
      // tautology the expectation exists to replace. The two are equal at this
      // point only because the expectation just accepted the receipt.
      state.completion_receipt_ttl_policy_ms = b.maximum_completion_receipt_ttl_ms;
      state.completion_artifact_digest = p.completion_expectation.artifact_digest;
      state.completion_fixture_set_digest = p.completion_expectation.fixture_set_digest;
      pending.push(["completion_observed", completion.observed, completion.receiptDigest]);
    }
    const stop = state.completion_observed_at !== null ? stamp(state.completion_observed_at) : now;
    const union = [];
    for (const [start, rawEnd] of intervals.sort((a, b) => a[0] - b[0])) {
      const end = Math.min(rawEnd, stop);
      if (end <= start) continue;
      if (union.length && start <= union.at(-1)[1]) union.at(-1)[1] = Math.max(union.at(-1)[1], end);
      else union.push([start, end]);
    }
    let pauseMs = 0;
    // Observational timeliness: whether the completion's OWN observation instant
    // fell at or before the current deadline. Null while there is no completion
    // or no resolvable deadline. It is reported beside the verdict and never
    // folded into it — after a recorded miss the two deliberately disagree.
    let observedWithinDeadline = null;
    if (base.due_at !== null) {
      const baseMs = stamp(base.due_at);
      for (const [start, end] of union) {
        // A pause beginning after an already missed deadline cannot revive it.
        if (start <= baseMs + pauseMs) pauseMs = Math.min(CAP, pauseMs + end - start);
      }
      const dueMs = baseMs + pauseMs;
      state.paused_ms = pauseMs; state.due_at = iso(dueMs);
      // miss_at and due_at answer two different questions and are allowed to
      // disagree. due_at is the CURRENT deadline, recomputed from the whole
      // inventory every evaluation; miss_at is the HISTORICAL fact that a
      // deadline once passed with NO COMPLETION EVIDENCE IN HAND, and it is
      // written once. A pause legitimately approved before the deadline but
      // reported after the miss credits its actual elapsed hours, so due_at can
      // move PAST an existing miss_at. That does not erase or reinterpret the
      // miss: it stays in the record with its own event, and this kernel adds no
      // new refusal for the ordinary late report that produced it. It does not
      // restore deadline success either — changing a deadline through an
      // admissible pause never clears a miss the record already carries.
      if (state.miss_at === null && stop > dueMs) {
        state.miss_at = state.due_at;
        pending.push(["deadline_missed", stamp(state.miss_at), digest(["deadline_missed", state.due_at, state.origin_receipt_digest])]);
      }
      // A DURABLY RECORDED MISS ENDS DEADLINE SUCCESS. Q008.D1, as accepted:
      // a miss "is durably recorded, requires replan, preserves origin and
      // elapsed history, forbids claiming deadline success". It grants no
      // exception for a proof that arrives after the miss carrying an earlier
      // observation instant. Whether such a proof ought to earn one is an open
      // question with no answer on the record, so this kernel implements the
      // accepted rule and nothing beyond it.
      //
      // TWO FACTS, TWO FIELDS, NEITHER OVERWRITING THE OTHER.
      //   * The OBSERVATION is never rewritten. A completion observed at or
      //     before the current deadline — inclusive at the boundary — reports
      //     `completion_observed_within_deadline: true` and is never called
      //     late, whatever an evaluation happened to record before it was
      //     admitted.
      //   * The VERDICT is separate. With a miss standing, that same completion
      //     is `completed_after_recorded_miss`: no success claimed, replan still
      //     required, and the miss instant, its event and the elapsed history
      //     all preserved exactly as they were written.
      // The completion stays usable and safe construction continues, which is
      // precisely what r7's terminus gate says a late passing kernel receipt
      // does — without retroactively certifying the deadline.
      // Absent a completion the recorded miss is still the status, and it never
      // un-sticks.
      const completedAt = state.completion_observed_at === null ? null : stamp(state.completion_observed_at);
      observedWithinDeadline = completedAt === null ? null : completedAt <= dueMs;
      state.status = completedAt === null
        ? (state.miss_at === null ? "running" : "missed")
        : state.miss_at !== null
          ? (observedWithinDeadline ? "completed_after_recorded_miss" : "completed_late")
          : (observedWithinDeadline ? "completed_on_time" : "completed_late");
    } else {
      for (const [start, end] of union) pauseMs = Math.min(CAP, pauseMs + end - start);
      state.paused_ms = pauseMs; state.due_at = null;
      state.status = state.completion_receipt_digest !== null ? "completed_unresolved_deadline" : "unresolved_deadline";
    }
    for (const [type, at, evidence] of pending.sort((x, y) => x[1] - y[1] || order(x[0], y[0]) || order(x[2], y[2]))) {
      event(state, type, at, now, evidence);
    }
    state.evaluated_at = iso(now);
    state.pause_intervals = p.pauses.map(x => ({ pause_id: x.pause_id, ends_at: x.ends_at }));
    delete state.history_digest; state.history_digest = digest(state);
    return freeze({ schema_version: JOURNEY_ONE_CLOCK_SCHEMA, state,
      // The verified binding, BESIDE the state. Every value here was enforced
      // above — `b`'s three digests against the accepted benchmark and against
      // every receipt read, the tenant against this kernel's own, and the two
      // gate ids by `same(benchmark.deadline_contract, ...)`, which is why they
      // are taken from the contract constant rather than from the projection.
      // Nothing is added to `state`: doing so would rebase every stored history.
      verified_binding: { schema_version: JOURNEY_ONE_CLOCK_VERIFIED_BINDING, tenant: p.tenant,
        subject_digest: b.subject_digest, candidate_digest: b.candidate_digest,
        policy_digest: b.policy_digest,
        clock_origin_gate_id: JOURNEY_ONE_DEADLINE_CONTRACT.clock_origin_gate_id,
        clock_terminus_gate_id: JOURNEY_ONE_DEADLINE_CONTRACT.clock_terminus_gate_id,
        // WHICH projection, not WHETHER it was authentic. The five values above
        // are the accepted scope and are shared by every snapshot built for this
        // program; this one is exact, and it is the only thing here that
        // distinguishes two valid snapshots of the same scope from each other.
        authenticated_projection_digest: authenticatedProjectionDigest },
      // Success is claimable only by a clock that carries NO recorded miss. The
      // status can never spell `completed_on_time` after one, and the miss test
      // is stated here as well so the two can never drift into disagreement.
      deadline_success: state.miss_at === null && state.status === "completed_on_time",
      // REPLAN FOLLOWS THE RECORDED MISS, not the later verdict: Q008.D1 makes
      // the durable miss itself the trigger, so a completion admitted afterwards
      // — however early its own observation — never retires the obligation. A
      // deadline this kernel cannot resolve fails closed into a replan too,
      // rather than into a deadline that can never be missed.
      replan_required: state.miss_at !== null || REPLAN_STATUSES.includes(state.status),
      safe_construction_may_continue: true,
      completion_currently_usable: completion !== null,
      // OBSERVATIONAL TIMELINESS, reported apart from the verdict: true when the
      // completion's own observation instant fell at or before the current
      // deadline, false when it fell after, null with no completion or no
      // resolvable deadline. A true here after a recorded miss is an honest
      // report about the observation and never a claim of deadline success.
      completion_observed_within_deadline: observedWithinDeadline,
      // The recording of the miss itself, reported apart from the verdict so
      // neither fact hides the other. It is written when a deadline passed with
      // no completion evidence in hand, and it never un-sticks.
      missing_evidence_miss_recorded: state.miss_at !== null,
      benchmark_amended: state.current_benchmark_manifest_digest !== state.origin_benchmark_manifest_digest,
      deadline_resolution: base.reason_id,
      unresolved_reason: base.status === "unresolved_deadline" ? base.reason_id : null,
      durable_history_write_required: true, authority_granted: false, effects: V5_NO_EFFECTS });
  } });
}
