// WR-000110 — the program-controller census reader, one test per acceptance
// criterion it owns, plus the supporting proofs the frozen spec names.
//
// The database-backed halves of F02-LEASE-ROW, F02-ADMISSION-OVERLAP and
// T-CATALOG-DELTA live in program-controller-census-role-boundary.v5.test.mjs
// and program-controller-census-postgres.sql, which the migration class runs on
// a disposable cluster. What is here is decidable without one.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { digest } from "../src/artifact-trust.js";
import { V5BoundaryError } from "../src/global-boundaries.v5.js";
import { ENGINEERING_REPOSITORY_ACTIONS } from "../src/engineering-runtime.js";
import {
  V5_DATA_DISPOSITIONS, V5_EVIDENCE_STATES, V5_MAX_CENSUS_AGE_SECONDS, V5_MODEL_ROLES,
  V5_PROGRAM_WIDTH_LADDER, V5_REFUSED_DATA_DISPOSITIONS, V5_RELEASE_RECEIPT_KINDS,
  V5_REUSE_DISPOSITIONS, V5_SERIALIZED_SURFACES, V5_WIDTH_EVIDENCE_CLASSES,
  evaluateReleaseEligibility, evaluateSliceAdmission,
} from "../src/engineering-program-controller.v5.js";
import {
  AUTO_RELEASE_REQUESTED, CENSUS_SOURCE, PROGRAM_CONTROLLER_PARAMETERS,
  PROGRAM_CONTROLLER_PATH, readCheckpointResumeRequest, readLiveLeaseCensus, readOriginHead,
  readProgramControllerCensus, readReleaseRequest, readSliceAdmissionRequest, readWidthRequest,
} from "../src/program-controller-census.v5.js";

const CONTROLLER_SOURCE = fileURLToPath(
  new URL("../src/engineering-program-controller.v5.js", import.meta.url));
const MIGRATION_0517 = fileURLToPath(
  new URL("../../migrations/0517_program_controller_seams.sql", import.meta.url));

const HEAD = "a".repeat(40);
const OTHER_HEAD = "b".repeat(40);
const ROOT = "/Users/booko/carr-system";
const PROGRAM = "program:v5-f02";

const iso = date => date.toISOString().replace(/\.(\d{3})\d*Z$/, ".$1Z");

// ---------------------------------------------------------------------------
// A fake client that answers exactly the legs the reader issues. Matching is on
// the relation each leg names, so a leg that changes shape does not silently
// keep passing.
// ---------------------------------------------------------------------------

function fakeClient(rows = {}, { raise = null } = {}) {
  const calls = [];
  return {
    calls,
    async query(sql, params = []) {
      calls.push({ sql, params });
      if (raise) throw raise;
      if (sql.includes("ops.program_origin_head_observation")) return { rows: rows.head ?? [] };
      if (sql.includes("ops.slice_source_lease")) return { rows: rows.leases ?? [] };
      if (sql.includes("ops.program_width_state")) return { rows: rows.widthState ?? [] };
      // The evidence leg is bound to the census head, so the fake filters on it
      // too: evidence on an older base must not come back just because it exists.
      if (sql.includes("ops.program_width_evidence"))
        return { rows: (rows.widthEvidence ?? []).filter(row => row.bound_base_sha === params[1]) };
      if (sql.includes("ops.slice_checkpoint")) return { rows: rows.checkpoints ?? [] };
      if (sql.includes("ops.release_receipt")) return { rows: rows.receipts ?? [] };
      throw new Error(`the census reader issued a leg this fake does not answer: ${sql}`);
    },
  };
}

const headRow = (observedAt = new Date()) => ([{
  repository_root: ROOT, origin_main_sha: HEAD, observed_at: observedAt,
}]);

const leaseRow = (overrides = {}) => ({
  slice_ref: "slice:v5-f02-a",
  worktree_ref: "worktree:wr110-a",
  worktree_path: "/Users/booko/carr-system/.claude/worktrees/wr110-a",
  branch_ref: "branch:wr110-a",
  base_commit_sha: HEAD,
  source_paths: ["mcp-server/src/program-controller-census.v5.js"],
  database_disposition: "schema_only_fixture",
  database_resources: [],
  serialized_surfaces: [],
  repository_actions: ["repository:commit", "repository:create-worktree"],
  reuse_disposition: "extend",
  model_roles: ["author"],
  ...overrides,
});

