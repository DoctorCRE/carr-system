// The A00 -> M01 seam, exercised end to end on ONE artifact.
//
// Each module's own suite proves its side of the contract against its own
// fixtures, which is exactly how the two shapes drifted apart in the first
// place: a receipt A00 proposed could not be read by M01 at all, and the same
// receipt carried two different digests on the two sides. Everything here is
// therefore a CROSS-MODULE assertion — the artifact A00 publishes is the artifact
// M01 consumes, byte for byte and digest for digest — and nothing here re-tests
// either module in isolation.
//
// It authenticates nothing real: both gates are driven through their installed
// test-only verifiers, no clock is started, no receipt is issued, and no effect
// is claimed on either side.
import test from "node:test";
import assert from "node:assert/strict";
import { digest } from "../src/artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  BENCHMARK_COST_VARIANCE_THRESHOLDS, BENCHMARK_DEADLINE_CONTRACT, BENCHMARK_MANIFEST_SCHEMA,
  BENCHMARK_MEASUREMENT_SET_SCHEMA, BENCHMARK_PAYLOAD_DOMAIN_TAG, BENCHMARK_SLO_THRESHOLDS,
  CONSUMER_GATE_RECEIPT_FIELDS, FOUNDATION_ASSURANCE_MINIMUM_PROJECTION, GATE_ZERO_STEP_REF,
  MINIMUM_REQUIRED_MEMBERS, P95_AGGREGATION_METHOD,
  benchmarkPayloadDigest, benchmarkRequiredCells, createFoundationAssuranceMinimumGate,
  journeyOneClockMinimumReceiptView, proposeBenchmarkCoverageFact,
} from "../src/benchmark-minimum.v5.js";
import {
  DEADLINE_GAP_SHIFTED, DEADLINE_PLAIN, JOURNEY_ONE_CLOCK_PROJECTION,
  JOURNEY_ONE_DEADLINE_CONTRACT, chicagoThirtyDayDeadline, createJourneyOneClock,
} from "../src/journey-one-clock.v5.js";
import {
  createEphemeralJourneyOneClockJournal, createJourneyOneClockRecorder,
  createJourneyOneClockStore, journeyOneClockHistoryDigest, journeyOneClockKeyForState,
  journeyOneClockScopeKey,
} from "../src/journey-one-clock-store.v5.js";
import {
  createEphemeralJourneyOneMinimumAdmissionJournal, createJourneyOneClockMinimumInputStore,
  createJourneyOneClockProjectionComposer, journeyOneMinimumBenchmarkAcceptedSources,
} from "../src/journey-one-clock-input-store.v5.js";
import { createJourneyOneClockRuntime } from "../src/journey-one-clock-runtime.v5.js";

const D = n => `sha256:${String(n).padStart(2, "0").repeat(32)}`;
const I = actor => ({
  actor_id: actor,
  session_ref: `session:j1-seam-${actor}`,
  authority_class: actor === "joe" || actor === "dell" ? "verified_partner" : "synthetic_oracle",
});
const copy = x => JSON.parse(JSON.stringify(x));
const iso = ms => new Date(ms).toISOString();
const DAY = 86400000;
const HOUR = 3600000;

const GATE_ZERO_AT = "2026-02-01T00:00:00.000Z";
const ACCEPTED_AT = "2026-02-02T00:00:00.000Z";
const COVERAGE_AT = "2026-02-02T12:00:00.000Z";
const OBSERVED_AT = "2026-02-03T00:00:00.000Z";
const AS_OF = "2026-02-04T00:00:00.000Z";
/** The M01 evaluation instant: an hour after the join that proposed the receipt. */
const CLOCK_AS_OF = iso(Date.parse(AS_OF) + HOUR);
const MINIMUM_TTL = 7 * DAY;

// --- the A00 side -----------------------------------------------------------

