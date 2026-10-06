// Unit tests for the DoctorCRE v5 benchmark acceptance store.
//
// THE DATABASE HERE IS A MOCK, AND NOTHING IN THIS FILE IS EVIDENCE THAT
// ANYTHING WAS STORED OR ACCEPTED. `mockDatabase` records the statements the
// module issues and returns the rows the test hands it. That is enough to prove
// what the module SENDS, what it REFUSES, and what it refuses to send — and it
// is not, and is not treated as, a claim about durable behaviour. The
// transaction-scoped proofs against a real PostgreSQL live in
// benchmark-acceptance-postgres.sql. NO benchmark is durably accepted by
// running this mock-backed file; the last group proves both the reachable
// success response and the fail-closed prerequisite paths.
//
// Refusal tests assert that prerequisite failures issue only reads and no audit
// event. The success fixture reaches the full handler and pins its truthful
// accepted status, effects, and positional audit call.
//
// WHAT THIS FILE CAN AND CANNOT REACH, named rather than left implicit.
// readMeasurementCoverageBinding is still private and still not injectable —
// deliberately, since an injectable reader is a configurable one — so it cannot
// be called directly from here. It IS now exercised, through the review path,
// which reads the attestation back through the same reader acceptance uses: the
// four refusals below drive it by handing the mock a record that does not hold
// up. The successful-acceptance fixture also exercises it inside the acceptance
// path; benchmark-acceptance-postgres.sql remains the durable database proof.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { digest } from "../src/artifact-trust.js";
import {
  BENCHMARK_CELL_DOMAIN_TAG,
  BENCHMARK_COST_VARIANCE_THRESHOLDS, BENCHMARK_DEADLINE_CONTRACT, BENCHMARK_GATE_ID,
  BENCHMARK_MEASUREMENT_SET_SCHEMA, BENCHMARK_PAYLOAD_DOMAIN_TAG, BENCHMARK_PAYLOAD_FIELDS,
  BENCHMARK_PRODUCER_ROLE, BENCHMARK_SLO_THRESHOLDS, BENCHMARK_STEP_REF, GATE_ZERO_STEP_REF,
  P95_AGGREGATION_METHOD, benchmarkPayloadDigest, benchmarkRequiredCells,
  evaluateBenchmarkWorkloadCoverage,
} from "../src/benchmark-minimum.v5.js";
import * as store from "../src/benchmark-acceptance-store.v5.js";
import {
  BENCHMARK_COVERAGE_EVALUATOR, BENCHMARK_COVERAGE_EVALUATORS,
  BENCHMARK_DIMENSIONS, BENCHMARK_DRAFT_ROWS_SCHEMA, BENCHMARK_DRAFT_SCALAR_FIELDS,
  BENCHMARK_EMITTED_CONSTANT_FIELDS, BENCHMARK_GATE_ZERO_INTEGRATION_REQUIREMENT,
  BENCHMARK_MEASUREMENT_COVERAGE_INTEGRATION_REQUIREMENT,
  BenchmarkAcceptanceStoreError, benchmarkAcceptancePrerequisites, benchmarkAcceptanceStoreTools,
  benchmarkDraftRows, benchmarkPayloadFromRows, deriveBenchmarkAcceptor,
  validateBenchmarkDraftPayload,
} from "../src/benchmark-acceptance-store.v5.js";
import {
  FOUNDATION_ASSURANCE_COMPARATORS, FOUNDATION_ASSURANCE_GITHUB_CHECKS,
  foundationAssuranceEvidenceDigest,
} from "../src/foundation-assurance-evidence.v5.js";

// --- fixtures ---------------------------------------------------------------

const D = n => `sha256:${String(n).padStart(2, "0").repeat(32)}`;
const copy = x => JSON.parse(JSON.stringify(x));

const DRAFT_ID = "11111111-2222-4333-8444-555555555555";
const REVIEW_ID = "66666666-7777-4888-8999-aaaaaaaaaaaa";
const KEY = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff";
const FOUNDATION_CONFIG = JSON.parse(readFileSync(new URL(
  "../../ops/config/foundation-assurance-benchmark.v1.json", import.meta.url), "utf8"));

/**
 * One minimal admissible payload. Every environment dimension carries exactly
 * one member except workload_mix, which carries two so that the weight split is
 * exercised rather than trivial. The required matrix is therefore eight cells --
 * (routes x warm) + (routes x cold) + (acknowledgement_endpoints x both cache
 * states), each crossed with the two workloads -- and a complete measurement set
 * fits in a test.
 */
function payload() {
  return {
    subject_digest: D(1), candidate_digest: D(2), policy_digest: D(3),
    capacity_profiles: ["baseline"],
    workload_mix: [
      { workload_id: "search", weight_basis_points: 7000, operation_mix_digest: D(10) },
      { workload_id: "detail", weight_basis_points: 3000, operation_mix_digest: D(11) },
    ],
    request_size_distribution: [{ percentile: 50, bytes: 2048 }, { percentile: 95, bytes: 16384 }],
    concurrency_levels: [8],
    arrival_patterns: ["steady"],
    routes: ["/deals"],
    browsers: [{ name: "chrome", version: "141", build: "141.0.1" }],
    runtime_versions: ["node-24"],
    device_profiles: ["macbook-pro-m3"],
    hardware_profiles: ["m3-16gb"],
    network_profiles: ["broadband"],
    cache_states: ["cold", "warm"],
    samples_per_cell: 20,
    warmup_runs: 1,
    p95_aggregation_method: P95_AGGREGATION_METHOD,
    outlier_rule: "discard samples above five times the cell median",
    acknowledgement_endpoints: ["/commands/ack"],
    evaluator_identities: [
      { actor_id: "bench-evaluator", session_ref: "session:a00-store-evaluator", authority_class: "synthetic_oracle" },
    ],
    comparator_versions: ["comparator-1.4.0"],
    slo_thresholds: { ...BENCHMARK_SLO_THRESHOLDS },
    cost_expectation_matrix_digest: D(12),
    cost_variance_thresholds: { ...BENCHMARK_COST_VARIANCE_THRESHOLDS },
    deadline_contract: copy(BENCHMARK_DEADLINE_CONTRACT),
  };
}

/** A measurement set covering exactly the required matrix, well inside every SLO. */
function measurements(body = payload()) {
  return {
    schema_version: BENCHMARK_MEASUREMENT_SET_SCHEMA,
    benchmark_payload_digest: benchmarkPayloadDigest(body),
    outlier_rule: body.outlier_rule,
    p95_aggregation_method: body.p95_aggregation_method,
    cells: benchmarkRequiredCells(body).map(cell => ({
      cell: copy(cell),
      warmup_samples: [40],
      samples: Array.from({ length: 20 }, () => 10),
      excluded_sample_indexes: [],
    })),
  };
}

// --- the mock database and tool harness -------------------------------------

/**
 * A statement recorder. Responses are matched on a substring of the SQL, so a
 * test declares which function it expects to be called; a statement with no
 * declared response throws, which is how an unexpected query fails a test
 * rather than passing silently.
 */
function mockDatabase(responses = {}) {
  const calls = [];
  const events = [];
  return {
    calls, events,
    query: async (sql, params = []) => {
      calls.push({ sql, params });
      for (const [needle, rows] of Object.entries(responses)) {
        if (sql.includes(needle)) return { rows };
      }
      throw new Error(`mock database has no declared response for: ${sql}`);
    },
  };
}

class ToolError extends Error {
  constructor(payload) {
    super(payload.error);
    this.name = "ToolError";
    this.payload = payload;
  }
}

// The real withEnvelope opens a transaction and records a tool-call envelope.
// Here it only invokes the body: these tests are about what the handler does,
// not about the envelope, and a mock that pretended to be transactional would
// be claiming durability this file explicitly does not claim.
const withEnvelope = async (_c, _actor, _verb, _args, fn) => fn();
// The audit helper is POSITIONAL — writeEvent(client, actor, verb, subjectType,
// subjectId, fields) — and this double records every argument so a handler that
// passes an options object fails here instead of at the event.actor_id NOT NULL
// constraint in production. Defect 65ed5e3e-db28-498a-a0e7-84ae53658dea.
const writeEvent = async (c, actor, verb, subjectType, subjectId, fields) => {
  c.events.push({ actor, verb, subjectType, subjectId, fields });
};

const tools = benchmarkAcceptanceStoreTools({
  withEnvelope, writeEvent, ToolError,
  authenticatedIdentity: { receiptIdentity: () => ({ actor_id: "joe" }) },
});

