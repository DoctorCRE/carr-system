// amend-closed-loop-postgres.test.mjs — amend-closed-loop on REAL PostgreSQL.
//
// The fake-client suite (amend-closed-loop.test.mjs) proves the handler's own
// logic in isolation. This file proves the things only a real database can
// prove: the migration 0702 constraints actually hold, trg_touch_row really
// bumps loop_item.version, versionGuard's `for update` really serializes a
// concurrent amend, and an idempotent replay through the REAL tool_call table
// really returns the recorded response without writing a second
// loop_amendment row.
//
// Skips in the unit class; the migration class supplies DATABASE_URL and sets
// CARR_AMEND_CLOSED_LOOP_DB_REQUIRED=1, which turns a silent skip into a
// failure.

import test from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { TOOLS, ToolError } from "../src/tools.js";

// Planted-bug mutant infrastructure for the concurrency proof below, same
// shape as amend-closed-loop-mutants.test.mjs's own mutate()/loadMutant() —
// duplicated rather than imported because that file's real DATABASE_URL-free
// suite must stay import-side-effect-free, and this one only needs it for
// ONE mutant that a fake client cannot exercise: removing versionGuard's
// `for update` is invisible to a single-threaded fake, and only shows up
// against two REAL concurrent Postgres sessions racing the same row.
const MUTANT_SRC = fileURLToPath(new URL("../src/", import.meta.url));
const MUTANT_FILE = "tools.js";
let mutantWork = null;
function relinkMutant(source) {
  return source.replace(/from\s+"\.\/([^"]+)"/g, (_, file) =>
    `from "${pathToFileURL(join(MUTANT_SRC, file)).href}"`);
}
async function loadForUpdateRemovedMutant() {
  if (!mutantWork) mutantWork = mkdtempSync(join(tmpdir(), "amend-closed-loop-concurrency-mutant-"));
  const source = readFileSync(join(MUTANT_SRC, MUTANT_FILE), "utf8");
  const anchor = "for update`, [id]);";
  const count = source.split(anchor).length - 1;
  assert.equal(count, 1, `mutant anchor must occur exactly once in ${MUTANT_FILE}`);
  const mutated = relinkMutant(source.replace(anchor, "`, [id]);"));
  const path = join(mutantWork, `${Date.now()}-tools.mjs`);
  writeFileSync(path, mutated);
  return import(pathToFileURL(path).href);
}
test.after(() => { if (mutantWork) rmSync(mutantWork, { recursive: true, force: true }); });

const DSN = process.env.DATABASE_URL || "";
const REQUIRED = process.env.CARR_AMEND_CLOSED_LOOP_DB_REQUIRED === "1";
const LOOPBACK = /@(localhost|127[.]0[.]0[.]1)[:/]|^postgres(ql)?:\/\/(localhost|\/)/;

async function database(t) {
  if (!DSN) {
    assert.equal(REQUIRED, false, "this proof was required and no database URL was given to it");
    t.skip("the migration class supplies DATABASE_URL");
    return null;
  }
  assert.ok(LOOPBACK.test(DSN),
    "REFUSED: this proof writes loop rows and runs against a disposable loopback only");
  return (await import("pg")).default ?? (await import("pg"));
}

async function connect(pg) {
  const client = new pg.Client({ connectionString: DSN });
  await client.connect();
  return client;
}

/** Two real connections racing under Promise.all is not, by itself, a
 * reliable reproduction of a lost-update race: nothing guarantees the two
 * sessions' `select version from loop_item ...` reads overlap rather than one
 * session writing (or committing) before the other's read executes. An
 * earlier version of this helper only held each read until the other session
 * had ISSUED its own, then let both run free — which said nothing about when
 * either read actually executed relative to the other session's writes.
 *
 * This patches both clients' `query` so that each session's first
 * "select version from loop_item" read RUNS, and then its result is held —
 * the session still inside its open transaction, before any write — until
 * one of exactly two things is observed:
 *
 *   - the OTHER session's read has also returned. Without `for update` this
 *     is always what happens: both sessions have read the same version before
 *     either writes, deterministically, which is the lost-update interleaving
 *     the mutant must exhibit.
 *   - the OTHER session's backend is waiting on a Lock, on that same read,
 *     blocked by THIS session's pid (pg_stat_activity + pg_blocking_pids).
 *     With `for update` this is always what happens: the second reader is
 *     provably queued behind the first's row lock, so the real test exercises
 *     the lock rather than a lucky ordering. Holding for "both read" here
 *     would deadlock (the blocked read cannot return until this session
 *     commits), which is why the lock wait is itself a release condition —
 *     and why pointing the mutant test at the real code fails its assertion
 *     instead of hanging.
 *
 * Neither condition within 10s is a loud failure, never a silent pass. The
 * returned state records what each session read and which condition released
 * the holds, for the tests to assert on. */
