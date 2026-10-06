// V5-A02 WORKFLOW AND RULE LIFECYCLE CLAUSES — A TEST HELPER, NOT A MODULE.
//
// READ THIS FIRST, BECAUSE ITS LOCATION IS THE CONTRACT. This file is not in
// mcp-server/src/. It is in the test directory, beside the one test that
// imports it, and it is named `.testhelper.mjs` so `npm test`'s own glob
// (`test/*.test.js test/*.test.mjs`) does not even collect it as a suite. An
// earlier version of this file DID sit in src/ under a `.testonly.js` name, and
// a reviewer was right that the name was doing work the filesystem was not: a
// module in src/ is importable by anything in src/, whatever it calls itself.
// The route is now closed by construction — there is no test-only entry in src/
// at all — and lifecycle-assurance.v5.test.mjs proves it two ways with a real
// ESM parser: no `.testonly.` file exists in src/, and no module in src/ names
// any specifier under `../test/`.
//
// WHY IT EXISTS AT ALL. The lifecycle clauses V5-A02's concrete_output and
// checkable_done name are real and worth proving clause by clause:
//
//   1. the complex/short workflow lifecycle, derived in V5-F09's own precedence
//      order, where a shortened requirement list cannot launder an unproven
//      activation into a finished workflow;
//   2. the rule ladder proposed -> reviewed -> tested/shadow -> active ->
//      retired, with the evidence each target state owes;
//   3. enforcement coverage — every active rule mapping to a control that
//      actually enforces it, plus a fallback, or being LISTED as unmapped.
//
// WHAT THEY ARE NOT, AND WHAT THEY MAY NOT SAY. There is NO authoritative
// workflow-state reader, NO rule registry reader, NO control-implementation
// reader, NO test-result reader and NO acceptance-receipt reader in this
// repository. A caller object carrying evidence booleans, a reviewer id, a test
// result, a control digest or a verifier id is a DESCRIPTION of those facts, not
// the facts. So these clauses do not answer `operational`, `active`, `allow`,
// `coverage_complete`, `green` or `joins_exactly` about anything — not as a
// field, and NOT AS A VALUE UNDER A CONDITIONAL FIELD NAME, which was the second
// half of the same reviewer's finding: renaming the field left the privileged
// word itself sitting in the result, one property read away from a consumer.
//
// SO THE VOCABULARY ITSELF IS CONDITIONAL, IN BOTH DIRECTIONS. These clauses
// speak only in the tokens lifecycle-assurance.v5.js publishes —
// `would_be_operational_if_authoritative`, `would_be_active_if_authoritative`,
// one per state — as their INPUT claim vocabulary and as their answers:
//
//   would_derive_state_token_if_authoritative — the token the evidence SHAPE
//                                               derives to
//   would_permit_if_authoritative             — whether the edge and its
//                                               evidence shape would satisfy
//   would_be_covered_if_authoritative         — whether the rule set SHAPE is
//                                               covered
//
// THE ONE DIRECTION THAT IS MISSING HERE ON PURPOSE. This file maps states to
// tokens (`TOKEN.operational`), never tokens to states. Nothing in it can turn
// a token back into a state, because that translation belongs to exactly one
// place: the module-private `stateFromConditionalToken` in
// lifecycle-assurance.v5.js, behind a workflow-state reader seam bound to null.
// A token that escaped this file into a consumer is therefore not a state, does
// not match any state, and has no function anywhere that would convert it.
//
// Every result also carries `is_not_authority: true` and
// `evidence_source: "caller_supplied_shapes_not_authority"`.
//
// NOTHING HERE INVENTS A VOCABULARY IT COULD READ INSTEAD. The eleven workflow
// states and their precedence are V5-F09's, out of `ops.completion_projection`;
// the six rule classes, six enforcement mechanisms and three retirement
// behaviours are V5-F05's, imported rather than retyped; the class/mechanism
// pairing, the two token vocabularies and every other list come from
// lifecycle-assurance.v5.js, which holds them as the slice's public vocabulary.
//
// Every function is PURE — no filesystem, no network, no database, no clock, no
// environment. Contract violations throw V5BoundaryError; classification
// answers are returned.

