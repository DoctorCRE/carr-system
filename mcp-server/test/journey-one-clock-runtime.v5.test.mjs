// journey-one-clock-runtime.v5.js — the seat that joins the admitted-minimum
// inventory, the kernel and the clock history.
//
// WHAT THIS SUITE IS AND IS NOT. The REAL loop — a real composer over a real
// admitted inventory, a real kernel and the real store — is exercised end to end
// in journey-one-minimum-integration.v5.test.mjs, where the one artifact those
// three rails share is already built; duplicating that fixture here would be a
// second home for it. This suite is about what the SEAT itself does: its
// construction refusals, the two facts it derives rather than accepts, the order
// in which it refuses, and the composition binding, driven exhaustively as the
// pure function it is.
//
// The doubles below stand in for the rails on purpose and are honest about it:
// each one is a recorder of calls, not a model of the thing it replaces, and no
// assertion here is evidence about a rail's own behaviour. Nothing is
// authenticated, no receipt is issued and no clock is started.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { digest } from "../src/artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  JOURNEY_ONE_CLOCK_PROJECTION, JOURNEY_ONE_CLOCK_VERIFIED_BINDING,
  JOURNEY_ONE_DEADLINE_CONTRACT,
} from "../src/journey-one-clock.v5.js";
import {
  journeyOneClockKeyForState, journeyOneClockScopeBinding,
} from "../src/journey-one-clock-store.v5.js";
import {
  JOURNEY_ONE_MINIMUM_COMPOSE_FIELDS, JOURNEY_ONE_MINIMUM_PROJECTION_INPUTS_SCHEMA,
} from "../src/journey-one-clock-input-store.v5.js";
import {
  JOURNEY_ONE_CLOCK_ADVANCE_COMPOSED_FROM_FIELDS, JOURNEY_ONE_CLOCK_ADVANCE_FIELDS,
  JOURNEY_ONE_CLOCK_ADVANCE_RECEIPT_FIELDS, JOURNEY_ONE_CLOCK_COMPOSITION_BINDING_FACTS,
  JOURNEY_ONE_CLOCK_COMPOSITION_PROOF_FACT, JOURNEY_ONE_CLOCK_DERIVED_NOT_SUPPLIED_FIELDS,
  JOURNEY_ONE_CLOCK_REPLAY_EFFECTS, JOURNEY_ONE_CLOCK_RUNTIME_CANNOT_PROVE,
  JOURNEY_ONE_CLOCK_RUNTIME_DERIVED_COMPOSE_FIELDS,
  assertJourneyOneClockComputedFromComposition, createJourneyOneClockRuntime,
  journeyOneClockRuntimeIntegrationRequirements,
} from "../src/journey-one-clock-runtime.v5.js";

const D = n => `sha256:${String(n).padStart(2, "0").repeat(32)}`;
const copy = x => JSON.parse(JSON.stringify(x));
const DAY = 86400000;

const SUBJECT = D(1), CANDIDATE = D(2), POLICY = D(3);
const MANIFEST = D(8);
const MINIMUM_TTL = 7 * DAY;
const OBSERVED_AT = "2026-02-03T00:00:00.000Z";
const ADMITTED_AT = "2026-02-03T01:00:00.000Z";
const AS_OF = "2026-02-04T00:00:00.000Z";
const VERIFIER_REF = "safe:verifier:j1-runtime-unit";

const SCOPE = Object.freeze({
  benchmark_candidate_digest: CANDIDATE,
  benchmark_policy_digest: POLICY,
  benchmark_subject_digest: SUBJECT,
  clock_origin_gate_id: JOURNEY_ONE_DEADLINE_CONTRACT.clock_origin_gate_id,
  clock_terminus_gate_id: JOURNEY_ONE_DEADLINE_CONTRACT.clock_terminus_gate_id,
  scope_ref: "safe:clock-scope:j1-runtime-unit",
  tenant: ORGANIZATION_TENANT_ID,
});
const SCOPE_BINDING = journeyOneClockScopeBinding(SCOPE);
const SCOPE_KEY = SCOPE_BINDING.clock_scope_key;

/**
 * A stand-in admitted receipt. The binding under test computes `digest(receipt)`
 * — the exact expression the kernel identifies an admission with — and reads
 * `observed_at`; it is a membership test and deliberately not a second receipt
 * validator, so nothing here needs to be an r7-exact receipt and nothing here
 * should be read as one.
 */
const RECEIPT = Object.freeze({
  receipt_producer_step_ref: "step:foundation-assurance-minimum-receipt",
  gate_id: JOURNEY_ONE_DEADLINE_CONTRACT.clock_origin_gate_id,
  observed_at: OBSERVED_AT,
  fixture_note: "a stand-in artifact, hashed the way the kernel hashes an admission",
});
const ORIGIN_DIGEST = digest(RECEIPT);

function projectionFixture(over = {}) {
  return {
    schema_version: JOURNEY_ONE_CLOCK_PROJECTION,
    tenant: ORGANIZATION_TENANT_ID,
    as_of: AS_OF,
    binding: {
      subject_digest: SUBJECT, candidate_digest: CANDIDATE, policy_digest: POLICY,
      minimum_environment_manifest_digest: D(4),
      production_environment_manifest_digest: D(7),
      maximum_minimum_receipt_ttl_ms: MINIMUM_TTL,
      maximum_completion_receipt_ttl_ms: 30 * DAY,
    },
    benchmark: { manifest_digest: MANIFEST, subject_digest: SUBJECT,
      candidate_digest: CANDIDATE, policy_digest: POLICY },
    minimum_history: [{ admitted_at: ADMITTED_AT, receipt: copy(RECEIPT) }],
    completion: null, completion_expectation: null,
    pauses: [], amendments: [], history: null,
    ...over,
  };
}
function compositionFixture(over = {}) {
  const { projection, ...rest } = over;
  return {
    schema_version: JOURNEY_ONE_MINIMUM_PROJECTION_INPUTS_SCHEMA,
    clock_scope_key: SCOPE_KEY,
    clock_scope_ref: SCOPE.scope_ref,
    head_admission_digest: D(80),
    admission_count: 1,
    benchmark_envelope: { derived_from_validated_accepted_manifest: true },
    authenticated: false,
    projection: projectionFixture(projection),
    ...rest,
  };
}
/**
 * A kernel result. `authenticated_projection_digest` defaults to the digest of
 * the default composed projection, because that is the ordinary case: the
 * verifier resolved the projection the composer produced. A test that is about a
 * DIFFERENT judged projection overrides it, which is the only way this fixture
 * can express the case the proof exists for.
 */
