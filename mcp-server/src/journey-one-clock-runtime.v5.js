// DoctorCRE v5 slice V5-M01, the last unjoined seam: THE ONE SEAT WHERE THE
// ADMITTED-MINIMUM INVENTORY, THE KERNEL AND THE CLOCK HISTORY MEET.
//
// Three rails already exist and each one publishes a trusted_integration_contract
// naming the next:
//
//   journey-one-clock-input-store.v5.js  createJourneyOneClockProjectionComposer(...)
//                                        .compose({ ..., history }) -> a projection
//                                        assembled from the stored inventory
//   journey-one-clock.v5.js              createJourneyOneClock({ verifySnapshot })
//                                        .evaluate(envelope) -> state + verified_binding
//   journey-one-clock-store.v5.js        createJourneyOneClockRecorder({ clock, store })
//                                        .evaluateAndRecord({ envelope, expected_prior... })
//
// NOTHING IN THIS REPOSITORY JOINED THEM. The composed projection reached a
// kernel only inside a test, every caller re-derived the order for itself, and
// the two facts that make the loop coherent — WHICH HISTORY WAS EVALUATED and
// WHICH HEAD THE RESULT APPENDS ONTO — were two independent caller arguments on
// two different rails, free to disagree. This file is that join and nothing else.
//
// ---------------------------------------------------------------------------
// WHAT IT ACTUALLY ADDS, because a wrapper that only forwards is not worth a file.
//
//   1. ONE READ, BOTH DERIVED. advance() reads the authoritative scope's clock
//      and its head history ONCE, and derives from that single read both the
//      `history` the projection is composed against and the
//      `expected_prior_history_digest` the append compare-and-swaps on. Neither
//      is an argument: passing either is refused BY NAME. A caller who could
//      supply both could evaluate against one history and swap onto another.
//   2. THE COMPUTATION IS THE COMPOSED PROJECTION, EXACTLY.
//      assertJourneyOneClockComputedFromComposition() compares the kernel's own
//      `authenticated_projection_digest` — the canonical digest of the whole
//      snapshot the installed verifier returned, taken inside evaluate() — with
//      the digest of the projection this record layer composed, and refuses
//      before anything is written when they differ. It also refuses an origin
//      that is not a receipt in the composed inventory — which the digest
//      ENTAILS, since the kernel selects its origin out of that same
//      minimum_history, and which is kept so the common failure is named rather
//      than reported as two unequal hashes.
//      AN EARLIER REVISION OF THIS FILE CHECKED ONLY NAMED FIELDS — the scope
//      digests, the instant, the manifest, the sealed TTL policy, the origin and
//      the pause ids and ends — and claimed that established identity. It did
//      not: every projection built for one program shares the accepted scope, so
//      a second VALID snapshot differing only in its completion, its history,
//      its amendments or a pause's START instant passed all of them and would
//      have been filed. Those checks remain as named diagnostics; the digest is
//      the proof.
//   3. ONE SCOPE ACROSS BOTH RAILS, CHECKED AT CONSTRUCTION. A composer reading
//      the inventory of scope A beside a store writing for scope B is refused
//      when the runtime is built, rather than after a kernel evaluation. The
//      composed history is re-checked against the prior the append names for the
//      same reason: the construction check was against the composer OBJECT, and
//      this is the artifact that gets filed.
//   4. A RETRY IS A RETRY, NOT A SECOND REQUEST. Deriving the prior from the head
//      makes an advance correct and makes it NON-idempotent: after a lost
//      response the head has moved, so a re-sent request computes a different
//      revision and meets `clock_idempotency_key_reused` — true about the payload,
//      false about the situation, and an invitation to re-key and append a second
//      legitimate-looking revision to an append-only history. So the key's
//      recorded revision is read FIRST, from the same idempotency evidence the
//      store already keeps, and a retry is re-composed against THE EXACT PRIOR
//      THAT REVISION WAS WRITTEN UNDER. It is replayed only when the
//      re-computation reproduces the recorded history digest, in the same scope,
//      by the same writing seat. Nothing is appended, and nothing is skipped.
//
// ---------------------------------------------------------------------------
// WHAT IT IS NOT, AND EVERY ONE OF THESE IS STRUCTURAL RATHER THAN PROMISED.
//
//   * IT IS NOT A VERIFIER AND IT NEVER BECOMES ONE. It takes an ALREADY-
//     CONSTRUCTED kernel — the object createJourneyOneClock({ verifySnapshot })
//     returns — exactly as createJourneyOneClockRecorder does. It accepts no
//     verifySnapshot callback, no `{ verified: true }`, no self-hashed envelope
//     and no boolean. A caller-supplied truth about authentication meets the A00
//     authority guard before any read, composition or evaluation happens.
//   * IT IS NOT A PRODUCER. It issues no minimum receipt, no terminus receipt
//     and no acceptance, and it admits nothing to any inventory: the composer it
//     holds READS the admitted-minimum ledger and never writes to it.
//   * IT DECIDES NO START, and it starts no PRODUCTION clock here. The kernel's
//     own origin selection is what starts a clock and a first advance is the
//     write that files one — `created_clock: true` says so on the receipt — so
//     "this seat starts nothing" would be false. What is true is narrower and is
//     the thing that matters: no ISSUED foundation-assurance-minimum receipt
//     exists in this repository, so no production inventory can be opened and no
//     production advance can be made. THE SUITES DO RUN THIS LOOP, end to end,
//     on FIXTURE receipts through the real rails; a fixture is not an issued
//     artifact, and "not authorized/not issued" and "not possible" are different
//     findings. That is an absence of evidence HERE and not a proof of absence
//     about the record as a whole.
//   * IT RE-DECIDES NOTHING. The 30 Chicago calendar days, the 120-hour accepted
//     pause union, the sticky miss, the replan obligation, the terminus contract
//     and every receipt rule live in the kernel and are reached by CALLING it.
//     This file computes no deadline, judges no receipt, holds no pause budget
//     and reads no live clock: every instant it touches comes from a value one of
//     the rails already validated, and the only thing it does with two of them is
//     ask whether they are the same instant.
//   * IT IS NOT A SECOND evaluate-check-record SEQUENCE. That order has one home,
//     in createJourneyOneClockRecorder, and this file reaches its extra refusal
//     through that recorder's `assert_before_write` seam rather than re-writing
//     the sequence beside it. A second copy is the drift rule a8c55a47 forbids.
//   * IT REGISTERS NO VERB. There are no tools in this file, deliberately: the
//     public write verbs on both rails still fail closed ahead of any query, and
//     nothing here opens them.
//
// ---------------------------------------------------------------------------
// THE PRESENTATION SEAM, STATED PLAINLY BECAUSE IT IS THE ONE CALLBACK.
//
// The kernel's input contract is an ENVELOPE, not a projection: trusted server
// code presents an envelope, and the installed verifier answers with the snapshot
// it authenticated. `present_projection` is that presentation, supplied at
// CONSTRUCTION by the same trusted server code that installed the verifier, and
// it is not a request field.
//
// IT AUTHENTICATES NOTHING AND IT CANNOT. It hands back an envelope reference;
// the installed verifier still has to authenticate that envelope, and a
// presentation that names an envelope the verifier does not know refuses inside
// the kernel. A presentation that names an envelope the verifier maps to some
// OTHER projection is exactly what check (2) above catches, which is why that
// check exists rather than a comment asking the seam to be honest.
//
// THE CONTRACT THAT FALLS OUT OF (2), STATED SO NOBODY MEETS IT IN PRODUCTION.
// For a presented composition, the installed verifier must return the snapshot
// that was presented — CANONICALLY IDENTICAL, not merely equivalent:
//   * KEY ORDER IS FREE. digest() canonicalizes, so a re-serialized object with
//     the same values is the same projection.
//   * EVERYTHING ELSE IS EXACT. A differently spelled instant
//     (`+00:00` for `Z`), a re-ordered `minimum_history`, a `history` rebuilt
//     from rows rather than carried through, an added or dropped field: each is
//     a different projection and each refuses.
// A verifier that INDEPENDENTLY RE-DERIVES the projection from the record layer,
// rather than resolving the envelope to the composition it was presented with,
// will therefore refuse every legitimate advance. That is a real constraint on
// the reader that has yet to land, it is named in `blocked_by`, and it is the one
// requirement in this file a future implementer could not discover from the code
// without hitting it live.
//
// ---------------------------------------------------------------------------
// WHAT REMAINS INTEGRATION WORK, named rather than implied. NOTHING HERE MINTS
// ANY OF IT:
//   * The issuance adapter for a foundation-assurance-minimum receipt.
//     benchmark-minimum.v5.js PROPOSES one (`receipt_state:
//     "proposed_not_issued"`), so no inventory can be opened and this loop cannot
//     run.
//   * The authenticated projection reader and the verifier it installs, together
//     with the accepted scope and accepted minimum policy both rails take at
//     construction. This file consumes them; it does not produce them.
//   * The terminus producer, so `completion` and `completion_expectation` have no
//     authenticated source here.
//   * Applying the three candidate SQL files as numbered migrations. None has
//     been applied and none has ever been executed.

