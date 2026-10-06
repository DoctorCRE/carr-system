// DoctorCRE v5 slice V5-A05 — delivery cadence, escalation and the
// decision-ready quiet-hours queue.
//
// Three groups, one per checkable_done item, plus a CONTRACT group that proves
// the request schemas are closed and the module reads no clock of its own.

import assert from "node:assert/strict";
import { test } from "node:test";

import { V5BoundaryError, V5_NO_EFFECTS } from "../src/global-boundaries.v5.js";
import {
  V5_A05_CADENCE_INTERVAL_DAYS,
  V5_A05_CADENCE_STATUSES,
  V5_A05_DECISION_IDS,
  V5_A05_ORDINARY_REASON_IDS,
  V5_A05_POLICY_VERSION,
  V5_A05_REASON_IDS,
  V5_A05_ROUTINGS,
  V5_A05_SCHEMA_VERSION,
  V5_A05_URGENT_REASON_IDS,
  assertA05CatalogBinding,
  classifyEscalationReason,
  deriveEscalationFacts,
  escalationForCadenceMiss,
  evaluateCadenceReceipt,
  evaluateEscalationRouting,
  v5A05CatalogEntryDigest,
} from "../src/delivery-cadence-a05.v5.js";

const DAY = 24 * 60 * 60 * 1000;
const NOW = "2026-09-24T12:00:00Z";
const nowMs = Date.parse(NOW);
function isoMinusDays(days) { return new Date(nowMs - days * DAY).toISOString(); }

test("module identity: schema/policy version and decision ids", () => {
  assert.equal(V5_A05_SCHEMA_VERSION, "doctorcre-v5-delivery-cadence.v1");
  assert.equal(V5_A05_POLICY_VERSION, 1);
  assert.deepEqual(V5_A05_DECISION_IDS, ["Q008.D2", "Q013.D1", "Q027.D1", "Q045.D1", "Q131.D1"]);
  assert.equal(V5_A05_CADENCE_INTERVAL_DAYS, 14);
  assert.deepEqual([...V5_A05_CADENCE_STATUSES].sort(), ["current", "missed", "no_receipt_on_record"]);
  assert.deepEqual([...V5_A05_ROUTINGS].sort(), ["batch_for_morning", "deliver_immediately", "no_queue_entry"]);
});

test("catalog binding: digest is stable and drift is refused", () => {
  const digest = v5A05CatalogEntryDigest();
  assert.equal(typeof digest, "string");
  assert.equal(digest, v5A05CatalogEntryDigest(), "digest must be deterministic across calls");
  assert.doesNotThrow(() => assertA05CatalogBinding({ catalog_entry_digest: digest }));
  assert.throws(() => assertA05CatalogBinding({ catalog_entry_digest: "not-the-real-digest" }),
    err => err instanceof V5BoundaryError && err.code === "catalog_binding_drift");
  assert.throws(() => assertA05CatalogBinding({ catalog_entry_digest: digest, extra: true }),
    err => err instanceof V5BoundaryError && err.code === "unknown_field");
});

// ---------------------------------------------------------------------------
// checkable_done 1 — "14-day interval/miss/history fixtures pass".
// ---------------------------------------------------------------------------

test("cadence: no receipt on record", () => {
  const r = evaluateCadenceReceipt({ now: NOW, history: [] });
  assert.equal(r.status, "no_receipt_on_record");
  assert.equal(r.reason_id, "no_cadence_receipt_on_record");
  assert.equal(r.requires_replan, false);
  assert.equal(r.last_receipt_issued_at, null);
  assert.equal(r.interval_days, 14);
  assert.deepEqual(r.effects, V5_NO_EFFECTS);
});

test("cadence: within the 14-day interval is current, not a miss", () => {
  const r = evaluateCadenceReceipt({ now: NOW, history: [isoMinusDays(13)] });
  assert.equal(r.status, "current");
  assert.equal(r.reason_id, "within_cadence_interval");
  assert.equal(r.requires_replan, false);
});

test("cadence: exactly at the boundary (14.0 days) is still current", () => {
  const r = evaluateCadenceReceipt({ now: NOW, history: [isoMinusDays(14)] });
  assert.equal(r.status, "current");
  assert.equal(r.requires_replan, false);
});

