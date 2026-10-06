// V5-F08 second half — the restore exercise, the recovery matrix cells, the
// outbound-queue reconciliation and the degraded-mode projection, proved
// criterion by criterion.
//
// Every negative is ONE named mutation of a clean request that passes, and every
// refusal asserts the reason id AND the check that decided it, so a request that
// fails for the wrong reason fails here. Boundaries are tested on both sides:
// exactly at a bound passes, one second past it fails.
//
// The RPO fixtures are shaped exactly like tools/pitr-restore-proof.py prove's
// output; the adapter tests read the real ops/config/business-calendar.us-federal.json
// (its digest must equal the module's pin), so the config file is what is
// tested; the CLI tests run the real bin with the real clock.
//
//   node --test mcp-server/test/recovery-matrix.v5.test.mjs

import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

import { digest } from "../src/artifact-trust.js";

import { V5BoundaryError, V5_NO_EFFECTS } from "../src/global-boundaries.v5.js";
import {
  V5_RESTORE_EXERCISE_CHECKS,
  V5_RECOVERY_MATRIX_CELLS,
  V5_DEGRADED_MODES,
  V5_BUSINESS_CALENDAR_DIGEST,
  V5_EVIDENCE_VERIFIERS,
  V5_REVERIFY_MAX_AGE_SECONDS,
  evaluateRestoreExercise,
  evaluateRecoveryMatrix,
  evaluateOutboundQueueRelease,
  nextBusinessDayDeadline,
  v5DegradedModeProjection,
  v5OutboundCensusDigest,
  v5RecoveryMatrixPolicyDigest,
  v5RecoveryMatrixPolicyCanonicalBytes,
  v5RecoveryMatrixPolicyPreimage,
} from "../src/recovery-matrix.v5.js";
import {
  V5_RESTORE_AND_RECOVERY_MATRIX_SEAM,
  V5_OUTBOUND_RECONCILIATION_SEAM,
} from "../src/backup-quarantine.v5.js";

const D = ch => `sha256:${ch.repeat(64)}`;
const clone = v => JSON.parse(JSON.stringify(v));
const W = (rows, ch) => ({ rows, content_digest: D(ch) });
const at = iso => ({ now_ms: Date.parse(iso) });
const wholeSecond = ms => new Date(Math.floor(ms / 1000) * 1000).toISOString().replace(".000Z", "Z");

/**
 * What the verify step does (lib/recovery_evidence.py bind): stamp re-derived
 * facts with { verifier, verified_at, facts_digest }. Tests stamp at the
 * evaluator's own clock; the binding's own refusals are tested directly below.
 */
function stamp(facts, kind, clock, verifiedAtMs = clock.now_ms) {
  const { verification: _old, ...rest } = facts;
  return { ...rest, verification: { verifier: V5_EVIDENCE_VERIFIERS[kind], verified_at: wholeSecond(verifiedAtMs), facts_digest: digest(rest) } };
}
const R_NOW = at("2026-09-24T10:30:00Z");
const R = (req, clock = R_NOW) => evaluateRestoreExercise(stamp(req, "restore_exercise", clock), clock);
function stampCells(m, clock) {
  const c = clone(m);
  if (c.cells?.record_layer_rpo) c.cells.record_layer_rpo = stamp(c.cells.record_layer_rpo, "record_layer_rpo", clock);
  for (const name of ["independent_daily_restorable_copy", "core_rto"]) {
    if (c.cells?.[name]?.restore_exercise) c.cells[name].restore_exercise = stamp(c.cells[name].restore_exercise, "restore_exercise", clock);
  }
  return c;
}
const M = (m, clock) => evaluateRecoveryMatrix(stampCells(m, clock), clock);
const O = (req, clock) => (clock && typeof clock.now_ms === "number"
  ? evaluateOutboundQueueRelease(stamp(req, "outbound_census", clock), clock)
  : evaluateOutboundQueueRelease(req, clock));

function receipt() {
  return {
    receipt_kind: "restore-exercise-receipt.v1",
    target_kind: "disposable_branch",
    copy: {
      copy_id: "carr-backup-20260924-run-101-attempt-1",
      custody_domain: "github-actions-artifacts",
      primary_domain: "neon-primary",
      producer_id: "backup-nightly-workflow",
      produced_at: "2026-09-24T03:00:00Z",
      recorded_artifact_digest: D("a"),
      recorded_digest_source: { kind: "github_actions_backup_check", check_run_id: 555, workflow_run_id: 101 },
      store_readback_digest: D("a"),
    },
    oracle_id: "restore-rehearse",
    observed_artifact_digest: D("a"),
    artifact_watermark: { "public.deal": W(40, "d"), "public.party": W(120, "e"), "ops.run": W(9, "f") },
    restored_watermark: { "ops.run": W(9, "f"), "public.party": W(120, "e"), "public.deal": W(40, "d") },
    started_at: "2026-09-24T10:00:00Z",
    finished_at: "2026-09-24T10:20:00Z",
    target_point: { head_lsn: "16/B374D848", parent_lsn: "16/B374E000", gap_bytes: 0x7B8, settle_seconds: 30 },
  };
}

function assertRefused(result, reason, check) {
  assert.equal(result.decision, "fail");
  assert.equal(result.reason_id, reason);
  assert.equal(result.checks[check], "fail");
  const i = V5_RESTORE_EXERCISE_CHECKS.indexOf(check);
  for (const later of V5_RESTORE_EXERCISE_CHECKS.slice(i + 1)) assert.equal(result.checks[later], "not_reached");
  for (const earlier of V5_RESTORE_EXERCISE_CHECKS.slice(0, i)) assert.equal(result.checks[earlier], "pass");
}

// --- item 4: restore from independently controlled copy, exact watermark/hash

test("item 4: an exact restore of an independently held copy passes every check", () => {
  const r = R(receipt());
  assert.equal(r.decision, "pass");
  assert.equal(r.reason_id, "restore_exercise_exact");
  for (const c of V5_RESTORE_EXERCISE_CHECKS) assert.equal(r.checks[c], "pass");
  assert.equal(r.tables_compared, 3);
  assert.deepEqual(r.watermark_mismatches, []);
  assert.equal(r.restore_seconds, 1200);
  assert.deepEqual(r.recorded_digest_source, { kind: "github_actions_backup_check", check_run_id: 555, workflow_run_id: 101 });
  assert.deepEqual(r.effects, V5_NO_EFFECTS);
});

test("item 4: a restore aimed at production is refused before anything else is read", () => {
  const req = receipt();
  req.target_kind = "production";
  assertRefused(R(req), "restore_target_is_production", "restore_target_not_production");
  req.target_kind = "unstated";
  assertRefused(R(req), "restore_target_unstated", "restore_target_not_production");
  req.target_kind = "staging";
  assert.equal(R(req).decision, "pass");
});

test("item 4: a copy held in the primary's own domain is not independently controlled", () => {
  const req = receipt();
  req.copy.custody_domain = "neon-primary";
  assertRefused(R(req), "copy_not_independently_controlled", "copy_independently_controlled");
});

test("item 4: the producer cannot be its own restore oracle", () => {
  const req = receipt();
  req.oracle_id = "backup-nightly-workflow";
  assertRefused(R(req), "oracle_is_the_producer", "oracle_independent_of_producer");
});

test("item 4: a digest the operator typed in is not the producer's record", () => {
  const req = receipt();
  req.copy.recorded_digest_source.kind = "operator_supplied";
  assertRefused(R(req), "recorded_digest_not_from_producer", "recorded_digest_from_producer");
  const unknown = receipt();
  unknown.copy.recorded_digest_source.kind = "a_file_i_wrote";
  assert.throws(() => R(unknown), e => e.code === "unknown_state");
  const noSource = receipt();
  delete noSource.copy.recorded_digest_source;
  assert.throws(() => R(noSource), e => e.code === "missing_field");
});