import { digest } from "./artifact-trust.js";
import { V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import { assertNoSelfAssertedAuthority } from "./benchmark-acceptance-store.v5.js";
import {
  JOURNEY_ONE_CLOCK_PROJECTION, JOURNEY_ONE_CLOCK_VERIFIED_BINDING,
} from "./journey-one-clock.v5.js";
import {
  JOURNEY_ONE_CLOCK_RECORD_EFFECTS, JOURNEY_ONE_CLOCK_STORE_CANNOT_PROVE,
  createJourneyOneClockRecorder, journeyOneClockKeyForState,
} from "./journey-one-clock-store.v5.js";
import {
  JOURNEY_ONE_MINIMUM_COMPOSE_FIELDS, JOURNEY_ONE_MINIMUM_INPUT_STORE_CANNOT_PROVE,
  JOURNEY_ONE_MINIMUM_PROJECTION_INPUTS_SCHEMA,
} from "./journey-one-clock-input-store.v5.js";

/** Module-local adapter schemas. NOT r7 schemas; r7 declares no runtime shape. */
export const JOURNEY_ONE_CLOCK_RUNTIME_SCHEMA = "doctorcre-v5-journey-one-clock-runtime.v1";
export const JOURNEY_ONE_CLOCK_ADVANCE_RECEIPT_SCHEMA =
  "doctorcre-v5-journey-one-clock-advance.v1";
export const JOURNEY_ONE_CLOCK_RUNTIME_INTEGRATION_SCHEMA =
  "doctorcre-v5-journey-one-clock-runtime-integration.v1";

/**
 * The per-evaluation facts one advance is given, C-sorted.
 *
 * `history` IS ABSENT ON PURPOSE and so is `expected_prior_history_digest`: both
 * are derived from this rail's own single read of the head, and supplying either
 * is refused by name rather than silently ignored.
 */
export const JOURNEY_ONE_CLOCK_ADVANCE_FIELDS = Object.freeze([
  "amendments", "as_of", "clock_ref", "completion", "completion_expectation",
  "idempotency_key", "pauses",
]);

/** The compose fields this seat derives instead of accepting. */
export const JOURNEY_ONE_CLOCK_RUNTIME_DERIVED_COMPOSE_FIELDS = Object.freeze(["history"]);

/** The advance fields that are this rail's own rather than the composer's. */
const RUNTIME_OWN_FIELDS = Object.freeze(["clock_ref", "idempotency_key"]);

/** The two facts a caller may never supply here, and the reason is the same one. */
export const JOURNEY_ONE_CLOCK_DERIVED_NOT_SUPPLIED_FIELDS = Object.freeze([
  "expected_prior_history_digest", "history",
]);

// THE ADVANCE FIELD SET IS RECONCILED AGAINST THE COMPOSER'S AT LOAD rather than
// kept in step by hand. A compose field that fell out of both halves would stop
// being passed and stop being declared derived, and the composition would quietly
// lose an inventory the caller believed it had supplied. That is a startup
// failure here, not a silent narrowing at the seam.
{
  const forwarded = JOURNEY_ONE_MINIMUM_COMPOSE_FIELDS.filter(
    field => !JOURNEY_ONE_CLOCK_RUNTIME_DERIVED_COMPOSE_FIELDS.includes(field));
  const missing = forwarded.filter(field => !JOURNEY_ONE_CLOCK_ADVANCE_FIELDS.includes(field));
  const unexpected = JOURNEY_ONE_CLOCK_ADVANCE_FIELDS.filter(
    field => !forwarded.includes(field) && !RUNTIME_OWN_FIELDS.includes(field));
  if (missing.length || unexpected.length) {
    throw new Error(
      "journey-one-clock-runtime: the advance field set is no longer the composer's own fields minus the derived ones plus this rail's two");
  }
  // And a field declared derived-not-supplied must actually be one no caller can
  // pass: `history` is a compose field this seat derives, and
  // `expected_prior_history_digest` is not an advance field at all.
  if (!JOURNEY_ONE_CLOCK_RUNTIME_DERIVED_COMPOSE_FIELDS.every(
    field => JOURNEY_ONE_CLOCK_DERIVED_NOT_SUPPLIED_FIELDS.includes(field)) ||
      JOURNEY_ONE_CLOCK_DERIVED_NOT_SUPPLIED_FIELDS.some(
        field => JOURNEY_ONE_CLOCK_ADVANCE_FIELDS.includes(field))) {
    throw new Error(
      "journey-one-clock-runtime: a field declared derived-not-supplied is either not derived here or is still an advance field");
  }
}

/**
 * THE FACTS THIS SEAT CHECKS, AND WHICH ONE OF THEM IS THE PROOF.
 *
 * C-sorted by the name each is reported under. Every entry is individually
 * TOTAL — true by construction of evaluate() for the composed projection, so it
 * can fail only when the computation was made from another one — but the SEVEN
 * NAMED FIELD FACTS ARE NOT JOINTLY COMPLETE, and saying otherwise was the
 * overclaim this list now corrects. They read the accepted SCOPE and a handful
 * of parts of the state, and every projection built for one program shares the
 * scope. Two different valid snapshots pass all seven and produce different
 * clocks:
 *
 *   * one carrying a terminus `completion` and one carrying null — same tenant,
 *     same scope digests, same as_of, same manifest, same TTL policy, same
 *     origin, same (empty) pauses — and the second records a COMPLETED clock
 *     the record layer never composed;
 *   * two pauses with one `pause_id` and one `ends_at` but different
 *     `starts_at`, which the pause check cannot see because it compares exactly
 *     those two fields, and which changes `paused_ms` and `due_at`;
 *   * a different `history`, `amendments`, `completion_expectation`, or
 *     `binding.maximum_completion_receipt_ttl_ms`, none of which any of the
 *     seven reads.
 *
 * `projection_identity` is what actually settles it, and it is checked LAST so
 * that the seven can name a specific divergence first. The seven are therefore
 * DIAGNOSTICS: they exist to say WHICH part differs when a difference happens to
 * be one they can see. Deleting them would lose nothing but the error message.
 */
export const JOURNEY_ONE_CLOCK_COMPOSITION_BINDING_FACTS = Object.freeze([
  Object.freeze({ id: "as_of", role: "diagnostic",
    statement: "the state's evaluated_at is the instant the composition was composed as of" }),
  Object.freeze({ id: "benchmark_manifest_digest", role: "diagnostic",
    statement: "the benchmark manifest the state records as current is the composed one" }),
  Object.freeze({ id: "binding_digests", role: "diagnostic",
    statement: "the kernel's verified subject, candidate and policy digests are the composed binding's" }),
  Object.freeze({ id: "minimum_receipt_ttl_policy", role: "diagnostic",
    statement: "the origin was judged under the TTL policy sealed on the inventory the composition was read from" }),
  Object.freeze({ id: "origin_admission", role: "diagnostic",
    statement: "the origin the kernel selected is a receipt this composed inventory carries, at that receipt's own observed_at" }),
  Object.freeze({ id: "pause_intervals", role: "diagnostic",
    statement: "the pause intervals the state mirrors carry the composed pause ids and end instants, in the composed order. It does NOT see a pause's start, approval or approver, so two pauses sharing an id and an end pass it while crediting different elapsed hours" }),
  Object.freeze({ id: "projection_identity", role: "proof",
    statement: "the kernel's authenticated_projection_digest is the canonical digest of the exact projection this record layer composed. This is the only entry that establishes identity; the others are named diagnostics for the parts they happen to cover" }),
  Object.freeze({ id: "tenant", role: "diagnostic",
    statement: "the tenant the kernel verified under is the composed projection's" }),
]);

/**
 * The one fact that proves identity, DERIVED from the table above rather than
 * restated beside it: two homes for "which one is the proof" is exactly the
 * second copy rule 0f38532e forbids, and this one would drift silently, because
 * a reader checking `role` and a reader checking this constant would each still
 * look right on their own.
 */
export const JOURNEY_ONE_CLOCK_COMPOSITION_PROOF_FACT = (() => {
  const proofs = JOURNEY_ONE_CLOCK_COMPOSITION_BINDING_FACTS.filter(f => f.role === "proof");
  if (proofs.length !== 1) {
    throw new Error(
      "journey-one-clock-runtime: exactly one composition binding fact is the proof");
  }
  return proofs[0].id;
})();

/**
 * The closed field set of an advance receipt, C-sorted, asserted on the way out.
 * A published schema tag whose key set is checked nowhere is a shape a consumer
 * cannot rely on; this rail pins its readbacks and this is the same discipline.
 */
export const JOURNEY_ONE_CLOCK_ADVANCE_RECEIPT_FIELDS = Object.freeze([
  "appended", "authenticated_projection_verified_here", "benchmark_envelope",
  "cannot_prove", "clock_key", "clock_scope_key", "clock_scope_matches_verified_binding",
  "clock_scope_ref", "composed_from", "composition_binding", "created_clock",
  "deadline_accepted_by_record_layer", "durable_history_write_required", "effects",
  "expected_prior_history_digest", "history_digest", "input_store_cannot_prove",
  "kernel_verdict", "ok", "record_layer_cannot_prove", "recorded_at", "replayed",
  "replayed_recorded_request", "revision_ordinal", "runtime_schema_version",
  "schema_version",
]);

/** The closed field set of the `composed_from` evidence block, C-sorted. */
export const JOURNEY_ONE_CLOCK_ADVANCE_COMPOSED_FROM_FIELDS = Object.freeze([
  "admission_count", "as_of", "head_admission_digest", "prior_history_digest",
  "prior_revision_ordinal", "prior_source",
]);

/**
 * A CALL THAT APPENDED NOTHING, stated in the same key set an appending call
 * uses so a consumer reads one shape on both paths. Every count is zero and
 * `history_appended` is false, which is the whole difference and the whole point.
 */
export const JOURNEY_ONE_CLOCK_REPLAY_EFFECTS = Object.freeze({
  creates_effect: false,
  database_writes: 0,
  network_calls: 0, provider_actions: 0, notifications: 0,
  schedules: 0, deployments: 0, activations: 0, acceptances: 0,
  clock_started: false,
  history_appended: false,
  grants_dispatch_activation_or_execution: false,
});

// AND THE TWO EFFECT SHAPES ARE ONE SHAPE, reconciled at load rather than kept in
// step by hand. A receipt reporting an append and a receipt reporting a replay
// are read by one consumer, so they differ in their VALUES and never in their
// keys: a key present on one and missing from the other is a consumer reading
// `undefined` as `false`.
{
  const appended = Object.keys(JOURNEY_ONE_CLOCK_RECORD_EFFECTS).sort().join("|");
  const replayed = Object.keys(JOURNEY_ONE_CLOCK_REPLAY_EFFECTS).sort().join("|");
  if (appended !== replayed) {
    throw new Error(
      "journey-one-clock-runtime: the replay and append effect shapes are no longer one key set");
  }
  if (JOURNEY_ONE_CLOCK_REPLAY_EFFECTS.database_writes !== 0 ||
      JOURNEY_ONE_CLOCK_REPLAY_EFFECTS.history_appended !== false) {
    throw new Error(
      "journey-one-clock-runtime: replay effects must report no write and no appended history");
  }
}

/**
 * What this seat cannot prove, carried on every advance beside the two rails' own
 * lists so nothing downstream can read a joined loop as an authenticated one.
 */
export const JOURNEY_ONE_CLOCK_RUNTIME_CANNOT_PROVE = Object.freeze([
  "that the projection the kernel read was authentic. The verifier is trusted server code installed elsewhere; this seat proves only that the snapshot it judged IS the projection this record layer composed, which is identity and not authenticity -- an invented projection that reached both sides would satisfy it, and that is a different and weaker statement",
  "that the admitted receipts the composition carries were issued by a real independent oracle. No issuance adapter exists, and the input store says so in its own words on the same result",
  "that the pauses, amendments, completion and completion expectation handed to an advance came from any record at all. They have no producer in this repository and travel as trusted caller inputs, exactly as they do through the composer",
  "anything about deadline SUCCESS or about a clock having started. An advance records a computation; the kernel's verdict travels through it unchanged and unre-decided",
  "that a STORED revision was the composed projection. The identity proof is a PRE-WRITE GATE and not a durable receipt: doctorcre-v5-journey-one-clock.v2 has no field for a projection digest, adding one would change every history_digest and rebase every stored clock, and this seat proposes neither. A later reader of a stored revision learns what the store's own readback proves and no more",
  "that two different projections cannot produce one digest. The proof rests on the canonical JSON digest this repository already uses everywhere, over a value the kernel's own JSON gate has refused a hidden key, an exotic prototype, a lone surrogate or a non-JSON value in; it is as exact as sha256 and is not stronger than it",
  "that a REPLAYED receipt describes the current head. It describes the revision that request wrote, at the ordinal and prior it was written under; the head may have moved on since, and this seat does not read it to find out. A replay also appends nothing, which is why its effects report no database write",
  "that a retry is idempotent for anything but the SAME request. The recorded revision is replayed only when re-composing against its own exact prior, in this scope, as this writing seat, reproduces its recorded history digest; a changed as_of, a changed pause, a different actor or another scope is a different request and is refused rather than answered from the stored row",
  "that every recorded request can still be re-computed. Replay works by re-composing, so it needs the inventory to still admit that composition: an admission accepted AFTER the request landed, whose admitted_at postdates the request's own as_of, makes the composer refuse `projection_as_of_precedes_admission`. On a retry that refusal is re-raised as `clock_runtime_recorded_request_not_recomputable`, CARRYING the recorded clock key, history digest, revision ordinal and the underlying cause_code, so it can never be read as 'the request did not land'. It fails closed, appends nothing, and the recorded revision stays readable through the store's own readback",
]);

/** The repository's ordinary digest grammar, reused rather than widened. */
const SHA256_REF = /^sha256:[0-9a-f]{64}$/;

export class JourneyOneClockRuntimeError extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = "JourneyOneClockRuntimeError";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function refuse(code, message, detail) {
  throw new JourneyOneClockRuntimeError(code, message, detail);
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

const copy = value => JSON.parse(JSON.stringify(value));

/** One closed-shape reading, used by the argument set and by the receipt alike. */
function closedShape(value, fields, path) {
  if (!isPlainObject(value)) refuse("invalid_shape", `${path} must be an object`, { path });
  const keys = Object.keys(value);
  if (keys.length !== fields.length || fields.some(field => !Object.hasOwn(value, field))) {
    refuse("closed_shape", `${path} must carry exactly its declared fields`,
      { path, expected: [...fields], actual: [...keys].sort() });
  }
  return value;
}

/**
 * The receipt, checked against its own published shape before it leaves. A tag
 * whose key set nothing asserts is a promise to a consumer that no code keeps,
 * and this rail pins every other readback it publishes.
 */
function closedAdvanceReceipt(receipt) {
  closedShape(receipt, JOURNEY_ONE_CLOCK_ADVANCE_RECEIPT_FIELDS, "advance_receipt");
  closedShape(receipt.composed_from, JOURNEY_ONE_CLOCK_ADVANCE_COMPOSED_FROM_FIELDS,
    "advance_receipt.composed_from");
  return deepFreeze(receipt);
}

/**
 * TWO INSTANTS, COMPARED AS INSTANTS RATHER THAN AS TEXT — and that is the whole
 * of this file's dealings with time.
 *
 * Both values reach here already validated by the rail that produced them: one
 * is a string the composer accepted, the other is a string the kernel rendered
 * through toISOString(). The kernel re-renders every instant it records, so
 * `2026-02-04T00:00:00+00:00` and `2026-02-04T00:00:00.000Z` are one instant
 * spelled two ways and a text comparison would report a false difference. No
 * grammar is stated here, no arithmetic is done, no zone is applied and no clock
 * is read: this asks one question and returns a boolean.
 */
function sameInstant(left, right) {
  const a = Date.parse(left), b = Date.parse(right);
  return Number.isFinite(a) && Number.isFinite(b) && a === b;
}

function bindingMismatch(fact, detail) {
  refuse("clock_computation_not_the_composed_projection",
    `this computation is not the projection this record layer composed: ${fact}`,
    { fact, ...detail });
}

/**
 * DID THIS KERNEL COMPUTATION COME FROM THE PROJECTION THIS RECORD LAYER
 * COMPOSED? Asked once, before anything is written, and answered from facts.
 *
 * WHY IT IS NEEDED AT ALL. The kernel authenticates an ENVELOPE and reads back
 * whatever snapshot the installed verifier returns for it. The composer assembles
 * a projection from the stored inventory. Between those two lies the trusted
 * presentation seam, and nothing before this function ever asked whether the
 * snapshot that was judged is the composition that was read. Without it, the
 * record layer's whole inventory read is decorative: a computation over some
 * other projection with the same three binding digests passes the scope check and
 * is filed as this program's clock.
 *
 * WHAT ESTABLISHES IT: ONE DIGEST, AND SEVEN NAMED DIAGNOSTICS AROUND IT.
 *
 * The proof is `projection_identity`: the kernel's own
 * `authenticated_projection_digest` — the canonical digest of the whole snapshot
 * verifySnapshot returned, taken inside evaluate() before a clause read it —
 * compared against the canonical digest of the projection this record layer
 * composed. Equal digests are the same projection, field for field, including
 * every part no field check below looks at.
 *
 * THE SEVEN FIELD CHECKS ARE DIAGNOSTICS AND ARE RUN FIRST, so a divergence they
 * can see is reported by NAME rather than as two unequal hashes. Each is
 * individually total, and together they are NOT sufficient — that was the earlier
 * overclaim, and JOURNEY_ONE_CLOCK_COMPOSITION_BINDING_FACTS now says exactly
 * which valid snapshots slip past them. None of them re-decides anything: this
 * function computes no deadline, judges no receipt and reads no status.
 *
 * THE ORIGIN TEST IS ENTAILED BY THE PROOF, and is kept anyway. The kernel
 * selects its origin OUT OF the projection's own minimum_history, so once the
 * judged projection is the composed one, membership follows and the duplicate
 * branch is unreachable — the kernel refuses `duplicate_minimum` first. It is
 * retained because it names the divergence a reader will actually meet, and
 * because it is the only thing standing if the digest ever collided. The digest
 * compared is `digest(receipt)`, the exact expression the kernel identifies an
 * admission with, so it is a membership test against the kernel's own arithmetic
 * and not a second receipt validator.
 *
 * WHAT A PASS IS NOT. It is not evidence that the projection was authentic, that
 * the receipts were issued, or that the verifier is honest. A dishonest verifier
 * that returned an invented projection would have that projection's digest
 * reported here, and if the composer had somehow been handed the same invented
 * object the two would agree — identity is not authenticity, and the seam that
 * authenticates remains trusted server code. See
 * JOURNEY_ONE_CLOCK_RUNTIME_CANNOT_PROVE, which travels on every advance.
 *
 * @param {object} result a createJourneyOneClock().evaluate() result
 * @param {object} composition a doctorcre-v5-journey-one-clock-projection-inputs.v1
 */
export function assertJourneyOneClockComputedFromComposition(result, composition) {
  if (!isPlainObject(composition) ||
      composition.schema_version !== JOURNEY_ONE_MINIMUM_PROJECTION_INPUTS_SCHEMA ||
      !isPlainObject(composition.projection)) {
    refuse("invalid_shape",
      `binding a computation requires the composer's own ${JOURNEY_ONE_MINIMUM_PROJECTION_INPUTS_SCHEMA} wrapper, which carries the projection that was read from the inventory`,
      { path: "composition" });
  }
  // THE TWO EVIDENCE FIELDS ARE REQUIRED, NOT DEFAULTED. This function is
  // exported and callable outside the loop, where nothing else has checked the
  // wrapper; defaulting them produced a `bound: true` receipt reporting
  // `head_admission_digest: null` for a wrapper that simply omitted it, which
  // reads as "this inventory had no head" rather than "nobody said". A real
  // composition always carries both.
  if (typeof composition.head_admission_digest !== "string" ||
      !SHA256_REF.test(composition.head_admission_digest) ||
      !Number.isSafeInteger(composition.admission_count) || composition.admission_count < 1) {
    refuse("invalid_shape",
      "binding a computation requires the composition's own inventory evidence -- the head admission digest it was read at and how many admissions it carried -- and will not default them into a receipt that then reports an absence nobody stated",
      { path: "composition",
        head_admission_digest: composition.head_admission_digest ?? null,
        admission_count: composition.admission_count ?? null });
  }
  const projection = composition.projection;
  if (projection.schema_version !== JOURNEY_ONE_CLOCK_PROJECTION) {
    refuse("invalid_shape",
      `the composed projection must be ${JOURNEY_ONE_CLOCK_PROJECTION}`,
      { path: "composition.projection.schema_version", actual: projection.schema_version });
  }
  if (!isPlainObject(result) || !isPlainObject(result.state) ||
      !isPlainObject(result.verified_binding)) {
    refuse("invalid_shape",
      "binding a computation requires a kernel result carrying both its state and the verified binding beside it",
      { path: "result" });
  }
  const state = result.state, verified = result.verified_binding;
  if (verified.schema_version !== JOURNEY_ONE_CLOCK_VERIFIED_BINDING) {
    refuse("invalid_shape",
      `result.verified_binding.schema_version must be ${JOURNEY_ONE_CLOCK_VERIFIED_BINDING}`,
      { path: "result.verified_binding.schema_version", actual: verified.schema_version });
  }

  if (verified.tenant !== projection.tenant) {
    bindingMismatch("tenant",
      { composed: projection.tenant, verified: verified.tenant });
  }
  for (const field of ["subject_digest", "candidate_digest", "policy_digest"]) {
    if (verified[field] !== projection.binding?.[field]) {
      bindingMismatch("binding_digests",
        { field, composed: projection.binding?.[field], verified: verified[field] });
    }
  }
  if (!sameInstant(state.evaluated_at, projection.as_of)) {
    bindingMismatch("as_of",
      { composed_as_of: projection.as_of, evaluated_at: state.evaluated_at });
  }
  if (state.current_benchmark_manifest_digest !== projection.benchmark?.manifest_digest) {
    bindingMismatch("benchmark_manifest_digest",
      { composed: projection.benchmark?.manifest_digest,
        computed: state.current_benchmark_manifest_digest });
  }
  if (state.origin_receipt_ttl_policy_ms !== projection.binding?.maximum_minimum_receipt_ttl_ms) {
    bindingMismatch("minimum_receipt_ttl_policy",
      { composed: projection.binding?.maximum_minimum_receipt_ttl_ms,
        recorded: state.origin_receipt_ttl_policy_ms });
  }
  // THE PAUSES THE STATE MIRRORS ARE THE COMPOSED ONES, in the composed order.
  // The kernel copies pause_intervals straight off the projection, so a state
  // mirroring a different pause inventory was computed from a different
  // projection — and the union that produced paused_ms was a different union.
  const pauses = Array.isArray(projection.pauses) ? projection.pauses : null;
  const intervals = Array.isArray(state.pause_intervals) ? state.pause_intervals : null;
  if (pauses === null || intervals === null || pauses.length !== intervals.length ||
      pauses.some((pause, index) => pause?.pause_id !== intervals[index]?.pause_id ||
        (pause?.ends_at ?? null) !== (intervals[index]?.ends_at ?? null))) {
    bindingMismatch("pause_intervals",
      { composed: copy(pauses ?? null), mirrored: copy(intervals ?? null) });
  }

  // THE ORIGIN, AGAINST THE ADMISSIONS THIS RECORD LAYER ACTUALLY READ.
  const admissions = Array.isArray(projection.minimum_history) ? projection.minimum_history : [];
  const matched = admissions.filter(admission =>
    isPlainObject(admission) && admission.receipt !== undefined &&
    digest(admission.receipt) === state.origin_receipt_digest);
  if (matched.length !== 1) {
    refuse("clock_computation_origin_not_in_composed_inventory",
      matched.length === 0
        ? "the origin this kernel selected is not a receipt the composed inventory carries, so this computation was not made from the admitted-minimum ledger this record layer read; nothing is filed for it"
        : "the composed inventory carries the selected origin more than once, which is a shape the kernel itself refuses as duplicate_minimum; nothing is filed for it",
      { fact: "origin_admission",
        origin_receipt_digest: state.origin_receipt_digest,
        matching_admissions: matched.length,
        composed_admission_count: admissions.length,
        composed_receipt_digests: admissions.map(admission =>
          admission?.receipt === undefined ? null : digest(admission.receipt)),
        head_admission_digest: composition.head_admission_digest ?? null });
  }
  if (!sameInstant(state.origin_at, matched[0].receipt.observed_at)) {
    bindingMismatch("origin_admission",
      { origin_at: state.origin_at, admitted_receipt_observed_at: matched[0].receipt.observed_at });
  }

  // THE PROOF, LAST. Everything above named a part; this settles the whole.
  // A valid snapshot for this same accepted scope, origin and instant that
  // differs in its completion, its history, its amendments, its completion
  // expectation or a pause's start instant passes every check above and is
  // refused here — which is the case this clause exists for, and the reason the
  // checks above are diagnostics rather than the proof.
  // HASHED OVER THE SAME NORMALIZATION AS THE KERNEL'S, BY CONSTRUCTION. The
  // kernel hashes `copy(verified.snapshot)`; every value in a real composition is
  // itself a copy, so the two agreed already — accidentally. An `undefined`
  // scalar arriving on one side would emit a key canonicalJson renders and the
  // JSON round trip drops, and the two would then differ. The divergence refuses
  // rather than admits, so this is hardening and not a defect repair; making it
  // structural costs one call.
  const composedDigest = digest(copy(projection));
  const judgedDigest = verified.authenticated_projection_digest;
  if (typeof judgedDigest !== "string" || !SHA256_REF.test(judgedDigest)) {
    refuse("invalid_shape",
      `result.verified_binding.authenticated_projection_digest must be a sha256: reference; a ${JOURNEY_ONE_CLOCK_VERIFIED_BINDING} binding carries the digest of the projection the kernel judged, and without it this seat cannot establish identity at all`,
      { path: "result.verified_binding.authenticated_projection_digest", actual: judgedDigest });
  }
  if (judgedDigest !== composedDigest) {
    refuse("clock_computation_projection_digest_mismatch",
      "this computation was made from a different projection than the one this record layer composed. The two agree on every part named above -- the accepted scope, the instant, the manifest, the sealed policy, the origin admission and the pause ids and ends -- and are not the same snapshot; a valid projection for this program that differs in its completion, history, amendments, completion expectation or a pause's start instant is exactly this case. Nothing is filed for it",
      { fact: JOURNEY_ONE_CLOCK_COMPOSITION_PROOF_FACT,
        composed_projection_digest: composedDigest,
        authenticated_projection_digest: judgedDigest,
        // Said rather than guessed at: this seat holds the composed projection
        // and the DIGEST of the judged one, so it can prove they differ and
        // cannot say in which field. Naming a field would be inventing one.
        differing_fields_unavailable:
          "this seat holds the composed projection and only the digest of the judged one; the parts that differ are not derivable here" });
  }

  return deepFreeze({
    bound: true,
    // The proof and the diagnostics, distinguished rather than listed as equals.
    proof_fact: JOURNEY_ONE_CLOCK_COMPOSITION_PROOF_FACT,
    facts: JOURNEY_ONE_CLOCK_COMPOSITION_BINDING_FACTS.map(fact => fact.id),
    authenticated_projection_digest: judgedDigest,
    composed_projection_digest: composedDigest,
    origin_receipt_digest: state.origin_receipt_digest,
    head_admission_digest: composition.head_admission_digest,
    admission_count: composition.admission_count,
    // Said on the object, because a "bound" flag is exactly the shape a reader
    // would otherwise take for authentication. Identity is not authenticity.
    authenticated_here: false,
  });
}

/**
 * THE LOOP: read the head once, compose against it, evaluate, bind, append.
 *
 * `composer` is createJourneyOneClockProjectionComposer(...), built over a
 * minimum input store carrying the authoritative scope and the accepted minimum
 * policy. `clock` is the object createJourneyOneClock({ verifySnapshot })
 * returns — this file never constructs one and never accepts a verifier as data.
 * `clock_store` is createJourneyOneClockStore({ ..., clock_scope }), constructed
 * with the authoritative scope it writes for. `present_projection` is the trusted
 * presentation seam described in the header. `verifier_ref` NAMES the installed
 * verifier for provenance and is a name, never a proof.
 *
 * ONE SCOPE, CHECKED HERE, BECAUSE THIS IS THE FIRST SEAT THAT HOLDS BOTH. The
 * composer knows the scope its inventory was opened under and the store knows the
 * scope it writes for; until they met, nothing compared them. A disagreement is
 * refused at CONSTRUCTION — before any read, any composition and any evaluation —
 * rather than surfacing later as a scope refusal about a computation that should
 * never have been made.
 */
export function createJourneyOneClockRuntime({
  composer, clock, clock_store, present_projection, verifier_ref } = {}) {
  if (!composer || typeof composer.compose !== "function" ||
      typeof composer.clock_scope_key !== "string") {
    refuse("invalid_shape",
      "an advance needs a projection composer built over the admitted-minimum inventory of one authoritative clock scope",
      { path: "composer" });
  }
  if (!clock || typeof clock.evaluate !== "function") {
    refuse("authenticated_kernel_required",
      "advancing requires a kernel built by createJourneyOneClock({ verifySnapshot }); this seat never constructs one and never accepts a verifier as data",
      { path: "clock" });
  }
  if (!clock_store || typeof clock_store.record !== "function" ||
      typeof clock_store.read !== "function" ||
      typeof clock_store.readClockKeyForScope !== "function") {
    refuse("invalid_shape", "an advance needs a Journey 1 clock store", { path: "clock_store" });
  }
  // THE REQUEST READ IS REQUIRED, not optional, and refusing here rather than
  // degrading is the point: a seat that silently fell back to "read the head and
  // hope" would turn every retry after a lost response into either a refusal
  // that misdescribes it or, if a caller re-keyed, a second legitimate-looking
  // revision on an append-only history. Both shipped stores answer it.
  if (typeof clock_store.readRecordedRevisionForKey !== "function") {
    refuse("clock_runtime_request_read_required",
      "advancing requires a store that can say what one idempotency key already wrote, without opening an append. Without it a retry cannot be told from a second request, and this seat will not guess",
      { invariant: "j1_clock_idempotency_key_binds_its_payload",
        path: "clock_store.readRecordedRevisionForKey" });
  }
  if (typeof clock_store.writer?.actor_id !== "string") {
    refuse("clock_writer_identity_unavailable",
      "advancing requires a store built over an authenticated actor: a recorded request is replayed only to the seat that wrote it, and that comparison needs this store's own derived writer",
      { path: "clock_store.writer" });
  }
  const installed = clock_store.clock_scope ?? null;
  if (!isPlainObject(installed) || typeof installed.clock_scope_key !== "string") {
    refuse("clock_scope_binding_required",
      "an advance writes, and every write is filed under the authoritative clock scope its store was constructed for; a store without one cannot be advanced",
      { invariant: "j1_clock_scope_binds_one_clock", path: "clock_store.clock_scope" });
  }
  if (typeof present_projection !== "function") {
    refuse("clock_presentation_seam_required",
      "advancing requires the trusted presentation that turns a composed projection into the envelope the INSTALLED verifier authenticates. It is supplied at construction by the server code that installed that verifier, never by a request, and it authenticates nothing on its own",
      { path: "present_projection" });
  }
  if (composer.clock_scope_key !== installed.clock_scope_key) {
    refuse("clock_runtime_scope_disagreement",
      "this composer reads the admitted-minimum inventory of one authoritative clock scope and this store writes clocks for another. A projection composed from one program's ledger is never filed as another program's clock",
      { invariant: "j1_clock_scope_binds_one_clock",
        composer_clock_scope_key: composer.clock_scope_key,
        store_clock_scope_key: installed.clock_scope_key });
  }

  return Object.freeze({
    clock_scope_key: installed.clock_scope_key,
    clock_scope_ref: installed.clock_scope_ref ?? null,

    /**
     * Advance this authoritative scope's clock by one revision.
     *
     * `history` and `expected_prior_history_digest` are DELIBERATELY NOT
     * arguments. They are two halves of one fact — which history this revision
     * was computed against — and a caller free to supply them separately is a
     * caller free to evaluate against one history and compare-and-swap onto
     * another. Both are derived HERE, from one read, and the composition is then
     * checked against that prior rather than assumed to carry it.
     *
     * WHICH prior depends on one question asked first: has this idempotency key
     * already written a revision? If not, the prior is this scope's current head
     * and this call may append. If so, the prior is the exact token that recorded
     * revision was written under, the request is re-computed against it, and it
     * is replayed only if the re-computation reproduces the recorded revision
     * exactly — same scope, same writing seat, same state. That is what makes a
     * retry after a lost response a retry rather than a second request, and it
     * appends nothing.
     *
     * The remaining inventories have no producer in this repository and are
     * REQUIRED with no default, exactly as the composer requires them: an omitted
     * `pauses` is a caller who has not said whether there are any, and a default
     * of [] would silently claim there are none.
     */
    async advance(args = {}) {
      if (!isPlainObject(args)) {
        refuse("invalid_shape", "advance takes exactly its declared fields", { path: "advance" });
      }
      // THE ARGUMENT KEYS FIRST, on A00's own guard, so a key that reads like a
      // caller asserting the authority this seat consumes — `verified`,
      // `authority_granted`, `clock_started`, `issued` — meets a NAMED refusal
      // ahead of the generic closed-shape one. Silently dropped is how a caller
      // comes to believe it was honoured.
      //
      // THE KEY NAMES ONLY, AND DELIBERATELY NOT THE VALUES UNDERNEATH. The
      // declared inventories legitimately carry `accepted_at` and
      // `accepted_by_identity`: an amendment IS the record of an act a verified
      // partner performed, and the kernel is what judges it. Walking into them
      // would refuse every legitimate amendment for using the vocabulary of the
      // thing it records. What this guard is for is a claim smuggled BESIDE the
      // declared fields, and that is a top-level key.
      assertNoSelfAssertedAuthority(
        Object.fromEntries(Object.keys(args).map(key => [key, null])), "advance");
      for (const field of JOURNEY_ONE_CLOCK_DERIVED_NOT_SUPPLIED_FIELDS) {
        if (Object.hasOwn(args, field)) {
          refuse("clock_runtime_history_is_derived",
            `${field} is derived by this rail and is never supplied: from this scope's head on a first attempt, and from the exact prior a recorded request was written under on a retry. The history a revision is computed against and the prior it appends onto are one fact; two arguments for it are two chances to disagree, and the disagreement is a revision evaluated against a history that is not the one it swaps onto`,
            { invariant: "j1_clock_exact_prior_history_digest", path: `advance.${field}` });
        }
      }
      closedShape(args, JOURNEY_ONE_CLOCK_ADVANCE_FIELDS, "advance");

      // 0. DID THIS EXACT REQUEST ALREADY LAND? Asked FIRST, before the head is
      //    read and before anything is composed, because the answer decides
      //    WHICH prior this advance is about. A read only: it opens no append,
      //    takes no lock and decides nothing on its own.
      const recorded = await clock_store.readRecordedRevisionForKey(args.idempotency_key);
      const retry = recorded?.exists === true;
      if (retry) {
        // THE RECORDED REVISION MUST BE THIS SEAT'S TO REPLAY. A stored success
        // is never handed back for another program or another writer: the scope
        // it was filed under must be the scope this store writes for, and the
        // seat that wrote it must be the seat asking.
        if (recorded.clock_scope_key !== installed.clock_scope_key) {
          refuse("clock_runtime_replay_scope_mismatch",
            "this idempotency key already wrote a revision for another authoritative clock scope, or for a clock with no scope binding at all. A recorded success is replayed only to the scope that produced it",
            { invariant: "j1_clock_scope_binds_one_clock",
              idempotency_key: args.idempotency_key,
              store_clock_scope_key: installed.clock_scope_key,
              recorded_clock_scope_key: recorded.clock_scope_key,
              recorded_clock_scope_bound: recorded.clock_scope_bound });
        }
        const writer = clock_store.writer?.actor_id ?? null;
        if (writer === null || recorded.written_by_actor_id !== writer) {
          refuse("clock_runtime_replay_actor_mismatch",
            "this idempotency key already wrote a revision as another actor. Both classes were derived from a live actor at their own write time and neither is read back to decide anything; a key is replayed only to the seat that used it",
            { idempotency_key: args.idempotency_key,
              recorded_written_by_actor_id: recorded.written_by_actor_id,
              advancing_as_actor_id: writer });
        }
      }

      // 1. THE PRIOR THIS ADVANCE IS ABOUT.
      //    FIRST ATTEMPT: one read of the head. A scope that holds no clock reads
      //    null and this advance is a creation; a scope that holds one reads its
      //    exact head, and both the composed history and the compare-and-swap
      //    token come from that single read.
      //    RETRY: the EXACT prior the recorded revision was written under, not
      //    the head. The head has moved — that is what a lost response looks like
      //    from here — and composing against it would compute a different
      //    revision and then compare it to the recorded one, which is how an
      //    honest retry came to be reported as key reuse.
      let head = null;
      let priorDigest = null;
      let priorHistory = null;
      if (retry) {
        priorDigest = recorded.expected_prior_history_digest;
        priorHistory = recorded.prior_history;
      } else {
        const boundClockForScope = await clock_store.readClockKeyForScope();
        const boundKey = boundClockForScope?.clock_key ?? null;
        if (boundKey !== null) {
          const readback = await clock_store.read(boundKey);
          if (readback?.exists !== true) {
            refuse("clock_runtime_bound_clock_unreadable",
              "this authoritative scope names a clock the record layer cannot produce a history for. Composing against a null history here would present a fresh origin for a scope that already holds a clock, which is a reset wearing a new address; the advance refuses instead",
              { invariant: "j1_clock_scope_binds_one_clock",
                clock_scope_key: installed.clock_scope_key, clock_key: boundKey });
          }
          head = readback;
          priorDigest = readback.history_digest;
          priorHistory = readback.history;
        }
      }

      // 2. COMPOSE AGAINST THAT EXACT PRIOR — this scope's head on a first
      //    attempt, the recorded request's own prior on a retry. The composer's
      //    own refusals travel unchanged on a first attempt: a scope with no
      //    inventory refuses with `minimum_inventory_unavailable`, which is the
      //    state of this repository.
      //    ON A RETRY A COMPOSER REFUSAL IS RE-RAISED WITH THE RECORDED REVISION
      //    ATTACHED. Replay works by re-composing, so it needs the inventory to
      //    still admit that composition: an admission accepted after the request
      //    landed, whose admitted_at postdates the request's own as_of, makes the
      //    composer refuse `projection_as_of_precedes_admission`. Bare, that
      //    error is indistinguishable from a first attempt with a bad as_of — and
      //    a caller reading it as "my request failed" re-keys and appends a
      //    second legitimate-looking revision, which is the exact hazard the
      //    layer below separates by name. So the refusal carries what this seat
      //    already knows: the request LANDED, here is the revision, here is why
      //    it cannot be re-established. Nothing about failing closed changes.
      let composition;
      try {
        composition = await composer.compose({
          as_of: args.as_of,
          completion: args.completion,
          completion_expectation: args.completion_expectation,
          pauses: args.pauses,
          amendments: args.amendments,
          history: priorHistory === null ? null : copy(priorHistory),
        });
      } catch (error) {
        if (!retry) throw error;
        refuse("clock_runtime_recorded_request_not_recomputable",
          "this request ALREADY LANDED and its revision is recorded; it cannot be re-computed here, so this call can neither replay it nor append. The recorded revision is unchanged and readable, and re-sending this request under a NEW key would file a second revision of work that is already filed",
          { invariant: "j1_clock_idempotency_key_binds_its_payload",
            idempotency_key: args.idempotency_key,
            request_landed: true,
            appended: false,
            recorded_clock_key: recorded.clock_key,
            recorded_history_digest: recorded.history_digest,
            recorded_revision_ordinal: recorded.revision_ordinal,
            recorded_expected_prior_history_digest: recorded.expected_prior_history_digest,
            recorded_at: recorded.recorded_at,
            // The refusal underneath, by its own name, so the reason is readable
            // without the caller having to guess which rail produced it.
            cause_code: error?.code ?? null,
            cause_name: error?.name ?? null,
            cause_message: typeof error?.message === "string" ? error.message : null });
      }
      // The scope is compared again on the composition itself, because the
      // construction-time check was against the composer OBJECT and this is the
      // artifact that will be filed.
      if (composition.clock_scope_key !== installed.clock_scope_key) {
        refuse("clock_runtime_scope_disagreement",
          "the composition names an authoritative clock scope that is not the one this store writes for",
          { invariant: "j1_clock_scope_binds_one_clock",
            composed_clock_scope_key: composition.clock_scope_key,
            store_clock_scope_key: installed.clock_scope_key });
      }
      // AND THE HISTORY IT WAS COMPOSED AGAINST IS THE PRIOR THIS ADVANCE NAMES,
      // for exactly the reason the scope is re-checked: the check above was
      // against the composer OBJECT, and this is the artifact that will be
      // filed. "Composed against the revision the append names" held only
      // insofar as the composer forwarded the argument it was handed; here it is
      // structural. The digest carried on a composed history is not believed —
      // the composer read it through the kernel's own readJourneyOneClockHistory,
      // which recomputes it — so comparing it is comparing a verified value.
      const composedPriorDigest = composition.projection?.history === null ||
        composition.projection?.history === undefined
        ? null : composition.projection.history.history_digest ?? null;
      if (composedPriorDigest !== priorDigest) {
        refuse("clock_runtime_composed_history_is_not_the_prior",
          "the projection was composed against a history that is not the prior this advance appends onto. The history a revision is computed against and the prior it swaps onto are one fact, and this seat derives both from one read -- of this scope's head on a first attempt, of the recorded request's own prior on a retry; a composition that carries a different one is not the artifact this advance asked for",
          { invariant: "j1_clock_exact_prior_history_digest",
            expected_prior_history_digest: priorDigest,
            composed_history_digest: composedPriorDigest,
            replaying_recorded_request: retry });
      }

      // 3. PRESENT IT. A copy, so the presentation cannot edit the composition
      //    this advance is about to bind the computation against.
      const envelope = present_projection(copy(composition.projection));
      if (envelope === undefined || envelope === null) {
        refuse("clock_presentation_failed",
          "the trusted presentation returned no envelope for the composed projection, so there is nothing for the installed verifier to authenticate",
          { path: "present_projection" });
      }

      // 4. EVALUATE, BIND AND APPEND — in the recorder, which is the one home for
      //    that order. The binding runs through its pre-write seam, so a
      //    computation that is not this exact composition — including a second
      //    VALID projection for the same accepted scope and origin — refuses
      //    with no journal read, no compare-and-swap and no row.
      let binding = null;
      const recorder = createJourneyOneClockRecorder({
        clock, store: clock_store, verifier_ref,
        assert_before_write: result => {
          binding = assertJourneyOneClockComputedFromComposition(result, composition);
        },
      });

      // 5. WRITE, OR REPLAY WHAT THIS REQUEST ALREADY WROTE.
      //
      // ON A RETRY NOTHING IS APPENDED AND NOTHING IS SKIPPED. The recorder's
      // evaluate-check sequence runs in full — the kernel, the scope derived
      // from its verified binding, and the composition identity proof — and then
      // the RE-COMPUTED state has to reproduce the recorded history digest
      // exactly. That is the request binding: a stored success is never handed
      // back because a key matched, only because the same inputs, against the
      // same prior, in the same scope, by the same seat, computed the same
      // revision. A key presented with a different intent produces a different
      // state and meets the store's own `clock_idempotency_key_reused` — the
      // same name for the same fact, rather than a second vocabulary for it.
      let outcome;
      if (retry) {
        const { result, clock_scope_key } = recorder.evaluateAndBind(envelope);
        if (result.state.history_digest !== recorded.history_digest) {
          refuse("clock_idempotency_key_reused",
            `idempotency key ${args.idempotency_key} already recorded a different Journey 1 clock revision. Re-computing this request against the exact prior that key was written under produced another state, so this is a second request wearing the first one's key and not a retry of it`,
            { invariant: "j1_clock_idempotency_key_binds_its_payload",
              idempotency_key: args.idempotency_key,
              recorded: { clock_key: recorded.clock_key,
                history_digest: recorded.history_digest,
                expected_prior_history_digest: recorded.expected_prior_history_digest,
                revision_ordinal: recorded.revision_ordinal },
              recomputed: { clock_key: journeyOneClockKeyForState(result.state),
                history_digest: result.state.history_digest,
                expected_prior_history_digest: priorDigest } });
        }
        // The digest covers the whole state, so an equal digest is an equal
        // origin and therefore an equal clock; deriving it anyway costs one hash
        // and makes the receipt's clock_key the RE-COMPUTED one rather than a
        // string copied off a stored row.
        const derivedClockKey = journeyOneClockKeyForState(result.state);
        if (derivedClockKey !== recorded.clock_key) {
          refuse("clock_identity_mismatch",
            "the re-computed revision derives a different clock than the one this idempotency key wrote",
            { invariant: "j1_clock_identity_derived_from_origin",
              recorded_clock_key: recorded.clock_key, derived_clock_key: derivedClockKey });
        }
        outcome = { clock_scope_key, clock_key: derivedClockKey,
          revision_ordinal: recorded.revision_ordinal,
          history_digest: recorded.history_digest,
          expected_prior_history_digest: recorded.expected_prior_history_digest,
          recorded_at: recorded.recorded_at, replayed: true, appended: false,
          kernel_verdict: {
            status: result.state.status,
            deadline_success: result.deadline_success,
            replan_required: result.replan_required,
            completion_currently_usable: result.completion_currently_usable,
            completion_observed_within_deadline: result.completion_observed_within_deadline,
            missing_evidence_miss_recorded: result.missing_evidence_miss_recorded,
            benchmark_amended: result.benchmark_amended,
            deadline_resolution: result.deadline_resolution,
            unresolved_reason: result.unresolved_reason,
          } };
      } else {
        const written = await recorder.evaluateAndRecord({
          envelope,
          expected_prior_history_digest: priorDigest,
          idempotency_key: args.idempotency_key,
          clock_ref: args.clock_ref,
        });
        outcome = { clock_scope_key: written.clock_scope_key, clock_key: written.clock_key,
          revision_ordinal: written.revision_ordinal, history_digest: written.history_digest,
          expected_prior_history_digest: written.expected_prior_history_digest,
          recorded_at: written.recorded_at, replayed: written.replayed === true,
          // The store's OWN replay branch — one head, two racing callers with one
          // key — is a read of a stored row too, and appends nothing either.
          appended: written.replayed !== true,
          kernel_verdict: copy(written.kernel_verdict) };
      }

      return closedAdvanceReceipt({
        ok: true,
        schema_version: JOURNEY_ONE_CLOCK_ADVANCE_RECEIPT_SCHEMA,
        runtime_schema_version: JOURNEY_ONE_CLOCK_RUNTIME_SCHEMA,
        clock_scope_key: outcome.clock_scope_key,
        clock_scope_ref: installed.clock_scope_ref ?? null,
        clock_key: outcome.clock_key,
        revision_ordinal: outcome.revision_ordinal,
        history_digest: outcome.history_digest,
        expected_prior_history_digest: outcome.expected_prior_history_digest,
        // Whether the revision this receipt describes CREATED the clock, read off
        // the prior it was written under rather than off a caller's intent: a
        // null prior is a creation by the store's own compare-and-swap rule. On a
        // replay this is a fact about the RECORDED revision, which is what the
        // whole receipt is about.
        created_clock: outcome.expected_prior_history_digest === null,
        // TWO DIFFERENT FACTS, KEPT APART. `replayed` says this receipt describes
        // a revision that already existed; `appended` says whether THIS CALL
        // wrote one. A replay describes the revision that request wrote, not the
        // current head — the head may have moved on since.
        replayed: outcome.replayed,
        appended: outcome.appended,
        replayed_recorded_request: retry,
        recorded_at: outcome.recorded_at,
        // The exact inputs this revision was computed from, reported rather than
        // implied. The head admission digest is EVIDENCE ON THIS RESULT and is
        // not stored: doctorcre-v5-journey-one-clock.v2 has no field for it, and
        // adding one would change every history_digest.
        composed_from: {
          as_of: composition.projection.as_of,
          head_admission_digest: composition.head_admission_digest,
          admission_count: composition.admission_count,
          prior_history_digest: priorDigest,
          // ONE MEANING ON BOTH PATHS: the ordinal of the revision this one was
          // written onto, and null only when there was none. On the append path
          // that is the head this call read; on a replay it is the ordinal the
          // store derived for the recorded revision's own prior — from the row it
          // rebuilt, not from arithmetic here.
          prior_revision_ordinal: retry
            ? recorded.prior_revision_ordinal ?? null
            : (head === null ? null : head.head_revision_ordinal),
          // Where that prior came from: this scope's current head, or the exact
          // token the recorded request was written under.
          prior_source: retry ? "recorded_request_prior" : "current_scope_head",
        },
        composition_binding: binding,
        // DERIVED FROM THE TWO KEYS, not asserted. It is earned on both paths —
        // the recorder refuses before either arm returns — but a literal `true`
        // is the shape a later refactor falsifies without anything noticing.
        clock_scope_matches_verified_binding:
          outcome.clock_scope_key === installed.clock_scope_key,
        // The kernel's own verdict, passed through unchanged and unre-decided.
        // On a replay it is the verdict of the RE-COMPUTATION, which had to
        // reproduce the recorded revision exactly to get here.
        kernel_verdict: copy(outcome.kernel_verdict),
        // FALSE on both paths, and for two different reasons: the write this
        // names has happened, or it happened on an earlier call.
        durable_history_write_required: false,
        deadline_accepted_by_record_layer: false,
        authenticated_projection_verified_here: false,
        benchmark_envelope: copy(composition.benchmark_envelope),
        cannot_prove: [...JOURNEY_ONE_CLOCK_RUNTIME_CANNOT_PROVE],
        record_layer_cannot_prove: [...JOURNEY_ONE_CLOCK_STORE_CANNOT_PROVE],
        input_store_cannot_prove: [...JOURNEY_ONE_MINIMUM_INPUT_STORE_CANNOT_PROVE],
        // TRUTHFUL PER CALL. `effects` describes what THIS call did, not what the
        // revision it names once did: a call that appended nothing reports no
        // database write and no appended history.
        effects: outcome.appended
          ? JOURNEY_ONE_CLOCK_RECORD_EFFECTS : JOURNEY_ONE_CLOCK_REPLAY_EFFECTS,
      });
    },
  });
}

/**
 * A reader is entitled to know what this seat joins, what it refuses to be, and
 * why it cannot run in this repository. Zero effect.
 */
export function journeyOneClockRuntimeIntegrationRequirements() {
  return deepFreeze({
    schema_version: JOURNEY_ONE_CLOCK_RUNTIME_INTEGRATION_SCHEMA,
    runtime_schema_version: JOURNEY_ONE_CLOCK_RUNTIME_SCHEMA,
    projection_schema_version: JOURNEY_ONE_CLOCK_PROJECTION,
    composition_schema_version: JOURNEY_ONE_MINIMUM_PROJECTION_INPUTS_SCHEMA,
    loop_implemented: true,
    loop_notes: [
      "On a first attempt the head is read once and both the composed history and the compare-and-swap prior are derived from that one read; neither is an argument, and supplying either is refused by name. The composition is then re-checked against that prior, because the artifact filed is what matters and not what the composer was handed.",
      "The computation filed IS the projection this record layer composed: the kernel's authenticated_projection_digest, the canonical digest of the whole snapshot its verifier returned, must equal the digest of the composed projection. Seven named field checks run first as diagnostics and are not the proof -- every projection for one program shares the accepted scope, so a second valid snapshot differing only in its completion, history, amendments or a pause's start instant passes all seven.",
      "The origin the kernel selected must also be a receipt the composed minimum_history carries, exactly once, at that receipt's own observed_at. The proof ENTAILS it -- the kernel selects out of that same list -- and it is kept so the common divergence is named rather than reported as two unequal hashes.",
      "AN ADVANCE IS IDEMPOTENT ACROSS SEQUENTIAL RETRIES, and the store's own guards do the work. Before anything is composed, the key's recorded revision is read; if one exists it must be for this authoritative scope and this writing seat, the request is re-composed against the EXACT prior that revision was written under rather than against the moved head, evaluated, bound, and required to reproduce the recorded history digest. A match replays it and appends nothing; a different intent under one key meets the store's own clock_idempotency_key_reused.",
      "The evaluate-check-record order has ONE home, in createJourneyOneClockRecorder. A replay runs its evaluateAndBind -- the same kernel call, scope derivation and pre-write assertion, with the write left off -- so no second copy of that order exists here.",
      "One authoritative clock scope holds the inventory and the clock, and a composer and store bound to different scopes refuse when the runtime is constructed rather than after an evaluation.",
      "The kernel is taken ALREADY CONSTRUCTED. No verifySnapshot callback, no envelope carrying a verification claim and no boolean is accepted anywhere in this file.",
    ],
    binding_facts: JOURNEY_ONE_CLOCK_COMPOSITION_BINDING_FACTS.map(fact => ({ ...fact })),
    /** Which of those facts is the proof; the others describe, they do not establish. */
    proof_fact: JOURNEY_ONE_CLOCK_COMPOSITION_PROOF_FACT,
    verified_binding_schema_version: JOURNEY_ONE_CLOCK_VERIFIED_BINDING,
    derived_not_supplied: [...JOURNEY_ONE_CLOCK_DERIVED_NOT_SUPPLIED_FIELDS],
    advance_fields: [...JOURNEY_ONE_CLOCK_ADVANCE_FIELDS],
    /**
     * THE LOOP CANNOT RUN HERE, and the reason is upstream of everything this
     * file does. Stated as the absence it is: no receipt can be admitted, so no
     * inventory exists, so compose() refuses before a kernel is reached.
     */
    runnable_in_this_repository: false,
    /**
     * PRECISELY WHAT "not runnable" MEANS, because the suites for this file DO
     * run the whole loop: the loop is executable against FIXTURE receipts driven
     * through the real rails, and there is no ISSUED receipt for it to run
     * against outside a test. Those are different findings and rule 88e9b5eb
     * says not to report them as one.
     */
    not_runnable_means: "no ISSUED foundation-assurance-minimum receipt exists here, so no production inventory can be opened and no production advance can be made. The suites drive this loop end to end with fixture receipts through the same rails; what they cannot manufacture is an issued artifact, an authenticated projection reader, or a started clock.",
    blocked_by: [
      "binding:journey-one-minimum-admitted-receipt-issuance — benchmark-minimum.v5.js PROPOSES a foundation-assurance-minimum receipt and marks it proposed_not_issued. With no issuance adapter there is no ISSUED receipt to admit, so no production inventory can be opened and compose() refuses with minimum_inventory_unavailable; a fixture receipt admitted by a suite is a test artifact and never that.",
      "binding:journey-one-clock-authenticated-projection — nothing in this repository builds the trusted projection from the record layer or installs the verifier the kernel demands, so no kernel can be constructed for a real projection.",
      "THE READER'S EXACT RETURN CONTRACT, which this loop imposes and which no code here can enforce on it: for a presented composition the installed verifier must return the CANONICALLY IDENTICAL snapshot -- the same value, not an equivalent one. Key order is free, because the digest is canonical; a differently spelled instant, a re-serialized history, a re-ordered minimum_history or an added field is a different projection, and a verifier that independently RE-DERIVES the projection rather than resolving the presented one will make this loop refuse every legitimate advance with clock_computation_projection_digest_mismatch and no field-level diagnostic. A reader that rebuilds from the record layer must therefore resolve the presented envelope to the composition it was presented with, or the two seats must share one composer.",
      "the terminus producer for step:j1-kernel-production-outcome, so completion and completion_expectation have no authenticated source here.",
      "the three candidate SQL files, which are candidate source in ops/ rather than numbered migrations, so this repository declares no durable journal for either rail to advance against. That is what the SOURCE says; whether any database has them is not something this module, or any read of the checked-in schema snapshot, can answer.",
    ],
    explicitly_refused: [
      "constructing or accepting a verifySnapshot callback, an envelope carrying { verified: true }, or any self-hashed attestation",
      "a caller-supplied history or expected_prior_history_digest",
      "a caller-chosen clock scope, accepted minimum policy or accepted source binding",
      "issuing, minting or admitting any receipt, and writing to the admitted-minimum inventory at all",
      "a second evaluate-check-record sequence, a second history validator, a second receipt validator and a second scope derivation",
      "a public verb or a tool registration: there are none in this file",
    ],
    starts_no_clock:
      "An advance records a computation. It admits no receipt, accepts no deadline and DECIDES no start: the kernel's own origin selection is what starts a clock, and a first advance is the write that files one -- created_clock: true says so on the receipt. NO CLOCK HAS BEEN STARTED in this repository, which is an absence of evidence here and not a proof of absence about the record as a whole.",
    cannot_prove: [...JOURNEY_ONE_CLOCK_RUNTIME_CANNOT_PROVE],
    clock_started: false,
    effects: V5_NO_EFFECTS,
  });
}