function resultFixture(stateOver = {}, bindingOver = {}) {
  return Object.freeze({
    state: {
      schema_version: "doctorcre-v5-journey-one-clock.v2",
      origin_receipt_digest: ORIGIN_DIGEST,
      origin_at: OBSERVED_AT,
      origin_benchmark_manifest_digest: MANIFEST,
      current_benchmark_manifest_digest: MANIFEST,
      origin_receipt_ttl_policy_ms: MINIMUM_TTL,
      evaluated_at: AS_OF,
      status: "running",
      pause_intervals: [],
      history_digest: D(90),
      ...stateOver,
    },
    verified_binding: {
      schema_version: JOURNEY_ONE_CLOCK_VERIFIED_BINDING,
      tenant: ORGANIZATION_TENANT_ID,
      subject_digest: SUBJECT, candidate_digest: CANDIDATE, policy_digest: POLICY,
      clock_origin_gate_id: JOURNEY_ONE_DEADLINE_CONTRACT.clock_origin_gate_id,
      clock_terminus_gate_id: JOURNEY_ONE_DEADLINE_CONTRACT.clock_terminus_gate_id,
      authenticated_projection_digest: digest(projectionFixture()),
      ...bindingOver,
    },
    deadline_success: false, replan_required: false,
    completion_currently_usable: false, completion_observed_within_deadline: null,
    missing_evidence_miss_recorded: false, benchmark_amended: false,
    deadline_resolution: "same_chicago_wall_time_after_30_dates", unresolved_reason: null,
  });
}

/**
 * A kernel result whose judged projection IS the given composition. Every
 * diagnostic case below uses it, so the named check under test is what fires
 * rather than the digest catching the divergence first.
 */
function resultFor(composition, stateOver = {}, bindingOver = {}) {
  return resultFixture(stateOver,
    { authenticated_projection_digest: digest(composition.projection), ...bindingOver });
}

// ---------------------------------------------------------------------------
// The doubles. Recorders of calls, never models of the rails they stand in for.
// ---------------------------------------------------------------------------

function doubles({ head = null, composition = compositionFixture(), result = resultFixture(),
  scope = SCOPE, presented = null, recordedForKey = null, actor = "claude" } = {}) {
  const calls = [];
  const seen = {};
  const bound = journeyOneClockScopeBinding(scope);
  const composer = {
    clock_scope_key: bound.clock_scope_key,
    async compose(args) { calls.push("compose"); seen.compose = args; return composition; },
  };
  const clock = { evaluate(envelope) { calls.push("evaluate"); seen.envelope = envelope; return result; } };
  const clock_store = {
    writer: { actor_id: actor, authority_class: "sponsored_agent" },
    clock_scope: { clock_scope_key: bound.clock_scope_key,
      clock_scope_ref: bound.clock_scope_ref, tenant: bound.tenant,
      scope: bound.scope, scope_identity: bound.scope_identity },
    async readRecordedRevisionForKey(idempotency_key) {
      calls.push("readRecordedRevisionForKey");
      seen.readRecordedRevisionForKey = idempotency_key;
      return recordedForKey ?? { exists: false };
    },
    async readClockKeyForScope() {
      calls.push("readClockKeyForScope");
      return { clock_key: head === null ? null : head.clock_key };
    },
    async read(clockKey) { calls.push(`read:${clockKey}`); return head ?? { exists: false }; },
    async record(args) {
      calls.push("record"); seen.record = args;
      return { clock_key: D(60), revision_ordinal: head === null ? 0 : 1,
        history_digest: args.state.history_digest,
        expected_prior_history_digest: args.expected_prior_history_digest ?? null,
        recorded_at: "2026-02-04T00:00:01.000Z", replayed: false };
    },
  };
  const runtime = createJourneyOneClockRuntime({ composer, clock, clock_store,
    present_projection: presented ?? (projection => ({ synthetic_envelope_ref: "unit",
      presented_digest: digest(projection) })),
    verifier_ref: VERIFIER_REF });
  return { calls, seen, composer, clock, clock_store, runtime };
}

const advanceArgs = (over = {}) => ({
  as_of: AS_OF, pauses: [], amendments: [], completion: null,
  completion_expectation: null, clock_ref: null,
  idempotency_key: "00000000-0000-4000-8000-000000000001", ...over,
});

/**
 * What the store reports for a key that already wrote. The default is the
 * revision `resultFixture()` describes, written as a creation by this seat — so
 * a re-computation reproduces it and the advance replays.
 */
const recordedFixture = (over = {}) => ({
  schema_version: "doctorcre-v5-journey-one-clock-request-readback.v1",
  exists: true,
  clock_key: journeyOneClockKeyForState(resultFixture().state),
  clock_scope_key: SCOPE_KEY,
  clock_scope_ref: SCOPE.scope_ref,
  clock_scope_bound: true,
  written_by_actor_id: "claude",
  revision_ordinal: 0,
  history_digest: D(90),
  expected_prior_history_digest: null,
  prior_revision_ordinal: null,
  prior_history: null,
  recorded_at: "2026-02-04T00:00:01.000Z",
  ...over,
});

// ---------------------------------------------------------------------------
// 1. Construction: every rail this seat needs, and the one scope across two.
// ---------------------------------------------------------------------------

test("a runtime cannot be constructed without each rail it joins", () => {
  const good = doubles();
  const base = { composer: good.composer, clock: good.clock, clock_store: good.clock_store,
    present_projection: () => ({}), verifier_ref: VERIFIER_REF };
  for (const [field, code] of [
    ["composer", "invalid_shape"],
    ["clock", "authenticated_kernel_required"],
    ["clock_store", "invalid_shape"],
    ["present_projection", "clock_presentation_seam_required"],
  ]) {
    assert.throws(() => createJourneyOneClockRuntime({ ...base, [field]: undefined }),
      error => {
        assert.equal(error.name, "JourneyOneClockRuntimeError");
        assert.equal(error.code, code);
        return true;
      }, `${field} must be required`);
  }
  assert.throws(() => createJourneyOneClockRuntime({}), /./);
});

