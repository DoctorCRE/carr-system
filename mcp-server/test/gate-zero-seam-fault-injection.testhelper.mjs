// THE FAULTS, AND THE REASON THEY ARE NOT IN A FIXTURE.
//
// A reader has a boundary of its own — the `guarded` wrapper and the two
// non-refusal shapes `fetchOrRefuse` cannot answer for — and a boundary nothing
// can reach is a boundary nobody has tested. Reaching it needs a store that
// MISBEHAVES: one that throws a value which is not an Error, one whose answer
// throws from the getter the reader reads outside its own try, and one that
// answers about a different store than the one the ruling named.
//
// Until the seventh review round those three lived in
// gate-zero-seam-stores.v5.fixture.mjs, behind three exported trigger strings,
// and each fired when a caller's query value equalled one. The file argued that
// a door with an exact address is not an open door. That argument is the one the
// standing rule of 2026-09-11 refuses in so many words: an exported constant
// used as an exact caller-supplied label is the exception the rule forbids,
// whatever the label opens and however narrow the match. It does not matter that
// only a test held the address; what matters is that the surface answered one.
//
// SO THE FAULT IS NOT A CASE ANY MORE — IT IS AN INSTANCE. `faultedStore` below
// takes three fault functions and CLOSES OVER them at construction. The three
// fetchers it builds ignore their query entirely: there is no comparison against
// a label, no table lookup keyed by a caller value, and no argument that selects
// anything. The fault a fetcher performs was decided when this module was
// evaluated and cannot be changed, chosen or reached by anything a caller
// passes. The test proves exactly that, by calling each fetcher with every
// hostile argument the sweep holds and asserting the fault is identical every
// time.
//
// ONE FAULT PER FETCHER, so one staged instance proves all three:
//
//   card 11 / predecessor  RETURNS an answer whose `store_ref` getter throws.
//                          The reader reads that property OUTSIDE its own try,
//                          so the throw lands in the reader itself — the one
//                          thing the outer `guarded` boundary exists for — and
//                          the answer must be the gate's own.
//   card 12 / scheduler    THROWS a bare string, `throw "allow"`, the value the
//                          third review round named. It is not an Error, so
//                          nothing about it is readable as a reason and
//                          `fetchOrRefuse` must answer with the reader's own
//                          closed phrase.
//   card 13 / checks       RETURNS rows that would otherwise report a conclusion,
//                          under a `store_ref` naming a DIFFERENT store than the
//                          ruling. The reader must refuse on the identity check
//                          rather than report over rows it did not ask for.
//
// IT LIVES IN test/ AND IS NAMED `.testhelper.`, which is what keeps it out of
// production: the parser-backed import scan in gate-zero-seam-readers.v5.test.mjs
// reads every module in src/ with V8's own ESM parser and fails on a specifier
// that reaches a `/test/` path, a `.fixture.` file or a `.testhelper.` file, and
// fails again if a file so named is sitting in src/ at all. Nothing in this file
// is exported to production, because production cannot name this file.

import { createHash } from "node:crypto";

// Re-declared rather than imported, for the reason both fixtures re-declare
// them: this file is copied OVER src/gate-zero-seam-stores.v5.js into a staged
// tree, so a relative import that resolved from test/ would not resolve from the
// staged src/. MODULE-PRIVATE, because the substituted reader reads `.because`
// straight into an answer and the set it can come back with has to be one
// nothing outside this file can enumerate or extend.
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
 * THE CLOSED SHAPE FOR AN EXPORTED CALLABLE — amendment 2 of 2026-09-12, clauses
 * (a) and (b) — and this file owes it for the same concrete reason the fixtures
 * do: it is staged OVER the store module, so what the staged reader imports is
 * THIS surface. A faulted store whose exports were weaker in shape than the ones
 * production ships would prove the reader's boundary against a surface that
 * never runs.
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

// THE SAME SHAPE THE REAL STORE MODULE SHIPS, under the same two names: the
// class is MODULE-PRIVATE, what leaves is an arrow factory that builds one, and
// membership is asked by a guarded predicate rather than by `instanceof`. The
// substituted reader imports `isSeamStoreUnreachable` from whichever module file
// is in place, so this file owes both names.
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

/** A chain longer than this is not a chain that reaches this type. */
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

