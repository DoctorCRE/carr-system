// DoctorCRE v5 slice V5-A02, Step B: the gateway half of the Gate Zero
// read-only outcome record.
//
// WHAT THIS FILE IS. The contract for one `consumer-gate-receipt.v1` as the Gate
// Zero producer emits it, and the ONE authority test that decides whether a
// transaction may record it. `mcp-server/src/tools.js` registers the verb
// `record-gate-zero-read-only-outcome` over these functions; the record layer's
// own half is `ops.gate_zero_producer_actor_id()` and
// `ops.gate_zero_record_read_only_outcome()` in migration 0502. The two are
// deliberately independent and neither substitutes for the other: a handler bug
// cannot step around the database, and a writer connection opened outside the
// gateway cannot step around the database's derivation either.
//
// WHAT IT IS NOT. It is NOT the producer. Nothing here aims the three bound
// evidence readers at rows, applies a `checkable_done` clause, or decides
// whether Gate Zero passed. That is Step A
// (`mcp-server/src/gate-zero-producer.v5.js`), which emits the receipt this file
// records. This file's whole job is to refuse everyone who is not entitled to
// sign, and to refuse a receipt whose shape does not match the one r7 registers.
//
// AND THE RECEIPT IS NOT A CALLER'S (2026-09-13, PR 1014 correction). The verb
// takes ONE argument, `idempotency_key`, and gets the receipt by invoking the
// bound producer seam: `emitGateZeroOutcome()` in gate-zero-assurance.v5.js,
// which calls Step A's zero-argument producer and hands back what it emitted.
// The first draft of this slice accepted a `receipt` object from the caller and
// persisted it, which made the durable row a statement about whatever the caller
// had assembled. There is now NO input that can carry one: a `receipt` argument
// is refused by the verb's closed input schema as an unregistered field, before
// this module is reached at all.
//
// THE NEW AUTHORITY SHAPE, AND THE RULING THAT CREATED IT. Every write verb in
// this system before this one gated on a verified human partner (`humanOnly`)
// or admitted any sponsored agent. The Gate Zero producer is neither: its role
// is `independent_control_plane_oracle` and its seat is the Codex reviewer lane,
// whose derived authority class is `review_agent` (identity.js:265-271) -- an
// identity that authenticates, resolves shared-only, and which no write verb in
// this system accepted. Joe ruled on 2026-09-13 (decision
// d4e5f6a7-b8c9-4d0e-9f1a-2b3c4d5e6f70) that this seat records the outcome row
// on its own authority, with NO partner countersign, under the card-9 charter
// ruling 8a1dad08-8707-4bb0-a159-c2831a00cea2 and the blanket approval
// 5e2b8c1a-9f47-4d63-b0e5-7a3d1c9f2e84. So the verb is `humanOnly: false` and
// refuses every actor except that one seat.
//
// `review_agent` IS NOT THE TEST, AND THIS IS THE PART A READER MUST NOT SKIM.
// `grok-reviewer` authenticates through the same review-token door and derives
// the same `review_agent` class (identity.js:31-36). A rule that admitted the
// CLASS would hand the Gate Zero signature to a second reviewer lane nobody
// ruled on. The test is the class AND the seat: the actor slug must be the LANE
// of the staffed holder ref, which is derived from
// `V5_A02_GATE_ZERO_PRODUCER_REGISTRATION.oracle_seat_holder_ref` and is `null`
// the moment that seat goes back to unstaffed. Put `holder_ref: null` back in
// gate-zero-producer-registration.v5.js and this verb refuses everyone,
// including the seat -- which is the mutation control the test suite runs.
//
// NO CALLER FIELD DECIDES ANYTHING.
//   * The seat comes from the frozen registration, never from an argument.
//   * The actor comes from the authenticated actor object the gateway resolved
//     from a Worker-secret bearer match, never from an argument.
//   * The digest is recomputed by the database from the stored receipt, so this
//     module never accepts one and never sends one.
//   * `assertNoCallerAuthorityFields` already refuses `actor`, `identity`,
//     `authorization_class` and their family at the choke point; and since there
//     is no receipt argument left, there is nothing for a caller to put an
//     identity inside either.
//   * THE NESTED IDENTITIES COME FROM THE AUTHENTICATED CALL AND ARE CHECKED
//     AGAINST IT. `producer_identity` and `evaluator_identity` must equal, field
//     for field, the `authenticated-receipt-identity.v1` that identity.js
//     derived for THIS call — the same value the producer read when it built the
//     receipt. A receipt naming a foreign session is refused here even though
//     its actor_id and authority_class are the seat's, which is the one mutation
//     a same-seat-different-session forgery would otherwise pass.
//   * THE IDENTITY OBJECT'S SHAPE IS r7's, CLOSED. `authenticated-receipt-
//     identity.v1` sets `additional_properties: false`, names exactly three
//     required fields, and gives `session_ref` a LOWERCASE pattern with a
//     minimum length. All three clauses are enforced here and again in SQL.
//
// THE DIGEST RECIPE IS NO LONGER AN ASSUMPTION; r7 DECLARES IT. Until 2026-09-13
// r7's `receipt_payload_digest_rule` named domain tags for `benchmark-manifest.v1`,
// `attended-effect-capability.v1`, `attended-effect-consumption-receipt.v2` and
// `attended-effect-outcome-receipt.v1` -- and NOT for `consumer-gate-receipt.v1`.
// This file took the plain reading under that silence and said so out loud, on
// the record, precisely so one line could overturn it.
//
// THAT LINE WAS WRITTEN. The r7 amendment of 2026-09-13 -- act 5 of accepted
// plan PLAN-a5059eb52474-v3, landed in the packet now pinned at
// `4379c60e9a4fefbcf044f4bc5a34e5a95c90f77b9adf348b6da7475e17d5e6d7` -- declares
// this schema's payload digest to be THE TAGGED PREIMAGE: sha256 over the
// RFC8785/JCS serialization of the two-element array whose first element is the
// domain-tag string `consumer-gate-receipt.v1` and whose second is the
// twenty-one-field receipt. The rule says in its own words that a plain digest
// over the receipt alone does not satisfy it. So `gateZeroOutcomeDigest` below
// computes `digest([GATE_ZERO_RECEIPT_SCHEMA, receipt])`, and migration 0505's
// `ops.gate_zero_outcome_digest()` computes the same preimage byte for byte.
//
// WHAT IS NOT TAGGED, AND WHY THAT IS NOT AN INCONSISTENCY. The candidate-scoped
// digest below is computed over a PROJECTION of the receipt -- five fields
// removed -- which is not a `consumer-gate-receipt.v1` and therefore not a
// payload r7's rule speaks about at all. Tagging a projection with the schema
// name of the thing it is not would be the misstatement, so it stays plain, on
// both sides of the seam. It is an informational comparison aid, never evidence
// and never an admission key; candidate_digest arbitrates the immutable row.

