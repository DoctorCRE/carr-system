// V5-A04 — budget signals FIRE at a crossing, once, and overdrawn ancestors
// deny new reservations without touching the ones that already exist.
//
// The shipped suites prove the SNAPSHOT: evaluateReplan says "warn" at 150 and
// "replan" at 200. This file proves the EVENT: a warning or replan fires when a
// committed operation crosses a line, never twice for one crossing, never on a
// replay, a refusal or a race loser — and the deny path leaves every existing
// reservation exactly where it was.

import test from "node:test";
import assert from "node:assert/strict";

import {
  V5_BUDGET_SIGNAL_KINDS,
  V5_BUDGET_SIGNAL_SCHEMA_VERSION,
  V5_BUDGET_SIGNAL_TRIGGERS,
  V5_REPLAN_LEVERS,
  V5VarianceError,
  detectCostThresholdCrossings,
  detectQualificationReplan,
  v5CostVariancePreimage,
  v5CostVarianceProjection,
} from "../src/cost-variance-replan.v5.js";
import {
  V5_ANCESTOR_OVERDRAWN_DETAIL,
  V5_OVERDRAWN_REMEDIES,
  V5_SCOPE_TREE_SCHEMA_VERSION,
  assertLedgerConservation,
  cancelReservation,
  compileScopeTree,
  convertLiabilityToActual,
  convertReservationToActual,
  ledgerVersion,
  openLedger,
  openLedgerCell,
  postActual,
  recordEstimate,
  recordLateLiability,
  reserve,
  v5CostLedgerProjection,
} from "../src/hierarchical-cost-ledger.v5.js";

const AT = "2026-09-26T12:00:00.000Z";
const ROOT = "portfolio:v5";
const CHILD = "child:assurance";
const SLICE = "slice:a04";
const SIBLING = "slice:a05";

function treeSource(sliceCeiling = 300) {
  return {
    schema_version: V5_SCOPE_TREE_SCHEMA_VERSION,
    tree_id: "tree:a04-signals",
    tree_version: 1,
    nodes: [
      { node_id: ROOT, parent_node_id: null, scope_kind: "portfolio", authorization_ceiling_units: 5000 },
      { node_id: CHILD, parent_node_id: ROOT, scope_kind: "child", authorization_ceiling_units: 2000 },
      { node_id: SLICE, parent_node_id: CHILD, scope_kind: "slice", authorization_ceiling_units: sliceCeiling },
      { node_id: SIBLING, parent_node_id: CHILD, scope_kind: "slice", authorization_ceiling_units: 300 },
    ],
  };
}

function treeValue(sliceCeiling = 300) {
  return compileScopeTree(treeSource(sliceCeiling));
}

let counter = 0;
const nextId = prefix => `${prefix}:${++counter}`;

/**
 * A ledger whose two slices are each estimated at 100 units, so CHILD and ROOT
 * roll up to 200: a slice at 150 percent is its parent at 75 percent. With
 * `siblingEstimate: false` only SLICE carries an estimate.
 */
function estimated(sliceCeiling, { siblingEstimate = true } = {}) {
  let ledger = recordEstimate(openLedger(treeValue(sliceCeiling)), {
    operation_id: "op:estimate", node_id: SLICE, expected_total_cost_units: 100,
    basis_digest: "basis:a04", recorded_at: AT,
  }).ledger;
  if (siblingEstimate) {
    ledger = recordEstimate(ledger, {
      operation_id: "op:estimate-sibling", node_id: SIBLING, expected_total_cost_units: 100,
      basis_digest: "basis:a05", recorded_at: AT,
    }).ledger;
  }
  return ledger;
}

function actualOp(amount, nodeId = SLICE) {
  const id = nextId("actual");
  return { operation_id: `op:${id}`, node_id: nodeId, amount_units: amount,
    vendor_reference: `INV-${id.replace(":", "-")}`, incurred_at: AT };
}

/** Apply one operation and return the ledger, the outcome and what it fired. */
function step(ledger, apply, operation) {
  const result = apply(ledger, operation);
  const signals = detectCostThresholdCrossings({ before_ledger: ledger, after_ledger: result.ledger });
  return { ledger: result.ledger, outcome: result.outcome, signals };
}

const summary = signals => signals.map(s => `${s.node_id}:${s.kind}`).sort();

