// THE THREE SEAM STORES, SERVED FROM ROWS A GATE ZERO RUN COULD ACTUALLY STAND
// ON — the producer's counterpart to gate-zero-seam-stores.v5.fixture.mjs.
//
// WHY A SECOND FIXTURE AND NOT THE FIRST ONE. The reader fixture exists to prove
// the CLAUSE TABLE: it holds one negative row per field, and exactly one of its
// three Work Requests is accepted at all. A producer needs the opposite shape —
// a world where all three clauses can hold at once — plus faults it can be
// steered into. So this file serves a clean world and three broken ones, and
// every one of them is chosen by an ADDRESS rather than by a label.
//
// HOW A TEST STEERS IT, AND WHY THAT IS NOT A DOOR. The producer's addresses are
// module-private constants in gate-zero-producer.v5.js's run binding. A test
// stages a copy of src, PASTES those lines — the same way Joe pastes a decision
// id onto a ruling line — substitutes this file for the store module, and
// imports the copy. Nothing is passed in, nothing is set, no environment
// variable is read; the variation is a source edit in a throwaway tree, which is
// the only shape the standing rule of 2026-09-11 leaves available.
//
// THE ROWS GO THROUGH THE SAME REDUCTIONS THE REAL STORE APPLIES: an identifier
// leaves as a digest, an instant leaves re-serialized, a status leaves as a
// digest or a count. A fixture that skipped the reduction would hand the reader
// a shape production never produces, and every clause proved over it would be
// proved against fiction.

import { createHash } from "node:crypto";

// Re-declared rather than imported, for the reason the reader fixture gives:
// this file is copied OVER src/gate-zero-seam-stores.v5.js, so a specifier that
// resolved from test/ would not resolve from the staged src/.
const REGISTERED_REASONS = Object.freeze([
  "the connection target for this store is not configured in this process",
  "the reason this store was unreachable is not a registered one",
]);
const REGISTERED_STORE_TOKENS = Object.freeze([
  "record-layer:work-request-outcome-feedback",
  "control-plane:ops.service+ops.run",
  "github:checks",
  "a-store-this-file-does-not-serve",
]);

function closedCallable(callable) {
  const closed = callable.bind(null);
  Object.defineProperty(closed, Symbol.hasInstance, {
    value: () => false, writable: false, enumerable: false, configurable: false,
  });
  return Object.freeze(closed);
}

class SeamStoreUnreachableType extends Error {}

export const seamStoreUnreachable = closedCallable((storeRef, because) => {
  const error = new SeamStoreUnreachableType("a seam store was not reachable");
  const token = REGISTERED_STORE_TOKENS.includes(storeRef)
    ? storeRef : "a-store-this-file-does-not-serve";
  const reason = REGISTERED_REASONS.includes(because)
    ? because : "the reason this store was unreachable is not a registered one";
  for (const [key, value] of [["store_ref", token], ["because", reason]])
    Object.defineProperty(error, key, {
      value, writable: false, enumerable: true, configurable: false,
    });
  return error;
});

const seamStoreInstance = value => value instanceof SeamStoreUnreachableType;
export const isSeamStoreUnreachable = closedCallable(value => seamStoreInstance(value));
Object.defineProperty(SeamStoreUnreachableType, Symbol.hasInstance, {
  value: seamStoreInstance, writable: false, enumerable: false, configurable: false,
});

/** artifact-trust.js's format, because the reader compares against its digests. */
function digest(value) {
  return `sha256:${createHash("sha256").update(value).digest("hex")}`;
}
function opaque(value) {
  return typeof value === "string" && value.length > 0 ? digest(value) : null;
}
function instantText(value) {
  const parsed = typeof value === "string" ? Date.parse(value) : Number.NaN;
  return Number.isFinite(parsed) ? new Date(parsed).toISOString() : null;
}
/** One field of a caller's query, safely: a throwing trap is an absent field. */
function cell(query, key) {
  try {
    const value = Reflect.get(Object(query ?? {}), key);
    return typeof value === "string" ? value : null;
  } catch {
    return null;
  }
}
function rowsFor(table, key) {
  return key !== null && Object.hasOwn(table, key) ? table[key] : [];
}

// ---------------------------------------------------------------------------
// THE ADDRESSES A STAGED RUN BINDING PASTES. Exported so a test writes the same
// literal into the staged source that this file answers for, rather than two
// copies of a magic string drifting apart.
// ---------------------------------------------------------------------------

