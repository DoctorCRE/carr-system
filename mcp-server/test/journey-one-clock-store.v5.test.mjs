// V5-M01 durable clock-history store. Every test drives the REAL storage
// mechanics — identity derivation, row decomposition, whole-content
// reconstruction, digest recomputation, exact-prior compare-and-swap,
// idempotency binding, the append-only diff and the deterministic readback —
// against real kernel output produced by journey-one-clock.v5.js.
//
// WHAT IS AND IS NOT PROVED HERE, so nobody reads more into a green run:
//   * PROVED: the store's own logic, over the non-durable reference journal.
//     The kernel is called for real; no deadline, status or event is
//     hand-written by this file except where a test deliberately tampers.
//   * NOT PROVED: anything about ops/journey-one-clock-store.candidate.sql.
//     It has never been executed — the local initdb is blocked and no remote
//     database was used as a workaround. The only mechanical claim made about
//     it here is textual: that it states every shared append invariant and
//     contains no ALTER or backfill. mcp-server/test/journey-one-clock-store-postgres.sql
//     is the proof that has to run against a database, and it has not.
//   * NOT PROVED: that ops.j1_clock_history_digest and this module compute the
//     same hash for the same history. Both are asserted to hash the canonical
//     serialization of the same twenty-field preimage, and the SQL side reuses
//     ops.portfolio_canonical_json, which migration 0496 already reconciles
//     against artifact-trust.js's canonicalJson — but a JavaScript suite cannot
//     execute SQL. It is the single most important thing to check first when
//     this rail is exercised end to end. The SCOPE key has exactly the same
//     status: a disagreement between ops.j1_clock_scope_digest and
//     journeyOneClockScopeKey would file one program's clock under two scope
//     keys, one per language, and the uniqueness both sides rely on would hold
//     over two different sets rather than one.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { digest } from "../src/artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  JOURNEY_ONE_CLOCK_PROJECTION, JOURNEY_ONE_CLOCK_SCHEMA, JOURNEY_ONE_DEADLINE_CONTRACT,
  JOURNEY_ONE_CLOCK_VERIFIED_BINDING, DEADLINE_GAP_SHIFTED, DEADLINE_OVERLAP_ORIGIN_OFFSET,
  DEADLINE_PLAIN, createJourneyOneClock,
} from "../src/journey-one-clock.v5.js";
import {
  JOURNEY_ONE_CLOCK_APPEND_INVARIANT_IDS, JOURNEY_ONE_CLOCK_COMPLETION_SEAL_FIELDS,
  JOURNEY_ONE_CLOCK_IDENTITY_DOMAIN_TAG, JOURNEY_ONE_CLOCK_ORIGIN_FIELDS,
  JOURNEY_ONE_CLOCK_SCOPE_DOMAIN_TAG, JOURNEY_ONE_CLOCK_SCOPE_FIELDS,
  JOURNEY_ONE_CLOCK_SCOPE_IDENTITY_FIELDS,
  JOURNEY_ONE_CLOCK_STATE_FIELDS, JOURNEY_ONE_CLOCK_STORE_CANNOT_PROVE,
  createEphemeralJourneyOneClockJournal, createJourneyOneClockRecorder,
  createJourneyOneClockStore, createPostgresJourneyOneClockJournal,
  journeyOneClockHistoryDigest, journeyOneClockHistoryFromRows, journeyOneClockHistoryRows,
  journeyOneClockKey, journeyOneClockKeyForState, journeyOneClockScopeBinding,
  journeyOneClockScopeKey,
  journeyOneClockStoreIntegrationRequirements, journeyOneClockStoreTools,
} from "../src/journey-one-clock-store.v5.js";

// --- the kernel fixtures, exactly as journey-one-clock.v5.test.mjs builds them

const D = n => `sha256:${String(n).padStart(2, "0").repeat(32)}`;
const I = actor => ({ actor_id: actor, session_ref: `session:synthetic-${actor}`,
  authority_class: actor === "joe" || actor === "dell" ? "verified_partner" : "synthetic_oracle" });
const copy = x => JSON.parse(JSON.stringify(x));
const iso = ms => new Date(ms).toISOString();
const HOUR = 3600000;
const DAY = 24 * HOUR;
const ORIGIN = "2026-09-09T15:00:00.000Z";
const EXPECTATION = { artifact_digest: D(7), fixture_set_digest: D(5) };
const MINIMUM_TTL_POLICY = 48 * HOUR;
const COMPLETION_TTL_POLICY = 72 * HOUR;
const VERIFIER = "safe:verifier:synthetic-trusted-projection-verifier";

function minimum(origin = ORIGIN) {
  return {
    gate_id: "foundation-assurance-minimum-accepted",
    receipt_producer_step_ref: "step:foundation-assurance-minimum-receipt",
    subject_digest: D(1), candidate_digest: D(2), policy_digest: D(3), environment_manifest_digest: D(4),
    subject_environment: "candidate", evidence_scope: "candidate-and-test",
    subject_maker_identity: I("maker"), producer_identity: I("producer"), evaluator_identity: I("evaluator"),
    producer_role: "independent_foundation_assurance_minimum_oracle",
    independent_oracle_ref: "oracle:gate-producer:foundation-assurance-minimum", oracle_version: "1.0.0",
    evidence_ref: "safe:synthetic:min-evidence", fixture_set_digest: D(5), observed_at: origin,
    ttl_expires_at: iso(Date.parse(origin) + 24 * HOUR), status: "pass",
    comparator: "synthetic-exact-comparator",
    negative_admission_result: "all_required_denials_observed",
  };
}
function completed(at) {
  const m = minimum(at);
  delete m.gate_id; delete m.receipt_producer_step_ref; delete m.environment_manifest_digest;
  return { gate_id: "journey-one-kernel-production-accepted", combiner: "all_current_exact_distinct_pass",
    obligation_decision_ids: ["Q002.D1", "Q014.D1", "Q123.D1"], receipts: [{ ...m,
      receipt_ref: "safe:receipt:journey-one-kernel-production",
      producer_step_ref: "step:j1-kernel-production-outcome",
      rollout_environment_manifest_digest: D(6), artifact_digest: D(7),
      subject_environment: "production", evidence_scope: "production",
      producer_role: "independent_journey_one_kernel_outcome_oracle",
      independent_oracle_ref: "oracle:rollout-component:journey-one-kernel-production",
    }] };
}
function snapshot(as_of = ORIGIN, origin = ORIGIN) {
  return { schema_version: JOURNEY_ONE_CLOCK_PROJECTION, tenant: ORGANIZATION_TENANT_ID, as_of,
    binding: { subject_digest: D(1), candidate_digest: D(2), policy_digest: D(3),
      minimum_environment_manifest_digest: D(4), production_environment_manifest_digest: D(6),
      maximum_minimum_receipt_ttl_ms: MINIMUM_TTL_POLICY,
      maximum_completion_receipt_ttl_ms: COMPLETION_TTL_POLICY },
    benchmark: { manifest_digest: D(8), subject_digest: D(1), candidate_digest: D(2), policy_digest: D(3),
      deadline_contract: copy(JOURNEY_ONE_DEADLINE_CONTRACT),
      accepted_at: iso(Date.parse(origin) - HOUR), accepted_by_identity: I("joe") },
    minimum_history: [{ admitted_at: origin, receipt: minimum(origin) }],
    completion: null, completion_expectation: copy(EXPECTATION),
    pauses: [], amendments: [], history: null,
  };
}
function pause(p, start, end, id = "one", partner = "joe", approved = iso(Date.parse(start) - 1)) {
  const payload = { pause_id: `safe:synthetic:pause-${id}`,
    clock_origin_digest: digest(p.minimum_history[0].receipt),
    blocker_ref: "safe:external-blocker:synthetic-provider", starts_at: start,
    approved_at: approved, approved_by_identity: I(partner) };
  return { ...payload, ends_at: end, approval_digest: digest(["doctorcre:j1-clock-pause:v1", payload]) };
}

/**
 * A trusted, test-installed authenticating verifier. The WeakMap-shaped evidence
 * table is installed by TEST CODE, exactly as a real one would be installed by
 * server code: no JSON flag can cause an unknown envelope to acquire a
 * projection, and nothing in this file constructs a verifier from data.
 */
function harness() {
  const evidence = new Map(); let serial = 0;
  const clock = createJourneyOneClock({ verifySnapshot(envelope) {
    const found = evidence.get(digest(envelope));
    if (!found) throw new Error("synthetic-authentication-refused");
    return { envelope_digest: digest(envelope), snapshot: copy(found) };
  } });
  return {
    clock,
    envelopeFor(p) { const e = { synthetic_receipt_ref: `test-${++serial}` };
      evidence.set(digest(e), copy(p)); return e; },
    evaluate(p) { return clock.evaluate(this.envelopeFor(p)); },
  };
}
function run(p) { return harness().evaluate(p); }

// --- store fixtures ---------------------------------------------------------

const ACTOR = { slug: "claude", human: false, sponsoring_human_slug: "joe" };
let keySerial = 0;
const key = () => {
  const n = (++keySerial).toString(16).padStart(12, "0");
  return `00000000-0000-4000-8000-${n}`;
};
/**
 * THE AUTHORITATIVE SCOPE, as a trusted integration would compose one: the
 * accepted binding's three digests -- exactly the ones the kernel forces the
 * benchmark and every receipt to match -- and the two gate ids the deadline
 * contract names. Nothing in a request produces it; the store is constructed
 * with it.
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
/** A second accepted scope: a different program, and therefore a different clock. */
const OTHER_SCOPE = Object.freeze({ ...SCOPE,
  benchmark_subject_digest: D(11), scope_ref: "safe:clock-scope:synthetic-journey-one-other" });
/**
 * THE SAME ACCEPTED SCOPE UNDER ANOTHER NAME. Every identity field is SCOPE's;
 * only the human label differs. It must be the same scope, or "one scope, one
 * clock" is really "one label, one clock" and a rename walks around it.
 */
const RELABELLED_SCOPE = Object.freeze({ ...SCOPE,
  scope_ref: "safe:clock-scope:synthetic-journey-one-renamed" });

function newStore({ now = Date.now, actor = ACTOR, clock_scope = SCOPE } = {}) {
  const journal = createEphemeralJourneyOneClockJournal({ now });
  return { journal, store: createJourneyOneClockStore({ journal, actor, now, clock_scope }) };
}
/** Recompute the digest after a deliberate tamper, so the seal is self-consistent. */
function reseal(state) { state.history_digest = journeyOneClockHistoryDigest(state); return state; }
async function refuses(promise, code) {
  await assert.rejects(promise, error => {
    assert.equal(error.code, code, `expected ${code}, got ${error.code}: ${error.message}`);
    return true;
  });
}

// ---------------------------------------------------------------------------
// 1. Content: decomposition, reconstruction and the digest.
// ---------------------------------------------------------------------------

test("a kernel state decomposes into typed rows and rebuilds to the identical history", () => {
  const p = snapshot(iso(Date.parse(ORIGIN) + 3 * DAY));
  p.pauses = [pause(p, iso(Date.parse(ORIGIN) + DAY), iso(Date.parse(ORIGIN) + DAY + 6 * HOUR))];
  const result = run(p);
  const rows = journeyOneClockHistoryRows(copy(result.state));
  const rebuilt = journeyOneClockHistoryFromRows(rows);

  // WHOLE-CONTENT RECONSTRUCTION, not a blob comparison: the state is taken
  // apart into scalars, ordered pause intervals and an ordered event chain, and
  // put back. If that round trip lost a field, an order or a value, the record
  // layer could not reproduce the history a partner's ledger names.
  assert.deepEqual(rebuilt, copy(result.state));
  assert.equal(journeyOneClockHistoryDigest(rebuilt), result.state.history_digest);
  assert.equal(Object.keys(rebuilt).length, JOURNEY_ONE_CLOCK_STATE_FIELDS.length);
  assert.equal(rows.scalars.paused_ms, result.state.paused_ms);
  assert.equal(rows.events.length, result.state.events.length);
  rows.events.forEach((row, index) => assert.equal(row.ordinal, index));
});

test("a pause end is stored verbatim, because the kernel never normalizes it", () => {
  // The same instant in two spellings. The kernel copies pause_intervals[].ends_at
  // straight off the projection, so `+00:00` and `Z` are two different stored
  // values naming one instant — and only the exact one reproduces the digest.
  const start = iso(Date.parse(ORIGIN) + DAY);
  const zulu = snapshot(iso(Date.parse(ORIGIN) + 3 * DAY));
  zulu.pauses = [pause(zulu, start, "2026-09-11T21:00:00.000Z")];
  const offset = snapshot(iso(Date.parse(ORIGIN) + 3 * DAY));
  offset.pauses = [pause(offset, start, "2026-09-11T21:00:00.000+00:00")];

  const a = run(zulu).state, b = run(offset).state;
  assert.equal(a.paused_ms, b.paused_ms, "the same instant credits the same elapsed hours");
  assert.notEqual(a.history_digest, b.history_digest, "but they are different stored histories");
  assert.equal(journeyOneClockHistoryRows(copy(a)).pause_intervals[0].ends_at, "2026-09-11T21:00:00.000Z");
  assert.equal(journeyOneClockHistoryRows(copy(b)).pause_intervals[0].ends_at, "2026-09-11T21:00:00.000+00:00");
  for (const state of [a, b]) {
    assert.equal(journeyOneClockHistoryDigest(journeyOneClockHistoryFromRows(
      journeyOneClockHistoryRows(copy(state)))), state.history_digest);
  }
});

test("the clock identity is derived from the kernel origin, not from anything a caller names", () => {
  const state = run(snapshot()).state;
  const derived = journeyOneClockKeyForState(state);
  assert.equal(derived, journeyOneClockKey({
    tenant: ORGANIZATION_TENANT_ID,
    origin_receipt_digest: state.origin_receipt_digest,
    origin_at: state.origin_at,
    origin_benchmark_manifest_digest: state.origin_benchmark_manifest_digest,
  }));
  // Exactly the four facts, under the declared domain tag. Nothing a caller
  // supplies participates.
  assert.equal(derived, digest([JOURNEY_ONE_CLOCK_IDENTITY_DOMAIN_TAG, {
    origin_at: state.origin_at,
    origin_benchmark_manifest_digest: state.origin_benchmark_manifest_digest,
    origin_receipt_digest: state.origin_receipt_digest,
    tenant: ORGANIZATION_TENANT_ID,
  }]));
  // A different origin is a different clock, which is what makes an alias
  // useless: there is no name to reuse.
  const other = run(snapshot(iso(Date.parse(ORIGIN) + DAY), iso(Date.parse(ORIGIN) + DAY))).state;
  assert.notEqual(journeyOneClockKeyForState(other), derived);
});

test("a v1 history is refused by name and never silently re-derived", () => {
  const state = copy(run(snapshot()).state);
  state.schema_version = "doctorcre-v5-journey-one-clock.v1";
  reseal(state);
  assert.throws(() => journeyOneClockHistoryRows(state),
    e => e.code === "legacy_history_migration_required" &&
      e.detail.invariant === "j1_clock_state_schema_current");
  state.schema_version = "doctorcre-v5-journey-one-clock.v9";
  reseal(state);
  assert.throws(() => journeyOneClockHistoryRows(state), e => e.code === "unsupported_history_schema");
});

test("a tenant this rail does not hold is refused rather than stored", () => {
  const state = run(snapshot()).state;
  assert.throws(() => journeyOneClockHistoryRows(copy(state), "someone-elses-tenant"),
    e => e.code === "wrong_tenant");
  assert.throws(() => createJourneyOneClockStore({
    journal: createEphemeralJourneyOneClockJournal(), actor: ACTOR, tenant: "someone-elses-tenant" }),
    e => e.code === "wrong_tenant" && e.detail.invariant === "j1_clock_tenant_bound");
});