function holdVersionReadsUntilOverlap(admin, clientA, clientB) {
  const TIMEOUT_MS = 10000;
  const state = { reads: {}, lockWaitObserved: false, bothReadObserved: false };
  const returned = { A: false, B: false };
  function wrap(name, client, otherName, other) {
    const original = client.query.bind(client);
    let intercepted = false;
    client.query = (text, params) => {
      if (intercepted || typeof text !== "string" || !text.startsWith("select version from loop_item"))
        return original(text, params);
      intercepted = true;
      return original(text, params).then(async (result) => {
        state.reads[name] = result.rows[0]?.version;
        returned[name] = true;
        const deadline = Date.now() + TIMEOUT_MS;
        for (;;) {
          if (returned[otherName]) { state.bothReadObserved = true; return result; }
          const waiting = await admin.query(
            `select 1 from pg_stat_activity
              where pid = $1 and wait_event_type = 'Lock'
                and query like 'select version from loop_item%'
                and $2::int = any(pg_blocking_pids(pid))`,
            [other.processID, client.processID]);
          if (waiting.rows.length) { state.lockWaitObserved = true; return result; }
          if (Date.now() > deadline)
            throw new Error(`session ${name} read version ${state.reads[name]} and session ${otherName} ` +
              `neither read nor blocked on the row lock within ${TIMEOUT_MS}ms`);
          await new Promise((resolve) => setTimeout(resolve, 2));
        }
      });
    };
  }
  wrap("A", clientA, "B", clientB);
  wrap("B", clientB, "A", clientA);
  return state;
}

/** One amend attempt inside its already-open transaction. The COMMIT (or
 * rollback) is AWAITED before the attempt settles. It used to be fired and
 * forgotten, so Promise.all could settle — and the admin connection read the
 * amendment trail — while the last winner's COMMIT was still in flight: the
 * trail then showed one row where two had been written (the hosted-CI flake,
 * "actual: 1, expected: 2"), and the same query moments later showed both. */
async function attemptInTransaction(client, run) {
  let result;
  try {
    result = await run();
  } catch (e) {
    await client.query("rollback");
    return { ok: false, e };
  }
  await client.query("commit");
  return { ok: true, r: result };
}

async function mintActor(admin, slug) {
  const r = await admin.query(
    `insert into public.actor(slug, kind, display_name, active) values ($1,'automation',$1,true)
       on conflict (slug) do update set active = true returning id, slug`, [slug]);
  return { id: r.rows[0].id, slug, human: false, via: "test", client_id: "amend-closed-loop-postgres.test" };
}

/** One verb call in its own transaction, exactly as mcp.js runs a write. */
async function call(client, actor, verb, args) {
  await client.query("begin");
  try {
    const result = await TOOLS[verb].handler(client, actor, args);
    await client.query("commit");
    return result;
  } catch (e) {
    await client.query("rollback");
    throw e;
  }
}

function refusal(error) {
  return (e) => e instanceof ToolError && e.payload.error === error;
}

/** The loop_block scaffolding rows only exist once the loop importer has run
 * against real vault files — never true in a disposable migration-class
 * database. add-loop and close-loop both look one up by (kind, block_key)
 * and refuse with no_block when it is missing. Seed the two team_loop blocks
 * this proof needs (open, done) once, idempotently, so the fixture doesn't
 * depend on an importer this database will never run. */