import { digest } from "./artifact-trust.js";
import { authenticatedIdentity, authorizationClassForActor } from "./identity.js";
import { ToolError } from "./tool-error.js";
import { V5_A02_GATE_ZERO_PRODUCER_REGISTRATION } from "./gate-zero-producer-registration.v5.js";

/**
 * AMENDMENT 2'S CLOSED SHAPE FOR AN EXPORTED CALLABLE, lifted from
 * gate-zero-assurance.v5.js and gate-zero-producer.v5.js rather than retyped,
 * because a retyped guard is a second implementation that passes because it was
 * written from the same misunderstanding as the code it checks.
 *
 * (a) NOT CONSTRUCTABLE. A bound function carries no `prototype`, so `new` and
 *     `Reflect.construct` are refused by the engine before a line here runs, and
 *     the engine's refusal quotes `[native code]` rather than this module's own
 *     source back at whoever probed it.
 * (b) `instanceof` ANSWERS FALSE WITHOUT TOUCHING THE OPERAND. The intrinsic
 *     `Symbol.hasInstance` walks the LEFT operand's prototype chain, which runs
 *     the caller's own `getPrototypeOf` trap; an own non-writable,
 *     non-configurable, non-enumerable data property answers false instead.
 * (d) FROZEN, so no property of the export can be written over afterwards.
 */