// ---------------------------------------------------------------------------
// 2. The positive path: persist, restart, read, evaluate again, append.
// ---------------------------------------------------------------------------

test("an initial persist stores the whole history and reads back exactly what the kernel produced", async () => {
  // A pinned server clock, so "server time is not the caller's as_of" is a
  // deterministic assertion rather than one that depends on when the suite runs.
  const SERVER_NOW = "2026-09-20T12:00:00.000Z";
  const { store } = newStore({ now: () => Date.parse(SERVER_NOW) });
  const result = run(snapshot());
  assert.equal(result.durable_history_write_required, true,
    "the kernel says the durable write is still owed; this store is what owes it");

  const written = await store.record({
    state: copy(result.state), expected_prior_history_digest: null,
    idempotency_key: key(), claimed_history_digest: result.state.history_digest,
    clock_ref: "J1-SYNTHETIC-CLOCK", verifier_ref: VERIFIER,
  });
  assert.equal(written.revision_ordinal, 0);
  assert.equal(written.history_digest, result.state.history_digest);
  assert.equal(written.replayed, false);

  const readback = await store.read(journeyOneClockKeyForState(result.state));
  assert.equal(readback.exists, true);
  assert.equal(readback.revision_count, 1);
  assert.equal(readback.head_revision_ordinal, 0);
  assert.deepEqual(readback.history, copy(result.state));
  assert.equal(readback.history_digest, result.state.history_digest);
  assert.equal(readback.state_schema_version, JOURNEY_ONE_CLOCK_SCHEMA);
  assert.equal(readback.clock_ref, "J1-SYNTHETIC-CLOCK");
  assert.equal(readback.revisions[0].expected_prior_history_digest, null,
    "creation is an explicit null prior, recorded as such");

  // PROVENANCE IS SCOPED, AND SAYS SO IN ITS OWN FIELDS.
  assert.equal(readback.provenance.computed_by, "mcp-server/src/journey-one-clock.v5.js");
  assert.equal(readback.provenance.projection_schema_version, JOURNEY_ONE_CLOCK_PROJECTION);
  assert.equal(readback.provenance.verifier_ref, VERIFIER);
  assert.equal(readback.provenance.input_authority,
    "trusted_projection_not_independently_verified_by_this_record_layer");
  assert.equal(readback.provenance.written_by_actor_id, "claude");
  assert.equal(readback.provenance.written_by_authority_class, "sponsored_agent");

  // STORING A STATUS IS NOT ACCEPTING A DEADLINE, and the readback says so.
  assert.equal(readback.deadline_accepted_by_record_layer, false);
  assert.deepEqual(readback.record_layer_cannot_prove, [...JOURNEY_ONE_CLOCK_STORE_CANNOT_PROVE]);
  assert.ok(readback.record_layer_cannot_prove.some(line => line.includes("deadline SUCCESS")));

  // SERVER TIME AND KERNEL TIME ARE TWO FIELDS. `evaluated_at` came from the
  // projection's as_of, which a caller influences; `recorded_at` is the server
  // clock. Neither ever stands in for the other.
  assert.equal(readback.evaluated_at, result.state.evaluated_at);
  assert.equal(readback.evaluated_at, ORIGIN);
  assert.equal(readback.recorded_at, SERVER_NOW);
  assert.notEqual(readback.recorded_at, readback.evaluated_at);
});

test("a restart reads the stored history back and the kernel accepts it as its own history", async () => {
  // THE POINT OF THIS TEST, and it still proves more than the exported
  // validator does. readJourneyOneClockHistory asks whether a history is
  // READABLE; this asks whether the stored one is still THIS CLOCK'S history,
  // by handing it back to evaluate() against a live projection, where the
  // origin selection, the recorded-approval completeness check and the sealed
  // policy comparisons all run over it. A store whose round trip lost a field,
  // reordered an event or normalized an instant refuses here.
  const { journal } = newStore();
  const first = run(snapshot());
  const clockKey = journeyOneClockKeyForState(first.state);
  const writer = createJourneyOneClockStore({ journal, actor: ACTOR, clock_scope: SCOPE });
  await writer.record({ state: copy(first.state), expected_prior_history_digest: null,
    idempotency_key: key(), verifier_ref: VERIFIER });

  // A COLD PROCESS: a new store object, a new kernel, a new verifier. Only the
  // journal survives, exactly as only the database would.
  const restarted = createJourneyOneClockStore({ journal, actor: ACTOR, clock_scope: SCOPE });
  const stored = await restarted.read(clockKey);

  const later = snapshot(iso(Date.parse(ORIGIN) + 5 * DAY));
  later.history = copy(stored.history);
  const second = run(later);
  assert.equal(second.state.status, "running");
  assert.equal(second.state.origin_receipt_digest, first.state.origin_receipt_digest);
  assert.equal(second.state.origin_at, first.state.origin_at);
  assert.equal(second.state.base_deadline_at, first.state.base_deadline_at);
  assert.equal(second.state.events.length, first.state.events.length, "no new fact, no new event");

  const appended = await restarted.record({
    state: copy(second.state), expected_prior_history_digest: stored.history_digest,
    idempotency_key: key(), verifier_ref: VERIFIER });
  assert.equal(appended.revision_ordinal, 1);

  const after = await restarted.read(clockKey);
  assert.equal(after.revision_count, 2);
  assert.deepEqual(after.history, copy(second.state));
  assert.equal(after.revisions[1].expected_prior_history_digest, stored.history_digest);
  assert.deepEqual(after.revisions.map(r => r.revision_ordinal), [0, 1]);
});

test("a valid append records a new fact and never drops the ones already recorded", async () => {
  const { store } = newStore();
  const first = run(snapshot());
  const clockKey = journeyOneClockKeyForState(first.state);
  await store.record({ state: copy(first.state), expected_prior_history_digest: null,
    idempotency_key: key(), verifier_ref: VERIFIER });

  const withPause = snapshot(iso(Date.parse(ORIGIN) + 5 * DAY));
  withPause.history = copy(first.state);
  withPause.pauses = [pause(withPause, iso(Date.parse(ORIGIN) + DAY),
    iso(Date.parse(ORIGIN) + DAY + 12 * HOUR))];
  const second = run(withPause);
  assert.equal(second.state.events.length, first.state.events.length + 1);
  assert.equal(second.state.paused_ms, 12 * HOUR);

  await store.record({ state: copy(second.state),
    expected_prior_history_digest: first.state.history_digest,
    idempotency_key: key(), verifier_ref: VERIFIER });
  const readback = await store.read(clockKey);
  assert.equal(readback.revision_count, 2);
  assert.equal(readback.history.paused_ms, 12 * HOUR);
  // THE PRIOR CHAIN IS A PREFIX, checked field by field rather than by length.
  first.state.events.forEach((event, index) =>
    assert.equal(readback.history.events[index].event_digest, event.event_digest));
  assert.equal(readback.history.events.at(-1).type, "pause_approved");
  // The origin never moved.
  for (const field of JOURNEY_ONE_CLOCK_ORIGIN_FIELDS) {
    assert.equal(readback.history[field], first.state[field], field);
  }
});

test("the Chicago and DST resolutions survive storage exactly, and are never recomputed here", async () => {
  const cases = [
    ["2026-02-06T08:30:00.000Z", "2026-03-08T08:30:00.000Z", DEADLINE_GAP_SHIFTED],
    ["2026-10-02T06:30:00.000Z", "2026-11-01T06:30:00.000Z", DEADLINE_OVERLAP_ORIGIN_OFFSET],
    [ORIGIN, "2026-10-09T15:00:00.000Z", DEADLINE_PLAIN],
  ];
  for (const [origin, due, resolution] of cases) {
    const { store } = newStore();
    const result = run(snapshot(iso(Date.parse(origin) + DAY), origin));
    assert.equal(result.state.base_deadline_resolution, resolution);
    await store.record({ state: copy(result.state), expected_prior_history_digest: null,
      idempotency_key: key(), verifier_ref: VERIFIER });
    const readback = await store.read(journeyOneClockKeyForState(result.state));
    assert.equal(readback.history.base_deadline_at, due);
    assert.equal(readback.history.due_at, due);
    // The reason code is stored, so a later reader can tell WHICH rule produced
    // this deadline rather than having to guess or re-derive it.
    assert.equal(readback.history.base_deadline_resolution, resolution);
  }
});

test("the preapproved 120-hour pause union survives storage as the kernel computed it", async () => {
  const { store } = newStore();
  const p = snapshot(iso(Date.parse(ORIGIN) + 20 * DAY));
  // Six overlapping seven-day pauses. The kernel's union caps the credit at the
  // 120-hour budget; this rail stores that number and never recounts an hour.
  p.pauses = [0, 1, 2, 3, 4, 5].map(n => pause(p,
    iso(Date.parse(ORIGIN) + (n + 1) * DAY),
    iso(Date.parse(ORIGIN) + (n + 8) * DAY), `p${n}`));
  const result = run(p);
  assert.equal(result.state.paused_ms,
    JOURNEY_ONE_DEADLINE_CONTRACT.maximum_external_blocker_pause_hours * HOUR);
  await store.record({ state: copy(result.state), expected_prior_history_digest: null,
    idempotency_key: key(), verifier_ref: VERIFIER });
  const readback = await store.read(journeyOneClockKeyForState(result.state));
  assert.equal(readback.history.paused_ms, 120 * HOUR);
  assert.equal(readback.history.pause_intervals.length, 6);
  assert.deepEqual(readback.history.pause_intervals.map(i => i.pause_id),
    result.state.pause_intervals.map(i => i.pause_id), "order is part of the hash");
});

// ---------------------------------------------------------------------------
// 3. The compare-and-swap.
// ---------------------------------------------------------------------------

test("two writers computed against the same head collide, and exactly one append lands", async () => {
  const { store } = newStore();
  const first = run(snapshot());
  const clockKey = journeyOneClockKeyForState(first.state);
  await store.record({ state: copy(first.state), expected_prior_history_digest: null,
    idempotency_key: key(), verifier_ref: VERIFIER });

  const branch = (as_of) => {
    const p = snapshot(as_of);
    p.history = copy(first.state);
    return run(p).state;
  };
  const a = branch(iso(Date.parse(ORIGIN) + 2 * DAY));
  const b = branch(iso(Date.parse(ORIGIN) + 3 * DAY));
  assert.notEqual(a.history_digest, b.history_digest);

  // Fired without awaiting between them, so both enter the serialized section
  // with the same idea of the head. The store serializes them and the loser is
  // told exactly why.
  const outcomes = await Promise.allSettled([
    store.record({ state: copy(a), expected_prior_history_digest: first.state.history_digest,
      idempotency_key: key(), verifier_ref: VERIFIER }),
    store.record({ state: copy(b), expected_prior_history_digest: first.state.history_digest,
      idempotency_key: key(), verifier_ref: VERIFIER }),
  ]);
  assert.equal(outcomes.filter(o => o.status === "fulfilled").length, 1);
  const rejected = outcomes.find(o => o.status === "rejected").reason;
  assert.equal(rejected.code, "clock_stale_prior_history_digest");
  assert.equal(rejected.detail.invariant, "j1_clock_exact_prior_history_digest");
  assert.equal(rejected.detail.supplied, first.state.history_digest);

  const readback = await store.read(clockKey);
  assert.equal(readback.revision_count, 2, "the loser wrote nothing at all");
});

test("an older genuine history cannot replay over a newer one", async () => {
  // This is the exact attack the kernel's own header says only the durable store
  // can stop: readHistory proves a history is internally consistent and
  // self-bound, and an OLDER GENUINE history passes that test perfectly.
  const { store } = newStore();
  const first = run(snapshot());
  await store.record({ state: copy(first.state), expected_prior_history_digest: null,
    idempotency_key: key(), verifier_ref: VERIFIER });
  const second = (() => {
    const p = snapshot(iso(Date.parse(ORIGIN) + 5 * DAY));
    p.history = copy(first.state);
    p.pauses = [pause(p, iso(Date.parse(ORIGIN) + DAY), iso(Date.parse(ORIGIN) + DAY + 8 * HOUR))];
    return run(p).state;
  })();
  await store.record({ state: copy(second), expected_prior_history_digest: first.state.history_digest,
    idempotency_key: key(), verifier_ref: VERIFIER });

  // A perfectly genuine earlier history, re-presented with the prior it was
  // computed against. The store refuses because that prior is no longer the head.
  await refuses(store.record({ state: copy(first.state), expected_prior_history_digest: null,
    idempotency_key: key(), verifier_ref: VERIFIER }), "clock_already_exists");
  const replay = (() => {
    const p = snapshot(iso(Date.parse(ORIGIN) + 6 * DAY));
    p.history = copy(first.state);
    return run(p).state;
  })();
  await refuses(store.record({ state: copy(replay),
    expected_prior_history_digest: first.state.history_digest,
    idempotency_key: key(), verifier_ref: VERIFIER }), "clock_stale_prior_history_digest");
});

test("a caller-chosen alias cannot restart a clock that already has history", async () => {
  const { store } = newStore();
  const result = run(snapshot());
  await store.record({ state: copy(result.state), expected_prior_history_digest: null,
    idempotency_key: key(), clock_ref: "J1-FIRST-LABEL", verifier_ref: VERIFIER });

  // A second creation under a brand new label. The label is not the address;
  // the origin is, so this addresses the SAME clock and meets the creation CAS.
  await refuses(store.record({ state: copy(result.state), expected_prior_history_digest: null,
    idempotency_key: key(), clock_ref: "J1-A-COMPLETELY-DIFFERENT-LABEL", verifier_ref: VERIFIER }),
    "clock_already_exists");
  const readback = await store.read(journeyOneClockKeyForState(result.state));
  assert.equal(readback.revision_count, 1);
  assert.equal(readback.clock_ref, "J1-FIRST-LABEL", "the label is set once and is never authority");
});

test("an append to a clock that does not exist is not quietly turned into a creation", async () => {
  const { store } = newStore();
  const result = run(snapshot());
  await refuses(store.record({ state: copy(result.state),
    expected_prior_history_digest: D(99), idempotency_key: key(), verifier_ref: VERIFIER }),
    "clock_prior_history_unknown");
  // And an omitted CAS token is a refusal, never a defaulted creation.
  await refuses(store.record({ state: copy(result.state), idempotency_key: key(),
    verifier_ref: VERIFIER }), "missing_prior_history_digest");
});

// ---------------------------------------------------------------------------
// 4. Idempotency, and the caller's claimed hash.
// ---------------------------------------------------------------------------

test("an exact idempotent replay returns the stored revision and writes nothing", async () => {
  const { store } = newStore();
  const result = run(snapshot());
  const k = key();
  const args = { state: copy(result.state), expected_prior_history_digest: null,
    idempotency_key: k, verifier_ref: VERIFIER };
  const first = await store.record(copy2(args));
  const second = await store.record(copy2(args));
  assert.equal(first.replayed, false);
  assert.equal(second.replayed, true);
  assert.equal(second.revision_ordinal, first.revision_ordinal);
  assert.equal(second.history_digest, first.history_digest);
  const readback = await store.read(journeyOneClockKeyForState(result.state));
  assert.equal(readback.revision_count, 1, "a replay is not a second write");

  // The same key carrying different content is a different request wearing that
  // key, and is refused rather than returning the first row.
  const other = (() => {
    const p = snapshot(iso(Date.parse(ORIGIN) + 2 * DAY));
    p.history = copy(result.state);
    return run(p).state;
  })();
  await refuses(store.record({ state: copy(other),
    expected_prior_history_digest: result.state.history_digest,
    idempotency_key: k, verifier_ref: VERIFIER }), "clock_idempotency_key_reused");
  assert.equal((await store.read(journeyOneClockKeyForState(result.state))).revision_count, 1);
});
function copy2(args) { return { ...args, state: copy(args.state) }; }

