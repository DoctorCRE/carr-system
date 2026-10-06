// journey-one-clock-door.v5.js — the live door to the Journey 1 clock.
//
// WHAT THIS SUITE PROVES, and at which seam. The kernel suite proves the clock
// arithmetic and the integration suite proves the runtime loop. This suite drives
// the same real rails — A00's join, the admitted-minimum inventory, the composer,
// a real kernel, the real clock store — THROUGH THE TWO REGISTERED VERB
// HANDLERS, because the V5-M01 checkable_done is about what the live door does:
//
//   1. clock fixtures cover start / pause / miss / late, and pass at the door;
//   2. the kernel receipt carries the exact decisions and observed_at — asserted
//      as deep equality against an independent kernel evaluation of the exact
//      projection the door composed, never against restated literals;
//   3. a miss records a replan without erasing history — every earlier revision
//      reads back through the read door with the digest it was written under.
//
// And the refusal paths: every refusal the door adds has a witness here, and
// each witness is what kills the planted-bug mutant that deletes it.
//
// NOTHING HERE IS AUTHENTICATED AND NO PRODUCTION CLOCK IS TOUCHED. The kernel
// runs on a test-only verifier over a reference (non-durable) journal, the
// installation is a declared `fixture`, and the door refuses a fixture over a
// durable journal by name.
import test from "node:test";
import assert from "node:assert/strict";
import { digest } from "../src/artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  BENCHMARK_COST_VARIANCE_THRESHOLDS, BENCHMARK_DEADLINE_CONTRACT,
  BENCHMARK_MEASUREMENT_SET_SCHEMA, BENCHMARK_PAYLOAD_DOMAIN_TAG, BENCHMARK_SLO_THRESHOLDS,
  BENCHMARK_MANIFEST_SCHEMA, FOUNDATION_ASSURANCE_MINIMUM_PROJECTION, GATE_ZERO_STEP_REF,
  MINIMUM_REQUIRED_MEMBERS, P95_AGGREGATION_METHOD,
  benchmarkPayloadDigest, benchmarkRequiredCells, createFoundationAssuranceMinimumGate,
  proposeBenchmarkCoverageFact,
} from "../src/benchmark-minimum.v5.js";
import {
  JOURNEY_ONE_DEADLINE_CONTRACT, chicagoThirtyDayDeadline, createJourneyOneClock,
} from "../src/journey-one-clock.v5.js";
import {
  createEphemeralJourneyOneClockJournal, createJourneyOneClockStore, journeyOneClockScopeKey,
} from "../src/journey-one-clock-store.v5.js";
import {
  createEphemeralJourneyOneMinimumAdmissionJournal, createJourneyOneClockMinimumInputStore,
  createJourneyOneClockProjectionComposer, journeyOneMinimumBenchmarkAcceptedSources,
} from "../src/journey-one-clock-input-store.v5.js";
import {
  JOURNEY_ONE_CLOCK_ADVANCE_VERB, JOURNEY_ONE_CLOCK_DOOR_ADVANCE_SCHEMA,
  JOURNEY_ONE_CLOCK_DOOR_READ_FIELDS, JOURNEY_ONE_CLOCK_DOOR_READ_SCHEMA,
  JOURNEY_ONE_CLOCK_INSTALLED_ADVANCE_FIELDS, JOURNEY_ONE_CLOCK_NO_INSTALLATION,
  JOURNEY_ONE_CLOCK_READ_VERB, advanceAdmittedJourneyOneClock, journeyOneClockDoorIntegrationRequirements,
  journeyOneClockDoorTools, readJourneyOneClockDoor,
} from "../src/journey-one-clock-door.v5.js";