async function ensureTeamLoopBlocks(client, actor) {
  const existing = await client.query(
    "select block_key from loop_block where kind='team_loop' and block_key in ('open','done')");
  const have = new Set(existing.rows.map(r => r.block_key));
  const rows = [
    { block_key: "open", seq: 1, renders_closed: false, header_cols: ["#", "Owner", "Ask"], col_order: ["number", "owner", "title"] },
    { block_key: "done", seq: 2, renders_closed: true, header_cols: ["#", "Owner", "Ask", "Outcome"], col_order: ["number", "owner", "title", "close_outcome"] },
  ];
  for (const row of rows) {
    if (have.has(row.block_key)) continue;
    await client.query(
      `insert into loop_block (rel_path, kind, seq, block_key, header_cols, col_order, renders_closed, created_by, updated_by)
         values ('team-loops.md', 'team_loop', $1, $2, $3, $4, $5, $6, $6)
         on conflict (rel_path, seq) do nothing`,
      [row.seq, row.block_key, row.header_cols, row.col_order, row.renders_closed, actor.id]);
  }
}

/** Opens a fresh team_loop (no blocker requirement, unlike open_loop) and
 * closes it, returning its id and the version read back after closing. */
async function freshClosedLoop(client, actor, { outcome = "Landed in PR #900, verified twice." } = {}) {
  await ensureTeamLoopBlocks(client, actor);
  const opened = await call(client, actor, "add-loop", {
    idempotency_key: randomUUID(), kind: "team_loop", owner: "joe",
    title: `amend-closed-loop proof ${randomUUID().slice(0, 8)}`, body: "fixture row" });
  const closed = await call(client, actor, "close-loop", {
    idempotency_key: randomUUID(), loop_id: opened.loop_id, base_version: 1,
    outcome, resolution: "done" });
  return { loop_id: opened.loop_id, version: 2, prior_outcome: outcome };
}

test("DB: an amend appends loop_amendment, updates loop_item's current projection, and bumps its version", async (t) => {
  const pg = await database(t); if (!pg) return;
  const admin = await connect(pg);
  const actor = await mintActor(admin, `amend-loop-actor-${randomUUID().slice(0, 8)}`);
  try {
    const { loop_id, version, prior_outcome } = await freshClosedLoop(admin, actor);

    // Captured BEFORE the amend — these two fields belong to the original
    // close, not the correction, and the reviewer wants exact identity
    // proven, not merely "still non-null" (which a bug that stamps a new
    // closed_at/closed_by would still pass).
    const before = await admin.query(
      "select closed_at, closed_by from loop_item where id=$1", [loop_id]);

    const newOutcome = "Corrected: the card visual system shipped, not the bio-header reminder.";
    const reason = "Original outcome text was pasted from the wrong loop.";
    const result = await call(admin, actor, "amend-closed-loop", {
      idempotency_key: randomUUID(), loop_id, base_version: version,
      outcome: newOutcome, reason });

    assert.equal(result.ok, true);
    assert.equal(result.prior_outcome, prior_outcome);
    assert.equal(result.outcome, newOutcome);

    const row = await admin.query(
      "select close_outcome, outcome, status, version, closed_at, closed_by from loop_item where id=$1", [loop_id]);
    assert.equal(row.rows[0].close_outcome, newOutcome, "the current projection reads the latest amendment");
    assert.equal(row.rows[0].outcome, newOutcome);
    assert.equal(row.rows[0].status, "done", "resolution unchanged when not passed");
    assert.equal(row.rows[0].version, version + 1, "trg_touch_row bumps the version on the projection update");
    assert.equal(row.rows[0].closed_at.getTime(), before.rows[0].closed_at.getTime(),
      "closed_at is EXACTLY unchanged by an amendment, not merely still-present");
    assert.equal(row.rows[0].closed_by, before.rows[0].closed_by,
      "closed_by is EXACTLY unchanged by an amendment — the original closer stays the original closer");

    const trail = await admin.query(
      "select prior_outcome, new_outcome, reason, actor_id from loop_amendment where loop_id=$1 order by created_at", [loop_id]);
    assert.equal(trail.rows.length, 1, "exactly one amendment row for one correction");
    assert.equal(trail.rows[0].prior_outcome, prior_outcome);
    assert.equal(trail.rows[0].new_outcome, newOutcome);
    assert.equal(trail.rows[0].reason, reason);
    assert.equal(trail.rows[0].actor_id, actor.id, "actor_id is the server-derived caller, never a client field");
  } finally {
    await admin.end();
  }
});

