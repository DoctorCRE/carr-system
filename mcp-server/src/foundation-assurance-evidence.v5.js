import { digest } from "./artifact-trust.js";
import {
  BENCHMARK_COST_VARIANCE_THRESHOLDS,
  BENCHMARK_CELL_DOMAIN_TAG,
  BENCHMARK_DEADLINE_CONTRACT,
  BENCHMARK_SLO_THRESHOLDS,
  benchmarkPayloadDigest,
  benchmarkRequiredCells,
  evaluateBenchmarkWorkloadCoverage,
} from "./benchmark-minimum.v5.js";

export const FOUNDATION_ASSURANCE_CONFIG_SCHEMA =
  "doctorcre-v5-foundation-assurance-benchmark-config.v1";
export const FOUNDATION_ASSURANCE_EVIDENCE_SCHEMA =
  "doctorcre-v5-foundation-assurance-evidence.v1";
export const FOUNDATION_ASSURANCE_SEAL_SCHEMA =
  "doctorcre-v5-foundation-assurance-evidence-seal.v1";

export const FOUNDATION_ASSURANCE_COMPARATORS = Object.freeze([
  "assurance-fabric-preactivation",
  "foundation-control-plane-preactivation",
  "global-execution-contract",
  "global-no-phi-boundary",
  "global-prompt-injection-boundary",
  "global-secrets-boundary",
  "global-source-authority",
]);
export const FOUNDATION_ASSURANCE_GITHUB_CHECKS = Object.freeze([
  "local-db-ci --class migration",
  "main canary",
  "ops/ci.sh --strict",
].sort());

const SHA = /^[0-9a-f]{40}$/;
const SHA256 = /^sha256:[0-9a-f]{64}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const HTTPS = /^https:\/\/[^\s]+$/;
const CONFIG_FIELDS = Object.freeze([
  "acknowledgement_endpoints", "arrival_patterns", "benchmark_ref", "cache_states",
  "capacity_profiles", "comparator_versions", "concurrency_levels",
  "cost_expectation_matrix_digest", "device_profiles", "draft_version",
  "hardware_profiles", "maximum_member_receipt_ttl_ms", "minimum_receipt_ttl_ms",
  "network_profiles", "outlier_rule", "p95_aggregation_method",
  "request_size_distribution", "routes", "samples_per_cell", "schema_version",
  "warmup_runs", "workload_mix",
]);
const EVIDENCE_FIELDS = Object.freeze([
  "benchmark_config_digest", "benchmark_payload", "captured_at", "comparators",
  "database", "final_provider_version", "github_checks", "measurements",
  "release", "sample_provenance", "schema_version", "source_sha", "source_tree",
  "staging_provider_version",
]);

export class FoundationAssuranceEvidenceError extends Error {
  constructor(code, detail) {
    super(code); this.name = "FoundationAssuranceEvidenceError";
    this.code = code; if (detail !== undefined) this.detail = detail;
  }
}
const fail = (code, detail) => { throw new FoundationAssuranceEvidenceError(code, detail); };
const plain = value => value && typeof value === "object" && !Array.isArray(value);
function closed(value, fields, path) {
  if (!plain(value)) fail("invalid_object", path);
  const actual = Object.keys(value).sort(), expected = [...fields].sort();
  if (actual.length !== expected.length || actual.some((key, at) => key !== expected[at]))
    fail("closed_shape", { path, missing: expected.filter(k => !actual.includes(k)),
      extra: actual.filter(k => !expected.includes(k)) });
}
function list(value, path) {
  if (!Array.isArray(value) || value.length === 0) fail("invalid_list", path);
  return value;
}
function exactSet(actual, expected, code) {
  const a = [...actual].sort(), e = [...expected].sort();
  if (a.length !== e.length || a.some((value, at) => value !== e[at])) fail(code, { expected: e, actual: a });
}
function iso(value, path) {
  const n = Date.parse(value);
  if (!Number.isFinite(n) || new Date(n).toISOString() !== value) fail("invalid_instant", path);
  return n;
}
function copy(value) { return JSON.parse(JSON.stringify(value)); }
function freeze(value) {
  if (Array.isArray(value)) value.forEach(freeze);
  else if (plain(value)) Object.values(value).forEach(freeze);
  return Object.freeze(value);
}