test("a caller's claimed history digest is only ever the loser of a comparison", async () => {
  const { store } = newStore();
  const result = run(snapshot());
  await refuses(store.record({ state: copy(result.state), expected_prior_history_digest: null,
    idempotency_key: key(), claimed_history_digest: D(77), verifier_ref: VERIFIER }),
    "claimed_history_digest_mismatch");
  assert.equal((await store.read(journeyOneClockKeyForState(result.state))).exists, false);
});

test("content changed underneath a carried digest is refused before anything is stored", async () => {
  const { store } = newStore();
  const state = copy(run(snapshot()).state);
  // The seal is kept and the content is changed: the exact shape of a record
  // that was edited after it was computed.
  state.status = "completed_on_time";
  await refuses(store.record({ state, expected_prior_history_digest: null,
    idempotency_key: key(), verifier_ref: VERIFIER }), "clock_history_digest_mismatch");
});

// ---------------------------------------------------------------------------
// 5. The append-only diff: origin, seals, miss, events.
// ---------------------------------------------------------------------------

/** Store one clock and return { store, clockKey, head } ready for an append. */
async function seeded(build = p => p) {
  const { store, journal } = newStore();
  const result = run(build(snapshot()));
  await store.record({ state: copy(result.state), expected_prior_history_digest: null,
    idempotency_key: key(), verifier_ref: VERIFIER });
  return { store, journal, head: copy(result.state),
    clockKey: journeyOneClockKeyForState(result.state) };
}

test("a rebased origin is refused even when the record is internally perfect", async () => {
  const { store, head } = await seeded();
  const rebased = copy(head);
  // The origin digest and instant are untouched, so this still ADDRESSES the
  // same clock — and the base deadline it claims has moved a day. That is a
  // rebase, and the origin fields alone would not have caught it.
  rebased.base_deadline_at = iso(Date.parse(head.base_deadline_at) + DAY);
  rebased.due_at = rebased.base_deadline_at;
  reseal(rebased);
  await refuses(store.record({ state: rebased,
    expected_prior_history_digest: head.history_digest,
    idempotency_key: key(), verifier_ref: VERIFIER }), "clock_origin_reset_or_rebase");

  const ttl = copy(head);
  ttl.origin_receipt_ttl_policy_ms = MINIMUM_TTL_POLICY + 1;
  reseal(ttl);
  await refuses(store.record({ state: ttl, expected_prior_history_digest: head.history_digest,
    idempotency_key: key(), verifier_ref: VERIFIER }), "clock_origin_reset_or_rebase");
});

test("a lost event is refused, however plausible the rest of the record is", async () => {
  const { store, head } = await seeded(p => {
    p.as_of = iso(Date.parse(ORIGIN) + DAY);
    p.pauses = [pause(p, iso(Date.parse(ORIGIN) + HOUR), iso(Date.parse(ORIGIN) + 2 * HOUR))];
    return p;
  });
  assert.ok(head.events.length >= 2);
  const truncated = copy(head);
  truncated.events.pop();
  truncated.pause_intervals = [];
  reseal(truncated);
  // The chain still links and the digest still recomputes: this record is
  // internally flawless and is exactly what an erased approval looks like.
  await refuses(store.record({ state: truncated,
    expected_prior_history_digest: head.history_digest,
    idempotency_key: key(), verifier_ref: VERIFIER }), "clock_event_history_truncated");

  const rewritten = copy(head);
  rewritten.events[1] = { ...rewritten.events[1], evidence_digest: D(55) };
  const { event_digest, ...body } = rewritten.events[1];
  rewritten.events[1].event_digest = digest(body);
  reseal(rewritten);
  await refuses(store.record({ state: rewritten,
    expected_prior_history_digest: head.history_digest,
    idempotency_key: key(), verifier_ref: VERIFIER }), "clock_event_history_rewritten");
});

test("a recorded miss is never removed, and never becomes deadline success", async () => {
  // A clock evaluated past its deadline with no completion in hand: the kernel
  // records the miss and it sticks.
  const missed = iso(Date.parse("2026-10-09T15:00:00.000Z") + HOUR);
  const { store, head, clockKey } = await seeded(() => snapshot(missed));
  assert.equal(head.status, "missed");
  assert.notEqual(head.miss_at, null);

  // TWO DIFFERENT LIES, TWO DIFFERENT OWNERS.
  // The first is a lie a SINGLE history tells about itself: miss_at cleared
  // while the recorded miss event is still in its own chain. That is malformed
  // on its own terms, so the KERNEL's read refuses it under its own name -- and
  // it is refused at creation too, where there is no head to diff against and
  // the store's pairwise invariant could never have seen it.
  const cleared = copy(head);
  cleared.miss_at = null;
  cleared.status = "running";
  reseal(cleared);
  await refuses(store.record({ state: copy(cleared), expected_prior_history_digest: head.history_digest,
    idempotency_key: key(), verifier_ref: VERIFIER }), "erased_miss_history");
  const fresh = newStore().store;
  await refuses(fresh.record({ state: copy(cleared), expected_prior_history_digest: null,
    idempotency_key: key(), verifier_ref: VERIFIER }), "erased_miss_history");

  // The second is a lie a history tells about ANOTHER history: the miss and its
  // event are removed together, so the record is internally flawless and the
  // kernel reads it happily. Only the head on disk says otherwise, and that is
  // the store's own invariant, which no single-history validator could own.
  const rolledBack = copy(head);
  rolledBack.miss_at = null;
  rolledBack.status = "running";
  rolledBack.events = rolledBack.events.filter(e => e.type !== "deadline_missed");
  reseal(rolledBack);
  assert.equal(rolledBack.events.length, head.events.length - 1);
  await refuses(store.record({ state: rolledBack, expected_prior_history_digest: head.history_digest,
    idempotency_key: key(), verifier_ref: VERIFIER }), "clock_recorded_miss_removed");

  // And the forbidden claim itself is unstorable, at creation or on append.
  const claimed = copy(head);
  claimed.status = "completed_on_time";
  reseal(claimed);
  await refuses(store.record({ state: claimed, expected_prior_history_digest: head.history_digest,
    idempotency_key: key(), verifier_ref: VERIFIER }), "deadline_success_claimed_after_recorded_miss");
  assert.equal((await store.read(clockKey)).history.status, "missed");
});

test("a completion seal cannot be changed once the completion is recorded", async () => {
  const { store, head } = await seeded(() => {
    // The terminus receipt's own 24-hour window has to still be current at the
    // evaluation instant, or the kernel refuses it outright as a receipt it
    // never recorded — which would be a different test from this one.
    const p = snapshot(iso(Date.parse(ORIGIN) + 9 * DAY + HOUR));
    p.completion = completed(iso(Date.parse(ORIGIN) + 9 * DAY));
    return p;
  });
  assert.equal(head.status, "completed_on_time");
  for (const field of JOURNEY_ONE_CLOCK_COMPLETION_SEAL_FIELDS) {
    assert.notEqual(head[field], null, `${field} is sealed by the first completion`);
  }
  const tampered = copy(head);
  tampered.completion_artifact_digest = D(66);
  reseal(tampered);
  await refuses(store.record({ state: tampered, expected_prior_history_digest: head.history_digest,
    idempotency_key: key(), verifier_ref: VERIFIER }), "clock_completion_seal_changed");

  const policy = copy(head);
  policy.completion_receipt_ttl_policy_ms = COMPLETION_TTL_POLICY + 1;
  reseal(policy);
  await refuses(store.record({ state: policy, expected_prior_history_digest: head.history_digest,
    idempotency_key: key(), verifier_ref: VERIFIER }), "clock_completion_seal_changed");
});

test("a late completion after a recorded miss stores the truth on both sides of it", async () => {
  // Q008.D1, exactly as accepted: a late passing kernel receipt stays usable,
  // the miss and the replan obligation stand, and deadline success is never
  // claimed. All three have to survive the round trip or the record is a lie.
  const due = "2026-10-09T15:00:00.000Z";
  const { store, head, clockKey } = await seeded(() => snapshot(iso(Date.parse(due) + HOUR)));
  assert.equal(head.status, "missed");

  const late = snapshot(iso(Date.parse(due) + 2 * DAY + HOUR));
  late.history = copy(head);
  late.completion = completed(iso(Date.parse(due) + 2 * DAY));
  const result = run(late);
  assert.equal(result.state.status, "completed_late");
  assert.equal(result.deadline_success, false);
  assert.equal(result.replan_required, true);
  assert.equal(result.completion_observed_within_deadline, false);
  assert.equal(result.completion_currently_usable, true);

  await store.record({ state: copy(result.state), expected_prior_history_digest: head.history_digest,
    idempotency_key: key(), verifier_ref: VERIFIER });
  const readback = await store.read(clockKey);
  assert.equal(readback.history.status, "completed_late");
  assert.equal(readback.history.miss_at, head.miss_at, "the miss instant is preserved exactly");
  assert.equal(readback.history.completion_receipt_digest, result.state.completion_receipt_digest);
  assert.equal(readback.history.completion_observed_at, result.state.completion_observed_at);
  assert.equal(readback.revision_count, 2);
  // The miss event is still in the chain at the position it was written.
  const missEvents = readback.history.events.filter(e => e.type === "deadline_missed");
  assert.equal(missEvents.length, 1);
  assert.equal(missEvents[0].at, head.miss_at);
  // And the record layer has still accepted nothing.
  assert.equal(readback.deadline_accepted_by_record_layer, false);
});

test("a completion observed inside the deadline beside a recorded miss stores as neither on-time nor late", async () => {
  // The kernel's third completed case. The store has to carry it verbatim: a
  // record layer that collapsed it into on-time would make the forbidden claim,
  // and one that collapsed it into late would misreport the observation.
  const due = "2026-10-09T15:00:00.000Z";
  const { store, head, clockKey } = await seeded(() => snapshot(iso(Date.parse(due) + HOUR)));
  const revived = snapshot(iso(Date.parse(due) + DAY + HOUR));
  revived.history = copy(head);
  // A pause approved before the deadline, honestly reported after the miss. It
  // moves due_at forward by 48 hours; it never clears the miss.
  revived.pauses = [pause(revived, iso(Date.parse(ORIGIN) + DAY),
    iso(Date.parse(ORIGIN) + DAY + 48 * HOUR))];
  revived.completion = completed(iso(Date.parse(due) + DAY));
  const result = run(revived);
  assert.equal(result.state.status, "completed_after_recorded_miss");
  assert.equal(result.completion_observed_within_deadline, true);
  assert.equal(result.deadline_success, false);
  assert.equal(result.replan_required, true);

  await store.record({ state: copy(result.state), expected_prior_history_digest: head.history_digest,
    idempotency_key: key(), verifier_ref: VERIFIER });
  const readback = await store.read(clockKey);
  assert.equal(readback.history.status, "completed_after_recorded_miss");
  assert.equal(readback.history.miss_at, head.miss_at);
  assert.ok(Date.parse(readback.history.due_at) > Date.parse(head.due_at),
    "due_at moved forward past the miss, exactly as the kernel allows");
  assert.equal(readback.deadline_accepted_by_record_layer, false);
});

test("a late-reported pause end that lowers the credited hours is storable", async () => {
  // paused_ms is NOT monotone and the store must not pretend it is. A pause
  // whose end was unknown was counted to the evaluation instant; the honest
  // later report credits fewer hours, and refusing it would leave the false
  // number as the only writable answer.
  const { store, head, clockKey } = await seeded(p => {
    p.as_of = iso(Date.parse(ORIGIN) + 5 * DAY);
    p.pauses = [pause(p, iso(Date.parse(ORIGIN) + DAY), null)];
    return p;
  });
  assert.ok(head.paused_ms > 24 * HOUR);

  const settled = snapshot(iso(Date.parse(ORIGIN) + 6 * DAY));
  settled.history = copy(head);
  settled.pauses = [pause(settled, iso(Date.parse(ORIGIN) + DAY),
    iso(Date.parse(ORIGIN) + DAY + 12 * HOUR))];
  const result = run(settled);
  assert.equal(result.state.paused_ms, 12 * HOUR);
  assert.ok(result.state.paused_ms < head.paused_ms);

  await store.record({ state: copy(result.state), expected_prior_history_digest: head.history_digest,
    idempotency_key: key(), verifier_ref: VERIFIER });
  assert.equal((await store.read(clockKey)).history.paused_ms, 12 * HOUR);
});

// ---------------------------------------------------------------------------
// 5b. THE KERNEL'S OWN READ AT THE STORAGE BOUNDARY.
//
// Every history below is CLOSED-SHAPED and RESEALED, so it passes the two things
// this rail used to check for itself and would have been stored. Each is a
// history the kernel will not read, and each now refuses under the kernel's own
// name, because journey-one-clock.v5.js exports readJourneyOneClockHistory and
// this store calls it instead of owning a second, weaker validator.
// ---------------------------------------------------------------------------

test("a malformed history that survives closed shape and a recomputed digest is refused by the kernel's own read", async () => {
  const running = copy(run(snapshot()).state);
  const done = copy(run((() => {
    const p = snapshot(iso(Date.parse(ORIGIN) + 9 * DAY + HOUR));
    p.completion = completed(iso(Date.parse(ORIGIN) + 9 * DAY));
    return p;
  })()).state);
  assert.equal(done.status, "completed_on_time");

  const forgedType = copy(running);
  forgedType.events[0] = { ...forgedType.events[0], type: "clock_reset" };
  const { event_digest, ...typeBody } = forgedType.events[0];
  forgedType.events[0].event_digest = digest(typeBody);

  for (const [name, state, code] of [
    ["a completion seal dropped while the completion stands",
      reseal({ ...copy(done), completion_receipt_ttl_policy_ms: null }), "corrupt_history"],
    ["a completion whose own event was removed from the chain",
      reseal({ ...copy(done), events: done.events.filter(e => e.type !== "completion_observed") }),
      "erased_completion_history"],
    ["a recorded miss with no event to attest it",
      reseal({ ...copy(running), miss_at: running.due_at }), "erased_miss_history"],
    ["a status the kernel never produces",
      reseal({ ...copy(running), status: "on_time" }), "corrupt_history"],
    ["credited hours beyond the 120-hour budget",
      reseal({ ...copy(running), paused_ms: 121 * HOUR }), "corrupt_history"],
    ["an origin that postdates its own evaluation",
      reseal({ ...copy(running), origin_at: iso(Date.parse(running.evaluated_at) + DAY) }),
      "history_time_reversed"],
    ["a pause id that is not a safe reference",
      reseal({ ...copy(running), pause_intervals: [{ pause_id: "pause-one", ends_at: null }] }),
      "invalid_reference"],
    ["an event type the kernel never writes", reseal(forgedType), "corrupt_history"],
  ]) {
    const { store } = newStore();
    // It would have passed the two checks this rail owns on its own: the shape
    // is closed and the digest recomputes from the rows it would be stored as.
    assert.equal(journeyOneClockHistoryDigest(state), state.history_digest, name);
    await refuses(store.record({ state: copy(state), expected_prior_history_digest: null,
      idempotency_key: key(), verifier_ref: VERIFIER }), code);
    assert.equal((await store.read(journeyOneClockKeyForState(state))).exists, false,
      `nothing was stored for: ${name}`);
  }
});

