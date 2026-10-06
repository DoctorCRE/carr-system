// V5-F02 — the program-controller census: the records half of the four
// evaluators in engineering-program-controller.v5.js.
//
// WHAT THIS IS. A read-only projection of ops.slice_source_lease,
// ops.program_width_state, ops.program_width_evidence, ops.slice_checkpoint,
// ops.release_receipt and ops.program_origin_head_observation into the four
// CLOSED request shapes the controller enforces, plus the module-bound release
// receipt snapshot the controller resolves release facts through.
//
// THE ONE RULE THIS MODULE EXISTS TO KEEP. Every field of every closed shape is
// either READ FROM A NAMED COLUMN or is a STATED INVARIANT with its reason.
// Nothing is taken from a caller argument, nothing is defaulted, and nothing is
// derived from a neighbouring vocabulary that answers a different question. The
// two invariants are stated at their construction sites and repeated here so a
// reader does not have to find them:
//
//   * `census.source` is the literal "live_lease_census"
//     (V5_DISJOINTNESS_SOURCES[0]). It is the only admissible provenance for a
//     disjointness claim, and the refused member exists so the module can name
//     what it will not accept — never so this reader can produce it.
//   * `request.auto_release_requested` is the literal `false`. Auto release is
//     not a product of this Work Request: the evaluator reports
//     auto_release_state "unavailable" unconditionally because the upstream
//     steps do not exist in this repository. A column would imply someone may
//     set it; a query parameter would let the gated party ask for its own auto
//     release. There is no column of that name in
//     migrations/0517_program_controller_seams.sql, and the route's admitted
//     parameter set does not contain it.
//
// A MISSING ROW IS A TYPED ERROR, NEVER A FABRICATED DEFAULT. No live lease, no
// width state, or fewer than six release receipts for a release reference, all
// raise DEPENDENCY_UNAVAILABLE and return nothing. An absent or stale head
// observation raises FRESHNESS_UNKNOWN. A missing GRANT — PostgreSQL 42501 — is
// a DEPENDENCY the reading role does not have, exactly as both existing census
// readers class it, and never an authorization refusal; classing it as one would
// report a grant gap as if the caller had asked for something it may not have.
//
// THE OBSERVATION INSTANT COMES FROM THE RUNTIME, not from the database clock.
// now() is constant for a whole transaction, so a census taken with it would
// report the age of the transaction rather than the age of the observation.
//
// This module is deliberately a library, not a program: it takes nothing from a
// process and carries no self-execution construct. The sealed frontier
// predicate in ops/scac-mutation-inventory.mjs matches on PLAIN TEXT, so even
// naming those constructs in a comment here would move the frontier and cost a
// registry successor.

import { assertReadOnly } from "./atlas-inventory-graph.v5.js";
import {
  V5_DISJOINTNESS_SOURCES, V5_MAX_CENSUS_AGE_SECONDS, V5_RELEASE_RECEIPT_KINDS,
  evaluateProgramWidth, evaluateSliceAdmission, evaluateCheckpointResume,
  evaluateReleaseEligibility,
} from "./engineering-program-controller.v5.js";
import { installReleaseReceiptSnapshot } from "./program-controller-release-state-holder.v5.js";

export const PROGRAM_CONTROLLER_PATH = "/api/v1/program-controller";

/** The three parameters the read route admits. auto_release_requested is not one. */
export const PROGRAM_CONTROLLER_PARAMETERS = Object.freeze(["program_ref", "release_ref", "slice_ref"]);

/** The provenance invariant, named once so both readers and tests cite one thing. */
export const CENSUS_SOURCE = V5_DISJOINTNESS_SOURCES[0];

/** The auto-release invariant, named once for the same reason. */
export const AUTO_RELEASE_REQUESTED = false;

const VALID_ACTORS = new Set(["joe", "dell"]);
const TENANT = "carr-internal";

