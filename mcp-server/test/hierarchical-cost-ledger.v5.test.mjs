// V5-A04 — the hierarchical cost ledger: reservation, cancellation, late
// liability, conversion and truthful actuals across slice, child and portfolio.
//
// The suite is organised around the six conservation fixtures the slice has to
// pass — race, cancel, retry, vendor, late actual and conversion — followed by
// the ceiling/overdrawn behaviour and the contract violations that must THROW.
//
// ORDER IS NOT A RACE, and the two are separated below on purpose. An ORDER
// fixture applies operations one after another to prove the arithmetic cannot
// admit more than the ceiling allows however the sequence falls; that is a
// property of a SINGLE writer and every ORDER test below is labelled as one.
// A RACE fixture is two callers who each computed against the SAME base and
// then both try to commit, which is the case an ordering cannot express: the
// RACE tests drive the compare-and-swap boundary, prove exactly one commit
// lands, and prove the loser's recomputation meets the refusal it had been
// about to talk its way past.
// Every accepted operation re-checks the conservation law internally, so a
// fixture that passes has proved conservation at every intermediate step and
// not merely at the end; the explicit `assertLedgerConservation` calls below
// are there so a test can assert against the NUMBERS, not only against the
// absence of a throw.
//
// EVERY AMOUNT, VENDOR REFERENCE AND CEILING BELOW IS A SYNTHETIC FIXTURE. No
// money moved, no provider was called, no charge was settled and nothing here
// could reach an external payment rail.

import test from "node:test";
import assert from "node:assert/strict";

import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  V5_SCOPE_TREE_SCHEMA_VERSION,
  V5_LEDGER_ENTRY_KINDS,
  V5_LEDGER_REFUSAL_REASONS,
  V5_OVERDRAWN_REMEDIES,
  V5_LEDGER_OPERATION_KINDS,
  V5CostLedgerError,
  compileScopeTree,
  openLedger,
  recordEstimate,
  reserve,
  cancelReservation,
  recordLateLiability,
  postActual,
  convertReservationToActual,
  convertLiabilityToActual,
  applyOperations,
  ledgerVersion,
  ledgerStateDigest,
  prepareCommit,
  openLedgerCell,
  projectLedger,
  assertLedgerConservation,
  v5CostLedgerPreimage,
  v5CostLedgerProjection,
} from "../src/hierarchical-cost-ledger.v5.js";
import * as ledgerModule from "../src/hierarchical-cost-ledger.v5.js";

function refuses(fn, code) {
  try {
    fn();
  } catch (error) {
    assert.ok(error instanceof V5CostLedgerError,
      `expected a V5CostLedgerError, got ${error?.name}: ${error?.message}`);
    assert.equal(error.code, code, `expected code "${code}", got "${error.code}" (${error.message})`);
    return error;
  }
  return assert.fail(`expected a refusal with code "${code}"`);
}

const AT = "2026-09-11T12:00:00.000Z";

// --- the fixture hierarchy -------------------------------------------------
//
//   portfolio:doctorcre-v5          ceiling 1000
//     child:assurance-fabric        ceiling  600
//       slice:a04                   ceiling  300
//       slice:a05                   ceiling  300
//     child:product-journey         ceiling  500
//       slice:j201                  ceiling  200

function tree(overrides = {}) {
  const ceilings = {
    portfolio: 1000, assurance: 600, product: 500, a04: 300, a05: 300, j201: 200,
    ...overrides,
  };
  return compileScopeTree({
    schema_version: V5_SCOPE_TREE_SCHEMA_VERSION,
    tree_id: "tree:a04-fixture",
    tree_version: 1,
    nodes: [
      {
        node_id: "portfolio:doctorcre-v5", parent_node_id: null,
        scope_kind: "portfolio", authorization_ceiling_units: ceilings.portfolio,
      },
      {
        node_id: "child:assurance-fabric", parent_node_id: "portfolio:doctorcre-v5",
        scope_kind: "child", authorization_ceiling_units: ceilings.assurance,
      },
      {
        node_id: "child:product-journey", parent_node_id: "portfolio:doctorcre-v5",
        scope_kind: "child", authorization_ceiling_units: ceilings.product,
      },
      {
        node_id: "slice:a04", parent_node_id: "child:assurance-fabric",
        scope_kind: "slice", authorization_ceiling_units: ceilings.a04,
      },
      {
        node_id: "slice:a05", parent_node_id: "child:assurance-fabric",
        scope_kind: "slice", authorization_ceiling_units: ceilings.a05,
      },
      {
        node_id: "slice:j201", parent_node_id: "child:product-journey",
        scope_kind: "slice", authorization_ceiling_units: ceilings.j201,
      },
    ],
  });
}

const PORTFOLIO = "portfolio:doctorcre-v5";
const ASSURANCE = "child:assurance-fabric";
const PRODUCT = "child:product-journey";
const A04 = "slice:a04";
const A05 = "slice:a05";
const J201 = "slice:j201";

function fresh(overrides) {
  return openLedger(tree(overrides));
}

function node(ledger, nodeId) {
  return projectLedger(ledger).by_node[nodeId];
}

function reserveOp(id, nodeId, amount) {
  return {
    operation_id: `op:${id}`, node_id: nodeId, reservation_id: `res:${id}`,
    amount_units: amount, requested_at: AT,
  };
}

// --- the scope tree --------------------------------------------------------

test("TREE: a compiled tree carries ancestors self-first up to the portfolio", () => {
  const compiled = tree();
  assert.deepEqual(compiled.ancestors[A04], [A04, ASSURANCE, PORTFOLIO]);
  assert.deepEqual(compiled.ancestors[PORTFOLIO], [PORTFOLIO]);
  assert.deepEqual(compiled.children[ASSURANCE], [A04, A05]);
  assert.equal(compiled.root_node_id, PORTFOLIO);
});

test("TREE: the effective ceiling is the tightest one on the chain", () => {
  const compiled = tree({ a04: 5000 });
  assert.equal(compiled.nodes[A04].authorization_ceiling_units, 5000);
  assert.equal(compiled.effective_ceiling_units[A04], 600,
    "a slice authorised for more than its child is still bound by the child");
});

test("TREE: a slice may not hang from a portfolio, and a child may not hang from a slice", () => {
  refuses(() => compileScopeTree({
    schema_version: V5_SCOPE_TREE_SCHEMA_VERSION,
    tree_id: "tree:flat", tree_version: 1,
    nodes: [
      { node_id: "p:one", parent_node_id: null, scope_kind: "portfolio", authorization_ceiling_units: 10 },
      { node_id: "s:one", parent_node_id: "p:one", scope_kind: "slice", authorization_ceiling_units: 5 },
    ],
  }), "invalid_scope_tree");
});

test("TREE: two roots, no root, or a missing parent are all refused", () => {
  const twoRoots = {
    schema_version: V5_SCOPE_TREE_SCHEMA_VERSION, tree_id: "tree:two", tree_version: 1,
    nodes: [
      { node_id: "p:one", parent_node_id: null, scope_kind: "portfolio", authorization_ceiling_units: 10 },
      { node_id: "p:two", parent_node_id: null, scope_kind: "portfolio", authorization_ceiling_units: 10 },
    ],
  };
  refuses(() => compileScopeTree(twoRoots), "invalid_scope_tree");
  refuses(() => compileScopeTree({
    schema_version: V5_SCOPE_TREE_SCHEMA_VERSION, tree_id: "tree:orphan", tree_version: 1,
    nodes: [
      { node_id: "p:one", parent_node_id: null, scope_kind: "portfolio", authorization_ceiling_units: 10 },
      { node_id: "c:one", parent_node_id: "p:missing", scope_kind: "child", authorization_ceiling_units: 5 },
    ],
  }), "unknown_node");
});

test("TREE: an unknown node on an operation throws rather than being refused", () => {
  refuses(() => reserve(fresh(), reserveOp("x", "slice:not-in-tree", 10)), "unknown_node");
});

// --- the plain path --------------------------------------------------------

test("LEDGER: a reservation is committed but not incurred", () => {
  const { ledger } = reserve(fresh(), reserveOp("a", A04, 100));
  const a04 = node(ledger, A04);
  assert.equal(a04.rolled_up.reservation_outstanding_units, 100);
  assert.equal(a04.rolled_up.committed_units, 100);
  assert.equal(a04.rolled_up.incurred_units, 0, "a reservation is an intention, not a charge");
  assert.equal(a04.rolled_up.actual_units, 0);
});

