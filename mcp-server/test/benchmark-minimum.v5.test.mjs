import test from "node:test";
import assert from "node:assert/strict";
import { digest } from "../src/artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import { JOURNEY_ONE_DEADLINE_CONTRACT } from "../src/journey-one-clock.v5.js";
import {
  BENCHMARK_ACCEPTANCE_ENVELOPE_FIELDS, BENCHMARK_CACHE_STATES, BENCHMARK_COST_VARIANCE_THRESHOLDS,
  BENCHMARK_COVERAGE_FACT_FIELDS, BENCHMARK_COVERAGE_FACT_SCHEMA,
  BENCHMARK_DEADLINE_CONTRACT, BENCHMARK_MANIFEST_FIELDS, BENCHMARK_MANIFEST_SCHEMA,
  BENCHMARK_METRICS, BENCHMARK_PAYLOAD_DOMAIN_TAG, BENCHMARK_PAYLOAD_FIELDS,
  BENCHMARK_SLO_THRESHOLDS, BENCHMARK_STEP_REF, CONSUMER_GATE_RECEIPT_FIELDS,
  FOUNDATION_ASSURANCE_MINIMUM_PROJECTION, GATE_ZERO_STEP_REF, MINIMUM_GATE_ID,
  MINIMUM_ORACLE_REF, MINIMUM_PRODUCER_ROLE, MINIMUM_REQUIRED_MEMBERS, MINIMUM_STEP_REF,
  P95_AGGREGATION_METHOD, WORKLOAD_WEIGHT_TOTAL_BASIS_POINTS,
  benchmarkPayloadDigest, benchmarkPayloadOf, benchmarkRequiredCells,
  createFoundationAssuranceMinimumGate, evaluateBenchmarkAdmissibility,
  evaluateBenchmarkWorkloadCoverage, foundationAssuranceMinimumNegativeAdmission,
  journeyOneClockMinimumReceiptView, nearestRankP95, proposeBenchmarkCoverageFact,
  validateBenchmarkManifest, validateBenchmarkPayload,
} from "../src/benchmark-minimum.v5.js";

// --- fixtures ---------------------------------------------------------------

const D = n => `sha256:${String(n).padStart(2, "0").repeat(32)}`;
const I = actor => ({
  actor_id: actor,
  session_ref: `session:a00-test-${actor}`,
  authority_class: actor === "joe" || actor === "dell" ? "verified_partner" : "synthetic_oracle",
});
const copy = x => JSON.parse(JSON.stringify(x));
const iso = ms => new Date(ms).toISOString();
const DAY = 86400000;

const GATE_ZERO_AT = "2026-02-01T00:00:00.000Z";
const ACCEPTED_AT = "2026-02-02T00:00:00.000Z";
const COVERAGE_AT = "2026-02-02T12:00:00.000Z";
const OBSERVED_AT = "2026-02-03T00:00:00.000Z";
const AS_OF = "2026-02-04T00:00:00.000Z";

function payload() {
  return {
    subject_digest: D(1), candidate_digest: D(2), policy_digest: D(3),
    capacity_profiles: ["baseline", "peak"],
    workload_mix: [
      { workload_id: "search", weight_basis_points: 6000, operation_mix_digest: D(10) },
      { workload_id: "detail", weight_basis_points: 4000, operation_mix_digest: D(11) },
    ],
    request_size_distribution: [{ percentile: 50, bytes: 2048 }, { percentile: 95, bytes: 16384 }],
    concurrency_levels: [1, 8],
    arrival_patterns: ["steady"],
    routes: ["/deals"],
    browsers: [{ name: "chrome", version: "141", build: "141.0.1" }],
    runtime_versions: ["node-24"],
    device_profiles: ["macbook-pro-m3"],
    hardware_profiles: ["m3-16gb"],
    network_profiles: ["broadband"],
    cache_states: ["cold", "warm"],
    samples_per_cell: 20,
    warmup_runs: 2,
    p95_aggregation_method: P95_AGGREGATION_METHOD,
    outlier_rule: "discard samples above five times the cell median",
    acknowledgement_endpoints: ["/commands/ack"],
    evaluator_identities: [I("bench-evaluator")],
    comparator_versions: ["comparator-1.4.0"],
    slo_thresholds: { ...BENCHMARK_SLO_THRESHOLDS },
    cost_expectation_matrix_digest: D(12),
    cost_variance_thresholds: { ...BENCHMARK_COST_VARIANCE_THRESHOLDS },
    deadline_contract: copy(BENCHMARK_DEADLINE_CONTRACT),
  };
}

function manifest(body = payload(), { acceptedAt = ACCEPTED_AT, acceptor = I("joe") } = {}) {
  return {
    ...copy(body),
    benchmark_manifest_digest: digest([BENCHMARK_PAYLOAD_DOMAIN_TAG, body]),
    accepted_by_identity: acceptor,
    accepted_at: acceptedAt,
    status: "accepted",
  };
}

/** One well-formed measurement set covering exactly the required matrix. */
function measurements(body = payload(), { valueFor = () => 100 } = {}) {
  return {
    schema_version: "doctorcre-v5-benchmark-measurement-set.v1",
    benchmark_payload_digest: benchmarkPayloadDigest(body),
    outlier_rule: body.outlier_rule,
    p95_aggregation_method: body.p95_aggregation_method,
    cells: benchmarkRequiredCells(body).map(cell => ({
      cell: copy(cell),
      warmup_samples: Array.from({ length: body.warmup_runs }, () => 999),
      samples: Array.from({ length: body.samples_per_cell }, () => valueFor(cell)),
      excluded_sample_indexes: [],
    })),
  };
}

/**
 * The coverage fact a trusted producer would compose from that measurement set.
 * Composed through the module's own composer so the fixture cannot drift from
 * the evaluator; a test that needs a fact the evaluator would never emit builds
 * it by mutating this one, which is the only way such a fact can exist.
 */
function coverageFact(body = payload(), overrides = {}) {
  const fact = copy(proposeBenchmarkCoverageFact({
    payload: body,
    measurements: measurements(body),
    evaluator_identity: I("bench-evaluator"),
    evidence_ref: "safe:a00-test:coverage-evidence",
    observed_at: COVERAGE_AT,
    ttl_expires_at: iso(Date.parse(COVERAGE_AT) + 7 * DAY),
  }));
  return { ...fact, ...overrides };
}

function member(spec, overrides = {}) {
  if (spec.output_schema_ref === BENCHMARK_MANIFEST_SCHEMA) {
    return { step_ref: spec.step_ref, receipt: manifest() };
  }
  return {
    step_ref: spec.step_ref,
    receipt: {
      gate_id: spec.gate_id,
      receipt_producer_step_ref: spec.step_ref,
      subject_digest: D(1), candidate_digest: D(2), policy_digest: D(3),
      environment_manifest_digest: D(4),
      subject_environment: spec.subject_environment,
      evidence_scope: spec.evidence_scope,
      subject_maker_identity: I("builder"),
      producer_identity: I(`producer-${spec.gate_id}`),
      evaluator_identity: I(`evaluator-${spec.gate_id}`),
      producer_role: spec.producer_role,
      independent_oracle_ref: spec.oracle_ref,
      oracle_version: spec.oracle_version,
      evidence_ref: `safe:a00-test:${spec.gate_id}`,
      fixture_set_digest: D(5),
      observed_at: OBSERVED_AT,
      ttl_expires_at: iso(Date.parse(OBSERVED_AT) + 7 * DAY),
      status: "pass",
      comparator: "exact closed-contract comparator",
      negative_admission_result: "all_required_denials_observed",
      ...overrides,
    },
  };
}

function snapshot() {
  return {
    schema_version: FOUNDATION_ASSURANCE_MINIMUM_PROJECTION,
    tenant: ORGANIZATION_TENANT_ID,
    as_of: AS_OF,
    binding: {
      subject_digest: D(1), candidate_digest: D(2), policy_digest: D(3),
      minimum_environment_manifest_digest: D(4),
      benchmark_manifest_digest: benchmarkPayloadDigest(payload()),
      maximum_member_receipt_ttl_ms: 30 * DAY,
      minimum_receipt_ttl_ms: 7 * DAY,
    },
    gate_zero: { step_ref: GATE_ZERO_STEP_REF, outcome_digest: D(6), observed_at: GATE_ZERO_AT },
    benchmark_coverage: coverageFact(),
    members: MINIMUM_REQUIRED_MEMBERS.map(spec => member(spec)),
    minimum_receipt_context: {
      subject_maker_identity: I("builder"),
      producer_identity: I("minimum-oracle"),
      evaluator_identity: I("minimum-evaluator"),
      evidence_ref: "safe:a00-test:minimum-join",
      fixture_set_digest: D(5),
      comparator: "exact closed minimum join comparator",
    },
  };
}

const at = (s, step) => s.members.find(m => m.step_ref === step);
const PHI = "step:global-no-phi-boundary-independent-receipt";
const SECRETS = "step:global-secrets-boundary-independent-receipt";

/**
 * Test-only authenticating capability. The evidence map is installed by trusted
 * test code; no JSON flag can cause an unknown envelope to acquire a projection.
 */
function harness() {
  const evidence = new Map();
  let serial = 0;
  const gate = createFoundationAssuranceMinimumGate({
    authenticateEvidence(envelope) {
      const found = evidence.get(digest(envelope));
      if (!found) throw new Error("synthetic-authentication-refused");
      return { envelope_digest: digest(envelope), snapshot: copy(found) };
    },
  });
  return {
    gate,
    evaluate(projection) {
      const envelope = { synthetic_evidence_ref: `a00-test-${++serial}` };
      evidence.set(digest(envelope), copy(projection));
      return gate.evaluate(envelope);
    },
  };
}
const run = projection => harness().evaluate(projection);
const refuse = (projection, code) => assert.throws(() => run(projection), e => e.code === code, code);
const refuseCall = (fn, code) => assert.throws(fn, e => e.code === code, code);

// --- the closed benchmark manifest -----------------------------------------