Object.defineProperty(SeamStoreUnreachableType.prototype, "constructor", {
  value: seamStoreUnreachable, writable: false, enumerable: false, configurable: false,
});
Object.defineProperty(SeamStoreUnreachableType, Symbol.hasInstance, {
  value: seamStoreInstance, writable: false, enumerable: false, configurable: false,
});
Object.freeze(SeamStoreUnreachableType.prototype);

/** The real store's reductions, spelled the way the fixtures spell them. */
function digest(value) {
  return `sha256:${createHash("sha256").update(value).digest("hex")}`;
}

function opaque(value) {
  return typeof value === "string" && value.length > 0 ? digest(value) : null;
}

/**
 * THE TEXT NO ANSWER MAY CARRY. The test asserts this marker appears nowhere in
 * what a reader hands back, which is how "the reader surfaces only its own
 * module-owned errors" is checked rather than merely claimed.
 */
const HOSTILE_MARKER = "HOSTILEMARKERTEXT";

/** The commit and the check name the card 13 fault's rows are written for. */
const FAULT_HEAD_SHA = "a".repeat(40);
const FAULT_ENDED_AT = "2026-09-11T17:00:30.000Z";

/**
 * A check run that WOULD report `gate_conclusion_observed` with a conclusion of
 * "success" — completed, for the asked commit, with a documented conclusion. It
 * is served under the wrong `store_ref`, so the point of the fault is that this
 * row is never reached: a reader that reported over it would be reporting rows
 * it did not ask the ruled store for.
 */
const WOULD_OTHERWISE_REPORT = Object.freeze([Object.freeze({
  head_sha: FAULT_HEAD_SHA,
  status_digest: opaque("completed"),
  conclusion_digest: opaque("success"),
  ended_at: FAULT_ENDED_AT,
})]);

// ---------------------------------------------------------------------------
// THE THREE FAULTS, as functions of nothing.
//
// Each takes NO argument. That is the whole design: a fault that took one could
// be steered by a caller, and a fault that is steered by a caller is a label
// again under a different spelling.
// ---------------------------------------------------------------------------

/** An answer that throws from the two getters the reader reads off a fetch. */
function answerWhoseGettersThrow() {
  return {
    get store_ref() { throw new Error(`${HOSTILE_MARKER}-from-a-store-answer`); },
    get rows() { throw new Error(`${HOSTILE_MARKER}-from-a-store-answer`); },
  };
}

/** A value that is not an Error at all, so nothing about it reads as a reason. */
function throwARawValue() {
  throw "allow";
}

/** An answer about a store other than the one the ruling for this card names. */
function answerAboutAnotherStore() {
  return { store_ref: "control-plane:ops.service+ops.run", rows: WOULD_OTHERWISE_REPORT };
}

/**
 * ONE FAULTED STORE INSTANCE, BUILT HERE AND NOT ELSEWHERE.
 *
 * The three faults are arguments to THIS call, which happens once, at module
 * evaluation, with three module-private functions written above. They are then
 * closed over. `faultedStore` is not exported, is never called again, and takes
 * nothing from a caller; the fetchers it returns declare no parameter at all, so
 * there is no query value for a fault to depend on even by accident.
 */
function faultedStore(onPredecessorFetch, onSchedulerFetch, onChecksFetch,
                     onCandidateRecordFetch) {
  return Object.freeze({
    fetchPredecessorOutcomeRows: closedCallable(async () => onPredecessorFetch()),
    fetchSchedulerLedgerRows: closedCallable(async () => onSchedulerFetch()),
    fetchCheckConclusionRows: closedCallable(async () => onChecksFetch()),
    fetchCandidateBuildRecordRows: closedCallable(async () => onCandidateRecordFetch()),
  });
}

const FAULTED = faultedStore(answerWhoseGettersThrow, throwARawValue, answerAboutAnotherStore,
                             throwARawValue);

export const fetchPredecessorOutcomeRows = FAULTED.fetchPredecessorOutcomeRows;
export const fetchSchedulerLedgerRows = FAULTED.fetchSchedulerLedgerRows;
export const fetchCheckConclusionRows = FAULTED.fetchCheckConclusionRows;
// AND THE FOURTH STORE, which is not a card's (amendment 9): the producer reads
// the candidate-build record directly, so a fault there must be proved not to
// escape either — it throws the same raw value the scheduler fault throws.
export const fetchCandidateBuildRecordRows = FAULTED.fetchCandidateBuildRecordRows;