import { V5BoundaryError, V5_NO_EFFECTS } from "../src/global-boundaries.v5.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  V5_F05_ENFORCEMENT_MECHANISMS,
  V5_F05_RETIREMENT_BEHAVIORS,
  V5_F05_RULE_CLASSES,
} from "../src/rule-applicability.v5.js";
import {
  V5_A02_CLASS_ENFORCEMENT_MECHANISM,
  V5_A02_CONDITIONAL_RULE_STATE_TOKENS,
  V5_A02_CONDITIONAL_WORKFLOW_STATE_TOKENS,
  V5_A02_FALLBACK_KINDS,
  V5_A02_LIFECYCLE_POLICY_VERSION,
  V5_A02_LIFECYCLE_REASON_IDS,
  V5_A02_LIFECYCLE_SCHEMA_VERSION,
  V5_A02_MACHINE_ENFORCEMENT_MECHANISMS,
  V5_A02_MANDATORY_PROOF_DIMENSIONS,
  V5_A02_RULE_STATES,
  V5_A02_RULE_TRANSITIONS,
  V5_A02_SHADOW_MISS_DISPOSITIONS,
  V5_A02_SHORT_WORKFLOW_DROPPABLE,
  V5_A02_WORKFLOW_DIMENSIONS,
  V5_A02_WORKFLOW_DISPOSITIONS,
  V5_A02_WORKFLOW_KINDS,
  V5_A02_WORKFLOW_LIFECYCLE_STATES,
} from "../src/lifecycle-assurance.v5.js";

/** Said on every result, so an escaped value still reads as "not authority". */
export const V5_A02_CLASSIFIER_EVIDENCE_SOURCE = "caller_supplied_shapes_not_authority";

/**
 * STATE -> TOKEN, and only that direction, for both ladders. Built by index from
 * the two parallel vocabularies the public module publishes, so a state that
 * gained a token here without gaining one there would throw on the first read
 * rather than quietly answer `undefined`.
 */
function tokenTable(states, tokens) {
  if (states.length !== tokens.length)
    throw new V5BoundaryError("token_vocabulary_mismatch",
      "every state must have exactly one conditional token",
      { states: states.length, tokens: tokens.length });
  return Object.freeze(Object.fromEntries(states.map((state, index) => [state, tokens[index]])));
}

/** `TOKEN.operational` is a token. There is no table here that reverses it. */
const TOKEN = tokenTable(V5_A02_WORKFLOW_LIFECYCLE_STATES, V5_A02_CONDITIONAL_WORKFLOW_STATE_TOKENS);
const RULE_TOKEN = tokenTable(V5_A02_RULE_STATES, V5_A02_CONDITIONAL_RULE_STATE_TOKENS);

/** The ladder, re-keyed into token space once, so no clause below holds a state. */
const RULE_TOKEN_TRANSITIONS = Object.freeze(Object.fromEntries(
  V5_A02_RULE_STATES.map(state =>
    [RULE_TOKEN[state], Object.freeze(V5_A02_RULE_TRANSITIONS[state].map(to => RULE_TOKEN[to]))])));