/** One acceptance-receipt hash per Work-Request predecessor. All distinct. */
export const ACCEPTED_HASHES = Object.freeze({
  "step:wr40-repository-outcome": `sha256:${"1".repeat(64)}`,
  "step:wr46-dissolution-outcome": `sha256:${"2".repeat(64)}`,
  "step:wr54-backup-recovery-outcome": `sha256:${"3".repeat(64)}`,
});
/** A well-formed hash no row carries. A forged acceptance, in other words. */
export const FORGED_HASH = `sha256:${"e".repeat(64)}`;

export const SERVICE_KEY = "gate-zero-canary";
/** All three of card 12's clauses hold. */
export const CANARY_JOINING = "gate-zero-run-0001";
/** The dispatch names no receipt of its own, so the canary binds to nothing. */
export const CANARY_UNBOUND = "gate-zero-run-unbound";
/** No ledger row at all: the canary this run names never ran. */
export const CANARY_ABSENT = "gate-zero-run-absent";

/** Every declared check succeeds for this revision. */
export const REVISION_ALL_SUCCEED = "1".repeat(40);
/** The top gate succeeds over an ancestor that did not. The clause-3 falsifier. */
export const REVISION_FAILED_ANCESTOR = "2".repeat(40);
/** One declared check has no conclusion yet, so nothing was answered about it. */
export const REVISION_UNFINISHED = "3".repeat(40);

const T0 = "2026-09-12T09:00:00.000Z";
const T1 = "2026-09-12T09:00:30.000Z";
const T2 = "2026-09-12T09:00:31.000Z";

// --- card 11 ---------------------------------------------------------------

function acceptedRow(hash) {
  return Object.freeze({
    status: "accepted", detail_row_count: 1,
    accepted_feedback_hash: hash, feedback_hash: hash,
  });
}

const PREDECESSOR_ROWS = Object.freeze({
  "WR-000040": Object.freeze([acceptedRow(ACCEPTED_HASHES["step:wr40-repository-outcome"])]),
  "WR-000046": Object.freeze([acceptedRow(ACCEPTED_HASHES["step:wr46-dissolution-outcome"])]),
  "WR-000054": Object.freeze([acceptedRow(ACCEPTED_HASHES["step:wr54-backup-recovery-outcome"])]),
});

// --- card 12 ---------------------------------------------------------------

function receipt({ forRunKey, mintedAt, nonce = "0123456789abcdef" }) {
  const stamp = new Date(Date.parse(mintedAt)).toISOString()
    .replace(/-/g, "").replace(/:/g, "");
  const runKeyHash = createHash("sha256").update(forRunKey).digest("hex").slice(0, 32);
  return `carr-run-receipt:v1:${stamp}:${nonce}:${runKeyHash}`;
}
const SCHEDULED_RECEIPT =
  /^carr-run-receipt:v1:(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})\.(\d{3})Z:[0-9a-f]{16}:([0-9a-f]{32})$/;
function receiptFields(value) {
  const parsed = typeof value === "string" ? SCHEDULED_RECEIPT.exec(value) : null;
  if (parsed === null) return { runKeyHash: null, mintedAt: null };
  const [, year, month, day, hour, minute, second, millis, runKeyHash] = parsed;
  return {
    runKeyHash,
    mintedAt: instantText(`${year}-${month}-${day}T${hour}:${minute}:${second}.${millis}Z`),
  };
}
function runKeyReceiptHash(value) {
  return typeof value === "string" && value.length > 0
    ? createHash("sha256").update(value).digest("hex").slice(0, 32) : null;
}
function ledgerRow(raw) {
  const parsed = receiptFields(raw.evidence_ref);
  return Object.freeze({
    service_key_digest: opaque(raw.service_key),
    run_key_digest: opaque(raw.run_key),
    evidence_ref_digest: opaque(raw.evidence_ref),
    source_kind_digest: opaque(raw.source_kind),
    source_ref_digest: opaque(raw.source_ref),
    receipt_run_key_digest: opaque(parsed.runKeyHash),
    run_key_receipt_digest: opaque(runKeyReceiptHash(raw.run_key)),
    receipt_minted_at: parsed.mintedAt,
    started_at: instantText(raw.started_at),
    ended_at: instantText(raw.ended_at),
    observed_at: instantText(raw.observed_at),
  });
}

const RUN = Object.freeze({
  service_key: SERVICE_KEY,
  started_at: T0, ended_at: T1, observed_at: T2,
  source_kind: "wrapper", source_ref: "bin/run-scheduled.sh",
});