// --- the thresholds fire at the crossing, not on every read ----------------

test("FIRE: 149 fires nothing, 150 fires one warning, staying above fires nothing, 200 fires one replan", () => {
  let ledger = estimated();
  let fired = step(ledger, postActual, actualOp(149));
  assert.deepEqual(fired.signals, [], "149 percent has not reached the warning line");

  const before150 = fired.ledger;
  const crossingOp = actualOp(1);
  fired = step(before150, postActual, crossingOp);
  assert.deepEqual(summary(fired.signals), [`${SLICE}:warning`]);
  const [warning] = fired.signals;
  assert.equal(warning.schema_version, V5_BUDGET_SIGNAL_SCHEMA_VERSION);
  assert.equal(warning.trigger, "cost_reached_warning_threshold");
  assert.equal(warning.threshold_basis_points, 15000);
  assert.equal(warning.variance_basis_points_before, 14900);
  assert.equal(warning.variance_basis_points_after, 15000);
  assert.equal(warning.ledger_version, ledgerVersion(fired.ledger));
  assert.equal(warning.signal_id, `budget-signal:tree:a04-signals:t1:${SLICE}:warning:v${ledgerVersion(fired.ledger)}`);
  assert.equal(warning.operation_id, crossingOp.operation_id, "the signal names the operation that crossed");
  const scoped = detectCostThresholdCrossings({ before_ledger: before150, after_ledger: fired.ledger,
    scope_ref: "wr111:stored-tree" });
  assert.equal(scoped[0].signal_id, `budget-signal:wr111:stored-tree:${SLICE}:warning:v${ledgerVersion(fired.ledger)}`,
    "a stored ledger's tree_ref, not its tree_id, scopes the id");
  assert.match(warning.message, /^Cost warning: slice:a04 has now spent 150 units, 150\.00% of its 100-unit estimate/);
  assert.equal(warning.permitted_levers, undefined, "a warning pulls no replan lever");

  fired = step(fired.ledger, postActual, actualOp(49));
  assert.deepEqual(fired.signals, [], "199 percent is still inside the warning band: no second warning");

  fired = step(fired.ledger, postActual, actualOp(1));
  assert.deepEqual(summary(fired.signals), [`${SLICE}:replan`]);
  const [replan] = fired.signals;
  assert.equal(replan.trigger, "cost_reached_replan_threshold");
  assert.equal(replan.variance_basis_points_after, 20000);
  assert.deepEqual(replan.permitted_levers, [...V5_REPLAN_LEVERS]);
  assert.equal(replan.permits_unqualified_routing, false);
  assert.equal(replan.permits_quality_downgrade, false);
  assert.match(replan.message, /^Replan required: slice:a04 /);

  fired = step(fired.ledger, postActual, actualOp(60));
  assert.deepEqual(fired.signals, [], "260 percent fires nothing new: the replan already fired for this crossing");
  assertLedgerConservation(fired.ledger);
});

test("FIRE: one operation that jumps from under 150 to over 200 fires both, once each", () => {
  const fired = step(estimated(), postActual, actualOp(250));
  assert.deepEqual(summary(fired.signals), [`${SLICE}:replan`, `${SLICE}:warning`]);
  assert.equal(new Set(fired.signals.map(s => s.signal_id)).size, 2);
  assert.ok(fired.signals.every(s => s.variance_basis_points_after === 25000));
});

test("FIRE: a replay, a refusal and an outstanding reservation fire nothing", () => {
  const operation = actualOp(150);
  const first = step(estimated(), postActual, operation);
  assert.equal(first.signals.length, 1);

  // The same operation again is a retry: the ledger returns unchanged, the
  // version does not move, and nothing fires a second time.
  const replay = step(first.ledger, postActual, operation);
  assert.equal(replay.outcome.replayed, true);
  assert.equal(ledgerVersion(replay.ledger), ledgerVersion(first.ledger));
  assert.deepEqual(replay.signals, []);

  // A refused reservation advances the version but moves no money.
  const refused = step(first.ledger, reserve, {
    operation_id: "op:too-big", node_id: SLICE, reservation_id: "res:too-big",
    amount_units: 9999, requested_at: AT,
  });
  assert.equal(refused.outcome.accepted, false);
  assert.equal(ledgerVersion(refused.ledger), ledgerVersion(first.ledger) + 1);
  assert.deepEqual(refused.signals, []);

  // A reservation is an intention, not spend: the ratio reads incurred only.
  const reserved = step(estimated(), reserve, {
    operation_id: "op:big-intent", node_id: SLICE, reservation_id: "res:big-intent",
    amount_units: 300, requested_at: AT,
  });
  assert.equal(reserved.outcome.accepted, true);
  assert.deepEqual(reserved.signals, []);
});

