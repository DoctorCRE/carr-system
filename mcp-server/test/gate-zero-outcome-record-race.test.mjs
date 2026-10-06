// THE GATE ZERO RETRY PROOF — one authenticated produced receipt, two real
// database writers, one candidate and one durable row.
//
// WHAT THIS FILE IS FOR, AND WHAT IT USED TO BE. Its first version handed ONE
// hand-built fixture receipt to two raw connections and called the SQL writer
// directly. That proved the candidate key arbitrates a race between two callers
// holding identical bytes — and nothing at all about a retry, because two
// genuine authenticated calls never hold identical bytes. identity.js derives
// `session_ref` from the request's own correlation id, and the producer stamps
// `observed_at` and `ttl_expires_at` when the run happens, so the second real
// call for one candidate differed from the first in three fields, the writer
// compared the FULL receipt digest, and it refused. That was Sol's finding 3,
// and a fixture-fed race test is exactly why a green suite did not see it.
//
// The receipt is produced inside the registered verb over a staged candidate
// tree. Its exact bytes are then raced through two authenticated seat
// connections at the real record seam; no hand-built receipt is used.
//
// THE THREE PROPERTIES IT ESTABLISHES:
//
//   1. IDEMPOTENT FOR AN EXACT RECEIPT REPLAY. Both writers receive one row.
//   2. ATOMICALLY, under concurrency. The interleaving is made deterministic
//      rather than hoped for: A calls the verb and leaves its transaction open,
//      so its candidate-key entry is speculative; B's call is sent and blocks
//      inside the insert; A commits; B takes the ON CONFLICT DO NOTHING branch,
//      reads the committed row in its fallback select -- a new statement and so
//      a new snapshot -- and answers with the same id. Against a
//      lookup-then-insert writer this fails with a unique_violation.
//   3. IMMUTABLE UNDER A DIFFERENT OUTCOME. A later different receipt for the
//      same candidate converges to, and cannot replace, the immutable first row.
//
// The writer's own definition is read back from the catalog and checked for the
// unconditional fallback; the different-outcome case then executes that path.
//
// THE CLIENTS ARE REAL, and the recorder around them is a passthrough: every
// statement reaches PostgreSQL, and the wrapper keeps a copy of the receipt each
// call sent so the two can be compared afterwards. Nothing is answered locally.
//
// IT SKIPS WITHOUT A DATABASE, and it REFUSES a database that is not loopback.
// The rows it writes are committed on purpose -- an uncommitted race proves
// nothing -- and the record is append-only by trigger, so they cannot be removed
// afterwards. That is safe on the throwaway database the migration class builds
// and is why this file declines to point at anything else.
//
//   DATABASE_URL=postgresql://localhost/... node --test \
//     mcp-server/test/gate-zero-outcome-record-race.test.mjs

import test, { after } from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";

import { gateZeroOutcomeCandidateDigest, gateZeroOutcomeDigest }
  from "../src/gate-zero-outcome-store.v5.js";
import { cleanupStagedTrees, DYNAMIC_PREDECESSOR_WORLD, inServedReview,
  moduleOfTree, stageTree, withStamps }
  from "./gate-zero-candidate-tree.testhelper.mjs";
import { authorizeAs, ensureProducerRoles }
  from "./gate-zero-producer-role.testhelper.mjs";

const DSN = process.env.CARR_GATE_ZERO_RACE_DSN || process.env.DATABASE_URL || "";
// A SKIP THAT NOBODY NOTICES IS A TEST COLLECTED BY NOBODY. The migration class
// sets this, so in the one place this proof is meant to run, "no database" and
// "no reachable Postgres" FAIL instead of skipping quietly. Everywhere else --
// the unit class, a developer's shell -- the skip is the honest answer.
const REQUIRED = process.env.CARR_GATE_ZERO_RACE_REQUIRED === "1";
const LOOPBACK = /@(localhost|127\.0\.0\.1)[:/]|^postgres(ql)?:\/\/(localhost|\/)/;