const widthStateRow = (current, requested) =>
  ([{ program_ref: PROGRAM, current_width: current, requested_width: requested }]);

const evidenceRows = (state = "accepted", baseSha = HEAD, classes = V5_WIDTH_EVIDENCE_CLASSES) =>
  classes.map(evidence_class => ({
    evidence_class, state, evidence_ref: `evidence:${evidence_class}`,
    bound_base_sha: baseSha, observed_at: new Date(),
  }));

// The six receipt bodies that satisfy all six predicates. Built once here and
// content-addressed exactly as the controller and the definer function both do.
function releaseBodies(sliceRef = "slice:v5-f02-a") {
  return {
    head_observation: { kind: "head_observation", slice_ref: sliceRef, head_sha: HEAD,
      observed_head_sha: HEAD },
    merge_slot: { kind: "merge_slot", slice_ref: sliceRef, head_sha: HEAD, state: "free",
      held_by_slice_ref: null },
    readback: { kind: "readback", slice_ref: sliceRef, head_sha: HEAD, state: "verified",
      main_sha: HEAD, delivered_source_digest: "sha256:delivered",
      expected_source_digest: "sha256:delivered" },
    required_checks: { kind: "required_checks", slice_ref: sliceRef, head_sha: HEAD,
      checks: [{ name: "strict", conclusion: "success", head_sha: HEAD }] },
    revalidation: { kind: "revalidation", slice_ref: sliceRef, head_sha: HEAD, required: false,
      revalidated_against_sha: null, current_main_sha: HEAD },
    review: { kind: "review", slice_ref: sliceRef, head_sha: HEAD, state: "accepted",
      reviewer_actor_id: "claude", maker_actor_id: "codex", reviewed_head_sha: HEAD },
  };
}

function receiptRows(releaseRef = "release:wr110", kinds = V5_RELEASE_RECEIPT_KINDS) {
  const bodies = releaseBodies();
  return kinds.map(kind => ({
    release_ref: releaseRef, slice_ref: bodies[kind].slice_ref, head_sha: bodies[kind].head_sha,
    receipt_ref: `receipt:${digest(bodies[kind])}`, receipt_kind: kind, body: bodies[kind],
  }));
}

async function rejectsWith(promise, code) {
  try {
    await promise;
  } catch (error) {
    assert.equal(error.code, code, `expected ${code}, got ${error.code}: ${error.message}`);
    return error;
  }
  assert.fail(`expected a rejection with ${code}`);
}

// ---------------------------------------------------------------------------
// F02-WIDTH-LADDER
// ---------------------------------------------------------------------------

test("F02-WIDTH-LADDER: 1 -> 2 on six accepted classes bound to the census head allows", async () => {
  const client = fakeClient({ head: headRow(), leases: [], widthState: widthStateRow(1, 2),
    widthEvidence: evidenceRows() });
  const census = await readLiveLeaseCensus({ client, repositoryRoot: ROOT });
  const request = await readWidthRequest({ client, programRef: PROGRAM, census });
  const { evaluateProgramWidth } = await import("../src/engineering-program-controller.v5.js");
  const answer = evaluateProgramWidth(request);
  assert.equal(answer.decision, "allow");
  assert.equal(answer.granted_width, 2);
  assert.equal(answer.reason_id, "width_granted_on_current_accepted_evidence");
});

test("F02-WIDTH-LADDER: one pending class refuses and names it in missing_evidence_classes", async () => {
  const rows = evidenceRows();
  rows[0] = { ...rows[0], state: "pending" };
  const client = fakeClient({ head: headRow(), leases: [], widthState: widthStateRow(1, 2),
    widthEvidence: rows });
  const census = await readLiveLeaseCensus({ client, repositoryRoot: ROOT });
  const { evaluateProgramWidth } = await import("../src/engineering-program-controller.v5.js");
  const answer = evaluateProgramWidth(await readWidthRequest({ client, programRef: PROGRAM, census }));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "width_evidence_incomplete");
  assert.deepEqual(answer.missing_evidence_classes, [V5_WIDTH_EVIDENCE_CLASSES[0]]);
  assert.equal(answer.granted_width, 1, "a refusal leaves the program running at its earned width");
});