test("LEDGER: an actual posted on a slice rolls up through every ancestor", () => {
  const { ledger } = postActual(fresh(), {
    operation_id: "op:a", node_id: A04, amount_units: 70,
    vendor_reference: "INV-2026-0001", incurred_at: AT,
  });
  const projection = projectLedger(ledger);
  for (const id of [A04, ASSURANCE, PORTFOLIO]) {
    assert.equal(projection.by_node[id].rolled_up.actual_units, 70, `${id} did not see the charge`);
  }
  assert.equal(projection.by_node[A05].rolled_up.actual_units, 0, "a sibling saw another slice's charge");
  assert.equal(projection.by_node[PRODUCT].rolled_up.actual_units, 0);
  assert.equal(projection.by_node[A04].own.actual_units, 70);
  assert.equal(projection.by_node[PORTFOLIO].own.actual_units, 0,
    "the portfolio holds nothing of its own; its total is derived");
});

test("LEDGER: an estimate is a restatement, not an accrual", () => {
  let ledger = fresh();
  ({ ledger } = recordEstimate(ledger, {
    operation_id: "op:e1", node_id: A04, expected_total_cost_units: 300,
    basis_digest: "sha256:aa", recorded_at: AT,
  }));
  ({ ledger } = recordEstimate(ledger, {
    operation_id: "op:e2", node_id: A04, expected_total_cost_units: 400,
    basis_digest: "sha256:bb", recorded_at: AT,
  }));
  assert.equal(node(ledger, A04).rolled_up.estimate_units, 400,
    "re-estimating must replace, not add");
  assert.equal(node(ledger, ASSURANCE).rolled_up.estimate_units, 400);
});

test("LEDGER: an estimate commits nothing, so no ceiling can refuse it", () => {
  const { outcome } = recordEstimate(fresh(), {
    operation_id: "op:e", node_id: A04, expected_total_cost_units: 99999,
    basis_digest: "sha256:aa", recorded_at: AT,
  });
  assert.equal(outcome.accepted, true);
  assert.equal(outcome.reason_id, "estimate_recorded");
});

// --- fixture 1a: order — the single-writer half ----------------------------

test("ORDER: applied one after another, two reservations for one headroom admit exactly one", () => {
  // slice:a04 ceiling 300. Two reservations of 200 cannot both fit.
  const forward = applyOperations(fresh(), [
    { kind: "reserve", operation: reserveOp("first", A04, 200) },
    { kind: "reserve", operation: reserveOp("second", A04, 200) },
  ]);
  const backward = applyOperations(fresh(), [
    { kind: "reserve", operation: reserveOp("second", A04, 200) },
    { kind: "reserve", operation: reserveOp("first", A04, 200) },
  ]);
  for (const run of [forward, backward]) {
    const accepted = run.outcomes.filter(outcome => outcome.accepted);
    assert.equal(accepted.length, 1, "exactly one reservation must win the race");
    const refused = run.outcomes.find(outcome => !outcome.accepted);
    assert.equal(refused.reason_id, "ceiling_exceeded");
    assert.equal(refused.binding_node_id, A04);
    assert.equal(refused.available_units, 100);
    assert.equal(node(run.ledger, A04).rolled_up.committed_units, 200);
    assert.ok(node(run.ledger, A04).rolled_up.committed_units <= 300);
    assertLedgerConservation(run.ledger);
  }
  // WHICH reservation wins differs by order, and the conserved total does not.
  // Compared by identity, not by position: the accepted one is always the first
  // APPLIED, so comparing indexes would pass on any implementation at all. This
  // is an ordering property and NOT a concurrency one — two callers who both
  // computed against the empty ledger are the fixture below, not this one.
  const winner = run => run.outcomes.find(outcome => outcome.accepted).reservation_id;
  assert.equal(winner(forward), "res:first");
  assert.equal(winner(backward), "res:second");
  assert.notEqual(winner(forward), winner(backward));
});

test("ORDER: sibling reservations are settled by the shared child ceiling", () => {
  // child:assurance-fabric ceiling 600, each slice ceiling 300. Two 300-unit
  // reservations fit; a third anywhere under the child does not.
  const run = applyOperations(fresh(), [
    { kind: "reserve", operation: reserveOp("one", A04, 300) },
    { kind: "reserve", operation: reserveOp("two", A05, 300) },
    { kind: "reserve", operation: reserveOp("three", A05, 1) },
  ]);
  assert.deepEqual(run.outcomes.map(outcome => outcome.accepted), [true, true, false]);
  assert.equal(run.outcomes[2].reason_id, "ceiling_exceeded");
  assert.equal(run.outcomes[2].binding_node_id, A05,
    "the tightest binding scope is named, not the outermost");
  assert.equal(node(run.ledger, ASSURANCE).rolled_up.committed_units, 600);
  assertLedgerConservation(run.ledger);
});

test("ORDER: the portfolio ceiling binds even when every slice and child has room", () => {
  const compiled = tree({ portfolio: 400 });
  const run = applyOperations(openLedger(compiled), [
    { kind: "reserve", operation: reserveOp("one", A04, 300) },
    { kind: "reserve", operation: reserveOp("two", J201, 200) },
  ]);
  assert.deepEqual(run.outcomes.map(outcome => outcome.accepted), [true, false]);
  assert.equal(run.outcomes[1].binding_node_id, PORTFOLIO);
  assert.equal(run.outcomes[1].authorization_ceiling_units, 400);
  assert.equal(run.outcomes[1].available_units, 100);
  assertLedgerConservation(run.ledger);
});

// --- fixture 1b: the race — two callers, one base, one cell -----------------
//
// THE CASE AN ORDERING CANNOT EXPRESS. Above, each operation saw the ledger the
// one before it produced. Here both callers read the SAME ledger, each computes
// a complete answer against it, and only then do they try to commit. Both
// computations say yes — that is not a bug, it is what a stale read looks like
// — and the whole question is whether the LEDGER lets both of them land.
//
// EVERY COMMIT BELOW IS OFFERED TO THE SAME CELL, and that is the point of the
// fixture rather than an incidental detail. A fixture that handed the winner's
// ledger to the second commit itself would be doing the ledger's job for it:
// the serialization would live in the test, and an implementation with no
// admission point of its own would pass. The cell owns the value; a caller can
// only offer.

function reserveStep(id, nodeId, amount) {
  return { kind: "reserve", operation: reserveOp(id, nodeId, amount) };
}

function convertStep(id, reservationId, amount) {
  return {
    kind: "convert_reservation_to_actual",
    operation: {
      operation_id: `op:conv-${id}`, conversion_id: `conv:${id}`,
      reservation_id: reservationId, actual_amount_units: amount,
      vendor_reference: `INV-${id.toUpperCase()}`, settled_at: AT,
    },
  };
}

test("RACE: two reservations computed from ONE base — exactly one commit lands", () => {
  // slice:a04 ceiling 300. Two reservations of 200 cannot both fit, and both
  // callers are about to be told, correctly and uselessly, that theirs does.
  const cell = openLedgerCell(fresh());
  const base = cell.read();
  const first = cell.prepare(reserveStep("first", A04, 200));
  const second = cell.prepare(reserveStep("second", A04, 200));

  assert.equal(first.outcome.accepted, true);
  assert.equal(second.outcome.accepted, true,
    "each caller, computing against the empty ledger, is told yes — which is exactly why a computed acceptance is not a commit");
  assert.equal(first.base_version, 0);
  assert.equal(second.base_version, first.base_version);
  assert.equal(second.base_state_digest, first.base_state_digest,
    "the two callers really did compute against the same base");
  assert.equal(cell.version(), 0, "preparing installs nothing");
  assert.equal(cell.read(), base, "the cell is still standing on the base, by identity");
  assert.deepEqual(projectLedger(base).open_reservation_ids, [],
    "the base a commit was computed from is untouched by computing it");

  // Both are now offered to THE SAME CELL. Neither caller passes a ledger and
  // neither caller can: the cell holds the only one there is.
  const landed = cell.commit(first);
  const lost = cell.commit(second);

  assert.equal(landed.committed, true);
  assert.equal(lost.committed, false,
    "the second commit was computed from a base the cell has already left");
  assert.equal([landed, lost].filter(result => result.committed).length, 1,
    "exactly one of two commits from one base may land");
  assert.equal(lost.outcome.reason_id, "version_conflict");
  assert.equal(lost.outcome.accepted, false);
  assert.equal(lost.outcome.base_version, 0);
  assert.equal(lost.outcome.current_version, 1);
  assert.equal(lost.outcome.operation_id, "op:second");
  assert.equal(lost.ledger, landed.ledger, "a conflicted commit installs nothing at all");
  assert.equal(cell.read(), landed.ledger, "and the cell still holds what the winner installed");
  assert.deepEqual(projectLedger(cell.read()).open_reservation_ids, ["res:first"]);
  assertLedgerConservation(cell.read());

  // The loser's only honest move: compute the same step again, against what
  // actually committed. The answer is different, and the difference is the
  // point — this is the acceptance the serial fixture could never withdraw.
  const retried = cell.recompute(second);
  assert.equal(retried.base_version, 1);
  assert.equal(retried.outcome.accepted, false);
  assert.equal(retried.outcome.reason_id, "ceiling_exceeded");
  assert.equal(retried.outcome.binding_node_id, A04);
  assert.equal(retried.outcome.available_units, 100);

  const settled = cell.commit(retried);
  assert.equal(settled.committed, true,
    "a refusal is a decision the ledger made and it commits like any other");
  assert.deepEqual(projectLedger(cell.read()).open_reservation_ids, ["res:first"]);
  assert.equal(node(cell.read(), A04).rolled_up.committed_units, 200);
  assert.ok(node(cell.read(), A04).rolled_up.committed_units <= 300);
  assert.equal(cell.version(), 2,
    "two operations were applied: one acceptance and one refusal");
  assertLedgerConservation(cell.read());
});

