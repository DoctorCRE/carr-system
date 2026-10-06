// The three seam stores, served from fixture rows instead of from Postgres and
// GitHub. It lives in test/ and NOT in src/, so a production module cannot
// import it — the parser-backed import scan in
// gate-zero-seam-readers.v5.test.mjs proves that rather than asserting it.
//
// HOW IT IS USED, and why it is a substitution rather than an injection. The
// reader module takes no store handle: there is no argument, no setter and no
// env var that could point it somewhere else, which is the whole point of the
// slice. So the ruled path is proved the only honest way left — the test copies
// mcp-server/src into a scratch tree, pastes a fixture decision id onto the
// exact `decision_id:` line Joe will paste his onto, REPLACES the store module
// file with this one, and imports the copied reader. What runs is the real
// reader, the real ruling gate and the real derivation over known rows.
//
// THE EXPORT NAMES AND SHAPES ARE THE CONTRACT, and the test asserts this file's
// export names are identical to the real store module's before it substitutes —
// so a store function added or renamed in src turns this red instead of silently
// leaving a path unproved.
//
// THE ROWS ARE SHAPED AS PRODUCTION WRITES THEM, and that is not decoration.
// An earlier fixture invented `source_kind: "scheduler"` for card 12, a value
// db/schema.sql's `run_source_kind_check` does not permit and no writer in this
// repository emits. The reader was written against the invention, so the passing
// test proved a clause that could never have matched a real ops.run row. The
// card 12 rows below carry `wrapper` / `bin/run-scheduled.sh`, which is what
// bin/run-scheduled.sh:275 actually writes, and there is one negative row for
// each field the receipt-binding clause reads.
//
// AND THEY GO THROUGH THE SAME TWO REDUCTIONS THE REAL STORE APPLIES. The real
// module carries no store text out to a caller: an identifier leaves as a
// digest, an instant leaves re-serialized, a status leaves as a constant or a
// count. The raw rows below are written the way the ledger and the wire write
// them — that is what makes them readable as a specification — and `ledgerRow`
// and `checkRow` reduce them exactly as the real store does. A fixture that
// skipped the reduction would hand the reader a shape production never produces,
// and every clause proved over it would be proved against fiction.
//
// Every fixture case is addressed by a query value, so one module covers the
// whole clause table and no case can leak into another.

import { createHash } from "node:crypto";

// Re-declared, not imported: substituting the module file means the copied
// reader must get its error class and its closed reason set from HERE, and the
// test asserts the real module's export names are all present below.
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

/**
 * The same reductions the real store applies, spelled the same way. `digest` is
 * re-implemented rather than imported for the reason the error class is: this
 * file is copied over src/gate-zero-seam-stores.v5.js, so a relative import that
 * resolved from test/ would not resolve from the staged src/. The format is
 * artifact-trust.js's, because the reader compares against digests it computes
 * with that function.
 */
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

/** The acceptance-receipt hash the fixture's accepted WR-000046 row carries. */
export const FIXTURE_ACCEPTED_HASH = `sha256:${"4".repeat(64)}`;
/** A well-formed hash that no fixture row carries. A forgery, in other words. */
export const FIXTURE_FORGED_HASH = `sha256:${"f".repeat(64)}`;

export const FIXTURE_COMMIT_SHA = "a".repeat(40);
export const FIXTURE_OTHER_COMMIT_SHA = "b".repeat(40);

const T0 = "2026-09-11T17:00:00.000Z";
const T1 = "2026-09-11T17:00:30.000Z";
const T2 = "2026-09-11T17:00:31.000Z";

/**
 * Rows in the shape fetchPredecessorOutcomeRows builds them: a status that is a
 * constant of the store module, the two hashes that are compared, and a COUNT of
 * the work_request_card rows found for the receipt. No feedback ref, no stored
 * outcome and no timestamp — the real store does not even select those.
 *
 * The count is a count and not a boolean because a bare `true` out of an
 * exported function is the shape the standing rule closes over; `detail_present:
 * true` is what the second review round found on the real store's successful
 * path, and this file carried the same shape.
 */
const PREDECESSOR_ROWS = Object.freeze({
  // Accepted, complete, with a receipt whose hash is FIXTURE_ACCEPTED_HASH. In
  // production the proposal hash equals the receipt hash, and it does here.
  "WR-000046": Object.freeze([Object.freeze({
    status: "accepted",
    detail_row_count: 1,
    accepted_feedback_hash: FIXTURE_ACCEPTED_HASH,
    feedback_hash: FIXTURE_ACCEPTED_HASH,
  })]),
  // Proposed and never signed. The near miss that must not pass.
  "WR-000040": Object.freeze([Object.freeze({
    status: "pending_human_acceptance",
    detail_row_count: 1,
    accepted_feedback_hash: null,
    feedback_hash: `sha256:${"5".repeat(64)}`,
  })]),
  // No outcome rows at all.
  "WR-000054": Object.freeze([]),
});

