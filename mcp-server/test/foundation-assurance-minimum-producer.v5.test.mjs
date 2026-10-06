import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

import { digest } from "../src/artifact-trust.js";
import {
  BENCHMARK_CELL_DOMAIN_TAG,
  BENCHMARK_PAYLOAD_DOMAIN_TAG,
  MINIMUM_REQUIRED_MEMBERS,
  benchmarkRequiredCells,
} from "../src/benchmark-minimum.v5.js";
import { produceStoredBenchmarkCoverage } from
  "../src/benchmark-coverage-store.v5.js";
import {
  FOUNDATION_ASSURANCE_COMPARATORS,
  FOUNDATION_ASSURANCE_GITHUB_CHECKS,
  foundationAssuranceBenchmarkPayload,
  foundationAssuranceEvidenceDigest,
  validateFoundationAssuranceBenchmarkConfig,
} from "../src/foundation-assurance-evidence.v5.js";
import {
  evaluateFoundationAssuranceMinimum,
  foundationAssuranceMemberReceipt,
  foundationAssuranceMinimumTools,
} from "../src/foundation-assurance-minimum-producer.v5.js";
import { FOUNDATION_ASSURANCE_PRODUCER_REGISTRATION } from
  "../src/foundation-assurance-minimum-registration.v5.js";

const config = JSON.parse(await readFile(new URL(
  "../../ops/config/foundation-assurance-benchmark.v1.json", import.meta.url), "utf8"));
const identity = actor_id => ({ actor_id,
  session_ref: `session:wr95-${actor_id}`, authority_class: "review_agent" });
const author = identity("codex-benchmark-author");
const acceptor = { actor_id: "joe", session_ref: "session:wr95-joe-acceptance",
  authority_class: "verified_partner" };
const coverageIdentity = identity("codex-fa-coverage");