test("FIRE: falling back below and crossing again is a new crossing with a new id", () => {
  // A 160-unit liability crosses 150. Settling it for 100 drops the node back
  // to 100 percent. A later 60-unit actual crosses 150 again.
  let fired = step(estimated(), recordLateLiability, {
    operation_id: "op:liab", node_id: SLICE, liability_id: "liab:1", amount_units: 160,
    vendor_reference: "INV-LIAB", incurred_at: AT,
  });
  assert.deepEqual(summary(fired.signals), [`${SLICE}:warning`]);
  const firstId = fired.signals[0].signal_id;

  fired = step(fired.ledger, convertLiabilityToActual, {
    operation_id: "op:settle", conversion_id: "conv:settle", liability_id: "liab:1",
    actual_amount_units: 100, vendor_reference: "INV-SETTLE", settled_at: AT,
  });
  assert.deepEqual(fired.signals, [], "a downward move fires nothing");

  fired = step(fired.ledger, postActual, actualOp(60));
  assert.deepEqual(summary(fired.signals), [`${SLICE}:warning`]);
  assert.notEqual(fired.signals[0].signal_id, firstId);
});

test("FIRE: a lowered estimate that puts incurred cost over the line is a crossing too", () => {
  let fired = step(estimated(), postActual, actualOp(120));
  assert.deepEqual(fired.signals, []);
  fired = step(fired.ledger, recordEstimate, {
    operation_id: "op:re-estimate", node_id: SLICE, expected_total_cost_units: 80,
    basis_digest: "basis:a04-v2", recorded_at: AT,
  });
  assert.deepEqual(summary(fired.signals), [`${SLICE}:warning`]);
  assert.equal(fired.signals[0].variance_basis_points_after, 15000);
});

test("FIRE: an ancestor fires on its own rolled-up crossing, and a node with no estimate never fires", () => {
  // CHILD and ROOT carry no estimate of their own; they roll up to 200. At
  // 300 units SLICE is at 300 percent and both ancestors at exactly 150.
  const fired = step(estimated(), postActual, actualOp(300));
  assert.deepEqual(summary(fired.signals),
    [`${CHILD}:warning`, `${ROOT}:warning`, `${SLICE}:replan`, `${SLICE}:warning`]);
  const below = step(estimated(), postActual, actualOp(299));
  assert.deepEqual(summary(below.signals), [`${SLICE}:replan`, `${SLICE}:warning`],
    "at 149.5 percent the ancestors have not crossed");

  // With only SLICE estimated, SIBLING has no denominator of its own and never
  // fires, while its ancestors measure against SLICE's estimate and do.
  const sibling = step(estimated(undefined, { siblingEstimate: false }), postActual,
    actualOp(10_000, SIBLING));
  assert.deepEqual(summary(sibling.signals), [`${CHILD}:replan`, `${CHILD}:warning`,
    `${ROOT}:replan`, `${ROOT}:warning`]);
});

