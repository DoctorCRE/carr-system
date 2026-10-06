// DoctorCRE v5 slice V5-F06 — the workflow EFFECT ENVELOPE, the one-use
// capability, pre-effect idempotency and the unknown-outcome quarantine.
//
// This closes the four `checkable_done` items of the reviewed catalog item as a
// set of PURE EVALUATORS over typed observations:
//
//   1. replay / substitution / wrong-account / expired capability refuses
//      -> evaluateCapabilityPresentation, nine ordered checks.
//   2. consumption commits before the provider call
//      -> evaluateConsumptionOrder, a proof over two reported sequence numbers.
//   3. a timeout enters `unknown` and a readback precedes any retry
//      -> evaluateAttemptResolution, the quarantine and its exits.
//   4. actor / account / deal-owner / signer remain distinct
//      -> four separately named, separately compared, positionally sealed
//         principal fields; see V5_ENVELOPE_PRINCIPAL_ROLES.
//
// WHAT THIS FILE IS NOT, said first because the name invites the wrong reading.
// It is not the Workflow Runtime, not the Connector Gateway, not a capability
// MINTER and not a transport. It issues no nonce, starts no clock, opens no
// connection, calls no provider, writes no row and consumes nothing. Every fact
// it decides on is a TYPED OBSERVATION THE CALLER SUPPLIES. An `allow` from
// evaluateCapabilityPresentation says the nine registered negatives did not fire
// on the facts as reported; it admits nobody to any outward action, and every
// result says so in `outward_effect_granted: false`.
//
// EFFECT CLASS: `capability_issuance_internal_until_attended_activation`. The
// catalog's own runtime evidence input —
// `step:foundation-control-plane-preactivation-contract-receipt` — is a receipt
// NOBODY HAS ISSUED, so no result here can be read as activation. It rides on
// every answer as `attended_activation_receipt_present: false` beside the exact
// step reference, so a consumer that wants activation has to go find it rather
// than infer it from an allow.
//
// TWO KINDS OF NO, inherited unchanged from global-boundaries.v5.js:
//   * A POLICY ANSWER is RETURNED — a frozen result whose `decision` is "allow"
//     or "refuse" with a stable `reason_id`. A refusal is an answer the caller
//     may record. "I cannot say" is one of these: an attempt that does not state
//     whether consumption committed is a thing a caller is allowed to report,
//     and it BLOCKS. Silence is never an allow, and silence is never a success.
//   * A CONTRACT VIOLATION THROWS V5BoundaryError. Unknown fields, unknown
//     states, open schemas, malformed digests and unreadable instants are not
//     policy questions; the module cannot read the request at all, so it fails
//     closed rather than guessing which negative was meant.
//
// NO CALLER MAY NAME WHICH CHECKS APPLY. `enforced_checks`, `skip_checks` and
// `trusted` are unknown fields, so a request that tries to narrow the test
// cannot be read at all — the closed-key discipline that makes `enforced_axes`
// unreadable in command-version-compatibility.v5.js and `skip_checks`
// unreadable in command-supervisor-admission.v5.js. The check list is a module
// constant in a FIXED ORDER, and the order is load-bearing: the first check that
// does not pass refuses and everything after it reports `not_reached`. That is
// how "an unauthorized actor refuses BEFORE any binding is compared" is a
// structural property of this file rather than a promise about it.
//
// A LABEL NEVER OUTVOTES THE CLOCK. A capability whose reported state says
// "active" while its own `expires_at` has passed is EXPIRED. The reported state
// is carried into the answer so a reader can see what was claimed; it decides
// nothing on its own. This is command-supervisor-admission.v5.js's rule — a
// label never outvotes a digest — applied to time.
//
// THE DATA BOUNDARY IS STRUCTURAL, NOT ADVISORY. Capabilities here are OPAQUE
// HANDLES. The closed key sets contain no field that could hold a token, a
// secret, a bearer credential or a provider password, so a caller that tries to
// attach one gets `unknown_field` rather than a stored secret; and the handle
// itself is refused if it is shaped like a credential (a dotted JWT-like triple,
// a PEM block, embedded whitespace). The exact ACTION CONTENT never enters this
// module either: the envelope carries `payload_digest` and never the payload, so
// a receipt built from any result here cannot contain the body of an email.
//
// WHERE THE VOCABULARY COMES FROM. Nothing is re-decided that the tree already
// decides. The action registry, the capability each action requires and the
// controls each action names are global-boundaries.v5.js's `V5_ACTIONS`, read
// and never copied. The nonce vocabulary, the capability-state vocabulary and
// the per-check state vocabulary are command-supervisor-admission.v5.js's —
// V5-F07 is a declared source build dependency of this slice, and importing its
// constants means a drift there breaks here instead of two lists silently
// disagreeing. The actor authority answer is global-boundaries.v5.js's, READ AS
// AN INPUT and never recomputed.
//
// WHAT IS DELIBERATELY NOT BUILT, AND WHY GUESSING WOULD BE WORSE.
//
//   * THE SETTLED DECISION BINDING. This slice's seven decisions — Q015.D1,
//     Q101.D1, Q102.D1, Q105.D1, Q109.D1, Q110.D1, Q132.D1 — are named in the
//     catalog by ID ONLY. Their settled text and their source-evidence digests
//     live in the doctrine store and appear nowhere in this repository, so there
//     is no `assertSettledDecisionBinding` here: siblings that have one copied
//     verbatim text from a reviewed source binding, and inventing seven digests
//     to imitate that shape would ship a false provenance. The decision IDS are
//     hashed into the policy preimage and the binding is declared `unbound`
//     behind V5_F06_DECISION_BINDING_SEAM, so the day the text arrives the
//     policy digest moves and stale readers are refused.
//
//   * THE AUTONOMY TIER LADDER. The catalog's excluded scope forbids a "silent
//     autonomy upgrade", but the tier VOCABULARY and the ladder saying which
//     tier permits which action are doctrine and are not in this tree. So the
//     tier is carried as an OPAQUE LABEL and compared for BYTE EQUALITY: a
//     presentation whose tier label differs from the issued one in any way
//     refuses. That is the fail-closed reading, it needs no vocabulary, and it
//     cannot be mistaken for a ladder. V5_F06_AUTONOMY_TIER_LADDER_SEAM.
//
//   * THE EFFECT CAP VOCABULARY. Which caps exist and what their limits are is
//     product policy nobody has written down here. What IS decided here is the
//     DISCIPLINE: every cap the envelope declares must be observed at
//     presentation (an unobserved cap blocks — silence is not headroom), an
//     observation over its limit refuses, and an observed cap the envelope never
//     declared refuses. V5_F06_EFFECT_CAP_VOCABULARY_SEAM.
//
//   * THE PROVIDER-SIDE READBACK KEY. The idempotency key this module joins on
//     is the ENVELOPE DIGEST, which it computes. How each provider echoes that
//     key back on its own surface is that provider's contract and is not held
//     here. V5_F06_PROVIDER_READBACK_JOIN_SEAM.
//
//   * THE SHORT-LIFETIME CEILING. "Short-lived" is enforced structurally — a
//     capability MUST carry an explicit `expires_at` strictly after `issued_at`,
//     so an open-ended capability is unreadable rather than merely discouraged.
//     The NUMBER of seconds that counts as short is a policy value this slice
//     does not hold, and picking one would ship an invented binding.
//     V5_F06_CAPABILITY_LIFETIME_CEILING_SEAM.
//
// The module is pure. It reads no filesystem, no network, no database, no
// scheduler, no environment and no clock: every evaluation that depends on time
// takes `now` from its caller, so two callers holding the same request always
// reach the same answer. `V5_NO_EFFECTS` rides on every result to say so.

import { canonicalJson, digest } from "./artifact-trust.js";
import {
  V5BoundaryError,
  V5_NO_EFFECTS,
  V5_ACTIONS,
  V5_ACTION_KEYS,
  V5_AUTHORITY_CLASSES,
} from "./global-boundaries.v5.js";
import { ORGANIZATION_TENANT_ID } from "./identity.js";
import {
  V5_ADMISSION_CHECK_STATES,
  V5_CAPABILITY_STATES,
  V5_NONCE_STATES,
} from "./command-supervisor-admission.v5.js";

export const V5_EFFECT_ENVELOPE_SCHEMA_VERSION = "doctorcre-v5-workflow-effect-envelope.v1";
export const V5_EFFECT_ENVELOPE_POLICY_VERSION = 1;

/** The sealed shape whose digest IS the effect's identity and idempotency key. */
export const V5_EFFECT_ENVELOPE_KIND = "workflow-effect-envelope.v1";
/** The sealed shape of one opaque, single-use, expiring capability. */
export const V5_EFFECT_CAPABILITY_KIND = "workflow-effect-capability.v1";

/** The catalog's own runtime evidence input. Nobody has issued it. */
export const V5_ATTENDED_ACTIVATION_RECEIPT_STEP =
  "step:foundation-control-plane-preactivation-contract-receipt";

/** The seven decisions this slice answers to, by ID. Their text is not held. */
export const V5_F06_DECISION_IDS = Object.freeze([
  "Q015.D1", "Q101.D1", "Q102.D1", "Q105.D1", "Q109.D1", "Q110.D1", "Q132.D1",
]);

export const V5_F06_DECISION_BINDING_SEAM = "step:v5-f06-settled-decision-source-binding";
export const V5_F06_AUTONOMY_TIER_LADDER_SEAM = "step:v5-f06-autonomy-tier-vocabulary-and-ladder";
export const V5_F06_EFFECT_CAP_VOCABULARY_SEAM = "step:v5-f06-effect-cap-vocabulary";
export const V5_F06_PROVIDER_READBACK_JOIN_SEAM = "step:v5-f06-provider-readback-join-key";
export const V5_F06_CAPABILITY_LIFETIME_CEILING_SEAM =
  "step:v5-f06-capability-lifetime-ceiling";

export const V5_F06_SEAMS = Object.freeze([
  V5_F06_AUTONOMY_TIER_LADDER_SEAM,
  V5_F06_CAPABILITY_LIFETIME_CEILING_SEAM,
  V5_F06_DECISION_BINDING_SEAM,
  V5_F06_EFFECT_CAP_VOCABULARY_SEAM,
  V5_F06_PROVIDER_READBACK_JOIN_SEAM,
].sort());