test("a stored history the kernel will not read refuses on readback rather than being served", async () => {
  const { journal, store } = newStore();
  const result = run(snapshot());
  const clockKey = journeyOneClockKeyForState(result.state);
  await store.record({ state: copy(result.state), expected_prior_history_digest: null,
    idempotency_key: key(), verifier_ref: VERIFIER });

  // THE SHAPE A DIRECT SQL WRITER LEAVES BEHIND: rows that hash to their own
  // digest, so recomputation says nothing at all, carrying a status the kernel
  // has no reading for. Serving it as ordinary source is how a malformed record
  // becomes an input to something else.
  const forged = createJourneyOneClockStore({
    journal: tampering(journal, rows => {
      const row = rows[rows.length - 1];
      row.scalars.status = "on_time";
      row.history_digest = journeyOneClockHistoryDigest(journeyOneClockHistoryFromRows({
        schema_version: "doctorcre-v5-journey-one-clock-history-rows.v1",
        tenant: row.tenant, clock_key: row.clock_key,
        state_schema_version: row.state_schema_version, history_digest: row.history_digest,
        scalars: row.scalars, pause_intervals: row.pause_intervals, events: row.events }));
      return rows;
    }), actor: ACTOR });
  await refuses(forged.read(clockKey), "corrupt_history");
  // The untampered store still reads it, so the refusal is about the tamper.
  assert.equal((await store.read(clockKey)).history.status, "running");
});

// ---------------------------------------------------------------------------
// 6. Readback tamper, wrong principal, wrong time.
// ---------------------------------------------------------------------------

/** A journal whose reads are mutated on the way out: a tampered store. */
function tampering(inner, mutate) {
  return {
    durable: false, kind: "tampering-journal",
    runAppend: (...a) => inner.runAppend(...a),
    readClock: (...a) => inner.readClock(...a),
    readScopeBindings: (...a) => inner.readScopeBindings(...a),
    bindScope: (...a) => inner.bindScope(...a),
    async readRevisions(clockKey) { return mutate(copy(await inner.readRevisions(clockKey))); },
  };
}

test("a readback whose stored rows were edited refuses instead of serving them", async () => {
  const { journal, store } = newStore();
  const result = run(snapshot());
  const clockKey = journeyOneClockKeyForState(result.state);
  await store.record({ state: copy(result.state), expected_prior_history_digest: null,
    idempotency_key: key(), verifier_ref: VERIFIER });

  const edited = createJourneyOneClockStore({
    journal: tampering(journal, rows => {
      rows[rows.length - 1].scalars.status = "completed_on_time";
      return rows;
    }), actor: ACTOR });
  await refuses(edited.read(clockKey), "clock_readback_tampered");

  // An edited EVENT is caught the same way: the chain is inside the digest.
  const eventEdited = createJourneyOneClockStore({
    journal: tampering(journal, rows => {
      rows[rows.length - 1].events[0].evidence_digest = D(44);
      return rows;
    }), actor: ACTOR });
  await refuses(eventEdited.read(clockKey), "clock_readback_tampered");

  // And a revision that was filed under a clock its own origin does not derive.
  const misfiled = createJourneyOneClockStore({
    journal: tampering(journal, rows => {
      const row = rows[rows.length - 1];
      row.scalars.origin_benchmark_manifest_digest = D(33);
      row.history_digest = journeyOneClockHistoryDigest(journeyOneClockHistoryFromRows({
        schema_version: "doctorcre-v5-journey-one-clock-history-rows.v1",
        tenant: row.tenant, clock_key: row.clock_key,
        state_schema_version: row.state_schema_version, history_digest: row.history_digest,
        scalars: row.scalars, pause_intervals: row.pause_intervals, events: row.events }));
      return rows;
    }), actor: ACTOR });
  await refuses(misfiled.read(clockKey), "clock_identity_mismatch");
});

test("a revision missing from the middle of the chain refuses rather than reading past it", async () => {
  const { journal, store } = newStore();
  const first = run(snapshot());
  const clockKey = journeyOneClockKeyForState(first.state);
  await store.record({ state: copy(first.state), expected_prior_history_digest: null,
    idempotency_key: key(), verifier_ref: VERIFIER });
  const p = snapshot(iso(Date.parse(ORIGIN) + 2 * DAY));
  p.history = copy(first.state);
  const second = run(p).state;
  await store.record({ state: copy(second), expected_prior_history_digest: first.state.history_digest,
    idempotency_key: key(), verifier_ref: VERIFIER });

  const holed = createJourneyOneClockStore({
    journal: tampering(journal, rows => rows.filter(r => r.revision_ordinal !== 0)), actor: ACTOR });
  await refuses(holed.read(clockKey), "clock_revision_ordinal_gap");
});

test("an unauthenticated writer cannot build a store at all", () => {
  const journal = createEphemeralJourneyOneClockJournal();
  for (const actor of [undefined, null, {}, { slug: "not-a-registered-actor" }, { slug: 42 }]) {
    assert.throws(() => createJourneyOneClockStore({ journal, actor }),
      e => e.code === "clock_writer_identity_unavailable");
  }
  // A sponsored runtime agent IS a legitimate writer: recording a computation is
  // ordinary trusted-writer work. It is not a partner act, no humanOnly gate is
  // introduced, and the derived class rides on the revision so attribution is
  // exact rather than assumed.
  const sponsored = createJourneyOneClockStore({ journal, actor: ACTOR });
  assert.equal(sponsored.writer.authority_class, "sponsored_agent");
  assert.equal(sponsored.writer.grants_authority, false);
  // Holding a partner login does not make a caller a human here, and does not
  // change what a revision means: both classes write the same kind of row.
  const partner = createJourneyOneClockStore({ journal, actor: { slug: "joe", human: true } });
  assert.equal(partner.writer.authority_class, "verified_partner");
  assert.equal(partner.writer.grants_authority, false);
});

test("a revision evaluated before the head it follows is refused", async () => {
  // TWO GENUINE REVISIONS OF ONE CLOCK, neither tampered with. The second is
  // the same history re-evaluated later with no new fact, so the two carry an
  // identical event chain and differ only in evaluated_at -- which isolates the
  // backdating invariant from every other clause in the diff.
  const { store } = newStore();
  const first = run(snapshot(iso(Date.parse(ORIGIN) + DAY))).state;
  await store.record({ state: copy(first), expected_prior_history_digest: null,
    idempotency_key: key(), verifier_ref: VERIFIER });
  const laterProjection = snapshot(iso(Date.parse(ORIGIN) + 5 * DAY));
  laterProjection.history = copy(first);
  const later = run(laterProjection).state;
  assert.deepEqual(later.events, first.events, "no new fact, no new event");
  assert.notEqual(later.history_digest, first.history_digest);
  await store.record({ state: copy(later), expected_prior_history_digest: first.history_digest,
    idempotency_key: key(), verifier_ref: VERIFIER });

  // The kernel itself refuses to read a history evaluated after `as_of`, which
  // is the first line of defence.
  const earlier = snapshot(iso(Date.parse(ORIGIN) + DAY));
  earlier.history = copy(later);
  assert.throws(() => run(earlier), e => e.code === "history_time_reversed");
  // The store's own diff is the second, and it is what a DIRECT SQL WRITER
  // meets: replaying the perfectly genuine earlier revision over the head.
  await refuses(store.record({ state: copy(first),
    expected_prior_history_digest: later.history_digest,
    idempotency_key: key(), verifier_ref: VERIFIER }), "clock_backdated_evaluation");
});

test("a server clock that moved backwards between revisions is refused", async () => {
  let serverMs = Date.parse("2026-09-20T00:00:00.000Z");
  const { store } = newStore({ now: () => serverMs });
  const first = run(snapshot());
  await store.record({ state: copy(first.state), expected_prior_history_digest: null,
    idempotency_key: key(), verifier_ref: VERIFIER });
  const p = snapshot(iso(Date.parse(ORIGIN) + 2 * DAY));
  p.history = copy(first.state);
  const second = run(p).state;
  serverMs -= HOUR;
  await refuses(store.record({ state: copy(second),
    expected_prior_history_digest: first.state.history_digest,
    idempotency_key: key(), verifier_ref: VERIFIER }), "clock_backdated_revision");
});

// ---------------------------------------------------------------------------
// 7. The authority boundary.
// ---------------------------------------------------------------------------

test("the public evaluate-and-record verb fails closed before it touches the database", async () => {
  class ToolError extends Error {
    constructor(payload) { super(payload.error); Object.assign(this, payload); }
  }
  let queries = 0;
  const connection = { query() { queries += 1; throw new Error("no query may be issued on this path"); } };
  const tools = journeyOneClockStoreTools({
    withEnvelope: (_c, _actor, _verb, _args, fn) => fn(), ToolError,
  });
  const verb = tools["evaluate-and-record-journey-one-clock"];
  assert.equal(verb.write, true);
  assert.equal(verb.inputSchema.additionalProperties, false);
  // No humanOnly and no authorityOnly flag: this rail invents no new identity or
  // authority infrastructure and does not pretend a recording is a partner act.
  assert.equal(verb.humanOnly, undefined);
  assert.equal(verb.authorityOnly, undefined);

  await assert.rejects(
    verb.handler(connection, ACTOR, { idempotency_key: key(), expected_prior_history_digest: null }),
    error => {
      assert.equal(error.error, "clock_input_authority_unbound");
      assert.equal(error.detail.resolved, false);
      assert.ok(error.detail.explicitly_refused.some(l => l.includes("verified: true")));
      return true;
    });
  assert.equal(queries, 0, "the refusal happens before any query, so nothing can be half-started");
});

test("a caller cannot smuggle an asserted authority past the store", async () => {
  const { store } = newStore();
  const state = copy(run(snapshot()).state);
  for (const smuggled of [
    { verified: true }, { authority_granted: true }, { clock_started: true },
    { accepted_by: "joe" }, { gate_zero_outcome_digest: D(9) },
  ]) {
    await refuses(store.record({ state: copy(state), expected_prior_history_digest: null,
      idempotency_key: key(), verifier_ref: VERIFIER, ...smuggled }),
      "self_asserted_authority_refused");
  }
  assert.equal((await store.read(journeyOneClockKeyForState(state))).exists, false);
});

test("the recorder cannot be built without a kernel, and the kernel cannot be built without a verifier", () => {
  assert.throws(() => createJourneyOneClock({}), e => e.code === "authenticated_verifier_required");
  assert.throws(() => createJourneyOneClock({ verifySnapshot: { verified: true } }),
    e => e.code === "authenticated_verifier_required");
  assert.throws(() => createJourneyOneClockRecorder({ store: {} }),
    e => e.code === "authenticated_kernel_required");
  const { store } = newStore();
  assert.throws(() => createJourneyOneClockRecorder({ clock: {}, store }),
    e => e.code === "authenticated_kernel_required");
});

test("the trusted recorder evaluates and records in one act, and claims nothing more", async () => {
  const { store } = newStore();
  const h = harness();
  const recorder = createJourneyOneClockRecorder({ clock: h.clock, store, verifier_ref: VERIFIER });
  const p = snapshot();
  const result = await recorder.evaluateAndRecord({
    envelope: h.envelopeFor(p), expected_prior_history_digest: null, idempotency_key: key(),
    clock_ref: "J1-TRUSTED-PATH" });
  assert.equal(result.ok, true);
  assert.equal(result.revision_ordinal, 0);
  assert.equal(result.kernel_verdict.status, "running");
  assert.equal(result.kernel_verdict.deadline_success, false);
  assert.equal(result.kernel_verdict.replan_required, false);
  // The kernel's obligation is discharged for THIS revision and for no other.
  assert.equal(result.durable_history_write_required, false);
  assert.equal(result.deadline_accepted_by_record_layer, false);
  assert.equal(result.effects.acceptances, 0);
  assert.equal(result.effects.clock_started, false);
  assert.equal(result.effects.database_writes, 1,
    "a record layer does not get to claim the pure kernel's zero-writes");

  const readback = await store.read(result.clock_key);
  assert.equal(readback.history_digest, result.history_digest);

  // An unauthenticated envelope gets nowhere, and nothing is stored for it.
  await assert.rejects(recorder.evaluateAndRecord({
    envelope: { synthetic_receipt_ref: "never-authenticated", verified: true },
    expected_prior_history_digest: null, idempotency_key: key() }),
    e => e.message === "synthetic-authentication-refused");
  assert.equal((await store.read(result.clock_key)).revision_count, 1);
});

test("the integration descriptor names the missing authority narrowly and claims no more", () => {
  const d = journeyOneClockStoreIntegrationRequirements();
  assert.equal(d.public_evaluate_and_record_available, false);
  assert.equal(d.storage_implemented, true, "the storage is not disabled; only the input authority is missing");
  assert.equal(d.clock_started, false);
  assert.equal(d.input_authority.resolved, false);
  assert.deepEqual(d.public_evaluate_and_record_blocked_by, [d.input_authority.binding_ref]);
  assert.equal(d.state_schema_version, JOURNEY_ONE_CLOCK_SCHEMA);
  assert.deepEqual(d.legacy_state_schemas_refused, ["doctorcre-v5-journey-one-clock.v1"]);
  assert.deepEqual(d.append_invariants.map(i => i.id), JOURNEY_ONE_CLOCK_APPEND_INVARIANT_IDS);
  assert.equal(d.effects.database_writes, 0);
  // It describes what is missing HERE; it makes no claim about the record as a
  // whole, and it does not say a clock exists or does not exist elsewhere.
  assert.ok(d.input_authority.scope.includes("not a claim about what exists outside it"));
  assert.ok(d.input_authority.why_unresolved.some(l => l.includes("NO CLOCK HAS BEEN STARTED")));

  // THE ONE THING IT ASKED ANOTHER FILE FOR, now landed — and the surrounding
  // authority is still missing, which the descriptor must keep saying.
  const ask = d.input_authority.read_only_kernel_projection_extension_required;
  assert.equal(ask.resolved, true);
  assert.ok(ask.exact_requirement.includes("Read-only"));
  assert.ok(ask.exact_requirement.includes("adds no field to the hashed state"),
    "a field added to the state would rebase every stored clock");
  assert.ok(ask.what_landed.includes("verified_binding"));
  assert.ok(ask.what_landed.includes("BEFORE any journal read or write"));
  assert.ok(ask.hashed_state_unchanged.includes("gained no field"));
  assert.ok(ask.still_not_proved.some(l => l.includes("direct store.record()")),
    "resolving the extension must not be read as closing the direct record path");
  assert.ok(ask.still_not_proved.some(l => l.includes("has never been executed")));
  assert.ok(ask.explicitly_not_done_instead.some(l => l.includes("one-clock-per-tenant")),
    "the descriptor must say the invented policy was refused, not applied");
  // The requirement AROUND it is still open: no reader, no producers.
  assert.equal(d.input_authority.resolved, false);
  assert.ok(d.input_authority.required_to_resolve.some(l => l.includes("authenticated projection reader")));
  assert.ok(d.input_authority.why_unresolved.some(l => l.includes("No live producer")));

  // The label defect, recorded beside it with what was and was not done.
  const label = d.input_authority.scope_label_excluded_from_identity;
  assert.equal(label.resolved, true);
  assert.ok(label.defect.includes("scope_ref"));
  assert.ok(label.fix.includes("doctorcre:j1-clock-scope:v2"));
  assert.ok(label.nothing_was_rewritten.includes("never been applied"));
  assert.equal(d.clock_scope_binding_required_for_writes, true);
  assert.ok(d.storage_notes.some(l => l.includes("defends against a caller RENAMING a clock and against nothing else")),
    "the descriptor must not oversell the derived identity");
  assert.ok(d.input_authority.explicitly_refused.some(l => l.includes("clock scope taken from a request")));
});

// ---------------------------------------------------------------------------
// 8. The two homes of one rule set, and the candidate SQL's own hygiene.
// ---------------------------------------------------------------------------

