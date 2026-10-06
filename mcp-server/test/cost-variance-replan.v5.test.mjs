// V5-A04 — the 150/200 thresholds, what a replan may change, and who gets
// involved: Q115.D1 and Q128.D1 proved case by case.
//
// The suite is organised around the two claims that matter most and are easiest
// to get quietly wrong: that the thresholds fire at exactly the stated
// percentages (boundaries included), and that NO input anywhere produces a
// replan permitting unqualified routing or weakened quality. The second is
// proved by an exhaustive sweep over every combination the closed vocabularies
// admit, because a spot check cannot establish a "never".
//
// EVERY COST AND ESTIMATE BELOW IS A SYNTHETIC FIXTURE. Nothing was spent, no
// replan was really directed, and no human was really escalated to.

import test from "node:test";
import assert from "node:assert/strict";

import { ORGANIZATION_TENANT_ID, isKnownPartner } from "../src/identity.js";
import {
  V5_SCOPE_TREE_SCHEMA_VERSION,
  V5_OVERDRAWN_REMEDIES,
  compileScopeTree,
  openLedger,
  applyOperations,
  projectLedger,
} from "../src/hierarchical-cost-ledger.v5.js";
import {
  V5_VARIANCE_RESULT_SCHEMA_VERSION,
  V5_REPLAN_DIRECTIVE_SCHEMA_VERSION,
  V5_COST_BASIS_POINTS,
  V5_WARNING_THRESHOLD_BASIS_POINTS,
  V5_REPLAN_THRESHOLD_BASIS_POINTS,
  V5_VARIANCE_DIRECTIVES,
  V5_REPLAN_TRIGGERS,
  V5_REPLAN_LEVERS,
  V5_QUALIFICATION_STATES,
  V5_REPLAN_CHANGE_KINDS,
  V5_HUMAN_ESCALATION_CHANGE_KINDS,
  V5_Q115_MEASURED_DIMENSIONS,
  V5VarianceError,
  varianceBasisPoints,
  directiveForBasisPoints,
  assessVariance,
  assessNodeVariance,
  evaluateReplan,
  continueUnderReplan,
  classifyHumanEscalation,
  measureQ115Dimensions,
  v5CostVariancePreimage,
  v5CostVarianceProjection,
} from "../src/cost-variance-replan.v5.js";

function refuses(fn, code) {
  try {
    fn();
  } catch (error) {
    assert.ok(error instanceof V5VarianceError,
      `expected a V5VarianceError, got ${error?.name}: ${error?.message}`);
    assert.equal(error.code, code, `expected code "${code}", got "${error.code}" (${error.message})`);
    return error;
  }
  return assert.fail(`expected a refusal with code "${code}"`);
}

function assess(expected, incurred) {
  return assessVariance({ expected_total_cost_units: expected, incurred_units: incurred });
}

// --- the thresholds are the decision's own numbers -------------------------

test("THRESHOLD: the constants are Q128's 150 and 200 percent, in basis points", () => {
  assert.equal(V5_COST_BASIS_POINTS, 10000);
  assert.equal(V5_WARNING_THRESHOLD_BASIS_POINTS, 15000);
  assert.equal(V5_REPLAN_THRESHOLD_BASIS_POINTS, 20000);
});

test("THRESHOLD: 150 percent warns — at exactly 150, and not one unit below", () => {
  assert.equal(assess(100, 149).directive, "continue");
  assert.equal(assess(100, 150).directive, "warn");
  assert.equal(assess(100, 150).variance_basis_points, V5_WARNING_THRESHOLD_BASIS_POINTS);
});

test("THRESHOLD: 200 percent replans — at exactly 200, and not one unit below", () => {
  assert.equal(assess(100, 199).directive, "warn");
  assert.equal(assess(100, 200).directive, "replan");
  assert.equal(assess(100, 200).variance_basis_points, V5_REPLAN_THRESHOLD_BASIS_POINTS);
});

test("THRESHOLD: on plan and under plan both continue", () => {
  assert.equal(assess(100, 0).directive, "continue");
  assert.equal(assess(100, 100).directive, "continue");
  assert.equal(assess(100, 100).variance_basis_points, 10000);
  assert.equal(assess(100, 100).variance_units, 0);
});