function closedCallable(callable) {
  const closed = callable.bind(null);
  Object.defineProperty(closed, Symbol.hasInstance, {
    value: () => false, writable: false, enumerable: false, configurable: false,
  });
  return Object.freeze(closed);
}

/** The schema r7 registers as this producer's output. Not a schema of our own. */
export const GATE_ZERO_RECEIPT_SCHEMA = "consumer-gate-receipt.v1";

/** The schema r7 registers for each of the receipt's three identity objects. */
export const GATE_ZERO_IDENTITY_SCHEMA = "authenticated-receipt-identity.v1";

/**
 * The twenty-one required fields of `consumer-gate-receipt.v1`, in r7's own
 * order. The schema is CLOSED (`additional_properties: false`), so this list is
 * both the required set and the permitted set: an unknown field denies, which is
 * r7's own rule and is what makes a digest a statement about a known shape.
 */
export const GATE_ZERO_RECEIPT_FIELDS = Object.freeze([
  "gate_id", "receipt_producer_step_ref", "subject_digest", "candidate_digest",
  "policy_digest", "environment_manifest_digest", "subject_environment", "evidence_scope",
  "subject_maker_identity", "producer_identity", "evaluator_identity", "producer_role",
  "independent_oracle_ref", "oracle_version", "evidence_ref", "fixture_set_digest",
  "observed_at", "ttl_expires_at", "status", "comparator", "negative_admission_result",
]);

/**
 * The twelve fields that are CONSTANTS for this producer, taken from the r7
 * registry row rather than restated by hand: nine come off the registration
 * module's exports, and the three that r7 states directly on the row are here.
 * A receipt that disagrees with one of them is a receipt for a different gate.
 */
export const GATE_ZERO_RECEIPT_CONSTANTS = Object.freeze({
  gate_id: "gate-zero-read-only-accepted",
  receipt_producer_step_ref: "step:gate-zero-read-only-outcome",
  producer_role: "independent_control_plane_oracle",
  independent_oracle_ref: "oracle:gate-producer:gate-zero-read-only",
  oracle_version: "1.0.0",
  evidence_scope: "candidate-and-test",
  subject_environment: "candidate",
  negative_admission_result: "all_required_denials_observed",
});

/** r7's status enum for a consumer gate receipt. Five members, closed. */
export const GATE_ZERO_RECEIPT_STATUSES =
  Object.freeze(["pass", "fail", "unknown", "stale", "quarantined"]);

/**
 * `authenticated-receipt-identity.v1`'s CLOSED field set, in r7's own order.
 * The schema sets `additional_properties: false` and names these three as
 * required, so this list is both the required set and the permitted set: a
 * fourth key denies exactly as a missing one does.
 */
export const GATE_ZERO_IDENTITY_FIELDS =
  Object.freeze(["actor_id", "session_ref", "authority_class"]);

const DIGEST_REF = /^sha256:[0-9a-f]{64}$/;
// LOWERCASE ONLY, and it is worth the comment: a single capital is refused by
// the r7 pattern with a bare error naming no field, which has cost a real
// debugging session before.
const EVIDENCE_REF = /^safe:[a-z0-9][a-z0-9:_./-]*$/;
// r7's OWN PATTERN FOR session_ref, character for character, and it is neither
// of the two things the first draft wrote. It is LOWERCASE (a single capital
// denies, the same trap `safe:` refs carry), it admits `/`, and it has a
// MINIMUM LENGTH — `session:` plus at least nine more characters — so a
// one-character session ref is refused rather than accepted as well-formed.
const SESSION_REF = /^session:[a-z0-9][a-z0-9:._/-]{8,199}$/;
const RFC3339 = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$/;

const DIGEST_FIELDS = Object.freeze([
  "subject_digest", "candidate_digest", "policy_digest",
  "environment_manifest_digest", "fixture_set_digest",
]);
const IDENTITY_FIELDS =
  Object.freeze(["subject_maker_identity", "producer_identity", "evaluator_identity"]);

function refuse(error, detail) {
  throw new ToolError({ error, ...detail });
}

