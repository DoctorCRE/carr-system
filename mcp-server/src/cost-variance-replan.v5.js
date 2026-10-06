// DoctorCRE v5 slice V5-A04: ESTIMATE VARIANCE, THE 150/200 THRESHOLDS, AND
// WHAT A REPLAN IS ALLOWED TO CHANGE (decisions Q115.D1, Q128.D1).
//
// THE TWO SETTLED SENTENCES THIS FILE EXISTS TO ENFORCE:
//
//   Q115.D1 — "Measure total builder, retry, review, adjudication, and rework
//   cost; major estimate error triggers automated review and replan but never
//   unqualified routing or weakened quality."
//
//   Q128.D1 — "Warn at 150 percent expected cost, automatically replan at 200
//   percent or qualification failure, continue only when value and quality
//   remain justified, and involve Joe only for envelope, critical-path, or
//   quality changes."
//
// WHY THE NUMBERS ARE CONSTANTS HERE AND POLICY INPUTS NEXT DOOR. V5-F04's
// routing kernel holds no number of its own because every number it uses is a
// business judgement that a policy author owns. 150 and 200 are not that: they
// are the settled decision's own text, and a caller who could pass 300 instead
// would be overriding Q128 rather than configuring this module. So they are
// exported constants, spelled in basis points, with the decision id on the
// line. Changing one means changing the decision.
//
// WHAT "COST" MEANS IN THE NUMERATOR, and it is the clause most easily got
// wrong. The comparison is INCURRED against EXPECTED. Incurred is actuals plus
// liabilities still owed — money that is really gone. It is NOT commitments:
// an outstanding reservation is an intention and can still be cancelled, and
// counting one as a 200-percent overrun would replan a slice that had not yet
// spent anything. The ledger's projection already separates the two and this
// module reads `incurred_units`, never `committed_units`.
//
// THE THING A REPLAN MAY NEVER DO. Q115 is explicit that a major estimate error
// "never" buys unqualified routing or weakened quality, so the replan directive
// this module returns carries `permits_unqualified_routing: false` and
// `permits_quality_downgrade: false` as CONSTANTS on every answer, and the four
// levers it may pull are a closed list that contains no cheaper-but-unqualified
// option. There is no input — no threshold, no justification, no escalation —
// that produces a fifth lever or flips either flag. That is tested by sweeping
// every combination the closed vocabularies admit, not by spot checks.
//
// "INVOLVE JOE ONLY FOR..." IS READ AS THE HUMAN PARTNER, NOT AS JOE'S NAME.
// Rule b42e217e forbids scoping capability by WHICH partner holds it — Dell is
// a full writer and teacher, equal to Joe — so Q128's escalation is implemented
// as "escalate to a human partner", with the partner slug validated against
// identity.js's `isKnownPartner` rather than compared to a literal. The
// RESTRICTIVE half of the clause, which is the half that does work, is
// unchanged and enforced exactly: only an envelope, critical-path or quality
// change escalates, and every other change kind is handled without a human.
//
// A SHORTFALL IS AN ANSWER; A MALFORMED INPUT IS A THROW, as everywhere else in
// this slice. "No estimate was ever recorded, so there is no denominator" is a
// fact about the ledger and is RETURNED as `unavailable`. "This change kind is
// not registered" is a contract violation and THROWS.
//
// NO EFFECTS. No filesystem, no network, no database, no environment and no
// clock. This module does not open the incident it says is required, because
// opening one is the record layer's authority-bound verb and no pure module can
// reach it; the projection says so rather than imitating it.

