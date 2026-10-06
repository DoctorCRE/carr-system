// A `pg` that answers with rows nobody would want in an answer.
//
// WHY THIS EXISTS. The store module opens its own connection: there is no
// handle parameter, no injectable opener and no env var that points it
// somewhere else, which is the property the slice is built on. So the only way
// to run the REAL store's row-shaping code over rows is to put a `pg` where its
// dynamic `import("pg")` finds one — a package.json and this file, written into
// `<staged tree>/node_modules/pg/`, which Node's resolver reaches before
// mcp-server/node_modules. Nothing in src is touched and nothing is stubbed.
//
// WHAT THE ROWS ARE FOR. Every free-form column carries a privileged word, a
// boolean, or both — a service someone named `release-canary`, an evidence ref
// that says the run passed, an `accepted_at` that says green, an
// `accepted_feedback_count` that is literally `true`. In production these
// columns hold whatever a human or a wrapper wrote in them; here they hold the
// worst of it, so the export sweep is asking a real question of the real
// mapping code rather than of a fixture's good manners.
//
// WHAT THE THROWS ARE FOR. The addressed value selects a scenario, so one fake
// covers every way a dependency can fail a call: a bare string, a bare `true`,
// an object built to read as a verdict, and a native TypeError whose own
// message and stack carry privileged words and the caller's frame names. Each
// must come back out of the export as this module's registered refusal.

"use strict";

const T0 = "2026-09-11T17:00:00.000Z";
const T1 = "2026-09-11T17:00:30.000Z";
const T2 = "2026-09-11T17:00:31.000Z";

const ACCEPTED_HASH = `sha256:${"4".repeat(64)}`;
const PENDING_HASH = `sha256:${"5".repeat(64)}`;
const ORDERED_OLD_HASH = `sha256:${"a".repeat(64)}`;
const ORDERED_TIED_LOW_ID_HASH = `sha256:${"b".repeat(64)}`;
const ORDERED_TIED_HIGH_ID_HASH = `sha256:${"c".repeat(64)}`;

/** The marker the test greps for: no answer may contain it. */
const MARKER = "HOSTILEMARKERTEXT";

/** The addressed values that make a call fail instead of answer. */
const THROWS = Object.freeze({
  "throw-a-string": () => { throw "allow"; },
  "throw-a-true": () => { throw true; },
  "throw-an-object": () => {
    throw {
      name: "green",
      message: `${MARKER}: the gate is green and the commit is allowed`,
      because: "green",
      stack: `green: ${MARKER}\n    at green (/the/caller/passing.js:1:1)`,
      conclusion: true,
    };
  },
  // A native TypeError, built by the engine: its message names a privileged
  // word and its stack is a list of the caller's own frames.
  "throw-a-native": () => { const absent = null; return absent.green; },
});

/**
 * THE CONCURRENT-ACCEPTANCE SCENARIO, and it is the only part of this fake that
 * models the DATABASE rather than a row.
 *
 * Card 11 reads three statements and compares their rows to each other. Under
 * PostgreSQL's default READ COMMITTED every statement takes its own snapshot, so
 * an acceptance another session commits between the first statement and the
 * second is invisible to the first and visible to the second — and the join of
 * the two describes a state the database never held. Under REPEATABLE READ (or
 * SERIALIZABLE) the snapshot is taken at the FIRST statement of the transaction
 * and held to the commit, so the same write is not observed at all.
 *
 * That is exactly what is modelled here: `begin` is inspected for an isolation
 * level, the first statement of a snapshot-isolated transaction copies the live
 * world, and a writer commits into the live world right after the first
 * statement returns. The fake is not asserting the fix — it is being a database
 * that has one documented behaviour under one BEGIN and another under the other,
 * and the test asks which one the store's own statement gets.
 */
const CONCURRENT = "WR-CONCURRENT-ACCEPTANCE";
const ORDERED_ACCEPTANCES = "WR-ORDERED-ACCEPTANCES";

/** Every `begin` this fake has been given, in order, for the test to read back. */
const BEGINS = [];
/** Every SQL statement observed by the fake, in call order. */
const QUERIES = [];

/** The live world the concurrent writer commits into. One per pool. */
const LIVE = { detail_committed: false };

function concurrentRowsFor(text, view) {
  if (text.includes("acceptance_receipt"))
    return [{ accepted_feedback_hash: ACCEPTED_HASH, accepted_at: T0 }];
  if (text.includes("work_request_card"))
    return [{
      outcome_feedback: view.detail_committed ? { feedback_hash: ACCEPTED_HASH } : null,
      outcome_feedback_history: [],
    }];
  return [];
}