const PARTNER = { slug: "joe", human: true, via: "oauth-google" };
const AGENT = { slug: "codex", human: false, sponsoring_human_slug: "joe" };
const VERIFIED_AGENT = { ...AGENT, native_agent_verified: true };

async function refusal(promise) {
  try {
    await promise;
  } catch (error) {
    if (error instanceof ToolError) return error.payload;
    throw error;
  }
  throw new assert.AssertionError({ message: "expected a tool refusal, none was thrown" });
}

/**
 * The synchronous twin of `refusal`: capture the error one refusing call threw.
 *
 * assert.throws returns undefined, so `assert.throws(fn, Type).code` reads a
 * property off undefined rather than off the error. The expected constructor is
 * still asserted here when one is named -- nothing about what these tests demand
 * is relaxed, the error is simply handed back so its code and detail can be
 * asserted on the object that actually carries them.
 */
function thrown(fn, expected) {
  try {
    fn();
  } catch (error) {
    if (expected !== undefined && !(error instanceof expected)) {
      throw new assert.AssertionError({
        message: `expected a ${expected.name}, got ${error?.name}: ${error?.message}`,
        actual: error?.name, expected: expected.name,
      });
    }
    return error;
  }
  throw new assert.AssertionError({ message: "expected a refusal, none was thrown" });
}

// --- the draft contract -----------------------------------------------------

test("a valid draft reports the kernel's payload digest and accepts nothing", () => {
  const view = validateBenchmarkDraftPayload(payload());
  assert.equal(view.payload_digest, benchmarkPayloadDigest(payload()));
  assert.equal(view.payload_digest, digest([BENCHMARK_PAYLOAD_DOMAIN_TAG, payload()]));
  assert.equal(view.gate_id, BENCHMARK_GATE_ID);
  assert.equal(view.producer_step_ref, BENCHMARK_STEP_REF);
  assert.equal(view.producer_role, BENCHMARK_PRODUCER_ROLE);
  assert.equal(view.accepted, false);
  assert.equal(view.effects.creates_effect, false);
});

test("each acceptance-envelope field is refused on a draft by name", () => {
  const cases = {
    accepted_at: "2026-02-02T00:00:00.000Z",
    accepted_by_identity: { actor_id: "joe", session_ref: "session:a00-store-x", authority_class: "verified_partner" },
    benchmark_manifest_digest: D(9),
    status: "accepted",
  };
  for (const [field, value] of Object.entries(cases)) {
    const error = thrown(() => validateBenchmarkDraftPayload({ ...payload(), [field]: value }),
      BenchmarkAcceptanceStoreError);
    assert.equal(error.code, "benchmark_acceptance_envelope_refused");
    assert.equal(error.detail.path, `payload.${field}`);
  }
});

test("a self-asserted authority key anywhere in a draft is refused", () => {
  for (const key of ["verified_human", "partner_confirmed", "authority_granted", "gate_zero_digest"]) {
    const body = payload();
    body.workload_mix[0][key] = true;
    const error = thrown(() => validateBenchmarkDraftPayload(body), BenchmarkAcceptanceStoreError);
    assert.equal(error.code, "self_asserted_authority_refused");
  }
});

test("a payload the kernel refuses is refused with the kernel's own code", () => {
  const body = payload();
  body.workload_mix[0].weight_basis_points = 6999;
  // No constructor is named: the kernel's own error class is deliberately not
  // imported here, because the point of the case is that its CODE survives
  // un-rewrapped.
  const error = thrown(() => validateBenchmarkDraftPayload(body));
  assert.equal(error.name, "BenchmarkMinimumError");
  assert.equal(error.code, "benchmark_weight_total_mismatch");
});

// --- the row decomposition --------------------------------------------------

test("every payload field is either stored as a row or emitted as a constant", () => {
  const persisted = new Set([
    ...BENCHMARK_DRAFT_SCALAR_FIELDS, ...BENCHMARK_DIMENSIONS,
    "workload_mix", "request_size_distribution", "concurrency_levels",
    "browsers", "evaluator_identities",
  ]);
  const accounted = BENCHMARK_PAYLOAD_FIELDS.filter(field =>
    persisted.has(field) || BENCHMARK_EMITTED_CONSTANT_FIELDS.includes(field));
  assert.deepEqual([...accounted].sort(), [...BENCHMARK_PAYLOAD_FIELDS].sort());
  // And the two sets are disjoint: a field cannot be both stored and emitted.
  for (const field of BENCHMARK_EMITTED_CONSTANT_FIELDS) assert.equal(persisted.has(field), false);
});

test("the fixed constant groups are not stored anywhere in the rows", () => {
  const rows = benchmarkDraftRows(payload());
  const serialized = JSON.stringify(rows);
  for (const field of BENCHMARK_EMITTED_CONSTANT_FIELDS) {
    assert.equal(Object.hasOwn(rows.scalars, field), false);
    assert.equal(serialized.includes(field), false, `${field} leaked into the stored rows`);
  }
});

test("rows round-trip to the same payload digest", () => {
  const body = payload();
  const rebuilt = benchmarkPayloadFromRows(benchmarkDraftRows(body));
  assert.deepEqual(rebuilt, body);
  assert.equal(benchmarkPayloadDigest(rebuilt), benchmarkPayloadDigest(body));
});

test("a rebuilt payload carries the kernel's constants, not the caller's", () => {
  const rows = benchmarkDraftRows(payload());
  const rebuilt = benchmarkPayloadFromRows(rows);
  assert.deepEqual(rebuilt.slo_thresholds, { ...BENCHMARK_SLO_THRESHOLDS });
  assert.deepEqual(rebuilt.cost_variance_thresholds, { ...BENCHMARK_COST_VARIANCE_THRESHOLDS });
  assert.deepEqual(rebuilt.deadline_contract, copy(BENCHMARK_DEADLINE_CONTRACT));
});

test("list order is part of the hash: reordering rows changes the digest", () => {
  const body = payload();
  const rows = copy(benchmarkDraftRows(body));
  // Two request-size points, ordinals swapped. The set is identical; the
  // distribution's order is not, and r7 array order participates in the digest.
  rows.request_sizes = [
    { ...rows.request_sizes[1], ordinal: 0 },
    { ...rows.request_sizes[0], ordinal: 1 },
  ];
  const reordered = benchmarkPayloadFromRows(rows);
  assert.notEqual(benchmarkPayloadDigest(reordered), benchmarkPayloadDigest(body));
});

test("a gap in the stored ordinals refuses instead of rebuilding a shorter list", () => {
  const rows = copy(benchmarkDraftRows(payload()));
  rows.dimensions = rows.dimensions.filter(row => !(row.dimension === "cache_states" && row.ordinal === 0));
  const error = thrown(() => benchmarkPayloadFromRows(rows), BenchmarkAcceptanceStoreError);
  assert.equal(error.code, "benchmark_row_ordinal_gap");
});

test("rows carry every dimension in declared order with contiguous ordinals", () => {
  const rows = benchmarkDraftRows(payload());
  assert.equal(rows.schema_version, BENCHMARK_DRAFT_ROWS_SCHEMA);
  for (const dimension of BENCHMARK_DIMENSIONS) {
    const values = rows.dimensions.filter(row => row.dimension === dimension);
    assert.ok(values.length >= 1, `${dimension} has no rows`);
    values.forEach((row, index) => assert.equal(row.ordinal, index));
    assert.deepEqual(values.map(row => row.value), payload()[dimension]);
  }
});

// --- the acceptance prerequisites -------------------------------------------

test("both private readers are private and not exported", () => {
  assert.equal("readGateZeroOutcome" in store, false);
  assert.equal("readMeasurementCoverageBinding" in store, false);
  for (const name of Object.keys(store)) {
    assert.ok(!/gateZero|gate_zero/i.test(name) || name === "BENCHMARK_GATE_ZERO_INTEGRATION_REQUIREMENT",
      `${name} exposes a Gate Zero surface`);
    // The coverage binding is a stated gap, not a callable surface either.
    assert.ok(!/^read[A-Z]/.test(name), `${name} exposes a reader`);
  }
});