// 42501 insufficient_privilege: a missing grant is a DEPENDENCY the reading role
// does not have, not a defect in this module and not an authorization refusal.
const DEPENDENCY_CODES = new Set([
  "DEPENDENCY_UNAVAILABLE", "ECONNRESET", "ECONNREFUSED", "ETIMEDOUT", "ENOTFOUND",
  "08000", "57P01", "42501",
]);

function typedError(code, detail = null) {
  const error = new Error(code);
  error.code = code;
  if (detail) error.detail = detail;
  return error;
}

function classifyReadError(error) {
  if (["AUTHORIZATION_REFUSED", "TENANT_SCOPE_REFUSED", "FRESHNESS_UNKNOWN", "INTERNAL_ERROR"]
    .includes(error?.code)) return error.code;
  if (DEPENDENCY_CODES.has(error?.code)) return "DEPENDENCY_UNAVAILABLE";
  return "INTERNAL_ERROR";
}

/** The module's ISO_INSTANT: UTC, at most three fractional digits, trailing Z. */
function instant(value) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.valueOf())) throw typedError("INTERNAL_ERROR");
  return date.toISOString().replace(/\.(\d{3})\d*Z$/, ".$1Z");
}

/**
 * C-sorted and duplicate-free, because the controller throws on anything else:
 * two callers who disagree about the order of a lease are describing two
 * different leases.
 */
function sortedUnique(values) {
  return [...new Set((values ?? []).map(String))].sort();
}

async function read(client, sql, params) {
  if (!assertReadOnly(sql)) throw typedError("INTERNAL_ERROR");
  const result = await client.query(sql, params);
  return result?.rows ?? [];
}

// ---------------------------------------------------------------------------
// The legs. Each one is a bare select over exactly the columns 0517 grants.
// ---------------------------------------------------------------------------

// $1 null means "the newest recorded observation, whatever root it names" — the
// door does not know the repository root and must not invent one, so it asks for
// the newest row and takes the root that row itself reports.
const HEAD_SQL = `select repository_root, origin_main_sha, observed_at
  from ops.program_origin_head_observation
  where $1::text is null or repository_root = $1::text
  order by observed_at desc, recorded_at desc
  limit 1`;

const LEASE_SQL = `select slice_ref, worktree_ref, worktree_path, branch_ref, base_commit_sha,
    source_paths, database_disposition, database_resources, serialized_surfaces,
    repository_actions, reuse_disposition, model_roles
  from ops.slice_source_lease
  where released_at is null
  order by slice_ref`;

const WIDTH_STATE_SQL = `select program_ref, current_width, requested_width
  from ops.program_width_state where program_ref = $1`;

const WIDTH_EVIDENCE_SQL = `select evidence_class, state, evidence_ref, bound_base_sha, observed_at
  from ops.program_width_evidence
  where program_ref = $1 and bound_base_sha = $2
  order by evidence_class`;

const CHECKPOINT_SQL = `select checkpoint_ref, recorded_at, base_commit_sha, worktree_ref,
    completed_step_refs, next_step_ref, record_evidence_refs,
    reconstruction_source, inherited_transcript_used
  from ops.slice_checkpoint
  where slice_ref = $1
  order by recorded_at desc, written_at desc
  limit 1`;

const RECEIPT_SQL = `select release_ref, slice_ref, head_sha, receipt_ref, receipt_kind, body
  from ops.release_receipt
  where release_ref = $1
  order by receipt_kind`;

// ---------------------------------------------------------------------------
// The reads.
// ---------------------------------------------------------------------------

/**
 * The AUTHORITATIVE origin/main head. Never a caller argument and never a
 * lease's own base_commit_sha: a per-slice base would let the gated party move
 * the base its own evidence is judged against, which is the hole
 * admissionWidthAnswer was written to close. An observation older than
 * V5_MAX_CENSUS_AGE_SECONDS is an old story, not a fact, so it is refused as
 * unknown freshness rather than used.
 */
