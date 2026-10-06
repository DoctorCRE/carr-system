// WR-000110 — F02-LEASE-ROW and T-WRITER-DENIED, on a real connection role.
//
// WHY THIS CANNOT BE A MOCK. Two of this Work Request's properties are
// properties of GRANTS and of a real session_user, and nothing else can show
// them: (1) the column-scoped SELECT grants really do reach carr_writer, which
// is the bundle the admission door connects as — a reader-only grant would
// answer 42501 before the evaluator was ever called; and (2) the privileged
// writer's kind gate really does let the writer bundle record an admission
// refusal and nothing else.
//
// SET SESSION AUTHORIZATION is the door, not SET ROLE: the writer's gate reads
// session_user, so a proof built on SET ROLE would pass while session_user
// stayed the superuser's. The same door ops/assurance-evidence-acceptance-local-pg-gate.py
// and the Gate Zero boundary proof already use.
//
// This establishes the role state it depends on rather than inheriting whatever
// the alphabetically preceding gate left behind, and it rolls back every row it
// writes.
//
//   DATABASE_URL=postgresql://localhost/... node --test \
//     mcp-server/test/program-controller-census-role-boundary.v5.test.mjs

import test from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";

import {
  V5_MAX_CENSUS_AGE_SECONDS, evaluateSliceAdmission,
} from "../src/engineering-program-controller.v5.js";
import { readLiveLeaseCensus, readSliceAdmissionRequest }
  from "../src/program-controller-census.v5.js";

const DSN = process.env.DATABASE_URL || "";
const REQUIRED = process.env.CARR_PROGRAM_CONTROLLER_DB_REQUIRED === "1";
const LOOPBACK = /@(localhost|127[.]0[.]0[.]1)[:/]|^postgres(ql)?:\/\/(localhost|\/)/;
const WRITER_FUNCTION = "ops.record_program_controller_fact(text,uuid,jsonb)";

const AUTHORITY_LOGIN = "carr_authority_joe";
const WRITER_LOGIN = "program_controller_probe_writer";
const READER_LOGIN = "program_controller_probe_reader";

/** The eight fact kinds, exactly as the migration declares them. */
const PRIVILEGED_KINDS = [
  "slice_source_lease", "slice_lease_release", "program_width_evidence", "program_width_state",
  "slice_checkpoint", "release_receipt", "origin_head_observation",
];
const WRITER_KIND = "admission_refusal";

const HEAD = "a".repeat(40);
const ROOT = "/tmp/wr110-role-boundary";
const SLICE = "slice:wr110-role-boundary";
const PROGRAM = "wr:00000000-0000-4000-8000-000000000110";

async function connect(pg) {
  const client = new pg.Client({ connectionString: DSN });
  await client.connect();
  return client;
}

async function authorizeAs(client, role) {
  await client.query(`set session authorization ${role}`);
  const who = (await client.query("select session_user as who")).rows[0].who;
  if (who !== role)
    throw new Error(`session authorization did not take: session_user is ${who}, not ${role}`);
}

/**
 * The three login roles this proof needs, minted here because db/schema.sql
 * deliberately does not create human authority logins and 0517 mints no role at
 * all. Passwordless, reachable only by SET SESSION AUTHORIZATION, and only ever
 * on a throwaway loopback database.
 */
async function ensureRoles(owner) {
  await owner.query(`
    do $$
    begin
      if not exists (select 1 from pg_roles where rolname = '${AUTHORITY_LOGIN}') then
        create role ${AUTHORITY_LOGIN} login;
      end if;
      if not exists (select 1 from pg_roles where rolname = '${WRITER_LOGIN}') then
        create role ${WRITER_LOGIN} login;
      end if;
      if not exists (select 1 from pg_roles where rolname = '${READER_LOGIN}') then
        create role ${READER_LOGIN} login;
      end if;
    end $$;`);
  await owner.query(`grant carr_authority to ${AUTHORITY_LOGIN}`);
  await owner.query(`grant carr_writer to ${WRITER_LOGIN}`);
  await owner.query(`grant carr_reader to ${READER_LOGIN}`);
}

/** The pg client shape the census reader expects. */
const censusClient = client => ({
  query: async (text, values = []) => client.query(text, values),
});