test("THRESHOLD: the ratio floors rather than rounds, so 199.99 percent has not reached 200", () => {
  // 100 expected, 19999 hundredths incurred is not expressible in whole units,
  // so use a larger denominator: 10000 expected, 19999 incurred = 19999 bp.
  assert.equal(varianceBasisPoints(10000, 19999), 19999);
  assert.equal(directiveForBasisPoints(19999), "warn");
  assert.equal(varianceBasisPoints(10000, 20000), 20000);
  assert.equal(directiveForBasisPoints(20000), "replan");
});

test("THRESHOLD: the directive is always inside the closed list", () => {
  for (let incurred = 0; incurred <= 400; incurred += 7) {
    const directive = assess(100, incurred).directive;
    assert.ok(V5_VARIANCE_DIRECTIVES.includes(directive), `${incurred} -> ${directive}`);
  }
});

test("THRESHOLD: a caller cannot pass its own threshold", () => {
  refuses(() => assessVariance({
    expected_total_cost_units: 100, incurred_units: 500,
    replan_threshold_basis_points: 90000,
  }), "unknown_field");
});

test("VARIANCE: an absent estimate is unavailable, not a divide by zero and not a continue", () => {
  const result = assess(0, 500);
  assert.equal(result.available, false);
  assert.equal(result.reason_id, "estimate_not_recorded");
  assert.equal(result.directive, undefined);
  assert.equal(result.incurred_units, 500);
  assert.equal(result.schema_version, V5_VARIANCE_RESULT_SCHEMA_VERSION);
});

test("VARIANCE: a negative incurred or a fractional estimate is refused", () => {
  refuses(() => assess(100, -1), "invalid_cost_units");
  refuses(() => assess(100.5, 100), "invalid_cost_units");
  refuses(() => varianceBasisPoints(0, 10), "invalid_cost_units");
});

// --- the replan directive --------------------------------------------------

test("REPLAN: 200 percent fires the cost trigger", () => {
  const directive = evaluateReplan({
    assessment: assess(100, 250), qualification_state: "qualified",
  });
  assert.equal(directive.directive, "replan");
  assert.deepEqual(directive.triggers, ["cost_reached_replan_threshold"]);
  assert.deepEqual(directive.permitted_levers, [...V5_REPLAN_LEVERS]);
});

test("REPLAN: a qualification failure fires a replan at any cost at all", () => {
  const directive = evaluateReplan({
    assessment: assess(100, 1), qualification_state: "qualification_failed",
  });
  assert.equal(directive.directive, "replan");
  assert.deepEqual(directive.triggers, ["qualification_failure"]);
  assert.equal(directive.variance_basis_points, 100,
    "the cost was one percent of plan and it replanned anyway");
});

test("REPLAN: both triggers can fire together and both are reported", () => {
  const directive = evaluateReplan({
    assessment: assess(100, 500), qualification_state: "qualification_failed",
  });
  assert.deepEqual(directive.triggers,
    ["cost_reached_replan_threshold", "qualification_failure"]);
});

test("REPLAN: a warning is a warning, not a replan", () => {
  const directive = evaluateReplan({
    assessment: assess(100, 160), qualification_state: "qualified",
  });
  assert.equal(directive.directive, "warn");
  assert.deepEqual(directive.triggers, []);
  assert.deepEqual(directive.permitted_levers, [],
    "a warning does not unlock the replan levers");
});

test("REPLAN: an unavailable variance does not silently become a continue when qualification failed", () => {
  const directive = evaluateReplan({
    assessment: assess(0, 900), qualification_state: "qualification_failed",
  });
  assert.equal(directive.directive, "replan");
  assert.equal(directive.variance_available, false);
  assert.equal(directive.variance_basis_points, null);
  assert.deepEqual(directive.triggers, ["qualification_failure"]);
});