test("FIRE: only adjacent ledgers of one lineage can be compared", () => {
  const base = estimated();
  const one = postActual(base, actualOp(10)).ledger;
  const two = postActual(one, actualOp(10)).ledger;
  const code = fn => { try { fn(); } catch (error) { return error instanceof V5VarianceError ? error.code : error; } return null; };

  assert.equal(code(() => detectCostThresholdCrossings({ before_ledger: base, after_ledger: two })),
    "ledger_versions_not_adjacent");
  assert.equal(code(() => detectCostThresholdCrossings({ before_ledger: one, after_ledger: base })),
    "ledger_versions_not_adjacent");
  const otherLineage = postActual(base, actualOp(10)).ledger;
  assert.equal(code(() => detectCostThresholdCrossings({ before_ledger: one, after_ledger: postActual(otherLineage, actualOp(1)).ledger })),
    "ledger_lineage_mismatch");
  const otherTree = openLedger(compileScopeTree({
    schema_version: V5_SCOPE_TREE_SCHEMA_VERSION, tree_id: "tree:other", tree_version: 1,
    nodes: [{ node_id: ROOT, parent_node_id: null, scope_kind: "portfolio", authorization_ceiling_units: 1 }],
  }));
  assert.equal(code(() => detectCostThresholdCrossings({ before_ledger: otherTree, after_ledger: base })),
    "ledger_lineage_mismatch");

  // Same nodes, but a different tree_id, or the same tree_id with a different
  // ceiling: each is a different scope tree, not one lineage.
  const next = postActual(one, actualOp(1)).ledger;
  const renamed = compileScopeTree({ ...treeSource(), tree_id: "tree:renamed" });
  assert.equal(code(() => detectCostThresholdCrossings({ before_ledger: one, after_ledger: { ...next, tree: renamed } })),
    "ledger_lineage_mismatch");
  assert.equal(code(() => detectCostThresholdCrossings({ before_ledger: one, after_ledger: { ...next, tree: treeValue(999) } })),
    "ledger_lineage_mismatch");

  // Identical entry logs that differ only in a REFUSED operation: a refusal
  // writes no entry, so only the applied index tells the lineages apart.
  const tooBig = id => ({ operation_id: `op:${id}`, node_id: SLICE, reservation_id: `res:${id}`,
    amount_units: 9999, requested_at: AT });
  const refusedX = reserve(base, tooBig("refused-x")).ledger;
  const refusedYThenActual = postActual(reserve(base, tooBig("refused-y")).ledger, actualOp(5)).ledger;
  assert.deepEqual(refusedX.entries, base.entries);
  assert.equal(code(() => detectCostThresholdCrossings({ before_ledger: refusedX, after_ledger: refusedYThenActual })),
    "ledger_lineage_mismatch");

  // Same applied operations, but a history entry was rewritten: an "after"
  // that lowered an earlier charge could otherwise manufacture or hide a
  // crossing while looking adjacent.
  const real = postActual(one, actualOp(200)).ledger;
  const doctored = { ...real, entries: real.entries.map((entry, index) =>
    index === 2 ? { ...entry, amount_units: entry.amount_units + 1 } : entry) };
  assert.equal(code(() => detectCostThresholdCrossings({ before_ledger: one, after_ledger: doctored })),
    "ledger_lineage_mismatch");
});

test("FIRE: the first estimate recorded for work already over the line fires at once", () => {
  // No estimate means no denominator and no band. The moment one is recorded
  // the work is measured, and if it is already at 200 percent both lines were
  // crossed by that measurement.
  let fired = step(openLedger(treeValue()), postActual, actualOp(200));
  assert.deepEqual(fired.signals, [], "unmeasurable work fires nothing");
  fired = step(fired.ledger, recordEstimate, {
    operation_id: "op:late-estimate", node_id: SLICE, expected_total_cost_units: 100,
    basis_digest: "basis:late", recorded_at: AT,
  });
  assert.deepEqual(summary(fired.signals).filter(s => s.startsWith(SLICE)),
    [`${SLICE}:replan`, `${SLICE}:warning`]);
  assert.equal(fired.signals.find(s => s.node_id === SLICE).variance_basis_points_before, null);
});

// --- qualification failure --------------------------------------------------

test("FIRE: qualification failure fires one replan on the transition, and only on it", () => {
  const ask = (before_state, after_state, observation_ref = "obs:1") => detectQualificationReplan({
    tree_id: "tree:a04-signals", node_id: SLICE, before_state, after_state, observation_ref,
  });
  const [fired] = ask("qualified", "qualification_failed");
  assert.equal(fired.kind, "replan");
  assert.equal(fired.trigger, "qualification_failure");
  assert.equal(fired.signal_id, `budget-signal:tree:a04-signals:${SLICE}:replan:qualification:obs:1`);
  assert.equal(fired.permits_unqualified_routing, false);
  assert.equal(fired.permits_quality_downgrade, false);
  assert.match(fired.message, /no matter how little has been spent/);
  assert.deepEqual(ask("qualified", "qualification_failed"), [fired], "the same observation derives the same id");

  assert.deepEqual(ask("qualification_failed", "qualification_failed"), [], "staying failed fires nothing");
  assert.deepEqual(ask("qualification_failed", "qualified"), []);
  assert.deepEqual(ask("qualified", "qualified"), []);
  assert.notEqual(ask("qualified", "qualification_failed", "obs:2")[0].signal_id, fired.signal_id,
    "recovering and failing again is a new failure");
  assert.throws(() => ask("qualified", "expired"), { code: "unknown_qualification_state" });
});