test("RACE: whichever of the two reservations is offered first, the other loses the same way", () => {
  // Symmetry, and it is not cosmetic: an implementation that privileged the
  // earlier-prepared commit rather than the earlier-COMMITTED one would pass
  // the fixture above and fail this one.
  for (const [winnerId, loserId] of [["first", "second"], ["second", "first"]]) {
    const cell = openLedgerCell(fresh());
    const winner = cell.prepare(reserveStep(winnerId, A04, 200));
    const loser = cell.prepare(reserveStep(loserId, A04, 200));

    const landed = cell.commit(winner);
    const conflicted = cell.commit(loser);
    assert.equal(landed.committed, true, `${winnerId} was offered first and must land`);
    assert.equal(conflicted.committed, false);
    assert.equal(conflicted.outcome.reason_id, "version_conflict");

    const retried = cell.recompute(loser);
    assert.equal(retried.outcome.reason_id, "ceiling_exceeded");
    cell.commit(retried);
    const final = cell.read();
    assert.deepEqual(projectLedger(final).open_reservation_ids, [`res:${winnerId}`],
      "the reservation that committed is the one that is held, by identity");
    assert.equal(node(final, A04).rolled_up.committed_units, 200);
    assertLedgerConservation(final);
  }
});

test("RACE: two conversions of ONE reservation computed from one base book the charge once", () => {
  let opened = fresh();
  ({ ledger: opened } = reserve(opened, reserveOp("a", A04, 100)));
  const cell = openLedgerCell(opened);
  // Two settlement callers, each with its own conversion id and its own vendor
  // reference, so nothing but the commit boundary can tell them apart: the
  // duplicate-vendor-charge guard does not fire and the reservation is open in
  // both of their views.
  const alpha = cell.prepare(convertStep("alpha", "res:a", 90));
  const beta = cell.prepare(convertStep("beta", "res:a", 95));
  assert.equal(alpha.outcome.accepted, true);
  assert.equal(beta.outcome.accepted, true,
    "both callers compute a valid conversion of the same open reservation — this is the double count, one commit away");

  const landed = cell.commit(alpha);
  const lost = cell.commit(beta);
  assert.equal(landed.committed, true);
  assert.equal(lost.committed, false);
  assert.equal(lost.outcome.reason_id, "version_conflict");
  assert.equal(lost.outcome.base_version, 1);
  assert.equal(lost.outcome.current_version, 2);

  const retried = cell.recompute(beta);
  assert.equal(retried.outcome.accepted, false);
  assert.equal(retried.outcome.reason_id, "reservation_not_open",
    "recomputed against what committed, the second settlement has nothing left to convert");
  assert.equal(cell.commit(retried).committed, true);

  const a04 = node(cell.read(), A04);
  assert.equal(a04.rolled_up.actual_units, 90, "the charge is booked exactly once");
  assert.equal(a04.rolled_up.incurred_units, 90);
  assert.equal(a04.rolled_up.reservation_outstanding_units, 0);
  assert.equal(a04.rolled_up.committed_units, 90);
  assert.equal(node(cell.read(), PORTFOLIO).rolled_up.actual_units, 90,
    "and once at every ancestor, not once per caller");
  assert.deepEqual(assertLedgerConservation(cell.read()).conversion_ids, ["conv:alpha"],
    "one conversion id exists, so the log cannot carry a second release or a second actual");
});

test("RACE: whichever conversion is offered first is the one that is booked", () => {
  for (const [winnerId, winnerAmount, loserId] of [["alpha", 90, "beta"], ["beta", 95, "alpha"]]) {
    let opened = fresh();
    ({ ledger: opened } = reserve(opened, reserveOp("a", A04, 100)));
    const cell = openLedgerCell(opened);
    const winner = cell.prepare(convertStep(winnerId, "res:a", winnerAmount));
    const loser = cell.prepare(convertStep(loserId, "res:a", winnerId === "alpha" ? 95 : 90));

    const landed = cell.commit(winner);
    const conflicted = cell.commit(loser);
    assert.equal(landed.committed, true);
    assert.equal(conflicted.committed, false);
    assert.equal(conflicted.outcome.reason_id, "version_conflict");

    const retried = cell.recompute(loser);
    assert.equal(retried.outcome.reason_id, "reservation_not_open");
    cell.commit(retried);
    const final = cell.read();
    assert.equal(node(final, A04).rolled_up.actual_units, winnerAmount,
      "the amount booked is the one that committed, not the larger or the first prepared");
    assert.equal(node(final, A04).rolled_up.reservation_outstanding_units, 0);
    assertLedgerConservation(final);
  }
});

// --- fixture 1c: a proposal is not trusted, it is rederived ------------------
//
// A prepared commit is an ordinary value and its holder can edit it. These
// fixtures hand the cell proposals that are internally dishonest — the base
// they name is genuinely current, so the compare-and-swap has nothing to catch
// — and prove the cell still refuses them, because it recomputes the whole
// transition from its own value and compares.

function doctor(prepared, overrides) {
  return Object.freeze({ ...prepared, ...overrides });
}

test("ADVERSARIAL: a proposal whose next_ledger is its own base is refused, not committed empty", () => {
  const cell = openLedgerCell(fresh());
  const honest = cell.prepare(reserveStep("first", A04, 200));
  // The proposal now says: accept this 200-unit reservation, and the ledger it
  // produces is the base. A commit that installed what it was handed would
  // answer `committed: true`, record no entry and leave the version at zero —
  // after which a second 200-unit proposal from that same base would commit
  // too, and 400 units would be accepted under a 300-unit ceiling.
  const doctored = doctor(honest, { next_ledger: cell.read() });
  const refused = cell.commit(doctored);

  assert.equal(refused.committed, false, "a proposal the ledger cannot rederive never commits");
  assert.equal(refused.outcome.accepted, false);
  assert.equal(refused.outcome.reason_id, "prepared_commit_mismatch");
  assert.equal(refused.outcome.operation_id, "op:first");
  assert.notEqual(refused.outcome.proposed_state_digest, refused.outcome.rederived_state_digest);
  assert.equal(cell.version(), 0, "nothing was applied, so nothing advanced");
  assert.equal(cell.read().entries.length, 0);
  assert.deepEqual(projectLedger(cell.read()).open_reservation_ids, []);

  // The honest proposal, which names the same still-current base, still lands.
  const landed = cell.commit(honest);
  assert.equal(landed.committed, true);
  assert.equal(cell.version(), 1);
  assert.deepEqual(projectLedger(cell.read()).open_reservation_ids, ["res:first"]);
  assertLedgerConservation(cell.read());
});

test("ADVERSARIAL: a proposal whose recorded outcome disagrees with the rederived one is refused", () => {
  const cell = openLedgerCell(fresh());
  const fits = cell.prepare(reserveStep("fits", A04, 100));
  const breaches = cell.prepare(reserveStep("breaches", A04, 400));
  assert.equal(breaches.outcome.accepted, false);
  assert.equal(breaches.outcome.reason_id, "ceiling_exceeded");

  // Same operation and same base, and the ledger it proposes is the right one
  // — a refusal is applied like any other decision — so only the ANSWER has
  // been swapped, for one that says the 400 units were accepted. The ledger
  // halves match and the outcome halves do not.
  const doctored = doctor(breaches, { outcome: fits.outcome });
  const refused = cell.commit(doctored);
  assert.equal(refused.committed, false);
  assert.equal(refused.outcome.reason_id, "prepared_commit_mismatch");
  assert.equal(refused.outcome.proposed_state_digest, refused.outcome.rederived_state_digest,
    "the proposed ledger was honest; the answer attached to it was not");
  assert.equal(cell.version(), 0);
  assert.equal(node(cell.read(), A04).rolled_up.committed_units, 0);

  // And the same operation, offered with the answer it actually produces, is
  // admitted — as a refusal, which is what it always was.
  const settled = cell.commit(breaches);
  assert.equal(settled.committed, true);
  assert.equal(settled.outcome.accepted, false);
  assert.equal(settled.outcome.reason_id, "ceiling_exceeded");
  assert.equal(cell.version(), 1);
  assert.equal(node(cell.read(), A04).rolled_up.committed_units, 0);
  assertLedgerConservation(cell.read());
});