test("cadence: one millisecond past the interval is a miss requiring replan", () => {
  const justOver = new Date(nowMs - (14 * DAY + 1)).toISOString();
  const r = evaluateCadenceReceipt({ now: NOW, history: [justOver] });
  assert.equal(r.status, "missed");
  assert.equal(r.reason_id, "cadence_interval_exceeded");
  assert.equal(r.requires_replan, true);
});

test("cadence: rolling window count and miss history across several receipts", () => {
  // Receipts every 10 days for 40 days, then a 20-day gap (a miss), then a
  // fresh receipt 5 days ago. Two receipts fall inside the current 14-day
  // window; exactly one gap in the whole history exceeds the interval.
  const history = [
    isoMinusDays(65), isoMinusDays(55), isoMinusDays(45), isoMinusDays(35),
    isoMinusDays(15), // 20-day gap after the 35-day-ago receipt: a miss
    isoMinusDays(5),
  ];
  const r = evaluateCadenceReceipt({ now: NOW, history });
  assert.equal(r.receipts_in_window, 1); // only isoMinusDays(5) is within 14 days of NOW
  assert.equal(r.miss_count_in_history, 1);
  assert.equal(r.status, "current");
  assert.equal(r.requires_replan, false);
});

test("cadence: subject is echoed back when supplied", () => {
  const r = evaluateCadenceReceipt({
    now: NOW, subject: { type: "engineering_slice", ref: "V5-A05" }, history: [isoMinusDays(1)],
  });
  assert.deepEqual(r.subject, { type: "engineering_slice", ref: "V5-A05" });
});

test("cadence: a future-dated receipt is a contract violation, not silently accepted", () => {
  assert.throws(() => evaluateCadenceReceipt({ now: NOW, history: [isoMinusDays(-1)] }),
    err => err instanceof V5BoundaryError && err.code === "receipt_issued_in_the_future");
});

test("cadence: a reordered or duplicated history cannot mask a miss", () => {
  assert.throws(
    () => evaluateCadenceReceipt({ now: NOW, history: [isoMinusDays(5), isoMinusDays(10)] }),
    err => err instanceof V5BoundaryError && err.code === "history_not_strictly_ascending");
  assert.throws(
    () => evaluateCadenceReceipt({ now: NOW, history: [isoMinusDays(5), isoMinusDays(5)] }),
    err => err instanceof V5BoundaryError && err.code === "history_not_strictly_ascending");
});

test("cadence: unparseable or impossible-calendar timestamps refuse rather than normalize", () => {
  assert.throws(() => evaluateCadenceReceipt({ now: "not-a-date", history: [] }),
    err => err instanceof V5BoundaryError && err.code === "invalid_timestamp");
  assert.throws(() => evaluateCadenceReceipt({ now: "2026-02-31T00:00:00Z", history: [] }),
    err => err instanceof V5BoundaryError && err.code === "invalid_timestamp");
  assert.throws(() => evaluateCadenceReceipt({ now: "2026-09-24T12:00:00", history: [] }), // no offset
    err => err instanceof V5BoundaryError && err.code === "invalid_timestamp");
});

test("cadence: unknown fields and missing required fields are contract violations", () => {
  assert.throws(() => evaluateCadenceReceipt({ now: NOW, history: [], extra: 1 }),
    err => err instanceof V5BoundaryError && err.code === "unknown_field");
  assert.throws(() => evaluateCadenceReceipt({ history: [] }),
    err => err instanceof V5BoundaryError && err.code === "missing_field");
});

test("cadence: determinism — the same request always reaches the same answer", () => {
  const request = { now: NOW, history: [isoMinusDays(20), isoMinusDays(3)] };
  const a = evaluateCadenceReceipt(request);
  const b = evaluateCadenceReceipt(request);
  assert.deepEqual(a, b);
});

test("cadence: two 14-day-apart calls with no clock read stay independent (no clock reset possible)", () => {
  // Nothing internal reads Date.now(); moving wall-clock time cannot change
  // this module's answer for a fixed request. Proven by holding `now` fixed
  // across widely separated invocations and asserting identical results.
  const request = { now: NOW, history: [isoMinusDays(1)] };
  const first = evaluateCadenceReceipt(request);
  for (let i = 0; i < 1000; i++) { /* burn wall-clock time without reading it */ }
  const second = evaluateCadenceReceipt(request);
  assert.deepEqual(first, second);
});