test("the prerequisites report NO unbound acceptance binding, and say what that does not mean", () => {
  const prerequisites = benchmarkAcceptancePrerequisites();
  // THE ASSERTION THE WHOLE UNIT TURNS ON, AND IT HAS MOVED TWICE. The list was
  // two entries, then one when the coverage binding cleared on its own
  // evidence, and it is now zero because migration 0502 landed the Gate Zero
  // record and both its readers together. Each step is a record landing in a
  // field a reader can check, never two requirements being merged.
  assert.deepEqual(prerequisites.acceptance_blocked_by, []);
  // `acceptance_available` reports the SHAPE of the rail, not a prediction: no
  // binding is structurally absent any more. Every acceptance still refuses
  // unless all three answer, which the refusal tests below prove directly.
  assert.equal(prerequisites.acceptance_available, true);
  assert.equal(prerequisites.gate_zero.resolved, true);
  assert.equal(prerequisites.gate_zero.step_ref, GATE_ZERO_STEP_REF);
  assert.ok(prerequisites.gate_zero.resolved_by.length > 0);
  assert.ok(prerequisites.gate_zero.still_refused_after_resolution.length > 0);
  // THE STALE CLAUSE IS CORRECTED, NOT DELETED. A wrong sentence that simply
  // disappears teaches nobody, and this one is the sentence that would send the
  // next reader looking for a human to supply the outcome.
  assert.equal(prerequisites.gate_zero.external_producer_is_intentional, false);
  assert.match(prerequisites.gate_zero.corrected_stale_clause.said, /registers no v5 producer/);
  assert.match(prerequisites.gate_zero.corrected_stale_clause.corrected_to,
    /independent_control_plane_oracle/);
  assert.equal(prerequisites.gate_zero.corrected_stale_clause.corrected_by,
    "311a9af5-3685-4c47-a158-f8dd70870ca1");
  assert.equal(prerequisites.measurement_coverage_proof.resolved, true);
  assert.equal(prerequisites.measurement_coverage_proof.independent_of, GATE_ZERO_STEP_REF);
  assert.equal(prerequisites.portfolio_constitution.resolved, true);
  assert.equal(prerequisites.portfolio_constitution.resolved_by, "ops.portfolio_accepted_revision(text)");
  // It is a statement of a MISSING BINDING, not a Gate Zero outcome.
  assert.equal(Object.hasOwn(prerequisites.gate_zero, "outcome_digest"), false);
  assert.equal(Object.hasOwn(prerequisites.gate_zero, "observed_at"), false);
});

test("the portfolio prerequisite reports what it proves and refuses to claim lineage", () => {
  const portfolio = benchmarkAcceptancePrerequisites().portfolio_constitution;
  assert.ok(portfolio.proves.length > 0);
  assert.ok(portfolio.does_not_prove.length > 0);
  // The claim that used to be made in prose -- "the portfolio this benchmark
  // descends from" -- is now stated as the thing NOT proved, and it is stated on
  // the negative side only.
  assert.ok(portfolio.does_not_prove.some(clause => /descend/i.test(clause)));
  assert.equal(portfolio.proves.some(clause => /descend/i.test(clause)), false);
  assert.ok(/lineage/i.test(portfolio.lineage_note));
});

test("the Gate Zero requirement is still scoped to this record layer after resolving", () => {
  const requirement = BENCHMARK_GATE_ZERO_INTEGRATION_REQUIREMENT;
  assert.equal(requirement.resolved, true);
  // RESOLVING IT DID NOT TURN IT INTO A JUDGEMENT. The scope clause still says
  // this constant is about what is bindable here, not about what a Gate Zero
  // run decided — a rail that started reporting verdicts would be a second
  // Gate Zero policy, which is the thing this slice is forbidden to invent.
  //
  // AND THE STALE CLAUSE STAYS CORRECTED. It used to say r7 registers no v5
  // producer for Gate Zero and that this was intentional; the loop-589
  // amendment (decision 311a9af5) wrote the producer row into the frozen packet
  // and Step A built the producer itself. A stale sentence inside a frozen
  // constant is the single most likely thing to send the next reader looking for
  // a human outcome arriving from outside, so the assertion below keeps it gone.
  assert.equal(requirement.external_producer_is_intentional, false);
  assert.equal(Object.hasOwn(requirement, "why_unresolved"), false,
    "a resolved requirement kept a why_unresolved list");
  assert.equal(requirement.what_was_unbound.some(clause =>
    /r7 registers no v5 producer/i.test(clause)), false,
    "the stale clause is back");
  assert.match(requirement.corrected_stale_clause.corrected_by, /311a9af5/);
  assert.ok(/record layer/i.test(requirement.scope));
  assert.equal(/verdict|passed|green/i.test(requirement.scope), false);
  assert.ok(requirement.what_was_unbound.some(clause => /this record layer held no/i.test(clause)));
  // THE REFUSALS SURVIVE THE RESOLUTION, and one is NEW because resolving it
  // created a way to get it wrong that did not exist before: a recorded
  // non-passing or expired outcome standing in for a current one.
  for (const clause of [
    "a Gate Zero outcome digest supplied by a caller",
    "a digest derived from a synthetic test fixture",
    "a Gate Zero policy invented in this slice",
    "a recorded non-passing or expired outcome standing in for a current one",
  ]) assert.ok(requirement.still_refused_after_resolution.includes(clause), clause);
  // Resolving Gate Zero must not silently upgrade the coverage attestation,
  // which is the fourth thing that requirement explicitly refuses.
  assert.ok(requirement.still_refused_after_resolution.some(
    clause => /silently upgrading the measurement coverage attestation/i.test(clause)));
});