const D = n => `sha256:${String(n).padStart(2, "0").repeat(32)}`;
const I = actor => ({
  actor_id: actor,
  session_ref: `session:j1-door-${actor}`,
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
const CLOCK_AS_OF = iso(Date.parse(AS_OF) + HOUR);
const MINIMUM_TTL = 7 * DAY;
const COMPLETION_TTL = 30 * DAY;
const PAUSE_CAP_MS = JOURNEY_ONE_DEADLINE_CONTRACT.maximum_external_blocker_pause_hours * HOUR;
const KERNEL_ARTIFACT = D(20);
const KERNEL_FIXTURES = D(21);
const VERIFIER_REF = "safe:verifier:j1-door-fixture-verifier";

/** The seat the fixture installation names; it is an ordinary known runtime actor. */
const SEAT = { slug: "claude", human: false, sponsoring_human_slug: "joe" };
const OTHER = { slug: "codex", human: false, sponsoring_human_slug: "joe" };

const SCOPE = Object.freeze({
  benchmark_candidate_digest: D(2),
  benchmark_policy_digest: D(3),
  benchmark_subject_digest: D(1),
  clock_origin_gate_id: JOURNEY_ONE_DEADLINE_CONTRACT.clock_origin_gate_id,
  clock_terminus_gate_id: JOURNEY_ONE_DEADLINE_CONTRACT.clock_terminus_gate_id,
  scope_ref: "safe:clock-scope:j1-door",
  tenant: ORGANIZATION_TENANT_ID,
});
const SCOPE_KEY = journeyOneClockScopeKey(SCOPE);
const MINIMUM_POLICY = Object.freeze({
  maximum_minimum_receipt_ttl_ms: MINIMUM_TTL,
  minimum_environment_manifest_digest: D(4),
});

let serial = 0;
const uuid = () => `00000000-0000-4000-8000-${(++serial).toString(16).padStart(12, "0")}`;

// --- A00: the artifact the origin is selected from --------------------------

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
function manifest(body = payload()) {
  return {
    ...copy(body),
    benchmark_manifest_digest: digest([BENCHMARK_PAYLOAD_DOMAIN_TAG, body]),
    accepted_by_identity: I("joe"),
    accepted_at: ACCEPTED_AT,
    status: "accepted",
  };
}
function coverageFact(body = payload()) {
  return copy(proposeBenchmarkCoverageFact({
    payload: body,
    measurements: {
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
    },
    evaluator_identity: I("bench-evaluator"),
    evidence_ref: "safe:j1-door:coverage-evidence",
    observed_at: COVERAGE_AT,
    ttl_expires_at: iso(Date.parse(COVERAGE_AT) + 7 * DAY),
  }));
}
function minimumProjection() {
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
      ? { step_ref: spec.step_ref, receipt: manifest() }
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
          evidence_ref: `safe:j1-door:${spec.gate_id}`,
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
      evidence_ref: "safe:j1-door:minimum-join",
      fixture_set_digest: D(5),
      comparator: "exact closed minimum join comparator",
    },
  };
}
function joinOnce() {
  const evidence = new Map();
  const gate = createFoundationAssuranceMinimumGate({
    authenticateEvidence(envelope) {
      const found = evidence.get(digest(envelope));
      if (!found) throw new Error("synthetic-authentication-refused");
      return { envelope_digest: digest(envelope), snapshot: copy(found) };
    },
  });
  const envelope = { synthetic_evidence_ref: "j1-door-join" };
  evidence.set(digest(envelope), copy(minimumProjection()));
  return gate.evaluate(envelope);
}

// --- M01: the kernel, a pause, a terminus -----------------------------------

/** A test-only kernel whose envelopes are handed out one at a time. */
function kernelHarness() {
  const evidence = new Map(); let n = 0;
  const clock = createJourneyOneClock({
    verifySnapshot(envelope) {
      const found = evidence.get(digest(envelope));
      if (!found) throw new Error("synthetic-authentication-refused");
      return { envelope_digest: digest(envelope), snapshot: copy(found) };
    },
  });
  return { clock, envelopeFor(projection) {
    const envelope = { synthetic_receipt_ref: `j1-door-${++n}` };
    evidence.set(digest(envelope), copy(projection));
    return envelope;
  } };
}
/** An INDEPENDENT kernel, for the exact-decisions comparison. */
function evaluateIndependently(projection) {
  const k = kernelHarness();
  return k.clock.evaluate(k.envelopeFor(projection));
}
function pause(originDigest, startsAt, endsAt, id) {
  const body = {
    pause_id: `safe:j1-door:pause-${id}`,
    clock_origin_digest: originDigest,
    blocker_ref: "safe:external-blocker:door-provider",
    starts_at: startsAt,
    approved_at: iso(Date.parse(startsAt) - HOUR),
    approved_by_identity: I("dell"),
  };
  return { ...body, ends_at: endsAt, approval_digest: digest(["doctorcre:j1-clock-pause:v1", body]) };
}
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
    evidence_ref: "safe:j1-door:kernel-production-evidence",
    fixture_set_digest: KERNEL_FIXTURES,
    observed_at: observedAt,
    ttl_expires_at: iso(Date.parse(observedAt) + 2 * DAY),
    status: "pass",
    comparator: "exact kernel production comparator",
    negative_admission_result: "all_required_denials_observed",
    subject_environment: "production",
  };
}
function completion(at) {
  return { gate_id: JOURNEY_ONE_DEADLINE_CONTRACT.clock_terminus_gate_id,
    combiner: "all_current_exact_distinct_pass",
    obligation_decision_ids: [...JOURNEY_ONE_DEADLINE_CONTRACT.kernel_obligation_decision_ids],
    receipts: [terminusReceipt(at)] };
}

// --- THE DOOR, over real rails -----------------------------------------------