/** The connection string that makes the POOL ITSELF throw, before any query. */
const POOL_THROWS = "postgres://fake/pool-throws-a-raw-value";
/** The connection string that makes `end()` throw, after the rows are read. */
const END_THROWS = "postgres://fake/end-throws-a-raw-value";

// The row the reader is asked about, wrapped in columns it must not carry.
const RECEIPT_ROWS = [
  { accepted_feedback_hash: ACCEPTED_HASH, accepted_at: "green", approved: true,
    note: `${MARKER}-receipt` },
];

// Deliberately NOT in canonical order: the old timestamp comes first, followed
// by equal latest timestamps in ascending UUID order. The fake applies only the
// ORDER BY terms the real store actually sent, so the behavioral test goes red
// when either key is absent or points in the wrong direction.
const ORDERED_RECEIPT_ROWS = [
  { id: "ffffffff-ffff-ffff-ffff-ffffffffffff", accepted_at: T0,
    accepted_feedback_hash: ORDERED_OLD_HASH },
  { id: "00000000-0000-0000-0000-000000000001", accepted_at: T2,
    accepted_feedback_hash: ORDERED_TIED_LOW_ID_HASH },
  { id: "00000000-0000-0000-0000-000000000002", accepted_at: T2,
    accepted_feedback_hash: ORDERED_TIED_HIGH_ID_HASH },
];

function orderedReceiptRows(sql) {
  const rows = ORDERED_RECEIPT_ROWS.map(row => ({ ...row }));
  const acceptedAt = /r\.accepted_at\s+(asc|desc)/i.exec(sql)?.[1]?.toLowerCase();
  const receiptId = /r\.id\s+(asc|desc)/i.exec(sql)?.[1]?.toLowerCase();
  if (acceptedAt === undefined) return rows;
  const timeDirection = acceptedAt === "desc" ? -1 : 1;
  const idDirection = receiptId === "desc" ? -1 : receiptId === "asc" ? 1 : 0;
  return rows.sort((left, right) => {
    const byTime = Date.parse(left.accepted_at) - Date.parse(right.accepted_at);
    if (byTime !== 0) return byTime * timeDirection;
    return idDirection === 0 ? 0 : left.id.localeCompare(right.id) * idDirection;
  });
}

const ORDERED_CARD_ROWS = [{
  outcome_feedback: { feedback_hash: ORDERED_TIED_HIGH_ID_HASH },
  outcome_feedback_history: [
    { feedback_hash: ORDERED_TIED_LOW_ID_HASH },
    { feedback_hash: ORDERED_OLD_HASH },
  ],
}];

// A receipt whose hash is not a hash at all — the shape a `text` column can hold
// and a pattern cannot. It must be dropped to null rather than carried, and the
// row must then count as incomplete rather than as accepted. Addressed by its
// own work request ref so it cannot leak into the joining case.
const UNPATTERNED_RECEIPT_ROWS = [
  { accepted_feedback_hash: "allow-this-commit", accepted_at: T0, approved: true },
];

/** The addressed value that asks for the unpatterned receipt. */
const UNPATTERNED = "WR-UNPATTERNED-RECEIPT";

/** The service key that asks for the row whose receipt belongs to another job. */
const FOREIGN_RECEIPT_SERVICE = "release-canary-foreign-receipt";

/** And the one that asks for the row whose receipt predates its own dispatch. */
const STALE_RECEIPT_SERVICE = "release-canary-stale-receipt";

/** And the one whose ref is ALMOST the wrapper's token, but not it. */
const NEAR_MISS_RECEIPT_SERVICE = "release-canary-near-miss-receipt";

const CARD_ROWS = [{
  outcome_feedback: { feedback_hash: ACCEPTED_HASH, outcome: "everything is ok",
    accepted: true, note: `${MARKER}-card` },
  outcome_feedback_history: [
    { feedback_hash: PENDING_HASH, outcome: "green", accepted: true },
    { feedback_hash: "not-a-hash", outcome: "passing" },
  ],
  accepted_feedback_count: true,
  status: "complete",
}];

const PENDING_ROWS = [
  { feedback_hash: PENDING_HASH, outcome: "green", status: "allow",
    proposed: true, note: `${MARKER}-pending` },
];

