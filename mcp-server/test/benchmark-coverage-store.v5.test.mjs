import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

import { digest } from "../src/artifact-trust.js";
import { BENCHMARK_CELL_DOMAIN_TAG, BENCHMARK_PAYLOAD_DOMAIN_TAG,
  benchmarkRequiredCells } from "../src/benchmark-minimum.v5.js";
import { produceStoredBenchmarkCoverage } from "../src/benchmark-coverage-store.v5.js";
import { FOUNDATION_ASSURANCE_COMPARATORS, FOUNDATION_ASSURANCE_GITHUB_CHECKS,
  foundationAssuranceBenchmarkPayload, foundationAssuranceEvidenceDigest,
  validateFoundationAssuranceBenchmarkConfig } from
  "../src/foundation-assurance-evidence.v5.js";

const config = JSON.parse(await readFile(new URL(
  "../../ops/config/foundation-assurance-benchmark.v1.json", import.meta.url), "utf8"));
const identity = actor_id => ({ actor_id, session_ref: `session:wr95-${actor_id}`,
  authority_class: "review_agent" });

function fixture() {
  const evaluator = identity("codex-fa-coverage");
  const payload = foundationAssuranceBenchmarkPayload(config, {
    subject_digest: `sha256:${"1".repeat(64)}`, candidate_digest: `sha256:${"2".repeat(64)}`,
    policy_digest: `sha256:${"3".repeat(64)}`,
    browser: { name: "Chrome", version: "140", build: "140.0.1" },
    runtime_version: "00000000-0000-4000-8000-000000000010",
    evaluator_identities: [evaluator],
  });
  const cells = benchmarkRequiredCells(payload);
  const evidence = {
    schema_version: "doctorcre-v5-foundation-assurance-evidence.v1",
    source_sha: "a".repeat(40), source_tree: "b".repeat(40),
    staging_provider_version: "00000000-0000-4000-8000-000000000010",
    final_provider_version: "00000000-0000-4000-8000-000000000011",
    release: { key: "release.foundation-assurance.minimum.v1",
      provider_version: "00000000-0000-4000-8000-000000000011",
      source_sha: "a".repeat(40), test_evidence_ref: "pending" },
    database: { environment: "staging",
      migration: "0513_release_candidate_environment_identity.sql", read_only: true,
      source: "ops/foundation-assurance-candidate-rehearsal.py --read-foundation-facts" },
    github_checks: FOUNDATION_ASSURANCE_GITHUB_CHECKS.map((name, run_id) => ({
      name, conclusion: "success", head_sha: "a".repeat(40), run_id: run_id + 1,
      url: `https://github.com/jbookout/carr-system/actions/runs/${run_id + 1}` })),
    benchmark_config_digest: digest(validateFoundationAssuranceBenchmarkConfig(config)),
    benchmark_payload: payload,
    measurements: { schema_version: "doctorcre-v5-benchmark-measurement-set.v1",
      benchmark_payload_digest: digest([BENCHMARK_PAYLOAD_DOMAIN_TAG, payload]),
      outlier_rule: payload.outlier_rule, p95_aggregation_method: payload.p95_aggregation_method,
      cells: cells.map(cell => ({ cell, warmup_samples: [1], samples: Array(20).fill(1),
        excluded_sample_indexes: [] })) },
    sample_provenance: cells.map((cell, at) => ({
      cell_digest: digest([BENCHMARK_CELL_DOMAIN_TAG, cell]),
      origin: `https://staging.doctorcre.com/coverage/${at}`,
      sample_count: 20, warmup_count: 1 })),
    comparators: FOUNDATION_ASSURANCE_COMPARATORS.map(id => ({ id, status: "pass",
      detail_digest: digest([id, "live"]),
      origin: `https://staging.doctorcre.com/comparator/${id}` })),
    captured_at: "2026-09-14T13:30:00.000Z",
  };
  const evidenceDigest = foundationAssuranceEvidenceDigest(evidence);
  evidence.release.test_evidence_ref = `safe:wr95-evidence/${evidenceDigest.slice(7)}`;
  const accepted_manifest = { ...JSON.parse(JSON.stringify(payload)),
    benchmark_manifest_digest: digest([BENCHMARK_PAYLOAD_DOMAIN_TAG, payload]),
    accepted_by_identity: { actor_id: "joe", session_ref: "session:wr95-joe-acceptance",
      authority_class: "verified_partner" },
    accepted_at: "2026-09-14T13:35:00.000Z", status: "accepted" };
  return { config, evidence, accepted_manifest,
    subject_maker_identity: identity("codex-benchmark-author"), evaluator_identity: evaluator,
    observed_at: "2026-09-14T13:36:00.000Z", ttl_ms: 3600000 };
}

test("coverage row is independently recomputed from accepted sealed evidence", () => {
  const row = produceStoredBenchmarkCoverage(fixture());
  assert.equal(row.fact.status, "pass");
  assert.equal(row.fact.evaluator_identity.actor_id, "codex-fa-coverage");
  assert.match(row.fact_digest, /^sha256:[0-9a-f]{64}$/);
  assert.equal(Object.isFrozen(row), true);
});

test("coverage refuses subject/acceptor collapse and tampered evidence", () => {
  const same = fixture();
  same.subject_maker_identity = same.accepted_manifest.accepted_by_identity;
  assert.throws(() => produceStoredBenchmarkCoverage(same), /benchmark_subject_author_invalid/);
  const tampered = fixture();
  tampered.evidence.measurements.cells[0].samples[0] = 999999;
  assert.throws(() => produceStoredBenchmarkCoverage(tampered));
});

test("coverage refuses an evaluator outside the accepted payload", () => {
  const value = fixture();
  value.evaluator_identity = identity("codex-unbound-evaluator");
  assert.throws(() => produceStoredBenchmarkCoverage(value),
    /benchmark_coverage_evaluator_unbound/);
});

test("coverage evaluator cannot be the benchmark subject author", () => {
  const value = fixture();
  value.subject_maker_identity = value.evaluator_identity;
  assert.throws(() => produceStoredBenchmarkCoverage(value),
    /benchmark_coverage_independence_invalid/);
});