class ToolError extends Error {
  constructor(payload) { super(payload.error); Object.assign(this, payload); }
}

/**
 * The whole fixture: an admitted inventory, a composer over it, a real kernel, a
 * real store over a reference journal, and a `fixture` installation whose trusted
 * advance-input reader is `feed` — the stand-in for the record layer's own
 * pause, amendment and terminus producers, and for the server's clock.
 */
async function doorHarness({ storeActor = SEAT, durable = false, kind = "fixture",
  seat = SEAT.slug, readAdvanceInputs = null } = {}) {
  const join = joinOnce();
  const inputJournal = createEphemeralJourneyOneMinimumAdmissionJournal({ now: () => Date.parse(CLOCK_AS_OF) });
  const inputs = createJourneyOneClockMinimumInputStore({
    journal: inputJournal, actor: SEAT, clock_scope: SCOPE, accepted_minimum_policy: MINIMUM_POLICY });
  const admitted = await inputs.admit({
    receipt: copy(join.proposed_receipt), expected_prior_admission_digest: null,
    idempotency_key: uuid(), claimed_receipt_digest: join.proposed_receipt_reference_digest,
    source_ref: "safe:a00:proposed-minimum-receipt" });
  const acceptedManifest = manifest();
  const composer = createJourneyOneClockProjectionComposer({ store: inputs,
    accepted_sources: { ...journeyOneMinimumBenchmarkAcceptedSources(acceptedManifest),
      maximum_completion_receipt_ttl_ms: COMPLETION_TTL,
      production_environment_manifest_digest: D(7) },
    benchmark_manifest: acceptedManifest });
  const base = createEphemeralJourneyOneClockJournal();
  const journal = durable ? Object.freeze({ ...base, durable: true }) : base;
  const store = createJourneyOneClockStore({ journal, actor: storeActor, clock_scope: SCOPE });
  const kernel = kernelHarness();
  const feed = {
    as_of: CLOCK_AS_OF, pauses: [], amendments: [], completion: null,
    completion_expectation: { artifact_digest: KERNEL_ARTIFACT, fixture_set_digest: KERNEL_FIXTURES },
  };
  const opened = [];
  const installation = {
    kind, advancing_seat: seat,
    async open(context) {
      opened.push(context);
      return { composer, clock: kernel.clock, clock_store: store,
        present_projection: projection => kernel.envelopeFor(projection),
        verifier_ref: VERIFIER_REF,
        readAdvanceInputs: readAdvanceInputs ?? (async () => copy(feed)) };
    },
  };
  const envelopes = [];
  const tools = journeyOneClockDoorTools({
    withEnvelope: (_c, _actor, verb, args, fn) => { envelopes.push({ verb, args }); return fn(); },
    ToolError,
    resolve_installation: () => installation,
  });
  // The connection a verb handler is given. The fixture rails never reach it,
  // so ANY query here is the door reaching past its installation.
  const connection = { queries: 0, query() { this.queries += 1; throw new Error("no query on a fixture door"); } };
  const advance = (actor = SEAT, args = { idempotency_key: uuid() }) =>
    tools[JOURNEY_ONE_CLOCK_ADVANCE_VERB].handler(connection, actor, args);
  // READ EXACTLY AS THE VERB DOES: fresh stores with NO scope binding — the
  // construction that cannot record — over the same journals.
  const read = () => readJourneyOneClockDoor({
    input_store: createJourneyOneClockMinimumInputStore({ journal: inputJournal, actor: SEAT }),
    clock_store: createJourneyOneClockStore({ journal, actor: SEAT }),
    clock_journal: journal, clock_scope_key: SCOPE_KEY });
  // The projection the door WILL compose for the current feed against the
  // current head, so a test can evaluate it independently.
  const composeNext = async () => {
    const bound = await journal.readScopeBindings({ clockScopeKey: SCOPE_KEY });
    const head = bound.by_scope ? await store.read(bound.by_scope.clock_key) : null;
    return (await composer.compose({ ...copy(feed), history: head ? copy(head.history) : null })).projection;
  };
  return { join, admitted, inputs, composer, store, journal, kernel, feed, installation, opened,
    envelopes, tools, connection, advance, read, composeNext,
    origin: join.proposed_receipt_reference_digest,
    receiptObservedAt: join.proposed_receipt.observed_at };
}
async function refusesWith(promise, code) {
  await assert.rejects(promise, error => {
    assert.equal(error.error ?? error.code, code,
      `expected ${code}, got ${error.error ?? error.code}: ${error.message}`);
    return true;
  });
}