test("a store with no authoritative scope cannot be advanced through", () => {
  const good = doubles();
  assert.throws(() => createJourneyOneClockRuntime({
    composer: good.composer, clock: good.clock,
    clock_store: { ...good.clock_store, clock_scope: null },
    present_projection: () => ({}), verifier_ref: VERIFIER_REF }),
  error => {
    assert.equal(error.code, "clock_scope_binding_required");
    assert.equal(error.detail.invariant, "j1_clock_scope_binds_one_clock");
    return true;
  });
});

test("a composer and a store bound to different scopes refuse at construction", () => {
  const composerSide = doubles();
  const storeSide = doubles({ scope: { ...SCOPE, benchmark_subject_digest: D(50) } });
  assert.throws(() => createJourneyOneClockRuntime({
    composer: composerSide.composer, clock: composerSide.clock,
    clock_store: storeSide.clock_store,
    present_projection: () => ({}), verifier_ref: VERIFIER_REF }),
  error => {
    assert.equal(error.code, "clock_runtime_scope_disagreement");
    assert.equal(error.detail.composer_clock_scope_key, SCOPE_KEY);
    assert.notEqual(error.detail.store_clock_scope_key, SCOPE_KEY);
    return true;
  });
  // Nothing was read to discover it: the disagreement is structural.
  assert.deepEqual(composerSide.calls, []);
  assert.deepEqual(storeSide.calls, []);
});

// ---------------------------------------------------------------------------
// 2. The two derived facts, and the order the refusals happen in.
// ---------------------------------------------------------------------------

test("the history and the append prior are DERIVED from one read, never supplied", async () => {
  for (const field of JOURNEY_ONE_CLOCK_DERIVED_NOT_SUPPLIED_FIELDS) {
    const { runtime, calls } = doubles();
    await assert.rejects(runtime.advance({ ...advanceArgs(), [field]: null }), error => {
      assert.equal(error.code, "clock_runtime_history_is_derived");
      assert.equal(error.detail.path, `advance.${field}`);
      assert.equal(error.detail.invariant, "j1_clock_exact_prior_history_digest");
      return true;
    });
    // Refused before anything was read, composed, evaluated or written.
    assert.deepEqual(calls, [], `${field} must refuse ahead of every call`);
  }
});

test("one read of the head supplies BOTH the composed history and the compare-and-swap prior", async () => {
  const history = { schema_version: "doctorcre-v5-journey-one-clock.v2",
    origin_receipt_digest: ORIGIN_DIGEST, history_digest: D(91) };
  // The composition carries that same head, because a composition that did not
  // is refused by name — see the composed-history check below.
  const composition = compositionFixture({ projection: { history: copy(history) } });
  const { runtime, seen, calls } = doubles({
    composition, result: resultFor(composition),
    head: { clock_key: D(60), exists: true, history_digest: D(91),
      head_revision_ordinal: 3, history },
  });
  const advanced = await runtime.advance(advanceArgs());

  // The projection was composed against the exact head this rail read...
  assert.deepEqual(seen.compose.history, history);
  // ...and the append names that same head as its prior. One read, both facts.
  assert.equal(seen.record.expected_prior_history_digest, D(91));
  assert.equal(advanced.expected_prior_history_digest, D(91));
  assert.equal(advanced.composed_from.prior_history_digest, D(91));
  assert.equal(advanced.composed_from.prior_revision_ordinal, 3);
  assert.equal(advanced.created_clock, false);
  // And the order is fixed: the request read first — this key wrote nothing —
  // then the head, exactly once, then the composition.
  assert.deepEqual(calls.slice(0, 4),
    ["readRecordedRevisionForKey", "readClockKeyForScope", `read:${D(60)}`, "compose"]);
  assert.equal(calls.filter(c => c.startsWith("read:")).length, 1);
  assert.equal(seen.readRecordedRevisionForKey, advanceArgs().idempotency_key);
});

test("a scope holding no clock composes against a null history and creates", async () => {
  const { runtime, seen, calls } = doubles();
  const advanced = await runtime.advance(advanceArgs());
  assert.equal(seen.compose.history, null);
  assert.equal(seen.record.expected_prior_history_digest, null);
  assert.equal(advanced.created_clock, true);
  assert.equal(advanced.composed_from.prior_revision_ordinal, null);
  // No clock to read, so no readback was issued for one.
  assert.deepEqual(calls.filter(c => c.startsWith("read:")), []);
  assert.deepEqual(seen.compose.pauses, []);
  assert.deepEqual(Object.keys(seen.compose).sort(), [...JOURNEY_ONE_MINIMUM_COMPOSE_FIELDS]);
});

test("a composition carrying a history that is not the prior refuses before any write", async () => {
  // The scope is re-checked on the returned artifact for a stated reason: the
  // construction check was against the composer OBJECT. The same reasoning
  // applies to the history, and this is that check — it turns "the composer
  // forwards what it was handed" from a trusted-collaborator assumption into a
  // structural one.
  const head = { clock_key: D(60), exists: true, history_digest: D(91),
    head_revision_ordinal: 0, history: { schema_version: "v2", history_digest: D(91) } };
  for (const [label, composition] of [
    ["a null history for a scope that holds a clock", compositionFixture()],
    ["another revision's history", compositionFixture({ projection: {
      history: { schema_version: "v2", history_digest: D(92) } } })],
  ]) {
    const { runtime, calls } = doubles({ head, composition });
    await assert.rejects(runtime.advance(advanceArgs()), error => {
      assert.equal(error.name, "JourneyOneClockRuntimeError");
      assert.equal(error.code, "clock_runtime_composed_history_is_not_the_prior");
      assert.equal(error.detail.invariant, "j1_clock_exact_prior_history_digest");
      assert.equal(error.detail.expected_prior_history_digest, D(91));
      assert.equal(error.detail.replaying_recorded_request, false);
      return true;
    }, label);
    assert.ok(!calls.includes("evaluate"), `${label}: nothing is evaluated for it`);
    assert.ok(!calls.includes("record"));
  }
  // And the honest case passes: a composition carrying exactly the head.
  const honest = compositionFixture({ projection: { history: copy(head.history) } });
  const { runtime } = doubles({ head, composition: honest, result: resultFor(honest) });
  const advanced = await runtime.advance(advanceArgs());
  assert.equal(advanced.composed_from.prior_history_digest, D(91));
});

