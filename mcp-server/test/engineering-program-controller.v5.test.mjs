// V5-F02 — the deterministic program controller, proved clause by clause.
//
// The positive case comes first on purpose: a rule that only ever refuses
// cannot be told apart from a broken one, so every negative below is a single
// NAMED mutation of one clean request that allows. Each `clean*()` helper
// returns a fresh deep copy, so a mutation in one test cannot leak into
// another and quietly turn a later refusal into a pass.
//
// The admission suite feeds in the width EVIDENCE and lets admission derive the
// granted width itself; there is no width answer a test could hand it, which is
// the point — see "a forged width answer".
//
// The release suite proves a NEGATIVE first: the release decision cannot reach
// allow, because the authoritative receipt store it reads does not exist and no
// caller can supply one. The clauses that decision will be made of are proved
// separately as pure predicates over fixture receipt shapes — and a predicate,
// unlike a decision, cannot be handed a counterfeit ledger.
//
//   node --test mcp-server/test/engineering-program-controller.v5.test.mjs

import test from "node:test";
import assert from "node:assert/strict";

import { digest } from "../src/artifact-trust.js";
import { V5BoundaryError, V5_NO_EFFECTS } from "../src/global-boundaries.v5.js";
import { ENGINEERING_REPOSITORY_ACTIONS } from "../src/engineering-runtime.js";
import { GATE_ZERO_STEP_REF } from "../src/benchmark-minimum.v5.js";
import {
  V5_PROGRAM_CONTROLLER_SCHEMA_VERSION,
  V5_PROGRAM_CONTROLLER_POLICY_VERSION,
  V5_PROGRAM_CONTROLLER_DECISION_IDS,
  V5_PROGRAM_WIDTH_LADDER,
  V5_WIDTH_EVIDENCE_CLASSES,
  V5_SERIALIZED_SURFACES,
  V5_DATA_DISPOSITIONS,
  V5_REFUSED_DATA_DISPOSITIONS,
  V5_REUSE_DISPOSITIONS,
  V5_MODEL_ROLES,
  V5_DISJOINTNESS_SOURCES,
  V5_MAX_CENSUS_AGE_SECONDS,
  V5_WIDTH_CHECKS,
  V5_ADMISSION_CHECKS,
  V5_CHECKPOINT_CHECKS,
  V5_RELEASE_CHECKS,
  V5_RELEASE_RECEIPT_KINDS,
  V5_RELEASE_CHECK_RECEIPT_KINDS,
  V5_RELEASE_STATE_HOLDER_SEAM,
  V5_PRE_MERGE_RELEASE_CHECKS,
  V5_PROGRAM_CONTROLLER_REASON_IDS,
  V5_AUTO_RELEASE_UPSTREAM_STEPS,
  evaluateProgramWidth,
  evaluateSliceAdmission,
  evaluateCheckpointResume,
  evaluateReleaseEligibility,
  evaluateReleaseCheck,
  verifyReleaseReceipt,
  v5ProgramControllerPolicyPreimage,
  v5ProgramControllerPolicyDigest,
  v5ProgramControllerPolicyCanonicalBytes,
} from "../src/engineering-program-controller.v5.js";
// The whole export surface, to prove nothing on it binds a release state holder.
import * as controller from "../src/engineering-program-controller.v5.js";

const MAIN = "52fe8c39033547b806cd35a3ddb4c2d0f36eb42b";
const OLD_MAIN = "36ba65c9a1b2c3d4e5f60718293a4b5c6d7e8f90";
const OTHER_HEAD = "0123456789abcdef0123456789abcdef01234567";
const HEAD = "fedcba9876543210fedcba9876543210fedcba98";
const NOW = "2026-09-11T12:00:00Z";
const NOW_MS = Date.parse(NOW);
const at = offsetSeconds => new Date(NOW_MS + offsetSeconds * 1000).toISOString().replace(/\.\d{3}Z$/, "Z");

const copy = value => JSON.parse(JSON.stringify(value));

/** Every width evidence class accepted and bound to the current base. */
function acceptedEvidence(baseSha = MAIN) {
  return Object.fromEntries(V5_WIDTH_EVIDENCE_CLASSES.map(cls => [cls, {
    state: "accepted",
    evidence_ref: `evidence:width-${cls.replace(/_/g, "-")}`,
    base_sha: baseSha,
    observed_at: at(-60),
  }]));
}

const cleanWidth = () => copy({
  program_ref: "program:foundation-and-control-plane",
  current_width: 1,
  requested_width: 2,
  base: { origin_main_sha: MAIN, observed_at: at(-30) },
  evidence: acceptedEvidence(),
});

const cleanAdmission = () => copy({
  slice_ref: "slice:v5-f02",
  observed_at: NOW,
  reuse_disposition: "extend",
  model_roles: ["author", "reviewer"],
  lease: {
    worktree_ref: "worktree:v5-f02-slice-8dcd",
    worktree_path: "/repo/.claude/worktrees/v5-f02-slice-8dcd",
    branch_ref: "branch:v5-f02-slice-8dcd",
    base_sha: MAIN,
    source_paths: [
      "mcp-server/src/engineering-program-controller.v5.js",
      "mcp-server/test/engineering-program-controller.v5.test.mjs",
    ],
    database_disposition: "no_database",
    database_resources: [],
    serialized_surfaces: [],
    repository_actions: ["repository:create-worktree", "repository:open-pr"],
  },
  census: {
    source: "live_lease_census",
    observed_at: at(-10),
    origin_main_sha: MAIN,
    repository_root: "/repo",
    active_leases: [{
      slice_ref: "slice:v5-a04",
      worktree_ref: "worktree:v5-a04-slice-8dcd",
      worktree_path: "/repo/.claude/worktrees/v5-a04-slice-8dcd",
      source_paths: ["mcp-server/src/qualification-cost-ledger.v5.js"],
      database_resources: ["table:ops.cost_ledger"],
      serialized_surfaces: [],
    }],
  },
  width: null,
});

/**
 * The width REQUEST admission takes. There is no base here on purpose: the base
 * admission measures evidence currency against is the census's own origin/main,
 * so a caller cannot pair stale evidence with a base that flatters it.
 */
const widthRequest = (currentWidth = 1, requestedWidth = 2, evidence = acceptedEvidence()) => copy({
  program_ref: "program:foundation-and-control-plane",
  current_width: currentWidth,
  requested_width: requestedWidth,
  evidence,
});

function admissionRequest(mutate = () => {}) {
  const request = cleanAdmission();
  request.width = widthRequest();
  mutate(request);
  return request;
}

const cleanResume = () => copy({
  slice_ref: "slice:v5-f02",
  observed_at: NOW,
  checkpoint: {
    checkpoint_ref: "checkpoint:v5-f02-step-3",
    recorded_at: at(-600),
    base_sha: MAIN,
    worktree_ref: "worktree:v5-f02-slice-8dcd",
    completed_step_refs: ["step:f02-module-written", "step:f02-tests-written"],
    next_step_ref: "step:f02-mutation-checked",
    record_evidence_refs: ["evidence:f02-checkpoint-receipt"],
  },
  census: {
    source: "live_lease_census",
    observed_at: at(-10),
    origin_main_sha: MAIN,
    repository_root: "/repo",
    active_leases: [{
      slice_ref: "slice:v5-f02",
      worktree_ref: "worktree:v5-f02-slice-8dcd",
      worktree_path: "/repo/.claude/worktrees/v5-f02-slice-8dcd",
      source_paths: ["mcp-server/src/engineering-program-controller.v5.js"],
      database_resources: [],
      serialized_surfaces: [],
    }],
  },
  reconstruction: { source: "durable_records", inherited_transcript_used: false },
});

