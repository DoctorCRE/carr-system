// THE SEAT BOUNDARY IS THE DATABASE'S, AND THIS ASKS THE DATABASE
// (2026-09-14, PR 1014 third correction, standing-rule amendment 9).
//
// WHAT SOL'S FINDING 2 WAS. ops.gate_zero_producer_actor_id() used to decide
// authority from current_setting('carr.acting_actor_slug'), and EXECUTE on the
// SECURITY DEFINER writer was granted to carr_writer -- the role every ordinary
// verb already runs on. PostgreSQL lets any session define and set a two-part
// custom parameter on itself, so a direct carr_writer connection could name the
// staffed lane and be believed. The record layer was not independently enforcing
// seat-only write; it was repeating what the caller told it.
//
// WHAT REPLACED IT. Amendment 9, logged 2026-09-14: seat-only write is enforced
// by CONNECTION ROLE. The writer's EXECUTE now belongs to one capability bundle
// reachable by one login role, and the producing seat is derived from
// session_user -- the role the connection authenticated as, which no session can
// change for itself. The GUC survives and is still what every other verb derives
// its actor from; on this path it is informational and unread.
//
// THE FOUR THINGS THIS FILE ESTABLISHES, each against a real connection:
//
//   1. THE PRIVILEGE HALF. A carr_writer session that sets the GUC to the
//      staffed slug is refused BY THE DATABASE, with permission denied, before
//      a line of the writer's body runs.
//   2. THE MUTATION CONTROL, EXECUTED RATHER THAN DESCRIBED. Granting EXECUTE
//      back to carr_writer is applied, the proof in (1) is re-run against the
//      mutated database, and it is asserted to FAIL. Then the grant is put back.
//      "Re-granting carr_writer turns this red" is a statement this file makes
//      by running it.
//   3. THE DERIVATION HALF, WHICH DOES NOT DEPEND ON (1). With EXECUTE granted
//      back to carr_writer -- the mutated state -- a carr_writer session naming
//      the staffed lane in the GUC is STILL refused, now by session_user. So a
//      slip in the grant alone opens nothing, which is what defence in depth has
//      to mean to be worth writing down.
//   4. THE DEDICATED ROLE WRITES, and the GUC decides nothing. The producer's
//      own connection records a real outcome through the REGISTERED VERB with a
//      receipt produced inside the call, while the GUC is set to a deliberately
//      wrong slug. The row lands and names the staffed seat.
//
// IT SKIPS WITHOUT A DATABASE and REFUSES a database that is not loopback. Case
// (4) commits to an append-only record, which is safe only on the throwaway
// database the migration class builds -- the same posture as the race proof.
//
//   DATABASE_URL=postgresql://localhost/... node --test \
//     mcp-server/test/gate-zero-outcome-role-boundary.test.mjs

import test, { after } from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";

import { cleanupStagedTrees, inServedReview, moduleOfTree, stageTree, withStamps }
  from "./gate-zero-candidate-tree.testhelper.mjs";
import { authorizeAs, ensureProducerRoles, resetAuthorization }
  from "./gate-zero-producer-role.testhelper.mjs";

const DSN = process.env.CARR_GATE_ZERO_RACE_DSN || process.env.DATABASE_URL || "";
const REQUIRED = process.env.CARR_GATE_ZERO_RACE_REQUIRED === "1";
const LOOPBACK = /@(localhost|127\.0\.0\.1)[:/]|^postgres(ql)?:\/\/(localhost|\/)/;
const VERB = "record-gate-zero-read-only-outcome";
const WRITER_FUNCTION = "ops.gate_zero_record_read_only_outcome(uuid,jsonb)";

after(cleanupStagedTrees);

async function connect(pg) {
  const client = new pg.Client({ connectionString: DSN });
  await client.connect();
  return client;
}

/**
 * THE PROOF OF (1), AS A FUNCTION, so the mutation control can run the same one.
 * A carr_writer connection, the GUC set to the staffed lane, calling the writer
 * directly: the database answers permission denied (SQLSTATE 42501) and nothing
 * inside the writer runs.
 */
async function assertCarrWriterIsRefusedByPrivilege(pg, roles) {
  const probe = await connect(pg);
  try {
    await authorizeAs(probe, roles.probeRole);
    await probe.query("begin");
    await probe.query("select set_config('carr.acting_actor_slug', $1, true)", [roles.lane]);
    let error = null;
    try {
      await probe.query("select ops.gate_zero_record_read_only_outcome($1::uuid, $2::jsonb)",
        [randomUUID(), JSON.stringify({})]);
    } catch (raised) { error = raised; }
    assert.ok(error, "a carr_writer session reached the Gate Zero writer at all");
    assert.equal(error.code, "42501",
      `a carr_writer session was refused for some reason other than privilege: ${error.message}`);
    assert.match(error.message, /permission denied for function/);
    await probe.query("rollback").catch(() => {});
  } finally {
    await probe.end().catch(() => {});
  }
}