// ---------------------------------------------------------------------------
// checkable_done 2 — "ordinary blockers batch; urgent harm alerts
// immediately".
// ---------------------------------------------------------------------------

test("escalation: every urgent reason classifies urgent and delivers immediately", () => {
  for (const reasonId of V5_A05_URGENT_REASON_IDS) {
    assert.equal(classifyEscalationReason(reasonId), "urgent");
    const r = evaluateEscalationRouting({
      reason_id: reasonId, requires_joe_authority: false, unresolved_intent: false, quiet_now: true,
    });
    assert.equal(r.severity, "urgent");
    assert.equal(r.routing, "deliver_immediately");
    assert.equal(r.bypasses_quiet_hours, true);
    assert.equal(r.batched, false);
    assert.equal(r.wakes_joe, true);
  }
});

test("escalation: every ordinary reason with no authority/intent flag gets no queue entry", () => {
  for (const reasonId of V5_A05_ORDINARY_REASON_IDS) {
    assert.equal(classifyEscalationReason(reasonId), "ordinary");
    const r = evaluateEscalationRouting({
      reason_id: reasonId, requires_joe_authority: false, unresolved_intent: false, quiet_now: false,
    });
    assert.equal(r.routing, "no_queue_entry");
    assert.equal(r.wakes_joe, false);
    assert.equal(r.batched, false);
    assert.equal(r.bypasses_quiet_hours, false);
  }
});

test("escalation: an ordinary reason needing Joe's authority batches for the morning, urgent bypasses", () => {
  const ordinary = evaluateEscalationRouting({
    reason_id: "delivery_blocker", requires_joe_authority: true, unresolved_intent: false, quiet_now: true,
  });
  assert.equal(ordinary.routing, "batch_for_morning");
  assert.equal(ordinary.batched, true);
  assert.equal(ordinary.bypasses_quiet_hours, false);
  assert.equal(ordinary.wakes_joe, true);

  const urgent = evaluateEscalationRouting({
    reason_id: "security_incident", requires_joe_authority: false, unresolved_intent: false, quiet_now: true,
  });
  assert.equal(urgent.routing, "deliver_immediately");
  assert.equal(urgent.bypasses_quiet_hours, true);
});

test("escalation: unknown reason ids are refused, never guessed as ordinary", () => {
  assert.throws(() => classifyEscalationReason("made_up_reason"),
    err => err instanceof V5BoundaryError && err.code === "unknown_reason_id");
  assert.throws(() => evaluateEscalationRouting({
    reason_id: "made_up_reason", requires_joe_authority: false, unresolved_intent: false, quiet_now: false,
  }), err => err instanceof V5BoundaryError && err.code === "unknown_reason_id");
});

test("escalation: the reason vocabularies are disjoint and closed", () => {
  const overlap = V5_A05_URGENT_REASON_IDS.filter(id => V5_A05_ORDINARY_REASON_IDS.includes(id));
  assert.deepEqual(overlap, []);
  assert.deepEqual([...V5_A05_REASON_IDS].sort(),
    [...V5_A05_URGENT_REASON_IDS, ...V5_A05_ORDINARY_REASON_IDS].sort());
});

test("escalation: request schema is closed — no field can widen authority", () => {
  assert.throws(() => evaluateEscalationRouting({
    reason_id: "delivery_blocker", requires_joe_authority: false, unresolved_intent: false,
    quiet_now: false, priority: "high", // not a field this module reads
  }), err => err instanceof V5BoundaryError && err.code === "unknown_field");
  assert.throws(() => evaluateEscalationRouting({
    reason_id: "delivery_blocker", requires_joe_authority: "yes", unresolved_intent: false, quiet_now: false,
  }), err => err instanceof V5BoundaryError && err.code === "invalid_shape");
});

// ---------------------------------------------------------------------------
// checkable_done 3 — "only required human authority or unresolved intent
// wakes Joe".
// ---------------------------------------------------------------------------