/** The kernel's verdict fields, projected off an independent evaluate() result. */
const verdictOf = result => ({
  status: result.state.status,
  deadline_success: result.deadline_success,
  replan_required: result.replan_required,
  completion_currently_usable: result.completion_currently_usable,
  completion_observed_within_deadline: result.completion_observed_within_deadline,
  missing_evidence_miss_recorded: result.missing_evidence_miss_recorded,
  benchmark_amended: result.benchmark_amended,
  deadline_resolution: result.deadline_resolution,
  unresolved_reason: result.unresolved_reason,
});

/**
 * ONE ADVANCE THROUGH THE DOOR, checked for exactness against an independent
 * kernel evaluation of the projection the door is about to compose:
 *   * the receipt's kernel_verdict IS the independent verdict, field for field;
 *   * the stored head IS the independent state, byte for byte (same digest,
 *     deep-equal, every event's `at` included);
 *   * the read door reports that same head unchanged.
 */
async function advanceExactly(h) {
  const projection = await h.composeNext();
  const independent = evaluateIndependently(projection);
  const result = await h.advance();
  assert.equal(result.ok, true);
  assert.equal(result.schema_version, JOURNEY_ONE_CLOCK_DOOR_ADVANCE_SCHEMA);
  assert.deepEqual(result.advance.kernel_verdict, verdictOf(independent));
  assert.equal(result.advance.history_digest, independent.state.history_digest);
  assert.equal(result.advance.composition_binding.authenticated_projection_digest,
    independent.verified_binding.authenticated_projection_digest);
  const read = await h.read();
  assert.deepEqual(read.clock.history, copy(independent.state));
  return { result, read, independent };
}

// ---------------------------------------------------------------------------
// 1. NOT STARTED is read, not asserted.
// ---------------------------------------------------------------------------

test("before any advance the read door derives clock_started false from the record", async () => {
  const h = await doorHarness();
  const read = await h.read();
  assert.deepEqual(Object.keys(read).sort(), [...JOURNEY_ONE_CLOCK_DOOR_READ_FIELDS]);
  assert.equal(read.schema_version, JOURNEY_ONE_CLOCK_DOOR_READ_SCHEMA);
  assert.equal(read.clock_scope_key, SCOPE_KEY);
  assert.equal(read.clock_started, false);
  assert.equal(read.clock_started_basis, "no_clock_bound_to_this_scope");
  assert.equal(read.clock_key, null);
  assert.equal(read.clock, null);
  // THE ORIGIN CANDIDATE IS VISIBLE AND UNUSED. One admitted receipt, at its own
  // observation instant, and still no clock.
  assert.equal(read.inventory.exists, true);
  assert.equal(read.inventory.admission_count, 1);
  assert.equal(read.inventory.head_admission_digest, h.admitted.admission_digest);
  assert.equal(read.inventory.admissions[0].observed_at, h.receiptObservedAt);
  assert.equal(read.inventory.admissions[0].receipt_digest, h.origin);
  assert.equal(read.deadline_accepted_by_record_layer, false);
  assert.equal(read.effects.database_writes, 0);
  assert.ok(read.record_layer_cannot_prove.some(line => line.includes("SHOULD have started")));
});

test("a scope with no inventory and no clock reads as not started, not as an error", async () => {
  const h = await doorHarness();
  const empty = createEphemeralJourneyOneMinimumAdmissionJournal();
  const read = await readJourneyOneClockDoor({
    input_store: createJourneyOneClockMinimumInputStore({ journal: empty, actor: SEAT }),
    clock_store: createJourneyOneClockStore({ journal: h.journal, actor: SEAT }),
    clock_journal: h.journal, clock_scope_key: D(99) });
  assert.deepEqual(read.inventory, { exists: false });
  assert.equal(read.clock_started, false);
  assert.equal(read.clock_key, null);
});

// ---------------------------------------------------------------------------
// 2. START / PAUSE / MISS / LATE, through the verb handlers.
// ---------------------------------------------------------------------------