/**
 * The six receipt bodies a fully evidenced release rests on. Each one binds the
 * slice and the head it is a receipt FOR, so a receipt from another release
 * cannot be reused here however true it is of its own.
 */
const releaseBodies = () => copy({
  head_observation: {
    kind: "head_observation", slice_ref: "slice:v5-f02", head_sha: HEAD, observed_head_sha: HEAD,
  },
  merge_slot: {
    kind: "merge_slot", slice_ref: "slice:v5-f02", head_sha: HEAD,
    state: "free", held_by_slice_ref: null,
  },
  readback: {
    kind: "readback", slice_ref: "slice:v5-f02", head_sha: HEAD, state: "verified",
    main_sha: MAIN, delivered_source_digest: "sha256:aa", expected_source_digest: "sha256:aa",
  },
  required_checks: {
    kind: "required_checks", slice_ref: "slice:v5-f02", head_sha: HEAD,
    checks: [
      { name: "gates", conclusion: "success", head_sha: HEAD },
      { name: "unit", conclusion: "success", head_sha: HEAD },
    ],
  },
  revalidation: {
    kind: "revalidation", slice_ref: "slice:v5-f02", head_sha: HEAD,
    required: false, revalidated_against_sha: null, current_main_sha: MAIN,
  },
  review: {
    kind: "review", slice_ref: "slice:v5-f02", head_sha: HEAD, state: "accepted",
    reviewer_actor_id: "actor:independent-reviewer", maker_actor_id: "actor:author-seat",
    reviewed_head_sha: HEAD,
  },
});

/** The release these fixture receipts are receipts OF. */
const RELEASE_BINDING = { slice_ref: "slice:v5-f02", head_sha: HEAD };

/**
 * A release request: six content-addressed references and nothing else. The
 * reference IS the digest of the bytes it names, so a fixture that edits a body
 * after citing it breaks its own citation — which is the property
 * verifyReleaseReceipt checks.
 *
 * There is no holder here, and there is no holder ANYWHERE in this file: the
 * module binds its own, and a test that built one would be testing a door the
 * module does not have.
 */
function releaseCase(mutateBodies = () => {}, mutateRequest = () => {}) {
  const bodies = releaseBodies();
  mutateBodies(bodies);
  const receipts = {};
  for (const kind of V5_RELEASE_RECEIPT_KINDS) receipts[kind] = `receipt:${digest(bodies[kind])}`;
  const request = {
    slice_ref: "slice:v5-f02", head_sha: HEAD, receipts, auto_release_requested: false,
  };
  mutateRequest(request, bodies);
  return { request, bodies, receipts };
}

const release = (mutateBodies, mutateRequest) =>
  evaluateReleaseEligibility(releaseCase(mutateBodies, mutateRequest).request);

/** One check decided from its own fixture receipt body — the pure predicate. */
const releaseCheck = (check, mutateBodies = () => {}, binding = RELEASE_BINDING) => {
  const bodies = releaseBodies();
  mutateBodies(bodies);
  return evaluateReleaseCheck(check, bodies[V5_RELEASE_CHECK_RECEIPT_KINDS[check]], binding);
};

/** One receipt verified against the reference that cited it — the pure check. */
function releaseReceiptVerification(kind, {
  mutateBodies = () => {}, citedRef = null, servedBody = undefined, binding = RELEASE_BINDING,
} = {}) {
  const bodies = releaseBodies();
  const cited = citedRef ?? `receipt:${digest(bodies[kind])}`;
  mutateBodies(bodies);
  return verifyReleaseReceipt(kind, cited,
    servedBody === undefined ? bodies[kind] : servedBody, binding);
}

function checkRefusal(outcome, reasonId) {
  assert.equal(outcome.state, "refused");
  assert.equal(outcome.reason_id, reasonId);
  return outcome;
}

function refusalOf(answer, reasonId, blockingCheck) {
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, reasonId);
  assert.equal(answer.blocking_check, blockingCheck);
  assert.equal(answer.check_states[blockingCheck].state, "refused");
}

function throwsWith(fn, code) {
  assert.throws(fn, error => {
    assert.ok(error instanceof V5BoundaryError, `expected V5BoundaryError, got ${error?.name}`);
    assert.equal(error.code, code);
    return true;
  });
}

// ---------------------------------------------------------------------------
// 1. Earned width.
// ---------------------------------------------------------------------------

test("width: one step up the ladder on six current accepted evidence classes", () => {
  const answer = evaluateProgramWidth(cleanWidth());
  assert.equal(answer.decision, "allow");
  assert.equal(answer.reason_id, "width_granted_on_current_accepted_evidence");
  assert.equal(answer.granted_width, 2);
  assert.deepEqual(answer.missing_evidence_classes, []);
  assert.deepEqual(answer.stale_evidence_classes, []);
  assert.deepEqual(answer.checks_satisfied, [...V5_WIDTH_CHECKS]);
  assert.equal(answer.blocking_check, null);
  assert.equal(answer.schema_version, V5_PROGRAM_CONTROLLER_SCHEMA_VERSION);
  assert.equal(answer.policy_version, V5_PROGRAM_CONTROLLER_POLICY_VERSION);
  assert.deepEqual(answer.effects, V5_NO_EFFECTS);
  assert.equal(answer.decided_by, "deterministic_controller");
  assert.equal(answer.model_judgment_admitted, false);
});

test("width: two to three is also one step, on the same six classes", () => {
  const request = cleanWidth();
  request.current_width = 2;
  request.requested_width = 3;
  const answer = evaluateProgramWidth(request);
  assert.equal(answer.decision, "allow");
  assert.equal(answer.granted_width, 3);
});

test("width: staying at the current width needs no new evidence", () => {
  const request = cleanWidth();
  request.requested_width = 1;
  request.evidence = {};
  const answer = evaluateProgramWidth(request);
  assert.equal(answer.decision, "allow");
  assert.equal(answer.reason_id, "width_at_or_below_current_earned");
  assert.equal(answer.granted_width, 1);
  assert.deepEqual(answer.missing_evidence_classes, [...V5_WIDTH_EVIDENCE_CLASSES]);
});

test("width: one to three skips the ladder and refuses", () => {
  const request = cleanWidth();
  request.requested_width = 3;
  const answer = evaluateProgramWidth(request);
  refusalOf(answer, "width_ladder_step_skipped", "ladder_step");
  assert.equal(answer.granted_width, 1, "a refused increase leaves the program at its earned width");
  assert.deepEqual(answer.checks_not_reached, ["evidence_complete", "evidence_current"]);
});

test("width: five of six accepted classes is not six", () => {
  for (const held of V5_WIDTH_EVIDENCE_CLASSES) {
    const request = cleanWidth();
    delete request.evidence[held];
    const answer = evaluateProgramWidth(request);
    refusalOf(answer, "width_evidence_incomplete", "evidence_complete");
    assert.deepEqual(answer.missing_evidence_classes, [held]);
    assert.equal(answer.granted_width, 1);
  }
});

test("width: a pending or failed class is not an accepted one", () => {
  for (const state of ["pending", "failed"]) {
    const request = cleanWidth();
    request.evidence.race.state = state;
    const answer = evaluateProgramWidth(request);
    refusalOf(answer, "width_evidence_incomplete", "evidence_complete");
    assert.deepEqual(answer.missing_evidence_classes, ["race"]);
  }
});

test("width: accepted evidence bound to an older base is not current evidence", () => {
  const request = cleanWidth();
  request.evidence.merge_serialization.base_sha = OLD_MAIN;
  const answer = evaluateProgramWidth(request);
  refusalOf(answer, "width_evidence_stale_base", "evidence_current");
  assert.deepEqual(answer.stale_evidence_classes, ["merge_serialization"]);
  assert.deepEqual(answer.checks_not_reached, []);
  assert.equal(answer.check_states.evidence_complete.state, "satisfied");
});