function fixture() {
  const payload = foundationAssuranceBenchmarkPayload(config, {
    subject_digest: `sha256:${"1".repeat(64)}`,
    candidate_digest: `sha256:${"2".repeat(64)}`,
    policy_digest: `sha256:${"3".repeat(64)}`,
    browser: { name: "Chrome", version: "140", build: "140.0.1" },
    runtime_version: "worker-2026.09",
    evaluator_identities: [coverageIdentity],
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
    github_checks: FOUNDATION_ASSURANCE_GITHUB_CHECKS.map((name, index) => ({
      name, conclusion: "success", head_sha: "a".repeat(40), run_id: index + 1,
      url: `https://github.com/jbookout/carr-system/actions/runs/${index + 1}` })),
    benchmark_config_digest: digest(validateFoundationAssuranceBenchmarkConfig(config)),
    benchmark_payload: payload,
    measurements: { schema_version: "doctorcre-v5-benchmark-measurement-set.v1",
      benchmark_payload_digest: digest([BENCHMARK_PAYLOAD_DOMAIN_TAG, payload]),
      outlier_rule: payload.outlier_rule,
      p95_aggregation_method: payload.p95_aggregation_method,
      cells: cells.map(cell => ({ cell, warmup_samples: [1], samples: Array(20).fill(1),
        excluded_sample_indexes: [] })) },
    sample_provenance: cells.map((cell, index) => ({
      cell_digest: digest([BENCHMARK_CELL_DOMAIN_TAG, cell]),
      origin: `https://staging.doctorcre.com/measurement/${index}`,
      sample_count: 20, warmup_count: 1 })),
    comparators: FOUNDATION_ASSURANCE_COMPARATORS.map(id => ({ id, status: "pass",
      detail_digest: digest({ id, ok: true }),
      origin: `https://staging.doctorcre.com/comparator/${id}` })),
    captured_at: "2026-09-14T13:30:00.000Z",
  };
  const evidenceDigest = foundationAssuranceEvidenceDigest(evidence);
  evidence.release.test_evidence_ref = `safe:wr95-evidence/${evidenceDigest.slice(7)}`;
  const accepted_manifest = { ...JSON.parse(JSON.stringify(payload)),
    benchmark_manifest_digest: digest([BENCHMARK_PAYLOAD_DOMAIN_TAG, payload]),
    accepted_by_identity: acceptor, accepted_at: "2026-09-14T13:35:00.000Z",
    status: "accepted" };
  return { payload, evidence, accepted_manifest };
}

test("stored benchmark coverage binds the accepted manifest to sealed evidence", () => {
  const f = fixture();
  const row = produceStoredBenchmarkCoverage({ ...f, config,
    subject_maker_identity: author, evaluator_identity: coverageIdentity,
    observed_at: "2026-09-14T13:36:00.000Z", ttl_ms: 3600000 });
  assert.equal(row.fact.status, "pass");
  assert.equal(row.subject_maker_identity.actor_id, "codex-benchmark-author");
  assert.match(row.fact_digest, /^sha256:[0-9a-f]{64}$/);
});

test("seven distinct oracle seats produce the exact minimum members", () => {
  const f = fixture();
  const registrations = FOUNDATION_ASSURANCE_PRODUCER_REGISTRATION.producers
    .filter(row => row.kind === "member_receipt");
  const receipts = registrations.map(row => foundationAssuranceMemberReceipt({ ...f, config,
    verb: row.verb, subject_maker_identity: author,
    producer_identity: identity(row.actor_slug),
    comparator: f.evidence.comparators.find(item => item.id === row.comparator_id),
    observed_at: "2026-09-14T13:37:00.000Z", ttl_ms: 3600000 }));
  assert.equal(receipts.length, 7);
  assert.deepEqual(receipts.map(row => row.receipt_producer_step_ref).sort(),
    MINIMUM_REQUIRED_MEMBERS.filter(row => row.output_schema_ref === "consumer-gate-receipt.v1")
      .map(row => row.step_ref).sort());
  assert.equal(new Set(receipts.map(row => row.producer_identity.actor_id)).size, 7);
});

test("minimum evaluation admits the exact current nine-part join", () => {
  const f = fixture();
  const coverage_fact = produceStoredBenchmarkCoverage({ ...f, config,
    subject_maker_identity: author, evaluator_identity: coverageIdentity,
    observed_at: "2026-09-14T13:36:00.000Z", ttl_ms: 3600000 }).fact;
  const member_receipts = FOUNDATION_ASSURANCE_PRODUCER_REGISTRATION.producers
    .filter(row => row.kind === "member_receipt")
    .map(row => foundationAssuranceMemberReceipt({ ...f, config, verb: row.verb,
      subject_maker_identity: author, producer_identity: identity(row.actor_slug),
      comparator: f.evidence.comparators.find(item => item.id === row.comparator_id),
      observed_at: "2026-09-14T13:37:00.000Z", ttl_ms: 3600000 }));
  const result = evaluateFoundationAssuranceMinimum({ ...f, config, coverage_fact,
    member_receipts, subject_maker_identity: author,
    producer_identity: identity("codex-fa-minimum"),
    gate_zero: { step_ref: "step:gate-zero-read-only-outcome",
      outcome_digest: `sha256:${"9".repeat(64)}`, observed_at: "2026-09-14T13:00:00.000Z" },
    as_of: "2026-09-14T13:40:00.000Z" });
  assert.equal(result.admissible, true);
  assert.equal(result.proposed_receipt.status, "pass");
  assert.equal(result.proposed_receipt.producer_identity.actor_id, "codex-fa-minimum");
});

test("a member seat cannot attest its own subject", () => {
  const f = fixture();
  const row = FOUNDATION_ASSURANCE_PRODUCER_REGISTRATION.producers
    .find(item => item.kind === "member_receipt");
  const same = identity(row.actor_slug);
  assert.throws(() => foundationAssuranceMemberReceipt({ ...f, config, verb: row.verb,
    subject_maker_identity: same, producer_identity: same,
    comparator: f.evidence.comparators.find(item => item.id === row.comparator_id),
    observed_at: "2026-09-14T13:37:00.000Z", ttl_ms: 3600000 }),
  /foundation_assurance_member_self_attestation/);
});

test("an ambiguous post-commit retry replays before rebuilding issuance material", async () => {
  const registration = FOUNDATION_ASSURANCE_PRODUCER_REGISTRATION.producers
    .find(row => row.kind === "member_receipt");
  const verb = registration.verb;
  const producerIdentity = identity(registration.actor_slug);
  const actor = { slug: registration.actor_slug, review: true,
    via: "review-token" };
  const f = fixture();
  const comparator = f.evidence.comparators
    .find(item => item.id === registration.comparator_id);
  const durable = new Map();
  let materialReads = 0;
  let recordCalls = 0;
  const seat = {
    async query(sql, params) {
      if (sql.includes("foundation_assurance_producer_material")) {
        materialReads += 1;
        return { rows: [{ material: { ...f, config,
          subject_maker_identity: author, comparator,
          observed_at: `2026-09-14T13:${36 + materialReads}:00.000Z` } }] };
      }
      if (sql.includes("foundation_assurance_record_production")) {
        const key = `${params[0]}:${params[1]}:${JSON.parse(params[2]).actor_id}`;
        if (params[3] === null) return { rows: [{ result: durable.get(key) || null }] };
        recordCalls += 1;
        const produced = JSON.parse(params[3]);
        const stored = durable.get(key);
        if (stored && stored.produced_digest !== digest(produced)) {
          const error = new Error("foundation_assurance_idempotency_conflict");
          error.code = "foundation_assurance_idempotency_conflict";
          throw error;
        }
        if (stored) return { rows: [{ result: stored }] };
        const result = { status: "recorded", produced_digest: digest(produced) };
        durable.set(key, result);
        return { rows: [{ result }] };
      }
      throw new Error(`unexpected query: ${sql}`);
    },
  };
  class ToolError extends Error {
    constructor(payload) { super(payload.error); Object.assign(this, payload); }
  }
  const tools = foundationAssuranceMinimumTools({
    withEnvelope: (_c, _actor, _verb, _args, callback) => callback(),
    ToolError,
    authenticatedIdentity: { receiptIdentity: () => producerIdentity },
  });
  const context = { seatConnection: callback => callback(seat),
    foundationAssuranceRuntime: { schema_version:
      "doctorcre-v5-foundation-assurance-runtime-binding.v1" } };
  const args = { idempotency_key: "00000000-0000-4000-8000-000000000099" };

  const first = await tools[verb].handler(context, actor, args);
  const replay = await tools[verb].handler(context, actor, args);

  assert.deepEqual(replay, first);
  assert.equal(materialReads, 1);
  assert.equal(recordCalls, 1);
});