test("the manifest is exactly thirty closed fields split into payload and envelope", () => {
  assert.equal(BENCHMARK_MANIFEST_FIELDS.length, 30);
  assert.deepEqual([...BENCHMARK_ACCEPTANCE_ENVELOPE_FIELDS].sort(),
    ["accepted_at", "accepted_by_identity", "benchmark_manifest_digest", "status"]);
  assert.equal(BENCHMARK_PAYLOAD_FIELDS.length, 26);
  for (const field of BENCHMARK_ACCEPTANCE_ENVELOPE_FIELDS) {
    assert.ok(!BENCHMARK_PAYLOAD_FIELDS.includes(field), `${field} must not be hashed into its own digest`);
  }
  assert.deepEqual(Object.keys(manifest()).sort(), [...BENCHMARK_MANIFEST_FIELDS].sort());
});

test("every missing manifest field refuses, one at a time", () => {
  for (const field of BENCHMARK_MANIFEST_FIELDS) {
    const m = manifest();
    delete m[field];
    assert.throws(() => validateBenchmarkManifest(m), e => e.code === "closed_shape", `missing ${field}`);
  }
});

test("an extra manifest field refuses rather than being ignored", () => {
  refuseCall(() => validateBenchmarkManifest({ ...manifest(), rebaseline_note: "approved verbally" }), "closed_shape");
  refuseCall(() => validateBenchmarkManifest({ ...manifest(), obligation_decision_ids: ["Q008.D1"] }), "closed_shape");
});

test("a decision id is never evidence: it cannot ride on a manifest or a member receipt", () => {
  refuseCall(() => validateBenchmarkManifest({ ...manifest(), decision_ids: ["Q012.D1"] }), "closed_shape");
  const s = snapshot();
  at(s, PHI).receipt.obligation_decision_ids = ["Q033.D1"];
  refuse(s, "closed_shape");
});

test("workload weights must total exactly ten thousand basis points", () => {
  const p = payload();
  p.workload_mix[1].weight_basis_points = 3999;
  refuseCall(() => validateBenchmarkPayload(p), "benchmark_weight_total_mismatch");
  const over = payload();
  over.workload_mix[1].weight_basis_points = 4001;
  refuseCall(() => validateBenchmarkPayload(over), "benchmark_weight_total_mismatch");
  assert.equal(payload().workload_mix.reduce((n, w) => n + w.weight_basis_points, 0),
    WORKLOAD_WEIGHT_TOTAL_BASIS_POINTS);
});

test("a single workload may hold the whole weight but a zero-weight one may not exist", () => {
  const whole = payload();
  whole.workload_mix = [{ workload_id: "only", weight_basis_points: 10000, operation_mix_digest: D(10) }];
  assert.ok(validateBenchmarkPayload(whole));
  const zero = payload();
  zero.workload_mix = [{ workload_id: "a", weight_basis_points: 10000, operation_mix_digest: D(10) },
    { workload_id: "b", weight_basis_points: 0, operation_mix_digest: D(11) }];
  refuseCall(() => validateBenchmarkPayload(zero), "invalid_integer");
});

test("the fixed SLO, cost and clock constants are identity, not configuration", () => {
  for (const [field, mutate] of [
    ["slo_thresholds", p => { p.slo_thresholds.warm_core_navigation_p95_ms = 2500; }],
    ["slo_thresholds", p => { p.slo_thresholds.cold_lcp_p95_ms = 4500; }],
    ["slo_thresholds", p => { p.slo_thresholds.command_acknowledgement_p95_ms = 350; }],
    ["cost_variance_thresholds", p => { p.cost_variance_thresholds.warn_basis_points = 12000; }],
    ["cost_variance_thresholds", p => { p.cost_variance_thresholds.mandatory_replan_basis_points = 25000; }],
    ["deadline_contract", p => { p.deadline_contract.calendar_days = 45; }],
    ["deadline_contract", p => { p.deadline_contract.clock_origin_gate_id = "portfolio-constitution-accepted"; }],
    ["deadline_contract", p => { p.deadline_contract.reset_policy = "reset_on_replan"; }],
    ["deadline_contract", p => { p.deadline_contract.maximum_external_blocker_pause_days = 30; }],
    ["deadline_contract", p => { p.deadline_contract.kernel_obligation_decision_ids = ["Q002.D1"]; }],
  ]) {
    const p = payload();
    mutate(p);
    assert.throws(() => validateBenchmarkPayload(p), e => e.code === "benchmark_constant_mismatch", field);
  }
  const method = payload();
  method.p95_aggregation_method = "mean-of-cells";
  refuseCall(() => validateBenchmarkPayload(method), "benchmark_constant_mismatch");
});

test("a missing or extra nested constant field refuses as a closed shape", () => {
  const dropped = payload();
  delete dropped.slo_thresholds.cold_lcp_p95_ms;
  refuseCall(() => validateBenchmarkPayload(dropped), "closed_shape");
  const added = payload();
  added.deadline_contract.grace_days = 5;
  refuseCall(() => validateBenchmarkPayload(added), "closed_shape");
  const nestedWorkload = payload();
  nestedWorkload.workload_mix[0].note = "primary";
  refuseCall(() => validateBenchmarkPayload(nestedWorkload), "closed_shape");
  const nestedBrowser = payload();
  delete nestedBrowser.browsers[0].build;
  refuseCall(() => validateBenchmarkPayload(nestedBrowser), "closed_shape");
});

test("wildcard, unversioned and under-sampled manifests refuse", () => {
  const emptyRoute = payload();
  emptyRoute.routes = [""];
  refuseCall(() => validateBenchmarkPayload(emptyRoute), "invalid_list_item");
  const noComparator = payload();
  noComparator.comparator_versions = [];
  refuseCall(() => validateBenchmarkPayload(noComparator), "invalid_list");
  const thin = payload();
  thin.samples_per_cell = 19;
  refuseCall(() => validateBenchmarkPayload(thin), "invalid_integer");
  const noWarmup = payload();
  noWarmup.warmup_runs = 0;
  refuseCall(() => validateBenchmarkPayload(noWarmup), "invalid_integer");
  const oneCache = payload();
  oneCache.cache_states = ["warm"];
  refuseCall(() => validateBenchmarkPayload(oneCache), "invalid_list");
  const unknownCache = payload();
  unknownCache.cache_states = ["cold", "lukewarm"];
  refuseCall(() => validateBenchmarkPayload(unknownCache), "benchmark_unknown_cache_state");
  assert.deepEqual([...BENCHMARK_CACHE_STATES], ["cold", "warm"]);
});

test("duplicate dimension members refuse because a cell could not resolve one weight", () => {
  const workload = payload();
  workload.workload_mix = [
    { workload_id: "search", weight_basis_points: 5000, operation_mix_digest: D(10) },
    { workload_id: "search", weight_basis_points: 5000, operation_mix_digest: D(11) },
  ];
  refuseCall(() => validateBenchmarkPayload(workload), "benchmark_duplicate_workload_id");
  const browser = payload();
  browser.browsers = [browser.browsers[0], copy(browser.browsers[0])];
  refuseCall(() => validateBenchmarkPayload(browser), "benchmark_duplicate_browser");
  const routes = payload();
  routes.routes = ["/deals", "/deals"];
  refuseCall(() => validateBenchmarkPayload(routes), "duplicate_list_item");
  const evaluators = payload();
  evaluators.evaluator_identities = [I("bench-evaluator"), I("bench-evaluator")];
  refuseCall(() => validateBenchmarkPayload(evaluators), "benchmark_duplicate_evaluator_identity");
  const percentiles = payload();
  percentiles.request_size_distribution = [{ percentile: 50, bytes: 1 }, { percentile: 50, bytes: 2 }];
  refuseCall(() => validateBenchmarkPayload(percentiles), "benchmark_duplicate_request_size_percentile");
});

test("the payload digest omits the acceptance envelope and is stable across key order", () => {
  const body = payload();
  const expected = digest([BENCHMARK_PAYLOAD_DOMAIN_TAG, body]);
  assert.equal(benchmarkPayloadDigest(body), expected);
  const shuffled = Object.fromEntries(Object.keys(body).reverse().map(k => [k, body[k]]));
  assert.equal(benchmarkPayloadDigest(shuffled), expected);
  const m = manifest(body);
  assert.equal(m.benchmark_manifest_digest, expected);
  assert.deepEqual(benchmarkPayloadOf(m), body);
  // Re-accepting the same payload with a different acceptor or instant does not
  // move the digest: the envelope is authenticated separately, never hashed in.
  assert.equal(manifest(body, { acceptor: I("dell"), acceptedAt: "2026-03-09T00:00:00.000Z" })
    .benchmark_manifest_digest, expected);
});

test("a mutated payload under a recorded digest refuses as a wrong digest", () => {
  const m = manifest();
  m.routes = ["/deals", "/prospecting"];
  refuseCall(() => validateBenchmarkManifest(m), "benchmark_manifest_digest_mismatch");
  const forged = manifest();
  forged.benchmark_manifest_digest = D(99);
  refuseCall(() => validateBenchmarkManifest(forged), "benchmark_manifest_digest_mismatch");
});

test("acceptance requires a verified partner and the one permitted status", () => {
  refuseCall(() => validateBenchmarkManifest(manifest(payload(), { acceptor: I("codex") })), "verified_partner_required");
  const claimed = manifest();
  claimed.accepted_by_identity = { actor_id: "codex", session_ref: "session:a00-test-codex", authority_class: "verified_partner" };
  refuseCall(() => validateBenchmarkManifest(claimed), "verified_partner_required");
  const pretend = manifest();
  pretend.status = "pass";
  refuseCall(() => validateBenchmarkManifest(pretend), "benchmark_status_not_accepted");
  assert.ok(validateBenchmarkManifest(manifest(payload(), { acceptor: I("dell") })));
});

test("no caller boolean can stand in for a verified human acceptance", () => {
  const m = manifest();
  m.accepted_by_identity.authority_class = "sponsored_agent";
  refuseCall(() => validateBenchmarkManifest(m), "verified_partner_required");
  refuseCall(() => validateBenchmarkManifest({ ...manifest(), verified_human: true }), "closed_shape");
});

// --- admissibility is a proposal, never an acceptance -----------------------