const SQL = readFileSync(
  fileURLToPath(new URL("../../ops/journey-one-clock-store.candidate.sql", import.meta.url)), "utf8");
const POSTGRES_PROOF = readFileSync(
  fileURLToPath(new URL("./journey-one-clock-store-postgres.sql", import.meta.url)), "utf8");

test("every shared append invariant is stated in both homes, and the one-home entry says so", () => {
  // Rule a8c55a47: a duplicated operation needs something that COMPARES the two
  // copies. This is that comparison. It proves neither home dropped an entry. It
  // does NOT prove the SQL is correct — the SQL has never run.
  assert.equal(JOURNEY_ONE_CLOCK_APPEND_INVARIANT_IDS.length, 17);
  assert.ok(JOURNEY_ONE_CLOCK_APPEND_INVARIANT_IDS.includes("j1_clock_scope_label_is_not_identity"));
  const invariants = journeyOneClockStoreIntegrationRequirements().append_invariants;
  for (const id of JOURNEY_ONE_CLOCK_APPEND_INVARIANT_IDS) {
    assert.ok(SQL.includes(id), `ops/journey-one-clock-store.candidate.sql never names ${id}`);
  }
  const jsSource = readFileSync(
    fileURLToPath(new URL("../src/journey-one-clock-store.v5.js", import.meta.url)), "utf8");
  for (const invariant of invariants) {
    // Twice in the module: once in the declaration, once in at least one refusal
    // detail. Except the one whose only possible home is the database, which
    // declares that in its own field rather than being quietly exempted.
    const occurrences = jsSource.split(invariant.id).length - 1;
    if (invariant.enforced_in.includes("module")) {
      assert.ok(occurrences >= 2, `the module never enforces ${invariant.id}`);
    } else {
      assert.deepEqual(invariant.enforced_in, ["record_layer"]);
      assert.ok(invariant.statement.includes("Enforced only at the database"),
        "a one-home invariant must say why it has one home");
    }
  }
  assert.equal(invariants.filter(i => !i.enforced_in.includes("module")).length, 1);
  // THE SHARED COUNT IS THE LIST'S OWN, read off enforced_in rather than off any
  // description of it: sixteen of the seventeen have two homes, and the
  // seventeenth is the record layer's alone.
  assert.equal(invariants.filter(i =>
    i.enforced_in.includes("module") && i.enforced_in.includes("record_layer")).length, 16);
});

test("the candidate SQL is fresh-or-exactly-compatible and namespaced, with no ALTER and no backfill", () => {
  assert.ok(!/\balter\s+table\b/i.test(SQL), "no ALTER TABLE");
  // Column zero is top level: every data statement in this file lives indented
  // inside a function body, so none of them runs when the file is applied.
  assert.ok(!/^(insert|update|delete|truncate|copy)\b/im.test(SQL),
    "no data statement at the top level; applying the file writes no row");
  assert.ok(!/insert\s+into\s+public\.schema_migrations/i.test(SQL),
    "candidate source registers no migration ledger entry");
  assert.ok(!/create\s+(role|schema|extension)\b/i.test(SQL),
    "it creates no role, schema or extension");
  assert.ok(!/create\s+or\s+replace\s+function\s+ops\.(?!j1_clock_)/i.test(SQL),
    "every function it defines is namespaced ops.j1_clock_*");
  for (const relation of ["ops.j1_clock", "ops.j1_clock_scope_binding", "ops.j1_clock_revision",
    "ops.j1_clock_revision_pause_interval", "ops.j1_clock_revision_event"]) {
    assert.ok(SQL.includes(`create table if not exists ${relation} (`),
      `${relation} is created only when absent`);
  }
  // The append-only triggers, the freeze protocol, the deferred guard and the
  // partial unique index that makes a null prior a real compare-and-swap.
  assert.ok(SQL.includes("before update or delete on ops."));
  assert.ok(SQL.includes("create unique index if not exists j1_clock_revision_one_creation"));
  assert.ok(SQL.includes("create constraint trigger j1_clock_append_guard"));
  assert.ok(SQL.includes("deferrable initially deferred"));
  assert.ok(SQL.includes("for share") && SQL.includes("for update"),
    "both halves of the freeze/append lock protocol are present");
  // NO COLUMN MAY BE NAMED AS THOUGH THE DATABASE VERIFIED SOMETHING. Matched
  // against column DEFINITIONS — an indented name followed by a type — rather
  // than against the file text, so the prose that explains the rule does not
  // trip the check that enforces it.
  const columns = [...SQL.matchAll(/^\s{2,}([a-z_]+)\s+(text|boolean|uuid|bigint|integer|timestamptz|jsonb)\b/gm)]
    .map(m => m[1]);
  assert.ok(columns.length > 20, "the column scan found the table definitions");
  for (const forbidden of ["deadline_success", "verified", "verified_human",
    "partner_confirmed", "accepted", "accepted_by", "admitted", "human_approved"]) {
    assert.ok(!columns.includes(forbidden), `no column may be named ${forbidden}`);
  }
  // And the one column that carries the trust statement is pinned to a single
  // value by a CHECK, so no writer can widen it into a claim of verification.
  assert.ok(SQL.includes("check (input_authority = 'trusted_projection_not_independently_verified_by_this_record_layer')"));
  assert.ok(columns.includes("input_authority"));
});

test("truncate is refused by a statement-level trigger and not only by a revoke", () => {
  // The header of the candidate SQL claims "update, delete and truncate are
  // refused everywhere on this rail". A ROW-LEVEL trigger never sees TRUNCATE --
  // it is a statement event -- and `revoke ... truncate` does not bind the table
  // owner, so for a while that claim was true of every runtime bundle and false
  // of the owner. The second trigger is what makes it true. Same pattern as
  // ops/journey-one-clock-input-store.candidate.sql, which fixed it first.
  assert.match(SQL,
    /create trigger %I before truncate on ops\.%I for each statement execute function ops\.j1_clock_rows_immutable\(\)/);
  assert.ok(SQL.includes("_no_truncate"));
  assert.ok(SQL.includes("using errcode = '42501'"),
    "a refusal that is really a privilege refusal says so in its SQLSTATE, as the house pattern does");
  assert.ok(SQL.includes("revoke insert, update, delete, truncate on ops.j1_clock,"),
    "the grant half stays; it is the half that binds a runtime bundle");
  // And the rollback-only fixture attempts it and checks the trigger by name --
  // the part a revoke could never demonstrate.
  assert.ok(POSTGRES_PROOF.includes("truncate ops.%I cascade"));
  assert.ok(POSTGRES_PROOF.includes("_no_truncate"));
  assert.ok(POSTGRES_PROOF.includes("has_table_privilege(current_user, 'ops.' || v_relation, 'TRUNCATE')"),
    "the fixture must skip rather than re-raise when the invoking role cannot reach the trigger");
  assert.ok(POSTGRES_PROOF.includes("has_table_privilege(v_role, v_relation, 'TRUNCATE')"),
    "and truncate must be part of the bundle-privilege assertion, now that it is a first-class claim");
});

test("the candidate SQL reuses the existing canonicalizer and writer derivation rather than restating them", () => {
  assert.ok(SQL.includes("ops.portfolio_canonical_json("),
    "one canonicalizer, reused; two that agree today are two that can disagree after one edit");
  assert.ok(SQL.includes("ops.portfolio_writer_actor_id()"), "one writer derivation, reused");
  assert.ok(!/create\s+or\s+replace\s+function\s+ops\.j1_clock_canonical/i.test(SQL));
  // Direct DML reaches nobody, and the write path reaches the writer bundle only.
  assert.ok(SQL.includes("revoke insert, update, delete, truncate on ops.j1_clock,"));
  assert.ok(/grant execute on function ops\.j1_clock_lock\(text\)[\s\S]{0,400}to carr_writer, carr_authority;/.test(SQL));
  // The arity of every granted function matches the one that is revoked first.
  for (const signature of [
    "ops.j1_clock_identity_digest(text,text,text,text)",
    "ops.j1_clock_append_revision(text,text,text,text,uuid,text,jsonb,jsonb,jsonb,jsonb)",
    "ops.j1_clock_assert_same(text,text,anyelement,anyelement)",
    "ops.j1_clock_history(text)", "ops.j1_clock_revision_integrity_error(uuid)",
    "ops.j1_clock_scope_digest(jsonb)", "ops.j1_clock_scope_bindings(text,text)",
    "ops.j1_clock_bind_scope(text,jsonb)", "ops.j1_clock_scope_lock(text)",
  ]) {
    assert.equal(SQL.split(signature).length - 1 >= 2, true,
      `${signature} must be both revoked and granted with the same arity`);
  }
});