test("DB: history is preserved across TWO amendments — both rows survive, in order, and the projection reads the latest", async (t) => {
  const pg = await database(t); if (!pg) return;
  const admin = await connect(pg);
  const actor = await mintActor(admin, `amend-loop-history-${randomUUID().slice(0, 8)}`);
  try {
    const { loop_id, version: v1 } = await freshClosedLoop(admin, actor, { outcome: "x" });

    const first = await call(admin, actor, "amend-closed-loop", {
      idempotency_key: randomUUID(), loop_id, base_version: v1,
      outcome: "First correction of the placeholder outcome.", reason: "placeholder was never real" });
    const second = await call(admin, actor, "amend-closed-loop", {
      idempotency_key: randomUUID(), loop_id, base_version: v1 + 1,
      outcome: "Second correction, now accurate for real.", reason: "first correction still had the wrong PR number" });

    assert.equal(first.prior_outcome, "x");
    assert.equal(second.prior_outcome, "First correction of the placeholder outcome.");

    const trail = await admin.query(
      "select prior_outcome, new_outcome from loop_amendment where loop_id=$1 order by created_at", [loop_id]);
    assert.equal(trail.rows.length, 2, "NEITHER prior amendment is overwritten — append-only means both survive");
    assert.equal(trail.rows[0].new_outcome, "First correction of the placeholder outcome.");
    assert.equal(trail.rows[1].new_outcome, "Second correction, now accurate for real.");

    const row = await admin.query("select close_outcome from loop_item where id=$1", [loop_id]);
    assert.equal(row.rows[0].close_outcome, "Second correction, now accurate for real.",
      "the loop's current outcome reads the LATEST amendment, not the first");
  } finally {
    await admin.end();
  }
});

test("DB: a stale base_version is refused with version_conflict and writes nothing", async (t) => {
  const pg = await database(t); if (!pg) return;
  const admin = await connect(pg);
  const actor = await mintActor(admin, `amend-loop-stale-${randomUUID().slice(0, 8)}`);
  try {
    const { loop_id, version } = await freshClosedLoop(admin, actor);
    await assert.rejects(() => call(admin, actor, "amend-closed-loop", {
      idempotency_key: randomUUID(), loop_id, base_version: version - 1,
      outcome: "This should never land.", reason: "stale read" }), refusal("version_conflict"));
    const trail = await admin.query("select count(*)::int as n from loop_amendment where loop_id=$1", [loop_id]);
    assert.equal(trail.rows[0].n, 0, "a refused amend never appends a row");
  } finally {
    await admin.end();
  }
});

test("DB: an OPEN loop is refused with loop_open, never partially amended", async (t) => {
  const pg = await database(t); if (!pg) return;
  const admin = await connect(pg);
  const actor = await mintActor(admin, `amend-loop-open-${randomUUID().slice(0, 8)}`);
  try {
    const opened = await call(admin, actor, "add-loop", {
      idempotency_key: randomUUID(), kind: "team_loop", owner: "joe",
      title: `still-open ${randomUUID().slice(0, 8)}`, body: "fixture" });
    await assert.rejects(() => call(admin, actor, "amend-closed-loop", {
      idempotency_key: randomUUID(), loop_id: opened.loop_id, base_version: 1,
      outcome: "This should never land.", reason: "premature" }), refusal("loop_open"));
    const row = await admin.query("select status, close_outcome from loop_item where id=$1", [opened.loop_id]);
    assert.equal(row.rows[0].status, "open");
    assert.equal(row.rows[0].close_outcome, null);
  } finally {
    await admin.end();
  }
});