test("item 4: the store's own digest must equal the producer's record", () => {
  const req = receipt();
  req.copy.store_readback_digest = D("c");
  assertRefused(R(req), "store_readback_mismatch", "store_readback_matches_record");
});

test("item 4: a restored artifact whose hash differs from the recorded one fails", () => {
  const req = receipt();
  req.observed_artifact_digest = D("b");
  assertRefused(R(req), "artifact_hash_mismatch", "artifact_hash_exact");
});

test("item 4: one row off, one row changed, one table missing or one table extra is not exact", () => {
  const off = receipt();
  off.restored_watermark["public.party"].rows = 119;
  const r1 = R(off);
  assertRefused(r1, "watermark_mismatch", "watermark_exact");
  assert.deepEqual(r1.watermark_mismatches, [{ table: "public.party", artifact_rows: 120, restored_rows: 119, content_differs: false }]);

  const changed = receipt();
  changed.restored_watermark["public.party"].content_digest = D("9");
  const r2 = R(changed);
  assertRefused(r2, "watermark_mismatch", "watermark_exact");
  assert.deepEqual(r2.watermark_mismatches, [{ table: "public.party", artifact_rows: 120, restored_rows: 120, content_differs: true }]);

  const missing = receipt();
  delete missing.restored_watermark["ops.run"];
  assert.deepEqual(R(missing).watermark_mismatches,
    [{ table: "ops.run", artifact_rows: 9, restored_rows: null, content_differs: true }]);

  const extra = receipt();
  extra.restored_watermark["public.stray"] = W(0, "0");
  assertRefused(R(extra), "watermark_mismatch", "watermark_exact");
});

test("item 4: a watermark must be non-empty, whole, non-negative and digest-bearing", () => {
  const empty = receipt(); empty.artifact_watermark = {};
  assert.throws(() => R(empty), e => e.code === "missing_field");
  const negative = receipt(); negative.restored_watermark["ops.run"].rows = -1;
  assert.throws(() => R(negative), e => e.code === "invalid_shape");
  const fractional = receipt(); fractional.restored_watermark["ops.run"].rows = 1.5;
  assert.throws(() => R(fractional), e => e.code === "invalid_shape");
  const countOnly = receipt(); countOnly.restored_watermark["ops.run"] = 9;
  assert.throws(() => R(countOnly), e => e.code === "invalid_shape");
  const noDigest = receipt(); delete noDigest.restored_watermark["ops.run"].content_digest;
  assert.throws(() => R(noDigest), e => e.code === "missing_field");
  const badName = receipt(); badName.restored_watermark.nodot = W(1, "1");
  assert.throws(() => R(badName), e => e.code === "invalid_identifier");
  const zero = receipt(); zero.artifact_watermark["ops.run"].rows = 0; zero.restored_watermark["ops.run"].rows = 0;
  assert.equal(R(zero).decision, "pass");
});

test("item 4: a restore that finishes before it starts, or starts before the copy existed, is refused", () => {
  const req = receipt();
  req.finished_at = "2026-09-24T09:59:59Z";
  assertRefused(R(req), "restore_interval_invalid", "restore_interval_well_formed");
  const early = receipt();
  early.started_at = "2026-09-24T02:00:00Z";
  assertRefused(R(early), "restore_interval_invalid", "restore_interval_well_formed");
  const instant = receipt();
  instant.finished_at = instant.started_at;
  assert.equal(R(instant).decision, "pass");
  const atProduction = receipt();
  atProduction.started_at = atProduction.copy.produced_at;
  assert.equal(R(atProduction).decision, "pass");
});

test("item 4: no caller field can skip a check or assert trust", () => {
  for (const key of ["trusted", "skip_checks", "already_verified"]) {
    const req = { ...receipt(), [key]: true };
    assert.throws(() => R(req), e => e instanceof V5BoundaryError && e.code === "unknown_field");
  }
  const req = receipt();
  req.observed_artifact_digest = "sha256:ABC";
  assert.throws(() => R(req), e => e.code === "invalid_digest");
});

test("item 4 (round-5 review): a branch receipt records its parent point, closed and self-consistent", () => {
  const r = R(receipt());
  assert.equal(r.decision, "pass");
  assert.deepEqual(r.target_point, { head_lsn: "16/B374D848", parent_lsn: "16/B374E000", gap_bytes: 1976, settle_seconds: 30 });
  // A parent exactly at the head (an idle production) is a zero gap and passes.
  const idle = receipt();
  idle.target_point = { head_lsn: "16/B374D848", parent_lsn: "16/B374D848", gap_bytes: 0, settle_seconds: 30 };
  assert.equal(R(idle).decision, "pass");
  // Across the 32-bit boundary the gap is still exact.
  const wrap = receipt();
  wrap.target_point = { head_lsn: "16/FFFFFFF0", parent_lsn: "17/10", gap_bytes: 32, settle_seconds: 30 };
  assert.equal(R(wrap).decision, "pass");
  // A branch receipt without the block is a contract violation; a local-cluster one needs none.
  const missing = receipt(); delete missing.target_point;
  assert.throws(() => R(missing), e => e.code === "missing_field");
  const local = receipt(); delete local.target_point; local.target_kind = "disposable_local_cluster";
  assert.equal(R(local).decision, "pass");
  assert.equal(R(local).target_point, null);
  const bad = [
    tp => { tp.gap_bytes = 1975; },                               // does not recompute
    tp => { tp.parent_lsn = "16/B374D000"; tp.gap_bytes = -2120; }, // a parent before the head
    tp => { tp.head_lsn = "16/b374d848"; },                       // lower case is not what the verifier writes
    tp => { tp.parent_lsn = "not-an-lsn"; },
    tp => { tp.head_lsn = "123456789/0"; },
    tp => { tp.head_lsn = "000000016/B374D848"; },                // same value, but more than 8 digits
    tp => { tp.head_lsn = "x16/B374D848 "; },                     // same value inside junk
    tp => { tp.gap_bytes = "1976"; },
    tp => { tp.settle_seconds = 0; },
    tp => { tp.settle_seconds = 1.5; },
  ];
  for (const edit of bad) {
    const req = receipt(); edit(req.target_point);
    assert.throws(() => R(req), e => e instanceof V5BoundaryError && e.code === "invalid_shape");
  }
  const extra = receipt(); extra.target_point.trusted = true;
  assert.throws(() => R(extra), e => e.code === "unknown_field");
  const absent = receipt(); delete absent.target_point.settle_seconds;
  assert.throws(() => R(absent), e => e.code === "missing_field");
  // The block is inside the binding: editing it after verification is not re-verified.
  const verified = stamp(receipt(), "restore_exercise", R_NOW);
  verified.target_point = { head_lsn: "0/0", parent_lsn: "0/0", gap_bytes: 0, settle_seconds: 30 };
  assertRefused(evaluateRestoreExercise(verified, R_NOW), "evidence_not_reverified", "evidence_reverified");
});