test("the append function brackets its deferred guard instead of leaving a mode behind", () => {
  // A TEXT-ORDERING CHECK OVER SQL THAT HAS NEVER RUN. It proves the three mode
  // statements are WRITTEN in the only order that can work, and nothing about
  // what a database does with them. The behavioural claim lives in the postgres
  // fixture, which has not been executed either.
  //
  // WHY THE ORDER IS LOAD-BEARING: SET CONSTRAINTS is transaction-scoped, so an
  // IMMEDIATE left in force by one call validates the NEXT revision row at the
  // end of its own INSERT -- before that revision's events and pause intervals
  // have been written -- and refuses a valid append for carrying no events.
  const start = SQL.indexOf("create or replace function ops.j1_clock_append_revision(");
  assert.ok(start > 0, "the candidate SQL still defines the append function");
  const body = SQL.slice(start, SQL.indexOf("\n$$;", start));
  const modes = [...body.matchAll(
    /execute 'set constraints ops\.j1_clock_append_guard (deferred|immediate)'/g)];
  assert.deepEqual(modes.map(m => m[1]), ["deferred", "immediate", "deferred"],
    "defer before the revision row exists, force over its complete children, restore before returning");

  const at = (needle) => {
    const index = body.indexOf(needle);
    assert.ok(index > 0, `the append function no longer contains: ${needle}`);
    return index;
  };
  const parentInsert = at("insert into ops.j1_clock_revision(");
  const pauseInsert = at("insert into ops.j1_clock_revision_pause_interval(");
  const eventInsert = at("insert into ops.j1_clock_revision_event(");
  const [deferBefore, force, restore] = modes.map(m => m.index);
  assert.ok(deferBefore < parentInsert,
    "the guard is deferred BEFORE the revision row is inserted, or a mode left immediate fires against no children");
  assert.ok(force > pauseInsert && force > eventInsert,
    "the guard is forced only after both child relations are written");
  assert.ok(force > parentInsert && restore > force,
    "the deferred mode is restored after the forced check, not before it");
  assert.ok(restore < at("return v_row"),
    "the mode is restored before the function returns, not left for the next caller");
  // The guard itself is untouched: still a deferrable constraint trigger, still
  // firing after insert. This fix changed WHEN it is asked, never whether.
  assert.ok(/create constraint trigger j1_clock_append_guard\s+after insert on ops\.j1_clock_revision\s+deferrable initially deferred/
    .test(SQL), "the append guard is still a deferrable constraint trigger");
  assert.ok(!/execute\s+'set constraints all/i.test(body),
    "the write path narrows its own guard's mode and never every constraint's");
  // And the whole candidate issues no OTHER constraint-mode statement: three
  // executed statements in the file, and all three are the ones bracketed above.
  assert.equal([...SQL.matchAll(/execute\s+'set constraints[^']*'/g)].length, modes.length,
    "the candidate's only constraint-mode statements are this function's three");
});

test("the postgres proof is transaction-scoped, skips cleanly and asserts the direct-writer negatives", () => {
  assert.ok(POSTGRES_PROOF.includes("\\set ON_ERROR_STOP on"));
  assert.ok(POSTGRES_PROOF.trimEnd().endsWith("rollback;"), "every fixture row is rolled back");
  assert.ok(POSTGRES_PROOF.includes("raise notice 'SKIPPED:"),
    "it skips with a notice when the candidate SQL has not been applied");
  for (const negative of [
    "stale prior", "second creation", "idempotency", "removed miss", "changed seal",
    "lost event", "rebased origin", "tampered", "append-only",
    "A SECOND ORIGIN FOR ONE AUTHORITATIVE SCOPE", "rebound to a second scope",
    "no scope binding at all",
  ]) {
    assert.ok(POSTGRES_PROOF.toLowerCase().includes(negative.toLowerCase()),
      `the proof does not exercise: ${negative}`);
  }

  // THE TRANSACTION-SEQUENCING GROUP. The fixture opens ONE transaction, so its
  // four appends are four appends in one transaction by construction -- the
  // shape that a constraint mode left in force would break at the second one.
  // One of them is issued after the fixture itself sets every constraint
  // immediate, which is the caller-entered case the append function corrects.
  assert.equal(POSTGRES_PROOF.split("begin;").length - 1, 1,
    "the fixture is one transaction, so its appends share one constraint-mode state");
  assert.ok(POSTGRES_PROOF.includes("execute 'set constraints all immediate'"),
    "the fixture enters the caller-set IMMEDIATE mode the append function has to correct");
  assert.ok(POSTGRES_PROOF.includes("must be ordinal 2") && POSTGRES_PROOF.includes("must be ordinal 3"),
    "the fixture appends a third and a fourth revision in that same transaction");
  assert.ok(POSTGRES_PROOF.includes("lost its event chain")
    && POSTGRES_PROOF.includes("lost its pause interval"),
    "each of those appends is checked for its complete children, not merely for landing");
  assert.ok(POSTGRES_PROOF.split("ops.j1_clock_append_revision(").length - 1 >= 20,
    "the fixture still drives the append function directly for every case");
});

test("the postgres journal issues only the definer calls the candidate SQL defines", async () => {
  // A SHAPE ASSERTION, NOT A SIMULATION. It records the SQL text this journal
  // would issue and checks each statement names a function the candidate file
  // actually defines with that arity. It proves nothing about behaviour: the
  // candidate SQL has never been applied and this journal has never run against
  // a database.
  const issued = [];
  const journal = createPostgresJourneyOneClockJournal({
    query(sql) { issued.push(sql); throw new Error("halt: this proof reads the calls, not their answers"); },
  });
  const clockKey = `sha256:${"0".repeat(64)}`;
  await assert.rejects(journal.readClock(clockKey), /halt/);
  await assert.rejects(journal.readRevisions(clockKey), /halt/);
  const beforeAppend = issued.length;
  await assert.rejects(journal.runAppend(clockKey,
    { idempotencyKey: key(), build: () => { throw new Error("unreached"); } }), /halt/);
  assert.equal(issued.length, beforeAppend + 1);
  for (const sql of issued) {
    const name = /ops\.(j1_clock[a-z_]*)\(/.exec(sql)[1];
    assert.ok(SQL.includes(`create or replace function ops.${name}(`),
      `the journal calls ops.${name}, which the candidate SQL does not define`);
  }
  // The serialized section is opened FIRST, before the head is read, so the head
  // this journal reads is a settled answer rather than a stale one.
  assert.ok(issued[beforeAppend].includes("ops.j1_clock_lock("));
  // Direct INSERT is granted to nobody, so this journal must never issue one.
  for (const sql of issued) assert.ok(!/\binsert\b/i.test(sql));
});

// ---------------------------------------------------------------------------
// 9. THE AUTHORITATIVE CLOCK SCOPE.
//
// The derived clock key defends against a caller RENAMING a clock. It cannot
// defend against a caller presenting a DIFFERENT ORIGIN, because that derives a
// different key, and a different key has no head for a compare-and-swap to
// refuse. These tests are about the thing that does, and about being honest that
// the same-origin negative above never proved it.
// ---------------------------------------------------------------------------

test("the authoritative scope is an exact trusted binding, and it is not the origin", () => {
  const derived = journeyOneClockScopeKey(SCOPE);
  // THE PREIMAGE IS THE SIX IDENTITY FIELDS, and the label is not one of them.
  assert.equal(derived, digest([JOURNEY_ONE_CLOCK_SCOPE_DOMAIN_TAG,
    Object.fromEntries(JOURNEY_ONE_CLOCK_SCOPE_IDENTITY_FIELDS.map(f => [f, SCOPE[f]]))]));
  assert.deepEqual([...JOURNEY_ONE_CLOCK_SCOPE_IDENTITY_FIELDS],
    JOURNEY_ONE_CLOCK_SCOPE_FIELDS.filter(f => f !== "scope_ref"));
  const { store } = newStore();
  assert.equal(store.clock_scope.clock_scope_key, derived);
  assert.equal(store.clock_scope.clock_scope_ref, SCOPE.scope_ref);
  assert.ok(!Object.hasOwn(store.clock_scope.scope_identity, "scope_ref"),
    "the key's preimage must not carry the human label");

  // IT IS STABLE ACROSS ORIGINS, which is the whole point: two different origins
  // for one accepted scope address one scope and meet each other.
  const first = run(snapshot()).state;
  const second = run(snapshot(iso(Date.parse(ORIGIN) + DAY), iso(Date.parse(ORIGIN) + DAY))).state;
  assert.notEqual(journeyOneClockKeyForState(first), journeyOneClockKeyForState(second));

  // Closed, tenant-bound, and the two gate ids are COMPARED against the deadline
  // contract rather than believed.
  for (const [mutation, code] of [
    [{ ...SCOPE, extra_field: true }, "closed_shape"],
    [{ ...SCOPE, tenant: "someone-elses-tenant" }, "wrong_tenant"],
    [{ ...SCOPE, clock_origin_gate_id: "some-other-gate-accepted" }, "wrong_clock_scope_gate"],
    [{ ...SCOPE, clock_terminus_gate_id: "some-other-gate-accepted" }, "wrong_clock_scope_gate"],
    [{ ...SCOPE, scope_ref: "journey-one" }, "invalid_reference"],
    [{ ...SCOPE, benchmark_subject_digest: "not-a-digest" }, "invalid_digest"],
  ]) {
    assert.throws(() => journeyOneClockScopeKey(mutation), e => e.code === code);
    // A malformed scope is a refusal AT CONSTRUCTION, not a surprise on the
    // first write.
    assert.throws(() => createJourneyOneClockStore({
      journal: createEphemeralJourneyOneClockJournal(), actor: ACTOR, clock_scope: mutation }),
      e => e.code === code);
  }
  // And the rail says out loud that it compares this binding rather than
  // verifying it: the stored state carries nothing to verify it against.
  assert.ok(JOURNEY_ONE_CLOCK_STORE_CANNOT_PROVE.some(line =>
    line.includes("authoritative scope") && line.includes("never derives one from a stored history")));
});

test("a new origin for a scope that already holds a clock is refused, not given a fresh clock", async () => {
  const { store } = newStore();
  const first = run(snapshot()).state;
  await store.record({ state: copy(first), expected_prior_history_digest: null,
    idempotency_key: key(), verifier_ref: VERIFIER });
  const firstKey = journeyOneClockKeyForState(first);

  // A SECOND ADMITTED MINIMUM, observed a day later, presented with NO HISTORY.
  // The kernel never sees a rebase -- there is no old history in this projection
  // to rebase -- and the creation CAS has nothing to refuse, because this origin
  // derives an address the record layer has never seen. The scope is what stops
  // it.
  const rebased = run(snapshot(iso(Date.parse(ORIGIN) + DAY), iso(Date.parse(ORIGIN) + DAY))).state;
  const rebasedKey = journeyOneClockKeyForState(rebased);
  assert.notEqual(rebasedKey, firstKey);
  await refuses(store.record({ state: copy(rebased), expected_prior_history_digest: null,
    idempotency_key: key(), verifier_ref: VERIFIER }), "clock_scope_already_bound");
  assert.equal((await store.read(rebasedKey)).exists, false, "no second clock was created");
  assert.equal((await store.read(firstKey)).revision_count, 1, "and the running clock is untouched");

  // AN AMENDED BENCHMARK MANIFEST READ AS THE ORIGIN MANIFEST is the same attack
  // wearing another field, and so is a CHANGED ORIGIN RECEIPT: each derives a
  // fresh address and each meets the scope that already holds a clock.
  for (const field of ["origin_benchmark_manifest_digest", "origin_receipt_digest"]) {
    const moved = copy(first);
    moved[field] = D(88);
    if (field === "origin_benchmark_manifest_digest") {
      moved.current_benchmark_manifest_digest = D(88);
    } else {
      // The clock_started event evidences the origin receipt, so a moved origin
      // receipt takes its own event with it: this is a WELL FORMED history of a
      // clock that started somewhere else, which is exactly the difficulty.
      moved.events[0].evidence_digest = D(88);
      const { event_digest, ...body } = moved.events[0];
      moved.events[0].event_digest = digest(body);
    }
    reseal(moved);
    assert.notEqual(journeyOneClockKeyForState(moved), firstKey);
    await refuses(store.record({ state: moved, expected_prior_history_digest: null,
      idempotency_key: key(), verifier_ref: VERIFIER }), "clock_scope_already_bound");
  }

  // A COPIED HISTORY WITH THE OLD STATE LEFT OUT lands in the same place: it is
  // a creation for an origin this scope did not start with.
  const { store: elsewhere } = newStore({ clock_scope: OTHER_SCOPE });
  const landed = await elsewhere.record({ state: copy(rebased),
    expected_prior_history_digest: null, idempotency_key: key(), verifier_ref: VERIFIER });
  assert.equal(landed.revision_ordinal, 0,
    "the same origin under a DIFFERENT accepted scope is a different clock, and is storable");
});

test("two origins racing for one authoritative scope: exactly one clock lands", async () => {
  const { store } = newStore();
  const a = run(snapshot()).state;
  const b = run(snapshot(iso(Date.parse(ORIGIN) + DAY), iso(Date.parse(ORIGIN) + DAY))).state;
  assert.notEqual(journeyOneClockKeyForState(a), journeyOneClockKeyForState(b));

  // TWO DIFFERENT CLOCK KEYS HOLD NOTHING IN COMMON: the per-clock serialized
  // section does not serialize them against each other, so they interleave.
  // That is why the binding refuses inside the JOURNAL and not only in the
  // caller -- a check the caller makes and a write it makes later are two
  // moments, and something has to be atomic across them.
  const outcomes = await Promise.allSettled([
    store.record({ state: copy(a), expected_prior_history_digest: null,
      idempotency_key: key(), verifier_ref: VERIFIER }),
    store.record({ state: copy(b), expected_prior_history_digest: null,
      idempotency_key: key(), verifier_ref: VERIFIER }),
  ]);
  assert.equal(outcomes.filter(o => o.status === "fulfilled").length, 1);
  assert.equal(outcomes.find(o => o.status === "rejected").reason.code, "clock_scope_already_bound");
  const bound = await store.readClockKeyForScope();
  assert.equal(bound.clock_key, outcomes.find(o => o.status === "fulfilled").value.clock_key);
});

test("a clock is bound to one authoritative scope at creation and is never rebound", async () => {
  const { journal, store } = newStore();
  const first = run(snapshot()).state;
  await store.record({ state: copy(first), expected_prior_history_digest: null,
    idempotency_key: key(), verifier_ref: VERIFIER });

  const p = snapshot(iso(Date.parse(ORIGIN) + 2 * DAY));
  p.history = copy(first);
  const next = run(p).state;

  const other = createJourneyOneClockStore({ journal, actor: ACTOR, clock_scope: OTHER_SCOPE });
  await refuses(other.record({ state: copy(next),
    expected_prior_history_digest: first.history_digest,
    idempotency_key: key(), verifier_ref: VERIFIER }), "clock_scope_changed");
  // The same append under the scope the clock was created with lands normally.
  await store.record({ state: copy(next), expected_prior_history_digest: first.history_digest,
    idempotency_key: key(), verifier_ref: VERIFIER });
  assert.equal((await store.read(journeyOneClockKeyForState(first))).revision_count, 2);
});

test("a write with no authoritative scope refuses, and a read needs none", async () => {
  const { journal, store } = newStore();
  const first = run(snapshot()).state;
  const clockKey = journeyOneClockKeyForState(first);
  await store.record({ state: copy(first), expected_prior_history_digest: null,
    idempotency_key: key(), verifier_ref: VERIFIER });

  const unscoped = createJourneyOneClockStore({ journal, actor: ACTOR });
  assert.equal(unscoped.clock_scope, null);
  await refuses(unscoped.record({ state: copy(first), expected_prior_history_digest: null,
    idempotency_key: key(), verifier_ref: VERIFIER }), "clock_scope_binding_required");
  await assert.rejects(unscoped.readClockKeyForScope(),
    e => e.code === "clock_scope_binding_required");

  // A readback addresses a clock that already exists BY ITS OWN KEY, so it needs
  // no scope -- and it reports which scope holds the clock rather than assuming.
  const readback = await unscoped.read(clockKey);
  assert.equal(readback.exists, true);
  assert.equal(readback.clock_scope_bound, true);
  assert.equal(readback.clock_scope_key, journeyOneClockScopeKey(SCOPE));
  assert.equal(readback.clock_scope_ref, SCOPE.scope_ref);

  // A trusted integration may ASK which clock its scope holds before it presents
  // an origin, instead of learning the answer as a refusal. It reads only.
  const asked = await store.readClockKeyForScope();
  assert.equal(asked.clock_key, clockKey);
  assert.equal(asked.effects.database_writes, 0);
  const empty = await newStore().store.readClockKeyForScope();
  assert.equal(empty.clock_key, null, "a null here is 'no clock in this record layer', not 'no clock'");
});

// ---------------------------------------------------------------------------
// 10. THE SCOPE LABEL IS PROVENANCE AND NEVER IDENTITY.
//
// The key used to be hashed over all seven declared fields, including the human
// scope_ref, while every comment beside it said that field carried no authority.
// With the label inside the preimage, one accepted scope spelled two ways
// produced two keys -- so "one scope, one clock" silently meant "one LABEL, one
// clock", and a relabelled store could start a second clock for one program on a
// fresh origin. These tests are that hole, from both sides.
// ---------------------------------------------------------------------------

/** A journal that records every call, so "no write" can be proved as "no call". */
function watchedJournal({ now = Date.now } = {}) {
  const inner = createEphemeralJourneyOneClockJournal({ now });
  const calls = [];
  const wrap = name => async (...args) => { calls.push(name); return inner[name](...args); };
  return { calls, inner, journal: {
    durable: false, kind: inner.kind,
    runAppend: wrap("runAppend"), readClock: wrap("readClock"),
    readRevisions: wrap("readRevisions"), readScopeBindings: wrap("readScopeBindings"),
    bindScope: wrap("bindScope"),
  } };
}

test("one accepted scope under two names is one scope, and two subjects are still two", () => {
  // Identical identity fields, different label: ONE key.
  assert.equal(journeyOneClockScopeKey(RELABELLED_SCOPE), journeyOneClockScopeKey(SCOPE));
  assert.notEqual(RELABELLED_SCOPE.scope_ref, SCOPE.scope_ref);
  // And the label is not lost: it travels beside the key as provenance.
  const binding = journeyOneClockScopeBinding(RELABELLED_SCOPE);
  assert.equal(binding.clock_scope_ref, RELABELLED_SCOPE.scope_ref);
  assert.equal(binding.scope.scope_ref, RELABELLED_SCOPE.scope_ref);
  assert.deepEqual(Object.keys(binding.scope_identity).sort(),
    [...JOURNEY_ONE_CLOCK_SCOPE_IDENTITY_FIELDS]);
  // MOVING A REAL IDENTITY FIELD STILL SEPARATES TWO PROGRAMS. Excluding the
  // label narrows what addresses a scope; it does not merge scopes.
  for (const field of JOURNEY_ONE_CLOCK_SCOPE_IDENTITY_FIELDS) {
    if (field.endsWith("_gate_id") || field === "tenant") continue;
    assert.notEqual(journeyOneClockScopeKey({ ...SCOPE, [field]: D(21) }),
      journeyOneClockScopeKey(SCOPE), `${field} must still separate two accepted scopes`);
  }
  assert.notEqual(journeyOneClockScopeKey(OTHER_SCOPE), journeyOneClockScopeKey(SCOPE));
});

test("a relabelled scope cannot open a second clock for one program", async () => {
  const watch = watchedJournal();
  const store = createJourneyOneClockStore({ journal: watch.journal, actor: ACTOR, clock_scope: SCOPE });
  const first = run(snapshot()).state;
  await store.record({ state: copy(first), expected_prior_history_digest: null,
    idempotency_key: key(), verifier_ref: VERIFIER });

  // THE ATTACK: the same accepted scope under a new name, presenting a FRESH
  // ORIGIN with no history. The origin derives a clock key this rail has never
  // seen, so the compare-and-swap has nothing to refuse it with. Before the fix
  // the relabelled scope hashed to a second key and this landed a second clock.
  const renamed = createJourneyOneClockStore({
    journal: watch.journal, actor: ACTOR, clock_scope: RELABELLED_SCOPE });
  assert.equal(renamed.clock_scope.clock_scope_key, store.clock_scope.clock_scope_key);
  const rebased = run(snapshot(iso(Date.parse(ORIGIN) + DAY), iso(Date.parse(ORIGIN) + DAY))).state;
  const rebasedKey = journeyOneClockKeyForState(rebased);
  await refuses(renamed.record({ state: copy(rebased), expected_prior_history_digest: null,
    idempotency_key: key(), verifier_ref: VERIFIER }), "clock_scope_already_bound");

  assert.equal((await store.read(rebasedKey)).exists, false, "no second clock was created");
  assert.equal((await store.read(journeyOneClockKeyForState(first))).revision_count, 1);
  // And a genuinely different accepted subject is still its own scope and its
  // own clock: the fix refuses relabelling, not legitimate separation.
  const elsewhere = createJourneyOneClockStore({
    journal: watch.journal, actor: ACTOR, clock_scope: OTHER_SCOPE });
  const landed = await elsewhere.record({ state: copy(rebased),
    expected_prior_history_digest: null, idempotency_key: key(), verifier_ref: VERIFIER });
  assert.equal(landed.revision_ordinal, 0);
});

test("the label a scope was bound under is recorded once and is never replaced", async () => {
  const { journal, store } = newStore();
  const first = run(snapshot()).state;
  const clockKey = journeyOneClockKeyForState(first);
  await store.record({ state: copy(first), expected_prior_history_digest: null,
    idempotency_key: key(), verifier_ref: VERIFIER });

  const p = snapshot(iso(Date.parse(ORIGIN) + 2 * DAY));
  p.history = copy(first);
  const next = run(p).state;

  // The same clock, the same scope KEY, a different name for it. It is not
  // identity, so it cannot address a second scope — and precisely because it is
  // not identity, it must not silently overwrite the provenance either.
  const renamed = createJourneyOneClockStore({ journal, actor: ACTOR, clock_scope: RELABELLED_SCOPE });
  await refuses(renamed.record({ state: copy(next),
    expected_prior_history_digest: first.history_digest,
    idempotency_key: key(), verifier_ref: VERIFIER }), "clock_scope_label_changed");
  const readback = await store.read(clockKey);
  assert.equal(readback.revision_count, 1, "the refused relabelling stored nothing");
  assert.equal(readback.clock_scope_ref, SCOPE.scope_ref);
  // The same append under the name the scope was bound with lands normally.
  await store.record({ state: copy(next), expected_prior_history_digest: first.history_digest,
    idempotency_key: key(), verifier_ref: VERIFIER });
  assert.equal((await store.read(clockKey)).revision_count, 2);
});

test("the store itself meets the label seal, before the journal's own seal can", async () => {
  // The refusal above is real, but until now its only home on the DURABLE path
  // was ops.j1_clock_bind_scope — SQL that has never run. The store would pass
  // its whole read, its compare-and-swap and its append-only diff, and meet the
  // seal in the one place this rail cannot execute. So the store holds it too,
  // and this proves the refusal happens without the binding write being reached.
  const watch = watchedJournal();
  const store = createJourneyOneClockStore({
    journal: watch.journal, actor: ACTOR, clock_scope: SCOPE });
  const first = run(snapshot()).state;
  const clockKey = journeyOneClockKeyForState(first);
  await store.record({ state: copy(first), expected_prior_history_digest: null,
    idempotency_key: key(), verifier_ref: VERIFIER });

  const p = snapshot(iso(Date.parse(ORIGIN) + 2 * DAY));
  p.history = copy(first);
  const next = run(p).state;
  const renamed = createJourneyOneClockStore({
    journal: watch.journal, actor: ACTOR, clock_scope: RELABELLED_SCOPE });
  watch.calls.length = 0;
  await refuses(renamed.record({ state: copy(next),
    expected_prior_history_digest: first.history_digest,
    idempotency_key: key(), verifier_ref: VERIFIER }), "clock_scope_label_changed");
  assert.deepEqual(watch.calls, ["runAppend", "readScopeBindings"],
    "the seal is met on the bindings already read, not by attempting the binding write");
  assert.equal((await store.read(clockKey)).revision_count, 1);
  assert.equal((await store.read(clockKey)).clock_scope_ref, SCOPE.scope_ref);
});

test("a journal that does not carry the label back cannot be false-refused by that seal", async () => {
  // The store's clause is guarded on `!== undefined` for exactly this: a journal
  // whose binding rows omit the label has said nothing about it, and reading
  // silence as disagreement would refuse legitimate appends. Both journals in
  // this repository do return it; a third implementation need not.
  const watch = watchedJournal();
  const silent = { ...watch.journal,
    async readScopeBindings(args) {
      const bindings = await watch.journal.readScopeBindings(args);
      const strip = row => { if (!row) return row; const { clock_scope_ref, ...rest } = row; return rest; };
      return { by_scope: strip(bindings.by_scope), by_clock: strip(bindings.by_clock) };
    } };
  const store = createJourneyOneClockStore({ journal: silent, actor: ACTOR, clock_scope: SCOPE });
  const first = run(snapshot()).state;
  await store.record({ state: copy(first), expected_prior_history_digest: null,
    idempotency_key: key(), verifier_ref: VERIFIER });
  const p = snapshot(iso(Date.parse(ORIGIN) + 2 * DAY));
  p.history = copy(first);
  const next = run(p).state;

  const renamed = createJourneyOneClockStore({ journal: silent, actor: ACTOR, clock_scope: RELABELLED_SCOPE });
  watch.calls.length = 0;
  // Still refused — but by the JOURNAL, which is the home that holds the rows.
  // The store's clause did not fire, which is what `!== undefined` buys.
  await refuses(renamed.record({ state: copy(next),
    expected_prior_history_digest: first.history_digest,
    idempotency_key: key(), verifier_ref: VERIFIER }), "clock_scope_label_changed");
  assert.ok(watch.calls.includes("bindScope"),
    "with no label in the readback the store must pass the seal through to the journal");
});

test("both homes state the label rule, and the candidate SQL hashes the six identity fields", () => {
  // The SQL half of the same fix, read as text. It has still never been executed.
  assert.ok(SQL.includes("'doctorcre:j1-clock-scope:v2'::text"),
    "the domain tag is versioned on the SQL side too, because the published key changed");
  // The preimage the SQL actually hashes, read out of the file: the six identity
  // fields by name, and the label nowhere in it.
  const preimage = /v_fields := jsonb_build_object\(([\s\S]*?)\);/.exec(SQL)[1];
  for (const field of JOURNEY_ONE_CLOCK_SCOPE_IDENTITY_FIELDS) {
    assert.ok(preimage.includes(`'${field}'`), `the SQL preimage drops ${field}`);
  }
  assert.ok(!preimage.includes("scope_ref"), "scope_ref must not be part of the SQL preimage");
  // But it is still validated and still stored, because it is provenance.
  assert.ok(SQL.includes("'scope_ref', ''"), "a scope with no label is still refused");
  assert.ok(SQL.includes("p_clock_scope ->> 'scope_ref'"), "the label is still stored");
  assert.ok(SQL.includes("[j1_clock_scope_label_is_not_identity]"),
    "the SQL guard names the shared invariant on its refusal");
  // The rollback-only proof fixture carries the same three assertions.
  assert.ok(POSTGRES_PROOF.includes("relabelling an accepted scope produced a second scope key"));
  assert.ok(POSTGRES_PROOF.includes("a relabelled scope opened a second clock for one program"));
  assert.ok(POSTGRES_PROOF.includes("a bound scope was relabelled by a later write"));
  assert.ok(POSTGRES_PROOF.includes("two different accepted subjects collapsed onto one scope key"));
});

// ---------------------------------------------------------------------------
// 11. THE KERNEL'S VERIFIED BINDING, AND THE SCOPE THE STORE WRITES FOR.
//
// The kernel returns `verified_binding` beside its state: the three digests it
// forced the accepted benchmark and every receipt to match, the tenant, and the
// two gate ids the accepted deadline contract names. The recorder is the one
// seat holding both that and the store's authoritative scope, so it compares
// them — before any journal call at all.
// ---------------------------------------------------------------------------

function recorderOn(scope = SCOPE, journal = createEphemeralJourneyOneClockJournal()) {
  const h = harness();
  const store = createJourneyOneClockStore({ journal, actor: ACTOR, clock_scope: scope });
  return { h, journal, store,
    recorder: createJourneyOneClockRecorder({ clock: h.clock, store, verifier_ref: VERIFIER }) };
}

test("the recorder files a revision under the scope the kernel verified it against", async () => {
  const { h, store, recorder } = recorderOn();
  const p = snapshot();
  const expected = run(p).state;

  const result = await recorder.evaluateAndRecord({
    envelope: h.envelopeFor(p), expected_prior_history_digest: null, idempotency_key: key() });
  assert.equal(result.clock_scope_key, store.clock_scope.clock_scope_key);
  assert.equal(result.clock_scope_matches_verified_binding, true);
  // THE STORED HISTORY IS EXACTLY THE KERNEL'S, digest for digest and field for
  // field. Nothing about the binding entered the hashed state.
  assert.equal(result.history_digest, expected.history_digest);
  const readback = await store.read(result.clock_key);
  assert.deepEqual(readback.history, copy(expected));
  assert.equal(readback.history_digest, expected.history_digest);
  assert.equal(Object.keys(readback.history).length, JOURNEY_ONE_CLOCK_STATE_FIELDS.length);
  assert.equal(journeyOneClockHistoryDigest(readback.history), expected.history_digest);
  assert.equal(readback.clock_scope_key, journeyOneClockScopeKey(SCOPE));
  assert.equal(readback.clock_scope_ref, SCOPE.scope_ref);
});

test("a store whose scope is not the verified one refuses before any journal call", async () => {
  for (const [field, mutation] of [
    ["benchmark_subject_digest", D(21)],
    ["benchmark_candidate_digest", D(22)],
    ["benchmark_policy_digest", D(23)],
  ]) {
    const watch = watchedJournal();
    const { h, recorder } = recorderOn({ ...SCOPE, [field]: mutation }, watch.journal);
    await assert.rejects(recorder.evaluateAndRecord({
      envelope: h.envelopeFor(snapshot()), expected_prior_history_digest: null,
      idempotency_key: key() }), error => {
      assert.equal(error.code, "clock_scope_not_the_verified_binding");
      assert.equal(error.detail.invariant, "j1_clock_scope_binds_one_clock");
      assert.deepEqual(error.detail.differing_fields, [field]);
      assert.notEqual(error.detail.store_clock_scope_key, error.detail.verified_clock_scope_key);
      return true;
    });
    // NOT A READ, NOT A LOCK, NOT A ROW. The kernel ran; the record layer was
    // never asked anything.
    assert.deepEqual(watch.calls, [],
      "the refusal must happen before the store touches the journal at all");
    assert.equal(await watch.inner.readClock(journeyOneClockKeyForState(run(snapshot()).state)), null);
  }
});

test("a missing or malformed verified binding refuses, with no bypass", async () => {
  const real = copy(run(snapshot()));
  const good = copy(real.verified_binding);
  const without = copy(real); delete without.verified_binding;
  const withBinding = value => ({ ...copy(without), verified_binding: value });

  for (const [label, result, code] of [
    ["absent", copy(without), "clock_verified_binding_unavailable"],
    ["null", withBinding(null), "clock_verified_binding_unavailable"],
    ["a string", withBinding("sha256:" + "aa".repeat(32)), "clock_verified_binding_malformed"],
    ["an array", withBinding([good]), "clock_verified_binding_malformed"],
    ["carrying an extra field", withBinding({ ...good, verified: true }), "clock_verified_binding_malformed"],
    ["missing a field", withBinding({ ...good, policy_digest: undefined }), "clock_verified_binding_malformed"],
    ["another schema", withBinding({ ...good, schema_version: "doctorcre-v5-something-else.v1" }),
      "clock_verified_binding_malformed"],
    ["an unhashed digest", withBinding({ ...good, subject_digest: "not-a-digest" }),
      "clock_verified_binding_malformed"],
    // v2's projection digest is not read on this path — this rail holds no
    // projection to compare it against — but a binding it cannot read whole is
    // not one to derive a scope from either.
    ["an unhashed projection digest",
      withBinding({ ...good, authenticated_projection_digest: "not-a-digest" }),
      "clock_verified_binding_malformed"],
    ["another gate", withBinding({ ...good, clock_terminus_gate_id: "some-other-gate-accepted" }),
      "wrong_clock_scope_gate"],
    ["another tenant", withBinding({ ...good, tenant: "someone-elses-tenant" }), "wrong_tenant"],
  ]) {
    // `missing a field` is spelled with an explicit undefined, so the key is
    // present and the closed-shape check is doing the work a deletion would.
    if (label === "missing a field") delete result.verified_binding.policy_digest;
    const watch = watchedJournal();
    const store = createJourneyOneClockStore({
      journal: watch.journal, actor: ACTOR, clock_scope: SCOPE });
    const recorder = createJourneyOneClockRecorder({
      clock: { evaluate: () => result }, store, verifier_ref: VERIFIER });
    await refuses(recorder.evaluateAndRecord({
      envelope: {}, expected_prior_history_digest: null, idempotency_key: key() }), code);
    assert.deepEqual(watch.calls, [], `${label}: nothing may be written for it`);
  }
  assert.equal(good.schema_version, JOURNEY_ONE_CLOCK_VERIFIED_BINDING);
});

test("a recorder cannot be built over a store with no authoritative scope", async () => {
  const journal = createEphemeralJourneyOneClockJournal();
  const unscoped = createJourneyOneClockStore({ journal, actor: ACTOR });
  assert.throws(() => createJourneyOneClockRecorder({
    clock: harness().clock, store: unscoped, verifier_ref: VERIFIER }),
    e => e.code === "clock_scope_binding_required" &&
      e.detail.invariant === "j1_clock_scope_binds_one_clock");
  assert.equal(await journal.readClock(journeyOneClockKeyForState(run(snapshot()).state)), null);
});

test("the binding is read once off the kernel result and never off the caller", async () => {
  // THE RESULT IS FROZEN, so a caller holding one cannot edit the binding it is
  // about to be recorded under.
  const real = run(snapshot());
  assert.throws(() => { real.verified_binding.subject_digest = D(21); }, TypeError);
  assert.equal(real.verified_binding.subject_digest, D(1));

  // A BINDING THAT ANSWERS TWICE cannot pass validation as one value and be
  // stored under another: each field is read exactly once into a snapshot.
  let reads = 0;
  const honest = copy(real.verified_binding);
  const trick = { ...honest };
  Object.defineProperty(trick, "subject_digest", { enumerable: true, configurable: true,
    get() { reads += 1; return reads === 1 ? D(21) : honest.subject_digest; } });
  const watch = watchedJournal();
  const store = createJourneyOneClockStore({ journal: watch.journal, actor: ACTOR, clock_scope: SCOPE });
  const twoFaced = createJourneyOneClockRecorder({
    clock: { evaluate: () => ({ ...copy(real), verified_binding: trick }) }, store, verifier_ref: VERIFIER });
  await refuses(twoFaced.evaluateAndRecord({ envelope: {},
    expected_prior_history_digest: null, idempotency_key: key() }), "clock_scope_not_the_verified_binding");
  assert.equal(reads, 1, "a second read is a second answer, so there is only ever one");
  assert.deepEqual(watch.calls, []);

  // AND THE CALL ARGUMENTS ARE NOT A CHANNEL FOR IT. A caller who supplies the
  // scope's own binding beside a mismatched kernel is still refused: the value
  // comes from the kernel result and from nowhere else.
  const { h, recorder } = recorderOn({ ...SCOPE, benchmark_policy_digest: D(23) });
  await refuses(recorder.evaluateAndRecord({ envelope: h.envelopeFor(snapshot()),
    expected_prior_history_digest: null, idempotency_key: key(),
    verified_binding: { ...honest, policy_digest: D(23) },
    clock_scope: { ...SCOPE, benchmark_policy_digest: D(23) } }),
    "clock_scope_not_the_verified_binding");
});

test("the existing compare-and-swap and idempotency are unchanged through the recorder", async () => {
  const { h, store, recorder } = recorderOn();
  const p = snapshot();
  const shared = key();
  const first = await recorder.evaluateAndRecord({
    envelope: h.envelopeFor(p), expected_prior_history_digest: null, idempotency_key: shared });
  assert.equal(first.revision_ordinal, 0);

  // EXACT REPLAY, not a second write.
  const replay = await recorder.evaluateAndRecord({
    envelope: h.envelopeFor(p), expected_prior_history_digest: null, idempotency_key: shared });
  assert.equal(replay.replayed, true);
  assert.equal(replay.history_digest, first.history_digest);
  assert.equal((await store.read(first.clock_key)).revision_count, 1);

  // A SECOND CREATION for a clock that already has history.
  await refuses(recorder.evaluateAndRecord({ envelope: h.envelopeFor(p),
    expected_prior_history_digest: null, idempotency_key: key() }), "clock_already_exists");

  // A STALE PRIOR, and then the exact one.
  const stored = (await store.read(first.clock_key)).history;
  assert.equal(stored.history_digest, first.history_digest);
  const advanced = snapshot(iso(Date.parse(ORIGIN) + 2 * DAY));
  advanced.history = copy(stored);
  await refuses(recorder.evaluateAndRecord({ envelope: h.envelopeFor(advanced),
    expected_prior_history_digest: D(44), idempotency_key: key() }),
    "clock_stale_prior_history_digest");
  assert.equal((await store.read(first.clock_key)).revision_count, 1);

  const appended = await recorder.evaluateAndRecord({ envelope: h.envelopeFor(advanced),
    expected_prior_history_digest: first.history_digest, idempotency_key: key() });
  assert.equal(appended.revision_ordinal, 1);
  assert.equal(appended.clock_scope_key, store.clock_scope.clock_scope_key);
  // The appended history is exactly what the kernel computed for it.
  const expected = run(advanced).state;
  const readback = await store.read(first.clock_key);
  assert.equal(readback.history_digest, expected.history_digest);
  assert.deepEqual(readback.history, copy(expected));
  assert.equal(readback.revision_count, 2);
});

// ---------------------------------------------------------------------------
// 12. THE OPTIONAL PRE-WRITE ASSERTION, WHICH CAN ONLY REFUSE.
//
// journey-one-clock-runtime.v5.js holds a fact this rail structurally cannot
// see — the admitted-minimum inventory a computation was composed from — and
// needs to refuse on it BEFORE a row exists. Rather than a second
// evaluate-check-record sequence living there, the sequence stays here and the
// extra refusal is injected. These tests are about the seam's limits, not about
// what the runtime asserts through it.
// ---------------------------------------------------------------------------

test("a pre-write assertion refuses before any journal call, and its error travels unchanged", async () => {
  const watch = watchedJournal();
  const h = harness();
  const store = createJourneyOneClockStore({
    journal: watch.journal, actor: ACTOR, clock_scope: SCOPE });
  const seen = [];
  const recorder = createJourneyOneClockRecorder({
    clock: h.clock, store, verifier_ref: VERIFIER,
    assert_before_write: result => {
      seen.push(result);
      const error = new Error("the computation was not composed from this inventory");
      error.name = "JourneyOneClockRuntimeError";
      error.code = "clock_computation_origin_not_in_composed_inventory";
      throw error;
    },
  });
  await assert.rejects(recorder.evaluateAndRecord({ envelope: h.envelopeFor(snapshot()),
    expected_prior_history_digest: null, idempotency_key: key() }), error => {
    // The seat that holds the inventory owns the vocabulary for its own fact.
    assert.equal(error.code, "clock_computation_origin_not_in_composed_inventory");
    return true;
  });
  assert.equal(seen.length, 1, "the assertion is called once, with the kernel result");
  assert.deepEqual(watch.calls, [],
    "no read, no compare-and-swap and no row for a refused computation");
  assert.equal(await watch.inner.readClock(journeyOneClockKeyForState(run(snapshot()).state)), null);
});

test("the pre-write assertion runs AFTER the scope check, and is never reached without it", async () => {
  const watch = watchedJournal();
  const h = harness();
  const store = createJourneyOneClockStore({
    journal: watch.journal, actor: ACTOR, clock_scope: { ...SCOPE, benchmark_policy_digest: D(23) } });
  let called = false;
  const recorder = createJourneyOneClockRecorder({
    clock: h.clock, store, verifier_ref: VERIFIER,
    assert_before_write: () => { called = true; },
  });
  await refuses(recorder.evaluateAndRecord({ envelope: h.envelopeFor(snapshot()),
    expected_prior_history_digest: null, idempotency_key: key() }),
    "clock_scope_not_the_verified_binding");
  assert.equal(called, false,
    "a computation judged under another scope never reaches the extra assertion");
  assert.deepEqual(watch.calls, []);
});

test("the pre-write assertion is handed the kernel's frozen result and can admit nothing", async () => {
  const { h, store } = recorderOn();
  let handed = null;
  const recorder = createJourneyOneClockRecorder({
    clock: h.clock, store, verifier_ref: VERIFIER,
    // A hook that returns a truthy value, mutates nothing it can mutate, and
    // tries to: none of it changes the outcome, because the return value is
    // never read and the result is frozen.
    assert_before_write: result => {
      handed = result;
      assert.throws(() => { result.state.status = "completed_on_time"; }, TypeError);
      assert.throws(() => { result.verified_binding.subject_digest = D(21); }, TypeError);
      return { admitted: true, deadline_success: true };
    },
  });
  const p = snapshot();
  const recorded = await recorder.evaluateAndRecord({
    envelope: h.envelopeFor(p), expected_prior_history_digest: null, idempotency_key: key() });
  assert.equal(handed.state.history_digest, run(p).state.history_digest);
  assert.equal(recorded.kernel_verdict.status, "running");
  assert.equal(recorded.kernel_verdict.deadline_success, false);
  assert.equal((await store.read(recorded.clock_key)).history.status, "running");
});

test("a recorder without a pre-write assertion is unchanged, and a non-function one refuses", async () => {
  const journal = createEphemeralJourneyOneClockJournal();
  const store = createJourneyOneClockStore({ journal, actor: ACTOR, clock_scope: SCOPE });
  const h = harness();
  for (const bad of [true, "assert", {}, 0]) {
    assert.throws(() => createJourneyOneClockRecorder({
      clock: h.clock, store, verifier_ref: VERIFIER, assert_before_write: bad }),
    error => {
      assert.equal(error.code, "invalid_shape");
      assert.equal(error.detail.path, "assert_before_write");
      return true;
    });
  }
  // Omitted and explicitly null are the same recorder, and both still write.
  for (const value of [undefined, null]) {
    const own = harness();
    const scoped = createJourneyOneClockStore({
      journal: createEphemeralJourneyOneClockJournal(), actor: ACTOR, clock_scope: SCOPE });
    const recorder = createJourneyOneClockRecorder({
      clock: own.clock, store: scoped, verifier_ref: VERIFIER, assert_before_write: value });
    const recorded = await recorder.evaluateAndRecord({
      envelope: own.envelopeFor(snapshot()), expected_prior_history_digest: null,
      idempotency_key: key() });
    assert.equal(recorded.revision_ordinal, 0);
  }
});

// ---------------------------------------------------------------------------
// 13. WHAT ONE IDEMPOTENCY KEY ALREADY WROTE, read without opening an append.
// ---------------------------------------------------------------------------

test("a key that wrote nothing reads as absent, and one that wrote reads back its own prior", async () => {
  const { store } = newStore();
  const unused = key();
  const nothing = await store.readRecordedRevisionForKey(unused);
  assert.equal(nothing.exists, false);
  assert.equal(nothing.idempotency_key, unused);
  assert.equal(nothing.effects.database_writes, 0, "asking is not writing");

  // A CREATION: the prior is an explicit null, and the readback says so.
  const first = run(snapshot());
  const createdKey = key();
  const created = await store.record({ state: copy(first.state),
    expected_prior_history_digest: null, idempotency_key: createdKey, verifier_ref: VERIFIER });
  const readCreated = await store.readRecordedRevisionForKey(createdKey);
  assert.equal(readCreated.exists, true);
  assert.equal(readCreated.clock_key, created.clock_key);
  assert.equal(readCreated.history_digest, first.state.history_digest);
  assert.equal(readCreated.expected_prior_history_digest, null);
  assert.equal(readCreated.prior_history, null);
  assert.equal(readCreated.prior_revision_ordinal, null, "a creation has no prior");
  assert.equal(readCreated.revision_ordinal, 0);
  assert.equal(readCreated.written_by_actor_id, ACTOR.slug);
  assert.equal(readCreated.clock_scope_key, journeyOneClockScopeKey(SCOPE));
  assert.equal(readCreated.clock_scope_bound, true);
  // The revision is REBUILT and re-hashed, not copied out of a row.
  assert.deepEqual(readCreated.history, copy(first.state));
  assert.equal(journeyOneClockHistoryDigest(readCreated.history), first.state.history_digest);

  // A LATER REVISION: the prior is the exact history it was written against, and
  // it is rebuilt from its own rows rather than pointed at.
  const advanced = snapshot(iso(Date.parse(ORIGIN) + 2 * DAY));
  advanced.history = copy((await store.read(created.clock_key)).history);
  const secondState = run(advanced).state;
  const appendKey = key();
  await store.record({ state: copy(secondState), idempotency_key: appendKey,
    expected_prior_history_digest: first.state.history_digest, verifier_ref: VERIFIER });
  const readAppended = await store.readRecordedRevisionForKey(appendKey);
  assert.equal(readAppended.revision_ordinal, 1);
  assert.equal(readAppended.history_digest, secondState.history_digest);
  assert.equal(readAppended.expected_prior_history_digest, first.state.history_digest);
  // THE PRIOR IS THE REVISION BEFORE IT, taken by ordinal and then checked
  // against the digest — a revision filed at N was refused unless its prior was
  // the head, and the head of a clock holding N revisions is N-1.
  assert.equal(readAppended.prior_revision_ordinal, 0);
  assert.deepEqual(readAppended.prior_history, copy(first.state));
  assert.equal(journeyOneClockHistoryDigest(readAppended.prior_history),
    readAppended.expected_prior_history_digest);
  // TWO REVISIONS, AND ASKING ABOUT THEM ADDED NEITHER.
  assert.equal((await store.read(created.clock_key)).revision_count, 2);
  assert.equal(readAppended.effects.database_writes, 0);
});

test("the request read refuses a journal that cannot answer, rather than reporting absence", async () => {
  const { journal } = newStore();
  const blind = { durable: false, kind: "no-idempotency-read",
    runAppend: (...a) => journal.runAppend(...a), readClock: (...a) => journal.readClock(...a),
    readRevisions: (...a) => journal.readRevisions(...a),
    readScopeBindings: (...a) => journal.readScopeBindings(...a),
    bindScope: (...a) => journal.bindScope(...a) };
  const store = createJourneyOneClockStore({ journal: blind, actor: ACTOR, clock_scope: SCOPE });
  // "This journal cannot say" and "this key wrote nothing" are different
  // findings, and a caller acting on the second would append a second revision.
  await refuses(store.readRecordedRevisionForKey(key()), "clock_idempotency_read_unavailable");
  await refuses(store.readRecordedRevisionForKey("not-a-uuid"), "invalid_uuid");
});

test("a recorded request whose prior is gone refuses instead of pointing at the head", async () => {
  const { journal, store } = newStore();
  const first = run(snapshot());
  const createdKey = key(), appendKey = key();
  const created = await store.record({ state: copy(first.state),
    expected_prior_history_digest: null, idempotency_key: createdKey, verifier_ref: VERIFIER });
  const advanced = snapshot(iso(Date.parse(ORIGIN) + 2 * DAY));
  advanced.history = copy((await store.read(created.clock_key)).history);
  await store.record({ state: copy(run(advanced).state), idempotency_key: appendKey,
    expected_prior_history_digest: first.state.history_digest, verifier_ref: VERIFIER });

  // A journal that can no longer produce the revision the CAS token names. The
  // current head is not a substitute: re-computing against it would be the
  // rebase this rail exists to refuse.
  const lossy = createJourneyOneClockStore({ actor: ACTOR, clock_scope: SCOPE, journal: {
    durable: false, kind: "lossy",
    runAppend: (...a) => journal.runAppend(...a), readClock: (...a) => journal.readClock(...a),
    readScopeBindings: (...a) => journal.readScopeBindings(...a),
    bindScope: (...a) => journal.bindScope(...a),
    readRevisionByIdempotencyKey: (...a) => journal.readRevisionByIdempotencyKey(...a),
    async readRevisions(clockKey) {
      return (await journal.readRevisions(clockKey)).filter(r => r.revision_ordinal !== 0);
    },
  } });
  await refuses(lossy.readRecordedRevisionForKey(appendKey),
    "clock_replay_prior_history_unavailable");

  // AND A ROW AT THAT ORDINAL CARRYING ANOTHER DIGEST IS THE SAME FINDING. The
  // prior is taken by ordinal because the compare-and-swap decides it, and the
  // digest is then checked against that row rather than searched for — a search
  // would happily settle on some other revision that shares the digest.
  const swapped = createJourneyOneClockStore({ actor: ACTOR, clock_scope: SCOPE, journal: {
    durable: false, kind: "swapped-prior",
    runAppend: (...a) => journal.runAppend(...a), readClock: (...a) => journal.readClock(...a),
    readScopeBindings: (...a) => journal.readScopeBindings(...a),
    bindScope: (...a) => journal.bindScope(...a),
    readRevisionByIdempotencyKey: (...a) => journal.readRevisionByIdempotencyKey(...a),
    async readRevisions(clockKey) {
      const rows = copy(await journal.readRevisions(clockKey));
      rows[0].history_digest = D(44);
      return rows;
    },
  } });
  await assert.rejects(swapped.readRecordedRevisionForKey(appendKey), error => {
    assert.equal(error.code, "clock_replay_prior_history_unavailable");
    assert.equal(error.detail.expected_prior_revision_ordinal, 0);
    assert.equal(error.detail.found_prior_history_digest, D(44));
    return true;
  });
});

test("the request read is bound to this tenant and to intact rows", async () => {
  const { journal, store } = newStore();
  const first = run(snapshot());
  const usedKey = key();
  await store.record({ state: copy(first.state), expected_prior_history_digest: null,
    idempotency_key: usedKey, verifier_ref: VERIFIER });

  const edited = createJourneyOneClockStore({ actor: ACTOR, clock_scope: SCOPE, journal: {
    durable: false, kind: "tampering-idempotency-read",
    runAppend: (...a) => journal.runAppend(...a), readClock: (...a) => journal.readClock(...a),
    readRevisions: (...a) => journal.readRevisions(...a),
    readScopeBindings: (...a) => journal.readScopeBindings(...a),
    bindScope: (...a) => journal.bindScope(...a),
    async readRevisionByIdempotencyKey(k) {
      const row = copy(await journal.readRevisionByIdempotencyKey(k));
      row.scalars.status = "completed_on_time";
      return row;
    },
  } });
  await refuses(edited.readRecordedRevisionForKey(usedKey), "clock_readback_tampered");
});

test("evaluateAndBind is the write path's own order with the write left off", async () => {
  const watch = watchedJournal();
  const h = harness();
  const store = createJourneyOneClockStore({
    journal: watch.journal, actor: ACTOR, clock_scope: SCOPE });
  const seen = [];
  const recorder = createJourneyOneClockRecorder({ clock: h.clock, store, verifier_ref: VERIFIER,
    assert_before_write: result => { seen.push(result.state.history_digest); } });
  const p = snapshot();
  const bound = recorder.evaluateAndBind(h.envelopeFor(p));
  assert.equal(bound.result.state.history_digest, run(p).state.history_digest);
  assert.equal(bound.clock_scope_key, store.clock_scope.clock_scope_key);
  assert.deepEqual(seen, [run(p).state.history_digest], "the pre-write assertion still fires");
  assert.deepEqual(watch.calls, [], "and nothing is read or written");
  assert.equal(Object.isFrozen(bound), true);
  // The same scope check refuses here too — it is one implementation.
  const elsewhere = createJourneyOneClockStore({ journal: watch.journal, actor: ACTOR,
    clock_scope: { ...SCOPE, benchmark_policy_digest: D(23) } });
  assert.throws(() => createJourneyOneClockRecorder({ clock: h.clock, store: elsewhere,
    verifier_ref: VERIFIER }).evaluateAndBind(h.envelopeFor(snapshot())),
  e => e.code === "clock_scope_not_the_verified_binding");
  assert.deepEqual(watch.calls, []);
});

test("the descriptor names the pre-write seam as something that can only refuse", () => {
  const contract = journeyOneClockStoreIntegrationRequirements().trusted_integration_contract;
  assert.ok(contract.optional_pre_write_assertion.includes("can only add a refusal"));
  assert.ok(contract.optional_pre_write_assertion.includes("return value is ignored"));
  assert.ok(contract.composition_loop.includes("journey-one-clock-runtime.v5.js"));
  assert.ok(contract.composition_loop.includes("cannot run here"));

  const loop = journeyOneClockStoreIntegrationRequirements()
    .input_authority.record_layer_composition_loop;
  assert.equal(loop.resolved, true);
  assert.ok(loop.what_landed.includes("neither is an argument"));
  assert.ok(loop.still_not_resolved.includes("minimum_inventory_unavailable"));
  assert.ok(loop.explicitly_not_done_instead.some(l => l.includes("second evaluate-check-record")));
  // And the requirement AROUND it is untouched: no reader, no producers.
  assert.equal(journeyOneClockStoreIntegrationRequirements().input_authority.resolved, false);
});