export async function readOriginHead({ client, repositoryRoot = null, now = () => new Date() }) {
  const rows = await read(client, HEAD_SQL, [repositoryRoot ?? null]);
  if (rows.length === 0) throw typedError("FRESHNESS_UNKNOWN", { repository_root: repositoryRoot });
  const row = rows[0];
  const observedAt = instant(row.observed_at);
  const ageSeconds = (now().valueOf() - Date.parse(observedAt)) / 1000;
  if (ageSeconds < 0 || ageSeconds > V5_MAX_CENSUS_AGE_SECONDS)
    throw typedError("FRESHNESS_UNKNOWN",
      { observation_age_seconds: ageSeconds, max_census_age_seconds: V5_MAX_CENSUS_AGE_SECONDS });
  return {
    repository_root: row.repository_root,
    origin_main_sha: row.origin_main_sha,
    observed_at: observedAt,
  };
}

/**
 * The live lease census. `source` and `observed_at` are the two stated
 * invariants; every other field is a column.
 */
export async function readLiveLeaseCensus({ client, repositoryRoot = null, now = () => new Date() }) {
  const head = await readOriginHead({ client, repositoryRoot, now });
  const rows = await read(client, LEASE_SQL, []);
  return {
    source: CENSUS_SOURCE,
    observed_at: instant(now()),
    origin_main_sha: head.origin_main_sha,
    repository_root: head.repository_root,
    active_leases: rows.map(row => ({
      slice_ref: row.slice_ref,
      worktree_ref: row.worktree_ref,
      worktree_path: row.worktree_path,
      source_paths: sortedUnique(row.source_paths),
      database_resources: sortedUnique(row.database_resources),
      serialized_surfaces: sortedUnique(row.serialized_surfaces),
    })),
  };
}

/**
 * The width request. The base is NOT a field of this read: the evaluator takes
 * it from the census, so evidence is always judged against the same head the
 * admission is computed on.
 */
export async function readWidthRequest({ client, programRef, census }) {
  const stateRows = await read(client, WIDTH_STATE_SQL, [programRef]);
  if (stateRows.length === 0)
    throw typedError("DEPENDENCY_UNAVAILABLE", { missing: "ops.program_width_state", program_ref: programRef });
  const state = stateRows[0];
  const evidenceRows = await read(client, WIDTH_EVIDENCE_SQL, [programRef, census.origin_main_sha]);
  const evidence = {};
  for (const row of evidenceRows) {
    evidence[row.evidence_class] = {
      state: row.state,
      evidence_ref: row.evidence_ref,
      base_sha: row.bound_base_sha,
      observed_at: instant(row.observed_at),
    };
  }
  return {
    program_ref: state.program_ref,
    current_width: Number(state.current_width),
    requested_width: Number(state.requested_width),
    base: { origin_main_sha: census.origin_main_sha, observed_at: census.observed_at },
    evidence,
  };
}

/**
 * The slice admission request. `reuse_disposition` and `model_roles` are STORED
 * columns and not derived: the accepted slice plan holds neither, and mapping
 * concurrency_posture or risk_class onto them would be inventing the very
 * decision the module refuses to let a caller invent.
 */
export async function readSliceAdmissionRequest({
  client, sliceRef, programRef, repositoryRoot = null, now = () => new Date(),
}) {
  const census = await readLiveLeaseCensus({ client, repositoryRoot, now });
  const leaseRows = await read(client, LEASE_SQL, []);
  const lease = leaseRows.find(row => row.slice_ref === sliceRef) ?? null;
  if (lease === null)
    throw typedError("DEPENDENCY_UNAVAILABLE", { missing: "ops.slice_source_lease", slice_ref: sliceRef });
  const width = await readWidthRequest({ client, programRef, census });
  return {
    slice_ref: lease.slice_ref,
    observed_at: census.observed_at,
    reuse_disposition: lease.reuse_disposition,
    model_roles: sortedUnique(lease.model_roles),
    lease: {
      worktree_ref: lease.worktree_ref,
      worktree_path: lease.worktree_path,
      branch_ref: lease.branch_ref,
      base_sha: lease.base_commit_sha,
      source_paths: sortedUnique(lease.source_paths),
      database_disposition: lease.database_disposition,
      database_resources: sortedUnique(lease.database_resources),
      serialized_surfaces: sortedUnique(lease.serialized_surfaces),
      repository_actions: sortedUnique(lease.repository_actions),
    },
    census,
    width: {
      program_ref: width.program_ref,
      current_width: width.current_width,
      requested_width: width.requested_width,
      evidence: width.evidence,
    },
  };
}

