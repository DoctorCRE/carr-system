import test from "node:test";
import assert from "node:assert/strict";
import { canonicalJson, digest } from "../src/artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import { DEADLINE_GAP_SHIFTED, DEADLINE_OVERLAP_ORIGIN_OFFSET, DEADLINE_PLAIN,
  JOURNEY_ONE_CLOCK_PROJECTION, JOURNEY_ONE_CLOCK_SCHEMA, JOURNEY_ONE_DEADLINE_CONTRACT,
  JOURNEY_ONE_CLOCK_VERIFIED_BINDING, JOURNEY_ONE_CLOCK_VERIFIED_BINDING_FIELDS,
  createJourneyOneClock, chicagoThirtyDayDeadline,
  readJourneyOneClockHistory } from "../src/journey-one-clock.v5.js";

const D = n => `sha256:${String(n).padStart(2, "0").repeat(32)}`;
const I = actor => ({ actor_id: actor, session_ref: `session:synthetic-${actor}`, authority_class: actor === "joe" || actor === "dell" ? "verified_partner" : "synthetic_oracle" });
const copy = x => JSON.parse(JSON.stringify(x));
const iso = ms => new Date(ms).toISOString();
const HOUR = 3600000;
const ORIGIN = "2026-09-09T15:00:00.000Z";
/** The trusted projection's expected kernel scope: artifact D(7), fixtures D(5). */
const EXPECTATION = { artifact_digest: D(7), fixture_set_digest: D(5) };
const MINIMUM_TTL_POLICY = 48 * HOUR;
const COMPLETION_TTL_POLICY = 72 * HOUR;
// r7's consumer-gate-receipt.v1: exactly twenty-one fields, no schema_version.
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
    ttl_expires_at: iso(Date.parse(origin) + 24 * HOUR), status: "pass", comparator: "synthetic-exact-comparator",
    negative_admission_result: "all_required_denials_observed",
  };
}
// r7's rollout-component-receipt.v1: exactly twenty-two fields, no schema_version.
function completed(at) {
  const m = minimum(at);
  delete m.gate_id; delete m.receipt_producer_step_ref; delete m.environment_manifest_digest;
  return { gate_id: "journey-one-kernel-production-accepted", combiner: "all_current_exact_distinct_pass",
    obligation_decision_ids: ["Q002.D1", "Q014.D1", "Q123.D1"], receipts: [{ ...m,
      receipt_ref: "safe:receipt:journey-one-kernel-production",
      producer_step_ref: "step:j1-kernel-production-outcome", rollout_environment_manifest_digest: D(6), artifact_digest: D(7),
      subject_environment: "production", evidence_scope: "production",
      producer_role: "independent_journey_one_kernel_outcome_oracle",
      independent_oracle_ref: "oracle:rollout-component:journey-one-kernel-production",
    }] };
}
function snapshot(as_of = ORIGIN, origin = ORIGIN) {
  return { schema_version: JOURNEY_ONE_CLOCK_PROJECTION, tenant: ORGANIZATION_TENANT_ID, as_of,
    binding: { subject_digest: D(1), candidate_digest: D(2), policy_digest: D(3), minimum_environment_manifest_digest: D(4),
      production_environment_manifest_digest: D(6),
      maximum_minimum_receipt_ttl_ms: MINIMUM_TTL_POLICY, maximum_completion_receipt_ttl_ms: COMPLETION_TTL_POLICY },
    benchmark: { manifest_digest: D(8), subject_digest: D(1), candidate_digest: D(2), policy_digest: D(3),
      deadline_contract: copy(JOURNEY_ONE_DEADLINE_CONTRACT), accepted_at: iso(Date.parse(origin) - HOUR), accepted_by_identity: I("joe") },
    minimum_history: [{ admitted_at: origin, receipt: minimum(origin) }],
    completion: null, completion_expectation: copy(EXPECTATION), pauses: [], amendments: [], history: null,
  };
}
// Test-only authenticating capability. The WeakMap is installed by trusted test
// code; no JSON flag can cause an unknown envelope to acquire a projection.
function harness() {
  const evidence = new Map(); let serial = 0;
  const clock = createJourneyOneClock({ verifySnapshot(envelope) {
    const found = evidence.get(digest(envelope));
    if (!found) throw new Error("synthetic-authentication-refused");
    return { envelope_digest: digest(envelope), snapshot: copy(found) };
  } });
  return { clock, evaluate(p) { const e = { synthetic_receipt_ref: `test-${++serial}` }; evidence.set(digest(e), copy(p)); return clock.evaluate(e); } };
}
function run(p) { return harness().evaluate(p); }
function refuse(p, code) { assert.throws(() => run(p), e => e.code === code); }
function pause(p, start, end, id = "one", partner = "joe", approved = iso(Date.parse(start) - 1)) {
  const payload = { pause_id: `safe:synthetic:pause-${id}`, clock_origin_digest: digest(p.minimum_history[0].receipt),
    blocker_ref: "safe:external-blocker:synthetic-provider", starts_at: start,
    approved_at: approved, approved_by_identity: I(partner) };
  return { ...payload, ends_at: end, approval_digest: digest(["doctorcre:j1-clock-pause:v1", payload]) };
}
function resignPause(pause) { const { approval_digest, ends_at, ...body } = pause; pause.approval_digest = digest(["doctorcre:j1-clock-pause:v1", body]); }
function amend(originDigest, at, { name = "amendment", manifest = null, partner = "dell" } = {}) {
  const body = { amendment_ref: `safe:synthetic:${name}`, clock_origin_digest: originDigest,
    benchmark_manifest_digest: manifest, description_digest: D(42), accepted_at: at, accepted_by_identity: I(partner) };
  return { ...body, amendment_digest: digest(["doctorcre:j1-clock-amendment:v1", body]) };
}
function reseal(history) { const { history_digest, ...body } = history; history.history_digest = digest(body); return history; }

// --- the deadline itself: 30 Chicago dates, not 720 hours ------------------

for (const [origin, due, hours] of [
  // An ordinary spring crossing is 719 elapsed hours and an ordinary autumn one
  // is 721. Thirty calendar dates is never converted into a fixed 720.
  ["2026-02-20T16:15:00.000Z", "2026-03-22T15:15:00.000Z", 719],
  ["2026-10-10T15:15:00.000Z", "2026-11-09T16:15:00.000Z", 721],
  [ORIGIN, "2026-10-09T15:00:00.000Z", 720],
]) test(`thirty Chicago dates from ${origin} is ${hours} elapsed hours`, () => {
  const result = chicagoThirtyDayDeadline(origin);
  assert.equal(result.due_at, due);
  assert.equal(result.reason_id, DEADLINE_PLAIN);
  assert.equal((Date.parse(due) - Date.parse(origin)) / HOUR, hours);
});

test("a spring-gap target shifts forward by the gap length rather than snapping to the transition", () => {
  // 02:30 Chicago on 6 February 2026 is CST. Thirty dates later is 8 March, the
  // morning the wall clock jumps 02:00 to 03:00, so 02:30 never happens. The
  // decided convention shifts the WALL TIME forward by exactly the gap: 03:30
  // CDT, not the 03:00 transition instant.
  const result = chicagoThirtyDayDeadline("2026-02-06T08:30:00.000Z");
  assert.equal(result.status, "resolved");
  assert.equal(result.due_at, "2026-03-08T08:30:00.000Z");
  assert.equal(result.reason_id, DEADLINE_GAP_SHIFTED);
  // 03:30 CDT is -05:00, so the shifted wall time is one hour past the target
  // and NOT the 08:00Z transition instant that a snap would have produced.
  assert.notEqual(result.due_at, "2026-03-08T08:00:00.000Z");
});

test("an autumn-fold target takes the occurrence with the origin's own UTC offset", () => {
  // 01:30 Chicago on 2 October 2026 is CDT. Thirty dates later is 1 November,
  // when 01:30 happens twice. The origin ran on daylight time, so the deadline
  // is the first, daylight occurrence: 01:30 CDT, 06:30Z.
  const result = chicagoThirtyDayDeadline("2026-10-02T06:30:00.000Z");
  assert.equal(result.status, "resolved");
  assert.equal(result.due_at, "2026-11-01T06:30:00.000Z");
  assert.equal(result.reason_id, DEADLINE_OVERLAP_ORIGIN_OFFSET);
  // The standard-time occurrence an hour later is the one NOT taken.
  assert.notEqual(result.due_at, "2026-11-01T07:30:00.000Z");
});

test("both DST edges produce a real running deadline and record which rule resolved it", () => {
  const gap = run(snapshot("2026-03-01T00:00:00.000Z", "2026-02-06T08:30:00.000Z"));
  assert.equal(gap.state.status, "running");
  assert.equal(gap.state.due_at, "2026-03-08T08:30:00.000Z");
  assert.equal(gap.state.base_deadline_at, "2026-03-08T08:30:00.000Z");
  assert.equal(gap.state.base_deadline_resolution, DEADLINE_GAP_SHIFTED);
  assert.equal(gap.deadline_resolution, DEADLINE_GAP_SHIFTED);
  assert.equal(gap.unresolved_reason, null);

  const fold = run(snapshot("2026-10-15T00:00:00.000Z", "2026-10-02T06:30:00.000Z"));
  assert.equal(fold.state.status, "running");
  assert.equal(fold.state.due_at, "2026-11-01T06:30:00.000Z");
  assert.equal(fold.state.base_deadline_resolution, DEADLINE_OVERLAP_ORIGIN_OFFSET);

  const plain = run(snapshot());
  assert.equal(plain.state.base_deadline_resolution, DEADLINE_PLAIN);
  // The three cases stay distinguishable in the sealed state, not just in the
  // returned result, so a later reader can tell how a deadline was reached.
  assert.equal(new Set([gap.state.base_deadline_resolution, fold.state.base_deadline_resolution,
    plain.state.base_deadline_resolution]).size, 3);
});

test("a DST-edge deadline is missed and completed on exactly the same terms as any other", () => {
  const late = snapshot("2026-03-08T08:30:00.001Z", "2026-02-06T08:30:00.000Z");
  const missed = run(late);
  assert.equal(missed.state.status, "missed");
  assert.equal(missed.state.miss_at, "2026-03-08T08:30:00.000Z");
  assert.equal(missed.replan_required, true);
  // And a completion observed AT the shifted deadline is on time.
  const done = snapshot("2026-03-08T08:30:00.000Z", "2026-02-06T08:30:00.000Z");
  done.completion = completed(done.as_of);
  const result = run(done);
  assert.equal(result.state.status, "completed_on_time");
  assert.equal(result.deadline_success, true);
  assert.equal(result.replan_required, false);
});

// --- origin selection -------------------------------------------------------

test("first historical admissible minimum fixes origin after its TTL has elapsed", () => {
  const p = snapshot("2026-09-15T15:00:00.000Z");
  p.minimum_history.unshift({ admitted_at: "2026-09-14T15:00:00.000Z", receipt: minimum("2026-09-14T15:00:00.000Z") });
  const r = run(p); assert.equal(r.state.origin_at, ORIGIN); assert.equal(r.state.due_at, "2026-10-09T15:00:00.000Z");
});