test("COMMIT: a version counts applied operations, not entries, and a replay does not advance it", () => {
  let ledger = fresh();
  assert.equal(ledgerVersion(ledger), 0);
  ({ ledger } = reserve(ledger, reserveOp("a", A04, 100)));
  assert.equal(ledgerVersion(ledger), 1);

  // A refusal writes no entry and still advances the version: it is a decision
  // the ledger made against a state, so a commit computed before it is stale.
  const refused = reserve(ledger, reserveOp("b", A04, 250));
  assert.equal(refused.outcome.accepted, false);
  assert.equal(ledgerVersion(refused.ledger), 2);
  assert.equal(refused.ledger.entries.length, 1, "the refusal wrote no entry");

  // A replay is not a second operation, so it is not a second version either.
  const replay = reserve(refused.ledger, reserveOp("a", A04, 100));
  assert.equal(replay.outcome.replayed, true);
  assert.equal(ledgerVersion(replay.ledger), 2);
  assert.equal(ledgerStateDigest(replay.ledger), ledgerStateDigest(refused.ledger));
});

test("COMMIT: a commit from a different lineage of the same age is refused, not installed", () => {
  // Both cells have applied exactly one operation, so a version check alone
  // would wave this through. The state digest is what stops it.
  const here = openLedgerCell(reserve(fresh(), reserveOp("here", A04, 50)).ledger);
  const elsewhere = openLedgerCell(reserve(fresh(), reserveOp("elsewhere", A05, 50)).ledger);
  assert.equal(here.version(), elsewhere.version());
  assert.notEqual(here.stateDigest(), elsewhere.stateDigest());

  const prepared = elsewhere.prepare(reserveStep("stranger", A05, 10));
  const result = here.commit(prepared);
  assert.equal(result.committed, false);
  assert.equal(result.outcome.reason_id, "version_conflict");
  assert.equal(result.outcome.base_version, result.outcome.current_version,
    "same age, different lineage — the version agreed and the digest did not");
  assert.notEqual(result.outcome.base_state_digest, result.outcome.current_state_digest);
  assert.deepEqual(projectLedger(here.read()).open_reservation_ids, ["res:here"]);
});

test("COMMIT: a prepared commit is a value, and re-offering it to its own base is refused after it lands", () => {
  const cell = openLedgerCell(fresh());
  const prepared = cell.prepare(reserveStep("a", A04, 100));
  const landed = cell.commit(prepared);
  assert.equal(landed.committed, true);
  // Offering the same prepared commit again against the ledger it produced is a
  // stale base like any other. The idempotency gate is a separate guard and
  // this must not be allowed to depend on it.
  const again = cell.commit(prepared);
  assert.equal(again.committed, false);
  assert.equal(again.outcome.reason_id, "version_conflict");
  assert.equal(node(cell.read(), A04).rolled_up.reservation_outstanding_units, 100);
  assertLedgerConservation(cell.read());
});

test("COMMIT: a malformed commit throws rather than being treated as a conflict", () => {
  const cell = openLedgerCell(fresh());
  const honest = cell.prepare(reserveStep("a", A04, 100));
  refuses(() => cell.commit({ base_version: 0 }), "invalid_shape");
  refuses(() => cell.commit(doctor(honest, { kind: "settle_invoice" })), "unknown_operation_kind");
  refuses(() => cell.commit(doctor(honest, { operation: {} })), "invalid_reference");
  refuses(() => cell.commit(doctor(honest, { next_ledger: {} })), "invalid_shape");
  refuses(() => cell.commit(doctor(honest, { base_version: "0" })), "invalid_shape");
  refuses(() => prepareCommit(cell.read(), { kind: "settle_invoice", operation: {} }),
    "unknown_operation_kind");
  refuses(() => prepareCommit(cell.read(), { kind: "reserve" }), "missing_field");
  assert.equal(cell.version(), 0, "a throw is not a commit either");
});

test("COMMIT: nothing outside the cell can install a ledger value", () => {
  // The single-writer functions return a ledger to their caller and always
  // will — that is the whole of the sequencing interface. What must not exist
  // anywhere is a second thing that can answer `committed: true`, because a
  // second admission point is the same defect as none.
  const cell = openLedgerCell(fresh());
  const committers = Object.entries(ledgerModule)
    .filter(([, value]) => typeof value === "function")
    .filter(([name]) => /commit/i.test(name) && name !== "openLedgerCell");
  assert.deepEqual(committers.map(([name]) => name).sort(), ["prepareCommit"],
    "prepareCommit computes and installs nothing; commitPrepared is gone");
  assert.equal(prepareCommit(cell.read(), reserveStep("a", A04, 10)).committed, undefined,
    "a preparation is not a commit and does not carry the word");
  assert.throws(() => { cell.commit = () => ({ committed: true }); }, TypeError,
    "the cell's admission point cannot be replaced by its caller");
});

// --- fixture 2: cancel -----------------------------------------------------

test("CANCEL: cancelling a reservation returns the headroom and incurs nothing", () => {
  let ledger = fresh();
  ({ ledger } = reserve(ledger, reserveOp("a", A04, 300)));
  const blocked = reserve(ledger, reserveOp("b", A04, 50)).outcome;
  assert.equal(blocked.accepted, false);

  const cancelled = cancelReservation(ledger, {
    operation_id: "op:cancel", reservation_id: "res:a", cancelled_at: AT,
  });
  assert.equal(cancelled.outcome.accepted, true);
  assert.equal(cancelled.outcome.released_units, 300);
  const after = node(cancelled.ledger, A04);
  assert.equal(after.rolled_up.reservation_outstanding_units, 0);
  assert.equal(after.rolled_up.committed_units, 0);
  assert.equal(after.rolled_up.actual_units, 0, "a cancellation must not book a charge");
  assert.equal(after.available_units, 300);

  const now = reserve(cancelled.ledger, reserveOp("b", A04, 50)).outcome;
  assert.equal(now.accepted, true, "the released headroom must be usable again");
  assertLedgerConservation(cancelled.ledger);
});

test("CANCEL: the release is append-only — the reservation entry stays in the log", () => {
  let ledger = fresh();
  ({ ledger } = reserve(ledger, reserveOp("a", A04, 120)));
  ({ ledger } = cancelReservation(ledger, {
    operation_id: "op:cancel", reservation_id: "res:a", cancelled_at: AT,
  }));
  assert.equal(ledger.entries.length, 2);
  assert.deepEqual(ledger.entries.map(entry => entry.kind), ["reservation", "reservation_release"]);
  const proof = assertLedgerConservation(ledger);
  assert.deepEqual(proof.reservation_ids, ["res:a"]);
  assert.equal(proof.open_reservation_units, 0);
});

test("CANCEL: cancelling twice is a returned refusal, not a second release", () => {
  let ledger = fresh();
  ({ ledger } = reserve(ledger, reserveOp("a", A04, 120)));
  ({ ledger } = cancelReservation(ledger, {
    operation_id: "op:cancel-1", reservation_id: "res:a", cancelled_at: AT,
  }));
  const second = cancelReservation(ledger, {
    operation_id: "op:cancel-2", reservation_id: "res:a", cancelled_at: AT,
  });
  assert.equal(second.outcome.accepted, false);
  assert.equal(second.outcome.reason_id, "reservation_not_open");
  assert.equal(second.ledger.entries.length, 2, "a refused cancel must append nothing");
  assertLedgerConservation(second.ledger);
});

test("CANCEL: a reservation id the ledger never made throws, an id already closed refuses", () => {
  let ledger = fresh();
  refuses(() => cancelReservation(ledger, {
    operation_id: "op:c", reservation_id: "res:never", cancelled_at: AT,
  }), "unknown_reservation");
  ({ ledger } = reserve(ledger, reserveOp("a", A04, 10)));
  ({ ledger } = cancelReservation(ledger, {
    operation_id: "op:c1", reservation_id: "res:a", cancelled_at: AT,
  }));
  const closed = cancelReservation(ledger, {
    operation_id: "op:c2", reservation_id: "res:a", cancelled_at: AT,
  });
  assert.equal(closed.outcome.reason_id, "reservation_not_open");
});

test("CANCEL: re-using a reservation id after it closed is refused", () => {
  let ledger = fresh();
  ({ ledger } = reserve(ledger, reserveOp("a", A04, 10)));
  ({ ledger } = cancelReservation(ledger, {
    operation_id: "op:c", reservation_id: "res:a", cancelled_at: AT,
  }));
  const again = reserve(ledger, {
    operation_id: "op:again", node_id: A04, reservation_id: "res:a",
    amount_units: 10, requested_at: AT,
  });
  assert.equal(again.outcome.accepted, false);
  assert.equal(again.outcome.reason_id, "duplicate_reservation_id");
});