/**
 * The checkpoint resume request, or `null` when the slice has no checkpoint row
 * at all. `null` is the honest answer for "there is nothing to resume from
 * records", and the caller reports it as `checkpoint_absent`. A checkpoint is
 * never invented.
 */
export async function readCheckpointResumeRequest({
  client, sliceRef, repositoryRoot = null, now = () => new Date(),
}) {
  const rows = await read(client, CHECKPOINT_SQL, [sliceRef]);
  if (rows.length === 0) return null;
  const row = rows[0];
  const census = await readLiveLeaseCensus({ client, repositoryRoot, now });
  return {
    slice_ref: sliceRef,
    observed_at: census.observed_at,
    checkpoint: {
      checkpoint_ref: row.checkpoint_ref,
      recorded_at: instant(row.recorded_at),
      base_sha: row.base_commit_sha,
      worktree_ref: row.worktree_ref,
      completed_step_refs: sortedUnique(row.completed_step_refs),
      next_step_ref: row.next_step_ref,
      record_evidence_refs: sortedUnique(row.record_evidence_refs),
    },
    census,
    reconstruction: {
      source: row.reconstruction_source,
      inherited_transcript_used: row.inherited_transcript_used === true,
    },
  };
}

/**
 * The release request, AND the one place the module-bound receipt snapshot is
 * filled.
 *
 * ALL SIX RECEIPTS OR NOTHING. normalizeReleaseRequest requires `receipts` to be
 * an exact() set of all six kinds and each value a non-empty string, so a
 * request with five is not a refusable input — it is a contract violation the
 * evaluator cannot be called with. A partial set therefore raises
 * DEPENDENCY_UNAVAILABLE and returns nothing at all: no partial request, no
 * placeholder, and no fabricated receipt reference.
 */
export async function readReleaseRequest({ client, releaseRef }) {
  const rows = await read(client, RECEIPT_SQL, [releaseRef]);
  const byKind = new Map(rows.map(row => [row.receipt_kind, row]));
  const missing = V5_RELEASE_RECEIPT_KINDS.filter(kind => !byKind.has(kind));
  if (missing.length > 0)
    throw typedError("DEPENDENCY_UNAVAILABLE",
      { missing: "ops.release_receipt", release_ref: releaseRef, missing_receipt_kinds: missing });

  const slices = new Set(rows.map(row => row.slice_ref));
  const heads = new Set(rows.map(row => row.head_sha));
  if (slices.size !== 1 || heads.size !== 1)
    throw typedError("DEPENDENCY_UNAVAILABLE",
      { release_ref: releaseRef, disagreeing: slices.size !== 1 ? "slice_ref" : "head_sha" });

  installReleaseReceiptSnapshot(rows.map(row => [row.receipt_ref, row.body]));

  return {
    slice_ref: [...slices][0],
    head_sha: [...heads][0],
    receipts: Object.fromEntries(
      V5_RELEASE_RECEIPT_KINDS.map(kind => [kind, byKind.get(kind).receipt_ref])),
    // STATED INVARIANT, not a store and not a route parameter. See the header.
    auto_release_requested: AUTO_RELEASE_REQUESTED,
  };
}

/**
 * The whole census read behind the route. Every answer is the evaluator's own
 * frozen result; `coverage` names any answer that could not be produced and
 * why, so an incomplete census is never mistaken for a complete one.
 */