// --- concurrency: one base, two writers --------------------------------------

test("RACE: two actuals that each cross 150 from one base fire exactly one warning", () => {
  const base = postActual(estimated(), actualOp(130)).ledger;
  const cell = openLedgerCell(base);
  const a = cell.prepare({ kind: "post_actual", operation: actualOp(30) });
  const b = cell.prepare({ kind: "post_actual", operation: actualOp(30) });
  // Computed naively against the shared base, BOTH would claim the crossing.
  // That is the double-fire the cell exists to prevent.
  for (const prepared of [a, b]) {
    assert.deepEqual(summary(detectCostThresholdCrossings({ before_ledger: base, after_ledger: prepared.next_ledger })),
      [`${SLICE}:warning`]);
  }

  const fired = [];
  let before = cell.read();
  const landed = cell.commit(a);
  assert.equal(landed.committed, true);
  fired.push(...detectCostThresholdCrossings({ before_ledger: before, after_ledger: cell.read() }));

  const lost = cell.commit(b);
  assert.equal(lost.committed, false);
  assert.equal(lost.outcome.reason_id, "version_conflict");

  before = cell.read();
  const retried = cell.commit(cell.recompute(b));
  assert.equal(retried.committed, true);
  assert.equal(retried.outcome.accepted, true);
  fired.push(...detectCostThresholdCrossings({ before_ledger: before, after_ledger: cell.read() }));

  assert.deepEqual(summary(fired), [`${SLICE}:warning`], "190 percent: one crossing, one warning");
  assertLedgerConservation(cell.read());
});

test("RACE: two reservations that together pass the ceiling — one lands, the recomputed loser is refused", () => {
  const cell = openLedgerCell(estimated(100));
  const reserveStep = id => ({ kind: "reserve", operation: {
    operation_id: `op:${id}`, node_id: SLICE, reservation_id: `res:${id}`, amount_units: 60, requested_at: AT,
  } });
  const a = cell.prepare(reserveStep("race-a"));
  const b = cell.prepare(reserveStep("race-b"));
  assert.equal(a.outcome.accepted, true);
  assert.equal(b.outcome.accepted, true, "each fits the base on its own");

  assert.equal(cell.commit(a).committed, true);
  assert.equal(cell.commit(b).outcome.reason_id, "version_conflict");
  const recomputed = cell.recompute(b);
  assert.equal(recomputed.outcome.accepted, false);
  assert.equal(recomputed.outcome.reason_id, "ceiling_exceeded");
  assert.equal(recomputed.outcome.available_units, 40);
  assert.equal(cell.commit(recomputed).committed, true, "the refusal itself is recorded");
  assert.deepEqual(Object.keys(cell.read().open_reservations), ["res:race-a"]);
  assertLedgerConservation(cell.read());
});

test("RACE: a reservation racing an overdrawing actual is refused ancestor_overdrawn once recomputed", () => {
  const cell = openLedgerCell(estimated());
  const overdraw = cell.prepare({ kind: "post_actual", operation: actualOp(450) });
  const sibling = cell.prepare({ kind: "reserve", operation: {
    operation_id: "op:sibling", node_id: SIBLING, reservation_id: "res:sibling",
    amount_units: 10, requested_at: AT,
  } });
  assert.equal(sibling.outcome.accepted, true, "against the stale base it looked fine");

  assert.equal(cell.commit(overdraw).committed, true);
  assert.equal(cell.commit(sibling).outcome.reason_id, "version_conflict");
  const recomputed = cell.recompute(sibling);
  assert.equal(recomputed.outcome.reason_id, "ancestor_overdrawn");
  assert.deepEqual(recomputed.outcome.overdrawn_node_ids, [CHILD, ROOT]);
  assert.deepEqual(recomputed.outcome.overdrawn_caused_by_node_ids, [SLICE],
    "SIBLING, CHILD and ROOT are all within their own ceilings; SLICE is the budget actually over");
  assert.equal(recomputed.outcome.detail, V5_ANCESTOR_OVERDRAWN_DETAIL);
});

// --- overdrawn ancestors deny new reservations; existing ones are untouched --