test("REPLAN: every trigger a fixture can produce is inside the closed list", () => {
  const produced = new Set();
  for (const qualification_state of V5_QUALIFICATION_STATES) {
    for (const incurred of [0, 150, 250]) {
      const directive = evaluateReplan({
        assessment: assess(100, incurred), qualification_state,
      });
      directive.triggers.forEach(trigger => produced.add(trigger));
    }
  }
  assert.deepEqual([...produced].sort(), [...V5_REPLAN_TRIGGERS]);
});

test("REPLAN: NO input produces unqualified routing or a weakened quality floor", () => {
  const results = [];
  for (const qualification_state of V5_QUALIFICATION_STATES) {
    for (let incurred = 0; incurred <= 1000; incurred += 25) {
      results.push(evaluateReplan({ assessment: assess(100, incurred), qualification_state }));
    }
    results.push(evaluateReplan({ assessment: assess(0, 5000), qualification_state }));
  }
  assert.equal(results.length, 84);
  for (const directive of results) {
    assert.equal(directive.permits_unqualified_routing, false);
    assert.equal(directive.permits_quality_downgrade, false);
    for (const lever of directive.permitted_levers) {
      assert.ok(V5_REPLAN_LEVERS.includes(lever), `${lever} is outside the closed lever list`);
    }
    assert.ok(V5_VARIANCE_DIRECTIVES.includes(directive.directive));
  }
  // The sweep is worthless if it never reached the interesting states.
  assert.ok(results.some(entry => entry.directive === "continue"));
  assert.ok(results.some(entry => entry.directive === "warn"));
  assert.ok(results.some(entry => entry.directive === "replan"));
});

test("REPLAN: no lever is a cheaper-but-unqualified route or a lowered floor", () => {
  const text = JSON.stringify(V5_REPLAN_LEVERS);
  for (const forbidden of ["unqualified", "downgrade", "lower", "weaken", "cheaper", "skip_review"]) {
    assert.ok(!text.includes(forbidden), `${forbidden} appears among the replan levers`);
  }
  assert.deepEqual([...V5_REPLAN_LEVERS], [...V5_REPLAN_LEVERS].sort());
});

test("REPLAN: the directive carries Q142's overdrawn remedies rather than a second copy", () => {
  const directive = evaluateReplan({
    assessment: assess(100, 250), qualification_state: "qualified",
  });
  assert.deepEqual(directive.overdrawn_remedies, [...V5_OVERDRAWN_REMEDIES]);
});

test("REPLAN: a raw object is not a variance assessment", () => {
  refuses(() => evaluateReplan({
    assessment: { directive: "continue" }, qualification_state: "qualified",
  }), "invalid_shape");
});

test("REPLAN: an unregistered qualification state is refused by name", () => {
  refuses(() => evaluateReplan({
    assessment: assess(100, 100), qualification_state: "probably_fine",
  }), "unknown_qualification_state");
});

// --- continue only when value and quality remain justified -----------------

test("CONTINUE: both justifications true is the only way through", () => {
  const directive = evaluateReplan({
    assessment: assess(100, 250), qualification_state: "qualified",
  });
  const ok = continueUnderReplan({
    directive,
    justification: { value_still_justified: true, quality_floor_still_met: true },
  });
  assert.equal(ok.continued, true);
  assert.deepEqual(ok.blockers, []);
  assert.equal(ok.lever, null);
});

test("CONTINUE: either justification false stops the work", () => {
  const directive = evaluateReplan({
    assessment: assess(100, 250), qualification_state: "qualified",
  });
  const noValue = continueUnderReplan({
    directive,
    justification: { value_still_justified: false, quality_floor_still_met: true },
  });
  assert.equal(noValue.continued, false);
  assert.equal(noValue.lever, "stop_work");
  assert.deepEqual(noValue.blockers, ["value_no_longer_justified"]);

  const noQuality = continueUnderReplan({
    directive,
    justification: { value_still_justified: true, quality_floor_still_met: false },
  });
  assert.equal(noQuality.continued, false);
  assert.deepEqual(noQuality.blockers, ["quality_no_longer_justified"]);
});