/**
 * THE STAFFED SEAT LANE, DERIVED. `null` when the seat is unstaffed, when the
 * holder ref is not a well-formed `seat:<lane>:<desk>`, or when the derivation
 * in the registration module reports the seat unbound for any of the six
 * reasons it fails closed on.
 *
 * It reads the registration's DERIVED fields rather than re-deriving the seat
 * here, for the same reason the registration reads its own witness off itself:
 * two derivations of one fact are two authorities, and they drift.
 */
export const gateZeroOracleSeatLane = closedCallable(() => {
  const registration = V5_A02_GATE_ZERO_PRODUCER_REGISTRATION;
  if (registration?.oracle_seat_bound !== true) return null;
  const holder = registration.oracle_seat_holder_ref;
  if (typeof holder !== "string") return null;
  const lane = holder.split(":")[1];
  return typeof lane === "string" && lane.length ? lane : null;
});

/**
 * THE ONE AUTHORITY TEST. Four ordered questions, each answering "refuse", and
 * the order matters: the seat is checked before the actor, so an unstaffed seat
 * refuses everyone rather than refusing everyone-except-whoever-happens-to-match.
 *
 *   1. Is the oracle seat staffed at all? No -> refuse; nobody may sign.
 *   2. Is the actor's SERVER-DERIVED authority class `review_agent`? A partner,
 *      a sponsored agent, a probe, Hermes and an unsponsored runtime are all
 *      refused here, whichever connection they hold.
 *   3. Is the actor slug the staffed seat's LANE? `grok-reviewer` derives the
 *      same class through the same door and is refused by this question alone,
 *      which is the whole reason the question exists.
 *   4. Is the actor a machine (`human !== true`)? A human reaching a
 *      review-token class would be a contradiction, and the human act in this
 *      chain is the benchmark acceptance downstream.
 *
 * It returns the seat it admitted, so the caller records what it derived rather
 * than re-deriving it a second time and hoping the two agree.
 */
export const deriveGateZeroProducerSeat = closedCallable((actor) => {
  const lane = gateZeroOracleSeatLane();
  if (lane === null) {
    refuse("gate_zero_oracle_seat_unstaffed", {
      hint: "oracle:gate-producer:gate-zero-read-only has no staffed holder, so no actor may record an outcome. " +
            "Staffing is an edit to gate-zero-producer-registration.v5.js, never something a caller can claim.",
      oracle_ref: GATE_ZERO_RECEIPT_CONSTANTS.independent_oracle_ref,
    });
  }
  const derivedClass = authorizationClassForActor(actor);
  if (derivedClass !== "review_agent" || actor?.human === true) {
    refuse("gate_zero_oracle_seat_required", {
      derived_authority_class: derivedClass,
      required_authority_class: "review_agent",
      hint: "this verb records an independent control-plane oracle's receipt. It is not humanOnly and it is not " +
            "open to sponsored agents: it refuses every actor except the one review-token seat that holds " +
            "oracle:gate-producer:gate-zero-read-only. The human act in this chain is the benchmark acceptance " +
            "downstream, which is unchanged.",
    });
  }
  if (actor?.slug !== lane) {
    refuse("gate_zero_oracle_seat_mismatch", {
      derived_authority_class: derivedClass,
      seat_lane: lane,
      hint: "the review-token door admits more than one lane and they derive the same authority class. Only the " +
            "lane holding the Gate Zero oracle may record its outcome; a second reviewer lane is refused here " +
            "by name rather than admitted by class.",
    });
  }
  return Object.freeze({
    holder_ref: V5_A02_GATE_ZERO_PRODUCER_REGISTRATION.oracle_seat_holder_ref,
    lane,
    derived_authority_class: derivedClass,
    charter_decision_ref: V5_A02_GATE_ZERO_PRODUCER_REGISTRATION.oracle_seat_charter_decision_ref,
    staffing_decision_ref: V5_A02_GATE_ZERO_PRODUCER_REGISTRATION.oracle_seat_staffing_decision_ref,
  });
});