test("a scope naming a clock the record layer cannot produce refuses rather than creating a second one", async () => {
  const { runtime, calls } = doubles({ head: { clock_key: D(60), exists: false } });
  await assert.rejects(runtime.advance(advanceArgs()), error => {
    assert.equal(error.code, "clock_runtime_bound_clock_unreadable");
    assert.equal(error.detail.invariant, "j1_clock_scope_binds_one_clock");
    assert.equal(error.detail.clock_key, D(60));
    return true;
  });
  assert.ok(!calls.includes("compose"), "nothing is composed against a missing head");
  assert.ok(!calls.includes("record"));
});

test("advance takes exactly its declared fields, and a smuggled authority claim is named", async () => {
  const { runtime } = doubles();
  const missing = advanceArgs();
  delete missing.pauses;
  await assert.rejects(runtime.advance(missing), error => {
    assert.equal(error.code, "closed_shape");
    assert.deepEqual(error.detail.expected, [...JOURNEY_ONE_CLOCK_ADVANCE_FIELDS]);
    return true;
  });
  await assert.rejects(runtime.advance({ ...advanceArgs(), verified: true }), error => {
    assert.equal(error.name, "BenchmarkAcceptanceStoreError");
    assert.equal(error.code, "self_asserted_authority_refused");
    return true;
  });
  await assert.rejects(runtime.advance({ ...advanceArgs(), clock_started: true }), error => {
    assert.equal(error.code, "self_asserted_authority_refused");
    return true;
  });
  // The declared inventories keep the vocabulary of the acts they record: an
  // amendment IS an accepted_at and an accepted_by_identity, and the guard must
  // not refuse the thing it exists to let through.
  const amendment = { amendment_ref: "safe:j1:amendment-one", accepted_at: AS_OF,
    accepted_by_identity: { actor_id: "joe", session_ref: "session:x",
      authority_class: "verified_partner" } };
  const { runtime: second, seen } = doubles();
  await second.advance(advanceArgs({ amendments: [amendment] }));
  assert.deepEqual(seen.compose.amendments, [amendment]);
});

test("a presentation that returns no envelope refuses, and nothing is evaluated", async () => {
  const { runtime, calls } = doubles({ presented: () => null });
  await assert.rejects(runtime.advance(advanceArgs()), error => {
    assert.equal(error.code, "clock_presentation_failed");
    return true;
  });
  assert.ok(calls.includes("compose"));
  assert.ok(!calls.includes("evaluate"));
  assert.ok(!calls.includes("record"));
});

test("the composed projection is presented as a copy, so a presentation cannot edit what it is bound against", async () => {
  const composition = compositionFixture();
  let handed = null;
  const { runtime, seen } = doubles({ composition,
    presented: projection => { handed = projection; projection.tenant = "tampered"; return { e: 1 }; } });
  const advanced = await runtime.advance(advanceArgs());
  assert.notEqual(handed, composition.projection);
  assert.equal(composition.projection.tenant, ORGANIZATION_TENANT_ID);
  assert.equal(advanced.composition_binding.bound, true);
  assert.deepEqual(seen.envelope, { e: 1 });
});

// ---------------------------------------------------------------------------
// 2b. The retry seam: a recorded request is replayed only to its own seat.
// ---------------------------------------------------------------------------

test("a store that cannot say what a key already wrote cannot be advanced through", () => {
  const good = doubles();
  const blind = { ...good.clock_store };
  delete blind.readRecordedRevisionForKey;
  assert.throws(() => createJourneyOneClockRuntime({
    composer: good.composer, clock: good.clock, clock_store: blind,
    present_projection: () => ({}), verifier_ref: VERIFIER_REF }),
  error => {
    assert.equal(error.code, "clock_runtime_request_read_required");
    assert.equal(error.detail.invariant, "j1_clock_idempotency_key_binds_its_payload");
    return true;
  });
  // And a store with no derived writer cannot compare a recorded seat to itself.
  assert.throws(() => createJourneyOneClockRuntime({
    composer: good.composer, clock: good.clock,
    clock_store: { ...good.clock_store, writer: null },
    present_projection: () => ({}), verifier_ref: VERIFIER_REF }),
  error => {
    assert.equal(error.code, "clock_writer_identity_unavailable");
    return true;
  });
});

test("a recorded request is re-computed against ITS OWN prior, not the head", async () => {
  // The head has moved on — that is what a lost response looks like from here —
  // and the retry must not compose against it.
  const movedHead = { clock_key: D(60), exists: true, history_digest: D(91),
    head_revision_ordinal: 3, history: { schema_version: "x", history_digest: D(91) } };
  const { runtime, seen, calls } = doubles({ head: movedHead,
    recordedForKey: recordedFixture() });
  const replayed = await runtime.advance(advanceArgs());

  // Composed against the RECORDED prior (a creation: null), never the head.
  assert.equal(seen.compose.history, null);
  assert.equal(replayed.composed_from.prior_source, "recorded_request_prior");
  assert.equal(replayed.expected_prior_history_digest, null);
  assert.equal(replayed.created_clock, true);
  // NOTHING WAS WRITTEN, and the head was never even read.
  assert.equal(replayed.replayed, true);
  assert.equal(replayed.appended, false);
  assert.equal(replayed.replayed_recorded_request, true);
  assert.ok(!calls.includes("record"), "a replay appends nothing");
  assert.ok(!calls.includes("readClockKeyForScope"),
    "a retry is about the recorded prior, so the head is not consulted");
  assert.deepEqual(replayed.effects, JOURNEY_ONE_CLOCK_REPLAY_EFFECTS);
  assert.equal(replayed.effects.database_writes, 0);
  assert.equal(replayed.effects.history_appended, false);
  // The kernel still ran and the binding still held: a replay is checked, not
  // trusted.
  assert.equal(replayed.composition_binding.bound, true);
  assert.equal(replayed.kernel_verdict.status, "running");
  assert.equal(replayed.clock_key, journeyOneClockKeyForState(resultFixture().state));
});