/**
 * THE FOUR PRINCIPALS, IN THE ORDER THEY ARE COMPARED. This list is the whole
 * of `checkable_done` item 4 and its order is load-bearing in two places: the
 * comparison order (so the reported reason is deterministic when more than one
 * has drifted) and the SEAL (so the envelope digest is a function of WHICH role
 * holds which value, not of the set of values).
 *
 * They are four fields, never one. Two of them may legitimately hold the same
 * slug — Joe is routinely both actor and deal owner — and that coincidence must
 * not collapse them: swapping two values between roles is a DIFFERENT envelope
 * with a different digest, and a capability bound to one arrangement refuses
 * against the other. `account` is deliberately the odd one out: it is the
 * PROVIDER ACCOUNT the effect lands on — a mailbox, a connector seat — and not
 * an actor slug, which is why "wrong account" is a refusal of its own rather
 * than a restatement of "wrong actor".
 */
export const V5_ENVELOPE_PRINCIPAL_ROLES = Object.freeze([
  "actor", "account", "deal_owner", "signer",
]);

/** The envelope field each role is sealed and compared under. */
const PRINCIPAL_FIELD = Object.freeze({
  actor: "actor_slug",
  account: "account_ref",
  deal_owner: "deal_owner_slug",
  signer: "signer_slug",
});

/** The refusal each role produces when the presented value is not the bound one. */
const PRINCIPAL_MISMATCH_REASON = Object.freeze({
  actor: "actor_mismatch",
  account: "account_mismatch",
  deal_owner: "deal_owner_mismatch",
  signer: "signer_mismatch",
});

const PRINCIPAL_ISSUANCE_MISMATCH_REASON = Object.freeze({
  actor: "issuance_actor_binding_mismatch",
  account: "issuance_account_binding_mismatch",
  deal_owner: "issuance_deal_owner_binding_mismatch",
  signer: "issuance_signer_binding_mismatch",
});

/**
 * A capability is good for exactly one consumption. This is a module constant
 * rather than a capability field, because a caller that can declare its own use
 * count can declare its way out of single use.
 */
export const V5_CAPABILITY_USES_ALLOWED = 1;

/**
 * The idempotency key is the ENVELOPE DIGEST, and it exists BEFORE the effect —
 * that is what "pre-effect idempotency" means. It is not a provider receipt id,
 * not a row id and not a retry counter: it is the deterministic identity of the
 * intended effect, computable by both sides before anything happens.
 */
export const V5_IDEMPOTENCY_KEY_SOURCE = "envelope_digest";

// --- the check ladders -----------------------------------------------------

/**
 * The registered presentation negatives, IN THE ORDER THEY ARE APPLIED.
 * `actor_authority` is first so that an actor the boundary kernel refused is
 * turned away BEFORE any envelope digest, principal or payload is compared:
 * a system that compares bindings for an unauthorized actor has already leaked
 * which bindings exist.
 */
export const V5_PRESENTATION_CHECKS = Object.freeze([
  "actor_authority",
  "capability_binding",
  "capability_validity_window",
  "nonce_state",
  "principal_binding",
  "autonomy_tier",
  "action_binding",
  "payload_binding",
  "effect_caps",
]);

/** The checks decided before any binding is compared. */
export const V5_CHECKS_BEFORE_ANY_BINDING_IS_COMPARED = Object.freeze(["actor_authority"]);

export const V5_ISSUANCE_CHECKS = Object.freeze([
  "actor_authority",
  "envelope_binding",
  "capability_scope",
  "principal_binding",
  "autonomy_tier",
  "validity_window",
]);

export const V5_ORDER_CHECKS = Object.freeze([
  "attempt_binding",
  "consumption_commit",
  "provider_call_order",
]);

export const V5_RESOLUTION_CHECKS = Object.freeze([
  "consumption_order",
  "readback_join",
  "outcome_resolution",
  "next_step",
]);

// --- the closed state vocabularies -----------------------------------------

/**
 * Did the consumption record commit? `unstated` BLOCKS: an attempt that cannot
 * say whether it committed its consumption before calling out is exactly the
 * attempt the ordering rule exists to catch.
 */
export const V5_CONSUMPTION_STATES = Object.freeze([
  "committed", "uncommitted", "failed", "unstated",
]);

export const V5_PROVIDER_CALL_STATES = Object.freeze(["not_started", "started", "unstated"]);

/**
 * How the attempt ended AS REPORTED. `timed_out` is the one this slice is named
 * for and `unstated` is treated identically: both mean the caller does not know
 * what the provider did, and both enter the quarantine.
 */
export const V5_ATTEMPT_OUTCOME_STATES = Object.freeze([
  "succeeded", "failed", "timed_out", "unstated",
]);

/** What a provider readback found. `indeterminate` keeps the quarantine shut. */
export const V5_READBACK_STATES = Object.freeze([
  "effect_present", "effect_absent", "indeterminate", "unstated",
]);

/** The quarantine has exactly three exits and `unknown` is a first-class one. */
export const V5_ATTEMPT_RESOLUTIONS = Object.freeze([
  "confirmed_success", "confirmed_failure", "unknown",
]);

/** What a caller may propose doing next. */
export const V5_NEXT_STEPS = Object.freeze(["provider_readback", "retry", "settle"]);

/**
 * The outcome-to-resolution map. It is a constant, hashed into the policy
 * preimage, because it is the whole of `checkable_done` item 3's first half and
 * an inline `if` at the comparison site would let it change without moving the
 * policy digest — the defect this lane already paid for once.
 */
export const V5_OUTCOME_RESOLUTIONS = Object.freeze({
  succeeded: "confirmed_success",
  failed: "confirmed_failure",
  timed_out: "unknown",
  unstated: "unknown",
});

/** What a readback resolves a quarantined attempt to. */
export const V5_READBACK_RESOLUTIONS = Object.freeze({
  effect_present: "confirmed_success",
  effect_absent: "confirmed_failure",
  indeterminate: "unknown",
  unstated: "unknown",
});

export const V5_EFFECT_ENVELOPE_REASON_IDS = Object.freeze([
  "account_mismatch",
  "action_substituted",
  "actor_authority_refused",
  "actor_authority_unobservable",
  "actor_mismatch",
  "attempt_bound_to_other_capability",
  "attempt_bound_to_other_envelope",
  "autonomy_tier_changed_after_issuance",
  "cap_exceeded",
  "cap_unobserved",
  "capability_expired",
  "capability_not_issued_for_envelope",
  "capability_not_yet_valid",
  "capability_replayed",
  "capability_revoked",
  "capability_state_unobservable",
  "capability_substituted",
  "capability_unissued",
  "consumption_committed_before_provider_call",
  "consumption_committed_provider_call_not_started",
  "consumption_failed",
  "consumption_order_refused",
  "consumption_sequence_unobservable",
  "consumption_state_unobservable",
  "consumption_uncommitted",
  "deal_owner_mismatch",
  "effect_already_present",
  "envelope_substituted",
  "issuance_account_binding_mismatch",
  "issuance_actor_binding_mismatch",
  "issuance_admissible_internal_only",
  "issuance_authority_refused",
  "issuance_authority_unobservable",
  "issuance_autonomy_tier_mismatch",
  "issuance_capability_scope_mismatch",
  "issuance_deal_owner_binding_mismatch",
  "issuance_envelope_binding_mismatch",
  "issuance_signer_binding_mismatch",
  "issuance_window_already_expired",
  "issuance_window_not_yet_open",
  "nonce_state_unobservable",
  "outcome_already_confirmed",
  "outcome_readback_conflict",
  "payload_substituted",
  "presentation_admissible_for_consumption",
  "provider_call_preceded_consumption",
  "provider_call_sequence_unobservable",
  "provider_call_state_unobservable",
  "readback_admissible",
  "readback_indeterminate",
  "readback_joined_on_other_effect",
  "readback_required_before_retry",
  "retry_admissible_with_fresh_capability",
  "retry_capability_unstated",
  "retry_reuses_consumed_capability",
  "retry_without_readback",
  "settlement_admissible",
  "signer_mismatch",
  "undeclared_cap_observed",
  "unknown_outcome_may_not_settle",
]);

// ---------------------------------------------------------------------------
// Shape discipline. Lifted from the sibling v5 modules rather than reinvented:
// the same closed-key posture, the same two-kinds-of-no split, and the same
// literal-calendar instant check that keeps 2026-02-31 from silently becoming
// 3 March inside a capability window.
// ---------------------------------------------------------------------------

const SHA256_REF = /^sha256:[0-9a-f]{64}$/;
const STABLE_ID = /^[A-Za-z0-9][A-Za-z0-9._:+-]{0,255}$/;
const ISO_INSTANT =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(?:Z|([+-])(\d{2}):(\d{2}))$/;

// A handle that LOOKS like a credential is refused as one. Three dot-separated
// segments is the JWT shape; a PEM header is a key; whitespace lets one handle
// read as two. None of these is a policy question — an opaque handle that is not
// opaque means the data boundary has already been crossed.
const JWT_SHAPED = /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/;
const PEM_SHAPED = /-----BEGIN /;

const ENVELOPE_KEYS = Object.freeze([
  "action", "autonomy_tier_label", "caps", "envelope_id", "payload_digest",
  "principals", "step_id", "tenant", "workflow_id",
]);
const PRINCIPALS_KEYS = Object.freeze([
  "account_ref", "actor_slug", "deal_owner_slug", "signer_slug",
]);
const CAP_KEYS = Object.freeze(["cap_key", "limit"]);
const CAP_OBSERVATION_KEYS = Object.freeze(["cap_key", "observed"]);

const CAPABILITY_KEYS = Object.freeze([
  "autonomy_tier_label", "capability", "capability_id", "envelope_digest",
  "expires_at", "issued_at", "nonce", "principals", "tenant",
]);

const PRESENTATION_KEYS = Object.freeze([
  "cap_observations", "capability_id", "capability_state", "envelope_digest",
  "nonce_state", "payload_digest", "presentation_id", "presented_autonomy_tier_label",
  "presented_principals", "requested_action",
]);