test("benchmark admissibility proposes a digest and accepts nothing", () => {
  const body = payload();
  const result = evaluateBenchmarkAdmissibility({ payload: body });
  assert.equal(result.schema_admissible, true);
  assert.equal(result.proposed_benchmark_manifest_digest, benchmarkPayloadDigest(body));
  assert.equal(result.accepted, false);
  assert.equal(result.acceptance_recorded_here, false);
  assert.equal(result.durable_acceptance_required, true);
  assert.equal(result.acceptance_required_from_producer_role, "verified_partner_benchmark_authority");
  assert.equal(result.acceptance_required_after_step_ref, GATE_ZERO_STEP_REF);
  assert.equal(result.authority_granted, false);
  assert.equal(result.effects.acceptances, 0);
  assert.equal(result.effects.creates_effect, false);
  assert.equal(result.workload_coverage, null);
  assert.equal(result.workload_coverage_evaluated, false);
  assert.ok(Object.isFrozen(result));
});

// --- the workload matrix and the nearest-rank p95 rule ----------------------

test("nearest-rank p95 is the one-based ceiling(0.95 * n) observation", () => {
  assert.equal(nearestRankP95(Array.from({ length: 20 }, (_, i) => i + 1)), 19);
  assert.equal(nearestRankP95(Array.from({ length: 100 }, (_, i) => i + 1)), 95);
  assert.equal(nearestRankP95(Array.from({ length: 40 }, (_, i) => i + 1)), 38);
  assert.equal(nearestRankP95([5, 1, 3]), 5);
  // Order of presentation cannot change the answer.
  assert.equal(nearestRankP95([9, 2, 7, 4, 1]), 9);
});

test("the required matrix is the declared cross product and every cell must be measured", () => {
  const body = payload();
  const cells = benchmarkRequiredCells(body);
  // 2 capacity x 2 workloads x 2 concurrency x 1 arrival x 1 browser x 1 runtime
  // x 1 device x 1 hardware x 1 network = 8 environment cells, crossed with
  // 1 warm route + 1 cold route + 1 endpoint over both cache states.
  assert.equal(cells.length, 8 * (1 + 1 + 2));
  assert.equal(new Set(cells.map(c => digest(c))).size, cells.length);
  for (const metric of Object.keys(BENCHMARK_METRICS)) {
    assert.ok(cells.some(c => c.metric === metric), metric);
  }
  const result = evaluateBenchmarkWorkloadCoverage({ payload: body, measurements: measurements(body) });
  assert.equal(result.coverage_complete, true);
  assert.equal(result.required_cell_count, cells.length);
  assert.equal(result.measured_cell_count, cells.length);
  assert.equal(result.all_required_cells_meet_slo, true);
  assert.equal(result.weights_applied_to_thresholds, false);
});

test("a missing, unrequired or duplicated cell denies coverage", () => {
  const body = payload();
  const short = measurements(body);
  short.cells.pop();
  refuseCall(() => evaluateBenchmarkWorkloadCoverage({ payload: body, measurements: short }),
    "benchmark_matrix_coverage_incomplete");
  const extra = measurements(body);
  extra.cells.push({ ...copy(extra.cells[0]), cell: { ...copy(extra.cells[0].cell), capacity_profile: "unlisted" } });
  refuseCall(() => evaluateBenchmarkWorkloadCoverage({ payload: body, measurements: extra }),
    "benchmark_unrequired_cell");
  const twice = measurements(body);
  twice.cells.push(copy(twice.cells[0]));
  refuseCall(() => evaluateBenchmarkWorkloadCoverage({ payload: body, measurements: twice }),
    "benchmark_duplicate_cell");
});

test("each metric is measured against its own fixed SLO", () => {
  const body = payload();
  for (const [metric, threshold] of [
    ["core_navigation_ms", BENCHMARK_SLO_THRESHOLDS.warm_core_navigation_p95_ms],
    ["lcp_ms", BENCHMARK_SLO_THRESHOLDS.cold_lcp_p95_ms],
    ["command_acknowledgement_ms", BENCHMARK_SLO_THRESHOLDS.command_acknowledgement_p95_ms],
  ]) {
    const met = measurements(body, { valueFor: cell => (cell.metric === metric ? threshold : 10) });
    assert.equal(evaluateBenchmarkWorkloadCoverage({ payload: body, measurements: met })
      .all_required_cells_meet_slo, true, `${metric} exactly at threshold passes`);
    const missed = measurements(body, { valueFor: cell => (cell.metric === metric ? threshold + 1 : 10) });
    assert.throws(() => evaluateBenchmarkWorkloadCoverage({ payload: body, measurements: missed }),
      e => e.code === "benchmark_slo_not_met" && e.detail.failing_cells[0].slo_key === BENCHMARK_METRICS[metric].slo_key,
      metric);
  }
});

test("a low workload weight never relaxes a cell's threshold", () => {
  const body = payload();
  body.workload_mix = [
    { workload_id: "search", weight_basis_points: 9999, operation_mix_digest: D(10) },
    { workload_id: "rare", weight_basis_points: 1, operation_mix_digest: D(11) },
  ];
  const met = measurements(body, {
    valueFor: cell => (cell.workload_id === "rare" && cell.metric === "command_acknowledgement_ms" ? 301 : 10),
  });
  refuseCall(() => evaluateBenchmarkWorkloadCoverage({ payload: body, measurements: met }), "benchmark_slo_not_met");
});

test("warmup runs must be exercised and excluded, and exclusions cannot shrink a cell below its floor", () => {
  const body = payload();
  const noWarmup = measurements(body);
  noWarmup.cells[0].warmup_samples = [];
  refuseCall(() => evaluateBenchmarkWorkloadCoverage({ payload: body, measurements: noWarmup }),
    "benchmark_warmup_run_count_mismatch");
  // A warmup observation is never a sample: a slow warmup cannot fail a cell.
  const slowWarmup = measurements(body);
  slowWarmup.cells.forEach(c => { c.warmup_samples = c.warmup_samples.map(() => 60000); });
  assert.equal(evaluateBenchmarkWorkloadCoverage({ payload: body, measurements: slowWarmup })
    .all_required_cells_meet_slo, true);
  const thin = measurements(body);
  thin.cells[0].excluded_sample_indexes = [0];
  refuseCall(() => evaluateBenchmarkWorkloadCoverage({ payload: body, measurements: thin }),
    "benchmark_sample_floor_not_met");
  // With headroom above the floor an exclusion is permitted, and it excludes:
  // twenty-two samples put the nearest-rank index on the twenty-first, so the
  // two outliers decide the cell unless they are actually removed.
  const wide = measurements(body, { valueFor: () => 10 });
  wide.cells.forEach(c => {
    c.samples = [...c.samples, 9000, 9000];
    c.excluded_sample_indexes = [c.samples.length - 2, c.samples.length - 1];
  });
  assert.equal(evaluateBenchmarkWorkloadCoverage({ payload: body, measurements: wide })
    .all_required_cells_meet_slo, true);
  const unexcluded = measurements(body, { valueFor: () => 10 });
  unexcluded.cells.forEach(c => { c.samples = [...c.samples, 9000, 9000]; });
  refuseCall(() => evaluateBenchmarkWorkloadCoverage({ payload: body, measurements: unexcluded }),
    "benchmark_slo_not_met");
});

test("a measurement set must quote the accepted payload, rule and method exactly", () => {
  const body = payload();
  const wrongPayload = measurements(body);
  wrongPayload.benchmark_payload_digest = D(98);
  refuseCall(() => evaluateBenchmarkWorkloadCoverage({ payload: body, measurements: wrongPayload }),
    "measurement_payload_binding_mismatch");
  const wrongRule = measurements(body);
  wrongRule.outlier_rule = "discard whatever looks wrong";
  refuseCall(() => evaluateBenchmarkWorkloadCoverage({ payload: body, measurements: wrongRule }),
    "measurement_outlier_rule_mismatch");
  const wrongMethod = measurements(body);
  wrongMethod.p95_aggregation_method = "mean";
  refuseCall(() => evaluateBenchmarkWorkloadCoverage({ payload: body, measurements: wrongMethod }),
    "measurement_p95_method_mismatch");
  const openSet = measurements(body);
  openSet.note = "rerun of the third pass";
  refuseCall(() => evaluateBenchmarkWorkloadCoverage({ payload: body, measurements: openSet }), "closed_shape");
});

test("admissibility carries the coverage verdict when measurements are supplied", () => {
  const body = payload();
  const result = evaluateBenchmarkAdmissibility({ payload: body, measurements: measurements(body) });
  assert.equal(result.workload_coverage_evaluated, true);
  assert.equal(result.workload_coverage.coverage_complete, true);
  assert.equal(result.accepted, false);
  refuseCall(() => evaluateBenchmarkAdmissibility({
    payload: body,
    measurements: measurements(body, { valueFor: () => 99999 }),
  }), "benchmark_slo_not_met");
});

// --- the Foundation/Assurance minimum join ----------------------------------

test("the join consumes exactly the eight declared gates and Gate Zero", () => {
  assert.equal(MINIMUM_REQUIRED_MEMBERS.length, 8);
  assert.deepEqual(MINIMUM_REQUIRED_MEMBERS.map(m => m.gate_id).sort(), [
    "assurance-fabric-child-accepted", "benchmark-contract-accepted",
    "foundation-control-plane-child-accepted", "global-execution-contract-accepted",
    "global-phi-boundary-accepted", "global-prompt-injection-boundary-accepted",
    "global-secrets-boundary-accepted", "global-source-authority-accepted",
  ]);
  const result = run(snapshot());
  assert.equal(result.admissible, true);
  assert.deepEqual(result.satisfied_gate_ids, MINIMUM_REQUIRED_MEMBERS.map(m => m.gate_id).sort());
  assert.equal(result.gate_zero_outcome_digest, D(6));
  assert.equal(result.gate_id, MINIMUM_GATE_ID);
  assert.equal(result.producer_step_ref, MINIMUM_STEP_REF);
  assert.equal(result.combiner, "all_current_independent_pass");
  assert.deepEqual(result.obligation_decision_ids, ["Q012.D2"]);
});