const VERB = "record-gate-zero-read-only-outcome";
/** The seat lane the record layer staffs. Not a choice this file makes. */
const SEAT_SLUG = "codex-reviewer";

after(cleanupStagedTrees);

/**
 * TWO REAL CONNECTIONS PER CALLER, WHICH IS WHAT PRODUCTION OPENS
 * (2026-09-14, PR 1014 third correction, standing-rule amendment 9).
 *
 *   * `client` is the ordinary writer connection every verb runs on. It carries
 *     the actor lookup and the audit event, and it still sets the server-
 *     established actor context mcp.js sets -- which on this path is now
 *     INFORMATIONAL: the record layer stopped reading it.
 *   * `seat` AUTHENTICATES AS THE DEDICATED PRODUCER LOGIN ROLE, and it is the
 *     only connection here that may execute the writer at all. carr_writer's
 *     EXECUTE is revoked; session_user is what the record layer derives the
 *     producing seat from.
 *
 * THE SEAT'S TRANSACTION IS THE TEST'S, not gateZeroSeatConnection's, and that
 * is the point of this file: the production door begins and commits its own
 * transaction, which would settle the race before it could be staged. Handing
 * the handler a conforming door whose transaction stays open is what makes the
 * interleaving deterministic rather than hoped for.
 *
 * The returned object is a PASSTHROUGH RECORDER: every statement reaches
 * PostgreSQL and answers with what PostgreSQL answered, and the receipt this
 * caller sent to the writer is kept so the two calls can be compared afterwards.
 */
async function seatedClient(pg, producerLoginRole) {
  const client = new pg.Client({ connectionString: DSN });
  await client.connect();
  await client.query("begin");
  await client.query("select set_config('carr.acting_actor_slug', $1, true)", [SEAT_SLUG]);

  const seat = new pg.Client({ connectionString: DSN });
  await seat.connect();
  await authorizeAs(seat, producerLoginRole);

  const recorder = {
    client,
    seat,
    sent: null,
    query: async (sql, params = []) => client.query(sql, params),
    // THE TRANSACTION OPENS HERE, NOT AT CONNECT, and that is not a detail.
    // ops.gate_zero_read_only_outcome.recorded_at defaults to now(), which is
    // TRANSACTION START, and the constraint is recorded_at >= observed_at. The
    // production door begins its transaction immediately before the write, after
    // the producer has stamped observed_at, so the order holds by construction.
    // A transaction opened at connect time -- before staging a candidate tree
    // and running the producer, which takes well over a second -- makes now()
    // EARLIER than observed_at and the row is refused. What this file needs is
    // only that the transaction stay OPEN afterwards, which it does: nothing
    // commits it but the test.
    seatConnection: async run => {
      await seat.query("begin");
      return run({
        query: async (sql, params = []) => {
          if (typeof sql === "string" && sql.includes("ops.gate_zero_record_read_only_outcome"))
            recorder.sent = JSON.parse(params[1]);
          return seat.query(sql, params);
        },
      });
    },
  };
  return recorder;
}