// --- fixture 3: retry ------------------------------------------------------

test("RETRY: replaying an operation returns the recorded outcome and changes nothing", () => {
  const first = reserve(fresh(), reserveOp("a", A04, 120));
  const replay = reserve(first.ledger, reserveOp("a", A04, 120));
  assert.equal(replay.outcome.accepted, true);
  assert.equal(replay.outcome.replayed, true);
  assert.equal(first.outcome.replayed, false);
  assert.equal(replay.ledger.entries.length, 1, "a retry must not append a second entry");
  assert.equal(node(replay.ledger, A04).rolled_up.committed_units, 120);
  assertLedgerConservation(replay.ledger);
});

test("RETRY: replaying a REFUSED operation replays the refusal, not a fresh attempt", () => {
  let ledger = fresh();
  ({ ledger } = reserve(ledger, reserveOp("big", A04, 300)));
  const refused = reserve(ledger, reserveOp("over", A04, 50));
  assert.equal(refused.outcome.accepted, false);
  // Free the headroom, then replay the refused operation: it must still refuse,
  // because a retry replays one intent and does not re-evaluate it.
  const cancelled = cancelReservation(refused.ledger, {
    operation_id: "op:cancel", reservation_id: "res:big", cancelled_at: AT,
  });
  const replay = reserve(cancelled.ledger, reserveOp("over", A04, 50));
  assert.equal(replay.outcome.accepted, false);
  assert.equal(replay.outcome.replayed, true);
  assert.equal(replay.outcome.reason_id, "ceiling_exceeded");
  assertLedgerConservation(replay.ledger);
});

test("RETRY: the same operation id over different arguments throws rather than replaying", () => {
  const first = reserve(fresh(), reserveOp("a", A04, 120));
  refuses(() => reserve(first.ledger, {
    operation_id: "op:a", node_id: A04, reservation_id: "res:a",
    amount_units: 121, requested_at: AT,
  }), "operation_id_reused_with_different_arguments");
});

test("RETRY: retrying every operation kind is safe, and the ledger is identical afterwards", () => {
  let ledger = fresh();
  const steps = [
    { kind: "record_estimate", operation: {
      operation_id: "op:est", node_id: A04, expected_total_cost_units: 250,
      basis_digest: "sha256:aa", recorded_at: AT } },
    { kind: "reserve", operation: reserveOp("r1", A04, 100) },
    { kind: "cancel_reservation", operation: {
      operation_id: "op:cancel", reservation_id: "res:r1", cancelled_at: AT } },
    { kind: "reserve", operation: reserveOp("r2", A04, 80) },
    { kind: "convert_reservation_to_actual", operation: {
      operation_id: "op:conv", conversion_id: "conv:1", reservation_id: "res:r2",
      actual_amount_units: 90, vendor_reference: "INV-A", settled_at: AT } },
    { kind: "record_late_liability", operation: {
      operation_id: "op:liab", node_id: A04, liability_id: "liab:1",
      amount_units: 30, vendor_reference: "INV-B", incurred_at: AT } },
    { kind: "convert_liability_to_actual", operation: {
      operation_id: "op:liabconv", conversion_id: "conv:2", liability_id: "liab:1",
      actual_amount_units: 30, vendor_reference: "INV-B-SETTLED", settled_at: AT } },
    { kind: "post_actual", operation: {
      operation_id: "op:act", node_id: A04, amount_units: 15,
      vendor_reference: "INV-C", incurred_at: AT } },
  ];
  assert.deepEqual([...new Set(steps.map(step => step.kind))].sort(),
    [...V5_LEDGER_OPERATION_KINDS],
    "every registered operation kind must appear in the retry fixture");
  ({ ledger } = applyOperations(ledger, steps));
  const before = projectLedger(ledger);
  const entryCount = ledger.entries.length;

  const replayed = applyOperations(ledger, steps);
  assert.equal(replayed.ledger.entries.length, entryCount, "a full replay appended entries");
  assert.ok(replayed.outcomes.every(outcome => outcome.replayed === true));
  assert.deepEqual(projectLedger(replayed.ledger).nodes, before.nodes);
  assertLedgerConservation(replayed.ledger);
});

// --- fixture 4: vendor -----------------------------------------------------

test("VENDOR: the same vendor charge posted twice is refused the second time", () => {
  let ledger = fresh();
  ({ ledger } = postActual(ledger, {
    operation_id: "op:a", node_id: A04, amount_units: 40,
    vendor_reference: "INV-2026-0007", incurred_at: AT,
  }));
  const duplicate = postActual(ledger, {
    operation_id: "op:b", node_id: A04, amount_units: 40,
    vendor_reference: "INV-2026-0007", incurred_at: AT,
  });
  assert.equal(duplicate.outcome.accepted, false);
  assert.equal(duplicate.outcome.reason_id, "duplicate_vendor_charge");
  assert.equal(node(duplicate.ledger, A04).rolled_up.actual_units, 40,
    "the duplicate must not be counted");
  assertLedgerConservation(duplicate.ledger);
});

test("VENDOR: the duplicate check is the vendor reference, not the amount or the node", () => {
  let ledger = fresh();
  ({ ledger } = postActual(ledger, {
    operation_id: "op:a", node_id: A04, amount_units: 40,
    vendor_reference: "INV-A", incurred_at: AT,
  }));
  // A different charge of the same size on the same node is a real second
  // charge, and must be accepted.
  const second = postActual(ledger, {
    operation_id: "op:b", node_id: A04, amount_units: 40,
    vendor_reference: "INV-B", incurred_at: AT,
  });
  assert.equal(second.outcome.accepted, true);
  assert.equal(node(second.ledger, A04).rolled_up.actual_units, 80);
  // The same reference on a DIFFERENT node is still the same charge.
  const elsewhere = postActual(second.ledger, {
    operation_id: "op:c", node_id: A05, amount_units: 40,
    vendor_reference: "INV-A", incurred_at: AT,
  });
  assert.equal(elsewhere.outcome.reason_id, "duplicate_vendor_charge");
});

test("VENDOR: an outside reference keeps its own case and punctuation", () => {
  const { outcome } = postActual(fresh(), {
    operation_id: "op:a", node_id: A04, amount_units: 1,
    vendor_reference: "Anthropic/INV-2026-09.0001", incurred_at: AT,
  });
  assert.equal(outcome.accepted, true);
});

test("VENDOR: a vendor charge larger than the reservation is booked at the vendor's number", () => {
  let ledger = fresh();
  ({ ledger } = reserve(ledger, reserveOp("a", A04, 100)));
  const converted = convertReservationToActual(ledger, {
    operation_id: "op:conv", conversion_id: "conv:1", reservation_id: "res:a",
    actual_amount_units: 175, vendor_reference: "INV-OVER", settled_at: AT,
  });
  assert.equal(converted.outcome.accepted, true);
  assert.equal(converted.outcome.released_units, 100);
  assert.equal(converted.outcome.actual_units, 175);
  assert.equal(converted.outcome.variance_units, 75);
  const a04 = node(converted.ledger, A04);
  assert.equal(a04.rolled_up.actual_units, 175);
  assert.equal(a04.rolled_up.reservation_outstanding_units, 0);
  assert.equal(a04.rolled_up.committed_units, 175, "the intention is withdrawn, the charge stands");
  assertLedgerConservation(converted.ledger);
});

test("VENDOR: a vendor charge smaller than the reservation releases the whole intention", () => {
  let ledger = fresh();
  ({ ledger } = reserve(ledger, reserveOp("a", A04, 100)));
  ({ ledger } = convertReservationToActual(ledger, {
    operation_id: "op:conv", conversion_id: "conv:1", reservation_id: "res:a",
    actual_amount_units: 25, vendor_reference: "INV-UNDER", settled_at: AT,
  }));
  const a04 = node(ledger, A04);
  assert.equal(a04.rolled_up.actual_units, 25);
  assert.equal(a04.rolled_up.committed_units, 25);
  assert.equal(a04.available_units, 275, "unspent headroom must come back");
  assertLedgerConservation(ledger);
});

// --- fixture 5: the late actual and the late liability ---------------------

test("LATE ACTUAL: a charge above the ceiling is booked in full, never truncated", () => {
  const { ledger, outcome } = postActual(fresh(), {
    operation_id: "op:a", node_id: A04, amount_units: 450,
    vendor_reference: "INV-LATE", incurred_at: AT,
  });
  assert.equal(outcome.accepted, true, "a ceiling must never refuse a real charge");
  assert.equal(outcome.amount_units, 450);
  assert.equal(node(ledger, A04).rolled_up.actual_units, 450,
    "the charge must not be clipped to the 300 ceiling");
  assertLedgerConservation(ledger);
});