async function skipUnlessDatabase(t) {
  if (!DSN) {
    assert.equal(REQUIRED, false, "this proof was required and no database URL was given to it");
    t.skip("the migration class supplies DATABASE_URL");
    return null;
  }
  assert.ok(LOOPBACK.test(DSN),
    "REFUSED: this proof mints login roles and runs against a disposable loopback only");
  return (await import("pg")).default ?? (await import("pg"));
}

test("T-WRITER-DENIED: the writer bundle may record an admission refusal and nothing else",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const owner = await connect(pg);
    t.after(() => owner.end().catch(() => {}));
    await ensureRoles(owner);

    // The ACL itself: EXECUTE for exactly two bundles, and not for public.
    const acl = (await owner.query(
      `select has_function_privilege('carr_authority',$1,'execute') authority,
              has_function_privilege('carr_writer',$1,'execute') writer,
              has_function_privilege('carr_reader',$1,'execute') reader,
              has_function_privilege('carr_jobs',$1,'execute') jobs,
              has_function_privilege('public',$1,'execute') anyone`,
      [WRITER_FUNCTION])).rows[0];
    assert.deepEqual(acl,
      { authority: true, writer: true, reader: false, jobs: false, anyone: false });

    const writer = await connect(pg);
    t.after(() => writer.end().catch(() => {}));
    await authorizeAs(writer, WRITER_LOGIN);
    await writer.query("begin");
    t.after(() => writer.query("rollback").catch(() => {}));

    // Every privileged kind is refused BY PRIVILEGE, not by a shape error.
    for (const kind of PRIVILEGED_KINDS) {
      let error = null;
      try {
        await writer.query(
          "select ops.record_program_controller_fact($1::text, $2::uuid, $3::jsonb)",
          [kind, randomUUID(), JSON.stringify({ slice_ref: SLICE })]);
      } catch (raised) {
        error = raised;
        await writer.query("rollback");
        await writer.query("begin");
      }
      assert.ok(error, `the writer bundle recorded ${kind}`);
      assert.equal(error.code, "42501",
        `${kind} was refused for some reason other than privilege: ${error.message}`);
    }

    // And the ONE kind it may record, because a refusal it cannot record is a
    // refusal that is not evidence.
    const recorded = (await writer.query(
      "select ops.record_program_controller_fact($1::text, $2::uuid, $3::jsonb) as fact",
      [WRITER_KIND, randomUUID(), JSON.stringify({
        slice_ref: SLICE, reason_id: "source_path_overlap_denied",
        blocking_check: "source_path_overlap", decided_at: new Date().toISOString(),
        decision_digest: `sha256:${"b".repeat(64)}`,
      })])).rows[0].fact;
    assert.equal(recorded.ok, true);
    assert.equal(recorded.reason_id, "source_path_overlap_denied");
    await writer.query("rollback");
  });

