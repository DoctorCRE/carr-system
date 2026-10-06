import { digest } from "./artifact-trust.js";
import {
  benchmarkPayloadOf,
  benchmarkPayloadDigest,
  proposeBenchmarkCoverageFact,
  validateBenchmarkManifest,
} from "./benchmark-minimum.v5.js";
import {
  foundationAssuranceEvidenceDigest,
  sealFoundationAssuranceEvidence,
} from "./foundation-assurance-evidence.v5.js";

export const BENCHMARK_COVERAGE_ROW_SCHEMA =
  "doctorcre-v5-benchmark-coverage-row.v1";

export class BenchmarkCoverageStoreError extends Error {
  constructor(code, detail) {
    super(code); this.name = "BenchmarkCoverageStoreError";
    this.code = code; if (detail !== undefined) this.detail = detail;
  }
}
const fail = (code, detail) => { throw new BenchmarkCoverageStoreError(code, detail); };
const copy = value => JSON.parse(JSON.stringify(value));
function freeze(value) {
  if (Array.isArray(value)) value.forEach(freeze);
  else if (value && typeof value === "object") Object.values(value).forEach(freeze);
  return Object.freeze(value);
}
function instant(value) {
  const n = Date.parse(value);
  if (!Number.isFinite(n)) fail("invalid_observed_at");
  return n;
}

export function produceStoredBenchmarkCoverage({
  evidence, config, accepted_manifest, subject_maker_identity,
  evaluator_identity, observed_at, ttl_ms,
}) {
  validateBenchmarkManifest(accepted_manifest);
  const seal = sealFoundationAssuranceEvidence(evidence, config);
  if (foundationAssuranceEvidenceDigest(evidence) !== seal.evidence_digest)
    fail("evidence_digest_mismatch");
  const payload = benchmarkPayloadOf(accepted_manifest);
  if (benchmarkPayloadDigest(payload) !== seal.benchmark_payload_digest ||
      benchmarkPayloadDigest(evidence.benchmark_payload) !== seal.benchmark_payload_digest)
    fail("accepted_benchmark_evidence_mismatch");
  if (!subject_maker_identity || subject_maker_identity.actor_id === accepted_manifest.accepted_by_identity.actor_id)
    fail("benchmark_subject_author_invalid");
  const evaluatorBound = payload.evaluator_identities.some(identity =>
    identity.actor_id === evaluator_identity?.actor_id &&
    identity.session_ref === evaluator_identity?.session_ref &&
    identity.authority_class === evaluator_identity?.authority_class);
  if (!evaluatorBound) fail("benchmark_coverage_evaluator_unbound");
  if (evaluator_identity.actor_id === subject_maker_identity.actor_id ||
      evaluator_identity.actor_id === accepted_manifest.accepted_by_identity.actor_id)
    fail("benchmark_coverage_independence_invalid");
  if (!Number.isSafeInteger(ttl_ms) || ttl_ms < 1) fail("invalid_ttl");
  const start = instant(observed_at);
  const ttl_expires_at = new Date(start + ttl_ms).toISOString();
  const fact = proposeBenchmarkCoverageFact({
    payload,
    measurements: evidence.measurements,
    evaluator_identity,
    evidence_ref: seal.evidence_ref,
    observed_at,
    ttl_expires_at,
  });
  return freeze({
    schema_version: BENCHMARK_COVERAGE_ROW_SCHEMA,
    evidence_digest: seal.evidence_digest,
    subject_maker_identity: copy(subject_maker_identity),
    fact,
    fact_digest: digest(fact),
  });
}