test("item 4 (G4): a receipt counts only as verify-restore's fresh output", () => {
  const verified = stamp(receipt(), "restore_exercise", R_NOW);
  assert.equal(evaluateRestoreExercise(verified, R_NOW).decision, "pass");
  // Unbound: a contract violation. A malformed binding likewise.
  assert.throws(() => evaluateRestoreExercise(receipt(), R_NOW), e => e.code === "missing_field");
  const extra = clone(verified); extra.verification.trusted = true;
  assert.throws(() => evaluateRestoreExercise(extra, R_NOW), e => e.code === "unknown_field");
  const badDigest = clone(verified); badDigest.verification.facts_digest = "sha256:nope";
  assert.throws(() => evaluateRestoreExercise(badDigest, R_NOW), e => e.code === "invalid_digest");
  // Edited after verification: every other field would pass, the binding does not.
  const edited = clone(verified); edited.finished_at = "2026-09-24T10:19:00Z";
  assertRefused(evaluateRestoreExercise(edited, R_NOW), "evidence_not_reverified", "evidence_reverified");
  // Another verifier's stamp, a stamp from the future, and a stale stamp are refused.
  const other = clone(verified); other.verification.verifier = V5_EVIDENCE_VERIFIERS.record_layer_rpo;
  assertRefused(evaluateRestoreExercise(other, R_NOW), "evidence_not_reverified", "evidence_reverified");
  const ahead = stamp(receipt(), "restore_exercise", R_NOW, R_NOW.now_ms + 1000);
  assertRefused(evaluateRestoreExercise(ahead, R_NOW), "evidence_not_reverified", "evidence_reverified");
  const maxAgeMs = V5_REVERIFY_MAX_AGE_SECONDS * 1000;
  const edge = stamp(receipt(), "restore_exercise", R_NOW, R_NOW.now_ms - maxAgeMs);
  assert.equal(evaluateRestoreExercise(edge, R_NOW).decision, "pass");
  const stale = stamp(receipt(), "restore_exercise", R_NOW, R_NOW.now_ms - maxAgeMs - 1000);
  assertRefused(evaluateRestoreExercise(stale, R_NOW), "evidence_not_reverified", "evidence_reverified");
  // The evaluator needs a clock to judge freshness.
  assert.throws(() => evaluateRestoreExercise(verified), e => e.code === "invalid_shape");
  const numericVerifier = clone(verified); numericVerifier.verification.verifier = 7;
  assert.throws(() => evaluateRestoreExercise(numericVerifier, R_NOW), e => e.code === "invalid_shape");
});

test("item 5 (G4): the matrix judges each cell's receipt binding at the MATRIX's clock", () => {
  const m = matrix();
  const staleAt = NOW.now_ms - (V5_REVERIFY_MAX_AGE_SECONDS + 1) * 1000;
  m.cells.record_layer_rpo = stamp(m.cells.record_layer_rpo, "record_layer_rpo", NOW);
  m.cells.independent_daily_restorable_copy.restore_exercise =
    stamp(m.cells.independent_daily_restorable_copy.restore_exercise, "restore_exercise", NOW, staleAt);
  m.cells.core_rto.restore_exercise = stamp(m.cells.core_rto.restore_exercise, "restore_exercise", NOW, staleAt);
  const r = evaluateRecoveryMatrix(m, NOW);
  assert.deepEqual(r.cells.independent_daily_restorable_copy.failures, ["restore_exercise_not_exact"]);
  assert.equal(r.cells.core_rto.state, "fail");
  assert.equal(r.cells.core_rto.restore_exercise_reason, "evidence_not_reverified");
  assert.equal(r.cells.record_layer_rpo.state, "pass");
});

// --- item 5: RPO/RTO cells pass independently --------------------------------

const NOW = at("2026-09-24T12:00:00Z");
const POSITIVE = { id: "0b8f3c7e-1a2b-4c3d-8e9f-0123456789ab", nonce: "a".repeat(32), written_at: "2026-09-24T11:50:00.123456Z" };
const NEGATIVE = { id: "1c9f4d8f-2b3c-4d4e-9f00-123456789abc", nonce: "b".repeat(32), written_at: "2026-09-24T11:58:05.000000Z" };

// tools/pitr-restore-proof.py prove's evidence: T = 11:58:00; the positive
// probe written 11:50, the negative 11:58:05; the branch's parent is production
// at exactly T; production re-read at 11:59.
function pitrProof() {
  return {
    source: "pitr_branch_proof",
    proof_target_kind: "disposable_branch",
    project_id: "fixture-project",
    production_branch_id: "br-production-1",
    branch_id: "br-pitr-proof-2",
    requested_parent_timestamp: "2026-09-24T11:58:00Z",
    branch_parent_id: "br-production-1",
    // the provider reports the latest timestamped WAL record at or before T, not T itself
    branch_parent_timestamp: "2026-09-24T11:57:44Z",
    branch_parent_lsn: "0/1A2B3C4D",
    positive_probe: clone(POSITIVE),
    negative_probe: clone(NEGATIVE),
    positive_on_branch: clone(POSITIVE),
    negative_present_on_branch: false,
    branch_core_table_rows: { "public.party": 120, "ops.run": 9 },
    branch_operations: { create: "op-create-7", delete: "op-delete-9" },
    history_retention_seconds: 604800,
    retention_read_at: "2026-09-24T11:58:30Z",
    production_readback: { positive: clone(POSITIVE), negative: clone(NEGATIVE), read_at: "2026-09-24T11:59:00Z" },
  };
}

function rpoOf(mutate, clock = NOW) {
  const block = pitrProof(); mutate(block);
  return M({ cells: { record_layer_rpo: block } }, clock).cells.record_layer_rpo;
}

function matrix() {
  return {
    cells: {
      record_layer_rpo: pitrProof(),
      independent_daily_restorable_copy: {
        newest_copy: {
          custody_domain: "github-actions-artifacts",
          primary_domain: "neon-primary",
          producer_id: "backup-nightly-workflow",
          produced_at: "2026-09-24T03:00:00Z",
          recorded_artifact_digest: D("a"),
          independent_store_readback_digest: D("a"),
        },
        restore_exercise: receipt(),
      },
      core_rto: { restore_exercise: receipt() },
      adapter_rto: { recoveries: [{ adapter_id: "salesforce", outage_started_at: "2026-09-20T08:00:00Z", recovered_at: "2026-09-20T15:00:00Z" }] },
    },
  };
}

test("item 5: every cell passes on measured evidence inside the settled floor", () => {
  const m = M(matrix(), NOW);
  assert.equal(m.decision, "pass");
  assert.equal(m.observed_at, "2026-09-24T12:00:00.000Z");
  assert.deepEqual(m.not_passed, []);
  for (const c of V5_RECOVERY_MATRIX_CELLS) assert.equal(m.cells[c].state, "pass", c);
  assert.equal(m.cells.record_layer_rpo.measured_seconds, 599);
  assert.equal(m.cells.record_layer_rpo.proven_restorable_point, POSITIVE.written_at);
  assert.equal(m.cells.core_rto.measured_seconds, 1200);
});

test("item 5 (F1): the clock is the caller's, never the evidence's", () => {
  const stamped = matrix();
  stamped.observed_at = "2026-09-24T12:00:00Z";
  assert.throws(() => evaluateRecoveryMatrix(stamped, NOW), e => e.code === "unknown_field");
  assert.throws(() => evaluateRecoveryMatrix(matrix()), e => e.code === "invalid_shape");
  assert.throws(() => evaluateRecoveryMatrix(matrix(), {}), e => e.code === "missing_field");
  assert.throws(() => evaluateRecoveryMatrix(matrix(), { now_ms: "2026-09-24T12:00:00Z" }), e => e.code === "invalid_shape");
  assert.throws(() => evaluateRecoveryMatrix(matrix(), { now_ms: 1, trusted: true }), e => e.code === "unknown_field");
  // The same evidence judged a day later is stale on every clocked cell.
  const later = M(matrix(), at("2026-09-25T12:00:00Z"));
  assert.equal(later.cells.record_layer_rpo.state, "fail");
  assert.ok(later.cells.record_layer_rpo.failures.includes("exposure_exceeds_rpo"));
  assert.ok(later.cells.record_layer_rpo.failures.includes("production_readback_stale"));
});