test("F02-WIDTH-LADDER: 1 -> 3 skips a step and refuses even on complete evidence", async () => {
  const client = fakeClient({ head: headRow(), leases: [], widthState: widthStateRow(1, 3),
    widthEvidence: evidenceRows() });
  const census = await readLiveLeaseCensus({ client, repositoryRoot: ROOT });
  const { evaluateProgramWidth } = await import("../src/engineering-program-controller.v5.js");
  const answer = evaluateProgramWidth(await readWidthRequest({ client, programRef: PROGRAM, census }));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "width_ladder_step_skipped");
});

test("F02-WIDTH-LADDER: evidence bound to a superseded base is not current evidence", async () => {
  const client = fakeClient({ head: headRow(), leases: [], widthState: widthStateRow(1, 2),
    widthEvidence: evidenceRows("accepted", OTHER_HEAD) });
  const census = await readLiveLeaseCensus({ client, repositoryRoot: ROOT });
  const { evaluateProgramWidth } = await import("../src/engineering-program-controller.v5.js");
  const request = await readWidthRequest({ client, programRef: PROGRAM, census });
  // The reader asks for evidence bound to the census head, so evidence on an
  // older base is not even returned: the answer is "incomplete", never "the
  // caller paired stale evidence with a base that flatters it".
  assert.deepEqual(Object.keys(request.evidence), []);
  assert.equal(evaluateProgramWidth(request).reason_id, "width_evidence_incomplete");
});

// ---------------------------------------------------------------------------
// F02-ADMISSION-OVERLAP (the decision half; the ledger half is the SQL proof)
// ---------------------------------------------------------------------------

test("F02-ADMISSION-OVERLAP: a peer lease on an overlapping path segment refuses by name", async () => {
  const mine = leaseRow();
  const peer = leaseRow({
    slice_ref: "slice:v5-f02-b", worktree_ref: "worktree:wr110-b",
    worktree_path: "/Users/booko/carr-system/.claude/worktrees/wr110-b",
    branch_ref: "branch:wr110-b", source_paths: ["mcp-server/src"],
  });
  const client = fakeClient({ head: headRow(), leases: [mine, peer],
    widthState: widthStateRow(3, 3), widthEvidence: evidenceRows() });
  const answer = evaluateSliceAdmission(await readSliceAdmissionRequest({
    client, sliceRef: mine.slice_ref, programRef: PROGRAM, repositoryRoot: ROOT }));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "source_path_overlap_denied");
  assert.equal(answer.blocking_check, "source_path_overlap");
  assert.equal(answer.check_states.source_path_overlap.detail.conflicting_slice_ref, peer.slice_ref);
  assert.equal(answer.refused_before_edit, true);
  assert.equal(answer.lease_claimed, false);
});

test("F02-ADMISSION-OVERLAP: a disjoint peer admits, and the answer carries the stored decision", async () => {
  const mine = leaseRow();
  const peer = leaseRow({
    slice_ref: "slice:v5-f02-b", worktree_ref: "worktree:wr110-b",
    worktree_path: "/Users/booko/carr-system/.claude/worktrees/wr110-b",
    branch_ref: "branch:wr110-b", source_paths: ["ops/ci.sh"],
  });
  const client = fakeClient({ head: headRow(), leases: [mine, peer],
    widthState: widthStateRow(3, 3), widthEvidence: evidenceRows() });
  const answer = evaluateSliceAdmission(await readSliceAdmissionRequest({
    client, sliceRef: mine.slice_ref, programRef: PROGRAM, repositoryRoot: ROOT }));
  assert.equal(answer.decision, "allow");
  // The two fields that exist only because they are STORED: no accepted slice
  // plan holds either, so an admission that carries them proves they were read.
  assert.equal(answer.reuse_disposition, "extend");
  assert.deepEqual(answer.model_roles_declared, ["author"]);
});