test("START: the first advance through the door starts the clock at the admitted receipt's own observed_at", async () => {
  const h = await doorHarness();
  const { result, read, independent } = await advanceExactly(h);
  assert.equal(result.installation_kind, "fixture");
  assert.equal(result.advancing_seat, SEAT.slug);
  assert.equal(result.clock_started_by_this_call, true);
  assert.equal(result.advance.created_clock, true);
  assert.equal(result.advance.appended, true);
  assert.equal(result.advance.kernel_verdict.status, "running");
  assert.equal(result.advance.kernel_verdict.deadline_success, false);
  assert.equal(result.advance.kernel_verdict.replan_required, false);
  // The installation was opened with the verb's own connection and live actor.
  assert.equal(h.opened.length, 1);
  assert.equal(h.opened[0].c, h.connection);
  assert.equal(h.opened[0].actor, SEAT);
  assert.deepEqual(h.envelopes.map(e => e.verb), [JOURNEY_ONE_CLOCK_ADVANCE_VERB]);
  assert.equal(h.connection.queries, 0);

  // THE READ DOOR NOW DERIVES started, from the kernel's own event.
  assert.equal(read.clock_started, true);
  assert.equal(read.clock_started_basis, "bound_clock_head_records_the_kernel_clock_started_event");
  assert.equal(read.clock_key, result.advance.clock_key);
  assert.equal(read.clock.revision_count, 1);
  // EXACT observed_at: the origin is the admitted receipt's observation instant,
  // and the clock_started event is dated to it and names that receipt.
  assert.equal(read.clock.history.origin_at, h.receiptObservedAt);
  assert.equal(read.clock.history.origin_receipt_digest, h.origin);
  const started = read.clock.history.events[0];
  assert.equal(started.type, "clock_started");
  assert.equal(started.at, h.receiptObservedAt);
  assert.equal(started.recorded_at, CLOCK_AS_OF);
  assert.equal(started.evidence_digest, h.origin);
  assert.equal(read.clock.history.due_at, chicagoThirtyDayDeadline(h.receiptObservedAt).due_at);
  assert.equal(independent.state.evaluated_at, CLOCK_AS_OF);
});

test("PAUSE within the budget: a partner-approved twelve-hour pause is credited in full", async () => {
  const h = await doorHarness();
  await advanceExactly(h);
  const start = iso(Date.parse(AS_OF) + 2 * DAY);
  const p = pause(h.origin, start, iso(Date.parse(start) + 12 * HOUR), "short");
  h.feed.pauses = [p];
  h.feed.as_of = iso(Date.parse(AS_OF) + 3 * DAY);
  const { result, read } = await advanceExactly(h);
  assert.equal(result.clock_started_by_this_call, false);
  assert.equal(result.advance.created_clock, false);
  assert.equal(result.advance.kernel_verdict.status, "running");
  assert.equal(read.clock.history.paused_ms, 12 * HOUR);
  assert.equal(read.clock.history.due_at,
    iso(Date.parse(chicagoThirtyDayDeadline(h.receiptObservedAt).due_at) + 12 * HOUR));
  const approved = read.clock.history.events.at(-1);
  assert.equal(approved.type, "pause_approved");
  assert.equal(approved.at, p.approved_at);
  assert.equal(approved.evidence_digest, p.approval_digest);
});

test("PAUSE over the budget: six accepted days are credited as exactly five (120 hours)", async () => {
  const h = await doorHarness();
  await advanceExactly(h);
  const start = iso(Date.parse(AS_OF) + 2 * DAY);
  h.feed.pauses = [pause(h.origin, start, iso(Date.parse(start) + 6 * DAY), "long")];
  h.feed.as_of = iso(Date.parse(AS_OF) + 9 * DAY);
  const { read } = await advanceExactly(h);
  assert.equal(PAUSE_CAP_MS, 5 * 24 * HOUR, "the accepted budget is five pause days of actual hours");
  assert.equal(read.clock.history.paused_ms, PAUSE_CAP_MS);
  assert.equal(read.clock.history.due_at,
    iso(Date.parse(chicagoThirtyDayDeadline(h.receiptObservedAt).due_at) + PAUSE_CAP_MS));
});

test("MISS then LATE: the miss records a replan and no revision is erased by the late completion", async () => {
  const h = await doorHarness();
  const start = await advanceExactly(h);
  const due = start.read.clock.history.due_at;

  // MISS: the deadline passes with no terminus.
  h.feed.as_of = iso(Date.parse(AS_OF) + 40 * DAY);
  const missed = await advanceExactly(h);
  assert.equal(missed.result.advance.kernel_verdict.status, "missed");
  assert.equal(missed.result.advance.kernel_verdict.replan_required, true);
  assert.equal(missed.result.advance.kernel_verdict.missing_evidence_miss_recorded, true);
  assert.equal(missed.result.advance.kernel_verdict.deadline_success, false);
  assert.equal(missed.read.clock.history.miss_at, due);
  const missEvent = missed.read.clock.history.events.find(e => e.type === "deadline_missed");
  assert.equal(missEvent.at, due);
  const afterMiss = missed.read.clock.revisions;
  assert.equal(afterMiss.length, 2);

  // LATE: a real terminus, observed after the deadline.
  const lateAt = iso(Date.parse(AS_OF) + 41 * DAY);
  h.feed.as_of = lateAt;
  h.feed.completion = completion(lateAt);
  const late = await advanceExactly(h);
  assert.equal(late.result.advance.kernel_verdict.status, "completed_late");
  assert.equal(late.result.advance.kernel_verdict.deadline_success, false);
  assert.equal(late.result.advance.kernel_verdict.replan_required, true);
  assert.equal(late.result.advance.kernel_verdict.completion_observed_within_deadline, false);

  // NOTHING ERASED. The chain grew by one and its prefix is exactly what the
  // read door showed after the miss, digest for digest; the miss, the origin and
  // the start are all still in the head.
  const history = late.read.clock.history;
  assert.equal(late.read.clock.revisions.length, 3);
  assert.deepEqual(late.read.clock.revisions.slice(0, 2), afterMiss);
  assert.equal(late.read.clock.revisions[2].expected_prior_history_digest, afterMiss[1].history_digest);
  assert.equal(history.miss_at, due);
  assert.equal(history.origin_at, h.receiptObservedAt);
  assert.equal(history.origin_receipt_digest, h.origin);
  assert.equal(history.completion_observed_at, lateAt);
  assert.deepEqual(history.events.map(e => e.type),
    ["clock_started", "deadline_missed", "completion_observed"]);
  assert.equal(history.events.find(e => e.type === "completion_observed").at, lateAt);
  assert.equal(late.read.clock_started, true);
});

