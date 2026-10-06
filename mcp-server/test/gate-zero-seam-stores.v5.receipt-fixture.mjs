// A SECOND store fixture, for the one clause production rows cannot separate.
//
// WHY IT EXISTS. In production
// ops.accept_sourced_work_request_outcome_feedback writes the acceptance
// receipt's hash EQUAL to the proposal's, so a reader that consulted the
// proposal's `feedback_hash` instead of the receipt's `accepted_feedback_hash`
// looks correct on every row the record layer will ever hold. Mutation testing
// found exactly that: swapping the two fields changed no assertion. The clause
// is the most load-bearing one in card 11 — it is what makes a forged hash fail
// to match a SIGNATURE rather than merely a proposal — and it was unproved.
//
// So these rows do what no production row does: they pull the two hashes apart,
// one work request per case, and the same three steps address them. Each case is
// a row the store could hold if something went wrong, which is the only
// condition under which the distinction matters.
//
// It lives in test/ for the same reason the other fixture does, and the same
// parser-backed import scan proves nothing in src/ can reach it.

// MODULE-PRIVATE, exactly as in the real store module, and for the same reason:
// the substituted reader reads `.because` straight into an answer, so the set it
// can come back with has to be a set nothing outside this file can enumerate or
// extend. Neither name is exported, so the export-parity check in the test still
// sees this file's surface as the real module's.
const REGISTERED_REASONS = Object.freeze([
  "the checks source answer did not parse",
  "the checks source credentials are not configured in this process",
  "the checks source refused the request",
  "the checks source was not reachable",
  "the database client is not available in this process",
  "the connection target for this store is not configured in this process",
  "the query did not finish",
  "the query did not address a row",
  "the configured checks repository is not the one this file serves",
  "the call did not finish",
  "the reason this store was unreachable is not a registered one",
]);

const REGISTERED_STORE_TOKENS = Object.freeze([
  "record-layer:work-request-outcome-feedback",
  "control-plane:ops.service+ops.run",
  "github:checks",
  "a-store-this-file-does-not-serve",
]);

function own(target, key, value, enumerable) {
  Object.defineProperty(target, key, { value, writable: false, enumerable, configurable: false });
}

/**
 * A GUARDED FIELD READ, the real store module's `cell` under its own name, and
 * it is here for the reason that module has one: the three fetchers below are
 * EXPORTS, the sweep calls every export with a revoked Proxy and with an object
 * whose getters throw, and a plain `query?.canaryRunKey` answers those with the
 * CALLER'S own text out of an exported callable.
 */
function cell(holder, key) {
  try {
    return holder === null || holder === undefined ? undefined : Reflect.get(Object(holder), key);
  } catch {
    return undefined;
  }
}

/**
 * A CASE IS ADDRESSED BY AN OWN STRING KEY OR IT IS NOT ADDRESSED AT ALL.
 * `TABLE[key]` with a caller's value reaches `constructor` and `__proto__` as
 * readily as it reaches a canary name, and coerces an object key through a
 * `toString` the caller wrote. Neither is a row this fixture holds.
 */
function rowsFor(table, key) {
  return typeof key === "string" && Object.hasOwn(table, key) ? table[key] : [];
}

/**
 * THE CLOSED SHAPE FOR AN EXPORTED CALLABLE — amendment 2 of 2026-09-12, clauses
 * (a) and (b) — and it belongs in a FIXTURE for a concrete reason rather than
 * for symmetry: this file is copied OVER src/gate-zero-seam-stores.v5.js, so
 * what the staged reader imports is THIS surface. A fixture whose exports carry
 * a `prototype`, answer `instanceof` with the intrinsic — which walks the LEFT
 * operand's chain and runs a caller's getPrototypeOf trap — or hand the raw
 * class back through `.constructor` is a weaker surface than the one production
 * ships, and every clause proved across the staged module would be proved over a
 * shape that never runs.
 *
 * Bound rather than bare, for the reason the real module gives: the engine's
 * refusal of a construction quotes a BARE arrow's own SOURCE TEXT back, and this
 * file's source is no more shippable than the real one's. A bound arrow is still
 * not a constructor and still has no `prototype`; the refusal names
 * `function () { [native code] }` and nothing of this file.
 */