test("a lease with no live row at all is a typed dependency, never a fabricated lease", async () => {
  const client = fakeClient({ head: headRow(), leases: [], widthState: widthStateRow(1, 1) });
  await rejectsWith(readSliceAdmissionRequest({
    client, sliceRef: "slice:absent", programRef: PROGRAM, repositoryRoot: ROOT }),
  "DEPENDENCY_UNAVAILABLE");
});

// ---------------------------------------------------------------------------
// The authoritative head (planned check 3)
// ---------------------------------------------------------------------------

test("the head comes from the newest recorded observation, never from a lease's own base", async () => {
  const client = fakeClient({ head: headRow(), leases: [leaseRow({ base_commit_sha: OTHER_HEAD })] });
  const census = await readLiveLeaseCensus({ client, repositoryRoot: ROOT });
  assert.equal(census.origin_main_sha, HEAD);
  assert.equal(census.source, CENSUS_SOURCE);
  assert.equal(census.source, "live_lease_census");
  assert.equal(census.repository_root, ROOT);
});

test("an observation older than the permitted age is refused as unknown freshness", async () => {
  const stale = new Date(Date.now() - (V5_MAX_CENSUS_AGE_SECONDS + 60) * 1000);
  const client = fakeClient({ head: headRow(stale) });
  await rejectsWith(readOriginHead({ client, repositoryRoot: ROOT }), "FRESHNESS_UNKNOWN");
});

test("no head observation at all is refused as unknown freshness, never defaulted", async () => {
  await rejectsWith(readOriginHead({ client: fakeClient({ head: [] }), repositoryRoot: ROOT }),
    "FRESHNESS_UNKNOWN");
});

test("the observation instant comes from the runtime clock, not from the database", async () => {
  const pinned = new Date("2026-09-17T18:00:00.000Z");
  const client = fakeClient({ head: headRow(pinned), leases: [] });
  const census = await readLiveLeaseCensus({ client, repositoryRoot: ROOT, now: () => pinned });
  assert.equal(census.observed_at, iso(pinned));
  assert.match(census.observed_at, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z$/);
});

// ---------------------------------------------------------------------------
// F02-RESUME-DOOR (the decision half)
// ---------------------------------------------------------------------------

const checkpointRow = (overrides = {}) => ({
  checkpoint_ref: "checkpoint:wr110-1", recorded_at: new Date(), base_commit_sha: HEAD,
  worktree_ref: "worktree:wr110-a", completed_step_refs: ["step:one"],
  next_step_ref: "step:two", record_evidence_refs: ["record:one"],
  reconstruction_source: "durable_records", inherited_transcript_used: false, ...overrides,
});

test("F02-RESUME-DOOR: a checkpoint bound to a superseded head refuses on its own reason", async () => {
  const client = fakeClient({ head: headRow(), leases: [leaseRow()],
    checkpoints: [checkpointRow({ base_commit_sha: OTHER_HEAD })] });
  const { evaluateCheckpointResume } = await import("../src/engineering-program-controller.v5.js");
  const answer = evaluateCheckpointResume(await readCheckpointResumeRequest({
    client, sliceRef: "slice:v5-f02-a", repositoryRoot: ROOT }));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "checkpoint_base_moved_revalidation_required");
});

test("F02-RESUME-DOOR: a current checkpoint on the held tree resumes from records", async () => {
  const client = fakeClient({ head: headRow(), leases: [leaseRow()], checkpoints: [checkpointRow()] });
  const { evaluateCheckpointResume } = await import("../src/engineering-program-controller.v5.js");
  const answer = evaluateCheckpointResume(await readCheckpointResumeRequest({
    client, sliceRef: "slice:v5-f02-a", repositoryRoot: ROOT }));
  assert.equal(answer.decision, "allow");
  assert.equal(answer.resumes_from_records, true);
  assert.equal(answer.next_step_ref, "step:two");
});

