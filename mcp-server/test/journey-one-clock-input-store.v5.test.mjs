// V5-M01 admitted-minimum input store. Every test drives the REAL storage
// mechanics — the server admission instant and its binding to the receipt's own
// observation, the hash-chained compare-and-swap, exact-payload idempotency, the
// sealed accepted policy, the append-only ordering that keeps the first origin
// from moving, and the deterministic readback — and then hands the SAME stored
// rows to the REAL M01 kernel through the real composer.
//
// WHAT IS AND IS NOT PROVED HERE, so nobody reads more into a green run:
//   * PROVED: this rail's own logic over the non-durable reference journal, and
//     that journey-one-clock.v5.js evaluates the stored inventory to the verdicts
//     asserted below. The kernel is called for real; no origin, deadline, status
//     or event is hand-written except where a test deliberately tampers.
//   * NOT PROVED: anything about ops/journey-one-clock-input-store.candidate.sql.
//     It has never been executed. The only mechanical claim made about it here
//     is textual: that it states every shared admission invariant, contains no
//     ALTER or backfill, and defines every function the postgres journal calls.
//   * NOT PROVED: that ops.j1_minimum_receipt_digest and this module compute the
//     same hash for one receipt, or that ops.j1_minimum_admission_digest and
//     journeyOneMinimumAdmissionDigest agree. Both sides hash the canonical
//     serialization of the same preimage and the SQL side reuses
//     ops.portfolio_canonical_json, which migration 0496 already reconciles
//     against artifact-trust.js's canonicalJson — but a JavaScript suite cannot
//     execute SQL. A disagreement would give one artifact two origin digests, so
//     it is the first thing to check when this rail runs end to end.
//   * NOT PROVED, AND NOT CLAIMED: that any receipt admitted below is genuine.
//     Every fixture is synthetic. No issuance producer exists, which is why the
//     public verb refuses and why this suite asserts that it does.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { digest } from "../src/artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  JOURNEY_ONE_CLOCK_PROJECTION, JOURNEY_ONE_DEADLINE_CONTRACT, createJourneyOneClock,
} from "../src/journey-one-clock.v5.js";
import { journeyOneClockScopeKey } from "../src/journey-one-clock-store.v5.js";
import {
  BENCHMARK_COST_VARIANCE_THRESHOLDS, BENCHMARK_DEADLINE_CONTRACT, BENCHMARK_SLO_THRESHOLDS,
  MINIMUM_SAMPLES_PER_CELL, MINIMUM_WARMUP_RUNS, P95_AGGREGATION_METHOD,
  WORKLOAD_WEIGHT_TOTAL_BASIS_POINTS, benchmarkPayloadDigest,
} from "../src/benchmark-minimum.v5.js";
import {
  JOURNEY_ONE_MINIMUM_ACCEPTED_POLICY_FIELDS, JOURNEY_ONE_MINIMUM_ACCEPTED_SOURCE_FIELDS,
  JOURNEY_ONE_MINIMUM_ADMISSION_FIELDS, JOURNEY_ONE_MINIMUM_ADMISSION_INVARIANT_IDS,
  JOURNEY_ONE_MINIMUM_BENCHMARK_DERIVED_SOURCE_FIELDS,
  JOURNEY_ONE_MINIMUM_COMPOSE_FIELDS, JOURNEY_ONE_MINIMUM_INPUT_AUTHORITY_REQUIREMENT,
  JOURNEY_ONE_MINIMUM_INPUT_STORE_CANNOT_PROVE, JOURNEY_ONE_MINIMUM_INVENTORY_READBACK_SCHEMA,
  JOURNEY_ONE_MINIMUM_PROJECTION_INPUTS_SCHEMA, JOURNEY_ONE_MINIMUM_RECEIPT_IDENTITY_SEATS,
  JOURNEY_ONE_MINIMUM_TRUSTED_POLICY_SOURCE_FIELDS,
  createEphemeralJourneyOneMinimumAdmissionJournal, createJourneyOneClockMinimumInputStore,
  createJourneyOneClockProjectionComposer, createPostgresJourneyOneMinimumAdmissionJournal,
  journeyOneClockMinimumInputStoreIntegrationRequirements, journeyOneClockMinimumInputStoreTools,
  journeyOneMinimumAdmissionDigest, journeyOneMinimumBenchmarkAcceptedSources,
  journeyOneMinimumReceiptDigest,
} from "../src/journey-one-clock-input-store.v5.js";

// --- fixtures ---------------------------------------------------------------

const D = n => `sha256:${String(n).padStart(2, "0").repeat(32)}`;
const I = actor => ({ actor_id: actor, session_ref: `session:synthetic-${actor}`,
  authority_class: actor === "joe" || actor === "dell" ? "verified_partner" : "synthetic_oracle" });
const copy = x => JSON.parse(JSON.stringify(x));
const iso = ms => new Date(ms).toISOString();
const HOUR = 3600000;
const DAY = 24 * HOUR;
const ORIGIN = "2026-09-09T15:00:00.000Z";
const ORIGIN_MS = Date.parse(ORIGIN);
const EXPECTATION = { artifact_digest: D(7), fixture_set_digest: D(5) };
const MINIMUM_TTL_POLICY = 48 * HOUR;
const COMPLETION_TTL_POLICY = 72 * HOUR;
const SOURCE = "safe:a00:proposed-minimum-fixture";

/** One r7-exact consumer-gate-receipt.v1 from the minimum's own producer step. */
function minimum(observed = ORIGIN, evidence_ref = "safe:synthetic:min-evidence") {
  return {
    gate_id: "foundation-assurance-minimum-accepted",
    receipt_producer_step_ref: "step:foundation-assurance-minimum-receipt",
    subject_digest: D(1), candidate_digest: D(2), policy_digest: D(3),
    environment_manifest_digest: D(4),
    subject_environment: "candidate", evidence_scope: "candidate-and-test",
    subject_maker_identity: I("maker"), producer_identity: I("producer"),
    evaluator_identity: I("evaluator"),
    producer_role: "independent_foundation_assurance_minimum_oracle",
    independent_oracle_ref: "oracle:gate-producer:foundation-assurance-minimum",
    oracle_version: "1.0.0",
    evidence_ref, fixture_set_digest: D(5), observed_at: observed,
    ttl_expires_at: iso(Date.parse(observed) + 24 * HOUR), status: "pass",
    comparator: "synthetic-exact-comparator",
    negative_admission_result: "all_required_denials_observed",
  };
}
/** The terminus, in the exact shape the kernel's `completion` field takes. */
function completed(at) {
  const m = minimum(at);
  delete m.gate_id; delete m.receipt_producer_step_ref; delete m.environment_manifest_digest;
  return { gate_id: "journey-one-kernel-production-accepted",
    combiner: "all_current_exact_distinct_pass",
    obligation_decision_ids: ["Q002.D1", "Q014.D1", "Q123.D1"],
    receipts: [{ ...m,
      receipt_ref: "safe:receipt:journey-one-kernel-production",
      producer_step_ref: "step:j1-kernel-production-outcome",
      rollout_environment_manifest_digest: D(6), artifact_digest: D(7),
      subject_environment: "production", evidence_scope: "production",
      producer_role: "independent_journey_one_kernel_outcome_oracle",
      independent_oracle_ref: "oracle:rollout-component:journey-one-kernel-production" }] };
}

/**
 * THE AUTHORITATIVE SCOPE, as a trusted integration composes one: the accepted
 * binding's three digests and the two gate ids the deadline contract names.
 * Nothing in a request produces it; the store is constructed with it.
 */
const SCOPE = Object.freeze({
  benchmark_candidate_digest: D(2),
  benchmark_policy_digest: D(3),
  benchmark_subject_digest: D(1),
  clock_origin_gate_id: JOURNEY_ONE_DEADLINE_CONTRACT.clock_origin_gate_id,
  clock_terminus_gate_id: JOURNEY_ONE_DEADLINE_CONTRACT.clock_terminus_gate_id,
  scope_ref: "safe:clock-scope:synthetic-journey-one",
  tenant: ORGANIZATION_TENANT_ID,
});
/** The SAME accepted scope under another name: identical identity, new label. */
const RELABELLED_SCOPE = Object.freeze({ ...SCOPE,
  scope_ref: "safe:clock-scope:synthetic-journey-one-renamed" });
/** A different accepted scope: another program, another inventory. */
const OTHER_SCOPE = Object.freeze({ ...SCOPE,
  benchmark_subject_digest: D(11), scope_ref: "safe:clock-scope:synthetic-journey-one-other" });

/** The accepted minimum-input policy, a construction-time trusted binding. */
const POLICY = Object.freeze({
  maximum_minimum_receipt_ttl_ms: MINIMUM_TTL_POLICY,
  minimum_environment_manifest_digest: D(4),
});

/**
 * THE ACCEPTED BENCHMARK MANIFEST THIS SUITE'S PROJECTIONS ARE ABOUT.
 *
 * It is a real, valid benchmark-manifest.v1 for this suite's own accepted scope,
 * composed by this file exactly as a trusted integration composes a real one —
 * and it is SYNTHETIC. validateBenchmarkManifest reads the shape of an
 * acceptance envelope and authenticates nobody, so nothing below is evidence
 * that a verified partner accepted anything.
 */