test("width: an unknown evidence class is unreadable, not merely unaccepted", () => {
  const request = cleanWidth();
  request.evidence.vibes = { state: "accepted", evidence_ref: "evidence:vibes", base_sha: MAIN, observed_at: at(-60) };
  throwsWith(() => evaluateProgramWidth(request), "unknown_field");
});

test("width: a width off the ladder is unreadable", () => {
  const request = cleanWidth();
  request.requested_width = 4;
  throwsWith(() => evaluateProgramWidth(request), "unknown_enum_member");
});

// ---------------------------------------------------------------------------
// 2. Slice admission — refusal before edit.
// ---------------------------------------------------------------------------

test("admission: a disjoint slice inside the earned width is admitted for edit", () => {
  const answer = evaluateSliceAdmission(admissionRequest());
  assert.equal(answer.decision, "allow");
  assert.equal(answer.reason_id, "admitted_for_edit_within_earned_width");
  assert.equal(answer.admitted_for_edit, true);
  assert.equal(answer.refused_before_edit, false);
  assert.deepEqual(answer.checks_satisfied, [...V5_ADMISSION_CHECKS]);
  assert.equal(answer.granted_width, 2);
  assert.equal(answer.active_after_admission, 2);
  assert.equal(answer.reuse_disposition, "extend");
  assert.deepEqual(answer.census_binding.active_peer_slice_refs, ["slice:v5-a04"]);
  // An allow is a decision, not a lease.
  assert.equal(answer.lease_claimed, false);
  assert.equal(answer.worktree_created, false);
  assert.deepEqual(answer.effects, V5_NO_EFFECTS);
});

test("admission: disjointness claimed from the candidate grouping is refused", () => {
  const request = admissionRequest(r => { r.census.source = "candidate_group_catalog"; });
  const answer = evaluateSliceAdmission(request);
  refusalOf(answer, "disjointness_not_computed_from_live_census", "census_provenance");
  assert.equal(answer.refused_before_edit, true);
  assert.deepEqual(answer.checks_not_reached, V5_ADMISSION_CHECKS.slice(1));
  assert.deepEqual(answer.check_states.fresh_base.detail, { blocked_by: "census_provenance" });
});

test("admission: a census observed outside the window is an old story", () => {
  const request = admissionRequest(r => { r.census.observed_at = at(-(V5_MAX_CENSUS_AGE_SECONDS + 1)); });
  refusalOf(evaluateSliceAdmission(request), "census_observation_stale", "census_provenance");
  const edge = admissionRequest(r => { r.census.observed_at = at(-V5_MAX_CENSUS_AGE_SECONDS); });
  assert.equal(evaluateSliceAdmission(edge).decision, "allow", "the window boundary itself is inside the window");
});

test("admission: a census observed after the admission instant is refused", () => {
  const request = admissionRequest(r => { r.census.observed_at = at(30); });
  refusalOf(evaluateSliceAdmission(request), "census_observation_stale", "census_provenance");
});

test("admission: a stale base refuses before any edit", () => {
  const request = admissionRequest(r => { r.lease.base_sha = OLD_MAIN; });
  const answer = evaluateSliceAdmission(request);
  refusalOf(answer, "stale_base_refused_before_edit", "fresh_base");
  assert.deepEqual(answer.check_states.fresh_base.detail,
    { lease_base_sha: OLD_MAIN, origin_main_sha: MAIN });
});

test("admission: the shared repository root is not an owned writer tree", () => {
  const request = admissionRequest(r => { r.lease.worktree_path = "/repo"; });
  refusalOf(evaluateSliceAdmission(request), "shared_root_editing_refused", "owned_worktree");
});

test("admission: two slices may not share one writer tree", () => {
  const byPath = admissionRequest(r => {
    r.census.active_leases[0].worktree_path = r.lease.worktree_path;
  });
  const answer = evaluateSliceAdmission(byPath);
  refusalOf(answer, "writer_tree_shared_with_active_lease", "owned_worktree");
  assert.equal(answer.check_states.owned_worktree.detail.conflicting_slice_ref, "slice:v5-a04");

  // A tree has two names and either one being shared is the same defect. A peer
  // on a different path under the same stable reference is one tree, two
  // writers -- and it is the reference resume compares, so admission must not
  // hand out a tree whose identity is already held.
  const byRef = admissionRequest(r => {
    r.census.active_leases[0].worktree_ref = r.lease.worktree_ref;
  });
  const refAnswer = evaluateSliceAdmission(byRef);
  refusalOf(refAnswer, "writer_tree_shared_with_active_lease", "owned_worktree");
  assert.equal(refAnswer.check_states.owned_worktree.detail.worktree_ref, "worktree:v5-f02-slice-8dcd");
  assert.equal(refAnswer.check_states.owned_worktree.detail.conflicting_slice_ref, "slice:v5-a04");
});

test("admission: whole-tree and glob staging are refused as inexact", () => {
  for (const path of [".", "mcp-server/src/*.v5.js", "/etc/passwd", "../other-worktree/file.js", "mcp-server/src/"]) {
    const request = admissionRequest(r => { r.lease.source_paths = [path]; });
    const answer = evaluateSliceAdmission(request);
    refusalOf(answer, "explicit_path_staging_required", "explicit_path_staging");
    assert.equal(answer.check_states.explicit_path_staging.detail.path, path);
  }
});

test("admission: a directory lease and a file inside it are one resource with two writers", () => {
  const asParent = admissionRequest(r => {
    r.lease.source_paths = ["mcp-server/src"];
    r.census.active_leases[0].source_paths = ["mcp-server/src/qualification-cost-ledger.v5.js"];
  });
  const answer = evaluateSliceAdmission(asParent);
  refusalOf(answer, "source_path_overlap_denied", "source_path_overlap");
  assert.deepEqual(answer.check_states.source_path_overlap.detail, {
    conflicting_slice_ref: "slice:v5-a04",
    path: "mcp-server/src",
    conflicting_path: "mcp-server/src/qualification-cost-ledger.v5.js",
  });

  const asChild = admissionRequest(r => {
    r.lease.source_paths = ["mcp-server/src/engineering-program-controller.v5.js"];
    r.census.active_leases[0].source_paths = ["mcp-server/src"];
  });
  refusalOf(evaluateSliceAdmission(asChild), "source_path_overlap_denied", "source_path_overlap");

  const identical = admissionRequest(r => {
    r.census.active_leases[0].source_paths = ["mcp-server/src/engineering-program-controller.v5.js"];
  });
  refusalOf(evaluateSliceAdmission(identical), "source_path_overlap_denied", "source_path_overlap");
});

test("admission: overlap is decided by path segment, not by string prefix", () => {
  const request = admissionRequest(r => {
    r.lease.source_paths = ["mcp-server/src"];
    r.census.active_leases[0].source_paths = ["mcp-server/srcache/entry.js"];
  });
  assert.equal(evaluateSliceAdmission(request).decision, "allow",
    "'src' must not be read as containing 'srcache'");
});

test("admission: a slice's own earlier census row is not a conflict with itself", () => {
  const request = admissionRequest(r => {
    r.census.active_leases.push({
      slice_ref: r.slice_ref,
      worktree_ref: r.lease.worktree_ref,
      worktree_path: r.lease.worktree_path,
      source_paths: [...r.lease.source_paths],
      database_resources: [],
      serialized_surfaces: [],
    });
  });
  const answer = evaluateSliceAdmission(request);
  assert.equal(answer.decision, "allow");
  assert.equal(answer.active_after_admission, 2, "re-admitting one slice does not consume a second lane");
});