test("a nonpassing or lapsed earlier attempt is skipped rather than deadlocking the clock", () => {
  const p = snapshot("2026-09-15T15:00:00.000Z");
  const failed = "2026-09-07T15:00:00.000Z";
  p.minimum_history.unshift({ admitted_at: failed, receipt: { ...minimum(failed), status: "fail" } });
  const lapsed = "2026-09-08T15:00:00.000Z";
  p.minimum_history.unshift({ admitted_at: "2026-09-09T16:00:00.000Z", receipt: minimum(lapsed) });
  const r = run(p);
  assert.equal(r.state.origin_at, ORIGIN);
  assert.equal(r.state.origin_receipt_digest, digest(minimum()));
  const q = snapshot();
  q.minimum_history = [{ admitted_at: ORIGIN, receipt: { ...minimum(), status: "fail" } }];
  refuse(q, "origin_unavailable");
});

test("a structurally wrong minimum refuses the projection even beside an admissible one", () => {
  for (const [key, value, code] of [
    ["gate_id", "journey-one-production-accepted", "wrong_origin_gate"],
    ["candidate_digest", D(99), "receipt_binding_mismatch"],
    ["environment_manifest_digest", D(99), "receipt_environment_mismatch"],
    ["receipt_producer_step_ref", "step:foundation-assurance-receipt", "wrong_producer_or_schema"],
  ]) {
    const p = snapshot();
    p.minimum_history.push({ admitted_at: ORIGIN, receipt: { ...minimum(), [key]: value } });
    refuse(p, code);
  }
});

test("two minimums sharing an observed_at pick a digest-ordered origin, not an array-ordered one", () => {
  // Both were admitted at the same instant as well, so the admission-ordered
  // selector reaches its tie-break and answers on the receipt digest alone.
  const one = minimum(), two = { ...minimum(), evidence_ref: "safe:synthetic:min-evidence-two" };
  const expected = [digest(one), digest(two)].sort()[0];
  const forward = snapshot(), reverse = snapshot();
  forward.minimum_history = [{ admitted_at: ORIGIN, receipt: one }, { admitted_at: ORIGIN, receipt: two }];
  reverse.minimum_history = [{ admitted_at: ORIGIN, receipt: two }, { admitted_at: ORIGIN, receipt: one }];
  assert.equal(run(forward).state.origin_receipt_digest, expected);
  assert.equal(run(reverse).state.origin_receipt_digest, expected);
});

test("a later admission of an earlier observation appends instead of rebasing the origin", () => {
  // The origin receipt passed at 15:00 and was admitted at that same instant.
  const p = snapshot("2026-09-09T16:00:00.000Z");
  const before = run(p);
  assert.equal(before.state.origin_at, ORIGIN);
  // This one was observed an hour EARLIER but only reached the authoritative
  // inventory the next morning, still inside its own TTL. It is fully
  // admissible and an authoritative inventory cannot shed it, so selecting the
  // origin by observed_at would rebase — and then permanently brick — a clock
  // that was already running. Selection is by admission; the selected receipt's
  // observed_at is still the fixed origin.
  const earlier = { admitted_at: "2026-09-10T10:00:00.000Z", receipt: minimum("2026-09-09T14:00:00.000Z") };
  p.history = before.state; p.as_of = "2026-09-10T12:00:00.000Z";
  p.minimum_history.push(copy(earlier));
  const after = run(p);
  assert.equal(after.state.origin_at, ORIGIN);
  assert.equal(after.state.origin_receipt_digest, digest(minimum()));
  assert.equal(after.state.base_deadline_at, "2026-10-09T15:00:00.000Z");
  assert.equal(after.state.due_at, "2026-10-09T15:00:00.000Z");
  assert.deepEqual(after.state.events, before.state.events);
  // Read fresh, the same two admissions choose the same origin.
  const fresh = snapshot("2026-09-10T12:00:00.000Z");
  fresh.minimum_history.push(copy(earlier));
  const clean = run(fresh);
  assert.equal(clean.state.origin_at, ORIGIN);
  assert.equal(clean.state.origin_receipt_digest, digest(minimum()));
});

test("missing minimum and duplicates refuse", () => {
  const p = snapshot(); p.minimum_history = []; refuse(p, "origin_unavailable");
  p.minimum_history = [{ admitted_at: ORIGIN, receipt: minimum() }, { admitted_at: ORIGIN, receipt: minimum() }]; refuse(p, "duplicate_minimum");
});

test("an overlong receipt window refuses loudly instead of skipping the first passing origin", () => {
  // A genuine first pass issued with a seventy-two-hour window under a
  // forty-eight-hour policy. Treating that as a non-pass would silently hand the
  // origin to the LATER receipt and start the clock on the wrong day, and the
  // same history re-read under a wider policy would then name a different
  // origin and become unreadable. It is a misissued receipt or a misbound
  // policy, and it is fatal by name.
  const p = snapshot("2026-09-15T15:00:00.000Z");
  const early = "2026-09-08T15:00:00.000Z";
  p.minimum_history.unshift({ admitted_at: early,
    receipt: { ...minimum(early), ttl_expires_at: iso(Date.parse(early) + 72 * HOUR) } });
  refuse(p, "receipt_ttl_policy_exceeded");
  // Without the overlong receipt the later one is the origin, so the refusal
  // above is the overlong window and not the presence of a second admission.
  const clean = snapshot("2026-09-15T15:00:00.000Z");
  assert.equal(run(clean).state.origin_at, ORIGIN);
  // The two skips that ARE legitimate stay legitimate: a receipt that did not
  // pass, and one whose window had already lapsed when it was admitted.
  const skipped = snapshot("2026-09-15T15:00:00.000Z");
  skipped.minimum_history.unshift({ admitted_at: "2026-09-07T15:00:00.000Z",
    receipt: { ...minimum("2026-09-07T15:00:00.000Z"), status: "fail" } });
  skipped.minimum_history.unshift({ admitted_at: "2026-09-09T16:00:00.000Z", receipt: minimum("2026-09-08T15:00:00.000Z") });
  assert.equal(run(skipped).state.origin_at, ORIGIN);
});

test("the policy that selected an origin is recorded, and a changed one refuses by name", () => {
  const p = snapshot("2026-09-10T15:00:00.000Z");
  const before = run(p);
  assert.equal(before.state.origin_receipt_ttl_policy_ms, MINIMUM_TTL_POLICY);
  // Re-reading the same history under a different minimum-receipt policy would
  // admit or skip a different set of attempts and could name a different first
  // pass. That refuses on its own terms rather than surfacing as a rebase, and
  // there is no implicit reset.
  const widened = copy(p);
  widened.history = before.state;
  widened.binding.maximum_minimum_receipt_ttl_ms = 96 * HOUR;
  assert.throws(() => run(widened), e => e.code === "origin_ttl_policy_changed" &&
    e.detail.recorded === MINIMUM_TTL_POLICY && e.detail.supplied === 96 * HOUR);
  // The unchanged policy reads the same history without complaint, and the
  // COMPLETION policy is free to differ because it selects no origin.
  const same = copy(p); same.history = before.state;
  assert.equal(run(same).state.origin_receipt_ttl_policy_ms, MINIMUM_TTL_POLICY);
  const otherCompletion = copy(same); otherCompletion.binding.maximum_completion_receipt_ttl_ms = 96 * HOUR;
  assert.equal(run(otherCompletion).state.status, "running");
});

test("a history sealed under the previous state schema is refused, never rebased", () => {
  // v1 sealed its origin digest over a twenty-two-field minimum. This kernel
  // reads the r7-exact twenty-one, so it cannot recompute that digest — and
  // re-deriving one would rebase a sealed origin. The refusal names the
  // migration instead, and the migration belongs to the durable store.
  const p = snapshot("2026-09-10T15:00:00.000Z");
  const current = copy(run(p).state);
  assert.equal(current.schema_version, JOURNEY_ONE_CLOCK_SCHEMA);
  const legacy = { ...current, schema_version: "doctorcre-v5-journey-one-clock.v1" };
  delete legacy.origin_receipt_ttl_policy_ms;
  delete legacy.base_deadline_resolution;
  p.history = reseal(legacy);
  assert.throws(() => run(p), e => e.code === "legacy_history_migration_required" &&
    e.detail.history_schema_version === "doctorcre-v5-journey-one-clock.v1" &&
    e.detail.current_schema_version === JOURNEY_ONE_CLOCK_SCHEMA);
  // An unknown schema is not silently treated as legacy either.
  p.history = reseal({ ...current, schema_version: "doctorcre-v5-journey-one-clock.v3" });
  refuse(p, "corrupt_history");
});

test("an inadmissible or impossible admission cannot start the clock", () => {
  for (const mode of ["expired", "future", "status", "admitted_ahead"]) {
    const p = snapshot("2026-09-11T15:00:00.000Z");
    if (mode === "expired") p.minimum_history[0].admitted_at = p.minimum_history[0].receipt.ttl_expires_at;
    if (mode === "future") p.minimum_history[0].receipt.observed_at = "2026-09-10T15:00:00.000Z";
    if (mode === "status") p.minimum_history[0].receipt.status = "fail";
    if (mode === "admitted_ahead") p.minimum_history[0].admitted_at = "2026-09-12T15:00:00.000Z";
    refuse(p, { expired: "origin_unavailable", status: "origin_unavailable",
      future: "receipt_observed_after_reference", admitted_ahead: "future_admission" }[mode]);
  }
});

// --- benchmark acceptance and amendment ------------------------------------

test("benchmark must be partner accepted strictly before the first pass", () => {
  // Late acceptance and future-dated acceptance are distinct defects and are
  // presented one at a time. `as_of` sits a day past the origin pass here, so
  // these acceptances are late without also being ahead of the verified instant.
  for (const accepted of [ORIGIN, "2026-09-10T12:00:00.000Z"]) {
    const late = snapshot("2026-09-10T15:00:00.000Z"); late.benchmark.accepted_at = accepted;
    refuse(late, "benchmark_not_accepted_before_origin");
  }
  const edge = snapshot(); edge.benchmark.accepted_at = iso(Date.parse(ORIGIN) - 1);
  assert.equal(run(edge).state.origin_benchmark_manifest_digest, D(8));
  // An acceptance dated past the verified instant is refused on its own terms,
  // before any question of where it falls relative to the origin.
  const ahead = snapshot(); ahead.benchmark.accepted_at = iso(Date.parse(ORIGIN) + 1);
  refuse(ahead, "benchmark_accepted_in_the_future");
  const p = snapshot(); p.benchmark.accepted_by_identity = I("model"); refuse(p, "verified_partner_required");
  const q = snapshot(); q.benchmark.deadline_contract.calendar_days = 31; refuse(q, "wrong_deadline_contract");
  const r = snapshot(); r.benchmark.subject_digest = D(99); refuse(r, "benchmark_binding_mismatch");
});