test("the record layer enforces seat-only write by connection role, not by a session setting",
  async t => {
    if (!DSN) {
      assert.equal(REQUIRED, false,
        "this proof was required and no database URL was given to it");
      return t.skip("no DATABASE_URL / CARR_GATE_ZERO_RACE_DSN (the migration class provides one)");
    }
    assert.ok(LOOPBACK.test(DSN),
      "REFUSED: this proof commits rows to an append-only record and runs against a throwaway only");
    const pg = (await import("pg")).default ?? (await import("pg"));

    let owner;
    try {
      owner = await connect(pg);
    } catch (error) {
      assert.equal(REQUIRED, false,
        `this proof was required and Postgres was unreachable: ${error.message}`);
      return t.skip(`no reachable Postgres: ${error.message}`);
    }
    t.after(async () => { await owner.end().catch(() => {}); });

    // The role names come from the record layer's own literals, and the login
    // role this database admits is created here because 0502 deliberately does
    // not mint one.
    const roles = await ensureProducerRoles(owner);
    assert.match(roles.seat, /^seat:[a-z0-9][a-z0-9.-]*:[a-z0-9][a-z0-9.-]*$/);
    assert.notEqual(roles.login, "carr_writer");
    assert.notEqual(roles.login, roles.probeRole);
    // AND IT IS A LOGIN ROLE OUTSIDE THE SEALED role_authority CLOSURE, which
    // is the property that lets a cluster carry it without invalidating the
    // snapshot seal for every other database in that cluster.
    const shape = (await owner.query(
      "select rolcanlogin, rolsuper, rolcreaterole, rolbypassrls from pg_roles where rolname=$1",
      [roles.login])).rows[0];
    assert.deepEqual(shape, { rolcanlogin: true, rolsuper: false,
      rolcreaterole: false, rolbypassrls: false });

    // THE GRANT IS WHAT THE MIGRATION SAYS IT IS, asked of the catalog rather
    // than read off the file: the bundle holds EXECUTE and carr_writer does not.
    const acl = (await owner.query(
      `select has_function_privilege('carr_writer', '${WRITER_FUNCTION}', 'execute') as writer,
              has_function_privilege('carr_authority', '${WRITER_FUNCTION}', 'execute') as authority,
              has_function_privilege('carr_jobs', '${WRITER_FUNCTION}', 'execute') as jobs,
              has_function_privilege('carr_reader', '${WRITER_FUNCTION}', 'execute') as reader,
              has_function_privilege($1, '${WRITER_FUNCTION}', 'execute') as login`,
      [roles.login])).rows[0];
    assert.equal(acl.login, true, "the dedicated login role does not hold the writer EXECUTE");
    assert.equal(acl.reader, false, "carr_reader holds EXECUTE on the Gate Zero writer");
    assert.equal(acl.writer, false, "carr_writer still holds EXECUTE on the Gate Zero writer");
    assert.equal(acl.authority, false, "carr_authority holds EXECUTE on the Gate Zero writer");
    assert.equal(acl.jobs, false, "carr_jobs holds EXECUTE on the Gate Zero writer");

    // (1) THE PRIVILEGE HALF.
    await assertCarrWriterIsRefusedByPrivilege(pg, roles);

    // (2) THE MUTATION CONTROL, applied to the live database and then undone.
    await owner.query(`grant execute on function ${WRITER_FUNCTION} to carr_writer`);
    let controlFailed = false;
    try {
      await assertCarrWriterIsRefusedByPrivilege(pg, roles);
    } catch (raised) {
      controlFailed = true;
      assert.match(String(raised.message), /refused for some reason other than privilege|reached the Gate Zero writer/);
    }

    // (3) THE DERIVATION HALF, measured while the mutation is still applied: the
    //     GUC names the staffed lane, the privilege is there, and session_user
    //     refuses anyway.
    const mutated = await connect(pg);
    let derivationRefusal = null;
    try {
      await authorizeAs(mutated, roles.probeRole);
      await mutated.query("begin");
      await mutated.query("select set_config('carr.acting_actor_slug', $1, true)", [roles.lane]);
      try {
        await mutated.query("select ops.gate_zero_record_read_only_outcome($1::uuid, $2::jsonb)",
          [randomUUID(), JSON.stringify({})]);
      } catch (raised) { derivationRefusal = raised; }
      await mutated.query("rollback").catch(() => {});
    } finally {
      await mutated.end().catch(() => {});
      await owner.query(`revoke execute on function ${WRITER_FUNCTION} from carr_writer`);
    }

    assert.equal(controlFailed, true,
      "granting EXECUTE back to carr_writer did not turn the privilege proof red, so it proves nothing");
    assert.ok(derivationRefusal,
      "with EXECUTE granted back, a carr_writer session naming the staffed lane in the GUC was ACCEPTED");
    assert.notEqual(derivationRefusal.code, "42501");
    assert.match(derivationRefusal.message, /requires the dedicated producer connection/);
    assert.match(derivationRefusal.message, new RegExp(roles.probeRole),
      "the refusal did not name the role the connection actually authenticated as");

    // AND THE MUTATION IS OFF AGAIN. Asked, not assumed: a control that leaves
    // the database mutated would make every later case in this run a lie.
    assert.equal((await owner.query(
      `select has_function_privilege('carr_writer', '${WRITER_FUNCTION}', 'execute') as writer`))
      .rows[0].writer, false, "the mutation control did not restore the revoke");

    // (4) THE DEDICATED ROLE WRITES, THROUGH THE REGISTERED VERB.
    //
    // Nothing is handed in: the receipt is produced inside the call by the real
    // producer over a staged candidate tree, exactly as the race proof does it.
    // `c` stays the ordinary writer connection -- it carries the audit event --
    // and the outcome itself travels on the seat's own connection, which is the
    // whole shape this correction added.
    const seatRow = (await owner.query(
      "select id, kind, active from public.actor where slug = $1", [roles.lane])).rows[0];
    assert.ok(seatRow, `this database has no ${roles.lane} actor to act as`);

    const seat = await connect(pg);
    t.after(async () => { await seat.end().catch(() => {}); });
    await authorizeAs(seat, roles.login);

    // ITS OWN CANDIDATE, DELIBERATELY. Both DB proofs in the migration class
    // commit to one append-only record, and a default-staged tree is
    // deterministic -- so sharing one would make this proof's write land on the
    // race proof's row (or the race proof's first call land on this one's,
    // which is how the race stopped racing the first time these ran together).
    // One appended comment in the staged src is one byte the candidate tree did
    // not have, and nothing else about the run moves.
    const { target, stamps } =
      stageTree({ candidateEdit: "the seat/connection-role boundary proof" });
    const tools = await moduleOfTree(target, "tools.js");
    // THE CALL IS SERVED, NOT ASSEMBLED. `inServedReview` hands the staged
    // review door the recorded Authorization header and this request's
    // correlation id; the door authenticates it, derives the receipt identity
    // and enters the call, and the continuation runs inside it with that call's
    // own actor. Nothing here chooses an identity, because since PR 1013's fifth
    // correction identity.js exports nothing that could.
    const CALL = "4b7c1e05-9a62-4d38-8f10-3c5e7d2b6a94";
    const dispatch = client => withStamps(stamps,
      () => inServedReview(target, { correlationId: CALL },
        actor => tools.executeRegisteredTool(client, Object.assign(actor, { id: seatRow.id }),
          VERB, { idempotency_key: randomUUID() })));

    // THE GUC IS SET TO SOMETHING WRONG ON PURPOSE. If it still decided anything,
    // this call would be refused; it is not, and the row names the staffed seat.
    await owner.query("begin");
    await owner.query("select set_config('carr.acting_actor_slug', $1, true)",
      ["definitely-not-the-seat"]);
    const client = {
      query: (sql, params = []) => owner.query(sql, params),
      // The production door is gateZeroSeatConnection(env, Pool); here the
      // connection is the test's, already authenticated as the login role.
      seatConnection: async run => {
        await seat.query("begin");
        try {
          const result = await run(seat);
          await seat.query("commit");
          return result;
        } catch (error) {
          await seat.query("rollback").catch(() => {});
          throw error;
        }
      },
    };
    const served = await dispatch(client);
    assert.equal(served.served, true, "the recorded review bearer was not served");
    const result = served.answered;
    await owner.query("commit");

    assert.equal(result.ok, true);
    assert.equal(result.producing_seat_ref, roles.seat,
      "the recorded seat is not the staffed one the record layer names");
    assert.match(result.outcome_id, /^[0-9a-f-]{36}$/);

    const stored = (await owner.query(
      `select producing_seat_ref, producing_actor_id
         from ops.gate_zero_read_only_outcome where id = $1::uuid`, [result.outcome_id])).rows[0];
    assert.equal(stored.producing_seat_ref, roles.seat);
    assert.equal(stored.producing_actor_id, seatRow.id,
      "the row was attributed to an actor the record layer did not derive from the staffed lane");

    // AND THE VERB REFUSES RATHER THAN FALLING BACK when no seat connection
    // exists. A deployment missing the secret must say so, not quietly record an
    // oracle's signature over the ordinary writer connection.
    await assert.rejects(
      () => dispatch({ query: (sql, params = []) => owner.query(sql, params) }),
      error => {
        assert.equal(error.payload?.error, "gate_zero_seat_connection_unavailable");
        assert.equal(error.payload?.required_secret, "DATABASE_URL_GATE_ZERO_WRITER");
        return true;
      },
      "the verb fell back to the ordinary writer connection when the seat credential was absent");

    await resetAuthorization(seat);
  });