const SCHEDULER_ROWS = Object.freeze({
  [CANARY_JOINING]: Object.freeze([ledgerRow({ ...RUN,
    run_key: CANARY_JOINING,
    evidence_ref: receipt({ forRunKey: CANARY_JOINING, mintedAt: T1 }) })]),
  // The row the wrapper wrote for every run before it minted receipts at all.
  [CANARY_UNBOUND]: Object.freeze([ledgerRow({ ...RUN,
    run_key: CANARY_UNBOUND, evidence_ref: null })]),
  // CANARY_ABSENT is deliberately not a key here: the table answers no rows.
});

// --- card 13 ---------------------------------------------------------------

function checkRow(raw) {
  return Object.freeze({
    head_sha: /^[0-9a-f]{40}$/.test(raw.head_sha ?? "") ? raw.head_sha : null,
    status_digest: opaque(raw.status),
    conclusion_digest: opaque(raw.conclusion),
    ended_at: instantText(raw.completed_at),
  });
}
function completed(headSha, conclusion) {
  return Object.freeze([checkRow({
    head_sha: headSha, status: "completed", conclusion, completed_at: T1,
  })]);
}
function unfinished(headSha) {
  return Object.freeze([checkRow({
    head_sha: headSha, status: "queued", conclusion: null, completed_at: null,
  })]);
}

const CI = "ops/ci.sh --strict";
const DB = "local-db-ci --class migration";
const CANARY_CHECK = "main canary";

/**
 * Keyed by revision AND check name, because the fault is a property of the pair:
 * the same check succeeds for one revision and fails for another, which is what
 * a real re-run against a new head looks like.
 */
const CHECK_ROWS = Object.freeze({
  [`${REVISION_ALL_SUCCEED}|${CI}`]: completed(REVISION_ALL_SUCCEED, "success"),
  [`${REVISION_ALL_SUCCEED}|${DB}`]: completed(REVISION_ALL_SUCCEED, "success"),
  [`${REVISION_ALL_SUCCEED}|${CANARY_CHECK}`]: completed(REVISION_ALL_SUCCEED, "success"),

  // THE INJECTED FAILURE, AND THE TOP GATE STILL SAYS SUCCESS. This is the pair
  // a conjunction of self-conclusions would read as green.
  [`${REVISION_FAILED_ANCESTOR}|${CI}`]: completed(REVISION_FAILED_ANCESTOR, "failure"),
  [`${REVISION_FAILED_ANCESTOR}|${DB}`]: completed(REVISION_FAILED_ANCESTOR, "success"),
  [`${REVISION_FAILED_ANCESTOR}|${CANARY_CHECK}`]: completed(REVISION_FAILED_ANCESTOR, "success"),

  [`${REVISION_UNFINISHED}|${CI}`]: completed(REVISION_UNFINISHED, "success"),
  [`${REVISION_UNFINISHED}|${DB}`]: unfinished(REVISION_UNFINISHED),
  [`${REVISION_UNFINISHED}|${CANARY_CHECK}`]: completed(REVISION_UNFINISHED, "success"),
});

// ---------------------------------------------------------------------------
// THE PUBLIC SURFACE. Three bound arrows that serve rows and decide nothing.
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// THE TWO STAGED VARIANT LINES (2026-09-12, PR 1013 correction round).
//
// The producer no longer takes an address for either of these: it reads the
// acceptance receipt by the step ref and the canary by the service key, because
// the review required the run binding to be DERIVED and not pasted. So a test
// that needs a broken world can no longer steer by writing a different address
// into the producer — it rewrites one of the two lines below in the staged copy
// of this file, which is the same throwaway-tree source edit the rest of the
// staging already does, moved to the store that serves the row.
// ---------------------------------------------------------------------------

/** Which canary run the ledger holds for this service. */
const LEDGER_CANARY = CANARY_JOINING;
/** Which predecessor world the record layer holds: clean | receipt-card-mismatch. */
const PREDECESSOR_WORLD = "clean";
/**
 * Which release-candidate world ops.release holds for the revision asked about
 * (standing-rule amendment 9): filed | absent | ambiguous | unregistered-maker.
 *
 *   filed              one candidate row, made by a registered partner.
 *   absent             no row for that revision — a build nobody filed for.
 *   ambiguous          two rows naming two makers; a receipt would have to pick.
 *   unregistered-maker a slug-shaped maker identity.js does not register.
 *   same-session       a candidate filed in the SESSION THAT IS NOW REVIEWING —
 *                      r7's self-review denial, reachable because the release
 *                      row's correlation and the evaluator's are the same value.
 */
const CANDIDATE_BUILD_RECORD_WORLD = "filed";