test("a valid join proposes a receipt and issues nothing", () => {
  const result = run(snapshot());
  assert.deepEqual(Object.keys(result.proposed_receipt).sort(), [...CONSUMER_GATE_RECEIPT_FIELDS].sort());
  assert.equal(result.proposed_receipt.gate_id, MINIMUM_GATE_ID);
  assert.equal(result.proposed_receipt.receipt_producer_step_ref, MINIMUM_STEP_REF);
  assert.equal(result.proposed_receipt.producer_role, MINIMUM_PRODUCER_ROLE);
  assert.equal(result.proposed_receipt.independent_oracle_ref, MINIMUM_ORACLE_REF);
  assert.equal(result.proposed_receipt.oracle_version, "1.0.0");
  assert.equal(result.proposed_receipt.subject_environment, "candidate");
  assert.equal(result.proposed_receipt.evidence_scope, "candidate-and-test");
  assert.equal(result.proposed_receipt.status, "pass");
  assert.equal(result.proposed_receipt.observed_at, AS_OF);
  assert.equal(result.proposed_receipt.ttl_expires_at, iso(Date.parse(AS_OF) + 7 * DAY));
  assert.equal(result.proposed_receipt.negative_admission_result, "all_required_denials_observed");

  assert.equal(result.receipt_state, "proposed_not_issued");
  assert.equal(result.issued, false);
  assert.equal(result.persisted, false);
  assert.equal(result.durable_receipt_issuance_required, true);
  assert.equal(result.proposed_receipt_reference_digest, digest(result.proposed_receipt));
  assert.ok(Object.isFrozen(result) && Object.isFrozen(result.proposed_receipt));
});

test("source construction cannot start the clock, accept the benchmark or claim any effect", () => {
  const result = run(snapshot());
  assert.equal(result.clock_started, false);
  assert.equal(result.journey_one_clock_origin_available, false);
  assert.equal(result.journey_one_clock_origin_gate_id, MINIMUM_GATE_ID);
  assert.equal(result.benchmark_accepted_here, false);
  assert.equal(result.product_activation_authorized, false);
  assert.equal(result.authority_granted, false);
  assert.deepEqual({ ...result.effects }, {
    creates_effect: false, database_writes: 0, network_calls: 0, provider_actions: 0,
    notifications: 0, schedules: 0, deployments: 0, activations: 0, acceptances: 0,
  });
  // Two evaluations of the same projection are byte-identical: nothing accrued.
  assert.equal(digest(run(snapshot())), digest(result));
});

test("the join is unreachable without an installed authenticating dependency", () => {
  refuseCall(() => createFoundationAssuranceMinimumGate(), "authenticated_verifier_required");
  refuseCall(() => createFoundationAssuranceMinimumGate({}), "authenticated_verifier_required");
  refuseCall(() => createFoundationAssuranceMinimumGate({ authenticateEvidence: true }), "authenticated_verifier_required");
  // A projection is not reachable by asserting one in the request.
  const gate = createFoundationAssuranceMinimumGate({ authenticateEvidence: () => { throw new Error("refused"); } });
  assert.throws(() => gate.evaluate({ snapshot: snapshot() }), /refused/);
});

test("authentication must bind to the exact envelope bytes supplied", () => {
  const gate = createFoundationAssuranceMinimumGate({
    authenticateEvidence: () => ({ envelope_digest: D(97), snapshot: snapshot() }),
  });
  refuseCall(() => gate.evaluate({ synthetic_evidence_ref: "a00-test-unbound" }), "verification_binding_mismatch");
  const open = createFoundationAssuranceMinimumGate({
    authenticateEvidence: envelope => ({ envelope_digest: digest(envelope), snapshot: snapshot(), trusted: true }),
  });
  refuseCall(() => open.evaluate({ synthetic_evidence_ref: "a00-test-open" }), "closed_shape");
});

// --- missing, extra, duplicate and unrequired members -----------------------

test("a missing member denies, whichever member it is", () => {
  for (const spec of MINIMUM_REQUIRED_MEMBERS) {
    const s = snapshot();
    s.members = s.members.filter(m => m.step_ref !== spec.step_ref);
    assert.throws(() => run(s), e => e.code === "missing_required_member" &&
      e.detail.missing.includes(spec.step_ref), spec.step_ref);
  }
});

test("an unrequired producer step is a claim about this gate, not spare evidence", () => {
  const s = snapshot();
  s.members.push({
    step_ref: "step:journey-one-contract-binding-receipt",
    receipt: copy(at(s, PHI).receipt),
  });
  refuse(s, "unknown_member_step");
});

test("a duplicated member, receipt or gate denies the join", () => {
  const duplicateStep = snapshot();
  duplicateStep.members.push(copy(at(duplicateStep, PHI)));
  refuse(duplicateStep, "duplicate_member_step");
  const twoGates = snapshot();
  at(twoGates, SECRETS).receipt.gate_id = "global-phi-boundary-accepted";
  refuse(twoGates, "member_gate_mismatch");
  const empty = snapshot();
  empty.members = [];
  refuse(empty, "missing_required_member");
});

// --- currentness, binding, scope and provenance -----------------------------

test("a stale, future or over-long member receipt denies", () => {
  const stale = snapshot();
  at(stale, PHI).receipt.ttl_expires_at = "2026-02-03T06:00:00.000Z";
  refuse(stale, "member_receipt_not_current");
  const future = snapshot();
  at(future, PHI).receipt.observed_at = "2026-02-05T00:00:00.000Z";
  refuse(future, "member_receipt_observed_after_reference");
  const overlong = snapshot();
  at(overlong, PHI).receipt.ttl_expires_at = iso(Date.parse(OBSERVED_AT) + 400 * DAY);
  refuse(overlong, "member_receipt_ttl_policy_exceeded");
  const inverted = snapshot();
  at(inverted, PHI).receipt.ttl_expires_at = OBSERVED_AT;
  refuse(inverted, "member_receipt_window_invalid");
  const unreadable = snapshot();
  at(unreadable, PHI).receipt.observed_at = "2026-02-31T00:00:00.000Z";
  refuse(unreadable, "invalid_timestamp");
});

test("every common digest must match the bound subject, candidate, policy and environment", () => {
  for (const field of ["subject_digest", "candidate_digest", "policy_digest", "environment_manifest_digest"]) {
    const s = snapshot();
    at(s, PHI).receipt[field] = D(77);
    assert.throws(() => run(s), e => e.code === "member_binding_mismatch" && e.detail.field === field, field);
  }
  const malformed = snapshot();
  at(malformed, PHI).receipt.policy_digest = "sha256:not-a-digest";
  refuse(malformed, "invalid_digest");
});

test("a valid enum from another scope or environment is substitution, not evidence", () => {
  const scope = snapshot();
  at(scope, PHI).receipt.evidence_scope = "production";
  refuse(scope, "member_scope_mismatch");
  const environment = snapshot();
  at(environment, PHI).receipt.subject_environment = "staging";
  refuse(environment, "member_scope_mismatch");
  const unregistered = snapshot();
  at(unregistered, PHI).receipt.evidence_scope = "candidate-and-vibes";
  refuse(unregistered, "member_scope_unregistered");
});

test("producer role, oracle and step provenance are matched against the registry entry", () => {
  const role = snapshot();
  at(role, PHI).receipt.producer_role = "independent_secret_boundary_oracle";
  refuse(role, "member_role_mismatch");
  const invented = snapshot();
  at(invented, PHI).receipt.producer_role = "independent_benchmark_reviewer";
  refuse(invented, "member_role_unregistered");
  const oracle = snapshot();
  at(oracle, PHI).receipt.independent_oracle_ref = "oracle:gate-producer:global-secrets";
  refuse(oracle, "member_oracle_mismatch");
  const version = snapshot();
  at(version, PHI).receipt.oracle_version = "1.0.1";
  refuse(version, "member_oracle_mismatch");
  const step = snapshot();
  at(step, PHI).receipt.receipt_producer_step_ref = "step:global-secrets-boundary-independent-receipt";
  refuse(step, "member_step_mismatch");
});

test("a non-passing, unknown or quarantined member denies, and so does a missing denial set", () => {
  for (const status of ["fail", "unknown", "stale", "quarantined"]) {
    const s = snapshot();
    at(s, PHI).receipt.status = status;
    assert.throws(() => run(s), e => e.code === "member_not_passing", status);
  }
  const invented = snapshot();
  at(invented, PHI).receipt.status = "passed-with-notes";
  refuse(invented, "member_status_unregistered");
  const noDenials = snapshot();
  at(noDenials, PHI).receipt.negative_admission_result = "not_evaluated";
  refuse(noDenials, "member_negative_admission_missing");
});

test("a contract-presence receipt with no comparator or evidence pointer denies", () => {
  const short = snapshot();
  at(short, PHI).receipt.comparator = "eq";
  refuse(short, "invalid_text");
  const unsafe = snapshot();
  at(unsafe, PHI).receipt.evidence_ref = "https://example.invalid/evidence";
  refuse(unsafe, "invalid_evidence_ref");
});

// --- independence -----------------------------------------------------------

test("a member that reviews its own maker is self-attested", () => {
  for (const seat of ["producer_identity", "evaluator_identity"]) {
    const s = snapshot();
    at(s, PHI).receipt[seat] = copy(at(s, PHI).receipt.subject_maker_identity);
    assert.throws(() => run(s), e => e.code === "member_self_attestation" && e.detail.field === seat, seat);
  }
  // Sharing only the session is still one seat.
  const session = snapshot();
  at(session, PHI).receipt.producer_identity.session_ref = at(session, PHI).receipt.subject_maker_identity.session_ref;
  refuse(session, "member_self_attestation");
});

test("two consumed gates may not be produced by one seat wearing two roles", () => {
  const s = snapshot();
  at(s, SECRETS).receipt.producer_identity = copy(at(s, PHI).receipt.producer_identity);
  refuse(s, "member_producers_not_distinct");
});

