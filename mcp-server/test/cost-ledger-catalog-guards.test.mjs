// V5-A04 — the catalog's three shipped-release acceptance clauses, expressed
// against the existing public seams.  These tests are deliberately thin: the
// detailed fixture suites remain the authority for each module, while this
// file makes the slice-to-behaviour mapping executable and attributable.

import test from "node:test";
import assert from "node:assert/strict";

import {
  assessVariance,
  evaluateReplan,
} from "../src/cost-variance-replan.v5.js";
import {
  V5_OVERDRAWN_REMEDIES,
  V5_SCOPE_TREE_SCHEMA_VERSION,
  assertLedgerConservation,
  cancelReservation,
  compileScopeTree,
  convertReservationToActual,
  openLedger,
  openLedgerCell,
  postActual,
  projectLedger,
  reserve,
} from "../src/hierarchical-cost-ledger.v5.js";

const AT = "2026-09-25T12:00:00.000Z";
const ROOT = "portfolio:doctorcre-v5";
const CHILD = "child:assurance";
const SLICE = "slice:a04";
const SIBLING = "slice:a05";

function fresh() {
  return openLedger(compileScopeTree({
    schema_version: V5_SCOPE_TREE_SCHEMA_VERSION,
    tree_id: "tree:v5-a04-acceptance",
    tree_version: 1,
    nodes: [
      { node_id: ROOT, parent_node_id: null, scope_kind: "portfolio", authorization_ceiling_units: 1000 },
      { node_id: CHILD, parent_node_id: ROOT, scope_kind: "child", authorization_ceiling_units: 600 },
      { node_id: SLICE, parent_node_id: CHILD, scope_kind: "slice", authorization_ceiling_units: 300 },
      { node_id: SIBLING, parent_node_id: CHILD, scope_kind: "slice", authorization_ceiling_units: 300 },
    ],
  }));
}

function reservation(id, nodeId = SLICE, amount = 100) {
  return {
    operation_id: `op:${id}`,
    node_id: nodeId,
    reservation_id: `res:${id}`,
    amount_units: amount,
    requested_at: AT,
  };
}

function reserveStep(id, amount) {
  return { kind: "reserve", operation: reservation(id, SLICE, amount) };
}

test("V5-A04 acceptance: 150% warning and 200%/qualification-failure replan fire", () => {
  const directive = (incurred, qualification_state = "qualified") => evaluateReplan({
    assessment: assessVariance({ expected_total_cost_units: 100, incurred_units: incurred }),
    qualification_state,
  });

  assert.equal(directive(149).directive, "continue");
  assert.equal(directive(150).directive, "warn");
  assert.equal(directive(199).directive, "warn");
  assert.equal(directive(200).directive, "replan");
  assert.deepEqual(directive(1, "qualification_failed").triggers, ["qualification_failure"]);
});

test("V5-A04 acceptance: qualification failure alone forces replan at negligible spend", () => {
  const directive = evaluateReplan({
    assessment: assessVariance({ expected_total_cost_units: 100, incurred_units: 1 }),
    qualification_state: "qualification_failed",
  });

  // A route that lost its qualification replans at one percent of budget just
  // as it does at two hundred: the trigger list alone does not pin this down,
  // because a directive computed some other way could still land on "replan"
  // by coincidence at this ratio. The directive itself is the acceptance
  // clause, so it is what this test asserts.
  assert.equal(directive.directive, "replan");
});

test("V5-A04 acceptance: qualification failure outranks a merely-warning cost ratio", () => {
  const directive = evaluateReplan({
    assessment: assessVariance({ expected_total_cost_units: 100, incurred_units: 150 }),
    qualification_state: "qualification_failed",
  });

  // At exactly 150 percent the cost ratio alone would only warn. Qualification
  // failure must still win outright and replan, not settle for whichever of
  // the two triggers looks the more severe by cost band.
  assert.equal(directive.directive, "replan");
});

test("V5-A04 acceptance: the 150/200 floor boundaries hold at a non-round estimate", () => {
  const directive = (incurred) => evaluateReplan({
    assessment: assessVariance({ expected_total_cost_units: 10001, incurred_units: incurred }),
    qualification_state: "qualified",
  });

  // 20001 / 10001 is 199.99...%: short of 200 percent, so it must warn, not
  // replan. 15001 / 10001 is 149.99...%: short of 150 percent, so it must
  // continue, not warn. A ratio computed with Math.ceil instead of Math.floor
  // rounds both of these up into the next band; this is the boundary that
  // catches it where round numbers (100/149, 100/150) cannot.
  assert.equal(directive(20001).directive, "warn");
  assert.equal(directive(15001).directive, "continue");
});