test("a replacement benchmark manifest needs an exact partner amendment and never rebases the clock", () => {
  const p = snapshot("2026-09-10T15:00:00.000Z");
  const before = run(p);
  p.history = before.state;
  p.benchmark.manifest_digest = D(9);
  p.benchmark.accepted_at = "2026-09-10T12:00:00.000Z";
  refuse(p, "unamended_benchmark_replacement");
  const other = copy(p);
  other.amendments = [amend(before.state.origin_receipt_digest, "2026-09-10T13:00:00.000Z", { manifest: D(10) })];
  refuse(other, "unamended_benchmark_replacement");
  p.amendments = [amend(before.state.origin_receipt_digest, "2026-09-10T13:00:00.000Z", { manifest: D(9) })];
  const after = run(p);
  assert.equal(after.state.origin_at, before.state.origin_at);
  assert.equal(after.state.origin_receipt_digest, before.state.origin_receipt_digest);
  assert.equal(after.state.base_deadline_at, before.state.base_deadline_at);
  assert.equal(after.state.due_at, before.state.due_at);
  assert.equal(after.state.origin_benchmark_manifest_digest, D(8));
  assert.equal(after.state.current_benchmark_manifest_digest, D(9));
  assert.equal(after.benchmark_amended, true);
  assert.equal(after.state.events.at(-1).type, "amendment_recorded");
  assert.deepEqual(after.state.events.slice(0, before.state.events.length), before.state.events);
  // A clock with no recorded history has no original benchmark to preserve, so
  // the first evaluation must still present the pre-origin accepted manifest.
  // `as_of` again trails the origin, isolating lateness from future-dating.
  const fresh = snapshot("2026-09-10T15:00:00.000Z");
  fresh.benchmark.manifest_digest = D(9); fresh.benchmark.accepted_at = "2026-09-09T16:00:00.000Z";
  refuse(fresh, "benchmark_not_accepted_before_origin");
  // An amendment naming the manifest cannot stand in for that acceptance: with
  // no history there is no original for the amendment to be a replacement of.
  const unhistoried = copy(fresh);
  unhistoried.amendments = [amend(digest(minimum()), "2026-09-10T13:00:00.000Z", { manifest: D(9) })];
  refuse(unhistoried, "benchmark_not_accepted_before_origin");
  // The future-dating guard covers an amended replacement too.
  const ahead = copy(p); ahead.benchmark.accepted_at = "2026-09-10T15:00:00.001Z";
  refuse(ahead, "benchmark_accepted_in_the_future");
});

test("a recorded amendment cannot be silently rolled back to the origin manifest", () => {
  const p = snapshot("2026-09-10T15:00:00.000Z");
  p.history = run(p).state;
  p.benchmark.manifest_digest = D(9);
  p.benchmark.accepted_at = "2026-09-10T12:00:00.000Z";
  p.amendments = [amend(p.history.origin_receipt_digest, "2026-09-10T13:00:00.000Z", { manifest: D(9) })];
  const amended = run(p);
  assert.equal(amended.state.current_benchmark_manifest_digest, D(9));
  assert.equal(amended.benchmark_amended, true);
  // Reverting to the originating manifest is a replacement of the CURRENT one,
  // and the amendment that authorized D(9) is not evidence for un-amending it.
  // The pre-origin acceptance the fresh path relies on is presented here too, so
  // the only thing missing is the authorization.
  const rollback = copy(p);
  rollback.history = amended.state; rollback.as_of = "2026-09-11T15:00:00.000Z";
  rollback.benchmark.manifest_digest = D(8);
  rollback.benchmark.accepted_at = iso(Date.parse(ORIGIN) - HOUR);
  refuse(rollback, "unamended_benchmark_replacement");
  // A second exact partner amendment naming D(8) authorizes the return.
  const authorized = copy(rollback);
  authorized.amendments.push(amend(amended.state.origin_receipt_digest, "2026-09-11T13:00:00.000Z", { name: "revert", manifest: D(8) }));
  const reverted = run(authorized);
  assert.equal(reverted.state.current_benchmark_manifest_digest, D(8));
  assert.equal(reverted.state.origin_benchmark_manifest_digest, D(8));
  assert.equal(reverted.benchmark_amended, false);
  assert.equal(reverted.state.events.filter(e => e.type === "amendment_recorded").length, 2);
  // Presenting the current manifest again needs no further amendment.
  const unchanged = copy(p); unchanged.history = amended.state; unchanged.as_of = "2026-09-11T15:00:00.000Z";
  assert.equal(run(unchanged).state.current_benchmark_manifest_digest, D(9));
});

test("an exact partner amendment appends metadata without resetting origin or deadline", () => {
  const p = snapshot("2026-09-10T15:00:00.000Z"); const before = run(p);
  p.amendments = [amend(before.state.origin_receipt_digest, p.as_of)]; p.history = before.state;
  const after = run(p);
  assert.equal(after.state.origin_at, before.state.origin_at); assert.equal(after.state.due_at, before.state.due_at);
  assert.equal(after.state.events.at(-1).type, "amendment_recorded");
  assert.equal(after.benchmark_amended, false);
  const forged = copy(p); forged.amendments[0].description_digest = D(43); refuse(forged, "amendment_digest_mismatch");
  const agent = copy(p); agent.amendments = [amend(before.state.origin_receipt_digest, p.as_of, { partner: "model" })];
  refuse(agent, "verified_partner_required");
  const rebased = copy(p); rebased.amendments = [amend(D(99), p.as_of)]; refuse(rebased, "amendment_reset_or_time");
  const twice = copy(p); twice.amendments.push(copy(twice.amendments[0])); refuse(twice, "duplicate_amendment");
  p.amendments[0].reset_at = p.as_of; refuse(p, "closed_shape");
});

// --- the terminus -----------------------------------------------------------

for (const offset of [-1, 0, 1]) test(`completion deadline offset ${offset} milliseconds`, () => {
  // The boundary is INCLUSIVE and exact to the millisecond, and a first
  // completion with no recorded miss is the genuine success case.
  const at = iso(Date.parse("2026-10-09T15:00:00.000Z") + offset), p = snapshot(at);
  p.completion = completed(at); const r = run(p);
  assert.equal(r.deadline_success, offset <= 0); assert.equal(r.replan_required, offset > 0);
  assert.equal(r.state.status, offset <= 0 ? "completed_on_time" : "completed_late");
  // The observational diagnostic and the recorded miss agree with the verdict
  // here precisely because nothing was recorded before this evaluation.
  assert.equal(r.completion_observed_within_deadline, offset <= 0);
  assert.equal(r.missing_evidence_miss_recorded, offset > 0);
});

test("passing full migration and wrong kernel obligation joins never substitute", () => {
  const p = snapshot(); p.completion = completed(ORIGIN);
  p.completion.gate_id = "journey-one-production-accepted"; refuse(p, "wrong_terminus_gate");
  p.completion.gate_id = "journey-one-kernel-production-accepted";
  p.completion.combiner = "any_current_pass"; refuse(p, "wrong_terminus_gate");
  p.completion.combiner = "all_current_exact_distinct_pass";
  p.completion.obligation_decision_ids = ["Q002.D1", "Q014.D1", "Q014.D1"]; refuse(p, "wrong_kernel_obligations");
  p.completion.obligation_decision_ids = ["Q002.D1", "Q123.D1", "Q014.D1"]; refuse(p, "wrong_kernel_obligations");
  p.completion.obligation_decision_ids = ["Q002.D1", "Q014.D1", "Q123.D1"];
  p.completion.receipts.push(copy(p.completion.receipts[0])); refuse(p, "missing_or_duplicate_kernel_receipt");
});

test("kernel receipt exact schema, identities, currentness, scope and digests are enforced", () => {
  const cases = [
    // r7's rollout-component-receipt.v1 is closed and declares no
    // schema_version, so adding one is an extra field like any other.
    ["schema_version", "rollout-component-receipt.v1", "closed_shape"],
    ["producer_step_ref", "step:j1-core-production-outcome", "wrong_producer_or_schema"],
    ["receipt_ref", "safe:receipt:journey-one-production", "wrong_terminus_receipt"],
    ["candidate_digest", D(99), "receipt_binding_mismatch"],
    ["rollout_environment_manifest_digest", D(99), "receipt_environment_mismatch"],
    ["evidence_scope", "candidate-and-test", "receipt_scope_mismatch"],
    ["status", "stale", "nonpassing_receipt"],
    ["negative_admission_result", "denials_not_exercised", "nonpassing_receipt"],
    ["ttl_expires_at", ORIGIN, "invalid_receipt_window"],
    ["ttl_expires_at", iso(Date.parse(ORIGIN) + COMPLETION_TTL_POLICY + 1), "receipt_ttl_policy_exceeded"],
    ["observed_at", "2026-09-10T15:00:00.000Z", "receipt_observed_after_reference"],
    ["evaluator_identity", I("maker"), "self_attestation"],
    ["producer_identity", I("maker"), "self_attestation"],
    ["producer_role", "independent_journey_one_outcome_oracle", "receipt_oracle_mismatch"],
    ["oracle_version", "1.1.0", "receipt_oracle_mismatch"],
    ["comparator", "x", "invalid_comparator"],
  ];
  for (const [key, value, code] of cases) { const p = snapshot(); p.completion = completed(ORIGIN); p.completion.receipts[0][key] = value; refuse(p, code); }
  const lapsed = snapshot("2026-09-11T15:00:00.000Z");
  lapsed.completion = completed(ORIGIN); refuse(lapsed, "receipt_not_current");
});

test("the exact r7 field sets are the schema, on both receipts, with nothing added", () => {
  // Twenty-one for consumer-gate-receipt.v1 and twenty-two for
  // rollout-component-receipt.v1, exactly as r7 declares them.
  assert.equal(Object.keys(minimum()).length, 21);
  assert.equal(Object.keys(completed(ORIGIN).receipts[0]).length, 22);
  for (const receipt of [minimum(), completed(ORIGIN).receipts[0]]) {
    assert.ok(!Object.hasOwn(receipt, "schema_version"));
  }
  // Both raw shapes are consumable as they stand.
  assert.equal(run(snapshot()).state.status, "running");
  const done = snapshot(); done.completion = completed(ORIGIN);
  assert.equal(run(done).state.status, "completed_on_time");
  // And an added schema_version on the MINIMUM is refused as a closed shape
  // rather than quietly tolerated, exactly as it is on the terminus above.
  const p = snapshot();
  p.minimum_history[0].receipt = { ...minimum(), schema_version: "consumer-gate-receipt.v1" };
  refuse(p, "closed_shape");
});

test("the minimum and completion TTL maxima are two policies, neither derived from the other", () => {
  // A seventy-two-hour terminus window is admissible under the completion
  // policy while the same window on a minimum is refused by the minimum policy.
  // One number for both would have refused a legitimate terminus.
  assert.ok(COMPLETION_TTL_POLICY > MINIMUM_TTL_POLICY);
  const wide = snapshot();
  wide.completion = completed(ORIGIN);
  wide.completion.receipts[0].ttl_expires_at = iso(Date.parse(ORIGIN) + COMPLETION_TTL_POLICY);
  assert.equal(run(wide).state.status, "completed_on_time");
  const minimumTooWide = snapshot();
  minimumTooWide.minimum_history[0].receipt.ttl_expires_at = iso(Date.parse(ORIGIN) + COMPLETION_TTL_POLICY);
  refuse(minimumTooWide, "receipt_ttl_policy_exceeded");
  // Each policy is validated on its own terms.
  for (const field of ["maximum_minimum_receipt_ttl_ms", "maximum_completion_receipt_ttl_ms"]) {
    const bad = snapshot(); bad.binding[field] = 0;
    assert.throws(() => run(bad), e => e.code === "invalid_receipt_ttl_policy" && e.detail === field, field);
  }
});