export async function readProgramControllerCensus({
  client, actor, correlationId, program_ref: programRef = null, slice_ref: sliceRef = null,
  release_ref: releaseRef = null, repository_root: repositoryRoot = null, now = () => new Date(),
}) {
  if (!correlationId || typeof correlationId !== "string") throw typedError("INTERNAL_ERROR");
  if (!actor?.slug || !VALID_ACTORS.has(actor.slug)) throw typedError("AUTHORIZATION_REFUSED");
  if (actor.tenant !== undefined && actor.tenant !== TENANT) throw typedError("TENANT_SCOPE_REFUSED");

  const coverage = [];
  const note = (answer, code, detail) => {
    coverage.push({ answer, complete: false, missing_reason: code, detail: detail ?? null });
    return null;
  };

  let census = null;
  try {
    census = await readLiveLeaseCensus({ client, repositoryRoot, now });
    coverage.push({ answer: "census", complete: true, missing_reason: null, detail: null });
  } catch (error) {
    // The census is the base of three of the four answers, so a failure here is
    // reported once and the answers that needed it are reported as not produced.
    const code = classifyReadError(error);
    note("census", code, error.detail ?? null);
    for (const answer of ["width_answer", "admission_answer", "resume_answer"])
      note(answer, code, null);
  }

  let widthAnswer = null;
  let admissionAnswer = null;
  let resumeAnswer = null;
  let releaseAnswer = null;

  if (census && programRef) {
    try {
      widthAnswer = evaluateProgramWidth(await readWidthRequest({ client, programRef, census }));
      coverage.push({ answer: "width_answer", complete: true, missing_reason: null, detail: null });
    } catch (error) { note("width_answer", classifyReadError(error), error.detail ?? null); }
  }

  if (census && sliceRef && programRef) {
    try {
      admissionAnswer = evaluateSliceAdmission(await readSliceAdmissionRequest({
        client, sliceRef, programRef, repositoryRoot, now }));
      coverage.push({ answer: "admission_answer", complete: true, missing_reason: null, detail: null });
    } catch (error) { note("admission_answer", classifyReadError(error), error.detail ?? null); }
  }

  if (census && sliceRef) {
    try {
      const request = await readCheckpointResumeRequest({ client, sliceRef, repositoryRoot, now });
      if (request === null) {
        coverage.push({ answer: "resume_answer", complete: true, missing_reason: null,
          detail: { checkpoint_absent: true } });
      } else {
        resumeAnswer = evaluateCheckpointResume(request);
        coverage.push({ answer: "resume_answer", complete: true, missing_reason: null, detail: null });
      }
    } catch (error) { note("resume_answer", classifyReadError(error), error.detail ?? null); }
  }

  if (releaseRef) {
    try {
      releaseAnswer = evaluateReleaseEligibility(await readReleaseRequest({ client, releaseRef }));
      coverage.push({ answer: "release_answer", complete: true, missing_reason: null, detail: null });
    } catch (error) { note("release_answer", classifyReadError(error), error.detail ?? null); }
  }

  return {
    schema_version: "doctorcre-v5-program-controller-census.v1",
    correlation_id: correlationId,
    census,
    width_answer: widthAnswer,
    admission_answer: admissionAnswer,
    resume_answer: resumeAnswer,
    release_answer: releaseAnswer,
    coverage,
  };
}

/**
 * The admission decision for one slice, for the door in engineering-runtime.js.
 * Returns the evaluator's own frozen answer, or a typed dependency failure the
 * door records and returns rather than throwing away.
 */
export async function evaluateSliceAdmissionForSlice({
  client, sliceRef, programRef, repositoryRoot = null, now = () => new Date(),
}) {
  return evaluateSliceAdmission(await readSliceAdmissionRequest({
    client, sliceRef, programRef, repositoryRoot, now }));
}

/**
 * The resume decision for one slice, or `null` when the slice has no checkpoint
 * row — which leaves the replay branch exactly as it was.
 */
export async function evaluateCheckpointResumeForSlice({
  client, sliceRef, repositoryRoot = null, now = () => new Date(),
}) {
  const request = await readCheckpointResumeRequest({ client, sliceRef, repositoryRoot, now });
  return request === null ? null : evaluateCheckpointResume(request);
}
