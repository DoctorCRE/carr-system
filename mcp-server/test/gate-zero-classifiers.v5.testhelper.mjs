// V5-A02 GATE ZERO DETERMINISTIC CLASSIFIERS — THE TEST-ONLY ENTRY.
//
// READ THIS FIRST, BECAUSE THE FILE NAME IS THE CONTRACT. Nothing in this file
// is part of the Gate Zero public surface. `gate-zero-assurance.v5.js` does not
// import it, no production module imports it, and
// gate-zero-assurance.v5.test.mjs proves that with a parser-backed import scan
// rather than a promise. The only importer is the test file.
//
// WHY IT EXISTS AT ALL. The three deterministic clauses V5-A02's checkable_done
// names are real, and they are worth proving clause by clause:
//
//   1. the four bound predecessors, each once, each a self-binding accepted
//      criteria_met outcome, no outcome record doing double duty;
//   2. a scheduler canary whose readback joins it exactly and which is bound to
//      the accepted scheduler-active outcome;
//   3. non-green propagation — a gate cannot report success over a failed
//      ancestor.
//
// WHAT THEY ARE NOT. There is NO authoritative predecessor-outcome reader, NO
// scheduler reader and NO gate-conclusion reader in this repository. A caller
// object is therefore not evidence and never becomes evidence, so these
// functions may not say `ok`, `green`, `joins_exactly` or `allow` about
// anything. They answer in the conditional and in the conditional only:
//
//   would_satisfy_if_authoritative        — this observation's SHAPE would
//   would_join_exactly_if_authoritative     satisfy the clause IF it had come
//   would_be_green_if_authoritative         from an authoritative reader, which
//                                           it did not and cannot have.
//
// Every result also carries `is_not_authority: true` and
// `evidence_source: "caller_supplied_shapes_not_authority"`, so a value that
// escaped into a consumer would still refuse to read as a pass.
//
// Every function here is PURE — no filesystem, no network, no database, no
// clock, no environment — and takes its reference instant from the caller.
//
// TWO KINDS OF NO, inherited unchanged from global-boundaries.v5.js:
//   * A CLASSIFICATION is RETURNED: `would_*_if_authoritative` false with a
//     stable `reason_id` from the public module's closed registry.
//   * A CONTRACT VIOLATION THROWS V5BoundaryError. Unknown fields, open
//     schemas, unknown enum members, malformed digests and cyclic graphs are
//     not classification questions: the shape cannot be read at all.

import { digest } from "../src/artifact-trust.js";
import { V5BoundaryError, V5_NO_EFFECTS } from "../src/global-boundaries.v5.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  GATE_ZERO_STEP_REF,
  V5_A02_ACCEPTANCE_STATES,
  V5_A02_CRITERIA_STATES,
  V5_A02_GATE_CONCLUSIONS,
  V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS,
  V5_A02_GATE_ZERO_REASON_IDS,
  V5_A02_GATE_ZERO_SCHEMA_VERSION,
  V5_A02_OBSERVATION_STATES,
  V5_A02_POLICY_VERSION,
  V5_A02_SCHEDULER_STEP_REF,
} from "../src/gate-zero-assurance.v5.js";

/** Said on every result, so an escaped value still reads as "not authority". */
export const V5_A02_CLASSIFIER_EVIDENCE_SOURCE = "caller_supplied_shapes_not_authority";