test("PAIRED SELFTEST — neither Gate Zero reader may be implemented without the other", () => {
  // THE PAIRING WAS ENFORCED BY TWO THROWS AND IS NOW ENFORCED BY THIS TEST.
  // While both readers were fail-closed stubs, "delete the throw in one and the
  // gate opens without a record" was the hazard, and the throws themselves were
  // the guard. Both are implemented now, so the hazard reversed: reverting
  // EITHER side to a raise while the other still reads is a half-finished
  // rollback, and that is what this asserts — from the two sources, because
  // there is no runtime state that would show it.
  const moduleSource = readFileSync(
    new URL("../src/benchmark-acceptance-store.v5.js", import.meta.url), "utf8");
  const correction = readFileSync(
    new URL("../../migrations/0506_gate_zero_applied_0505_recovery.sql", import.meta.url), "utf8");
  const candidate = readFileSync(
    new URL("../../ops/benchmark-acceptance.candidate.sql", import.meta.url), "utf8");

  // (a) THE MODULE HALF READS. It takes a connection, selects the current
  // passing row, and is not a parameterless always-throwing stub.
  assert.match(moduleSource, /async function readGateZeroOutcome\(c\) \{/);
  assert.match(moduleSource, /from ops\.gate_zero_read_only_outcome/);
  assert.match(moduleSource, /select step_ref, receipt, outcome_digest,/);
  assert.match(moduleSource, /digest\(\[CONSUMER_GATE_RECEIPT_SCHEMA, row\.receipt\]\)/);
  assert.equal(/function readGateZeroOutcome\(\) \{/.test(moduleSource), false,
    "the module reader reverted to the parameterless stub while the SQL half still reads");

  // (b) BOTH CURRENT SQL HALVES READ AND RECOMPUTE: the forward correction that
  // binds, and the candidate source that must not drift from it. A body present
  // in one and absent from the other is the half-landed state the pairing guard
  // exists to catch.
  for (const [name, sql] of [["migration 0506", correction], ["candidate source", candidate]]) {
    assert.match(sql, /create or replace function ops\.benchmark_gate_zero_outcome\(\)/, name);
    assert.match(sql, /where status = 'pass' and ttl_expires_at > now\(\)/, name);
    assert.ok(sql.includes("order by observed_at desc, outcome_digest collate \"C\" desc"), name);
    assert.match(sql, /ops\.gate_zero_outcome_digest\(v_row\.receipt\)/, name);
    assert.match(sql, /Gate Zero outcome digest divergence/, name);
  }
  const normalizedDefinition = sql => {
    const definition = sql.match(
      /create or replace function ops\.benchmark_gate_zero_outcome\(\)[\s\S]*?\n\$\$;/i)?.[0];
    assert.ok(definition, "the current Gate Zero SQL reader definition is absent");
    return definition.replace(/--[^\n]*/g, " ").replace(/\s+/g, " ").trim();
  };
  assert.equal(normalizedDefinition(correction), normalizedDefinition(candidate),
    "the migration and candidate SQL readers drifted in executable body");

  // (c) AND BOTH STILL FAIL CLOSED. Implementing them was not the same as
  // opening them: each still raises when no current passing outcome exists, and
  // a reader that stopped raising would be a gate opened by omission.
  assert.match(moduleSource, /refuse\("gate_zero_outcome_unresolved"/);
  assert.match(moduleSource, /refuse\("gate_zero_outcome_not_current"/);
  for (const [name, sql] of [["migration 0505", correction], ["candidate source", candidate]]) {
    assert.equal(sql.includes(
      "benchmark acceptance requires a current passing Gate Zero read-only outcome"), true, name);
    assert.equal(sql.includes(
      "benchmark acceptance requires an authenticated Gate Zero read-only outcome binding, and none has been recorded here yet"),
      true, name);
  }
});

test("the coverage requirement is resolved and its trust boundary is unchanged", () => {
  const requirement = BENCHMARK_MEASUREMENT_COVERAGE_INTEGRATION_REQUIREMENT;
  assert.equal(requirement.resolved, true);
  assert.equal(requirement.binding_ref, "binding:benchmark-measurement-coverage-proof");
  assert.equal(requirement.independent_of, GATE_ZERO_STEP_REF);
  // ALL FOUR REFUSALS SURVIVE THE RESOLUTION. Resolving the binding retires none
  // of them, and the last one is the reason the list is checked here rather than
  // deleted with the gap: "silently upgrading the assertion when the Gate Zero
  // binding lands" is a rule about the change that has NOT happened yet.
  assert.ok(requirement.explicitly_refused.includes(
    "treating measurement_set_digest as evidence that coverage was proved"));
  assert.ok(requirement.explicitly_refused.includes(
    "a coverage verdict supplied by a caller"));
  assert.ok(requirement.explicitly_refused.includes(
    "a second coverage evaluator written outside benchmark-minimum.v5.js"));
  assert.ok(requirement.explicitly_refused.includes(
    "silently upgrading the assertion when the Gate Zero binding lands"));
  // KEPT VERBATIM. This is the sentence that set the bar before the work was
  // done, and a resolution that quietly enlarged it would be the overselling the
  // requirement exists to prevent. Asserted whole, not by keyword.
  assert.equal(requirement.remaining_trust_boundary,
    "The samples stay outside the record layer. The attestation makes a trusted writer's assertion explicit, attributed and auditable; it does not make the record layer an independent verifier of coverage, because the evaluation cannot be repeated there.");
  // The history is kept rather than deleted, and it reads as history.
  assert.ok(requirement.what_was_unbound.some(clause => /trusted/i.test(clause)));
  assert.equal(Object.hasOwn(requirement, "why_unresolved"), false);
  // Both readers landed together, which is what the second clause demanded.
  assert.ok(requirement.resolved_by.some(clause => /ops\.benchmark_measurement_coverage_binding/.test(clause)));
  assert.ok(requirement.resolved_by.some(clause => /readMeasurementCoverageBinding/.test(clause)));
  // And the third clause: Gate Zero is untouched and said to be untouched.
  assert.ok(requirement.still_unresolved_elsewhere.some(clause => /Gate Zero/.test(clause)));
});

test("the coverage evaluator set is a closed frozen constant naming the kernel", () => {
  // NOT AN EVALUATOR. A name, cited from a closed set, so "which evaluator
  // proved this" is a constrained fact rather than text a writer chooses.
  assert.ok(Object.isFrozen(BENCHMARK_COVERAGE_EVALUATORS));
  assert.deepEqual([...BENCHMARK_COVERAGE_EVALUATORS],
    ["benchmark-minimum.v5.js#evaluateBenchmarkWorkloadCoverage"]);
  assert.equal(BENCHMARK_COVERAGE_EVALUATOR, BENCHMARK_COVERAGE_EVALUATORS[0]);
  // It names the KERNEL module and the KERNEL export. If this file ever named
  // something inside benchmark-acceptance-store.v5.js, a second coverage
  // authority would have been written.
  assert.ok(/^benchmark-minimum\.v5\.js#/.test(BENCHMARK_COVERAGE_EVALUATOR));
});

test("both integration requirements are frozen and name what they refuse to invent", () => {
  for (const requirement of [BENCHMARK_GATE_ZERO_INTEGRATION_REQUIREMENT,
    BENCHMARK_MEASUREMENT_COVERAGE_INTEGRATION_REQUIREMENT]) {
    assert.ok(Object.isFrozen(requirement));
    assert.ok(Object.isFrozen(requirement.explicitly_refused));
    assert.throws(() => { requirement.resolved = true; }, TypeError);
  }
});

// --- the derived acceptor ---------------------------------------------------

test("a verified partner's authority class is derived from the live actor", () => {
  const acceptor = deriveBenchmarkAcceptor(PARTNER);
  assert.equal(acceptor.actor_id, "joe");
  assert.equal(acceptor.authority_class, "verified_partner");
  assert.equal(acceptor.authority_class_source, "identity.authorizationClassForActor");
  assert.equal(acceptor.producer_role, BENCHMARK_PRODUCER_ROLE);
});

test("a verified native agent derives the benchmark acceptor from its server-held sponsor", () => {
  const acceptor = deriveBenchmarkAcceptor(VERIFIED_AGENT);
  assert.equal(acceptor.actor_id, "joe");
  assert.equal(acceptor.authority_class, "verified_partner");
  assert.equal(acceptor.authority_class_source,
    "partner-authority.partnerAuthoritySlugForActor");
  assert.equal(acceptor.producer_role, BENCHMARK_PRODUCER_ROLE);
});

test("no actor short of a verified human partner may be the acceptor", () => {
  const cases = [
    [AGENT, "sponsored_agent"],
    [{ slug: "codex", human: true }, "unsponsored_agent"],
    [{ slug: "joe", human: false, sponsoring_human_slug: "joe" }, "sponsored_agent"],
    [{ slug: "hermes-pilot", human: false, hermes: true }, "unsponsored_agent"],
    // A self-asserted class on the actor object. It is not consulted, so it
    // does not rescue an actor whose live shape is a sponsored agent.
    [{ slug: "codex", human: false, sponsoring_human_slug: "joe", authority_class: "verified_partner" },
      "sponsored_agent"],
  ];
  for (const [actor, expected] of cases) {
    const error = thrown(() => deriveBenchmarkAcceptor(actor), BenchmarkAcceptanceStoreError);
    assert.equal(error.code, "verified_partner_required");
    assert.equal(error.detail.derived_authority_class, expected);
  }
  assert.throws(() => deriveBenchmarkAcceptor(null), BenchmarkAcceptanceStoreError);
});

test("a self-asserted authority_class on a real partner changes nothing", () => {
  // The class is recomputed from the live actor either way. A claim that agrees
  // with the derivation is not what admits this actor, and one that disagreed
  // would not have admitted the actor above.
  const claimed = deriveBenchmarkAcceptor({ ...PARTNER, authority_class: "unsponsored_agent" });
  assert.equal(claimed.authority_class, "verified_partner");
  assert.deepEqual(claimed, deriveBenchmarkAcceptor(PARTNER));
});

// --- the verb surface -------------------------------------------------------

test("the verbs carry the expected write, humanOnly and authorityOnly metadata", () => {
  assert.deepEqual(Object.keys(tools).sort(), [
    "accept-benchmark-manifest-draft", "propose-benchmark-manifest-draft",
    "read-benchmark-manifest", "review-benchmark-manifest-draft",
  ]);
  assert.equal(tools["read-benchmark-manifest"].write, false);
  assert.equal(tools["propose-benchmark-manifest-draft"].write, true);
  assert.equal(tools["review-benchmark-manifest-draft"].write, true);
  const accept = tools["accept-benchmark-manifest-draft"];
  assert.equal(accept.write, true);
  assert.equal(accept.humanOnly, true);
  assert.equal(accept.authorityOnly, true);
  // Only acceptance is human/authority gated; a writer connection must be able
  // to propose and review, and must not be able to accept.
  for (const name of ["read-benchmark-manifest", "propose-benchmark-manifest-draft",
    "review-benchmark-manifest-draft"]) {
    assert.equal(tools[name].humanOnly, undefined);
    assert.equal(tools[name].authorityOnly, undefined);
  }
});

test("every verb takes a closed input schema and no identity or authority field", () => {
  const forbidden = /^(actor|actor_id|partner|tenant|accepted_by|accepted_by_identity|accepted_at|acceptor|verified|verified_human|partner_confirmed|human_approved|authority_class|authority_granted|gate_zero.*|status)$/;
  for (const [name, tool] of Object.entries(tools)) {
    assert.equal(tool.inputSchema.additionalProperties, false, `${name} has an open input schema`);
    for (const key of Object.keys(tool.inputSchema.properties)) {
      assert.ok(!forbidden.test(key), `${name} accepts "${key}" from a caller`);
    }
  }
});

// THE DRIFT ASSERTION. Five stale comments were the whole of the last review's
// rejection, and exactly one of them was machine-readable: the read verb's
// description told callers the coverage proof was unbound while the JSON the
// same call returns said resolved:true. Nothing caught it because nothing read
// the description. This does.
//
// It derives the expected word from the constant's own `resolved` flag rather
// than restating today's answer, so it bites in BOTH directions: flipping a
// flag without rewriting the prose fails here, and rewriting the prose ahead of
// the flag fails here too. WHAT IT DOES NOT COVER, said plainly: the other four
// stale sites were SQL and JS comments, which no test can read, and this is not
// a general guard against a comment going stale. It guards the one surface that
// ships to callers.
test("the read verb's description says bound or unbound to match each requirement's own flag", () => {
  const description = tools["read-benchmark-manifest"].description;
  const bindings = [
    ["the Gate Zero outcome", BENCHMARK_GATE_ZERO_INTEGRATION_REQUIREMENT],
    ["the measurement coverage proof", BENCHMARK_MEASUREMENT_COVERAGE_INTEGRATION_REQUIREMENT],
  ];
  for (const [named, requirement] of bindings) {
    const said = new RegExp(`${named} \\((unbound|bound)`).exec(description);
    assert.ok(said, `the description does not say whether ${named} is bound`);
    assert.equal(said[1], requirement.resolved ? "bound" : "unbound",
      `the description contradicts resolved:${requirement.resolved} for ${named}`);
  }
  // Resolving a binding must not quietly delete what it is still NOT. The same
  // pair of clauses is pinned on the accept verb's description below.
  if (BENCHMARK_MEASUREMENT_COVERAGE_INTEGRATION_REQUIREMENT.resolved) {
    assert.ok(/not an independent verification/i.test(description));
    assert.ok(/samples stay outside this record layer/i.test(description));
  }
});

// --- propose ----------------------------------------------------------------

test("propose sends the decomposed rows and the digest it computed itself", async () => {
  const c = mockDatabase({ benchmark_propose_manifest_draft: [{ id: DRAFT_ID }] });
  const body = payload();
  const expected = benchmarkPayloadDigest(body);
  const result = await tools["propose-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY, benchmark_ref: "BENCH-A00", draft_version: 1,
    payload_digest: expected, payload: body,
  });

  assert.equal(c.calls.length, 1);
  const [, , , suppliedDigest, scalars, dimensions, workloads, sizes, concurrency, browsers, evaluators] =
    c.calls[0].params;
  assert.equal(suppliedDigest, expected);
  assert.deepEqual(JSON.parse(scalars).samples_per_cell, 20);
  assert.equal(JSON.parse(dimensions).length,
    BENCHMARK_DIMENSIONS.reduce((total, key) => total + body[key].length, 0));
  assert.equal(JSON.parse(workloads).length, 2);
  assert.equal(JSON.parse(sizes).length, 2);
  assert.equal(JSON.parse(concurrency).length, 1);
  assert.equal(JSON.parse(browsers).length, 1);
  assert.equal(JSON.parse(evaluators).length, 1);

  assert.equal(result.draft_id, DRAFT_ID);
  assert.equal(result.payload_digest, expected);
  assert.equal(result.accepted, false);
  assert.equal(result.admissibility.accepted, false);
  assert.equal(result.admissibility.proposed_benchmark_manifest_digest, expected);
  // The rail's bindings are all present now; proposing one still accepts nothing.
  assert.equal(result.acceptance_prerequisites.acceptance_available, true);
  assert.equal(result.effects.creates_effect, false);
  assert.equal(result.effects.clock_started, false);
  assert.equal(c.events.length, 1);
  // THE FOUR VALUES THE OBJECT CALL SHAPE LOST, each NOT NULL in event.
  const [proposed] = c.events;
  assert.equal(proposed.actor, PARTNER, "the actor must be forwarded, not replaced by an options object");
  assert.equal(proposed.verb, "propose-benchmark-manifest-draft");
  assert.equal(proposed.subjectType, "benchmark");
  assert.equal(proposed.subjectId, DRAFT_ID);
  assert.equal(proposed.fields.idempotency_key, KEY);
  assert.equal(proposed.fields.new.benchmark_ref, "BENCH-A00");
  assert.equal(proposed.fields.new.draft_version, 1);
  // The digest audited is the one this module computed, never the caller's.
  assert.equal(proposed.fields.new.payload_digest, expected);
});

test("propose refuses a supplied digest that is not the one the payload produces", async () => {
  const c = mockDatabase({ benchmark_propose_manifest_draft: [{ id: DRAFT_ID }] });
  const refused = await refusal(tools["propose-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY, benchmark_ref: "BENCH-A00", draft_version: 1,
    payload_digest: D(99), payload: payload(),
  }));
  assert.equal(refused.error, "benchmark_payload_digest_mismatch");
  assert.equal(refused.expected, benchmarkPayloadDigest(payload()));
  assert.equal(c.calls.length, 0, "a mismatched digest reached the database");
});

test("propose refuses an acceptance-envelope field before any statement", async () => {
  const c = mockDatabase({ benchmark_propose_manifest_draft: [{ id: DRAFT_ID }] });
  const body = { ...payload(), status: "accepted" };
  const refused = await refusal(tools["propose-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY, benchmark_ref: "BENCH-A00", draft_version: 1,
    payload_digest: D(98), payload: body,
  }));
  assert.equal(refused.error, "benchmark_acceptance_envelope_refused");
  assert.equal(c.calls.length, 0);
});

// --- review -----------------------------------------------------------------

function liveDraftResponse(body = payload()) {
  return {
    benchmark_payload_preimage: [{
      payload: body,
      payload_digest: benchmarkPayloadDigest(body),
      structure_error: null,
    }],
  };
}

function foundationEvidence(body = payload(), measurementSet = measurements(body)) {
  const cells = benchmarkRequiredCells(body);
  const evidence = {
    schema_version: "doctorcre-v5-foundation-assurance-evidence.v1",
    source_sha: "a".repeat(40), source_tree: "b".repeat(40),
    staging_provider_version: "00000000-0000-4000-8000-000000000010",
    final_provider_version: "00000000-0000-4000-8000-000000000011",
    release: {
      key: "release.foundation-assurance.minimum.v1",
      provider_version: "00000000-0000-4000-8000-000000000011",
      source_sha: "a".repeat(40), test_evidence_ref: "pending",
    },
    database: {
      environment: "staging", migration: "0513_release_candidate_environment_identity.sql",
      read_only: true,
      source: "ops/foundation-assurance-candidate-rehearsal.py --read-foundation-facts",
    },
    github_checks: FOUNDATION_ASSURANCE_GITHUB_CHECKS.map((name, index) => ({
      name, conclusion: "success", head_sha: "a".repeat(40), run_id: index + 1,
      url: `https://github.com/jbookout/carr-system/actions/runs/${index + 1}`,
    })),
    benchmark_config_digest: digest(FOUNDATION_CONFIG),
    benchmark_payload: body,
    measurements: measurementSet,
    sample_provenance: cells.map((cell, index) => ({
      cell_digest: digest([BENCHMARK_CELL_DOMAIN_TAG, cell]),
      origin: `https://staging.doctorcre.com/measurement/${index}`,
      sample_count: FOUNDATION_CONFIG.samples_per_cell,
      warmup_count: FOUNDATION_CONFIG.warmup_runs,
    })),
    comparators: FOUNDATION_ASSURANCE_COMPARATORS.map(id => ({
      id, status: "pass", detail_digest: digest({ id, ok: true }),
      origin: `https://staging.doctorcre.com/comparator/${id}`,
    })),
    captured_at: "2026-09-14T13:30:00.000Z",
  };
  const evidenceDigest = foundationAssuranceEvidenceDigest(evidence);
  evidence.release.test_evidence_ref = `safe:wr95-evidence/${evidenceDigest.slice(7)}`;
  return evidence;
}

function reviewMaterialResponse(body = payload(), measurementSet = measurements(body)) {
  return {
    foundation_assurance_benchmark_review_material: [{
      material: { config: FOUNDATION_CONFIG, evidence: foundationEvidence(body, measurementSet) },
    }],
  };
}

/**
 * The row the private coverage reader's own statement comes back with.
 *
 * The reader joins the review to its attestation and asks the database to
 * RECOMPUTE the draft's payload digest beside them. This helper returns the
 * healthy shape; each negative below overrides exactly one field, which is what
 * makes the four refusals separable rather than one refusal with four names.
 */
function coverageBindingResponse(body = payload(), overrides = {}) {
  const evidence = measurements(body);
  return {
    benchmark_measurement_coverage_attestation: [{
      review_id: REVIEW_ID,
      review_measurement_set_digest: digest(evidence),
      live_payload_digest: benchmarkPayloadDigest(body),
      coverage_proved_by: BENCHMARK_COVERAGE_EVALUATOR,
      attested_payload_digest: benchmarkPayloadDigest(body),
      attested_measurement_set_digest: digest(evidence),
      ...overrides,
    }],
  };
}

/** A passing review handled against a healthy record, for reuse by the negatives. */
function passingReview(body, extraResponses = {}) {
  const c = mockDatabase({
    ...liveDraftResponse(body),
    ...reviewMaterialResponse(body),
    benchmark_review_manifest_draft: [{ id: REVIEW_ID }],
    ...coverageBindingResponse(body),
    ...extraResponses,
  });
  return [c, tools["review-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY, draft_id: DRAFT_ID,
    reviewed_payload_digest: benchmarkPayloadDigest(body),
    verdict: "pass", review_summary: "matrix complete, every cell inside its fixed SLO",
  })];
}

test("a passing review proves coverage against the stored payload and binds its digest", async () => {
  const body = payload();
  const c = mockDatabase({
    ...liveDraftResponse(body),
    ...reviewMaterialResponse(body),
    benchmark_review_manifest_draft: [{ id: REVIEW_ID }],
    ...coverageBindingResponse(body),
  });
  const evidence = measurements(body);
  const result = await tools["review-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY, draft_id: DRAFT_ID,
    reviewed_payload_digest: benchmarkPayloadDigest(body),
    verdict: "pass", review_summary: "matrix complete, every cell inside its fixed SLO",
  });

  // FOUR STATEMENTS: read the draft, read server-derived evidence, write the
  // review with its attestation, then read the attestation back.
  assert.equal(c.calls.length, 4);
  const params = c.calls[2].params;
  // The digest written is the one read back from the rows, not the one supplied.
  assert.equal(params[2], benchmarkPayloadDigest(body));
  assert.equal(params[3], "pass");
  assert.equal(params[4], digest(evidence));
  assert.equal(result.review_id, REVIEW_ID);
  assert.equal(result.measurement_set_digest, digest(evidence));
  assert.equal(result.coverage_proved_against_stored_payload, true);
  // THE REAL ANSWER, and it is real because it came back from the record layer
  // through the same reader acceptance uses -- not from this handler restating
  // the branch it is in.
  assert.equal(result.measurement_coverage_proof_recorded, true);
  assert.deepEqual(result.measurement_coverage_proof, {
    review_id: REVIEW_ID,
    measurement_set_digest: digest(evidence),
    coverage_proved_by: BENCHMARK_COVERAGE_EVALUATOR,
  });
  assert.equal(result.measurement_coverage_proof_binding,
    BENCHMARK_MEASUREMENT_COVERAGE_INTEGRATION_REQUIREMENT.binding_ref);
  // AND IT IS NOT OVERSOLD. The result carries the trust boundary verbatim, so a
  // consumer reading measurement_coverage_proof_recorded: true is told in the
  // same object what it is not.
  assert.equal(result.measurement_coverage_proof_limit,
    BENCHMARK_MEASUREMENT_COVERAGE_INTEGRATION_REQUIREMENT.remaining_trust_boundary);
  assert.ok(/does not make the record layer an independent verifier/i
    .test(result.measurement_coverage_proof_limit));
  assert.equal(result.accepted, false);
});