test("item 5 (F2): a copy's bare production instant is not a recovery point", () => {
  const m = matrix();
  m.cells.record_layer_rpo = { source: "verified_copy_produced_at", recovery_point_at: "2026-09-24T11:59:00Z" };
  assert.throws(() => evaluateRecoveryMatrix(m, NOW), e => e.code === "unknown_field" || e.code === "unknown_state");
  const s = pitrProof(); s.source = "verified_copy_produced_at";
  assert.throws(() => evaluateRecoveryMatrix({ cells: { record_layer_rpo: stamp(s, "record_layer_rpo", NOW) } }, NOW), e => e.code === "unknown_state");
});

test("item 5: RPO is measured from the PROVEN positive probe, passes at exactly 15 minutes and fails one second later", () => {
  // positive written 11:50:00.123456; at 12:05:00.124 it is 900.000544 s old.
  const exact = rpoOf(() => {}, at("2026-09-24T12:05:00.124Z"));
  assert.equal(exact.measured_seconds, 900);
  assert.equal(exact.state, "pass");
  const over = rpoOf(() => {}, at("2026-09-24T12:05:01.124Z"));
  assert.equal(over.state, "fail");
  assert.deepEqual(over.failures, ["exposure_exceeds_rpo"]);
  // Measured from the write, not from T: a T near now proves nothing newer than the probe.
  assert.equal(rpoOf(() => {}).measured_seconds, 599);
});

test("item 5 (F6/Q7): the point-in-time proof fails on each broken link, one at a time", () => {
  const cases = [
    [b => { b.positive_on_branch = null; }, "positive_probe_absent_on_branch"],
    [b => { b.positive_on_branch.nonce = "c".repeat(32); }, "positive_probe_absent_on_branch"],
    [b => { b.positive_on_branch.written_at = "2026-09-24T11:50:00.123457Z"; }, "positive_probe_absent_on_branch"],
    [b => { b.positive_on_branch.id = NEGATIVE.id; }, "positive_probe_absent_on_branch"],
    [b => { b.negative_present_on_branch = true; }, "negative_control_present_on_branch"],
    [b => { b.positive_probe.written_at = "2026-09-24T11:57:00.000001Z"; b.positive_on_branch.written_at = b.positive_probe.written_at; b.production_readback.positive.written_at = b.positive_probe.written_at; }, "positive_probe_not_before_point"],
    [b => { b.negative_probe.written_at = "2026-09-24T11:58:04.999999Z"; b.production_readback.negative.written_at = b.negative_probe.written_at; }, "negative_probe_not_after_point"],
    [b => { b.proof_target_kind = "production"; }, "proof_target_not_disposable"],
    [b => { b.branch_id = b.production_branch_id; }, "proof_target_is_production"],
    [b => { b.branch_parent_id = "br-some-other-branch"; }, "branch_parent_not_production"],
    [b => { b.branch_parent_timestamp = "2026-09-24T11:58:00.000001Z"; }, "branch_parent_timestamp_outside_probe_window"],
    [b => { b.branch_parent_timestamp = "2026-09-24T11:50:00.123455Z"; }, "branch_parent_timestamp_outside_probe_window"],
    [b => { b.branch_parent_lsn = ""; }, "branch_parent_lsn_missing"],
    [b => { b.branch_core_table_rows["public.party"] = 0; }, "core_table_empty_on_branch"],
    [b => { b.production_readback.positive = null; }, "probe_not_recomputed_from_production"],
    [b => { b.production_readback.negative.nonce = "d".repeat(32); }, "probe_not_recomputed_from_production"],
    [b => { b.production_readback.read_at = "2026-09-24T11:44:59Z"; }, "production_readback_stale"],
    [b => { b.production_readback.read_at = "2026-09-24T12:00:01Z"; }, "production_readback_stale"],
    [b => { b.branch_operations.create = null; }, "branch_create_not_confirmed"],
    [b => { b.branch_operations.delete = null; }, "branch_delete_not_confirmed"],
    [b => { b.history_retention_seconds = 300; }, "retention_does_not_cover_exposure"],
    [b => { b.retention_read_at = "2026-09-24T11:44:59Z"; }, "retention_readback_stale"],
    [b => { b.retention_read_at = "2026-09-24T12:00:01Z"; }, "retention_readback_stale"],
  ];
  for (const [mutate, failure] of cases) {
    const c = rpoOf(mutate);
    assert.equal(c.state, "fail", failure);
    assert.deepEqual(c.failures, [failure], `${failure}: ${c.failures}`);
  }
  // Boundaries that pass: probe exactly 60 s before T, negative exactly 5 s after,
  // retention exactly the exposure, readbacks exactly 15 minutes old or read at now.
  assert.equal(rpoOf(b => {
    for (const p of [b.positive_probe, b.positive_on_branch, b.production_readback.positive]) p.written_at = "2026-09-24T11:57:00.000000Z";
  }).state, "pass");
  assert.equal(rpoOf(b => { b.negative_probe.written_at = b.production_readback.negative.written_at = "2026-09-24T11:58:05.000000Z"; }).state, "pass");
  assert.equal(rpoOf(b => { b.history_retention_seconds = 599; }).state, "pass");
  assert.equal(rpoOf(b => { b.branch_parent_timestamp = "2026-09-24T11:58:00Z"; }).state, "pass");
  assert.equal(rpoOf(b => { b.branch_parent_timestamp = POSITIVE.written_at; }).state, "pass");
  assert.equal(rpoOf(b => { b.production_readback.read_at = "2026-09-24T11:45:00Z"; b.retention_read_at = "2026-09-24T12:00:00Z"; }).state, "pass");
  // A point in the future of the clock is refused as such.
  const future = rpoOf(() => {}, at("2026-09-24T11:57:59Z"));
  assert.ok(future.failures.includes("restorable_point_after_observation"));
});

test("item 5 (G4): the RPO proof counts only as `pitr-restore-proof.py prove`'s fresh output", () => {
  const judge = block => evaluateRecoveryMatrix({ cells: { record_layer_rpo: block } }, NOW).cells.record_layer_rpo;
  const verified = stamp(pitrProof(), "record_layer_rpo", NOW);
  assert.equal(judge(verified).state, "pass");
  const edited = clone(verified); edited.branch_operations.delete = "op-delete-10";
  assert.deepEqual(judge(edited).failures, ["evidence_not_reverified"]);
  const stale = stamp(pitrProof(), "record_layer_rpo", NOW, NOW.now_ms - (V5_REVERIFY_MAX_AGE_SECONDS + 1) * 1000);
  assert.deepEqual(judge(stale).failures, ["evidence_not_reverified"]);
  const other = clone(verified); other.verification.verifier = V5_EVIDENCE_VERIFIERS.outbound_census;
  assert.deepEqual(judge(other).failures, ["evidence_not_reverified"]);
  const { verification: _v, ...bare } = verified;
  assert.throws(() => judge(bare), e => e.code === "missing_field");
});