const ATTEMPT_KEYS = Object.freeze([
  "attempt_id", "capability_id", "consumption", "envelope_digest", "outcome",
  "provider_call", "readback",
]);
const CONSUMPTION_KEYS = Object.freeze(["committed_seq", "state"]);
const PROVIDER_CALL_KEYS = Object.freeze(["started_seq", "state"]);
const OUTCOME_KEYS = Object.freeze(["state"]);
const READBACK_KEYS = Object.freeze(["join_key", "state"]);

const NORMALIZED_ENVELOPE_KEYS = Object.freeze([
  "action", "autonomy_tier_label", "caps", "envelope_digest", "envelope_id",
  "envelope_kind", "payload_digest", "principals", "required_capability",
  "schema_version", "step_id", "tenant", "workflow_id",
]);
const NORMALIZED_CAPABILITY_KEYS = Object.freeze([
  "autonomy_tier_label", "capability", "capability_id", "capability_kind",
  "envelope_digest", "expires_at", "issued_at", "nonce", "principals",
  "schema_version", "tenant", "uses_allowed",
]);
const NORMALIZED_PRESENTATION_KEYS = Object.freeze([
  "cap_observations", "capability_id", "capability_state", "envelope_digest",
  "nonce_state", "payload_digest", "presentation_id", "presented_autonomy_tier_label",
  "presented_principals", "report_kind", "requested_action", "schema_version",
]);
const NORMALIZED_ATTEMPT_KEYS = Object.freeze([
  "attempt_id", "capability_id", "consumption", "envelope_digest", "outcome",
  "provider_call", "readback", "report_kind", "schema_version",
]);

// The keys a genuine evaluateActorAuthority answer can carry. A key outside this
// union means the object did not come out of that evaluator.
const AUTHORITY_ANSWER_KEYS = Object.freeze([
  "action", "actor_slug", "authority_class", "authorization_class", "bound_action",
  "decision", "deferred_for", "delegable", "effects", "expires_at", "grant_kind",
  "grant_ref", "permanent_privilege_granted", "reason_id", "required_capability",
  "required_controls", "satisfied_controls", "tenant", "validated_grants",
  "accepted_grant_kinds",
]);

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

/** An open schema is an unenforced one; an unread field could be a smuggled control. */
function assertClosedKeys(object, allowed, path) {
  for (const key of Object.keys(object)) {
    if (!allowed.includes(key)) {
      fail("unknown_field", `unknown field "${key}" at ${path}`, { path: `${path}.${key}`, key });
    }
  }
}

function assertRequiredKeys(object, required, path) {
  for (const key of required) {
    if (!(key in object)) fail("missing_field", `${path}.${key} is required`, { path: `${path}.${key}` });
  }
}

function assertNonEmptyString(value, path) {
  if (typeof value !== "string" || value.length === 0) {
    fail("invalid_shape", `${path} must be a non-empty string`, { path });
  }
  return value;
}

function assertStableId(value, path) {
  assertNonEmptyString(value, path);
  if (!STABLE_ID.test(value)) {
    fail("invalid_identifier", `${path} must be a stable identifier`, { path });
  }
  return value;
}

/**
 * An OPAQUE HANDLE. Stable-id shaped, and additionally refused when it carries
 * the shape of credential material. The data boundary says credentials remain at
 * the execution platform or device; a handle that is a bearer token has already
 * moved one here.
 */
function assertOpaqueHandle(value, path) {
  assertNonEmptyString(value, path);
  if (/\s/.test(value) || PEM_SHAPED.test(value) || JWT_SHAPED.test(value)) {
    fail("credential_material_in_capability",
      `${path} is shaped like credential material; capabilities are opaque handles and credentials stay at the execution platform`,
      { path });
  }
  if (!STABLE_ID.test(value)) {
    fail("invalid_identifier", `${path} must be a stable opaque handle`, { path });
  }
  return value;
}

function assertDigestRef(value, path) {
  if (typeof value !== "string" || !SHA256_REF.test(value)) {
    fail("invalid_digest", `${path} must be a "sha256:" reference to a 64-character lower-case digest`, { path });
  }
  return value;
}

function assertEnum(value, allowed, path, code) {
  if (typeof value !== "string" || !allowed.includes(value)) {
    fail(code, `${path} must be one of the registered values`, { path, registered: [...allowed] });
  }
  return value;
}

function assertSafeCount(value, path) {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    fail("invalid_shape", `${path} must be a non-negative safe integer`, { path });
  }
  return value;
}

function optionalSafeCount(value, path) {
  if (value === undefined || value === null) return null;
  return assertSafeCount(value, path);
}