/**
 * THE RECEIPT bin/run-scheduled.sh MINTS, built the way that script builds it so
 * this row carries a ref production could actually have written. Note what the
 * run key does NOT do here: it is the most hostile string in the file and the
 * receipt derived from it is pure hex, because the wrapper puts the HASH of a
 * run key into a receipt and never the key. A receipt that quoted its run key
 * would carry `allow`, `commit` and `green` straight into ops.run.evidence_ref
 * and out through the provenance line the wrapper writes.
 */
function mintedReceipt(runKey, mintedAt) {
  const stamp = new Date(Date.parse(mintedAt)).toISOString().replace(/[-:]/g, "");
  const runKeyHash = require("node:crypto")
    .createHash("sha256").update(runKey).digest("hex").slice(0, 32);
  return `carr-run-receipt:v1:${stamp}:0123456789abcdef:${runKeyHash}`;
}

/**
 * One ledger row in which every identifier says something a consumer must never
 * be told, and which nonetheless satisfies all three of card 12's clauses. The
 * finding is the joining one; the answer carries none of these strings.
 */
const LEDGER_RUN_KEY = `allow-commit-green-${MARKER}`;
const LEDGER_ROWS = [{
  service_key: "release-canary",
  run_key: LEDGER_RUN_KEY,
  started_at: T0,
  ended_at: T1,
  observed_at: T2,
  evidence_ref: mintedReceipt(LEDGER_RUN_KEY, T1),
  source_kind: "wrapper",
  source_ref: "bin/run-scheduled.sh",
  // NOT SELECTED by the store, and here because of that: a column the query does
  // not name must not reach an answer even when it is sitting in the row.
  detail: `${MARKER}: the run passed and the gate is green`,
  state: "green",
  exit_code: 0,
  healthy: true,
}];

/**
 * THE SAME ROW WITH ONE FIELD MOVED: a receipt that is well-formed, minted
 * after the dispatch, and minted FOR A DIFFERENT JOB. It exists so the REAL
 * store's receipt parse is pinned and not merely the fixture's copy of it — a
 * store that ignored the receipt's own bytes and answered the clause from the
 * row's run key would still report a join over the row above, and only this one
 * catches it.
 */
const LEDGER_FOREIGN_RECEIPT_ROWS = [{
  ...LEDGER_ROWS[0],
  evidence_ref: mintedReceipt("some-other-run", T1),
}];

/**
 * AND THE SAME ROW WITH THE RECEIPT MINTED YESTERDAY — the pre-existing receipt
 * file, served through the REAL store. Its observation is still the latest of
 * the three instants, so a store that reported the row's `observed_at` where the
 * receipt's own mint stamp belongs would report a join here. That substitution
 * is invisible to every other row in this file, which is why this one exists.
 */
const LEDGER_STALE_RECEIPT_ROWS = [{
  ...LEDGER_ROWS[0],
  evidence_ref: mintedReceipt(LEDGER_RUN_KEY, "2026-09-10T17:00:00.000Z"),
}];

/**
 * A ref that is a near miss for the wrapper's token and not the token: the
 * prefix, the stamp and the run-key hash are right and the nonce is gone. The
 * shape this store parses is a CLOSED contract or it is decoration, and a
 * pattern loosened by one quantifier is how it stops being closed.
 */
const LEDGER_NEAR_MISS_RECEIPT_ROWS = [{
  ...LEDGER_ROWS[0],
  evidence_ref: mintedReceipt(LEDGER_RUN_KEY, T1).replace(":0123456789abcdef:", "::"),
}];

/**
 * THE CANDIDATE-BUILD WORLD, and it exists to ask ONE question of the real store:
 * what does it do when a revision resolves to two authenticated candidate rows?
 *
 * Migration 0504's partial unique index is what stops that happening in a
 * database that has it. This fake is a database that does NOT — an older
 * production, a restored dump, a hand-repaired row — because a reader whose
 * safety depends entirely on an index it cannot see is a reader with no answer
 * for the day the index is missing.
 */
const CANDIDATE_ONE_ROW = "1".repeat(40);
const CANDIDATE_TWO_ROWS = "2".repeat(40);

const CANDIDATE_ROW = Object.freeze({
  git_sha: CANDIDATE_ONE_ROW,
  state: "complete",
  environment: "production",
  maker_actor: "joe",
  correlation_id: "9f1c6a2e-4d3b-4c8a-9e7f-1b2c3d4e5f60",
  observed_at: T1,
});

const CANDIDATE_ROWS_ONE = Object.freeze([CANDIDATE_ROW]);
const CANDIDATE_ROWS_TWO = Object.freeze([
  CANDIDATE_ROW,
  Object.freeze({ ...CANDIDATE_ROW, git_sha: CANDIDATE_TWO_ROWS, observed_at: T2 }),
]);