test("item 5: the RPO block is closed and typed", () => {
  const throwsWith = (mutate, code) => assert.throws(() => rpoOf(mutate), e => e.code === code, code);
  throwsWith(b => { b.rpo_max_seconds = 86400; }, "unknown_field");
  throwsWith(b => { delete b.negative_probe; }, "missing_field");
  throwsWith(b => { b.negative_present_on_branch = "no"; }, "invalid_shape");
  throwsWith(b => { b.branch_deleted_confirmed = true; }, "unknown_field");
  throwsWith(b => { b.branch_operations = { create: "op-1" }; }, "missing_field");
  throwsWith(b => { b.branch_operations.delete = 7; }, "invalid_identifier");
  throwsWith(b => { b.history_retention_seconds = -1; }, "invalid_shape");
  throwsWith(b => { b.history_retention_seconds = 1.5; }, "invalid_shape");
  throwsWith(b => { b.branch_core_table_rows = {}; }, "missing_field");
  throwsWith(b => { b.branch_core_table_rows["ops.run"] = -1; }, "invalid_shape");
  throwsWith(b => { b.branch_parent_lsn = "latest"; }, "invalid_shape");
  throwsWith(b => { b.positive_probe.nonce = "short"; }, "invalid_identifier");
  throwsWith(b => { b.positive_probe.id = "not-a-uuid"; }, "invalid_identifier");
  throwsWith(b => { b.source = "configured_retention_window"; }, "unknown_state");
});

test("item 5: cells are independent — a missing, null or failing cell changes no other cell", () => {
  const base = M(matrix(), NOW);
  const noAdapter = matrix(); delete noAdapter.cells.adapter_rto;
  const m1 = M(noAdapter, NOW);
  assert.equal(m1.cells.adapter_rto.state, "no_evidence");
  assert.equal(m1.decision, "fail");
  assert.deepEqual(m1.not_passed, ["adapter_rto"]);
  for (const c of ["record_layer_rpo", "independent_daily_restorable_copy", "core_rto"]) assert.deepEqual(m1.cells[c], base.cells[c]);

  const nullCore = matrix(); nullCore.cells.core_rto = null;
  assert.equal(M(nullCore, NOW).cells.core_rto.state, "no_evidence");

  const badRpo = matrix(); badRpo.cells.record_layer_rpo.negative_present_on_branch = true;
  const m2 = M(badRpo, NOW);
  assert.deepEqual(m2.not_passed, ["record_layer_rpo"]);
  for (const c of ["independent_daily_restorable_copy", "core_rto", "adapter_rto"]) assert.deepEqual(m2.cells[c], base.cells[c]);

  const empty = M({ cells: {} }, NOW);
  assert.deepEqual(empty.not_passed, [...V5_RECOVERY_MATRIX_CELLS]);
  for (const c of V5_RECOVERY_MATRIX_CELLS) assert.equal(empty.cells[c].state, "no_evidence");
});

test("item 5: the daily copy cell needs a fresh independent copy, a matching readback and a same-lineage exact restore", () => {
  const daily = m => m.cells.independent_daily_restorable_copy;
  const cases = [
    [m => { daily(m).newest_copy.produced_at = "2026-09-23T09:59:59Z"; }, "newest_copy_too_old", NOW],
    [m => { daily(m).newest_copy.produced_at = "2026-09-24T12:00:01Z"; }, "copy_produced_after_observation", NOW],
    [m => { daily(m).newest_copy.independent_store_readback_digest = D("c"); }, "independent_store_readback_mismatch", NOW],
    [m => { daily(m).newest_copy.custody_domain = "neon-primary"; daily(m).newest_copy.primary_domain = "neon-primary"; daily(m).restore_exercise.copy.custody_domain = "neon-primary"; }, "copy_not_independently_controlled", NOW],
    [m => { daily(m).newest_copy.producer_id = "mac-nightly"; }, "restore_exercise_other_producer", NOW],
    [m => { daily(m).newest_copy.custody_domain = "r2-archive"; }, "restore_exercise_other_custody_domain", NOW],
    [m => { daily(m).restore_exercise.restored_watermark["ops.run"].rows = 8; }, "restore_exercise_not_exact", NOW],
    [() => {}, "restore_exercise_after_observation", at("2026-09-24T10:19:59Z")],
    [m => { daily(m).newest_copy.produced_at = "2026-10-01T03:00:00Z"; }, "restore_exercise_too_old", at("2026-10-01T10:20:01Z")],
  ];
  for (const [mutate, failure, clock] of cases) {
    const m = matrix(); mutate(m);
    const cellResult = M(m, clock).cells.independent_daily_restorable_copy;
    assert.equal(cellResult.state, "fail", failure);
    assert.ok(cellResult.failures.includes(failure), `${failure} in ${cellResult.failures}`);
  }
  // Exactly 26 h old passes; a restore exercise exactly 7 days old passes; one finished exactly now passes.
  const at26h = matrix();
  daily(at26h).newest_copy.produced_at = "2026-09-23T10:00:00Z";
  daily(at26h).restore_exercise.copy.produced_at = "2026-09-23T10:00:00Z";
  assert.equal(M(at26h, NOW).cells.independent_daily_restorable_copy.state, "pass");
  const at7d = matrix();
  daily(at7d).newest_copy.produced_at = "2026-10-01T03:00:00Z";
  const c7 = M(at7d, at("2026-10-01T10:20:00Z")).cells.independent_daily_restorable_copy;
  assert.equal(c7.restore_exercise_age_seconds, 7 * 86400);
  assert.equal(c7.state, "pass");
  const atNow = M(matrix(), at("2026-09-24T10:20:00Z")).cells.independent_daily_restorable_copy;
  assert.equal(atNow.state, "pass");
});

test("item 5: core RTO passes at 4 hours, fails beyond, and a non-exact restore has no RTO", () => {
  const at4h = matrix(); at4h.cells.core_rto.restore_exercise.finished_at = "2026-09-24T14:00:00Z";
  assert.equal(M(at4h, NOW).cells.core_rto.state, "pass");
  const over = matrix(); over.cells.core_rto.restore_exercise.finished_at = "2026-09-24T14:00:01Z";
  assert.equal(M(over, NOW).cells.core_rto.state, "fail");
  const inexact = matrix(); inexact.cells.core_rto.restore_exercise.observed_artifact_digest = D("f");
  const c = M(inexact, NOW).cells.core_rto;
  assert.equal(c.state, "fail");
  assert.equal(c.restore_exercise_reason, "artifact_hash_mismatch");
});

const CALENDAR = JSON.parse(readFileSync(new URL("../../ops/config/business-calendar.us-federal.json", import.meta.url), "utf8"));

function adapter(start, end, calendar = CALENDAR) {
  const m = { cells: { adapter_rto: { recoveries: [{ adapter_id: "crm", outage_started_at: start, recovered_at: end }] } } };
  if (calendar) m.business_calendar = calendar;
  return M(m, at("2026-12-31T00:00:00Z")).cells.adapter_rto;
}

test("item 5: the pinned calendar digest is the digest of the config file", () => {
  assert.equal(V5_BUSINESS_CALENDAR_DIGEST, digest(CALENDAR));
  assert.equal(CALENDAR.timezone, "America/Chicago");
  assert.equal(Object.keys(CALENDAR.holidays).length, 33);
});

test("item 5: adapter recovery is due by the same Central time on the next business day", () => {
  // Friday 17:00 CDT -> due Monday 17:00 CDT (22:00Z); a weekend never counts.
  const fri = adapter("2026-09-18T22:00:00Z", "2026-09-21T21:00:00Z");
  assert.equal(fri.state, "pass");
  assert.equal(fri.adapters[0].business_day_deadline, "2026-09-21T22:00:00.000Z");
  assert.equal(adapter("2026-09-18T22:00:00Z", "2026-09-21T22:30:00Z").state, "fail");
  // Recovered exactly AT the deadline passes; one second after fails.
  assert.equal(adapter("2026-09-18T22:00:00Z", "2026-09-21T22:00:00Z").state, "pass");
  assert.equal(adapter("2026-09-18T22:00:00Z", "2026-09-21T22:00:01Z").state, "fail");
  // Friday before Labor Day: Sat, Sun and the Monday holiday are skipped -> Tuesday.
  assert.equal(adapter("2026-09-04T15:00:00Z", "2026-09-08T14:59:00Z").state, "pass");
  assert.equal(adapter("2026-09-04T15:00:00Z", "2026-09-08T15:01:00Z").state, "fail");
  // Tuesday 10:00 -> Wednesday 11:00: 25 hours between business days fails.
  assert.equal(adapter("2026-09-22T15:00:00Z", "2026-09-23T16:00:00Z").state, "fail");
  // DST ends Sunday 2026-11-01: Friday 12:00 CDT (17:00Z) is due Monday 12:00 CST (18:00Z).
  const dst = adapter("2026-10-30T17:00:00Z", "2026-11-02T17:30:00Z");
  assert.equal(dst.adapters[0].business_day_deadline, "2026-11-02T18:00:00.000Z");
  assert.equal(dst.state, "pass");
  assert.equal(adapter("2026-10-30T17:00:00Z", "2026-11-02T18:30:00Z").state, "fail");
});