test("CONTINUE: a failed qualification cannot be justified past", () => {
  const directive = evaluateReplan({
    assessment: assess(100, 10), qualification_state: "qualification_failed",
  });
  const attempted = continueUnderReplan({
    directive,
    justification: { value_still_justified: true, quality_floor_still_met: true },
  });
  assert.equal(attempted.continued, false);
  assert.deepEqual(attempted.blockers, ["qualification_failed"]);
});

test("CONTINUE: there is no third field that buys a continuation", () => {
  const directive = evaluateReplan({
    assessment: assess(100, 250), qualification_state: "qualified",
  });
  refuses(() => continueUnderReplan({
    directive,
    justification: {
      value_still_justified: false, quality_floor_still_met: false,
      approved_by_agent: true,
    },
  }), "unknown_field");
  refuses(() => continueUnderReplan({
    directive,
    justification: { value_still_justified: "yes", quality_floor_still_met: true },
  }), "invalid_shape");
});

test("CONTINUE: every combination is swept, and none permits a downgrade", () => {
  const outcomes = [];
  for (const qualification_state of V5_QUALIFICATION_STATES) {
    for (const value_still_justified of [true, false]) {
      for (const quality_floor_still_met of [true, false]) {
        const directive = evaluateReplan({
          assessment: assess(100, 250), qualification_state,
        });
        outcomes.push(continueUnderReplan({
          directive, justification: { value_still_justified, quality_floor_still_met },
        }));
      }
    }
  }
  assert.equal(outcomes.length, 8);
  assert.equal(outcomes.filter(entry => entry.continued).length, 1,
    "exactly one of the eight — qualified, valuable and still at quality — may continue");
  for (const entry of outcomes) {
    assert.equal(entry.permits_unqualified_routing, false);
    assert.equal(entry.permits_quality_downgrade, false);
  }
});

// --- Q128's escalation rule ------------------------------------------------

test("ESCALATION: exactly three of the registered change kinds reach a human", () => {
  const escalating = V5_REPLAN_CHANGE_KINDS.filter(kind =>
    classifyHumanEscalation({ change_kinds: [kind], partner_slug: "joe" })
      .escalate_to_human_partner);
  assert.deepEqual(escalating, [...V5_HUMAN_ESCALATION_CHANGE_KINDS]);
  assert.equal(escalating.length, 3);
  assert.ok(V5_REPLAN_CHANGE_KINDS.length > 3,
    "a vocabulary where everything escalates would make Q128's 'only' meaningless");
});

test("ESCALATION: a route, vendor, slack or in-envelope cost change involves nobody", () => {
  for (const kind of ["route_change", "vendor_change", "schedule_slack_change",
    "cost_within_envelope_change"]) {
    const result = classifyHumanEscalation({ change_kinds: [kind], partner_slug: null });
    assert.equal(result.escalate_to_human_partner, false, `${kind} escalated`);
    assert.equal(result.reason_id, "no_human_escalation");
    assert.equal(result.partner_slug, null);
  }
});

test("ESCALATION: one envelope change among many harmless ones still reaches a human", () => {
  const result = classifyHumanEscalation({
    change_kinds: ["route_change", "vendor_change", "envelope_change", "schedule_slack_change"],
    partner_slug: "joe",
  });
  assert.equal(result.escalate_to_human_partner, true);
  assert.deepEqual(result.escalating_change_kinds, ["envelope_change"]);
});

test("ESCALATION: the partner is identity.js's, and either partner is equally valid", () => {
  for (const slug of ["joe", "dell"]) {
    assert.ok(isKnownPartner(slug), `${slug} is not a known partner in identity.js`);
    const result = classifyHumanEscalation({
      change_kinds: ["quality_change"], partner_slug: slug,
    });
    assert.equal(result.escalate_to_human_partner, true);
    assert.equal(result.partner_slug, slug);
  }
});

test("ESCALATION: an escalating change with no known partner throws rather than escalating to nobody", () => {
  refuses(() => classifyHumanEscalation({
    change_kinds: ["critical_path_change"], partner_slug: null,
  }), "unknown_partner");
  refuses(() => classifyHumanEscalation({
    change_kinds: ["critical_path_change"], partner_slug: "not-a-partner",
  }), "unknown_partner");
});