test("admission: a raw production data fork is refused by name, not by schema error", () => {
  for (const disposition of V5_REFUSED_DATA_DISPOSITIONS) {
    const request = admissionRequest(r => { r.lease.database_disposition = disposition; });
    const answer = evaluateSliceAdmission(request);
    refusalOf(answer, "database_disposition_refused", "database_disposition");
    assert.equal(answer.check_states.database_disposition.detail.database_disposition, disposition);
  }
});

test("admission: every admissible data disposition is admitted", () => {
  for (const disposition of V5_DATA_DISPOSITIONS) {
    const request = admissionRequest(r => { r.lease.database_disposition = disposition; });
    assert.equal(evaluateSliceAdmission(request).decision, "allow", disposition);
  }
});

test("admission: two writers may not overlap on a database resource", () => {
  const request = admissionRequest(r => {
    r.lease.database_resources = ["table:ops.cost_ledger"];
  });
  const answer = evaluateSliceAdmission(request);
  refusalOf(answer, "database_resource_overlap_denied", "database_resource_overlap");
  assert.equal(answer.check_states.database_resource_overlap.detail.database_resource, "table:ops.cost_ledger");
});

test("admission: a serialized surface has one owner at a time even when paths are disjoint", () => {
  for (const surface of V5_SERIALIZED_SURFACES) {
    const request = admissionRequest(r => {
      r.lease.serialized_surfaces = [surface];
      r.census.active_leases[0].serialized_surfaces = [surface];
    });
    const answer = evaluateSliceAdmission(request);
    refusalOf(answer, "serialized_surface_single_owner_required", "serialized_surface_ownership");
    assert.equal(answer.check_states.serialized_surface_ownership.detail.serialized_surface, surface);
  }
});

test("admission: an unowned serialized surface is admitted", () => {
  const request = admissionRequest(r => { r.lease.serialized_surfaces = ["migration_number_frontier"]; });
  assert.equal(evaluateSliceAdmission(request).decision, "allow");
});

test("admission: an action outside the registered repository actions is a generic shell", () => {
  const request = admissionRequest(r => {
    r.lease.repository_actions = ["repository:commit", "shell:rm -rf"].sort();
  });
  const answer = evaluateSliceAdmission(request);
  refusalOf(answer, "generic_shell_action_refused", "repository_action_scope");
  assert.deepEqual(answer.check_states.repository_action_scope.detail.refused_actions, ["shell:rm -rf"]);
});

test("admission: every registered repository action is admitted", () => {
  const request = admissionRequest(r => {
    r.lease.repository_actions = [...ENGINEERING_REPOSITORY_ACTIONS].sort();
  });
  assert.equal(evaluateSliceAdmission(request).decision, "allow");
});

test("admission: a third lane refuses while only two are earned", () => {
  const request = admissionRequest(r => {
    r.census.active_leases.push({
      slice_ref: "slice:v5-j201",
      worktree_ref: "worktree:v5-j201-slice-8dcd",
      worktree_path: "/repo/.claude/worktrees/v5-j201-slice-8dcd",
      source_paths: ["mcp-server/src/meeting-call-mode.v5.js"],
      database_resources: [],
      serialized_surfaces: [],
    });
  });
  const answer = evaluateSliceAdmission(request);
  refusalOf(answer, "program_width_exceeded", "program_width");
  assert.deepEqual(answer.check_states.program_width.detail,
    { active_after_admission: 3, granted_width: 2 });
});

test("admission: the same three lanes are admitted once width three is earned", () => {
  const wide = widthRequest(2, 3);
  const request = admissionRequest(r => {
    r.width = wide;
    r.census.active_leases.push({
      slice_ref: "slice:v5-j201",
      worktree_ref: "worktree:v5-j201-slice-8dcd",
      worktree_path: "/repo/.claude/worktrees/v5-j201-slice-8dcd",
      source_paths: ["mcp-server/src/meeting-call-mode.v5.js"],
      database_resources: [],
      serialized_surfaces: [],
    });
  });
  assert.equal(evaluateSliceAdmission(request).decision, "allow");
});

test("admission: a refused width request caps admission at the earned width, not the asked one", () => {
  const request = admissionRequest(r => { r.width = widthRequest(1, 3); });
  const answer = evaluateSliceAdmission(request);
  refusalOf(answer, "program_width_exceeded", "program_width");
  assert.equal(answer.granted_width, 1);
  assert.equal(answer.width_binding.reason_id, "width_ladder_step_skipped");
});

test("admission: the per-slice reuse decision cannot be skipped", () => {
  const missing = admissionRequest(r => { delete r.reuse_disposition; });
  throwsWith(() => evaluateSliceAdmission(missing), "missing_field");
  const unknown = admissionRequest(r => { r.reuse_disposition = "rewrite"; });
  throwsWith(() => evaluateSliceAdmission(unknown), "unknown_enum_member");
  for (const disposition of V5_REUSE_DISPOSITIONS) {
    const request = admissionRequest(r => { r.reuse_disposition = disposition; });
    assert.equal(evaluateSliceAdmission(request).reuse_disposition, disposition);
  }
});

test("admission: a model may declare a role and may not carry a verdict", () => {
  const roles = admissionRequest(r => { r.model_roles = [...V5_MODEL_ROLES].sort(); });
  assert.deepEqual(evaluateSliceAdmission(roles).model_roles_declared, [...V5_MODEL_ROLES].sort());
  const verdict = admissionRequest(r => { r.model_recommendation = "admit"; });
  throwsWith(() => evaluateSliceAdmission(verdict), "unknown_field");
  const override = admissionRequest(r => { r.model_roles = ["judge"]; });
  throwsWith(() => evaluateSliceAdmission(override), "unknown_enum_member");
});

test("admission: a forged width answer with every checked field correct is not an answer", () => {
  // The bypass the independent review of PR 980 proved. Take a REAL answer from
  // evaluateProgramWidth — so schema_version, policy_version and the answer name
  // are exactly right, not approximately — and raise granted_width to three.
  // Under the old contract this bought a third lane with no evidence at all.
  const genuine = evaluateProgramWidth({
    program_ref: "program:foundation-and-control-plane",
    current_width: 1,
    requested_width: 2,
    base: { origin_main_sha: MAIN, observed_at: at(-30) },
    evidence: acceptedEvidence(),
  });
  const forged = { ...genuine, granted_width: 3, decision: "allow" };
  assert.equal(forged.schema_version, V5_PROGRAM_CONTROLLER_SCHEMA_VERSION);
  assert.equal(forged.policy_version, V5_PROGRAM_CONTROLLER_POLICY_VERSION);
  assert.equal(forged.answer, "program_width");
  assert.equal(forged.granted_width, 3);

  // There is no longer a field it can arrive through: `width` is the evidence.
  throwsWith(() => evaluateSliceAdmission(admissionRequest(r => { r.width_answer = forged; })), "unknown_field");
  throwsWith(() => evaluateSliceAdmission(admissionRequest(r => { r.width = forged; })), "unknown_field");
  throwsWith(() => evaluateSliceAdmission(admissionRequest(r => { r.width.granted_width = 3; })), "unknown_field");
});

test("admission: width is derived from the evidence, so no evidence means one lane", () => {
  // Three slices want to run. The width request asks for three and says the
  // program is already at two. Without the six accepted classes it gets one.
  const request = admissionRequest(r => {
    r.width = widthRequest(2, 3, {});
    r.census.active_leases.push({
      slice_ref: "slice:v5-j201",
      worktree_ref: "worktree:v5-j201-slice-8dcd",
      worktree_path: "/repo/.claude/worktrees/v5-j201-slice-8dcd",
      source_paths: ["mcp-server/src/meeting-call-mode.v5.js"],
      database_resources: [],
      serialized_surfaces: [],
    });
  });
  const answer = evaluateSliceAdmission(request);
  refusalOf(answer, "program_width_exceeded", "program_width");
  assert.equal(answer.granted_width, 2, "the refused increase leaves the program at the width it had");
  assert.equal(answer.width_derived_by_controller, true);
  assert.equal(answer.width_binding.decision, "refuse");
  assert.equal(answer.width_binding.reason_id, "width_evidence_incomplete");
  assert.deepEqual(answer.width_binding.missing_evidence_classes, [...V5_WIDTH_EVIDENCE_CLASSES]);
});