test("F02-RESUME-DOOR: no checkpoint row returns null, and a checkpoint is never invented", async () => {
  const client = fakeClient({ head: headRow(), leases: [leaseRow()], checkpoints: [] });
  assert.equal(await readCheckpointResumeRequest({
    client, sliceRef: "slice:v5-f02-a", repositoryRoot: ROOT }), null);
});

// ---------------------------------------------------------------------------
// F02-RELEASE-ALLOW
// ---------------------------------------------------------------------------

test("F02-RELEASE-ALLOW: six recorded receipts reach allow through the module-bound holder", async () => {
  const client = fakeClient({ receipts: receiptRows() });
  const request = await readReleaseRequest({ client, releaseRef: "release:wr110" });
  const answer = evaluateReleaseEligibility(request);
  assert.equal(answer.decision, "allow");
  assert.equal(answer.released, true);
  assert.equal(answer.merge_admitted, true);
  assert.equal(answer.state_holder_bound, true);
  assert.equal(answer.state_holder_is_caller_supplied, false);
  assert.equal(answer.caller_stated_release_facts, false);
  assert.equal(answer.observed_head_sha, HEAD);
  assert.equal(answer.receipt_refs_verified.length, V5_RELEASE_RECEIPT_KINDS.length);
});

test("F02-RELEASE-ALLOW: a caller-supplied holder is still impossible, allow or not", async () => {
  const client = fakeClient({ receipts: receiptRows() });
  const request = await readReleaseRequest({ client, releaseRef: "release:wr110" });
  assert.throws(() => evaluateReleaseEligibility(request, { resolveReceipt: () => null }),
    error => {
      assert.ok(error instanceof V5BoundaryError);
      assert.equal(error.code, "release_state_holder_is_not_an_argument");
      return true;
    });
});

test("F02-RELEASE-ALLOW: the binding is built by the evaluator from the request's own slice and head", async () => {
  const client = fakeClient({ receipts: receiptRows() });
  const request = await readReleaseRequest({ client, releaseRef: "release:wr110" });
  // There is no binding field to pass and no store that holds one: the request
  // carries a slice and a head, and the evaluator pairs them itself.
  assert.deepEqual(Object.keys(request).sort(),
    ["auto_release_requested", "head_sha", "receipts", "slice_ref"]);
  assert.equal(request.slice_ref, "slice:v5-f02-a");
  assert.equal(request.head_sha, HEAD);
});

// ---------------------------------------------------------------------------
// T-AUTO-RELEASE (gap 20)
// ---------------------------------------------------------------------------

test("T-AUTO-RELEASE (a): a complete receipt set yields the literal false, strictly", async () => {
  const client = fakeClient({ receipts: receiptRows() });
  const request = await readReleaseRequest({ client, releaseRef: "release:wr110" });
  assert.equal(request.auto_release_requested, false);
  assert.strictEqual(request.auto_release_requested, false);
  assert.strictEqual(AUTO_RELEASE_REQUESTED, false);
});

test("T-AUTO-RELEASE (b): zero and five receipts both refuse and return no request at all", async () => {
  for (const kinds of [[], V5_RELEASE_RECEIPT_KINDS.slice(0, 5)]) {
    const client = fakeClient({ receipts: receiptRows("release:wr110", kinds) });
    const error = await rejectsWith(readReleaseRequest({ client, releaseRef: "release:wr110" }),
      "DEPENDENCY_UNAVAILABLE");
    assert.equal(error.detail.missing, "ops.release_receipt");
    assert.ok(error.detail.missing_receipt_kinds.length > 0);
    // The evaluator was never called and no receipt reference was constructed:
    // the only query the reader issued was the receipt read itself.
    assert.equal(client.calls.length, 1);
    assert.ok(client.calls[0].sql.includes("ops.release_receipt"));
  }
});

test("T-AUTO-RELEASE (d): no column named auto_release_requested exists in 0517", () => {
  const migration = readFileSync(MIGRATION_0517, "utf8");
  assert.equal(migration.includes("auto_release_requested"), false,
    "a column of that name would imply someone may set it");
});