test("the attestation carries the evaluator's OWN returned payload digest, not a recomputation", async () => {
  const body = payload();
  const [c, promise] = passingReview(body);
  await promise;
  const attestation = JSON.parse(c.calls[2].params[6]);
  // FOUR FIELDS, CLOSED. An extra one would be a value the SQL side refuses.
  assert.deepEqual(Object.keys(attestation).sort(), [
    "benchmark_payload_digest", "coverage_proved_by", "evaluation_digest",
    "measurement_set_digest",
  ]);
  // THE FIELD THE WHOLE UNIT EXISTS FOR. This is the value the kernel RETURNED
  // from the evaluation that was actually performed -- taken from the return
  // value that used to be discarded, not computed a second time beside it.
  const coverage = evaluateBenchmarkWorkloadCoverage({
    payload: body, measurements: measurements(body),
  });
  assert.equal(attestation.benchmark_payload_digest, coverage.benchmark_payload_digest);
  // And the evaluation itself is digested, so a holder of the samples can replay
  // the judgement rather than having to trust that it happened.
  assert.equal(attestation.evaluation_digest, digest(coverage));
  assert.equal(attestation.measurement_set_digest, digest(measurements(body)));
  // The evaluator is the closed constant, never a caller value and never text
  // this handler composed.
  assert.equal(attestation.coverage_proved_by, BENCHMARK_COVERAGE_EVALUATOR);
});