test("admission: width evidence is current against the census head, not a base the caller picks", () => {
  // Evidence gathered on an older main is not evidence about this one, and the
  // caller has no base field to pair it with.
  const request = admissionRequest(r => { r.width = widthRequest(1, 2, acceptedEvidence(OLD_MAIN)); });
  const answer = evaluateSliceAdmission(request);
  refusalOf(answer, "program_width_exceeded", "program_width");
  assert.equal(answer.granted_width, 1);
  assert.equal(answer.width_binding.reason_id, "width_evidence_stale_base");
  assert.deepEqual(answer.width_binding.stale_evidence_classes, [...V5_WIDTH_EVIDENCE_CLASSES]);
});

test("admission: an unsorted or repeating path lease is unreadable", () => {
  const unsorted = admissionRequest(r => { r.lease.source_paths = ["b.js", "a.js"]; });
  throwsWith(() => evaluateSliceAdmission(unsorted), "unsorted_list");
  const repeating = admissionRequest(r => { r.lease.source_paths = ["a.js", "a.js"]; });
  throwsWith(() => evaluateSliceAdmission(repeating), "duplicate_member");
});

test("admission: an unknown field anywhere in the lease is unreadable", () => {
  const request = admissionRequest(r => { r.lease.skip_checks = ["source_path_overlap"]; });
  throwsWith(() => evaluateSliceAdmission(request), "unknown_field");
});

// ---------------------------------------------------------------------------
// 3. Checkpoint resume.
// ---------------------------------------------------------------------------

test("resume: a recorded checkpoint on the current base resumes at its next step", () => {
  const answer = evaluateCheckpointResume(cleanResume());
  assert.equal(answer.decision, "allow");
  assert.equal(answer.reason_id, "resumes_from_recorded_checkpoint");
  assert.equal(answer.resumes_from_records, true);
  assert.equal(answer.next_step_ref, "step:f02-mutation-checked");
  assert.equal(answer.replays_completed_steps, false);
  assert.deepEqual(answer.checks_satisfied, [...V5_CHECKPOINT_CHECKS]);
  assert.deepEqual(answer.effects, V5_NO_EFFECTS);
});

test("resume: an inherited transcript is not a record", () => {
  const fromTranscript = cleanResume();
  fromTranscript.reconstruction = { source: "inherited_transcript", inherited_transcript_used: true };
  const answer = evaluateCheckpointResume(fromTranscript);
  refusalOf(answer, "checkpoint_resume_requires_record_evidence", "checkpoint_record_evidence");
  assert.equal(answer.next_step_ref, null);

  const mixed = cleanResume();
  mixed.reconstruction.inherited_transcript_used = true;
  refusalOf(evaluateCheckpointResume(mixed), "checkpoint_resume_requires_record_evidence", "checkpoint_record_evidence");
});

test("resume: a checkpoint with no recorded evidence is a claim, not a checkpoint", () => {
  const request = cleanResume();
  request.checkpoint.record_evidence_refs = [];
  refusalOf(evaluateCheckpointResume(request), "checkpoint_resume_requires_record_evidence", "checkpoint_record_evidence");
});

test("resume: main moving under a checkpoint requires revalidation first", () => {
  const request = cleanResume();
  request.checkpoint.base_sha = OLD_MAIN;
  const answer = evaluateCheckpointResume(request);
  refusalOf(answer, "checkpoint_base_moved_revalidation_required", "checkpoint_base_current");
  assert.deepEqual(answer.check_states.checkpoint_base_current.detail,
    { checkpoint_base_sha: OLD_MAIN, origin_main_sha: MAIN });
});

test("resume: a lease on a different tree is not the tree this checkpoint was written in", () => {
  // The defect the independent review of PR 980 named: matching on slice_ref
  // alone let a slice that had lost its worktree resume into whatever tree it
  // was next admitted to, replaying a checkpoint written somewhere else.
  const request = cleanResume();
  request.census.active_leases[0].worktree_ref = "worktree:v5-f02-slice-REPLACEMENT";
  const answer = evaluateCheckpointResume(request);
  refusalOf(answer, "checkpoint_worktree_not_held", "checkpoint_lease_held");
  assert.deepEqual(answer.check_states.checkpoint_lease_held.detail, {
    slice_ref: "slice:v5-f02",
    checkpoint_worktree_ref: "worktree:v5-f02-slice-8dcd",
    held_worktree_ref: "worktree:v5-f02-slice-REPLACEMENT",
  });
  assert.equal(answer.resumes_from_records, false);
  assert.equal(answer.next_step_ref, null, "nothing is resumed into");

  // A peer holding the checkpoint's tree is not this slice holding it either.
  const peerHolds = cleanResume();
  peerHolds.census.active_leases[0].slice_ref = "slice:v5-a04";
  refusalOf(evaluateCheckpointResume(peerHolds), "checkpoint_lease_not_held", "checkpoint_lease_held");
});

test("resume: a slice holding no active lease must be re-admitted first", () => {
  const request = cleanResume();
  request.census.active_leases = [];
  refusalOf(evaluateCheckpointResume(request), "checkpoint_lease_not_held", "checkpoint_lease_held");
});

test("resume: resuming into a completed step is a duplicate queue", () => {
  const request = cleanResume();
  request.checkpoint.next_step_ref = request.checkpoint.completed_step_refs[0];
  refusalOf(evaluateCheckpointResume(request), "checkpoint_next_step_already_complete", "checkpoint_next_step");
});

test("resume: a catalog-sourced census refuses here too, before any checkpoint fact is read", () => {
  const request = cleanResume();
  request.census.source = "candidate_group_catalog";
  const answer = evaluateCheckpointResume(request);
  refusalOf(answer, "disjointness_not_computed_from_live_census", "census_provenance");
  assert.deepEqual(answer.checks_not_reached, V5_CHECKPOINT_CHECKS.slice(1));
});

// ---------------------------------------------------------------------------
// 4. Release.
// ---------------------------------------------------------------------------

// THE DECISION. The facts the release decision is made of live in an
// authoritative receipt store that DOES now exist — ops.release_receipt, added
// by WR-000110 — and the module binds its in-process holder itself. What this
// file proves is the property the store's arrival does not change: a BOUND
// holder that does not hold the cited receipt still refuses, by name, and NO
// CALLER-SUPPLIED HOLDER IS EVER CONSULTED.
//
// THE REGIME THIS FILE RUNS IN, and it is the key to reading every assertion
// below. The holder resolves from a module-level snapshot that is empty until
// the census reader installs one. THIS FILE NEVER IMPORTS THE HOLDER MODULE AND
// NEVER INSTALLS A SNAPSHOT, and node --test gives each test file its own
// process, so the snapshot is empty for the whole of this file. The holder is
// therefore BOUND AND HOLDS NOTHING: every resolveReceipt answers null and every
// check refuses `release_receipt_unresolvable`. The allow is proved elsewhere,
// on a seeded cluster, where a snapshot is legitimately installed.
//
// THAT MAKES THE FORGERY TEST BELOW STRICTLY STRONGER, and it must stay that
// way. Before the bind, a refused forgery attempt was explained by the null
// holder alone. Now the counterfeit holds a complete, working six-receipt set
// while the module-bound holder holds nothing — so every attempt still refusing
// is positive proof that the counterfeit was never consulted. Installing the
// honest snapshot in this file would make the attempts refuse for a different
// reason and destroy that proof.
//
// THE CLAUSES. Everything the decision WILL be made of the day that seam exists
// is proved below as pure predicates over fixture receipt shapes:
// verifyReleaseReceipt (is this receipt the one that was cited, of the right
// kind, for this slice and this head) and evaluateReleaseCheck (review, CI,
// exact head, serialized merge slot, descendant revalidation, readback). A
// predicate takes a receipt body, never a ledger, so proving one proves a
// clause and can never manufacture a release.