test("the minimum oracle may not have produced or evaluated the evidence it consumes", () => {
  const producer = snapshot();
  producer.minimum_receipt_context.producer_identity = copy(at(producer, PHI).receipt.producer_identity);
  refuse(producer, "minimum_producer_not_independent");
  const evaluator = snapshot();
  evaluator.minimum_receipt_context.evaluator_identity = copy(at(evaluator, PHI).receipt.evaluator_identity);
  refuse(evaluator, "minimum_producer_not_independent");
  const maker = snapshot();
  maker.minimum_receipt_context.producer_identity = copy(maker.minimum_receipt_context.subject_maker_identity);
  refuse(maker, "minimum_self_attestation");
});

test("the human benchmark authority is distinct from the builder, the reviewers and the oracle", () => {
  const acceptor = I("joe");
  for (const install of [
    s => { s.minimum_receipt_context.subject_maker_identity = copy(acceptor); },
    s => { s.minimum_receipt_context.producer_identity = copy(acceptor); },
    s => { s.minimum_receipt_context.evaluator_identity = copy(acceptor); },
    s => { at(s, PHI).receipt.producer_identity = copy(acceptor); },
    s => { at(s, PHI).receipt.evaluator_identity = copy(acceptor); },
  ]) {
    const s = snapshot();
    install(s);
    assert.throws(() => run(s), e => e.code === "benchmark_authority_not_independent");
  }
  // Either verified partner may hold the benchmark authority: independence is
  // per seat, not a prohibition on a partner appearing in the record.
  const other = snapshot();
  at(other, BENCHMARK_STEP_REF).receipt = manifest(payload(), { acceptor: I("dell") });
  assert.equal(run(other).admissible, true);
});

// --- the benchmark member inside the join -----------------------------------

test("the join binds the one exact accepted manifest digest", () => {
  const replacement = payload();
  replacement.routes = ["/deals", "/prospecting"];
  const s = snapshot();
  at(s, BENCHMARK_STEP_REF).receipt = manifest(replacement);
  refuse(s, "benchmark_acceptance_mismatched");
  const forged = snapshot();
  at(forged, BENCHMARK_STEP_REF).receipt.capacity_profiles = ["baseline"];
  refuse(forged, "benchmark_manifest_digest_mismatch");
});

test("the benchmark subject binding must match the join's own", () => {
  const other = payload();
  other.candidate_digest = D(66);
  const s = snapshot();
  s.binding.benchmark_manifest_digest = benchmarkPayloadDigest(other);
  at(s, BENCHMARK_STEP_REF).receipt = manifest(other);
  assert.throws(() => run(s), e => e.code === "benchmark_binding_mismatch" && e.detail.field === "candidate_digest");
});

test("benchmark acceptance before or at Gate Zero is refused, and after it is admitted", () => {
  const before = snapshot();
  at(before, BENCHMARK_STEP_REF).receipt = manifest(payload(), { acceptedAt: "2026-01-31T00:00:00.000Z" });
  refuse(before, "benchmark_accepted_before_gate_zero");
  const exactly = snapshot();
  at(exactly, BENCHMARK_STEP_REF).receipt = manifest(payload(), { acceptedAt: GATE_ZERO_AT });
  refuse(exactly, "benchmark_accepted_before_gate_zero");
  const after = snapshot();
  at(after, BENCHMARK_STEP_REF).receipt = manifest(payload(), { acceptedAt: "2026-02-01T00:00:00.001Z" });
  assert.equal(run(after).admissible, true);
  const ahead = snapshot();
  at(ahead, BENCHMARK_STEP_REF).receipt = manifest(payload(), { acceptedAt: "2026-02-05T00:00:00.000Z" });
  refuse(ahead, "benchmark_accepted_at_or_after_reference");
});

test("acceptance AT the reference instant is refused, because M01 needs it strictly earlier", () => {
  // The join stamps the receipt it proposes with observed_at = as_of, and the
  // clock kernel refuses a benchmark accepted at or after the origin receipt's
  // own observation. Admitting the equal instant here would propose a receipt
  // that can never become a clock origin, so the two boundaries are one.
  const equal = snapshot();
  at(equal, BENCHMARK_STEP_REF).receipt = manifest(payload(), { acceptedAt: AS_OF });
  // The coverage fact beside it is complete, current and passing: a fully
  // measured benchmark accepted at the reference instant is still refused, and
  // the acceptance boundary is not something coverage evidence can buy past.
  assert.equal(equal.benchmark_coverage.coverage_complete, true);
  assert.equal(equal.benchmark_coverage.all_required_cells_meet_slo, true);
  refuse(equal, "benchmark_accepted_at_or_after_reference");
  // One millisecond earlier is admitted, so the rule is exclusive rather than
  // merely strict-looking, and the admitted instant is the one reported.
  const before = snapshot();
  const acceptedAt = iso(Date.parse(AS_OF) - 1);
  at(before, BENCHMARK_STEP_REF).receipt = manifest(payload(), { acceptedAt });
  const result = run(before);
  assert.equal(result.admissible, true);
  assert.equal(result.benchmark_accepted_at, acceptedAt);
  assert.ok(Date.parse(result.benchmark_accepted_at) < Date.parse(result.proposed_receipt.observed_at));
});

// --- the authenticated benchmark coverage fact ------------------------------

test("the join consumes an authenticated coverage fact and republishes what it bound", () => {
  const result = run(snapshot());
  const fact = coverageFact();
  assert.deepEqual(Object.keys(fact).sort(), [...BENCHMARK_COVERAGE_FACT_FIELDS].sort());
  assert.equal(fact.schema_version, BENCHMARK_COVERAGE_FACT_SCHEMA);
  assert.equal(result.admissible, true);
  assert.equal(result.benchmark_coverage_measurement_set_digest, fact.measurement_set_digest);
  assert.equal(result.benchmark_coverage_evaluation_digest, fact.evaluation_digest);
  assert.equal(result.benchmark_coverage_observed_at, COVERAGE_AT);
  assert.equal(result.benchmark_coverage_required_cell_count, benchmarkRequiredCells(payload()).length);
  // The join evaluated no measurements and verified no stored artifact; it bound
  // a fact and says so in both directions.
  assert.equal(result.benchmark_coverage_evaluated_here, false);
  assert.equal(result.durable_coverage_verification_required, true);
  assert.equal(result.authority_granted, false);
  // And none of it touches the r7-exact receipt or its digest.
  assert.deepEqual(Object.keys(result.proposed_receipt).sort(), [...CONSUMER_GATE_RECEIPT_FIELDS].sort());
  assert.equal(result.proposed_receipt_reference_digest, digest(result.proposed_receipt));
});

test("a projection carrying no coverage fact refuses by naming the fact it lacks", () => {
  // This is the gap the audit found: an accepted manifest with no measurement
  // evidence used to be indistinguishable here from a measured one. It now
  // refuses, and it refuses with the concrete missing fact rather than by
  // defaulting the manifest to unevaluated and passing anyway.
  const absent = snapshot();
  delete absent.benchmark_coverage;
  assert.throws(() => run(absent),
    e => e.code === "closed_shape" && e.detail.missing.includes("benchmark_coverage"));
  const nulled = snapshot();
  nulled.benchmark_coverage = null;
  refuse(nulled, "invalid_object");
  for (const field of BENCHMARK_COVERAGE_FACT_FIELDS) {
    const s = snapshot();
    delete s.benchmark_coverage[field];
    assert.throws(() => run(s),
      e => e.code === "closed_shape" && e.detail.missing.includes(field), field);
  }
});

test("a coverage fact bound to another payload, rule or method refuses", () => {
  const wrongPayload = snapshot();
  wrongPayload.benchmark_coverage.benchmark_payload_digest = D(96);
  refuse(wrongPayload, "benchmark_coverage_payload_binding_mismatch");
  // A complete, passing evaluation of a DIFFERENT benchmark is not evidence
  // about this one, however honest it is about its own.
  const other = payload();
  other.routes = ["/prospecting"];
  const swapped = snapshot();
  swapped.benchmark_coverage = coverageFact(other);
  refuse(swapped, "benchmark_coverage_payload_binding_mismatch");
  const rule = snapshot();
  rule.benchmark_coverage.outlier_rule = "discard whatever looks wrong";
  refuse(rule, "benchmark_coverage_rule_mismatch");
  const method = snapshot();
  method.benchmark_coverage.p95_aggregation_method = "mean";
  refuse(method, "benchmark_coverage_rule_mismatch");
  const schema = snapshot();
  schema.benchmark_coverage.schema_version = "doctorcre-v5-benchmark-measurement-set.v1";
  refuse(schema, "benchmark_coverage_wrong_schema");
});

test("the measurement and evaluation pointers are bound in form and left to the verifier in fact", () => {
  for (const field of ["measurement_set_digest", "evaluation_digest"]) {
    const s = snapshot();
    s.benchmark_coverage[field] = "measured-somewhere-else";
    assert.throws(() => run(s), e => e.code === "invalid_digest", field);
  }
  const unsafe = snapshot();
  unsafe.benchmark_coverage.evidence_ref = "https://example.invalid/coverage";
  refuse(unsafe, "invalid_evidence_ref");
  // STATED PLAINLY, because it is the limit of what a source kernel holding no
  // measurements can prove: a well-formed pointer to the WRONG measurement set
  // is admitted here. Re-deriving these two digests from the stored artifacts is
  // the verifier obligation the module header retains, and nothing in this file
  // discharges it or claims it was discharged.
  const unbound = snapshot();
  unbound.benchmark_coverage.measurement_set_digest = D(95);
  unbound.benchmark_coverage.evaluation_digest = D(94);
  const result = run(unbound);
  assert.equal(result.admissible, true);
  assert.equal(result.benchmark_coverage_evaluated_here, false);
  assert.equal(result.durable_coverage_verification_required, true);
});