test("a replay of a LATER revision reports the prior ordinal the store derived", async () => {
  // `null` on this field means "there was no prior" on the append path, so a
  // replay of a later revision must not report it — the store carries the
  // ordinal of the row it actually rebuilt, and this seat forwards that.
  const priorHistory = { schema_version: "v2", history_digest: D(91) };
  const composition = compositionFixture({ projection: { history: copy(priorHistory) } });
  const { runtime } = doubles({ composition, result: resultFor(composition),
    recordedForKey: recordedFixture({ revision_ordinal: 4,
      expected_prior_history_digest: D(91), prior_revision_ordinal: 3, prior_history: priorHistory }) });
  const replayed = await runtime.advance(advanceArgs());
  assert.equal(replayed.replayed, true);
  assert.equal(replayed.revision_ordinal, 4);
  assert.equal(replayed.composed_from.prior_revision_ordinal, 3);
  assert.equal(replayed.composed_from.prior_history_digest, D(91));
  assert.equal(replayed.created_clock, false);
  // And the derived scope flag is a comparison, not a literal.
  assert.equal(replayed.clock_scope_matches_verified_binding, true);
  assert.equal(replayed.clock_scope_key, SCOPE_KEY);
});

test("a retry that cannot be re-composed says the request LANDED, and names the cause", async () => {
  // A composer refusal on the retry arm is indistinguishable, bare, from a first
  // attempt with a bad as_of — and a caller reading it as failure re-keys and
  // appends a second revision of work already filed.
  const refusal = Object.assign(new Error("as_of precedes the latest admission"),
    { name: "JourneyOneMinimumInputStoreError", code: "projection_as_of_precedes_admission" });
  const recorded = recordedFixture({ revision_ordinal: 2,
    expected_prior_history_digest: D(91), prior_revision_ordinal: 1,
    prior_history: { schema_version: "v2", history_digest: D(91) } });
  const { runtime, calls, composer } = doubles({ recordedForKey: recorded });
  composer.compose = async () => { calls.push("compose"); throw refusal; };

  await assert.rejects(runtime.advance(advanceArgs()), error => {
    assert.equal(error.name, "JourneyOneClockRuntimeError");
    assert.equal(error.code, "clock_runtime_recorded_request_not_recomputable");
    assert.equal(error.detail.request_landed, true);
    assert.equal(error.detail.appended, false);
    assert.equal(error.detail.recorded_clock_key, recorded.clock_key);
    assert.equal(error.detail.recorded_history_digest, recorded.history_digest);
    assert.equal(error.detail.recorded_revision_ordinal, 2);
    assert.equal(error.detail.recorded_expected_prior_history_digest, D(91));
    assert.equal(error.detail.cause_code, "projection_as_of_precedes_admission");
    assert.equal(error.detail.cause_name, "JourneyOneMinimumInputStoreError");
    assert.ok(error.message.includes("ALREADY LANDED"));
    return true;
  });
  assert.ok(!calls.includes("evaluate"));
  assert.ok(!calls.includes("record"));

  // ON A FIRST ATTEMPT THE SAME REFUSAL TRAVELS UNCHANGED: there is no recorded
  // revision to name, and wrapping it would invent one.
  const fresh = doubles();
  fresh.composer.compose = async () => { throw refusal; };
  await assert.rejects(fresh.runtime.advance(advanceArgs()), error => {
    assert.equal(error.code, "projection_as_of_precedes_admission");
    return true;
  });
});

test("a recorded request whose re-computation differs is a second request, not a retry", async () => {
  const { runtime, calls } = doubles({
    recordedForKey: recordedFixture({ history_digest: D(92) }) });
  await assert.rejects(runtime.advance(advanceArgs()), error => {
    assert.equal(error.code, "clock_idempotency_key_reused");
    assert.equal(error.detail.invariant, "j1_clock_idempotency_key_binds_its_payload");
    assert.equal(error.detail.recorded.history_digest, D(92));
    assert.equal(error.detail.recomputed.history_digest, D(90));
    return true;
  });
  assert.ok(!calls.includes("record"));
});

test("a recorded request is never replayed across scope or actor", async () => {
  for (const [label, recorded, code] of [
    ["another scope", recordedFixture({ clock_scope_key: D(55) }),
      "clock_runtime_replay_scope_mismatch"],
    ["a clock with no scope binding", recordedFixture({ clock_scope_key: null,
      clock_scope_bound: false }), "clock_runtime_replay_scope_mismatch"],
    ["another actor", recordedFixture({ written_by_actor_id: "codex" }),
      "clock_runtime_replay_actor_mismatch"],
  ]) {
    const { runtime, calls } = doubles({ recordedForKey: recorded });
    await assert.rejects(runtime.advance(advanceArgs()), error => {
      assert.equal(error.name, "JourneyOneClockRuntimeError");
      assert.equal(error.code, code);
      return true;
    }, label);
    // Refused before anything was composed, evaluated or written: a stored
    // success is never handed to a seat that did not produce it.
    assert.deepEqual(calls, ["readRecordedRevisionForKey"], label);
  }
});

test("a recorded request whose clock cannot be re-derived refuses rather than replaying", async () => {
  const { runtime } = doubles({ recordedForKey: recordedFixture({ clock_key: D(56) }) });
  await assert.rejects(runtime.advance(advanceArgs()), error => {
    assert.equal(error.code, "clock_identity_mismatch");
    assert.equal(error.detail.invariant, "j1_clock_identity_derived_from_origin");
    return true;
  });
});