function closedCallable(callable) {
  const closed = callable.bind(null);
  Object.defineProperty(closed, Symbol.hasInstance, {
    value: () => false, writable: false, enumerable: false, configurable: false,
  });
  return closed;
}

function causeKind(cause) {
  try {
    if (cause === undefined || cause === null) return "none";
    if (seamStoreInstance(cause)) return "a-seam-store-that-was-unreachable";
    if (cause instanceof Error) return "an-error";
    return "not-an-error";
  } catch {
    return "undetermined";
  }
}

// THE SAME SHAPE THE REAL STORE MODULE SHIPS, for the same reason and under the
// same two names: the class is MODULE-PRIVATE, what leaves is an arrow factory
// that builds one, and membership is asked by a guarded predicate rather than by
// `instanceof`. The substituted reader imports `isSeamStoreUnreachable` from
// whichever module file is in place, so this file owes both names.
class SeamStoreUnreachableType extends Error {
  constructor(storeRef, because, cause) {
    const store = REGISTERED_STORE_TOKENS.includes(storeRef)
      ? storeRef : "a-store-this-file-does-not-serve";
    const reason = REGISTERED_REASONS.includes(because)
      ? because : "the reason this store was unreachable is not a registered one";
    const message = `${store}: ${reason}`;
    super(message);
    own(this, "name", "SeamStoreUnreachable", false);
    own(this, "message", message, false);
    own(this, "stack", `SeamStoreUnreachable: ${message}`, false);
    own(this, "store_ref", store, true);
    own(this, "because", reason, true);
    own(this, "cause_kind", causeKind(cause), true);
    Object.freeze(this);
  }
}

export const seamStoreUnreachable = closedCallable(
  (storeRef, because, cause) => new SeamStoreUnreachableType(storeRef, because, cause));

/**
 * AND THE WALK IS BOUNDED, which the intrinsic `instanceof` is not: a Proxy
 * whose getPrototypeOf trap answers with ITSELF makes an unbounded walk never
 * return, and a hang is a refusal the caller chose. A chain longer than this is
 * not a chain that reaches this type.
 */
const PROTOTYPE_WALK_LIMIT = 100;

function seamStoreInstance(value) {
  try {
    if (value === null || (typeof value !== "object" && typeof value !== "function")) return false;
    let walked = Reflect.getPrototypeOf(value);
    for (let step = 0; step < PROTOTYPE_WALK_LIMIT; step += 1) {
      if (walked === null || walked === undefined) return false;
      if (walked === SeamStoreUnreachableType.prototype) return true;
      walked = Reflect.getPrototypeOf(walked);
    }
    return false;
  } catch {
    return false;
  }
}

export const isSeamStoreUnreachable = closedCallable(value => seamStoreInstance(value));

/**
 * AND AN INSTANCE IS NOT A ROUTE BACK TO THE CLASS EITHER. Every class installs
 * its own unwrapped self on `prototype.constructor`, so a refusal this file
 * returns WAS a route to a constructable class — the sixth review round's first
 * finding, and `Reflect.construct(error.constructor, [])` succeeded on it. It is
 * redefined as the arrow factory, non-writable and non-configurable, and the
 * prototype is frozen afterwards so it cannot be redefined back. The private
 * `Symbol.hasInstance` beside it keeps this file's OWN membership questions from
 * walking a hostile operand's chain with the intrinsic.
 */
Object.defineProperty(SeamStoreUnreachableType.prototype, "constructor", {
  value: seamStoreUnreachable, writable: false, enumerable: false, configurable: false,
});
Object.defineProperty(SeamStoreUnreachableType, Symbol.hasInstance, {
  value: seamStoreInstance, writable: false, enumerable: false, configurable: false,
});
Object.freeze(SeamStoreUnreachableType.prototype);