test("counts cannot be claimed: the join re-derives the required cell count", () => {
  const required = benchmarkRequiredCells(payload()).length;
  const inflated = snapshot();
  inflated.benchmark_coverage.required_cell_count = required + 1;
  inflated.benchmark_coverage.measured_cell_count = required + 1;
  refuse(inflated, "benchmark_coverage_cell_count_mismatch");
  const short = snapshot();
  short.benchmark_coverage.measured_cell_count = required - 1;
  refuse(short, "benchmark_coverage_cell_count_mismatch");
  const none = snapshot();
  none.benchmark_coverage.measured_cell_count = 0;
  refuse(none, "invalid_integer");
  const incomplete = snapshot();
  incomplete.benchmark_coverage.coverage_complete = false;
  refuse(incomplete, "benchmark_coverage_incomplete");
  // The case the re-derivation exists for: the accepted manifest's matrix is
  // wider than the one the evaluation covered, and every field of the fact is
  // internally consistent. Only recomputing the count from the ACCEPTED payload
  // catches it.
  const wider = payload();
  wider.routes = ["/deals", "/prospecting"];
  assert.ok(benchmarkRequiredCells(wider).length > required);
  const grown = snapshot();
  grown.binding.benchmark_manifest_digest = benchmarkPayloadDigest(wider);
  at(grown, BENCHMARK_STEP_REF).receipt = manifest(wider);
  grown.benchmark_coverage.benchmark_payload_digest = benchmarkPayloadDigest(wider);
  assert.throws(() => run(grown), e => e.code === "benchmark_coverage_cell_count_mismatch" &&
    e.detail.required === benchmarkRequiredCells(wider).length);
});

test("a coverage claim with no per-cell SLO proof cannot pass", () => {
  const dropped = snapshot();
  delete dropped.benchmark_coverage.worst_p95_by_slo_key.cold_lcp_p95_ms;
  assert.throws(() => run(dropped), e => e.code === "benchmark_coverage_slo_proof_missing" &&
    e.detail.missing.includes("cold_lcp_p95_ms"));
  const empty = snapshot();
  empty.benchmark_coverage.worst_p95_by_slo_key = {};
  refuse(empty, "benchmark_coverage_slo_proof_missing");
  const invented = snapshot();
  invented.benchmark_coverage.worst_p95_by_slo_key.invented_p95_ms =
    copy(invented.benchmark_coverage.worst_p95_by_slo_key.cold_lcp_p95_ms);
  refuse(invented, "benchmark_coverage_slo_proof_missing");
  // The outcome boolean does not carry itself: `all_required_cells_meet_slo`
  // still says true here, and the p95 beside it refuses.
  const over = snapshot();
  over.benchmark_coverage.worst_p95_by_slo_key.warm_core_navigation_p95_ms.p95_ms =
    BENCHMARK_SLO_THRESHOLDS.warm_core_navigation_p95_ms + 1;
  assert.equal(over.benchmark_coverage.all_required_cells_meet_slo, true);
  refuse(over, "benchmark_coverage_slo_not_met");
  const claimed = snapshot();
  claimed.benchmark_coverage.all_required_cells_meet_slo = false;
  refuse(claimed, "benchmark_coverage_slo_not_met");
  // Exactly at the threshold passes, on the same reading the evaluator uses.
  const exact = snapshot();
  exact.benchmark_coverage.worst_p95_by_slo_key.warm_core_navigation_p95_ms.p95_ms =
    BENCHMARK_SLO_THRESHOLDS.warm_core_navigation_p95_ms;
  assert.equal(run(exact).admissible, true);
  // A proof filed under the wrong threshold, and a proof about a cell the
  // accepted matrix never required.
  const mismatched = snapshot();
  mismatched.benchmark_coverage.worst_p95_by_slo_key.cold_lcp_p95_ms.cell.metric = "command_acknowledgement_ms";
  refuse(mismatched, "benchmark_coverage_slo_proof_mismatched");
  const unrequired = snapshot();
  unrequired.benchmark_coverage.worst_p95_by_slo_key.cold_lcp_p95_ms.cell.network_profile = "lab-fiber";
  refuse(unrequired, "benchmark_coverage_unrequired_cell");
  const openEntry = snapshot();
  openEntry.benchmark_coverage.worst_p95_by_slo_key.cold_lcp_p95_ms.note = "rerun of the third pass";
  refuse(openEntry, "closed_shape");
});

test("a stale, future, pre-Gate-Zero or over-long coverage proof refuses", () => {
  const stale = snapshot();
  stale.benchmark_coverage.ttl_expires_at = "2026-02-03T00:00:00.000Z";
  refuse(stale, "benchmark_coverage_not_current");
  const future = snapshot();
  future.benchmark_coverage.observed_at = "2026-02-05T00:00:00.000Z";
  refuse(future, "benchmark_coverage_observed_after_reference");
  const early = snapshot();
  early.benchmark_coverage.observed_at = "2026-01-20T00:00:00.000Z";
  refuse(early, "benchmark_coverage_observed_before_gate_zero");
  // The boundary instant itself, on the one exclusive convention this file reads
  // every other instant under.
  const atGateZero = snapshot();
  atGateZero.benchmark_coverage.observed_at = GATE_ZERO_AT;
  refuse(atGateZero, "benchmark_coverage_observed_before_gate_zero");
  const overlong = snapshot();
  overlong.benchmark_coverage.ttl_expires_at = iso(Date.parse(COVERAGE_AT) + 400 * DAY);
  refuse(overlong, "benchmark_coverage_ttl_policy_exceeded");
  const inverted = snapshot();
  inverted.benchmark_coverage.ttl_expires_at = COVERAGE_AT;
  refuse(inverted, "benchmark_coverage_window_invalid");
  const unreadable = snapshot();
  unreadable.benchmark_coverage.observed_at = "2026-02-31T00:00:00.000Z";
  refuse(unreadable, "invalid_timestamp");
});

test("a non-passing coverage proof refuses, whichever way it fails to pass", () => {
  for (const status of ["fail", "unknown", "stale", "quarantined"]) {
    const s = snapshot();
    s.benchmark_coverage.status = status;
    assert.throws(() => run(s), e => e.code === "benchmark_coverage_not_passing", status);
  }
  const invented = snapshot();
  invented.benchmark_coverage.status = "measured-with-notes";
  refuse(invented, "benchmark_coverage_status_unregistered");
});

test("the coverage evaluator must be a declared, independent seat", () => {
  const undeclared = snapshot();
  undeclared.benchmark_coverage.evaluator_identity = I("some-other-evaluator");
  refuse(undeclared, "benchmark_coverage_evaluator_not_declared");
  // Matched on the seat pair the manifest's own duplicate-evaluator rule uses,
  // never on a class string read back out of a record.
  const otherSession = snapshot();
  otherSession.benchmark_coverage.evaluator_identity.session_ref = "session:a00-test-bench-evaluator-two";
  refuse(otherSession, "benchmark_coverage_evaluator_not_declared");
  const openIdentity = snapshot();
  openIdentity.benchmark_coverage.evaluator_identity.verified_human = true;
  refuse(openIdentity, "closed_shape");
  // Declared and STILL self-attested: the builder measuring its own subject.
  const withMaker = payload();
  withMaker.evaluator_identities = [I("bench-evaluator"), I("builder")];
  const maker = snapshot();
  maker.binding.benchmark_manifest_digest = benchmarkPayloadDigest(withMaker);
  at(maker, BENCHMARK_STEP_REF).receipt = manifest(withMaker);
  maker.benchmark_coverage = coverageFact(withMaker, { evaluator_identity: I("builder") });
  refuse(maker, "benchmark_coverage_self_attestation");
  // The join's own oracle may not have measured the benchmark it consumes...
  const oracle = snapshot();
  oracle.minimum_receipt_context.producer_identity = copy(oracle.benchmark_coverage.evaluator_identity);
  refuse(oracle, "minimum_producer_not_independent");
  // ...and the partner who accepted the manifest may not be the seat that
  // measured it, on the same independence rule that already covers reviewers.
  const partner = payload();
  partner.evaluator_identities = [I("joe")];
  const acceptor = snapshot();
  acceptor.binding.benchmark_manifest_digest = benchmarkPayloadDigest(partner);
  at(acceptor, BENCHMARK_STEP_REF).receipt = manifest(partner);
  acceptor.benchmark_coverage = coverageFact(partner, { evaluator_identity: I("joe") });
  refuse(acceptor, "benchmark_authority_not_independent");
});

test("no caller boolean can stand in for an evaluated benchmark", () => {
  const onFact = snapshot();
  onFact.benchmark_coverage.coverage_verified_by_partner = true;
  refuse(onFact, "closed_shape");
  const onProjection = snapshot();
  onProjection.benchmark_evaluated = true;
  refuse(onProjection, "closed_shape");
  const onManifest = snapshot();
  at(onManifest, BENCHMARK_STEP_REF).receipt.workload_coverage_verified = true;
  refuse(onManifest, "closed_shape");
});

test("composing a coverage fact from the evaluator is not authenticating one", () => {
  const body = payload();
  const compose = (overrides = {}) => proposeBenchmarkCoverageFact({
    payload: body,
    measurements: measurements(body),
    evaluator_identity: I("bench-evaluator"),
    evidence_ref: "safe:a00-test:coverage-evidence",
    observed_at: COVERAGE_AT,
    ttl_expires_at: iso(Date.parse(COVERAGE_AT) + 7 * DAY),
    ...overrides,
  });
  const fact = compose();
  assert.ok(Object.isFrozen(fact));
  assert.deepEqual(Object.keys(fact).sort(), [...BENCHMARK_COVERAGE_FACT_FIELDS].sort());
  // The composer is the one deterministic coverage evaluator and nothing else:
  // its numbers are that evaluator's, and evaluation_digest names that result.
  const evaluated = evaluateBenchmarkWorkloadCoverage({ payload: body, measurements: measurements(body) });
  assert.equal(fact.evaluation_digest, digest(evaluated));
  assert.equal(fact.required_cell_count, evaluated.required_cell_count);
  assert.equal(fact.measurement_set_digest, digest(measurements(body)));
  // It cannot compose evidence the evaluator denies...
  const short = measurements(body);
  short.cells.pop();
  refuseCall(() => compose({ measurements: short }), "benchmark_matrix_coverage_incomplete");
  refuseCall(() => compose({ measurements: measurements(body, { valueFor: () => 99999 }) }), "benchmark_slo_not_met");
  refuseCall(() => compose({ ttl_expires_at: COVERAGE_AT }), "benchmark_coverage_window_invalid");
  refuseCall(() => compose({ evidence_ref: "https://example.invalid/coverage" }), "invalid_evidence_ref");
  // ...and composing one grants nothing. It carries no acceptance, no authority
  // and no claim to have been verified; it reaches the join only by way of the
  // installed verifier, which is the act this function is not.
  for (const field of ["verified", "authenticated", "accepted", "authority_granted"]) {
    assert.ok(!Object.hasOwn(fact, field), field);
  }
  assert.equal(evaluateBenchmarkAdmissibility({ payload: body, measurements: measurements(body) }).accepted, false);
});