test("the advance receipt carries exactly its declared shape, on both paths", async () => {
  const written = await doubles().runtime.advance(advanceArgs());
  const replayed = await doubles({ recordedForKey: recordedFixture() })
    .runtime.advance(advanceArgs());
  for (const [label, receipt] of [["appended", written], ["replayed", replayed]]) {
    assert.deepEqual(Object.keys(receipt).sort(),
      [...JOURNEY_ONE_CLOCK_ADVANCE_RECEIPT_FIELDS].sort(), label);
    assert.deepEqual(Object.keys(receipt.composed_from).sort(),
      [...JOURNEY_ONE_CLOCK_ADVANCE_COMPOSED_FROM_FIELDS].sort(), label);
    assert.equal(receipt.schema_version, "doctorcre-v5-journey-one-clock-advance.v1");
    assert.equal(receipt.durable_history_write_required, false, label);
    assert.equal(receipt.deadline_accepted_by_record_layer, false, label);
    assert.equal(receipt.authenticated_projection_verified_here, false, label);
    assert.equal(Object.isFrozen(receipt), true, label);
  }
  // ONE KEY SET, TWO VALUE SETS: a consumer reads the same effects shape on
  // either path and never an absent key it would take for false.
  assert.deepEqual(Object.keys(written.effects).sort(), Object.keys(replayed.effects).sort());
  assert.equal(written.effects.database_writes, 1);
  assert.equal(written.appended, true);
  assert.equal(replayed.appended, false);
});

// ---------------------------------------------------------------------------
// 3. The binding: is this computation the projection this record layer composed?
// ---------------------------------------------------------------------------

test("a computation made from the composed projection binds, and says what a pass is not", () => {
  const composition = compositionFixture();
  const bound = assertJourneyOneClockComputedFromComposition(resultFixture(), composition);
  assert.equal(bound.bound, true);
  assert.equal(bound.origin_receipt_digest, ORIGIN_DIGEST);
  assert.equal(bound.head_admission_digest, D(80));
  assert.equal(bound.admission_count, 1);
  assert.equal(bound.authenticated_here, false);
  assert.deepEqual(bound.facts, JOURNEY_ONE_CLOCK_COMPOSITION_BINDING_FACTS.map(f => f.id));
  // THE PROOF IS NAMED AS THE PROOF, and both sides of it are reported so a
  // reader can recompute the comparison rather than believe the boolean.
  assert.equal(bound.proof_fact, JOURNEY_ONE_CLOCK_COMPOSITION_PROOF_FACT);
  assert.equal(bound.composed_projection_digest, digest(composition.projection));
  assert.equal(bound.authenticated_projection_digest, digest(composition.projection));
});

test("exactly one declared fact is the proof; the rest declare themselves diagnostics", () => {
  const facts = JOURNEY_ONE_CLOCK_COMPOSITION_BINDING_FACTS;
  const proofs = facts.filter(f => f.role === "proof");
  assert.deepEqual(proofs.map(f => f.id), [JOURNEY_ONE_CLOCK_COMPOSITION_PROOF_FACT]);
  assert.equal(facts.filter(f => f.role === "diagnostic").length, facts.length - 1);
  // The list says out loud what the field checks cannot see, because an earlier
  // revision claimed they established identity and they do not.
  const pauseFact = facts.find(f => f.id === "pause_intervals");
  assert.ok(pauseFact.statement.includes("does NOT see a pause's start"));
  assert.ok(proofs[0].statement.includes("only entry that establishes identity"));
});

test("one instant spelled two ways is one instant, and is not a mismatch", () => {
  // The kernel re-renders every instant through toISOString(), so a composed
  // as_of written with an explicit offset comes back as Z. A text comparison in
  // the as_of DIAGNOSTIC would report a difference that does not exist — and the
  // proof still holds, because the judged projection is that exact composition.
  const composition = compositionFixture({ projection: { as_of: "2026-02-04T00:00:00+00:00" } });
  const bound = assertJourneyOneClockComputedFromComposition(resultFor(composition), composition);
  assert.equal(bound.bound, true);
});

test("each composition binding DIAGNOSTIC refuses by name when it can see the divergence", () => {
  // Every case here judges the composition it is checked against, so the named
  // check under test is what fires rather than the digest catching it first.
  const cases = [
    ["tenant", c => resultFor(c, {}, { tenant: "someone-else" })],
    ["binding_digests", c => resultFor(c, {}, { candidate_digest: D(51) })],
    ["as_of", c => resultFor(c, { evaluated_at: "2026-02-05T00:00:00.000Z" })],
    ["benchmark_manifest_digest", c => resultFor(c, { current_benchmark_manifest_digest: D(52) })],
    ["minimum_receipt_ttl_policy", c => resultFor(c, { origin_receipt_ttl_policy_ms: 3 * DAY })],
    ["pause_intervals", c => resultFor(c, { pause_intervals: [{ pause_id: "safe:p", ends_at: null }] })],
    ["origin_admission", c => resultFor(c, { origin_at: "2026-02-03T06:00:00.000Z" })],
  ];
  for (const [fact, build] of cases) {
    const composition = compositionFixture();
    assert.throws(() => assertJourneyOneClockComputedFromComposition(build(composition), composition),
      error => {
        assert.equal(error.name, "JourneyOneClockRuntimeError");
        assert.equal(error.code, "clock_computation_not_the_composed_projection");
        assert.equal(error.detail.fact, fact);
        return true;
      }, `${fact} must refuse by name`);
  }
  // Every declared DIAGNOSTIC is driven above, and the proof is driven by its own
  // test below. A declared fact with no case would be a rule with nothing
  // enforcing it.
  assert.deepEqual(cases.map(c => c[0]).sort(),
    JOURNEY_ONE_CLOCK_COMPOSITION_BINDING_FACTS
      .filter(f => f.role === "diagnostic").map(f => f.id).sort());
});

