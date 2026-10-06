// DoctorCRE v5 slice V5-M01, next source integration: THE DURABLE APPEND-ONLY
// ADMITTED-MINIMUM INPUT HISTORY, and the reader that assembles the M01
// projection's `minimum_history` from the stored rows.
//
// journey-one-clock-store.v5.js already stores what the kernel COMPUTES. This
// file stores what the kernel READS. Its own requirement descriptor names the
// gap verbatim: "Land the admitted-minimum ledger the projection's
// minimum_history is read from, stamping admitted_at from the same trusted
// clock and asserting it at write time. A single instant of skew between
// admitted_at and a receipt's observed_at is fatal to the whole projection and
// an append-only inventory cannot shed it." This is that ledger.
//
// ---------------------------------------------------------------------------
// WHY THE ADMISSION INSTANT IS THE WHOLE DESIGN.
//
// journey-one-clock.v5.js selects the origin by ADMISSION ORDER, not by
// observation order, and says why: "An append-only inventory can only ever gain
// LATER admissions, so a receipt observed earlier but admitted later ... is an
// ordinary fact that appends honestly. Selecting on observed_at instead would
// let that ordinary admission rebase a running clock, which is not a repairable
// state." That guarantee is not a property of the kernel. It is a property THIS
// FILE has to actually provide:
//   * `admitted_at` is stamped by the record layer's own trusted clock and is
//     never a caller field. There is no argument for it and no default for one.
//   * The durable half derives it from `now()`, which is the TRANSACTION
//     timestamp and is therefore the SAME reading the append function re-derives
//     and asserts against. One clock, one value, compared at write time — not
//     two readings hoped to be close.
//   * The ledger is stored IN THE KERNEL'S OWN SELECTION ORDER and an append
//     only ever extends it: `admitted_at` is non-decreasing and, within one
//     `admitted_at` group, the receipt digest strictly increases. The kernel
//     orders candidates by (admitted_at, receipt_digest) and takes the first
//     ELIGIBLE one, so a row that sorts after every stored row cannot be
//     preferred to any of them and cannot take the origin from the row that
//     already has it. That is what makes "the first origin is never replaced" a
//     structural fact rather than a promise.
//     COMPARING AGAINST THE FIRST LEDGER ROW WOULD NOT DO IT, and an earlier
//     revision of this file did exactly that. The first row may be an
//     inadmissible attempt the kernel skips: with a failed attempt at T0 and a
//     passing one at T1, the origin is the T1 row, and a second T1 row with a
//     lower digest ties nothing at T0 while displacing it. The next evaluation
//     against the retained history then refuses `origin_reset_or_rebase` and the
//     clock is unreadable rather than wrong. This rail does not compute
//     eligibility — that is the kernel's — it just refuses to store a row out of
//     the order the kernel selects in.
//
// ---------------------------------------------------------------------------
// WHAT IS REFUSED AT ADMISSION, AND WHAT IS STORED — THE DIVISION IS THE
// KERNEL'S OWN, NOT A NEW POLICY.
//
// The kernel splits receipt refusals in two. INADMISSIBLE
// (`nonpassing_receipt`, `receipt_not_current`) is "an ordinary fact of the
// ledger: it can never become the origin, and it must not make the clock
// permanently uncomputable". Everything else is FATAL — one such row makes
// EVERY later evaluation of the inventory refuse, and an append-only inventory
// "is authoritative and cannot shed it".
//
//   * FATAL-class facts are refused HERE, at write time, by name. The
//     enumeration is exact rather than illustrative: a receipt from another
//     producer step, gate, role, oracle, oracle version, evidence scope or
//     subject environment; a receipt whose subject/candidate/policy digests are
//     not the accepted scope's; a receipt whose environment manifest digest is
//     not the sealed accepted one; a window inverted or longer than the sealed
//     accepted TTL policy; an observation instant AFTER the server admission
//     instant; and the SHAPE facts A00's seam validator leaves open — the
//     `safe:` and `session:` prefixes, the three-field identity seats, the
//     fixture-set digest format and the comparator bounds. TYPE AND UNIT ARE
//     PART OF THOSE FACTS: each field must be a JSON string, not a number
//     rendered as one, and the comparator's 5-to-300 bound is counted in UTF-16
//     code units, which is what the kernel counts and what the record layer's
//     mirror of these clauses counts too.
//   * INADMISSIBLE-class facts are STORED. A failed attempt and a receipt whose
//     window later lapses are both real history. This rail has no filter and no
//     discard path: nothing is dropped, and the kernel decides what may be an
//     origin.
//
// WHAT IT DELIBERATELY DOES NOT RE-JUDGE, AND THIS IS NOW THE WHOLE OF IT. The
// INDEPENDENCE of the three seats inside a receipt — that the subject's maker
// shares neither actor nor session with the producer or the evaluator — is
// enforced where the receipt is PROPOSED, in benchmark-minimum.v5.js's join, and
// refused by name by the kernel as `self_attestation`. This rail checks the
// seats' SHAPE and never their relationship to one another: an independence rule
// here would be a second home for seat authority, and the one that matters is
// the join's, which is also the only one holding the live identities. It is
// fatal-and-unshedable like the rest, so it is disclosed rather than implied —
// see JOURNEY_ONE_MINIMUM_INPUT_STORE_CANNOT_PROVE, which names the consequence.
//
// ---------------------------------------------------------------------------
// REUSED READ-ONLY, NEVER RESTATED:
//   * benchmark-minimum.v5.js — journeyOneClockMinimumReceiptView is the ONE
//     validator of "is this an M01-readable r7-exact minimum receipt", and
//     digest(view) is by its own contract the exact origin_receipt_digest the
//     kernel records. validateBenchmarkManifest is the ONE validator of "is this
//     an accepted benchmark-manifest.v1", and the composer's benchmark
//     acceptance envelope is DERIVED through it rather than restated beside it —
//     shape only, never authentication, as that derivation says at length. The
//     gate, step, role, oracle and scope constants are A00's and are imported,
//     not copied.
//   * journey-one-clock.v5.js — the projection schema, the accepted deadline
//     contract, and readJourneyOneClockHistory for a supplied history.
//   * journey-one-clock-store.v5.js — journeyOneClockScopeBinding (ONE scope
//     derivation, one domain tag, one preimage; the label is not identity) and
//     deriveJourneyOneClockWriter (ONE writer derivation, from the live actor).
//   * benchmark-acceptance-store.v5.js — assertNoSelfAssertedAuthority.
//   * ops.j1_clock_scope_digest, ops.portfolio_canonical_json and
//     ops.portfolio_writer_actor_id on the SQL side.
//
// ---------------------------------------------------------------------------
// WHAT REMAINS INTEGRATION WORK, named rather than implied. NOTHING HERE MINTS
// ANY OF IT, and the public admission verb stays refused because of it:
//   * No live producer of an admitted foundation-assurance-minimum receipt.
//     A00's join PROPOSES one (`receipt_state: "proposed_not_issued"`); no
//     issuance adapter exists, so no genuine artifact can reach this rail.
//   * No live producer of a journey-one-kernel-production terminus receipt, so
//     a composed projection's `completion` is always null in this repository.
//   * The GATE ZERO outcome and the BENCHMARK COVERAGE fact the join consumed
//     are not carried across this seam. consumer-gate-receipt.v1 has no field
//     for either and this rail stores neither: inventing a column for them would
//     be minting the binding, and A00 says plainly that "nothing here asserts
//     that they reached the clock". It stays an explicit blocker.
//   * Applying ops/journey-one-clock-input-store.candidate.sql as a numbered
//     migration, and running mcp-server/test/journey-one-clock-input-store-postgres.sql.
//     Neither has been executed.
//   * Registering the verbs below. They are exported and wired to nothing.