test("item 5: a deadline whose wall time sits across a DST change resolves to the true instant", () => {
  // A synthetic calendar that works Sundays: Saturday 03:00 CDT (08:00Z) is due
  // Sunday 03:00 CST (09:00Z) — the first zone guess (CDT) is an hour early.
  const sundays = { ...CALENDAR, business_weekdays: [1, 2, 3, 4, 5, 6, 7], holidays: {} };
  assert.equal(new Date(nextBusinessDayDeadline(Date.parse("2026-10-31T08:00:00Z"), sundays)).toISOString(), "2026-11-01T09:00:00.000Z");
  // Nothing within 31 days is a business day: no deadline.
  assert.equal(nextBusinessDayDeadline(Date.parse("2026-10-31T08:00:00Z"), { ...CALENDAR, business_weekdays: [] }), null);
});

test("item 5: at exactly 24h an adapter passes with no calendar; one second more is indeterminate without one or outside its years", () => {
  assert.equal(adapter("2026-09-22T15:00:00Z", "2026-09-23T15:00:00Z", null).state, "pass");
  const justOver = adapter("2026-09-22T15:00:00Z", "2026-09-23T15:00:01Z", null);
  assert.equal(justOver.state, "indeterminate");
  assert.equal(justOver.adapters[0].reason, "business_calendar_not_supplied");
  const beyond = adapter("2028-12-29T15:00:00Z", "2029-01-02T15:00:00Z");
  assert.equal(beyond.state, "indeterminate");
  assert.equal(beyond.adapters[0].reason, "business_calendar_does_not_cover_dates");
  // Before the calendar's first covered date is indeterminate too, not a guess.
  const before = adapter("2025-12-30T15:00:00Z", "2025-12-31T16:00:00Z");
  assert.equal(before.state, "indeterminate");
  assert.equal(before.adapters[0].reason, "business_calendar_does_not_cover_dates");
});

test("item 5: a calendar with an extra holiday is not the pinned calendar and cannot be read", () => {
  const stretched = clone(CALENDAR);
  stretched.holidays["2026-09-21"] = "made_up_day";
  assert.throws(() => adapter("2026-09-18T22:00:00Z", "2026-09-22T21:00:00Z", stretched),
    e => e.code === "business_calendar_digest_moved");
});

test("item 5: one failing adapter fails the cell even beside an indeterminate one; backwards intervals fail", () => {
  const long = matrix();
  long.cells.adapter_rto.recoveries.push({ adapter_id: "drive", outage_started_at: "2026-09-19T17:00:00Z", recovered_at: "2026-09-22T09:00:00Z" });
  const m = M(long, NOW);
  assert.equal(m.cells.adapter_rto.state, "indeterminate");
  assert.deepEqual(m.not_passed, ["adapter_rto"]);
  assert.equal(m.decision, "fail");
  long.cells.adapter_rto.recoveries.push({ adapter_id: "mail", outage_started_at: "2026-09-20T07:00:00Z", recovered_at: "2026-09-20T06:00:00Z" });
  const mixed = M(long, NOW).cells.adapter_rto;
  assert.equal(mixed.state, "fail");
  assert.equal(mixed.adapters[2].reason, "recovered_before_outage");
});

// --- item 6: outbound queues quarantine until reconciliation -----------------

const K = { a: D("1"), b: D("2"), c: D("3"), d: D("4"), e: D("5") };
const OUT_NOW = at("2026-09-24T12:00:00Z");
/** Each readback's own read instant: 30 minutes after the 11:00 attempts, so outside the settle window. */
const READ = "2026-09-24T11:30:00Z";

function outbound(items) {
  const list = items ?? [
    { item_id: "mail-1", envelope_digest: K.a, state: "pending", last_attempt_at: null },
    { item_id: "mail-2", envelope_digest: K.b, state: "in_flight", last_attempt_at: "2026-09-24T11:00:00Z" },
    { item_id: "sf-3", envelope_digest: K.c, state: "outcome_unknown", last_attempt_at: "2026-09-24T11:00:00Z" },
    { item_id: "sf-4", envelope_digest: K.d, state: "pending", last_attempt_at: null },
    { item_id: "done-5", envelope_digest: K.e, state: "settled", last_attempt_at: "2026-09-24T10:00:00Z" },
  ];
  return {
    restore_id: "restore-2026-09-24",
    census: { source: "ops.notification_delivery:device", digest: v5OutboundCensusDigest(list), item_count: list.length },
    items: list,
    readbacks: [],
  };
}

const allPresent = req => req.items.filter(i => i.state !== "settled")
  .map(i => ({ item_id: i.item_id, idempotency_key: i.envelope_digest, read_at: READ, readback: "effect_present" }));

test("item 6: after a restore every unsettled outbound item is quarantined and nothing is released", () => {
  const r = O(outbound(), OUT_NOW);
  assert.equal(r.decision, "hold");
  assert.deepEqual(r.quarantined_item_ids, ["mail-1", "mail-2", "sf-3", "sf-4"]);
  assert.equal(r.dispositions.find(d => d.item_id === "done-5").disposition, "already_settled");
  assert.equal(r.dispositions.find(d => d.item_id === "mail-1").reason, "no_readback");
  assert.equal(r.releases_anything, false);
  assert.deepEqual(r.effects, V5_NO_EFFECTS);
});

test("item 6: only an exact-key provider readback moves an item, and present is never resent", () => {
  const req = outbound();
  req.readbacks = [
    { item_id: "mail-1", idempotency_key: K.a, read_at: READ, readback: "effect_present" },
    { item_id: "mail-2", idempotency_key: K.b, read_at: READ, readback: "effect_absent" },
    { item_id: "sf-3", idempotency_key: K.c, read_at: READ, readback: "indeterminate" },
    { item_id: "sf-4", idempotency_key: K.a, read_at: READ, readback: "effect_absent" },
  ];
  const r = O(req, OUT_NOW);
  const by = Object.fromEntries(r.dispositions.map(d => [d.item_id, d]));
  assert.equal(by["mail-1"].disposition, "settle_without_resend");
  assert.equal(by["mail-2"].disposition, "release_for_governed_send");
  assert.equal(by["sf-3"].disposition, "quarantined");
  assert.equal(by["sf-3"].reason, "readback_indeterminate");
  assert.equal(by["sf-4"].disposition, "quarantined");
  assert.equal(by["sf-4"].reason, "idempotency_key_mismatch");
  assert.equal(r.decision, "hold");
  assert.deepEqual(r.quarantined_item_ids, ["sf-3", "sf-4"]);
});