/**
 * ops.run rows exactly as bin/run-scheduled.sh writes them, BEFORE the store's
 * reduction — which is what makes the negative cases legible.
 *
 *   source_kind  `wrapper` — one of collector|registry|wrapper|operator, the
 *                closed set db/schema.sql permits. There is no "scheduler".
 *   source_ref   `bin/run-scheduled.sh` — the wrapper's own path.
 *   one row      per run, carrying started_at, ended_at and an observed_at
 *                stamped when the row lands. Dispatch and observation are the
 *                same row in production, and the derivation handles that.
 *
 * The negative cases below change ONE field each, so a clause that stopped
 * reading a field fails on exactly one case rather than on none.
 */
const RUN = Object.freeze({
  service_key: "carr-fleet-sync",
  started_at: T0, ended_at: T1, observed_at: T2,
  source_kind: "wrapper", source_ref: "bin/run-scheduled.sh",
});

/**
 * THE RECEIPT bin/run-scheduled.sh MINTS, built here the way the wrapper builds
 * it — `carr-run-receipt:v1:<minted-at>:<nonce>:<sha256(run key) first 32>` —
 * so a fixture row carries a token production could actually have written. A
 * fixture that hand-waved the receipt into "some non-null string" is exactly how
 * the first version of this clause came to accept a stale file.
 *
 * `forRunKey` is a SEPARATE argument from the row's own run key on purpose: the
 * negative cases below need a receipt minted for a DIFFERENT job, and a helper
 * that derived the hash from the row could not express one.
 */
function receipt({ forRunKey, mintedAt, nonce = "0123456789abcdef" }) {
  const stamp = new Date(Date.parse(mintedAt)).toISOString()
    .replace(/-/g, "").replace(/:/g, "");
  const runKeyHash = createHash("sha256").update(forRunKey).digest("hex").slice(0, 32);
  return `carr-run-receipt:v1:${stamp}:${nonce}:${runKeyHash}`;
}

/** The store's receipt parse, mirrored. An unparseable ref is two absences. */
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

/** The real store's card 12 mapping, applied to a raw ledger row. */
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