test("every member must be observed after the Gate Zero outcome", () => {
  const early = snapshot();
  at(early, PHI).receipt.observed_at = "2026-01-20T00:00:00.000Z";
  at(early, PHI).receipt.ttl_expires_at = iso(Date.parse("2026-01-20T00:00:00.000Z") + 20 * DAY);
  refuse(early, "member_observed_before_gate_zero");
});

test("Gate Zero itself must be present, correctly named and not in the future", () => {
  const future = snapshot();
  future.gate_zero.observed_at = "2026-02-05T00:00:00.000Z";
  refuse(future, "gate_zero_observed_after_reference");
  const wrongStep = snapshot();
  wrongStep.gate_zero.step_ref = MINIMUM_STEP_REF;
  refuse(wrongStep, "wrong_gate_zero_step");
  const unbound = snapshot();
  unbound.gate_zero.outcome_digest = "gate-zero-passed";
  refuse(unbound, "invalid_digest");
  const asserted = snapshot();
  delete asserted.gate_zero.outcome_digest;
  asserted.gate_zero.passed = true;
  refuse(asserted, "closed_shape");
});

// --- projection and canonicalization discipline -----------------------------

test("the projection is closed and tenant-bound", () => {
  const wrongTenant = snapshot();
  wrongTenant.tenant = "carr-external";
  refuse(wrongTenant, "wrong_tenant");
  const wrongSchema = snapshot();
  wrongSchema.schema_version = "doctorcre-v5-journey-one-clock-projection.v1";
  refuse(wrongSchema, "wrong_projection");
  const open = snapshot();
  open.approved_by_reviewer = true;
  refuse(open, "closed_shape");
  const openBinding = snapshot();
  openBinding.binding.skip_ttl = true;
  refuse(openBinding, "closed_shape");
  const badTtl = snapshot();
  badTtl.binding.minimum_receipt_ttl_ms = 0;
  refuse(badTtl, "invalid_integer");
});

// These are asserted against the pure validators rather than through the
// authenticated gate on purpose: JSON.stringify is what a projection survives,
// and it silently repairs a hole, a non-finite number and a non-enumerable
// property. The shapes have to be refused where they can still exist.
test("values that would canonicalize ambiguously are refused before they are hashed", () => {
  const surrogate = payload();
  surrogate.outlier_rule = "discard a lone \uD800 surrogate";
  refuseCall(() => validateBenchmarkPayload(surrogate), "invalid_unicode");
  const nonFinite = payload();
  nonFinite.samples_per_cell = Number.POSITIVE_INFINITY;
  refuseCall(() => validateBenchmarkPayload(nonFinite), "non_finite_number");
  const sparse = payload();
  sparse.routes.length += 1;
  refuseCall(() => validateBenchmarkPayload(sparse), "sparse_array");
  const hidden = payload();
  Object.defineProperty(hidden, "shadow", { value: "unhashed", enumerable: false });
  refuseCall(() => validateBenchmarkPayload(hidden), "hidden_key");
  const accessor = payload();
  Object.defineProperty(accessor, "routes", { get: () => ["/deals"], enumerable: true, configurable: true });
  refuseCall(() => validateBenchmarkPayload(accessor), "hidden_key");
  // A lone surrogate still refuses inside an authenticated projection, because
  // that one does survive the round trip.
  const s = snapshot();
  at(s, PHI).receipt.comparator = "lone \uD800 surrogate comparator";
  refuse(s, "invalid_unicode");
});

test("identities are closed three-field records with a well-formed session reference", () => {
  const open = snapshot();
  at(open, PHI).receipt.producer_identity.email = "someone@example.invalid";
  refuse(open, "closed_shape");
  const bareSession = snapshot();
  at(bareSession, PHI).receipt.producer_identity.session_ref = "producer-1";
  refuse(bareSession, "invalid_identity");
  const shortSession = snapshot();
  at(shortSession, PHI).receipt.producer_identity.session_ref = "session:x";
  refuse(shortSession, "invalid_identity");
  const empty = snapshot();
  at(empty, PHI).receipt.producer_identity.actor_id = "";
  refuse(empty, "invalid_identity");
});

// --- negative admission, proved rather than asserted ------------------------

test("the negative admission result is earned by re-deriving every required denial", () => {
  const proof = foundationAssuranceMinimumNegativeAdmission();
  assert.equal(proof.result, "all_required_denials_observed");
  assert.ok(proof.case_count >= 30, `expected a substantive denial set, saw ${proof.case_count}`);
  for (const code of [
    "missing_required_member", "unknown_member_step", "duplicate_member_step",
    "member_receipt_not_current", "member_binding_mismatch", "member_scope_mismatch",
    "member_self_attestation", "member_producers_not_distinct", "benchmark_authority_not_independent",
    "benchmark_manifest_digest_mismatch", "benchmark_acceptance_mismatched",
    "benchmark_accepted_before_gate_zero", "benchmark_weight_total_mismatch",
    "benchmark_constant_mismatch", "closed_shape",
  ]) {
    assert.ok(proof.observed_codes.includes(code), code);
  }
  assert.equal(run(snapshot()).negative_admission.result, "all_required_denials_observed");
  assert.equal(foundationAssuranceMinimumNegativeAdmission(), proof, "memoized, not re-derived per call");
});

// --- reuse of the M01 deadline contract -------------------------------------

test("the manifest deadline contract and the M01 kernel contract are one contract", () => {
  for (const field of Object.keys(BENCHMARK_DEADLINE_CONTRACT)) {
    if (field === "maximum_external_blocker_pause_days") continue;
    assert.deepEqual(BENCHMARK_DEADLINE_CONTRACT[field], JOURNEY_ONE_DEADLINE_CONTRACT[field], field);
  }
  assert.equal(BENCHMARK_DEADLINE_CONTRACT.maximum_external_blocker_pause_days * 24,
    JOURNEY_ONE_DEADLINE_CONTRACT.maximum_external_blocker_pause_hours);
  assert.equal(BENCHMARK_DEADLINE_CONTRACT.clock_origin_gate_id, MINIMUM_GATE_ID);
  assert.equal(BENCHMARK_DEADLINE_CONTRACT.clock_terminus_gate_id, "journey-one-kernel-production-accepted");
});

test("the M01 receipt view keeps one receipt with one canonical form and one digest", () => {
  const result = run(snapshot());
  const proposed = result.proposed_receipt;
  // r7's consumer-gate-receipt.v1 is closed and declares no schema_version, and
  // neither side of the seam adds one.
  assert.ok(!Object.hasOwn(proposed, "schema_version"));
  const view = journeyOneClockMinimumReceiptView(proposed);
  assert.ok(!Object.hasOwn(view, "schema_version"));
  assert.equal(Object.keys(view).length, CONSUMER_GATE_RECEIPT_FIELDS.length);
  assert.deepEqual({ ...view }, { ...proposed });
  // The identity that matters downstream: the digest A00 publishes is the digest
  // M01 records as the clock origin, so a store keyed on one finds the other.
  assert.equal(digest(view), digest(proposed));
  assert.equal(digest(view), result.proposed_receipt_reference_digest);
  // The view is still a distinct object, not the frozen result's own receipt.
  assert.notEqual(view, proposed);
});

test("a receipt carrying an added schema_version is refused on both sides of the seam", () => {
  const proposed = run(snapshot()).proposed_receipt;
  refuseCall(() => journeyOneClockMinimumReceiptView({ ...copy(proposed), schema_version: "consumer-gate-receipt.v1" }),
    "closed_shape");
  const s = snapshot();
  at(s, PHI).receipt.schema_version = "consumer-gate-receipt.v1";
  refuse(s, "closed_shape");
});

// --- the denial set earns the field it writes -------------------------------

test("the denial set exercises both categories r7's denial_rule names, by their exact codes", () => {
  const proof = foundationAssuranceMinimumNegativeAdmission();
  for (const code of [
    // The replayed receipt and the over-long window: the two categories r7
    // names verbatim and the set previously asserted without exercising.
    "duplicate_member_receipt", "member_receipt_ttl_policy_exceeded",
    "member_receipt_window_invalid", "member_observed_before_gate_zero",
    "member_producers_not_distinct",
    // Both acceptance boundaries, including the equal instant the clock kernel
    // refuses, so the seam's convention is proved and not merely written down.
    "benchmark_accepted_at_or_after_reference",
    // The "unevaluated" category. An accepted manifest with no coverage fact,
    // one bound to another payload, one whose counts or SLO proof do not hold
    // up, one measured by an undeclared seat, and one that has gone stale.
    "benchmark_coverage_payload_binding_mismatch", "benchmark_coverage_cell_count_mismatch",
    "benchmark_coverage_slo_not_met", "benchmark_coverage_slo_proof_missing",
    "benchmark_coverage_unrequired_cell", "benchmark_coverage_evaluator_not_declared",
    "benchmark_coverage_self_attestation", "benchmark_coverage_not_passing",
    "benchmark_coverage_not_current", "benchmark_coverage_observed_before_gate_zero",
  ]) {
    assert.ok(proof.observed_codes.includes(code), code);
  }
  assert.ok(proof.case_count >= 55, `expected the widened denial set, saw ${proof.case_count}`);
});

test("a member receipt replayed under a second step is a replay, not a second pass", () => {
  const s = snapshot();
  at(s, SECRETS).receipt = copy(at(s, PHI).receipt);
  refuse(s, "duplicate_member_receipt");
  // Byte-identical evidence under the SAME step is caught one check earlier, as
  // a duplicate step; the replay code exists for the cross-step case above.
  const sameStep = snapshot();
  sameStep.members.push(copy(at(sameStep, PHI)));
  refuse(sameStep, "duplicate_member_step");
});