test("T-AUTO-RELEASE: the route's admitted parameter set is exactly three and excludes it", () => {
  assert.deepEqual([...PROGRAM_CONTROLLER_PARAMETERS], ["program_ref", "release_ref", "slice_ref"]);
  assert.equal(PROGRAM_CONTROLLER_PARAMETERS.includes("auto_release_requested"), false);
  assert.equal(PROGRAM_CONTROLLER_PATH, "/api/v1/program-controller");
});

// ---------------------------------------------------------------------------
// T-ERROR-CLASS (planned check 20)
// ---------------------------------------------------------------------------

test("T-ERROR-CLASS: 42501 is a DEPENDENCY, exactly as both existing census readers class it", async () => {
  const privilege = Object.assign(new Error("permission denied"), { code: "42501" });
  const result = await readProgramControllerCensus({
    client: fakeClient({}, { raise: privilege }), actor: { slug: "joe" },
    correlationId: "corr:wr110", program_ref: PROGRAM });
  const censusCoverage = result.coverage.find(entry => entry.answer === "census");
  assert.equal(censusCoverage.missing_reason, "DEPENDENCY_UNAVAILABLE");
  assert.notEqual(censusCoverage.missing_reason, "AUTHORIZATION_REFUSED");
  assert.equal(result.census, null);
});

test("T-ERROR-CLASS: an unknown driver code is our own bug, and reads INTERNAL_ERROR", async () => {
  const unknown = Object.assign(new Error("boom"), { code: "XX999" });
  const result = await readProgramControllerCensus({
    client: fakeClient({}, { raise: unknown }), actor: { slug: "joe" },
    correlationId: "corr:wr110" });
  assert.equal(result.coverage.find(entry => entry.answer === "census").missing_reason,
    "INTERNAL_ERROR");
});

test("T-ERROR-CLASS: an unadmitted actor is an authorization refusal, which is a different path", async () => {
  await rejectsWith(readProgramControllerCensus({
    client: fakeClient(), actor: { slug: "mallory" }, correlationId: "corr:wr110" }),
  "AUTHORIZATION_REFUSED");
});

// ---------------------------------------------------------------------------
// T-CONST — every DDL list equals the exported constant, in BOTH directions
// ---------------------------------------------------------------------------

const migrationText = () => readFileSync(MIGRATION_0517, "utf8");

/** The quoted members of the first `in (...)` list following a column name. */
function ddlEnum(column) {
  const migration = migrationText();
  const at = migration.indexOf(`${column} text not null`);
  assert.notEqual(at, -1, `0517 declares no ${column} column`);
  const list = migration.slice(at).match(/in \(([^)]*)\)/);
  assert.ok(list, `${column} carries no enumerated check`);
  return list[1].split(",").map(part => part.trim().replace(/^'|'$/g, "")).sort();
}

/** The quoted members of the first `array[...]` containment check for a column. */
function ddlArrayEnum(column) {
  const migration = migrationText();
  const at = migration.indexOf(`${column} text[] not null`);
  assert.notEqual(at, -1, `0517 declares no ${column} column`);
  const list = migration.slice(at).match(/array\[([^\]]*)\]/);
  assert.ok(list, `${column} carries no containment check`);
  return list[1].split(",").map(part => part.trim().replace(/^'|'$/g, "")).sort();
}

const bothWays = (declared, exported, what) => {
  const left = [...declared].sort();
  const right = [...exported].map(String).sort();
  assert.deepEqual(left, right, `${what}: the DDL admits ${left} and the module ${right}`);
  for (const member of right) assert.ok(left.includes(member), `${what} omits ${member}`);
  for (const member of left) assert.ok(right.includes(member), `${what} admits unknown ${member}`);
};

