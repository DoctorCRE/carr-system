// DoctorCRE v5 slice V5-A02, half two: THE WORKFLOW AND RULE LIFECYCLE PUBLIC
// SURFACE — and, like half one, it is a surface that cannot say yes.
//
// WHAT THIS FILE MAY NOT DECIDE, said first because it is the whole point.
//
// (1) ACTIVATION. The catalog's excluded_scope for V5-A02 begins "product
// activation", and no activation controller with a reversible, receipted,
// durable transition record exists in this repository.
//
// (2) EVERY FACT AN ANSWER WOULD STAND ON. There is no authoritative workflow
// -state reader, no rule registry reader, no control-implementation reader, no
// test-result reader and no acceptance-receipt reader here either. A caller
// object carrying `has_telemetry: true`, a reviewer id, a passing test, a
// control digest and a verifier id is a DESCRIPTION of those facts. A public
// function that turned that description into `operational`, `active`, `allow`
// or `coverage_complete: true` would be handing out authority nothing in this
// system holds — the exact defect the V5-A02 review of PR 985 named, and the
// thing the standing rule learned from nine review rounds on 2026-09-11 forbids
// under ANY name, including a "predicate", "fixture" or "unwired" variant.
//
// SO THE PUBLIC SURFACE IS THIS, and it is the whole of it:
//
//   readWorkflowLifecycle        -> status "unavailable", naming the reader owed
//   readRuleLifecycleTransition  -> status "unavailable", naming the readers owed
//   readRuleEnforcementCoverage  -> status "unavailable", naming the readers owed
//   emitRuleActivation           -> activated false, for every caller on every
//                                   input, embedding NO transition verdict
//
// NONE OF THE FOUR READS ITS REQUEST. If no field of the request can change the
// answer, no caller can smuggle authority in through one. `request_read: false`
// says so in every result.
//
// WHERE THE SHAPE LOGIC LIVES, AND WHY IT IS NOT IN src/ AT ALL. The lifecycle
// clauses are worth proving clause by clause, but a clause that can be IMPORTED
// is a clause a consumer can read an answer out of, whatever its file is called.
// So they do not live in this directory: they live in
// mcp-server/test/lifecycle-classifiers.v5.testhelper.mjs, beside the test that
// is their only caller. No module in src/ can reach them — src/ holds no
// test-only entry at all, and lifecycle-assurance.v5.test.mjs proves both halves
// of that with a parser-backed import scan (no `.testonly.` file exists here,
// and no src module imports anything under ../test/).
//
// AND THEY ANSWER IN A VOCABULARY NO CONSUMER CAN ACT ON. The helper never
// yields a lifecycle state: it yields the conditional TOKENS this module
// publishes — `would_be_operational_if_authoritative`,
// `would_be_active_if_authoritative`, one per state — which are not states and
// are not accepted anywhere as one. THE ONE PLACE A TOKEN BECOMES A STATE is
// `stateFromConditionalToken` below: module-private, not exported, and reached
// only through `readWorkflowStateFromAuthority`, which answers unavailable
// because the workflow-state reader seam is bound to null and nothing exported
// by this module can bind it.
//
// NOTHING HERE INVENTS A VOCABULARY IT COULD READ INSTEAD.
//
//   * THE ELEVEN WORKFLOW LIFECYCLE STATES AND THEIR PRECEDENCE are V5-F09's,
//     lifted from the live `ops.completion_projection` view (db/schema.sql, the
//     `possibility(lifecycle_state, applies)` VALUES list) in its own order.
//     F09 is one of this slice's three source_build_dependencies and its census
//     is what "workflow lifecycle" means in this system; a second ordering here
//     would be a second authority. The projection is SQL this module cannot
//     import, so lifecycle-assurance.v5.test.mjs reads db/schema.sql and asserts
//     the two lists are identical, in order — a schema change turns this test
//     red instead of letting the two drift apart in silence.
//   * THE SIX RULE CLASSES, THE SIX ENFORCEMENT MECHANISMS and THE THREE
//     RETIREMENT BEHAVIOURS are V5-F05's and are IMPORTED from
//     rule-applicability.v5.js, never retyped, so the day F05 adds one this
//     module follows instead of drifting.
//   * THE RULE LIFECYCLE STATES are the catalog's own words for this slice:
//     "rule proposed -> reviewed -> tested/shadow -> active -> retired
//     transitions and enforcement coverage".
//
// THE TWO REMAINING EXCLUSIONS ARE ENFORCED IN THE CLAUSES, NOT DOCUMENTED:
// `rule_presence_is_not_enforcement` (a rule that names only its own binding
// text is UNMAPPED, however well written) and `shadow_miss_without_disposition`
// (a shadow window with an undisposed miss cannot advance a rule). Both reasons
// are registered here and cited by the classifiers.
//
// TWO KINDS OF NO, inherited unchanged from global-boundaries.v5.js:
//   * A POLICY ANSWER is RETURNED — `decision` is "refuse" with a stable
//     `reason_id`. On this surface there is no allow at all.
//   * A CONTRACT VIOLATION THROWS V5BoundaryError. Handing a controller in as a
//     second argument is not a policy question.
//
// DECISION BINDING. Q017.D1, Q036.D1, Q067.D1 and Q086.D1, carried in
// V5_A02_DECISION_IDS by gate-zero-assurance.v5.js and re-exported here so both
// halves of the slice bind one list. Only their routing is readable (see that
// file's header); this module claims no knowledge of their text.