test("the attestation is never a caller input: it is not in the verb's schema", () => {
  const schema = tools["review-benchmark-manifest-draft"].inputSchema;
  assert.equal(schema.additionalProperties, false);
  // Every coverage input and attested value is derived from server-held release
  // evidence; none is accepted from the MCP caller.
  for (const key of ["coverage_proved_by", "coverage", "coverage_attestation",
    "benchmark_payload_digest", "evaluation_digest", "measurement_set_digest"]) {
    assert.equal(Object.hasOwn(schema.properties, key), false,
      `${key} is a caller input on the review verb`);
  }
  assert.deepEqual(Object.keys(schema.properties).sort(), [
    "draft_id", "idempotency_key", "review_summary", "reviewed_payload_digest",
    "verdict",
  ]);
});

test("a recorded attestation naming a payload digest the draft no longer produces refuses", async () => {
  // THE CASE THE RECOMPUTATION EXISTS FOR. A draft's content can still be
  // appended to before acceptance, which moves the digest its rows produce. An
  // attestation that outlived those bytes proves nothing about them.
  const body = payload();
  const [c, promise] = passingReview(body, coverageBindingResponse(body, {
    attested_payload_digest: D(77),
  }));
  const refused = await refusal(promise);
  assert.equal(refused.error, "measurement_coverage_payload_stale");
  assert.equal(refused.detail.attested, D(77));
  assert.equal(refused.detail.recomputed, benchmarkPayloadDigest(body));
  // The review is not returned as a pass whose proof could not be read.
  assert.equal(c.events.length, 0);
});

test("a recorded attestation over a measurement set the review does not name refuses", async () => {
  const body = payload();
  const [, promise] = passingReview(body, coverageBindingResponse(body, {
    attested_measurement_set_digest: D(66),
  }));
  const refused = await refusal(promise);
  assert.equal(refused.error, "measurement_coverage_measurement_mismatch");
  assert.equal(refused.detail.attested, D(66));
  assert.equal(refused.detail.reviewed, digest(measurements(body)));
});

test("a recorded attestation naming an evaluator outside the closed set refuses", async () => {
  const body = payload();
  const [, promise] = passingReview(body, coverageBindingResponse(body, {
    coverage_proved_by: "some-other-module.js#proveCoverage",
  }));
  const refused = await refusal(promise);
  assert.equal(refused.error, "measurement_coverage_evaluator_unknown");
  assert.equal(refused.detail.coverage_proved_by, "some-other-module.js#proveCoverage");
  assert.deepEqual(refused.detail.admitted, [...BENCHMARK_COVERAGE_EVALUATORS]);
});

test("a review with no recorded attestation refuses on the unbound coverage proof", async () => {
  // The write path refuses an unattested pass before this can happen; this is
  // the reader's own fail-closed half, and it keeps the refusal code the
  // requirement has always been quoted under.
  const body = payload();
  const [, promise] = passingReview(body, coverageBindingResponse(body, {
    coverage_proved_by: null, attested_payload_digest: null,
    attested_measurement_set_digest: null,
  }));
  const refused = await refusal(promise);
  assert.equal(refused.error, "measurement_coverage_proof_unbound");
  assert.equal(refused.detail.binding_ref,
    BENCHMARK_MEASUREMENT_COVERAGE_INTEGRATION_REQUIREMENT.binding_ref);
  assert.equal(refused.detail.review_id, REVIEW_ID);
});

test("a review naming an unknown draft is a named refusal, not a raw database error", async () => {
  // The draft lookup is in the FROM clause, so an unknown id comes back as zero
  // rows. Called as bare scalars these functions RAISE, and the raise would have
  // surfaced as a database error in place of a refusal a caller can act on.
  const c = mockDatabase({ benchmark_payload_preimage: [] });
  const refused = await refusal(tools["review-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY, draft_id: DRAFT_ID,
    reviewed_payload_digest: benchmarkPayloadDigest(payload()),
    verdict: "fail", review_summary: "no such draft",
  }));
  assert.equal(refused.error, "benchmark_draft_unknown");
  assert.equal(refused.draft_id, DRAFT_ID);
  assert.equal(c.calls.length, 1, "the review continued past an unknown draft");
  assert.equal(c.events.length, 0);
  // And the statement it did issue selects the draft row rather than calling the
  // raising functions on a bare id.
  assert.ok(/from\s+ops\.benchmark_manifest_draft/i.test(c.calls[0].sql));
});