function payload() {
  return {
    subject_digest: D(1), candidate_digest: D(2), policy_digest: D(3),
    capacity_profiles: ["baseline"],
    workload_mix: [{ workload_id: "search", weight_basis_points: 10000, operation_mix_digest: D(10) }],
    request_size_distribution: [{ percentile: 50, bytes: 2048 }],
    concurrency_levels: [1],
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
function manifest(body = payload(), acceptedAt = ACCEPTED_AT) {
  return {
    ...copy(body),
    benchmark_manifest_digest: digest([BENCHMARK_PAYLOAD_DOMAIN_TAG, body]),
    accepted_by_identity: I("joe"),
    accepted_at: acceptedAt,
    status: "accepted",
  };
}
/**
 * The measurement set and the coverage fact a trusted producer would compose
 * from it. Composed through A00's own evaluator, so the seam is exercised on the
 * numbers that evaluator actually emits.
 */
function measurements(body = payload()) {
  return {
    schema_version: BENCHMARK_MEASUREMENT_SET_SCHEMA,
    benchmark_payload_digest: benchmarkPayloadDigest(body),
    outlier_rule: body.outlier_rule,
    p95_aggregation_method: body.p95_aggregation_method,
    cells: benchmarkRequiredCells(body).map(cell => ({
      cell: copy(cell),
      warmup_samples: Array.from({ length: body.warmup_runs }, () => 999),
      samples: Array.from({ length: body.samples_per_cell }, () => 100),
      excluded_sample_indexes: [],
    })),
  };
}
function coverageFact(body = payload()) {
  return copy(proposeBenchmarkCoverageFact({
    payload: body,
    measurements: measurements(body),
    evaluator_identity: I("bench-evaluator"),
    evidence_ref: "safe:j1-seam:coverage-evidence",
    observed_at: COVERAGE_AT,
    ttl_expires_at: iso(Date.parse(COVERAGE_AT) + 7 * DAY),
  }));
}

function minimumProjection({ acceptedAt = ACCEPTED_AT } = {}) {
  return {
    schema_version: FOUNDATION_ASSURANCE_MINIMUM_PROJECTION,
    tenant: ORGANIZATION_TENANT_ID,
    as_of: AS_OF,
    binding: {
      subject_digest: D(1), candidate_digest: D(2), policy_digest: D(3),
      minimum_environment_manifest_digest: D(4),
      benchmark_manifest_digest: benchmarkPayloadDigest(payload()),
      maximum_member_receipt_ttl_ms: 30 * DAY,
      minimum_receipt_ttl_ms: MINIMUM_TTL,
    },
    gate_zero: { step_ref: GATE_ZERO_STEP_REF, outcome_digest: D(6), observed_at: GATE_ZERO_AT },
    benchmark_coverage: coverageFact(),
    members: MINIMUM_REQUIRED_MEMBERS.map(spec => spec.output_schema_ref === BENCHMARK_MANIFEST_SCHEMA
      ? { step_ref: spec.step_ref, receipt: manifest(payload(), acceptedAt) }
      : {
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
          evidence_ref: `safe:j1-seam:${spec.gate_id}`,
          fixture_set_digest: D(5),
          observed_at: OBSERVED_AT,
          ttl_expires_at: iso(Date.parse(OBSERVED_AT) + 7 * DAY),
          status: "pass",
          comparator: "exact closed-contract comparator",
          negative_admission_result: "all_required_denials_observed",
        },
      }),
    minimum_receipt_context: {
      subject_maker_identity: I("builder"),
      producer_identity: I("minimum-oracle"),
      evaluator_identity: I("minimum-evaluator"),
      evidence_ref: "safe:j1-seam:minimum-join",
      fixture_set_digest: D(5),
      comparator: "exact closed minimum join comparator",
    },
  };
}
/** Test-only authenticating capability, installed by trusted test code. */
function joinOnce(projection) {
  const evidence = new Map();
  const gate = createFoundationAssuranceMinimumGate({
    authenticateEvidence(envelope) {
      const found = evidence.get(digest(envelope));
      if (!found) throw new Error("synthetic-authentication-refused");
      return { envelope_digest: digest(envelope), snapshot: copy(found) };
    },
  });
  const envelope = { synthetic_evidence_ref: "j1-seam-join" };
  evidence.set(digest(envelope), copy(projection));
  return gate.evaluate(envelope);
}

// --- the M01 side -----------------------------------------------------------

const KERNEL_ARTIFACT = D(20);
const KERNEL_FIXTURES = D(21);

function terminusReceipt(observedAt) {
  return {
    receipt_ref: "safe:receipt:journey-one-kernel-production",
    producer_step_ref: "step:j1-kernel-production-outcome",
    subject_digest: D(1), candidate_digest: D(2), policy_digest: D(3),
    rollout_environment_manifest_digest: D(7),
    artifact_digest: KERNEL_ARTIFACT,
    evidence_scope: "production",
    subject_maker_identity: I("builder"),
    producer_identity: I("kernel-outcome-oracle"),
    evaluator_identity: I("kernel-outcome-evaluator"),
    producer_role: "independent_journey_one_kernel_outcome_oracle",
    independent_oracle_ref: "oracle:rollout-component:journey-one-kernel-production",
    oracle_version: "1.0.0",
    evidence_ref: "safe:j1-seam:kernel-production-evidence",
    fixture_set_digest: KERNEL_FIXTURES,
    observed_at: observedAt,
    ttl_expires_at: iso(Date.parse(observedAt) + 2 * DAY),
    status: "pass",
    comparator: "exact kernel production comparator",
    negative_admission_result: "all_required_denials_observed",
    subject_environment: "production",
  };
}
/**
 * The M01 projection the issuance adapter would build around the SAME receipt
 * A00 proposed. The benchmark digest and its acceptance instant are carried
 * across from the join's own result rather than re-asserted, which is the only
 * reason this projection is about the same benchmark at all.
 *
 * `accepted_by_identity` is NOT carried across: A00's result publishes no
 * acceptor, so the value below is asserted by this projection the way a live
 * verifier would have to assert it — derived from the authenticated acceptance
 * record and the LIVE actor, never read back out of a stored class string. It is
 * written here as a fixture, and the two modules agreeing about it proves
 * nothing about the acceptor.
 */
function clockProjection(join, receipt, { asOf = CLOCK_AS_OF, admittedAt = AS_OF } = {}) {
  return {
    schema_version: JOURNEY_ONE_CLOCK_PROJECTION,
    tenant: ORGANIZATION_TENANT_ID,
    as_of: asOf,
    binding: {
      subject_digest: D(1), candidate_digest: D(2), policy_digest: D(3),
      minimum_environment_manifest_digest: D(4),
      production_environment_manifest_digest: D(7),
      maximum_minimum_receipt_ttl_ms: 30 * DAY,
      maximum_completion_receipt_ttl_ms: 30 * DAY,
    },
    benchmark: {
      manifest_digest: join.benchmark_manifest_digest,
      subject_digest: D(1), candidate_digest: D(2), policy_digest: D(3),
      // DAYS on the manifest, HOURS here: the conversion is the adapter's, and
      // this is the one place in these two suites where it is performed.
      deadline_contract: copy(JOURNEY_ONE_DEADLINE_CONTRACT),
      accepted_at: join.benchmark_accepted_at,
      accepted_by_identity: I("joe"),
    },
    minimum_history: [{ admitted_at: admittedAt, receipt: copy(receipt) }],
    completion: null,
    completion_expectation: { artifact_digest: KERNEL_ARTIFACT, fixture_set_digest: KERNEL_FIXTURES },
    pauses: [], amendments: [], history: null,
  };
}
function evaluateOnce(projection) {
  const evidence = new Map();
  const clock = createJourneyOneClock({
    verifySnapshot(envelope) {
      const found = evidence.get(digest(envelope));
      if (!found) throw new Error("synthetic-authentication-refused");
      return { envelope_digest: digest(envelope), snapshot: copy(found) };
    },
  });
  const envelope = { synthetic_receipt_ref: "j1-seam-clock" };
  evidence.set(digest(envelope), copy(projection));
  return clock.evaluate(envelope);
}

function seam() {
  const join = joinOnce(minimumProjection());
  const view = journeyOneClockMinimumReceiptView(join.proposed_receipt);
  return { join, view, result: evaluateOnce(clockProjection(join, view)) };
}

// --- the record layer, on the same one artifact ------------------------------
//
// The two modules above are pure. This third seam is the durable one: the clock
// the join started, evaluated by a REAL kernel and filed by the REAL store
// through its trusted recorder, into a non-durable reference journal. Nothing
// here is authenticated and nothing is persisted beyond the process; what is
// exercised is that the scope a revision is filed under is the scope the kernel
// verified THIS artifact's projection against.

/** A kernel whose envelopes can be handed out one at a time, for the recorder. */
function clockHarness() {
  const evidence = new Map(); let serial = 0;
  const clock = createJourneyOneClock({
    verifySnapshot(envelope) {
      const found = evidence.get(digest(envelope));
      if (!found) throw new Error("synthetic-authentication-refused");
      return { envelope_digest: digest(envelope), snapshot: copy(found) };
    },
  });
  return { clock, envelopeFor(projection) {
    const envelope = { synthetic_receipt_ref: `j1-seam-record-${++serial}` };
    evidence.set(digest(envelope), copy(projection));
    return envelope;
  } };
}

const STORE_ACTOR = { slug: "claude", human: false, sponsoring_human_slug: "joe" };
const VERIFIER_REF = "safe:verifier:j1-seam-trusted-projection-verifier";
/**
 * The accepted scope this seam's clock belongs to, composed exactly as a trusted
 * integration would: the projection's own three binding digests and the two gate
 * ids the deadline contract names. `scope_ref` is a label and is not hashed.
 */
const SEAM_SCOPE = Object.freeze({
  benchmark_candidate_digest: D(2),
  benchmark_policy_digest: D(3),
  benchmark_subject_digest: D(1),
  clock_origin_gate_id: JOURNEY_ONE_DEADLINE_CONTRACT.clock_origin_gate_id,
  clock_terminus_gate_id: JOURNEY_ONE_DEADLINE_CONTRACT.clock_terminus_gate_id,
  scope_ref: "safe:clock-scope:j1-seam",
  tenant: ORGANIZATION_TENANT_ID,
});
let seamKeySerial = 0;
const uuid = () =>
  `00000000-0000-4000-8000-${(++seamKeySerial).toString(16).padStart(12, "0")}`;

function recordingSeam(scope = SEAM_SCOPE) {
  const journal = createEphemeralJourneyOneClockJournal();
  const kernel = clockHarness();
  const store = createJourneyOneClockStore({ journal, actor: STORE_ACTOR, clock_scope: scope });
  return { journal, kernel, store,
    recorder: createJourneyOneClockRecorder({ clock: kernel.clock, store, verifier_ref: VERIFIER_REF }) };
}

test("the clock the join started is stored under the scope the kernel verified this artifact against", async () => {
  const { join, view } = seam();
  const projection = clockProjection(join, view);
  const expected = evaluateOnce(copy(projection));
  const { kernel, store, recorder } = recordingSeam();

  const recorded = await recorder.evaluateAndRecord({
    envelope: kernel.envelopeFor(projection), expected_prior_history_digest: null,
    idempotency_key: uuid(), clock_ref: "J1-SEAM" });

  // The clock is addressed by the origin A00 published, and filed under the
  // scope derived from the binding the kernel enforced over that same artifact.
  assert.equal(recorded.clock_key, journeyOneClockKeyForState(expected.state));
  assert.equal(recorded.clock_scope_key, journeyOneClockScopeKey(SEAM_SCOPE));
  assert.equal(recorded.clock_scope_matches_verified_binding, true);
  assert.equal(expected.verified_binding.subject_digest, D(1));
  assert.equal(expected.verified_binding.candidate_digest, D(2));
  assert.equal(expected.verified_binding.policy_digest, D(3));
  assert.equal(expected.verified_binding.tenant, ORGANIZATION_TENANT_ID);

  // THE STORED HISTORY IS THE KERNEL'S, DIGEST FOR DIGEST. The binding rides
  // outside the hashed state, so storing it changed nothing about the history.
  assert.equal(recorded.history_digest, expected.state.history_digest);
  const readback = await store.read(recorded.clock_key);
  assert.deepEqual(readback.history, copy(expected.state));
  assert.equal(journeyOneClockHistoryDigest(readback.history), expected.state.history_digest);
  assert.equal(readback.history.origin_receipt_digest, join.proposed_receipt_reference_digest);
  assert.equal(readback.history.origin_at, AS_OF);
  assert.equal(readback.clock_scope_ref, SEAM_SCOPE.scope_ref);
  // And storing it accepts nothing.
  assert.equal(recorded.deadline_accepted_by_record_layer, false);
  assert.equal(recorded.kernel_verdict.deadline_success, false);
  assert.equal(recorded.effects.acceptances, 0);
  assert.equal(recorded.effects.clock_started, false);
});

test("the terminus receipt appends onto the stored clock, and the CAS is exact", async () => {
  const { join, view } = seam();
  const { kernel, store, recorder } = recordingSeam();
  const first = await recorder.evaluateAndRecord({
    envelope: kernel.envelopeFor(clockProjection(join, view)),
    expected_prior_history_digest: null, idempotency_key: uuid() });

  // AN EXACT REPLAY IS NOT A SECOND WRITE.
  const shared = uuid();
  const advanced = clockProjection(join, view);
  const observedAt = iso(Date.parse(AS_OF) + 20 * DAY);
  advanced.as_of = observedAt;
  advanced.history = copy((await store.read(first.clock_key)).history);
  advanced.completion = {
    gate_id: "journey-one-kernel-production-accepted",
    combiner: "all_current_exact_distinct_pass",
    obligation_decision_ids: ["Q002.D1", "Q014.D1", "Q123.D1"],
    receipts: [terminusReceipt(observedAt)],
  };
  const done = await recorder.evaluateAndRecord({ envelope: kernel.envelopeFor(advanced),
    expected_prior_history_digest: first.history_digest, idempotency_key: shared });
  assert.equal(done.revision_ordinal, 1);
  assert.equal(done.kernel_verdict.status, "completed_on_time");
  const replay = await recorder.evaluateAndRecord({ envelope: kernel.envelopeFor(advanced),
    expected_prior_history_digest: first.history_digest, idempotency_key: shared });
  assert.equal(replay.replayed, true);
  assert.equal(replay.history_digest, done.history_digest);

  // A STALE PRIOR IS REFUSED, and the head is untouched by the attempt.
  await assert.rejects(recorder.evaluateAndRecord({ envelope: kernel.envelopeFor(advanced),
    expected_prior_history_digest: first.history_digest, idempotency_key: uuid() }),
    error => error.code === "clock_stale_prior_history_digest");
  const readback = await store.read(first.clock_key);
  assert.equal(readback.revision_count, 2);
  assert.equal(readback.history_digest, done.history_digest);
  assert.equal(readback.history.completion_observed_at, observedAt);
});

test("a store for another accepted scope cannot file this artifact, and files nothing", async () => {
  const { join, view } = seam();
  const projection = clockProjection(join, view);
  const expectedKey = journeyOneClockKeyForState(evaluateOnce(copy(projection)).state);

  // Each of the three binding digests, moved one at a time on the STORE's side.
  for (const field of ["benchmark_subject_digest", "benchmark_candidate_digest",
    "benchmark_policy_digest"]) {
    const { kernel, store, journal } = recordingSeam({ ...SEAM_SCOPE, [field]: D(50) });
    const recorder = createJourneyOneClockRecorder({
      clock: kernel.clock, store, verifier_ref: VERIFIER_REF });
    await assert.rejects(recorder.evaluateAndRecord({
      envelope: kernel.envelopeFor(projection), expected_prior_history_digest: null,
      idempotency_key: uuid() }), error => {
      assert.equal(error.code, "clock_scope_not_the_verified_binding");
      assert.deepEqual(error.detail.differing_fields, [field]);
      return true;
    });
    assert.equal(await journal.readClock(expectedKey), null, "nothing was written for it");
    assert.equal((await store.read(expectedKey)).exists, false);
  }
});

test("relabelling this artifact's accepted scope opens no second clock for it", async () => {
  const { join, view } = seam();
  const { journal, kernel, store, recorder } = recordingSeam();
  const first = await recorder.evaluateAndRecord({
    envelope: kernel.envelopeFor(clockProjection(join, view)),
    expected_prior_history_digest: null, idempotency_key: uuid() });

  // The same accepted scope under a different human name is the SAME scope, so
  // a second admitted minimum presented under it meets the clock that scope
  // already holds instead of starting a fresh one.
  const renamed = createJourneyOneClockStore({ journal, actor: STORE_ACTOR,
    clock_scope: { ...SEAM_SCOPE, scope_ref: "safe:clock-scope:j1-seam-renamed" } });
  assert.equal(renamed.clock_scope.clock_scope_key, store.clock_scope.clock_scope_key);
  // A SECOND ADMITTED MINIMUM, joined an hour later: a genuinely different
  // origin, and therefore a clock key this journal has never seen, so the
  // compare-and-swap has nothing to refuse it with.
  const laterAt = iso(Date.parse(AS_OF) + HOUR);
  const laterProjection = minimumProjection();
  laterProjection.as_of = laterAt;
  const secondJoin = joinOnce(laterProjection);
  const secondView = journeyOneClockMinimumReceiptView(secondJoin.proposed_receipt);
  const secondProjection = clockProjection(secondJoin, secondView,
    { asOf: iso(Date.parse(laterAt) + HOUR), admittedAt: laterAt });
  const rebased = evaluateOnce(copy(secondProjection)).state;
  assert.equal(rebased.origin_at, laterAt);
  assert.notEqual(journeyOneClockKeyForState(rebased), first.clock_key);

  const other = clockHarness();
  const otherRecorder = createJourneyOneClockRecorder({
    clock: other.clock, store: renamed, verifier_ref: VERIFIER_REF });
  await assert.rejects(otherRecorder.evaluateAndRecord({
    envelope: other.envelopeFor(secondProjection),
    expected_prior_history_digest: null, idempotency_key: uuid() }),
    error => error.code === "clock_scope_already_bound");
  assert.equal(await journal.readClock(journeyOneClockKeyForState(rebased)), null);
  assert.equal((await store.read(first.clock_key)).revision_count, 1);
});

// --- one receipt, one shape -------------------------------------------------

test("the receipt A00 proposes is the receipt M01 reads, with nothing added between them", () => {
  const { join, view } = seam();
  assert.deepEqual(Object.keys(join.proposed_receipt).sort(), [...CONSUMER_GATE_RECEIPT_FIELDS].sort());
  assert.deepEqual({ ...view }, { ...join.proposed_receipt });
  assert.ok(!Object.hasOwn(view, "schema_version"));
  // The raw r7-exact receipt is consumable by the clock without the view at all;
  // the view exists to REFUSE what M01 cannot read, not to make it readable.
  const raw = evaluateOnce(clockProjection(join, copy(join.proposed_receipt)));
  const adapted = evaluateOnce(clockProjection(join, view));
  assert.equal(raw.state.origin_receipt_digest, adapted.state.origin_receipt_digest);
  assert.deepEqual(raw.state, adapted.state);
});

test("one receipt has one identity on both sides of the seam", () => {
  const { join, view, result } = seam();
  // The digest A00 publishes for the issuance adapter, the digest of the view,
  // and the origin digest the clock records are all the same string. A durable
  // store keyed on either can find the origin the clock names.
  assert.equal(digest(view), digest(join.proposed_receipt));
  assert.equal(join.proposed_receipt_reference_digest, digest(join.proposed_receipt));
  assert.equal(result.state.origin_receipt_digest, join.proposed_receipt_reference_digest);
  // And that identity is what a pause binds to: an approval bound to the digest
  // A00 published is accepted by the clock without translation.
  const projection = clockProjection(join, view);
  const startsAt = iso(Date.parse(CLOCK_AS_OF) + DAY);
  const body = {
    pause_id: "safe:j1-seam:pause-one",
    clock_origin_digest: join.proposed_receipt_reference_digest,
    blocker_ref: "safe:external-blocker:seam-provider",
    starts_at: startsAt,
    approved_at: iso(Date.parse(startsAt) - HOUR),
    approved_by_identity: I("dell"),
  };
  projection.pauses = [{ ...body, ends_at: null, approval_digest: digest(["doctorcre:j1-clock-pause:v1", body]) }];
  projection.as_of = iso(Date.parse(startsAt) + 12 * HOUR);
  const paused = evaluateOnce(projection);
  assert.equal(paused.state.paused_ms, 12 * HOUR);
  assert.equal(paused.state.events.at(-1).type, "pause_approved");
});

test("the clock the join proposes actually starts, on the join's own observation instant", () => {
  const { join, result } = seam();
  assert.equal(join.proposed_receipt.observed_at, AS_OF);
  assert.equal(result.state.origin_at, AS_OF);
  assert.equal(result.state.status, "running");
  assert.equal(result.state.base_deadline_at, chicagoThirtyDayDeadline(AS_OF).due_at);
  assert.equal(result.state.base_deadline_resolution, DEADLINE_PLAIN);
  assert.equal(result.state.origin_benchmark_manifest_digest, join.benchmark_manifest_digest);
  assert.equal(result.deadline_success, false);
  assert.equal(result.replan_required, false);
  // Neither module claims to have done anything durable.
  assert.equal(join.issued, false);
  assert.equal(join.clock_started, false);
  assert.equal(result.durable_history_write_required, true);
  assert.equal(result.authority_granted, false);
  assert.equal(result.effects.database_writes, 0);
});

test("an r7-exact terminus receipt stops the clock the join started", () => {
  const { join, view } = seam();
  const projection = clockProjection(join, view);
  const observedAt = iso(Date.parse(AS_OF) + 20 * DAY);
  projection.as_of = observedAt;
  projection.completion = {
    gate_id: "journey-one-kernel-production-accepted",
    combiner: "all_current_exact_distinct_pass",
    obligation_decision_ids: ["Q002.D1", "Q014.D1", "Q123.D1"],
    receipts: [terminusReceipt(observedAt)],
  };
  const done = evaluateOnce(projection);
  assert.equal(Object.keys(projection.completion.receipts[0]).length, 22);
  assert.equal(done.state.status, "completed_on_time");
  assert.equal(done.deadline_success, true);
  assert.equal(done.replan_required, false);
  assert.equal(done.state.completion_observed_at, observedAt);
  // The same terminus with a schema_version added is refused, on the same
  // closed-shape rule that refuses any other extra field.
  const decorated = copy(projection);
  decorated.completion.receipts[0].schema_version = "rollout-component-receipt.v1";
  assert.throws(() => evaluateOnce(decorated), e => e.code === "closed_shape");
  // And a terminus naming a different kernel artifact does not stop this clock.
  const other = copy(projection);
  other.completion.receipts[0].artifact_digest = D(99);
  assert.throws(() => evaluateOnce(other), e => e.code === "completion_artifact_mismatch");
});

// --- the acceptance boundary is one convention across the seam --------------

test("an acceptance at the join instant is refused by A00 because M01 could never use it", () => {
  // A00 refuses to propose it...
  assert.throws(() => joinOnce(minimumProjection({ acceptedAt: AS_OF })),
    e => e.code === "benchmark_accepted_at_or_after_reference");
  // ...and the clock refuses the same instant against the origin it would have
  // become, so the two modules draw the boundary in the same place. The join
  // below is built one millisecond earlier only so a receipt exists to test.
  const join = joinOnce(minimumProjection({ acceptedAt: iso(Date.parse(AS_OF) - 1) }));
  const view = journeyOneClockMinimumReceiptView(join.proposed_receipt);
  assert.equal(evaluateOnce(clockProjection(join, view)).state.origin_at, AS_OF);
  const equal = clockProjection(join, view);
  equal.benchmark.accepted_at = join.proposed_receipt.observed_at;
  assert.throws(() => evaluateOnce(equal), e => e.code === "benchmark_not_accepted_before_origin");
});

// --- the unevaluated manifest, and what the seam still does not carry -------

test("an unmeasured benchmark cannot reach the clock, because the join refuses it first", () => {
  // This was the surviving r7 denial category ("unevaluated"): an accepted
  // manifest with no measurement evidence used to join, propose a receipt and
  // start a clock. The join now requires the coverage fact, so the artifact M01
  // consumes cannot exist without one.
  const absent = minimumProjection();
  delete absent.benchmark_coverage;
  assert.throws(() => joinOnce(absent),
    e => e.code === "closed_shape" && e.detail.missing.includes("benchmark_coverage"));
  const stale = minimumProjection();
  stale.benchmark_coverage.ttl_expires_at = "2026-02-03T00:00:00.000Z";
  assert.throws(() => joinOnce(stale), e => e.code === "benchmark_coverage_not_current");
  const claimed = minimumProjection();
  claimed.benchmark_coverage.measured_cell_count += 1;
  claimed.benchmark_coverage.required_cell_count += 1;
  assert.throws(() => joinOnce(claimed), e => e.code === "benchmark_coverage_cell_count_mismatch");
});

test("the coverage binding does not survive the seam, and neither module pretends it does", () => {
  const { join } = seam();
  // A00 publishes the two digests a durable store must persist beside the
  // receipt, and says in the same result that it evaluated nothing itself.
  assert.equal(join.benchmark_coverage_evaluated_here, false);
  assert.equal(join.durable_coverage_verification_required, true);
  assert.equal(join.benchmark_coverage_required_cell_count, benchmarkRequiredCells(payload()).length);
  assert.equal(join.benchmark_coverage_evaluation_digest, coverageFact().evaluation_digest);
  // r7's twenty-one receipt fields have no slot for any of it, so the receipt
  // that crosses the seam carries the benchmark's identity and not its coverage.
  const view = journeyOneClockMinimumReceiptView(join.proposed_receipt);
  for (const field of Object.keys(view)) assert.ok(!field.startsWith("benchmark_coverage"));
  // M01's benchmark projection has no coverage slot either. That is the
  // remaining integration fact, stated rather than papered over: the clock
  // relies on the join having refused an unevaluated manifest upstream, and the
  // store must keep the coverage digests beside the origin receipt for anyone
  // who later has to prove the origin was measured.
  const projection = clockProjection(join, view);
  assert.deepEqual(Object.keys(projection.benchmark).sort(), [
    "accepted_at", "accepted_by_identity", "candidate_digest", "deadline_contract",
    "manifest_digest", "policy_digest", "subject_digest",
  ]);
  assert.equal(evaluateOnce(projection).state.origin_receipt_digest, join.proposed_receipt_reference_digest);
});

// --- the third module: the admitted-minimum inventory, between the two -------
//
// Everything above joins A00 to the kernel and the kernel to the clock store,
// over a projection this file HAND-BUILDS. In production nobody hands the kernel
// a minimum_history: it is read out of the admitted-minimum inventory, and the
// projection is assembled by that rail's own composer. That third module sat
// between these two with no test carrying one artifact across all three, which
// is exactly the drift class this file exists to catch — the two-module seam
// would stay green while the artifact stopped surviving the trip through
// storage.
//
// So everything below carries ONE artifact the whole way: A00 proposes it, the
// input store admits it, the composer assembles the projection FROM THE STORED
// ROWS, the real kernel evaluates that, and the real clock store files the
// result — on ONE scope key, derived once. Nothing is authenticated: both gates
// run through their installed test-only verifiers, the admission instant is the
// reference journal's own, no receipt is issued, no acceptance is minted and no
// clock is started.

/**
 * The accepted minimum-input policy the inventory is opened under. The window is
 * the one A00's binding declares for the receipt it proposes, so the policy this
 * rail judges that receipt against is the projection's own — not a second number
 * chosen here.
 */
const MINIMUM_POLICY = Object.freeze({
  maximum_minimum_receipt_ttl_ms: MINIMUM_TTL,
  minimum_environment_manifest_digest: D(4),
});
const COMPLETION_TTL = 30 * DAY;
const PAUSE_CAP_MS = JOURNEY_ONE_DEADLINE_CONTRACT.maximum_external_blocker_pause_hours * HOUR;

/**
 * The five accepted source bindings the composer is constructed with. THE THREE
 * ENVELOPE FIELDS ARE DERIVED from the same accepted manifest A00's join read,
 * rather than written out here as literals — which is the point: a literal
 * digest, instant or acceptor would be three strings nothing compares against
 * the artifact they claim to describe. The other two are on no benchmark
 * manifest at all and stay trusted policy inputs.
 */
function seamSources(acceptedManifest = manifest()) {
  return {
    ...journeyOneMinimumBenchmarkAcceptedSources(acceptedManifest),
    maximum_completion_receipt_ttl_ms: COMPLETION_TTL,
    production_environment_manifest_digest: D(7),
  };
}
function seamPause(originDigest, startsAt, endsAt, id = "one") {
  const body = {
    pause_id: `safe:j1-seam:pause-${id}`,
    clock_origin_digest: originDigest,
    blocker_ref: "safe:external-blocker:seam-provider",
    starts_at: startsAt,
    approved_at: iso(Date.parse(startsAt) - HOUR),
    approved_by_identity: I("dell"),
  };
  return { ...body, ends_at: endsAt,
    approval_digest: digest(["doctorcre:j1-clock-pause:v1", body]) };
}

/**
 * A00 proposes, the inventory admits, and the composer is built over the stored
 * rows. `admittedAt` is the RECORD LAYER's instant and is deliberately later
 * than the receipt's own observation, which is the whole reason that rail exists.
 */
async function admittedSeam({ projection = minimumProjection(),
  scope = SEAM_SCOPE, admittedAt = CLOCK_AS_OF, acceptedManifest = manifest() } = {}) {
  const join = joinOnce(projection);
  // The record layer's own instant, held in one settable place so a test can
  // move it FORWARD and admit a later receipt into this same inventory. It is
  // still the journal's clock and never a caller field: the store reads it, and
  // nothing in an admission argument can name it.
  const admissionClock = { at: admittedAt };
  const journal = createEphemeralJourneyOneMinimumAdmissionJournal(
    { now: () => Date.parse(admissionClock.at) });
  const inputs = createJourneyOneClockMinimumInputStore({ journal, actor: STORE_ACTOR,
    clock_scope: scope, accepted_minimum_policy: MINIMUM_POLICY });
  const admitted = await inputs.admit({
    receipt: copy(join.proposed_receipt),
    expected_prior_admission_digest: null,
    idempotency_key: uuid(),
    // A00's OWN published reference digest, presented as the claim. The store
    // only ever compares a claimed digest against the one the artifact produces,
    // so this passing is a cross-module fact and not a courtesy.
    claimed_receipt_digest: join.proposed_receipt_reference_digest,
    source_ref: "safe:a00:proposed-minimum-receipt",
  });
  const composer = createJourneyOneClockProjectionComposer({ store: inputs,
    accepted_sources: seamSources(acceptedManifest), benchmark_manifest: acceptedManifest });
  const compose = async args => (await composer.compose({
    completion: null,
    completion_expectation: { artifact_digest: KERNEL_ARTIFACT, fixture_set_digest: KERNEL_FIXTURES },
    pauses: [], amendments: [], history: null, ...args }));
  return { join, inputs, admitted, composer, compose, admissionClock };
}

test("the artifact A00 proposes keeps one identity through the inventory the kernel reads", async () => {
  const { join, inputs, admitted, compose } = await admittedSeam();

  // ONE DIGEST, THREE MODULES. A00's published reference digest, the digest the
  // input store filed the row under, and the origin the kernel seals are one
  // string — now with a durable ledger in the middle of that chain.
  const view = journeyOneClockMinimumReceiptView(join.proposed_receipt);
  assert.equal(admitted.receipt_digest, join.proposed_receipt_reference_digest);
  assert.equal(admitted.receipt_digest, digest(view));

  // THE STORED ROW IS THE ARTIFACT, not a copy of it that drifted in storage.
  const inventory = await inputs.read();
  assert.equal(inventory.admission_count, 1);
  assert.deepEqual(inventory.minimum_history,
    [{ admitted_at: CLOCK_AS_OF, receipt: copy(join.proposed_receipt) }]);
  // The ADMISSION instant is the record layer's and the ORIGIN instant is the
  // join's; the two are an hour apart on purpose and neither stands in for the
  // other.
  assert.equal(admitted.admitted_at, CLOCK_AS_OF);
  assert.equal(join.proposed_receipt.observed_at, AS_OF);

  const composed = await compose({ as_of: CLOCK_AS_OF });
  assert.equal(composed.head_admission_digest, admitted.admission_digest);
  assert.equal(composed.admission_count, 1);
  assert.deepEqual(composed.projection.minimum_history, copy(inventory.minimum_history));
  // Composing is not verifying, and the composition says so itself.
  assert.equal(composed.authenticated, false);
  assert.equal(composed.trusted_verifier_still_required, true);

  // AND THE COMPOSED PROJECTION REACHES THE SAME ORIGIN the two-module seam
  // already proves from a hand-built one. The third module changed nothing about
  // the artifact's identity, which is the fact this test exists for.
  const fromStorage = evaluateOnce(composed.projection);
  assert.equal(fromStorage.state.origin_receipt_digest, join.proposed_receipt_reference_digest);
  assert.equal(fromStorage.state.origin_at, AS_OF);
  assert.equal(fromStorage.state.status, "running");
  assert.equal(fromStorage.state.origin_receipt_digest,
    evaluateOnce(clockProjection(join, view)).state.origin_receipt_digest);
});

test("the benchmark envelope the composer uses is derived from the manifest A00 read", async () => {
  const { join, compose } = await admittedSeam();
  const composed = await compose({ as_of: CLOCK_AS_OF });

  // A00 publishes the accepted manifest's digest and acceptance instant out of
  // the projection it validated. M01's composer DERIVES the same two, plus the
  // acceptor, from that same accepted manifest through A00's own validator. They
  // agree because they came from one artifact, not because two files were
  // written to match.
  assert.equal(composed.benchmark_envelope.derived_from_validated_accepted_manifest, true);
  assert.equal(composed.projection.benchmark.manifest_digest, join.benchmark_manifest_digest);
  assert.equal(composed.projection.benchmark.accepted_at, join.benchmark_accepted_at);
  assert.deepEqual(composed.projection.benchmark.accepted_by_identity, I("joe"));

  // AND THAT IS SHAPE, NOT ACCEPTANCE, on both sides of the seam. A00 says it
  // accepted no benchmark here; M01 says no human acceptance was authenticated.
  // Neither module may be read as having watched a partner accept anything.
  assert.equal(join.benchmark_accepted_here, false);
  assert.equal(composed.benchmark_envelope.human_acceptance_authenticated, false);
  assert.ok(composed.benchmark_envelope.statement.includes(
    "not evidence that a verified partner accepted anything"));
  // The two fields no benchmark manifest carries stay trusted policy inputs, and
  // the kernel reads them from the binding exactly as before.
  assert.deepEqual(composed.benchmark_envelope.trusted_policy_fields,
    ["maximum_completion_receipt_ttl_ms", "production_environment_manifest_digest"]);
  assert.equal(composed.projection.binding.maximum_completion_receipt_ttl_ms, COMPLETION_TTL);
  assert.equal(composed.projection.binding.production_environment_manifest_digest, D(7));
  // The TTL policy and the environment the RECEIPT was judged under come from
  // the sealed inventory, never from the receipt and never from a caller.
  assert.equal(composed.projection.binding.maximum_minimum_receipt_ttl_ms, MINIMUM_TTL);
  assert.equal(composed.projection.binding.minimum_environment_manifest_digest, D(4));
});

test("one scope key, derived once, addresses the inventory and the clock the composition starts", async () => {
  const { join, inputs, compose } = await admittedSeam();
  const { kernel, store, recorder } = recordingSeam();
  const composed = await compose({ as_of: CLOCK_AS_OF });
  const expected = evaluateOnce(copy(composed.projection));

  const recorded = await recorder.evaluateAndRecord({
    envelope: kernel.envelopeFor(copy(composed.projection)),
    expected_prior_history_digest: null, idempotency_key: uuid(), clock_ref: "J1-SEAM-STORED" });

  // THE THREE RAILS AGREE ON THE ADDRESS, and it is derived in one place.
  assert.equal(journeyOneClockScopeKey(SEAM_SCOPE), inputs.clock_scope.clock_scope_key);
  assert.equal(recorded.clock_scope_key, journeyOneClockScopeKey(SEAM_SCOPE));
  assert.equal(composed.clock_scope_key, journeyOneClockScopeKey(SEAM_SCOPE));
  assert.equal(recorded.clock_scope_matches_verified_binding, true);
  assert.equal(recorded.clock_key, journeyOneClockKeyForState(expected.state));

  const readback = await store.read(recorded.clock_key);
  assert.deepEqual(readback.history, copy(expected.state));
  assert.equal(journeyOneClockHistoryDigest(readback.history), expected.state.history_digest);
  assert.equal(readback.history.origin_receipt_digest, join.proposed_receipt_reference_digest);
  assert.equal(readback.history.origin_at, AS_OF);
  assert.equal(readback.history.base_deadline_at, chicagoThirtyDayDeadline(AS_OF).due_at);
  assert.equal(readback.history.base_deadline_resolution, DEADLINE_PLAIN);
  // Storing it accepts nothing, and the inventory it was read from is untouched.
  assert.equal(recorded.deadline_accepted_by_record_layer, false);
  assert.equal(recorded.kernel_verdict.deadline_success, false);
  assert.equal(recorded.effects.clock_started, false);
  assert.equal((await inputs.read()).head_admission_digest, composed.head_admission_digest);
});

test("a pause, a miss and a late completion driven through the COMPOSED projection", async () => {
  const { join, inputs, compose } = await admittedSeam();
  const { kernel, store, recorder } = recordingSeam();
  const origin = join.proposed_receipt_reference_digest;
  const before = await inputs.read();
  const file = async (projection, prior) => recorder.evaluateAndRecord({
    envelope: kernel.envelopeFor(copy(projection)),
    expected_prior_history_digest: prior, idempotency_key: uuid() });
  const historyOf = async clockKey => copy((await store.read(clockKey)).history);

  // 1. RUNNING.
  const started = await file((await compose({ as_of: CLOCK_AS_OF })).projection, null);
  assert.equal(started.kernel_verdict.status, "running");
  const base = chicagoThirtyDayDeadline(AS_OF).due_at;

  // 2. A PAUSE, approved by a partner before it started, twelve hours long. The
  // budget it is credited against is the accepted contract's own, read from the
  // contract rather than restated here.
  const pauseStart = iso(Date.parse(AS_OF) + 2 * DAY);
  const pause = seamPause(origin, pauseStart, iso(Date.parse(pauseStart) + 12 * HOUR));
  const paused = await file((await compose({ as_of: iso(Date.parse(AS_OF) + 3 * DAY),
    pauses: [pause], history: await historyOf(started.clock_key) })).projection,
  started.history_digest);
  const pausedHistory = await historyOf(started.clock_key);
  assert.equal(paused.kernel_verdict.status, "running");
  assert.equal(pausedHistory.paused_ms, 12 * HOUR);
  assert.ok(pausedHistory.paused_ms <= PAUSE_CAP_MS);
  assert.equal(pausedHistory.due_at, iso(Date.parse(base) + 12 * HOUR));
  assert.equal(pausedHistory.events.at(-1).type, "pause_approved");

  // 3. A MISS. The deadline passes with no completion evidence in hand.
  const missedAt = iso(Date.parse(AS_OF) + 40 * DAY);
  const missed = await file((await compose({ as_of: missedAt, pauses: [pause],
    history: await historyOf(started.clock_key) })).projection, paused.history_digest);
  const missedHistory = await historyOf(started.clock_key);
  assert.equal(missed.kernel_verdict.status, "missed");
  assert.equal(missed.kernel_verdict.replan_required, true);
  assert.equal(missed.kernel_verdict.missing_evidence_miss_recorded, true);
  assert.equal(missedHistory.miss_at, pausedHistory.due_at);

  // 4. A LATE COMPLETION. The terminus is real and usable; the deadline is not
  // retroactively certified, and the miss is still in the record.
  const lateAt = iso(Date.parse(AS_OF) + 41 * DAY);
  const late = await file((await compose({ as_of: lateAt, pauses: [pause],
    history: await historyOf(started.clock_key),
    completion: { gate_id: "journey-one-kernel-production-accepted",
      combiner: "all_current_exact_distinct_pass",
      obligation_decision_ids: [...JOURNEY_ONE_DEADLINE_CONTRACT.kernel_obligation_decision_ids],
      receipts: [terminusReceipt(lateAt)] } })).projection, missed.history_digest);
  assert.equal(late.kernel_verdict.status, "completed_late");
  assert.equal(late.kernel_verdict.deadline_success, false);
  assert.equal(late.kernel_verdict.replan_required, true);
  assert.equal(late.kernel_verdict.completion_observed_within_deadline, false);

  // THE ORIGIN AND THE ELAPSED HISTORY SURVIVED ALL FOUR, and so did the miss.
  const final = await store.read(started.clock_key);
  assert.equal(final.revision_count, 4);
  assert.equal(final.history.origin_receipt_digest, origin);
  assert.equal(final.history.origin_at, AS_OF);
  assert.equal(final.history.base_deadline_at, base);
  assert.equal(final.history.miss_at, missedHistory.miss_at);
  assert.equal(final.history.paused_ms, 12 * HOUR);
  assert.equal(final.history.completion_observed_at, lateAt);
  // AND THE INPUT INVENTORY NEVER MOVED. Four evaluations, one admitted row.
  const after = await inputs.read();
  assert.deepEqual(after.minimum_history, before.minimum_history);
  assert.equal(after.head_admission_digest, before.head_admission_digest);
});

test("a completion observed exactly ON the composed deadline is on time, not late", async () => {
  // The boundary is INCLUSIVE, and the seam is where that has to hold: the
  // deadline the completion is compared against was computed from an origin that
  // travelled through storage. due_at is read off the kernel's own state rather
  // than recomputed here.
  const { compose } = await admittedSeam();
  const { kernel, store, recorder } = recordingSeam();
  const started = await recorder.evaluateAndRecord({
    envelope: kernel.envelopeFor((await compose({ as_of: CLOCK_AS_OF })).projection),
    expected_prior_history_digest: null, idempotency_key: uuid() });
  const running = copy((await store.read(started.clock_key)).history);
  const dueAt = running.due_at;
  assert.equal(dueAt, chicagoThirtyDayDeadline(AS_OF).due_at);

  const onTimeProjection = (await compose({ as_of: dueAt, history: running,
    completion: { gate_id: "journey-one-kernel-production-accepted",
      combiner: "all_current_exact_distinct_pass",
      obligation_decision_ids: [...JOURNEY_ONE_DEADLINE_CONTRACT.kernel_obligation_decision_ids],
      receipts: [terminusReceipt(dueAt)] } })).projection;
  const onTime = await recorder.evaluateAndRecord({
    envelope: kernel.envelopeFor(onTimeProjection),
    expected_prior_history_digest: started.history_digest, idempotency_key: uuid() });
  assert.equal(onTime.kernel_verdict.status, "completed_on_time");
  assert.equal(onTime.kernel_verdict.deadline_success, true);
  assert.equal(onTime.kernel_verdict.completion_observed_within_deadline, true);
  assert.equal(onTime.kernel_verdict.replan_required, false);
  assert.equal((await store.read(started.clock_key)).history.completion_observed_at, dueAt);
});

test("a completion within the deadline that arrives AFTER a recorded miss claims no success", async () => {
  // Q008.D1's third completed case, at the seam. The observation and the verdict
  // are two facts and neither overwrites the other: the completion was observed
  // at or before the deadline, so it is never called late; a miss is durably
  // recorded, so no deadline success may be claimed and replan still stands.
  // The kernel suite proves this; the seam is where it has to hold over a
  // projection composed from the stored inventory, revision after revision.
  const { join, compose } = await admittedSeam();
  const { kernel, store, recorder } = recordingSeam();
  const file = async (projection, prior) => recorder.evaluateAndRecord({
    envelope: kernel.envelopeFor(copy(projection)),
    expected_prior_history_digest: prior, idempotency_key: uuid() });
  const historyOf = async clockKey => copy((await store.read(clockKey)).history);

  const started = await file((await compose({ as_of: CLOCK_AS_OF })).projection, null);
  const dueAt = (await historyOf(started.clock_key)).due_at;
  assert.equal(dueAt, chicagoThirtyDayDeadline(AS_OF).due_at, "no pause, so the deadline is the base one");

  // The deadline passes with nothing in hand, and the miss is written down.
  const missed = await file((await compose({ as_of: iso(Date.parse(dueAt) + HOUR),
    history: await historyOf(started.clock_key) })).projection, started.history_digest);
  assert.equal(missed.kernel_verdict.status, "missed");
  const missAt = (await historyOf(started.clock_key)).miss_at;
  assert.equal(missAt, dueAt);

  // THEN a terminus receipt observed ON the deadline arrives, still inside its
  // own window. Its observation instant is not rewritten by the miss that was
  // recorded before it was admitted.
  const done = await file((await compose({ as_of: iso(Date.parse(dueAt) + 2 * HOUR),
    history: await historyOf(started.clock_key),
    completion: { gate_id: "journey-one-kernel-production-accepted",
      combiner: "all_current_exact_distinct_pass",
      obligation_decision_ids: [...JOURNEY_ONE_DEADLINE_CONTRACT.kernel_obligation_decision_ids],
      receipts: [terminusReceipt(dueAt)] } })).projection, missed.history_digest);

  assert.equal(done.kernel_verdict.status, "completed_after_recorded_miss");
  assert.equal(done.kernel_verdict.completion_observed_within_deadline, true,
    "the observation is within the deadline and is never reported as late");
  assert.equal(done.kernel_verdict.deadline_success, false, "and it still claims no success");
  assert.equal(done.kernel_verdict.replan_required, true);

  // THE MISS AND THE ORIGIN BOTH SURVIVED IT, in the stored history.
  const final = await historyOf(started.clock_key);
  assert.equal(final.miss_at, missAt, "the miss is preserved, not reinterpreted");
  assert.equal(final.completion_observed_at, dueAt);
  assert.equal(final.origin_receipt_digest, join.proposed_receipt_reference_digest);
  assert.equal(final.origin_at, AS_OF);
  assert.equal(final.events.filter(e => e.type === "deadline_missed").length, 1);
  assert.equal((await store.read(started.clock_key)).revision_count, 3);
});

test("a pause longer than the accepted budget is credited only up to the contract's cap", async () => {
  // ≤5 accepted pause days, preserved as the union of overlaps clipped to the
  // contract's own hour budget. The number is READ from the accepted deadline
  // contract; nothing here restates it.
  const { join, compose } = await admittedSeam();
  const origin = join.proposed_receipt_reference_digest;
  const pauseStart = iso(Date.parse(AS_OF) + 2 * DAY);
  const overlong = seamPause(origin, pauseStart, iso(Date.parse(pauseStart) + 10 * DAY), "overlong");
  const result = evaluateOnce((await compose({ as_of: iso(Date.parse(AS_OF) + 15 * DAY),
    pauses: [overlong] })).projection);
  assert.equal(10 * DAY > PAUSE_CAP_MS, true, "the fixture must actually exceed the budget");
  assert.equal(result.state.paused_ms, PAUSE_CAP_MS);
  assert.equal(result.state.due_at,
    iso(Date.parse(chicagoThirtyDayDeadline(AS_OF).due_at) + PAUSE_CAP_MS));
  assert.equal(result.state.status, "running");
});

test("the Chicago DST resolution the kernel recorded travels through storage as well", async () => {
  // An origin whose thirtieth Chicago date lands in the spring-forward GAP: the
  // wall time never happens, the kernel shifts it forward by the gap length, and
  // it RECORDS which rule produced the deadline. That resolution has to survive
  // the composer and the store, because a deadline nobody can explain is a
  // deadline nobody can defend.
  const gapOrigin = "2026-02-06T08:30:00.000Z";   // 02:30 Chicago, CST
  const gapProjection = minimumProjection();
  gapProjection.as_of = gapOrigin;
  const { join, compose } = await admittedSeam({ projection: gapProjection,
    admittedAt: iso(Date.parse(gapOrigin) + HOUR) });
  assert.equal(join.proposed_receipt.observed_at, gapOrigin);

  const deadline = chicagoThirtyDayDeadline(gapOrigin);
  assert.equal(deadline.reason_id, DEADLINE_GAP_SHIFTED,
    "this fixture must actually land in the gap, or it is testing the plain case twice");

  const { kernel, store, recorder } = recordingSeam();
  const recorded = await recorder.evaluateAndRecord({
    envelope: kernel.envelopeFor(
      (await compose({ as_of: iso(Date.parse(gapOrigin) + HOUR) })).projection),
    expected_prior_history_digest: null, idempotency_key: uuid() });
  assert.equal(recorded.kernel_verdict.deadline_resolution, DEADLINE_GAP_SHIFTED);
  const history = (await store.read(recorded.clock_key)).history;
  assert.equal(history.origin_at, gapOrigin);
  assert.equal(history.base_deadline_at, deadline.due_at);
  assert.equal(history.base_deadline_resolution, DEADLINE_GAP_SHIFTED);
  // AND THE SHIFT IS THE ONE THE RULE DECIDES, read in Chicago wall time: the
  // origin's 02:30 does not exist on that date, so the deadline is 03:30 — the
  // wall time moved forward by the gap length — and not the 03:00 transition
  // instant it would snap to under the other plausible rule.
  const chicago = new Intl.DateTimeFormat("en-US", { timeZone: "America/Chicago",
    hourCycle: "h23", hour: "2-digit", minute: "2-digit" });
  const wallMinutes = instant => {
    const parts = Object.fromEntries(
      chicago.formatToParts(Date.parse(instant)).map(part => [part.type, part.value]));
    return Number(parts.hour) * 60 + Number(parts.minute);
  };
  assert.equal(wallMinutes(gapOrigin), 2 * 60 + 30);
  assert.equal(wallMinutes(history.base_deadline_at), 3 * 60 + 30);
});

test("a projection composed for one accepted scope cannot be filed by a store bound to another", async () => {
  // The two-module seam already proves this for a hand-built projection. Through
  // the composer it is the stronger fact: the projection was assembled from the
  // inventory of scope A, and the store bound to scope B refuses it before it
  // touches its journal — so a real composition cannot be filed under the wrong
  // program's clock by handing it to the wrong store.
  const { compose } = await admittedSeam();
  const composed = await compose({ as_of: CLOCK_AS_OF });
  const expectedKey = journeyOneClockKeyForState(evaluateOnce(copy(composed.projection)).state);

  for (const field of ["benchmark_subject_digest", "benchmark_candidate_digest",
    "benchmark_policy_digest"]) {
    const { kernel, store, journal } = recordingSeam({ ...SEAM_SCOPE, [field]: D(50) });
    const recorder = createJourneyOneClockRecorder({
      clock: kernel.clock, store, verifier_ref: VERIFIER_REF });
    assert.equal((await store.read(expectedKey)).exists, false);
    await assert.rejects(recorder.evaluateAndRecord({
      envelope: kernel.envelopeFor(copy(composed.projection)),
      expected_prior_history_digest: null, idempotency_key: uuid() }), error => {
      assert.equal(error.code, "clock_scope_not_the_verified_binding");
      assert.deepEqual(error.detail.differing_fields, [field]);
      return true;
    });
    assert.equal(await journal.readClock(expectedKey), null, "nothing was written for it");
  }

  // And an input store for another accepted scope refuses the artifact outright,
  // so the wrong-scope composition cannot be assembled in the first place.
  await assert.rejects(admittedSeam({ scope: { ...SEAM_SCOPE,
    benchmark_subject_digest: D(50), scope_ref: "safe:clock-scope:j1-seam-elsewhere" } }),
  error => {
    assert.equal(error.code, "minimum_receipt_scope_mismatch");
    assert.equal(error.detail.invariant, "j1_minimum_receipt_binds_accepted_scope");
    return true;
  });
});

test("the EARLIEST-ADMITTED ELIGIBLE row is the origin, over a skipped row and a later one", async () => {
  // EVERY OTHER COMPOSITION ABOVE CARRIES ONE ADMITTED ROW, where the first
  // ledger row, the first eligible row and the earliest-admitted eligible row
  // are all the same row and the kernel's selection rule decides nothing. In
  // production they are three different rows, so this fixture holds THREE
  // admissions, all of them receipts A00 proposed:
  //
  //   row 0  observed 02-03T00:00, admitted 02-10T01:00  -> LAPSED, skipped
  //   row 1  observed 02-04T00:00, admitted 02-10T02:00  -> eligible, EARLIEST admitted
  //   row 2  observed 02-03T12:00, admitted 02-10T03:00  -> eligible, later admitted
  //
  // TWO INDEPENDENT FACTS ARE LOAD-BEARING IN THAT LAYOUT, and each one is
  // separately provable only because the other rows exist.
  //   * Row 0 is skipped rather than refused: an attempt whose own window had
  //     already closed by the instant the record layer admitted it is ordinary
  //     history the kernel passes over, not a defect this rail rejects and not a
  //     row it may discard.
  //   * Row 1 beats row 2 ON ADMISSION ORDER AND ON NOTHING ELSE. Both are
  //     eligible — proved below by starting a clock from each ALONE — and row 2
  //     was observed EARLIER. So the origin being row 1 is the earliest-ADMITTED
  //     rule deciding against the earliest-OBSERVED one, which is exactly the
  //     guarantee that makes a running clock structurally un-rebasable. Were the
  //     kernel's comparator reversed, row 2 would win and the origin digest and
  //     origin instant asserted below would both change.
  const lapsedExpiresAt = iso(Date.parse(OBSERVED_AT) + MINIMUM_TTL);
  // Half a day after row 0's observation and still well before row 1's, so the
  // fixture's admission order and observation order genuinely disagree.
  const EARLIER_OBSERVED_AT = iso(Date.parse(OBSERVED_AT) + 12 * HOUR);
  assert.ok(Date.parse(OBSERVED_AT) < Date.parse(EARLIER_OBSERVED_AT) &&
    Date.parse(EARLIER_OBSERVED_AT) < Date.parse(AS_OF));

  // ROW 0: admitted an hour AFTER its own window closed. Admission is not
  // backdated and the receipt is not edited; the delay is the record layer's.
  const lapsedProjection = minimumProjection();
  lapsedProjection.as_of = OBSERVED_AT;
  const seam = await admittedSeam({ projection: lapsedProjection,
    admittedAt: iso(Date.parse(lapsedExpiresAt) + HOUR) });
  assert.equal(seam.join.proposed_receipt.observed_at, OBSERVED_AT);
  assert.equal(seam.join.proposed_receipt.ttl_expires_at, lapsedExpiresAt);
  assert.ok(Date.parse(seam.admitted.admitted_at) > Date.parse(lapsedExpiresAt),
    "row 0 must actually be lapsed at its own admission instant, or it proves nothing");

  const admitNext = async (join, at, source_ref) => {
    seam.admissionClock.at = at;
    const admitted = await seam.inputs.admit({
      receipt: copy(join.proposed_receipt),
      expected_prior_admission_digest: (await seam.inputs.read()).head_admission_digest,
      idempotency_key: uuid(),
      claimed_receipt_digest: join.proposed_receipt_reference_digest,
      source_ref,
    });
    assert.ok(Date.parse(admitted.admitted_at) < Date.parse(join.proposed_receipt.ttl_expires_at),
      `${source_ref} must be current at its own admission instant`);
    return admitted;
  };

  // ROW 1: the receipt this whole file carries.
  const current = joinOnce(minimumProjection());
  const admittedCurrent = await admitNext(current,
    iso(Date.parse(lapsedExpiresAt) + 2 * HOUR), "safe:a00:current-proposed-minimum-receipt");

  // ROW 2: observed BEFORE row 1 and admitted AFTER it. This row is the whole
  // reason the comparator is tested rather than merely exercised.
  const earlierObservedProjection = minimumProjection();
  earlierObservedProjection.as_of = EARLIER_OBSERVED_AT;
  const earlierObserved = joinOnce(earlierObservedProjection);
  const admittedEarlierObserved = await admitNext(earlierObserved,
    iso(Date.parse(lapsedExpiresAt) + 3 * HOUR), "safe:a00:earlier-observed-minimum-receipt");

  // THE TWO ELIGIBLE ROWS DISAGREE ON THE TWO KEYS, which is what makes the
  // assertions below able to tell the keys apart at all.
  assert.equal(earlierObserved.proposed_receipt.observed_at, EARLIER_OBSERVED_AT);
  assert.ok(Date.parse(earlierObserved.proposed_receipt.observed_at) <
    Date.parse(current.proposed_receipt.observed_at), "row 2 is observed EARLIER than row 1");
  assert.ok(Date.parse(admittedCurrent.admitted_at) <
    Date.parse(admittedEarlierObserved.admitted_at), "and admitted LATER than row 1");
  assert.equal(new Set([seam.admitted.receipt_digest, admittedCurrent.receipt_digest,
    admittedEarlierObserved.receipt_digest]).size, 3, "three distinct artifacts");

  // THE COMPOSITION IS ANCHORED ON THE HEAD AND CARRIES ALL THREE ROWS.
  const inventory = await seam.inputs.read();
  assert.equal(inventory.admission_count, 3);
  const composed = await seam.compose({ as_of: iso(Date.parse(lapsedExpiresAt) + 4 * HOUR) });
  assert.equal(composed.head_admission_digest, admittedEarlierObserved.admission_digest);
  assert.equal(composed.admission_count, 3);
  assert.deepEqual(composed.projection.minimum_history, copy(inventory.minimum_history));

  // THE COMPOSER'S ORDERING KEY IS ADMISSION ORDER, AND IT IS NAMED. The stored
  // sequence is ascending admitted_at; the observation instants it carries are
  // NOT in ascending order, so this pins the key rather than passing under
  // either one.
  assert.deepEqual(composed.projection.minimum_history.map(row => row.admitted_at),
    [seam.admitted.admitted_at, admittedCurrent.admitted_at,
      admittedEarlierObserved.admitted_at]);
  const storedObservations = composed.projection.minimum_history.map(row => row.receipt.observed_at);
  assert.deepEqual(storedObservations, [OBSERVED_AT, AS_OF, EARLIER_OBSERVED_AT]);
  assert.notDeepEqual(storedObservations, [...storedObservations].sort(),
    "admission order and observation order must disagree here, or the assertion above pins neither");

  // BOTH ROW 1 AND ROW 2 ARE ELIGIBLE ON THEIR OWN. Each alone starts a clock
  // from its own observation instant, so neither is skipped and the choice
  // between them below is decided by admission order and by nothing else.
  const alone = index => {
    const one = copy(composed.projection);
    one.minimum_history = [copy(composed.projection.minimum_history[index])];
    return one;
  };
  const rowOneAlone = evaluateOnce(alone(1));
  assert.equal(rowOneAlone.state.status, "running");
  assert.equal(rowOneAlone.state.origin_receipt_digest, admittedCurrent.receipt_digest);
  assert.equal(rowOneAlone.state.origin_at, AS_OF);
  const rowTwoAlone = evaluateOnce(alone(2));
  assert.equal(rowTwoAlone.state.status, "running");
  assert.equal(rowTwoAlone.state.origin_receipt_digest, admittedEarlierObserved.receipt_digest);
  assert.equal(rowTwoAlone.state.origin_at, EARLIER_OBSERVED_AT);

  // AND OVER ALL THREE THE ORIGIN IS ROW 1 — the earliest-admitted eligible row,
  // not the first ledger row and not the earliest-observed one. Same digest the
  // two-module seam proves from a hand-built projection, same origin instant.
  const fromStorage = evaluateOnce(copy(composed.projection));
  assert.equal(fromStorage.state.origin_receipt_digest,
    current.proposed_receipt_reference_digest);
  assert.notEqual(fromStorage.state.origin_receipt_digest,
    seam.admitted.receipt_digest, "the skipped row must not be the origin");
  assert.notEqual(fromStorage.state.origin_receipt_digest,
    admittedEarlierObserved.receipt_digest,
    "nor may the eligible row that was observed earlier but admitted later");
  assert.equal(fromStorage.state.origin_at, AS_OF);
  assert.notEqual(fromStorage.state.origin_at, EARLIER_OBSERVED_AT);
  assert.equal(fromStorage.state.status, "running");
  assert.equal(fromStorage.state.base_deadline_at, chicagoThirtyDayDeadline(AS_OF).due_at);
  assert.equal(fromStorage.state.origin_receipt_digest,
    evaluateOnce(clockProjection(current,
      journeyOneClockMinimumReceiptView(current.proposed_receipt))).state.origin_receipt_digest);

  // THE SKIP IS REAL AND IT IS THE INADMISSIBLE KIND, proved rather than assumed:
  // row 0 alone leaves the clock with no origin AND NO REFUSAL OF THE INVENTORY —
  // `origin_unavailable`, counting one inadmissible admission. Had it been fatal
  // instead, the three-row evaluation above could never have returned at all.
  assert.throws(() => evaluateOnce(alone(0)), error => {
    assert.equal(error.name, "JourneyOneClockError");
    assert.equal(error.code, "origin_unavailable");
    assert.equal(error.detail.inadmissible_admissions, 1);
    return true;
  });

  // AND THE REAL CLOCK STORE FILES THE RESULT under the one scope key, so the
  // third rail agrees with the first two about which row started this clock.
  const { kernel, store, recorder } = recordingSeam();
  const recorded = await recorder.evaluateAndRecord({
    envelope: kernel.envelopeFor(copy(composed.projection)),
    expected_prior_history_digest: null, idempotency_key: uuid() });
  assert.equal(recorded.kernel_verdict.status, "running");
  assert.equal(recorded.clock_scope_key, journeyOneClockScopeKey(SEAM_SCOPE));
  const readback = await store.read(recorded.clock_key);
  assert.equal(readback.history.origin_receipt_digest,
    current.proposed_receipt_reference_digest);
  assert.equal(readback.history.origin_at, AS_OF);
  // Filing it starts nothing and accepts nothing, as everywhere else in this file.
  assert.equal(recorded.effects.clock_started, false);
  assert.equal(recorded.deadline_accepted_by_record_layer, false);
  assert.equal(recorded.kernel_verdict.deadline_success, false);
  // The inventory still holds all three rows: being skipped removed none of them.
  assert.equal((await seam.inputs.read()).admission_count, 3);
});

test("an acceptance envelope the manifest A00 read does not produce composes nothing", async () => {
  // The composer's construction is where a caller could still assert the
  // benchmark half of the projection, and it is refused there rather than
  // surfacing as a clock started on three strings. Both halves of this are
  // cross-module: the manifest is the one A00's join validated, and the scope is
  // the inventory the receipt was admitted into.
  const { inputs, join } = await admittedSeam();
  const accepted = manifest();
  assert.equal(seamSources(accepted).benchmark_manifest_digest, join.benchmark_manifest_digest,
    "the manifest under test must be the one the join published a digest for");

  // ONE ASSERTED FIELD AT A TIME. Each is a value the accepted manifest does not
  // produce, and the derivation catches it by name.
  for (const [field, value] of [
    ["benchmark_manifest_digest", D(8)],
    ["benchmark_accepted_at", iso(Date.parse(ACCEPTED_AT) - HOUR)],
    ["benchmark_accepted_by_identity", I("dell")],
  ]) {
    assert.throws(() => createJourneyOneClockProjectionComposer({
      store: inputs, benchmark_manifest: accepted,
      accepted_sources: { ...seamSources(accepted), [field]: value } }), error => {
      // The NAME as well as the code: a stray non-domain error that happened to
      // carry a matching code would otherwise pass this loop.
      assert.equal(error.name, "JourneyOneMinimumInputStoreError", field);
      assert.equal(error.code, "benchmark_accepted_sources_not_derived", field);
      assert.equal(error.detail.field, field);
      return true;
    }, field);
  }

  // AND A GENUINELY ACCEPTED MANIFEST FOR ANOTHER PROGRAM, whose envelope is
  // internally consistent and honestly derived — it is simply not about the
  // subject this inventory admitted a receipt for. The kernel could never catch
  // it: the manifest digest is in none of the comparisons it makes.
  const otherPayload = payload();
  otherPayload.subject_digest = D(11);
  const otherManifest = manifest(otherPayload);
  // THAT IT IS GENUINELY ACCEPTED IS JUDGED BY THE MODULE, not asserted from the
  // fixture's own hardcoded status: the derivation runs it through A00's
  // validateBenchmarkManifest and returns a real envelope, whose digest is the
  // one A00 recomputes from THIS payload and is not the one the join published.
  const otherSources = seamSources(otherManifest);
  assert.equal(otherSources.benchmark_manifest_digest, benchmarkPayloadDigest(otherPayload));
  assert.notEqual(otherSources.benchmark_manifest_digest, join.benchmark_manifest_digest);
  assert.throws(() => createJourneyOneClockProjectionComposer({
    store: inputs, benchmark_manifest: otherManifest,
    accepted_sources: otherSources }), error => {
    assert.equal(error.name, "JourneyOneMinimumInputStoreError");
    assert.equal(error.code, "benchmark_manifest_scope_mismatch");
    assert.equal(error.detail.field, "subject_digest");
    assert.equal(error.detail.expected, SEAM_SCOPE.benchmark_subject_digest);
    assert.equal(error.detail.supplied, D(11));
    return true;
  });
});

// --- the loop, in one seat --------------------------------------------------
//
// Everything above joins the three rails BY HAND, which is how they were joined
// everywhere: the composer, the kernel and the store met only inside a test, and
// the two facts that make an advance coherent — which history was evaluated and
// which head it appends onto — were two independent arguments on two rails.
// journey-one-clock-runtime.v5.js is the seat that joins them, and these tests
// are about what that seat adds rather than about any rail on its own.

function runtimeSeam({ composer, kernel, store, present = null } = {}) {
  return createJourneyOneClockRuntime({
    composer, clock: kernel.clock, clock_store: store,
    present_projection: present ?? (projection => kernel.envelopeFor(projection)),
    verifier_ref: VERIFIER_REF,
  });
}
/** Every declared advance field, stated once; a test overrides what it is about. */
const advanceArgs = (over = {}) => ({
  as_of: CLOCK_AS_OF, pauses: [], amendments: [], completion: null,
  completion_expectation: { artifact_digest: KERNEL_ARTIFACT, fixture_set_digest: KERNEL_FIXTURES },
  clock_ref: null, idempotency_key: uuid(), ...over,
});

test("the runtime advances one clock, deriving the composed history AND the append prior from one read", async () => {
  const { join, inputs, admitted, composer } = await admittedSeam();
  const { kernel, store } = recordingSeam();
  const runtime = runtimeSeam({ composer, kernel, store });
  assert.equal(runtime.clock_scope_key, journeyOneClockScopeKey(SEAM_SCOPE));

  // 1. THE CREATION. The scope holds no clock, so the composition is made
  //    against a null history and the append names an explicit null prior —
  //    both derived from the same read, neither supplied.
  const started = await runtime.advance(advanceArgs());
  assert.equal(started.created_clock, true);
  assert.equal(started.expected_prior_history_digest, null);
  assert.equal(started.composed_from.prior_history_digest, null);
  assert.equal(started.composed_from.head_admission_digest, admitted.admission_digest);
  assert.equal(started.composed_from.admission_count, 1);
  assert.equal(started.kernel_verdict.status, "running");
  assert.equal(started.kernel_verdict.deadline_success, false);
  // AND THE COMPUTATION IS BOUND TO THE INVENTORY THIS RECORD LAYER READ.
  assert.equal(started.composition_binding.bound, true);
  assert.equal(started.composition_binding.origin_receipt_digest,
    join.proposed_receipt_reference_digest);
  assert.equal(started.composition_binding.head_admission_digest, admitted.admission_digest);
  // AND IT IS THE COMPOSED PROJECTION, EXACTLY: the kernel's digest of the
  // snapshot its verifier resolved is the digest of what this rail composed.
  assert.equal(started.composition_binding.proof_fact, "projection_identity");
  assert.match(started.composition_binding.authenticated_projection_digest,
    /^sha256:[0-9a-f]{64}$/);
  assert.equal(started.composition_binding.authenticated_projection_digest,
    started.composition_binding.composed_projection_digest);
  // A bound loop is not an authenticated one, and the result says so itself.
  assert.equal(started.composition_binding.authenticated_here, false);
  assert.equal(started.authenticated_projection_verified_here, false);
  assert.equal(started.deadline_accepted_by_record_layer, false);
  assert.equal(started.durable_history_write_required, false);
  assert.equal(started.effects.clock_started, false);
  assert.ok(started.cannot_prove.some(l => l.includes("was authentic")));

  // 2. THE ADVANCE. A partner-approved pause, and the prior is the head this
  //    rail just read rather than anything the caller named.
  const origin = join.proposed_receipt_reference_digest;
  const pauseStart = iso(Date.parse(AS_OF) + 2 * DAY);
  const pause = seamPause(origin, pauseStart, iso(Date.parse(pauseStart) + 12 * HOUR));
  const paused = await runtime.advance(advanceArgs({
    as_of: iso(Date.parse(AS_OF) + 3 * DAY), pauses: [pause] }));
  assert.equal(paused.created_clock, false);
  assert.equal(paused.expected_prior_history_digest, started.history_digest);
  assert.equal(paused.composed_from.prior_history_digest, started.history_digest);
  assert.equal(paused.composed_from.prior_revision_ordinal, 0);
  assert.equal(paused.clock_key, started.clock_key);
  assert.equal(paused.kernel_verdict.status, "running");

  // THE STORED CLOCK IS THE ONE THE INVENTORY STARTED, and the accepted pause
  // budget is the contract's own.
  const head = await store.read(started.clock_key);
  assert.equal(head.revision_count, 2);
  assert.equal(head.history_digest, paused.history_digest);
  assert.equal(head.history.origin_receipt_digest, origin);
  assert.equal(head.history.origin_at, AS_OF);
  assert.equal(head.history.base_deadline_at, chicagoThirtyDayDeadline(AS_OF).due_at);
  assert.equal(head.history.paused_ms, 12 * HOUR);
  assert.ok(head.history.paused_ms <= PAUSE_CAP_MS);
  // AND THE INPUT INVENTORY NEVER MOVED. This seat reads it and never writes it.
  const after = await inputs.read();
  assert.equal(after.head_admission_digest, admitted.admission_digest);
  assert.equal(after.admission_count, 1);
});

test("a miss and a late completion through the runtime keep the miss and the replan", async () => {
  const { join, composer } = await admittedSeam();
  const { kernel, store } = recordingSeam();
  const runtime = runtimeSeam({ composer, kernel, store });
  const origin = join.proposed_receipt_reference_digest;

  const started = await runtime.advance(advanceArgs());
  const missedAt = iso(Date.parse(AS_OF) + 40 * DAY);
  const missed = await runtime.advance(advanceArgs({ as_of: missedAt }));
  assert.equal(missed.kernel_verdict.status, "missed");
  assert.equal(missed.kernel_verdict.replan_required, true);
  assert.equal(missed.kernel_verdict.missing_evidence_miss_recorded, true);
  const missAt = (await store.read(started.clock_key)).history.miss_at;
  assert.equal(missAt, chicagoThirtyDayDeadline(AS_OF).due_at);

  const lateAt = iso(Date.parse(AS_OF) + 41 * DAY);
  const late = await runtime.advance(advanceArgs({ as_of: lateAt,
    completion: { gate_id: "journey-one-kernel-production-accepted",
      combiner: "all_current_exact_distinct_pass",
      obligation_decision_ids: [...JOURNEY_ONE_DEADLINE_CONTRACT.kernel_obligation_decision_ids],
      receipts: [terminusReceipt(lateAt)] } }));
  assert.equal(late.kernel_verdict.status, "completed_late");
  assert.equal(late.kernel_verdict.deadline_success, false);
  assert.equal(late.kernel_verdict.replan_required, true);
  assert.equal(late.kernel_verdict.completion_observed_within_deadline, false);

  // THE ORIGIN, THE MISS AND THE ELAPSED HISTORY ALL SURVIVED THE LOOP.
  const final = await store.read(started.clock_key);
  assert.equal(final.revision_count, 3);
  assert.equal(final.history.origin_receipt_digest, origin);
  assert.equal(final.history.origin_at, AS_OF);
  assert.equal(final.history.miss_at, missAt);
  assert.equal(final.history.completion_observed_at, lateAt);
  assert.equal(final.deadline_accepted_by_record_layer, false);
});

test("a computation the presentation did not make from THIS inventory is never filed", async () => {
  // The case the whole binding exists for. Two inventories for ONE accepted
  // scope: the composition is read from the first, and the presentation hands
  // the verifier the SECOND one's projection. Every existing check passes —
  // the three binding digests are the same accepted scope's, so the kernel's
  // verified_binding derives the store's own scope key and the recorder is
  // satisfied — and the revision that would be filed carries an origin this
  // record layer never admitted for this scope.
  const { admitted, composer } = await admittedSeam();
  const elsewhere = minimumProjection();
  elsewhere.as_of = iso(Date.parse(AS_OF) + 30 * 60 * 1000);
  const other = await admittedSeam({ projection: elsewhere });
  assert.notEqual(other.admitted.receipt_digest, admitted.receipt_digest,
    "the second inventory must actually hold a different artifact");
  const otherComposed = await other.compose({ as_of: CLOCK_AS_OF });

  const { kernel, store, journal } = recordingSeam();
  const runtime = runtimeSeam({ composer, kernel, store,
    present: () => kernel.envelopeFor(copy(otherComposed.projection)) });

  await assert.rejects(runtime.advance(advanceArgs()), error => {
    assert.equal(error.name, "JourneyOneClockRuntimeError");
    assert.equal(error.code, "clock_computation_origin_not_in_composed_inventory");
    assert.equal(error.detail.fact, "origin_admission");
    assert.equal(error.detail.matching_admissions, 0);
    assert.equal(error.detail.origin_receipt_digest, other.admitted.receipt_digest);
    assert.deepEqual(error.detail.composed_receipt_digests, [admitted.receipt_digest]);
    assert.equal(error.detail.head_admission_digest, admitted.admission_digest);
    return true;
  });

  // NOTHING WAS WRITTEN, for either clock, and the scope still holds none.
  assert.equal((await store.readClockKeyForScope()).clock_key, null);
  const wouldHaveBeen = journeyOneClockKeyForState(
    evaluateOnce(copy(otherComposed.projection)).state);
  assert.equal(await journal.readClock(wouldHaveBeen), null);
});

// --- the adversarial half: two VALID projections for one accepted scope ------
//
// The seven named field checks read the accepted SCOPE and a few parts of the
// state, and every projection built for one program shares the scope. These
// tests present, through the REAL kernel and the REAL composer, a second valid
// snapshot that agrees on every one of those named facts and is a different
// clock. Each must be refused BEFORE store.record is called.

/** A store whose calls are recorded, so "before any write" is a fact, not a hope. */
function watchedStore(store) {
  const calls = [];
  const wrap = name => async (...args) => { calls.push(name); return store[name](...args); };
  return { calls, store: { clock_scope: store.clock_scope, writer: store.writer,
    readClockKeyForScope: wrap("readClockKeyForScope"),
    readRecordedRevisionForKey: wrap("readRecordedRevisionForKey"),
    read: wrap("read"), record: wrap("record") } };
}
function terminusCompletion(at) {
  return { gate_id: "journey-one-kernel-production-accepted",
    combiner: "all_current_exact_distinct_pass",
    obligation_decision_ids: [...JOURNEY_ONE_DEADLINE_CONTRACT.kernel_obligation_decision_ids],
    receipts: [terminusReceipt(at)] };
}
/** The seven named facts, computed off two real kernel results for comparison. */
function namedFactsAgree(a, b) {
  return a.verified_binding.tenant === b.verified_binding.tenant &&
    ["subject_digest", "candidate_digest", "policy_digest"].every(
      f => a.verified_binding[f] === b.verified_binding[f]) &&
    Date.parse(a.state.evaluated_at) === Date.parse(b.state.evaluated_at) &&
    a.state.current_benchmark_manifest_digest === b.state.current_benchmark_manifest_digest &&
    a.state.origin_receipt_ttl_policy_ms === b.state.origin_receipt_ttl_policy_ms &&
    a.state.origin_receipt_digest === b.state.origin_receipt_digest &&
    Date.parse(a.state.origin_at) === Date.parse(b.state.origin_at) &&
    JSON.stringify(a.state.pause_intervals) === JSON.stringify(b.state.pause_intervals);
}

test("a valid projection carrying a COMPLETION the record layer never composed is refused before any write", async () => {
  // The severe case. The composed projection has completion null; the trusted
  // presentation resolves a projection identical to it except that it carries a
  // real, current, r7-exact terminus receipt. Every named check agrees — same
  // tenant, scope digests, as_of, manifest, sealed TTL policy, origin admission
  // and empty pauses — and the second one COMPLETES the clock.
  const { compose, composer } = await admittedSeam();
  const composed = await compose({ as_of: CLOCK_AS_OF });
  const judged = await compose({ as_of: CLOCK_AS_OF, completion: terminusCompletion(CLOCK_AS_OF) });

  const running = evaluateOnce(copy(composed.projection));
  const completedRun = evaluateOnce(copy(judged.projection));
  assert.equal(running.state.status, "running");
  assert.equal(completedRun.state.status, "completed_on_time",
    "the substituted projection must really complete the clock, or this proves nothing");
  assert.equal(namedFactsAgree(running, completedRun), true,
    "every named field check must agree, or the proof is not what is doing the work");
  assert.notEqual(running.verified_binding.authenticated_projection_digest,
    completedRun.verified_binding.authenticated_projection_digest);

  const { kernel, store, journal } = recordingSeam();
  const watched = watchedStore(store);
  const runtime = runtimeSeam({ composer, kernel, store: watched.store,
    present: () => kernel.envelopeFor(copy(judged.projection)) });
  await assert.rejects(runtime.advance(advanceArgs()), error => {
    assert.equal(error.name, "JourneyOneClockRuntimeError");
    assert.equal(error.code, "clock_computation_projection_digest_mismatch");
    assert.equal(error.detail.fact, "projection_identity");
    assert.equal(error.detail.composed_projection_digest, digest(composed.projection));
    assert.equal(error.detail.authenticated_projection_digest, digest(judged.projection));
    return true;
  });
  // BEFORE store.record, and nothing exists for either clock. The request read
  // comes first — no revision for this key — and then the head.
  assert.deepEqual(watched.calls, ["readRecordedRevisionForKey", "readClockKeyForScope"]);
  assert.equal(await journal.readClock(journeyOneClockKeyForState(completedRun.state)), null);
  assert.equal((await store.readClockKeyForScope()).clock_key, null);
});

test("two pauses with one id and one end but different STARTS are not one projection", async () => {
  // The case no other rail can catch. The pause_intervals check compares
  // pause_id and ends_at, which are identical here; the credited hours and the
  // deadline are not. The head's event chain is a prefix of both, so the store's
  // append-only diff would have accepted either one.
  const { join, compose, composer } = await admittedSeam();
  const { kernel, store, journal } = recordingSeam();
  const origin = join.proposed_receipt_reference_digest;
  const runtime = runtimeSeam({ composer, kernel, store });
  const started = await runtime.advance(advanceArgs());
  const head = copy((await store.read(started.clock_key)).history);

  const at = iso(Date.parse(AS_OF) + 3 * DAY);
  const endsAt = iso(Date.parse(AS_OF) + 2 * DAY + 12 * HOUR);
  const early = seamPause(origin, iso(Date.parse(AS_OF) + 2 * DAY), endsAt);
  const late = seamPause(origin, iso(Date.parse(AS_OF) + 2 * DAY + 6 * HOUR), endsAt);
  assert.equal(early.pause_id, late.pause_id);
  assert.equal(early.ends_at, late.ends_at);
  assert.notEqual(early.starts_at, late.starts_at);

  const composedEarly = await compose({ as_of: at, pauses: [early], history: head });
  const judgedLate = await compose({ as_of: at, pauses: [late], history: head });
  const runEarly = evaluateOnce(copy(composedEarly.projection));
  const runLate = evaluateOnce(copy(judgedLate.projection));
  assert.deepEqual(runEarly.state.pause_intervals, runLate.state.pause_intervals);
  assert.equal(namedFactsAgree(runEarly, runLate), true);
  assert.equal(runEarly.state.paused_ms, 12 * HOUR);
  assert.equal(runLate.state.paused_ms, 6 * HOUR);
  assert.notEqual(runEarly.state.due_at, runLate.state.due_at);

  const watched = watchedStore(store);
  const substituted = runtimeSeam({ composer, kernel, store: watched.store,
    present: () => kernel.envelopeFor(copy(judgedLate.projection)) });
  await assert.rejects(substituted.advance(advanceArgs({ as_of: at, pauses: [early] })), error => {
    assert.equal(error.code, "clock_computation_projection_digest_mismatch");
    assert.equal(error.detail.authenticated_projection_digest, digest(judgedLate.projection));
    return true;
  });
  assert.ok(!watched.calls.includes("record"), "refused before the store was asked to write");
  const after = await store.read(started.clock_key);
  assert.equal(after.revision_count, 1);
  assert.equal(after.history.paused_ms, 0);
  assert.equal(await journal.readClock(started.clock_key) !== null, true);
});

test("a computation against a different HISTORY is refused here, not later in the append diff", async () => {
  const { join, compose, composer } = await admittedSeam();
  const { kernel, store } = recordingSeam();
  const runtime = runtimeSeam({ composer, kernel, store });
  const started = await runtime.advance(advanceArgs());
  const head = copy((await store.read(started.clock_key)).history);

  const at = iso(Date.parse(AS_OF) + 3 * DAY);
  const pause = seamPause(join.proposed_receipt_reference_digest,
    iso(Date.parse(AS_OF) + 2 * DAY), iso(Date.parse(AS_OF) + 2 * DAY + 6 * HOUR));
  const composedOnHead = await compose({ as_of: at, pauses: [pause], history: head });
  const judgedFromNothing = await compose({ as_of: at, pauses: [pause], history: null });
  const onHead = evaluateOnce(copy(composedOnHead.projection));
  const fromNothing = evaluateOnce(copy(judgedFromNothing.projection));
  assert.equal(namedFactsAgree(onHead, fromNothing), true,
    "the two differ only in the history they were computed against");
  assert.notEqual(onHead.state.history_digest, fromNothing.state.history_digest);

  const watched = watchedStore(store);
  const substituted = runtimeSeam({ composer, kernel, store: watched.store,
    present: () => kernel.envelopeFor(copy(judgedFromNothing.projection)) });
  await assert.rejects(substituted.advance(advanceArgs({ as_of: at, pauses: [pause] })),
    error => {
      assert.equal(error.code, "clock_computation_projection_digest_mismatch");
      return true;
    });
  assert.ok(!watched.calls.includes("record"));

  // WHAT WOULD HAVE HAPPENED WITHOUT THIS SEAT, shown rather than asserted: the
  // store's own append-only diff catches THIS one, later and under a different
  // name. It is exactly the two cases above that it cannot catch.
  await assert.rejects(store.record({ state: copy(fromNothing.state),
    expected_prior_history_digest: started.history_digest, idempotency_key: uuid(),
    claimed_history_digest: null, clock_ref: null, verifier_ref: VERIFIER_REF }));
  assert.equal((await store.read(started.clock_key)).revision_count, 1);
});

// --- the retry half: a lost response is not a second request -----------------
//
// Deriving the prior from the head is what makes an advance correct and what
// used to make it non-idempotent: after a write lands and its response is lost,
// the head has moved, so a re-sent request computed a different revision and met
// `clock_idempotency_key_reused`. These drive the real rails end to end.

/** A second writing seat over the SAME journal: another actor, same scope. */
const OTHER_ACTOR = { slug: "codex", human: false, sponsoring_human_slug: "joe" };

test("a lost response on a CREATION replays, and appends nothing", async () => {
  const { composer } = await admittedSeam();
  const { kernel, store, journal } = recordingSeam();
  const runtime = runtimeSeam({ composer, kernel, store });
  const request = advanceArgs();

  const first = await runtime.advance(request);
  assert.equal(first.created_clock, true);
  assert.equal(first.appended, true);
  assert.equal(first.replayed, false);
  assert.equal(first.replayed_recorded_request, false);
  assert.equal(first.composed_from.prior_source, "current_scope_head");
  assert.equal(first.effects.database_writes, 1);
  assert.equal(first.effects.history_appended, true);

  // The caller never saw that receipt and re-sends the identical request.
  const again = await runtime.advance({ ...request });
  assert.equal(again.replayed, true);
  assert.equal(again.appended, false);
  assert.equal(again.replayed_recorded_request, true);
  assert.equal(again.composed_from.prior_source, "recorded_request_prior");
  assert.equal(again.created_clock, true, "the revision it names was the creation");
  // The SAME revision, not a new one that merely looks alike.
  assert.equal(again.clock_key, first.clock_key);
  assert.equal(again.history_digest, first.history_digest);
  assert.equal(again.revision_ordinal, first.revision_ordinal);
  assert.equal(again.expected_prior_history_digest, null);
  assert.equal(again.recorded_at, first.recorded_at);
  assert.equal(again.kernel_verdict.status, first.kernel_verdict.status);
  // TRUTHFUL EFFECTS: this call wrote nothing and says so.
  assert.equal(again.effects.database_writes, 0);
  assert.equal(again.effects.history_appended, false);
  // AND ZERO NEW APPEND, read off the record layer rather than off the receipt.
  const readback = await store.read(first.clock_key);
  assert.equal(readback.revision_count, 1);
  assert.equal((await journal.readRevisions(first.clock_key)).length, 1);
});

test("a lost response on a LATER revision replays against the prior it was written under", async () => {
  // The case the head-derived prior could never serve: by the time the retry
  // arrives the head IS the revision this key wrote, so composing against the
  // head would compute a third revision and compare it to the second.
  const { join, composer } = await admittedSeam();
  const { kernel, store } = recordingSeam();
  const runtime = runtimeSeam({ composer, kernel, store });
  const started = await runtime.advance(advanceArgs());

  const at = iso(Date.parse(AS_OF) + 3 * DAY);
  const pause = seamPause(join.proposed_receipt_reference_digest,
    iso(Date.parse(AS_OF) + 2 * DAY), iso(Date.parse(AS_OF) + 2 * DAY + 12 * HOUR));
  const request = advanceArgs({ as_of: at, pauses: [pause] });
  const second = await runtime.advance(request);
  assert.equal(second.appended, true);
  assert.equal(second.revision_ordinal, 1);
  assert.equal(second.expected_prior_history_digest, started.history_digest);

  const again = await runtime.advance({ ...request });
  assert.equal(again.replayed, true);
  assert.equal(again.appended, false);
  assert.equal(again.created_clock, false);
  assert.equal(again.revision_ordinal, 1);
  assert.equal(again.history_digest, second.history_digest);
  // THE PRIOR IS THE ORIGINAL ONE, not the head the retry actually found.
  assert.equal(again.expected_prior_history_digest, started.history_digest);
  assert.equal(again.composed_from.prior_history_digest, started.history_digest);
  assert.notEqual(started.history_digest, second.history_digest);
  const readback = await store.read(started.clock_key);
  assert.equal(readback.revision_count, 2);
  assert.equal(readback.history_digest, second.history_digest);
  assert.equal(readback.history.paused_ms, 12 * HOUR);
});

test("one key with a changed intent is refused, and the stored revision is not handed back", async () => {
  const { join, composer } = await admittedSeam();
  const { kernel, store } = recordingSeam();
  const runtime = runtimeSeam({ composer, kernel, store });
  const started = await runtime.advance(advanceArgs());
  const at = iso(Date.parse(AS_OF) + 3 * DAY);
  const pause = seamPause(join.proposed_receipt_reference_digest,
    iso(Date.parse(AS_OF) + 2 * DAY), iso(Date.parse(AS_OF) + 2 * DAY + 12 * HOUR));
  const request = advanceArgs({ as_of: at, pauses: [pause] });
  const second = await runtime.advance(request);

  // Same key, a different instant: a second request wearing the first one's key.
  for (const changed of [
    { ...request, as_of: iso(Date.parse(AS_OF) + 4 * DAY) },
    { ...request, pauses: [seamPause(join.proposed_receipt_reference_digest,
      iso(Date.parse(AS_OF) + 2 * DAY), iso(Date.parse(AS_OF) + 2 * DAY + 6 * HOUR))] },
    { ...request, pauses: [] },
  ]) {
    await assert.rejects(runtime.advance(changed), error => {
      assert.equal(error.code, "clock_idempotency_key_reused");
      assert.equal(error.detail.invariant, "j1_clock_idempotency_key_binds_its_payload");
      assert.equal(error.detail.recorded.history_digest, second.history_digest);
      assert.notEqual(error.detail.recomputed.history_digest, second.history_digest);
      // Re-computed against the ORIGINAL prior, which is what makes the
      // comparison about intent rather than about where the head has got to.
      assert.equal(error.detail.recomputed.expected_prior_history_digest,
        started.history_digest);
      return true;
    });
  }
  assert.equal((await store.read(started.clock_key)).revision_count, 2);
});

test("a retry blocked by a LATER admission keeps the recorded revision and appends nothing", async () => {
  // Replay works by re-composing, so it needs the inventory to still admit that
  // composition. A receipt admitted after the request landed, at an instant
  // after the request's own as_of, is the case where it cannot — and the whole
  // point is that the caller must not read that as "my request failed".
  const seam = await admittedSeam();
  const { kernel, store } = recordingSeam();
  const runtime = runtimeSeam({ composer: seam.composer, kernel, store });
  const request = advanceArgs();
  const landed = await runtime.advance(request);
  assert.equal(landed.appended, true);

  // A second, genuinely later admission into the SAME inventory.
  const later = minimumProjection();
  later.as_of = iso(Date.parse(AS_OF) + 3 * HOUR);
  const second = joinOnce(later);
  seam.admissionClock.at = iso(Date.parse(CLOCK_AS_OF) + 6 * HOUR);
  const admitted = await seam.inputs.admit({
    receipt: copy(second.proposed_receipt),
    expected_prior_admission_digest: seam.admitted.admission_digest,
    idempotency_key: uuid(),
    claimed_receipt_digest: second.proposed_receipt_reference_digest,
    source_ref: "safe:a00:second-proposed-minimum-receipt",
  });
  assert.equal(admitted.admitted_at, seam.admissionClock.at);
  assert.equal((await seam.inputs.read()).admission_count, 2);
  assert.ok(Date.parse(admitted.admitted_at) > Date.parse(request.as_of),
    "the new admission must actually postdate the request, or this proves nothing");

  // THE RETRY CANNOT BE RE-COMPOSED — and says so without ever implying the
  // write did not land.
  await assert.rejects(runtime.advance({ ...request }), error => {
    assert.equal(error.name, "JourneyOneClockRuntimeError");
    assert.equal(error.code, "clock_runtime_recorded_request_not_recomputable");
    assert.equal(error.detail.request_landed, true);
    assert.equal(error.detail.appended, false);
    assert.equal(error.detail.recorded_clock_key, landed.clock_key);
    assert.equal(error.detail.recorded_history_digest, landed.history_digest);
    assert.equal(error.detail.recorded_revision_ordinal, landed.revision_ordinal);
    assert.equal(error.detail.cause_code, "projection_as_of_precedes_admission");
    assert.ok(error.message.includes("ALREADY LANDED"));
    return true;
  });

  // NOTHING WAS APPENDED, and the recorded revision is still readable — the
  // outcome is recoverable by reading, not by re-writing.
  const readback = await store.read(landed.clock_key);
  assert.equal(readback.revision_count, 1);
  assert.equal(readback.history_digest, landed.history_digest);
  const stillRecorded = await store.readRecordedRevisionForKey(request.idempotency_key);
  assert.equal(stillRecorded.exists, true);
  assert.equal(stillRecorded.history_digest, landed.history_digest);
  assert.equal(stillRecorded.revision_ordinal, landed.revision_ordinal);
  assert.equal(stillRecorded.prior_revision_ordinal, null, "it was the creation");
});

test("a recorded request is never replayed to another writing seat", async () => {
  const { composer } = await admittedSeam();
  const { kernel, journal } = recordingSeam();
  const mine = createJourneyOneClockStore({ journal, actor: STORE_ACTOR, clock_scope: SEAM_SCOPE });
  const theirs = createJourneyOneClockStore({ journal, actor: OTHER_ACTOR, clock_scope: SEAM_SCOPE });
  const request = advanceArgs();
  const first = await runtimeSeam({ composer, kernel, store: mine }).advance(request);
  assert.equal(first.appended, true);

  const otherSeat = runtimeSeam({ composer, kernel, store: theirs });
  await assert.rejects(otherSeat.advance({ ...request }), error => {
    assert.equal(error.name, "JourneyOneClockRuntimeError");
    assert.equal(error.code, "clock_runtime_replay_actor_mismatch");
    assert.equal(error.detail.recorded_written_by_actor_id, STORE_ACTOR.slug);
    assert.equal(error.detail.advancing_as_actor_id, OTHER_ACTOR.slug);
    return true;
  });
  assert.equal((await mine.read(first.clock_key)).revision_count, 1);
});

test("a head that moves under two concurrent advances refuses one, and its key stays free", async () => {
  const { join, composer } = await admittedSeam();
  const { kernel, store } = recordingSeam();
  const runtime = runtimeSeam({ composer, kernel, store });
  const started = await runtime.advance(advanceArgs());

  // Two advances against ONE head. Both read it, both compose against it, and
  // the store's compare-and-swap decides — the retry read above changes nothing
  // about that, because neither key has written anything yet.
  const at = iso(Date.parse(AS_OF) + 3 * DAY);
  const pause = seamPause(join.proposed_receipt_reference_digest,
    iso(Date.parse(AS_OF) + 2 * DAY), iso(Date.parse(AS_OF) + 2 * DAY + 12 * HOUR));
  const requests = [
    advanceArgs({ as_of: at, pauses: [pause] }),
    advanceArgs({ as_of: iso(Date.parse(AS_OF) + 4 * DAY) }),
  ];
  const settled = await Promise.allSettled(requests.map(request => runtime.advance(request)));
  const rejectedIndex = settled.findIndex(s => s.status === "rejected");
  assert.equal(settled.filter(s => s.status === "fulfilled").length, 1,
    "exactly one of two concurrent appends lands");
  assert.notEqual(rejectedIndex, -1);
  assert.equal(settled[rejectedIndex].reason.code, "clock_stale_prior_history_digest");
  assert.equal((await store.read(started.clock_key)).revision_count, 2);

  // THE REFUSED CALL WROTE NOTHING, so its key is not spent — which is what
  // makes re-sending it an ordinary attempt against the new head rather than a
  // replay of something that never happened.
  const spent = await store.readRecordedRevisionForKey(requests[rejectedIndex].idempotency_key);
  assert.equal(spent.exists, false);
  const landed = await store.readRecordedRevisionForKey(
    requests[rejectedIndex === 0 ? 1 : 0].idempotency_key);
  assert.equal(landed.exists, true);
  assert.equal(landed.revision_ordinal, 1);
  assert.equal(landed.expected_prior_history_digest, started.history_digest);
});

test("a composer and a store bound to different accepted scopes refuse before anything is read", async () => {
  const { composer } = await admittedSeam();
  for (const field of ["benchmark_subject_digest", "benchmark_candidate_digest",
    "benchmark_policy_digest"]) {
    const { kernel, store } = recordingSeam({ ...SEAM_SCOPE, [field]: D(50) });
    assert.throws(() => runtimeSeam({ composer, kernel, store }), error => {
      assert.equal(error.code, "clock_runtime_scope_disagreement");
      assert.equal(error.detail.invariant, "j1_clock_scope_binds_one_clock");
      assert.equal(error.detail.composer_clock_scope_key, journeyOneClockScopeKey(SEAM_SCOPE));
      return true;
    });
  }
});