/**
 * THE RECEIPT CONTRACT. Every clause below refuses; none of them decides
 * anything about Gate Zero. It is checked HERE so a malformed receipt is named
 * by field instead of arriving as a bare database error, and it is checked AGAIN
 * in SQL because that is the copy a handler bug cannot step around.
 *
 * `seat` is the RESULT of deriveGateZeroProducerSeat, never a caller value: the
 * identity clauses compare the receipt against what the server derived.
 */
export const assertGateZeroReceipt = closedCallable((receipt, seat) => {
  if (!receipt || typeof receipt !== "object" || Array.isArray(receipt)) {
    refuse("gate_zero_receipt_malformed",
      { hint: `pass one ${GATE_ZERO_RECEIPT_SCHEMA} object` });
  }
  const keys = Object.keys(receipt);
  const missing = GATE_ZERO_RECEIPT_FIELDS.filter(field => !keys.includes(field));
  const unknown = keys.filter(field => !GATE_ZERO_RECEIPT_FIELDS.includes(field)).sort();
  // BOTH HALVES OF A CLOSED SCHEMA, reported together: r7 says unknown OR
  // missing fields deny, and a caller who has both wants to see both.
  if (missing.length || unknown.length) {
    refuse("gate_zero_receipt_fields", {
      schema: GATE_ZERO_RECEIPT_SCHEMA, missing, unknown,
      hint: `${GATE_ZERO_RECEIPT_SCHEMA} is closed: exactly the twenty-one r7 fields, no more and no fewer`,
    });
  }

  for (const [field, expected] of Object.entries(GATE_ZERO_RECEIPT_CONSTANTS)) {
    if (receipt[field] !== expected) {
      refuse("gate_zero_receipt_constant_mismatch", {
        field, expected, got: receipt[field],
        hint: "this field is fixed by the r7 producer registry row for step:gate-zero-read-only-outcome. " +
              "A receipt that renames one is a receipt for a different gate.",
      });
    }
  }

  for (const field of DIGEST_FIELDS) {
    if (typeof receipt[field] !== "string" || !DIGEST_REF.test(receipt[field]))
      refuse("gate_zero_receipt_digest_malformed", { field, got: receipt[field] });
  }
  if (typeof receipt.evidence_ref !== "string" || !EVIDENCE_REF.test(receipt.evidence_ref)) {
    refuse("gate_zero_receipt_evidence_ref_malformed", {
      got: receipt.evidence_ref,
      hint: "safe: refs are lowercase only; a single capital is refused by the r7 pattern",
    });
  }
  for (const field of ["observed_at", "ttl_expires_at"]) {
    if (typeof receipt[field] !== "string" || !RFC3339.test(receipt[field]))
      refuse("gate_zero_receipt_instant_malformed", { field, got: receipt[field] });
  }
  if (!(Date.parse(receipt.ttl_expires_at) > Date.parse(receipt.observed_at))) {
    refuse("gate_zero_receipt_expiry_not_after_observation",
      { observed_at: receipt.observed_at, ttl_expires_at: receipt.ttl_expires_at });
  }
  if (!GATE_ZERO_RECEIPT_STATUSES.includes(receipt.status)) {
    refuse("gate_zero_receipt_status_unknown",
      { got: receipt.status, admitted: [...GATE_ZERO_RECEIPT_STATUSES] });
  }
  if (typeof receipt.comparator !== "string"
      || receipt.comparator.length < 5 || receipt.comparator.length > 300) {
    refuse("gate_zero_receipt_comparator_malformed", {
      hint: "r7 requires a comparator sentence of 5 to 300 characters saying what this run compared",
    });
  }

  // THE THREE IDENTITIES, AND r7's SHAPE FOR THEM IS CLOSED. Three fields,
  // exactly — a fourth key denies as readily as a missing one, because
  // `authenticated-receipt-identity.v1` sets `additional_properties: false` and
  // a digest over an open shape is a statement about nothing in particular.
  for (const field of IDENTITY_FIELDS) {
    const identity = receipt[field];
    if (!identity || typeof identity !== "object" || Array.isArray(identity)) {
      refuse("gate_zero_receipt_identity_malformed", {
        field, schema: GATE_ZERO_IDENTITY_SCHEMA,
        hint: `each identity is one ${GATE_ZERO_IDENTITY_SCHEMA} object`,
      });
    }
    const keys = Object.keys(identity);
    const missing = GATE_ZERO_IDENTITY_FIELDS.filter(one => !keys.includes(one));
    const unknown = keys.filter(one => !GATE_ZERO_IDENTITY_FIELDS.includes(one)).sort();
    if (missing.length || unknown.length) {
      refuse("gate_zero_receipt_identity_fields", {
        field, schema: GATE_ZERO_IDENTITY_SCHEMA, missing, unknown,
        hint: `${GATE_ZERO_IDENTITY_SCHEMA} is closed: exactly actor_id, session_ref and authority_class`,
      });
    }
    if (typeof identity.actor_id !== "string" || !identity.actor_id
        || typeof identity.authority_class !== "string" || !identity.authority_class) {
      refuse("gate_zero_receipt_identity_malformed", {
        field, schema: GATE_ZERO_IDENTITY_SCHEMA,
        hint: "actor_id and authority_class are non-empty strings",
      });
    }
    if (typeof identity.session_ref !== "string" || !SESSION_REF.test(identity.session_ref)) {
      refuse("gate_zero_receipt_session_ref_malformed", {
        field, got: identity.session_ref,
        hint: "r7's session_ref pattern is lowercase and at least nine characters after `session:`; " +
              "a single capital denies, exactly as it does in a safe: ref",
      });
    }
  }

  // AND THE PRODUCER AND EVALUATOR ARE THIS CALL, FIELD FOR FIELD. r7's
  // identity_rule says the gateway DERIVES all identities from authenticated
  // execution context and that caller-supplied identity denies. The derivation
  // is identity.js's, taken with no argument from the AsyncLocalStorage that
  // tools.js's one dispatch entered — the same value the producer read when it
  // assembled this receipt — so what is compared here is not a claim about the
  // seat but the seat's own derived identity for THIS call.
  //
  // COMPARING actor_id AND authority_class ALONE WAS THE HOLE. Both are stable
  // across every call the seat ever makes, so a receipt carrying a foreign
  // `session_ref` passed a check that looked like an identity check. The session
  // ref is the server's per-call correlation id and is the only field in the
  // object that no caller writes, so it is the one that has to match.
  const call = authenticatedIdentity.receiptIdentity();
  if (call === null) {
    refuse("gate_zero_receipt_unauthenticated_call", {
      hint: "there is no authenticated call to derive receipt identities from, so no receipt may be recorded. " +
            "The one place a call is established is tools.js's verb dispatch; a direct import cannot obtain one.",
    });
  }
  for (const field of ["producer_identity", "evaluator_identity"]) {
    const identity = receipt[field];
    const differs = GATE_ZERO_IDENTITY_FIELDS.filter(one => identity[one] !== call[one]);
    if (differs.length) {
      refuse("gate_zero_receipt_identity_not_this_call", {
        field, differing_fields: differs, seat_lane: seat.lane,
        hint: "r7 derives the producer and evaluator identities from the authenticated execution context. " +
              "This receipt names an identity that is not the one this call derived — a different actor, a " +
              "different authority class, or a session that is not this call's.",
      });
    }
  }
  // AND THE DERIVED IDENTITY IS STILL THE STAFFED SEAT'S. The two checks are
  // not the same question: the one above says the receipt is this call's, and
  // this one says this call is the seat r7 registered. A call that authenticated
  // as something else never reaches here -- deriveGateZeroProducerSeat already
  // refused it -- so this is the assertion that the two derivations agree.
  if (call.actor_id !== seat.lane || call.authority_class !== "review_agent") {
    refuse("gate_zero_receipt_identity_not_the_seat", {
      seat_lane: seat.lane, derived_actor_id: call.actor_id,
      derived_authority_class: call.authority_class,
      hint: "r7 binds producer_role to the registry entry and derives identity from authenticated context. " +
            "The producer and the evaluator are the staffed oracle seat; nobody else's name may appear there.",
    });
  }
  // SAME-ACTOR SELF-REVIEW DENIES, in both dimensions r7 names. This is exactly
  // why the seat was staffed with the lane that reviewed every Gate Zero pull
  // request and built none of them.
  if (receipt.subject_maker_identity.actor_id === seat.lane) {
    refuse("gate_zero_receipt_self_review", {
      seat_lane: seat.lane,
      hint: "the subject maker must differ from the evaluator; the oracle cannot sign a candidate it built",
    });
  }
  if (receipt.subject_maker_identity.session_ref === receipt.evaluator_identity.session_ref) {
    refuse("gate_zero_receipt_self_review_session", {
      hint: "the subject maker's session must differ from the evaluator's; r7 requires both dimensions",
    });
  }
  return receipt;
});