test("an over-long review summary is refused by name before any statement", async () => {
  // The column bounds this at 1000 too. Asserting it here is what turns a check
  // constraint violation into a refusal that names the field and the number, and
  // both sides count UTF-16 code units so the bound means one thing.
  const c = mockDatabase(liveDraftResponse());
  const refused = await refusal(tools["review-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY, draft_id: DRAFT_ID,
    reviewed_payload_digest: benchmarkPayloadDigest(payload()),
    verdict: "fail", review_summary: "x".repeat(1001),
  }));
  assert.equal(refused.error, "invalid_review_summary");
  assert.equal(refused.detail.length, 1001);
  assert.equal(refused.detail.maximum, 1000);
  assert.equal(c.calls.length, 0);
});

test("a blank review summary is refused by name", async () => {
  const c = mockDatabase(liveDraftResponse());
  const refused = await refusal(tools["review-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY, draft_id: DRAFT_ID,
    reviewed_payload_digest: benchmarkPayloadDigest(payload()),
    verdict: "fail", review_summary: "   ",
  }));
  assert.equal(refused.error, "invalid_review_summary");
  assert.equal(c.calls.length, 0);
});

test("an astral review summary is measured in UTF-16 code units, as SQL measures it", async () => {
  // 501 astral codepoints is 501 characters to SQL's char_length and 1002 code
  // units to String#length. The candidate SQL's review guard counts the same way
  // through ops.benchmark_utf16_length(), so the two sides refuse the same text.
  const summary = "\u{1F600}".repeat(501);
  assert.equal([...summary].length, 501);
  assert.equal(summary.length, 1002);
  const c = mockDatabase(liveDraftResponse());
  const refused = await refusal(tools["review-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY, draft_id: DRAFT_ID,
    reviewed_payload_digest: benchmarkPayloadDigest(payload()),
    verdict: "fail", review_summary: summary,
  }));
  assert.equal(refused.error, "invalid_review_summary");
  assert.equal(refused.detail.length, 1002);
  assert.equal(c.calls.length, 0);
});

test("a passing review with no server-derived evidence is refused", async () => {
  const c = mockDatabase({
    ...liveDraftResponse(),
    foundation_assurance_benchmark_review_material: [{ material: null }],
  });
  const refused = await refusal(tools["review-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY, draft_id: DRAFT_ID,
    reviewed_payload_digest: benchmarkPayloadDigest(payload()),
    verdict: "pass", review_summary: "looks fine",
  }));
  assert.equal(refused.error, "foundation_assurance_evidence_unavailable");
  assert.equal(c.calls.length, 2);
});

test("a passing review whose server evidence misses a required cell is refused", async () => {
  const body = payload();
  const evidence = measurements(body);
  evidence.cells = evidence.cells.slice(1);
  const c = mockDatabase({ ...liveDraftResponse(body), ...reviewMaterialResponse(body, evidence) });
  const refused = await refusal(tools["review-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY, draft_id: DRAFT_ID,
    reviewed_payload_digest: benchmarkPayloadDigest(body),
    verdict: "pass", review_summary: "partial run",
  }));
  assert.equal(refused.error, "benchmark_matrix_coverage_incomplete");
  assert.equal(c.calls.length, 2);
});

test("a passing review whose server evidence misses an SLO is refused", async () => {
  const body = payload();
  const evidence = measurements(body);
  const ack = evidence.cells.find(entry => entry.cell.metric === "command_acknowledgement_ms");
  ack.samples = Array.from({ length: 20 }, () => 5000);
  const c = mockDatabase({ ...liveDraftResponse(body), ...reviewMaterialResponse(body, evidence) });
  const refused = await refusal(tools["review-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY, draft_id: DRAFT_ID,
    reviewed_payload_digest: benchmarkPayloadDigest(body),
    verdict: "pass", review_summary: "slow ack",
  }));
  assert.equal(refused.error, "benchmark_slo_not_met");
  assert.equal(c.calls.length, 2);
});

test("a review naming a digest the draft no longer produces is refused", async () => {
  const c = mockDatabase(liveDraftResponse());
  const refused = await refusal(tools["review-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY, draft_id: DRAFT_ID,
    reviewed_payload_digest: D(97), verdict: "fail", review_summary: "stale",
  }));
  assert.equal(refused.error, "benchmark_review_digest_stale");
  assert.equal(refused.expected, benchmarkPayloadDigest(payload()));
  assert.equal(c.calls.length, 1);
});

test("a failing review needs no measurement set, no attestation, and reads none back", async () => {
  const c = mockDatabase({
    ...liveDraftResponse(),
    benchmark_review_manifest_draft: [{ id: REVIEW_ID }],
    // Declared but never matched: a fail verdict must not read the attestation
    // back, and if it did this response would let it pass silently. The call
    // count below is what actually proves it did not.
    ...coverageBindingResponse(),
  });
  const result = await tools["review-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY, draft_id: DRAFT_ID,
    reviewed_payload_digest: benchmarkPayloadDigest(payload()),
    verdict: "fail", review_summary: "the outlier rule is unexecutable as written",
  });
  assert.equal(result.measurement_set_digest, null);
  assert.equal(result.coverage_proved_against_stored_payload, false);
  assert.equal(c.calls[1].params[4], null);
  // A FAIL VERDICT PROVES NO COVERAGE AND STORES NO ATTESTATION. The seventh
  // parameter is null, so the SQL side records nothing -- and it refuses a
  // non-pass that arrives carrying one.
  assert.equal(c.calls[1].params[6], null);
  assert.equal(result.measurement_coverage_proof_recorded, false);
  assert.equal(result.measurement_coverage_proof, null);
  assert.equal(c.calls.length, 2, "a failing review read a coverage attestation back");
});

test("a review writes its audit event with a real actor, verb and subject", async () => {
  // The review call site carried the same options object as propose. An audit
  // insert that died here would roll the review back, so a pass verdict the
  // reviewer watched succeed would simply not exist.
  const body = payload();
  const [c, pending] = passingReview(body);
  await pending;

  assert.equal(c.events.length, 1);
  const [reviewed] = c.events;
  assert.equal(reviewed.actor, PARTNER, "the actor must be forwarded, not replaced by an options object");
  assert.equal(reviewed.verb, "review-benchmark-manifest-draft");
  assert.equal(reviewed.subjectType, "benchmark");
  assert.equal(reviewed.subjectId, DRAFT_ID);
  assert.equal(reviewed.fields.idempotency_key, KEY);
  assert.equal(reviewed.fields.new.verdict, "pass");
  // The digest audited is the one read back from the stored rows.
  assert.equal(reviewed.fields.new.reviewed_payload_digest, benchmarkPayloadDigest(body));
  assert.equal(reviewed.fields.new.measurement_set_digest, digest(measurements(body)));
});

// --- acceptance: reachable success and fail-closed prerequisites ------------

test("acceptance fails closed when no Gate Zero outcome has been recorded, and writes nothing", async () => {
  // THE ASSERTION MOVED WITH THE POLICY, AND THIS IS THE HONEST FORM OF IT.
  // While Gate Zero was a stub the strongest claim was "ZERO statements". The
  // reader is a real read now, so it necessarily issues one — and the claim
  // that replaces it is stronger where it matters: the path issues only READS
  // and writes NOTHING when the binding refuses.
  // ORDER MATTERS in mockDatabase: it matches by substring in insertion order,
  // and the exists probe contains the table name too, so it is declared first.
  const c = mockDatabase({
    "select exists (select 1 from ops.gate_zero_read_only_outcome)": [{ recorded: false }],
    "from ops.gate_zero_read_only_outcome": [],
  });
  const refused = await refusal(tools["accept-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY, draft_id: DRAFT_ID,
    accepted_payload_digest: benchmarkPayloadDigest(payload()),
    review_id: REVIEW_ID, portfolio_ref: "WR-DOCTORCRE-V5",
  }));
  assert.equal(refused.error, "gate_zero_outcome_unresolved");
  assert.equal(refused.detail.step_ref, GATE_ZERO_STEP_REF);
  assert.equal(refused.detail.recorded_outcomes_present, false);
  assert.equal(c.events.length, 0, "the acceptance path recorded an event");
  for (const call of c.calls)
    assert.match(call.sql, /^\s*select/i, `the acceptance path issued a non-read: ${call.sql}`);
});