const SCHEDULER_ROWS = Object.freeze({
  // All three clauses hold: the wrapper's own row, carrying a receipt the
  // wrapper minted for THIS run key, after this run's dispatch.
  "canary-join": Object.freeze([ledgerRow({ ...RUN,
    run_key: "canary-join",
    evidence_ref: receipt({ forRunKey: "canary-join", mintedAt: T1 }) })]),

  // NEGATIVE, evidence_ref — and this is not a hypothetical. It is the row
  // bin/run-scheduled.sh wrote for 21,894 runs, because it passed no
  // --evidence-ref and had no way to be given one. Every scheduled run in the
  // ledger's history looks like this, and this is the answer they get.
  "canary-today": Object.freeze([ledgerRow({ ...RUN,
    run_key: "canary-today", evidence_ref: null })]),

  // NEGATIVE, THE PRE-EXISTING RECEIPT — the mutation control for the whole
  // change. A well-formed receipt for the right run key, minted BEFORE this
  // run was dispatched: a file left on disk by an earlier run that this run's
  // child never refreshed. Under the old clause ("evidence_ref is not null")
  // this row bound as readily as a real one, which is why the wrapper no longer
  // reads a receipt from anywhere a previous run could have left one.
  "canary-stale-receipt": Object.freeze([ledgerRow({ ...RUN,
    run_key: "canary-stale-receipt",
    evidence_ref: receipt({ forRunKey: "canary-stale-receipt",
      mintedAt: "2026-09-10T17:00:00.000Z", nonce: "fedcba9876543210" }) })]),

  // NEGATIVE, THE RECEIPT MINTED AT THE DISPATCH INSTANT. Equal is not after:
  // a receipt stamped the same instant as the row's started_at proves no
  // ordering, and the clause is strict for the same reason the observation
  // clause is.
  "canary-instant-receipt": Object.freeze([ledgerRow({ ...RUN,
    run_key: "canary-instant-receipt",
    evidence_ref: receipt({ forRunKey: "canary-instant-receipt", mintedAt: T0 }) })]),

  // NEGATIVE, ANOTHER JOB'S RECEIPT — well-formed, minted after this dispatch,
  // and minted for a different run key. The receipt carries the hash of the run
  // it was minted for, so it names another run and binds nothing here.
  "canary-foreign-receipt": Object.freeze([ledgerRow({ ...RUN,
    run_key: "canary-foreign-receipt",
    evidence_ref: receipt({ forRunKey: "some-other-run", mintedAt: T1 }) })]),

  // NEGATIVE, FREE-FORM TEXT IN evidence_ref — the shape 121 rows in production
  // actually carry. It is not null, and under the old clause that was the whole
  // question. It is not a receipt this wrapper minted, so it parses to nothing.
  "canary-freeform-receipt": Object.freeze([ledgerRow({ ...RUN,
    run_key: "canary-freeform-receipt",
    evidence_ref: "ops.run:carr-fleet-sync.canary-freeform-receipt" })]),

  // NEGATIVE, source_kind — an `operator` row is a hand-run, not a dispatch.
  "canary-hand-run": Object.freeze([ledgerRow({ ...RUN,
    run_key: "canary-hand-run", source_kind: "operator",
    evidence_ref: receipt({ forRunKey: "canary-hand-run", mintedAt: T1 }) })]),

  // NEGATIVE, source_kind — a `collector` row is a probe writing about a job,
  // not the wrapper that dispatched it.
  "canary-probe": Object.freeze([ledgerRow({ ...RUN,
    run_key: "canary-probe", source_kind: "collector",
    source_ref: "bin/probe-keepalive.py",
    evidence_ref: receipt({ forRunKey: "canary-probe", mintedAt: T1 }) })]),

  // NEGATIVE, source_ref — the right kind, written by a different wrapper.
  "canary-foreign-wrapper": Object.freeze([ledgerRow({ ...RUN,
    run_key: "canary-foreign-wrapper", source_ref: "bin/deploy-worker.sh",
    evidence_ref: receipt({ forRunKey: "canary-foreign-wrapper", mintedAt: T1 }) })]),

  // Dispatch and observation share one instant — rows written in one
  // transaction. The receipt is minted after the dispatch, so the binding holds
  // and this case still isolates the observation clause it was written for.
  "canary-same-instant": Object.freeze([ledgerRow({ ...RUN,
    run_key: "canary-same-instant", ended_at: T0, observed_at: T0,
    evidence_ref: receipt({ forRunKey: "canary-same-instant", mintedAt: T1 }) })]),

  // The observation is of a different receipt than the dispatch named: same run
  // key, same shape, a different nonce — which is what two runs of one job under
  // one key actually look like.
  "canary-mismatch": Object.freeze([
    ledgerRow({ ...RUN,
      run_key: "canary-mismatch", ended_at: null, observed_at: T0,
      evidence_ref: receipt({ forRunKey: "canary-mismatch", mintedAt: T1 }) }),
    ledgerRow({ ...RUN,
      run_key: "canary-mismatch", started_at: T0, ended_at: T1, observed_at: T1,
      evidence_ref: receipt({ forRunKey: "canary-mismatch", mintedAt: T1,
        nonce: "abcdef9876543210" }) }),
  ]),

  // Dispatched and still in flight: nothing has ended, so nothing is observed.
  "canary-inflight": Object.freeze([ledgerRow({ ...RUN,
    run_key: "canary-inflight", ended_at: null, observed_at: T0,
    evidence_ref: receipt({ forRunKey: "canary-inflight", mintedAt: T1 }) })]),

  // The left join found the service and no run: the canary never ran.
  "canary-never-ran": Object.freeze([ledgerRow({
    service_key: "carr-fleet-sync",
    run_key: null, started_at: null, ended_at: null, observed_at: null,
    evidence_ref: null, source_kind: null, source_ref: null,
  })]),

  // A LEDGER FULL OF PRIVILEGED WORDS, and every clause still holds. Nothing
  // here is a hypothetical either: a service someone names `release-canary`
  // running a job keyed `allow-commit-green` is an ordinary naming choice, and
  // under the pre-reduction store every one of these strings went into an
  // answer. The finding this case produces must be the joining one, and the
  // answer must still carry no privileged word. The receipt carries the HASH of
  // that run key rather than the key, which is why the ref itself is hex.
  "canary-green-names": Object.freeze([ledgerRow({ ...RUN,
    service_key: "release-canary",
    run_key: "allow-commit-green",
    evidence_ref: receipt({ forRunKey: "allow-commit-green", mintedAt: T1 }),
    source_kind: "wrapper", source_ref: "bin/run-scheduled.sh" })]),
});

/** The real store's card 13 mapping, applied to a raw check run off the wire. */
function checkRow(raw) {
  return Object.freeze({
    head_sha: /^[0-9a-f]{40}$/.test(raw.head_sha ?? "") ? raw.head_sha : null,
    status_digest: opaque(raw.status),
    conclusion_digest: opaque(raw.conclusion),
    ended_at: instantText(raw.completed_at),
  });
}