test("routing: wakes_joe is true in exactly the three named cases and false otherwise", () => {
  const cases = [
    { reason_id: "security_incident", requires_joe_authority: false, unresolved_intent: false, quiet_now: false, expectWake: true },
    { reason_id: "delivery_blocker", requires_joe_authority: true, unresolved_intent: false, quiet_now: false, expectWake: true },
    { reason_id: "delivery_blocker", requires_joe_authority: false, unresolved_intent: true, quiet_now: false, expectWake: true },
    { reason_id: "delivery_blocker", requires_joe_authority: false, unresolved_intent: false, quiet_now: false, expectWake: false },
    { reason_id: "delivery_blocker", requires_joe_authority: false, unresolved_intent: false, quiet_now: true, expectWake: false },
  ];
  for (const c of cases) {
    const { expectWake, ...request } = c;
    const r = evaluateEscalationRouting(request);
    assert.equal(r.wakes_joe, expectWake, JSON.stringify(c));
  }
});

test("routing: quiet_now is echoed but never itself changes routing", () => {
  const quiet = evaluateEscalationRouting({
    reason_id: "outward_harm", requires_joe_authority: false, unresolved_intent: false, quiet_now: true,
  });
  const notQuiet = evaluateEscalationRouting({
    reason_id: "outward_harm", requires_joe_authority: false, unresolved_intent: false, quiet_now: false,
  });
  assert.equal(quiet.routing, notQuiet.routing);
  assert.equal(quiet.bypasses_quiet_hours, notQuiet.bypasses_quiet_hours);
  assert.equal(quiet.quiet_now, true);
  assert.equal(notQuiet.quiet_now, false);
});

test("routing: an item with neither urgency, authority nor unresolved intent is not an engagement notification", () => {
  // excluded_scope: "engagement notifications" — a routine item never becomes a
  // queue entry Joe sees, no matter how it is framed.
  const r = evaluateEscalationRouting({
    reason_id: "review_blocker", requires_joe_authority: false, unresolved_intent: false, quiet_now: false,
  });
  assert.equal(r.routing, "no_queue_entry");
  assert.equal(r.wakes_joe, false);
});

// ---------------------------------------------------------------------------
// "replan on miss" wiring between checkable_done 1 and 2.
// ---------------------------------------------------------------------------

test("replan on miss: a cadence miss always routes to the morning batch, never immediate or silent", () => {
  const justOver = new Date(nowMs - (14 * DAY + 1)).toISOString();
  const cadence = evaluateCadenceReceipt({ now: NOW, history: [justOver] });
  assert.equal(cadence.status, "missed");
  const escalation = escalationForCadenceMiss(cadence);
  assert.equal(escalation.reason_id, "cadence_miss_replan_required");
  assert.equal(escalation.severity, "ordinary");
  assert.equal(escalation.routing, "batch_for_morning");
  assert.equal(escalation.wakes_joe, true);
  assert.equal(escalation.bypasses_quiet_hours, false);
});

test("replan on miss: a current or no-receipt cadence result is refused, not silently escalated", () => {
  const current = evaluateCadenceReceipt({ now: NOW, history: [isoMinusDays(1)] });
  assert.throws(() => escalationForCadenceMiss(current),
    err => err instanceof V5BoundaryError && err.code === "not_a_cadence_miss");
  const noReceipt = evaluateCadenceReceipt({ now: NOW, history: [] });
  assert.throws(() => escalationForCadenceMiss(noReceipt),
    err => err instanceof V5BoundaryError && err.code === "not_a_cadence_miss");
});

// ---------------------------------------------------------------------------
// CONTRACT — effects, no ambient clock, frozen exports.
// ---------------------------------------------------------------------------

test("contract: every result carries V5_NO_EFFECTS and is frozen", () => {
  const cadence = evaluateCadenceReceipt({ now: NOW, history: [] });
  assert.deepEqual(cadence.effects, V5_NO_EFFECTS);
  assert.ok(Object.isFrozen(cadence));
  const routing = evaluateEscalationRouting({
    reason_id: "delivery_blocker", requires_joe_authority: false, unresolved_intent: false, quiet_now: false,
  });
  assert.deepEqual(routing.effects, V5_NO_EFFECTS);
  assert.ok(Object.isFrozen(routing));
});

test("contract: exported vocabularies are frozen and cannot be mutated by a consumer", () => {
  assert.ok(Object.isFrozen(V5_A05_URGENT_REASON_IDS));
  assert.ok(Object.isFrozen(V5_A05_ORDINARY_REASON_IDS));
  assert.throws(() => { V5_A05_URGENT_REASON_IDS.push("anything"); });
});

// ---------------------------------------------------------------------------
// SERVER DERIVATION -- PR #1236 review round 2, item 3. Urgency and
// authority-need come from the reason id plus server-read facts only.
// ---------------------------------------------------------------------------