test("a recorded but non-current Gate Zero outcome refuses differently, and still writes nothing", async () => {
  // TWO EMPTY CASES, TOLD APART. "Nothing ever ran" and "everything that ran is
  // failing or expired" are different problems for whoever hits them, and a
  // rail that reported them identically would make a quarantined Gate Zero look
  // like a Gate Zero that never happened. This is Q036.D1's truthful-failure
  // clause reaching the acceptance rail.
  const c = mockDatabase({
    "select exists (select 1 from ops.gate_zero_read_only_outcome)": [{ recorded: true }],
    "from ops.gate_zero_read_only_outcome": [],
  });
  const refused = await refusal(tools["accept-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY, draft_id: DRAFT_ID,
    accepted_payload_digest: benchmarkPayloadDigest(payload()),
    review_id: REVIEW_ID, portfolio_ref: "WR-DOCTORCRE-V5",
  }));
  assert.equal(refused.error, "gate_zero_outcome_not_current");
  assert.equal(refused.detail.recorded_outcomes_present, true);
  assert.equal(c.events.length, 0);
  for (const call of c.calls)
    assert.match(call.sql, /^\s*select/i, `the acceptance path issued a non-read: ${call.sql}`);
});

test("a current Gate Zero row whose stored digest diverges from its receipt refuses before any write", async () => {
  const receipt = { candidate_digest: D(2), status: "pass", evidence_ref: "safe:test:gate-zero" };
  const recomputed = digest(["consumer-gate-receipt.v1", receipt]);
  const recorded = D(99);
  assert.notEqual(recorded, recomputed);
  const c = mockDatabase({
    "from ops.gate_zero_read_only_outcome": [{
      step_ref: GATE_ZERO_STEP_REF,
      receipt,
      outcome_digest: recorded,
      observed_at: "2026-09-13T15:00:00Z",
    }],
  });

  const refused = await refusal(tools["accept-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY, draft_id: DRAFT_ID,
    accepted_payload_digest: benchmarkPayloadDigest(payload()),
    review_id: REVIEW_ID, portfolio_ref: "WR-DOCTORCRE-V5",
  }));
  assert.equal(refused.error, "gate_zero_outcome_digest_divergence");
  assert.equal(refused.detail.recorded, recorded);
  assert.equal(refused.detail.recomputed_here, recomputed);
  assert.equal(c.calls.length, 1);
  assert.equal(c.events.length, 0);
  assert.match(c.calls[0].sql, /^\s*select/i);
});

test("the acceptance audit call remains positional at its source call site", () => {
  // Keep the exact call shape pinned in addition to the successful handler
  // fixture so a future argument-order regression fails at the smallest seam.
  const source = readFileSync(new URL("../src/benchmark-acceptance-store.v5.js", import.meta.url), "utf8");
  const call = source.match(/writeEvent\([^;]*"accept-benchmark-manifest-draft"[^;]*;/);
  assert.ok(call, "the acceptance path no longer writes an audit event");
  // The actor is the second argument, not an options object, and the verb and
  // subject are the third, fourth and fifth.
  assert.match(call[0],
    /^writeEvent\(c, actor, "accept-benchmark-manifest-draft", "benchmark", args\.draft_id,/);
  assert.match(call[0], /idempotency_key: args\.idempotency_key/);
  assert.equal(/writeEvent\(\s*c\s*,\s*\{/.test(call[0]), false);
});

test("the acceptance verb says all three bindings are bound AND that it still refuses", () => {
  const description = tools["accept-benchmark-manifest-draft"].description;
  // BOTH HALVES, because either alone would mislead. A description that only
  // said "bound" would read as an open gate; one that only said "refuses" would
  // hide that the structural gap is closed.
  assert.ok(/ALL THREE BINDINGS ARE NOW BOUND/.test(description));
  assert.ok(/STILL REFUSES UNLESS EACH ANSWERS/.test(description));
  assert.equal(/FOR ONE REMAINING REASON/.test(description), false,
    "the description still claims a remaining structural gap that migration 0502 closed");
  // The non-green case is named where a caller reads it, not only in the SQL.
  assert.ok(/recorded fail, unknown, stale or quarantined outcome is a run and not a binding/i.test(description));
  assert.ok(/truthful-failure-propagation/i.test(description));
  // The coverage retirement is still attributed to its own evidence, and Gate
  // Zero landing must not be described as having upgraded it.
  assert.ok(/on its own evidence and not by Gate Zero/i.test(description));
  assert.ok(/Gate Zero landing did not upgrade it/i.test(description));
  assert.ok(/not an independent verification/i.test(description));
  assert.ok(/samples are outside this record layer/i.test(description));
  // And the portfolio binding is still described as a prerequisite, not lineage.
  assert.ok(/not a claim of lineage/i.test(description));
});

test("a successful acceptance truthfully reports that acceptance was enabled", async () => {
  const receipt = {
    candidate_digest: D(2), status: "pass", evidence_ref: "safe:test:gate-zero",
  };
  const outcomeDigest = digest(["consumer-gate-receipt.v1", receipt]);
  const c = mockDatabase({
    "from ops.gate_zero_read_only_outcome": [{
      step_ref: GATE_ZERO_STEP_REF,
      receipt,
      outcome_digest: outcomeDigest,
      observed_at: "2026-09-14T15:00:00Z",
    }],
    ...coverageBindingResponse(),
    ...liveDraftResponse(),
    benchmark_accept_manifest_draft: [{ id: "dddddddd-eeee-4fff-8000-111111111111" }],
  });

  const result = await tools["accept-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY,
    draft_id: DRAFT_ID,
    accepted_payload_digest: benchmarkPayloadDigest(payload()),
    review_id: REVIEW_ID,
    portfolio_ref: "WR-DOCTORCRE-V5",
  });

  assert.equal(result.accepted, true);
  assert.equal(result.status, "accepted");
  assert.equal(result.effects.acceptance_enabled, true);
  assert.equal(result.effects.benchmark_gate_closed_for_draft, true);
  assert.equal(result.effects.draft_content_frozen, true);
  assert.equal(c.events.length, 1);
});

test("acceptance refuses a non-partner before it reaches the Gate Zero refusal", async () => {
  const c = mockDatabase();
  const refused = await refusal(tools["accept-benchmark-manifest-draft"].handler(c, AGENT, {
    idempotency_key: KEY, draft_id: DRAFT_ID,
    accepted_payload_digest: benchmarkPayloadDigest(payload()),
    review_id: REVIEW_ID, portfolio_ref: "WR-DOCTORCRE-V5",
  }));
  assert.equal(refused.error, "verified_partner_required");
  assert.equal(refused.detail.derived_authority_class, "sponsored_agent");
  assert.equal(c.calls.length, 0);
});

test("proposal and review do not claim acceptance, and acceptance refuses without Gate Zero", async () => {
  // Propose and review both say so in their own results; an acceptance without
  // a current Gate Zero outcome still refuses before any write.
  const c = mockDatabase({
    "select exists (select 1 from ops.gate_zero_read_only_outcome)": [{ recorded: false }],
    "from ops.gate_zero_read_only_outcome": [],
    ...liveDraftResponse(),
    benchmark_propose_manifest_draft: [{ id: DRAFT_ID }],
    benchmark_review_manifest_draft: [{ id: REVIEW_ID }],
  });
  const proposed = await tools["propose-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY, benchmark_ref: "BENCH-A00", draft_version: 1,
    payload_digest: benchmarkPayloadDigest(payload()), payload: payload(),
  });
  const reviewed = await tools["review-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY, draft_id: DRAFT_ID,
    reviewed_payload_digest: benchmarkPayloadDigest(payload()),
    verdict: "fail", review_summary: "not yet",
  });
  for (const result of [proposed, reviewed]) {
    assert.equal(result.accepted, false);
    assert.equal(result.effects.creates_effect, false);
    assert.equal(result.effects.clock_started, false);
  }
  const refused = await refusal(tools["accept-benchmark-manifest-draft"].handler(c, PARTNER, {
    idempotency_key: KEY, draft_id: DRAFT_ID,
    accepted_payload_digest: benchmarkPayloadDigest(payload()),
    review_id: REVIEW_ID, portfolio_ref: "WR-DOCTORCRE-V5",
  }));
  assert.equal(refused.error, "gate_zero_outcome_unresolved");
});