test("T-CONST: every enumerated DDL list in 0517 equals its exported constant both ways", () => {
  bothWays(ddlEnum("evidence_class"), V5_WIDTH_EVIDENCE_CLASSES, "V5_WIDTH_EVIDENCE_CLASSES");
  bothWays(ddlEnum("state"), V5_EVIDENCE_STATES, "V5_EVIDENCE_STATES");
  bothWays(ddlEnum("database_disposition"),
    [...V5_DATA_DISPOSITIONS, ...V5_REFUSED_DATA_DISPOSITIONS], "V5_DATA_DISPOSITIONS + refused");
  bothWays(ddlEnum("reuse_disposition"), V5_REUSE_DISPOSITIONS, "V5_REUSE_DISPOSITIONS");
  bothWays(ddlEnum("receipt_kind"), V5_RELEASE_RECEIPT_KINDS, "V5_RELEASE_RECEIPT_KINDS");
  bothWays(ddlArrayEnum("serialized_surfaces"), V5_SERIALIZED_SURFACES, "V5_SERIALIZED_SURFACES");
  bothWays(ddlArrayEnum("model_roles"), V5_MODEL_ROLES, "V5_MODEL_ROLES");
  bothWays(ddlArrayEnum("repository_actions"), ENGINEERING_REPOSITORY_ACTIONS,
    "ENGINEERING_REPOSITORY_ACTIONS");
});

test("T-CONST: reconstruction_source equals the module's own RECONSTRUCTION_SOURCES", () => {
  const source = readFileSync(CONTROLLER_SOURCE, "utf8");
  const declared = source.match(/const RECONSTRUCTION_SOURCES = deepFreeze\(\[([^\]]*)\]\)/);
  assert.ok(declared, "RECONSTRUCTION_SOURCES moved; T-CONST can no longer read it");
  bothWays(ddlEnum("reconstruction_source"),
    declared[1].split(",").map(part => part.trim().replace(/^"|"$/g, "")).filter(Boolean),
    "RECONSTRUCTION_SOURCES");
});

test("T-CONST: the width range equals V5_PROGRAM_WIDTH_LADDER in both directions", () => {
  const migration = migrationText();
  for (const column of ["current_width", "requested_width"]) {
    const at = migration.indexOf(`${column} integer not null`);
    assert.notEqual(at, -1, `0517 declares no ${column} column`);
    const list = migration.slice(at).match(/in \(([^)]*)\)/);
    const declared = list[1].split(",").map(part => Number(part.trim())).sort();
    const exported = [...V5_PROGRAM_WIDTH_LADDER].sort();
    assert.deepEqual(declared, exported, column);
    for (const width of exported) assert.ok(declared.includes(width), `${column} omits ${width}`);
    for (const width of declared) assert.ok(exported.includes(width), `${column} admits ${width}`);
  }
});

// ---------------------------------------------------------------------------
// T-EXACT-COVERAGE (gap 19) — A11 cannot silently go stale
// ---------------------------------------------------------------------------

/**
 * Every closed shape the controller enforces, read from its OWN CALL SITES
 * rather than from a list of constant names, plus the value the census reader
 * constructs for it. A new exact() set in the controller that this table does
 * not answer for fails the test.
 */
const EXACT_SET_COVERAGE = {
  WIDTH_REQUEST_FIELDS: "width request",
  WIDTH_BASE_FIELDS: "width base, built from the census",
  WIDTH_EVIDENCE_FIELDS: "one ops.program_width_evidence row",
  CENSUS_FIELDS: "the live lease census",
  ACTIVE_LEASE_FIELDS: "one live ops.slice_source_lease row",
  ADMISSION_WIDTH_FIELDS: "the width block of the admission request",
  ADMISSION_REQUEST_FIELDS: "the admission request",
  LEASE_FIELDS: "the slice's own live lease row, column for column",
  RESUME_REQUEST_FIELDS: "the resume request",
  CHECKPOINT_FIELDS: "the newest ops.slice_checkpoint row",
  RECONSTRUCTION_FIELDS: "the same checkpoint row",
  RELEASE_REQUEST_FIELDS: "the release request",
  V5_RELEASE_RECEIPT_KINDS: "the six ops.release_receipt rows",
  "RELEASE_RECEIPT_FIELDS[kind]": "the six per-kind receipt bodies",
  CHECK_FIELDS: "one entry of required_checks.checks[]",
  '["slice_ref"': "the release binding [slice_ref, head_sha], built inside the evaluator",
};