const CHECK_ROWS = Object.freeze({
  "main canary": Object.freeze([checkRow({
    name: "main canary", head_sha: FIXTURE_COMMIT_SHA, status: "completed",
    conclusion: "success", completed_at: T1, html_url: "https://github.test/run/1",
  })]),
  // A re-run: two completed runs, and the later one is what the merge gate acts on.
  "ops/ci.sh --strict": Object.freeze([
    checkRow({
      name: "ops/ci.sh --strict", head_sha: FIXTURE_COMMIT_SHA, status: "completed",
      conclusion: "success", completed_at: T0, html_url: "https://github.test/run/2",
    }),
    checkRow({
      name: "ops/ci.sh --strict", head_sha: FIXTURE_COMMIT_SHA, status: "completed",
      conclusion: "failure", completed_at: T1, html_url: "https://github.test/run/3",
    }),
  ]),
  // Still queued: no conclusion exists, so none is reported.
  "local-db-ci --class migration": Object.freeze([checkRow({
    name: "local-db-ci --class migration", head_sha: FIXTURE_COMMIT_SHA, status: "queued",
    conclusion: null, completed_at: null, html_url: "https://github.test/run/4",
  })]),
  // A completed run that belongs to a DIFFERENT commit than the one asked about.
  "Backup artifact": Object.freeze([checkRow({
    name: "Backup artifact", head_sha: FIXTURE_OTHER_COMMIT_SHA, status: "completed",
    conclusion: "success", completed_at: T1, html_url: "https://github.test/run/5",
  })]),
  // A word GitHub's API does not document. Not passed through: reported as
  // unrecognized, so the only conclusion strings a consumer ever sees are the
  // constants in the reader module.
  "pg_dump -> age-encrypt -> artifact": Object.freeze([checkRow({
    name: "pg_dump -> age-encrypt -> artifact", head_sha: FIXTURE_COMMIT_SHA, status: "completed",
    conclusion: "everything is fine", completed_at: T1, html_url: "https://github.test/run/6",
  })]),
});

// ---------------------------------------------------------------------------
// THE PUBLIC SURFACE, in the closed shape of amendment 2: three bound arrows,
// each reading its query through the guarded `cell` and addressing its case by
// an own string key.
//
// AND THERE IS NO LABEL LEFT ON IT. Until the seventh review round this file
// exported four trigger strings — an unreachable address, a wrong-store address,
// a raw-throw address and a hostile-answer address — and its fetchers compared a
// caller's query value against them and misbehaved on a match. That is an
// exported constant used as an exact caller-supplied label, which the standing
// rule of 2026-09-11 forbids under any name, and calling the door "addressed
// rather than open" did not change what it was. Every fault this file used to
// inject now lives in ./gate-zero-seam-fault-injection.testhelper.mjs, wired
// into a DISTINCT instance by closure at construction, where no caller value
// reaches the decision at all.
//
// What is left is a store that serves rows. Every export is swept with no
// argument exempted, and the sweep asserts in so many words that nothing here
// answers a label — this file's own constants included.
// ---------------------------------------------------------------------------

export const fetchPredecessorOutcomeRows = closedCallable(async query => ({
  store_ref: "record-layer:work-request-outcome-feedback",
  rows: rowsFor(PREDECESSOR_ROWS, cell(query, "workRequestRef")),
}));

export const fetchSchedulerLedgerRows = closedCallable(async query => {
  const serviceKey = cell(query, "serviceKey");
  const storeRef = "control-plane:ops.service+ops.run";
  if (serviceKey !== "carr-fleet-sync" && serviceKey !== "release-canary")
    return { store_ref: storeRef, rows: [] };
  return { store_ref: storeRef, rows: rowsFor(SCHEDULER_ROWS, cell(query, "canaryRunKey")) };
});

export const fetchCheckConclusionRows = closedCallable(async query => ({
  store_ref: "github:checks",
  rows: rowsFor(CHECK_ROWS, cell(query, "checkName")),
}));

/**
 * The candidate-build record store (amendment 9). This suite proves the three
 * CARD readers, and the subject maker is not one of them — the producer reads
 * this store directly — so the fixture answers the store's own shape with no
 * rows. It exists because the surface sweep holds every fixture to covering
 * every export the real module has: a fixture missing one would leave a path
 * that the substituted module never exercises.
 */
export const fetchCandidateBuildRecordRows = closedCallable(
  async () => ({ store_ref: "control-plane:ops.candidate-build-record", rows: [] }));