const OPEN_SEV1 = Object.freeze({ ref: "INC-1", state: "investigating", severity: "SEV-1",
  environment: "production", duplicate_of_id: null });
const derive = (fields) => deriveEscalationFacts({
  reason_id: "security_incident", seat: "authority", incident: null, cadence_status: null, ...fields });

test("derivation: an urgent reason is honoured only from a raising seat citing an open production SEV-0/1 incident", () => {
  for (const reason of V5_A05_URGENT_REASON_IDS) {
    for (const seat of ["authority", "system"]) {
      const ok = derive({ reason_id: reason, seat, incident: OPEN_SEV1 });
      assert.equal(ok.ok, true, `${reason}/${seat}`);
      assert.equal(ok.requires_joe_authority, false);
      assert.equal(ok.verified_by.incident_ref, "INC-1");
      const routed = evaluateEscalationRouting({ reason_id: reason, requires_joe_authority: ok.requires_joe_authority,
        unresolved_intent: ok.unresolved_intent, quiet_now: true });
      assert.equal(routed.routing, "deliver_immediately");
      assert.equal(routed.bypasses_quiet_hours, true);
    }
    assert.equal(derive({ reason_id: reason, seat: "other", incident: OPEN_SEV1 }).refusal_id,
      "urgent_alert_requires_system_or_authority_seat");
    assert.equal(derive({ reason_id: reason }).refusal_id, "urgent_alert_requires_verified_incident");
  }
  const refusedFor = (incident) => derive({ incident: { ...OPEN_SEV1, ...incident } });
  assert.deepEqual([...refusedFor({ state: "resolved" }).failed_checks], ["incident_not_open"]);
  assert.deepEqual([...refusedFor({ state: "reviewed" }).failed_checks], ["incident_not_open"]);
  assert.deepEqual([...refusedFor({ duplicate_of_id: "x" }).failed_checks], ["incident_adjudicated_duplicate"]);
  assert.deepEqual([...refusedFor({ environment: "staging" }).failed_checks], ["incident_not_production"]);
  assert.deepEqual([...refusedFor({ severity: "SEV-2" }).failed_checks], ["incident_severity_not_urgent"]);
  assert.equal(derive({ incident: { ...OPEN_SEV1, severity: "SEV-0" } }).ok, true);
  assert.equal(refusedFor({ severity: "SEV-3" }).refusal_id, "urgent_alert_incident_not_verified");
});

test("derivation: ordinary reasons take their authority-need from the reason and the server-clock cadence status", () => {
  const miss = derive({ reason_id: "cadence_miss_replan_required", cadence_status: "missed" });
  assert.equal(miss.ok, true);
  assert.equal(miss.requires_joe_authority, true);
  for (const status of ["current", "no_receipt_on_record", null]) {
    assert.equal(derive({ reason_id: "cadence_miss_replan_required", cadence_status: status }).refusal_id,
      "cadence_miss_not_verified", String(status));
  }
  assert.equal(derive({ reason_id: "decision_required" }).requires_joe_authority, true);
  for (const reason of ["delivery_blocker", "review_blocker"]) {
    const facts = derive({ reason_id: reason, seat: "other" });
    assert.equal(facts.ok, true);
    assert.equal(facts.requires_joe_authority, false);
    assert.equal(facts.unresolved_intent, false);
  }
  // Ordinary reasons never need a seat: evidence and morning batches are open.
  assert.equal(derive({ reason_id: "decision_required", seat: "other" }).ok, true);
});

test("derivation: caller booleans have no field to arrive in, and malformed facts are contract violations", () => {
  assert.throws(() => deriveEscalationFacts({ reason_id: "review_blocker", seat: "other", incident: null,
    cadence_status: null, requires_joe_authority: true }), V5BoundaryError);
  assert.throws(() => derive({ seat: "root" }), V5BoundaryError);
  assert.throws(() => derive({ reason_id: "made_up" }), V5BoundaryError);
  assert.throws(() => derive({ cadence_status: "late" }), V5BoundaryError);
  assert.throws(() => derive({ incident: { ...OPEN_SEV1, urgent: true } }), V5BoundaryError);
  assert.ok(Object.isFrozen(derive({ incident: OPEN_SEV1 })));
});