/**
 * THE DIGEST r7 DECLARES: the TAGGED preimage, not the receipt alone.
 *
 * `digest([GATE_ZERO_RECEIPT_SCHEMA, receipt])` is sha256 over the canonical
 * JSON of that exact two-element array — `["consumer-gate-receipt.v1",{…}]` —
 * which is what r7's `receipt_payload_digest_rule` has required for this schema
 * since the 2026-09-13 amendment. See the header: the plain `digest(receipt)`
 * this file used to compute is now a value r7 says does not satisfy the rule.
 *
 * IT IS NOT AUTHORITATIVE HERE. The recorded value is the one the DATABASE
 * recomputes from the persisted receipt with ops.gate_zero_outcome_digest(); this
 * function exists so the verb can report back the digest a caller can check, and
 * so a test can assert the two sides agree. If they ever disagreed, the database
 * would win and the row would carry its value, not this one. The cross-
 * implementation proof that they do agree — over the SAME tagged preimage — is
 * mcp-server/test/gate-zero-outcome-digest-tagged.test.mjs, which also asserts
 * that the plain digest is NOT accepted.
 */
export const gateZeroOutcomeDigest =
  closedCallable(receipt => digest([GATE_ZERO_RECEIPT_SCHEMA, receipt]));

/**
 * THE SAME RECEIPT, REDUCED TO WHAT THE CANDIDATE DECIDES — an informational
 * comparison aid, and the gateway's copy of ops.gate_zero_outcome_candidate_digest.
 *
 * WHY IT IS NOT THE IDEMPOTENCY KEY. Five values of a
 * consumer-gate receipt legitimately move between two GENUINE authenticated runs
 * of one candidate: `observed_at` and `ttl_expires_at`, stamped when each run
 * happened, and the `session_ref` inside ALL THREE identities, every one of
 * which identity.js derives from the request's own correlation id — the subject
 * maker's included, because Step A's third correction stopped manufacturing it
 * out of the revision and made it the candidate-build seat WITHIN the
 * authenticated call. Keying idempotency on a digest covering those made the
 * only two calls that could ever agree two calls carrying the same bytes — a
 * fixture, not a retry — so the second real call for one candidate was refused.
 *
 * WHAT IS DROPPED IS EXACTLY THE PER-CALL VALUES, and what is kept is the point:
 *   * every identity's actor_id and authority_class stay, so a changed identity
 *     remains visible in the offered projection and metadata.
 *   * every digest, constant, status, comparator and evidence ref stays, so a
 *     run that read different rows remains visible in that offered projection.
 * RETRY CONVERGENCE is decided by candidate_digest, not this projection: any
 * later observation of that candidate receives the immutable first row so the
 * caller can heal a missing audit event. This value only helps report how the
 * offered and recorded observations differ; it authorizes neither one.
 *
 * NOTHING IS LOST BY IT. The dropped values are inside the receipt this projects
 * from, which is stored whole, and inside the full outcome digest stored beside
 * it. This projection narrows an explanatory view, not the write-once key.
 */
export const gateZeroOutcomeCandidateDigest = closedCallable(receipt => {
  const { observed_at, ttl_expires_at, ...rest } = receipt;
  const withoutSession = identity => {
    const { session_ref, ...keep } = identity ?? {};
    return keep;
  };
  return digest(Object.fromEntries(Object.entries(rest).map(([field, value]) =>
    [field, field.endsWith("_identity") ? withoutSession(value) : value])));
});