test("release: the decision cannot reach allow, and names the receipt it is owed", () => {
  const answer = release();
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "release_receipt_unresolvable");
  assert.equal(answer.blocking_check, V5_RELEASE_CHECKS[0]);
  assert.equal(answer.released, false);
  assert.equal(answer.merge_admitted, false);
  assert.equal(answer.state_holder_bound, true);
  assert.equal(answer.state_holder_is_caller_supplied, false);
  assert.equal(answer.release_state_holder_seam, V5_RELEASE_STATE_HOLDER_SEAM);
  // The detail is still proved — the honest one. A bound holder that resolves
  // nothing names the receipt it could not resolve, not a seam it is owed.
  const blockingDetail = answer.check_states[V5_RELEASE_CHECKS[0]].detail;
  assert.equal(blockingDetail.receipt_kind, V5_RELEASE_CHECK_RECEIPT_KINDS[V5_RELEASE_CHECKS[0]]);
  assert.equal(blockingDetail.receipt_ref,
    releaseCase().request.receipts[V5_RELEASE_CHECK_RECEIPT_KINDS[V5_RELEASE_CHECKS[0]]]);
  assert.equal(blockingDetail.required_seam, undefined,
    "the seam is bound, so no check is owed one");
  assert.equal(answer.observed_head_sha, null, "no observation is restated as a fact");
  assert.deepEqual(answer.receipt_refs_verified, []);
  assert.equal(answer.caller_stated_release_facts, false);
  assert.deepEqual(answer.checks_not_reached, V5_RELEASE_CHECKS.slice(1));
  // The references are still reported, so a reader can see what WOULD be
  // resolved. Citing a receipt is not evidence of one.
  assert.deepEqual(Object.keys(answer.receipt_refs_cited).sort(),
    [...V5_RELEASE_RECEIPT_KINDS].sort());
});

test("release: no caller-supplied input reaches allow while the bound holder holds no receipt", () => {
  // Every route a caller has: perfect evidence, the counterfeit holder the
  // second review of PR 980 built, the holder smuggled in as a request field,
  // as a global, or onto the prototype of the request. None of them decides
  // anything, because the module never reads a holder from a caller at all.
  //
  // THE COUNTERFEIT BELOW IS A COMPLETE, WORKING SIX-RECEIPT SET, and it must
  // stay working: the module's own holder is bound and empty in this file, so
  // every attempt refusing on RECEIPT grounds is proof that the counterfeit was
  // never consulted rather than merely out-voted.
  const counterfeitHolder = () => {
    const store = new Map();
    const bodies = releaseBodies();
    for (const kind of V5_RELEASE_RECEIPT_KINDS) store.set(`receipt:${digest(bodies[kind])}`, bodies[kind]);
    return { resolveReceipt: receiptRef => store.get(receiptRef) ?? null };
  };

  const attempts = [
    ["perfect evidence, honestly cited", () => release()],
    ["a second argument carrying a working counterfeit holder",
      () => evaluateReleaseEligibility(releaseCase().request, counterfeitHolder())],
    ["a holder on the request object",
      () => release(() => {}, request => { request.state_holder = counterfeitHolder(); })],
    ["a holder under the old parameter name",
      () => release(() => {}, request => { request.stateHolder = counterfeitHolder(); })],
    ["a holder among the receipts",
      () => release(() => {}, request => { request.receipts.state_holder = counterfeitHolder(); })],
    ["a resolveReceipt method on the request",
      () => release(() => {}, request => { request.resolveReceipt = counterfeitHolder().resolveReceipt; })],
    ["a holder on the request's prototype",
      () => evaluateReleaseEligibility(Object.assign(
        Object.create({ resolveReceipt: counterfeitHolder().resolveReceipt }),
        releaseCase().request))],
    ["a holder planted on globalThis", () => {
      globalThis.V5_RELEASE_STATE_HOLDER = counterfeitHolder();
      try { return release(); } finally { delete globalThis.V5_RELEASE_STATE_HOLDER; }
    }],
    ["a receipt claiming its own release is already complete",
      () => release(bodies => { bodies.readback.main_sha = MAIN; },
        request => { request.auto_release_requested = true; })],
  ];

  for (const [what, attempt] of attempts) {
    let answer = null;
    try {
      answer = attempt();
    } catch (error) {
      // Refusing to READ the input is also not an allow; it must be the
      // module's own boundary error and not a crash.
      assert.ok(error instanceof V5BoundaryError, `${what}: ${error}`);
      continue;
    }
    assert.equal(answer.decision, "refuse", `${what} produced a decision other than refuse`);
    assert.equal(answer.reason_id, "release_receipt_unresolvable", what);
    assert.equal(answer.released, false, what);
    assert.equal(answer.merge_admitted, false, what);
    assert.equal(answer.state_holder_bound, true, what);
    // The two caller-facing statements the bind is what makes provable: the
    // holder that decided was the module's, and nothing the caller offered was
    // ever verified.
    assert.equal(answer.state_holder_is_caller_supplied, false, what);
    assert.equal(answer.receipt_refs_verified.length, 0, what);
    assert.deepEqual(answer.receipt_refs_verified, [], what);
  }

  // And after all nine: the module-bound holder STILL resolves nothing. The
  // honest request, evaluated once more, verifies zero receipts and refuses on
  // the first one it could not resolve — so no attempt installed anything into
  // the holder, and the counterfeit is proved UNCONSULTED rather than merely
  // outvoted. (This file never imports the holder module; that it holds nothing
  // is exactly what makes these refusals evidence.)
  const after = release();
  assert.equal(after.receipt_refs_verified.length, 0,
    "an attempt installed something into the module-bound holder");
  assert.equal(after.reason_id, "release_receipt_unresolvable");
  assert.equal(after.state_holder_bound, true);
  // The counterfeit really was a complete, working set, which is the other half
  // of why the refusals mean something.
  const counterfeit = counterfeitHolder();
  const bodies = releaseBodies();
  for (const kind of V5_RELEASE_RECEIPT_KINDS)
    assert.ok(counterfeit.resolveReceipt(`receipt:${digest(bodies[kind])}`),
      `the counterfeit holder did not actually hold ${kind}`);
});

test("release: the state holder is not an argument, and nothing exported binds one", () => {
  assert.equal(evaluateReleaseEligibility.length, 1);
  throwsWith(() => evaluateReleaseEligibility(releaseCase().request, { resolveReceipt: () => null }),
    "release_state_holder_is_not_an_argument");
  // Even a second argument that is plainly not a holder is unreadable: the
  // module refuses the SHAPE of being handed one, not a particular fake.
  throwsWith(() => evaluateReleaseEligibility(releaseCase().request, null),
    "release_state_holder_is_not_an_argument");
  // The seam is a name, and the only exported thing about the holder. No
  // exported function offers to bind, set or inject one.
  assert.equal(typeof V5_RELEASE_STATE_HOLDER_SEAM, "string");
  assert.match(V5_RELEASE_STATE_HOLDER_SEAM, /^seam:[a-z0-9-]+$/);
  const binders = Object.entries(controller)
    .filter(([name, value]) => typeof value === "function" && /holder|ledger|inject/i.test(name));
  assert.deepEqual(binders.map(([name]) => name), [],
    "an exported function naming the holder would be a door back into the hole");
  // The seam is bound, so an allow is reachable — from six receipts the bound
  // holder resolves and from nothing else. The middle assertion is the one that
  // must never move: no caller may supply the holder, bound or not.
  assert.equal(v5ProgramControllerPolicyPreimage().release_allow_reachable, true);
  assert.equal(v5ProgramControllerPolicyPreimage().caller_may_supply_release_state_holder, false);
  assert.equal(v5ProgramControllerPolicyPreimage().release_state_holder_available, true);
});