test("a DIFFERENT valid projection sharing every named fact is refused by the proof", () => {
  // THE CASE THE SEVEN NAMED CHECKS CANNOT SEE, in isolation: the judged
  // projection agrees on tenant, scope digests, as_of, manifest, sealed TTL
  // policy, origin admission and pause ids and ends, and differs somewhere none
  // of them reads. Each variation below is a real projection field.
  const composed = compositionFixture();
  const elsewhere = [
    ["completion_expectation", compositionFixture({ projection: {
      completion_expectation: { artifact_digest: D(70), fixture_set_digest: D(71) } } })],
    ["amendments", compositionFixture({ projection: { amendments: [{ amendment_ref: "safe:a" }] } })],
    ["history", compositionFixture({ projection: { history: { schema_version: "x" } } })],
    ["maximum_completion_receipt_ttl_ms", compositionFixture({ projection: {
      binding: { ...projectionFixture().binding, maximum_completion_receipt_ttl_ms: 99 * DAY } } })],
  ];
  for (const [label, judged] of elsewhere) {
    assert.notEqual(digest(judged.projection), digest(composed.projection), `${label} must differ`);
    assert.throws(
      () => assertJourneyOneClockComputedFromComposition(resultFor(judged), composed),
      error => {
        assert.equal(error.name, "JourneyOneClockRuntimeError");
        assert.equal(error.code, "clock_computation_projection_digest_mismatch");
        assert.equal(error.detail.fact, JOURNEY_ONE_CLOCK_COMPOSITION_PROOF_FACT);
        assert.equal(error.detail.composed_projection_digest, digest(composed.projection));
        assert.equal(error.detail.authenticated_projection_digest, digest(judged.projection));
        // It proves they differ and does not claim to know how.
        assert.ok(error.detail.differing_fields_unavailable.includes("not derivable here"));
        return true;
      }, `${label} must be refused by the proof`);
  }
});

test("a binding with no usable projection digest cannot establish identity and refuses", () => {
  // None of these is a sha256 reference, so none can be compared against the
  // composed digest. A MISSING PROOF IS NOT A PASSING ONE: the binding refuses
  // rather than falling back to the diagnostics, which cannot establish identity
  // between them.
  for (const bad of [undefined, null, "not-a-digest", 7, `SHA256:${"53".repeat(32)}`]) {
    const composition = compositionFixture();
    const result = resultFixture({}, { authenticated_projection_digest: bad });
    assert.throws(() => assertJourneyOneClockComputedFromComposition(result, composition),
      error => {
        assert.equal(error.name, "JourneyOneClockRuntimeError");
        assert.equal(error.code, "invalid_shape");
        assert.equal(error.detail.path, "result.verified_binding.authenticated_projection_digest");
        return true;
      }, `${String(bad)} must not pass as a proof`);
  }
});

test("an origin the composed inventory never carried is refused, with the digests it did carry", () => {
  const composition = compositionFixture();
  const result = resultFixture({ origin_receipt_digest: D(53) });
  assert.throws(() => assertJourneyOneClockComputedFromComposition(result, composition), error => {
    assert.equal(error.code, "clock_computation_origin_not_in_composed_inventory");
    assert.equal(error.detail.fact, "origin_admission");
    assert.equal(error.detail.matching_admissions, 0);
    assert.equal(error.detail.composed_admission_count, 1);
    assert.deepEqual(error.detail.composed_receipt_digests, [ORIGIN_DIGEST]);
    assert.ok(error.message.includes("not a receipt the composed inventory carries"));
    return true;
  });
});

test("a composed inventory carrying one receipt twice is refused rather than quietly matched", () => {
  const composition = compositionFixture({ projection: { minimum_history: [
    { admitted_at: ADMITTED_AT, receipt: copy(RECEIPT) },
    { admitted_at: ADMITTED_AT, receipt: copy(RECEIPT) },
  ] } });
  assert.throws(() => assertJourneyOneClockComputedFromComposition(resultFixture(), composition),
    error => {
      assert.equal(error.code, "clock_computation_origin_not_in_composed_inventory");
      assert.equal(error.detail.matching_admissions, 2);
      assert.ok(error.message.includes("duplicate_minimum"));
      return true;
    });
});

test("the binding refuses a wrapper or a result it cannot read, rather than passing it", () => {
  const bad = [
    [resultFixture(), null],
    [resultFixture(), { schema_version: "something-else", projection: projectionFixture() }],
    [resultFixture(), compositionFixture({ projection: { schema_version: "not-the-projection" } })],
    [{ state: null }, compositionFixture()],
    [{ state: {}, verified_binding: { schema_version: "wrong" } }, compositionFixture()],
    // THE WRAPPER'S EVIDENCE IS REQUIRED, NOT DEFAULTED. This function is
    // exported and callable outside the loop; a wrapper that omits the head
    // admission digest used to yield `bound: true` reporting it as null, which
    // reads as "the inventory had no head" rather than "nobody said".
    [resultFixture(), compositionFixture({ head_admission_digest: undefined })],
    [resultFixture(), compositionFixture({ head_admission_digest: "not-a-digest" })],
    [resultFixture(), compositionFixture({ admission_count: undefined })],
    [resultFixture(), compositionFixture({ admission_count: 0 })],
  ];
  for (const [result, composition] of bad) {
    assert.throws(() => assertJourneyOneClockComputedFromComposition(result, composition),
      error => {
        assert.equal(error.name, "JourneyOneClockRuntimeError");
        assert.equal(error.code, "invalid_shape");
        return true;
      });
  }
});

test("the binding runs BEFORE the store is called, so an unbound computation writes nothing", async () => {
  const { runtime, calls } = doubles({ result: resultFixture({ origin_receipt_digest: D(53) }) });
  await assert.rejects(runtime.advance(advanceArgs()), error => {
    assert.equal(error.code, "clock_computation_origin_not_in_composed_inventory");
    return true;
  });
  assert.ok(calls.includes("evaluate"));
  assert.ok(!calls.includes("record"), "nothing is filed for a computation that is not bound");
});

// ---------------------------------------------------------------------------
// 4. The field sets, the descriptor, and what this file refuses to be.
// ---------------------------------------------------------------------------

test("the advance fields are the composer's own, minus what this seat derives, plus its two", () => {
  const forwarded = JOURNEY_ONE_MINIMUM_COMPOSE_FIELDS.filter(
    f => !JOURNEY_ONE_CLOCK_RUNTIME_DERIVED_COMPOSE_FIELDS.includes(f));
  for (const field of forwarded) {
    assert.ok(JOURNEY_ONE_CLOCK_ADVANCE_FIELDS.includes(field),
      `advance no longer forwards ${field}, which the composer requires`);
  }
  assert.deepEqual(JOURNEY_ONE_CLOCK_ADVANCE_FIELDS.filter(f => !forwarded.includes(f)),
    ["clock_ref", "idempotency_key"]);
  // And nothing declared derived is still an advance field.
  for (const field of JOURNEY_ONE_CLOCK_DERIVED_NOT_SUPPLIED_FIELDS) {
    assert.ok(!JOURNEY_ONE_CLOCK_ADVANCE_FIELDS.includes(field));
  }
  assert.deepEqual([...JOURNEY_ONE_CLOCK_DERIVED_NOT_SUPPLIED_FIELDS],
    ["expected_prior_history_digest", "history"]);
});