test("DB: a too-short outcome is refused by the same defect a2c04ffa shape ('x') before any write", async (t) => {
  const pg = await database(t); if (!pg) return;
  const admin = await connect(pg);
  const actor = await mintActor(admin, `amend-loop-short-${randomUUID().slice(0, 8)}`);
  try {
    const { loop_id, version } = await freshClosedLoop(admin, actor);
    await assert.rejects(() => call(admin, actor, "amend-closed-loop", {
      idempotency_key: randomUUID(), loop_id, base_version: version,
      outcome: "x", reason: "typo" }), refusal("outcome_too_short"));
    const trail = await admin.query("select count(*)::int as n from loop_amendment where loop_id=$1", [loop_id]);
    assert.equal(trail.rows[0].n, 0);
  } finally {
    await admin.end();
  }
});

test("DB: a caller-supplied actor field never reaches the actor_id column", async (t) => {
  const pg = await database(t); if (!pg) return;
  const admin = await connect(pg);
  const actor = await mintActor(admin, `amend-loop-actor-spoof-${randomUUID().slice(0, 8)}`);
  const impersonated = await mintActor(admin, `amend-loop-impersonated-${randomUUID().slice(0, 8)}`);
  try {
    const { loop_id, version } = await freshClosedLoop(admin, actor);
    // TOOLS["amend-closed-loop"].inputSchema has no `actor`/`actor_id` property at
    // all; the handler never reads args.actor* — this asserts the OUTCOME (the
    // row) rather than merely the schema, which is what actually matters.
    await call(admin, actor, "amend-closed-loop", {
      idempotency_key: randomUUID(), loop_id, base_version: version,
      outcome: "Corrected outcome text, real fix landed.", reason: "server actor proof",
      actor: impersonated.slug, actor_id: impersonated.id });
    const trail = await admin.query("select actor_id from loop_amendment where loop_id=$1", [loop_id]);
    assert.equal(trail.rows[0].actor_id, actor.id);
    assert.notEqual(trail.rows[0].actor_id, impersonated.id);
  } finally {
    await admin.end();
  }
});

test("DB: an idempotent replay returns the recorded response and writes no second loop_amendment row", async (t) => {
  const pg = await database(t); if (!pg) return;
  const admin = await connect(pg);
  const actor = await mintActor(admin, `amend-loop-replay-${randomUUID().slice(0, 8)}`);
  try {
    const { loop_id, version } = await freshClosedLoop(admin, actor);
    const key = randomUUID();
    const args = { idempotency_key: key, loop_id, base_version: version,
      outcome: "Replayed correction text, landed once.", reason: "idempotency proof" };

    const first = await call(admin, actor, "amend-closed-loop", args);
    const second = await call(admin, actor, "amend-closed-loop", args);

    assert.equal(second.replayed, true);
    assert.equal(second.loop_id, first.loop_id);
    assert.equal(second.outcome, first.outcome);

    const trail = await admin.query("select count(*)::int as n from loop_amendment where loop_id=$1", [loop_id]);
    assert.equal(trail.rows[0].n, 1, "the replay must not append a second amendment row");
    const row = await admin.query("select version from loop_item where id=$1", [loop_id]);
    assert.equal(row.rows[0].version, version + 1, "the replay must not bump the version a second time");
  } finally {
    await admin.end();
  }
});