test("a retried advance replays what it wrote and starts nothing a second time", async () => {
  const h = await doorHarness();
  const args = { idempotency_key: uuid() };
  const first = await h.advance(SEAT, args);
  const again = await h.advance(SEAT, { ...args });
  assert.equal(first.clock_started_by_this_call, true);
  assert.equal(again.clock_started_by_this_call, false);
  assert.equal(again.advance.replayed, true);
  assert.equal(again.advance.appended, false);
  assert.equal(again.advance.history_digest, first.advance.history_digest);
  assert.equal((await h.read()).clock.revision_count, 1);
});

// ---------------------------------------------------------------------------
// 3. THE ADVANCE DOOR'S REFUSALS. Each is a mutant witness.
// ---------------------------------------------------------------------------

test("REFUSAL: the door registered the way tools.js registers it refuses before any query or envelope", async () => {
  let queries = 0, envelopes = 0;
  const connection = { query() { queries += 1; throw new Error("no query may be issued"); } };
  const tools = journeyOneClockDoorTools({
    withEnvelope: (_c, _a, _v, _args, fn) => { envelopes += 1; return fn(); }, ToolError });
  const verb = tools[JOURNEY_ONE_CLOCK_ADVANCE_VERB];
  await refusesWith(verb.handler(connection, SEAT, { idempotency_key: uuid() }),
    "journey_one_clock_installation_unavailable");
  assert.equal(queries, 0);
  assert.equal(envelopes, 0, "the refusal precedes even the envelope's replay lookup");
  // An explicit null resolver result is the same refusal.
  const explicit = journeyOneClockDoorTools({ withEnvelope: () => assert.fail("no envelope"),
    ToolError, resolve_installation: JOURNEY_ONE_CLOCK_NO_INSTALLATION });
  await refusesWith(explicit[JOURNEY_ONE_CLOCK_ADVANCE_VERB].handler(connection, SEAT,
    { idempotency_key: uuid() }), "journey_one_clock_installation_unavailable");
  const notAFunction = journeyOneClockDoorTools({ withEnvelope: () => assert.fail("no envelope"),
    ToolError, resolve_installation: "installed" });
  await refusesWith(notAFunction[JOURNEY_ONE_CLOCK_ADVANCE_VERB].handler(connection, SEAT,
    { idempotency_key: uuid() }), "journey_one_clock_installation_unavailable");
});

test("REFUSAL: an unauthenticated actor is refused before the installation is consulted", async () => {
  const h = await doorHarness();
  await refusesWith(h.advance({ slug: "nobody", human: false }), "clock_writer_identity_unavailable");
  await refusesWith(h.advance(null), "clock_writer_identity_unavailable");
  assert.equal(h.opened.length, 0);
  assert.equal(h.envelopes.length, 0);
});

test("REFUSAL: the request carries one idempotency key and nothing the installation owns", async () => {
  const h = await doorHarness();
  for (const extra of [{ as_of: AS_OF }, { pauses: [] }, { completion: null }, { history: null },
    { expected_prior_history_digest: null }, { clock_ref: null }, { amendments: [] }]) {
    await refusesWith(h.advance(SEAT, { idempotency_key: uuid(), ...extra }), "closed_shape");
  }
  await refusesWith(h.advance(SEAT, {}), "closed_shape");
  await refusesWith(h.advance(SEAT, "key"), "invalid_shape");
  await refusesWith(h.advance(SEAT, null), "invalid_shape");
  for (const smuggled of [{ verified: true }, { clock_started: true }, { authority_granted: true }]) {
    await refusesWith(h.advance(SEAT, { idempotency_key: uuid(), ...smuggled }),
      "self_asserted_authority_refused");
  }
  assert.equal(h.opened.length, 0);
  assert.equal((await h.read()).clock_started, false);
});

