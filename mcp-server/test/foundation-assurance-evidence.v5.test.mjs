import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

import { digest } from "../src/artifact-trust.js";
import {
  BENCHMARK_CELL_DOMAIN_TAG,
  benchmarkRequiredCells,
} from "../src/benchmark-minimum.v5.js";
import {
  FOUNDATION_ASSURANCE_COMPARATORS,
  FOUNDATION_ASSURANCE_GITHUB_CHECKS,
  foundationAssuranceBenchmarkPayload,
  foundationAssuranceEvidenceDigest,
  sealFoundationAssuranceEvidence,
  validateFoundationAssuranceBenchmarkConfig,
} from "../src/foundation-assurance-evidence.v5.js";

const config = JSON.parse(await readFile(new URL(
  "../../ops/config/foundation-assurance-benchmark.v1.json", import.meta.url), "utf8"));
const evaluator = {
  actor_id: "codex-fa-coverage",
  session_ref: "session:00000000-0000-4000-8000-000000000001",
  authority_class: "review_agent",
};

function fixture() {
  const payload = foundationAssuranceBenchmarkPayload(config, {
    subject_digest: `sha256:${"1".repeat(64)}`,
    candidate_digest: `sha256:${"2".repeat(64)}`,
    policy_digest: `sha256:${"3".repeat(64)}`,
    browser: { name: "Chrome", version: "140", build: "140.0.1" },
    runtime_version: "worker-2026.09",
    evaluator_identities: [evaluator],
  });
  const cells = benchmarkRequiredCells(payload);
  const evidence = {
    schema_version: "doctorcre-v5-foundation-assurance-evidence.v1",
    source_sha: "a".repeat(40),
    source_tree: "b".repeat(40),
    staging_provider_version: "00000000-0000-4000-8000-000000000010",
    final_provider_version: "00000000-0000-4000-8000-000000000011",
    release: {
      key: "release.foundation-assurance.minimum.v1",
      provider_version: "00000000-0000-4000-8000-000000000011",
      source_sha: "a".repeat(40),
      test_evidence_ref: "pending",
    },
    database: {
      environment: "staging",
      migration: "0513_release_candidate_environment_identity.sql",
      read_only: true,
      source: "ops/foundation-assurance-candidate-rehearsal.py --read-foundation-facts",
    },
    github_checks: FOUNDATION_ASSURANCE_GITHUB_CHECKS.map((name, index) => ({
      name, conclusion: "success", head_sha: "a".repeat(40), run_id: index + 1,
      url: `https://github.com/jbookout/carr-system/actions/runs/${index + 1}`,
    })),
    benchmark_config_digest: digest(validateFoundationAssuranceBenchmarkConfig(config)),
    benchmark_payload: payload,
    measurements: {
      schema_version: "doctorcre-v5-benchmark-measurement-set.v1",
      benchmark_payload_digest: digest(["doctorcre:benchmark-payload:v1", payload]),
      outlier_rule: payload.outlier_rule,
      p95_aggregation_method: payload.p95_aggregation_method,
      cells: cells.map(cell => ({
        cell, warmup_samples: [1], samples: Array(20).fill(1),
        excluded_sample_indexes: [],
      })),
    },
    sample_provenance: cells.map((cell, index) => ({
      cell_digest: digest([BENCHMARK_CELL_DOMAIN_TAG, cell]),
      origin: `https://staging.doctorcre.com/measurement/${index}`,
      sample_count: 20, warmup_count: 1,
    })),
    comparators: FOUNDATION_ASSURANCE_COMPARATORS.map(id => ({
      id, status: "pass", detail_digest: digest({ id, ok: true }),
      origin: `https://staging.doctorcre.com/comparator/${id}`,
    })),
    captured_at: "2026-09-14T13:30:00.000Z",
  };
  const expected = foundationAssuranceEvidenceDigest(evidence);
  evidence.release.test_evidence_ref = `safe:wr95-evidence/${expected.slice(7)}`;
  return evidence;
}

test("checked-in benchmark config is closed and produces four required cells", () => {
  assert.equal(validateFoundationAssuranceBenchmarkConfig(config).benchmark_ref,
    "doctorcre-v5-foundation-assurance-production");
  assert.equal(benchmarkRequiredCells(fixture().benchmark_payload).length, 4);
});

test("seal binds source, providers, release, checks, samples and comparators", () => {
  const evidence = fixture();
  const seal = sealFoundationAssuranceEvidence(evidence, config);
  assert.equal(seal.evidence_ref, evidence.release.test_evidence_ref);
  assert.equal(seal.required_cell_count, 4);
  assert.equal(seal.final_provider_version, evidence.final_provider_version);
  assert.equal(seal.release_key, evidence.release.key);
});

test("seal accepts the receipted replacement database reader provenance", () => {
  const evidence = fixture();
  assert.doesNotThrow(() => sealFoundationAssuranceEvidence(evidence, config));

  const staleAlias = fixture();
  staleAlias.database.source = "tools/db-tap.py --project staging";
  assert.throws(() => sealFoundationAssuranceEvidence(staleAlias, config),
    /database_provenance_invalid/);

  const beforeFoundation = fixture();
  beforeFoundation.database.migration = "0511_foundation_assurance_scac_successor.sql";
  assert.throws(() => sealFoundationAssuranceEvidence(beforeFoundation, config),
    /database_provenance_invalid/);
});

test("seal rejects caller/synthetic provenance and provider drift", () => {
  const badOrigin = fixture();
  badOrigin.sample_provenance[0].origin = "file:///tmp/result.json";
  assert.throws(() => sealFoundationAssuranceEvidence(badOrigin, config),
    /sample_provenance_invalid/);
  const wrongProvider = fixture();
  wrongProvider.release.provider_version =
    "00000000-0000-4000-8000-000000000099";
  assert.throws(() => sealFoundationAssuranceEvidence(wrongProvider, config),
    /release_binding_mismatch/);
  const failedCheck = fixture();
  failedCheck.github_checks[0].conclusion = "failure";
  assert.throws(() => sealFoundationAssuranceEvidence(failedCheck, config),
    /github_check_invalid/);
});

test("seal rejects incomplete comparator and measurement sets", () => {
  const missingComparator = fixture();
  missingComparator.comparators.pop();
  assert.throws(() => sealFoundationAssuranceEvidence(missingComparator, config),
    /comparator_set_mismatch/);
  const missingCell = fixture();
  missingCell.measurements.cells.pop();
  assert.throws(() => sealFoundationAssuranceEvidence(missingCell, config),
    /benchmark_matrix_coverage_incomplete/);
});