test("F02-LEASE-ROW: a lease recorded by the authority reads back column for column as the reader",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const owner = await connect(pg);
    t.after(() => owner.end().catch(() => {}));
    await ensureRoles(owner);

    const authority = await connect(pg);
    t.after(() => authority.end().catch(() => {}));
    await authorizeAs(authority, AUTHORITY_LOGIN);
    await authority.query("begin");
    t.after(() => authority.query("rollback").catch(() => {}));

    const lease = {
      slice_ref: SLICE, worktree_ref: "worktree:wr110-role", worktree_path: "/tmp/wr110-role",
      branch_ref: "branch:wr110-role", base_commit_sha: HEAD,
      source_paths: ["mcp-server/src/program-controller-census.v5.js"],
      database_disposition: "schema_only_fixture", database_resources: [],
      serialized_surfaces: [], repository_actions: ["repository:commit"],
      reuse_disposition: "extend", model_roles: ["author"], held_by_actor: "joe",
    };
    await authority.query(
      "select ops.record_program_controller_fact('slice_source_lease', $1::uuid, $2::jsonb)",
      [randomUUID(), JSON.stringify(lease)]);
    await authority.query(
      "select ops.record_program_controller_fact('origin_head_observation', $1::uuid, $2::jsonb)",
      [randomUUID(), JSON.stringify({ repository_root: ROOT, origin_main_sha: HEAD,
        observed_at: new Date().toISOString(), observed_by: "joe" })]);
    await authority.query(
      "select ops.record_program_controller_fact('program_width_state', $1::uuid, $2::jsonb)",
      [randomUUID(), JSON.stringify({ program_ref: PROGRAM, current_width: 3, requested_width: 3 })]);

    // THE READ, on the READER login. If the grants named only carr_reader this
    // still passes; the writer-role read below is the one that proves the door.
    await authorizeAs(authority, READER_LOGIN);
    const census = await readLiveLeaseCensus({
      client: censusClient(authority), repositoryRoot: ROOT });
    const entry = census.active_leases.find(row => row.slice_ref === SLICE);
    assert.ok(entry, "the recorded lease did not come back in the census");
    assert.deepEqual(entry, {
      slice_ref: lease.slice_ref, worktree_ref: lease.worktree_ref,
      worktree_path: lease.worktree_path, source_paths: [...lease.source_paths].sort(),
      database_resources: [], serialized_surfaces: [],
    });
    assert.equal(census.origin_main_sha, HEAD);
    assert.equal(census.source, "live_lease_census");
    const ageSeconds = (Date.now() - Date.parse(census.observed_at)) / 1000;
    assert.ok(ageSeconds >= 0 && ageSeconds < V5_MAX_CENSUS_AGE_SECONDS,
      `the census reported an observation ${ageSeconds}s old`);

    await authority.query("rollback");
  });

test("the census read the admission door actually makes succeeds on the WRITER login",
  async t => {
    const pg = await skipUnlessDatabase(t);
    if (!pg) return;
    const owner = await connect(pg);
    t.after(() => owner.end().catch(() => {}));
    await ensureRoles(owner);

    const authority = await connect(pg);
    t.after(() => authority.end().catch(() => {}));
    await authorizeAs(authority, AUTHORITY_LOGIN);
    await authority.query("begin");
    t.after(() => authority.query("rollback").catch(() => {}));
    await authority.query(
      "select ops.record_program_controller_fact('slice_source_lease', $1::uuid, $2::jsonb)",
      [randomUUID(), JSON.stringify({
        slice_ref: SLICE, worktree_ref: "worktree:wr110-role", worktree_path: "/tmp/wr110-role",
        branch_ref: "branch:wr110-role", base_commit_sha: HEAD,
        source_paths: ["mcp-server/src/program-controller-census.v5.js"],
        database_disposition: "schema_only_fixture", database_resources: [],
        serialized_surfaces: [], repository_actions: ["repository:commit"],
        reuse_disposition: "extend", model_roles: ["author"], held_by_actor: "joe" })]);
    await authority.query(
      "select ops.record_program_controller_fact('origin_head_observation', $1::uuid, $2::jsonb)",
      [randomUUID(), JSON.stringify({ repository_root: ROOT, origin_main_sha: HEAD,
        observed_at: new Date().toISOString(), observed_by: "joe" })]);
    await authority.query(
      "select ops.record_program_controller_fact('program_width_state', $1::uuid, $2::jsonb)",
      [randomUUID(), JSON.stringify({ program_ref: PROGRAM, current_width: 3, requested_width: 3 })]);

    // THE ASSERTION THIS FILE EXISTS FOR. The admission door runs on
    // DATABASE_URL_WRITER as carr_writer, so every census SELECT it makes runs
    // as carr_writer. Column-scoped SELECT to carr_reader alone would answer
    // 42501 here, before the evaluator was ever called.
    await authorizeAs(authority, WRITER_LOGIN);
    const request = await readSliceAdmissionRequest({
      client: censusClient(authority), sliceRef: SLICE, programRef: PROGRAM,
      repositoryRoot: ROOT });
    const answer = evaluateSliceAdmission(request);
    assert.equal(answer.slice_ref, SLICE);
    assert.equal(answer.decision, "allow");
    // Read on the writer login, so these two are proof that the STORED columns
    // no accepted slice plan holds are reachable from the door's own role.
    assert.equal(answer.reuse_disposition, "extend");
    assert.deepEqual(answer.model_roles_declared, ["author"]);

    await authority.query("rollback");
  });