test("the terminus binds the exact kernel artifact and fixture set, not their shape", () => {
  // Two completions differing only in artifact_digest must not both pass.
  const honest = snapshot(); honest.completion = completed(ORIGIN);
  assert.equal(run(honest).state.status, "completed_on_time");
  const other = snapshot(); other.completion = completed(ORIGIN);
  other.completion.receipts[0].artifact_digest = D(77);
  refuse(other, "completion_artifact_mismatch");
  const fixtures = snapshot(); fixtures.completion = completed(ORIGIN);
  fixtures.completion.receipts[0].fixture_set_digest = D(78);
  refuse(fixtures, "completion_fixture_mismatch");
  // The expectation is the projection's, so relabelling it does not relabel the
  // artifact: a projection naming a different artifact refuses the same receipt.
  const moved = snapshot(); moved.completion = completed(ORIGIN);
  moved.completion_expectation = { artifact_digest: D(79), fixture_set_digest: D(5) };
  refuse(moved, "completion_artifact_mismatch");
  const malformed = snapshot();
  malformed.completion_expectation = { artifact_digest: "kernel-v1", fixture_set_digest: D(5) };
  refuse(malformed, "invalid_digest");
  const open = snapshot();
  open.completion_expectation = { ...copy(EXPECTATION), accepted: true };
  refuse(open, "closed_shape");
});

test("the expected kernel artifact is required at the terminus and never at the origin", () => {
  // The artifact does not exist when the clock starts, so a projection that has
  // no expectation yet still starts, runs and misses a clock.
  const running = snapshot(); running.completion_expectation = null;
  assert.equal(run(running).state.status, "running");
  // Nothing has been judged, so nothing about a completion is sealed either.
  assert.equal(run(running).state.completion_receipt_ttl_policy_ms, null);
  assert.equal(run(running).state.completion_artifact_digest, null);
  assert.equal(run(running).state.completion_fixture_set_digest, null);
  const missed = snapshot("2026-10-10T15:00:00.000Z"); missed.completion_expectation = null;
  assert.equal(run(missed).state.status, "missed");
  // It becomes required exactly where a completion is read, and never earlier.
  const done = snapshot(); done.completion_expectation = null; done.completion = completed(ORIGIN);
  refuse(done, "completion_expectation_unavailable");
});

test("a recorded completion survives its receipt lapsing and stops being currently usable", () => {
  const p = snapshot("2026-10-01T15:00:00.000Z"); p.completion = completed(p.as_of);
  const done = run(p);
  assert.equal(done.state.status, "completed_on_time"); assert.equal(done.completion_currently_usable, true);
  // The SAME honest inventory, read two days later: the terminus receipt is no
  // longer current, but the completion it evidenced is already a recorded fact.
  // Reading the truthful record must not be worse than withholding it.
  p.history = done.state; p.as_of = "2026-10-03T15:00:00.000Z";
  const lapsed = run(p);
  assert.equal(lapsed.state.status, "completed_on_time");
  assert.equal(lapsed.state.completion_receipt_digest, done.state.completion_receipt_digest);
  assert.equal(lapsed.state.completion_observed_at, done.state.completion_observed_at);
  assert.equal(lapsed.deadline_success, true);
  assert.equal(lapsed.completion_currently_usable, false);
  assert.deepEqual(lapsed.state.events, done.state.events);
  // The seals the completion was judged under survive with it, unchanged.
  assert.equal(lapsed.state.completion_receipt_ttl_policy_ms, COMPLETION_TTL_POLICY);
  assert.equal(lapsed.state.completion_artifact_digest, EXPECTATION.artifact_digest);
  assert.equal(lapsed.state.completion_fixture_set_digest, EXPECTATION.fixture_set_digest);
  // A completion this clock never recorded still refuses when it is not current,
  // and a lapsed receipt that is not the recorded one refuses as well.
  const fresh = snapshot("2026-10-03T15:00:00.000Z");
  fresh.completion = completed("2026-10-01T15:00:00.000Z");
  refuse(fresh, "receipt_not_current");
  const other = copy(p); other.completion = completed("2026-10-01T16:00:00.000Z");
  refuse(other, "receipt_not_current");
  // Currentness is the only refusal softened, and only for the exact recorded
  // receipt. Every other defect stays fatal in an authoritative inventory.
  const broken = copy(p); broken.completion.receipts[0].status = "stale";
  refuse(broken, "nonpassing_receipt");
});

test("the completion TTL policy that admitted a recorded completion is sealed, and a change refuses by name", () => {
  const at = "2026-10-01T15:00:00.000Z";
  const p = snapshot(at); p.completion = completed(at);
  // A sixty-hour terminus window: legitimate under the seventy-two-hour policy in
  // force when it was admitted, and longer than the tightened policy below.
  p.completion.receipts[0].ttl_expires_at = iso(Date.parse(at) + 60 * HOUR);
  const done = run(p);
  assert.equal(done.state.status, "completed_on_time");
  assert.equal(done.state.completion_receipt_ttl_policy_ms, COMPLETION_TTL_POLICY);
  // The SAME honest inventory re-read under a TIGHTENED completion policy. The
  // recorded receipt would now be "overlong" under a policy that never judged it,
  // and reporting that as `receipt_ttl_policy_exceeded` would make a sealed
  // history unreadable by calling a recorded fact defective. The change is what
  // is named, and it is named before any receipt is read.
  const tightened = copy(p);
  tightened.history = done.state;
  tightened.as_of = "2026-10-02T15:00:00.000Z";
  tightened.binding.maximum_completion_receipt_ttl_ms = 48 * HOUR;
  assert.throws(() => run(tightened), e => e.code === "completion_ttl_policy_changed" &&
    e.detail.recorded === COMPLETION_TTL_POLICY && e.detail.supplied === 48 * HOUR);
  // A WIDENED policy is a change too: the seal is the exact policy that judged the
  // completion, not an upper bound that a later reader may relax.
  const widened = copy(tightened);
  widened.binding.maximum_completion_receipt_ttl_ms = 96 * HOUR;
  refuse(widened, "completion_ttl_policy_changed");
  // Under the sealed policy the same evaluation reads the recorded completion
  // without complaint, so the refusal above is the change and not the receipt.
  const unchanged = copy(tightened);
  unchanged.binding.maximum_completion_receipt_ttl_ms = COMPLETION_TTL_POLICY;
  const again = run(unchanged);
  assert.equal(again.state.status, "completed_on_time");
  assert.equal(again.state.completion_receipt_ttl_policy_ms, COMPLETION_TTL_POLICY);
  assert.deepEqual(again.state.events, done.state.events);
  // Nothing here tolerates a NEW overlong receipt. Presented fresh under the
  // tightened policy, the same sixty-hour window is the fatal generic refusal it
  // should be: the seal preserves what was recorded, it never admits anything.
  const fresh = snapshot(at);
  fresh.completion = copy(p.completion);
  fresh.binding.maximum_completion_receipt_ttl_ms = 48 * HOUR;
  assert.throws(() => run(fresh), e => e.code === "receipt_ttl_policy_exceeded" && e.detail === "completion");
});

test("the accepted kernel scope a completion was judged against is sealed, and a change refuses by name", () => {
  const at = "2026-10-01T15:00:00.000Z";
  const p = snapshot(at); p.completion = completed(at);
  const done = run(p);
  assert.equal(done.state.completion_artifact_digest, EXPECTATION.artifact_digest);
  assert.equal(done.state.completion_fixture_set_digest, EXPECTATION.fixture_set_digest);
  const later = copy(p); later.history = done.state; later.as_of = "2026-10-02T15:00:00.000Z";
  // A projection naming a DIFFERENT accepted artifact is not a mismatched receipt.
  // `completion_artifact_mismatch` would report the recorded terminus as wrong;
  // what actually changed is the expectation, applied to a completion that was
  // already judged, and that is what is named.
  const moved = copy(later);
  moved.completion_expectation = { artifact_digest: D(79), fixture_set_digest: D(5) };
  assert.throws(() => run(moved), e => e.code === "completion_expectation_changed" &&
    e.detail.recorded.artifact_digest === EXPECTATION.artifact_digest &&
    e.detail.supplied.artifact_digest === D(79));
  const fixtures = copy(later);
  fixtures.completion_expectation = { artifact_digest: D(7), fixture_set_digest: D(78) };
  refuse(fixtures, "completion_expectation_changed");
  // It is named even when no completion is presented at all: the seal is a fact
  // about the record, not about what a later inventory happens to carry.
  const quiet = copy(moved); quiet.completion = null;
  refuse(quiet, "completion_expectation_changed");
  // The same expectation reads the record unchanged.
  const same = run(copy(later));
  assert.equal(same.state.status, "completed_on_time");
  assert.equal(same.state.completion_artifact_digest, EXPECTATION.artifact_digest);
  // The recorded scope is NOT re-derivable from whatever receipt is presented: a
  // second, different terminus whose own artifact matches a moved expectation
  // refuses on the change, before the artifact comparison it would have passed
  // and before the replacement refusal it would otherwise have reached.
  const rebuilt = copy(later);
  rebuilt.completion = completed("2026-10-02T15:00:00.000Z");
  rebuilt.completion.receipts[0].artifact_digest = D(79);
  rebuilt.completion_expectation = { artifact_digest: D(79), fixture_set_digest: D(5) };
  refuse(rebuilt, "completion_expectation_changed");
  // A projection that no longer carries the expectation is not a change — the
  // seal is already in the record and nothing re-judges the completion — but the
  // seal never STANDS IN for it either: presenting a completion without one still
  // refuses, so the trusted projection remains the only source of that binding.
  const absent = copy(later); absent.completion_expectation = null; absent.completion = null;
  assert.equal(run(absent).state.completion_artifact_digest, EXPECTATION.artifact_digest);
  const presented = copy(later); presented.completion_expectation = null;
  refuse(presented, "completion_expectation_unavailable");
});

test("a recorded completion stays readable past its receipt's expiry, bound to the exact recorded digest", () => {
  const at = "2026-10-01T15:00:00.000Z";
  const p = snapshot(at); p.completion = completed(at);
  p.completion.receipts[0].ttl_expires_at = iso(Date.parse(at) + COMPLETION_TTL_POLICY);
  const done = run(p);
  assert.equal(done.state.completion_receipt_ttl_policy_ms, COMPLETION_TTL_POLICY);
  // Four days on, the receipt has lapsed and the truthful inventory still carries
  // it. The recorded completion, its verdict and its seals all survive.
  p.history = done.state; p.as_of = "2026-10-05T15:00:00.000Z";
  const lapsed = run(p);
  assert.equal(lapsed.state.status, "completed_on_time");
  assert.equal(lapsed.deadline_success, true);
  assert.equal(lapsed.completion_currently_usable, false);
  assert.equal(lapsed.state.completion_receipt_digest, done.state.completion_receipt_digest);
  assert.equal(lapsed.state.completion_receipt_ttl_policy_ms, COMPLETION_TTL_POLICY);
  assert.deepEqual(lapsed.state.events, done.state.events);
  // The softening is currentness alone and it is bound to the exact recorded
  // receipt: a DIFFERENT lapsed terminus is still fatal.
  const other = copy(p); other.completion = completed("2026-10-01T16:00:00.000Z");
  refuse(other, "receipt_not_current");
});