test("LATE ACTUAL: an over-ceiling charge posts through every ancestor and marks them overdrawn", () => {
  const { ledger } = postActual(fresh(), {
    operation_id: "op:a", node_id: A04, amount_units: 450,
    vendor_reference: "INV-LATE", incurred_at: AT,
  });
  const projection = projectLedger(ledger);
  for (const id of [A04, ASSURANCE, PORTFOLIO]) {
    assert.equal(projection.by_node[id].rolled_up.actual_units, 450, `${id} did not see the charge`);
    assert.equal(projection.by_node[id].overdrawn, true, `${id} was not marked overdrawn`);
    assert.deepEqual(projection.by_node[id].requires, [...V5_OVERDRAWN_REMEDIES]);
    assert.deepEqual(projection.by_node[id].overdrawn_caused_by_node_ids, [A04]);
  }
  assert.equal(projection.hierarchy_overdrawn, true);
  // Only A04 actually breached its own ceiling; the ancestors are marked
  // because of it, and they say so.
  assert.equal(projection.by_node[A04].breaches_own_ceiling, true);
  assert.equal(projection.by_node[ASSURANCE].breaches_own_ceiling, false);
  assert.equal(projection.by_node[PRODUCT].overdrawn, false,
    "an unrelated branch must not be marked by another branch's overage");
});

test("LATE LIABILITY: an unsettled charge counts as incurred and is accepted above the ceiling", () => {
  const { ledger, outcome } = recordLateLiability(fresh(), {
    operation_id: "op:a", node_id: A04, liability_id: "liab:1", amount_units: 400,
    vendor_reference: "INV-OWED", incurred_at: AT,
  });
  assert.equal(outcome.accepted, true);
  const a04 = node(ledger, A04);
  assert.equal(a04.rolled_up.liability_outstanding_units, 400);
  assert.equal(a04.rolled_up.incurred_units, 400, "an owed charge is incurred");
  assert.equal(a04.rolled_up.actual_units, 0, "an owed charge is not yet an actual");
  assert.equal(a04.overdrawn, true);
  assertLedgerConservation(ledger);
});

test("LATE LIABILITY: a liability id recorded twice is refused", () => {
  let ledger = fresh();
  ({ ledger } = recordLateLiability(ledger, {
    operation_id: "op:a", node_id: A04, liability_id: "liab:1", amount_units: 10,
    vendor_reference: "INV-1", incurred_at: AT,
  }));
  const second = recordLateLiability(ledger, {
    operation_id: "op:b", node_id: A04, liability_id: "liab:1", amount_units: 10,
    vendor_reference: "INV-2", incurred_at: AT,
  });
  assert.equal(second.outcome.reason_id, "liability_not_open");
});

// --- fixture 6: conversion -------------------------------------------------

test("CONVERSION: one call appends exactly two entries under one conversion id", () => {
  let ledger = fresh();
  ({ ledger } = reserve(ledger, reserveOp("a", A04, 100)));
  ({ ledger } = convertReservationToActual(ledger, {
    operation_id: "op:conv", conversion_id: "conv:1", reservation_id: "res:a",
    actual_amount_units: 100, vendor_reference: "INV-A", settled_at: AT,
  }));
  const converted = ledger.entries.filter(entry => entry.conversion_id === "conv:1");
  assert.equal(converted.length, 2);
  assert.deepEqual(converted.map(entry => entry.kind).sort(),
    ["actual", "reservation_release"]);
  assert.equal(converted[0].sequence + 1, converted[1].sequence, "the pair is contiguous");
  const proof = assertLedgerConservation(ledger);
  assert.deepEqual(proof.conversion_ids, ["conv:1"]);
});

test("CONVERSION: converting the same reservation twice is refused, never double counted", () => {
  let ledger = fresh();
  ({ ledger } = reserve(ledger, reserveOp("a", A04, 100)));
  ({ ledger } = convertReservationToActual(ledger, {
    operation_id: "op:conv-1", conversion_id: "conv:1", reservation_id: "res:a",
    actual_amount_units: 100, vendor_reference: "INV-A", settled_at: AT,
  }));
  const second = convertReservationToActual(ledger, {
    operation_id: "op:conv-2", conversion_id: "conv:2", reservation_id: "res:a",
    actual_amount_units: 100, vendor_reference: "INV-B", settled_at: AT,
  });
  assert.equal(second.outcome.accepted, false);
  assert.equal(second.outcome.reason_id, "reservation_not_open");
  assert.equal(node(second.ledger, A04).rolled_up.actual_units, 100,
    "the charge must be booked exactly once");
  assertLedgerConservation(second.ledger);
});

test("CONVERSION: a reservation cancelled first cannot then be converted", () => {
  let ledger = fresh();
  ({ ledger } = reserve(ledger, reserveOp("a", A04, 100)));
  ({ ledger } = cancelReservation(ledger, {
    operation_id: "op:cancel", reservation_id: "res:a", cancelled_at: AT,
  }));
  const converted = convertReservationToActual(ledger, {
    operation_id: "op:conv", conversion_id: "conv:1", reservation_id: "res:a",
    actual_amount_units: 100, vendor_reference: "INV-A", settled_at: AT,
  });
  assert.equal(converted.outcome.reason_id, "reservation_not_open");
  assert.equal(node(converted.ledger, A04).rolled_up.actual_units, 0);
});

test("CONVERSION: cancel and convert applied either way round book the charge at most once", () => {
  const cancelStep = { kind: "cancel_reservation", operation: {
    operation_id: "op:cancel", reservation_id: "res:a", cancelled_at: AT } };
  const convertStep = { kind: "convert_reservation_to_actual", operation: {
    operation_id: "op:conv", conversion_id: "conv:1", reservation_id: "res:a",
    actual_amount_units: 90, vendor_reference: "INV-A", settled_at: AT } };
  const setup = { kind: "reserve", operation: reserveOp("a", A04, 100) };

  const cancelFirst = applyOperations(fresh(), [setup, cancelStep, convertStep]);
  const convertFirst = applyOperations(fresh(), [setup, convertStep, cancelStep]);

  assert.equal(node(cancelFirst.ledger, A04).rolled_up.actual_units, 0);
  assert.equal(node(convertFirst.ledger, A04).rolled_up.actual_units, 90);
  for (const run of [cancelFirst, convertFirst]) {
    assert.equal(run.outcomes.filter(outcome => outcome.accepted).length, 2,
      "exactly the reserve and one of the two closers may be accepted");
    assert.equal(node(run.ledger, A04).rolled_up.reservation_outstanding_units, 0);
    assertLedgerConservation(run.ledger);
  }
});

test("CONVERSION: a liability converts to an actual exactly once and stops being owed", () => {
  let ledger = fresh();
  ({ ledger } = recordLateLiability(ledger, {
    operation_id: "op:liab", node_id: A04, liability_id: "liab:1", amount_units: 60,
    vendor_reference: "INV-OWED", incurred_at: AT,
  }));
  ({ ledger } = convertLiabilityToActual(ledger, {
    operation_id: "op:conv", conversion_id: "conv:1", liability_id: "liab:1",
    actual_amount_units: 60, vendor_reference: "INV-PAID", settled_at: AT,
  }));
  const a04 = node(ledger, A04);
  assert.equal(a04.rolled_up.liability_outstanding_units, 0);
  assert.equal(a04.rolled_up.actual_units, 60);
  assert.equal(a04.rolled_up.incurred_units, 60,
    "settling an owed charge must not double the incurred total");

  const second = convertLiabilityToActual(ledger, {
    operation_id: "op:conv-2", conversion_id: "conv:2", liability_id: "liab:1",
    actual_amount_units: 60, vendor_reference: "INV-PAID-AGAIN", settled_at: AT,
  });
  assert.equal(second.outcome.reason_id, "liability_not_open");
  assertLedgerConservation(second.ledger);
});

test("CONVERSION: settling under the vendor reference the liability already used is refused", () => {
  let ledger = fresh();
  ({ ledger } = recordLateLiability(ledger, {
    operation_id: "op:liab", node_id: A04, liability_id: "liab:1", amount_units: 60,
    vendor_reference: "INV-OWED", incurred_at: AT,
  }));
  const converted = convertLiabilityToActual(ledger, {
    operation_id: "op:conv", conversion_id: "conv:1", liability_id: "liab:1",
    actual_amount_units: 60, vendor_reference: "INV-OWED", settled_at: AT,
  });
  assert.equal(converted.outcome.reason_id, "duplicate_vendor_charge");
});

test("CONVERSION: an over-ceiling conversion is accepted and marks the hierarchy overdrawn", () => {
  let ledger = fresh();
  ({ ledger } = reserve(ledger, reserveOp("a", A04, 250)));
  assert.equal(node(ledger, A04).overdrawn, false);
  ({ ledger } = convertReservationToActual(ledger, {
    operation_id: "op:conv", conversion_id: "conv:1", reservation_id: "res:a",
    actual_amount_units: 700, vendor_reference: "INV-SHOCK", settled_at: AT,
  }));
  const projection = projectLedger(ledger);
  assert.equal(projection.by_node[A04].rolled_up.actual_units, 700);
  assert.equal(projection.hierarchy_overdrawn, true);
  assertLedgerConservation(ledger);
});