const BENCHMARK_ACCEPTED_AT = iso(ORIGIN_MS - HOUR);
function benchmarkPayload() {
  return {
    subject_digest: D(1), candidate_digest: D(2), policy_digest: D(3),
    capacity_profiles: ["baseline"],
    workload_mix: [{ workload_id: "search",
      weight_basis_points: WORKLOAD_WEIGHT_TOTAL_BASIS_POINTS, operation_mix_digest: D(10) }],
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
    samples_per_cell: MINIMUM_SAMPLES_PER_CELL,
    warmup_runs: MINIMUM_WARMUP_RUNS,
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
function benchmarkManifest(overrides = {}) {
  const body = benchmarkPayload();
  return { ...copy(body), benchmark_manifest_digest: benchmarkPayloadDigest(body),
    accepted_by_identity: I("joe"), accepted_at: BENCHMARK_ACCEPTED_AT,
    status: "accepted", ...overrides };
}
/**
 * The five accepted source bindings the composer is constructed with. THE THREE
 * ACCEPTANCE-ENVELOPE FIELDS ARE DERIVED from the manifest above rather than
 * written out as literals; the two policy fields appear on no benchmark manifest
 * at all, so deriving them would mint a binding and they stay trusted inputs.
 */
const SOURCES = Object.freeze({
  ...journeyOneMinimumBenchmarkAcceptedSources(benchmarkManifest()),
  maximum_completion_receipt_ttl_ms: COMPLETION_TTL_POLICY,
  production_environment_manifest_digest: D(6),
});
/**
 * Every composer in this suite is constructed with the accepted manifest its
 * envelope was derived from, because the constructor requires it.
 */
function composerFor(store, overrides = {}) {
  return createJourneyOneClockProjectionComposer({
    store, accepted_sources: SOURCES, benchmark_manifest: benchmarkManifest(), ...overrides });
}

const ACTOR = { slug: "claude", human: false, sponsoring_human_slug: "joe" };
let keySerial = 0;
const key = () => `00000000-0000-4000-8000-${(++keySerial).toString(16).padStart(12, "0")}`;

/** A store over a fresh reference journal with a controllable server clock. */
function newStore({ at = ORIGIN_MS, clock_scope = SCOPE, accepted_minimum_policy = POLICY,
  actor = ACTOR, journal } = {}) {
  const clock = { ms: at };
  const j = journal ?? createEphemeralJourneyOneMinimumAdmissionJournal({ now: () => clock.ms });
  return { clock, journal: j, store: createJourneyOneClockMinimumInputStore({
    journal: j, actor, clock_scope, accepted_minimum_policy }) };
}
function admit(store, receipt, expected_prior_admission_digest, extra = {}) {
  return store.admit({ receipt, expected_prior_admission_digest, idempotency_key: key(),
    claimed_receipt_digest: null, source_ref: SOURCE, ...extra });
}
async function refusal(promise) {
  try { await promise; } catch (error) { return error; }
  throw new Error("expected a refusal");
}

/**
 * A trusted, TEST-INSTALLED authenticating verifier. The evidence table is
 * installed by test code exactly as a real one would be installed by server
 * code: no JSON flag can cause an unknown envelope to acquire a projection, and
 * nothing here constructs a verifier from data. Composing a projection is not
 * authenticating it, which is why this exists separately from the composer.
 */
function harness() {
  const evidence = new Map(); let serial = 0;
  const clock = createJourneyOneClock({ verifySnapshot(envelope) {
    const found = evidence.get(digest(envelope));
    if (!found) throw new Error("synthetic-authentication-refused");
    return { envelope_digest: digest(envelope), snapshot: copy(found) };
  } });
  return { clock, evaluate(projection) {
    const envelope = { synthetic_receipt_ref: `test-${++serial}` };
    evidence.set(digest(envelope), copy(projection));
    return clock.evaluate(envelope);
  } };
}
/** Admit one receipt, compose the projection over the STORED rows, evaluate it. */
async function evaluateStored(store, compose = {}) {
  const composer = composerFor(store);
  const composed = await composer.compose({ as_of: ORIGIN, completion: null,
    completion_expectation: copy(EXPECTATION), pauses: [], amendments: [], history: null,
    ...compose });
  return { composed, result: harness().evaluate(composed.projection) };
}

const CANDIDATE_SQL = readFileSync(
  fileURLToPath(new URL("../../ops/journey-one-clock-input-store.candidate.sql", import.meta.url)),
  "utf8");
const PROOF_SQL = readFileSync(
  fileURLToPath(new URL("./journey-one-clock-input-store-postgres.sql", import.meta.url)), "utf8");

/**
 * The TOP-LEVEL arguments of one SQL call, split on commas at depth one, so a
 * reader can assert what a jsonb_build_object EMITS rather than what the text
 * around it mentions. Nested calls keep their own commas; string literals in the
 * text this is used on contain none.
 */
function topLevelArguments(text, from) {
  const args = [];
  let depth = 0, current = "";
  for (let i = text.indexOf("(", from); i < text.length; i += 1) {
    const character = text[i];
    if (character === "(") {
      depth += 1;
      if (depth === 1) continue;   // the call's own opening parenthesis
    } else if (character === ")") {
      depth -= 1;
      if (depth === 0) { args.push(current.trim()); break; }
    } else if (character === "," && depth === 1) {
      args.push(current.trim()); current = ""; continue;
    }
    current += character;
  }
  return args;
}

// --- 1. a valid admission, read back, and evaluated by the real kernel -------

test("an admitted minimum is stored, read back, and evaluated by the real kernel", async () => {
  const { store } = newStore();
  const receipt = minimum();
  const row = await admit(store, receipt, null);

  assert.equal(row.replayed, false);
  assert.equal(row.admission_ordinal, 0);
  assert.equal(row.previous_admission_digest, null);
  // The admission instant is the RECORD LAYER'S clock, not the receipt's.
  assert.equal(row.admitted_at, ORIGIN);
  assert.equal(row.receipt_digest, journeyOneMinimumReceiptDigest(receipt));
  // And it is the kernel's own digest of the artifact, which is what makes it
  // the origin_receipt_digest a clock seals.
  assert.equal(row.receipt_digest, digest(receipt));
  assert.equal(row.provenance.input_authority,
    "trusted_admission_not_independently_verified_by_this_record_layer");
  assert.equal(row.admission_digest, journeyOneMinimumAdmissionDigest({
    admitted_at: ORIGIN, clock_scope_key: journeyOneClockScopeKey(SCOPE),
    minimum_environment_manifest_digest: D(4),
    minimum_receipt_ttl_policy_ms: MINIMUM_TTL_POLICY,
    previous_admission_digest: null, receipt_digest: row.receipt_digest,
    tenant: ORGANIZATION_TENANT_ID }));

  const inventory = await store.read();
  assert.equal(inventory.exists, true);
  assert.equal(inventory.admission_count, 1);
  assert.equal(inventory.head_admission_digest, row.admission_digest);
  // THE KERNEL'S OWN SHAPE, assembled from the stored rows.
  assert.deepEqual(inventory.minimum_history, [{ admitted_at: ORIGIN, receipt: copy(receipt) }]);
  assert.deepEqual(inventory.record_layer_cannot_prove,
    [...JOURNEY_ONE_MINIMUM_INPUT_STORE_CANNOT_PROVE]);
  assert.equal(inventory.gate_admitted_by_record_layer, false);

  const { composed, result } = await evaluateStored(store);
  assert.equal(composed.schema_version, JOURNEY_ONE_MINIMUM_PROJECTION_INPUTS_SCHEMA);
  assert.equal(composed.authenticated, false);
  assert.equal(composed.trusted_verifier_still_required, true);
  assert.equal(composed.projection.schema_version, JOURNEY_ONE_CLOCK_PROJECTION);
  assert.equal(result.state.origin_receipt_digest, row.receipt_digest);
  assert.equal(result.state.origin_at, ORIGIN);
  assert.equal(result.state.status, "running");
  assert.equal(result.deadline_success, false);
  assert.equal(result.replan_required, false);
});

test("the composer reads the binding from the accepted sources, never from the receipt", async () => {
  const { store } = newStore();
  await admit(store, minimum(), null);
  const { composed } = await evaluateStored(store);
  const { binding, benchmark } = composed.projection;

  // The three digests come from the SCOPE this store was constructed for.
  assert.equal(binding.subject_digest, SCOPE.benchmark_subject_digest);
  assert.equal(binding.candidate_digest, SCOPE.benchmark_candidate_digest);
  assert.equal(binding.policy_digest, SCOPE.benchmark_policy_digest);
  assert.equal(benchmark.subject_digest, SCOPE.benchmark_subject_digest);
  // The TTL policy and environment manifest come from the SEALED inventory.
  assert.equal(binding.maximum_minimum_receipt_ttl_ms, MINIMUM_TTL_POLICY);
  assert.equal(binding.minimum_environment_manifest_digest, D(4));
  // The deadline contract is the kernel's own constant.
  assert.deepEqual(benchmark.deadline_contract, copy(JOURNEY_ONE_DEADLINE_CONTRACT));
  // And neither the TTL policy nor the scope digests can be supplied per call or
  // per composer: there is no field for them anywhere on either surface.
  for (const field of ["maximum_minimum_receipt_ttl_ms", "minimum_environment_manifest_digest",
    "subject_digest", "candidate_digest", "policy_digest"]) {
    assert.equal(JOURNEY_ONE_MINIMUM_ACCEPTED_SOURCE_FIELDS.includes(field), false);
    assert.equal(JOURNEY_ONE_MINIMUM_COMPOSE_FIELDS.includes(field), false);
  }
});

// --- 2. the admission instant --------------------------------------------------

test("admitted_at is the server's and is never a caller field", async () => {
  const { store } = newStore();
  const error = await refusal(store.admit({ receipt: minimum(),
    expected_prior_admission_digest: null, idempotency_key: key(),
    claimed_receipt_digest: null, source_ref: SOURCE, admitted_at: ORIGIN }));
  assert.equal(error.code, "closed_shape");
  // And the schema of the (refusing) public verb has no property for it either.
  const tools = journeyOneClockMinimumInputStoreTools({ withEnvelope: null, ToolError: Error });
  const properties = tools["admit-journey-one-minimum-receipt"].inputSchema.properties;
  assert.equal(Object.hasOwn(properties, "admitted_at"), false);
});

test("a receipt observed after the server admitted it is refused, not stored", async () => {
  // One millisecond of skew. The kernel refuses this FATALLY, and an append-only
  // inventory could never shed the row.
  const { store } = newStore({ at: ORIGIN_MS });
  const error = await refusal(admit(store, minimum(iso(ORIGIN_MS + 1)), null));
  assert.equal(error.code, "minimum_admission_precedes_observation");
  assert.equal(error.detail.invariant, "j1_minimum_admission_not_before_observation");
  assert.equal((await store.read()).exists, false);
});

test("a window longer than the sealed accepted policy is refused, not stored", async () => {
  const { store } = newStore();
  const overlong = { ...minimum(), ttl_expires_at: iso(ORIGIN_MS + 400 * DAY) };
  const error = await refusal(admit(store, overlong, null));
  assert.equal(error.code, "minimum_receipt_ttl_policy_exceeded");
  assert.equal((await store.read()).exists, false);
});

// --- 3. idempotency, replay and the compare-and-swap ---------------------------

test("one idempotency key replays one admission and refuses a changed payload", async () => {
  const { store } = newStore();
  const receipt = minimum();
  const idempotency_key = key();
  const first = await store.admit({ receipt, expected_prior_admission_digest: null,
    idempotency_key, claimed_receipt_digest: null, source_ref: SOURCE });
  const replay = await store.admit({ receipt, expected_prior_admission_digest: null,
    idempotency_key, claimed_receipt_digest: null, source_ref: SOURCE });
  assert.equal(replay.replayed, true);
  assert.equal(replay.admission_digest, first.admission_digest);
  assert.equal((await store.read()).admission_count, 1);

  const error = await refusal(store.admit({
    receipt: minimum(ORIGIN, "safe:synthetic:min-evidence-two"),
    expected_prior_admission_digest: null, idempotency_key,
    claimed_receipt_digest: null, source_ref: SOURCE }));
  assert.equal(error.code, "minimum_idempotency_key_reused");
});

test("re-presenting one artifact under a new instant is a replay, not a second admission", async () => {
  const { store, clock } = newStore();
  const receipt = minimum();
  await admit(store, receipt, null);
  const head = (await store.read()).head_admission_digest;
  clock.ms = ORIGIN_MS + HOUR;
  const error = await refusal(admit(store, receipt, head));
  assert.equal(error.code, "minimum_receipt_already_admitted");
  assert.equal(error.detail.invariant, "j1_minimum_receipt_never_readmitted");
});

test("the compare-and-swap names the exact head, and an omitted token refuses", async () => {
  const { store, clock } = newStore();
  const missing = await refusal(store.admit({ receipt: minimum(), idempotency_key: key(),
    claimed_receipt_digest: null, source_ref: SOURCE }));
  assert.equal(missing.code, "closed_shape");

  // The key present with no value is the same caller: they have not said what
  // they are appending to, and there is no default that would guess for them.
  const undefinedToken = await refusal(store.admit({ receipt: minimum(),
    expected_prior_admission_digest: undefined, idempotency_key: key(),
    claimed_receipt_digest: null, source_ref: SOURCE }));
  assert.equal(undefinedToken.code, "missing_prior_admission_digest");

  const early = await refusal(admit(store, minimum(), D(9)));
  assert.equal(early.code, "minimum_prior_admission_unknown");

  const first = await admit(store, minimum(), null);
  const second = await refusal(admit(store, minimum(ORIGIN, "safe:synthetic:min-two"), null));
  assert.equal(second.code, "minimum_inventory_already_open");

  clock.ms = ORIGIN_MS + HOUR;
  const stale = await refusal(admit(store, minimum(ORIGIN, "safe:synthetic:min-three"), D(9)));
  assert.equal(stale.code, "minimum_stale_prior_admission_digest");

  const next = await admit(store, minimum(ORIGIN, "safe:synthetic:min-four"),
    first.admission_digest);
  assert.equal(next.admission_ordinal, 1);
  assert.equal(next.previous_admission_digest, first.admission_digest);
});

test("two concurrent admissions against one head: exactly one lands", async () => {
  const { store, clock } = newStore();
  const first = await admit(store, minimum(), null);
  clock.ms = ORIGIN_MS + HOUR;
  const settled = await Promise.allSettled([
    admit(store, minimum(ORIGIN, "safe:synthetic:race-a"), first.admission_digest),
    admit(store, minimum(ORIGIN, "safe:synthetic:race-b"), first.admission_digest),
  ]);
  assert.equal(settled.filter(s => s.status === "fulfilled").length, 1);
  assert.equal(settled.find(s => s.status === "rejected").reason.code,
    "minimum_stale_prior_admission_digest");
  assert.equal((await store.read()).admission_count, 2);
});

test("two concurrent openings of one inventory: exactly one lands", async () => {
  const { store } = newStore();
  const settled = await Promise.allSettled([
    admit(store, minimum(ORIGIN, "safe:synthetic:open-a"), null),
    admit(store, minimum(ORIGIN, "safe:synthetic:open-b"), null),
  ]);
  assert.equal(settled.filter(s => s.status === "fulfilled").length, 1);
  assert.equal(settled.find(s => s.status === "rejected").reason.code,
    "minimum_inventory_already_open");
});

// --- 4. the first origin never moves ------------------------------------------

test("an admission dated before the head is refused", async () => {
  const { store, clock } = newStore();
  const first = await admit(store, minimum(), null);
  clock.ms = ORIGIN_MS - 1000;
  const error = await refusal(admit(store, minimum(iso(ORIGIN_MS - 2000), "safe:synthetic:back"),
    first.admission_digest));
  assert.equal(error.code, "minimum_admission_out_of_order");
  assert.equal(error.detail.invariant, "j1_minimum_first_origin_never_replaced");
});

/** The two same-instant candidates, ordered by the digest the kernel sorts on. */
function tiePair(a = "safe:synthetic:tie-a", b = "safe:synthetic:tie-b") {
  return [a, b].map(ref => minimum(ORIGIN, ref))
    .sort((x, y) => journeyOneMinimumReceiptDigest(x) < journeyOneMinimumReceiptDigest(y) ? -1 : 1);
}

test("within one admission instant the receipt digest strictly increases", async () => {
  // The kernel orders candidates by (admitted_at, receipt_digest), so the one
  // thing a later same-instant admission may not do is arrive with a digest that
  // sorts at or before the head's.
  const [lower, higher] = tiePair();

  const blocked = newStore();
  const first = await admit(blocked.store, higher, null);
  const error = await refusal(admit(blocked.store, lower, first.admission_digest));
  assert.equal(error.code, "minimum_admission_would_rebase_origin");
  assert.equal(error.detail.invariant, "j1_minimum_first_origin_never_replaced");
  assert.equal(error.detail.head_receipt_digest, first.receipt_digest);

  // And it is not a blanket ban on same-instant admissions: the ordinary
  // direction extends the stored order and is admitted.
  const allowed = newStore();
  const opening = await admit(allowed.store, lower, null);
  const next = await admit(allowed.store, higher, opening.admission_digest);
  assert.equal(next.admission_ordinal, 1);
  const { result } = await evaluateStored(allowed.store);
  assert.equal(result.state.origin_receipt_digest, opening.receipt_digest);
});

test("REGRESSION: a failed attempt first does not let a later same-instant lower digest take the origin", async () => {
  // THE DEFECT THIS GUARDS, reproduced end to end. An earlier revision compared
  // a newcomer against admissions[0] -- the first LEDGER ROW -- but the kernel
  // skips inadmissible attempts and selects the first ELIGIBLE row, so the two
  // are not the same row and the comparison protected the wrong one.
  const { store, clock } = newStore();

  // T0: a valid-shaped attempt that did not pass. The kernel skips it; this rail
  // stores it, because a failed attempt is real history.
  const failed = { ...minimum(), status: "fail", evidence_ref: "safe:synthetic:t0-failed" };
  const row0 = await admit(store, failed, null);

  // T1, one second later: a passing attempt with the HIGHER of two digests.
  clock.ms = ORIGIN_MS + 1000;
  const at = iso(ORIGIN_MS + 1000);
  const [lower, higher] = tiePair("safe:synthetic:t1-a", "safe:synthetic:t1-b");
  const row1 = await admit(store, higher, row0.admission_digest);

  // The kernel's origin is the T1 row, NOT the first row in the ledger.
  const composer = composerFor(store);
  const evaluator = harness();
  const base = { completion: null, completion_expectation: copy(EXPECTATION),
    pauses: [], amendments: [], history: null };
  const running = evaluator.evaluate((await composer.compose({ ...base, as_of: at })).projection);
  assert.equal(running.state.origin_receipt_digest, row1.receipt_digest);
  assert.notEqual(running.state.origin_receipt_digest, row0.receipt_digest);

  // THE ROW THAT USED TO LAND: same instant as the head, lower digest. It ties
  // nothing against the T0 row a first-ledger-row comparison looks at.
  const error = await refusal(admit(store, lower, row1.admission_digest));
  assert.equal(error.code, "minimum_admission_would_rebase_origin");
  assert.equal(error.detail.invariant, "j1_minimum_first_origin_never_replaced");
  assert.equal(error.detail.head_receipt_digest, row1.receipt_digest);

  // WHY IT MATTERS, PROVED AGAINST THE REAL KERNEL RATHER THAN ASSERTED: had it
  // landed, the very next evaluation carrying the retained history refuses, and
  // the clock is unreadable rather than wrong. An append-only inventory cannot
  // shed the row, so the refusal has to happen at admission or not at all.
  const wouldHaveLanded = copy((await composer.compose({ ...base, as_of: at })).projection);
  wouldHaveLanded.minimum_history.push({ admitted_at: at, receipt: copy(lower) });
  wouldHaveLanded.history = copy(running.state);
  assert.throws(() => evaluator.evaluate(wouldHaveLanded),
    thrown => thrown.name === "JourneyOneClockError" && thrown.code === "origin_reset_or_rebase");

  // The inventory the store actually holds is unchanged and still evaluates,
  // with its origin and its retained history intact.
  const stored = await store.read();
  assert.equal(stored.admission_count, 2);
  assert.deepEqual(stored.minimum_history.map(a => a.receipt.evidence_ref),
    ["safe:synthetic:t0-failed", higher.evidence_ref]);
  const again = evaluator.evaluate((await composer.compose({
    ...base, as_of: at, history: copy(running.state) })).projection);
  assert.equal(again.state.origin_receipt_digest, row1.receipt_digest);
  assert.equal(again.state.status, "running");
});

test("a readback out of the kernel's selection order refuses rather than being served", async () => {
  // The write path compares against the head only, which is sound by induction.
  // A row that reached the tables by another route breaks exactly that
  // induction, so the read re-checks the whole sequence.
  const { store } = newStore();
  const [lower, higher] = tiePair("safe:synthetic:order-a", "safe:synthetic:order-b");
  const a = await admit(store, lower, null);
  const b = await admit(store, higher, a.admission_digest);
  const rows = [copy(a), copy(b)].map(row => { delete row.replayed; return row; });

  const straight = createJourneyOneClockMinimumInputStore({ journal: stubJournal(relink(rows)),
    actor: ACTOR, clock_scope: SCOPE, accepted_minimum_policy: POLICY });
  assert.equal((await straight.read()).admission_count, 2);

  // The same two rows, same instant, swapped so the digests descend. Ordinals
  // and chain links are recomputed, so ONLY the order is wrong.
  const swapped = createJourneyOneClockMinimumInputStore({
    journal: stubJournal(relink([rows[1], rows[0]])),
    actor: ACTOR, clock_scope: SCOPE, accepted_minimum_policy: POLICY });
  const error = await refusal(swapped.read());
  assert.equal(error.code, "minimum_admission_out_of_order");
  assert.equal(error.detail.invariant, "j1_minimum_first_origin_never_replaced");
});

// --- 5. the sealed scope and accepted policy ----------------------------------

test("the scope and the accepted policy are construction-time and required for a write", async () => {
  const unscoped = createJourneyOneClockMinimumInputStore({
    journal: createEphemeralJourneyOneMinimumAdmissionJournal(), actor: ACTOR,
    accepted_minimum_policy: POLICY });
  const noScope = await refusal(admit(unscoped, minimum(), null));
  assert.equal(noScope.code, "minimum_inventory_binding_required");
  assert.equal(noScope.detail.scope_bound, false);

  const unpoliced = createJourneyOneClockMinimumInputStore({
    journal: createEphemeralJourneyOneMinimumAdmissionJournal(), actor: ACTOR,
    clock_scope: SCOPE });
  const noPolicy = await refusal(admit(unpoliced, minimum(), null));
  assert.equal(noPolicy.code, "minimum_inventory_binding_required");
  assert.equal(noPolicy.detail.policy_bound, false);
});

test("a second store presenting another accepted policy for one inventory refuses", async () => {
  const { store, journal, clock } = newStore();
  const first = await admit(store, minimum(), null);
  const widened = createJourneyOneClockMinimumInputStore({ journal, actor: ACTOR,
    clock_scope: SCOPE,
    accepted_minimum_policy: { ...POLICY, maximum_minimum_receipt_ttl_ms: 90 * DAY } });
  clock.ms = ORIGIN_MS + HOUR;
  const error = await refusal(widened.admit({ receipt: minimum(ORIGIN, "safe:synthetic:widened"),
    expected_prior_admission_digest: first.admission_digest, idempotency_key: key(),
    claimed_receipt_digest: null, source_ref: SOURCE }));
  assert.equal(error.code, "minimum_accepted_policy_changed");
  assert.equal(error.detail.invariant, "j1_minimum_inventory_policy_sealed");
});

test("a receipt for another accepted subject is refused rather than widening the inventory", async () => {
  const { store } = newStore();
  const error = await refusal(admit(store, { ...minimum(), subject_digest: D(11) }, null));
  assert.equal(error.code, "minimum_receipt_scope_mismatch");
  assert.equal(error.detail.field, "subject_digest");

  const environment = await refusal(admit(store,
    { ...minimum(), environment_manifest_digest: D(12) }, null));
  assert.equal(environment.code, "minimum_receipt_environment_mismatch");
});

test("one accepted scope under two names is one inventory, and the label is sealed", async () => {
  const { store, journal, clock } = newStore();
  assert.equal(journeyOneClockScopeKey(RELABELLED_SCOPE), journeyOneClockScopeKey(SCOPE));
  assert.notEqual(journeyOneClockScopeKey(OTHER_SCOPE), journeyOneClockScopeKey(SCOPE));

  const first = await admit(store, minimum(), null);
  const relabelled = createJourneyOneClockMinimumInputStore({ journal, actor: ACTOR,
    clock_scope: RELABELLED_SCOPE, accepted_minimum_policy: POLICY });
  clock.ms = ORIGIN_MS + HOUR;
  // A rename does not open a second inventory: it meets the one that exists,
  // and then the sealed label refuses.
  const error = await refusal(relabelled.admit({
    receipt: minimum(ORIGIN, "safe:synthetic:relabelled"),
    expected_prior_admission_digest: first.admission_digest, idempotency_key: key(),
    claimed_receipt_digest: null, source_ref: SOURCE }));
  assert.equal(error.code, "minimum_scope_label_changed");
  assert.equal(error.detail.invariant, "j1_minimum_scope_label_is_not_identity");
  assert.equal((await store.read()).admission_count, 1);
});

// --- 6. the wrong producer ------------------------------------------------------

test("a receipt from another producer, gate, role, oracle or scope is refused", async () => {
  const { store } = newStore();
  for (const mutation of [
    { gate_id: "journey-one-kernel-production-accepted" },
    { receipt_producer_step_ref: "step:global-no-phi-boundary-independent-receipt" },
    { producer_role: "independent_secret_boundary_oracle" },
    { independent_oracle_ref: "oracle:gate-producer:global-secrets-boundary" },
    { oracle_version: "2.0.0" },
    { evidence_scope: "production" },
    { subject_environment: "production" },
  ]) {
    const error = await refusal(admit(store, { ...minimum(), ...mutation }, null));
    assert.equal(error.code, "wrong_minimum_receipt_producer", JSON.stringify(mutation));
    assert.equal(error.detail.invariant, "j1_minimum_receipt_producer_bound");
  }
  // A rollout-component receipt — the TERMINUS — is not a minimum receipt, and
  // A00's own seam validator says so before this rail says anything.
  const terminus = completed(ORIGIN).receipts[0];
  const error = await refusal(admit(store, terminus, null));
  assert.equal(error.name, "BenchmarkMinimumError");
  assert.equal(error.code, "closed_shape");
  assert.equal((await store.read()).exists, false);
});

test("a receipt the kernel could not READ is refused at admission, not stored", async () => {
  // Each of these is a well-formed twenty-one-field receipt from the right
  // producer that journeyOneClockMinimumReceiptView admits -- it checks
  // JSON-safety, the closed keys and M01's two value domains, and not these --
  // and that the kernel refuses FATALLY. One stored row would make every later
  // evaluation of the inventory throw, and an append-only inventory cannot shed
  // it, so the refusal has to happen here or not at all.
  const { store } = newStore();
  const seat = { ...I("producer") };
  for (const [code, mutation] of [
    ["minimum_receipt_invalid_reference", { evidence_ref: "notsafe:synthetic:wrong-prefix" }],
    ["minimum_receipt_invalid_digest", { fixture_set_digest: "not-a-digest" }],
    ["minimum_receipt_invalid_comparator", { comparator: "x" }],
    ["minimum_receipt_invalid_comparator", { comparator: "c".repeat(301) }],
    // A fourth key that is NOT an authority fragment, so this case reaches the
    // seat-shape clause rather than assertNoSelfAssertedAuthority.
    ["minimum_receipt_invalid_identity", { producer_identity: { ...seat, display_name: "x" } }],
    ["minimum_receipt_invalid_identity", { producer_identity: { ...seat, actor_id: "" } }],
    ["minimum_receipt_invalid_identity", { producer_identity: { ...seat, authority_class: "" } }],
    ["minimum_receipt_invalid_identity",
      { producer_identity: { ...seat, session_ref: "notsession:synthetic" } }],
    ["minimum_receipt_invalid_identity",
      { evaluator_identity: { actor_id: "e", session_ref: "session:e" } }],
  ]) {
    const error = await refusal(admit(store, { ...minimum(), ...mutation }, null));
    assert.equal(error.code, code, JSON.stringify(mutation));
    assert.equal(error.detail.invariant, "j1_minimum_receipt_readable_by_kernel");
  }
  assert.equal((await store.read()).exists, false);

  // AND THE KERNEL REALLY DOES REFUSE THEM FATALLY, proved rather than asserted:
  // an inventory carrying one is not skipped over, it is unreadable.
  const clean = newStore();
  await admit(clean.store, minimum(), null);
  const composer = composerFor(clean.store);
  const poisoned = copy((await composer.compose({ as_of: ORIGIN, completion: null,
    completion_expectation: copy(EXPECTATION), pauses: [], amendments: [],
    history: null })).projection);
  poisoned.minimum_history.push({ admitted_at: ORIGIN,
    receipt: { ...minimum(ORIGIN, "safe:synthetic:poison"), comparator: "x" } });
  assert.throws(() => harness().evaluate(poisoned),
    thrown => thrown.name === "JourneyOneClockError" && thrown.code === "invalid_comparator");
});

test("the comparator bound is UTF-16 code units, and the accepted domain is preserved", async () => {
  // U+1D11E: one codepoint, TWO UTF-16 code units. It is what makes the two
  // possible rulers give different answers, in BOTH directions -- which is why
  // the accepted half is asserted here and not only the refused half.
  const astral = "\u{1D11E}";
  assert.equal(astral.length, 2);
  assert.equal([...astral].length, 1);

  for (const [comparator, units] of [[astral.repeat(150), 300], [astral.repeat(3), 6],
    ["c".repeat(300), 300], ["exact", 5]]) {
    assert.equal(comparator.length, units);
    const accepted = newStore();
    const row = await admit(accepted.store, { ...minimum(), comparator }, null);
    assert.equal(row.admission_ordinal, 0, `${units} units should be admitted`);
    // And the real kernel agrees it is readable.
    const { result } = await evaluateStored(accepted.store);
    assert.equal(result.state.status, "running");
  }

  for (const [comparator, units] of [[astral.repeat(151), 302], [astral.repeat(2), 4],
    ["c".repeat(301), 301], ["four", 4]]) {
    assert.equal(comparator.length, units);
    const error = await refusal(admit(newStore().store, { ...minimum(), comparator }, null));
    assert.equal(error.code, "minimum_receipt_invalid_comparator", `${units} units`);
    assert.equal(error.detail.length, units);
  }
});

test("seat INDEPENDENCE is not re-judged here, and the rail says so rather than implying it", async () => {
  // Shape is checked; the relationship between seats is not. It is fatal in the
  // kernel like the rest, so it is disclosed instead of silently absent.
  const { store } = newStore();
  const selfAttested = { ...minimum(), producer_identity: copy(I("maker")) };
  const row = await admit(store, selfAttested, null);
  assert.equal(row.admission_ordinal, 0);
  const disclosure = JOURNEY_ONE_MINIMUM_INPUT_STORE_CANNOT_PROVE.find(
    line => line.includes("INDEPENDENT"));
  assert.ok(disclosure, "seat independence is not disclosed");
  assert.ok(disclosure.includes("self_attestation"));
  // And the kernel is what refuses it, by that name, over this stored row.
  const composer = composerFor(store);
  const projection = (await composer.compose({ as_of: ORIGIN, completion: null,
    completion_expectation: copy(EXPECTATION), pauses: [], amendments: [],
    history: null })).projection;
  assert.throws(() => harness().evaluate(projection),
    thrown => thrown.name === "JourneyOneClockError" && thrown.code === "self_attestation");
});

test("a caller-asserted authority claim beside a legitimate write is refused by name", async () => {
  const { store } = newStore();
  const error = await refusal(store.admit({ receipt: minimum(),
    expected_prior_admission_digest: null, idempotency_key: key(),
    claimed_receipt_digest: null, source_ref: SOURCE, verified: true }));
  assert.equal(error.name, "BenchmarkAcceptanceStoreError");
  assert.equal(error.code, "self_asserted_authority_refused");
});

test("a claimed receipt digest is only ever compared", async () => {
  const { store } = newStore();
  const error = await refusal(admit(store, minimum(), null, { claimed_receipt_digest: D(13) }));
  assert.equal(error.code, "claimed_receipt_digest_mismatch");
  assert.equal(error.detail.invariant, "j1_minimum_claimed_digest_is_never_trusted");
  const ok = await admit(store, minimum(), null,
    { claimed_receipt_digest: journeyOneMinimumReceiptDigest(minimum()) });
  assert.equal(ok.admission_ordinal, 0);
});

// --- 7. nothing is silently discarded -------------------------------------------

test("a non-passing attempt and a lapsed attempt are stored, and the kernel skips them", async () => {
  const { store, clock } = newStore();
  const failed = { ...minimum(), status: "fail", evidence_ref: "safe:synthetic:failed-attempt" };
  const opening = await admit(store, failed, null);

  clock.ms = ORIGIN_MS + HOUR;
  const passing = minimum(ORIGIN, "safe:synthetic:passing-attempt");
  const second = await admit(store, passing, opening.admission_digest);

  // A third attempt whose window had already lapsed when it was admitted: the
  // kernel calls that an ordinary fact of the ledger, so it is stored.
  clock.ms = ORIGIN_MS + 10 * DAY;
  const lapsed = minimum(iso(ORIGIN_MS + 2 * DAY), "safe:synthetic:lapsed-attempt");
  await admit(store, lapsed, second.admission_digest);

  const inventory = await store.read();
  assert.equal(inventory.admission_count, 3);
  assert.deepEqual(inventory.minimum_history.map(a => a.receipt.evidence_ref),
    ["safe:synthetic:failed-attempt", "safe:synthetic:passing-attempt",
      "safe:synthetic:lapsed-attempt"]);

  // The kernel reads all three and selects the first ADMISSIBLE one by admission
  // order. Nothing was dropped to get there.
  const { result } = await evaluateStored(store, { as_of: iso(ORIGIN_MS + 10 * DAY) });
  assert.equal(result.state.origin_receipt_digest, second.receipt_digest);
  assert.equal(result.state.origin_at, ORIGIN);
});

// --- 8. the exact history survives a miss, a replan and a late completion -------

test("the stored inventory is byte-identical across running, missed and late completion", async () => {
  const { store, clock } = newStore();
  const opening = await admit(store, minimum(), null);
  const before = await store.read();

  const composer = composerFor(store);
  const evaluator = harness();
  const compose = async args => (await composer.compose({ completion: null,
    completion_expectation: copy(EXPECTATION), pauses: [], amendments: [], history: null,
    ...args })).projection;

  const running = evaluator.evaluate(await compose({ as_of: ORIGIN }));
  assert.equal(running.state.status, "running");

  const lateAt = iso(ORIGIN_MS + 40 * DAY);
  const missed = evaluator.evaluate(await compose({ as_of: lateAt, history: copy(running.state) }));
  assert.equal(missed.state.status, "missed");
  assert.equal(missed.replan_required, true);
  assert.equal(missed.missing_evidence_miss_recorded, true);

  const late = evaluator.evaluate(await compose({ as_of: lateAt, history: copy(missed.state),
    completion: completed(lateAt) }));
  assert.equal(late.state.status, "completed_late");
  assert.equal(late.deadline_success, false);
  assert.equal(late.replan_required, true);
  // The origin and the elapsed history are preserved, exactly as Q008.D1 requires.
  assert.equal(late.state.origin_receipt_digest, opening.receipt_digest);
  assert.equal(late.state.miss_at, missed.state.miss_at);

  // AND THE INPUT HISTORY NEVER MOVED. Three evaluations, one inventory.
  const after = await store.read();
  assert.deepEqual(after.minimum_history, before.minimum_history);
  assert.equal(after.head_admission_digest, before.head_admission_digest);
  assert.deepEqual(after.admissions, before.admissions);

  // A LATER ADMISSION AFTER THE MISS STILL DOES NOT MOVE THE ORIGIN: it appends
  // honestly, the first row is untouched, and the clock keeps its origin.
  clock.ms = ORIGIN_MS + 41 * DAY;
  await admit(store, minimum(iso(ORIGIN_MS + 40 * DAY), "safe:synthetic:post-miss"),
    opening.admission_digest);
  const grown = await store.read();
  assert.equal(grown.admission_count, 2);
  assert.deepEqual(grown.minimum_history[0], before.minimum_history[0]);
  assert.deepEqual(grown.admissions[0], before.admissions[0]);

  const stillLate = evaluator.evaluate(await compose({ as_of: iso(ORIGIN_MS + 41 * DAY),
    history: copy(late.state), completion: completed(lateAt) }));
  assert.equal(stillLate.state.origin_receipt_digest, opening.receipt_digest);
  assert.equal(stillLate.state.origin_at, ORIGIN);
});

// --- 9. the readback refuses rather than serving a shortened inventory ----------

/**
 * Re-ordinal and re-chain a row list so a fixture can vary ONE thing at a time.
 * Without it a reordered pair would refuse on its broken chain and never reach
 * the order check the test is actually about.
 */
function relink(rows) {
  let previous = null;
  return rows.map((row, ordinal) => {
    const admission_digest = journeyOneMinimumAdmissionDigest({
      admitted_at: row.admitted_at, clock_scope_key: row.clock_scope_key,
      minimum_environment_manifest_digest: row.minimum_environment_manifest_digest,
      minimum_receipt_ttl_policy_ms: row.minimum_receipt_ttl_policy_ms,
      previous_admission_digest: previous, receipt_digest: row.receipt_digest,
      tenant: row.tenant });
    const relinked = { ...row, admission_ordinal: ordinal,
      previous_admission_digest: previous, admission_digest };
    previous = admission_digest;
    return relinked;
  });
}

/** A read-only journal serving exactly the rows a test hands it. */
function stubJournal(rows) {
  const inventory = {
    clock_scope_key: journeyOneClockScopeKey(SCOPE), clock_scope_ref: SCOPE.scope_ref,
    scope: copy(SCOPE), tenant: ORGANIZATION_TENANT_ID,
    minimum_receipt_ttl_policy_ms: MINIMUM_TTL_POLICY,
    minimum_environment_manifest_digest: D(4), opened_at: ORIGIN,
  };
  return { durable: false, kind: "stub",
    async runAppend() { throw new Error("read-only stub"); },
    async readInventory() { return inventory; },
    async readAdmissions() { return rows; },
    async openInventory() { throw new Error("read-only stub"); } };
}
async function storedRows() {
  const { store, clock } = newStore();
  const first = await admit(store, minimum(), null);
  clock.ms = ORIGIN_MS + HOUR;
  const second = await admit(store, minimum(ORIGIN, "safe:synthetic:second"),
    first.admission_digest);
  return [copy(first), copy(second)].map(row => { delete row.replayed; return row; });
}

test("a tampered readback refuses rather than serving a shorter or edited inventory", async () => {
  const clean = await storedRows();
  const healthy = createJourneyOneClockMinimumInputStore({ journal: stubJournal(clean),
    actor: ACTOR, clock_scope: SCOPE, accepted_minimum_policy: POLICY });
  assert.equal((await healthy.read()).admission_count, 2);

  const edited = await storedRows();
  edited[0].receipt.comparator = "a comparator nobody issued";
  const one = createJourneyOneClockMinimumInputStore({ journal: stubJournal(edited),
    actor: ACTOR, clock_scope: SCOPE, accepted_minimum_policy: POLICY });
  assert.equal((await refusal(one.read())).code, "minimum_admission_readback_tampered");

  const relabelledColumn = await storedRows();
  relabelledColumn[1].status = "quarantined";
  const two = createJourneyOneClockMinimumInputStore({ journal: stubJournal(relabelledColumn),
    actor: ACTOR, clock_scope: SCOPE, accepted_minimum_policy: POLICY });
  assert.equal((await refusal(two.read())).code, "minimum_admission_readback_tampered");

  // THE FIRST ROW REMOVED is the one that would move the origin, and it refuses
  // rather than serving a one-row inventory that reads perfectly well.
  const truncated = (await storedRows()).slice(1);
  const three = createJourneyOneClockMinimumInputStore({ journal: stubJournal(truncated),
    actor: ACTOR, clock_scope: SCOPE, accepted_minimum_policy: POLICY });
  assert.equal((await refusal(three.read())).code, "minimum_admission_ordinal_gap");

  const unlinked = await storedRows();
  unlinked[1].previous_admission_digest = null;
  const four = createJourneyOneClockMinimumInputStore({ journal: stubJournal(unlinked),
    actor: ACTOR, clock_scope: SCOPE, accepted_minimum_policy: POLICY });
  assert.equal((await refusal(four.read())).code, "minimum_admission_chain_broken");
});

// --- 10. the composer's own refusals ---------------------------------------------

test("the composer refuses an as_of the kernel could not read, and an incomplete source set", async () => {
  const { store } = newStore();
  await admit(store, minimum(), null);
  const composer = composerFor(store);

  const early = await refusal(composer.compose({ as_of: iso(ORIGIN_MS - HOUR), completion: null,
    completion_expectation: null, pauses: [], amendments: [], history: null }));
  assert.equal(early.code, "projection_as_of_precedes_admission");

  const partial = await refusal(composer.compose({ as_of: ORIGIN, completion: null,
    completion_expectation: null, pauses: [], history: null }));
  assert.equal(partial.code, "closed_shape");

  const incomplete = { ...SOURCES };
  delete incomplete.benchmark_manifest_digest;
  assert.throws(() => composerFor(store, { accepted_sources: incomplete }),
    error => error.code === "closed_shape");

  const empty = composerFor(newStore().store);
  const none = await refusal(empty.compose({ as_of: ORIGIN, completion: null,
    completion_expectation: null, pauses: [], amendments: [], history: null }));
  assert.equal(none.code, "minimum_inventory_unavailable");
});

test("a supplied history is read by the kernel's own reader, under the kernel's own name", async () => {
  const { store } = newStore();
  await admit(store, minimum(), null);
  const { result } = await evaluateStored(store);
  const broken = copy(result.state);
  broken.events = [];
  const composer = composerFor(store);
  const error = await refusal(composer.compose({ as_of: ORIGIN, completion: null,
    completion_expectation: copy(EXPECTATION), pauses: [], amendments: [], history: broken }));
  assert.equal(error.name, "JourneyOneClockError");
  assert.equal(error.code, "corrupt_history");
});

// --- 11. the missing public authority ---------------------------------------------

test("the public admit verb refuses before it issues any query, and says why", async () => {
  let queried = 0;
  const tools = journeyOneClockMinimumInputStoreTools({
    withEnvelope: (c, actor, name, args, run) => run(),
    ToolError: class ToolError extends Error {
      constructor(payload) { super(payload.error); Object.assign(this, payload); } },
  });
  const context = { query: async () => { queried += 1; return { rows: [] }; } };
  const error = await refusal(
    tools["admit-journey-one-minimum-receipt"].handler(context, ACTOR,
      { idempotency_key: key(), expected_prior_admission_digest: null, source_ref: SOURCE }));
  assert.equal(error.error, "minimum_input_authority_unbound");
  assert.equal(queried, 0);
  assert.equal(error.detail.resolved, false);
  assert.ok(error.detail.why_unresolved.some(w => w.includes("Gate Zero")));

  const requirements = journeyOneClockMinimumInputStoreIntegrationRequirements();
  assert.equal(requirements.public_admit_available, false);
  assert.equal(requirements.clock_started, false);
  assert.equal(requirements.storage_implemented, true);
  assert.deepEqual(requirements.public_admit_blocked_by,
    [JOURNEY_ONE_MINIMUM_INPUT_AUTHORITY_REQUIREMENT.binding_ref]);
  // The blockers this slice does NOT close are still named as unresolved.
  const why = JOURNEY_ONE_MINIMUM_INPUT_AUTHORITY_REQUIREMENT.why_unresolved.join(" ");
  for (const blocker of ["issuance", "terminus", "coverage", "Gate Zero"]) {
    assert.ok(why.includes(blocker), blocker);
  }
  // AND THE SEAT THAT READS THIS COMPOSER IS NAMED, with what it can and cannot
  // do: it reads this inventory, never writes to it, and cannot run here.
  const loop = requirements.trusted_integration_contract.composition_loop;
  assert.ok(loop.includes("journey-one-clock-runtime.v5.js"));
  assert.ok(loop.includes("reads this inventory and never writes to it"));
  assert.ok(loop.includes("minimum_inventory_unavailable"));
});

test("the postgres journal is constructible and issues no query until it is used", () => {
  let queried = 0;
  const journal = createPostgresJourneyOneMinimumAdmissionJournal({
    query: async () => { queried += 1; return { rows: [] }; } });
  assert.equal(journal.durable, true);
  assert.equal(queried, 0);
});

/**
 * A scripted connection handle for the durable journal. It answers each
 * statement the journal issues and records whether the append was reached, so a
 * test can prove a refusal happened BEFORE any row was written.
 */
function scriptedQuery(txids) {
  let reads = 0;
  const seen = { appended: 0, opened: 0 };
  const query = async (sql) => {
    if (sql.includes("txid_current")) return { rows: [{ txid: txids[reads++] ?? txids[0] }] };
    if (sql.includes("j1_minimum_admission_instant")) return { rows: [{ at: ORIGIN }] };
    if (sql.includes("j1_minimum_inventory_row")) return { rows: [{ inventory: null }] };
    if (sql.includes("j1_minimum_head")) return { rows: [{ head: null }] };
    if (sql.includes("j1_minimum_admissions")) return { rows: [{ admissions: [] }] };
    if (sql.includes("j1_minimum_admission_by_idempotency_key")) return { rows: [{ admission: null }] };
    if (sql.includes("j1_minimum_open_inventory")) {
      seen.opened += 1;
      return { rows: [{ inventory: { clock_scope_key: journeyOneClockScopeKey(SCOPE),
        clock_scope_ref: SCOPE.scope_ref, tenant: ORGANIZATION_TENANT_ID } }] };
    }
    if (sql.includes("j1_minimum_append_admission")) {
      seen.appended += 1;
      return { rows: [{ admission: { admission_ordinal: 0, replayed: false } }] };
    }
    return { rows: [{}] };
  };
  return { query, seen };
}

test("the durable journal refuses when its statements are not in one transaction", async () => {
  // pg_advisory_xact_lock releases at statement end under autocommit, and the
  // "one reading, not two" argument for the admission instant is exactly the
  // stability of now() within a transaction. txid_current() returns a different
  // id per statement when there is no explicit transaction.
  const split = scriptedQuery(["771", "772"]);
  const splitStore = createJourneyOneClockMinimumInputStore({
    journal: createPostgresJourneyOneMinimumAdmissionJournal({ query: split.query }),
    actor: ACTOR, clock_scope: SCOPE, accepted_minimum_policy: POLICY });
  const error = await refusal(admit(splitStore, minimum(), null));
  assert.equal(error.code, "minimum_admission_transaction_not_shared");
  assert.equal(error.detail.invariant, "j1_minimum_admission_instant_is_server_time");
  // NOTHING WAS WRITTEN. The check runs before the append, not after it.
  assert.equal(split.seen.appended, 0);

  // One transaction, one id: the same path proceeds to the append.
  const shared = scriptedQuery(["771", "771"]);
  const sharedStore = createJourneyOneClockMinimumInputStore({
    journal: createPostgresJourneyOneMinimumAdmissionJournal({ query: shared.query }),
    actor: ACTOR, clock_scope: SCOPE, accepted_minimum_policy: POLICY });
  await admit(sharedStore, minimum(), null);
  assert.equal(shared.seen.appended, 1);
});

test("the store refuses a relabelled scope before the journal's own seal can", async () => {
  // Both journals seal the label in openInventory, but on the durable path that
  // refusal lives in SQL that has never run, so the store would pass its whole
  // read and compare-and-swap before meeting it.
  const { store } = newStore();
  const opening = await admit(store, minimum(), null);
  const head = copy(opening); delete head.replayed;
  const calls = { opened: 0 };
  const relabelledInventory = {
    clock_scope_key: journeyOneClockScopeKey(SCOPE),
    clock_scope_ref: "safe:clock-scope:a-name-this-inventory-was-not-opened-under",
    scope: copy(SCOPE), tenant: ORGANIZATION_TENANT_ID,
    minimum_receipt_ttl_policy_ms: MINIMUM_TTL_POLICY,
    minimum_environment_manifest_digest: D(4), opened_at: ORIGIN,
  };
  const journal = {
    durable: true, kind: "build-only",
    async runAppend(scopeKey, { build }) {
      return build({ inventory: relabelledInventory, head, admissions: [head],
        replay: null, admitted_at: iso(ORIGIN_MS + HOUR) });
    },
    async readInventory() { return relabelledInventory; },
    async readAdmissions() { return [head]; },
    async openInventory() { calls.opened += 1; return relabelledInventory; },
  };
  const relabelled = createJourneyOneClockMinimumInputStore({ journal, actor: ACTOR,
    clock_scope: SCOPE, accepted_minimum_policy: POLICY });
  const error = await refusal(relabelled.admit({
    receipt: minimum(ORIGIN, "safe:synthetic:after-rename"),
    expected_prior_admission_digest: head.admission_digest, idempotency_key: key(),
    claimed_receipt_digest: null, source_ref: SOURCE }));
  assert.equal(error.code, "minimum_scope_label_changed");
  assert.equal(error.detail.invariant, "j1_minimum_scope_label_is_not_identity");
  assert.equal(calls.opened, 0);
});

// --- 12. one rule set, two homes ---------------------------------------------------

test("every shared admission invariant id appears verbatim in the candidate SQL", () => {
  for (const id of JOURNEY_ONE_MINIMUM_ADMISSION_INVARIANT_IDS) {
    assert.ok(CANDIDATE_SQL.includes(id), `${id} is not stated in the candidate SQL`);
  }
});

test("the candidate SQL is fresh-or-exactly-compatible and stores no minted binding", () => {
  assert.equal(/\balter\s+table\b/i.test(CANDIDATE_SQL), false);
  assert.equal(/\bupdate\s+ops\.j1_minimum/i.test(CANDIDATE_SQL), false);
  assert.equal(/\bdelete\s+from\s+ops\.j1_minimum/i.test(CANDIDATE_SQL), false);
  assert.equal(/\bcreate\s+role\b/i.test(CANDIDATE_SQL), false);
  assert.equal(/\bcreate\s+schema\b/i.test(CANDIDATE_SQL), false);
  // The unbound blockers have no column: a slot filled from a caller would mint
  // the binding rather than carry it.
  assert.equal(/coverage_digest|gate_zero_outcome_digest/i.test(CANDIDATE_SQL), false);
  // And there is one scope derivation, which is the clock rail's.
  assert.ok(CANDIDATE_SQL.includes("ops.j1_clock_scope_digest"));
  assert.equal(/create\s+or\s+replace\s+function\s+ops\.j1_minimum_scope_digest/i
    .test(CANDIDATE_SQL), false);
});

test("truncate is refused by a trigger and not only by a revoke", () => {
  // A row-level trigger never sees TRUNCATE -- it is a statement event -- and
  // `revoke ... truncate` does not bind the table owner. The invariant text
  // claims truncate is refused, so the statement-level trigger has to exist.
  assert.match(CANDIDATE_SQL,
    /create trigger %I before truncate on ops\.%I for each statement execute function ops\.j1_minimum_rows_immutable\(\)/);
  assert.ok(CANDIDATE_SQL.includes("_no_truncate"));
  assert.ok(PROOF_SQL.includes("truncate ops.%I cascade"));
  assert.ok(PROOF_SQL.includes("_no_truncate"));
});

test("the append-only negative in the fixture updates a column both relations have", () => {
  // REGRESSION for a fixture defect that no check could see: the loop updated
  // `tenant`, which ops.j1_minimum_admission does not have, so the statement
  // failed at parse with 42703 before the trigger could fire and the whole proof
  // aborted having proved nothing about append-only.
  assert.equal(/update ops\.%I set tenant/.test(PROOF_SQL), false);
  assert.ok(PROOF_SQL.includes("set minimum_receipt_ttl_policy_ms = minimum_receipt_ttl_policy_ms + 1"));
  assert.ok(PROOF_SQL.includes("information_schema.columns"),
    "the fixture must assert the updated column exists on both relations");
  for (const relation of ["j1_minimum_inventory", "j1_minimum_admission"]) {
    assert.ok(new RegExp(`create table if not exists ops\\.${relation}\\b`).test(CANDIDATE_SQL));
  }
  // Both relations really do carry it, read off their own DDL.
  const admission = CANDIDATE_SQL.slice(
    CANDIDATE_SQL.indexOf("create table if not exists ops.j1_minimum_admission"));
  assert.ok(admission.slice(0, admission.indexOf(");")).includes("minimum_receipt_ttl_policy_ms"));
  assert.equal(admission.slice(0, admission.indexOf(");")).includes("\n  tenant "), false,
    "ops.j1_minimum_admission has no tenant column; its tenant is reached through the inventory");
});

test("digest ordering is pinned to byte order in both SQL homes", () => {
  // The JavaScript home compares with UTF-16 code units. A database default
  // collation is locale-dependent, so two homes that agree by coincidence of
  // locale are two homes that can disagree after one initdb.
  const ordering = CANDIDATE_SQL.match(/receipt_digest collate "C"/g) ?? [];
  assert.ok(ordering.length >= 2,
    `both the guard and the readback pin COLLATE "C"; found ${ordering.length}`);
  assert.ok(PROOF_SQL.includes('collate "C"'));
});

test("the SQL shape guard reads jsonb TYPES, not the text `->>` renders them as", () => {
  // REGRESSION for a BOTH-homes invariant that admitted on one side what it
  // refused on the other: `->>` renders the number 12345 as '12345', so a bare
  // regex or length judged values the JavaScript home refuses outright.
  for (const field of ["evidence_ref", "fixture_set_digest", "comparator"]) {
    assert.ok(CANDIDATE_SQL.includes(
      `jsonb_typeof(new.receipt -> '${field}') is distinct from 'string'`),
      `${field} must be type-checked before its value is read`);
  }
  assert.ok(CANDIDATE_SQL.includes(
    "jsonb_typeof(new.receipt -> v_field -> v_seat_field) is distinct from 'string'"),
    "each identity seat field must be type-checked");
  assert.ok(CANDIDATE_SQL.includes("jsonb_typeof(new.receipt -> v_field) is distinct from 'object'"));
  // IS DISTINCT FROM, not <>: a missing key yields NULL and `if NULL` does not
  // fire, which would fail open on exactly the absent field.
  // (The receipt column itself is NOT NULL, so its own typeof test may use <>;
  //  every FIELD test reached through -> must not.)
  assert.equal(/jsonb_typeof\(new\.receipt ->[^)]*\) <> /.test(CANDIDATE_SQL), false);
});

test("the SQL comparator bound counts UTF-16 code units, through the one shared counter", () => {
  // char_length() counts CODEPOINTS and differs from the kernel's ruler in both
  // directions: 151 astral characters pass a codepoint bound the kernel refuses,
  // and 3 astral characters fail a codepoint floor the kernel accepts.
  assert.ok(CANDIDATE_SQL.includes(
    "ops.benchmark_utf16_length(new.receipt ->> 'comparator') < 5"));
  assert.ok(CANDIDATE_SQL.includes(
    "ops.benchmark_utf16_length(new.receipt ->> 'comparator') > 300"));
  assert.equal(/length\(coalesce\(new\.receipt ->> 'comparator'/.test(CANDIDATE_SQL), false);
  // The counter is a declared prerequisite, not a rail-local second copy.
  assert.equal(/create or replace function ops\.j1_minimum_utf16/.test(CANDIDATE_SQL), false);
  assert.ok(CANDIDATE_SQL.includes("ops/benchmark-acceptance.candidate.sql"));
  assert.ok(PROOF_SQL.includes("benchmark_utf16_length"),
    "the fixture must skip rather than fail when the shared counter is absent");
  // And the fixture proves both halves of the domain, not only the refusals.
  assert.ok(PROOF_SQL.includes("chr(119070)"));
  assert.ok(PROOF_SQL.includes("ACCEPTED DOMAIN:"));
});

test("the SQL readback answers in the module's own readback schema", () => {
  assert.ok(CANDIDATE_SQL.includes(JOURNEY_ONE_MINIMUM_INVENTORY_READBACK_SCHEMA),
    "ops.j1_minimum_history must name the schema version the module's read() returns");
});

test("the SQL exists:false branch carries the module's exists:false key set exactly", async () => {
  // The claim in the SQL header is PARITY, and five of six keys is not parity:
  // the branch used to return gate_admitted_by_record_layer, which read() does
  // not carry on an absent inventory, and to omit `effects`, which it does. An
  // overstated parity claim is worse than none, because it is the reason nobody
  // re-checks. This reads the module's own answer and the SQL's own text.
  const absent = await newStore().store.read();
  assert.equal(absent.exists, false);
  assert.deepEqual(Object.keys(absent).sort(), ["clock_scope_key", "effects", "exists",
    "record_layer_cannot_prove", "schema_version", "tenant"]);
  assert.equal(Object.hasOwn(absent, "gate_admitted_by_record_layer"), false);

  // The `if not found` branch of ops.j1_minimum_history, read out of the file --
  // WITH ITS COMMENTS STRIPPED, because what is under test is the object the
  // branch EMITS and not the prose beside it. A source-text scan that reads a
  // comment as an emitted field is the same defect one layer up: it reports on
  // what the file says rather than on what it does.
  const branch = CANDIDATE_SQL.slice(
    CANDIDATE_SQL.indexOf("  if not found then",
      CANDIDATE_SQL.indexOf("create or replace function ops.j1_minimum_history(")));
  const executable = branch.slice(0, branch.indexOf("  end if;"))
    .split("\n").map(line => line.replace(/--.*$/, "")).join("\n");
  // The TOP-LEVEL arguments of the emitted jsonb_build_object, split on commas
  // at depth one, so a nested object's own keys are not mistaken for this one's.
  // (Both this and the comment strip above are sound on THIS branch, whose
  // string literals contain neither a comma nor a double hyphen; neither is a
  // general SQL parser and neither is used as one.)
  const args = topLevelArguments(executable, executable.indexOf("jsonb_build_object"));
  const emitted = args.filter((_, index) => index % 2 === 0).map(a => a.replace(/^'|'$/g, ""));
  assert.deepEqual([...emitted].sort(), Object.keys(absent).sort(),
    "the SQL exists:false branch must emit the module's exists:false key set exactly");
  assert.equal(emitted.includes("gate_admitted_by_record_layer"), false,
    "an inventory that does not exist has admitted nothing to say false about");
  // And the effects object it emits is the nine keys of V5_NO_EFFECTS, which is
  // what the exists:true branch already emits.
  const effects = args[args.indexOf("'effects'") + 1];
  const effectKeys = topLevelArguments(effects, effects.indexOf("jsonb_build_object"))
    .filter((_, index) => index % 2 === 0).map(a => a.replace(/^'|'$/g, ""));
  assert.deepEqual([...effectKeys].sort(), Object.keys(absent.effects).sort());
});

test("the fixture's append-only negatives cannot silently prove nothing under the wrong role", () => {
  // update, delete and truncate are revoked from every runtime bundle, so under
  // carr_writer each negative fails with insufficient privilege BEFORE the
  // trigger fires; the handler matches on the invariant id, re-raises, and the
  // whole proof aborts having proved nothing -- the same defect class as the
  // column that did not exist, gated on role rather than on schema.
  assert.ok(PROOF_SQL.includes(
    "has_table_privilege(current_user, 'ops.j1_minimum_admission', 'TRUNCATE')"),
    "the fixture must check it can reach the trigger before attempting the negatives");
  assert.ok(/raise notice 'SKIPPED: % holds no UPDATE\/DELETE\/TRUNCATE/.test(PROOF_SQL),
    "and must SKIP with a notice rather than turning an environment fact into a false negative");
  // The structural half needs no privilege, so it is asserted on every run --
  // including one that skipped the attempts.
  assert.ok(PROOF_SQL.includes("_append_only' and not t.tgisinternal"));
  assert.ok(PROOF_SQL.includes("_no_truncate' and not t.tgisinternal"));
  // TRUNCATE is now a first-class claim, so the bundle-privilege assertion says
  // so too rather than checking only INSERT/UPDATE/DELETE.
  assert.ok(PROOF_SQL.includes("has_table_privilege(v_role, v_relation, 'TRUNCATE')"));
  assert.ok(CANDIDATE_SQL.includes("revoke insert, update, delete, truncate on"));
});

test("the postgres proof names every function the durable journal calls", () => {
  for (const signature of [
    "ops.j1_minimum_lock(text)",
    "ops.j1_minimum_admission_instant()",
    "ops.j1_minimum_inventory_row(text)",
    "ops.j1_minimum_head(text)",
    "ops.j1_minimum_admissions(text)",
    "ops.j1_minimum_admission_by_idempotency_key(uuid)",
    "ops.j1_minimum_open_inventory(jsonb,bigint,text)",
    "ops.j1_minimum_append_admission(text,text,uuid,text,text,text,jsonb,jsonb)",
  ]) {
    assert.ok(PROOF_SQL.includes(signature), `${signature} is not asserted by the proof fixture`);
    const name = signature.slice(0, signature.indexOf("("));
    assert.ok(CANDIDATE_SQL.includes(`create or replace function ${name}(`)
      || CANDIDATE_SQL.includes(`create or replace function ${name}()`),
      `${name} is not defined by the candidate SQL`);
  }
  assert.ok(PROOF_SQL.includes("HAS NOT BEEN RUN"));
});

test("the declared field sets are closed, C-sorted and match what the rail computes", () => {
  for (const fields of [JOURNEY_ONE_MINIMUM_ADMISSION_FIELDS,
    JOURNEY_ONE_MINIMUM_ACCEPTED_POLICY_FIELDS, JOURNEY_ONE_MINIMUM_ACCEPTED_SOURCE_FIELDS,
    JOURNEY_ONE_MINIMUM_COMPOSE_FIELDS, JOURNEY_ONE_MINIMUM_RECEIPT_IDENTITY_SEATS]) {
    assert.deepEqual([...fields], [...fields].sort());
  }
  // The chain preimage is closed: a missing or extra field refuses rather than
  // hashing a different object.
  assert.throws(() => journeyOneMinimumAdmissionDigest({ admitted_at: ORIGIN }),
    error => error.code === "closed_shape");
});

// --- 13. the benchmark acceptance envelope is DERIVED, and says what it is not ---
//
// Three of the five accepted source bindings are the acceptance envelope of
// benchmark-manifest.v1. Supplied as literals they were three unaudited strings
// nothing compared against the artifact they claim to describe: a digest no
// manifest produces, an instant no manifest records and an acceptor no manifest
// names would all compose a projection the kernel then starts a clock on. The
// composer now REQUIRES that artifact, so there is no path left on which those
// three are asserted rather than derived.
//
// NOTHING BELOW AUTHENTICATES ANYBODY, and the tests assert that too. A00's
// validateBenchmarkManifest reads the SHAPE of an acceptance envelope; the
// manifest this suite uses is composed by this file, exactly as a trusted
// integration would compose a real one.

test("the acceptance envelope is derived from the manifest, and the two policy fields are not", () => {
  const derived = journeyOneMinimumBenchmarkAcceptedSources(benchmarkManifest());
  assert.deepEqual(Object.keys(derived).sort(),
    [...JOURNEY_ONE_MINIMUM_BENCHMARK_DERIVED_SOURCE_FIELDS]);
  // The digest is the one A00's validator RECOMPUTED from the payload, so a
  // manifest carrying a digest of its own choosing cannot supply one.
  assert.equal(derived.benchmark_manifest_digest, benchmarkPayloadDigest(benchmarkPayload()));
  assert.equal(derived.benchmark_accepted_at, BENCHMARK_ACCEPTED_AT);
  assert.deepEqual(derived.benchmark_accepted_by_identity, I("joe"));
  // And this is exactly what the suite's own accepted sources carry, so every
  // composition in this file is composed from the manifest rather than beside it.
  for (const field of JOURNEY_ONE_MINIMUM_BENCHMARK_DERIVED_SOURCE_FIELDS) {
    assert.deepEqual(SOURCES[field], derived[field]);
  }
  assert.throws(() => { derived.benchmark_manifest_digest = D(9); }, TypeError);

  // THE OTHER TWO ARE ON NO BENCHMARK MANIFEST AT ALL. Deriving them would mint
  // a binding rather than carry one, so they stay trusted policy inputs.
  const manifest = benchmarkManifest();
  for (const field of JOURNEY_ONE_MINIMUM_TRUSTED_POLICY_SOURCE_FIELDS) {
    assert.equal(Object.hasOwn(derived, field), false);
    assert.equal(Object.hasOwn(manifest, field), false);
  }
  // And the two halves are exactly the accepted source set, with no overlap.
  assert.deepEqual([...JOURNEY_ONE_MINIMUM_BENCHMARK_DERIVED_SOURCE_FIELDS,
    ...JOURNEY_ONE_MINIMUM_TRUSTED_POLICY_SOURCE_FIELDS].sort(),
    [...JOURNEY_ONE_MINIMUM_ACCEPTED_SOURCE_FIELDS]);
});

test("a manifest that is not an accepted one refuses, under A00's own codes", () => {
  for (const [label, manifest, code] of [
    ["not an object at all", null, "invalid_shape"],
    ["an envelope-less payload", benchmarkPayload(), "closed_shape"],
    ["a manifest carrying an extra field", benchmarkManifest({ note: "x" }), "closed_shape"],
    ["a manifest that is not accepted", benchmarkManifest({ status: "proposed" }),
      "benchmark_status_not_accepted"],
    ["a digest that is not its payload's", benchmarkManifest({ benchmark_manifest_digest: D(9) }),
      "benchmark_manifest_digest_mismatch"],
    ["a payload edited after the digest was taken", benchmarkManifest({ routes: ["/elsewhere"] }),
      "benchmark_manifest_digest_mismatch"],
    ["an acceptor who is not a verified partner", benchmarkManifest({ accepted_by_identity: I("claude") }),
      "verified_partner_required"],
  ]) {
    assert.throws(() => journeyOneMinimumBenchmarkAcceptedSources(manifest), error => {
      assert.equal(error.code, code, label);
      return true;
    }, label);
  }
});

test("no composer exists without the accepted manifest its envelope is derived from", async () => {
  const { store } = newStore();
  await admit(store, minimum(), null);

  // THERE IS NO LITERAL PATH LEFT. An omitted, null or non-object manifest is a
  // caller asserting the acceptance envelope, and this rail has no way to tell
  // an asserted digest, instant or acceptor from a derived one — so it refuses
  // to be constructed rather than composing a projection nothing checked.
  for (const [label, manifest] of [
    ["omitted", undefined], ["explicitly null", null], ["a digest standing in for it", D(8)],
  ]) {
    assert.throws(() => createJourneyOneClockProjectionComposer({
      store, accepted_sources: SOURCES, ...(manifest === undefined ? {} : { benchmark_manifest: manifest }) }),
    error => {
      assert.equal(error.code, "benchmark_manifest_required", label);
      assert.equal(error.detail.derived_fields.length, 3);
      return true;
    }, label);
  }

  // WITH the manifest, the three envelope fields on the composed projection are
  // the ones that manifest produces.
  const composed = await composerFor(store).compose({ as_of: ORIGIN, completion: null,
    completion_expectation: copy(EXPECTATION), pauses: [], amendments: [], history: null });
  assert.equal(composed.benchmark_envelope.derived_from_validated_accepted_manifest, true);
  assert.deepEqual(composed.benchmark_envelope.derived_fields,
    [...JOURNEY_ONE_MINIMUM_BENCHMARK_DERIVED_SOURCE_FIELDS]);
  assert.equal(composed.projection.benchmark.manifest_digest,
    benchmarkPayloadDigest(benchmarkPayload()));
  assert.equal(composed.projection.benchmark.accepted_at, BENCHMARK_ACCEPTED_AT);
  assert.deepEqual(composed.projection.benchmark.accepted_by_identity, I("joe"));
  // AND IT IS STILL NOT ACCEPTANCE. Deriving removes a caller's freedom to
  // invent the envelope; it consults no live actor and reads no acceptance
  // record, and the composition refuses to be read as though it had.
  assert.equal(composed.benchmark_envelope.human_acceptance_authenticated, false);
  assert.equal(composed.authenticated, false);
  assert.equal(composed.trusted_verifier_still_required, true);
  assert.ok(composed.benchmark_envelope.statement.includes(
    "not evidence that a verified partner accepted anything"));
  // The two policy fields are reported as what they are: trusted inputs, and
  // the kernel still reads them off the binding.
  assert.deepEqual(composed.benchmark_envelope.trusted_policy_fields,
    [...JOURNEY_ONE_MINIMUM_TRUSTED_POLICY_SOURCE_FIELDS]);
  assert.equal(composed.projection.binding.production_environment_manifest_digest, D(6));
  assert.equal(composed.projection.binding.maximum_completion_receipt_ttl_ms, COMPLETION_TTL_POLICY);
  assert.equal(harness().evaluate(composed.projection).state.status, "running");
});

test("an accepted_sources the manifest does not produce, and a manifest for another scope, both refuse", async () => {
  const { store } = newStore();
  await admit(store, minimum(), null);

  // ONE FIELD AT A TIME, each a value nothing about the manifest supports.
  for (const [field, value] of [
    ["benchmark_manifest_digest", D(8)],
    ["benchmark_accepted_at", iso(ORIGIN_MS - 2 * HOUR)],
    ["benchmark_accepted_by_identity", I("dell")],
  ]) {
    assert.throws(() => composerFor(store,
      { accepted_sources: { ...SOURCES, [field]: value } }), error => {
      assert.equal(error.code, "benchmark_accepted_sources_not_derived");
      assert.equal(error.detail.field, field);
      return true;
    }, field);
  }

  // A GENUINELY ACCEPTED MANIFEST FOR ANOTHER PROGRAM. Its envelope is
  // internally consistent and its digest is real; it is simply not about this
  // inventory's accepted subject. The kernel could never catch it — the manifest
  // digest is in none of the comparisons it makes against a receipt.
  const other = benchmarkPayload(); other.subject_digest = D(11);
  const otherManifest = { ...copy(other), benchmark_manifest_digest: benchmarkPayloadDigest(other),
    accepted_by_identity: I("joe"), accepted_at: BENCHMARK_ACCEPTED_AT, status: "accepted" };
  assert.throws(() => composerFor(store, {
    accepted_sources: { ...SOURCES, benchmark_manifest_digest: benchmarkPayloadDigest(other) },
    benchmark_manifest: otherManifest }), error => {
    assert.equal(error.code, "benchmark_manifest_scope_mismatch");
    assert.equal(error.detail.field, "subject_digest");
    assert.equal(error.detail.expected, SCOPE.benchmark_subject_digest);
    return true;
  });
});