test("DB: read-loop surfaces the amendment trail — amended flag, oldest-first amendments, no base-table grant needed", async (t) => {
  const pg = await database(t); if (!pg) return;
  const admin = await connect(pg);
  const actor = await mintActor(admin, `amend-loop-history-read-${randomUUID().slice(0, 8)}`);
  try {
    const { loop_id, version: v1 } = await freshClosedLoop(admin, actor, { outcome: "x" });

    const before = await TOOLS["read-loop"].handler(admin, actor, { loop_id });
    assert.equal(before.amended, false, "an un-amended loop reports amended:false");
    assert.deepEqual(before.amendments, [], "an un-amended loop's amendment list is empty, not missing");

    const first = await call(admin, actor, "amend-closed-loop", {
      idempotency_key: randomUUID(), loop_id, base_version: v1,
      outcome: "First correction of the placeholder outcome.", reason: "placeholder was never real" });
    const second = await call(admin, actor, "amend-closed-loop", {
      idempotency_key: randomUUID(), loop_id, base_version: v1 + 1,
      outcome: "Second correction, now accurate for real.", reason: "first correction still had the wrong PR number" });

    const after = await TOOLS["read-loop"].handler(admin, actor, { loop_id });
    assert.equal(after.amended, true, "amended flips true once amend-closed-loop has ever run");
    assert.equal(after.amendments.length, 2);
    assert.equal(after.amendments[0].prior_outcome, "x", "oldest first: the very first correction reads first");
    assert.equal(after.amendments[0].new_outcome, "First correction of the placeholder outcome.");
    assert.equal(after.amendments[0].reason, "placeholder was never real");
    assert.equal(after.amendments[0].actor, actor.slug, "actor is a slug, not a raw uuid");
    assert.ok(after.amendments[0].created_at, "each amendment row carries a timestamp");
    assert.equal(after.amendments[1].prior_outcome, "First correction of the placeholder outcome.");
    assert.equal(after.amendments[1].new_outcome, "Second correction, now accurate for real.");
    assert.ok(new Date(after.amendments[0].created_at).getTime() <= new Date(after.amendments[1].created_at).getTime(),
      "oldest first means row[0].created_at <= row[1].created_at");
    assert.equal(after.loop.loop_id, first.loop_id, "the loop payload itself is unchanged in shape");

    // THE READER MUST NOT NEED A BASE-TABLE GRANT. Prove the exact privilege
    // shape the migration comment claims: carr_reader gets EXECUTE on the
    // definer function and NOTHING on loop_amendment itself — the same style
    // gate-zero-outcome-role-boundary.test.mjs uses for its own writer/reader
    // split, checked directly against pg_catalog rather than by opening a
    // second reader-credentialed connection.
    const acl = await admin.query(`select
        has_table_privilege('carr_reader', 'loop_amendment', 'select') as reader_select,
        has_function_privilege('carr_reader', 'loop_amendment_history(uuid)', 'execute') as reader_execute`);
    assert.equal(acl.rows[0].reader_select, false,
      "carr_reader must never get a base-table grant on the append-only loop_amendment table");
    assert.equal(acl.rows[0].reader_execute, true,
      "carr_reader must be able to call the SECURITY DEFINER read door read-loop actually uses");
  } finally {
    await admin.end();
  }
});

test("DB: for update really serializes two concurrent amends against the same base_version — exactly one wins, the other gets version_conflict", async (t) => {
  const pg = await database(t); if (!pg) return;
  const a = await connect(pg);
  const b = await connect(pg);
  const admin = await connect(pg);
  const actor = await mintActor(admin, `amend-loop-concurrency-${randomUUID().slice(0, 8)}`);
  try {
    const { loop_id, version } = await freshClosedLoop(admin, actor, { outcome: "x" });

    // Two separate sessions, each in its own transaction, racing the SAME
    // base_version. versionGuard's `select ... for update` is what makes this
    // safe: the second session's lock acquisition blocks behind the first's
    // until the first commits (bumping the row's version), so the second
    // sees the NEW version and gets version_conflict rather than also
    // succeeding against the stale one it read.
    await a.query("begin");
    await b.query("begin");
    const overlap = holdVersionReadsUntilOverlap(admin, a, b);
    const attemptA = attemptInTransaction(a, () => TOOLS["amend-closed-loop"].handler(a, actor, {
      idempotency_key: randomUUID(), loop_id, base_version: version,
      outcome: "Session A's correction of the placeholder outcome.", reason: "session A" }));
    const attemptB = attemptInTransaction(b, () => TOOLS["amend-closed-loop"].handler(b, actor, {
      idempotency_key: randomUUID(), loop_id, base_version: version,
      outcome: "Session B's correction of the placeholder outcome.", reason: "session B" }));

    const [resA, resB] = await Promise.all([attemptA, attemptB]);
    const outcomes = [resA, resB];
    const winners = outcomes.filter((o) => o.ok);
    const losers = outcomes.filter((o) => !o.ok);

    assert.equal(winners.length, 1, "exactly one of the two concurrent same-base_version amends must succeed");
    assert.equal(losers.length, 1, "the other must be refused, never silently dropped or silently doubled");
    assert.ok(losers[0].e instanceof ToolError && losers[0].e.payload.error === "version_conflict",
      "the loser's refusal must be version_conflict specifically");

    const trail = await admin.query(
      "select prior_outcome, new_outcome from loop_amendment where loop_id=$1 order by created_at", [loop_id]);
    assert.equal(trail.rows.length, 1,
      "the lock must produce EXACTLY ONE amendment row, not one per attempt — without `for update` both sessions " +
      "read the same stale version and both succeed, producing two rows that both record prior_outcome 'x'");
    assert.equal(trail.rows[0].prior_outcome, "x");
    assert.equal(overlap.lockWaitObserved, true,
      "the losing session's version read must have been OBSERVED waiting on the winner's row lock — " +
      "otherwise this run proved a lucky ordering, not the lock");
  } finally {
    await a.end();
    await b.end();
    await admin.end();
  }
});