// --- overdrawn denies new reservations -------------------------------------

test("OVERDRAWN: an overdrawn ancestor denies a new reservation on the breaching slice", () => {
  let ledger = fresh();
  ({ ledger } = postActual(ledger, {
    operation_id: "op:over", node_id: A04, amount_units: 450,
    vendor_reference: "INV-OVER", incurred_at: AT,
  }));
  const denied = reserve(ledger, reserveOp("next", A04, 1));
  assert.equal(denied.outcome.accepted, false);
  assert.equal(denied.outcome.reason_id, "ancestor_overdrawn");
  assert.deepEqual(denied.outcome.overdrawn_node_ids, [A04, ASSURANCE, PORTFOLIO]);
  assert.deepEqual(denied.outcome.requires, [...V5_OVERDRAWN_REMEDIES]);
});

test("OVERDRAWN: a sibling's unused headroom does not absorb the overage", () => {
  let ledger = fresh();
  // A04 breaches by 150; A05 has its whole 300 untouched. Sibling netting would
  // let A05 carry on. It must not.
  ({ ledger } = postActual(ledger, {
    operation_id: "op:over", node_id: A04, amount_units: 450,
    vendor_reference: "INV-OVER", incurred_at: AT,
  }));
  const denied = reserve(ledger, reserveOp("sibling", A05, 10));
  assert.equal(denied.outcome.accepted, false);
  assert.equal(denied.outcome.reason_id, "ancestor_overdrawn");
  assert.deepEqual(denied.outcome.overdrawn_node_ids, [ASSURANCE, PORTFOLIO],
    "the sibling itself is not overdrawn; its shared ancestors are");
});

test("OVERDRAWN: a branch with no overdrawn ancestor keeps working", () => {
  let ledger = fresh();
  ({ ledger } = postActual(ledger, {
    operation_id: "op:over", node_id: A04, amount_units: 450,
    vendor_reference: "INV-OVER", incurred_at: AT,
  }));
  // J201 hangs from child:product-journey, which is not on A04's chain — but
  // the PORTFOLIO is, and the portfolio is overdrawn, so J201 is denied too.
  const denied = reserve(ledger, reserveOp("j", J201, 10));
  assert.equal(denied.outcome.reason_id, "ancestor_overdrawn");
  assert.deepEqual(denied.outcome.overdrawn_node_ids, [PORTFOLIO]);
});

test("OVERDRAWN: the overdrawn check runs before the headroom check", () => {
  // A04 is overdrawn by an actual of 450 against a 300 ceiling. A reservation
  // that would ALSO exceed the ceiling must say overdrawn, not ceiling: the
  // caller's next step is an incident, not a smaller request.
  let ledger = fresh();
  ({ ledger } = postActual(ledger, {
    operation_id: "op:over", node_id: A04, amount_units: 450,
    vendor_reference: "INV-OVER", incurred_at: AT,
  }));
  const denied = reserve(ledger, reserveOp("huge", A04, 9999));
  assert.equal(denied.outcome.reason_id, "ancestor_overdrawn");
  assert.equal(denied.outcome.available_units, undefined,
    "an overdrawn hierarchy must not quote remaining headroom");
});

test("OVERDRAWN: raising the ceiling through a new tree version clears the denial", () => {
  const first = postActual(fresh(), {
    operation_id: "op:over", node_id: A04, amount_units: 450,
    vendor_reference: "INV-OVER", incurred_at: AT,
  });
  assert.equal(reserve(first.ledger, reserveOp("next", A04, 1)).outcome.reason_id,
    "ancestor_overdrawn");
  // An authority amendment is a NEW tree. The ledger is replayed against it
  // rather than edited, which is why the module has no ceiling setter.
  const amended = { ...first.ledger, tree: tree({ a04: 600 }) };
  assert.equal(projectLedger(amended).hierarchy_overdrawn, false);
  assert.equal(reserve(amended, reserveOp("next", A04, 1)).outcome.accepted, true);
});

// --- conservation across a long mixed sequence -----------------------------

test("CONSERVATION: a long mixed sequence conserves at every step and at the end", () => {
  const steps = [
    { kind: "record_estimate", operation: {
      operation_id: "op:e", node_id: A04, expected_total_cost_units: 200,
      basis_digest: "sha256:aa", recorded_at: AT } },
    { kind: "reserve", operation: reserveOp("r1", A04, 100) },
    { kind: "reserve", operation: reserveOp("r2", A05, 150) },
    { kind: "reserve", operation: reserveOp("r3", J201, 120) },
    { kind: "cancel_reservation", operation: {
      operation_id: "op:c1", reservation_id: "res:r2", cancelled_at: AT } },
    { kind: "convert_reservation_to_actual", operation: {
      operation_id: "op:v1", conversion_id: "conv:1", reservation_id: "res:r1",
      actual_amount_units: 130, vendor_reference: "INV-1", settled_at: AT } },
    { kind: "record_late_liability", operation: {
      operation_id: "op:l1", node_id: A05, liability_id: "liab:1",
      amount_units: 40, vendor_reference: "INV-2", incurred_at: AT } },
    { kind: "post_actual", operation: {
      operation_id: "op:a1", node_id: J201, amount_units: 25,
      vendor_reference: "INV-3", incurred_at: AT } },
    { kind: "convert_liability_to_actual", operation: {
      operation_id: "op:v2", conversion_id: "conv:2", liability_id: "liab:1",
      actual_amount_units: 45, vendor_reference: "INV-2-PAID", settled_at: AT } },
  ];
  const run = applyOperations(fresh(), steps);
  assert.ok(run.outcomes.every(outcome => outcome.accepted), "the fixture must not be refused away");

  const projection = projectLedger(run.ledger);
  // A04: one conversion at 130. A05: a cancelled 150 and a settled 45.
  // J201: an open 120 reservation and a 25 actual.
  assert.equal(projection.by_node[A04].rolled_up.actual_units, 130);
  assert.equal(projection.by_node[A05].rolled_up.actual_units, 45);
  assert.equal(projection.by_node[A05].rolled_up.liability_outstanding_units, 0);
  assert.equal(projection.by_node[J201].rolled_up.actual_units, 25);
  assert.equal(projection.by_node[J201].rolled_up.reservation_outstanding_units, 120);
  // Every ancestor is exactly the sum of its children.
  assert.equal(projection.by_node[ASSURANCE].rolled_up.actual_units, 175);
  assert.equal(projection.by_node[PRODUCT].rolled_up.actual_units, 25);
  assert.equal(projection.by_node[PORTFOLIO].rolled_up.actual_units, 200);
  assert.equal(projection.by_node[PORTFOLIO].rolled_up.committed_units, 320);
  assert.equal(projection.by_node[PORTFOLIO].rolled_up.incurred_units, 200);
  assert.equal(projection.by_node[A04].variance_units, 130 - 200);

  const proof = assertLedgerConservation(run.ledger);
  assert.equal(proof.portfolio_actual_units, 200);
  assert.equal(proof.open_reservation_units, 120);
  assert.equal(proof.open_liability_units, 0);
  assert.deepEqual(proof.conversion_ids, ["conv:1", "conv:2"]);
});

test("CONSERVATION: the order of independent operations never changes the totals", () => {
  const steps = [
    { kind: "reserve", operation: reserveOp("r1", A04, 50) },
    { kind: "post_actual", operation: {
      operation_id: "op:a1", node_id: A05, amount_units: 60,
      vendor_reference: "INV-1", incurred_at: AT } },
    { kind: "record_late_liability", operation: {
      operation_id: "op:l1", node_id: J201, liability_id: "liab:1",
      amount_units: 30, vendor_reference: "INV-2", incurred_at: AT } },
  ];
  const forward = applyOperations(fresh(), steps);
  const reversed = applyOperations(fresh(), [...steps].reverse());
  const a = projectLedger(forward.ledger).by_node[PORTFOLIO].rolled_up;
  const b = projectLedger(reversed.ledger).by_node[PORTFOLIO].rolled_up;
  assert.deepEqual(a, b);
  assert.equal(a.committed_units, 140);
});

test("CONSERVATION: the portfolio total equals every node's own total", () => {
  const run = applyOperations(fresh(), [
    { kind: "post_actual", operation: {
      operation_id: "op:1", node_id: A04, amount_units: 11,
      vendor_reference: "INV-1", incurred_at: AT } },
    { kind: "post_actual", operation: {
      operation_id: "op:2", node_id: A05, amount_units: 13,
      vendor_reference: "INV-2", incurred_at: AT } },
    { kind: "post_actual", operation: {
      operation_id: "op:3", node_id: J201, amount_units: 17,
      vendor_reference: "INV-3", incurred_at: AT } },
  ]);
  const projection = projectLedger(run.ledger);
  const everyOwn = projection.nodes.reduce((sum, entry) => sum + entry.own.actual_units, 0);
  assert.equal(projection.by_node[PORTFOLIO].rolled_up.actual_units, everyOwn);
  assert.equal(everyOwn, 41);
});