test("REFUSAL: only the installation's one seat may advance", async () => {
  const h = await doorHarness();
  await refusesWith(h.advance(OTHER), "journey_one_clock_advance_seat_required");
  await refusesWith(h.advance({ slug: "joe", human: true }), "journey_one_clock_advance_seat_required");
  assert.equal(h.opened.length, 0);
  assert.equal((await h.read()).clock_started, false);
});

test("REFUSAL: an installation of an unknown kind, or with no seat or opener, is invalid", async () => {
  for (const over of [{ kind: "production" }, { seat: "" }]) {
    const h = await doorHarness(over);
    await refusesWith(h.advance(), "journey_one_clock_installation_invalid");
    assert.equal(h.opened.length, 0);
  }
  const h = await doorHarness();
  const tools = journeyOneClockDoorTools({ withEnvelope: (_c, _a, _v, _x, fn) => fn(), ToolError,
    resolve_installation: () => ({ kind: "fixture", advancing_seat: SEAT.slug }) });
  await refusesWith(tools[JOURNEY_ONE_CLOCK_ADVANCE_VERB].handler(h.connection, SEAT,
    { idempotency_key: uuid() }), "journey_one_clock_installation_invalid");
  const noParts = journeyOneClockDoorTools({ withEnvelope: (_c, _a, _v, _x, fn) => fn(), ToolError,
    resolve_installation: () => ({ kind: "fixture", advancing_seat: SEAT.slug, open: async () => ({}) }) });
  await refusesWith(noParts[JOURNEY_ONE_CLOCK_ADVANCE_VERB].handler(h.connection, SEAT,
    { idempotency_key: uuid() }), "journey_one_clock_installation_invalid");
});

test("REFUSAL: a fixture installation never writes to a durable journal", async () => {
  const h = await doorHarness({ durable: true });
  await refusesWith(h.advance(), "journey_one_clock_fixture_installation_on_durable_journal");
  assert.equal((await h.read()).clock_started, false);
  // The same parts under a trusted_server_verifier kind are not refused by this
  // guard, which is specific to fixtures.
  const trusted = await doorHarness({ durable: true, kind: "trusted_server_verifier" });
  const result = await trusted.advance();
  assert.equal(result.installation_kind, "trusted_server_verifier");
});

test("REFUSAL: the installation's store must write as the live actor advancing", async () => {
  const h = await doorHarness({ storeActor: OTHER });
  await refusesWith(h.advance(), "journey_one_clock_store_actor_mismatch");
  assert.equal((await h.read()).clock_started, false);
});

test("REFUSAL: the installation's trusted inputs are exactly the five installed fields", async () => {
  const base = { as_of: CLOCK_AS_OF, pauses: [], amendments: [], completion: null,
    completion_expectation: { artifact_digest: KERNEL_ARTIFACT, fixture_set_digest: KERNEL_FIXTURES } };
  assert.deepEqual(Object.keys(base).sort(), [...JOURNEY_ONE_CLOCK_INSTALLED_ADVANCE_FIELDS]);
  for (const bad of [{ ...base, history: null }, { ...base, idempotency_key: uuid() },
    (({ pauses, ...rest }) => rest)(base)]) {
    const h = await doorHarness({ readAdvanceInputs: async () => copy(bad) });
    await refusesWith(h.advance(), "closed_shape");
    assert.equal((await h.read()).clock_started, false);
  }
});

test("REFUSAL: an advance runs only once its request has been admitted", async () => {
  await refusesWith(advanceAdmittedJourneyOneClock({ c: null, actor: SEAT, args: {}, admitted: null }),
    "journey_one_clock_advance_not_admitted");
});

// ---------------------------------------------------------------------------
// 4. THE READ DOOR'S REFUSALS.
// ---------------------------------------------------------------------------

function readDoorWith({ inventory = { exists: false }, bindings = { by_scope: null, by_clock: null },
  readback = { exists: false } } = {}) {
  return readJourneyOneClockDoor({
    input_store: { read: async () => inventory },
    clock_store: { read: async () => readback },
    clock_journal: { readScopeBindings: async () => bindings },
    clock_scope_key: SCOPE_KEY });
}

test("REFUSAL: the read door addresses a scope by a sha256 key and nothing else", async () => {
  for (const key of [undefined, "", "sha256:xyz", D(1).toUpperCase()]) {
    await refusesWith(readJourneyOneClockDoor({ input_store: { read: async () => ({}) },
      clock_store: { read: async () => ({}) }, clock_journal: { readScopeBindings: async () => ({}) },
      clock_scope_key: key }), "invalid_reference");
  }
  await refusesWith(readJourneyOneClockDoor({ clock_scope_key: SCOPE_KEY }), "invalid_shape");
});