import { canonicalJson, digest } from "./artifact-trust.js";
import { V5BoundaryError, V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import { ORGANIZATION_TENANT_ID } from "./identity.js";
import { V5_A02_DECISION_IDS } from "./gate-zero-assurance.v5.js";

export { V5_A02_DECISION_IDS, V5_NO_EFFECTS };

export const V5_A02_LIFECYCLE_SCHEMA_VERSION = "doctorcre-v5-a02-lifecycle-assurance.v1";

/**
 * 2, not 1: version 1 answered from caller-supplied evidence objects. This
 * version 2 answered `unavailable` and named what it was owed. Version 3 binds
 * enforcement coverage to the server-owned record connection while leaving
 * workflow state, lifecycle transition and activation unavailable.
 */
export const V5_A02_LIFECYCLE_POLICY_VERSION = 3;

function deepFreeze(value) {
  if (Array.isArray(value)) { value.forEach(deepFreeze); return Object.freeze(value); }
  if (isPlainObject(value)) { Object.values(value).forEach(deepFreeze); return Object.freeze(value); }
  return value;
}

function isPlainObject(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function fail(code, message, detail) {
  throw new V5BoundaryError(code, message, detail);
}

// ---------------------------------------------------------------------------
// Every refusal this half can answer with — the public surface's
// reader-unavailable reasons, and the clause reasons the module-private
// classifiers cite. Closed, so a reason is checkable.
// ---------------------------------------------------------------------------

export const V5_A02_LIFECYCLE_REASON_IDS = deepFreeze([
  "acceptance_receipt_reader_unavailable",
  "active_rule_fallback_absent",
  "active_rule_control_unmapped",
  "control_implementation_reader_unavailable",
  "mandatory_rule_without_machine_control",
  "rule_activation_seam_unavailable",
  "rule_control_mechanism_not_for_class",
  "rule_coverage_record_invalid",
  "rule_presence_is_not_enforcement",
  "rule_registry_reader_unavailable",
  "rule_retirement_successor_absent",
  "rule_reviewer_not_independent",
  "rule_state_transition_not_permitted",
  "rule_tests_absent",
  "rule_tests_not_passing",
  "shadow_miss_without_disposition",
  "shadow_window_absent",
  "short_workflow_drops_proof_dimension",
  "test_result_reader_unavailable",
  "workflow_operational_without_proof",
  "workflow_required_dimension_missing",
  "workflow_state_claim_not_derived",
  "workflow_state_reader_unavailable",
].sort());

function reason(id) {
  if (!V5_A02_LIFECYCLE_REASON_IDS.includes(id))
    fail("unknown_reason_id", `${id} is not a registered reason`, { reason_id: id });
  return id;
}

// ---------------------------------------------------------------------------
// WORKFLOW LIFECYCLE VOCABULARY. V5-F09's eleven states, in F09's precedence
// order.
//
// The order is load-bearing and is NOT alphabetical: `ops.completion_projection`
// picks the FIRST applicable possibility in this order, so `conflicting` beats
// `blocked` beats `planned`, and `operational` is last because it is only
// reached when nothing earlier applies. Reordering this list changes what the
// clause says a workflow IS.
// ---------------------------------------------------------------------------

export const V5_A02_WORKFLOW_LIFECYCLE_STATES = deepFreeze([
  "conflicting", "canceled", "superseded", "unknown_stale", "blocked",
  "planned", "built_unmerged", "merged_unactivated", "active_unproven",
  "partially_built", "operational",
]);

/**
 * THE CONDITIONAL TOKEN FOR A STATE, and the reason this module has one.
 *
 * `operational` and `active` are states a consumer ACTS ON: a caller who reads
 * either out of a function has been told a workflow is finished or a rule is
 * switched on. No function in this repository can know that — there is no
 * workflow-state reader and no rule registry — so nothing here, on any surface,
 * public or test-only, may answer with one of those words.
 *
 * What the shape logic answers with instead is the token: not `operational` but
 * `would_be_operational_if_authoritative`, which is a statement about a SHAPE
 * and reads as one wherever it lands. The two vocabularies below are that
 * translation, published so the clauses cite them rather than spelling a token
 * themselves, and they are deliberately NOT the states: a consumer who tries to
 * match a token against a lifecycle state gets no match, which is the point.
 */
function conditionalStateToken(state) {
  return `would_be_${state}_if_authoritative`;
}

/** The eleven workflow tokens, parallel to the states and in the same order. */
export const V5_A02_CONDITIONAL_WORKFLOW_STATE_TOKENS =
  deepFreeze(V5_A02_WORKFLOW_LIFECYCLE_STATES.map(conditionalStateToken));

/** The positive evidence dimensions a workflow accumulates, C-sorted. */
export const V5_A02_WORKFLOW_DIMENSIONS = deepFreeze([
  "activation", "artifact", "canonical", "intent", "readback", "telemetry",
]);

/**
 * THE TWO DIMENSIONS NO WORKFLOW MAY DROP, and this is F09's rule rather than
 * this module's: `ops.completion_projection` answers `active_unproven` whenever
 * `has_activation AND (NOT has_readback OR NOT has_telemetry)`, so a workflow
 * without readback and telemetry can never be operational. A "short" workflow
 * that declared them un-required would be laundering an unproven activation
 * into operational by shortening its own requirement list, which is exactly the
 * shape this half of the slice exists to catch.
 */
export const V5_A02_MANDATORY_PROOF_DIMENSIONS = deepFreeze(["readback", "telemetry"]);

/**
 * A COMPLEX workflow is built, merged, activated and proved: it declares all six
 * dimensions. A SHORT one has no separate build artifact — its canonical form is
 * the only form it ever has, which is what a configuration, rule or policy
 * change looks like — so it may declare five, dropping `artifact` and nothing
 * else.
 *
 * WHY `canonical` IS NOT THE DROPPABLE ONE, since that is the intuitive guess
 * and it is wrong: F09's projection answers `built_unmerged` for
 * `has_artifact AND NOT has_canonical`, and that clause sits ABOVE
 * `operational` in the precedence. A workflow with an artifact and no canonical
 * is therefore permanently `built_unmerged` — it cannot reach operational by any
 * evidence at all. `canonical` is structurally undroppable under F09's own
 * derivation, and a "short" lifecycle defined as dropping it would be a
 * lifecycle nothing could ever finish. See the test that proves it.
 */
export const V5_A02_WORKFLOW_KINDS = deepFreeze(["complex", "short"]);
export const V5_A02_SHORT_WORKFLOW_DROPPABLE = deepFreeze(["artifact"]);

/** The workflow's terminal disposition, when it has one. */
export const V5_A02_WORKFLOW_DISPOSITIONS = deepFreeze(["canceled", "none", "superseded"]);

// ---------------------------------------------------------------------------
// RULE LIFECYCLE VOCABULARY.
// proposed -> reviewed -> tested/shadow -> active -> retired.
// ---------------------------------------------------------------------------

export const V5_A02_RULE_STATES = deepFreeze([
  "proposed", "reviewed", "tested", "shadow", "active", "retired",
]);

/** The six rule tokens, parallel to the rule states and in the same order. */
export const V5_A02_CONDITIONAL_RULE_STATE_TOKENS =
  deepFreeze(V5_A02_RULE_STATES.map(conditionalStateToken));

/**
 * The permitted edges, as an explicit table rather than a rule about a rule.
 * `active -> shadow` is the catalog's "reversible activation": switching a rule
 * off returns it to observation, it does not delete it. `retired` is terminal —
 * a retired rule that could walk back to active would make retirement a mood.
 */
export const V5_A02_RULE_TRANSITIONS = deepFreeze({
  proposed: ["retired", "reviewed"],
  reviewed: ["retired", "shadow", "tested"],
  tested: ["active", "retired", "shadow"],
  shadow: ["active", "retired"],
  active: ["retired", "shadow"],
  retired: [],
});

/** What a shadow observation did about each miss it saw. "none" is not one. */
export const V5_A02_SHADOW_MISS_DISPOSITIONS = deepFreeze([
  "amended_rule", "confirmed_gap", "false_positive", "widened_control",
]);

/** What happens when the control is unavailable. Every active rule owes one. */
export const V5_A02_FALLBACK_KINDS = deepFreeze([
  "degraded_read_only", "documented_manual_procedure",
  "escalate_to_verified_partner", "refuse_closed",
]);

/**
 * The mechanisms that are MACHINE control. A mandatory rule enforced only by a
 * model reading prose or a stated preference is the defect V5-F05's class table
 * names: a rule whose only enforcement is a mind reading text cannot claim to
 * deny an action.
 */
export const V5_A02_MACHINE_ENFORCEMENT_MECHANISMS = deepFreeze([
  "behavioral_test", "code_control", "runtime_state_flag", "workflow_definition",
]);

/**
 * V5-F05's class table decides which mechanism belongs to which class. F05 does
 * not export the table itself, so the pairing is restated here as the
 * one-to-one map its RULE_CLASS_TABLE declares, and
 * lifecycle-assurance.v5.test.mjs asserts every class and every mechanism F05
 * exports appears here exactly once — so a class or mechanism added there
 * cannot pass unnoticed here.
 */
export const V5_A02_CLASS_ENFORCEMENT_MECHANISM = deepFreeze({
  code_enforced: "code_control",
  workflow: "workflow_definition",
  test: "behavioral_test",
  scoped_judgment: "model_judgment",
  preference: "partner_preference",
  runtime_state: "runtime_state_flag",
});

// ---------------------------------------------------------------------------
// THE SEAMS, NAMED BY THIS MODULE.
//
// Each is a NAME, not a port: no exported function accepts a controller or a
// reader as an argument and this module exports no way to bind one.
// ---------------------------------------------------------------------------

/** The seam an activation would be performed through. */
export const V5_A02_RULE_ACTIVATION_SEAM = "seam:rule-registry-activation-controller";

/** Where a workflow's own lifecycle evidence could be read from. Nothing today. */
export const V5_A02_WORKFLOW_STATE_READER_SEAM = "seam:workflow-state-reader";

/** Where a rule, its version and its state could be read from. Nothing today. */
export const V5_A02_RULE_REGISTRY_READER_SEAM = "seam:rule-registry-reader";

/** Where a control could be checked against the code it names. Nothing today. */
export const V5_A02_CONTROL_IMPLEMENTATION_READER_SEAM = "seam:control-implementation-reader";

/** Where a named test's real result could be read from. Nothing today. */
export const V5_A02_TEST_RESULT_READER_SEAM = "seam:test-result-reader";

/** Where a human acceptance receipt could be read from. Nothing today. */
export const V5_A02_ACCEPTANCE_RECEIPT_READER_SEAM = "seam:acceptance-receipt-reader";

/** Everything this half is owed before any of its answers could be yes. */
export const V5_A02_LIFECYCLE_OWED_SEAMS = deepFreeze([
  V5_A02_ACCEPTANCE_RECEIPT_READER_SEAM,
  V5_A02_CONTROL_IMPLEMENTATION_READER_SEAM,
  V5_A02_RULE_ACTIVATION_SEAM,
  V5_A02_RULE_REGISTRY_READER_SEAM,
  V5_A02_TEST_RESULT_READER_SEAM,
  V5_A02_WORKFLOW_STATE_READER_SEAM,
].sort());

/**
 * The bindings: all null, because none of these exist. When a ruling records
 * one, this object is the single place it binds and the four functions below
 * are the clauses it will be read through.
 */
const V5_A02_LIFECYCLE_BINDINGS = Object.freeze({
  [V5_A02_RULE_ACTIVATION_SEAM]: null,
  [V5_A02_WORKFLOW_STATE_READER_SEAM]: null,
  [V5_A02_RULE_REGISTRY_READER_SEAM]: null,
  [V5_A02_CONTROL_IMPLEMENTATION_READER_SEAM]: null,
  [V5_A02_TEST_RESULT_READER_SEAM]: null,
  [V5_A02_ACCEPTANCE_RECEIPT_READER_SEAM]: null,
});

/** The bound holder for a seam, or null when it is unavailable. Takes no input. */
function boundSeam(seam, method) {
  const holder = V5_A02_LIFECYCLE_BINDINGS[seam];
  return isPlainObject(holder) && typeof holder[method] === "function" ? holder : null;
}

function seamsOwed(seams) {
  return deepFreeze(seams.map(seam => ({ seam, bound: false })));
}

// ---------------------------------------------------------------------------
// THE ONE PLACE A CONDITIONAL TOKEN COULD EVER BECOME A STATE.
//
// Both functions below are module-private. They are not exported, they are not
// on the namespace object, and no argument of any exported function reaches
// them — so there is no route from a consumer to either one. That is the whole
// mechanism: the translation exists, it is written down, and it is behind a
// seam this module binds to null and exposes no setter for.
// ---------------------------------------------------------------------------

/** token -> state, for both vocabularies. Private, and the only such table. */
const CONDITIONAL_TOKEN_TO_STATE = Object.freeze(Object.fromEntries([
  ...V5_A02_WORKFLOW_LIFECYCLE_STATES.map((state, index) =>
    [V5_A02_CONDITIONAL_WORKFLOW_STATE_TOKENS[index], state]),
  ...V5_A02_RULE_STATES.map((state, index) =>
    [V5_A02_CONDITIONAL_RULE_STATE_TOKENS[index], state]),
]));

function stateFromConditionalToken(token) {
  return Object.hasOwn(CONDITIONAL_TOKEN_TO_STATE, token)
    ? CONDITIONAL_TOKEN_TO_STATE[token] : null;
}

/**
 * WHAT AN AUTHORITATIVE READING WOULD BE, AND WHY IT IS UNAVAILABLE.
 *
 * Ordered questions, so a second reader reaches the same verdict:
 *   1. Is a workflow-state reader bound at this seam? It is not: the bindings
 *      object is a module-private frozen object of nulls, and no exported
 *      function of this module writes to it. Answer: unavailable, and the two
 *      questions below are not asked.
 *   2. (When a ruling binds one.) What token did the reader's own derivation
 *      yield? Whatever it is, it is a token, not a state.
 *   3. Does this policy know that token? Only then does the token become a
 *      state, HERE, and nowhere else in this repository.
 *
 * Today every call returns `{ available: false, state: null }`, which is what
 * makes `readWorkflowLifecycle` answer `unavailable` for every caller.
 */
function readWorkflowStateFromAuthority() {
  const reader = boundSeam(V5_A02_WORKFLOW_STATE_READER_SEAM, "readWorkflow");
  if (reader === null)
    return { available: false, state: null, because: "no workflow-state reader is bound" };
  const state = stateFromConditionalToken(reader.readWorkflow());
  return state === null
    ? { available: false, state: null, because: "the bound reader answered a token this policy does not publish" }
    : { available: true, state, because: null };
}

/** The one shape every unavailable answer on this surface has. */
function unavailable(answer, reasonId, because, seams, extra) {
  return deepFreeze({
    schema_version: V5_A02_LIFECYCLE_SCHEMA_VERSION,
    policy_version: V5_A02_LIFECYCLE_POLICY_VERSION,
    answer,
    tenant: ORGANIZATION_TENANT_ID,
    status: "unavailable",
    decision: "refuse",
    reason_id: reason(reasonId),
    unavailable_because: because,
    owed_seams: [...seams].sort(),
    seams_bound: seamsOwed([...seams].sort()),
    // No field of any request can change any field of this answer.
    request_read: false,
    caller_evidence_admitted: false,
    decided_by: "no_authoritative_reader",
    model_judgment_admitted: false,
    effects: V5_NO_EFFECTS,
    ...extra,
  });
}

// ---------------------------------------------------------------------------
// The complex/short workflow lifecycle check.
// ---------------------------------------------------------------------------

/**
 * What a workflow lifecycle reading would need and cannot get.
 *
 * The nine evidence booleans F09's projection derives a state from would have
 * to be read from the workflow itself — its merge, its activation, its readback
 * and its telemetry. Nothing here can read any of them, and a caller who
 * asserts them has described a workflow rather than shown one.
 */
export function readWorkflowLifecycle() {
  // The private authority path is asked, and it is the only thing asked: no
  // field of any request reaches it, and it answers unavailable.
  const authority = readWorkflowStateFromAuthority();
  return unavailable(
    "workflow_lifecycle",
    "workflow_state_reader_unavailable",
    "no authoritative reader of workflow activation, readback or telemetry exists, so a lifecycle state can only be asserted by its caller",
    [V5_A02_WORKFLOW_STATE_READER_SEAM, V5_A02_TEST_RESULT_READER_SEAM],
    // The vocabularies are EXPORTED CONSTANTS, not fields of this answer: an
    // unavailable answer recites nothing a caller could mistake for a reading.
    // `workflow_state_reader_bound` is the private path's own answer, so the
    // field cannot drift from the seam it reports on.
    { workflow_state_reader_bound: authority.available });
}

// ---------------------------------------------------------------------------
// The rule lifecycle ladder.
// ---------------------------------------------------------------------------

/**
 * What a rule lifecycle transition reading would need and cannot get.
 *
 * The ladder's own clauses are deterministic, but every fact they stand on — a
 * rule's current state, who reviewed it, whether its named test really passed,
 * whether its control exists — belongs to a registry, a test runner and a
 * receipt store that this repository does not have.
 */
export function readRuleLifecycleTransition() {
  return unavailable(
    "rule_lifecycle_transition",
    "rule_registry_reader_unavailable",
    "no rule registry, test-result or acceptance-receipt reader exists, so a transition's evidence can only be asserted by its caller",
    [V5_A02_RULE_REGISTRY_READER_SEAM, V5_A02_TEST_RESULT_READER_SEAM,
      V5_A02_ACCEPTANCE_RECEIPT_READER_SEAM],
    {
      performs_transition: false,
      rule_registry_reader_bound: boundSeam(V5_A02_RULE_REGISTRY_READER_SEAM, "readRule") !== null,
    });
}

// ---------------------------------------------------------------------------
// Enforcement coverage — checkable_done 3.
// ---------------------------------------------------------------------------

/**
 * What an enforcement coverage reading would need and cannot get.
 *
 * "Every active rule maps to enforceable control and fallback" is a statement
 * about the live rule set and the live code. Answering it needs the rule
 * registry to enumerate what is active and a control-implementation reader to
 * confirm that each named control is the code it claims to be — a supplied
 * `implementation_digest` is a claim about a file, not a reading of one.
 */
function unavailableRuleEnforcementCoverage(
                                             reasonId = reason("control_implementation_reader_unavailable"),
                                             because = "the authoritative rule coverage record cannot be read") {
  return unavailable(
    "rule_enforcement_coverage",
    reasonId,
    because,
    [V5_A02_RULE_REGISTRY_READER_SEAM, V5_A02_CONTROL_IMPLEMENTATION_READER_SEAM,
      V5_A02_TEST_RESULT_READER_SEAM],
    {
      control_implementation_reader_bound: false,
      authoritative_record_reader_bound: false,
    });
}

const V5_A02_COVERAGE_SCHEMA_VERSION =
  "doctorcre-v5-a02-rule-enforcement-coverage.v2";
const V5_A02_COVERAGE_GAP_REASONS = Object.freeze(new Set([
  "active_rule_amended_needs_reapproval",
  "active_rule_approved_control_not_installed",
  "active_rule_control_unmapped",
  "active_rule_fallback_absent",
  "rule_test_evidence_changed_since_approval",
  "rule_test_evidence_future_dated",
  "rule_tests_not_passing",
]));
const V5_A02_COVERAGE_STATES = Object.freeze(new Set(["complete", "empty", "gaps"]));
const V5_A02_COVERAGE_RECORD_KEYS = Object.freeze([
  "active_rule_count", "coverage_complete", "coverage_state", "covered_rule_count",
  "evidence_digest", "gap_count", "gaps", "observed_at", "schema_version",
].sort().join(","));
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const SHA256_RE = /^sha256:[0-9a-f]{64}$/;

function validCoverageGap(gap) {
  return isPlainObject(gap)
    && Object.keys(gap).sort().join(",") === "detail,reason_id,rule_id"
    && typeof gap.rule_id === "string" && UUID_RE.test(gap.rule_id)
    && V5_A02_COVERAGE_GAP_REASONS.has(gap.reason_id)
    && typeof gap.detail === "string" && gap.detail.trim().length > 0;
}

/** The state the counts imply. Zero active rules is `empty`, never
 * `complete`: an empty registry proves nothing is enforced. */
function coverageStateFor(record) {
  if (record.active_rule_count === 0) return "empty";
  return record.gap_count > 0 ? "gaps" : "complete";
}

// Each invariant below is its own check, and each is killed by its own
// planted-inconsistency test in a02-rule-enforcement-reader-mutants.test.mjs.
function validCoverageRecord(record) {
  if (!isPlainObject(record)) return false;
  if (Object.keys(record).sort().join(",") !== V5_A02_COVERAGE_RECORD_KEYS) return false;
  if (record.schema_version !== V5_A02_COVERAGE_SCHEMA_VERSION) return false;
  if (typeof record.observed_at !== "string"
      || !Number.isFinite(Date.parse(record.observed_at))
      || !Number.isInteger(record.active_rule_count) || record.active_rule_count < 0
      || !Number.isInteger(record.covered_rule_count) || record.covered_rule_count < 0
      || !Number.isInteger(record.gap_count) || record.gap_count < 0
      || typeof record.coverage_complete !== "boolean"
      || !V5_A02_COVERAGE_STATES.has(record.coverage_state)
      || !Array.isArray(record.gaps)
      || !SHA256_RE.test(record.evidence_digest)) return false;
  if (record.gap_count !== record.gaps.length) return false;
  if (record.covered_rule_count + record.gap_count !== record.active_rule_count) return false;
  if (record.coverage_state !== coverageStateFor(record)) return false;
  if (record.coverage_complete !== (record.coverage_state === "complete")) return false;
  const seen = new Set();
  for (const gap of record.gaps) {
    if (!validCoverageGap(gap)) return false;
    if (seen.has(gap.rule_id)) return false;
    seen.add(gap.rule_id);
  }
  return true;
}

async function readRuleEnforcementCoverageFromRecords(database) {
  let result;
  try {
    result = await database.query(
      "select ops.v5_a02_rule_enforcement_coverage() as coverage", []);
  } catch {
    return unavailableRuleEnforcementCoverage();
  }
  const record = result?.rows?.length === 1 ? result.rows[0]?.coverage : null;
  if (!validCoverageRecord(record))
    return unavailableRuleEnforcementCoverage(
      reason("rule_coverage_record_invalid"),
      "the authoritative rule coverage record failed its closed shape and count invariants");
  return deepFreeze({
    schema_version: V5_A02_LIFECYCLE_SCHEMA_VERSION,
    policy_version: V5_A02_LIFECYCLE_POLICY_VERSION,
    answer: "rule_enforcement_coverage",
    tenant: ORGANIZATION_TENANT_ID,
    status: "available",
    decision: "report",
    observed_at: record.observed_at,
    active_rule_count: record.active_rule_count,
    covered_rule_count: record.covered_rule_count,
    gap_count: record.gap_count,
    coverage_state: record.coverage_state,
    coverage_complete: record.coverage_complete,
    gaps: record.gaps,
    evidence_digest: record.evidence_digest,
    evidence_source: "ops.v5_a02_rule_enforcement_coverage()",
    request_read: false,
    caller_evidence_admitted: false,
    decided_by: "authoritative_record_reader",
    model_judgment_admitted: false,
    effects: V5_NO_EFFECTS,
  });
}

export function readRuleEnforcementCoverage(database) {
  if (database === null || typeof database !== "object"
      || Array.isArray(database) || typeof database.query !== "function")
    return unavailableRuleEnforcementCoverage(
      "control_implementation_reader_unavailable",
      "no server-owned record connection was supplied to the lifecycle reader");
  return readRuleEnforcementCoverageFromRecords(database);
}

// ---------------------------------------------------------------------------
// The privileged path, and the refusal that stands where it would be.
// ---------------------------------------------------------------------------

/**
 * THE ONLY FUNCTION THAT COULD ACTIVATE A RULE, AND IT CANNOT.
 *
 * One argument, and the arity IS the boundary. The answer is fixed for every
 * caller on every input: `activated: false`, naming every seam it is owed.
 *
 * AND IT CARRIES NO TRANSITION VERDICT. The earlier version of this file ran the
 * transition predicate and returned it beside the refusal, so a clean caller
 * fixture produced `transition.decision: "allow"` inside a refusal — a verdict
 * reachable from nothing but caller input. The honest answer is that the
 * transition cannot be evaluated at all until a rule registry exists to say what
 * state the rule is actually in.
 */
export function emitRuleActivation(request) {
  // eslint-disable-next-line no-unused-vars, prefer-rest-params -- the arity IS
  // the boundary, and the request is deliberately not read.
  if (arguments.length > 1)
    fail("rule_activation_controller_is_not_an_argument",
      "emitRuleActivation takes one request; the activation controller is bound by this module",
      { arguments_received: arguments.length, required_seam: V5_A02_RULE_ACTIVATION_SEAM });

  return unavailable(
    "rule_activation",
    "rule_activation_seam_unavailable",
    "no activation controller exists, and no rule registry exists to say what state a rule is in",
    [...V5_A02_LIFECYCLE_OWED_SEAMS],
    {
      decision_ids: [...V5_A02_DECISION_IDS],
      // FIXED. Not derived from a transition, not derived from the request, not
      // derivable by any caller.
      activated: false,
      not_activated_because: "activation controller undecided, and no authoritative rule, control, test or receipt reader exists",
      activation_seam: V5_A02_RULE_ACTIVATION_SEAM,
      controller_bound: boundSeam(V5_A02_RULE_ACTIVATION_SEAM, "activate") !== null,
      controller_is_caller_supplied: false,
      performs_transition: false,
      // The transition fields a consumer would need. Null because there is no
      // transition to report, not because this one happened to fail.
      transition: null,
      transition_unavailable_because: "no rule registry reader exists to read the rule's current state or its evidence",
      undecided_governance_questions: deepFreeze([
        "which controller performs a rule activation",
        "what a reversible activation records so it can be undone",
        "which store an active rule and its version are read from",
        "how a control is checked against the code it names",
      ]),
    });
}

// ---------------------------------------------------------------------------
// The closed, versioned policy preimage and its digest.
// ---------------------------------------------------------------------------

export function v5A02LifecyclePolicyPreimage() {
  return {
    schema_version: V5_A02_LIFECYCLE_SCHEMA_VERSION,
    policy_version: V5_A02_LIFECYCLE_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    decision_ids: [...V5_A02_DECISION_IDS],
    workflow_lifecycle_states_in_precedence_order: [...V5_A02_WORKFLOW_LIFECYCLE_STATES],
    // The tokens the shape logic answers in. Sealed here so a later edit cannot
    // quietly reintroduce a bare state as an answer vocabulary.
    conditional_workflow_state_tokens: [...V5_A02_CONDITIONAL_WORKFLOW_STATE_TOKENS],
    conditional_rule_state_tokens: [...V5_A02_CONDITIONAL_RULE_STATE_TOKENS],
    conditional_token_to_state_is_module_private: true,
    workflow_dimensions: [...V5_A02_WORKFLOW_DIMENSIONS].sort(),
    workflow_kinds: [...V5_A02_WORKFLOW_KINDS].sort(),
    workflow_proof_dimensions: [...V5_A02_MANDATORY_PROOF_DIMENSIONS].sort(),
    short_workflow_droppable: [...V5_A02_SHORT_WORKFLOW_DROPPABLE].sort(),
    workflow_dispositions: [...V5_A02_WORKFLOW_DISPOSITIONS].sort(),
    rule_states: [...V5_A02_RULE_STATES],
    rule_transitions: Object.fromEntries(
      Object.keys(V5_A02_RULE_TRANSITIONS).sort()
        .map(from => [from, [...V5_A02_RULE_TRANSITIONS[from]].sort()])),
    shadow_miss_dispositions: [...V5_A02_SHADOW_MISS_DISPOSITIONS].sort(),
    fallback_kinds: [...V5_A02_FALLBACK_KINDS].sort(),
    machine_enforcement_mechanisms: [...V5_A02_MACHINE_ENFORCEMENT_MECHANISMS].sort(),
    class_enforcement_mechanism: Object.fromEntries(
      Object.keys(V5_A02_CLASS_ENFORCEMENT_MECHANISM).sort()
        .map(key => [key, V5_A02_CLASS_ENFORCEMENT_MECHANISM[key]])),
    reason_ids: [...V5_A02_LIFECYCLE_REASON_IDS].sort(),
    owed_seams: [...V5_A02_LIFECYCLE_OWED_SEAMS],
    rule_activation_seam: V5_A02_RULE_ACTIVATION_SEAM,
    rule_activation_controller_bound: false,
    authoritative_readers_bound: {
      rule_enforcement_coverage: true,
      rule_lifecycle_transition: false,
      workflow_lifecycle: false,
    },
    public_surface_answers:
      "rule_enforcement_coverage_record_or_unavailable;other_reads_unavailable",
  };
}

export function v5A02LifecyclePolicyDigest() {
  return digest(v5A02LifecyclePolicyPreimage());
}

export function v5A02LifecyclePolicyCanonicalBytes() {
  return canonicalJson(v5A02LifecyclePolicyPreimage());
}