test("ESCALATION: an unregistered change kind throws rather than being treated as harmless", () => {
  refuses(() => classifyHumanEscalation({
    change_kinds: ["quietly_swap_the_model"], partner_slug: "joe",
  }), "unknown_change_kind");
});

test("ESCALATION: an empty change list is a broken caller", () => {
  refuses(() => classifyHumanEscalation({ change_kinds: [], partner_slug: "joe" }),
    "invalid_shape");
});

// --- Q115's five measured dimensions ---------------------------------------

test("Q115: the five measured dimensions are read off the registry's own component names", () => {
  const components = {
    adjudication_cost_units: 3, builder_cost_units: 100, context_cost_units: 7,
    delegation_cost_units: 5, effort_cost_units: 11, escalation_cost_units: 2,
    failure_risk_cost_units: 13, retry_cost_units: 17, review_cost_units: 19,
    rework_cost_units: 23, tool_cost_units: 29,
  };
  const measured = measureQ115Dimensions(components);
  assert.deepEqual(measured.dimension_keys, [...V5_Q115_MEASURED_DIMENSIONS]);
  assert.equal(measured.measured_total_units, 3 + 100 + 17 + 19 + 23);
  assert.ok(measured.measured_total_units < Object.values(components).reduce((total, units) => total + units, 0),
    "Q115's five are a subset of Q040's eleven, not the whole total");
});

test("Q115: a basis missing one of the five is refused, not reported smaller", () => {
  refuses(() => measureQ115Dimensions({
    adjudication_cost_units: 1, builder_cost_units: 1,
    retry_cost_units: 1, review_cost_units: 1,
  }), "missing_field");
});

// --- the ledger seam -------------------------------------------------------

function ledgerFixture(estimate, steps) {
  const tree = compileScopeTree({
    schema_version: V5_SCOPE_TREE_SCHEMA_VERSION,
    tree_id: "tree:variance", tree_version: 1,
    nodes: [
      { node_id: "p:one", parent_node_id: null, scope_kind: "portfolio", authorization_ceiling_units: 10000 },
      { node_id: "c:one", parent_node_id: "p:one", scope_kind: "child", authorization_ceiling_units: 10000 },
      { node_id: "s:one", parent_node_id: "c:one", scope_kind: "slice", authorization_ceiling_units: 10000 },
    ],
  });
  const estimateSteps = estimate === null ? [] : [{
    kind: "record_estimate", operation: {
      operation_id: "op:est", node_id: "s:one", expected_total_cost_units: estimate,
      basis_digest: "sha256:aa", recorded_at: "2026-09-11T12:00:00.000Z",
    },
  }];
  const run = applyOperations(openLedger(tree), [...estimateSteps, ...steps]);
  return projectLedger(run.ledger);
}

test("LEDGER SEAM: a slice at 250 percent of its estimate replans", () => {
  const projection = ledgerFixture(100, [{
    kind: "post_actual", operation: {
      operation_id: "op:a", node_id: "s:one", amount_units: 250,
      vendor_reference: "INV-1", incurred_at: "2026-09-11T12:00:00.000Z",
    },
  }]);
  const assessment = assessNodeVariance({ projection, node_id: "s:one" });
  assert.equal(assessment.available, true);
  assert.equal(assessment.variance_basis_points, 25000);
  assert.equal(assessment.directive, "replan");
  assert.equal(assessment.scope_kind, "slice");
});

test("LEDGER SEAM: the estimate rolls up, so an ancestor is measured against its whole subtree", () => {
  const projection = ledgerFixture(100, [{
    kind: "post_actual", operation: {
      operation_id: "op:a", node_id: "s:one", amount_units: 160,
      vendor_reference: "INV-1", incurred_at: "2026-09-11T12:00:00.000Z",
    },
  }]);
  for (const nodeId of ["s:one", "c:one", "p:one"]) {
    const assessment = assessNodeVariance({ projection, node_id: nodeId });
    assert.equal(assessment.directive, "warn", `${nodeId} did not warn`);
    assert.equal(assessment.variance_basis_points, 16000);
  }
});