test("V5-A04 acceptance: a node exactly at its ceiling is not overdrawn", () => {
  const atCeiling = reserve(fresh(), reservation("at-ceiling", SLICE, 300));
  assert.equal(atCeiling.outcome.accepted, true);

  const projection = projectLedger(atCeiling.ledger);
  assert.equal(projection.by_node[SLICE].breaches_own_ceiling, false);
  assert.equal(projection.by_node[SLICE].overdrawn, false);
  assert.equal(projection.by_node[CHILD].overdrawn, false);
  assert.equal(projection.by_node[ROOT].overdrawn, false);

  // Sitting exactly at the ceiling must not poison a sibling's reservations:
  // if it did, that would mean "at" was silently treated as "past".
  const siblingReserve = reserve(atCeiling.ledger, reservation("sibling-at-ceiling", SIBLING, 1));
  assert.equal(siblingReserve.outcome.accepted, true);
  assertLedgerConservation(siblingReserve.ledger);
});

test("V5-A04 acceptance: overdrawn ancestors deny new reservation", () => {
  const charged = postActual(fresh(), {
    operation_id: "op:late",
    node_id: SLICE,
    amount_units: 450,
    vendor_reference: "V5-A04-LATE",
    incurred_at: AT,
  });
  const denied = reserve(charged.ledger, reservation("sibling", SIBLING, 1));

  assert.equal(charged.outcome.accepted, true, "truthful actuals are never clipped by a ceiling");
  assert.equal(denied.outcome.accepted, false);
  assert.equal(denied.outcome.reason_id, "ancestor_overdrawn");
  assert.deepEqual(denied.outcome.overdrawn_node_ids, [CHILD, ROOT]);
  assert.deepEqual(denied.outcome.requires, [...V5_OVERDRAWN_REMEDIES]);
  assertLedgerConservation(denied.ledger);
});

test("V5-A04 acceptance: race/cancel/retry/vendor/late-actual/conversion conservation fixtures pass", () => {
  // Race: two callers compute from one base; the cell admits one commit.
  const cell = openLedgerCell(fresh());
  const first = cell.prepare(reserveStep("race-a", 200));
  const second = cell.prepare(reserveStep("race-b", 200));
  assert.equal(first.outcome.accepted, true);
  assert.equal(second.outcome.accepted, true);
  assert.equal(cell.commit(first).committed, true);
  const conflict = cell.commit(second);
  assert.equal(conflict.committed, false);
  assert.equal(conflict.outcome.reason_id, "version_conflict");
  assertLedgerConservation(cell.read());

  // Cancel and retry: cancellation releases the intention, while replaying an
  // operation appends nothing and returns the original outcome.
  let ledger = fresh();
  const initial = reserve(ledger, reservation("cancel"));
  const replay = reserve(initial.ledger, reservation("cancel"));
  assert.equal(replay.outcome.replayed, true);
  assert.equal(replay.ledger.entries.length, 1);
  ({ ledger } = cancelReservation(replay.ledger, {
    operation_id: "op:cancel-release",
    reservation_id: "res:cancel",
    cancelled_at: AT,
  }));
  assert.equal(projectLedger(ledger).by_node[SLICE].rolled_up.committed_units, 0);
  assertLedgerConservation(ledger);

  // Vendor and late actual: a duplicate reference is refused, while a real
  // over-ceiling charge is recorded in full and conserved through ancestors.
  ({ ledger } = postActual(ledger, {
    operation_id: "op:vendor-a",
    node_id: SLICE,
    amount_units: 40,
    vendor_reference: "V5-A04-VENDOR",
    incurred_at: AT,
  }));
  const duplicate = postActual(ledger, {
    operation_id: "op:vendor-b",
    node_id: SLICE,
    amount_units: 40,
    vendor_reference: "V5-A04-VENDOR",
    incurred_at: AT,
  });
  assert.equal(duplicate.outcome.reason_id, "duplicate_vendor_charge");
  ({ ledger } = postActual(duplicate.ledger, {
    operation_id: "op:late",
    node_id: SLICE,
    amount_units: 450,
    vendor_reference: "V5-A04-LATE-SECOND",
    incurred_at: AT,
  }));
  assert.equal(projectLedger(ledger).by_node[ROOT].rolled_up.actual_units, 490);
  assertLedgerConservation(ledger);

  // Conversion: one operation releases one reservation and books one actual;
  // a second conversion cannot count the same intention twice.
  let conversionLedger = reserve(fresh(), reservation("convert")).ledger;
  ({ ledger: conversionLedger } = convertReservationToActual(conversionLedger, {
    operation_id: "op:convert-a",
    conversion_id: "conv:a",
    reservation_id: "res:convert",
    actual_amount_units: 90,
    vendor_reference: "V5-A04-CONVERT-A",
    settled_at: AT,
  }));
  const secondConversion = convertReservationToActual(conversionLedger, {
    operation_id: "op:convert-b",
    conversion_id: "conv:b",
    reservation_id: "res:convert",
    actual_amount_units: 90,
    vendor_reference: "V5-A04-CONVERT-B",
    settled_at: AT,
  });
  assert.equal(secondConversion.outcome.reason_id, "reservation_not_open");
  assert.equal(projectLedger(secondConversion.ledger).by_node[SLICE].rolled_up.actual_units, 90);
  assertLedgerConservation(secondConversion.ledger);
});