test("both completion seals are absent until a completion is recorded, and changing either while running is ordinary", () => {
  const p = snapshot("2026-09-10T15:00:00.000Z");
  const running = run(p);
  assert.equal(running.state.completion_receipt_ttl_policy_ms, null);
  assert.equal(running.state.completion_artifact_digest, null);
  assert.equal(running.state.completion_fixture_set_digest, null);
  // A clock that has judged no terminus has nothing to reinterpret, so a tightened
  // completion policy and a different expected kernel artifact are ordinary
  // changes rather than rewrites of anything recorded.
  const changed = copy(p);
  changed.history = running.state;
  changed.as_of = "2026-09-11T15:00:00.000Z";
  changed.binding.maximum_completion_receipt_ttl_ms = 24 * HOUR;
  changed.completion_expectation = { artifact_digest: D(80), fixture_set_digest: D(81) };
  const still = run(changed);
  assert.equal(still.state.status, "running");
  assert.equal(still.state.completion_receipt_ttl_policy_ms, null);
  assert.equal(still.state.completion_artifact_digest, null);
  // The FIRST completion seals whatever policy and expectation admitted it, and
  // the sealed scope is the PROJECTION's expectation rather than the receipt's own
  // artifact_digest — the two agree here only because the expectation accepted it.
  const finish = copy(changed);
  finish.history = still.state;
  finish.as_of = "2026-09-12T15:00:00.000Z";
  finish.completion = completed(finish.as_of);
  finish.completion.receipts[0].artifact_digest = D(80);
  finish.completion.receipts[0].fixture_set_digest = D(81);
  const sealed = run(finish);
  assert.equal(sealed.state.status, "completed_on_time");
  assert.equal(sealed.state.completion_receipt_ttl_policy_ms, 24 * HOUR);
  assert.equal(sealed.state.completion_artifact_digest, D(80));
  assert.equal(sealed.state.completion_fixture_set_digest, D(81));
  // From that moment the earlier policy and the earlier expectation are the
  // changed ones, and returning to either refuses by name.
  const back = copy(finish); back.history = sealed.state; back.as_of = "2026-09-13T15:00:00.000Z";
  back.binding.maximum_completion_receipt_ttl_ms = COMPLETION_TTL_POLICY;
  refuse(back, "completion_ttl_policy_changed");
  const rescoped = copy(finish); rescoped.history = sealed.state; rescoped.as_of = "2026-09-13T15:00:00.000Z";
  rescoped.completion_expectation = copy(EXPECTATION);
  refuse(rescoped, "completion_expectation_changed");
});

test("a history cannot carry a completion without its seals, or a seal without a completion", () => {
  const at = "2026-10-01T15:00:00.000Z";
  const q = snapshot(at); q.completion = completed(at);
  const done = copy(run(q).state); q.as_of = "2026-10-02T15:00:00.000Z";
  for (const key of ["completion_receipt_ttl_policy_ms", "completion_artifact_digest", "completion_fixture_set_digest"]) {
    q.history = reseal({ ...copy(done), [key]: null });
    refuse(q, "corrupt_history");
  }
  q.history = reseal({ ...copy(done), completion_receipt_ttl_policy_ms: 0 });
  refuse(q, "corrupt_history");
  q.history = reseal({ ...copy(done), completion_artifact_digest: "kernel-v1" });
  refuse(q, "invalid_digest");
  // And a clock that recorded no completion cannot acquire a seal it never earned.
  const p = snapshot("2026-09-10T15:00:00.000Z");
  const running = copy(run(p).state);
  p.history = reseal({ ...copy(running), completion_artifact_digest: D(7) });
  refuse(p, "corrupt_history");
  p.history = reseal({ ...copy(running), completion_receipt_ttl_policy_ms: COMPLETION_TTL_POLICY });
  refuse(p, "corrupt_history");
  // A record missing the fields altogether is not a v2 history at all, and is
  // refused as the closed shape it fails rather than read as a partial one.
  const short = copy(running); delete short.completion_artifact_digest;
  p.history = reseal(short); refuse(p, "closed_shape");
});

// --- pauses: actual elapsed hours, capped, approved in advance --------------

test("actual elapsed pauses union overlapping windows and clip ongoing time", () => {
  const p = snapshot("2026-09-11T15:00:00.000Z");
  p.pauses = [pause(p, "2026-09-10T15:00:00.000Z", "2026-09-11T03:00:00.000Z"),
    pause(p, "2026-09-10T21:00:00.000Z", null, "two", "dell")];
  const r = run(p); assert.equal(r.state.paused_ms, 24 * HOUR); assert.equal(r.state.due_at, "2026-10-10T15:00:00.000Z");
});

test("pause counts actual DST elapsed hours rather than calendar dates", () => {
  const origin = "2026-02-20T16:00:00.000Z", p = snapshot("2026-03-09T05:00:00.000Z", origin);
  p.pauses = [pause(p, "2026-03-08T06:00:00.000Z", "2026-03-09T05:00:00.000Z")];
  assert.equal(run(p).state.paused_ms, 23 * HOUR);
});

test("pause cap is 120 actual hours, even for an ongoing long pause", () => {
  const p = snapshot("2026-10-20T15:00:00.000Z");
  p.pauses = [pause(p, "2026-09-10T15:00:00.000Z", null)];
  const r = run(p); assert.equal(r.state.paused_ms, 120 * HOUR); assert.equal(r.state.due_at, "2026-10-14T15:00:00.000Z"); assert.equal(r.state.status, "missed");
  assert.equal(JOURNEY_ONE_DEADLINE_CONTRACT.maximum_external_blocker_pause_hours, 120);
});

test("two separate pauses are capped on their total, not each", () => {
  const p = snapshot("2026-10-20T15:00:00.000Z");
  p.pauses = [pause(p, "2026-09-10T15:00:00.000Z", "2026-09-14T15:00:00.000Z"),
    pause(p, "2026-09-20T15:00:00.000Z", "2026-09-27T15:00:00.000Z", "two", "dell")];
  assert.equal(run(p).state.paused_ms, 120 * HOUR);
});

test("a future pause gets no unelapsed credit and a post-deadline pause cannot rescue a miss", () => {
  const p = snapshot(); p.pauses = [pause(p, "2026-09-10T15:00:00.000Z", null)];
  p.pauses[0].approved_at = ORIGIN; resignPause(p.pauses[0]); assert.equal(run(p).state.paused_ms, 0);
  const q = snapshot("2026-10-11T15:00:00.000Z"); q.pauses = [pause(q, "2026-10-10T15:00:00.000Z", null)];
  const r = run(q); assert.equal(r.state.paused_ms, 0); assert.equal(r.state.status, "missed");
});

test("pause prior approval must be Joe or Dell and cannot be backdated", () => {
  for (const mode of ["at_start", "after_start", "before_origin", "agent", "wrong_hash", "wrong_origin", "not_external"]) {
    const p = snapshot("2026-09-11T15:00:00.000Z"); const a = pause(p, "2026-09-10T15:00:00.000Z", null);
    if (mode === "at_start") a.approved_at = a.starts_at;
    if (mode === "after_start") a.approved_at = "2026-09-10T16:00:00.000Z";
    if (mode === "before_origin") a.approved_at = "2026-09-09T14:00:00.000Z";
    if (mode === "agent") a.approved_by_identity = I("model");
    if (mode === "wrong_origin") a.clock_origin_digest = D(99);
    if (mode === "not_external") a.blocker_ref = "safe:internal:build-delay";
    resignPause(a); if (mode === "wrong_hash") a.approval_digest = D(99); p.pauses = [a];
    refuse(p, { at_start: "pause_backdating_or_order", after_start: "pause_backdating_or_order",
      before_origin: "pause_backdating_or_order", agent: "verified_partner_required",
      wrong_hash: "pause_approval_digest_mismatch", wrong_origin: "pause_origin_mismatch", not_external: "invalid_reference" }[mode]);
  }
});

test("a duplicate pause id refuses rather than double-counting", () => {
  const p = snapshot("2026-09-11T15:00:00.000Z");
  p.pauses = [pause(p, "2026-09-10T15:00:00.000Z", "2026-09-10T18:00:00.000Z"),
    pause(p, "2026-09-10T19:00:00.000Z", "2026-09-10T21:00:00.000Z", "one", "dell")];
  refuse(p, "duplicate_pause");
});

// --- miss, replan, and an append-only ledger --------------------------------

test("miss requires replan, survives late completion and never deadlocks safe construction", () => {
  const p = snapshot("2026-10-10T15:00:00.000Z"), missed = run(p);
  assert.equal(missed.replan_required, true); assert.equal(missed.safe_construction_may_continue, true);
  p.history = missed.state; p.as_of = "2026-10-11T15:00:00.000Z"; p.completion = completed(p.as_of);
  const late = run(p); assert.equal(late.state.status, "completed_late"); assert.equal(late.deadline_success, false); assert.equal(late.completion_currently_usable, true);
  assert.equal(late.replan_required, true);
  assert.equal(late.completion_observed_within_deadline, false);
  assert.deepEqual(late.state.events.slice(0, missed.state.events.length), missed.state.events);
  p.history = late.state; p.completion = null; p.as_of = "2026-10-12T15:00:00.000Z";
  const stale = run(p); assert.equal(stale.state.status, "completed_late"); assert.equal(stale.completion_currently_usable, false); assert.equal(stale.replan_required, true);
});

test("a completion observed after the deadline is late however early it is reported", () => {
  const p = snapshot("2026-10-09T15:00:00.001Z"); p.history = run(p).state;
  p.completion = completed("2026-10-09T15:00:00.001Z");
  const r = run(p);
  assert.equal(r.state.status, "completed_late");
  assert.equal(r.deadline_success, false);
  assert.equal(r.replan_required, true);
  // A single evaluation with no interval read reaches the same verdict, so the
  // lateness is the observation's and not the cadence's.
  const once = snapshot("2026-10-09T15:00:00.001Z");
  once.completion = completed("2026-10-09T15:00:00.001Z");
  assert.equal(run(once).state.status, "completed_late");
});