test("CONSERVATION: a tampered log is caught by the conservation law", () => {
  let ledger = fresh();
  ({ ledger } = reserve(ledger, reserveOp("a", A04, 100)));
  // Two releases for one reservation is exactly the double count Q142 forbids.
  const doubled = {
    ...ledger,
    entries: Object.freeze([
      ...ledger.entries,
      { ...ledger.entries[0], entry_id: "entry:00000001", sequence: 1, kind: "reservation_release" },
      { ...ledger.entries[0], entry_id: "entry:00000002", sequence: 2, kind: "reservation_release" },
    ]),
    sequence: 3,
  };
  refuses(() => assertLedgerConservation(doubled), "conservation_violated");
});

test("CONSERVATION: a conversion missing its release half is caught", () => {
  let ledger = fresh();
  ({ ledger } = reserve(ledger, reserveOp("a", A04, 100)));
  const lonely = {
    ...ledger,
    entries: Object.freeze([...ledger.entries, {
      ...ledger.entries[0], entry_id: "entry:00000001", sequence: 1,
      kind: "actual", conversion_id: "conv:1", vendor_reference: "INV-X",
    }]),
    sequence: 2,
  };
  const error = refuses(() => assertLedgerConservation(lonely), "conservation_violated");
  assert.equal(error.detail.conversion_id, "conv:1");
});

// --- contract violations ---------------------------------------------------

test("SHAPE: a negative or zero amount is refused everywhere", () => {
  for (const amount of [-1, 0, 1.5]) {
    refuses(() => reserve(fresh(), reserveOp("a", A04, amount)), "invalid_cost_units");
    refuses(() => postActual(fresh(), {
      operation_id: "op:a", node_id: A04, amount_units: amount,
      vendor_reference: "INV-1", incurred_at: AT,
    }), "invalid_cost_units");
    refuses(() => recordLateLiability(fresh(), {
      operation_id: "op:a", node_id: A04, liability_id: "liab:1", amount_units: amount,
      vendor_reference: "INV-1", incurred_at: AT,
    }), "invalid_cost_units");
  }
});

test("SHAPE: an undeclared field on an operation is refused by name", () => {
  const error = refuses(() => reserve(fresh(), {
    ...reserveOp("a", A04, 10), force: true,
  }), "unknown_field");
  assert.deepEqual(error.detail.unknown, ["force"]);
});

test("SHAPE: there is no field anywhere that raises a ceiling or waives one", () => {
  const text = JSON.stringify(v5CostLedgerPreimage());
  for (const forbidden of ["override", "bypass", "force", "waive", "ignore_ceiling"]) {
    assert.ok(!text.includes(forbidden), `${forbidden} appears in the ledger's vocabulary`);
  }
});

test("SHAPE: a malformed instant is refused rather than reinterpreted", () => {
  refuses(() => reserve(fresh(), {
    operation_id: "op:a", node_id: A04, reservation_id: "res:a",
    amount_units: 10, requested_at: "2026-09-11",
  }), "invalid_instant");
  refuses(() => reserve(fresh(), {
    operation_id: "op:a", node_id: A04, reservation_id: "res:a",
    amount_units: 10, requested_at: "2026-02-30T00:00:00.000Z",
  }), "invalid_instant");
});

test("SHAPE: an unregistered operation kind throws rather than being skipped", () => {
  refuses(() => applyOperations(fresh(), [{ kind: "settle_invoice", operation: {} }]),
    "unknown_operation_kind");
});

test("SHAPE: every refusal reason a fixture can produce is inside the closed list", () => {
  let ledger = fresh();
  const produced = new Set();
  const collect = outcome => { if (!outcome.accepted) produced.add(outcome.reason_id); };

  ({ ledger } = reserve(ledger, reserveOp("a", A04, 300)));
  collect(reserve(ledger, reserveOp("b", A04, 10)).outcome);
  // A fresh operation id re-using a reservation id that is already open: the
  // duplicate is the RESERVATION id, so this must not look like a retry.
  collect(reserve(ledger, {
    operation_id: "op:dup-reservation", node_id: A04, reservation_id: "res:a",
    amount_units: 10, requested_at: AT,
  }).outcome);
  ({ ledger } = recordLateLiability(ledger, {
    operation_id: "op:l", node_id: A05, liability_id: "liab:1", amount_units: 10,
    vendor_reference: "INV-1", incurred_at: AT,
  }));
  collect(recordLateLiability(ledger, {
    operation_id: "op:l2", node_id: A05, liability_id: "liab:1", amount_units: 10,
    vendor_reference: "INV-2", incurred_at: AT,
  }).outcome);
  collect(postActual(ledger, {
    operation_id: "op:dup", node_id: A05, amount_units: 10,
    vendor_reference: "INV-1", incurred_at: AT,
  }).outcome);
  ({ ledger } = cancelReservation(ledger, {
    operation_id: "op:c", reservation_id: "res:a", cancelled_at: AT,
  }));
  collect(cancelReservation(ledger, {
    operation_id: "op:c2", reservation_id: "res:a", cancelled_at: AT,
  }).outcome);
  ({ ledger } = postActual(ledger, {
    operation_id: "op:over", node_id: A04, amount_units: 900,
    vendor_reference: "INV-OVER", incurred_at: AT,
  }));
  collect(reserve(ledger, reserveOp("z", A04, 1)).outcome);
  // A commit computed against a base the cell has already left, and a commit
  // whose proposal the cell cannot rederive.
  const cell = openLedgerCell(ledger);
  const stale = prepareCommit(fresh(), { kind: "reserve", operation: reserveOp("stale", A04, 1) });
  collect(cell.commit(stale).outcome);
  const current = cell.prepare({ kind: "reserve", operation: reserveOp("doctored", A05, 1) });
  collect(cell.commit({ ...current, next_ledger: cell.read() }).outcome);

  assert.deepEqual([...produced].sort(), [...V5_LEDGER_REFUSAL_REASONS]);
});

// --- the projection --------------------------------------------------------

test("PROJECTION: every closed vocabulary is hashed in", () => {
  const text = JSON.stringify(v5CostLedgerPreimage());
  for (const name of [...V5_LEDGER_ENTRY_KINDS, ...V5_LEDGER_REFUSAL_REASONS,
    ...V5_OVERDRAWN_REMEDIES, ...V5_LEDGER_OPERATION_KINDS]) {
    assert.ok(text.includes(name), `${name} is not in the hashed preimage`);
  }
});

test("PROJECTION: the honest boundary and the effect class are stated, not implied", () => {
  const projection = v5CostLedgerProjection();
  assert.equal(projection.effect_class, "internal_financial_accounting_no_external_payment");
  assert.equal(projection.external_payment_reachable_here, false);
  assert.equal(projection.ceiling_can_refuse_an_actual, false);
  assert.equal(projection.ceiling_can_refuse_a_late_liability, false);
  assert.equal(projection.conversion_can_run_twice, false);
  assert.equal(projection.retry_creates_a_second_entry, false);
  assert.equal(projection.sibling_headroom_absorbs_an_overage, false);
  assert.equal(projection.ancestor_totals_are_derived_not_maintained, true);
  assert.equal(projection.tenant, ORGANIZATION_TENANT_ID);
  assert.ok(projection.unimplemented_dependencies.some(gap => gap.includes("durable store")));
  assert.ok(projection.unimplemented_dependencies.some(gap => gap.includes("incident opener")));
  assert.equal(projection.commit_names_the_base_it_was_computed_from, true);
  assert.equal(projection.stale_base_commit_can_be_admitted, false);
  assert.equal(projection.two_commits_from_one_base_can_both_land, false);
  assert.equal(projection.commit_admission_point_is_a_cell_that_owns_the_ledger, true);
  assert.equal(projection.a_caller_supplied_next_ledger_can_be_installed, false);
  assert.equal(projection.a_commit_is_rederived_before_it_is_installed, true);
  assert.ok(projection.unimplemented_dependencies.some(gap =>
    gap.includes("durable cross-process serialization boundary")),
  "the in-memory compare-and-swap must not be allowed to read as durable serialization");
});

test("PROJECTION: a returned ledger and projection cannot be mutated by their caller", () => {
  const { ledger } = reserve(fresh(), reserveOp("a", A04, 10));
  assert.throws(() => { ledger.entries.push({}); }, TypeError);
  const projection = projectLedger(ledger);
  assert.throws(() => { projection.by_node[A04].rolled_up.actual_units = 99; }, TypeError);
});