function daysInMonth(year, month) {
  if (month === 2) return (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0 ? 29 : 28;
  return [4, 6, 9, 11].includes(month) ? 30 : 31;
}

/**
 * Timestamps are parsed, never inferred, and the calendar is checked against the
 * LITERAL fields before parsing: Date.parse silently normalizes an impossible
 * date rather than rejecting it, and a capability window computed from
 * "2026-02-31T00:00:00Z" would expire at an instant nobody wrote.
 */
function assertInstant(value, path) {
  const match = typeof value === "string" ? ISO_INSTANT.exec(value) : null;
  if (!match) {
    fail("invalid_timestamp", `${path} must be an ISO-8601 instant with an explicit offset`, { path, value });
  }
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

function assertTenant(value, path) {
  if (value !== ORGANIZATION_TENANT_ID) {
    fail("tenant_mismatch", `${path} must be "${ORGANIZATION_TENANT_ID}"`,
      { path, expected: ORGANIZATION_TENANT_ID, actual: value });
  }
  return value;
}

/**
 * The four principals, read as four separate REQUIRED fields. There is no
 * default and no borrowing: an omitted signer is a missing field, never the
 * actor standing in for one.
 */
function normalizePrincipals(value, path) {
  const raw = assertObject(value, path);
  assertClosedKeys(raw, PRINCIPALS_KEYS, path);
  assertRequiredKeys(raw, PRINCIPALS_KEYS, path);
  const out = {};
  for (const role of V5_ENVELOPE_PRINCIPAL_ROLES) {
    const field = PRINCIPAL_FIELD[role];
    out[field] = assertStableId(raw[field], `${path}.${field}`);
  }
  return deepFreeze(out);
}

function normalizeCaps(value, path) {
  if (value === undefined || value === null) return deepFreeze([]);
  if (!Array.isArray(value)) fail("invalid_shape", `${path} must be an array`, { path });
  const seen = new Set();
  const caps = value.map((entry, index) => {
    const item = assertObject(entry, `${path}[${index}]`);
    assertClosedKeys(item, CAP_KEYS, `${path}[${index}]`);
    assertRequiredKeys(item, CAP_KEYS, `${path}[${index}]`);
    const cap_key = assertStableId(item.cap_key, `${path}[${index}].cap_key`);
    if (seen.has(cap_key)) {
      fail("duplicate_cap", `${path} declares "${cap_key}" more than once`, { path, cap_key });
    }
    seen.add(cap_key);
    return { cap_key, limit: assertSafeCount(item.limit, `${path}[${index}].limit`) };
  });
  caps.sort((left, right) => left.cap_key.localeCompare(right.cap_key));
  return deepFreeze(caps);
}

function normalizeCapObservations(value, path) {
  if (value === undefined || value === null) return deepFreeze([]);
  if (!Array.isArray(value)) fail("invalid_shape", `${path} must be an array`, { path });
  const seen = new Set();
  const observations = value.map((entry, index) => {
    const item = assertObject(entry, `${path}[${index}]`);
    assertClosedKeys(item, CAP_OBSERVATION_KEYS, `${path}[${index}]`);
    assertRequiredKeys(item, CAP_OBSERVATION_KEYS, `${path}[${index}]`);
    const cap_key = assertStableId(item.cap_key, `${path}[${index}].cap_key`);
    if (seen.has(cap_key)) {
      fail("duplicate_cap", `${path} observes "${cap_key}" more than once`, { path, cap_key });
    }
    seen.add(cap_key);
    return { cap_key, observed: assertSafeCount(item.observed, `${path}[${index}].observed`) };
  });
  observations.sort((left, right) => left.cap_key.localeCompare(right.cap_key));
  return deepFreeze(observations);
}

// ---------------------------------------------------------------------------
// The effect envelope.
// ---------------------------------------------------------------------------

/**
 * The exact bytes the envelope digest is taken over. Every field that changes
 * WHAT the effect is, WHO it is for, or WHAT BOUNDS it is in here; nothing that
 * merely describes the request is.
 *
 * The four principals are emitted UNDER THEIR OWN KEYS, not as a list, so the
 * digest is a function of which role holds which value. A set-shaped seal would
 * make swapping the deal owner and the signer invisible.
 */
export function effectEnvelopePreimage(envelope) {
  const source = assertObject(envelope, "envelope");
  return {
    envelope_kind: V5_EFFECT_ENVELOPE_KIND,
    schema_version: V5_EFFECT_ENVELOPE_SCHEMA_VERSION,
    tenant: source.tenant,
    envelope_id: source.envelope_id,
    workflow_id: source.workflow_id,
    step_id: source.step_id,
    action: source.action,
    required_capability: source.required_capability,
    principals: {
      actor_slug: source.principals.actor_slug,
      account_ref: source.principals.account_ref,
      deal_owner_slug: source.principals.deal_owner_slug,
      signer_slug: source.principals.signer_slug,
    },
    payload_digest: source.payload_digest,
    caps: source.caps.map(cap => ({ cap_key: cap.cap_key, limit: cap.limit })),
    autonomy_tier_label: source.autonomy_tier_label,
  };
}

export function effectEnvelopeDigest(envelope) {
  return digest(effectEnvelopePreimage(envelope));
}

/**
 * Read one effect envelope, or refuse to read it at all.
 *
 * The action must be a REGISTERED v5 action that names a required capability.
 * That is not a new registry: it is global-boundaries.v5.js's `V5_ACTIONS`, and
 * an action with no `required_capability` cannot be capability-issued because
 * there is no capability to issue. A generic "send anything" action does not
 * exist to be named — the catalog's excluded scope forbids generic send and
 * mutation authority, and here that exclusion is enforced by the registry rather
 * than by a wildcard check that a caller could spell around.
 *
 * The PAYLOAD ITSELF NEVER ENTERS. Only `payload_digest` does, so no receipt
 * built from any answer in this module can carry the body of an effect.
 */
export function normalizeEffectEnvelope(request) {
  const raw = assertObject(request, "envelope");
  assertClosedKeys(raw, ENVELOPE_KEYS, "envelope");
  assertRequiredKeys(raw,
    ["action", "autonomy_tier_label", "envelope_id", "payload_digest", "principals",
      "step_id", "tenant", "workflow_id"], "envelope");
  assertTenant(raw.tenant, "envelope.tenant");
  const action = assertEnum(raw.action, V5_ACTION_KEYS, "envelope.action", "unknown_action");
  const required_capability = V5_ACTIONS[action].required_capability;
  if (typeof required_capability !== "string" || required_capability.length === 0) {
    fail("action_has_no_required_capability",
      `"${action}" names no required capability, so no capability can be issued for it`,
      { action });
  }
  const sealed = {
    tenant: ORGANIZATION_TENANT_ID,
    envelope_id: assertStableId(raw.envelope_id, "envelope.envelope_id"),
    workflow_id: assertStableId(raw.workflow_id, "envelope.workflow_id"),
    step_id: assertStableId(raw.step_id, "envelope.step_id"),
    action,
    required_capability,
    principals: normalizePrincipals(raw.principals, "envelope.principals"),
    payload_digest: assertDigestRef(raw.payload_digest, "envelope.payload_digest"),
    caps: normalizeCaps(raw.caps, "envelope.caps"),
    autonomy_tier_label: assertStableId(raw.autonomy_tier_label, "envelope.autonomy_tier_label"),
  };
  return deepFreeze({
    schema_version: V5_EFFECT_ENVELOPE_SCHEMA_VERSION,
    envelope_kind: V5_EFFECT_ENVELOPE_KIND,
    ...sealed,
    envelope_digest: effectEnvelopeDigest(sealed),
  });
}

/** A normalized envelope, re-checked at an evaluator door. */
function assertNormalizedEnvelope(value, path) {
  const envelope = assertObject(value, path);
  assertClosedKeys(envelope, NORMALIZED_ENVELOPE_KEYS, path);
  assertRequiredKeys(envelope, NORMALIZED_ENVELOPE_KEYS, path);
  if (envelope.envelope_kind !== V5_EFFECT_ENVELOPE_KIND ||
      envelope.schema_version !== V5_EFFECT_ENVELOPE_SCHEMA_VERSION) {
    fail("unnormalized_report", `${path} is not a normalized v5 effect envelope`, { path });
  }
  // The seal is re-taken here rather than trusted: an envelope whose fields were
  // edited after normalization no longer hashes to its own digest, and this is
  // where that is caught instead of at the provider.
  if (effectEnvelopeDigest(envelope) !== envelope.envelope_digest) {
    fail("envelope_seal_broken",
      `${path} does not hash to its own envelope_digest; the envelope is immutable and this copy was edited`,
      { path, sealed: envelope.envelope_digest, recomputed: effectEnvelopeDigest(envelope) });
  }
  return envelope;
}

// ---------------------------------------------------------------------------
// The capability. Opaque, single-use, bounded in time, bound to one envelope.
// ---------------------------------------------------------------------------

/**
 * Read one issued capability, or refuse to read it at all.
 *
 * SHORT-LIVED IS ENFORCED AS BOUNDED. `expires_at` is required and must be
 * strictly after `issued_at`, so there is no such thing as an open-ended
 * capability here. How short "short" is is a policy number this slice does not
 * hold; see V5_F06_CAPABILITY_LIFETIME_CEILING_SEAM.
 *
 * The closed key set carries no place to put a token, and the two handles are
 * refused if they are shaped like credential material.
 */
export function normalizeEffectCapability(request) {
  const raw = assertObject(request, "capability");
  assertClosedKeys(raw, CAPABILITY_KEYS, "capability");
  assertRequiredKeys(raw, CAPABILITY_KEYS, "capability");
  assertTenant(raw.tenant, "capability.tenant");
  const issued_at = assertInstant(raw.issued_at, "capability.issued_at");
  const expires_at = assertInstant(raw.expires_at, "capability.expires_at");
  if (expires_at <= issued_at) {
    fail("unbounded_capability_window",
      "capability.expires_at must be strictly after capability.issued_at; a capability with no window is not short-lived",
      { issued_at: raw.issued_at, expires_at: raw.expires_at });
  }
  return deepFreeze({
    schema_version: V5_EFFECT_ENVELOPE_SCHEMA_VERSION,
    capability_kind: V5_EFFECT_CAPABILITY_KIND,
    tenant: ORGANIZATION_TENANT_ID,
    capability_id: assertOpaqueHandle(raw.capability_id, "capability.capability_id"),
    envelope_digest: assertDigestRef(raw.envelope_digest, "capability.envelope_digest"),
    capability: assertStableId(raw.capability, "capability.capability"),
    nonce: assertOpaqueHandle(raw.nonce, "capability.nonce"),
    principals: normalizePrincipals(raw.principals, "capability.principals"),
    autonomy_tier_label: assertStableId(raw.autonomy_tier_label, "capability.autonomy_tier_label"),
    issued_at: raw.issued_at,
    expires_at: raw.expires_at,
    uses_allowed: V5_CAPABILITY_USES_ALLOWED,
  });
}

function assertNormalizedCapability(value, path) {
  const capability = assertObject(value, path);
  assertClosedKeys(capability, NORMALIZED_CAPABILITY_KEYS, path);
  assertRequiredKeys(capability, NORMALIZED_CAPABILITY_KEYS, path);
  if (capability.capability_kind !== V5_EFFECT_CAPABILITY_KIND ||
      capability.schema_version !== V5_EFFECT_ENVELOPE_SCHEMA_VERSION) {
    fail("unnormalized_report", `${path} is not a normalized v5 effect capability`, { path });
  }
  if (capability.uses_allowed !== V5_CAPABILITY_USES_ALLOWED) {
    fail("unnormalized_report",
      `${path} claims a use count other than the single use this module allows`,
      { path, uses_allowed: capability.uses_allowed });
  }
  return capability;
}

// ---------------------------------------------------------------------------
// The actor authority answer — READ, never recomputed.
// ---------------------------------------------------------------------------

/**
 * Accept an answer from global-boundaries.v5.js's `evaluateActorAuthority` and
 * refuse anything else.
 *
 * WHAT THIS DOES AND DOES NOT ESTABLISH, said plainly because the difference
 * matters: this is a STRUCTURAL check that the object has the shape, the frozen
 * identity, the zero-effect marker and the registry consistency of a genuine
 * answer. It is not a signature and this module cannot verify provenance — a
 * gateway must obtain the answer from the kernel rather than assemble one. What
 * it does buy is that a hand-written `{decision:"allow"}` is unreadable, and an
 * answer that is inconsistent with `V5_ACTIONS` for the action it claims to be
 * about is unreadable too.
 */
function assertAuthorityAnswer(value, action, actorSlug, path) {
  const answer = assertObject(value, path);
  if (!Object.isFrozen(answer)) {
    fail("foreign_authority_answer",
      `${path} is not a frozen answer from the boundary kernel`, { path });
  }
  assertClosedKeys(answer, AUTHORITY_ANSWER_KEYS, path);
  // A missing field here is not a caller filling in a form badly; it means the
  // object did not come out of the boundary kernel, so it reports as a foreign
  // answer rather than as an incomplete request.
  for (const key of ["decision", "reason_id", "action", "actor_slug", "authority_class",
    "tenant", "permanent_privilege_granted", "effects"]) {
    if (!(key in answer)) {
      fail("foreign_authority_answer",
        `${path} is missing "${key}"; every answer from the boundary kernel carries it`,
        { path, key });
    }
  }
  if (answer.decision !== "allow" && answer.decision !== "refuse") {
    fail("foreign_authority_answer", `${path}.decision must be "allow" or "refuse"`, { path });
  }
  if (typeof answer.reason_id !== "string" || answer.reason_id.length === 0) {
    fail("foreign_authority_answer", `${path}.reason_id must be a non-empty string`, { path });
  }
  assertTenant(answer.tenant, `${path}.tenant`);
  if (answer.action !== action) {
    fail("foreign_authority_answer",
      `${path} answers a different action than the envelope names`,
      { path, expected: action, actual: answer.action });
  }
  // An answer about a DIFFERENT actor is not an answer about this effect. This
  // is the hole that makes "wrong actor" survivable everywhere else: refuse Dell
  // for one action, then hand that same refusal-shaped allow to an envelope
  // whose actor is Joe. The two must be the same person or the answer is
  // unreadable.
  if (answer.actor_slug !== actorSlug) {
    fail("foreign_authority_answer",
      `${path} answers for a different actor than the envelope binds`,
      { path, expected: actorSlug, actual: answer.actor_slug });
  }
  assertEnum(answer.authority_class, V5_AUTHORITY_CLASSES, `${path}.authority_class`,
    "foreign_authority_answer");
  if (answer.permanent_privilege_granted !== false) {
    fail("foreign_authority_answer",
      `${path} claims a permanent privilege; no answer from the boundary kernel does`, { path });
  }
  if (canonicalJson(answer.effects) !== canonicalJson(V5_NO_EFFECTS)) {
    fail("foreign_authority_answer",
      `${path} does not carry the boundary kernel's zero-effect marker`, { path });
  }
  // Registry consistency: an allow that claims to have satisfied controls must
  // claim exactly the controls the shared registry names for that action.
  if (answer.decision === "allow" && "satisfied_controls" in answer) {
    const required = [...(V5_ACTIONS[action].required_controls ?? [])];
    if (canonicalJson([...answer.satisfied_controls].sort()) !== canonicalJson(required.sort())) {
      fail("foreign_authority_answer",
        `${path} claims controls the shared action registry does not name for "${action}"`,
        { path, action });
    }
  }
  return answer;
}

// ---------------------------------------------------------------------------
// The ordered-check engine. One shape, used by every ladder in this file, so
// "the first check that does not pass refuses and the rest are not_reached" is
// one implementation rather than four that could drift.
// ---------------------------------------------------------------------------

const SATISFIED = detail => ({ state: "satisfied", detail: detail ?? null });
const VIOLATED = (reason_id, detail) => ({ state: "violated", reason_id, detail: detail ?? null });
const UNOBSERVABLE = (reason_id, detail) => ({ state: "unobservable", reason_id, detail: detail ?? null });

function runCheckLadder(checks, runners) {
  const check_states = {};
  for (const name of checks) check_states[name] = { state: "not_reached" };
  let blocking_check = null;
  let reason_id = null;
  for (const name of checks) {
    const outcome = runners[name]();
    check_states[name] = { state: outcome.state, ...(outcome.detail ?? {}) };
    if (outcome.state !== "satisfied") {
      blocking_check = name;
      reason_id = outcome.reason_id;
      break;
    }
  }
  return { check_states, blocking_check, reason_id };
}

/** Every answer this module returns says what it did NOT grant. */
function answerBase(extra) {
  return {
    schema_version: V5_EFFECT_ENVELOPE_SCHEMA_VERSION,
    policy_version: V5_EFFECT_ENVELOPE_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    ...extra,
    outward_effect_granted: false,
    attended_activation_receipt: V5_ATTENDED_ACTIVATION_RECEIPT_STEP,
    attended_activation_receipt_present: false,
    effects: V5_NO_EFFECTS,
  };
}

/** The four principal comparisons, in role order, as one reusable ladder step. */
function comparePrincipals(bound, presented, reasonMap) {
  for (const role of V5_ENVELOPE_PRINCIPAL_ROLES) {
    const field = PRINCIPAL_FIELD[role];
    if (bound[field] !== presented[field]) {
      return VIOLATED(reasonMap[role], {
        role, field, bound: bound[field], presented: presented[field],
        compared_roles: [...V5_ENVELOPE_PRINCIPAL_ROLES],
      });
    }
  }
  return SATISFIED({ compared_roles: [...V5_ENVELOPE_PRINCIPAL_ROLES] });
}

// ---------------------------------------------------------------------------
// Issuance. The point where the four principals are BOUND and the point that
// states, in the record, that binding them grants nothing outward.
// ---------------------------------------------------------------------------

const ISSUANCE_REQUEST_KEYS = Object.freeze(["authority", "capability", "envelope", "now"]);

export function evaluateCapabilityIssuance(request) {
  const raw = assertObject(request, "request");
  assertClosedKeys(raw, ISSUANCE_REQUEST_KEYS, "request");
  assertRequiredKeys(raw, ["envelope", "capability", "now"], "request");
  const envelope = assertNormalizedEnvelope(raw.envelope, "request.envelope");
  const capability = assertNormalizedCapability(raw.capability, "request.capability");
  const now = assertInstant(raw.now, "request.now");
  const authority = raw.authority === undefined || raw.authority === null
    ? null
    : assertAuthorityAnswer(raw.authority, envelope.action, envelope.principals.actor_slug,
      "request.authority");

  const ladder = runCheckLadder(V5_ISSUANCE_CHECKS, {
    actor_authority: () => {
      if (authority === null) {
        return UNOBSERVABLE("issuance_authority_unobservable", { authority_decision: null });
      }
      if (authority.decision !== "allow") {
        return VIOLATED("issuance_authority_refused", {
          authority_decision: authority.decision, authority_reason_id: authority.reason_id,
        });
      }
      return SATISFIED({ authority_decision: "allow", authority_reason_id: authority.reason_id });
    },
    envelope_binding: () => capability.envelope_digest === envelope.envelope_digest
      ? SATISFIED({ envelope_digest: envelope.envelope_digest })
      : VIOLATED("issuance_envelope_binding_mismatch", {
        bound: capability.envelope_digest, envelope_digest: envelope.envelope_digest,
      }),
    capability_scope: () => capability.capability === envelope.required_capability
      ? SATISFIED({ capability: capability.capability })
      : VIOLATED("issuance_capability_scope_mismatch", {
        required_capability: envelope.required_capability, issued_capability: capability.capability,
      }),
    principal_binding: () =>
      comparePrincipals(envelope.principals, capability.principals, PRINCIPAL_ISSUANCE_MISMATCH_REASON),
    autonomy_tier: () => capability.autonomy_tier_label === envelope.autonomy_tier_label
      ? SATISFIED({ autonomy_tier_label: capability.autonomy_tier_label })
      : VIOLATED("issuance_autonomy_tier_mismatch", {
        envelope_tier: envelope.autonomy_tier_label, capability_tier: capability.autonomy_tier_label,
        ladder_seam: V5_F06_AUTONOMY_TIER_LADDER_SEAM,
      }),
    validity_window: () => {
      const issued = Date.parse(capability.issued_at);
      const expires = Date.parse(capability.expires_at);
      if (now < issued) {
        return VIOLATED("issuance_window_not_yet_open", {
          now: raw.now, issued_at: capability.issued_at,
        });
      }
      if (now >= expires) {
        return VIOLATED("issuance_window_already_expired", {
          now: raw.now, expires_at: capability.expires_at,
        });
      }
      return SATISFIED({ issued_at: capability.issued_at, expires_at: capability.expires_at });
    },
  });

  return deepFreeze(answerBase({
    decision: ladder.blocking_check === null ? "allow" : "refuse",
    reason_id: ladder.reason_id ?? "issuance_admissible_internal_only",
    blocking_check: ladder.blocking_check,
    checks_required: [...V5_ISSUANCE_CHECKS],
    check_states: ladder.check_states,
    envelope_digest: envelope.envelope_digest,
    capability_id: capability.capability_id,
    idempotency_key: envelope.envelope_digest,
    idempotency_key_source: V5_IDEMPOTENCY_KEY_SOURCE,
    uses_allowed: V5_CAPABILITY_USES_ALLOWED,
  }));
}

// ---------------------------------------------------------------------------
// Presentation — `checkable_done` item 1.
// ---------------------------------------------------------------------------

/**
 * Read one presentation of a capability, or refuse to read it at all.
 *
 * `nonce_state` and `capability_state` are the caller's REPORTS, using
 * V5-F07's vocabularies unchanged. Neither has a default: a presentation that
 * does not state whether the nonce was already spent is not the same as one that
 * says it was not, and the evaluator treats the first as unobservable.
 */
export function normalizeEffectPresentation(request) {
  const raw = assertObject(request, "presentation");
  assertClosedKeys(raw, PRESENTATION_KEYS, "presentation");
  assertRequiredKeys(raw,
    ["capability_id", "envelope_digest", "payload_digest", "presentation_id",
      "presented_autonomy_tier_label", "presented_principals", "requested_action"],
    "presentation");
  return deepFreeze({
    schema_version: V5_EFFECT_ENVELOPE_SCHEMA_VERSION,
    report_kind: "workflow-effect-presentation.v1",
    presentation_id: assertStableId(raw.presentation_id, "presentation.presentation_id"),
    envelope_digest: assertDigestRef(raw.envelope_digest, "presentation.envelope_digest"),
    capability_id: assertOpaqueHandle(raw.capability_id, "presentation.capability_id"),
    requested_action: assertEnum(raw.requested_action, V5_ACTION_KEYS,
      "presentation.requested_action", "unknown_action"),
    payload_digest: assertDigestRef(raw.payload_digest, "presentation.payload_digest"),
    presented_principals: normalizePrincipals(raw.presented_principals, "presentation.presented_principals"),
    presented_autonomy_tier_label: assertStableId(raw.presented_autonomy_tier_label,
      "presentation.presented_autonomy_tier_label"),
    nonce_state: raw.nonce_state === undefined || raw.nonce_state === null
      ? null
      : assertEnum(raw.nonce_state, V5_NONCE_STATES, "presentation.nonce_state", "unknown_nonce_state"),
    capability_state: raw.capability_state === undefined || raw.capability_state === null
      ? null
      : assertEnum(raw.capability_state, V5_CAPABILITY_STATES, "presentation.capability_state",
        "unknown_capability_state"),
    cap_observations: normalizeCapObservations(raw.cap_observations, "presentation.cap_observations"),
  });
}

function assertNormalizedPresentation(value, path) {
  const presentation = assertObject(value, path);
  assertClosedKeys(presentation, NORMALIZED_PRESENTATION_KEYS, path);
  assertRequiredKeys(presentation, NORMALIZED_PRESENTATION_KEYS, path);
  if (presentation.report_kind !== "workflow-effect-presentation.v1" ||
      presentation.schema_version !== V5_EFFECT_ENVELOPE_SCHEMA_VERSION) {
    fail("unnormalized_report", `${path} is not a normalized v5 effect presentation`, { path });
  }
  return presentation;
}

const PRESENTATION_REQUEST_KEYS = Object.freeze([
  "authority", "capability", "envelope", "now", "presentation",
]);

/**
 * Decide whether one presented capability may be CONSUMED for one exact effect.
 *
 * An allow says the nine registered negatives did not fire on the facts as
 * reported. It does not authorize a provider call: the ordering rule in
 * evaluateConsumptionOrder still has to see the consumption commit first, and
 * the attended activation receipt still does not exist.
 */
export function evaluateCapabilityPresentation(request) {
  const raw = assertObject(request, "request");
  assertClosedKeys(raw, PRESENTATION_REQUEST_KEYS, "request");
  assertRequiredKeys(raw, ["envelope", "capability", "presentation", "now"], "request");
  const envelope = assertNormalizedEnvelope(raw.envelope, "request.envelope");
  const capability = assertNormalizedCapability(raw.capability, "request.capability");
  const presentation = assertNormalizedPresentation(raw.presentation, "request.presentation");
  const now = assertInstant(raw.now, "request.now");
  const authority = raw.authority === undefined || raw.authority === null
    ? null
    : assertAuthorityAnswer(raw.authority, envelope.action, envelope.principals.actor_slug,
      "request.authority");

  const ladder = runCheckLadder(V5_PRESENTATION_CHECKS, {
    actor_authority: () => {
      if (authority === null) {
        return UNOBSERVABLE("actor_authority_unobservable", { authority_decision: null });
      }
      if (authority.decision !== "allow") {
        return VIOLATED("actor_authority_refused", {
          authority_decision: authority.decision, authority_reason_id: authority.reason_id,
        });
      }
      return SATISFIED({ authority_decision: "allow", authority_reason_id: authority.reason_id });
    },

    // SUBSTITUTION. Three things must agree before anything else is read: the
    // capability must be for THIS envelope, the presentation must be about THIS
    // envelope, and the presented capability must be THIS capability.
    capability_binding: () => {
      if (capability.envelope_digest !== envelope.envelope_digest ||
          presentation.envelope_digest !== envelope.envelope_digest) {
        return VIOLATED("envelope_substituted", {
          envelope_digest: envelope.envelope_digest,
          capability_bound_to: capability.envelope_digest,
          presented_for: presentation.envelope_digest,
        });
      }
      if (presentation.capability_id !== capability.capability_id) {
        return VIOLATED("capability_substituted", {
          envelope_digest: envelope.envelope_digest,
          capability_id: capability.capability_id,
          presented_capability_id: presentation.capability_id,
        });
      }
      // A matching digest STRING is not a matching binding. A capability whose
      // own sealed fields disagree with the envelope it names was never issued
      // for that envelope, however its `envelope_digest` field is spelled — and
      // without this the whole principal ladder below could be bypassed by
      // pointing a stale capability at a newer envelope.
      if (capability.capability !== envelope.required_capability ||
          capability.autonomy_tier_label !== envelope.autonomy_tier_label ||
          canonicalJson(capability.principals) !== canonicalJson(envelope.principals)) {
        return VIOLATED("capability_not_issued_for_envelope", {
          envelope_digest: envelope.envelope_digest,
          required_capability: envelope.required_capability,
          issued_capability: capability.capability,
        });
      }
      return SATISFIED({ envelope_digest: envelope.envelope_digest });
    },

    // EXPIRY. The reported state is carried but never outvotes the clock.
    capability_validity_window: () => {
      const reported = presentation.capability_state;
      if (reported === "revoked") return VIOLATED("capability_revoked", { capability_state: reported });
      if (reported === "unissued") return VIOLATED("capability_unissued", { capability_state: reported });
      const issued = Date.parse(capability.issued_at);
      const expires = Date.parse(capability.expires_at);
      if (now >= expires) {
        return VIOLATED("capability_expired", {
          capability_state: reported, now: raw.now, expires_at: capability.expires_at,
          label_outvoted_by_clock: reported === "active",
        });
      }
      if (now < issued) {
        return VIOLATED("capability_not_yet_valid", {
          capability_state: reported, now: raw.now, issued_at: capability.issued_at,
        });
      }
      if (reported === "expired") {
        return VIOLATED("capability_expired", {
          capability_state: reported, now: raw.now, expires_at: capability.expires_at,
          label_outvoted_by_clock: false,
        });
      }
      if (reported === null) {
        return UNOBSERVABLE("capability_state_unobservable", { capability_state: null, now: raw.now });
      }
      return SATISFIED({ capability_state: reported, expires_at: capability.expires_at });
    },

    // REPLAY. A consumed nonce is a replay; an unknown one is indistinguishable
    // from a replay and therefore refuses too.
    nonce_state: () => {
      if (presentation.nonce_state === "consumed") {
        return VIOLATED("capability_replayed", {
          nonce_state: "consumed", uses_allowed: V5_CAPABILITY_USES_ALLOWED,
        });
      }
      if (presentation.nonce_state === "unconsumed") {
        return SATISFIED({ nonce_state: "unconsumed", uses_allowed: V5_CAPABILITY_USES_ALLOWED });
      }
      return UNOBSERVABLE("nonce_state_unobservable", {
        nonce_state: presentation.nonce_state, uses_allowed: V5_CAPABILITY_USES_ALLOWED,
      });
    },

    // WRONG ACCOUNT, and the other three, each named separately.
    principal_binding: () =>
      comparePrincipals(capability.principals, presentation.presented_principals,
        PRINCIPAL_MISMATCH_REASON),

    autonomy_tier: () =>
      presentation.presented_autonomy_tier_label === capability.autonomy_tier_label
        ? SATISFIED({ autonomy_tier_label: capability.autonomy_tier_label })
        : VIOLATED("autonomy_tier_changed_after_issuance", {
          issued_tier: capability.autonomy_tier_label,
          presented_tier: presentation.presented_autonomy_tier_label,
          ladder_seam: V5_F06_AUTONOMY_TIER_LADDER_SEAM,
        }),

    action_binding: () => presentation.requested_action === envelope.action
      ? SATISFIED({ action: envelope.action })
      : VIOLATED("action_substituted", {
        envelope_action: envelope.action, requested_action: presentation.requested_action,
      }),

    payload_binding: () => presentation.payload_digest === envelope.payload_digest
      ? SATISFIED({ payload_digest: envelope.payload_digest })
      : VIOLATED("payload_substituted", {
        envelope_payload_digest: envelope.payload_digest,
        presented_payload_digest: presentation.payload_digest,
      }),

    // CAPS. Silence is not headroom, and an undeclared cap is not a cap.
    effect_caps: () => {
      const declared = new Map(envelope.caps.map(cap => [cap.cap_key, cap.limit]));
      const observed = new Map(presentation.cap_observations.map(cap => [cap.cap_key, cap.observed]));
      for (const [cap_key, limit] of declared) {
        if (!observed.has(cap_key)) {
          return UNOBSERVABLE("cap_unobserved", {
            cap_key, limit, vocabulary_seam: V5_F06_EFFECT_CAP_VOCABULARY_SEAM,
          });
        }
        if (observed.get(cap_key) > limit) {
          return VIOLATED("cap_exceeded", {
            cap_key, limit, observed: observed.get(cap_key),
            vocabulary_seam: V5_F06_EFFECT_CAP_VOCABULARY_SEAM,
          });
        }
      }
      for (const cap_key of observed.keys()) {
        if (!declared.has(cap_key)) {
          return VIOLATED("undeclared_cap_observed", {
            cap_key, observed: observed.get(cap_key),
            vocabulary_seam: V5_F06_EFFECT_CAP_VOCABULARY_SEAM,
          });
        }
      }
      return SATISFIED({ declared_caps: envelope.caps.map(cap => cap.cap_key) });
    },
  });

  return deepFreeze(answerBase({
    decision: ladder.blocking_check === null ? "allow" : "refuse",
    reason_id: ladder.reason_id ?? "presentation_admissible_for_consumption",
    blocking_check: ladder.blocking_check,
    checks_required: [...V5_PRESENTATION_CHECKS],
    checks_before_any_binding_is_compared: [...V5_CHECKS_BEFORE_ANY_BINDING_IS_COMPARED],
    check_states: ladder.check_states,
    envelope_digest: envelope.envelope_digest,
    capability_id: capability.capability_id,
    presentation_id: presentation.presentation_id,
    idempotency_key: envelope.envelope_digest,
    idempotency_key_source: V5_IDEMPOTENCY_KEY_SOURCE,
    consumption_must_commit_before_provider_call: true,
  }));
}

// ---------------------------------------------------------------------------
// The attempt observation, the ordering proof, and the quarantine.
// ---------------------------------------------------------------------------

/**
 * Read one attempt observation, or refuse to read it at all.
 *
 * The two sequence numbers are the whole of `checkable_done` item 2. They are a
 * MONOTONIC ORDERING the caller reports, not wall-clock times: two timestamps
 * from two machines cannot prove an ordering, and a rule that depends on clock
 * agreement between the workflow runtime and the connector gateway is a rule
 * that fails silently on skew.
 */
export function normalizeEffectAttemptObservation(request) {
  const raw = assertObject(request, "attempt");
  assertClosedKeys(raw, ATTEMPT_KEYS, "attempt");
  assertRequiredKeys(raw, ["attempt_id", "capability_id", "envelope_digest"], "attempt");

  const consumptionRaw = raw.consumption === undefined || raw.consumption === null
    ? null : assertObject(raw.consumption, "attempt.consumption");
  if (consumptionRaw) {
    assertClosedKeys(consumptionRaw, CONSUMPTION_KEYS, "attempt.consumption");
    assertRequiredKeys(consumptionRaw, ["state"], "attempt.consumption");
  }
  const providerRaw = raw.provider_call === undefined || raw.provider_call === null
    ? null : assertObject(raw.provider_call, "attempt.provider_call");
  if (providerRaw) {
    assertClosedKeys(providerRaw, PROVIDER_CALL_KEYS, "attempt.provider_call");
    assertRequiredKeys(providerRaw, ["state"], "attempt.provider_call");
  }
  const outcomeRaw = raw.outcome === undefined || raw.outcome === null
    ? null : assertObject(raw.outcome, "attempt.outcome");
  if (outcomeRaw) {
    assertClosedKeys(outcomeRaw, OUTCOME_KEYS, "attempt.outcome");
    assertRequiredKeys(outcomeRaw, ["state"], "attempt.outcome");
  }
  const readbackRaw = raw.readback === undefined || raw.readback === null
    ? null : assertObject(raw.readback, "attempt.readback");
  if (readbackRaw) {
    assertClosedKeys(readbackRaw, READBACK_KEYS, "attempt.readback");
    assertRequiredKeys(readbackRaw, ["state"], "attempt.readback");
  }

  return deepFreeze({
    schema_version: V5_EFFECT_ENVELOPE_SCHEMA_VERSION,
    report_kind: "workflow-effect-attempt.v1",
    attempt_id: assertStableId(raw.attempt_id, "attempt.attempt_id"),
    envelope_digest: assertDigestRef(raw.envelope_digest, "attempt.envelope_digest"),
    capability_id: assertOpaqueHandle(raw.capability_id, "attempt.capability_id"),
    consumption: {
      state: consumptionRaw
        ? assertEnum(consumptionRaw.state, V5_CONSUMPTION_STATES, "attempt.consumption.state",
          "unknown_consumption_state")
        : "unstated",
      committed_seq: consumptionRaw
        ? optionalSafeCount(consumptionRaw.committed_seq, "attempt.consumption.committed_seq")
        : null,
    },
    provider_call: {
      state: providerRaw
        ? assertEnum(providerRaw.state, V5_PROVIDER_CALL_STATES, "attempt.provider_call.state",
          "unknown_provider_call_state")
        : "unstated",
      started_seq: providerRaw
        ? optionalSafeCount(providerRaw.started_seq, "attempt.provider_call.started_seq")
        : null,
    },
    outcome: {
      state: outcomeRaw
        ? assertEnum(outcomeRaw.state, V5_ATTEMPT_OUTCOME_STATES, "attempt.outcome.state",
          "unknown_outcome_state")
        : "unstated",
    },
    readback: readbackRaw === null ? null : {
      state: assertEnum(readbackRaw.state, V5_READBACK_STATES, "attempt.readback.state",
        "unknown_readback_state"),
      join_key: readbackRaw.join_key === undefined || readbackRaw.join_key === null
        ? null : assertDigestRef(readbackRaw.join_key, "attempt.readback.join_key"),
    },
  });
}

function assertNormalizedAttempt(value, path) {
  const attempt = assertObject(value, path);
  assertClosedKeys(attempt, NORMALIZED_ATTEMPT_KEYS, path);
  assertRequiredKeys(attempt, NORMALIZED_ATTEMPT_KEYS, path);
  if (attempt.report_kind !== "workflow-effect-attempt.v1" ||
      attempt.schema_version !== V5_EFFECT_ENVELOPE_SCHEMA_VERSION) {
    fail("unnormalized_report", `${path} is not a normalized v5 effect attempt`, { path });
  }
  return attempt;
}

const ORDER_REQUEST_KEYS = Object.freeze(["attempt", "capability", "envelope"]);

/**
 * `checkable_done` item 2 — consumption commits BEFORE the provider call.
 *
 * The proof is `committed_seq < started_seq` on the SAME attempt. Anything less
 * than that proof refuses: a missing sequence number, an uncommitted or failed
 * consumption, an unstated provider-call state, or a provider call that started
 * at or before the commit. "The consumption probably committed first" is not one
 * of the answers this function can give.
 */
export function evaluateConsumptionOrder(request) {
  const raw = assertObject(request, "request");
  assertClosedKeys(raw, ORDER_REQUEST_KEYS, "request");
  assertRequiredKeys(raw, ["envelope", "capability", "attempt"], "request");
  const envelope = assertNormalizedEnvelope(raw.envelope, "request.envelope");
  const capability = assertNormalizedCapability(raw.capability, "request.capability");
  const attempt = assertNormalizedAttempt(raw.attempt, "request.attempt");

  let allowReason = "consumption_committed_before_provider_call";

  const ladder = runCheckLadder(V5_ORDER_CHECKS, {
    attempt_binding: () => {
      if (attempt.envelope_digest !== envelope.envelope_digest) {
        return VIOLATED("attempt_bound_to_other_envelope", {
          envelope_digest: envelope.envelope_digest, attempt_envelope_digest: attempt.envelope_digest,
        });
      }
      if (attempt.capability_id !== capability.capability_id) {
        return VIOLATED("attempt_bound_to_other_capability", {
          capability_id: capability.capability_id, attempt_capability_id: attempt.capability_id,
        });
      }
      return SATISFIED({ envelope_digest: envelope.envelope_digest });
    },

    consumption_commit: () => {
      const { state, committed_seq } = attempt.consumption;
      if (state === "uncommitted") return VIOLATED("consumption_uncommitted", { consumption_state: state });
      if (state === "failed") return VIOLATED("consumption_failed", { consumption_state: state });
      if (state === "unstated") {
        return UNOBSERVABLE("consumption_state_unobservable", { consumption_state: state });
      }
      if (committed_seq === null) {
        return UNOBSERVABLE("consumption_sequence_unobservable", {
          consumption_state: state, committed_seq: null,
        });
      }
      return SATISFIED({ consumption_state: state, committed_seq });
    },

    provider_call_order: () => {
      const { state, started_seq } = attempt.provider_call;
      const committed_seq = attempt.consumption.committed_seq;
      if (state === "unstated") {
        return UNOBSERVABLE("provider_call_state_unobservable", { provider_call_state: state });
      }
      if (state === "not_started") {
        allowReason = "consumption_committed_provider_call_not_started";
        return SATISFIED({ provider_call_state: state, committed_seq, started_seq: null });
      }
      if (started_seq === null) {
        return UNOBSERVABLE("provider_call_sequence_unobservable", {
          provider_call_state: state, started_seq: null,
        });
      }
      if (started_seq <= committed_seq) {
        return VIOLATED("provider_call_preceded_consumption", {
          provider_call_state: state, committed_seq, started_seq,
        });
      }
      return SATISFIED({ provider_call_state: state, committed_seq, started_seq });
    },
  });

  return deepFreeze(answerBase({
    answer_kind: "workflow-effect-consumption-order.v1",
    decision: ladder.blocking_check === null ? "allow" : "refuse",
    reason_id: ladder.reason_id ?? allowReason,
    blocking_check: ladder.blocking_check,
    checks_required: [...V5_ORDER_CHECKS],
    check_states: ladder.check_states,
    envelope_digest: envelope.envelope_digest,
    capability_id: capability.capability_id,
    attempt_id: attempt.attempt_id,
    idempotency_key: envelope.envelope_digest,
    idempotency_key_source: V5_IDEMPOTENCY_KEY_SOURCE,
  }));
}

function assertOrderAnswer(value, attempt, path) {
  const answer = assertObject(value, path);
  if (!Object.isFrozen(answer) || answer.answer_kind !== "workflow-effect-consumption-order.v1" ||
      answer.schema_version !== V5_EFFECT_ENVELOPE_SCHEMA_VERSION) {
    fail("foreign_order_answer",
      `${path} is not this module's own consumption-order answer`, { path });
  }
  if (answer.attempt_id !== attempt.attempt_id ||
      answer.envelope_digest !== attempt.envelope_digest ||
      answer.capability_id !== attempt.capability_id) {
    fail("foreign_order_answer",
      `${path} answers a different attempt than the one supplied`,
      { path, answered: answer.attempt_id, supplied: attempt.attempt_id });
  }
  return answer;
}

const RESOLUTION_REQUEST_KEYS = Object.freeze([
  "attempt", "capability", "consumption_order", "envelope", "proposed_next_step", "proposed_retry",
]);
const PROPOSED_RETRY_KEYS = Object.freeze(["capability_id", "nonce"]);

/**
 * `checkable_done` item 3 — a timeout enters `unknown`, and a readback precedes
 * any retry.
 *
 * THE QUARANTINE. `timed_out` and `unstated` both resolve to `unknown`, because
 * both mean the same thing: the caller does not know what the provider did.
 * While an attempt is `unknown` the ONLY admissible next step is a provider
 * readback. A retry refuses, and settling refuses — an unknown outcome that can
 * be settled is a quarantine with a hole in it.
 *
 * THE EXITS. A readback that finds the effect present resolves the attempt to
 * success and a retry then refuses as a duplicate. A readback that finds the
 * effect absent resolves it to failure and a retry becomes admissible — but only
 * with a FRESH capability, because the nonce of the one that was consumed is
 * spent and reusing it is the replay this module refuses at presentation. A
 * readback that is indeterminate keeps the quarantine shut.
 *
 * THE CONFLICT. A reported outcome and a readback that disagree — "it succeeded"
 * against "the effect is not there" — resolve to `unknown`, not to whichever was
 * read last. Two disagreeing observations are less evidence than one, never more.
 */
export function evaluateAttemptResolution(request) {
  const raw = assertObject(request, "request");
  assertClosedKeys(raw, RESOLUTION_REQUEST_KEYS, "request");
  assertRequiredKeys(raw,
    ["envelope", "capability", "attempt", "consumption_order", "proposed_next_step"], "request");
  const envelope = assertNormalizedEnvelope(raw.envelope, "request.envelope");
  const capability = assertNormalizedCapability(raw.capability, "request.capability");
  const attempt = assertNormalizedAttempt(raw.attempt, "request.attempt");
  const order = assertOrderAnswer(raw.consumption_order, attempt, "request.consumption_order");
  const proposed_next_step = assertEnum(raw.proposed_next_step, V5_NEXT_STEPS,
    "request.proposed_next_step", "unknown_next_step");
  let proposedRetry = null;
  if (raw.proposed_retry !== undefined && raw.proposed_retry !== null) {
    const retry = assertObject(raw.proposed_retry, "request.proposed_retry");
    assertClosedKeys(retry, PROPOSED_RETRY_KEYS, "request.proposed_retry");
    assertRequiredKeys(retry, PROPOSED_RETRY_KEYS, "request.proposed_retry");
    proposedRetry = {
      capability_id: assertOpaqueHandle(retry.capability_id, "request.proposed_retry.capability_id"),
      nonce: assertOpaqueHandle(retry.nonce, "request.proposed_retry.nonce"),
    };
  }

  const reportedOutcome = attempt.outcome.state;
  const readbackState = attempt.readback === null ? null : attempt.readback.state;
  const outcomeResolution = V5_OUTCOME_RESOLUTIONS[reportedOutcome];
  const readbackResolution = readbackState === null ? null : V5_READBACK_RESOLUTIONS[readbackState];

  let resolution = outcomeResolution;
  let conflicted = false;
  if (readbackResolution !== null && readbackResolution !== "unknown") {
    if (outcomeResolution === "unknown") {
      resolution = readbackResolution;
    } else if (outcomeResolution !== readbackResolution) {
      // Two observations that disagree are less evidence than one.
      resolution = "unknown";
      conflicted = true;
    }
  }
  let allowReason = "settlement_admissible";

  const ladder = runCheckLadder(V5_RESOLUTION_CHECKS, {
    consumption_order: () => order.decision === "allow"
      ? SATISFIED({ order_reason_id: order.reason_id })
      : VIOLATED("consumption_order_refused", {
        order_reason_id: order.reason_id, order_blocking_check: order.blocking_check,
      }),

    // A readback about a DIFFERENT effect is not a readback about this one.
    readback_join: () => {
      if (attempt.readback === null) return SATISFIED({ readback_state: null });
      if (attempt.readback.join_key === null) {
        return UNOBSERVABLE("readback_joined_on_other_effect", {
          readback_state: readbackState, join_key: null,
          idempotency_key: envelope.envelope_digest,
          provider_join_seam: V5_F06_PROVIDER_READBACK_JOIN_SEAM,
        });
      }
      if (attempt.readback.join_key !== envelope.envelope_digest) {
        return VIOLATED("readback_joined_on_other_effect", {
          readback_state: readbackState, join_key: attempt.readback.join_key,
          idempotency_key: envelope.envelope_digest,
          provider_join_seam: V5_F06_PROVIDER_READBACK_JOIN_SEAM,
        });
      }
      return SATISFIED({ readback_state: readbackState, join_key: attempt.readback.join_key });
    },

    outcome_resolution: () => conflicted
      ? VIOLATED("outcome_readback_conflict", {
        reported_outcome: reportedOutcome, readback_state: readbackState, resolution: "unknown",
      })
      : SATISFIED({ reported_outcome: reportedOutcome, readback_state: readbackState, resolution }),

    next_step: () => {
      if (proposed_next_step === "provider_readback") {
        allowReason = resolution === "unknown" ? "readback_required_before_retry" : "readback_admissible";
        return SATISFIED({ proposed_next_step, resolution });
      }
      if (proposed_next_step === "settle") {
        if (resolution === "unknown") {
          return VIOLATED("unknown_outcome_may_not_settle", { proposed_next_step, resolution });
        }
        allowReason = "settlement_admissible";
        return SATISFIED({ proposed_next_step, resolution });
      }
      // proposed_next_step === "retry"
      if (resolution === "unknown") {
        if (readbackState !== null && readbackResolution === "unknown") {
          return VIOLATED("readback_indeterminate", {
            proposed_next_step, resolution, readback_state: readbackState,
          });
        }
        return VIOLATED("retry_without_readback", {
          proposed_next_step, resolution, readback_state: readbackState,
        });
      }
      if (resolution === "confirmed_success") {
        return VIOLATED(readbackState === "effect_present" ? "effect_already_present"
          : "outcome_already_confirmed", { proposed_next_step, resolution, readback_state: readbackState });
      }
      // confirmed_failure — a retry is admissible, on a FRESH capability only.
      if (proposedRetry === null) {
        return UNOBSERVABLE("retry_capability_unstated", { proposed_next_step, resolution });
      }
      if (proposedRetry.capability_id === capability.capability_id ||
          proposedRetry.nonce === capability.nonce) {
        return VIOLATED("retry_reuses_consumed_capability", {
          proposed_next_step, resolution,
          consumed_capability_id: capability.capability_id,
          proposed_capability_id: proposedRetry.capability_id,
        });
      }
      allowReason = "retry_admissible_with_fresh_capability";
      return SATISFIED({
        proposed_next_step, resolution, proposed_capability_id: proposedRetry.capability_id,
      });
    },
  });

  const quarantined = resolution === "unknown";
  return deepFreeze(answerBase({
    answer_kind: "workflow-effect-attempt-resolution.v1",
    decision: ladder.blocking_check === null ? "allow" : "refuse",
    reason_id: ladder.reason_id ?? allowReason,
    blocking_check: ladder.blocking_check,
    checks_required: [...V5_RESOLUTION_CHECKS],
    check_states: ladder.check_states,
    envelope_digest: envelope.envelope_digest,
    capability_id: capability.capability_id,
    attempt_id: attempt.attempt_id,
    reported_outcome: reportedOutcome,
    readback_state: readbackState,
    resolution,
    quarantined,
    required_next_step: quarantined ? "provider_readback" : null,
    proposed_next_step,
    idempotency_key: envelope.envelope_digest,
    idempotency_key_source: V5_IDEMPOTENCY_KEY_SOURCE,
  }));
}

// ---------------------------------------------------------------------------
// The closed policy, its digest, and the zero-effect projection.
//
// The digest exists so a consumer can pin the exact policy it read. Computing it
// accepts nothing, activates nothing and is not evidence for any consumer gate.
// ---------------------------------------------------------------------------

export function v5EffectEnvelopePolicyPreimage() {
  return {
    schema_version: V5_EFFECT_ENVELOPE_SCHEMA_VERSION,
    policy_version: V5_EFFECT_ENVELOPE_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    envelope_kind: V5_EFFECT_ENVELOPE_KIND,
    capability_kind: V5_EFFECT_CAPABILITY_KIND,

    // Item 4. Order is decision order, so this list is NOT sorted.
    principal_roles: [...V5_ENVELOPE_PRINCIPAL_ROLES],
    principal_fields: V5_ENVELOPE_PRINCIPAL_ROLES.map(role => ({
      role, field: PRINCIPAL_FIELD[role], mismatch_reason_id: PRINCIPAL_MISMATCH_REASON[role],
    })),
    principals_are_positionally_sealed: true,

    // The ladders. Each is an ORDER, not a set.
    issuance_checks: [...V5_ISSUANCE_CHECKS],
    presentation_checks: [...V5_PRESENTATION_CHECKS],
    checks_before_any_binding_is_compared: [...V5_CHECKS_BEFORE_ANY_BINDING_IS_COMPARED],
    order_checks: [...V5_ORDER_CHECKS],
    resolution_checks: [...V5_RESOLUTION_CHECKS],

    // The vocabularies. Sorted, because these are sets.
    check_states: [...V5_ADMISSION_CHECK_STATES].sort(),
    capability_states: [...V5_CAPABILITY_STATES].sort(),
    nonce_states: [...V5_NONCE_STATES].sort(),
    consumption_states: [...V5_CONSUMPTION_STATES].sort(),
    provider_call_states: [...V5_PROVIDER_CALL_STATES].sort(),
    attempt_outcome_states: [...V5_ATTEMPT_OUTCOME_STATES].sort(),
    readback_states: [...V5_READBACK_STATES].sort(),
    attempt_resolutions: [...V5_ATTEMPT_RESOLUTIONS].sort(),
    next_steps: [...V5_NEXT_STEPS].sort(),
    reason_ids: [...V5_EFFECT_ENVELOPE_REASON_IDS].sort(),

    // The two maps that decide item 3, as data rather than as inline branches.
    outcome_resolutions: Object.keys(V5_OUTCOME_RESOLUTIONS).sort()
      .map(outcome => ({ outcome, resolution: V5_OUTCOME_RESOLUTIONS[outcome] })),
    readback_resolutions: Object.keys(V5_READBACK_RESOLUTIONS).sort()
      .map(readback => ({ readback, resolution: V5_READBACK_RESOLUTIONS[readback] })),
    readback_precedes_retry: true,
    unknown_outcome_may_settle: false,

    // Item 2, and the idempotency that makes it meaningful.
    consumption_must_commit_before_provider_call: true,
    idempotency_key_source: V5_IDEMPOTENCY_KEY_SOURCE,
    capability_uses_allowed: V5_CAPABILITY_USES_ALLOWED,
    capability_window_required: true,

    // The action registry is read, not restated: only its shape is pinned here,
    // so a registry change moves this digest and stale readers are refused.
    capability_issuable_actions: V5_ACTION_KEYS
      .filter(action => typeof V5_ACTIONS[action].required_capability === "string")
      .map(action => ({
        action,
        required_capability: V5_ACTIONS[action].required_capability,
        required_controls: [...(V5_ACTIONS[action].required_controls ?? [])].sort(),
      })),

    // The data boundary, stated as policy rather than left to the reader.
    carries_credential_material: false,
    carries_payload_content: false,
    capability_handles_are_opaque: true,

    // What is NOT decided here, named so nobody reads an allow as covering it.
    decision_ids: [...V5_F06_DECISION_IDS].sort(),
    decision_binding: "unbound",
    seams: [...V5_F06_SEAMS],
    grants_outward_effect: false,
    attended_activation_receipt: V5_ATTENDED_ACTIVATION_RECEIPT_STEP,
    attended_activation_receipt_present: false,
  };
}

/** The deterministic `sha256:` digest of the closed v5 effect-envelope policy. */
export function v5EffectEnvelopePolicyDigest() {
  return digest(v5EffectEnvelopePolicyPreimage());
}

/** The exact canonical bytes hashed, so a reviewer can check the digest by hand. */
export function v5EffectEnvelopePolicyCanonicalBytes() {
  return canonicalJson(v5EffectEnvelopePolicyPreimage());
}

/**
 * The zero-effect projection: what is settled, what the policy hashes to, and
 * the explicit statement that reading it accepts and activates nothing.
 */
export function v5EffectEnvelopeProjection(options = {}) {
  assertObject(options, "options");
  assertClosedKeys(options, ["expected_policy_digest"], "options");
  const policyDigest = v5EffectEnvelopePolicyDigest();
  if (options.expected_policy_digest !== undefined) {
    if (typeof options.expected_policy_digest !== "string" ||
        !SHA256_REF.test(options.expected_policy_digest)) {
      fail("invalid_expected_digest", "options.expected_policy_digest must be a sha256: reference",
        { path: "options.expected_policy_digest" });
    }
    if (options.expected_policy_digest !== policyDigest) {
      fail("stale_expected_digest",
        "the policy no longer hashes to the expected digest; re-read it rather than acting on the stale one",
        { expected: options.expected_policy_digest, actual: policyDigest });
    }
  }
  return deepFreeze({
    schema_version: V5_EFFECT_ENVELOPE_SCHEMA_VERSION,
    policy_version: V5_EFFECT_ENVELOPE_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    policy_digest: policyDigest,
    decision_ids: [...V5_F06_DECISION_IDS],
    decision_binding: "unbound",
    unimplemented_dependencies: [
      `settled decision text and source-evidence digests for ${V5_F06_DECISION_IDS.join(", ")}: doctrine store (${V5_F06_DECISION_BINDING_SEAM})`,
      `autonomy tier vocabulary and ladder (${V5_F06_AUTONOMY_TIER_LADDER_SEAM})`,
      `effect cap vocabulary and limits (${V5_F06_EFFECT_CAP_VOCABULARY_SEAM})`,
      `provider-side readback join key format (${V5_F06_PROVIDER_READBACK_JOIN_SEAM})`,
      `capability lifetime ceiling in seconds (${V5_F06_CAPABILITY_LIFETIME_CEILING_SEAM})`,
      `durable envelope, capability, consumption and quarantine rows: no database disposition and no applied migration`,
      `Connector Gateway dispatch and provider readback: no deployed verb`,
    ],
    seams: [...V5_F06_SEAMS],
    // Later runtime and acceptance inputs. Named so nobody mistakes this
    // projection for one of them; none is produced or satisfied here.
    attended_activation_receipt: V5_ATTENDED_ACTIVATION_RECEIPT_STEP,
    attended_activation_receipt_present: false,
    grants_outward_effect: false,
    accepts_anything: false,
    effects: V5_NO_EFFECTS,
  });
}