const REF = /^[a-z][a-z0-9-]*:[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$/;
const DIGEST = /^sha256:[0-9a-f]{64}$/;
const ISO_INSTANT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z$/;
const IDENTIFIER = /^[a-z0-9][a-z0-9._-]{0,127}$/;

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

function object(value, path) {
  if (!isPlainObject(value)) fail("not_an_object", `${path} must be a plain object`, { path });
  return value;
}

function closed(value, allowed, path) {
  object(value, path);
  for (const key of Object.keys(value))
    if (!allowed.includes(key))
      fail("unknown_field", `${path}.${key} is not a field this classifier reads`,
        { path, key, allowed: [...allowed].sort() });
  return value;
}

function exact(value, allowed, path) {
  closed(value, allowed, path);
  for (const key of allowed)
    if (!Object.hasOwn(value, key))
      fail("missing_field", `${path}.${key} is required`, { path, key });
  return value;
}

function str(value, path) {
  if (typeof value !== "string" || value.length === 0)
    fail("not_a_string", `${path} must be a non-empty string`, { path });
  return value;
}

function pattern(value, re, code, path) {
  str(value, path);
  if (!re.test(value)) fail(code, `${path} is malformed`, { path, value });
  return value;
}

function instant(value, path) {
  str(value, path);
  if (!ISO_INSTANT.test(value) || Number.isNaN(Date.parse(value)))
    fail("malformed_instant", `${path} must be a UTC ISO-8601 instant`, { path, value });
  return Date.parse(value);
}

function member(value, allowed, path) {
  str(value, path);
  if (!allowed.includes(value))
    fail("unknown_enum_member", `${path} is not a member this classifier knows`,
      { path, value, allowed: [...allowed].sort() });
  return value;
}

function bool(value, path) {
  if (typeof value !== "boolean") fail("not_a_boolean", `${path} must be a boolean`, { path });
  return value;
}

function list(value, path) {
  if (!Array.isArray(value)) fail("not_an_array", `${path} must be an array`, { path });
  return value;
}

function sortedUnique(values, path, validate) {
  list(values, path);
  values.forEach((value, index) => validate(value, `${path}[${index}]`));
  for (let index = 1; index < values.length; index += 1) {
    if (values[index] === values[index - 1])
      fail("duplicate_member", `${path} repeats ${values[index]}`, { path, value: values[index] });
    if (values[index] < values[index - 1])
      fail("unsorted_list", `${path} must be C-sorted`, { path, at: index });
  }
  return values;
}

/** The public module owns the closed reason registry; this file only cites it. */
function reason(id) {
  if (!V5_A02_LIFECYCLE_REASON_IDS.includes(id))
    fail("unknown_reason_id", `${id} is not a registered reason`, { reason_id: id });
  return id;
}

// ---------------------------------------------------------------------------
// WORKFLOW LIFECYCLE. V5-F09's eleven states, in V5-F09's precedence order.
//
// The order is load-bearing and is NOT alphabetical: `ops.completion_projection`
// picks the FIRST applicable possibility in that order, so `conflicting` beats
// `blocked` beats `planned`, and `operational` is last because it is only
// reached when nothing earlier applies.
// ---------------------------------------------------------------------------

const WORKFLOW_EVIDENCE_FIELDS = Object.freeze([
  "has_activation", "has_artifact", "has_blocker", "has_canonical", "has_conflict",
  "has_intent", "has_readback", "has_stale", "has_telemetry",
]);
const WORKFLOW_REQUEST_FIELDS = Object.freeze([
  "workflow_ref", "workflow_kind", "required_dimensions", "disposition",
  "evidence", "claimed_state_token",
]);

/**
 * The eleven-way derivation, in F09's precedence order, over the nine observed
 * booleans and the disposition.
 *
 * WHAT IT ANSWERS WITH, and this is the whole correction: a conditional TOKEN,
 * never a state. `would_be_operational_if_authoritative` is not `operational`,
 * does not equal it, and no function in this repository outside the private path
 * in lifecycle-assurance.v5.js can turn one into the other. A value that escaped
 * this file into a consumer is therefore inert twice over — under a conditional
 * field name AND as a word no consumer matches on.
 *
 * `everyRequiredPresent` is supplied rather than recomputed here so the caller's
 * DECLARED requirement set is what decides `partially_built` versus the finished
 * state — which is precisely why the requirement set itself is checked first, by
 * classifyWorkflowLifecycle.
 */
export function classifyWorkflowLifecycleState(evidence, disposition, everyRequiredPresent) {
  exact(object(evidence, "evidence"), WORKFLOW_EVIDENCE_FIELDS, "evidence");
  for (const key of WORKFLOW_EVIDENCE_FIELDS) bool(evidence[key], `evidence.${key}`);
  member(disposition, V5_A02_WORKFLOW_DISPOSITIONS, "disposition");
  bool(everyRequiredPresent, "everyRequiredPresent");

  return deepFreeze({
    would_derive_state_token_if_authoritative:
      deriveStateToken(evidence, disposition, everyRequiredPresent),
    is_not_authority: true,
    evidence_source: V5_A02_CLASSIFIER_EVIDENCE_SOURCE,
  });
}

function deriveStateToken(evidence, disposition, everyRequiredPresent) {
  if (evidence.has_conflict) return TOKEN.conflicting;
  if (disposition === "canceled") return TOKEN.canceled;
  if (disposition === "superseded") return TOKEN.superseded;
  if (evidence.has_stale) return TOKEN.unknown_stale;
  if (evidence.has_blocker) return TOKEN.blocked;
  if (evidence.has_intent && !evidence.has_artifact && !evidence.has_canonical) return TOKEN.planned;
  if (evidence.has_artifact && !evidence.has_canonical) return TOKEN.built_unmerged;
  if (evidence.has_canonical && !evidence.has_activation) return TOKEN.merged_unactivated;
  if (evidence.has_activation && (!evidence.has_readback || !evidence.has_telemetry))
    return TOKEN.active_unproven;
  if (!everyRequiredPresent) return TOKEN.partially_built;
  return TOKEN.operational;
}

/**
 * The complex/short lifecycle clause.
 *
 * Ordered questions, so a second reader reaches the same verdict:
 *   1. Does the declared requirement set keep both proof dimensions?  If not,
 *      `short_workflow_drops_proof_dimension` — answered before the state is
 *      derived, because a shortened requirement set would otherwise make the
 *      finished state come out true.
 *   2. Is the declared set legal for the declared kind? A complex workflow
 *      declaring fewer than all six, or a short one dropping anything but
 *      `artifact`, is `workflow_required_dimension_missing`.
 *   3. What state does the evidence SHAPE derive to?
 *   4. Does that state reach the finished one without readback and telemetry?
 *      (Unreachable by construction, and asserted anyway — a derivation that
 *      could reach it would be the defect.)
 *   5. Does the caller's claimed token match the derived one? The claim is
 *      CITED, never honoured — and it is made in the same conditional token
 *      vocabulary the answer uses, so a caller cannot even SPELL a lifecycle
 *      state at this boundary, let alone be believed about one.
 */
export function classifyWorkflowLifecycle(request) {
  exact(object(request, "request"), WORKFLOW_REQUEST_FIELDS, "request");
  pattern(request.workflow_ref, REF, "malformed_ref", "request.workflow_ref");
  const kind = member(request.workflow_kind, V5_A02_WORKFLOW_KINDS, "request.workflow_kind");
  sortedUnique(request.required_dimensions, "request.required_dimensions",
    (value, path) => member(value, V5_A02_WORKFLOW_DIMENSIONS, path));
  const disposition = member(request.disposition, V5_A02_WORKFLOW_DISPOSITIONS, "request.disposition");
  const claimed = member(request.claimed_state_token, V5_A02_CONDITIONAL_WORKFLOW_STATE_TOKENS,
    "request.claimed_state_token");
  // The token for the last state in F09's precedence order. Not the state.
  const finished = TOKEN.operational;

  const declared = new Set(request.required_dimensions);
  const droppedProof = V5_A02_MANDATORY_PROOF_DIMENSIONS.filter(dim => !declared.has(dim));
  const expected = kind === "complex"
    ? [...V5_A02_WORKFLOW_DIMENSIONS]
    : V5_A02_WORKFLOW_DIMENSIONS.filter(dim => !V5_A02_SHORT_WORKFLOW_DROPPABLE.includes(dim));
  const missingRequired = expected.filter(dim => !declared.has(dim));
  const overDeclared = [...declared].filter(dim => !expected.includes(dim)).sort();

  const dimensionPresent = dim => ({
    activation: request.evidence?.has_activation,
    artifact: request.evidence?.has_artifact,
    canonical: request.evidence?.has_canonical,
    intent: request.evidence?.has_intent,
    readback: request.evidence?.has_readback,
    telemetry: request.evidence?.has_telemetry,
  })[dim] === true;

  const everyRequiredPresent = request.required_dimensions.every(dimensionPresent);
  const derived = classifyWorkflowLifecycleState(
    request.evidence, disposition, everyRequiredPresent).would_derive_state_token_if_authoritative;

  const unmet = request.required_dimensions.filter(dim => !dimensionPresent(dim));
  const finishedWithoutProof = derived === finished &&
    V5_A02_MANDATORY_PROOF_DIMENSIONS.some(dim => !dimensionPresent(dim));

  let reasonId = null;
  if (droppedProof.length > 0) reasonId = "short_workflow_drops_proof_dimension";
  else if (missingRequired.length > 0 || overDeclared.length > 0) reasonId = "workflow_required_dimension_missing";
  else if (finishedWithoutProof) reasonId = "workflow_operational_without_proof";
  else if (claimed !== derived) reasonId = "workflow_state_claim_not_derived";

  return deepFreeze({
    schema_version: V5_A02_LIFECYCLE_SCHEMA_VERSION,
    policy_version: V5_A02_LIFECYCLE_POLICY_VERSION,
    classification: "workflow_lifecycle_shape",
    tenant: ORGANIZATION_TENANT_ID,
    workflow_ref: request.workflow_ref,
    workflow_kind: kind,
    would_permit_if_authoritative: reasonId === null,
    reason_id: reasonId === null ? null : reason(reasonId),
    // The DERIVED token is the answer. `claimed_state_token_cited` is echoed so
    // a mismatch is legible, never so it can win.
    would_derive_state_token_if_authoritative: derived,
    claimed_state_token_cited: claimed,
    claim_matches_derivation: claimed === derived,
    required_dimensions: [...request.required_dimensions],
    expected_required_dimensions: expected,
    required_dimensions_missing_from_declaration: missingRequired,
    required_dimensions_over_declared: overDeclared,
    required_dimensions_unmet_by_evidence: unmet.sort(),
    proof_dimensions: [...V5_A02_MANDATORY_PROOF_DIMENSIONS],
    proof_dimensions_dropped: droppedProof,
    state_token_precedence: [...V5_A02_CONDITIONAL_WORKFLOW_STATE_TOKENS],
    caller_stated_state_honoured: false,
    is_not_authority: true,
    evidence_source: V5_A02_CLASSIFIER_EVIDENCE_SOURCE,
    decided_by: "deterministic_classifier",
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// RULE LIFECYCLE. proposed -> reviewed -> tested/shadow -> active -> retired.
// ---------------------------------------------------------------------------

const CONTROL_FIELDS = Object.freeze([
  "control_id", "control_ref", "enforcement_mechanism",
  "implementation_digest", "verifier_id", "verified_at",
]);
const FALLBACK_FIELDS = Object.freeze(["kind", "ref"]);
const REVIEW_FIELDS = Object.freeze(["proposer_actor_id", "reviewer_actor_id"]);
const TEST_FIELDS = Object.freeze(["test_ref", "result"]);
const SHADOW_FIELDS = Object.freeze(["window_ref", "opened_at", "closed_at", "misses"]);
const MISS_FIELDS = Object.freeze(["miss_ref", "disposition"]);
const RETIREMENT_FIELDS = Object.freeze(["behavior", "successor_rule_id"]);
const TRANSITION_FIELDS = Object.freeze([
  "rule_id", "rule_class", "mandatory", "from_state_token", "to_state_token",
  "review", "tests", "shadow_window", "control", "fallback", "retirement",
]);

function wouldNotPermit(reasonId, note, detail) {
  return { would_permit_if_authoritative: false, reason_id: reason(reasonId), note, detail: detail ?? {} };
}

function validateControl(control, path) {
  if (control === null) return null;
  exact(object(control, path), CONTROL_FIELDS, path);
  pattern(control.control_id, IDENTIFIER, "malformed_identifier", `${path}.control_id`);
  pattern(control.control_ref, REF, "malformed_ref", `${path}.control_ref`);
  member(control.enforcement_mechanism, V5_F05_ENFORCEMENT_MECHANISMS, `${path}.enforcement_mechanism`);
  pattern(control.implementation_digest, DIGEST, "malformed_digest", `${path}.implementation_digest`);
  str(control.verifier_id, `${path}.verifier_id`);
  instant(control.verified_at, `${path}.verified_at`);
  return control;
}

function validateFallback(fallback, path) {
  if (fallback === null) return null;
  exact(object(fallback, path), FALLBACK_FIELDS, path);
  member(fallback.kind, V5_A02_FALLBACK_KINDS, `${path}.kind`);
  pattern(fallback.ref, REF, "malformed_ref", `${path}.ref`);
  return fallback;
}

/**
 * One transition, as a pure SHAPE question. It says whether the edge is on the
 * ladder and whether the evidence the TARGET state demands is described. It
 * moves nothing and it reads nothing: only an activation controller could move a
 * rule, and lifecycle-assurance.v5.js holds that seam bound to null.
 *
 * Evidence owed per target state, each with its own refusal:
 *   reviewed -> an independent reviewer (not the proposer)
 *   tested   -> a named behavioural test that PASSED
 *   shadow   -> a shadow window, every miss carrying a disposition
 *   active   -> an enforceable control AND a fallback; and for a MANDATORY rule
 *               that control must be machine control
 *   retired  -> a retirement behaviour, plus a successor when the behaviour is
 *               `superseded_only`
 */
export function classifyRuleTransition(request) {
  exact(object(request, "request"), TRANSITION_FIELDS, "request");
  pattern(request.rule_id, IDENTIFIER, "malformed_identifier", "request.rule_id");
  member(request.rule_class, V5_F05_RULE_CLASSES, "request.rule_class");
  bool(request.mandatory, "request.mandatory");
  // TOKENS on the way in as well as on the way out: the edge being asked about
  // is itself conditional on a rule registry that does not exist, so `active`
  // is not a word this boundary accepts or returns.
  const from = member(request.from_state_token, V5_A02_CONDITIONAL_RULE_STATE_TOKENS,
    "request.from_state_token");
  const to = member(request.to_state_token, V5_A02_CONDITIONAL_RULE_STATE_TOKENS,
    "request.to_state_token");

  if (request.review !== null) {
    exact(object(request.review, "request.review"), REVIEW_FIELDS, "request.review");
    str(request.review.proposer_actor_id, "request.review.proposer_actor_id");
    str(request.review.reviewer_actor_id, "request.review.reviewer_actor_id");
  }
  if (request.tests !== null) {
    list(request.tests, "request.tests");
    request.tests.forEach((test, index) => {
      const path = `request.tests[${index}]`;
      exact(object(test, path), TEST_FIELDS, path);
      pattern(test.test_ref, REF, "malformed_ref", `${path}.test_ref`);
      member(test.result, ["fail", "pass", "skipped"], `${path}.result`);
    });
  }
  if (request.shadow_window !== null) {
    const path = "request.shadow_window";
    exact(object(request.shadow_window, path), SHADOW_FIELDS, path);
    pattern(request.shadow_window.window_ref, REF, "malformed_ref", `${path}.window_ref`);
    instant(request.shadow_window.opened_at, `${path}.opened_at`);
    if (request.shadow_window.closed_at !== null) instant(request.shadow_window.closed_at, `${path}.closed_at`);
    list(request.shadow_window.misses, `${path}.misses`);
    request.shadow_window.misses.forEach((miss, index) => {
      const at = `${path}.misses[${index}]`;
      exact(object(miss, at), MISS_FIELDS, at);
      pattern(miss.miss_ref, REF, "malformed_ref", `${at}.miss_ref`);
      if (miss.disposition !== null)
        member(miss.disposition, V5_A02_SHADOW_MISS_DISPOSITIONS, `${at}.disposition`);
    });
  }
  const control = validateControl(request.control, "request.control");
  const fallback = validateFallback(request.fallback, "request.fallback");
  if (request.retirement !== null) {
    const path = "request.retirement";
    exact(object(request.retirement, path), RETIREMENT_FIELDS, path);
    member(request.retirement.behavior, V5_F05_RETIREMENT_BEHAVIORS, `${path}.behavior`);
    if (request.retirement.successor_rule_id !== null)
      pattern(request.retirement.successor_rule_id, IDENTIFIER, "malformed_identifier",
        `${path}.successor_rule_id`);
  }

  let outcome = {
    would_permit_if_authoritative: true,
    reason_id: null,
    note: `${from} -> ${to} is on the ladder and its evidence shape is complete`,
    detail: {},
  };

  if (!RULE_TOKEN_TRANSITIONS[from].includes(to)) {
    outcome = wouldNotPermit("rule_state_transition_not_permitted",
      `${from} -> ${to} is not an edge of the rule lifecycle`,
      { from_state_token: from, to_state_token: to, permitted: [...RULE_TOKEN_TRANSITIONS[from]] });
  } else if (to === RULE_TOKEN.reviewed) {
    if (request.review === null)
      outcome = wouldNotPermit("rule_reviewer_not_independent",
        "review states no reviewer at all", { rule_id: request.rule_id });
    else if (request.review.reviewer_actor_id === request.review.proposer_actor_id)
      outcome = wouldNotPermit("rule_reviewer_not_independent",
        "the proposer may not be the reviewer",
        { actor_id: request.review.proposer_actor_id });
  } else if (to === RULE_TOKEN.tested) {
    const tests = request.tests ?? [];
    if (tests.length === 0)
      outcome = wouldNotPermit("rule_tests_absent",
        "a rule cannot be tested by no test", { rule_id: request.rule_id });
    else {
      const notPassing = tests.filter(test => test.result !== "pass").map(test => test.test_ref).sort();
      if (notPassing.length > 0)
        outcome = wouldNotPermit("rule_tests_not_passing",
          "every named test must have passed", { not_passing: notPassing });
    }
  } else if (to === RULE_TOKEN.shadow) {
    if (request.shadow_window === null)
      outcome = wouldNotPermit("shadow_window_absent",
        "shadow observation needs a shadow window", { rule_id: request.rule_id });
  } else if (to === RULE_TOKEN.active) {
    // Shadow misses are checked FIRST, because the catalog excludes "shadow
    // misses without disposition" from what may count, and an undisposed miss
    // is an open question about the very control being switched on.
    const misses = request.shadow_window?.misses ?? [];
    const undisposed = misses.filter(miss => miss.disposition === null).map(miss => miss.miss_ref).sort();
    if (undisposed.length > 0)
      outcome = wouldNotPermit("shadow_miss_without_disposition",
        "a shadow miss nobody dispositioned is an open question, not evidence",
        { undisposed });
    else if (control === null)
      outcome = wouldNotPermit("active_rule_control_unmapped",
        "an active rule must map to a control that actually enforces it",
        { rule_id: request.rule_id });
    else if (fallback === null)
      outcome = wouldNotPermit("active_rule_fallback_absent",
        "an active rule must say what happens when its control is unavailable",
        { rule_id: request.rule_id, control_id: control.control_id });
    else if (request.mandatory &&
      !V5_A02_MACHINE_ENFORCEMENT_MECHANISMS.includes(control.enforcement_mechanism))
      outcome = wouldNotPermit("mandatory_rule_without_machine_control",
        "a mandatory rule enforced only by judgment or preference denies nothing",
        { rule_id: request.rule_id, enforcement_mechanism: control.enforcement_mechanism });
  } else if (to === RULE_TOKEN.retired) {
    if (request.retirement === null)
      outcome = wouldNotPermit("rule_retirement_successor_absent",
        "retirement states no behaviour", { rule_id: request.rule_id });
    else if (request.retirement.behavior === "superseded_only" &&
      request.retirement.successor_rule_id === null)
      outcome = wouldNotPermit("rule_retirement_successor_absent",
        "a superseded_only retirement must name the rule that supersedes it",
        { rule_id: request.rule_id });
  }

  return deepFreeze({
    schema_version: V5_A02_LIFECYCLE_SCHEMA_VERSION,
    policy_version: V5_A02_LIFECYCLE_POLICY_VERSION,
    classification: "rule_lifecycle_transition_shape",
    tenant: ORGANIZATION_TENANT_ID,
    rule_id: request.rule_id,
    from_state_token: from,
    to_state_token: to,
    would_permit_if_authoritative: outcome.would_permit_if_authoritative,
    reason_id: outcome.reason_id,
    note: outcome.note,
    detail: deepFreeze(outcome.detail),
    permitted_to_state_tokens_from: [...RULE_TOKEN_TRANSITIONS[from]],
    reversible_activation_edge: from === RULE_TOKEN.active && to === RULE_TOKEN.shadow,
    // Satisfying the clause records that the clause is satisfied. It moves no
    // rule, and no function in this system can.
    performs_transition: false,
    is_not_authority: true,
    evidence_source: V5_A02_CLASSIFIER_EVIDENCE_SOURCE,
    decided_by: "deterministic_classifier",
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// ENFORCEMENT COVERAGE — checkable_done 3.
// "Every active rule maps to enforceable control and fallback."
// ---------------------------------------------------------------------------

const COVERAGE_RULE_FIELDS = Object.freeze([
  "rule_id", "version", "rule_class", "state_token", "mandatory",
  "binding_text_present", "control", "fallback",
]);
const COVERAGE_REQUEST_FIELDS = Object.freeze(["as_of", "rules"]);

function classAdmitsMechanism(ruleClass, mechanism) {
  return V5_A02_CLASS_ENFORCEMENT_MECHANISM[ruleClass] === mechanism;
}

/**
 * The coverage clause over a whole rule set SHAPE.
 *
 * ORDERED QUESTIONS per rule, so two readers reach the same verdict:
 *   1. Does the rule's declared token say it would be the switched-on one? If
 *      not it is out of scope and is reported as such — a rule that would be
 *      proposed or retired owes no control.  Tokens again, not states: the
 *      caller describes a rule set, and nothing here reads one.
 *   2. Does it name a control at all? If its only claim is that its binding text
 *      exists, it is UNMAPPED with `rule_presence_is_not_enforcement`. This is
 *      the catalog's excluded "rule presence as enforcement proof", enforced.
 *   3. Does the control's mechanism belong to the rule's class family? A
 *      code_enforced rule whose control is a partner preference is not enforced
 *      by that control.
 *   4. Is the rule mandatory with a non-machine control? Unmapped.
 *   5. Does it name a fallback? Without one, nobody knows what happens when the
 *      control is unavailable, and an unavailable control silently becomes no
 *      control.
 *
 * An unmapped rule is LISTED, with its reason, rather than hidden.
 * `would_be_covered_if_authoritative` is derived from that list and from
 * nothing a caller can set — and even then it is a statement about the SHAPE of
 * the rule set, because no rule registry or control-implementation reader
 * exists to check a single one of these controls against the code it names.
 */
export function classifyRuleEnforcementCoverage(request) {
  exact(object(request, "request"), COVERAGE_REQUEST_FIELDS, "request");
  instant(request.as_of, "request.as_of");
  list(request.rules, "request.rules");

  // The switched-on token; every other token owes no control.
  const seen = new Set();
  const mapped = [];
  const unmapped = [];
  const outOfScope = [];

  request.rules.forEach((rule, index) => {
    const path = `request.rules[${index}]`;
    exact(object(rule, path), COVERAGE_RULE_FIELDS, path);
    pattern(rule.rule_id, IDENTIFIER, "malformed_identifier", `${path}.rule_id`);
    if (!Number.isInteger(rule.version) || rule.version < 1)
      fail("not_an_integer", `${path}.version must be a positive integer`, { path });
    member(rule.rule_class, V5_F05_RULE_CLASSES, `${path}.rule_class`);
    member(rule.state_token, V5_A02_CONDITIONAL_RULE_STATE_TOKENS, `${path}.state_token`);
    bool(rule.mandatory, `${path}.mandatory`);
    bool(rule.binding_text_present, `${path}.binding_text_present`);
    const control = validateControl(rule.control, `${path}.control`);
    const fallback = validateFallback(rule.fallback, `${path}.fallback`);

    const key = `${rule.rule_id}@${rule.version}`;
    if (seen.has(key)) fail("duplicate_rule", `${path} repeats ${key}`, { path, rule: key });
    seen.add(key);

    if (rule.state_token !== RULE_TOKEN.active) {
      outOfScope.push(deepFreeze({
        rule_id: rule.rule_id, version: rule.version, state_token: rule.state_token }));
      return;
    }

    const record = {
      rule_id: rule.rule_id, version: rule.version, rule_class: rule.rule_class,
      mandatory: rule.mandatory,
      control_id: control?.control_id ?? null,
      control_ref: control?.control_ref ?? null,
      enforcement_mechanism: control?.enforcement_mechanism ?? null,
      fallback_kind: fallback?.kind ?? null,
      fallback_ref: fallback?.ref ?? null,
    };

    if (control === null) {
      unmapped.push(deepFreeze({
        ...record,
        reason_id: reason(rule.binding_text_present
          ? "rule_presence_is_not_enforcement"
          : "active_rule_control_unmapped"),
      }));
      return;
    }
    if (!classAdmitsMechanism(rule.rule_class, control.enforcement_mechanism)) {
      unmapped.push(deepFreeze({ ...record, reason_id: reason("rule_control_mechanism_not_for_class") }));
      return;
    }
    if (rule.mandatory && !V5_A02_MACHINE_ENFORCEMENT_MECHANISMS.includes(control.enforcement_mechanism)) {
      unmapped.push(deepFreeze({ ...record, reason_id: reason("mandatory_rule_without_machine_control") }));
      return;
    }
    if (fallback === null) {
      unmapped.push(deepFreeze({ ...record, reason_id: reason("active_rule_fallback_absent") }));
      return;
    }
    mapped.push(deepFreeze(record));
  });

  const covered = unmapped.length === 0;
  const blocking = covered ? null : unmapped[0].reason_id;

  return deepFreeze({
    schema_version: V5_A02_LIFECYCLE_SCHEMA_VERSION,
    policy_version: V5_A02_LIFECYCLE_POLICY_VERSION,
    classification: "rule_enforcement_coverage_shape",
    tenant: ORGANIZATION_TENANT_ID,
    as_of: request.as_of,
    would_be_covered_if_authoritative: covered,
    reason_id: blocking,
    // Derived from the unmapped list and from nothing a caller can set. An
    // EMPTY active set is covered only in the trivial sense, and the counts
    // beside it say so rather than letting "0 unmapped" read as coverage.
    active_rule_count: mapped.length + unmapped.length,
    mapped_count: mapped.length,
    unmapped_count: unmapped.length,
    mapped_rules: deepFreeze(mapped),
    // Listed, not hidden. This is the "or is listed as unmapped" half.
    unmapped_rules: deepFreeze(unmapped),
    out_of_scope_rules: deepFreeze(outOfScope),
    fallback_kinds: [...V5_A02_FALLBACK_KINDS],
    machine_enforcement_mechanisms: [...V5_A02_MACHINE_ENFORCEMENT_MECHANISMS],
    caller_stated_coverage: false,
    is_not_authority: true,
    evidence_source: V5_A02_CLASSIFIER_EVIDENCE_SOURCE,
    decided_by: "deterministic_classifier",
    effects: V5_NO_EFFECTS,
  });
}
