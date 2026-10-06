// DoctorCRE v5 slice V5-A05 — delivery cadence, escalation and the
// decision-ready quiet-hours queue.
//
// Catalog entry (doctrine document `doctorcre-v5-astra-integration-review`,
// section "DoctorCRE v5: reviewed coding slices, scope contracts and parallel
// groups", `proposed_id: "V5-A05"`), goal verbatim: "Keep assurance one risk
// ahead while surfacing only decisions and urgent harms at the right time."
//
// WHAT THIS FILE IS, unlike its two dependencies. V5-A01 and V5-A03 both
// discovered that the thing their checkable_done asked them to prove required
// an authoritative store this repository does not have, and each therefore
// answers "unavailable" on every input, forever, naming the seam it is owed.
// V5-A05's three checkable_done items are different in kind: whether a 14-day
// interval has been kept, whether a reason names an urgent harm, and whether an
// item needs Joe's authority are each decidable from the request alone, with no
// registry, ledger or receipt store standing behind the answer. So this module
// answers for real, on every well-formed request — it is not a stub waiting on
// a later seam, and it does not report `unavailable`.
//
// The module is pure, exactly as global-boundaries.v5.js is pure: it reads no
// filesystem, no network, no database, no scheduler and no clock. Every
// evaluation that depends on time takes `now` from its caller, so two callers
// holding the same request always reach the same answer, and nothing here can
// reset a clock to make a miss disappear (excluded_scope: "clock reset").
//
// WHAT IS DELIBERATELY OUT OF SCOPE, matching the catalog's excluded_scope:
//   * "assurance-complete-before-product"  — this module states cadence and
//     escalation facts; it does not gate product delivery on them.
//   * "engagement notifications"           — an item that is neither urgent
//     nor authority-requiring gets `no_queue_entry`. It is never surfaced to
//     Joe as an FYI; that is a different, unbuilt surface.
//   * "automatic authority widening"       — the ONLY things that can make an
//     item wake Joe are the closed urgent-reason vocabulary and the two
//     booleans `evaluateEscalationRouting`'s request schema names outright
//     (`requires_joe_authority`, `unresolved_intent`). Those booleans are NOT
//     caller assertions in production: `deriveEscalationFacts` below computes
//     them (and whether an urgent reason may be honoured at all) from the
//     reason id plus facts the SERVER read — the raising seat's server-derived
//     class, an open incident record, the server-clock cadence status — and
//     raise-delivery-cadence-alert feeds only that derivation to the router.
//     No other field, however labelled, can raise an item's routing — the
//     request schemas are closed and an unknown field is a contract violation,
//     not a policy question.
//   * "clock reset"                        — see above.
//
// TWO KINDS OF NO, inherited from global-boundaries.v5.js:
//   * A POLICY ANSWER is returned — a frozen result whose `decision` or
//     `routing` is one of a closed set, with a stable `reason_id`.
//   * A CONTRACT VIOLATION throws V5BoundaryError — unknown fields, unknown
//     reason ids, non-monotonic history and unreadable timestamps are not
//     policy questions; the module fails closed rather than guessing.
//
// WHAT THIS MODULE DOES NOT CLAIM. It was built from the catalog entry's own
// fields (goal, concrete_output, included/excluded scope, checkable_done,
// decision_ids), read verbatim from the doctrine store. It was NOT built from
// the verbatim text of decisions Q008.D2, Q013.D1, Q027.D1, Q045.D1 or Q131.D1
// — those sentences were not independently retrieved for this build, unlike
// V5-A03's five settled decisions, which were. `assertA05CatalogBinding` below
// therefore binds a caller to the CATALOG ENTRY this module was built from, not
// to the underlying decision text, and says so in its own field names rather
// than implying a binding this module cannot prove.

