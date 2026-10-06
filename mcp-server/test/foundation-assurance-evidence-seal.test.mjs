import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { EventEmitter } from "node:events";

import { digest } from "../src/artifact-trust.js";
import { BENCHMARK_CELL_DOMAIN_TAG, benchmarkRequiredCells } from
  "../src/benchmark-minimum.v5.js";
import {
  FOUNDATION_ASSURANCE_COMPARATORS,
  FOUNDATION_ASSURANCE_GITHUB_CHECKS,
  foundationAssuranceBenchmarkPayload,
} from "../src/foundation-assurance-evidence.v5.js";
import {
  assembleFoundationAssuranceEvidence,
  canonicalStagingOrigin,
  canonicalStagingOriginFromRegistry,
  closeChromeProfile,
  parseFoundationAssuranceArgs,
  selectProductionEvidenceDsn,
  stagingReleaseSource,
} from "../bin/seal-foundation-assurance-evidence.mjs";

test("Chrome exits before profile cleanup and ENOTEMPTY cleanup retries", async () => {
  class Chrome extends EventEmitter {
    constructor() { super(); this.exitCode = null; this.signalCode = null; this.signals = []; }
    kill(signal) {
      this.signals.push(signal);
      queueMicrotask(() => { this.signalCode = signal; this.emit("exit"); });
      return true;
    }
  }
  const chrome = new Chrome();
  let removes = 0;
  await closeChromeProfile(chrome, "/tmp/fake-profile", async () => {
    assert.equal(chrome.signalCode, "SIGTERM");
    if (++removes === 1) throw Object.assign(new Error("busy"), { code: "ENOTEMPTY" });
  });
  assert.deepEqual(chrome.signals, ["SIGTERM"]);
  assert.equal(removes, 2);
});

const config = JSON.parse(await readFile(new URL(
  "../../ops/config/foundation-assurance-benchmark.v1.json", import.meta.url), "utf8"));
const bindings = parseFoundationAssuranceArgs([
  "--source-sha", "a".repeat(40), "--source-tree", "b".repeat(40),
  "--staging-provider-version", "00000000-0000-4000-8000-000000000010",
  "--final-provider-version", "00000000-0000-4000-8000-000000000011",
  "--release-key", "release.foundation-assurance.minimum.v1",
  "--staging-origin", canonicalStagingOrigin(),
  "--idempotency-key", "00000000-0000-4000-8000-000000000012",
  "--staging-candidate-operation-id", "00000000-0000-4000-8000-000000000013",
  "--staging-replacement-receipt-id", "00000000-0000-4000-8000-000000000014",
  "--staging-replacement-source-sha", "c".repeat(40),
]);

function acquired() {
  const browser = { name: "Chrome", version: "140", build: "140.0.1" };
  const runtime_version = "worker-2026.09";
  const payload = foundationAssuranceBenchmarkPayload(config, {
    subject_digest: digest(["doctorcre:wr95-subject:v1", bindings.source_sha, bindings.source_tree]),
    candidate_digest: digest(["doctorcre:wr95-candidate:v1", bindings.final_provider_version]),
    policy_digest: digest(config), browser, runtime_version,
    evaluator_identities: [{ actor_id: "codex-fa-coverage",
      session_ref: `session:${bindings.idempotency_key}`,
      authority_class: "review_agent" }],
  });
  const cells = benchmarkRequiredCells(payload);
  return {
    browser, runtime_version,
    database: { environment: "staging", migration: "0513_release_candidate_environment_identity.sql",
      read_only: true,
      source: "ops/foundation-assurance-candidate-rehearsal.py --read-foundation-facts" },
    github_checks: FOUNDATION_ASSURANCE_GITHUB_CHECKS.map((name, index) => ({
      name, conclusion: "success", head_sha: bindings.source_sha, run_id: index + 1,
      url: `https://github.com/jbookout/carr-system/actions/runs/${index + 1}` })),
    measurements: { schema_version: "doctorcre-v5-benchmark-measurement-set.v1",
      outlier_rule: payload.outlier_rule, p95_aggregation_method: payload.p95_aggregation_method,
      captured_at_ms: Date.parse("2026-09-14T14:00:00.000Z"),
      cells: cells.map(cell => ({ cell, warmup_samples: [1], samples: Array(20).fill(1),
        excluded_sample_indexes: [] })) },
    sample_provenance: cells.map((cell, index) => ({
      cell_digest: digest([BENCHMARK_CELL_DOMAIN_TAG, cell]),
      origin: `https://staging.doctorcre.com/acquired/${index}`,
      sample_count: 20, warmup_count: 1 })),
    comparators: FOUNDATION_ASSURANCE_COMPARATORS.map(id => ({ id, status: "pass",
      detail_digest: digest([id, "live"]), origin: `https://staging.doctorcre.com/comparator/${id}` })),
  };
}