/** The hash every case below is asked about. */
export const FIXTURE_ASKED_HASH = `sha256:${"4".repeat(64)}`;
/** A different well-formed hash, used to separate the two fields. */
export const FIXTURE_OTHER_HASH = `sha256:${"7".repeat(64)}`;
/** Older accepted revisions for the same Work Request. */
const FIXTURE_OLDER_HASH = `sha256:${"8".repeat(64)}`;
const FIXTURE_OLDEST_HASH = `sha256:${"9".repeat(64)}`;
export const FIXTURE_PROPOSAL_ONLY_HASH = `sha256:${"6".repeat(64)}`;
const FIXTURE_OLDEST_PROPOSAL_HASH = `sha256:${"5".repeat(64)}`;

/**
 * WR-000046  the receipt carries the asked-about hash and the PROPOSAL carries a
 *            different one. Must ADMIT — which proves the receipt's field is the
 *            one consulted.
 * WR-000040  three accepted revisions, newest first. The current receipt and
 *            proposal agree; one historical proposal carries
 *            FIXTURE_PROPOSAL_ONLY_HASH while no receipt does. An omitted hash
 *            must select the current row, while naming the proposal-only hash
 *            must refuse — proving both currentness and receipt-side matching.
 * WR-000054  an acceptance receipt whose work_request_card detail is absent.
 *            Must refuse as an incomplete row rather than be synthesized into an
 *            accepted one with a null outcome.
 */
const PREDECESSOR_ROWS = Object.freeze({
  "WR-000046": Object.freeze([Object.freeze({
    status: "accepted", detail_row_count: 1,
    accepted_feedback_hash: FIXTURE_ASKED_HASH,
    feedback_hash: FIXTURE_OTHER_HASH,
  })]),
  "WR-000040": Object.freeze([
    Object.freeze({
      status: "accepted", detail_row_count: 1,
      accepted_feedback_hash: FIXTURE_OTHER_HASH,
      feedback_hash: FIXTURE_OTHER_HASH,
    }),
    Object.freeze({
      status: "accepted", detail_row_count: 1,
      accepted_feedback_hash: FIXTURE_OLDER_HASH,
      feedback_hash: FIXTURE_PROPOSAL_ONLY_HASH,
    }),
    Object.freeze({
      status: "accepted", detail_row_count: 1,
      accepted_feedback_hash: FIXTURE_OLDEST_HASH,
      feedback_hash: FIXTURE_OLDEST_PROPOSAL_HASH,
    }),
  ]),
  "WR-000054": Object.freeze([Object.freeze({
    status: "accepted", detail_row_count: 0,
    accepted_feedback_hash: FIXTURE_ASKED_HASH,
    feedback_hash: null,
  })]),
});

// ---------------------------------------------------------------------------
// THE PUBLIC SURFACE, in the closed shape of amendment 2, exactly as the other
// fixture and the real module carry it: bound arrows, no `prototype`, an own
// `Symbol.hasInstance` data property, and the query read through the guarded
// `cell`. This file is substituted for the store module too, so its surface owes
// the same shape.
//
// AND NO EXPORT HERE ANSWERS A LABEL EITHER. This file used to export an
// `unreachable` trigger string its predecessor fetcher compared a caller's query
// value against, which is the exported-constant-as-caller-label the standing
// rule forbids. The fault it injected lives in
// ./gate-zero-seam-fault-injection.testhelper.mjs now, wired in by closure at
// construction rather than reached by an address a caller can hold.
// ---------------------------------------------------------------------------

export const fetchPredecessorOutcomeRows = closedCallable(async query => ({
  store_ref: "record-layer:work-request-outcome-feedback",
  rows: rowsFor(PREDECESSOR_ROWS, cell(query, "workRequestRef")),
}));

/** Card 12 and card 13 are proved by the other fixture; here they hold nothing. */
export const fetchSchedulerLedgerRows = closedCallable(
  async () => ({ store_ref: "control-plane:ops.service+ops.run", rows: [] }));

export const fetchCheckConclusionRows = closedCallable(
  async () => ({ store_ref: "github:checks", rows: [] }));

/** And the candidate-build record store, which this fixture also holds nothing for. */
export const fetchCandidateBuildRecordRows = closedCallable(
  async () => ({ store_ref: "control-plane:ops.candidate-build-record", rows: [] }));