import { canonicalJson, digest } from "./artifact-trust.js";
import { V5BoundaryError, V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import { ORGANIZATION_TENANT_ID } from "./identity.js";

export const V5_A05_SCHEMA_VERSION = "doctorcre-v5-delivery-cadence.v1";
export const V5_A05_POLICY_VERSION = 1;

function fail(code, message, detail) {
  throw new V5BoundaryError(code, message, detail);
}

function isPlainObject(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function deepFreeze(value) {
  if (Array.isArray(value)) { value.forEach(deepFreeze); return Object.freeze(value); }
  if (isPlainObject(value)) { Object.values(value).forEach(deepFreeze); return Object.freeze(value); }
  return value;
}

function assertObject(value, path) {
  if (!isPlainObject(value)) fail("invalid_shape", `${path} must be a plain object`, { path });
  return value;
}

function assertClosedKeys(object, allowed, path) {
  for (const key of Object.keys(object)) {
    if (!allowed.includes(key)) fail("unknown_field", `unknown field "${key}" at ${path}`, { path: `${path}.${key}`, key });
  }
}

function assertRequiredKeys(object, required, path) {
  for (const key of required) {
    if (!(key in object)) fail("missing_field", `${path}.${key} is required`, { path: `${path}.${key}` });
  }
}

function assertBoolean(value, path) {
  if (typeof value !== "boolean") fail("invalid_shape", `${path} must be a boolean`, { path });
  return value;
}

const ISO_INSTANT =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(?:Z|([+-])(\d{2}):(\d{2}))$/;

function daysInMonth(year, month) {
  if (month === 2) return (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0 ? 29 : 28;
  return [4, 6, 9, 11].includes(month) ? 30 : 31;
}

/**
 * Timestamps are parsed, never inferred, and the calendar is checked against
 * the literal fields before parsing — copied from global-boundaries.v5.js's
 * assertInstant so a bare date, a locale string, an offsetless stamp or an
 * impossible calendar date (`Date.parse` silently normalizes "2026-02-31" into
 * 3 March rather than refusing it) cannot smuggle a different instant than the
 * one written into a 14-day cadence window.
 */
function assertInstant(value, path) {
  const match = typeof value === "string" ? ISO_INSTANT.exec(value) : null;
  if (!match) fail("invalid_timestamp", `${path} must be an ISO-8601 instant with an explicit offset`, { path, value });
  const [, year, month, day, hour, minute, second, , offsetHour, offsetMinute] = match;
  const y = Number(year), mo = Number(month), d = Number(day);
  const h = Number(hour), mi = Number(minute), s = Number(second);
  if (mo < 1 || mo > 12 || d < 1 || d > daysInMonth(y, mo) || h > 23 || mi > 59 || s > 59 ||
      (offsetHour !== undefined && (Number(offsetHour) > 23 || Number(offsetMinute) > 59))) {
    fail("invalid_timestamp",
      `${path} names an instant that does not exist on the calendar; it is not normalized into a different one`,
      { path, value });
  }
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) fail("invalid_timestamp", `${path} is not a readable instant`, { path, value });
  return parsed;
}

// ---------------------------------------------------------------------------
// The catalog entry this module was built from, carried verbatim so a drifted
// build binds against it rather than against a paraphrase. Field order matches
// the slice catalog's own JSON.
// ---------------------------------------------------------------------------

const V5_A05_CATALOG_ENTRY = deepFreeze({
  proposed_id: "V5-A05",
  title: "Delivery cadence, escalation and quiet-hours queue",
  item_kind: "coding_slice",
  child_program: "assurance-fabric",
  goal: "Keep assurance one risk ahead while surfacing only decisions and urgent harms at the right time.",
  concrete_output: "Rolling 14-day slice-outcome evidence, documented alternative/retry escalation rules and decision-ready quiet-hours queue.",
  included_scope: [
    "expiring cadence receipt", "replan on miss",
    "urgent security/data-loss/outward-harm alerts", "morning approval batches",
  ],
  excluded_scope: [
    "assurance-complete-before-product", "engagement notifications",
    "automatic authority widening", "clock reset",
  ],
  interfaces: ["Completion Register", "notification queue", "morning brief", "incident/replan"],
  decision_ids: ["Q008.D2", "Q013.D1", "Q027.D1", "Q045.D1", "Q131.D1"],
  requirement_ids: ["Q008", "Q013", "Q027", "Q045", "Q131"],
  source_build_dependencies: ["V5-A01", "V5-A03"],
  checkable_done: [
    "14-day interval/miss/history fixtures pass",
    "ordinary blockers batch; urgent harm alerts immediately",
    "only required human authority or unresolved intent wakes Joe",
  ],
});

export const V5_A05_DECISION_IDS = deepFreeze([...V5_A05_CATALOG_ENTRY.decision_ids].sort());

/** The catalog entry's own digest. A consumer pins this, not a paraphrase of it. */
export function v5A05CatalogEntryDigest() {
  return digest(canonicalJson(V5_A05_CATALOG_ENTRY));
}

/**
 * Refuse a caller whose copy of the V5-A05 catalog entry has drifted from the
 * one this module was built against.
 *
 * IT RETURNS NOTHING. Drift throws; agreement is silence — an affirmative
 * return value here would be one more thing a consumer could mistake for a
 * broader clearance than "these bytes match".
 */
export function assertA05CatalogBinding(binding) {
  assertObject(binding, "binding");
  assertClosedKeys(binding, ["catalog_entry_digest"], "binding");
  assertRequiredKeys(binding, ["catalog_entry_digest"], "binding");
  if (binding.catalog_entry_digest !== v5A05CatalogEntryDigest()) {
    fail("catalog_binding_drift", "the supplied catalog entry digest does not match the V5-A05 entry this module was built from", {
      expected: v5A05CatalogEntryDigest(), actual: binding.catalog_entry_digest,
    });
  }
}

// ---------------------------------------------------------------------------
// checkable_done 1 — "14-day interval/miss/history fixtures pass".
// ---------------------------------------------------------------------------

/** Q008/Q013's cadence window, verbatim from the catalog's concrete_output: "Rolling 14-day". */
export const V5_A05_CADENCE_INTERVAL_DAYS = 14;
const CADENCE_INTERVAL_MS = V5_A05_CADENCE_INTERVAL_DAYS * 24 * 60 * 60 * 1000;

export const V5_A05_CADENCE_STATUSES = deepFreeze(["current", "missed", "no_receipt_on_record"]);

const CADENCE_REQUEST_KEYS = Object.freeze(["now", "subject", "history"]);
const SUBJECT_KEYS = Object.freeze(["type", "ref"]);

function cadenceResult(fields) {
  return deepFreeze({
    schema_version: V5_A05_SCHEMA_VERSION,
    policy_version: V5_A05_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    interval_days: V5_A05_CADENCE_INTERVAL_DAYS,
    ...fields,
    effects: V5_NO_EFFECTS,
  });
}

/**
 * Evaluate a subject's cadence receipt history against the rolling 14-day
 * window.
 *
 * `history` is the subject's own receipt-issuance instants, STRICTLY ASCENDING
 * and each no later than `now` — a caller reordering or back-dating its own
 * history to make a miss disappear is a contract violation here, not a policy
 * question the module could be talked out of. Nothing internal reads a clock:
 * every fact below is a function of `now` and `history` alone, so replaying the
 * same request always reaches the same answer (excluded_scope: "clock reset").
 *
 * `requires_replan` is true exactly when the most recent receipt has expired —
 * this is the fact "replan on miss" (included_scope) is triggered from; this
 * module does not perform the replan, it states the fact a replan is owed.
 */
export function evaluateCadenceReceipt(request) {
  assertObject(request, "request");
  assertClosedKeys(request, CADENCE_REQUEST_KEYS, "request");
  assertRequiredKeys(request, ["now", "history"], "request");
  const now = assertInstant(request.now, "request.now");

  let subject = null;
  if (request.subject !== undefined && request.subject !== null) {
    subject = assertObject(request.subject, "request.subject");
    assertClosedKeys(subject, SUBJECT_KEYS, "request.subject");
    assertRequiredKeys(subject, ["type", "ref"], "request.subject");
    if (typeof subject.type !== "string" || subject.type.length === 0) {
      fail("invalid_shape", "request.subject.type must be a non-empty string", { path: "request.subject.type" });
    }
    if (typeof subject.ref !== "string" || subject.ref.length === 0) {
      fail("invalid_shape", "request.subject.ref must be a non-empty string", { path: "request.subject.ref" });
    }
  }

  if (!Array.isArray(request.history)) fail("invalid_shape", "request.history must be an array", { path: "request.history" });
  const history = request.history.map((value, index) => assertInstant(value, `request.history[${index}]`));
  for (let i = 0; i < history.length; i++) {
    if (history[i] > now) {
      fail("receipt_issued_in_the_future", `request.history[${i}] is later than request.now`,
        { path: `request.history[${i}]`, now: request.now });
    }
    if (i > 0 && history[i] <= history[i - 1]) {
      fail("history_not_strictly_ascending",
        "request.history must be strictly ascending; a reordered or duplicated history cannot mask a miss",
        { path: `request.history[${i}]`, prior: request.history[i - 1], value: request.history[i] });
    }
  }

  const windowStart = now - CADENCE_INTERVAL_MS;
  const receiptsInWindow = history.filter(t => t >= windowStart && t <= now).length;

  // Miss count across the WHOLE supplied history, not only the current window:
  // this is the "history fixtures" half of checkable_done 1, and it is what
  // the rolling 14-day EVIDENCE (concrete_output) is built from over time.
  let missCountInHistory = 0;
  for (let i = 1; i < history.length; i++) {
    if (history[i] - history[i - 1] > CADENCE_INTERVAL_MS) missCountInHistory++;
  }

  const base = { subject: subject ? { type: subject.type, ref: subject.ref } : null, receipts_in_window: receiptsInWindow, miss_count_in_history: missCountInHistory };

  if (history.length === 0) {
    return cadenceResult({
      ...base, status: "no_receipt_on_record", reason_id: "no_cadence_receipt_on_record",
      requires_replan: false, last_receipt_issued_at: null, expires_at: null, days_since_last_receipt: null,
    });
  }

  const last = history[history.length - 1];
  const expiresAt = last + CADENCE_INTERVAL_MS;
  const daysSinceLast = (now - last) / (24 * 60 * 60 * 1000);

  if (now <= expiresAt) {
    return cadenceResult({
      ...base, status: "current", reason_id: "within_cadence_interval", requires_replan: false,
      last_receipt_issued_at: request.history[request.history.length - 1],
      expires_at: new Date(expiresAt).toISOString(), days_since_last_receipt: daysSinceLast,
    });
  }
  return cadenceResult({
    ...base, status: "missed", reason_id: "cadence_interval_exceeded", requires_replan: true,
    last_receipt_issued_at: request.history[request.history.length - 1],
    expires_at: new Date(expiresAt).toISOString(), days_since_last_receipt: daysSinceLast,
  });
}

// ---------------------------------------------------------------------------
// checkable_done 2 — "ordinary blockers batch; urgent harm alerts immediately".
// ---------------------------------------------------------------------------

/**
 * The CLOSED set of reasons that are urgent harms, verbatim from the catalog's
 * included_scope: "urgent security/data-loss/outward-harm alerts". Nothing
 * outside this set is ever urgent, and no caller-asserted field can widen it
 * (excluded_scope: "automatic authority widening") — an unrecognized reason id
 * is a contract violation, not a request this module guesses about.
 */
export const V5_A05_URGENT_REASON_IDS = deepFreeze(["security_incident", "data_loss", "outward_harm"]);

/**
 * The CLOSED set of ordinary blocker reasons this slice knows how to route.
 * `cadence_miss_replan_required` is the reason `evaluateCadenceReceipt` names
 * when `requires_replan` is true — the "replan on miss" wiring (included_scope)
 * between the two checkable_done clauses.
 */
export const V5_A05_ORDINARY_REASON_IDS = deepFreeze([
  "cadence_miss_replan_required", "decision_required", "delivery_blocker", "review_blocker",
]);

export const V5_A05_REASON_IDS = deepFreeze(
  [...V5_A05_URGENT_REASON_IDS, ...V5_A05_ORDINARY_REASON_IDS].sort());

/** The reason's severity, from the closed vocabularies above. Throws on drift. */
export function classifyEscalationReason(reasonId) {
  if (V5_A05_URGENT_REASON_IDS.includes(reasonId)) return "urgent";
  if (V5_A05_ORDINARY_REASON_IDS.includes(reasonId)) return "ordinary";
  fail("unknown_reason_id", `"${reasonId}" is not a registered V5-A05 escalation reason`,
    { reason_id: reasonId, registered: [...V5_A05_REASON_IDS] });
}

// ---------------------------------------------------------------------------
// checkable_done 3 — "only required human authority or unresolved intent
// wakes Joe".
// ---------------------------------------------------------------------------

export const V5_A05_ROUTINGS = deepFreeze(["deliver_immediately", "batch_for_morning", "no_queue_entry"]);

const ROUTING_REQUEST_KEYS = Object.freeze(["reason_id", "requires_joe_authority", "unresolved_intent", "quiet_now"]);

/**
 * Route one escalation candidate to the decision-ready quiet-hours queue.
 *
 * `wakes_joe` is true for exactly three reasons, and no others exist on this
 * request's closed schema:
 *   (a) the reason id is in the urgent vocabulary,
 *   (b) `requires_joe_authority` is true,
 *   (c) `unresolved_intent` is true.
 * That is checkable_done 3, read literally: "only required human authority or
 * unresolved intent wakes Joe" — (a) is the urgent-harm carve-out checkable_done
 * 2 names separately, and (b)/(c) are exactly "required human authority" and
 * "unresolved intent".
 *
 * Urgent reasons bypass quiet hours and any batching entirely
 * (`deliver_immediately`, `bypasses_quiet_hours: true`) — checkable_done 2's
 * "urgent harm alerts immediately". A non-urgent item that still needs Joe's
 * authority or names unresolved intent is held for the next morning brief
 * (`batch_for_morning`) rather than interrupting during quiet hours or the
 * working day — checkable_done 2's "ordinary blockers batch" and the catalog's
 * included_scope "morning approval batches". Everything else gets
 * `no_queue_entry`: it is rolling cadence evidence, never an "engagement
 * notification" surfaced to Joe (excluded_scope).
 *
 * `quiet_now` is accepted and echoed for the caller's own record-keeping, but
 * it never changes the routing decision by itself — quiet hours cannot turn an
 * urgent alert into a batched one, and being outside quiet hours cannot turn an
 * ordinary blocker into an immediate interruption. The one axis that moves
 * routing is urgency/authority/intent, not the clock.
 */
export function evaluateEscalationRouting(request) {
  assertObject(request, "request");
  assertClosedKeys(request, ROUTING_REQUEST_KEYS, "request");
  assertRequiredKeys(request, ["reason_id", "requires_joe_authority", "unresolved_intent", "quiet_now"], "request");
  if (typeof request.reason_id !== "string" || request.reason_id.length === 0) {
    fail("invalid_shape", "request.reason_id must be a non-empty string", { path: "request.reason_id" });
  }
  const requiresJoeAuthority = assertBoolean(request.requires_joe_authority, "request.requires_joe_authority");
  const unresolvedIntent = assertBoolean(request.unresolved_intent, "request.unresolved_intent");
  const quietNow = assertBoolean(request.quiet_now, "request.quiet_now");

  const severity = classifyEscalationReason(request.reason_id);
  const wakesJoe = severity === "urgent" || requiresJoeAuthority || unresolvedIntent;

  let routing, reasonId, bypassesQuietHours, batched;
  if (severity === "urgent") {
    routing = "deliver_immediately"; reasonId = "urgent_harm_bypasses_batching";
    bypassesQuietHours = true; batched = false;
  } else if (wakesJoe) {
    routing = "batch_for_morning"; reasonId = requiresJoeAuthority
      ? "requires_joe_authority" : "unresolved_intent";
    bypassesQuietHours = false; batched = true;
  } else {
    routing = "no_queue_entry"; reasonId = "ordinary_blocker_no_authority_required";
    bypassesQuietHours = false; batched = false;
  }

  return deepFreeze({
    schema_version: V5_A05_SCHEMA_VERSION,
    policy_version: V5_A05_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    reason_id: request.reason_id,
    severity,
    requires_joe_authority: requiresJoeAuthority,
    unresolved_intent: unresolvedIntent,
    quiet_now: quietNow,
    wakes_joe: wakesJoe,
    routing,
    routing_reason_id: reasonId,
    bypasses_quiet_hours: bypassesQuietHours,
    batched,
    effects: V5_NO_EFFECTS,
  });
}

/**
 * The "replan on miss" wiring: turn a cadence receipt evaluation with
 * `requires_replan: true` into the fixed ordinary escalation candidate that
 * routes it to the morning batch, so a miss is never silently a queue entry
 * that only sometimes gets created by a caller remembering to ask for one. A
 * cadence receipt that is `current` or has `no_receipt_on_record` yet is not a
 * miss and this function is not the one that decides whether to raise a
 * `no_cadence_receipt_on_record` item — that is a caller choice this module
 * does not make.
 */
export function escalationForCadenceMiss(cadenceEvaluation) {
  assertObject(cadenceEvaluation, "cadenceEvaluation");
  assertRequiredKeys(cadenceEvaluation, ["status", "requires_replan"], "cadenceEvaluation");
  if (cadenceEvaluation.status !== "missed" || cadenceEvaluation.requires_replan !== true) {
    fail("not_a_cadence_miss", "escalationForCadenceMiss requires a cadence evaluation whose status is \"missed\" and requires_replan is true",
      { status: cadenceEvaluation.status, requires_replan: cadenceEvaluation.requires_replan });
  }
  return evaluateEscalationRouting({
    reason_id: "cadence_miss_replan_required",
    requires_joe_authority: true,
    unresolved_intent: false,
    quiet_now: false,
  });
}

// ---------------------------------------------------------------------------
// Server-side derivation of urgency and authority-need (PR #1236 review,
// round 2, item 3). The caller names a reason id; it never names whether that
// reason is urgent enough to bypass quiet hours, nor whether it needs Joe's
// authority. Those are computed HERE, deterministically, from the reason id
// plus facts the server itself read:
//
//   * `seat`            -- the raising actor's class, derived by the verb from
//                          the server-authenticated actor, never an argument:
//                          "authority" (a verified human partner), "system"
//                          (the partner-sponsored local machine credential the
//                          daily sweep and ops jobs run as) or "other" (model
//                          agents, bots, reviewers, probes, anything
//                          unsponsored).
//   * `incident`        -- the ops.incident row the caller's incident_ref
//                          names, as the server read it, or null.
//   * `cadence_status`  -- ops.v5_a05_cadence_status for the subject, read on
//                          the server clock inside the same transaction.
//
// The table, closed and total over the reason vocabulary:
//
//   security_incident | data_loss | outward_harm      (urgent)
//       refused unless the seat is authority or system, AND the incident is
//       open (not resolved/reviewed), not adjudicated a duplicate, in
//       production, at SEV-0 or SEV-1. Honoured: urgent routing.
//   cadence_miss_replan_required                      (ordinary)
//       refused unless the server-clock cadence status reads "missed". A
//       replan is Joe's call: requires_joe_authority = true (morning batch).
//   decision_required                                 (ordinary)
//       by definition a decision only Joe's authority settles:
//       requires_joe_authority = true (morning batch).
//   delivery_blocker | review_blocker                 (ordinary)
//       neither authority nor unresolved intent: evidence only.
//
// No reason in today's closed vocabulary names unresolved intent, so the
// derivation never sets that axis; the router keeps it so that adding such a
// reason is a vocabulary change here, not a caller-asserted flag.
//
// A refusal is a POLICY ANSWER (ok:false with a stable refusal_id), never a
// throw: "you may not raise this" is a question the verb answers to its
// caller. Malformed input is still a contract violation and throws.
// ---------------------------------------------------------------------------

export const V5_A05_SEATS = deepFreeze(["authority", "system", "other"]);
export const V5_A05_URGENT_SEATS = deepFreeze(["authority", "system"]);
export const V5_A05_URGENT_INCIDENT_SEVERITIES = deepFreeze(["SEV-0", "SEV-1"]);
const CLOSED_INCIDENT_STATES = Object.freeze(["resolved", "reviewed"]);
const DERIVATION_REQUEST_KEYS = Object.freeze(["reason_id", "seat", "incident", "cadence_status"]);
const INCIDENT_FACT_KEYS = Object.freeze(["ref", "state", "severity", "environment", "duplicate_of_id"]);

function derivationRefusal(reasonId, refusalId, detail) {
  return deepFreeze({
    schema_version: V5_A05_SCHEMA_VERSION, policy_version: V5_A05_POLICY_VERSION,
    ok: false, reason_id: reasonId, refusal_id: refusalId, ...detail, effects: V5_NO_EFFECTS,
  });
}

function derivation(reasonId, requiresJoeAuthority, verifiedBy) {
  return deepFreeze({
    schema_version: V5_A05_SCHEMA_VERSION, policy_version: V5_A05_POLICY_VERSION,
    ok: true, reason_id: reasonId, requires_joe_authority: requiresJoeAuthority,
    unresolved_intent: false, verified_by: verifiedBy, effects: V5_NO_EFFECTS,
  });
}

export function deriveEscalationFacts(request) {
  assertObject(request, "request");
  assertClosedKeys(request, DERIVATION_REQUEST_KEYS, "request");
  assertRequiredKeys(request, DERIVATION_REQUEST_KEYS, "request");
  const severity = classifyEscalationReason(request.reason_id);
  if (!V5_A05_SEATS.includes(request.seat))
    fail("invalid_shape", "request.seat must be one of the closed seat classes",
      { path: "request.seat", seat: request.seat });
  let incident = null;
  if (request.incident !== null) {
    incident = assertObject(request.incident, "request.incident");
    assertClosedKeys(incident, INCIDENT_FACT_KEYS, "request.incident");
    assertRequiredKeys(incident, INCIDENT_FACT_KEYS, "request.incident");
  }
  if (request.cadence_status !== null && !V5_A05_CADENCE_STATUSES.includes(request.cadence_status))
    fail("invalid_shape", "request.cadence_status must be a known cadence status or null",
      { path: "request.cadence_status" });

  const reasonId = request.reason_id;
  if (severity === "urgent") {
    if (!V5_A05_URGENT_SEATS.includes(request.seat))
      return derivationRefusal(reasonId, "urgent_alert_requires_system_or_authority_seat", { seat: request.seat });
    if (incident === null)
      return derivationRefusal(reasonId, "urgent_alert_requires_verified_incident", {});
    const failed = [];
    if (CLOSED_INCIDENT_STATES.includes(incident.state)) failed.push("incident_not_open");
    if (incident.duplicate_of_id !== null) failed.push("incident_adjudicated_duplicate");
    if (incident.environment !== "production") failed.push("incident_not_production");
    if (!V5_A05_URGENT_INCIDENT_SEVERITIES.includes(incident.severity)) failed.push("incident_severity_not_urgent");
    if (failed.length)
      return derivationRefusal(reasonId, "urgent_alert_incident_not_verified",
        { incident_ref: incident.ref, failed_checks: failed });
    return derivation(reasonId, false, { kind: "open_production_incident", incident_ref: incident.ref,
      incident_severity: incident.severity, seat: request.seat });
  }
  if (reasonId === "cadence_miss_replan_required") {
    if (request.cadence_status !== "missed")
      return derivationRefusal(reasonId, "cadence_miss_not_verified", { cadence_status: request.cadence_status });
    return derivation(reasonId, true, { kind: "server_clock_cadence_status", cadence_status: "missed" });
  }
  if (reasonId === "decision_required")
    return derivation(reasonId, true, { kind: "reason_vocabulary" });
  return derivation(reasonId, false, { kind: "reason_vocabulary" });
}