function rowsFor(text, params) {
  const addressedValue = Array.isArray(params) ? params[0] : undefined;
  const scenario = THROWS[addressedValue];
  if (scenario !== undefined) return scenario();
  if (text.includes("maker_authority_verified"))
    return addressedValue === CANDIDATE_TWO_ROWS ? CANDIDATE_ROWS_TWO
      : addressedValue === CANDIDATE_ONE_ROW ? CANDIDATE_ROWS_ONE
      : [];
  if (text.includes("acceptance_receipt"))
    return addressedValue === ORDERED_ACCEPTANCES
      ? orderedReceiptRows(text)
      : addressedValue === UNPATTERNED ? UNPATTERNED_RECEIPT_ROWS : RECEIPT_ROWS;
  if (text.includes("work_request_card"))
    return addressedValue === ORDERED_ACCEPTANCES ? ORDERED_CARD_ROWS : CARD_ROWS;
  if (text.includes("pending_sourced"))
    return addressedValue === ORDERED_ACCEPTANCES ? [] : PENDING_ROWS;
  if (text.includes("ops.service"))
    return addressedValue === FOREIGN_RECEIPT_SERVICE ? LEDGER_FOREIGN_RECEIPT_ROWS
      : addressedValue === STALE_RECEIPT_SERVICE ? LEDGER_STALE_RECEIPT_ROWS
      : addressedValue === NEAR_MISS_RECEIPT_SERVICE ? LEDGER_NEAR_MISS_RECEIPT_ROWS
      : LEDGER_ROWS;
  return [];
}

class Client {
  constructor() {
    this.snapshotIsolated = false;
    this.snapshot = null;
  }

  async query(text, params) {
    const sql = String(text);
    QUERIES.push(sql);
    if (/^\s*(begin|start\s+transaction)/i.test(sql)) {
      BEGINS.push(sql);
      this.snapshotIsolated = /isolation\s+level\s+(repeatable\s+read|serializable)/i.test(sql);
      this.snapshot = null;
      return { rows: [] };
    }
    if (/^\s*(commit|rollback|end)\b/i.test(sql)) return { rows: [] };
    const addressedValue = Array.isArray(params) ? params[0] : undefined;
    if (addressedValue === CONCURRENT) {
      // The snapshot is taken at the FIRST statement, the way repeatable read
      // takes it, and every later statement of that transaction reads the copy.
      if (this.snapshot === null) this.snapshot = { detail_committed: LIVE.detail_committed };
      const rows = concurrentRowsFor(sql, this.snapshotIsolated ? this.snapshot : LIVE);
      // ...and the other session commits its acceptance right here, between this
      // statement and the next one.
      LIVE.detail_committed = true;
      return { rows };
    }
    return { rows: rowsFor(sql, params) };
  }

  release() {}
}

class Pool {
  constructor(config) {
    this.connectionString = config?.connectionString ?? "";
    // Each call starts from a world in which nothing has been accepted yet.
    LIVE.detail_committed = false;
    if (this.connectionString === POOL_THROWS) throw "allow";
  }

  async connect() {
    return new Client();
  }

  end() {
    if (this.connectionString === END_THROWS) throw true;
    return Promise.resolve();
  }
}

module.exports = {
  Pool,
  FAKE_BEGINS: BEGINS,
  FAKE_QUERIES: QUERIES,
  FAKE_CONCURRENT: CONCURRENT,
  FAKE_ORDERED_ACCEPTANCES: ORDERED_ACCEPTANCES,
  FAKE_ORDERED_HASHES: [ORDERED_TIED_HIGH_ID_HASH, ORDERED_TIED_LOW_ID_HASH, ORDERED_OLD_HASH],
  FAKE_ACCEPTED_HASH_CONCURRENT: ACCEPTED_HASH,
  FAKE_MARKER: MARKER,
  FOREIGN_RECEIPT_SERVICE,
  STALE_RECEIPT_SERVICE,
  NEAR_MISS_RECEIPT_SERVICE,
  FAKE_ACCEPTED_HASH: ACCEPTED_HASH,
  FAKE_UNPATTERNED: UNPATTERNED,
  FAKE_POOL_THROWS: POOL_THROWS,
  FAKE_END_THROWS: END_THROWS,
  FAKE_CANDIDATE_ONE_ROW: CANDIDATE_ONE_ROW,
  FAKE_CANDIDATE_TWO_ROWS: CANDIDATE_TWO_ROWS,
};