test("an exact-deadline proof admitted after a recorded miss never claims deadline success", () => {
  const DEADLINE = "2026-10-09T15:00:00.000Z";
  // PATH A: nothing evaluated in between, so no miss was ever recorded. The
  // completion is observed at exactly the deadline — inclusive — and the clock
  // genuinely succeeded. This is the only shape that claims deadline success.
  const direct = snapshot(DEADLINE);
  direct.completion = completed(DEADLINE);
  const a = run(direct);
  assert.equal(a.state.status, "completed_on_time");
  assert.equal(a.deadline_success, true);
  assert.equal(a.replan_required, false);
  assert.equal(a.missing_evidence_miss_recorded, false);
  assert.equal(a.completion_observed_within_deadline, true);

  // PATH B: one evaluation ran a millisecond past the deadline before the same
  // completion was admitted, and DURABLY RECORDED A MISS because no completion
  // evidence was in hand then. Q008.D1 as accepted: that miss "is durably
  // recorded, requires replan, preserves origin and elapsed history, forbids
  // claiming deadline success". The question of excusing a proof that arrives
  // after the miss carrying an earlier observation has no answer on the record,
  // so no exception is taken here.
  const interval = snapshot("2026-10-09T15:00:00.001Z");
  const missed = run(interval);
  assert.equal(missed.state.status, "missed");
  assert.equal(missed.state.miss_at, DEADLINE);
  const late = copy(interval);
  late.history = missed.state;
  late.as_of = "2026-10-09T16:00:00.000Z";
  late.completion = completed(DEADLINE);
  const b = run(late);
  assert.equal(b.state.status, "completed_after_recorded_miss");
  assert.equal(b.deadline_success, false);
  assert.equal(b.replan_required, true);
  assert.notEqual(b.state.status, a.state.status);
  // The OBSERVATION is untouched and is never called late: it happened at the
  // deadline, and the diagnostic reports that beside the verdict rather than
  // inside it. The completion's own identity and usability survive intact, which
  // is what r7's terminus gate means by a late passing kernel receipt remaining
  // usable after the required miss/replan record.
  assert.equal(b.state.completion_observed_at, DEADLINE);
  assert.equal(b.completion_observed_within_deadline, true);
  assert.equal(b.state.completion_receipt_digest, digest(completed(DEADLINE).receipts[0]));
  assert.equal(b.completion_currently_usable, true);
  assert.equal(b.safe_construction_may_continue, true);
  // The earlier record is NOT erased or rewritten: the miss instant, its single
  // event, the origin and the elapsed history all survive, and the ledger only
  // ever gained the completion.
  assert.equal(b.state.miss_at, DEADLINE);
  assert.equal(b.missing_evidence_miss_recorded, true);
  assert.equal(b.state.events.filter(e => e.type === "deadline_missed").length, 1);
  assert.deepEqual(b.state.events.slice(0, missed.state.events.length), missed.state.events);
  assert.equal(b.state.events.at(-1).type, "completion_observed");
  assert.equal(b.state.origin_receipt_digest, missed.state.origin_receipt_digest);
  assert.equal(b.state.origin_at, missed.state.origin_at);
  assert.equal(b.state.paused_ms, missed.state.paused_ms);

  // REPLAY. Re-reading the sealed record keeps every fact and changes no verdict.
  const again = copy(late); again.history = b.state; again.as_of = "2026-10-09T17:00:00.000Z";
  const c = run(again);
  assert.equal(c.state.status, "completed_after_recorded_miss");
  assert.equal(c.deadline_success, false);
  assert.equal(c.replan_required, true);
  assert.equal(c.state.miss_at, DEADLINE);
  assert.equal(c.state.completion_observed_at, DEADLINE);
  assert.deepEqual(c.state.events, b.state.events);

  // TTL EXPIRY. Two days on the terminus receipt has lapsed and the truthful
  // inventory still carries it. Only its current usability lapses: the recorded
  // completion, its seals, the standing miss and the replan obligation are all
  // unchanged, and success is no more claimable than it was.
  const lapsed = copy(late); lapsed.history = c.state; lapsed.as_of = "2026-10-11T15:00:00.000Z";
  const d = run(lapsed);
  assert.equal(d.completion_currently_usable, false);
  assert.equal(d.state.status, "completed_after_recorded_miss");
  assert.equal(d.deadline_success, false);
  assert.equal(d.replan_required, true);
  assert.equal(d.completion_observed_within_deadline, true);
  assert.equal(d.state.completion_receipt_digest, b.state.completion_receipt_digest);
  assert.equal(d.state.completion_receipt_ttl_policy_ms, COMPLETION_TTL_POLICY);
  assert.equal(d.state.completion_artifact_digest, EXPECTATION.artifact_digest);
  assert.equal(d.state.completion_fixture_set_digest, EXPECTATION.fixture_set_digest);
  assert.deepEqual(d.state.events, b.state.events);
});

test("a history that claims deadline success beside a recorded miss is refused by name", () => {
  // No record this kernel writes can carry both, and one that arrives carrying
  // both is not quietly recomputed into something truthful. Q008.D1 forbids the
  // claim, so the contradiction is named where it is read.
  const p = snapshot("2026-10-09T15:00:00.001Z");
  const missed = copy(run(p).state);
  assert.equal(missed.miss_at, "2026-10-09T15:00:00.000Z");
  p.history = reseal({ ...copy(missed), status: "completed_on_time" });
  refuse(p, "deadline_success_claimed_after_recorded_miss");
  // The same history under its own recorded status reads without complaint, so
  // the refusal is the forbidden claim and not the presence of a miss.
  p.history = reseal(copy(missed));
  assert.equal(run(p).state.status, "missed");
});

test("completed-after-miss history requires both a recorded miss and a completion", () => {
  for (const now of ["2026-09-09T15:00:00Z", "2026-10-09T15:00:00.001Z"]) {
    const p = snapshot(now);
    const prior = run(p).state;
    p.history = reseal({ ...copy(prior), status: "completed_after_recorded_miss" });
    refuse(p, "corrupt_history");
  }
});

test("a late reported pause recomputes due_at without un-missing an immutable miss", () => {
  const p = snapshot("2026-10-09T15:00:00.001Z");
  const missed = run(p);
  assert.equal(missed.state.miss_at, "2026-10-09T15:00:00.000Z");
  assert.equal(missed.state.status, "missed");
  // A blocker pause approved a week before the deadline, reported an hour after
  // the miss was recorded. Its actual elapsed hours are credited, so the CURRENT
  // deadline moves past the recorded miss instant. The miss is a historical fact
  // about an instant that passed with no completion: it is not recomputed, not
  // erased, and does not become on time. Both facts stay in the record.
  p.history = missed.state; p.as_of = "2026-10-09T16:00:00.000Z";
  p.pauses = [pause(p, "2026-10-01T14:00:00.000Z", "2026-10-05T14:00:00.000Z", "late-report", "dell", "2026-10-01T13:00:00.000Z")];
  const after = run(p);
  assert.equal(after.state.paused_ms, 96 * HOUR);
  assert.equal(after.state.due_at, "2026-10-13T15:00:00.000Z");
  assert.equal(after.state.miss_at, missed.state.miss_at);
  assert.equal(Date.parse(after.state.due_at) > Date.parse(after.state.miss_at), true);
  assert.equal(after.state.status, "missed");
  assert.equal(after.replan_required, true);
  assert.equal(after.deadline_success, false);
  assert.deepEqual(after.state.events.slice(0, missed.state.events.length), missed.state.events);
  // That divergence is readable again: the miss stays bound to its own event,
  // never to the recomputed deadline.
  p.history = after.state; p.as_of = "2026-10-09T17:00:00.000Z";
  const again = run(p);
  assert.equal(again.state.miss_at, missed.state.miss_at);
  assert.equal(again.state.due_at, after.state.due_at);
  assert.equal(again.state.status, "missed");
});

test("a late pause that moves due_at into the future cannot un-miss a clock a later completion stops", () => {
  const p = snapshot("2026-10-09T15:00:00.001Z");
  const missed = run(p);
  assert.equal(missed.state.miss_at, "2026-10-09T15:00:00.000Z");
  assert.equal(missed.state.status, "missed");
  // A blocker pause approved a week before the deadline and reported an hour
  // after the miss was recorded. Its ninety-six actual elapsed hours are
  // credited, so the CURRENT deadline moves to 13 October — past the recorded
  // miss instant and past the completion below.
  p.history = missed.state; p.as_of = "2026-10-09T16:00:00.000Z";
  p.pauses = [pause(p, "2026-10-01T14:00:00.000Z", "2026-10-05T14:00:00.000Z", "late-report", "dell", "2026-10-01T13:00:00.000Z")];
  const extended = run(p);
  assert.equal(extended.state.paused_ms, 96 * HOUR);
  assert.equal(extended.state.due_at, "2026-10-13T15:00:00.000Z");
  assert.equal(extended.state.miss_at, missed.state.miss_at);
  assert.equal(extended.state.status, "missed");
  // The kernel is then completed INSIDE the extended deadline. Observationally
  // it beat the current due_at and the diagnostic says so, but changing a
  // deadline through an admissible pause never clears a miss the record already
  // carries: no success is claimed and the replan obligation stands.
  p.history = extended.state; p.as_of = "2026-10-12T15:00:00.000Z";
  p.completion = completed(p.as_of);
  const done = run(p);
  assert.equal(Date.parse(done.state.completion_observed_at) < Date.parse(done.state.due_at), true);
  assert.equal(done.completion_observed_within_deadline, true);
  assert.equal(done.state.status, "completed_after_recorded_miss");
  assert.equal(done.deadline_success, false);
  assert.equal(done.replan_required, true);
  assert.equal(done.completion_currently_usable, true);
  assert.equal(done.state.miss_at, missed.state.miss_at);
  assert.equal(done.state.paused_ms, 96 * HOUR);
  assert.equal(done.state.origin_at, missed.state.origin_at);
  assert.equal(done.state.events.filter(e => e.type === "deadline_missed").length, 1);
  assert.deepEqual(done.state.events.slice(0, extended.state.events.length), extended.state.events);
  // And the sealed record replays to the same verdict rather than settling into
  // a success once the miss is a few evaluations old.
  p.history = done.state; p.as_of = "2026-10-13T14:00:00.000Z";
  const replayed = run(p);
  assert.equal(replayed.state.status, "completed_after_recorded_miss");
  assert.equal(replayed.deadline_success, false);
  assert.equal(replayed.replan_required, true);
  assert.deepEqual(replayed.state.events, done.state.events);
});

test("a miss instant a history's own event does not attest refuses", () => {
  const p = snapshot("2026-10-10T15:00:00.000Z");
  const missed = copy(run(p).state);
  assert.equal(missed.miss_at, "2026-10-09T15:00:00.000Z");
  assert.equal(missed.events.at(-1).type, "deadline_missed");
  assert.equal(missed.events.at(-1).at, missed.miss_at);
  assert.equal(missed.events.at(-1).evidence_digest,
    digest(["deadline_missed", missed.miss_at, missed.origin_receipt_digest]));
  for (const forged of ["2027-01-01T00:00:00.000Z", "2026-10-09T14:59:59.999Z"]) {
    p.history = reseal({ ...copy(missed), miss_at: forged });
    refuse(p, "erased_miss_history");
  }
});

test("a second, different terminus receipt cannot replace a recorded completion", () => {
  const p = snapshot("2026-10-01T15:00:00.000Z"); p.completion = completed(p.as_of);
  p.history = run(p).state; p.as_of = "2026-10-02T15:00:00.000Z";
  p.completion = completed(p.as_of); refuse(p, "completion_history_replacement");
});

test("later minimum cannot replace an immutable origin", () => {
  const p = snapshot(); p.history = run(p).state; p.as_of = "2026-09-10T15:00:00.000Z";
  p.minimum_history = [{ admitted_at: p.as_of, receipt: minimum(p.as_of) }]; refuse(p, "origin_reset_or_rebase");
});