test("item 6 (F5/G2): an attempted item reading absent is released only when THAT readback was taken after the settle window", () => {
  const absent = readAt => {
    const req = outbound();
    req.readbacks = [{ item_id: "mail-2", idempotency_key: K.b, read_at: readAt, readback: "effect_absent" },
      { item_id: "sf-3", idempotency_key: K.c, read_at: readAt, readback: "effect_absent" }];
    return req;
  };
  // Read 1 s before the window closed: held, however late the evaluator runs.
  for (const clock of [at("2026-09-24T11:15:00Z"), OUT_NOW, at("2026-09-24T11:29:59Z")]) {
    const early = O(absent("2026-09-24T11:14:59Z"), clock);
    for (const id of ["mail-2", "sf-3"]) {
      const d = early.dispositions.find(x => x.item_id === id);
      assert.equal(d.disposition, "quarantined", id);
      assert.equal(d.reason, "readback_inside_settle_window");
      assert.equal(d.settles_at, "2026-09-24T11:15:00.000Z");
    }
  }
  // Read exactly at the window's close: released.
  const onTime = O(absent("2026-09-24T11:15:00Z"), OUT_NOW);
  assert.equal(onTime.dispositions.find(x => x.item_id === "mail-2").disposition, "release_for_governed_send");
  // A readback stamped after the evaluator's clock is not a reading yet; one read exactly now is.
  const future = O(absent("2026-09-24T12:00:01Z"), OUT_NOW);
  assert.equal(future.dispositions.find(x => x.item_id === "mail-2").reason, "readback_after_observation");
  const justNow = O(absent("2026-09-24T12:00:00Z"), OUT_NOW);
  assert.equal(justNow.dispositions.find(x => x.item_id === "mail-2").disposition, "release_for_governed_send");
  // A pending item was never attempted: an absent read releases it at once.
  const pending = outbound();
  pending.readbacks = [{ item_id: "mail-1", idempotency_key: K.a, read_at: "2026-09-24T11:00:01Z", readback: "effect_absent" }];
  assert.equal(O(pending, OUT_NOW).dispositions.find(x => x.item_id === "mail-1").disposition, "release_for_governed_send");
  // A present effect settles at once (nothing is resent), window or not.
  const present = outbound();
  present.readbacks = [{ item_id: "mail-2", idempotency_key: K.b, read_at: "2026-09-24T11:00:01Z", readback: "effect_present" }];
  assert.equal(O(present, OUT_NOW)
    .dispositions.find(x => x.item_id === "mail-2").disposition, "settle_without_resend");
  // read_at is required and typed.
  const noRead = outbound();
  noRead.readbacks = [{ item_id: "mail-2", idempotency_key: K.b, readback: "effect_absent" }];
  assert.throws(() => O(noRead, OUT_NOW), e => e.code === "missing_field");
  // An attempted item must say when it was last attempted.
  const noStamp = outbound();
  noStamp.items[1].last_attempt_at = null;
  noStamp.census = { ...noStamp.census, digest: v5OutboundCensusDigest(noStamp.items) };
  assert.throws(() => O(noStamp, OUT_NOW), e => e.code === "missing_field");
});

test("item 6 (F5): the item list is bound to the restored queue's census, and an empty census holds", () => {
  const dropped = outbound();
  dropped.items = dropped.items.filter(i => i.item_id !== "sf-3");
  dropped.readbacks = allPresent(dropped);
  const r1 = O(dropped, OUT_NOW);
  assert.equal(r1.decision, "hold");
  assert.equal(r1.reason_id, "outbound_census_mismatch");
  assert.equal(r1.releases_anything, false);

  const relabelled = outbound();
  relabelled.items[1].state = "settled";
  assert.equal(O(relabelled, OUT_NOW).reason_id, "outbound_census_mismatch");

  const miscounted = outbound();
  miscounted.census.item_count = 6;
  assert.equal(O(miscounted, OUT_NOW).reason_id, "outbound_census_mismatch");

  const empty = outbound([]);
  const r2 = O(empty, OUT_NOW);
  assert.equal(r2.decision, "hold");
  assert.equal(r2.reason_id, "outbound_census_empty");

  // Order does not matter: the census is over the items sorted by id.
  const shuffled = outbound();
  shuffled.items.reverse();
  shuffled.readbacks = allPresent(shuffled);
  assert.equal(O(shuffled, OUT_NOW).decision, "reconciled");

  // A census source other than the restored queue's reader is refused.
  const elsewhere = outbound();
  elsewhere.census.source = "caller_list";
  assert.throws(() => O(elsewhere, OUT_NOW), e => e.code === "unknown_state");
});

test("item 6 (G2/G4): the request must be the census reader's own re-read output; a caller's subset with its own digest holds", () => {
  const verified = stamp(outbound(), "outbound_census", OUT_NOW);
  verified.readbacks = [];
  // Drop an item and recompute the census digest, keeping the reader's binding.
  const subset = clone(verified);
  subset.items = subset.items.filter(i => i.item_id !== "sf-3");
  subset.census = { ...subset.census, digest: v5OutboundCensusDigest(subset.items), item_count: subset.items.length };
  subset.readbacks = allPresent(subset);
  const r = evaluateOutboundQueueRelease(subset, OUT_NOW);
  assert.equal(r.decision, "hold");
  assert.equal(r.reason_id, "outbound_evidence_not_reverified");
  assert.equal(r.releases_anything, false);
  // No binding at all is a contract violation, not a hold.
  const { verification: _v, ...bare } = verified;
  assert.throws(() => evaluateOutboundQueueRelease(bare, OUT_NOW), e => e.code === "missing_field");
  // Another step's verifier, or a stale stamp, holds.
  const wrong = clone(verified); wrong.verification.verifier = V5_EVIDENCE_VERIFIERS.restore_exercise;
  assert.equal(evaluateOutboundQueueRelease(wrong, OUT_NOW).reason_id, "outbound_evidence_not_reverified");
  const stale = stamp(outbound(), "outbound_census", OUT_NOW, OUT_NOW.now_ms - (V5_REVERIFY_MAX_AGE_SECONDS + 1) * 1000);
  assert.equal(evaluateOutboundQueueRelease(stale, OUT_NOW).reason_id, "outbound_evidence_not_reverified");
});

test("item 6: the queue is reconciled only when every item has an exact readback", () => {
  const req = outbound();
  req.readbacks = allPresent(req);
  const r = O(req, OUT_NOW);
  assert.equal(r.decision, "reconciled");
  assert.equal(r.census_digest, req.census.digest);
  assert.deepEqual(r.quarantined_item_ids, []);
  const onlySettled = outbound([{ item_id: "done-5", envelope_digest: K.e, state: "settled", last_attempt_at: null }]);
  assert.equal(O(onlySettled, OUT_NOW).decision, "reconciled");
});

test("item 6: no age-out, no operator override, no duplicate item or readback, and a clock is required", () => {
  for (const key of ["force_release", "release_after_seconds", "operator_override"]) {
    assert.throws(() => O({ ...outbound(), [key]: true }, OUT_NOW), e => e.code === "unknown_field");
  }
  const dupReadback = outbound();
  dupReadback.readbacks = [
    { item_id: "mail-1", idempotency_key: K.a, read_at: READ, readback: "effect_absent" },
    { item_id: "mail-1", idempotency_key: K.a, read_at: READ, readback: "effect_present" },
  ];
  assert.throws(() => O(dupReadback, OUT_NOW), e => e.code === "duplicate_readback");
  const dupItem = outbound();
  dupItem.items.push({ ...dupItem.items[0] });
  assert.throws(() => O(dupItem, OUT_NOW), e => e.code === "duplicate_item");
  assert.throws(() => O(outbound()), e => e.code === "invalid_shape");
  const noCensus = outbound(); delete noCensus.census;
  assert.throws(() => O(noCensus, OUT_NOW), e => e.code === "missing_field");
});

// --- degraded-mode projection ------------------------------------------------