test("T-EXACT-COVERAGE: every exact() call site in the controller is answered here", () => {
  const source = readFileSync(CONTROLLER_SOURCE, "utf8");
  const sites = [...source.matchAll(/^\s*(?:const \w+ = )?exact\([^,]+,\s*([^,]+),/gm)]
    .map(match => match[1].trim());
  assert.ok(sites.length >= 16, `expected at least sixteen exact() call sites, found ${sites.length}`);
  for (const set of sites)
    assert.ok(Object.hasOwn(EXACT_SET_COVERAGE, set),
      `the controller closes a shape the census reader does not fill: ${set}`);
  // And the other direction: a row here that no call site uses is a stale row.
  for (const set of Object.keys(EXACT_SET_COVERAGE))
    assert.ok(sites.includes(set), `no exact() call site uses ${set} any more`);
});

test("T-EXACT-COVERAGE: every field of every set is a recorded row or a stated invariant", async () => {
  const client = fakeClient({ head: headRow(), leases: [leaseRow()],
    widthState: widthStateRow(1, 2), widthEvidence: evidenceRows(),
    checkpoints: [checkpointRow()], receipts: receiptRows() });
  const admission = await readSliceAdmissionRequest({
    client, sliceRef: "slice:v5-f02-a", programRef: PROGRAM, repositoryRoot: ROOT });
  const resume = await readCheckpointResumeRequest({
    client, sliceRef: "slice:v5-f02-a", repositoryRoot: ROOT });
  const release = await readReleaseRequest({ client, releaseRef: "release:wr110" });
  // Each evaluator's own exact() is the assertion: a missing field, an extra
  // field, or a field of the wrong type throws V5BoundaryError rather than
  // returning an answer. Constructing all four answers proves all four shapes.
  assert.equal(evaluateSliceAdmission(admission).answer, "slice_admission");
  const { evaluateCheckpointResume } = await import("../src/engineering-program-controller.v5.js");
  assert.equal(evaluateCheckpointResume(resume).answer, "checkpoint_resume");
  assert.equal(evaluateReleaseEligibility(release).answer, "release_eligibility");
  const { evaluateProgramWidth } = await import("../src/engineering-program-controller.v5.js");
  const census = await readLiveLeaseCensus({ client, repositoryRoot: ROOT });
  assert.equal(evaluateProgramWidth(await readWidthRequest({ client, programRef: PROGRAM, census }))
    .answer, "program_width");
});

// ---------------------------------------------------------------------------
// The whole census read behind the route
// ---------------------------------------------------------------------------

test("the census read returns every answer it could produce and names the ones it could not", async () => {
  const client = fakeClient({ head: headRow(), leases: [leaseRow()],
    widthState: widthStateRow(1, 2), widthEvidence: evidenceRows(),
    checkpoints: [], receipts: receiptRows() });
  const result = await readProgramControllerCensus({
    client, actor: { slug: "joe" }, correlationId: "corr:wr110",
    program_ref: PROGRAM, slice_ref: "slice:v5-f02-a", release_ref: "release:wr110",
    repository_root: ROOT });
  assert.equal(result.census.source, "live_lease_census");
  assert.equal(result.width_answer.decision, "allow");
  assert.equal(result.admission_answer.decision, "allow");
  assert.equal(result.resume_answer, null);
  assert.equal(result.coverage.find(entry => entry.answer === "resume_answer").detail.checkpoint_absent,
    true);
  assert.equal(result.release_answer.decision, "allow");
});

test("a release reference with an incomplete receipt set yields a null answer with its reason", async () => {
  const client = fakeClient({ head: headRow(), leases: [],
    receipts: receiptRows("release:wr110", V5_RELEASE_RECEIPT_KINDS.slice(0, 5)) });
  const result = await readProgramControllerCensus({
    client, actor: { slug: "joe" }, correlationId: "corr:wr110",
    release_ref: "release:wr110", repository_root: ROOT });
  assert.equal(result.release_answer, null);
  assert.equal(result.coverage.find(entry => entry.answer === "release_answer").missing_reason,
    "DEPENDENCY_UNAVAILABLE");
});
