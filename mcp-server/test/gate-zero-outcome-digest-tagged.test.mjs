// THE TAGGED OUTCOME DIGEST, ASKED OF BOTH IMPLEMENTATIONS AT ONCE
// (2026-09-13, the third release candidate's refusal, finding 2).
//
// WHAT THE OUTSIDE REVIEW REFUSED. Accepted plan PLAN-a5059eb52474-v3 requires
// the recorded Gate Zero outcome digest to be
// `digest(["consumer-gate-receipt.v1", receipt])` — the canonical JSON of that
// exact two-element array — computed by ops.portfolio_canonical_json in SQL and
// canonicalJson in the gateway, BYTE FOR BYTE THE SAME PREIMAGE IN BOTH, and it
// names a plain `digest(receipt)` by either reader as a failure condition. The
// r7 amendment of 2026-09-13 (packet
// 4379c60e9a4fefbcf044f4bc5a34e5a95c90f77b9adf348b6da7475e17d5e6d7) declares
// that preimage in `receipt_payload_digest_rule` and says in its own words that
// a digest over the receipt alone does not satisfy it. The refused candidate
// computed the plain digest in all three places.
//
// WHY THIS FILE NEEDS A DATABASE. "The two implementations agree" is a claim
// about two canonicalizers, one written in plpgsql and one in JavaScript. A mock
// cannot hold it: a stand-in that called the JS function would agree with the JS
// function by construction. So the SQL side here is the function migration 0505
// actually installed, read out of the live catalog, and the JS side is the
// shipped module.
//
// FIVE THINGS IT ESTABLISHES, each executed rather than described:
//
//   1. AGREEMENT, over a receipt the REAL producer emitted inside a real
//      dispatched call, and again over a canonicalization-stress fixture whose
//      only job is to exercise the corners the two renderers could differ on —
//      key order, nesting, arrays, unicode, numbers, empty containers.
//   2. THE TAG IS LOAD-BEARING. The plain digest — the recipe 0502 shipped and
//      r7 now refuses — is computed on BOTH sides and asserted to differ from
//      the accepted value. A change that dropped the tag would make these equal.
//   3. THE TAG'S VALUE IS LOAD-BEARING. The live SQL function is lifted from the
//      catalog, its tag literal is moved by one character into a pg_temp copy,
//      and the result is asserted to differ from both the accepted value and the
//      JS value. A tag that drifted on one side only is a divergence, not a
//      quiet agreement.
//   4. AND THE UNTAGGED DIGEST IS NOT ACCEPTED BY THE VERB. The seat's readback
//      is intercepted and the row's `outcome_digest` replaced with the plain
//      one; the verb must refuse by name with
//      `gate_zero_outcome_digest_divergence` rather than report it.
//   5. BOTH CONSUMER READERS RECOMPUTE. The SQL reader returns the intact row,
//      then refuses after the installed digest function is transactionally
//      mutated; the gateway reader has its own direct unit falsifier.
//
// IT SKIPS WITHOUT A DATABASE and REFUSES a database that is not loopback: case
// 4 commits to an append-only record, which is safe only on the throwaway
// database the migration class builds.
//
//   DATABASE_URL=postgresql://localhost/... node --test \
//     mcp-server/test/gate-zero-outcome-digest-tagged.test.mjs

import test, { after } from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";

import { digest } from "../src/artifact-trust.js";
import { GATE_ZERO_RECEIPT_SCHEMA, gateZeroOutcomeDigest }
  from "../src/gate-zero-outcome-store.v5.js";
import { cleanupStagedTrees, inServedReview, moduleOfTree, stageTree, withStamps }
  from "./gate-zero-candidate-tree.testhelper.mjs";
import { authorizeAs, ensureProducerRoles } from "./gate-zero-producer-role.testhelper.mjs";

const DSN = process.env.CARR_GATE_ZERO_RACE_DSN || process.env.DATABASE_URL || "";
// A SKIP THAT NOBODY NOTICES IS A TEST COLLECTED BY NOBODY. The migration class
// sets this, so where this proof is meant to run "no database" FAILS.
const REQUIRED = process.env.CARR_GATE_ZERO_RACE_REQUIRED === "1";
const LOOPBACK = /@(localhost|127\.0\.0\.1)[:/]|^postgres(ql)?:\/\/(localhost|\/)/;