test("OVERDRAWN: a refused reservation leaves every existing reservation as it was, and they still work", () => {
  let ledger = estimated();
  ({ ledger } = reserve(ledger, { operation_id: "op:keep-a", node_id: SLICE,
    reservation_id: "res:keep-a", amount_units: 100, requested_at: AT }));
  ({ ledger } = reserve(ledger, { operation_id: "op:keep-b", node_id: SIBLING,
    reservation_id: "res:keep-b", amount_units: 50, requested_at: AT }));
  ({ ledger } = postActual(ledger, actualOp(450)));

  const before = ledger;
  for (const [nodeId, id] of [[SLICE, "self"], [SIBLING, "sibling"], [CHILD, "parent"], [ROOT, "root"]]) {
    const denied = reserve(ledger, { operation_id: `op:new-${id}`, node_id: nodeId,
      reservation_id: `res:new-${id}`, amount_units: 1, requested_at: AT });
    assert.equal(denied.outcome.accepted, false, `${nodeId} must be denied`);
    assert.equal(denied.outcome.reason_id, "ancestor_overdrawn");
    assert.deepEqual(denied.outcome.requires, [...V5_OVERDRAWN_REMEDIES]);
    assert.equal(denied.outcome.detail, V5_ANCESTOR_OVERDRAWN_DETAIL);
    assert.equal(denied.outcome.available_units, undefined);
    assert.deepEqual(denied.outcome.overdrawn_caused_by_node_ids, [SLICE]);
    ledger = denied.ledger;
  }
  assert.deepEqual(ledger.open_reservations, before.open_reservations);
  assert.deepEqual(ledger.closed_reservations, before.closed_reservations);
  assert.equal(ledger.entries, before.entries, "a refusal appends no entry at all");

  const cancelled = cancelReservation(ledger, { operation_id: "op:cancel-b",
    reservation_id: "res:keep-b", cancelled_at: AT });
  assert.equal(cancelled.outcome.accepted, true, "an existing reservation can still be cancelled");
  const converted = convertReservationToActual(cancelled.ledger, { operation_id: "op:convert-a",
    conversion_id: "conv:a", reservation_id: "res:keep-a", actual_amount_units: 100,
    vendor_reference: "INV-CONVERT-A", settled_at: AT });
  assert.equal(converted.outcome.accepted, true, "an existing reservation can still be converted");
  assertLedgerConservation(converted.ledger);
});

test("OVERDRAWN: the refusal text names what is denied, what is untouched and the three ways forward", () => {
  assert.match(V5_ANCESTOR_OVERDRAWN_DETAIL, /^New reservation refused/);
  assert.match(V5_ANCESTOR_OVERDRAWN_DETAIL, /Nothing already reserved is changed/);
  assert.match(V5_ANCESTOR_OVERDRAWN_DETAIL, /or some budget inside it has committed more than its authorized ceiling/,
    "a sibling's breach marks the shared parent, so the text must not claim the parent itself is over");
  assert.match(V5_ANCESTOR_OVERDRAWN_DETAIL, /open an incident, replan the work, or get the ceiling explicitly raised/);
  assert.equal(v5CostLedgerProjection().overdrawn_refusal_touches_existing_reservations, false);
});

// --- the projection ----------------------------------------------------------

test("PROJECTION: the signal vocabulary is hashed in and the durable gap is named", () => {
  const text = JSON.stringify(v5CostVariancePreimage());
  for (const name of [...V5_BUDGET_SIGNAL_KINDS, ...V5_BUDGET_SIGNAL_TRIGGERS, V5_BUDGET_SIGNAL_SCHEMA_VERSION]) {
    assert.ok(text.includes(name), `${name} is not in the hashed preimage`);
  }
  const projection = v5CostVarianceProjection();
  assert.equal(projection.signal_fires_once_per_upward_crossing, true);
  assert.equal(projection.signal_fires_on_replay_or_refusal, false);
  assert.ok(projection.unimplemented_dependencies.some(gap => gap.includes("durable signal record")));
});

test("PROJECTION: a fired signal cannot be mutated by its caller", () => {
  const { signals } = step(estimated(), postActual, actualOp(250));
  assert.throws(() => { signals.push({}); }, TypeError);
  assert.throws(() => { signals[0].permits_quality_downgrade = true; }, TypeError);
});