import { digest } from "./artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "./identity.js";
import { V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import { assertNoSelfAssertedAuthority } from "./benchmark-acceptance-store.v5.js";
import {
  AUTHENTICATED_RECEIPT_IDENTITY_SCHEMA, BENCHMARK_ACCEPTANCE_ENVELOPE_FIELDS,
  BENCHMARK_MANIFEST_SCHEMA, CONSUMER_GATE_RECEIPT_SCHEMA, MINIMUM_EVIDENCE_SCOPE,
  MINIMUM_GATE_ID, MINIMUM_ORACLE_REF, MINIMUM_ORACLE_VERSION, MINIMUM_PRODUCER_ROLE,
  MINIMUM_STEP_REF, MINIMUM_SUBJECT_ENVIRONMENT, journeyOneClockMinimumReceiptView,
  validateBenchmarkManifest,
} from "./benchmark-minimum.v5.js";
import {
  JOURNEY_ONE_CLOCK_PROJECTION, JOURNEY_ONE_DEADLINE_CONTRACT, readJourneyOneClockHistory,
} from "./journey-one-clock.v5.js";
import {
  deriveJourneyOneClockWriter, journeyOneClockScopeBinding,
} from "./journey-one-clock-store.v5.js";

/** Module-local adapter schemas. NOT r7 schemas; r7 declares no storage shape. */
export const JOURNEY_ONE_MINIMUM_INPUT_STORE_SCHEMA =
  "doctorcre-v5-journey-one-minimum-input-store.v1";
export const JOURNEY_ONE_MINIMUM_INVENTORY_READBACK_SCHEMA =
  "doctorcre-v5-journey-one-minimum-inventory-readback.v1";
export const JOURNEY_ONE_MINIMUM_INPUT_INTEGRATION_SCHEMA =
  "doctorcre-v5-journey-one-minimum-input-store-integration.v1";
/**
 * The wrapper a composed projection travels in. IT IS NOT THE PROJECTION AND IT
 * IS NOT A VERIFIED SNAPSHOT: the kernel's verifySnapshot is trusted server code
 * and this rail never becomes it. The exact eleven-field projection object sits
 * inside as `projection`, so nothing can mistake this wrapper for the thing the
 * kernel closed-shape checks.
 */
export const JOURNEY_ONE_MINIMUM_PROJECTION_INPUTS_SCHEMA =
  "doctorcre-v5-journey-one-clock-projection-inputs.v1";

/**
 * The domain tag one admission's chain link is derived under. Each link hashes
 * the previous link, so the chain is the inventory: a row cannot be removed,
 * reordered or re-dated without breaking every link after it.
 */
export const JOURNEY_ONE_MINIMUM_ADMISSION_DOMAIN_TAG = "doctorcre:j1-minimum-admission:v1";

/**
 * The exact fields one admission's link is derived from, C-sorted.
 *
 * THE SEALED POLICY AND ENVIRONMENT ARE IN THE PREIMAGE ON PURPOSE. They are the
 * accepted source bindings the receipt was admitted under, and the kernel
 * refuses a later projection that changes the TTL policy (`origin_ttl_policy_
 * changed`). Hashing them into every link means neither can be revised
 * retroactively without the whole chain refusing to rebuild.
 */
export const JOURNEY_ONE_MINIMUM_ADMISSION_FIELDS = Object.freeze([
  "admitted_at", "clock_scope_key", "minimum_environment_manifest_digest",
  "minimum_receipt_ttl_policy_ms", "previous_admission_digest", "receipt_digest", "tenant",
]);

/**
 * The accepted minimum-input policy an inventory is OPENED under and every later
 * admission is written under. A construction-time trusted binding, exactly as
 * the scope is, and sealed on the inventory at the first admission.
 */
export const JOURNEY_ONE_MINIMUM_ACCEPTED_POLICY_FIELDS = Object.freeze([
  "maximum_minimum_receipt_ttl_ms", "minimum_environment_manifest_digest",
]);

/**
 * The remaining accepted source bindings a full projection needs, which this
 * rail does not store and will not invent. Supplied to the composer at
 * CONSTRUCTION by trusted server code — never per request — because each one is
 * the record of an act another authority performed.
 */
export const JOURNEY_ONE_MINIMUM_ACCEPTED_SOURCE_FIELDS = Object.freeze([
  "benchmark_accepted_at", "benchmark_accepted_by_identity", "benchmark_manifest_digest",
  "maximum_completion_receipt_ttl_ms", "production_environment_manifest_digest",
]);

/**
 * THE SPLIT INSIDE THAT SET, AND WHY IT IS NOT COSMETIC.
 *
 * Three of the five are the ACCEPTANCE ENVELOPE of benchmark-manifest.v1 —
 * A00's own BENCHMARK_ACCEPTANCE_ENVELOPE_FIELDS minus `status`, renamed with
 * the `benchmark_` prefix M01's projection uses. They describe an artifact that
 * exists, so they can be DERIVED from it rather than restated beside it, and
 * journeyOneMinimumBenchmarkAcceptedSources below is that derivation.
 *
 * The other two appear NOWHERE on benchmark-manifest.v1. Deriving them would be
 * minting a binding rather than carrying one, so they stay exactly what they
 * were: accepted-policy inputs handed over at construction by trusted server
 * code, and no more audited than the code that hands them over.
 */
export const JOURNEY_ONE_MINIMUM_BENCHMARK_DERIVED_SOURCE_FIELDS = Object.freeze([
  "benchmark_accepted_at", "benchmark_accepted_by_identity", "benchmark_manifest_digest",
]);
export const JOURNEY_ONE_MINIMUM_TRUSTED_POLICY_SOURCE_FIELDS = Object.freeze([
  "maximum_completion_receipt_ttl_ms", "production_environment_manifest_digest",
]);

/**
 * The three seats on a consumer-gate-receipt.v1, and the closed field set of
 * each one, C-sorted.
 *
 * DECLARED HERE ONLY BECAUSE NOBODY EXPORTS THEM. benchmark-minimum.v5.js keeps
 * its IDENTITY_FIELDS module-private and the kernel keeps its IDENTITY private,
 * so this is the r7 authenticated-receipt-identity.v1 required set restated
 * beside the schema ref it belongs to rather than a rule invented here. It is
 * used for SHAPE only: nothing below reads an authority_class, derives one, or
 * compares two seats.
 */
export const JOURNEY_ONE_MINIMUM_RECEIPT_IDENTITY_SEATS = Object.freeze([
  "evaluator_identity", "producer_identity", "subject_maker_identity",
]);
const RECEIPT_IDENTITY_SEATS = JOURNEY_ONE_MINIMUM_RECEIPT_IDENTITY_SEATS;
const RECEIPT_IDENTITY_FIELDS = Object.freeze(["actor_id", "authority_class", "session_ref"]);

/** The per-evaluation facts a composer is handed, whose homes are elsewhere. */
export const JOURNEY_ONE_MINIMUM_COMPOSE_FIELDS = Object.freeze([
  "amendments", "as_of", "completion", "completion_expectation", "history", "pauses",
]);

/** The kernel's own eleven projection fields, in its declared order. */
const PROJECTION_FIELDS = Object.freeze([
  "schema_version", "tenant", "as_of", "binding", "benchmark", "minimum_history",
  "completion", "completion_expectation", "pauses", "amendments", "history",
]);

/**
 * THE ADMISSION INVARIANTS, STATED ONCE.
 *
 * Two homes — assertions in this module and ops.j1_minimum_append_guard() in the
 * candidate SQL — and rule a8c55a47 requires something that COMPARES them. Every
 * id below appears verbatim in the SQL, and the unit suite reads the .sql file
 * and asserts it. That proves neither home dropped an entry. It does NOT prove
 * the SQL is correct: it has never run.
 */
const BOTH = Object.freeze(["module", "record_layer"]);
export const JOURNEY_ONE_MINIMUM_ADMISSION_INVARIANTS = Object.freeze([
  Object.freeze({ id: "j1_minimum_inventory_scope_bound", enforced_in: BOTH,
    statement: "One authoritative clock scope holds at most one admitted-minimum inventory, and the scope is trusted construction-time context: it is derived once by journeyOneClockScopeBinding, is never a request field, and a caller who could choose it beside the receipt could open a second inventory and reopen a clock." }),
  Object.freeze({ id: "j1_minimum_inventory_policy_sealed", enforced_in: BOTH,
    statement: "The accepted minimum-receipt TTL policy and environment manifest an inventory was opened under are the ones every later admission is written under; a changed policy is refused by name rather than surfacing later as an unreadable clock." }),
  Object.freeze({ id: "j1_minimum_scope_label_is_not_identity", enforced_in: BOTH,
    statement: "A scope's human label is provenance and never identity: it is excluded from the scope key, so one accepted scope under two names is one inventory, and it is recorded once, so a second label for a bound scope is refused rather than kept or replaced." }),
  Object.freeze({ id: "j1_minimum_tenant_bound", enforced_in: BOTH,
    statement: "An admission is stored under the tenant its inventory was opened with, and a read for another tenant refuses rather than serving it." }),
  Object.freeze({ id: "j1_minimum_receipt_producer_bound", enforced_in: BOTH,
    statement: "An admitted row is a consumer-gate-receipt.v1 for gate foundation-assurance-minimum-accepted from step:foundation-assurance-minimum-receipt, with that step's declared role, oracle, oracle version, evidence scope and subject environment. Any other producer is refused rather than stored: the kernel refuses it fatally, and an append-only inventory cannot shed such a row." }),
  Object.freeze({ id: "j1_minimum_receipt_readable_by_kernel", enforced_in: BOTH,
    statement: "An admitted row is one the kernel can still READ: safe:/session: prefixes, identity seats that are objects of exactly the three declared names each holding a non-empty STRING, a sha256 fixture-set digest, and a comparator that is a STRING of 5 to 300 UTF-16 CODE UNITS. Type and unit are part of the rule and are counted the same way in both homes -- the record layer reads jsonb types rather than the text `->>` renders a number as, and counts through the one shared UTF-16 counter rather than codepoints, which differ on text above U+FFFF in both directions. Each fact is fatal in the kernel rather than skipped, so one admitted row would make every later evaluation of this inventory throw and an append-only inventory could not shed it. It is SHAPE only -- no seat independence is judged and no authority class is derived, because those belong to the join that proposes a receipt and to the kernel." }),
  Object.freeze({ id: "j1_minimum_receipt_binds_accepted_scope", enforced_in: BOTH,
    statement: "The receipt's subject, candidate and policy digests are the accepted scope's and its environment manifest digest is the sealed accepted one. They are read from those accepted source bindings and never from the receipt being judged." }),
  Object.freeze({ id: "j1_minimum_receipt_window_within_accepted_policy", enforced_in: BOTH,
    statement: "The receipt's ttl_expires_at is after its observed_at and no further from it than the sealed accepted maximum. An overlong window is a misissued receipt, which the kernel refuses fatally rather than skipping." }),
  Object.freeze({ id: "j1_minimum_admission_instant_is_server_time", enforced_in: BOTH,
    statement: "admitted_at is stamped by the record layer's own trusted clock. There is no argument for it, no default for one, and the durable half re-derives it from the same transaction timestamp and refuses a supplied value that differs." }),
  Object.freeze({ id: "j1_minimum_admission_not_before_observation", enforced_in: BOTH,
    statement: "admitted_at is at or after the receipt's own observed_at. A single instant of skew is refused at write time, because the kernel refuses it fatally and the inventory could never shed the row." }),
  Object.freeze({ id: "j1_minimum_first_origin_never_replaced", enforced_in: BOTH,
    statement: "AN INVENTORY IS STORED IN THE KERNEL'S OWN SELECTION ORDER, AND AN APPEND ONLY EVER EXTENDS IT: admitted_at is non-decreasing, and within one admitted_at group the receipt digest strictly increases. The kernel orders candidates by (admitted_at, receipt_digest) and takes the first ELIGIBLE one, so a row that sorts after every stored row can never displace whichever row it already selected — whatever the eligibility filter skipped. Comparing a newcomer against the first LEDGER ROW would not do it: that row may be an inadmissible attempt the kernel skips, and a later same-instant row with a lower digest could then still take the origin from the eligible row that had it." }),
  Object.freeze({ id: "j1_minimum_receipt_never_readmitted", enforced_in: BOTH,
    statement: "One receipt digest is admitted once per inventory. Re-presenting the same artifact under a new admission instant is a replay, not a second admission, and it is refused rather than appended." }),
  Object.freeze({ id: "j1_minimum_exact_prior_admission_digest", enforced_in: BOTH,
    statement: "An admission names the exact chain digest of the current head; an explicit null opens the inventory and succeeds only when it holds no admissions." }),
  Object.freeze({ id: "j1_minimum_idempotency_key_binds_its_payload", enforced_in: BOTH,
    statement: "One idempotency key replays exactly one admission; the same key presented with different content is refused rather than returning the first." }),
  Object.freeze({ id: "j1_minimum_content_rebuilds_to_its_digest", enforced_in: BOTH,
    statement: "The receipt rebuilt from the stored row hashes to the receipt digest it is filed under, its extracted columns equal the receipt's own values, and each chain link recomputes from the row and the link before it." }),
  Object.freeze({ id: "j1_minimum_claimed_digest_is_never_trusted", enforced_in: BOTH,
    statement: "A caller-claimed receipt digest is only ever compared against the one the stored artifact produces." }),
  // The one invariant with a single home, and it is honest about why: this
  // module has no update or delete path to refuse, and the durable journal
  // issues no DML at all.
  Object.freeze({ id: "j1_minimum_rows_are_append_only", enforced_in: Object.freeze(["record_layer"]),
    statement: "Update, delete and truncate are refused on every relation of this rail, by TWO triggers per relation: a row-level one for update and delete, and a statement-level one for truncate, which a row-level trigger never sees and which cannot be revoked from the table owner. Enforced only at the database: this module has no mutation path to refuse." }),
]);

export const JOURNEY_ONE_MINIMUM_ADMISSION_INVARIANT_IDS = Object.freeze(
  JOURNEY_ONE_MINIMUM_ADMISSION_INVARIANTS.map(i => i.id));

/**
 * ROW AND DIGEST INTEGRITY IS NOT AUTHENTIC PROVENANCE, and the two are
 * different findings. This rail can recompute; it cannot authenticate.
 */
export const JOURNEY_ONE_MINIMUM_INPUT_STORE_CANNOT_PROVE = Object.freeze([
  "that an admitted artifact is a receipt a real independent foundation-assurance-minimum oracle issued. No issuance adapter exists: benchmark-minimum.v5.js PROPOSES a receipt and marks it proposed_not_issued, and a trusted writer's composed object is indistinguishable here from a genuine one",
  "that the three seats inside an admitted receipt are INDEPENDENT of one another and of the subject's maker. Their shape is checked here; their relationship is not. A00's join enforces independence where the receipt is proposed and the kernel refuses a collision by name as self_attestation; an independence rule here would be a second home for seat authority, held by the seat with no live identities to check it against. A row failing it is fatal and unshedable, exactly like the shape facts this rail does refuse",
  "that the identities inside a receipt are live authenticated seats rather than strings, or that its authority classes were derived from the live actor",
  "that the Gate Zero outcome and the benchmark coverage fact the join consumed were bound to this receipt. consumer-gate-receipt.v1 has no field for either and this rail stores neither; that binding is an open blocker, not a stored fact",
  "that the accepted scope and the accepted minimum policy an inventory was opened under are the ones a verifier accepted for any projection. Both are trusted construction-time bindings: they are compared and sealed, never verified",
  "that a row written by a direct holder of the writer bundle is a genuine admission rather than that writer's assertion; both are trusted writers and nothing recorded here tells them apart",
  "anything about a deadline. An admitted row is an INPUT the kernel may read. It starts no clock, accepts no benchmark and grants no gate",
]);

/**
 * A record layer's own effects. V5_NO_EFFECTS asserts database_writes: 0, which
 * is true of every read and descriptor here and false of an admission.
 */
export const JOURNEY_ONE_MINIMUM_ADMISSION_EFFECTS = deepFreeze({
  creates_effect: false,
  database_writes: 1,
  network_calls: 0, provider_actions: 0, notifications: 0,
  schedules: 0, deployments: 0, activations: 0, acceptances: 0,
  gate_admission_granted: false,
  clock_started: false,
  input_history_appended: true,
  grants_dispatch_activation_or_execution: false,
});

const SHA256_REF = /^sha256:[0-9a-f]{64}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SAFE_REF = /^safe:[A-Za-z0-9:._/-]{3,290}$/;
/** The kernel's instant grammar, restated as a STORAGE ACCEPTANCE bound only. */
const TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?(?:Z|[+-]\d{2}:\d{2})$/;

// A00's minimum gate and the accepted deadline contract's clock origin gate are
// two authorities naming one gate. Reconciled at load rather than picked from
// one side, so a divergence is a startup failure and not a silent mis-binding.
if (MINIMUM_GATE_ID !== JOURNEY_ONE_DEADLINE_CONTRACT.clock_origin_gate_id) {
  throw new Error(
    "journey-one-clock-input-store: A00's minimum gate id and the accepted deadline contract's clock_origin_gate_id disagree");
}

// THE DERIVABLE/NOT-DERIVABLE SPLIT, RECONCILED AT LOAD RATHER THAN TRUSTED.
// A field that fell out of both halves would silently stop being derived and
// stop being declared a trusted input; a field in both would be claimed as
// derived while a caller still supplied it. Either is a startup failure here
// rather than a quiet weakening of the composer's own report about itself.
{
  const split = [...JOURNEY_ONE_MINIMUM_BENCHMARK_DERIVED_SOURCE_FIELDS,
    ...JOURNEY_ONE_MINIMUM_TRUSTED_POLICY_SOURCE_FIELDS].sort();
  if (split.length !== JOURNEY_ONE_MINIMUM_ACCEPTED_SOURCE_FIELDS.length ||
      split.some((field, i) => field !== JOURNEY_ONE_MINIMUM_ACCEPTED_SOURCE_FIELDS[i])) {
    throw new Error(
      "journey-one-clock-input-store: the derived and trusted-policy accepted-source halves are not exactly the accepted source field set");
  }
  // And the derivable half is A00's acceptance envelope under M01's names — not
  // a second opinion about which fields an acceptance envelope carries. `status`
  // is excluded because M01's projection has no slot for it: validateBenchmark-
  // Manifest already refuses any value but "accepted", so carrying it would be
  // carrying a constant. The rest gain the `benchmark_` prefix M01 spells them
  // with, and benchmark_manifest_digest already has it.
  const envelope = BENCHMARK_ACCEPTANCE_ENVELOPE_FIELDS.filter(f => f !== "status")
    .map(f => f.startsWith("benchmark_") ? f : `benchmark_${f}`).sort();
  if (envelope.join("|") !== [...JOURNEY_ONE_MINIMUM_BENCHMARK_DERIVED_SOURCE_FIELDS].join("|")) {
    throw new Error(
      `journey-one-clock-input-store: the derivable accepted-source fields are no longer ${BENCHMARK_MANIFEST_SCHEMA}'s acceptance envelope`);
  }
}

export class JourneyOneMinimumInputStoreError extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = "JourneyOneMinimumInputStoreError";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function refuse(code, message, detail) {
  throw new JourneyOneMinimumInputStoreError(code, message, detail);
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
const instant = value => Date.parse(value);

function closed(value, fields, path) {
  if (!isPlainObject(value)) refuse("invalid_shape", `${path} must be an object`, { path });
  const keys = Object.keys(value);
  if (keys.length !== fields.length || fields.some(f => !Object.hasOwn(value, f))) {
    refuse("closed_shape", `${path} must carry exactly its declared fields`,
      { path, expected: [...fields].sort(), actual: [...keys].sort() });
  }
  return value;
}

function assertDigestRef(value, path) {
  if (typeof value !== "string" || !SHA256_REF.test(value)) {
    refuse("invalid_digest", `${path} must be a sha256: reference`, { path, actual: value });
  }
  return value;
}

function assertTimestampText(value, path) {
  if (typeof value !== "string" || !TIMESTAMP.test(value) || !Number.isFinite(Date.parse(value))) {
    refuse("invalid_timestamp", `${path} must be an instant this rail can store verbatim`,
      { path, actual: value });
  }
  return value;
}

function assertSafeRef(value, path) {
  if (typeof value !== "string" || !SAFE_REF.test(value)) {
    refuse("invalid_reference", `${path} must be a safe: reference; it is a name, never a proof`,
      { path, actual: value });
  }
  return value;
}

function assertUuid(value, path) {
  if (typeof value !== "string" || !UUID.test(value)) {
    refuse("invalid_uuid", `${path} must be a uuid`, { path });
  }
  return value;
}

function assertPositiveMs(value, path) {
  if (!Number.isSafeInteger(value) || value <= 0) {
    refuse("invalid_duration", `${path} must be a positive safe integer of milliseconds`,
      { path, actual: value });
  }
  return value;
}

// ---------------------------------------------------------------------------
// DIGESTS. One receipt identity, one chain link, and neither is a second rule.
// ---------------------------------------------------------------------------

/**
 * The identity of one admitted minimum receipt.
 *
 * It is `digest(journeyOneClockMinimumReceiptView(receipt))`, and that function's
 * own contract is the reason this file computes no hash of its own:
 * "digest(view) === digest(proposed_receipt) is the exact origin_receipt_digest
 * M01 records". The view is also the ONE validator of the r7-exact twenty-one
 * fields and of M01's two narrower value domains, so a receipt this rail cannot
 * store refuses with A00's code naming the seam rather than a local one.
 */
export function journeyOneMinimumReceiptDigest(receipt) {
  return digest(journeyOneClockMinimumReceiptView(receipt));
}

/** One admission's chain link: sha256 over the canonical [domain tag, preimage]. */
export function journeyOneMinimumAdmissionDigest(fields) {
  closed(fields, JOURNEY_ONE_MINIMUM_ADMISSION_FIELDS, "admission");
  return digest([JOURNEY_ONE_MINIMUM_ADMISSION_DOMAIN_TAG, Object.fromEntries(
    JOURNEY_ONE_MINIMUM_ADMISSION_FIELDS.map(field => [field, fields[field]]))]);
}

/** Read one accepted minimum-input policy binding, exactly. */
export function journeyOneMinimumAcceptedPolicy(policy) {
  closed(policy, JOURNEY_ONE_MINIMUM_ACCEPTED_POLICY_FIELDS, "accepted_minimum_policy");
  assertPositiveMs(policy.maximum_minimum_receipt_ttl_ms,
    "accepted_minimum_policy.maximum_minimum_receipt_ttl_ms");
  assertDigestRef(policy.minimum_environment_manifest_digest,
    "accepted_minimum_policy.minimum_environment_manifest_digest");
  return deepFreeze({ ...policy });
}

/**
 * THE BENCHMARK ACCEPTANCE ENVELOPE, DERIVED FROM THE ACCEPTED MANIFEST RATHER
 * THAN RESTATED BESIDE IT.
 *
 * `benchmark_manifest_digest`, `benchmark_accepted_at` and
 * `benchmark_accepted_by_identity` describe an artifact that exists. Handed to a
 * composer as three literals they are three strings nothing compares against
 * that artifact: a digest no manifest produces, an instant no manifest records
 * and an acceptor no manifest names would all compose a projection the kernel
 * would then start a clock on. Derived here, each one comes off the manifest
 * itself, and the digest returned is the one A00's validator RECOMPUTED from the
 * payload rather than the field the manifest carries.
 *
 * IT REUSES A00's ONE VALIDATOR AND ADDS NO SECOND OPINION.
 * validateBenchmarkManifest owns "is this an accepted benchmark-manifest.v1",
 * and its refusals travel to the caller under its own codes rather than a local
 * vocabulary for the same fact.
 *
 * WHAT THIS IS NOT, AND THE DISTINCTION IS THE WHOLE TRUST BOUNDARY. That
 * validator reads the SHAPE of an acceptance envelope: that the payload hashes
 * to the digest the manifest claims, that `status` is the single accepted value,
 * and that `accepted_by_identity` NAMES a known partner in verified-partner
 * form. IT AUTHENTICATES NOBODY. No live actor is consulted, no acceptance
 * record is read, and a manifest trusted server code assembled a moment ago is
 * indistinguishable here from one Joe or Dell actually accepted — the exact
 * `authority_class` seat the kernel's own header warns must never be read back
 * out of a stored record. Deriving these three removes a caller's freedom to
 * INVENT them; it does not turn shape validation into human acceptance, and
 * nothing downstream may report it as such.
 */
export function journeyOneMinimumBenchmarkAcceptedSources(manifest) {
  if (!isPlainObject(manifest)) {
    refuse("invalid_shape",
      `the benchmark acceptance envelope is derived from an accepted ${BENCHMARK_MANIFEST_SCHEMA}, never from a pair of caller strings`,
      { path: "benchmark_manifest" });
  }
  const { payload_digest } = validateBenchmarkManifest(manifest);
  return deepFreeze({
    benchmark_accepted_at: manifest.accepted_at,
    benchmark_accepted_by_identity: copy(manifest.accepted_by_identity),
    benchmark_manifest_digest: payload_digest,
  });
}

/**
 * Read one candidate minimum receipt against the accepted source bindings it
 * would be admitted under, and return the stored view.
 *
 * EVERY CLAUSE HERE IS A FATAL-IN-KERNEL FACT. Nothing inadmissible-but-ordinary
 * is judged: a non-passing receipt and a receipt whose window will lapse are both
 * admitted and stored, because the kernel calls them ordinary ledger facts and
 * this rail has no discard path.
 */
export function journeyOneMinimumAdmissionView({ receipt, scope, policy }) {
  if (!isPlainObject(scope) || !isPlainObject(scope.scope) || !isPlainObject(policy)) {
    refuse("invalid_shape",
      "an admission is read against a DERIVED scope binding (journeyOneClockScopeBinding) and an accepted minimum policy, never against a raw pair of arguments",
      { path: "journeyOneMinimumAdmissionView" });
  }
  const view = journeyOneClockMinimumReceiptView(receipt);
  if (view.gate_id !== MINIMUM_GATE_ID ||
      view.receipt_producer_step_ref !== MINIMUM_STEP_REF ||
      view.producer_role !== MINIMUM_PRODUCER_ROLE ||
      view.independent_oracle_ref !== MINIMUM_ORACLE_REF ||
      view.oracle_version !== MINIMUM_ORACLE_VERSION ||
      view.evidence_scope !== MINIMUM_EVIDENCE_SCOPE ||
      view.subject_environment !== MINIMUM_SUBJECT_ENVIRONMENT) {
    refuse("wrong_minimum_receipt_producer",
      "this inventory admits the foundation-assurance-minimum receipt its own producer step issues, and nothing else; the kernel refuses another producer fatally and an append-only inventory could never shed the row",
      { invariant: "j1_minimum_receipt_producer_bound",
        expected: { gate_id: MINIMUM_GATE_ID, receipt_producer_step_ref: MINIMUM_STEP_REF,
          producer_role: MINIMUM_PRODUCER_ROLE, independent_oracle_ref: MINIMUM_ORACLE_REF,
          oracle_version: MINIMUM_ORACLE_VERSION, evidence_scope: MINIMUM_EVIDENCE_SCOPE,
          subject_environment: MINIMUM_SUBJECT_ENVIRONMENT },
        supplied: { gate_id: view.gate_id, receipt_producer_step_ref: view.receipt_producer_step_ref,
          producer_role: view.producer_role, independent_oracle_ref: view.independent_oracle_ref,
          oracle_version: view.oracle_version, evidence_scope: view.evidence_scope,
          subject_environment: view.subject_environment } });
  }
  // THE ACCEPTED SCOPE DECIDES, NOT THE RECEIPT. These three come from the
  // scope this store was constructed for, so a receipt for another subject is
  // refused rather than quietly widening the inventory's own binding.
  for (const [field, expected] of [
    ["subject_digest", scope.scope.benchmark_subject_digest],
    ["candidate_digest", scope.scope.benchmark_candidate_digest],
    ["policy_digest", scope.scope.benchmark_policy_digest],
  ]) {
    if (view[field] !== expected) {
      refuse("minimum_receipt_scope_mismatch",
        `receipt.${field} is not the accepted scope's; this inventory is the admitted-minimum history of one authoritative scope`,
        { invariant: "j1_minimum_receipt_binds_accepted_scope", field, expected, supplied: view[field] });
    }
  }
  if (view.environment_manifest_digest !== policy.minimum_environment_manifest_digest) {
    refuse("minimum_receipt_environment_mismatch",
      "receipt.environment_manifest_digest is not the accepted minimum environment manifest this inventory was opened under",
      { invariant: "j1_minimum_receipt_binds_accepted_scope",
        expected: policy.minimum_environment_manifest_digest,
        supplied: view.environment_manifest_digest });
  }
  // THE REMAINING FATAL-IN-KERNEL SHAPE FACTS. journeyOneClockMinimumReceiptView
  // validates JSON-safety, the closed twenty-one keys and M01's two narrower
  // value DOMAINS -- it does not check the `safe:`/`session:` PREFIXES, the
  // identity sub-object shape, the fixture digest format or the comparator
  // bounds. Each of those is fatal in the kernel (invalid_reference,
  // invalid_identity, invalid_digest, invalid_comparator), so one admitted row
  // would make every later evaluation of this inventory throw, and an
  // append-only inventory cannot shed it. They are SHAPE, not eligibility and
  // not seat authority: no independence is judged here and no authority class is
  // derived. Seat independence stays with A00's join and the kernel, disclosed
  // in JOURNEY_ONE_MINIMUM_INPUT_STORE_CANNOT_PROVE rather than re-implemented.
  //
  // TYPE AND UNIT ARE PART OF EACH RULE, and the record layer's mirror of these
  // clauses has to read them the same way: `typeof x !== "string"` there is
  // jsonb_typeof rather than `->>`, which renders a number as text and would
  // admit the numeric comparator 12345; and `.length` here is UTF-16 CODE UNITS,
  // which is neither codepoints nor bytes and differs from codepoints in BOTH
  // directions above U+FFFF.
  if (!view.evidence_ref.startsWith("safe:")) {
    refuse("minimum_receipt_invalid_reference",
      "receipt.evidence_ref is a safe: reference; the kernel refuses another prefix fatally",
      { invariant: "j1_minimum_receipt_readable_by_kernel", path: "receipt.evidence_ref",
        kernel_refusal: "invalid_reference", supplied: view.evidence_ref });
  }
  for (const seat of RECEIPT_IDENTITY_SEATS) {
    const identity = view[seat];
    if (!isPlainObject(identity) ||
        Object.keys(identity).length !== RECEIPT_IDENTITY_FIELDS.length ||
        RECEIPT_IDENTITY_FIELDS.some(field => typeof identity[field] !== "string" || identity[field] === "") ||
        !identity.session_ref.startsWith("session:")) {
      refuse("minimum_receipt_invalid_identity",
        `receipt.${seat} is an ${AUTHENTICATED_RECEIPT_IDENTITY_SCHEMA} seat: exactly its three declared fields, each a non-empty string, with a session: reference`,
        { invariant: "j1_minimum_receipt_readable_by_kernel", path: `receipt.${seat}`,
          kernel_refusal: "invalid_identity", expected: [...RECEIPT_IDENTITY_FIELDS] });
    }
  }
  if (typeof view.fixture_set_digest !== "string" || !SHA256_REF.test(view.fixture_set_digest)) {
    refuse("minimum_receipt_invalid_digest",
      "receipt.fixture_set_digest is a sha256 reference; the kernel refuses another shape fatally",
      { invariant: "j1_minimum_receipt_readable_by_kernel", path: "receipt.fixture_set_digest",
        kernel_refusal: "invalid_digest", supplied: view.fixture_set_digest });
  }
  if (typeof view.comparator !== "string" ||
      view.comparator.length < 5 || view.comparator.length > 300) {
    refuse("minimum_receipt_invalid_comparator",
      "receipt.comparator is between 5 and 300 UTF-16 code units, which is the bound the kernel refuses on",
      { invariant: "j1_minimum_receipt_readable_by_kernel", path: "receipt.comparator",
        kernel_refusal: "invalid_comparator",
        length: typeof view.comparator === "string" ? view.comparator.length : null });
  }
  const observed = instant(view.observed_at), expires = instant(view.ttl_expires_at);
  if (expires <= observed) {
    refuse("minimum_receipt_window_invalid",
      "a receipt's ttl_expires_at is after its observed_at",
      { invariant: "j1_minimum_receipt_window_within_accepted_policy",
        observed_at: view.observed_at, ttl_expires_at: view.ttl_expires_at });
  }
  if (expires - observed > policy.maximum_minimum_receipt_ttl_ms) {
    refuse("minimum_receipt_ttl_policy_exceeded",
      "the receipt's window is longer than the accepted maximum this inventory was opened under; an overlong window is a misissued receipt or a misbound policy, which the kernel refuses fatally rather than skipping",
      { invariant: "j1_minimum_receipt_window_within_accepted_policy",
        window_ms: expires - observed, maximum_ms: policy.maximum_minimum_receipt_ttl_ms });
  }
  return view;
}

// ---------------------------------------------------------------------------
// THE JOURNAL PORT.
//
// Two implementations, one contract. A journal must, for one clock scope key,
// SERIALIZE its appends, and must supply the SERVER ADMISSION INSTANT itself:
// that instant is the whole reason this rail exists and a caller never names it.
//
//   runAppend(scopeKey, { idempotencyKey, build }) -> { ...admission, replayed }
//     Serialized per scope key. Reads the idempotency key first; on a hit
//     build() returns null and the stored row is replayed. Otherwise it calls
//     build({ inventory, head, admissions, replay, admitted_at }) and persists
//     exactly what build returns.
//   readInventory(scopeKey) -> inventory row or null
//   readAdmissions(scopeKey) -> admissions ascending by admission_ordinal
//   openInventory(binding) -> the inventory row, idempotent for the exact
//     binding, refusing a changed policy, environment or label.
// ---------------------------------------------------------------------------

/**
 * THE NON-DURABLE REFERENCE JOURNAL. Keeps rows in a Map and loses everything
 * when the process exits. It exists so the CAS, idempotency, ordering and
 * readback can be exercised for real in-process. IT IS NOT A MODEL OF
 * POSTGRESQL: one writer, no transactions, no lock manager. `durable` is false.
 */
export function createEphemeralJourneyOneMinimumAdmissionJournal({ now = Date.now } = {}) {
  if (typeof now !== "function") refuse("invalid_shape", "now must be a function", { path: "now" });
  const inventories = new Map();   // scope key -> inventory row
  const admissions = new Map();    // scope key -> admission rows, ascending
  const byIdempotency = new Map(); // idempotency key -> admission row
  const queues = new Map();        // scope key -> tail promise

  const serialize = (scopeKey, fn) => {
    const previous = queues.get(scopeKey) ?? Promise.resolve();
    const settled = previous.then(fn, fn);
    // The queue must survive a rejection, or one refusal would wedge the scope.
    queues.set(scopeKey, settled.then(() => undefined, () => undefined));
    return settled;
  };

  return Object.freeze({
    durable: false,
    kind: "ephemeral-reference-minimum-admission-journal",
    async runAppend(scopeKey, { idempotencyKey, build }) {
      return serialize(scopeKey, async () => {
        const replay = byIdempotency.get(idempotencyKey) ?? null;
        const list = admissions.get(scopeKey) ?? [];
        const head = list.length ? list[list.length - 1] : null;
        // THE SERVER ADMISSION INSTANT, from this journal's own trusted clock and
        // from nowhere else. It is read once and the same value is both asserted
        // against and stored, so there is no second reading to skew against.
        const at = new Date(now());
        const admitted_at = at.toISOString();
        const candidate = await build({
          inventory: inventories.get(scopeKey) ?? null, head, admissions: [...list],
          replay, admitted_at });
        if (candidate === null) return { ...replay, replayed: true };
        const recorded_at = at.toISOString();
        if (head && instant(recorded_at) < instant(head.recorded_at)) {
          refuse("minimum_backdated_admission",
            "the server clock moved backwards between two admissions of one inventory",
            { invariant: "j1_minimum_admission_instant_is_server_time",
              recorded: head.recorded_at, supplied: recorded_at });
        }
        const stored = deepFreeze({ ...candidate, recorded_at,
          admission_ordinal: list.length });
        if (!admissions.has(scopeKey)) admissions.set(scopeKey, []);
        admissions.get(scopeKey).push(stored);
        byIdempotency.set(idempotencyKey, stored);
        return { ...stored, replayed: false };
      });
    },
    async readInventory(scopeKey) { return inventories.get(scopeKey) ?? null; },
    async readAdmissions(scopeKey) { return [...(admissions.get(scopeKey) ?? [])]; },
    /**
     * The seal is HERE and not only in the caller, because a journal that will
     * hold two policies for one inventory is a journal with the hole in it. The
     * check and the write are one synchronous step.
     */
    async openInventory(binding = {}) {
      const existing = inventories.get(binding.clock_scope_key) ?? null;
      if (existing) {
        for (const [field, invariant] of [
          ["minimum_receipt_ttl_policy_ms", "j1_minimum_inventory_policy_sealed"],
          ["minimum_environment_manifest_digest", "j1_minimum_inventory_policy_sealed"],
        ]) {
          if (existing[field] !== binding[field]) {
            refuse("minimum_accepted_policy_changed",
              `this inventory was opened under another ${field}; the accepted minimum policy is sealed at the first admission and a later one is refused rather than applied to rows it never judged`,
              { invariant, field, recorded: existing[field], supplied: binding[field] });
          }
        }
        if (existing.clock_scope_ref !== binding.clock_scope_ref) {
          refuse("minimum_scope_label_changed",
            "this authoritative scope was bound under another label; the label is provenance, is recorded once, and is never rewritten by a later write",
            { invariant: "j1_minimum_scope_label_is_not_identity",
              recorded: existing.clock_scope_ref, supplied: binding.clock_scope_ref });
        }
        return existing;
      }
      const row = deepFreeze({ ...binding, opened_at: new Date(now()).toISOString() });
      inventories.set(binding.clock_scope_key, row);
      return row;
    },
  });
}

/**
 * THE DURABLE JOURNAL: the binding to ops/journey-one-clock-input-store.candidate.sql.
 *
 * Thin on purpose. Serialization is ops.j1_minimum_lock(text). The admission
 * instant comes from ops.j1_minimum_admission_instant(), which is derived from
 * now() — the TRANSACTION timestamp — so the value this journal reads and the
 * value the append function re-derives are the same reading, and the append
 * function refuses a supplied one that differs.
 *
 * `query` is the repository's ordinary handle contract: query(text, params) ->
 * { rows }, inside the caller's transaction — and "inside" is ASSERTED rather
 * than documented: txid_current() is read after the lock and again before the
 * append, and a change means the statements are not sharing a transaction, so
 * the advisory lock has already been released and the admission instant is no
 * longer one reading. It refuses there, with nothing written.
 *
 * NEVER EXECUTED HERE. The candidate SQL has not been applied.
 */
export function createPostgresJourneyOneMinimumAdmissionJournal({ query } = {}) {
  if (typeof query !== "function") {
    refuse("invalid_shape", "a postgres journal needs a query function", { path: "query" });
  }
  const one = async (sql, params) => (await query(sql, params)).rows[0] ?? null;

  return Object.freeze({
    durable: true,
    kind: "postgres-minimum-admission-journal",
    async runAppend(scopeKey, { idempotencyKey, build }) {
      await query("select ops.j1_minimum_lock($1::text)", [scopeKey]);
      // ONE TRANSACTION, ASSERTED RATHER THAN DOCUMENTED. Two of this journal's
      // guarantees rest on the statements below sharing a transaction:
      // pg_advisory_xact_lock releases at statement end otherwise, and the
      // "one reading, not two" argument for the admission instant is exactly the
      // stability of now() within a transaction. txid_current() assigns and
      // returns the transaction's own id, so under autocommit these two readings
      // differ. It is compared BEFORE the append, so a caller who forgot the
      // transaction meets a named refusal instead of a row written under a lock
      // that was already gone.
      const txid = (await one("select txid_current()::text as txid"))?.txid ?? null;
      const admitted_at = (await one("select ops.j1_minimum_admission_instant() as at"))?.at ?? null;
      const inventory = (await one("select ops.j1_minimum_inventory_row($1::text) as inventory",
        [scopeKey]))?.inventory ?? null;
      const head = (await one("select ops.j1_minimum_head($1::text) as head", [scopeKey]))?.head ?? null;
      const admissions = (await one("select ops.j1_minimum_admissions($1::text) as admissions",
        [scopeKey]))?.admissions ?? [];
      const replay = (await one("select ops.j1_minimum_admission_by_idempotency_key($1::uuid) as admission",
        [idempotencyKey]))?.admission ?? null;
      const candidate = await build({ inventory, head, admissions, replay, admitted_at });
      // A REPLAY IS NOT A SECOND WRITE. build() has already refused a key whose
      // payload changed, so a null candidate means it matched.
      if (candidate === null) return { ...replay, replayed: true };
      const stillTxid = (await one("select txid_current()::text as txid"))?.txid ?? null;
      if (stillTxid === null || stillTxid !== txid) {
        refuse("minimum_admission_transaction_not_shared",
          "the statements of this append are not in one transaction, so the advisory lock taken on this inventory has already been released and the admission instant is no longer one reading. Nothing is written",
          { invariant: "j1_minimum_admission_instant_is_server_time",
            clock_scope_key: scopeKey, at_lock: txid, before_append: stillTxid });
      }
      const result = (await one(
        `select ops.j1_minimum_append_admission(
           $1::text,$2::text,$3::uuid,$4::text,$5::text,$6::text,$7::jsonb,$8::jsonb) as admission`,
        [scopeKey, candidate.tenant, idempotencyKey, candidate.previous_admission_digest,
          candidate.admitted_at, candidate.receipt_digest,
          JSON.stringify(candidate.receipt), JSON.stringify(candidate.provenance)]))?.admission;
      if (!result) {
        refuse("minimum_append_returned_nothing",
          "ops.j1_minimum_append_admission returned no admission", { clock_scope_key: scopeKey });
      }
      return result;
    },
    async readInventory(scopeKey) {
      return (await one("select ops.j1_minimum_inventory_row($1::text) as inventory",
        [scopeKey]))?.inventory ?? null;
    },
    async readAdmissions(scopeKey) {
      return (await one("select ops.j1_minimum_admissions($1::text) as admissions",
        [scopeKey]))?.admissions ?? [];
    },
    async openInventory(binding = {}) {
      // The whole scope object travels and the key is derived there, exactly as
      // it is here: a key the database accepted on trust would be a
      // caller-chosen address wearing a hash.
      return (await one(
        "select ops.j1_minimum_open_inventory($1::jsonb,$2::bigint,$3::text) as inventory",
        [JSON.stringify(binding.scope), binding.minimum_receipt_ttl_policy_ms,
          binding.minimum_environment_manifest_digest]))?.inventory ?? null;
    },
  });
}

// ---------------------------------------------------------------------------
// THE STORE.
// ---------------------------------------------------------------------------

/**
 * The durable admitted-minimum input history for ONE authoritative clock scope.
 *
 * `clock_scope` AND `accepted_minimum_policy` ARE CONSTRUCTION-TIME TRUSTED
 * BINDINGS AND ARE DELIBERATELY NOT REQUEST FIELDS. A caller who could choose
 * the scope beside the receipt could open a second inventory for one program and
 * hand a fresh origin to a clock that already has one; a caller who could choose
 * the TTL policy could make a receipt admissible by widening the window it is
 * judged against. Only trusted server code constructs a store.
 *
 * `now` is the SERVER clock and is passed to the journal, never to a caller.
 */
export function createJourneyOneClockMinimumInputStore({
  journal, actor, tenant = ORGANIZATION_TENANT_ID,
  clock_scope = null, accepted_minimum_policy = null,
} = {}) {
  for (const method of ["runAppend", "readInventory", "readAdmissions", "openInventory"]) {
    if (!journal || typeof journal[method] !== "function") {
      refuse("invalid_shape", "a minimum input store needs a journal implementing the port",
        { path: `journal.${method}` });
    }
  }
  const writer = deriveJourneyOneClockWriter(actor);
  if (tenant !== ORGANIZATION_TENANT_ID) {
    refuse("wrong_tenant", `this rail stores admissions for ${ORGANIZATION_TENANT_ID} only`,
      { invariant: "j1_minimum_tenant_bound", supplied: tenant });
  }
  // Read once, here, so a malformed scope or policy is a refusal at construction
  // rather than a surprise on the first write. The scope derivation is the clock
  // rail's own: one domain tag, one preimage, and the label is not identity.
  const scope = clock_scope === null ? null : journeyOneClockScopeBinding(clock_scope);
  if (scope !== null && scope.tenant !== tenant) {
    refuse("wrong_tenant", "the clock scope names another tenant than this store",
      { invariant: "j1_minimum_tenant_bound", supplied: scope.tenant, expected: tenant });
  }
  const policy = accepted_minimum_policy === null
    ? null : journeyOneMinimumAcceptedPolicy(accepted_minimum_policy);

  /** Rebuild and verify one stored admission, refusing a tampered readback. */
  const rebuild = (scopeKey, row, previous, ordinal) => {
    if (row.tenant !== tenant) {
      refuse("cross_tenant_minimum_admission", "the stored admission belongs to another tenant",
        { invariant: "j1_minimum_tenant_bound", stored: row.tenant, reading_as: tenant });
    }
    if (row.admission_ordinal !== ordinal) {
      refuse("minimum_admission_ordinal_gap",
        `admission ordinals are not contiguous from zero at position ${ordinal}`,
        { invariant: "j1_minimum_content_rebuilds_to_its_digest",
          expected: ordinal, actual: row.admission_ordinal });
    }
    const receipt = row.receipt;
    const recomputed = journeyOneMinimumReceiptDigest(receipt);
    if (recomputed !== row.receipt_digest) {
      refuse("minimum_admission_readback_tampered",
        "the stored receipt no longer hashes to the digest it was admitted under",
        { invariant: "j1_minimum_content_rebuilds_to_its_digest",
          clock_scope_key: scopeKey, admission_ordinal: ordinal,
          recomputed, stored: row.receipt_digest });
    }
    // The extracted columns are a projection of the artifact and never a second
    // source of truth: a row whose columns disagree with its own receipt is a
    // row somebody edited.
    for (const [column, value] of [
      ["gate_id", receipt.gate_id], ["receipt_producer_step_ref", receipt.receipt_producer_step_ref],
      ["observed_at", receipt.observed_at], ["ttl_expires_at", receipt.ttl_expires_at],
      ["status", receipt.status],
    ]) {
      if (row[column] !== value) {
        refuse("minimum_admission_readback_tampered",
          `the stored ${column} column disagrees with the receipt it was extracted from`,
          { invariant: "j1_minimum_content_rebuilds_to_its_digest", column,
            stored: row[column], receipt: value });
      }
    }
    const link = journeyOneMinimumAdmissionDigest({
      admitted_at: row.admitted_at, clock_scope_key: scopeKey,
      minimum_environment_manifest_digest: row.minimum_environment_manifest_digest,
      minimum_receipt_ttl_policy_ms: row.minimum_receipt_ttl_policy_ms,
      previous_admission_digest: previous, receipt_digest: row.receipt_digest, tenant: row.tenant,
    });
    if ((row.previous_admission_digest ?? null) !== previous || link !== row.admission_digest) {
      refuse("minimum_admission_chain_broken",
        `admission ${ordinal} does not link to the admission before it`,
        { invariant: "j1_minimum_exact_prior_admission_digest", ordinal,
          expected_previous: previous, stored_previous: row.previous_admission_digest ?? null,
          recomputed: link, stored: row.admission_digest });
    }
    return link;
  };

  /** Read every admission of one scope, verified, in order. */
  const readVerified = async (scopeKey) => {
    const rows = await journal.readAdmissions(scopeKey);
    if (!Array.isArray(rows)) {
      refuse("invalid_shape", "a journal must return an array of admissions", { path: "admissions" });
    }
    const ordered = [...rows].sort((a, b) => a.admission_ordinal - b.admission_ordinal);
    let previous = null;
    ordered.forEach((row, ordinal) => {
      previous = rebuild(scopeKey, row, previous, ordinal);
      // THE SELECTION ORDER, RE-CHECKED ON READ RATHER THAN ASSUMED. The write
      // path compares a newcomer against the head only, which is sound by
      // induction; this validates the whole sequence, because the induction is
      // exactly what a row inserted by some other route would break. An
      // out-of-order inventory would hand the kernel an origin this rail never
      // admitted first, so it refuses rather than serving one.
      if (ordinal > 0) {
        const before = ordered[ordinal - 1];
        const gap = instant(row.admitted_at) - instant(before.admitted_at);
        if (gap < 0 || (gap === 0 && row.receipt_digest <= before.receipt_digest)) {
          refuse("minimum_admission_out_of_order",
            `admission ${ordinal} does not sort after the one it follows; a stored inventory is in the kernel's own (admitted_at, receipt_digest) selection order`,
            { invariant: "j1_minimum_first_origin_never_replaced", ordinal,
              previous: { admitted_at: before.admitted_at, receipt_digest: before.receipt_digest },
              stored: { admitted_at: row.admitted_at, receipt_digest: row.receipt_digest } });
        }
      }
    });
    return { ordered, head_admission_digest: previous };
  };

  return Object.freeze({
    writer,
    journal_is_durable: journal.durable === true,
    /** The authoritative scope this store admits for, or null. Zero effect. */
    clock_scope: scope === null ? null : deepFreeze({
      clock_scope_key: scope.clock_scope_key, clock_scope_ref: scope.clock_scope_ref,
      tenant: scope.tenant, scope: copy(scope.scope), scope_identity: copy(scope.scope_identity) }),
    /** The accepted minimum policy this store admits under, or null. Zero effect. */
    accepted_minimum_policy: policy === null ? null : deepFreeze({ ...policy }),

    /**
     * Admit one minimum receipt as the next entry of this scope's inventory.
     *
     * `expected_prior_admission_digest` IS REQUIRED AND MAY BE EXPLICITLY NULL.
     * An omitted CAS token is a caller who does not know what they are appending
     * to, and a default of null would silently turn every such call into an
     * attempt to open the inventory.
     *
     * THERE IS NO `admitted_at` ARGUMENT. The instant comes from the journal's
     * own trusted clock; an argument for it is the one field that would let a
     * caller reorder an append-only inventory and rebase a running clock.
     */
    async admit(args = {}) {
      // The WHOLE argument object. An extra key that reads like a caller
      // asserting authority this rail derives is refused by name before anything
      // else: silently ignored is how a caller comes to believe it was honoured.
      assertNoSelfAssertedAuthority(args, "admit");
      closed(args, ["claimed_receipt_digest", "expected_prior_admission_digest",
        "idempotency_key", "receipt", "source_ref"], "admit");
      const { receipt, expected_prior_admission_digest, idempotency_key,
        claimed_receipt_digest, source_ref } = args;
      assertUuid(idempotency_key, "idempotency_key");
      assertSafeRef(source_ref,
        "source_ref (it NAMES where this artifact came from; naming is not proving)");
      if (scope === null || policy === null) {
        refuse("minimum_inventory_binding_required",
          "admitting a minimum receipt requires the authoritative clock scope and the accepted minimum policy this store admits under; both are bound at construction by trusted server code and are never request fields, because a caller who could choose either could open a second inventory for one program or widen the window a receipt is judged against",
          { invariant: "j1_minimum_inventory_scope_bound",
            scope_bound: scope !== null, policy_bound: policy !== null });
      }
      if (expected_prior_admission_digest === undefined) {
        refuse("missing_prior_admission_digest",
          "every admission names the exact chain digest it is appending to, or an explicit null to open the inventory",
          { invariant: "j1_minimum_exact_prior_admission_digest" });
      }
      if (expected_prior_admission_digest !== null) {
        assertDigestRef(expected_prior_admission_digest, "expected_prior_admission_digest");
      }

      // The artifact is validated and hashed HERE, from the object this call is
      // about to store, before anything is compared. A caller's claimed hash is
      // only ever the loser of that comparison.
      const view = journeyOneMinimumAdmissionView({ receipt, scope, policy });
      const receipt_digest = digest(view);
      if (claimed_receipt_digest !== null) {
        assertDigestRef(claimed_receipt_digest, "claimed_receipt_digest");
        if (claimed_receipt_digest !== receipt_digest) {
          refuse("claimed_receipt_digest_mismatch",
            "the claimed receipt digest is not the one this artifact produces",
            { invariant: "j1_minimum_claimed_digest_is_never_trusted",
              recomputed: receipt_digest, claimed: claimed_receipt_digest });
        }
      }
      const provenance = deepFreeze({
        receipt_schema_ref: CONSUMER_GATE_RECEIPT_SCHEMA,
        receipt_producer_step_ref: MINIMUM_STEP_REF,
        source_ref,
        input_authority: "trusted_admission_not_independently_verified_by_this_record_layer",
        admitted_by_actor_id: writer.actor_id,
        admitted_by_authority_class: writer.authority_class,
      });

      return journal.runAppend(scope.clock_scope_key, {
        idempotencyKey: idempotency_key,
        build: async ({ inventory, head, admissions, replay, admitted_at }) => {
          if (replay) {
            if (replay.clock_scope_key !== scope.clock_scope_key ||
                replay.receipt_digest !== receipt_digest ||
                (replay.previous_admission_digest ?? null) !== expected_prior_admission_digest) {
              refuse("minimum_idempotency_key_reused",
                `idempotency key ${idempotency_key} was already used for a different minimum admission`,
                { invariant: "j1_minimum_idempotency_key_binds_its_payload",
                  recorded: { clock_scope_key: replay.clock_scope_key,
                    receipt_digest: replay.receipt_digest,
                    previous_admission_digest: replay.previous_admission_digest ?? null },
                  supplied: { clock_scope_key: scope.clock_scope_key, receipt_digest,
                    previous_admission_digest: expected_prior_admission_digest } });
            }
            return null;
          }
          if (inventory && inventory.tenant !== tenant) {
            refuse("cross_tenant_minimum_admission", "that inventory belongs to another tenant",
              { invariant: "j1_minimum_tenant_bound" });
          }
          // THE SEALED ACCEPTED POLICY. A store constructed with a different
          // policy meets the one this inventory was opened under and refuses,
          // rather than admitting a row the kernel would later judge under a
          // policy that never selected it.
          if (inventory) {
            for (const [field, value] of [
              ["minimum_receipt_ttl_policy_ms", policy.maximum_minimum_receipt_ttl_ms],
              ["minimum_environment_manifest_digest", policy.minimum_environment_manifest_digest],
            ]) {
              if (inventory[field] !== value) {
                refuse("minimum_accepted_policy_changed",
                  `this inventory was opened under another ${field}; the accepted minimum policy is sealed and a later one is refused rather than applied to rows it never judged`,
                  { invariant: "j1_minimum_inventory_policy_sealed", field,
                    recorded: inventory[field], supplied: value });
              }
            }
            // THE LABEL SEAL, HERE AS WELL AS IN THE JOURNAL. Both journals
            // refuse a relabelled scope in openInventory, but on the durable
            // path that refusal lives in SQL that has never run, so the store
            // would pass its whole read and CAS before meeting it. The label is
            // not identity — the key ignores it, which is what makes a relabelled
            // scope the SAME scope — and precisely because it is not identity the
            // record must not end up holding two names for one scope.
            if (inventory.clock_scope_ref !== undefined &&
                inventory.clock_scope_ref !== scope.clock_scope_ref) {
              refuse("minimum_scope_label_changed",
                "this authoritative scope's inventory was opened under another label; the label is provenance, is recorded once, and is never rewritten by a later write",
                { invariant: "j1_minimum_scope_label_is_not_identity",
                  recorded: inventory.clock_scope_ref, supplied: scope.clock_scope_ref });
            }
          }
          // THE COMPARE-AND-SWAP. An explicit null opens the inventory and
          // succeeds only against an empty one; anything else must be the head.
          if (expected_prior_admission_digest === null) {
            if (head) {
              refuse("minimum_inventory_already_open",
                "this authoritative scope already holds an admitted-minimum inventory; a null prior opens one and an inventory is opened once. Append to the inventory that exists, or refuse",
                { invariant: "j1_minimum_exact_prior_admission_digest",
                  clock_scope_key: scope.clock_scope_key,
                  head_admission_digest: head.admission_digest,
                  head_admission_ordinal: head.admission_ordinal });
            }
          } else if (!head) {
            refuse("minimum_prior_admission_unknown",
              "this inventory holds no admissions, so there is no prior digest to match; open it with an explicit null prior",
              { invariant: "j1_minimum_exact_prior_admission_digest",
                supplied: expected_prior_admission_digest });
          } else if (head.admission_digest !== expected_prior_admission_digest) {
            refuse("minimum_stale_prior_admission_digest",
              "the prior admission digest is not the current head; another writer admitted first and this admission was computed against an inventory that is no longer the head",
              { invariant: "j1_minimum_exact_prior_admission_digest",
                head: head.admission_digest, supplied: expected_prior_admission_digest,
                head_admission_ordinal: head.admission_ordinal });
          }
          // NO ARTIFACT IS ADMITTED TWICE. Re-presenting one under a fresh
          // instant is a replay of evidence, not a second admission, and the
          // kernel refuses a duplicated receipt fatally.
          const already = (admissions ?? []).find(row => row.receipt_digest === receipt_digest);
          if (already) {
            refuse("minimum_receipt_already_admitted",
              "this exact receipt is already in this inventory; re-presenting one artifact under a new admission instant is a replay, not a second admission",
              { invariant: "j1_minimum_receipt_never_readmitted", receipt_digest,
                recorded_admission_ordinal: already.admission_ordinal,
                recorded_admitted_at: already.admitted_at });
          }
          // THE ADMISSION INSTANT, ASSERTED AT WRITE TIME AGAINST THE ARTIFACT.
          assertTimestampText(admitted_at, "admitted_at");
          if (instant(view.observed_at) > instant(admitted_at)) {
            refuse("minimum_admission_precedes_observation",
              "this receipt reports being observed after the instant the record layer admitted it. A single instant of skew between the producer's clock and this one is fatal to every later evaluation of the inventory, and an append-only inventory cannot shed the row, so it is refused here",
              { invariant: "j1_minimum_admission_not_before_observation",
                observed_at: view.observed_at, admitted_at });
          }
          // THE LEDGER IS STORED IN THE KERNEL'S OWN SELECTION ORDER, AND AN
          // APPEND ONLY EVER EXTENDS IT.
          //
          // journey-one-clock.v5.js orders candidates by (admitted_at,
          // receipt_digest) and takes the FIRST ELIGIBLE one, skipping every
          // inadmissible attempt. So the row it selected is not necessarily the
          // first row in this ledger, and comparing a newcomer against
          // admissions[0] is the wrong comparison: with a failed attempt at T0
          // and a passing one at T1, the kernel's origin is the T1 row, and a
          // second T1 row with a LOWER digest ties nothing at T0 while taking
          // the origin away from the row that had it — which surfaces on the
          // next evaluation as `origin_reset_or_rebase` against a retained
          // history, leaving the clock unreadable rather than wrong.
          //
          // The conservative rule that closes it WITHOUT this rail computing
          // eligibility — which is the kernel's and stays the kernel's — is to
          // keep the stored sequence in that same total order: admitted_at
          // non-decreasing, and STRICTLY INCREASING receipt digest within one
          // admitted_at group. A new row then sorts after every stored row, so
          // it cannot be preferred to any of them, whichever ones were eligible.
          // Comparing against the HEAD alone is sufficient because the head is
          // the maximum of its own group under this invariant; the readback
          // re-checks the whole sequence rather than trusting the induction.
          if (head && instant(admitted_at) < instant(head.admitted_at)) {
            refuse("minimum_admission_out_of_order",
              "an append-only admission inventory only ever gains LATER admissions; this one is dated before the head",
              { invariant: "j1_minimum_first_origin_never_replaced",
                head_admitted_at: head.admitted_at, admitted_at });
          }
          if (head && instant(admitted_at) === instant(head.admitted_at) &&
              receipt_digest <= head.receipt_digest) {
            refuse("minimum_admission_would_rebase_origin",
              "this admission shares the head's admission instant and does not sort after it, so the kernel could prefer it to a row already stored in that group — including the eligible row it has already selected as the origin. Within one admission instant the receipt digest strictly increases, because that is the order the kernel selects in",
              { invariant: "j1_minimum_first_origin_never_replaced",
                head_admitted_at: head.admitted_at, head_receipt_digest: head.receipt_digest,
                admitted_at, receipt_digest });
          }
          // The inventory is OPENED only once everything above has passed, and
          // it is idempotent for the exact binding. The journal seals the same
          // fields again from its own rows, because a direct writer never runs
          // the clauses above.
          await journal.openInventory({
            clock_scope_key: scope.clock_scope_key, clock_scope_ref: scope.clock_scope_ref,
            tenant, scope: copy(scope.scope),
            minimum_receipt_ttl_policy_ms: policy.maximum_minimum_receipt_ttl_ms,
            minimum_environment_manifest_digest: policy.minimum_environment_manifest_digest,
          });
          const previous_admission_digest = head ? head.admission_digest : null;
          return {
            clock_scope_key: scope.clock_scope_key, clock_scope_ref: scope.clock_scope_ref,
            tenant, admitted_at, receipt_digest, receipt: copy(view),
            gate_id: view.gate_id, receipt_producer_step_ref: view.receipt_producer_step_ref,
            observed_at: view.observed_at, ttl_expires_at: view.ttl_expires_at,
            status: view.status,
            minimum_receipt_ttl_policy_ms: policy.maximum_minimum_receipt_ttl_ms,
            minimum_environment_manifest_digest: policy.minimum_environment_manifest_digest,
            previous_admission_digest,
            admission_digest: journeyOneMinimumAdmissionDigest({
              admitted_at, clock_scope_key: scope.clock_scope_key,
              minimum_environment_manifest_digest: policy.minimum_environment_manifest_digest,
              minimum_receipt_ttl_policy_ms: policy.maximum_minimum_receipt_ttl_ms,
              previous_admission_digest, receipt_digest, tenant,
            }),
            provenance, written_by_actor_id: writer.actor_id,
          };
        },
      });
    },

    /**
     * Read one scope's admitted-minimum inventory deterministically, and
     * assemble the kernel's own `minimum_history` from the stored rows.
     *
     * Every row is rebuilt and re-hashed and every chain link is recomputed. A
     * tampered readback REFUSES rather than serving a shorter inventory: a
     * silently dropped attempt is exactly the discard this rail must not make.
     */
    async read(clockScopeKey = scope?.clock_scope_key ?? null) {
      if (clockScopeKey === null) {
        refuse("minimum_inventory_binding_required",
          "a read addresses one authoritative scope's inventory; this store was constructed without a scope and none was named",
          { invariant: "j1_minimum_inventory_scope_bound" });
      }
      assertDigestRef(clockScopeKey, "clock_scope_key");
      const inventory = await journal.readInventory(clockScopeKey);
      if (!inventory) {
        return deepFreeze({ schema_version: JOURNEY_ONE_MINIMUM_INVENTORY_READBACK_SCHEMA,
          clock_scope_key: clockScopeKey, tenant, exists: false,
          record_layer_cannot_prove: [...JOURNEY_ONE_MINIMUM_INPUT_STORE_CANNOT_PROVE],
          effects: V5_NO_EFFECTS });
      }
      if (inventory.tenant !== tenant) {
        refuse("cross_tenant_minimum_admission", "that inventory belongs to another tenant",
          { invariant: "j1_minimum_tenant_bound", stored: inventory.tenant, reading_as: tenant });
      }
      const { ordered, head_admission_digest } = await readVerified(clockScopeKey);
      if (ordered.length === 0) {
        refuse("minimum_inventory_empty",
          "an inventory exists with no admissions; the record layer cannot produce the history it claims to hold",
          { invariant: "j1_minimum_content_rebuilds_to_its_digest", clock_scope_key: clockScopeKey });
      }
      return deepFreeze({
        schema_version: JOURNEY_ONE_MINIMUM_INVENTORY_READBACK_SCHEMA,
        clock_scope_key: clockScopeKey, tenant, exists: true,
        clock_scope_ref: inventory.clock_scope_ref,
        clock_scope: copy(inventory.scope ?? null),
        // The accepted source bindings this inventory was SEALED under. The
        // composer reads the projection's TTL policy and environment manifest
        // from here, and never from a receipt being judged.
        minimum_receipt_ttl_policy_ms: inventory.minimum_receipt_ttl_policy_ms,
        minimum_environment_manifest_digest: inventory.minimum_environment_manifest_digest,
        admission_count: ordered.length,
        head_admission_digest,
        // THE KERNEL'S OWN SHAPE, assembled from the stored rows and nothing
        // else: closed({admitted_at, receipt}) is exactly what evaluate() reads.
        minimum_history: ordered.map(row => ({
          admitted_at: row.admitted_at, receipt: copy(row.receipt) })),
        admissions: ordered.map(row => ({
          admission_ordinal: row.admission_ordinal, admitted_at: row.admitted_at,
          receipt_digest: row.receipt_digest, status: row.status,
          observed_at: row.observed_at, ttl_expires_at: row.ttl_expires_at,
          previous_admission_digest: row.previous_admission_digest ?? null,
          admission_digest: row.admission_digest, recorded_at: row.recorded_at,
          provenance: copy(row.provenance ?? null) })),
        // Said out loud on every read.
        record_layer_cannot_prove: [...JOURNEY_ONE_MINIMUM_INPUT_STORE_CANNOT_PROVE],
        gate_admitted_by_record_layer: false,
        effects: V5_NO_EFFECTS,
      });
    },
  });
}

// ---------------------------------------------------------------------------
// THE READER: one stored inventory assembled into the M01 projection.
// ---------------------------------------------------------------------------

/**
 * Compose the kernel's projection from a stored inventory and the accepted
 * source bindings whose homes are elsewhere.
 *
 * IT IS NOT A VERIFIER AND IT DOES NOT AUTHENTICATE ANYTHING. createJourneyOneClock
 * refuses to exist without a verifySnapshot callback, and what this returns is
 * the OBJECT that callback will be asked about — never its answer. The result is
 * wrapped, so nothing can hand this straight to a store as a verified snapshot.
 *
 * WHAT IT READS FROM WHERE, and this is the whole point of the seam:
 *   * `binding.subject/candidate/policy_digest` and the benchmark's copies of
 *     them come from the ACCEPTED SCOPE this inventory was opened under.
 *   * `binding.maximum_minimum_receipt_ttl_ms` and
 *     `binding.minimum_environment_manifest_digest` come from the ACCEPTED
 *     POLICY SEALED ON THE INVENTORY.
 *   * `benchmark.deadline_contract` is JOURNEY_ONE_DEADLINE_CONTRACT itself.
 *   * `minimum_history` comes from the stored rows.
 *   * everything else comes from a construction-time trusted binding.
 * NONE of them is read off a receipt being judged. A composer that copied a
 * receipt's own digests into the binding would make every later comparison the
 * tautology the binding exists to replace.
 *
 * `accepted_sources` CARRIES ACCEPTANCE-ENVELOPE FIELDS ON PURPOSE, and that is
 * why it is a construction-time binding rather than a request: `benchmark_
 * accepted_at` and `benchmark_accepted_by_identity` are the record of an act a
 * verified partner performed, which A00's rail refuses outright from a caller.
 * They reach here only because trusted server code read them from the accepted
 * benchmark acceptance and handed them over.
 *
 * `benchmark_manifest` IS THE ARTIFACT THOSE THREE DESCRIBE, AND IT IS REQUIRED.
 * The envelope is derived from it by journeyOneMinimumBenchmarkAcceptedSources
 * and must MATCH the accepted_sources handed over, and the manifest's own
 * subject, candidate and policy digests must be this inventory's accepted scope
 * — so a real manifest for another program is refused rather than quietly
 * composed into this projection. There is deliberately NO path on which the three
 * are asserted instead of derived: an optional check is one a caller can decline
 * exactly when it would have mattered, and this rail cannot tell an invented
 * digest, instant or acceptor from a real one. A composer with no manifest is a
 * composer that cannot exist.
 * It is still shape and not authentication: see the derivation's own note.
 */
export function createJourneyOneClockProjectionComposer(
  { store, accepted_sources, benchmark_manifest } = {}) {
  if (!store || typeof store.read !== "function" || !isPlainObject(store.clock_scope)) {
    refuse("invalid_shape",
      "a projection composer needs a minimum input store constructed with its authoritative clock scope",
      { path: "store" });
  }
  closed(accepted_sources ?? {}, JOURNEY_ONE_MINIMUM_ACCEPTED_SOURCE_FIELDS, "accepted_sources");
  assertDigestRef(accepted_sources.benchmark_manifest_digest,
    "accepted_sources.benchmark_manifest_digest");
  assertDigestRef(accepted_sources.production_environment_manifest_digest,
    "accepted_sources.production_environment_manifest_digest");
  assertTimestampText(accepted_sources.benchmark_accepted_at,
    "accepted_sources.benchmark_accepted_at");
  assertPositiveMs(accepted_sources.maximum_completion_receipt_ttl_ms,
    "accepted_sources.maximum_completion_receipt_ttl_ms");
  closed(accepted_sources.benchmark_accepted_by_identity,
    ["actor_id", "authority_class", "session_ref"],
    "accepted_sources.benchmark_accepted_by_identity");
  const sources = deepFreeze(copy(accepted_sources));
  const scope = store.clock_scope;

  // THE ARTIFACT THE ENVELOPE DESCRIBES IS REQUIRED, and its absence is refused
  // by name rather than defaulted into a composition nothing checked.
  if (!isPlainObject(benchmark_manifest)) {
    refuse("benchmark_manifest_required",
      `composing a projection requires the accepted ${BENCHMARK_MANIFEST_SCHEMA} the benchmark acceptance envelope is derived from. The manifest digest, the acceptance instant and the acceptor seat are the record of an act another authority performed; taken as caller literals they are three strings this rail cannot tell from invented ones, and the kernel would start a clock on them`,
      { path: "benchmark_manifest",
        derived_fields: [...JOURNEY_ONE_MINIMUM_BENCHMARK_DERIVED_SOURCE_FIELDS],
        trusted_policy_fields: [...JOURNEY_ONE_MINIMUM_TRUSTED_POLICY_SOURCE_FIELDS] });
  }
  // THE ENVELOPE AGAINST THE ARTIFACT IT DESCRIBES. Compared field by field
  // through digest() so the identity seat is compared as a whole object rather
  // than by a hand-written walk of its three keys.
  const derived = journeyOneMinimumBenchmarkAcceptedSources(benchmark_manifest);
  for (const field of JOURNEY_ONE_MINIMUM_BENCHMARK_DERIVED_SOURCE_FIELDS) {
    if (digest(derived[field]) !== digest(sources[field])) {
      refuse("benchmark_accepted_sources_not_derived",
        `accepted_sources.${field} is not the value the supplied accepted benchmark manifest produces; the acceptance envelope is derived from the manifest and never asserted beside it`,
        { field, derived: copy(derived[field]), supplied: copy(sources[field]) });
    }
  }
  // AND THE MANIFEST IS THIS INVENTORY'S. A genuine accepted manifest for
  // another accepted subject would otherwise compose a projection whose
  // benchmark half is about one program and whose binding half is about
  // another; the kernel compares each receipt to the binding and would never
  // see the disagreement, because the manifest digest is not in that
  // comparison at all.
  for (const [field, expected] of [
    ["subject_digest", scope.scope.benchmark_subject_digest],
    ["candidate_digest", scope.scope.benchmark_candidate_digest],
    ["policy_digest", scope.scope.benchmark_policy_digest],
  ]) {
    if (benchmark_manifest[field] !== expected) {
      refuse("benchmark_manifest_scope_mismatch",
        `the supplied accepted benchmark manifest's ${field} is not this inventory's accepted scope's`,
        { field, expected, supplied: benchmark_manifest[field] });
    }
  }
  /**
   * WHERE EACH ACCEPTED SOURCE FIELD CAME FROM, reported on the composer and on
   * every composition it produces. The three envelope fields are derived, and
   * the two policy fields are not derivable from any benchmark manifest and stay
   * trusted construction-time inputs — a reader is entitled to see which is
   * which, and to see in the same place that neither is authentication.
   */
  const envelopeProvenance = deepFreeze({
    derived_from_validated_accepted_manifest: true,
    derived_fields: [...JOURNEY_ONE_MINIMUM_BENCHMARK_DERIVED_SOURCE_FIELDS],
    trusted_policy_fields: [...JOURNEY_ONE_MINIMUM_TRUSTED_POLICY_SOURCE_FIELDS],
    human_acceptance_authenticated: false,
    statement: "the benchmark manifest digest, acceptance instant and acceptor seat were derived from an accepted benchmark-manifest.v1 through A00's validateBenchmarkManifest, and that manifest's own subject, candidate and policy digests are this inventory's accepted scope. That is SHAPE: no live actor was consulted and no acceptance record was read, so it is not evidence that a verified partner accepted anything. The completion TTL maximum and the production environment manifest digest are on no benchmark manifest at all and remain trusted construction-time inputs.",
  });

  return Object.freeze({
    accepted_sources: sources,
    benchmark_envelope: envelopeProvenance,
    clock_scope_key: scope.clock_scope_key,
    /**
     * Read the inventory and compose the projection as of one instant.
     *
     * The four remaining inventories have no producer in this repository and are
     * supplied by the trusted caller. Each is REQUIRED, with no default: an
     * omitted `pauses` is a caller who has not said whether there are any, and a
     * default of [] would silently claim there are none.
     */
    async compose(args = {}) {
      closed(args, JOURNEY_ONE_MINIMUM_COMPOSE_FIELDS, "compose");
      const { as_of, completion, completion_expectation, pauses, amendments, history } = args;
      assertTimestampText(as_of, "as_of");
      for (const [name, value] of [["pauses", pauses], ["amendments", amendments]]) {
        if (!Array.isArray(value)) {
          refuse("invalid_shape", `${name} must be an array supplied by the trusted caller`,
            { path: name });
        }
      }
      const inventory = await store.read(scope.clock_scope_key);
      if (inventory.exists !== true) {
        refuse("minimum_inventory_unavailable",
          "this authoritative scope holds no admitted-minimum inventory, so there is no origin to read. That is an absence of evidence in this record layer, not a proof of absence about the record as a whole",
          { invariant: "j1_minimum_inventory_scope_bound",
            clock_scope_key: scope.clock_scope_key });
      }
      // A PROJECTION THE KERNEL WOULD FATALLY REFUSE IS NOT COMPOSED. The kernel
      // refuses `future_admission` when an admission postdates as_of, so an as_of
      // behind the inventory is named here rather than surfacing as a verdict
      // about a row that is perfectly good.
      const last = inventory.minimum_history[inventory.minimum_history.length - 1];
      if (instant(as_of) < instant(last.admitted_at)) {
        refuse("projection_as_of_precedes_admission",
          "as_of is before the most recent admission in this inventory; the kernel refuses an admission it cannot yet have seen",
          { as_of, latest_admitted_at: last.admitted_at });
      }
      // A supplied history is read by THE KERNEL'S OWN reader, on the kernel's
      // own terms, so a stored history this rail could not hand on refuses under
      // the kernel's name rather than a second vocabulary. This file owns no
      // history validator.
      if (history !== null) readJourneyOneClockHistory(history, history?.evaluated_at);

      const projection = {
        schema_version: JOURNEY_ONE_CLOCK_PROJECTION,
        tenant: scope.tenant,
        as_of,
        binding: {
          subject_digest: scope.scope.benchmark_subject_digest,
          candidate_digest: scope.scope.benchmark_candidate_digest,
          policy_digest: scope.scope.benchmark_policy_digest,
          minimum_environment_manifest_digest: inventory.minimum_environment_manifest_digest,
          production_environment_manifest_digest: sources.production_environment_manifest_digest,
          maximum_minimum_receipt_ttl_ms: inventory.minimum_receipt_ttl_policy_ms,
          maximum_completion_receipt_ttl_ms: sources.maximum_completion_receipt_ttl_ms,
        },
        benchmark: {
          manifest_digest: sources.benchmark_manifest_digest,
          subject_digest: scope.scope.benchmark_subject_digest,
          candidate_digest: scope.scope.benchmark_candidate_digest,
          policy_digest: scope.scope.benchmark_policy_digest,
          deadline_contract: copy(JOURNEY_ONE_DEADLINE_CONTRACT),
          accepted_at: sources.benchmark_accepted_at,
          accepted_by_identity: copy(sources.benchmark_accepted_by_identity),
        },
        minimum_history: copy(inventory.minimum_history),
        completion: copy(completion ?? null),
        completion_expectation: copy(completion_expectation ?? null),
        pauses: copy(pauses), amendments: copy(amendments),
        history: copy(history ?? null),
      };
      closed(projection, PROJECTION_FIELDS, "projection");
      return deepFreeze({
        schema_version: JOURNEY_ONE_MINIMUM_PROJECTION_INPUTS_SCHEMA,
        clock_scope_key: scope.clock_scope_key,
        clock_scope_ref: scope.clock_scope_ref,
        head_admission_digest: inventory.head_admission_digest,
        admission_count: inventory.admission_count,
        projection,
        // Stated on the object so nothing downstream can read composition as
        // verification. This is an INPUT to verifySnapshot, not its output.
        benchmark_envelope: envelopeProvenance,
        authenticated: false,
        authenticated_by: null,
        trusted_verifier_still_required: true,
        record_layer_cannot_prove: [...JOURNEY_ONE_MINIMUM_INPUT_STORE_CANNOT_PROVE],
        effects: V5_NO_EFFECTS,
      });
    },
  });
}

// ---------------------------------------------------------------------------
// THE MISSING PUBLIC AUTHORITY.
// ---------------------------------------------------------------------------

/**
 * The honest, zero-effect statement of what a PUBLIC admit verb still needs. It
 * describes MISSING RECORDS AND MISSING PRODUCERS; it grants nothing, reads
 * nothing and configures nothing.
 */
export const JOURNEY_ONE_MINIMUM_INPUT_AUTHORITY_REQUIREMENT = deepFreeze({
  binding_ref: "binding:journey-one-minimum-admitted-receipt-issuance",
  resolved: false,
  scope: "the authority available in this repository, not a claim about what exists outside it",
  why_unresolved: [
    "No live producer of an admitted foundation-assurance-minimum receipt exists here. benchmark-minimum.v5.js PROPOSES one and marks it receipt_state: proposed_not_issued, issued: false; there is no issuance adapter, so no genuine artifact can reach this rail and NO CLOCK HAS BEEN STARTED.",
    "The join's own verifier obligations are unmet: the Gate Zero outcome digest and the benchmark coverage fact must be authenticated from a stored measurement artifact and a live evaluator identity, and nothing here does that.",
    "The Gate Zero and coverage bindings have no home across this seam at all. consumer-gate-receipt.v1 carries neither and M01's projection has no slot for either, so a receipt admitted here is not evidence that they reached the clock. Storing caller-supplied coverage digests would mint the binding instead of carrying it.",
    "No live producer of a journey-one-kernel-production terminus receipt exists, so a composed projection's completion and completion_expectation have no authenticated source in this repository.",
  ],
  required_to_resolve: [
    "Land the issuance adapter that turns A00's proposed receipt into an issued artifact with authenticated identities, and make it the only producer whose output this rail admits.",
    "Land the authenticated Gate Zero and benchmark-coverage binding beside the receipt, with an explicit home, so an admitted row can carry the evidence the join consumed rather than leaving it unbound.",
    "Land the terminus producer, and derive completion_expectation and the completion TTL maximum from the accepted kernel scope and policy, never from the receipt being judged.",
    "Hand the accepted scope and accepted minimum policy to createJourneyOneClockMinimumInputStore at construction from the same trusted reader that authenticates the projection. Both are construction-time bindings on purpose: a scope taken from a request would let one caller open a second inventory and hand a fresh origin to a clock that already has one.",
  ],
  what_landed_here: {
    resolved: true,
    exact_requirement: "The admitted-minimum ledger the projection's minimum_history is read from, stamping admitted_at from the same trusted clock and asserting it at write time, as named in JOURNEY_ONE_CLOCK_INPUT_AUTHORITY_REQUIREMENT.required_to_resolve.",
    what_landed: "An append-only, per-authoritative-scope admitted-minimum inventory with a server-stamped admission instant asserted against the receipt's own observation, a hash-chained compare-and-swap, exact-payload idempotency, a sealed accepted TTL policy and environment manifest, and a reader that assembles the kernel's exact minimum_history from the stored rows and composes the eleven-field projection from accepted source bindings.",
    still_not_resolved: "Everything in why_unresolved. The storage is implemented and exercised; what is missing is a legitimate producer whose artifact this rail could admit, which is why the public verb below still refuses.",
  },
  explicitly_refused: [
    "a caller-supplied admitted_at, or any admission instant not taken from the record layer's own trusted clock",
    "a caller-chosen clock scope or accepted minimum policy",
    "a fabricated or self-hashed minimum receipt, a minted session_ref, or an envelope carrying { verified: true }",
    "a benchmark acceptance envelope asserted beside a projection rather than derived from the accepted manifest it describes; the composer refuses to exist without that manifest, and refuses one whose digests are not this inventory's accepted scope",
    "a coverage or Gate Zero binding invented here to fill a slot no producer supplies",
    "a public admit verb that steps around the missing issuance producer",
    "a second benchmark, a second receipt validator, or a second history validator in this file",
  ],
  remaining_trust_boundary:
    "Even with the issuance adapter landed, this record layer stores an artifact produced elsewhere. It can recompute every digest, enforce the admission invariants and refuse a tampered readback; it cannot authenticate the artifact, and nothing here should be read as saying it can.",
});

/**
 * THE PRIVATE FAIL-CLOSED AUTHENTICATED-INPUT READER. Deliberately not exported,
 * parameterless and without a configuration path: an exported stub is a callable
 * claim about the missing authority, and one that takes an argument is one edit
 * away from being a configuration surface. It always throws, and the public verb
 * calls it BEFORE it issues any query.
 */
function readAuthenticatedMinimumAdmissionInputs() {
  refuse("minimum_input_authority_unbound",
    "admitting a foundation-assurance-minimum receipt requires an issued artifact from an authenticated producer, and this repository holds none: benchmark-minimum.v5.js proposes a receipt and does not issue one, no issuance adapter exists, and the Gate Zero and coverage bindings the join consumed have no home across this seam. A caller-supplied receipt is not a substitute. This verb therefore fails closed before it issues any query. The storage, compare-and-swap, idempotency and readback underneath are implemented and exercised; what is missing is a legitimate producer.",
    JOURNEY_ONE_MINIMUM_INPUT_AUTHORITY_REQUIREMENT);
}

/** A reader is entitled to know the gate is shut and why. Zero effect. */
export function journeyOneClockMinimumInputStoreIntegrationRequirements() {
  return deepFreeze({
    schema_version: JOURNEY_ONE_MINIMUM_INPUT_INTEGRATION_SCHEMA,
    store_schema_version: JOURNEY_ONE_MINIMUM_INPUT_STORE_SCHEMA,
    projection_schema_version: JOURNEY_ONE_CLOCK_PROJECTION,
    receipt_schema_ref: CONSUMER_GATE_RECEIPT_SCHEMA,
    receipt_producer_step_ref: MINIMUM_STEP_REF,
    origin_gate_id: JOURNEY_ONE_DEADLINE_CONTRACT.clock_origin_gate_id,
    storage_implemented: true,
    storage_notes: [
      "The admission instant is the record layer's own and is never a caller field. The durable half derives it from now(), the transaction timestamp, so the value the journal reads and the value the append function re-derives are one reading rather than two.",
      "Admissions are hash-chained: each link hashes the previous link, the sealed accepted policy and environment, the scope key and the receipt digest, so a row cannot be removed, reordered or re-dated without every later link failing to rebuild.",
      "The ledger is stored in the kernel's own (admitted_at, receipt_digest) selection order and an append only ever extends it, so a later row cannot be preferred to one the kernel already selected as the origin. This rail computes no eligibility: the kernel skips inadmissible attempts, and storing in its selection order is what makes the guarantee hold across whatever it skipped. The order is enforced on write against the head and re-validated across the whole sequence on read.",
      "A receipt's identity is digest(journeyOneClockMinimumReceiptView(receipt)), which is by A00's own contract the exact origin_receipt_digest the kernel records. This file owns no second receipt validator and no second hash.",
      "Fatal-in-kernel facts are refused at admission; inadmissible-but-ordinary ones are stored. A non-passing attempt and a receipt whose window later lapses are real history and this rail has no discard path. The refused set includes the shape facts A00's seam validator leaves open -- the safe:/session: prefixes, the identity seats, the fixture digest and the comparator bounds -- because each is fatal rather than skipped. Seat INDEPENDENCE is the one fatal fact deliberately left to the join that proposes a receipt, and it is disclosed rather than implied.",
      "The durable journal asserts that its statements share one transaction, reading txid_current() after the lock and again before the append: under autocommit the advisory lock is already released and the admission instant is no longer one reading, so it refuses with nothing written.",
      "Update, delete and truncate are refused by TWO triggers per relation. A row-level trigger never sees TRUNCATE, and TRUNCATE cannot be revoked from the table owner, so the statement-level trigger is what makes the claim true rather than the grant.",
      "The scope derivation is the clock rail's own journeyOneClockScopeBinding: one domain tag, one preimage, and the human label is provenance rather than identity.",
      "ops/journey-one-clock-input-store.candidate.sql is candidate source: it has not been applied as a numbered migration and has never been executed. It depends on ops.j1_clock_scope_digest from the clock rail's candidate SQL rather than deriving a second scope key, and on ops.benchmark_utf16_length from the benchmark-acceptance rail's rather than defining a second UTF-16 counter -- both are candidate source too, so neither can be applied after this one.",
      "The record layer reads jsonb TYPES, not the text `->>` renders a number, boolean or null as: a numeric comparator or actor_id passes a bare regex or length while the kernel refuses it outright, and a BOTH-homes invariant that admits on one side what it refuses on the other is not one invariant.",
    ],
    clock_scope_and_policy_required_for_writes: true,
    input_authority: JOURNEY_ONE_MINIMUM_INPUT_AUTHORITY_REQUIREMENT,
    public_admit_available: false,
    public_admit_blocked_by: [JOURNEY_ONE_MINIMUM_INPUT_AUTHORITY_REQUIREMENT.binding_ref],
    trusted_integration_contract: {
      entry_point: "createJourneyOneClockMinimumInputStore({ journal, actor, clock_scope, accepted_minimum_policy })",
      reader: "createJourneyOneClockProjectionComposer({ store, accepted_sources, benchmark_manifest }).compose({ as_of, completion, completion_expectation, pauses, amendments, history })",
      benchmark_envelope: "the accepted benchmark manifest is REQUIRED at construction, and the three acceptance-envelope fields of accepted_sources are DERIVED from it by journeyOneMinimumBenchmarkAcceptedSources: a mismatch, a manifest for another accepted scope, and a missing manifest are each refused by name. Deriving them is SHAPE validation through A00's own validateBenchmarkManifest and is never a claim that a verified partner was authenticated; the other two accepted source fields are on no benchmark manifest at all and stay trusted construction-time policy inputs.",
      records: "one issued minimum receipt as an input the kernel may read, with the record layer's own admission instant and scoped provenance",
      does_not_record: "an acceptance, a gate admission, a verification, a clock start or any claim about a deadline",
      composition_loop: "createJourneyOneClockRuntime({ composer, clock, clock_store, present_projection, verifier_ref }) in journey-one-clock-runtime.v5.js is the seat that joins this composer to the clock rail: it reads the clock store's head once, composes against that exact head, and before anything is written asserts BOTH that the kernel judged this exact composition -- its v2 authenticated_projection_digest against the digest of the projection composed here -- and that the origin the kernel selected is a receipt THIS inventory admitted. The second is the fact only this rail can supply; the first is what makes the composition identity rather than a family resemblance. It reads this inventory and never writes to it, and it cannot run here, because no receipt can be admitted and compose refuses with minimum_inventory_unavailable",
    },
    admission_invariants: JOURNEY_ONE_MINIMUM_ADMISSION_INVARIANTS.map(i => ({ ...i })),
    record_layer_cannot_prove: [...JOURNEY_ONE_MINIMUM_INPUT_STORE_CANNOT_PROVE],
    clock_started: false,
    effects: V5_NO_EFFECTS,
  });
}

/**
 * The verbs. DELIBERATELY NOT ADDED TO ANY TOOL INDEX BY THIS SLICE. Registering
 * a verb is a separate reviewed act, and the write verb cannot succeed in any
 * case.
 */
export function journeyOneClockMinimumInputStoreTools({ withEnvelope, ToolError }) {
  const toolRefuse = (error, detail) => { throw new ToolError({ error, ...detail }); };
  const asToolError = (error) => {
    // The refusal types of the rails this file reuses travel too: A00's seam
    // validator raises BenchmarkMinimumError and its authority guard raises
    // BenchmarkAcceptanceStoreError, and a caller should meet those codes rather
    // than a second vocabulary for one fact.
    if (error instanceof JourneyOneMinimumInputStoreError ||
        ["JourneyOneClockError", "JourneyOneClockStoreError", "BenchmarkMinimumError",
          "BenchmarkAcceptanceStoreError"].includes(error?.name)) {
      toolRefuse(error.code, { message: error.message,
        ...(error.detail !== undefined ? { detail: error.detail } : {}) });
    }
    throw error;
  };
  const check = (fn) => { try { return fn(); } catch (error) { return asToolError(error); } };

  return {
    "read-journey-one-minimum-admission-inventory": {
      write: false,
      description: "Read one authoritative clock scope's admitted-minimum input inventory: every admission rebuilt from its stored row, its receipt re-hashed to the digest it was admitted under, the chain link recomputed, and the kernel's own minimum_history assembled from the rows in admission order. It reports in its own fields what this record layer cannot prove -- that the artifact is a receipt a real oracle issued, that its seats are independent, that the Gate Zero and coverage bindings reached it, or anything at all about a deadline -- and produces no effect. A tampered readback refuses rather than serving a shorter inventory.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: { clock_scope_key: { type: "string", pattern: "^sha256:[0-9a-f]{64}$" } },
        required: ["clock_scope_key"],
      },
      handler: async (c, actor, args) => {
        const store = check(() => createJourneyOneClockMinimumInputStore({
          journal: createPostgresJourneyOneMinimumAdmissionJournal({
            query: (sql, params) => c.query(sql, params) }),
          actor,
        }));
        try {
          return { ok: true, ...(await store.read(args.clock_scope_key)),
            integration: journeyOneClockMinimumInputStoreIntegrationRequirements() };
        } catch (error) { return asToolError(error); }
      },
    },

    "admit-journey-one-minimum-receipt": {
      write: true,
      description: "REFUSES TODAY, BY DESIGN. Admitting a foundation-assurance-minimum receipt requires an issued artifact from an authenticated producer, and this repository holds none: benchmark-minimum.v5.js proposes a receipt and marks it proposed_not_issued, no issuance adapter exists, and the Gate Zero and benchmark-coverage bindings the join consumed have no home across this seam. A caller-supplied receipt is not a substitute. This verb calls the private fail-closed reader BEFORE it issues any query, so no inventory can be half-opened or mistaken for one that nearly worked. The storage, admission-instant binding, compare-and-swap, idempotency and readback underneath are implemented and exercised; the trusted entry point is createJourneyOneClockMinimumInputStore, which only server code holding the accepted scope and policy can construct.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string" },
          source_ref: { type: "string" },
          // Present so the shape a live caller will use is stated in code rather
          // than only in prose. Nothing reads it: the refusal happens first, and
          // there is deliberately no admitted_at property at any point.
          expected_prior_admission_digest: { type: ["string", "null"], pattern: "^sha256:[0-9a-f]{64}$" },
        },
        required: ["idempotency_key", "expected_prior_admission_digest", "source_ref"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "admit-journey-one-minimum-receipt", args, async () => {
        // ORDER IS DELIBERATE. The writer is derived from the live actor first,
        // then the input authority refuses ahead of every query, which is what
        // makes "this verb cannot admit a receipt today" observable rather than
        // merely asserted.
        check(() => deriveJourneyOneClockWriter(actor));
        const inputs = check(() => readAuthenticatedMinimumAdmissionInputs());

        // UNREACHABLE UNTIL THE ISSUANCE PRODUCER EXISTS. Written out rather than
        // stubbed so landing it is a change to one function and not a fresh set
        // of decisions made by whoever happens to land it.
        const store = createJourneyOneClockMinimumInputStore({
          journal: createPostgresJourneyOneMinimumAdmissionJournal({
            query: (sql, params) => c.query(sql, params) }),
          actor,
          // Both come from the same trusted reader that authenticates the
          // artifact, and from nowhere else. Neither is read off `args`.
          clock_scope: inputs.clock_scope,
          accepted_minimum_policy: inputs.accepted_minimum_policy,
        });
        return { ...(await store.admit({
          receipt: inputs.receipt, idempotency_key: args.idempotency_key,
          expected_prior_admission_digest: args.expected_prior_admission_digest,
          claimed_receipt_digest: null, source_ref: args.source_ref,
        })), effects: JOURNEY_ONE_MINIMUM_ADMISSION_EFFECTS };
      }),
    },
  };
}