const VERB = "record-gate-zero-read-only-outcome";
const SEAT_SLUG = "codex-reviewer";

/**
 * THE CANONICALIZATION STRESS FIXTURE. Not a receipt and not pretending to be
 * one: its whole job is to make the two renderers disagree if they are going to.
 * Keys out of order and out of ASCII order, a nested object, an array whose
 * ORDER must survive, an empty object and an empty array, null, both booleans,
 * a negative integer, a string carrying a quote, a backslash and a non-BMP
 * character, and a key that is the empty string.
 */
const STRESS = Object.freeze({
  "zz": 1, "aa": { "b": [1, 2, 3], "a": null },
  "": "empty key", "Ω": "non-ascii key", "m": [],
  "n": {}, "o": false, "p": true, "q": -17,
  "r": "a \"quoted\" \\ backslash and an emoji 🜂",
  "s": [{ "k": "v" }, [], "x"],
});

after(cleanupStagedTrees);

/** The plain, untagged recipe 0502 shipped and r7 now refuses. */
const plainDigest = value => digest(value);

async function connectOrSkip(t) {
  if (!DSN) {
    assert.equal(REQUIRED, false,
      "this proof was required and no database URL was given to it");
    t.skip("no DATABASE_URL / CARR_GATE_ZERO_RACE_DSN (the migration class provides one)");
    return null;
  }
  assert.ok(LOOPBACK.test(DSN),
    "REFUSED: this proof commits rows to an append-only record and runs against a throwaway only");
  const pg = await import("pg");
  const PG = pg.default ?? pg;
  const client = new PG.Client({ connectionString: DSN });
  try {
    await client.connect();
  } catch (error) {
    await client.end().catch(() => {});
    assert.equal(REQUIRED, false,
      `this proof was required and Postgres was unreachable: ${error.message}`);
    t.skip(`no reachable Postgres: ${error.message}`);
    return null;
  }
  t.after(() => client.end().catch(() => {}));
  return { PG, client };
}