test("the descriptor says the loop cannot run here, and names what blocks it", () => {
  const d = journeyOneClockRuntimeIntegrationRequirements();
  assert.equal(d.loop_implemented, true);
  assert.equal(d.runnable_in_this_repository, false);
  assert.equal(d.clock_started, false);
  assert.equal(d.effects.database_writes, 0);
  assert.ok(d.blocked_by.some(l => l.includes("proposed_not_issued")),
    "the missing issuance producer is why no PRODUCTION inventory exists");
  // "No issued receipt" and "no receipt can be admitted" are different findings,
  // and this file's own suites admit fixture receipts through the real rails.
  assert.ok(d.not_runnable_means.includes("ISSUED"));
  assert.ok(d.not_runnable_means.includes("fixture receipts"));
  assert.ok(d.blocked_by.some(l => l.includes("a fixture receipt admitted by a suite")));
  // The one requirement this loop imposes on a reader that has yet to land.
  assert.ok(d.blocked_by.some(l => l.includes("CANONICALLY IDENTICAL")),
    "a verifier that re-derives an equivalent projection would refuse every advance");
  assert.ok(d.blocked_by.some(l => l.includes("Key order is free")));
  // The retry semantics, stated where a reader looks for behaviour.
  assert.ok(d.loop_notes.some(l => l.includes("IDEMPOTENT ACROSS SEQUENTIAL RETRIES")));
  assert.ok(d.loop_notes.some(l => l.includes("EXACT prior")));
  assert.ok(d.cannot_prove.some(l => l.includes("REPLAYED receipt describes the current head")));
  assert.ok(d.starts_no_clock.includes("DECIDES no start"),
    "the kernel starts a clock; a first advance is the write that files one");
  assert.ok(d.blocked_by.some(l => l.includes("journey-one-clock-authenticated-projection")));
  assert.ok(d.blocked_by.some(l => l.includes("numbered migration")),
    "no durable journal exists to advance against");
  assert.ok(d.starts_no_clock.includes("NO CLOCK HAS BEEN STARTED"));
  assert.ok(d.starts_no_clock.includes("not a proof of absence"),
    "an absence here is not a claim about the record as a whole");
  assert.deepEqual(d.binding_facts.map(f => f.id),
    JOURNEY_ONE_CLOCK_COMPOSITION_BINDING_FACTS.map(f => f.id));
  // THE DESCRIPTOR NAMES THE PROOF AND DOES NOT SELL THE DIAGNOSTICS AS ONE.
  assert.equal(d.proof_fact, JOURNEY_ONE_CLOCK_COMPOSITION_PROOF_FACT);
  assert.equal(d.verified_binding_schema_version,
    "doctorcre-v5-journey-one-clock-verified-binding.v2");
  assert.deepEqual(d.binding_facts.filter(f => f.role === "proof").map(f => f.id),
    [JOURNEY_ONE_CLOCK_COMPOSITION_PROOF_FACT]);
  assert.ok(d.loop_notes.some(l => l.includes("authenticated_projection_digest")));
  assert.ok(d.loop_notes.some(l => l.includes("are not the proof")),
    "the descriptor must say the named field checks do not establish identity");
  assert.deepEqual(d.advance_fields, [...JOURNEY_ONE_CLOCK_ADVANCE_FIELDS]);
  assert.deepEqual(d.cannot_prove, [...JOURNEY_ONE_CLOCK_RUNTIME_CANNOT_PROVE]);
  assert.ok(d.cannot_prove.some(l => l.includes("weaker statement")),
    "identity is not authenticity, and the descriptor must keep saying so");
  assert.ok(d.cannot_prove.some(l => l.includes("PRE-WRITE GATE and not a durable receipt")),
    "the proof gates a write; it is not recoverable from a stored revision");
  assert.ok(d.cannot_prove.some(l => l.includes("as exact as sha256 and is not stronger")),
    "the proof is a digest comparison and claims exactly that much");
  assert.ok(d.explicitly_refused.some(l => l.includes("verifySnapshot")));
  assert.ok(d.explicitly_refused.some(l => l.includes("public verb")));
});

test("this seat imports no kernel constructor, invokes no verifier and registers no verb", async () => {
  const source = readFileSync(
    fileURLToPath(new URL("../src/journey-one-clock-runtime.v5.js", import.meta.url)), "utf8");

  // IT CANNOT CONSTRUCT A KERNEL, because it does not import the constructor.
  // Checked against the import itself rather than against prose: the descriptor
  // and the refusal messages NAME createJourneyOneClock({ verifySnapshot }) on
  // purpose, so a substring scan would find the sentence and prove nothing.
  const kernelImport =
    /import\s*\{([^}]*)\}\s*from\s*"\.\/journey-one-clock\.v5\.js";/.exec(source);
  assert.ok(kernelImport, "the runtime must import from the kernel module by name");
  assert.deepEqual(
    kernelImport[1].split(",").map(name => name.trim()).filter(Boolean).sort(),
    ["JOURNEY_ONE_CLOCK_PROJECTION", "JOURNEY_ONE_CLOCK_VERIFIED_BINDING"],
    "the runtime takes an already-constructed kernel and imports only the two schema constants");

  // AND IT NEVER CALLS OR INSTALLS A VERIFIER. Naming one in a sentence is the
  // point; invoking one or passing one as a property would be the defect.
  const code = source.split("\n").filter(line => !/^\s*(\*|\/\/|\/\*)/.test(line)).join("\n");
  assert.ok(!code.includes("verifySnapshot("), "the runtime must never invoke a verifier");
  assert.ok(!code.includes("verifySnapshot:"), "the runtime must never install a verifier");

  // It exposes no tools: registering a verb is a separate reviewed act, and both
  // rails' public write verbs still fail closed.
  const module = await import("../src/journey-one-clock-runtime.v5.js");
  assert.deepEqual(Object.keys(module).filter(name => name.endsWith("Tools")), []);
  assert.ok(!code.includes("inputSchema"));
});