test("DB MUTANT: removing versionGuard's `for update` breaks the concurrency proof above — two rows, both prior_outcome 'x'", async (t) => {
  const pg = await database(t); if (!pg) return;
  const mutant = await loadForUpdateRemovedMutant();
  const a = await connect(pg);
  const b = await connect(pg);
  const admin = await connect(pg);
  const actor = await mintActor(admin, `amend-loop-mutant-concurrency-${randomUUID().slice(0, 8)}`);
  try {
    await ensureTeamLoopBlocks(admin, actor);
    const opened = await mutant.TOOLS["add-loop"].handler(admin, actor, {
      idempotency_key: randomUUID(), kind: "team_loop", owner: "joe",
      title: `amend-closed-loop mutant proof ${randomUUID().slice(0, 8)}`, body: "fixture row" });
    await mutant.TOOLS["close-loop"].handler(admin, actor, {
      idempotency_key: randomUUID(), loop_id: opened.loop_id, base_version: 1,
      outcome: "x", resolution: "done" });
    const loop_id = opened.loop_id;
    const version = 2;

    await a.query("begin");
    await b.query("begin");
    const overlap = holdVersionReadsUntilOverlap(admin, a, b);
    const attemptA = attemptInTransaction(a, () => mutant.TOOLS["amend-closed-loop"].handler(a, actor, {
      idempotency_key: randomUUID(), loop_id, base_version: version,
      outcome: "Session A's correction of the placeholder outcome.", reason: "session A" }));
    const attemptB = attemptInTransaction(b, () => mutant.TOOLS["amend-closed-loop"].handler(b, actor, {
      idempotency_key: randomUUID(), loop_id, base_version: version,
      outcome: "Session B's correction of the placeholder outcome.", reason: "session B" }));

    const [resA, resB] = await Promise.all([attemptA, attemptB]);
    const winners = [resA, resB].filter((o) => o.ok);

    // THE PROOF THAT THE LOCK IS LOAD-BEARING: without `for update`, both
    // sessions read version=2 before either writes (holdVersionReadsUntilOverlap
    // forces that interleaving), so BOTH pass versionGuard
    // and BOTH succeed — the exact bug the reviewer identified. If this
    // mutant somehow still produced only one winner, the concurrency test
    // above would not actually be exercising the lock and would need
    // rethinking; asserting the mutant's broken behavior here is what makes
    // the real test's green run meaningful instead of coincidental.
    assert.equal(winners.length, 2,
      "MUTANT DETECTED CORRECTLY: without `for update` both concurrent same-base_version amends succeed");

    const trail = await admin.query(
      "select prior_outcome from loop_amendment where loop_id=$1 order by created_at", [loop_id]);
    assert.equal(trail.rows.length, 2, "the mutant appends two amendment rows, not one");
    assert.ok(trail.rows.every((r) => r.prior_outcome === "x"),
      "both rows record the same stale prior_outcome — neither session saw the other's write");
    assert.deepEqual(overlap.reads, { A: version, B: version },
      "both sessions read the same base version inside their open transactions");
    assert.equal(overlap.bothReadObserved, true,
      "neither session was released to write until both had read — the forced lost-update interleaving");
    assert.equal(overlap.lockWaitObserved, false, "without `for update` no session ever waits on the read");
  } finally {
    await a.end();
    await b.end();
    await admin.end();
  }
});