test("release: auto-release is unavailable and names the receipts it waits on", () => {
  for (const requested of [false, true]) {
    const answer = release(() => {}, request => { request.auto_release_requested = requested; });
    assert.equal(answer.auto_release_state, "unavailable");
    assert.equal(answer.auto_release_decided, false);
    assert.equal(answer.auto_release_unavailable_reason_id, "auto_release_upstream_receipt_absent");
    assert.deepEqual(answer.auto_release_missing_steps, [...V5_AUTO_RELEASE_UPSTREAM_STEPS]);
    assert.ok(answer.auto_release_missing_steps.includes(GATE_ZERO_STEP_REF));
  }
});

test("release: a caller cannot assert the upstream receipts it does not have", () => {
  const { request } = releaseCase();
  request.gate_zero_receipt = { accepted: true };
  throwsWith(() => evaluateReleaseEligibility(request), "unknown_field");

  const extra = releaseCase();
  extra.request.receipts.gate_zero = "receipt:sha256:00";
  throwsWith(() => evaluateReleaseEligibility(extra.request), "unknown_field");
});

// ---------------------------------------------------------------------------
// The receipt-verification clause, as a pure predicate over fixture shapes.
// ---------------------------------------------------------------------------

test("receipt: a receipt cited by its own digest, for this slice and this head, verifies", () => {
  for (const kind of V5_RELEASE_RECEIPT_KINDS) {
    const outcome = releaseReceiptVerification(kind);
    assert.equal(outcome.ok, true, `${kind} did not verify`);
    assert.equal(outcome.body.kind, kind);
  }
});

test("receipt: a receipt that no longer hashes to the reference cited is refused", () => {
  // The forger's move: cite a genuine receipt reference, serve a different body.
  // Every field a reader would compare still matches; the bytes do not.
  const outcome = releaseReceiptVerification("review", {
    mutateBodies: bodies => { bodies.review.reviewer_actor_id = "actor:author-seat-in-a-hat"; },
  });
  assert.equal(outcome.ok, false);
  assert.equal(outcome.reasonId, "release_receipt_digest_mismatch");
  assert.equal(outcome.detail.receipt_kind, "review");
  assert.notEqual(outcome.detail.resolved_digest, null);
});

test("receipt: a reference the state holder does not hold resolves to nothing", () => {
  for (const servedBody of [null, "a receipt, honest", [], 42, new Map()]) {
    const outcome = releaseReceiptVerification("required_checks", { servedBody });
    assert.equal(outcome.ok, false);
    assert.equal(outcome.reasonId, "release_receipt_unresolvable");
  }
});

test("receipt: a receipt cited by name rather than by its own digest is refused", () => {
  for (const citedRef of [
    "receipt:ci-run-980", `receipt:sha256:${"a".repeat(63)}`, `receipt:sha256:${"A".repeat(64)}`,
    `sha256:${"a".repeat(64)}`, "", 17, `receipt:sha256:${"a".repeat(65)}`,
  ]) {
    const outcome = releaseReceiptVerification("head_observation", { citedRef });
    assert.equal(outcome.ok, false, `${citedRef} was accepted as a reference`);
    assert.equal(outcome.reasonId, "release_receipt_not_content_addressed");
  }
});

test("receipt: a receipt of another kind does not answer the check it was filed under", () => {
  const bodies = releaseBodies();
  const outcome = verifyReleaseReceipt("merge_slot", `receipt:${digest(bodies.review)}`,
    bodies.review, RELEASE_BINDING);
  assert.equal(outcome.ok, false);
  assert.equal(outcome.reasonId, "release_receipt_kind_mismatch");
  assert.equal(outcome.detail.resolved_kind, "review");
});

test("receipt: a receipt bound to another slice or another head proves nothing about this one", () => {
  const otherSlice = releaseReceiptVerification("review", {
    binding: { slice_ref: "slice:v5-a04", head_sha: HEAD },
  });
  assert.equal(otherSlice.reasonId, "release_receipt_bound_to_other_slice");
  assert.equal(otherSlice.detail.receipt_slice_ref, "slice:v5-f02");

  const otherHead = releaseReceiptVerification("review", {
    binding: { slice_ref: "slice:v5-f02", head_sha: OTHER_HEAD },
  });
  assert.equal(otherHead.reasonId, "release_receipt_bound_to_other_head");
  assert.equal(otherHead.detail.receipt_head_sha, HEAD);
});

test("receipt: a receipt the module cannot read fails closed rather than deciding", () => {
  // The body must genuinely hash to the reference that cites it, or the digest
  // check answers first and the shape is never reached. So a receipt the store
  // really holds, that this module cannot read, is the case under test.
  const unreadable = mutate => {
    const bodies = releaseBodies();
    mutate(bodies);
    return () => verifyReleaseReceipt("review", `receipt:${digest(bodies.review)}`,
      bodies.review, RELEASE_BINDING);
  };
  throwsWith(unreadable(bodies => { delete bodies.review.maker_actor_id; }), "missing_field");
  throwsWith(unreadable(bodies => { bodies.review.approved = true; }), "unknown_field");
  throwsWith(unreadable(bodies => { bodies.review.state = "rubber_stamped"; }), "unknown_enum_member");
  throwsWith(() => verifyReleaseReceipt("deploy_approval", `receipt:sha256:${"0".repeat(64)}`,
    {}, RELEASE_BINDING), "unknown_enum_member");
});

// ---------------------------------------------------------------------------
// The six release clauses, as pure predicates over fixture receipt shapes.
// Deciding a clause is not deciding a release: see the decision tests above.
// ---------------------------------------------------------------------------

test("clause: every release check is decided from its own kind of receipt", () => {
  assert.deepEqual(Object.keys(V5_RELEASE_CHECK_RECEIPT_KINDS).sort(), [...V5_RELEASE_CHECKS].sort());
  assert.deepEqual([...new Set(Object.values(V5_RELEASE_CHECK_RECEIPT_KINDS))].sort(),
    [...V5_RELEASE_RECEIPT_KINDS].sort());
  for (const check of V5_RELEASE_CHECKS) {
    const outcome = releaseCheck(check);
    assert.equal(outcome.state, "satisfied", `${check} refused a clean receipt`);
    assert.equal(outcome.check, check);
  }
});

test("clause: an absent or rejecting review refuses, and the maker may not be the reviewer", () => {
  const absent = releaseCheck("independent_review", bodies => {
    bodies.review.state = "absent";
    bodies.review.reviewer_actor_id = null;
    bodies.review.reviewed_head_sha = null;
  });
  checkRefusal(absent, "release_review_not_accepted");
  assert.equal(absent.detail.review_state, "absent");

  checkRefusal(releaseCheck("independent_review", bodies => { bodies.review.state = "requested_changes"; }),
    "release_review_not_accepted");
  checkRefusal(releaseCheck("independent_review",
    bodies => { bodies.review.reviewer_actor_id = bodies.review.maker_actor_id; }),
    "release_reviewer_not_independent");
  checkRefusal(releaseCheck("independent_review", bodies => { bodies.review.reviewed_head_sha = OTHER_HEAD; }),
    "release_review_bound_to_other_head");
});