test("SQL and the gateway compute the same TAGGED outcome digest, and the plain one is refused",
  async t => {
    const opened = await connectOrSkip(t);
    if (!opened) return;
    const { PG, client } = opened;

    // ── (1) AGREEMENT, over the stress fixture ──────────────────────────────
    // Asked of the function the database is actually carrying, not of a copy of
    // its text: if 0505 did not apply, or applied and was replaced, this is the
    // line that says so.
    const sqlOf = async value => (await client.query(
      "select ops.gate_zero_outcome_digest($1::jsonb) as d", [JSON.stringify(value)])).rows[0].d;

    const stressSql = await sqlOf(STRESS);
    assert.match(stressSql, /^sha256:[0-9a-f]{64}$/);
    assert.equal(stressSql, gateZeroOutcomeDigest(STRESS),
      "the record layer and artifact-trust.js disagree about the tagged preimage");
    assert.equal(stressSql, digest([GATE_ZERO_RECEIPT_SCHEMA, STRESS]),
      "the gateway's tagged digest is not digest([schema, value])");

    // ── (2) THE TAG IS LOAD-BEARING, on both sides ──────────────────────────
    // The plain digest is what 0502 recorded and what r7's amended rule states
    // does not satisfy it. Computed here on BOTH implementations so that
    // dropping the tag from either one turns this red rather than turning the
    // agreement above into a coincidence.
    const stressPlainSql = (await client.query(
      `select 'sha256:' || encode(public.digest(convert_to(
         ops.portfolio_canonical_json($1::jsonb), 'UTF8'), 'sha256'), 'hex') as d`,
      [JSON.stringify(STRESS)])).rows[0].d;
    assert.equal(stressPlainSql, plainDigest(STRESS),
      "the two canonicalizers do not even agree about the UNTAGGED preimage, so "
      + "the tagged agreement above proves less than it looks like it does");
    assert.notEqual(stressSql, stressPlainSql,
      "the recorded digest still equals the plain digest r7 refuses");

    // ── (3) THE TAG'S VALUE IS LOAD-BEARING ─────────────────────────────────
    // MUTATION CONTROL, EXECUTED. The writer's own definition is read out of the
    // catalog rather than retyped, one character of the domain tag is moved, and
    // the mutant is asserted to disagree with both implementations. Anchored the
    // way the race proof anchors its mutant: if the literal is no longer there
    // exactly once, this fails on the anchor instead of silently mutating
    // nothing.
    const def = (await client.query(
      `select pg_get_functiondef(p.oid) as def
         from pg_proc p join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'ops' and p.proname = 'gate_zero_outcome_digest'`)).rows[0].def;
    const TAG = `'${GATE_ZERO_RECEIPT_SCHEMA}'::text`;
    assert.equal(def.split(TAG).length - 1, 1,
      "the mutation anchor no longer matches the domain tag in the installed function");
    assert.equal(def.split("ops.gate_zero_outcome_digest(").length - 1, 1,
      "the mutation anchor no longer matches the function's own name");
    const mutant = def
      .replace("ops.gate_zero_outcome_digest(", "pg_temp.gate_zero_outcome_digest_moved_tag(")
      .replace(TAG, `'${GATE_ZERO_RECEIPT_SCHEMA.replace(/1$/, "0")}'::text`);
    await client.query(mutant);
    const moved = (await client.query(
      "select pg_temp.gate_zero_outcome_digest_moved_tag($1::jsonb) as d",
      [JSON.stringify(STRESS)])).rows[0].d;
    assert.notEqual(moved, stressSql, "moving the domain tag did not move the digest");
    assert.notEqual(moved, gateZeroOutcomeDigest(STRESS),
      "a moved tag on one side still agreed with the gateway, so the tag is not in the preimage");

    // ── (1b) AGREEMENT, over a receipt the REAL producer emitted ────────────
    // The stress fixture proves the canonicalizers; this proves the SHAPE that
    // actually gets recorded. No receipt is built here: it is produced inside a
    // real dispatched call over a staged candidate tree, then read back off the
    // durable row so the bytes compared are the bytes PERSISTED.
    let roles;
    try {
      roles = await ensureProducerRoles(client);
    } catch (error) {
      assert.equal(REQUIRED, false,
        `this proof was required and the producer roles were unreachable: ${error.message}`);
      return t.skip(`no producer roles: ${error.message}`);
    }
    const seatRow = (await client.query(
      "select id, active from public.actor where slug = $1", [SEAT_SLUG])).rows[0];
    assert.ok(seatRow, `this database has no ${SEAT_SLUG} actor to act as`);

    // THE SEAT'S DOOR, SHAPED LIKE PRODUCTION'S: its own connection, its own
    // transaction, committed before the outer one writes the event. The one
    // thing this harness adds is the interception case 4 needs.
    let corruptReadback = false;
    let corruptedReceipt = null;
    const outer = new PG.Client({ connectionString: DSN });
    await outer.connect();
    t.after(() => outer.end().catch(() => {}));
    await outer.query("begin");
    await outer.query("select set_config('carr.acting_actor_slug', $1, true)", [SEAT_SLUG]);
    const seat = new PG.Client({ connectionString: DSN });
    await seat.connect();
    await authorizeAs(seat, roles.login);
    t.after(() => seat.end().catch(() => {}));

    const recorder = {
      query: (sql, params = []) => outer.query(sql, params),
      seatConnection: async run => {
        await seat.query("begin");
        try {
          const answer = await run({
            query: async (sql, params = []) => {
              const result = await seat.query(sql, params);
              // CASE 4'S INTERCEPTION, and it is the ONLY thing in this file
              // that is not what production does. It stands in for a record
              // layer whose canonicalization diverged from the gateway's --
              // which is unreachable by construction while both are correct, and
              // is exactly the condition the verb's comparison exists to catch.
              if (corruptReadback && typeof sql === "string"
                  && sql.includes("from ops.gate_zero_read_only_outcome")) {
                corruptedReceipt = result.rows[0].receipt;
                return { ...result, rows: [{ ...result.rows[0],
                  outcome_digest: plainDigest(result.rows[0].receipt) }] };
              }
              return result;
            },
          });
          await seat.query("commit");
          return answer;
        } catch (error) {
          await seat.query("rollback").catch(() => {});
          throw error;
        }
      },
    };

    // A CANDIDATE OF ITS OWN. The outcome record is append-only and every proof
    // in the migration class shares one database, so this tree carries a comment
    // no other tree does: the candidate digest moves, the world does not, and
    // this file's row is its own rather than a convergence onto somebody else's.
    const { target, stamps } = stageTree({
      candidateEdit: "the tagged-digest proof's own candidate" });
    const tools = await moduleOfTree(target, "tools.js");
    const callVerb = correlationId => withStamps(stamps,
      () => inServedReview(target, { correlationId },
        actor => tools.executeRegisteredTool(recorder,
          Object.assign(actor, { id: seatRow.id }), VERB, { idempotency_key: randomUUID() })));

    const served = await callVerb("6b2f0d47-8c51-4a39-9e70-1d5a2c83bf64");
    assert.equal(served.served, true, "the recorded review bearer was not served");
    const result = served.answered;
    assert.equal(result.ok, true);

    // THE STORED RECEIPT, read back off the row, digested by both sides.
    const stored = (await outer.query(
      "select receipt, outcome_digest from ops.gate_zero_read_only_outcome where id = $1::uuid",
      [result.outcome_id])).rows[0];
    assert.equal(stored.outcome_digest, result.outcome_digest);
    assert.equal(stored.outcome_digest, gateZeroOutcomeDigest(stored.receipt),
      "the recorded digest of a real receipt is not the gateway's tagged digest of it");
    assert.equal(stored.outcome_digest, digest([GATE_ZERO_RECEIPT_SCHEMA, stored.receipt]));
    assert.notEqual(stored.outcome_digest, plainDigest(stored.receipt),
      "a real receipt's recorded digest is still the plain one");
    const currentRow = (await outer.query(
      `select receipt, outcome_digest
         from ops.gate_zero_read_only_outcome
        where status = 'pass' and ttl_expires_at > now()
        order by observed_at desc, outcome_digest collate "C" desc
        limit 1`)).rows[0];
    const intactRead = (await outer.query(
      "select ops.benchmark_gate_zero_outcome() as outcome")).rows[0].outcome;
    assert.equal(intactRead.outcome_digest, currentRow.outcome_digest,
      "the SQL consumer did not return the digest of the row its currentness rule selected");
    assert.equal(currentRow.outcome_digest, gateZeroOutcomeDigest(currentRow.receipt),
      "the SQL consumer's selected row is not internally consistent before the mutation control");
    // AND THE VERB SAYS WHICH RECIPE IT USED, in the words a consumer reads.
    assert.match(result.digest_recipe, /TAGGED two-element array/);
    assert.match(result.digest_recipe, /does not satisfy it/);

    // ── (4) THE UNTAGGED DIGEST IS NOT ACCEPTED ─────────────────────────────
    // A FIRST call for a separate candidate, with the readback's digest replaced
    // by the plain one. Its own candidate makes this corruption control test a
    // newly inserted row rather than converge onto the intact row above.
    corruptReadback = true;
    const corruptTree = stageTree({
      candidateEdit: "the tagged-digest corruption control's own candidate" });
    const corruptTools = await moduleOfTree(corruptTree.target, "tools.js");
    const callCorrupt = correlationId => withStamps(corruptTree.stamps,
      () => inServedReview(corruptTree.target, { correlationId },
        actor => corruptTools.executeRegisteredTool(recorder,
          Object.assign(actor, { id: seatRow.id }), VERB, { idempotency_key: randomUUID() })));
    let refusal = null;
    try {
      await callCorrupt("7c3a1e58-9d62-4b4a-af81-2e6b3d94ca75");
    } catch (error) {
      refusal = error?.payload ?? error;
    }
    assert.ok(refusal, "the verb accepted a row carrying the untagged digest");
    assert.equal(refusal.error, "gate_zero_outcome_digest_divergence");
    assert.ok(corruptedReceipt, "the corruption control never reached a stored receipt readback");
    assert.equal(refusal.recomputed_here, gateZeroOutcomeDigest(corruptedReceipt));
    assert.equal(refusal.recorded, plainDigest(corruptedReceipt));

    // THE SQL CONSUMER'S OWN FALSIFIER. Replacing the installed helper inside
    // this uncommitted outer transaction changes only what the reader
    // recomputes; the append-only row keeps the exact digest it was written
    // with. A reader that merely returns the stored value would stay green.
    await outer.query(def.replace(
      TAG, `'${GATE_ZERO_RECEIPT_SCHEMA.replace(/1$/, "0")}'::text`));
    await assert.rejects(
      outer.query("select ops.benchmark_gate_zero_outcome()"),
      /Gate Zero outcome digest divergence/);

    await outer.query("rollback");
  });