import { digest } from "./artifact-trust.js";
import { ORGANIZATION_TENANT_ID, isKnownPartner } from "./identity.js";
import { V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import {
  V5_OVERDRAWN_REMEDIES,
  ledgerVersion,
  projectLedger,
} from "./hierarchical-cost-ledger.v5.js";

export const V5_VARIANCE_SCHEMA_VERSION = "doctorcre-v5-cost-variance-replan.v1";
export const V5_VARIANCE_RESULT_SCHEMA_VERSION = "cost-variance-assessment.v1";
export const V5_REPLAN_DIRECTIVE_SCHEMA_VERSION = "cost-replan-directive.v1";

/** Ratios are integer basis points: 10000 is one hundred percent, exactly on plan. */
export const V5_COST_BASIS_POINTS = 10000;

/** Q128.D1: "Warn at 150 percent expected cost". */
export const V5_WARNING_THRESHOLD_BASIS_POINTS = 15000;

/** Q128.D1: "automatically replan at 200 percent or qualification failure". */
export const V5_REPLAN_THRESHOLD_BASIS_POINTS = 20000;

/** The three directives a variance can produce, C-sorted. */
export const V5_VARIANCE_DIRECTIVES = Object.freeze(["continue", "replan", "warn"]);

/** Why a replan fired, C-sorted. Both are Q128.D1's own two triggers. */
export const V5_REPLAN_TRIGGERS = Object.freeze([
  "cost_reached_replan_threshold",
  "qualification_failure",
]);

/**
 * THE CLOSED LIST OF LEVERS A REPLAN MAY PULL.
 *
 * Every one of them keeps the quality floor and the qualification gate where
 * they are. There is deliberately no "route to a cheaper unqualified model" and
 * no "lower the floor": Q115 forbids both by name, and a lever list that
 * contained either would make the flags below decorative.
 */
export const V5_REPLAN_LEVERS = Object.freeze([
  "re_estimate_and_reauthorize",
  "reduce_scope",
  "request_authority_amendment",
  "stop_work",
]);

/** Whether the route in play still holds a current qualification. */
export const V5_QUALIFICATION_STATES = Object.freeze(["qualification_failed", "qualified"]);

/**
 * THE CLOSED LIST OF CHANGE KINDS A REPLAN CAN PROPOSE, C-sorted, and exactly
 * three of them reach a human. The non-escalating four are here so the rule has
 * something to be false about: a vocabulary in which everything escalates would
 * satisfy Q128's letter and defeat its point, which is that a human is involved
 * ONLY for these three.
 */
export const V5_REPLAN_CHANGE_KINDS = Object.freeze([
  "cost_within_envelope_change",
  "critical_path_change",
  "envelope_change",
  "quality_change",
  "route_change",
  "schedule_slack_change",
  "vendor_change",
]);

/** Q128.D1's three, and only these three, C-sorted. */
export const V5_HUMAN_ESCALATION_CHANGE_KINDS = Object.freeze([
  "critical_path_change",
  "envelope_change",
  "quality_change",
]);

/**
 * Q115.D1's measured dimensions, held here as the list Q115 asks to be
 * measured so a reader can see the sentence and the field names line up.
 */
export const V5_Q115_MEASURED_DIMENSIONS = Object.freeze([
  "adjudication_cost_units",
  "builder_cost_units",
  "retry_cost_units",
  "review_cost_units",
  "rework_cost_units",
]);

/** Why a variance assessment can be unavailable. */
export const V5_VARIANCE_UNAVAILABLE_REASONS = Object.freeze(["estimate_not_recorded"]);

const REF = /^[a-z0-9][a-z0-9_.:/-]{0,127}$/;

export class V5VarianceError extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = "V5VarianceError";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function fail(code, message, detail) {
  throw new V5VarianceError(code, message, detail);
}

function isPlainObject(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function deepFreeze(value) {
  if (Array.isArray(value)) { value.forEach(deepFreeze); return Object.freeze(value); }
  if (isPlainObject(value)) { Object.values(value).forEach(deepFreeze); return Object.freeze(value); }
  return value;
}

function copy(value) {
  return JSON.parse(JSON.stringify(value));
}

function assertObject(value, path) {
  if (!isPlainObject(value)) fail("invalid_shape", `${path} must be a plain object`, { path });
}

function assertClosedKeys(value, allowed, path) {
  const unknown = Object.keys(value).filter(key => !allowed.includes(key)).sort();
  if (unknown.length > 0) {
    fail("unknown_field", `${path} carries fields this contract does not declare`,
      { path, unknown, allowed: [...allowed] });
  }
}

function assertRequiredKeys(value, required, path) {
  const missing = required.filter(key => value[key] === undefined).sort();
  if (missing.length > 0) {
    fail("missing_field", `${path} is missing required fields`, { path, missing });
  }
}

function assertRef(value, path) {
  if (typeof value !== "string" || !REF.test(value)) {
    fail("invalid_reference", `${path} must be a lower-case reference token`, { path, value });
  }
  return value;
}

function assertCostUnits(value, path, { minimum = 0 } = {}) {
  if (!Number.isSafeInteger(value) || value < minimum) {
    fail("invalid_cost_units", `${path} must be a safe integer of cost units >= ${minimum}`,
      { path, value });
  }
  return value;
}

function assertMember(value, allowed, path, code) {
  if (!allowed.includes(value)) {
    fail(code, `${path} must be one of ${allowed.join(", ")}`,
      { path, value, allowed: [...allowed] });
  }
  return value;
}

// ---------------------------------------------------------------------------
// Module-load invariant: the escalating kinds are a real subset.
// ---------------------------------------------------------------------------

{
  const unknown = V5_HUMAN_ESCALATION_CHANGE_KINDS
    .filter(kind => !V5_REPLAN_CHANGE_KINDS.includes(kind));
  if (unknown.length > 0) {
    fail("invalid_vocabulary", "an escalating change kind is not a registered change kind",
      { unknown });
  }
  if (V5_HUMAN_ESCALATION_CHANGE_KINDS.length >= V5_REPLAN_CHANGE_KINDS.length) {
    fail("invalid_vocabulary",
      "if every change kind escalates then Q128's word 'only' means nothing; the vocabulary must contain non-escalating kinds",
      { escalating: V5_HUMAN_ESCALATION_CHANGE_KINDS.length, all: V5_REPLAN_CHANGE_KINDS.length });
  }
}

// ---------------------------------------------------------------------------
// The variance.
// ---------------------------------------------------------------------------

const VARIANCE_KEYS = Object.freeze(["expected_total_cost_units", "incurred_units"]);

/**
 * THE RATIO, and the one arithmetic decision in this file.
 *
 * `floor(incurred * 10000 / expected)`, in integers. Floor rather than round
 * because a threshold is a floor: 199.99 percent has not reached 200 percent,
 * and rounding it up would replan work that is still inside its envelope.
 * Integer arithmetic throughout so the answer does not depend on floating-point
 * representation — the same reason F04 states pass rates in basis points.
 */
export function varianceBasisPoints(expectedTotalCostUnits, incurredUnits) {
  const expected = assertCostUnits(expectedTotalCostUnits, "expected_total_cost_units", { minimum: 1 });
  const incurred = assertCostUnits(incurredUnits, "incurred_units");
  return Math.floor((incurred * V5_COST_BASIS_POINTS) / expected);
}

/**
 * Classify a ratio against Q128's two thresholds.
 *
 * The boundaries are INCLUSIVE at each threshold: "warn at 150 percent" means
 * exactly 150 percent warns, and exactly 200 percent replans. Both boundary
 * cases are tested, because an off-by-one here is the difference between a
 * threshold that fires and one that never quite does.
 */
export function directiveForBasisPoints(basisPoints) {
  if (!Number.isInteger(basisPoints) || basisPoints < 0) {
    fail("invalid_shape", "basisPoints must be a non-negative integer",
      { path: "basisPoints", value: basisPoints });
  }
  if (basisPoints >= V5_REPLAN_THRESHOLD_BASIS_POINTS) return "replan";
  if (basisPoints >= V5_WARNING_THRESHOLD_BASIS_POINTS) return "warn";
  return "continue";
}

/**
 * Assess one node's incurred cost against its recorded estimate.
 *
 * Takes the numbers rather than the ledger so the arithmetic can be tested
 * without building a tree; `assessNodeVariance` below is the ledger-shaped
 * front door and is the one callers should use.
 */
export function assessVariance(input) {
  assertObject(input, "variance");
  assertClosedKeys(input, VARIANCE_KEYS, "variance");
  assertRequiredKeys(input, VARIANCE_KEYS, "variance");
  const expected = input.expected_total_cost_units;
  const incurred = assertCostUnits(input.incurred_units, "variance.incurred_units");

  // An estimate of zero is not a small estimate, it is an absent one: nothing
  // can be a percentage of it, and answering "infinitely over" would be a
  // number nobody could act on. This is the honest-unavailable path.
  if (expected === 0) {
    return deepFreeze({
      schema_version: V5_VARIANCE_RESULT_SCHEMA_VERSION,
      available: false,
      reason_id: "estimate_not_recorded",
      detail: "no estimate has been recorded for this scope, so there is no denominator; record one before asking whether the work is over it",
      incurred_units: incurred,
    });
  }
  const basisPoints = varianceBasisPoints(expected, incurred);
  return deepFreeze({
    schema_version: V5_VARIANCE_RESULT_SCHEMA_VERSION,
    available: true,
    expected_total_cost_units: expected,
    incurred_units: incurred,
    variance_units: incurred - expected,
    variance_basis_points: basisPoints,
    directive: directiveForBasisPoints(basisPoints),
    warning_threshold_basis_points: V5_WARNING_THRESHOLD_BASIS_POINTS,
    replan_threshold_basis_points: V5_REPLAN_THRESHOLD_BASIS_POINTS,
  });
}

/**
 * The ledger-shaped front door: assess one node of a ledger projection.
 *
 * READS `incurred_units`, NOT `committed_units`. See the header: a reservation
 * is an intention and cancelling one costs nothing, so counting commitments
 * would replan work that has not spent a unit.
 */
export function assessNodeVariance({ projection, node_id } = {}) {
  if (!isPlainObject(projection) || !isPlainObject(projection.by_node)) {
    fail("invalid_shape", "assessNodeVariance takes a ledger projection", { path: "projection" });
  }
  assertRef(node_id, "node_id");
  const node = projection.by_node[node_id];
  if (node === undefined) {
    fail("unknown_node", "node_id names a node this projection does not carry",
      { path: "node_id", node_id });
  }
  const assessment = assessVariance({
    expected_total_cost_units: node.rolled_up.estimate_units,
    incurred_units: node.rolled_up.incurred_units,
  });
  return deepFreeze({
    ...assessment,
    node_id,
    scope_kind: node.scope_kind,
    overdrawn: node.overdrawn,
  });
}

// ---------------------------------------------------------------------------
// The replan directive.
// ---------------------------------------------------------------------------

const REPLAN_KEYS = Object.freeze(["assessment", "qualification_state"]);

/**
 * Decide whether to continue, warn, or replan, and say what a replan may do.
 *
 * QUALIFICATION FAILURE IS CHECKED FIRST AND WINS OUTRIGHT. Q128 names it as a
 * replan trigger in its own right, so a route that lost its qualification
 * replans at ten percent of budget just as it does at two hundred. Asking the
 * cost question first and returning `continue` would let a comfortably cheap
 * unqualified route carry on, which is the exact failure Q115's "never
 * unqualified routing" forbids.
 *
 * An UNAVAILABLE assessment does not silently become `continue`: with no
 * estimate there is no cost question to answer, so the directive is whatever
 * the qualification state says and the cost trigger is simply absent.
 */
export function evaluateReplan(input) {
  assertObject(input, "replan");
  assertClosedKeys(input, REPLAN_KEYS, "replan");
  assertRequiredKeys(input, REPLAN_KEYS, "replan");
  const qualificationState = assertMember(input.qualification_state, V5_QUALIFICATION_STATES,
    "replan.qualification_state", "unknown_qualification_state");
  const assessment = input.assessment;
  if (!isPlainObject(assessment)
    || assessment.schema_version !== V5_VARIANCE_RESULT_SCHEMA_VERSION) {
    fail("invalid_shape", "replan.assessment must be a variance assessment",
      { path: "replan.assessment" });
  }

  const triggers = [];
  if (qualificationState === "qualification_failed") triggers.push("qualification_failure");
  if (assessment.available === true && assessment.directive === "replan") {
    triggers.push("cost_reached_replan_threshold");
  }
  triggers.sort();

  let directive;
  if (triggers.length > 0) {
    directive = "replan";
  } else if (assessment.available === true) {
    directive = assessment.directive;
  } else {
    directive = "continue";
  }

  return deepFreeze({
    schema_version: V5_REPLAN_DIRECTIVE_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    directive,
    triggers,
    variance_available: assessment.available === true,
    variance_basis_points: assessment.available === true ? assessment.variance_basis_points : null,
    qualification_state: qualificationState,
    // The levers, and the two flags Q115 puts beyond reach. These are constants
    // on every answer this function can produce; no input reaches them.
    permitted_levers: directive === "replan" ? [...V5_REPLAN_LEVERS] : [],
    permits_unqualified_routing: false,
    permits_quality_downgrade: false,
    continue_permitted_only_if: "value_and_quality_remain_justified",
    // Q142's remedies travel with an overdrawn scope; named here so a caller
    // holding only the directive still sees what an overdrawn hierarchy owes.
    overdrawn_remedies: [...V5_OVERDRAWN_REMEDIES],
  });
}

const CONTINUE_KEYS = Object.freeze(["directive", "justification"]);
const JUSTIFICATION_KEYS = Object.freeze(["value_still_justified", "quality_floor_still_met"]);

/**
 * Q128's "continue only when value and quality remain justified", as a gate.
 *
 * BOTH must be true. Either false — or a qualification that has already failed,
 * which no justification can talk its way past — ends in `stop_work`. The
 * justification's keys are closed, so there is no third field a caller could
 * add to buy a continuation, and `quality_floor_still_met: false` can never
 * produce one: that would be the weakened quality Q115 forbids.
 */
export function continueUnderReplan(input) {
  assertObject(input, "continue");
  assertClosedKeys(input, CONTINUE_KEYS, "continue");
  assertRequiredKeys(input, CONTINUE_KEYS, "continue");
  const directive = input.directive;
  if (!isPlainObject(directive)
    || directive.schema_version !== V5_REPLAN_DIRECTIVE_SCHEMA_VERSION) {
    fail("invalid_shape", "continue.directive must be a replan directive",
      { path: "continue.directive" });
  }
  const justification = input.justification;
  assertObject(justification, "continue.justification");
  assertClosedKeys(justification, JUSTIFICATION_KEYS, "continue.justification");
  assertRequiredKeys(justification, JUSTIFICATION_KEYS, "continue.justification");
  for (const key of JUSTIFICATION_KEYS) {
    if (typeof justification[key] !== "boolean") {
      fail("invalid_shape", `continue.justification.${key} must be a boolean`,
        { path: `continue.justification.${key}`, value: justification[key] });
    }
  }

  const blockers = [];
  if (directive.qualification_state === "qualification_failed") {
    blockers.push("qualification_failed");
  }
  if (justification.quality_floor_still_met !== true) blockers.push("quality_no_longer_justified");
  if (justification.value_still_justified !== true) blockers.push("value_no_longer_justified");
  blockers.sort();

  return deepFreeze({
    schema_version: V5_REPLAN_DIRECTIVE_SCHEMA_VERSION,
    continued: blockers.length === 0,
    lever: blockers.length === 0 ? null : "stop_work",
    blockers,
    permits_unqualified_routing: false,
    permits_quality_downgrade: false,
  });
}

// ---------------------------------------------------------------------------
// Q128's escalation rule.
// ---------------------------------------------------------------------------

const ESCALATION_KEYS = Object.freeze(["change_kinds", "partner_slug"]);

/**
 * Decide whether a human partner is involved at all, by the ordered procedure:
 *
 *   1. Is every proposed change kind registered? If not, THROW — an unregistered
 *      kind is a broken caller and must not be silently treated as harmless.
 *   2. Does the set intersect the three escalating kinds? If not, the answer is
 *      `no_human_escalation` and the partner slug is irrelevant.
 *   3. If it does, a known partner must be named. `isKnownPartner` is
 *      identity.js's test, not a second list, and an unknown slug THROWS rather
 *      than escalating to nobody.
 *
 * The escalating set is checked by INTERSECTION rather than by "the first kind",
 * so a batch containing one envelope change among six harmless ones still
 * reaches a human.
 */
export function classifyHumanEscalation(input) {
  assertObject(input, "escalation");
  assertClosedKeys(input, ESCALATION_KEYS, "escalation");
  assertRequiredKeys(input, ESCALATION_KEYS, "escalation");
  if (!Array.isArray(input.change_kinds) || input.change_kinds.length === 0) {
    fail("invalid_shape", "escalation.change_kinds must be a non-empty array",
      { path: "escalation.change_kinds" });
  }
  const kinds = input.change_kinds.map((kind, index) =>
    assertMember(kind, V5_REPLAN_CHANGE_KINDS, `escalation.change_kinds[${index}]`,
      "unknown_change_kind"));
  const escalating = [...new Set(
    kinds.filter(kind => V5_HUMAN_ESCALATION_CHANGE_KINDS.includes(kind)))].sort();

  if (escalating.length === 0) {
    return deepFreeze({
      schema_version: V5_REPLAN_DIRECTIVE_SCHEMA_VERSION,
      escalate_to_human_partner: false,
      reason_id: "no_human_escalation",
      change_kinds: [...new Set(kinds)].sort(),
      escalating_change_kinds: [],
      partner_slug: null,
    });
  }
  if (input.partner_slug === null || !isKnownPartner(input.partner_slug)) {
    fail("unknown_partner",
      "an escalating change must name a known human partner; identity.js decides who that is",
      { path: "escalation.partner_slug", value: input.partner_slug });
  }
  return deepFreeze({
    schema_version: V5_REPLAN_DIRECTIVE_SCHEMA_VERSION,
    escalate_to_human_partner: true,
    reason_id: "envelope_critical_path_or_quality_change",
    change_kinds: [...new Set(kinds)].sort(),
    escalating_change_kinds: escalating,
    partner_slug: input.partner_slug,
  });
}

/**
 * Q115's five measured dimensions, pulled off a compiled cost basis.
 *
 * Takes the basis's own `components` object so the field names are the
 * registry's, not a second spelling of them; a basis missing one of the five
 * throws rather than reporting a smaller total.
 */
export function measureQ115Dimensions(components) {
  assertObject(components, "components");
  const measured = {};
  let total = 0;
  for (const key of V5_Q115_MEASURED_DIMENSIONS) {
    if (components[key] === undefined) {
      fail("missing_field", `components.${key} is one of Q115's five measured dimensions`,
        { path: `components.${key}`, required: [...V5_Q115_MEASURED_DIMENSIONS] });
    }
    measured[key] = assertCostUnits(components[key], `components.${key}`);
    total += measured[key];
  }
  return deepFreeze({
    dimensions: measured,
    measured_total_units: total,
    dimension_keys: [...V5_Q115_MEASURED_DIMENSIONS],
  });
}

// ---------------------------------------------------------------------------
// Firing: a warning or a replan is an EVENT at a crossing, not a state.
// ---------------------------------------------------------------------------
//
// `evaluateReplan` answers "where is this node now?" and has no memory, so a
// caller who asks it twice is told "warn" twice. Q128 says to WARN at 150 and
// REPLAN at 200, which happens once when the line is crossed rather than on
// every read. So a signal FIRES when, and only when, one committed operation
// moves a node's band UPWARD across a threshold.
//
// WHY THIS IS IDEMPOTENT BY CONSTRUCTION, not by a dedupe table:
//   - a crossing is computed from two ADJACENT ledger values, the one before an
//     operation and the one after it, and each operation produces exactly one
//     version, so each crossing belongs to exactly one version;
//   - a replay, a refusal and a race loser all leave the incurred totals
//     unchanged, so their before and after bands are equal and nothing fires;
//   - a signal id is `budget-signal:<scope>:<node>:<kind>:v<version>`, so two
//     readers of the same commit derive the same id and a persisting caller can
//     keep the first and drop the rest;
//   - staying above a threshold fires nothing. Falling back below it and then
//     crossing again is a NEW crossing at a NEW version and fires again with a
//     distinct id. That is the definition of "once per crossing", not a lapse.
//
// A jump from under 150 straight to 200 or more crosses BOTH lines in one
// operation and fires both, warning and replan, each once: both thresholds were
// crossed, and a reader counting warnings must not find one missing because the
// replan happened in the same step.
//
// A qualification failure fires a replan on the TRANSITION from qualified to
// qualification_failed. Staying failed fires nothing; recovering and failing
// again fires again. This module holds no qualification record of its own (see
// the projection), so the caller supplies both sides of the transition.

export const V5_BUDGET_SIGNAL_SCHEMA_VERSION = "cost-budget-signal.v1";

/** What a fired signal is, C-sorted. */
export const V5_BUDGET_SIGNAL_KINDS = Object.freeze(["replan", "warning"]);

/** Why it fired, C-sorted. */
export const V5_BUDGET_SIGNAL_TRIGGERS = Object.freeze([
  "cost_reached_replan_threshold",
  "cost_reached_warning_threshold",
  "qualification_failure",
]);

const BAND = Object.freeze({ continue: 0, warn: 1, replan: 2 });

const COST_CROSSINGS = Object.freeze([
  Object.freeze({ band: BAND.warn, kind: "warning", trigger: "cost_reached_warning_threshold",
    threshold_basis_points: V5_WARNING_THRESHOLD_BASIS_POINTS }),
  Object.freeze({ band: BAND.replan, kind: "replan", trigger: "cost_reached_replan_threshold",
    threshold_basis_points: V5_REPLAN_THRESHOLD_BASIS_POINTS }),
]);

/** Basis points as a percentage with two decimals, floored like the ratio. */
function percentText(basisPoints) {
  return `${Math.floor(basisPoints / 100)}.${String(basisPoints % 100).padStart(2, "0")}%`;
}

const REPLAN_OPTIONS_TEXT =
  "Choose one: re-estimate and get it re-approved, cut scope, ask for more authority, or stop. Switching to an unqualified route or lowering the quality bar is not allowed.";

function warningMessage(nodeId, variance) {
  return `Cost warning: ${nodeId} has now spent ${variance.incurred_units} units, ${percentText(variance.variance_basis_points)} of its ${variance.expected_total_cost_units}-unit estimate (the warning line is 150%). Work can continue. Check the estimate now: at 200% the work must be replanned.`;
}

function costReplanMessage(nodeId, variance) {
  return `Replan required: ${nodeId} has now spent ${variance.incurred_units} units, ${percentText(variance.variance_basis_points)} of its ${variance.expected_total_cost_units}-unit estimate (the replan line is 200%). ${REPLAN_OPTIONS_TEXT} Work continues only if it is still worth doing at the required quality.`;
}

function qualificationReplanMessage(nodeId) {
  return `Replan required: the route doing the work for ${nodeId} is no longer qualified, so the work must be replanned no matter how little has been spent. ${REPLAN_OPTIONS_TEXT}`;
}

function bandOf(variance) {
  return variance.available === true ? BAND[variance.directive] : BAND.continue;
}

function nodeVariance(projection, nodeId) {
  const node = projection.by_node[nodeId];
  return assessVariance({
    expected_total_cost_units: node.rolled_up.estimate_units,
    incurred_units: node.rolled_up.incurred_units,
  });
}

function replanFields() {
  return {
    permitted_levers: [...V5_REPLAN_LEVERS],
    permits_unqualified_routing: false,
    permits_quality_downgrade: false,
    continue_permitted_only_if: "value_and_quality_remain_justified",
  };
}

function sameEntry(a, b) {
  return b !== undefined && a.entry_id === b.entry_id && a.kind === b.kind
    && a.node_id === b.node_id && a.amount_units === b.amount_units;
}

/**
 * The cost signals ONE committed operation fired.
 *
 * Takes the ledger value before the operation and the value after it. The two
 * must be ADJACENT — the same tree, `after` extending `before`'s log, and at
 * most one applied operation ahead — because a signal is pinned to the version
 * that crossed, and comparing across several operations would pin a crossing
 * to whichever version the caller happened to stop at. Equal versions (a
 * replay) fire nothing. A gap, a different tree or a rewritten history THROWS.
 *
 * `scope_ref` names the stored ledger the signal belongs to (the durable
 * path's tree_ref, which is unique where tree_id is not). Without one the id
 * is scoped by tree_id and tree_version, which is unique only in-process.
 */
export function detectCostThresholdCrossings({
  before_ledger: before, after_ledger: after, scope_ref: scopeRefInput,
} = {}) {
  const beforeVersion = ledgerVersion(before);
  const afterVersion = ledgerVersion(after);
  // The tree digest covers tree_id, tree_version, every node and every
  // ceiling: two ledgers over different trees are never one lineage.
  if (before.tree.tree_digest !== after.tree.tree_digest) {
    fail("ledger_lineage_mismatch", "before_ledger and after_ledger are not the same scope tree",
      { before_tree_id: before.tree.tree_id, after_tree_id: after.tree.tree_id });
  }
  const scopeRef = scopeRefInput === undefined
    ? `${after.tree.tree_id}:t${after.tree.tree_version}`
    : assertRef(scopeRefInput, "scope_ref");
  const delta = afterVersion - beforeVersion;
  if (delta !== 0 && delta !== 1) {
    fail("ledger_versions_not_adjacent",
      "a crossing is detected across exactly one operation; after_ledger must be at before_ledger's version or one ahead",
      { before_version: beforeVersion, after_version: afterVersion });
  }
  if (after.entries.length < before.entries.length
    || before.entries.some((entry, index) => !sameEntry(entry, after.entries[index]))) {
    fail("ledger_lineage_mismatch", "after_ledger does not extend before_ledger's entry log",
      { before_entries: before.entries.length, after_entries: after.entries.length });
  }
  const newOperationIds = Object.keys(after.applied)
    .filter(id => before.applied[id] === undefined).sort();
  if (newOperationIds.length !== delta
    || Object.keys(before.applied).some(id => after.applied[id] === undefined)) {
    fail("ledger_lineage_mismatch", "after_ledger's applied operations do not extend before_ledger's",
      { new_operation_ids: newOperationIds });
  }
  if (delta === 0) return deepFreeze([]);

  const beforeProjection = projectLedger(before);
  const afterProjection = projectLedger(after);
  const treeId = after.tree.tree_id;
  const operationId = newOperationIds[0];
  const signals = [];
  for (const nodeId of after.tree.node_ids) {
    const was = nodeVariance(beforeProjection, nodeId);
    const now = nodeVariance(afterProjection, nodeId);
    const wasBand = bandOf(was);
    const nowBand = bandOf(now);
    for (const crossing of COST_CROSSINGS) {
      if (!(wasBand < crossing.band && crossing.band <= nowBand)) continue;
      signals.push({
        schema_version: V5_BUDGET_SIGNAL_SCHEMA_VERSION,
        tenant: ORGANIZATION_TENANT_ID,
        signal_id: `budget-signal:${scopeRef}:${nodeId}:${crossing.kind}:v${afterVersion}`,
        kind: crossing.kind,
        trigger: crossing.trigger,
        tree_id: treeId,
        node_id: nodeId,
        scope_kind: after.tree.nodes[nodeId].scope_kind,
        ledger_version: afterVersion,
        operation_id: operationId,
        threshold_basis_points: crossing.threshold_basis_points,
        variance_basis_points_before: was.available === true ? was.variance_basis_points : null,
        variance_basis_points_after: now.variance_basis_points,
        expected_total_cost_units: now.expected_total_cost_units,
        incurred_units: now.incurred_units,
        message: crossing.kind === "warning"
          ? warningMessage(nodeId, now)
          : costReplanMessage(nodeId, now),
        ...(crossing.kind === "replan" ? replanFields() : {}),
      });
    }
  }
  return deepFreeze(signals);
}

const QUALIFICATION_SIGNAL_KEYS = Object.freeze([
  "tree_id", "node_id", "before_state", "after_state", "observation_ref",
]);

/**
 * The replan a qualification failure fired, if it fired one.
 *
 * Fires only on `qualified` -> `qualification_failed`. `observation_ref` names
 * the observation that saw the failure and is part of the signal id, so the
 * same observation reported twice derives the same id.
 */
export function detectQualificationReplan(input) {
  assertObject(input, "qualification");
  assertClosedKeys(input, QUALIFICATION_SIGNAL_KEYS, "qualification");
  assertRequiredKeys(input, QUALIFICATION_SIGNAL_KEYS, "qualification");
  const treeId = assertRef(input.tree_id, "qualification.tree_id");
  const nodeId = assertRef(input.node_id, "qualification.node_id");
  const before = assertMember(input.before_state, V5_QUALIFICATION_STATES,
    "qualification.before_state", "unknown_qualification_state");
  const after = assertMember(input.after_state, V5_QUALIFICATION_STATES,
    "qualification.after_state", "unknown_qualification_state");
  const observationRef = assertRef(input.observation_ref, "qualification.observation_ref");
  if (!(before === "qualified" && after === "qualification_failed")) return deepFreeze([]);
  return deepFreeze([{
    schema_version: V5_BUDGET_SIGNAL_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    signal_id: `budget-signal:${treeId}:${nodeId}:replan:qualification:${observationRef}`,
    kind: "replan",
    trigger: "qualification_failure",
    tree_id: treeId,
    node_id: nodeId,
    observation_ref: observationRef,
    message: qualificationReplanMessage(nodeId),
    ...replanFields(),
  }]);
}

// ---------------------------------------------------------------------------
// The honest, zero-effect projection.
// ---------------------------------------------------------------------------

const PREIMAGE = deepFreeze({
  schema_version: V5_VARIANCE_SCHEMA_VERSION,
  cost_basis_points: V5_COST_BASIS_POINTS,
  warning_threshold_basis_points: V5_WARNING_THRESHOLD_BASIS_POINTS,
  replan_threshold_basis_points: V5_REPLAN_THRESHOLD_BASIS_POINTS,
  directives: [...V5_VARIANCE_DIRECTIVES],
  replan_triggers: [...V5_REPLAN_TRIGGERS],
  replan_levers: [...V5_REPLAN_LEVERS],
  qualification_states: [...V5_QUALIFICATION_STATES],
  change_kinds: [...V5_REPLAN_CHANGE_KINDS],
  human_escalation_change_kinds: [...V5_HUMAN_ESCALATION_CHANGE_KINDS],
  q115_measured_dimensions: [...V5_Q115_MEASURED_DIMENSIONS],
  variance_unavailable_reasons: [...V5_VARIANCE_UNAVAILABLE_REASONS],
  budget_signal_schema_version: V5_BUDGET_SIGNAL_SCHEMA_VERSION,
  budget_signal_kinds: [...V5_BUDGET_SIGNAL_KINDS],
  budget_signal_triggers: [...V5_BUDGET_SIGNAL_TRIGGERS],
});

/** The exact bytes every closed vocabulary and threshold in this module is hashed over. */
export function v5CostVariancePreimage() {
  return PREIMAGE;
}

export const V5_COST_VARIANCE_DIGEST = digest(copy(PREIMAGE));

/** What this module decides, what it refuses, and what is still missing. */
export function v5CostVarianceProjection() {
  return deepFreeze({
    schema_version: V5_VARIANCE_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    result_schema_version: V5_VARIANCE_RESULT_SCHEMA_VERSION,
    directive_schema_version: V5_REPLAN_DIRECTIVE_SCHEMA_VERSION,
    preimage_digest: V5_COST_VARIANCE_DIGEST,
    // Properties, not aspirations: each is enforced above and tested.
    thresholds_are_decision_text_not_policy_input: true,
    threshold_overridable_by_caller: false,
    replan_can_permit_unqualified_routing: false,
    replan_can_weaken_quality: false,
    numerator_is_incurred_not_committed: true,
    signal_fires_once_per_upward_crossing: true,
    signal_fires_on_replay_or_refusal: false,
    escalation_scoped_by_which_partner: false,
    partner_vocabulary_owner: "identity.js#isKnownPartner",
    overdrawn_remedy_owner: "hierarchical-cost-ledger.v5.js#V5_OVERDRAWN_REMEDIES",
    unimplemented_dependencies: [
      "an automated review runner: Q115.D1 says a major estimate error triggers automated review AND replan; this module produces the replan directive and names the review as owed, but running a review needs dispatch (V5-F06/V5-F07) and an assurance runner, neither of which exists in this repository",
      "an incident opener: an overdrawn hierarchy requires an incident, and open-incident is an authority-bound record-layer verb no pure module can call; the requirement is reported on every overdrawn projection and never satisfied here",
      "a qualification-state feed: this module takes qualification_state as a declared input and cannot itself tell a currently-qualified route from one whose record expired; authenticating current qualification requires a trusted verifier and admission integration that this slice does not implement",
      "a durable directive record: nothing this module returns is persisted, so a replan it directs leaves no trace the next process could read",
      "a durable signal record: a fired warning or replan is returned to the committing caller (commitLedgerOperation carries it) with a deterministic id, but no table stores it yet; persisting it needs its own migration and SCAC successor seal",
    ],
    effects: V5_NO_EFFECTS,
  });
}