test("degraded modes: every outage is a reduction that quarantines outbound; none claims full offline capability", () => {
  for (const [name, mode] of Object.entries(V5_DEGRADED_MODES)) assert.equal(mode.outbound, "quarantine", name);
  const p = v5DegradedModeProjection(["record_layer_unavailable", "model_provider_unavailable"]);
  assert.equal(p.mode, "degraded");
  assert.equal(p.outbound, "quarantine_until_reconciliation");
  assert.equal(p.claims_full_offline_capability, false);
  assert.equal(p.modes.record_layer_unavailable.writes, "refuse");
  assert.equal(v5DegradedModeProjection([]).mode, "normal");
  assert.throws(() => v5DegradedModeProjection(["everything_fine"]), e => e.code === "unknown_state");
});

// --- the policy --------------------------------------------------------------

test("policy: the digest is sha256 of the canonical bytes and closes exactly the two #974 seams", () => {
  const hex = createHash("sha256").update(v5RecoveryMatrixPolicyCanonicalBytes()).digest("hex");
  assert.equal(v5RecoveryMatrixPolicyDigest(), `sha256:${hex}`);
  const pre = v5RecoveryMatrixPolicyPreimage();
  assert.deepEqual(pre.closes_seams, [V5_OUTBOUND_RECONCILIATION_SEAM, V5_RESTORE_AND_RECOVERY_MATRIX_SEAM].sort());
  assert.equal(pre.rpo_max_seconds, 900);
  assert.equal(pre.core_rto_max_seconds, 14400);
  assert.equal(pre.no_evidence_is_pass, false);
  assert.equal(pre.claims_full_offline_capability, false);
  assert.deepEqual(pre.recovery_point_sources, ["pitr_branch_proof"]);
  assert.equal(pre.clock_source, "caller_clock_option_never_evidence");
  assert.equal(pre.outbound_settle_window_seconds, 900);
  assert.equal(R(clone(receipt())).policy_digest, v5RecoveryMatrixPolicyDigest());
});

// --- the CLI, with the real clock ---------------------------------------------

const CLI = fileURLToPath(new URL("../bin/recovery-matrix-evaluate.mjs", import.meta.url));
const cli = (args, input) => spawnSync(process.execPath, [CLI, ...args], { input, encoding: "utf8" });

/** A proof whose instants are relative to the real current time, as verify produces it. */
function liveProof() {
  const now = Date.now();
  const iso = (offsetSeconds, micros = false) => {
    const s = new Date(Math.floor(now / 1000) * 1000 + offsetSeconds * 1000).toISOString();
    return micros ? s.replace(".000Z", ".000000Z") : s.replace(".000Z", "Z");
  };
  const b = pitrProof();
  b.requested_parent_timestamp = b.branch_parent_timestamp = iso(-120);
  for (const p of [b.positive_probe, b.positive_on_branch, b.production_readback.positive]) p.written_at = iso(-240, true);
  for (const p of [b.negative_probe, b.production_readback.negative]) p.written_at = iso(-100, true);
  b.production_readback.read_at = iso(-10);
  b.retention_read_at = iso(-30);
  return stamp(b, "record_layer_rpo", { now_ms: now });
}

test("CLI rpo: judges stdin evidence at the real current time; a supplied observed_at is refused", () => {
  const ok = cli(["rpo", "-"], JSON.stringify(liveProof()));
  assert.equal(ok.status, 0, ok.stdout + ok.stderr);
  const out = JSON.parse(ok.stdout);
  assert.equal(out.record_layer_rpo.state, "pass");
  assert.ok(Math.abs(Date.parse(out.observed_at) - Date.now()) < 60000);
  assert.ok(out.record_layer_rpo.measured_seconds >= 240 && out.record_layer_rpo.measured_seconds < 300);

  const stamped = { ...liveProof(), observed_at: new Date().toISOString() };
  const refused = cli(["rpo", "-"], JSON.stringify(stamped));
  assert.equal(refused.status, 2);
  assert.match(refused.stderr, /unknown_field/);

  // The fixed-clock fixture, run today, is long stale.
  const stale = cli(["rpo", "-"], JSON.stringify(stamp(pitrProof(), "record_layer_rpo", { now_ms: Date.now() })));
  assert.equal(stale.status, 1);
  assert.ok(JSON.parse(stale.stdout).record_layer_rpo.failures.includes("exposure_exceeds_rpo"));
});

test("CLI rpo: the clock is read after the evidence, so a slow `verify | evaluate` producer is not judged from the past", async () => {
  const { spawn } = await import("node:child_process");
  const child = spawn(process.execPath, [CLI, "rpo", "-"]);
  let out = "";
  child.stdout.on("data", d => { out += d; });
  await new Promise(r => setTimeout(r, 1500));
  const b = liveProof();
  // read "just now", 1.5 s after the evaluator started: whole seconds, never ahead of the real clock
  b.production_readback.read_at = b.retention_read_at = new Date(Math.floor(Date.now() / 1000) * 1000).toISOString().replace(".000Z", "Z");
  child.stdin.end(JSON.stringify(stamp(b, "record_layer_rpo", { now_ms: Date.now() })));
  const code = await new Promise(r => child.on("close", r));
  assert.equal(code, 0, out);
  assert.deepEqual(JSON.parse(out).record_layer_rpo.failures, []);
});

test("CLI matrix: supplies the pinned calendar, uses the real clock, refuses observed_at", () => {
  const evidence = {
    cells: {
      record_layer_rpo: liveProof(),
      adapter_rto: { recoveries: [{ adapter_id: "crm", outage_started_at: "2026-09-18T22:00:00Z", recovered_at: "2026-09-21T21:00:00Z" }] },
    },
  };
  const r = cli(["matrix", "-"], JSON.stringify(evidence));
  assert.equal(r.status, 1, r.stderr); // two cells have no evidence
  const out = JSON.parse(r.stdout);
  assert.equal(out.cells.record_layer_rpo.state, "pass");
  assert.equal(out.cells.adapter_rto.state, "pass");
  assert.equal(out.cells.adapter_rto.business_calendar_id, "us-federal-weekdays");
  assert.deepEqual(out.not_passed, ["independent_daily_restorable_copy", "core_rto"]);

  const stamped = cli(["matrix", "-"], JSON.stringify({ ...evidence, observed_at: "2026-09-24T12:00:00Z" }));
  assert.equal(stamped.status, 2);
  assert.match(stamped.stderr, /unknown_field/);

  const tampered = clone(CALENDAR); tampered.holidays["2026-09-21"] = "made_up_day";
  const moved = cli(["matrix", "-"], JSON.stringify({ ...evidence, business_calendar: tampered }));
  assert.equal(moved.status, 2);
  assert.match(moved.stderr, /business_calendar_digest_moved/);
});

test("CLI: restore, outbound, degraded and policy exit 0/1/2 by verdict", () => {
  assert.equal(cli(["restore", "-"], JSON.stringify(stamp(receipt(), "restore_exercise", { now_ms: Date.now() }))).status, 0);
  const bad = receipt(); bad.observed_artifact_digest = D("b");
  assert.equal(cli(["restore", "-"], JSON.stringify(stamp(bad, "restore_exercise", { now_ms: Date.now() }))).status, 1);
  assert.equal(cli(["restore", "-"], "{}").status, 2);
  assert.equal(cli(["outbound", "-"], JSON.stringify(stamp(outbound(), "outbound_census", { now_ms: Date.now() }))).status, 1);
  assert.equal(cli(["degraded", "edge_unavailable"]).status, 0);
  assert.equal(JSON.parse(cli(["policy"]).stdout).policy_digest, v5RecoveryMatrixPolicyDigest());
  assert.equal(cli(["bogus"]).status, 2);
});