test("an earlier approval reported after a later event appends instead of deadlocking the ledger", () => {
  const p = snapshot("2026-09-10T15:00:00.000Z");
  p.pauses = [pause(p, "2026-09-10T10:00:00.000Z", "2026-09-10T12:00:00.000Z", "late", "joe", "2026-09-10T09:00:00.000Z")];
  const before = run(p);
  assert.equal(before.state.events.at(-1).type, "pause_approved");
  p.history = before.state; p.as_of = "2026-09-11T15:00:00.000Z";
  p.pauses.push(pause(p, "2026-09-10T06:00:00.000Z", "2026-09-10T08:00:00.000Z", "earlier", "dell", "2026-09-10T05:00:00.000Z"));
  const after = run(p);
  assert.equal(after.state.paused_ms, 4 * HOUR);
  assert.deepEqual(after.state.events.slice(0, before.state.events.length), before.state.events);
  const appended = after.state.events.at(-1), previous = after.state.events.at(-2);
  assert.equal(appended.type, "pause_approved");
  assert.equal(appended.at, "2026-09-10T05:00:00.000Z");
  assert.equal(Date.parse(appended.at) < Date.parse(previous.at), true);
  assert.equal(Date.parse(appended.recorded_at) > Date.parse(previous.recorded_at), true);
  assert.equal(appended.previous_event_digest, previous.event_digest);
});

test("tampering with history hashes or deleting a recorded fact refuses", () => {
  const p = snapshot("2026-10-10T15:00:00.000Z"); p.history = copy(run(p).state);
  p.history.origin_at = "2026-09-10T15:00:00.000Z"; refuse(p, "corrupt_history");
  const missed = copy(run({ ...p, history: null }).state);
  p.history = reseal({ ...copy(missed), miss_at: null }); refuse(p, "erased_miss_history");
  p.history = reseal({ ...copy(missed), paused_ms: 121 * HOUR }); refuse(p, "corrupt_history");
  p.history = reseal({ ...copy(missed), status: "on_time" }); refuse(p, "corrupt_history");
  p.history = reseal({ ...copy(missed), events: missed.events.slice(0, 1) }); refuse(p, "erased_miss_history");
  const tampered = copy(missed);
  tampered.events.at(-1).evidence_digest = D(99); p.history = reseal(tampered); refuse(p, "corrupt_history");

  const q = snapshot("2026-10-01T15:00:00.000Z"); q.completion = completed(q.as_of);
  const done = copy(run(q).state); q.as_of = "2026-10-02T15:00:00.000Z";
  // Erasing a completion means erasing the whole recorded fact — receipt digest,
  // instant and seals together — and the event it evidenced still refuses.
  q.history = reseal({ ...copy(done), completion_receipt_digest: null, completion_observed_at: null,
    completion_receipt_ttl_policy_ms: null, completion_artifact_digest: null, completion_fixture_set_digest: null });
  refuse(q, "erased_completion_history");
  q.history = reseal({ ...copy(done), completion_observed_at: "2026-10-01T14:00:00.000Z" });
  refuse(q, "erased_completion_history");
});

test("previous pause approvals cannot vanish from a later inventory", () => {
  const p = snapshot("2026-09-11T15:00:00.000Z"); p.pauses = [pause(p, "2026-09-10T15:00:00.000Z", null)];
  p.history = run(p).state;
  const omitted = copy(p); omitted.pauses = []; refuse(omitted, "erased_approval_history");
  p.as_of = "2026-09-12T15:00:00.000Z"; p.pauses[0].ends_at = "2026-09-12T03:00:00.000Z";
  assert.equal(run(p).state.paused_ms, 36 * HOUR);
});

test("an honest late report of a pause end counts the hours that actually elapsed", () => {
  // The blocker cleared TWELVE hours into the pause, but nobody said so until
  // after an evaluation had already read the pause as ongoing and credited
  // twenty-four. The settled rule counts actual elapsed hours, so the honest
  // report is admissible and the only alternative — reporting the full
  // twenty-four to get past a guard — is the false one.
  const p = snapshot("2026-09-11T15:00:00.000Z");
  p.pauses = [pause(p, "2026-09-10T15:00:00.000Z", null)];
  const ongoing = run(p);
  assert.equal(ongoing.state.paused_ms, 24 * HOUR);
  assert.equal(ongoing.state.pause_intervals[0].ends_at, null);

  const honest = copy(p);
  honest.history = ongoing.state;
  honest.as_of = "2026-09-12T15:00:00.000Z";
  honest.pauses[0].ends_at = "2026-09-11T03:00:00.000Z";
  const settled = run(honest);
  assert.equal(settled.state.paused_ms, 12 * HOUR);
  assert.equal(settled.state.due_at, "2026-10-10T03:00:00.000Z");
  assert.deepEqual(settled.state.events, ongoing.state.events, "no event is rewritten by the report");

  // Once known, the end is immutable: neither a different instant nor a return
  // to "still ongoing" is admissible.
  const moved = copy(honest);
  moved.history = settled.state;
  moved.pauses[0].ends_at = "2026-09-11T09:00:00.000Z";
  refuse(moved, "pause_history_rewritten");
  const unended = copy(honest);
  unended.history = settled.state;
  unended.pauses[0].ends_at = null;
  refuse(unended, "pause_history_rewritten");

  // Immutability is over INSTANTS, not spellings: the same end written with an
  // explicit zero offset is the same end.
  const respelled = copy(honest);
  respelled.history = settled.state;
  respelled.pauses[0].ends_at = "2026-09-11T03:00:00.000+00:00";
  const same = run(respelled);
  assert.equal(same.state.paused_ms, 12 * HOUR);
  assert.equal(Date.parse(same.state.pause_intervals[0].ends_at), Date.parse(settled.state.pause_intervals[0].ends_at));
});

test("a previously recorded amendment cannot vanish from a later inventory", () => {
  const p = snapshot("2026-09-10T15:00:00.000Z"); const before = run(p);
  p.amendments = [amend(before.state.origin_receipt_digest, p.as_of)]; p.history = before.state;
  p.history = run(p).state; p.as_of = "2026-09-11T15:00:00.000Z";
  const omitted = copy(p); omitted.amendments = []; refuse(omitted, "erased_approval_history");
  assert.equal(run(p).state.events.filter(e => e.type === "amendment_recorded").length, 1);
});

// --- the exported history reader -------------------------------------------
// It exists so a durable store can ask THIS kernel whether a stored history is
// still readable, instead of owning a second, weaker validator of its own.

test("the exported history reader answers with a defensive frozen snapshot and touches nothing", () => {
  const p = snapshot("2026-09-19T15:00:00.000Z");
  p.pauses = [pause(p, "2026-09-10T15:00:00.000Z", "2026-09-11T03:00:00.000Z")];
  const state = copy(run(p).state);
  const before = copy(state);

  const read = readJourneyOneClockHistory(state, state.evaluated_at);
  assert.deepEqual(read, before, "a readable history comes back exactly as it went in");
  assert.deepEqual(state, before, "and the caller's own object was never written to");
  assert.notEqual(read, state, "the answer is a copy, not the caller's object");
  assert.equal(Object.isFrozen(read), true);
  assert.equal(Object.isFrozen(read.events), true);
  assert.equal(Object.isFrozen(read.events[0]), true);
  assert.equal(Object.isFrozen(read.pause_intervals[0]), true);
  assert.throws(() => { read.status = "completed_on_time"; }, TypeError,
    "a reader cannot edit the thing it was just told is well formed");
  assert.equal(readJourneyOneClockHistory(null, ORIGIN), null, "no history is not a malformed history");
});

test("the history reader takes an explicit instant and never reaches for a live clock", () => {
  const state = copy(run(snapshot()).state);
  // NO DEFAULT. A validator that quietly read the system clock would make one
  // stored history readable or unreadable depending on when it was asked.
  for (const bad of [undefined, null, Date.parse(ORIGIN), new Date(ORIGIN), "2026-09-09", "now", ""]) {
    assert.throws(() => readJourneyOneClockHistory(state, bad), e => e.code === "invalid_timestamp");
  }
  assert.throws(() => readJourneyOneClockHistory(state, "2026-09-09T14:59:59.000Z"),
    e => e.code === "history_time_reversed");
  assert.equal(readJourneyOneClockHistory(state, "2026-09-09T15:00:00.000Z").status, "running");
});

test("the history reader refuses exactly what evaluate refuses, by the same names", () => {
  const p = snapshot("2026-10-10T15:00:00.000Z");
  const missed = copy(run(p).state);
  assert.equal(missed.status, "missed");
  const at = missed.evaluated_at;
  const unsealed = copy(missed); unsealed.due_at = "2026-10-11T15:00:00.000Z";
  for (const [history, code] of [
    [reseal({ ...copy(missed), schema_version: "doctorcre-v5-journey-one-clock.v1" }), "legacy_history_migration_required"],
    [unsealed, "corrupt_history"],
    [reseal({ ...copy(missed), paused_ms: 121 * HOUR }), "corrupt_history"],
    [reseal({ ...copy(missed), status: "on_time" }), "corrupt_history"],
    [reseal({ ...copy(missed), miss_at: null }), "erased_miss_history"],
    [reseal({ ...copy(missed), events: missed.events.slice(0, 1) }), "erased_miss_history"],
    [reseal({ ...copy(missed), status: "completed_on_time" }), "deadline_success_claimed_after_recorded_miss"],
  ]) {
    assert.throws(() => readJourneyOneClockHistory(copy(history), at),
      e => e.code === code, `the reader should refuse ${code}`);
    // THE SAME HISTORY THROUGH THE WHOLE KERNEL, refused under the same name.
    // That is the comparison that makes the export "the kernel's own read"
    // rather than a second validator that happens to agree today.
    refuse({ ...snapshot("2026-10-10T15:00:00.000Z"), history: copy(history) }, code);
  }
});

test("the history reader refuses a value no two clauses could read the same way", () => {
  const state = copy(run(snapshot()).state);
  const at = state.evaluated_at;
  const withGetter = { ...state };
  Object.defineProperty(withGetter, "status", { get: () => "running", enumerable: true, configurable: true });
  assert.throws(() => readJourneyOneClockHistory(withGetter, at), e => e.code === "hidden_key");
  const exotic = { ...state, events: Object.setPrototypeOf([], { toJSON: () => copy(state.events) }) };
  assert.throws(() => readJourneyOneClockHistory(exotic, at), e => e.code === "invalid_object");
  for (const bad of [7, "history", true, []]) {
    assert.throws(() => readJourneyOneClockHistory(bad, at), e => e.code === "invalid_object");
  }
});

test("a pass from the history reader is not an anti-rollback claim", () => {
  // The kernel header says an OLDER GENUINE history replays unless a durable
  // store compare-and-swaps on the exact prior digest. This proves the export
  // did not quietly become that defence: both histories are perfectly readable.
  const first = copy(run(snapshot()).state);
  const later = copy(run({ ...snapshot("2026-09-14T15:00:00.000Z"), history: copy(first) }).state);
  assert.notEqual(first.history_digest, later.history_digest);
  for (const history of [first, later]) {
    assert.equal(readJourneyOneClockHistory(history, later.evaluated_at).origin_at, first.origin_at);
  }
});

// --- shape, time and effect boundaries -------------------------------------

for (const value of ["2026-02-30T12:00:00Z", "2026-09-09", "2026-09-09T12:00:00", "2026-09-09T24:00:00Z", "2026-09-09T12:00:00.0001Z"]) {
  test(`invalid or unsupported precision timestamp refuses: ${value}`, () => assert.throws(() => chicagoThirtyDayDeadline(value), e => e.code === "invalid_timestamp"));
}