export function validateFoundationAssuranceBenchmarkConfig(config) {
  closed(config, CONFIG_FIELDS, "config");
  if (config.schema_version !== FOUNDATION_ASSURANCE_CONFIG_SCHEMA) fail("wrong_config_schema");
  if (!Number.isSafeInteger(config.draft_version) || config.draft_version < 1 ||
      !Number.isSafeInteger(config.samples_per_cell) || config.samples_per_cell < 20 ||
      !Number.isSafeInteger(config.warmup_runs) || config.warmup_runs < 1 ||
      !Number.isSafeInteger(config.maximum_member_receipt_ttl_ms) ||
      !Number.isSafeInteger(config.minimum_receipt_ttl_ms) ||
      config.maximum_member_receipt_ttl_ms < 1 || config.minimum_receipt_ttl_ms < 1)
    fail("invalid_config_integer");
  if (!SHA256.test(config.cost_expectation_matrix_digest || "")) fail("invalid_config_digest");
  for (const field of ["capacity_profiles","workload_mix","request_size_distribution",
    "concurrency_levels","arrival_patterns","routes","device_profiles","hardware_profiles",
    "network_profiles","cache_states","acknowledgement_endpoints","comparator_versions"]) list(config[field], `config.${field}`);
  return freeze(copy(config));
}

export function foundationAssuranceBenchmarkPayload(config, {
  subject_digest, candidate_digest, policy_digest, browser, runtime_version,
  evaluator_identities,
}) {
  const c = validateFoundationAssuranceBenchmarkConfig(config);
  const payload = {
    subject_digest, candidate_digest, policy_digest,
    capacity_profiles: c.capacity_profiles, workload_mix: c.workload_mix,
    request_size_distribution: c.request_size_distribution,
    concurrency_levels: c.concurrency_levels, arrival_patterns: c.arrival_patterns,
    routes: c.routes, browsers: [browser], runtime_versions: [runtime_version],
    device_profiles: c.device_profiles, hardware_profiles: c.hardware_profiles,
    network_profiles: c.network_profiles, cache_states: c.cache_states,
    samples_per_cell: c.samples_per_cell, warmup_runs: c.warmup_runs,
    p95_aggregation_method: c.p95_aggregation_method, outlier_rule: c.outlier_rule,
    acknowledgement_endpoints: c.acknowledgement_endpoints,
    evaluator_identities, comparator_versions: c.comparator_versions,
    slo_thresholds: BENCHMARK_SLO_THRESHOLDS,
    cost_expectation_matrix_digest: c.cost_expectation_matrix_digest,
    cost_variance_thresholds: BENCHMARK_COST_VARIANCE_THRESHOLDS,
    deadline_contract: BENCHMARK_DEADLINE_CONTRACT,
  };
  benchmarkPayloadDigest(payload);
  return freeze(copy(payload));
}

export function foundationAssuranceEvidenceDigest(evidence) {
  const preimage = copy(evidence);
  if (!plain(preimage.release)) fail("invalid_object", "evidence.release");
  preimage.release.test_evidence_ref = null;
  return digest(["doctorcre:wr95-evidence:v1", preimage]);
}