test("an over-long member window denies on policy even while it is still current", () => {
  const s = snapshot();
  const expires = Date.parse(OBSERVED_AT) + 400 * DAY;
  at(s, PHI).receipt.ttl_expires_at = iso(expires);
  // Still current as of the reference instant: it is the policy that refuses it,
  // not currentness, which is why the two codes are distinct.
  assert.ok(expires > Date.parse(AS_OF));
  assert.ok(expires - Date.parse(OBSERVED_AT) > snapshot().binding.maximum_member_receipt_ttl_ms);
  refuse(s, "member_receipt_ttl_policy_exceeded");
});

// --- one definition of a seat, everywhere -----------------------------------

test("two member producers sharing one session are one seat whatever their actor ids", () => {
  const s = snapshot();
  const phiProducer = at(s, PHI).receipt.producer_identity;
  const secretsProducer = at(s, SECRETS).receipt.producer_identity;
  assert.notEqual(secretsProducer.actor_id, phiProducer.actor_id);
  secretsProducer.session_ref = phiProducer.session_ref;
  refuse(s, "member_producers_not_distinct");
  // The converse holds too: one actor id under two sessions is still one actor.
  const actor = snapshot();
  at(actor, SECRETS).receipt.producer_identity.actor_id = at(actor, PHI).receipt.producer_identity.actor_id;
  refuse(actor, "member_producers_not_distinct");
  // Genuinely distinct seats stay admissible; this is a distinctness rule, not a
  // prohibition on producers resembling one another.
  const distinct = snapshot();
  at(distinct, SECRETS).receipt.producer_identity = I("producer-second-seat");
  assert.equal(run(distinct).admissible, true);
});

// --- the Gate Zero boundary is one convention, not two ----------------------

test("a member observed AT the Gate Zero instant is refused exactly as one observed before it", () => {
  const exactly = snapshot();
  at(exactly, PHI).receipt.observed_at = GATE_ZERO_AT;
  at(exactly, PHI).receipt.ttl_expires_at = iso(Date.parse(GATE_ZERO_AT) + 20 * DAY);
  refuse(exactly, "member_observed_before_gate_zero");
  // One millisecond after it is admitted, so the rule is exclusive rather than
  // merely strict-looking.
  const after = snapshot();
  const observed = Date.parse(GATE_ZERO_AT) + 1;
  at(after, PHI).receipt.observed_at = iso(observed);
  at(after, PHI).receipt.ttl_expires_at = iso(observed + 20 * DAY);
  assert.equal(run(after).admissible, true);
  // And it is the SAME convention the benchmark acceptance is read under, which
  // is what the header claims for both.
  const acceptedAtGateZero = snapshot();
  at(acceptedAtGateZero, BENCHMARK_STEP_REF).receipt = manifest(payload(), { acceptedAt: GATE_ZERO_AT });
  refuse(acceptedAtGateZero, "benchmark_accepted_before_gate_zero");
});

// --- the benchmark member carries no role to compare ------------------------

test("the benchmark member's role is a load-time registry invariant, not an evidence check", () => {
  const entry = MINIMUM_REQUIRED_MEMBERS.find(m => m.step_ref === BENCHMARK_STEP_REF);
  assert.equal(entry.producer_role, "verified_partner_benchmark_authority");
  assert.equal(entry.output_schema_ref, BENCHMARK_MANIFEST_SCHEMA);
  // benchmark-manifest.v1 declares no producer_role at all, so there is nothing
  // on the accepted manifest for a validator to compare the entry against: one
  // supplied is refused as an open shape, never matched.
  assert.ok(!BENCHMARK_MANIFEST_FIELDS.includes("producer_role"));
  const s = snapshot();
  at(s, BENCHMARK_STEP_REF).receipt.producer_role = "verified_partner_benchmark_authority";
  refuse(s, "closed_shape");
  // member_role_mismatch stays reachable from the receipts that do carry it.
  const role = snapshot();
  at(role, PHI).receipt.producer_role = "independent_secret_boundary_oracle";
  refuse(role, "member_role_mismatch");
});

// --- the envelope digest must be injective ----------------------------------

test("an envelope that is not a plain JSON object is refused before anything is hashed", () => {
  // The collision this refusal exists to prevent: a top-level string is hashed
  // as its own bytes, which are exactly the canonical bytes of the object.
  const asObject = { synthetic_evidence_ref: "a00-test-collision" };
  const asString = '{"synthetic_evidence_ref":"a00-test-collision"}';
  assert.equal(digest(asString), digest(asObject), "the two shapes do share a digest");
  const gate = createFoundationAssuranceMinimumGate({
    authenticateEvidence: envelope => ({ envelope_digest: digest(envelope), snapshot: snapshot() }),
  });
  for (const notAnObject of [asString, ["a00-test-collision"], null, 7, true]) {
    refuseCall(() => gate.evaluate(notAnObject), "invalid_object");
  }
  // The object form still evaluates, so this closes a shape rather than a path.
  assert.equal(gate.evaluate(asObject).admissible, true);
});

// --- the M01 seam refuses what it cannot adapt ------------------------------

test("the M01 receipt view refuses a hidden property instead of dropping it in the copy", () => {
  const proposed = run(snapshot()).proposed_receipt;
  const smuggled = { ...copy(proposed) };
  Object.defineProperty(smuggled, "shadow", { value: "unhashed", enumerable: false });
  refuseCall(() => journeyOneClockMinimumReceiptView(smuggled), "hidden_key");
  const accessor = { ...copy(proposed) };
  Object.defineProperty(accessor, "status", { get: () => "pass", enumerable: true, configurable: true });
  refuseCall(() => journeyOneClockMinimumReceiptView(accessor), "hidden_key");
});

test("the M01 seam rejects a reference or instant M01 cannot read, and shortens neither", () => {
  const long = `safe:a00-test:${"e".repeat(400)}`;
  assert.ok(long.length > 300);
  // r7 declares minLength 1 and no maximum on evidence_ref, so this file invents
  // no cap: the long reference is admissible evidence HERE...
  const s = snapshot();
  s.minimum_receipt_context.evidence_ref = long;
  const admitted = run(s);
  assert.equal(admitted.admissible, true);
  assert.equal(admitted.proposed_receipt.evidence_ref, long, "evidence is carried whole");
  // ...and the seam is where M01's 300-character domain refuses it, by name and
  // without truncating it into a different reference.
  refuseCall(() => journeyOneClockMinimumReceiptView(admitted.proposed_receipt),
    "m01_incompatible_evidence_ref");

  // r7's date-time admits up to nine fractional digits; M01's stamp reads three.
  const proposed = run(snapshot()).proposed_receipt;
  refuseCall(() => journeyOneClockMinimumReceiptView({ ...copy(proposed), observed_at: "2026-02-04T00:00:00.123456789Z" }),
    "m01_incompatible_timestamp");
  refuseCall(() => journeyOneClockMinimumReceiptView({ ...copy(proposed), ttl_expires_at: "2026-02-11T00:00:00.1234Z" }),
    "m01_incompatible_timestamp");
  refuseCall(() => journeyOneClockMinimumReceiptView({
    ...copy(proposed),
    producer_identity: { ...copy(proposed.producer_identity), session_ref: `session:${"s".repeat(400)}` },
  }), "m01_incompatible_session_ref");

  // The receipt this join actually proposes is inside every M01 domain, which is
  // why the remaining divergence is a seam to check and not a live defect.
  const view = journeyOneClockMinimumReceiptView(proposed);
  assert.equal(view.evidence_ref, proposed.evidence_ref);
  assert.equal(view.observed_at, proposed.observed_at);
});

test("the seam changes no field of the receipt and converts no deadline contract", () => {
  const view = journeyOneClockMinimumReceiptView(run(snapshot()).proposed_receipt);
  assert.deepEqual(Object.keys(view).sort(), [...CONSUMER_GATE_RECEIPT_FIELDS].sort());
  assert.ok(!Object.hasOwn(view, "deadline_contract"));
  // Days here, hours there: reconciled at module load, never silently converted
  // for a caller. Neither contract carries the other's unit.
  assert.equal(BENCHMARK_DEADLINE_CONTRACT.maximum_external_blocker_pause_days, 5);
  assert.equal(JOURNEY_ONE_DEADLINE_CONTRACT.maximum_external_blocker_pause_hours, 120);
  assert.ok(!Object.hasOwn(BENCHMARK_DEADLINE_CONTRACT, "maximum_external_blocker_pause_hours"));
  assert.ok(!Object.hasOwn(JOURNEY_ONE_DEADLINE_CONTRACT, "maximum_external_blocker_pause_days"));
});

// --- the two derived rules that over-constrain an r7 silence ----------------

test("the derived percentile uniqueness rule reaches an accepted manifest inside the join", () => {
  const p = payload();
  p.request_size_distribution = [{ percentile: 50, bytes: 1 }, { percentile: 50, bytes: 2 }];
  const s = snapshot();
  s.binding.benchmark_manifest_digest = digest([BENCHMARK_PAYLOAD_DOMAIN_TAG, p]);
  at(s, BENCHMARK_STEP_REF).receipt = manifest(p);
  // Stated rather than silent: r7 declares no uniqueItems here, so a manifest a
  // partner accepted with a repeated percentile IS denied by this file.
  refuse(s, "benchmark_duplicate_request_size_percentile");
});

// --- the proposed TTL is the projection's, bounded by the issuer ------------

test("the proposed receipt's TTL is the projection's own, under no invented global rule", () => {
  const s = snapshot();
  // Deliberately longer than the policy bounding CONSUMED member receipts. r7
  // states no relation between the two, so this file states none either; the
  // bound that applies is the downstream issuer's and is named in the header.
  s.binding.minimum_receipt_ttl_ms = 60 * DAY;
  assert.ok(s.binding.minimum_receipt_ttl_ms > s.binding.maximum_member_receipt_ttl_ms);
  const result = run(s);
  assert.equal(result.proposed_receipt.ttl_expires_at, iso(Date.parse(AS_OF) + 60 * DAY));
  assert.equal(result.issued, false);
  assert.equal(result.persisted, false);
  assert.equal(result.durable_receipt_issuance_required, true);
});