test("REFUSAL: an inventory whose stored scope derives another key is not this program's", async () => {
  await refusesWith(readDoorWith({ inventory: { exists: true,
    clock_scope: { ...SCOPE, benchmark_subject_digest: D(9) }, admissions: [] } }),
  "clock_door_inventory_scope_mismatch");
});

test("REFUSAL: a scope bound to a clock the record cannot produce is not reported as not started", async () => {
  await refusesWith(readDoorWith({ bindings: { by_scope: { clock_key: D(70) } } }),
    "clock_door_bound_clock_unreadable");
});

test("REFUSAL: a bound clock that reads back under another scope refuses", async () => {
  await refusesWith(readDoorWith({ bindings: { by_scope: { clock_key: D(70) } },
    readback: { exists: true, clock_key: D(70), clock_scope_key: D(71), history: { events: [] } } }),
  "clock_door_bound_clock_scope_mismatch");
});

test("REFUSAL: a bound head with no clock_started event is never reported as started", async () => {
  await refusesWith(readDoorWith({ bindings: { by_scope: { clock_key: D(70) } },
    readback: { exists: true, clock_key: D(70), clock_scope_key: SCOPE_KEY, revisions: [],
      history: { events: [{ type: "pause_approved" }] } } }),
  "clock_door_head_without_start");
});

// ---------------------------------------------------------------------------
// 5. THE VERB CONTRACTS.
// ---------------------------------------------------------------------------

test("the two verbs: one read with no effect, one write that takes only an idempotency key", async () => {
  const tools = journeyOneClockDoorTools({ withEnvelope: () => assert.fail("no envelope"), ToolError });
  assert.deepEqual(Object.keys(tools).sort(), [JOURNEY_ONE_CLOCK_ADVANCE_VERB, JOURNEY_ONE_CLOCK_READ_VERB]);
  const read = tools[JOURNEY_ONE_CLOCK_READ_VERB];
  assert.equal(read.write, false);
  assert.deepEqual(read.inputSchema.required, ["clock_scope_key"]);
  assert.equal(read.inputSchema.additionalProperties, false);
  const advance = tools[JOURNEY_ONE_CLOCK_ADVANCE_VERB];
  assert.equal(advance.write, true);
  assert.deepEqual(Object.keys(advance.inputSchema.properties), ["idempotency_key"]);
  assert.deepEqual(advance.inputSchema.required, ["idempotency_key"]);
  assert.equal(advance.inputSchema.additionalProperties, false);
  // No new authority flag: the seat is the installation's, not a registry class.
  for (const verb of [read, advance]) {
    assert.equal(verb.humanOnly, undefined);
    assert.equal(verb.authorityOnly, undefined);
    assert.equal(verb.oracleSeatOnly, undefined);
  }
  assert.match(advance.description, /^REFUSES IN EVERY DEPLOYED WORKER/);
});

test("the read verb reads through the database journals and translates a refusal", async () => {
  const tools = journeyOneClockDoorTools({ withEnvelope: () => assert.fail("no envelope"), ToolError });
  const seen = [];
  const connection = { async query(sql, params) {
    seen.push(sql);
    if (sql.includes("j1_minimum_inventory_row")) return { rows: [{ inventory: null }] };
    if (sql.includes("j1_clock_scope_bindings")) return { rows: [{ bindings: { by_scope: null, by_clock: null } }] };
    throw new Error(`unexpected query: ${sql} ${JSON.stringify(params)}`);
  } };
  const result = await tools[JOURNEY_ONE_CLOCK_READ_VERB].handler(connection, SEAT, { clock_scope_key: SCOPE_KEY });
  assert.equal(result.clock_started, false);
  assert.equal(result.clock_started_basis, "no_clock_bound_to_this_scope");
  assert.ok(seen.every(sql => /^select ops\.j1_/.test(sql.trim())), "reads only, through the definer read functions");
  await assert.rejects(tools[JOURNEY_ONE_CLOCK_READ_VERB].handler(connection, SEAT, { clock_scope_key: "x" }),
    error => error instanceof ToolError && error.error === "invalid_reference");
});

test("the integration statement says the advance door is bound nowhere and starts nothing unattended", () => {
  const req = journeyOneClockDoorIntegrationRequirements();
  assert.equal(req.installation_bound_in_deployed_workers, false);
  assert.deepEqual(req.request_fields, ["idempotency_key"]);
  assert.deepEqual(req.installed_advance_fields, [...JOURNEY_ONE_CLOCK_INSTALLED_ADVANCE_FIELDS]);
  assert.ok(req.explicitly_refused.some(line => line.includes("scheduled job")));
  assert.equal(req.effects.database_writes, 0);
});