export function sealFoundationAssuranceEvidence(evidence, config) {
  closed(evidence, EVIDENCE_FIELDS, "evidence");
  if (evidence.schema_version !== FOUNDATION_ASSURANCE_EVIDENCE_SCHEMA) fail("wrong_evidence_schema");
  const c = validateFoundationAssuranceBenchmarkConfig(config);
  if (evidence.benchmark_config_digest !== digest(c)) fail("benchmark_config_digest_mismatch");
  if (!SHA.test(evidence.source_sha || "") || !SHA.test(evidence.source_tree || ""))
    fail("source_binding_invalid");
  for (const field of ["staging_provider_version","final_provider_version"])
    if (!UUID.test(evidence[field] || "")) fail("provider_binding_invalid", field);
  if (evidence.staging_provider_version === evidence.final_provider_version)
    fail("staging_and_final_provider_must_differ");
  closed(evidence.release, ["key","provider_version","source_sha","test_evidence_ref"], "evidence.release");
  if (typeof evidence.release.key !== "string" || !evidence.release.key.trim() ||
      evidence.release.provider_version !== evidence.final_provider_version ||
      evidence.release.source_sha !== evidence.source_sha) fail("release_binding_mismatch");
  closed(evidence.database, ["environment","migration","read_only","source"], "evidence.database");
  const migration = /^(\d{4})[a-z]?_[a-z0-9_]+\.sql$/.exec(evidence.database.migration || "");
  if (evidence.database.environment !== "staging" || evidence.database.read_only !== true ||
      !migration || Number(migration[1]) < 512 ||
      evidence.database.source !==
        "ops/foundation-assurance-candidate-rehearsal.py --read-foundation-facts")
    fail("database_provenance_invalid");
  exactSet(list(evidence.github_checks, "evidence.github_checks").map(row => row.name),
    FOUNDATION_ASSURANCE_GITHUB_CHECKS, "github_check_set_mismatch");
  for (const row of evidence.github_checks) {
    closed(row, ["conclusion","head_sha","name","run_id","url"], "evidence.github_checks[]");
    if (row.conclusion !== "success" || row.head_sha !== evidence.source_sha ||
        !Number.isSafeInteger(row.run_id) || row.run_id < 1 || !HTTPS.test(row.url || ""))
      fail("github_check_invalid", row.name);
  }
  exactSet(list(evidence.comparators, "evidence.comparators").map(row => row.id),
    FOUNDATION_ASSURANCE_COMPARATORS, "comparator_set_mismatch");
  for (const row of evidence.comparators) {
    closed(row, ["detail_digest","id","origin","status"], "evidence.comparators[]");
    if (row.status !== "pass" || !SHA256.test(row.detail_digest || "") ||
        !HTTPS.test(row.origin || "") || /(?:fixture|self-check|localhost|127\.0\.0\.1)/i.test(row.origin))
      fail("comparator_provenance_invalid", row.id);
  }
  const required = benchmarkRequiredCells(evidence.benchmark_payload);
  const coverage = evaluateBenchmarkWorkloadCoverage({
    payload: evidence.benchmark_payload, measurements: evidence.measurements,
  });
  const provenance = list(evidence.sample_provenance, "evidence.sample_provenance");
  if (provenance.length !== required.length) fail("sample_provenance_count_mismatch");
  const requiredDigests = new Set(required.map(cell => digest([BENCHMARK_CELL_DOMAIN_TAG, cell])));
  for (const row of provenance) {
    closed(row, ["cell_digest","origin","sample_count","warmup_count"], "evidence.sample_provenance[]");
    if (!requiredDigests.has(row.cell_digest) || !HTTPS.test(row.origin || "") ||
        /(?:fixture|self-check|localhost|127\.0\.0\.1|^data:|^file:)/i.test(row.origin) ||
        row.sample_count !== c.samples_per_cell || row.warmup_count !== c.warmup_runs)
      fail("sample_provenance_invalid");
    requiredDigests.delete(row.cell_digest);
  }
  if (requiredDigests.size) fail("sample_provenance_incomplete");
  iso(evidence.captured_at, "evidence.captured_at");
  // The release row names the seal, so it cannot participate in the digest
  // that gives that name. Bind every other release field and replace only the
  // back-reference with null in the documented preimage.
  const evidence_digest = foundationAssuranceEvidenceDigest(evidence);
  const evidence_ref = `safe:wr95-evidence/${evidence_digest.slice(7)}`;
  if (evidence.release.test_evidence_ref !== evidence_ref) fail("release_evidence_ref_mismatch");
  return freeze({
    schema_version: FOUNDATION_ASSURANCE_SEAL_SCHEMA,
    evidence_digest, evidence_ref,
    benchmark_payload_digest: benchmarkPayloadDigest(evidence.benchmark_payload),
    measurement_set_digest: digest(evidence.measurements),
    evaluation_digest: digest(coverage),
    required_cell_count: coverage.required_cell_count,
    source_sha: evidence.source_sha, source_tree: evidence.source_tree,
    staging_provider_version: evidence.staging_provider_version,
    final_provider_version: evidence.final_provider_version,
    release_key: evidence.release.key, captured_at: evidence.captured_at,
  });
}