/** The maker and correlation the recorder stamped on the candidate row. */
export const CANDIDATE_MAKER_ACTOR = "joe";
export const CANDIDATE_BUILD_CORRELATION_ID = "8c41d2ae-6f30-4a1b-9e57-2b6c0f4d81a3";
/** The correlation the SUITE's served request carries, for the self-review case. */
const REVIEWING_CORRELATION_ID = "3f2a6c18-9b4d-4e7a-8c11-5d0e2f7a6b93";
const SECOND_MAKER = "dell";
const SECOND_CORRELATION = "1d9f77b2-0c45-4d82-a3e6-90bb5e1c47f0";
const UNREGISTERED_MAKER = "carr-release-bot";

function candidateRecordRow(maker, correlationId, observedAt) {
  return Object.freeze({
    git_sha_digest: digest("release"),
    state_digest: digest("candidate"),
    environment_digest: digest("production"),
    maker_actor: maker,
    correlation_id: correlationId,
    observed_at: observedAt,
  });
}

/**
 * The receipt hash and the card's own proposal hash DISAGREE, which is the
 * falsifier the derived read still has to catch: the acceptance receipt signed
 * one thing and the card carries another, so no accepted row joins.
 */
function mismatchedRows(rows) {
  return rows.map(row => Object.freeze({ ...row, feedback_hash: FORGED_HASH }));
}

export const fetchPredecessorOutcomeRows = closedCallable(async query => {
  const rows = rowsFor(PREDECESSOR_ROWS, cell(query, "workRequestRef"));
  return {
    store_ref: "record-layer:work-request-outcome-feedback",
    rows: PREDECESSOR_WORLD === "receipt-card-mismatch" ? mismatchedRows(rows) : rows,
  };
});

export const fetchSchedulerLedgerRows = closedCallable(async query => {
  const storeRef = "control-plane:ops.service+ops.run";
  if (cell(query, "serviceKey") !== SERVICE_KEY) return { store_ref: storeRef, rows: [] };
  // AN OMITTED RUN KEY IS THE DERIVED ADDRESS — the latest run this service's
  // wrapper minted a receipt for, which in this fixture is whichever one
  // LEDGER_CANARY names. A named key still addresses that key alone.
  const named = cell(query, "canaryRunKey");
  return { store_ref: storeRef, rows: rowsFor(SCHEDULER_ROWS, named ?? LEDGER_CANARY) };
});

export const fetchCheckConclusionRows = closedCallable(async query => ({
  store_ref: "github:checks",
  rows: rowsFor(CHECK_ROWS, `${cell(query, "headSha")}|${cell(query, "checkName")}`),
}));

/**
 * ops.release, for the revision the producer stamped. The rows carry the maker
 * and the correlation as TEXT — the two columns the real store lets through as
 * text, because a receipt has to NAME its subject maker and a digest names
 * nobody — and everything else as a digest, exactly as the real store does.
 */
export const fetchCandidateBuildRecordRows = closedCallable(async query => {
  const storeRef = "control-plane:ops.candidate-build-record";
  void cell(query, "gitSha");
  if (CANDIDATE_BUILD_RECORD_WORLD === "absent") return { store_ref: storeRef, rows: [] };
  if (CANDIDATE_BUILD_RECORD_WORLD === "ambiguous")
    return { store_ref: storeRef, rows: [
      candidateRecordRow(CANDIDATE_MAKER_ACTOR, CANDIDATE_BUILD_CORRELATION_ID, "2026-09-12T08:00:00.000Z"),
      candidateRecordRow(SECOND_MAKER, SECOND_CORRELATION, "2026-09-12T07:00:00.000Z"),
    ] };
  if (CANDIDATE_BUILD_RECORD_WORLD === "same-session")
    return { store_ref: storeRef, rows: [
      candidateRecordRow(CANDIDATE_MAKER_ACTOR, REVIEWING_CORRELATION_ID, "2026-09-12T08:00:00.000Z"),
    ] };
  if (CANDIDATE_BUILD_RECORD_WORLD === "unregistered-maker")
    return { store_ref: storeRef, rows: [
      candidateRecordRow(UNREGISTERED_MAKER, CANDIDATE_BUILD_CORRELATION_ID, "2026-09-12T08:00:00.000Z"),
    ] };
  return { store_ref: storeRef, rows: [
    candidateRecordRow(CANDIDATE_MAKER_ACTOR, CANDIDATE_BUILD_CORRELATION_ID, "2026-09-12T08:00:00.000Z"),
  ] };
});