test("LEDGER SEAM: an outstanding reservation is NOT counted as incurred", () => {
  const projection = ledgerFixture(100, [{
    kind: "reserve", operation: {
      operation_id: "op:r", node_id: "s:one", reservation_id: "res:1",
      amount_units: 900, requested_at: "2026-09-11T12:00:00.000Z",
    },
  }]);
  const assessment = assessNodeVariance({ projection, node_id: "s:one" });
  assert.equal(assessment.incurred_units, 0);
  assert.equal(assessment.directive, "continue",
    "a 900-unit intention against a 100-unit estimate must not replan work that has spent nothing");
  assert.equal(projection.by_node["s:one"].rolled_up.committed_units, 900);
});

test("LEDGER SEAM: an unsettled liability IS counted as incurred", () => {
  const projection = ledgerFixture(100, [{
    kind: "record_late_liability", operation: {
      operation_id: "op:l", node_id: "s:one", liability_id: "liab:1",
      amount_units: 210, vendor_reference: "INV-1", incurred_at: "2026-09-11T12:00:00.000Z",
    },
  }]);
  const assessment = assessNodeVariance({ projection, node_id: "s:one" });
  assert.equal(assessment.incurred_units, 210);
  assert.equal(assessment.directive, "replan");
});

test("LEDGER SEAM: a node with no estimate is unavailable, not a continue", () => {
  const projection = ledgerFixture(null, [{
    kind: "post_actual", operation: {
      operation_id: "op:a", node_id: "s:one", amount_units: 500,
      vendor_reference: "INV-1", incurred_at: "2026-09-11T12:00:00.000Z",
    },
  }]);
  const assessment = assessNodeVariance({ projection, node_id: "s:one" });
  assert.equal(assessment.available, false);
  assert.equal(assessment.reason_id, "estimate_not_recorded");
});

test("LEDGER SEAM: a node the projection does not carry throws", () => {
  const projection = ledgerFixture(100, []);
  refuses(() => assessNodeVariance({ projection, node_id: "s:missing" }), "unknown_node");
});

// --- the projection --------------------------------------------------------

test("PROJECTION: the thresholds and every closed vocabulary are hashed in", () => {
  const preimage = v5CostVariancePreimage();
  assert.equal(preimage.warning_threshold_basis_points, 15000);
  assert.equal(preimage.replan_threshold_basis_points, 20000);
  const text = JSON.stringify(preimage);
  for (const name of [...V5_VARIANCE_DIRECTIVES, ...V5_REPLAN_TRIGGERS, ...V5_REPLAN_LEVERS,
    ...V5_REPLAN_CHANGE_KINDS, ...V5_QUALIFICATION_STATES, ...V5_Q115_MEASURED_DIMENSIONS]) {
    assert.ok(text.includes(name), `${name} is not in the hashed preimage`);
  }
});

test("PROJECTION: the honest boundary is stated, not implied", () => {
  const projection = v5CostVarianceProjection();
  assert.equal(projection.thresholds_are_decision_text_not_policy_input, true);
  assert.equal(projection.threshold_overridable_by_caller, false);
  assert.equal(projection.replan_can_permit_unqualified_routing, false);
  assert.equal(projection.replan_can_weaken_quality, false);
  assert.equal(projection.numerator_is_incurred_not_committed, true);
  assert.equal(projection.escalation_scoped_by_which_partner, false);
  assert.equal(projection.partner_vocabulary_owner, "identity.js#isKnownPartner");
  assert.equal(projection.tenant, ORGANIZATION_TENANT_ID);
  assert.ok(projection.unimplemented_dependencies.some(gap =>
    gap.includes("automated review runner")));
  assert.ok(projection.unimplemented_dependencies.some(gap => gap.includes("incident opener")));
});

test("PROJECTION: a returned directive cannot be mutated by its caller", () => {
  const directive = evaluateReplan({
    assessment: assess(100, 250), qualification_state: "qualified",
  });
  assert.equal(directive.schema_version, V5_REPLAN_DIRECTIVE_SCHEMA_VERSION);
  assert.throws(() => { directive.permits_quality_downgrade = true; }, TypeError);
  assert.throws(() => { directive.permitted_levers.push("lower_the_floor"); }, TypeError);
});