test("two identical concurrent writes for one candidate return one durable row", async t => {
  if (!DSN) {
    assert.equal(REQUIRED, false,
      "this proof was required and no database URL was given to it");
    return t.skip("no DATABASE_URL / CARR_GATE_ZERO_RACE_DSN (the migration class provides one)");
  }
  assert.ok(LOOPBACK.test(DSN),
    "REFUSED: this proof commits rows to an append-only record and runs against a throwaway only");
  const pg = await import("pg");

  let a;
  let b;
  let setup;
  try {
    // The dedicated producer login role 0502 deliberately does not create. On a
    // throwaway database this helper makes it; in production the provisioner
    // does, as a third LoginProfile with its own secret.
    setup = new (pg.default ?? pg).Client({ connectionString: DSN });
    await setup.connect();
    const roles = await ensureProducerRoles(setup);
    a = await seatedClient(pg.default ?? pg, roles.login);
    b = await seatedClient(pg.default ?? pg, roles.login);
  } catch (error) {
    if (setup) await setup.end().catch(() => {});
    for (const one of [a, b]) {
      if (one) await one.client.end().catch(() => {});
      if (one) await one.seat.end().catch(() => {});
    }
    assert.equal(REQUIRED, false, `this proof was required and Postgres was unreachable: ${error.message}`);
    return t.skip(`no reachable Postgres: ${error.message}`);
  }
  t.after(async () => {
    await setup.end().catch(() => {});
    for (const one of [a, b]) {
      await one.client.end().catch(() => {});
      await one.seat.end().catch(() => {});
    }
  });

  // THE ACTOR ROW IS THE DATABASE'S, read rather than asserted into existence.
  // The gateway needs the id for its audit envelope; the record layer derives
  // the same row again from the transaction's actor context, and neither takes
  // it from the other.
  const seatRow = (await a.query(
    "select id, kind, active from public.actor where slug = $1", [SEAT_SLUG])).rows[0];
  assert.ok(seatRow, `this database has no ${SEAT_SLUG} actor to act as`);
  assert.equal(seatRow.active, true);
  assert.notEqual(seatRow.kind, "human");

  // ONE CANDIDATE TREE, ONE MODULE GRAPH, TWO CALLS. Both calls read the same
  // candidate, so they must reach the same candidate digest; what differs is
  // the correlation id, which is what differs between two real requests.
  //
  // NEITHER ACTOR IS CHOSEN HERE, and since PR 1013's fifth correction there is
  // no way one could be. The staged identity.js exports no minter and no
  // dispatcher; `inServedReview` hands the staged review door the recorded
  // Authorization header and the server's per-request correlation id, and the
  // door authenticates, derives and enters the call itself. What the
  // continuation receives is that call's own actor, decorated IN PLACE with the
  // audit row id the way index.js decorates it -- so the two calls below differ
  // by exactly what two real requests differ by.
  const FIRST_CALL = "2c8f5a91-7d3e-4b06-9a14-6e0d8b5f37c2";
  const { target, stamps } = stageTree({});
  const tools = await moduleOfTree(target, "tools.js");
  // UNDER THIS DEPLOY'S BUILD STAMPS (amendment 9). The producer derives its
  // candidate from the three vars bin/deploy-worker.sh writes at build time, not
  // from a repository at request time, and `stageTree` seals the tree it just
  // staged into exactly those vars. `withStamps` is `wrangler --var` for the
  // duration of the call: without it a staged tree is a deploy that carries no
  // candidate stamp, which is its own refusal.
  const callVerb = (correlationId, client) => withStamps(stamps,
    () => inServedReview(target, { correlationId },
      actor => tools.executeRegisteredTool(client, Object.assign(actor, { id: seatRow.id }),
        VERB, { idempotency_key: randomUUID() })));

  // (1) A calls the verb and does NOT commit: its candidate-key entry is
  //     speculative, and its receipt was produced inside this call.
  const firstServed = await callVerb(FIRST_CALL, a);
  assert.equal(firstServed.served, true, "the recorded review bearer was not served");
  const firstResult = firstServed.answered;
  assert.equal(firstResult.ok, true);
  assert.match(firstResult.outcome_id, /^[0-9a-f-]{36}$/);
  assert.equal(firstResult.status, "pass");

  // (2) B offers the EXACT receipt A wrote and is left in flight. It blocks
  //     inside the insert on A's uncommitted candidate-key entry. Later tests
  //     separately prove that non-identical same-candidate retries converge too.
  const sentA = a.sent;
  assert.ok(sentA, "the first call never reached the writer");
  await b.seat.query("begin");
  const pending = b.seat.query(
    "select ops.gate_zero_record_read_only_outcome($1::uuid, $2::jsonb) as id",
    [randomUUID(), JSON.stringify(sentA)]).then(answer => answer.rows[0].id);
  let settledEarly = false;
  pending.then(() => { settledEarly = true; }, () => { settledEarly = true; });
  await new Promise(resolve => setTimeout(resolve, 500));
  assert.equal(settledEarly, false,
    "the second writer did not block on the first, so the candidate key is not arbitrating");

  // (3) A's seat commits, which releases B's identical replay.
  await a.seat.query("commit");
  const secondId = await pending;
  await b.seat.query("commit");
  await a.client.query("commit");
  await b.client.query("rollback");

  // BOTH IDENTICAL WRITES RECEIVED THE SAME DURABLE ROW.
  assert.equal(secondId, firstResult.outcome_id,
    "the identical concurrent replay did not receive the row the first write created");

  // AND EXACTLY ONE ROW EXISTS, which is the property the whole shape is for:
  // every run kept, one current outcome per candidate.
  const rows = await a.query(
    "select count(*)::int as n from ops.gate_zero_read_only_outcome where candidate_digest = $1",
    [firstResult.candidate_digest]);
  assert.equal(rows.rows[0].n, 1, "a retry minted a second outcome for one candidate");

  // The receipt came from the registered authenticated verb, not a hand-built
  // fixture; B merely replays those exact stored bytes at the record seam.
  assert.equal(sentA.producer_identity.session_ref, `session:${FIRST_CALL}`);

  // AND THE DATABASE AGREES WITH THE GATEWAY about both values, computed by its
  // own canonicalization rather than by artifact-trust.js.
  const digests = await a.query(
    `select ops.gate_zero_outcome_digest($1::jsonb) as full,
            ops.gate_zero_outcome_candidate_digest($1::jsonb) as candidate`,
    [JSON.stringify(sentA)]);
  assert.equal(digests.rows[0].full, gateZeroOutcomeDigest(sentA));
  assert.equal(digests.rows[0].candidate, gateZeroOutcomeCandidateDigest(sentA));

  // THE FALLBACK IS UNCONDITIONAL, read out of the live catalog rather than
  // trusted from source. It may log changed bytes, but it may not raise before
  // returning the immutable row: that is what leaves outer event healing
  // reachable after the world or per-call identity has moved.
  const def = (await a.query(
    `select pg_get_functiondef(p.oid) as def
       from pg_proc p join pg_namespace n on n.oid = p.pronamespace
      where n.nspname = 'ops' and p.proname = 'gate_zero_record_read_only_outcome'`)).rows[0].def;
  assert.match(def, /RETURN THE IMMUTABLE FIRST ROW UNCONDITIONALLY/);
  assert.doesNotMatch(def, /raise exception 'candidate % already has a different receipt/);

  // ===================================================================
  // AND A DIFFERENT OUTCOME CONVERGES, REPLACING NOTHING.
  // ===================================================================
  // The caller receives the earlier row, while the stored receipt and both
  // digests remain byte-for-byte those of the first run.
  const before = (await a.query(
    `select outcome_digest, candidate_scoped_digest, status, receipt
       from ops.gate_zero_read_only_outcome where id = $1::uuid`,
    [firstResult.outcome_id])).rows[0];
  await a.seat.query("begin");
  const divergent = { ...sentA, status: "fail" };
  assert.equal(divergent.candidate_digest, sentA.candidate_digest);
  const convergedId = (await a.seat.query(
    "select ops.gate_zero_record_read_only_outcome($1::uuid, $2::jsonb) as id",
    [randomUUID(), JSON.stringify(divergent)])).rows[0].id;
  assert.equal(convergedId, firstResult.outcome_id,
    "a different verdict did not converge onto the recorded row");
  await a.seat.query("commit");

  const after = (await a.query(
    `select count(*)::int as n from ops.gate_zero_read_only_outcome where candidate_digest = $1`,
    [firstResult.candidate_digest])).rows[0];
  assert.equal(after.n, 1, "the converging write minted a second outcome for one candidate");
  const unchanged = (await a.query(
    `select outcome_digest, candidate_scoped_digest, status, receipt
       from ops.gate_zero_read_only_outcome where id = $1::uuid`,
    [firstResult.outcome_id])).rows[0];
  assert.deepEqual(unchanged, before,
    "the divergent receipt moved the recorded row, which the append-only record forbids");
  assert.equal(unchanged.status, "pass");
});

test("a normal changed-evidence retry heals a missing event exactly once",
  async t => {
    // FINDING 3 OF THE THIRD RELEASE CANDIDATE'S REFUSAL, RUN RATHER THAN
    // ARGUED. Standing-rule amendment 9 puts the outcome write on the seat's own
    // login role and the audit event on the ordinary writer's, so they cannot
    // share a transaction and the seat's commits first. The reviewer asked what
    // happens when the outer one then fails, and answered it: an outcome with no
    // event, reachable, and -- before migration 0505 -- unrepairable.
    //
    // THE INTERLEAVING IS STAGED, NOT HOPED FOR. The seat's transaction is
    // committed by hand, exactly where gateZeroSeatConnection commits it, and the
    // outer transaction is then rolled back: the durable state is precisely what
    // a crash between the two leaves behind.
    if (!DSN) {
      assert.equal(REQUIRED, false,
        "this proof was required and no database URL was given to it");
      return t.skip("no DATABASE_URL / CARR_GATE_ZERO_RACE_DSN (the migration class provides one)");
    }
    assert.ok(LOOPBACK.test(DSN),
      "REFUSED: this proof commits rows to an append-only record and runs against a throwaway only");
    const pg = await import("pg");
    const PG = pg.default ?? pg;

    let setup;
    let caller;
    try {
      setup = new PG.Client({ connectionString: DSN });
      await setup.connect();
      const roles = await ensureProducerRoles(setup);
      caller = await seatedClient(PG, roles.login);
    } catch (error) {
      if (setup) await setup.end().catch(() => {});
      if (caller) {
        await caller.client.end().catch(() => {});
        await caller.seat.end().catch(() => {});
      }
      assert.equal(REQUIRED, false,
        `this proof was required and Postgres was unreachable: ${error.message}`);
      return t.skip(`no reachable Postgres: ${error.message}`);
    }
    t.after(async () => {
      await setup.end().catch(() => {});
      await caller.client.end().catch(() => {});
      await caller.seat.end().catch(() => {});
    });

    const seatRow = (await setup.query(
      "select id from public.actor where slug = $1", [SEAT_SLUG])).rows[0];
    assert.ok(seatRow, `this database has no ${SEAT_SLUG} actor to act as`);

    // A CANDIDATE OF ITS OWN, so this proof's row is not the one the race above
    // already recorded -- the record is append-only and both tests share one
    // database. The world is left exactly as it is: what moves is one comment
    // inside the sealed bytes, which moves the candidate manifest digest and
    // therefore the candidate, and nothing the producer reads.
    const { target, stamps } = stageTree({
      candidateEdit: "the outer-failure heal proof's own candidate",
      mutablePredecessorWorld: true,
    });
    globalThis[DYNAMIC_PREDECESSOR_WORLD] = "clean";
    t.after(() => { delete globalThis[DYNAMIC_PREDECESSOR_WORLD]; });
    const tools = await moduleOfTree(target, "tools.js");
    const callVerb = correlationId => withStamps(stamps,
      () => inServedReview(target, { correlationId },
        actor => tools.executeRegisteredTool(caller,
          Object.assign(actor, { id: seatRow.id }), VERB, { idempotency_key: randomUUID() })));

    const eventsFor = async outcomeId => (await setup.query(
      `select count(*)::int as n from event
        where verb = 'record-gate-zero-read-only-outcome'
          and subject_type = 'gate_zero_outcome' and subject_id = $1::uuid`,
      [outcomeId])).rows[0].n;
    const outcomesFor = async candidateDigest => (await setup.query(
      "select count(*)::int as n from ops.gate_zero_read_only_outcome where candidate_digest = $1",
      [candidateDigest])).rows[0].n;

    // (1) THE CALL SUCCEEDS, AND THEN THE OUTER TRANSACTION FAILS. The seat's
    //     work is committed where production commits it; everything the ordinary
    //     writer did -- the audit event and the idempotency envelope's tool_call
    //     row -- goes away with the rollback.
    const FIRST_CALL = "1f7e4c20-6a35-4b8d-9c42-0e1a7b5d38f6";
    const served = await callVerb(FIRST_CALL);
    assert.equal(served.served, true, "the recorded review bearer was not served");
    const first = served.answered;
    assert.equal(first.ok, true);
    assert.equal(first.receipt_status, "pass");
    await caller.seat.query("commit");
    await caller.client.query("rollback");

    // (2) THE STATE THE REVIEWER NAMED, MEASURED. One outcome, no event.
    assert.equal(await outcomesFor(first.candidate_digest), 1);
    assert.equal(await eventsFor(first.outcome_id), 0,
      "the outer rollback did not remove the audit event, so this proof is not staging the failure it claims");

    // (3) A NORMAL RETRY AFTER THE WORLD MOVES. It has a new correlation id,
    //     new time bytes, changed evidence and a changed verdict. Those offered
    //     bytes must remain visible, while the immutable recorded row wins.
    globalThis[DYNAMIC_PREDECESSOR_WORLD] = "receipt-card-mismatch";
    await caller.client.query("begin");
    await caller.client.query(
      "select set_config('carr.acting_actor_slug', $1, true)", [SEAT_SLUG]);
    const healed = (await callVerb("2a8d5b31-7c46-4d9e-8f53-1b2c8e6a49b7")).answered;
    assert.equal(healed.ok, true);
    assert.equal(healed.outcome_id, first.outcome_id,
      "the changed-evidence retry did not converge on the eventless row");
    assert.equal(healed.converged_onto_recorded_outcome, true);
    assert.notEqual(healed.offered_outcome_digest, healed.outcome_digest,
      "the retry accidentally reproduced the stored receipt bytes");
    assert.notEqual(healed.offered_candidate_scoped_digest, healed.candidate_scoped_digest,
      "the retry did not actually move the evidence projection");
    assert.equal(healed.receipt_status, "pass");
    assert.equal(healed.offered_receipt_status, "fail");
    assert.equal(healed.producer_reason_id, null);
    assert.ok(healed.offered_producer_reason_id);
    await caller.seat.query("commit");
    await caller.client.query("commit");
    assert.equal(await eventsFor(first.outcome_id), 1,
      "the changed-evidence retry did not heal the missing audit event");

    // (4) A THIRD NORMAL CALL CONVERGES BUT WRITES NO SECOND EVENT.
    await caller.client.query("begin");
    await caller.client.query(
      "select set_config('carr.acting_actor_slug', $1, true)", [SEAT_SLUG]);
    const thrice = (await callVerb("3b9e6c42-8d57-4e0f-9a64-2c3d9f7b5ac8")).answered;
    assert.equal(thrice.outcome_id, first.outcome_id);
    assert.equal(thrice.converged_onto_recorded_outcome, true);
    await caller.seat.query("commit");
    await caller.client.query("commit");

    // (5) THE FIRST ROW AND ITS ONE HEALED EVENT STILL STAND.
    assert.equal(await outcomesFor(first.candidate_digest), 1,
      "the retry minted a second outcome for one candidate");
    assert.equal(await eventsFor(first.outcome_id), 1,
      "a third retry wrote a duplicate audit event");
  });