test("clause: every required check must be green, on this head, and there must be one", () => {
  for (const conclusion of ["failure", "cancelled", "pending"]) {
    checkRefusal(releaseCheck("green_required_checks",
      bodies => { bodies.required_checks.checks[1].conclusion = conclusion; }),
      "release_required_check_not_green");
  }
  const none = checkRefusal(releaseCheck("green_required_checks",
    bodies => { bodies.required_checks.checks = []; }), "release_required_check_not_green");
  assert.deepEqual(none.detail.required_checks, []);

  const otherHead = checkRefusal(releaseCheck("green_required_checks",
    bodies => { bodies.required_checks.checks[0].head_sha = OTHER_HEAD; }),
    "release_required_check_bound_to_other_head");
  assert.deepEqual(otherHead.detail.bound_to_other_head, ["gates"]);
});

test("clause: a branch that moved after review refuses on the exact head", () => {
  const moved = checkRefusal(releaseCheck("exact_head",
    bodies => { bodies.head_observation.observed_head_sha = OTHER_HEAD; }), "release_head_moved");
  assert.equal(moved.detail.observed_head_sha, OTHER_HEAD);
});

test("clause: merge is serialized — another slice holding the slot refuses", () => {
  const held = checkRefusal(releaseCheck("serialized_merge_slot", bodies => {
    bodies.merge_slot.state = "held";
    bodies.merge_slot.held_by_slice_ref = "slice:v5-a04";
  }), "serialized_merge_slot_held");
  assert.equal(held.detail.held_by_slice_ref, "slice:v5-a04");

  const mine = releaseCheck("serialized_merge_slot", bodies => {
    bodies.merge_slot.state = "held";
    bodies.merge_slot.held_by_slice_ref = "slice:v5-f02";
  });
  assert.equal(mine.state, "satisfied", "holding one's own slot is not contention");
});

test("clause: an affected descendant is rebased and revalidated before its delivery", () => {
  checkRefusal(releaseCheck("descendant_revalidation",
    bodies => { bodies.revalidation.required = true; }), "descendant_revalidation_required");
  checkRefusal(releaseCheck("descendant_revalidation", bodies => {
    bodies.revalidation.required = true;
    bodies.revalidation.revalidated_against_sha = OLD_MAIN;
  }), "descendant_revalidation_required");
  assert.equal(releaseCheck("descendant_revalidation", bodies => {
    bodies.revalidation.required = true;
    bodies.revalidation.revalidated_against_sha = MAIN;
  }).state, "satisfied");
});

test("clause: a merge is not a delivery until what landed is read back from main", () => {
  for (const state of ["absent", "pending"]) {
    checkRefusal(releaseCheck("merged_readback", bodies => {
      bodies.readback.state = state;
      bodies.readback.main_sha = null;
      bodies.readback.delivered_source_digest = null;
      bodies.readback.expected_source_digest = null;
    }), "release_readback_not_verified");
  }
  const mismatch = checkRefusal(releaseCheck("merged_readback",
    bodies => { bodies.readback.delivered_source_digest = "sha256:bb"; }),
    "release_readback_digest_mismatch");
  assert.equal(mismatch.detail.delivered_source_digest, "sha256:bb");
});

test("clause: a receipt shape the module cannot read fails closed rather than deciding", () => {
  throwsWith(() => evaluateReleaseCheck("merged_readback", releaseBodies().review, RELEASE_BINDING),
    "unknown_field");
  throwsWith(() => releaseCheck("independent_review", bodies => { bodies.review.approved = true; }),
    "unknown_field");
  throwsWith(() => releaseCheck("independent_review", bodies => { delete bodies.review.maker_actor_id; }),
    "missing_field");
  throwsWith(() => evaluateReleaseCheck("deploy_it", releaseBodies().review, RELEASE_BINDING),
    "unknown_enum_member");
  throwsWith(() => releaseCheck("independent_review", () => {}, { slice_ref: "slice:v5-f02" }),
    "missing_field");
  throwsWith(() => releaseCheck("independent_review", () => {},
    { slice_ref: "slice:v5-f02", head_sha: HEAD, actor: "joe" }), "unknown_field");
  assert.throws(() => releaseCheck("serialized_merge_slot",
    bodies => { bodies.merge_slot.held_by_slice_ref = "slice:v5-a04"; }),
    error => error instanceof V5BoundaryError && error.code === "free_slot_names_a_holder");
  assert.throws(() => releaseCheck("independent_review", bodies => {
    bodies.review.state = "absent";
    bodies.review.reviewed_head_sha = null;
  }), error => error instanceof V5BoundaryError && error.code === "absent_review_states_a_reviewer");
});

// ---------------------------------------------------------------------------
// The policy identity.
// ---------------------------------------------------------------------------

test("policy: the digest is deterministic and covers every closed vocabulary", () => {
  assert.equal(v5ProgramControllerPolicyDigest(), v5ProgramControllerPolicyDigest());
  const preimage = v5ProgramControllerPolicyPreimage();
  assert.deepEqual(JSON.parse(v5ProgramControllerPolicyCanonicalBytes()), preimage);
  for (const vocabulary of [
    V5_PROGRAM_WIDTH_LADDER, V5_WIDTH_EVIDENCE_CLASSES, V5_SERIALIZED_SURFACES,
    V5_DATA_DISPOSITIONS, V5_REUSE_DISPOSITIONS, V5_MODEL_ROLES,
    V5_DISJOINTNESS_SOURCES, V5_PROGRAM_CONTROLLER_REASON_IDS,
    V5_ADMISSION_CHECKS, V5_RELEASE_CHECKS,
  ]) {
    const serialized = JSON.stringify([...vocabulary]);
    assert.ok(v5ProgramControllerPolicyCanonicalBytes().includes(serialized.slice(1, -1)),
      `vocabulary missing from the policy preimage: ${serialized.slice(0, 60)}`);
  }
  assert.equal(preimage.auto_release_state, "unavailable");
  assert.equal(preimage.caller_may_assert_upstream_receipt, false);
  assert.equal(preimage.caller_may_claim_catalog_disjointness, false);
  assert.equal(preimage.path_overlap_is_segment_wise, true);
  assert.equal(preimage.claims_lease, false);
});

test("policy: the reason vocabulary is sorted, unique, and fully exercised", () => {
  const sorted = [...V5_PROGRAM_CONTROLLER_REASON_IDS].sort();
  assert.deepEqual([...V5_PROGRAM_CONTROLLER_REASON_IDS], sorted);
  assert.equal(new Set(V5_PROGRAM_CONTROLLER_REASON_IDS).size, V5_PROGRAM_CONTROLLER_REASON_IDS.length);
  // The four decisions this module makes, each carrying its own answer name.
  const answers = [
    evaluateProgramWidth(cleanWidth()),
    evaluateSliceAdmission(admissionRequest()),
    evaluateCheckpointResume(cleanResume()),
    releaseCase().request && release(),
  ];
  assert.deepEqual(answers.map(answer => answer.answer),
    ["program_width", "slice_admission", "checkpoint_resume", "release_eligibility"]);
  for (const answer of answers) {
    assert.ok(V5_PROGRAM_CONTROLLER_REASON_IDS.includes(answer.reason_id));
    assert.equal(answer.decided_by, "deterministic_controller");
    assert.equal(answer.model_judgment_admitted, false);
    assert.ok(Object.isFrozen(answer));
  }
});

test("policy: the nine catalog decisions are carried in the binding", () => {
  assert.deepEqual([...V5_PROGRAM_CONTROLLER_DECISION_IDS], [
    "Q012.D2", "Q025.D1", "Q026.D1", "Q037.D1", "Q038.D1",
    "Q039.D1", "Q044.D1", "Q111.D1", "Q112.D1",
  ]);
  assert.deepEqual(v5ProgramControllerPolicyPreimage().decision_ids,
    [...V5_PROGRAM_CONTROLLER_DECISION_IDS].sort());
});