test("CLI accepts immutable bindings and refuses caller evidence", () => {
  assert.equal(bindings.release_key, "release.foundation-assurance.minimum.v1");
  assert.throws(() => parseFoundationAssuranceArgs([
    "--measurements", "/tmp/claimed.json",
  ]), /caller_evidence_input_refused/);
  assert.throws(() => parseFoundationAssuranceArgs([
    "--pass", "true",
  ]), /caller_evidence_input_refused/);
  assert.throws(() => parseFoundationAssuranceArgs([
    "--source-sha", "a".repeat(40), "--source-tree", "b".repeat(40),
    "--staging-provider-version", "00000000-0000-4000-8000-000000000010",
    "--final-provider-version", "00000000-0000-4000-8000-000000000011",
    "--release-key", "release.foundation-assurance.minimum.v1",
    "--staging-origin", "https://attacker.example",
    "--idempotency-key", "00000000-0000-4000-8000-000000000012",
    "--staging-candidate-operation-id", "00000000-0000-4000-8000-000000000013",
    "--staging-replacement-receipt-id", "00000000-0000-4000-8000-000000000014",
    "--staging-replacement-source-sha", "c".repeat(40),
  ]), /invalid_staging_origin/);
});

test("canonical staging registry shape fails closed", () => {
  const registry = endpoint => ({ services: [{ key: "carr-mcp", environments: [
    { environment: "staging", ...(endpoint === undefined ? {} : { endpoint }) },
  ] }] });
  assert.throws(() => canonicalStagingOriginFromRegistry(registry("attacker.example?")),
    /canonical_staging_origin_unavailable/);
  assert.throws(() => canonicalStagingOriginFromRegistry(registry(undefined)),
    /canonical_staging_origin_unavailable/);
  const ambiguous = registry("one.example");
  ambiguous.services[0].environments.push({ environment: "staging" });
  assert.throws(() => canonicalStagingOriginFromRegistry(ambiguous),
    /canonical_staging_origin_unavailable/);
});

test("staging release source reads the typed release envelope", () => {
  assert.equal(stagingReleaseSource({ git_sha: { value: "a".repeat(40), reason: null } }),
    "a".repeat(40));
});

test("acquired matrix seals against release key without a release UUID", () => {
  const bundle = assembleFoundationAssuranceEvidence(bindings, config, acquired());
  assert.equal(bundle.seal.release_key, bindings.release_key);
  assert.equal(bundle.evidence.release.test_evidence_ref, bundle.seal.evidence_ref);
  assert.equal(bundle.seal.required_cell_count, 4);
  assert.deepEqual(bundle.evidence.benchmark_payload.evaluator_identities, [{
    actor_id: "codex-fa-coverage",
    session_ref: `session:${bindings.idempotency_key}`,
    authority_class: "review_agent",
  }]);
});

test("missing or caller-shaped acquisition is refused", () => {
  const value = acquired();
  delete value.database;
  assert.throws(() => assembleFoundationAssuranceEvidence(bindings, config, value),
    /acquisition_shape_invalid/);
  const extra = { ...acquired(), caller_measurements: [] };
  assert.throws(() => assembleFoundationAssuranceEvidence(bindings, config, extra),
    /acquisition_shape_invalid/);
});

test("sealer accepts only the scoped authority DSN", () => {
  assert.equal(selectProductionEvidenceDsn({
    CARR_DB_AUTHORITY_JOE_URL: "postgresql://authority.invalid/db",
    DATABASE_URL: "postgresql://owner.invalid/db",
  }), "postgresql://authority.invalid/db");
  assert.throws(() => selectProductionEvidenceDsn({
    DATABASE_URL: "postgresql://owner.invalid/db",
  }), /production_evidence_authority_credential_unavailable/);
});