test("JSON verification flags cannot authenticate a snapshot", () => {
  assert.throws(() => createJourneyOneClock(), e => e.code === "authenticated_verifier_required");
  assert.throws(() => harness().clock.evaluate({ verified: true, accepted: true, snapshot: snapshot() }), /synthetic-authentication-refused/);
  const clock = createJourneyOneClock({ verifySnapshot: () => true });
  assert.throws(() => clock.evaluate({}), e => e.code === "invalid_object");
  const wrong = createJourneyOneClock({ verifySnapshot: () => ({ envelope_digest: D(99), snapshot: snapshot() }) });
  assert.throws(() => wrong.evaluate({}), e => e.code === "verification_binding_mismatch");
});

test("a JSON-string envelope cannot borrow an authenticated object's digest", () => {
  // artifact-trust.js hashes a top-level string as its own raw bytes, so an
  // object envelope and the string spelling of its canonical JSON collide on one
  // digest. JSON-safety cannot catch it — a string is perfectly JSON-safe — so
  // without the admission guard the string would satisfy the same verification
  // binding as the object a verifier actually authenticated.
  const object = { synthetic_receipt_ref: "collision" };
  const string = canonicalJson(object);
  assert.equal(typeof string, "string");
  assert.equal(digest(string), digest(object));
  const clock = createJourneyOneClock({ verifySnapshot: e => ({ envelope_digest: digest(e), snapshot: snapshot() }) });
  // The object still evaluates exactly as before; the colliding string does not.
  assert.equal(clock.evaluate(object).state.status, "running");
  for (const bad of [string, null, [], [object], 7, true]) {
    assert.throws(() => clock.evaluate(bad), e => e.code === "invalid_object" && e.detail === "envelope");
  }
});

test("a custom array prototype cannot smuggle a value past validation", () => {
  // copy() is JSON.stringify, which honours an inherited toJSON, so an array
  // with an exotic prototype could hand back a value no clause ever validated —
  // here the exact "__proto__" key the hidden-key clause exists to refuse.
  const exotic = () => {
    const arr = [];
    Object.setPrototypeOf(arr, { toJSON: () => JSON.parse('{"__proto__":{"polluted":true}}') });
    return arr;
  };
  assert.throws(() => harness().clock.evaluate({ minimum_history: exotic() }), e => e.code === "invalid_object");
  const clock = createJourneyOneClock({ verifySnapshot: envelope => ({ envelope_digest: digest(envelope), snapshot: { pauses: exotic() } }) });
  assert.throws(() => clock.evaluate({}), e => e.code === "invalid_object");
  // An ordinary array is untouched by the check.
  assert.equal(run(snapshot()).state.status, "running");
});

// --- the verified binding, beside the state ---------------------------------

test("the verified binding travels beside the state and never inside it", () => {
  const result = run(snapshot());
  const binding = result.verified_binding;
  // Exactly the declared closed field set, and the kernel's own values.
  assert.deepEqual(Object.keys(binding).sort(), [...JOURNEY_ONE_CLOCK_VERIFIED_BINDING_FIELDS]);
  assert.equal(binding.schema_version, JOURNEY_ONE_CLOCK_VERIFIED_BINDING);
  assert.equal(binding.tenant, ORGANIZATION_TENANT_ID);
  assert.equal(binding.subject_digest, D(1));
  assert.equal(binding.candidate_digest, D(2));
  assert.equal(binding.policy_digest, D(3));
  assert.equal(binding.clock_origin_gate_id, JOURNEY_ONE_DEADLINE_CONTRACT.clock_origin_gate_id);
  assert.equal(binding.clock_terminus_gate_id, JOURNEY_ONE_DEADLINE_CONTRACT.clock_terminus_gate_id);

  // THE HASHED STATE IS UNTOUCHED. Not one of those fields is in it, the state
  // is still the twenty-one the schema declares, and history_digest is still
  // exactly the hash of the state minus itself. A field added here would have
  // rebased every stored clock.
  assert.equal(Object.keys(result.state).length, 21);
  for (const field of [...JOURNEY_ONE_CLOCK_VERIFIED_BINDING_FIELDS, "verified_binding"]) {
    if (field === "schema_version") continue;
    assert.ok(!Object.hasOwn(result.state, field), `${field} must not be in the hashed state`);
  }
  const { history_digest, ...body } = result.state;
  assert.equal(digest(body), history_digest);
  assert.equal(result.state.schema_version, JOURNEY_ONE_CLOCK_SCHEMA);
  // And the same projection evaluated again produces the same history digest:
  // the binding is not in the preimage, so it cannot move it.
  assert.equal(run(snapshot()).state.history_digest, history_digest);

  // READ-ONLY, and frozen with the rest of the result.
  assert.equal(Object.isFrozen(binding), true);
  assert.throws(() => { binding.subject_digest = D(99); }, TypeError);
  assert.throws(() => { delete binding.policy_digest; }, TypeError);
  assert.throws(() => { result.verified_binding = { subject_digest: D(99) }; }, TypeError);
  assert.equal(binding.subject_digest, D(1));
});

test("the verified binding can only report values this evaluation actually enforced", () => {
  // The three digests are the binding EVERY receipt and the accepted benchmark
  // had to match, so a projection whose parts disagree never produces a result
  // to read a binding off at all.
  const benchmark = snapshot(); benchmark.benchmark.subject_digest = D(31);
  refuse(benchmark, "benchmark_binding_mismatch");
  const receipt = snapshot(); receipt.minimum_history[0].receipt.policy_digest = D(32);
  refuse(receipt, "receipt_binding_mismatch");
  // Moved consistently, the binding moves with them — it is read from the
  // projection the verifier authenticated, not from a constant.
  const moved = snapshot();
  for (const target of [moved.binding, moved.benchmark, moved.minimum_history[0].receipt]) {
    target.subject_digest = D(31);
  }
  assert.equal(run(moved).verified_binding.subject_digest, D(31));
  // The gate ids come from the accepted deadline contract, which the benchmark
  // is compared against exactly; a benchmark carrying another contract refuses.
  const contract = snapshot();
  contract.benchmark.deadline_contract.clock_terminus_gate_id = "some-other-gate-accepted";
  refuse(contract, "wrong_deadline_contract");
});

test("the binding names WHICH projection was judged, and the accepted scope alone cannot", () => {
  // TWO VALID SNAPSHOTS FOR ONE ACCEPTED SCOPE. Same tenant, same subject,
  // candidate and policy digests, same gate ids, same as_of, same benchmark
  // manifest, same TTL policy, same origin admission and the same (empty)
  // pauses — and one records a COMPLETED clock while the other records a
  // running one. Every value the v1 binding exposed is identical across them,
  // which is exactly why a consumer holding its own projection could not tell
  // from that binding which of the two it was looking at.
  const running = snapshot();
  const done = snapshot(); done.completion = completed(ORIGIN);
  const a = run(running), b = run(done);

  for (const field of ["tenant", "subject_digest", "candidate_digest", "policy_digest",
    "clock_origin_gate_id", "clock_terminus_gate_id"]) {
    assert.equal(a.verified_binding[field], b.verified_binding[field],
      `${field} cannot distinguish two valid snapshots of one accepted scope`);
  }
  assert.equal(a.state.status, "running");
  assert.equal(b.state.status, "completed_on_time");
  assert.notEqual(a.state.history_digest, b.state.history_digest);

  // THE ONE FIELD THAT DOES DISTINGUISH THEM, and a consumer can recompute it
  // from the projection it holds rather than being asked to believe it.
  assert.notEqual(a.verified_binding.authenticated_projection_digest,
    b.verified_binding.authenticated_projection_digest);
  assert.equal(a.verified_binding.authenticated_projection_digest, digest(running));
  assert.equal(b.verified_binding.authenticated_projection_digest, digest(done));
  assert.match(a.verified_binding.authenticated_projection_digest, /^sha256:[0-9a-f]{64}$/);
  assert.equal(a.verified_binding.schema_version,
    "doctorcre-v5-journey-one-clock-verified-binding.v2");
});

test("the projection digest is canonical, deterministic, and outside the hashed state", () => {
  const p = snapshot();
  const first = run(p), second = run(copy(p));
  // Deterministic: the same projection judged twice reports one digest.
  assert.equal(first.verified_binding.authenticated_projection_digest,
    second.verified_binding.authenticated_projection_digest);

  // CANONICAL, so a snapshot rebuilt in another key order is the same
  // projection rather than a different one. digest() sorts keys; a consumer
  // comparing this against its own composition must be able to rely on that.
  const reordered = { history: p.history, amendments: p.amendments, pauses: p.pauses,
    completion_expectation: p.completion_expectation, completion: p.completion,
    minimum_history: p.minimum_history, benchmark: p.benchmark, binding: p.binding,
    as_of: p.as_of, tenant: p.tenant, schema_version: p.schema_version };
  assert.notDeepEqual(Object.keys(reordered), Object.keys(p), "the fixture must actually reorder");
  assert.equal(run(reordered).verified_binding.authenticated_projection_digest,
    first.verified_binding.authenticated_projection_digest);

  // NOT IN THE HASHED STATE. The state is still twenty-one fields, its digest is
  // still the hash of itself minus history_digest, and adding this field rebased
  // nothing: a projection input has no business in a clock's whole history.
  assert.equal(Object.keys(first.state).length, 21);
  assert.ok(!Object.hasOwn(first.state, "authenticated_projection_digest"));
  const { history_digest, ...body } = first.state;
  assert.equal(digest(body), history_digest);

  // A DIFFERENT PAUSE START, SAME ID AND SAME END: the state's pause_intervals
  // mirror only the id and the end, so those two are identical while the credited
  // hours and the deadline are not. The digest is what separates them.
  const early = snapshot(iso(Date.parse(ORIGIN) + 3 * HOUR));
  early.pauses = [pause(early, iso(Date.parse(ORIGIN) + HOUR), iso(Date.parse(ORIGIN) + 2 * HOUR))];
  const late = copy(early);
  late.pauses = [pause(late, iso(Date.parse(ORIGIN) + 90 * 60000), iso(Date.parse(ORIGIN) + 2 * HOUR))];
  const runEarly = run(early), runLate = run(late);
  assert.deepEqual(runEarly.state.pause_intervals, runLate.state.pause_intervals);
  assert.notEqual(runEarly.state.paused_ms, runLate.state.paused_ms);
  assert.notEqual(runEarly.state.due_at, runLate.state.due_at);
  assert.notEqual(runEarly.verified_binding.authenticated_projection_digest,
    runLate.verified_binding.authenticated_projection_digest);
});

test("closed projections, hidden fields, mutation and side effects remain bounded", () => {
  const p = snapshot(); p.reset = true; refuse(p, "closed_shape");
  const q = snapshot(); q.minimum_history[0].receipt.authorized = true; refuse(q, "closed_shape");
  const t = snapshot(); t.tenant = "other-tenant"; refuse(t, "wrong_projection_or_tenant");
  const before = copy(snapshot()), r = run(before); assert.deepEqual(before, snapshot());
  assert.equal(Object.isFrozen(r.state.events), true); assert.equal(r.authority_granted, false);
  assert.equal(r.effects.database_writes, 0); assert.equal(r.effects.provider_actions, 0); assert.equal(r.durable_history_write_required, true);
  assert.throws(() => harness().clock.evaluate(Object.defineProperty({}, "hidden", { get() { throw new Error("must not invoke"); } })), e => e.code === "hidden_key");
});