const REF = /^[a-z][a-z0-9-]*:[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$/;
const STEP_REF = /^step:[a-z0-9][a-z0-9-]{0,127}$/;
const DIGEST = /^sha256:[0-9a-f]{64}$/;
const ISO_INSTANT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z$/;
const GATE_ID = /^[a-z0-9][a-z0-9-]{0,127}$/;

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

/** An open schema is an unenforced one: every request object is closed. */
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

function list(value, path) {
  if (!Array.isArray(value)) fail("not_an_array", `${path} must be an array`, { path });
  return value;
}

/** The public module owns the closed reason registry; this file only cites it. */
function reason(id) {
  if (!V5_A02_GATE_ZERO_REASON_IDS.includes(id))
    fail("unknown_reason_id", `${id} is not a registered reason`, { reason_id: id });
  return id;
}

// ---------------------------------------------------------------------------
// One predecessor observation.
// ---------------------------------------------------------------------------

const OBSERVATION_FIELDS = Object.freeze([
  "step_ref", "state", "outcome_digest", "outcome", "observed_at",
]);
const OUTCOME_FIELDS = Object.freeze([
  "step_ref", "outcome_ref", "acceptance_state", "criteria_state",
  "accepted_by_identity", "accepted_at",
]);

function wouldNot(reasonId, note, detail) {
  return deepFreeze({
    would_satisfy_if_authoritative: false,
    reason_id: reason(reasonId),
    note,
    detail: detail ?? {},
    is_not_authority: true,
    evidence_source: V5_A02_CLASSIFIER_EVIDENCE_SOURCE,
  });
}

function wouldSatisfy(note, detail) {
  return deepFreeze({
    would_satisfy_if_authoritative: true,
    reason_id: null,
    note,
    detail: detail ?? {},
    is_not_authority: true,
    evidence_source: V5_A02_CLASSIFIER_EVIDENCE_SOURCE,
  });
}

/**
 * WHAT A "STAND-IN" IS, PROCEDURALLY, because a classifier that cannot tell one
 * from a real outcome measures nothing. An observation fails the clause when it
 * fails any of the five structural bindings, and each refuses by its own name:
 *   (a) it states no outcome at all                  -> predecessor_outcome_absent
 *   (b) its body names a different step              -> predecessor_outcome_bound_to_other_step
 *   (c) its digest is not the digest of its body     -> predecessor_outcome_digest_mismatch
 *   (d) it is not an ACCEPTED criteria_met outcome   -> predecessor_outcome_not_accepted
 *                                                    -> predecessor_criteria_not_met
 *   (e) it reuses another predecessor's outcome      -> duplicate_predecessor_outcome (the join)
 * (d) is the trap the Gate Zero readiness study names explicitly: accepting
 * WR-000040's pending `criteria_not_met` proposal closes the review and does NOT
 * clear milestone 4. An accepted refusal is still a refusal.
 *
 * `binding` is `{ step_ref, as_of_ms }`: the predecessor this observation must
 * be OF, and the reference instant nothing may be observed after.
 */
export function classifyPredecessorObservation(observation, binding) {
  exact(object(binding, "binding"), ["step_ref", "as_of_ms"], "binding");
  const stepRef = member(binding.step_ref, V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS, "binding.step_ref");
  if (!Number.isInteger(binding.as_of_ms))
    fail("not_an_integer", "binding.as_of_ms must be an integer", { path: "binding.as_of_ms" });

  const path = `observation(${stepRef})`;
  exact(object(observation, path), OBSERVATION_FIELDS, path);
  pattern(observation.step_ref, STEP_REF, "malformed_step_ref", `${path}.step_ref`);
  member(observation.state, V5_A02_OBSERVATION_STATES, `${path}.state`);

  if (observation.step_ref !== stepRef)
    return wouldNot("predecessor_outcome_bound_to_other_step",
      "an observation of another step is not an observation of this one",
      { expected_step_ref: stepRef, observed_step_ref: observation.step_ref });

  // (a) NOTHING WAS SEEN. "pending" and "absent" state no outcome, and an
  // outcome nobody observed is the missing one this clause exists to name.
  if (observation.state !== "observed") {
    for (const key of ["outcome", "outcome_digest", "observed_at"])
      if (observation[key] !== null)
        fail("unobserved_states_an_outcome",
          `${path}.${key} must be null when nothing was observed`, { path, key });
    return wouldNot("predecessor_outcome_absent",
      "this predecessor states no observed outcome", { step_ref: stepRef, state: observation.state });
  }

  const outcomePath = `${path}.outcome`;
  exact(object(observation.outcome, outcomePath), OUTCOME_FIELDS, outcomePath);
  pattern(observation.outcome.step_ref, STEP_REF, "malformed_step_ref", `${outcomePath}.step_ref`);
  pattern(observation.outcome.outcome_ref, REF, "malformed_ref", `${outcomePath}.outcome_ref`);
  member(observation.outcome.acceptance_state, V5_A02_ACCEPTANCE_STATES, `${outcomePath}.acceptance_state`);
  member(observation.outcome.criteria_state, V5_A02_CRITERIA_STATES, `${outcomePath}.criteria_state`);
  pattern(observation.outcome_digest, DIGEST, "malformed_digest", `${path}.outcome_digest`);
  const observedMs = instant(observation.observed_at, `${path}.observed_at`);

  // (b) THE BODY NAMES ANOTHER STEP. The envelope agreeing with the binding is
  // not enough: the outcome itself has to be an outcome of this step.
  if (observation.outcome.step_ref !== stepRef)
    return wouldNot("predecessor_outcome_bound_to_other_step",
      "the outcome body is an outcome of a different step",
      { expected_step_ref: stepRef, outcome_step_ref: observation.outcome.step_ref });

  // (c) SELF-BINDING. The cited digest must be the digest of the body that came
  // back. This is the one check a stand-in cannot survive by relabelling.
  const computed = digest(observation.outcome);
  if (computed !== observation.outcome_digest)
    return wouldNot("predecessor_outcome_digest_mismatch",
      "the outcome does not hash to the digest that cited it",
      { cited: observation.outcome_digest, computed });

  // (d) AN ACCEPTED REFUSAL IS STILL A REFUSAL, and the two fields are separate
  // because accepting a criteria_not_met outcome closes a review and clears no
  // milestone.
  if (observation.outcome.acceptance_state !== "accepted")
    return wouldNot("predecessor_outcome_not_accepted",
      "a proposed or rejected outcome does not close its predecessor",
      { acceptance_state: observation.outcome.acceptance_state });
  for (const key of ["accepted_by_identity", "accepted_at"])
    str(observation.outcome[key], `${outcomePath}.${key}`);
  instant(observation.outcome.accepted_at, `${outcomePath}.accepted_at`);
  if (observation.outcome.criteria_state !== "criteria_met")
    return wouldNot("predecessor_criteria_not_met",
      "an accepted outcome that did not meet its criteria clears no milestone",
      { criteria_state: observation.outcome.criteria_state });

  if (observedMs > binding.as_of_ms)
    return wouldNot("predecessor_observed_after_reference",
      "an outcome observed after the reference instant is not evidence at it",
      { observed_at: observation.observed_at });

  return wouldSatisfy(
    "the SHAPE is an accepted criteria_met self-binding outcome at or before the reference",
    { step_ref: stepRef, outcome_digest: observation.outcome_digest });
}

// ---------------------------------------------------------------------------
// The scheduler canary and its readback.
// ---------------------------------------------------------------------------

const CANARY_FIELDS = Object.freeze([
  "canary_id", "dispatched_at", "expected_digest", "scheduler_outcome_digest",
]);
const READBACK_FIELDS = Object.freeze([
  "canary_id", "state", "observed_at", "observed_digest",
]);

/**
 * "Scheduler canary/readback join exactly", as four bindings rather than a
 * judgment: the readback is OF this canary, it hashes to what the canary
 * expected, it happened strictly after the dispatch, and the canary itself is
 * bound to the scheduler-active outcome. Without the fourth, a canary shape
 * describes a scheduler running — not THIS receipt's scheduler running.
 *
 * `schedulerOutcomeDigest` is null when the scheduler predecessor's own shape
 * did not satisfy its clause; the canary is then unbindable and says so.
 */
export function classifySchedulerCanary(canary, readback, schedulerOutcomeDigest) {
  exact(object(canary, "canary"), CANARY_FIELDS, "canary");
  str(canary.canary_id, "canary.canary_id");
  const dispatchedMs = instant(canary.dispatched_at, "canary.dispatched_at");
  pattern(canary.expected_digest, DIGEST, "malformed_digest", "canary.expected_digest");
  pattern(canary.scheduler_outcome_digest, DIGEST, "malformed_digest", "canary.scheduler_outcome_digest");

  exact(object(readback, "readback"), READBACK_FIELDS, "readback");
  member(readback.state, V5_A02_OBSERVATION_STATES, "readback.state");

  if (schedulerOutcomeDigest !== null)
    pattern(schedulerOutcomeDigest, DIGEST, "malformed_digest", "schedulerOutcomeDigest");

  if (readback.state !== "observed") {
    for (const key of ["observed_at", "observed_digest"])
      if (readback[key] !== null)
        fail("unobserved_states_a_readback",
          `readback.${key} must be null when nothing was read back`, { path: `readback.${key}`, key });
    return wouldNot("scheduler_readback_absent",
      "a canary nobody read back is a canary nobody heard", { state: readback.state });
  }

  str(readback.canary_id, "readback.canary_id");
  const readbackMs = instant(readback.observed_at, "readback.observed_at");
  pattern(readback.observed_digest, DIGEST, "malformed_digest", "readback.observed_digest");

  if (readback.canary_id !== canary.canary_id)
    return wouldNot("scheduler_readback_canary_mismatch",
      "the readback is of a different canary",
      { canary_id: canary.canary_id, readback_canary_id: readback.canary_id });
  if (readback.observed_digest !== canary.expected_digest)
    return wouldNot("scheduler_canary_digest_mismatch",
      "the readback does not carry what the canary dispatched",
      { expected: canary.expected_digest, observed: readback.observed_digest });
  if (readbackMs <= dispatchedMs)
    return wouldNot("scheduler_readback_not_after_dispatch",
      "a readback at or before its own dispatch describes nothing running",
      { dispatched_at: canary.dispatched_at, observed_at: readback.observed_at });
  if (schedulerOutcomeDigest === null || canary.scheduler_outcome_digest !== schedulerOutcomeDigest)
    return wouldNot("scheduler_canary_not_bound_to_receipt",
      "the canary is not bound to the accepted scheduler-active outcome",
      { canary_binds: canary.scheduler_outcome_digest, scheduler_outcome_digest: schedulerOutcomeDigest });

  return wouldSatisfy("canary and readback shapes join exactly and bind the scheduler outcome shape",
    { canary_id: canary.canary_id });
}

// ---------------------------------------------------------------------------
// Non-green propagation. "A failed injected gate cannot claim green."
// ---------------------------------------------------------------------------

const GATE_FIELDS = Object.freeze(["gate_id", "conclusion", "depends_on"]);

/**
 * A gate WOULD be green when its OWN conclusion is "success" AND every gate it
 * descends from would be green. The second half is what makes an injected
 * failure impossible to claim past: a gate that reports success over a failed
 * ancestor is answered `inherited_non_green`, and there is no field on the
 * request that can say otherwise — `claimed_green`, `override`, `waived` and
 * every other self-assertion are unknown fields, and an unknown field is
 * unreadable.
 *
 * A cycle is a CONTRACT VIOLATION: a dependency graph that is not a DAG cannot
 * be walked, so this fails closed rather than answering over it.
 */
export function classifyGateGraph(gates) {
  list(gates, "gates");
  const byId = new Map();
  gates.forEach((gate, index) => {
    const path = `gates[${index}]`;
    exact(object(gate, path), GATE_FIELDS, path);
    pattern(gate.gate_id, GATE_ID, "malformed_gate_id", `${path}.gate_id`);
    member(gate.conclusion, V5_A02_GATE_CONCLUSIONS, `${path}.conclusion`);
    list(gate.depends_on, `${path}.depends_on`);
    gate.depends_on.forEach((dep, at) =>
      pattern(dep, GATE_ID, "malformed_gate_id", `${path}.depends_on[${at}]`));
    for (let i = 1; i < gate.depends_on.length; i += 1) {
      if (gate.depends_on[i] === gate.depends_on[i - 1])
        fail("duplicate_member", `${path}.depends_on repeats ${gate.depends_on[i]}`, { path });
      if (gate.depends_on[i] < gate.depends_on[i - 1])
        fail("unsorted_list", `${path}.depends_on must be C-sorted`, { path, at: i });
    }
    if (byId.has(gate.gate_id))
      fail("duplicate_gate_id", `${path}.gate_id repeats ${gate.gate_id}`, { path, gate_id: gate.gate_id });
    byId.set(gate.gate_id, gate);
  });

  // Cycle detection first: an unwalkable graph is unreadable, not classifiable.
  const colour = new Map();
  const walk = id => {
    const state = colour.get(id);
    if (state === "done") return;
    if (state === "open") fail("cyclic_gate_graph", `gate ${id} depends on itself`, { gate_id: id });
    const gate = byId.get(id);
    if (!gate) return;
    colour.set(id, "open");
    for (const dep of gate.depends_on) walk(dep);
    colour.set(id, "done");
  };
  for (const id of byId.keys()) walk(id);

  const selfNonGreen = [];
  const unknownDeps = [];
  for (const gate of byId.values()) {
    if (gate.conclusion !== "success") selfNonGreen.push(gate.gate_id);
    for (const dep of gate.depends_on) if (!byId.has(dep)) unknownDeps.push(`${gate.gate_id}->${dep}`);
  }

  // Transitive closure over the DAG, memoised.
  const greenMemo = new Map();
  const wouldBeGreen = id => {
    if (greenMemo.has(id)) return greenMemo.get(id);
    const gate = byId.get(id);
    // An ancestor this graph does not contain would not be green: an unknown
    // gate is an unread gate, and unread is never success.
    if (!gate) { greenMemo.set(id, false); return false; }
    greenMemo.set(id, false); // pessimistic while the walk is open
    const green = gate.conclusion === "success" && gate.depends_on.every(wouldBeGreen);
    greenMemo.set(id, green);
    return green;
  };

  const inherited = [];
  for (const gate of byId.values())
    if (gate.conclusion === "success" && !wouldBeGreen(gate.gate_id)) inherited.push(gate.gate_id);

  // An EMPTY graph would not be green. A Gate Zero that read no gate read nothing.
  const allGreen = byId.size > 0 && [...byId.keys()].every(wouldBeGreen);
  const reasonId = unknownDeps.length > 0
    ? "gate_dependency_unknown"
    : (allGreen ? null : "gate_graph_not_green");

  return deepFreeze({
    schema_version: V5_A02_GATE_ZERO_SCHEMA_VERSION,
    policy_version: V5_A02_POLICY_VERSION,
    classification: "gate_graph_shape",
    would_be_green_if_authoritative: allGreen,
    reason_id: allGreen ? null : reason(reasonId ?? "gate_graph_not_green"),
    gates_read: [...byId.keys()].sort(),
    non_green_gates: selfNonGreen.sort(),
    inherited_non_green_gates: inherited.sort(),
    unknown_dependencies: unknownDeps.sort(),
    caller_stated_green: false,
    is_not_authority: true,
    evidence_source: V5_A02_CLASSIFIER_EVIDENCE_SOURCE,
    decided_by: "deterministic_classifier",
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// The join.
// ---------------------------------------------------------------------------

const JOIN_FIELDS = Object.freeze([
  "as_of", "predecessor_observations", "scheduler_canary", "scheduler_readback", "gates",
]);

/**
 * "WR prerequisites and scheduler canary/readback join exactly", as a SHAPE
 * question and nothing more.
 *
 * EXACTLY means exactly: the four bound predecessors, each present once, each a
 * self-binding accepted criteria_met outcome shape, no two of them the same
 * outcome record, plus a canary/readback pair bound to the scheduler receipt,
 * plus a gate graph that would be green.
 *
 * `would_join_exactly_if_authoritative: true` says only "the shapes you showed
 * me join". It is not a Gate Zero pass, it is not a predecessor reading, and no
 * consumer may treat it as either: the authoritative readers named in
 * V5_A02_GATE_ZERO_OWED_SEAMS do not exist.
 */
export function classifyGateZeroJoin(request) {
  exact(object(request, "request"), JOIN_FIELDS, "request");
  const asOfMs = instant(request.as_of, "request.as_of");
  list(request.predecessor_observations, "request.predecessor_observations");

  const seenSteps = new Set();
  const seenOutcomeRefs = new Map();
  const perPredecessor = {};
  const shapesCited = [];

  request.predecessor_observations.forEach((observation, index) => {
    const path = `request.predecessor_observations[${index}]`;
    object(observation, path);
    const stepRef = pattern(observation.step_ref, STEP_REF, "malformed_step_ref", `${path}.step_ref`);
    if (!V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS.includes(stepRef))
      fail("unknown_predecessor_step",
        `${path}.step_ref is not one of Gate Zero's four bound predecessors`,
        { path, step_ref: stepRef, allowed: [...V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS] });
    if (seenSteps.has(stepRef))
      fail("duplicate_predecessor_observation",
        `${path}.step_ref repeats ${stepRef}`, { path, step_ref: stepRef });
    seenSteps.add(stepRef);
    const outcome = classifyPredecessorObservation(observation, { step_ref: stepRef, as_of_ms: asOfMs });
    perPredecessor[stepRef] = outcome;
    shapesCited.push(deepFreeze({
      step_ref: stepRef,
      state: observation.state,
      outcome_digest: observation.state === "observed" ? observation.outcome_digest : null,
      outcome_ref: observation.state === "observed" ? observation.outcome.outcome_ref : null,
      acceptance_state: observation.state === "observed" ? observation.outcome.acceptance_state : null,
      criteria_state: observation.state === "observed" ? observation.outcome.criteria_state : null,
      observed_at: observation.state === "observed" ? observation.observed_at : null,
      would_satisfy_if_authoritative: outcome.would_satisfy_if_authoritative,
      reason_id: outcome.reason_id,
    }));
  });

  // (e) ONE OUTCOME RECORD MAY NOT CLOSE TWO PREDECESSORS. Keyed on the
  // outcome_ref, not the digest: two bodies naming two different steps can never
  // share a digest, so a digest key would be a clause that can never fire. The
  // ref is the record's identity, and citing WR-000046's accepted outcome as
  // though it were also WR-000040's is exactly the stand-in this catches.
  // Checked after the per-step pass so the reuse is reported against the step
  // that reused it, in supplied order.
  for (const entry of shapesCited) {
    if (entry.outcome_ref === null || !perPredecessor[entry.step_ref].would_satisfy_if_authoritative) continue;
    const firstHolder = seenOutcomeRefs.get(entry.outcome_ref);
    if (firstHolder !== undefined) {
      perPredecessor[entry.step_ref] = wouldNot("duplicate_predecessor_outcome",
        "one outcome record cannot close two predecessors",
        { step_ref: entry.step_ref, also_claimed_by: firstHolder, outcome_ref: entry.outcome_ref });
    } else {
      seenOutcomeRefs.set(entry.outcome_ref, entry.step_ref);
    }
  }

  const missing = V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS.filter(step => !seenSteps.has(step));
  for (const step of missing)
    perPredecessor[step] = wouldNot("predecessor_set_incomplete",
      "this predecessor was not supplied at all", { step_ref: step });

  const schedulerOutcome = perPredecessor[V5_A02_SCHEDULER_STEP_REF];
  const schedulerDigest = schedulerOutcome?.would_satisfy_if_authoritative
    ? schedulerOutcome.detail.outcome_digest ?? null
    : null;
  const scheduler = classifySchedulerCanary(
    request.scheduler_canary, request.scheduler_readback, schedulerDigest);

  const gateGraph = classifyGateGraph(request.gates);

  const unsatisfied = V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS
    .filter(step => !perPredecessor[step].would_satisfy_if_authoritative);
  const joinsExactly = unsatisfied.length === 0 &&
    scheduler.would_satisfy_if_authoritative && gateGraph.would_be_green_if_authoritative;
  const blocking = unsatisfied.length > 0
    ? perPredecessor[unsatisfied[0]].reason_id
    : (!scheduler.would_satisfy_if_authoritative
      ? scheduler.reason_id
      : (gateGraph.would_be_green_if_authoritative ? null : gateGraph.reason_id));

  return deepFreeze({
    schema_version: V5_A02_GATE_ZERO_SCHEMA_VERSION,
    policy_version: V5_A02_POLICY_VERSION,
    classification: "gate_zero_predecessor_join_shape",
    tenant: ORGANIZATION_TENANT_ID,
    gate_zero_step_ref: GATE_ZERO_STEP_REF,
    as_of: request.as_of,
    would_join_exactly_if_authoritative: joinsExactly,
    reason_id: joinsExactly ? null : reason(blocking),
    required_predecessors: [...V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS],
    predecessors_supplied: [...seenSteps].sort(),
    predecessors_missing: missing,
    predecessors_unsatisfied: unsatisfied,
    predecessor_results: deepFreeze(perPredecessor),
    // Exactly what was CITED, per predecessor, and never what was verified.
    predecessor_shapes_cited: deepFreeze(shapesCited),
    scheduler_canary_result: scheduler,
    gate_graph_result: gateGraph,
    is_not_authority: true,
    evidence_source: V5_A02_CLASSIFIER_EVIDENCE_SOURCE,
    is_gate_zero_pass: false,
    decided_by: "deterministic_classifier",
    model_judgment_admitted: false,
    effects: V5_NO_EFFECTS,
  });
}
