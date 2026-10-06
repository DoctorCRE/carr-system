// V5-A02 half one — the Gate Zero surface, proved in two halves that must not
// be confused with each other.
//
// HALF A, THE PUBLIC SURFACE. Everything `gate-zero-assurance.v5.js` exports,
// enumerated from the module namespace, swept with every caller-controlled
// input shape — including the exact one-gate `{ conclusion: "success" }`
// construction the PR 985 reviewer used to get `green: true` and
// `decision: "allow"` out of the old module — and asserted never to produce a
// privileged outcome under any name. The surface is also asserted INDIFFERENT to
// its input: every shape produces byte-identical output, so there is no field a
// caller could reach for. Plus a parser-backed scan (V8's own ESM parser, via
// vm.SourceTextModule in a child process — not a regex) proving that src/ holds
// no test-only entry at all and that no module in src/ names a specifier under
// ../test/.
//
// HALF B, THE CLAUSES. The three deterministic clauses, proved clause by clause
// through `./gate-zero-classifiers.v5.testhelper.mjs` — a helper in THIS
// directory, not a module in src/, so a production import of it is impossible
// rather than merely absent. Their answers are conditional by name
// (`would_satisfy_if_authoritative`, `would_be_green_if_authoritative`,
// `would_join_exactly_if_authoritative`) because no authoritative
// predecessor-outcome, scheduler or gate-conclusion reader exists to make them
// anything else. Every negative is a single NAMED mutation of one clean request
// that satisfies, and each `clean*()` returns a fresh deep copy so a mutation in
// one test cannot leak into another.
//
//   node --test mcp-server/test/gate-zero-assurance.v5.test.mjs

import test, { after } from "node:test";
import assert from "node:assert/strict";
import {
  cpSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync,
} from "node:fs";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { spawnSync } from "node:child_process";
import { types } from "node:util";

import { digest } from "../src/artifact-trust.js";
import { V5BoundaryError, V5_NO_EFFECTS } from "../src/global-boundaries.v5.js";
import { GATE_ZERO_STEP_REF } from "../src/benchmark-minimum.v5.js";

import * as surface from "../src/gate-zero-assurance.v5.js";
import {
  PRE_PR_BASELINE, PRE_PR_BASELINE_DIGEST, PRE_PR_COMMIT, commitReachable,
  prePrCommitReachable, releasePrePrTrees, stagePrePrTree,
} from "./gate-zero-pre-pr-baseline.v5.testhelper.mjs";
import * as prePrBaselineHelper from "./gate-zero-pre-pr-baseline.v5.testhelper.mjs";
import * as producerModule from "../src/gate-zero-producer-registration.v5.js";
import {
  V5_A02_GATE_ZERO_GATE_ID,
  V5_A02_GATE_ZERO_PRODUCER_ROLE,
} from "../src/gate-zero-producer-registration.v5.js";
import {
  V5_A02_GATE_ZERO_SCHEMA_VERSION,
  V5_A02_POLICY_VERSION,
  V5_A02_DECISION_IDS,
  V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS,
  V5_A02_SCHEDULER_STEP_REF,
  V5_A02_OBSERVATION_STATES,
  V5_A02_ACCEPTANCE_STATES,
  V5_A02_CRITERIA_STATES,
  V5_A02_GATE_CONCLUSIONS,
  V5_A02_GATE_ZERO_REASON_IDS,
  V5_A02_GATE_ZERO_PRODUCER_SEAM,
  V5_A02_GATE_ZERO_PRODUCER_REGISTRATION,
  V5_A02_GATE_ZERO_OWED_SEAMS,
  V5_A02_PREDECESSOR_OUTCOME_READER_SEAM,
  V5_A02_SCHEDULER_READER_SEAM,
  V5_A02_GATE_CONCLUSION_READER_SEAM,
  readGateZeroPredecessorJoin,
  readGateGraphAssurance,
  emitGateZeroOutcome,
  v5A02GateZeroPolicyPreimage,
  v5A02GateZeroPolicyDigest,
  v5A02GateZeroPolicyCanonicalBytes,
  v5A02GateZeroR7Presence,
  V5_A02_GATE_ZERO_R7_PACKET_SHA256,
  V5_A02_GATE_ZERO_R7_SUPERSEDED_PACKET_SHA256,
} from "../src/gate-zero-assurance.v5.js";

import {
  V5_A02_CLASSIFIER_EVIDENCE_SOURCE,
  classifyPredecessorObservation,
  classifySchedulerCanary,
  classifyGateGraph,
  classifyGateZeroJoin,
} from "./gate-zero-classifiers.v5.testhelper.mjs";

const AS_OF = "2026-09-11T18:00:00Z";

function outcomeBody(stepRef, suffix) {
  return {
    step_ref: stepRef,
    outcome_ref: `outcome:${suffix}`,
    acceptance_state: "accepted",
    criteria_state: "criteria_met",
    accepted_by_identity: "joe",
    accepted_at: "2026-09-06T16:03:09Z",
  };
}

function observation(stepRef, suffix) {
  const outcome = outcomeBody(stepRef, suffix);
  return {
    step_ref: stepRef,
    state: "observed",
    outcome_digest: digest(outcome),
    outcome,
    observed_at: "2026-09-06T16:03:09Z",
  };
}

const SCHEDULER_OUTCOME_DIGEST = digest(outcomeBody(V5_A02_SCHEDULER_STEP_REF, "scheduler"));
const CANARY_DIGEST = `sha256:${"a".repeat(64)}`;

function cleanCanary() {
  return {
    canary_id: "canary-2026-09-11-0001",
    dispatched_at: "2026-09-11T17:00:00Z",
    expected_digest: CANARY_DIGEST,
    scheduler_outcome_digest: SCHEDULER_OUTCOME_DIGEST,
  };
}

function cleanReadback() {
  return {
    canary_id: "canary-2026-09-11-0001",
    state: "observed",
    observed_at: "2026-09-11T17:00:30Z",
    observed_digest: CANARY_DIGEST,
  };
}

function cleanGates() {
  return [
    { gate_id: "assurance-fabric-preactivation", conclusion: "success", depends_on: ["repo-hygiene"] },
    { gate_id: "repo-hygiene", conclusion: "success", depends_on: [] },
    { gate_id: "scheduler-truth", conclusion: "success", depends_on: ["repo-hygiene"] },
  ];
}

function cleanJoin() {
  return {
    as_of: AS_OF,
    predecessor_observations: [
      observation("step:scheduler-active-receipt", "scheduler"),
      observation("step:wr40-repository-outcome", "wr40"),
      observation("step:wr46-dissolution-outcome", "wr46"),
      observation("step:wr54-backup-recovery-outcome", "wr54"),
    ],
    scheduler_canary: cleanCanary(),
    scheduler_readback: cleanReadback(),
    gates: cleanGates(),
  };
}

// ===========================================================================
// HALF A — THE PUBLIC SURFACE.
// ===========================================================================

/**
 * Exactly what this module may export. A new name here is a deliberate decision
 * a reader has to make, which is the point: the four classifiers used to be on
 * this list, and that was the defect.
 */
const EXPECTED_PUBLIC_EXPORTS = [
  "GATE_ZERO_STEP_REF",
  "V5_A02_ACCEPTANCE_STATES",
  "V5_A02_CRITERIA_STATES",
  "V5_A02_DECISION_IDS",
  "V5_A02_GATE_CONCLUSIONS",
  "V5_A02_GATE_CONCLUSION_READER_SEAM",
  "V5_A02_GATE_ZERO_OWED_SEAMS",
  "V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS",
  "V5_A02_GATE_ZERO_PRODUCER_REGISTRATION",
  "V5_A02_GATE_ZERO_PRODUCER_SEAM",
  "V5_A02_GATE_ZERO_R7_PACKET_SHA256",
  "V5_A02_GATE_ZERO_R7_SUPERSEDED_PACKET_SHA256",
  "V5_A02_GATE_ZERO_REASON_IDS",
  "V5_A02_GATE_ZERO_SCHEMA_VERSION",
  "V5_A02_OBSERVATION_STATES",
  "V5_A02_POLICY_VERSION",
  "V5_A02_PREDECESSOR_OUTCOME_READER_SEAM",
  "V5_A02_SCHEDULER_READER_SEAM",
  "V5_A02_SCHEDULER_STEP_REF",
  "V5_NO_EFFECTS",
  "emitGateZeroOutcome",
  "readGateGraphAssurance",
  "readGateZeroPredecessorJoin",
  "v5A02GateZeroPolicyCanonicalBytes",
  "v5A02GateZeroPolicyDigest",
  "v5A02GateZeroPolicyPreimage",
  "v5A02GateZeroR7Presence",
];

/**
 * THE CLOSED PRIVILEGED UNION, copied from the standing rule learned over nine
 * review rounds on 2026-09-11, swept AS EXACT MATCH AND AS SUBSTRING the rule
 * says to. The earlier revision of this file listed only the handful of words
 * this slice happened to use, which is how `present` — a union member — was
 * exported from two modules under the sweep's nose, as `present` on the r7
 * finding and as `r7_entry_present` on the registration. A sweep that only
 * knows the words the author already avoided catches nothing.
 */
const PRIVILEGED_WORDS = Object.freeze([
  "allow", "commit", "prompt", "suppress", "release", "read", "covered",
  "drafted", "proposed", "queued", "healthy", "passing", "ok", "pass",
  "satisfied", "complete", "admitted", "resumed", "attended", "verified",
  "present", "equivalent", "operational", "active", "green", "joins_exactly",
  "coverage_complete", "favorable",
]);

/** The slice's own privileged names, beyond the union's words. */
const PRIVILEGED_TRUE_KEYS = new Set([
  "allowed", "is_gate_zero_pass", "claim_matches_derivation", "producer_bound",
  // `passable` is already caught as a substring of the union's "pass"; it is
  // named here anyway because it is THE claim on this surface and the sweep's
  // own test asserts it is never exempted.
  "passable",
]);

/**
 * A key whose `true` would be a consumer-actionable claim: a union word exactly,
 * a union word anywhere inside the name, this slice's own list, a `would_`
 * classifier field, or an `_if_authoritative` conditional leaking outward.
 */
function privilegedKey(key) {
  const lower = key.toLowerCase();
  if (PRIVILEGED_TRUE_KEYS.has(key)) return true;
  if (lower.startsWith("would_") || lower.includes("_if_authoritative")) return true;
  return PRIVILEGED_WORDS.some(word => lower === word || lower.includes(word));
}

test("SWEEP: the privileged-word set is the standing rule's closed union", () => {
  // The union is a copied constant, so the copy is checked rather than trusted:
  // every word the rule names is swept, and the two names this correction
  // renamed are caught under both their exact and their embedded spellings.
  assert.equal(PRIVILEGED_WORDS.length, 28);
  for (const word of ["present", "read", "pass", "verified", "active", "complete"])
    assert.ok(PRIVILEGED_WORDS.includes(word), `${word} is missing from the union`);
  for (const key of ["present", "r7_entry_present", "gate_zero_present", "would_allow",
    "is_read", "coverage_complete", "was_verified_if_authoritative"])
    assert.equal(privilegedKey(key), true, `${key} must be swept`);
  // THE SEAM-STATE EXEMPTION IS THREE NAMES WIDE. Each is still privileged to
  // `privilegedKey` — the exemption lives in the finding walker, not in the
  // union — and `producer_bound`, the binding boolean that WOULD be a claim, is
  // outside it and stays swept through both.
  for (const key of SEAM_STATE_KEYS) assert.equal(privilegedKey(key), true, key);
  assert.equal(SEAM_STATE_KEYS.size, 4);
  assert.equal(SEAM_STATE_KEYS.has("producer_bound"), true);
  assert.deepEqual(privilegedFindings({ producer_bound: true }), [],
    "the fourth seam-state boolean is exempt now that a module stands behind it");
  assert.deepEqual(privilegedFindings({ predecessor_outcome_reader_bound: true }), []);
  // AND THE EXEMPTION STOPS THERE. `passable` is the field whose `true` IS the
  // authority claim, and it is swept — so a producer that signed without rows
  // would still be caught by this sweep rather than by nobody.
  assert.deepEqual(privilegedFindings({ passable: true }), ["$.passable === true"],
    "the exemption widened past the one boolean that is a signature");
  // And the names this slice now uses are NOT swept, so the sweep is a filter
  // rather than a blanket that would fire on anything.
  for (const key of ["r7_entry_witness", "witness_conjunction", "digest_matches",
    "entry_matches", "gate_registered", "role_registered", "parsed", "retryable",
    "is_superseded_packet", "failed_run_retained"])
    assert.equal(privilegedKey(key), false, `${key} must not be swept`);
});
const PRIVILEGED_VALUES = new Set([
  "allow", "allowed", "green", "pass", "passed", "passable", "operational", "active",
]);

/**
 * THE FOUR SEAM-STATE BOOLEANS, NAMED ONE BY ONE AND FOR ONE REASON.
 *
 * Each says whether a SEAM has something behind it — the same fact `seams_bound`
 * carries as a list — and none of them says any evidence was read, admitted or
 * acted on. The substring sweep catches the first three because "reader" carries
 * the union's "read", which is the word that exists to catch `read: true` and
 * `is_read: true`.
 *
 * `producer_bound` IS THE FOURTH AS OF THIS SLICE, and widening the exemption by
 * one name is a deliberate, reviewed change rather than a convenience. The
 * earlier comment here said it was excluded because "its `true` would be an
 * authority claim" — and that was right while nothing implemented the producer,
 * because the only way the field could have read true was somebody asserting it.
 * It is now DERIVED from a module that exists: `boundSeam` finds an
 * `emitOutcome` behind card 9's staffed seat, or it does not. A seam-state
 * boolean about a seam that is built is the same kind of fact as the other
 * three, and the claim that would matter — `passable` — is NOT here and is
 * still swept, which is what the assertions below hold.
 */
const SEAM_STATE_KEYS = new Set([
  "predecessor_outcome_reader_bound",
  "scheduler_reader_bound",
  "gate_conclusion_reader_bound",
  "producer_bound",
]);

/** Every string, key and boolean in a returned value, walked to the leaves. */
function privilegedFindings(value, path = "$", found = []) {
  if (Array.isArray(value)) {
    value.forEach((entry, index) => privilegedFindings(entry, `${path}[${index}]`, found));
    return found;
  }
  if (value !== null && typeof value === "object") {
    for (const [key, entry] of Object.entries(value)) {
      const at = `${path}.${key}`;
      if (entry === true && privilegedKey(key) && !SEAM_STATE_KEYS.has(key))
        found.push(`${at} === true`);
      if (key.startsWith("would_")) found.push(`${at} is a classifier field on the public surface`);
      privilegedFindings(entry, at, found);
    }
    return found;
  }
  if (typeof value === "string" && PRIVILEGED_VALUES.has(value)) found.push(`${path} === ${value}`);
  return found;
}

/**
 * Every caller-controlled shape this surface could ever be handed, including
 * the reviewer's. If none of them changes the answer, none of them is authority.
 */
function callerControlledShapes() {
  const shapes = [];
  for (const state of V5_A02_OBSERVATION_STATES) {
    for (const acceptance of V5_A02_ACCEPTANCE_STATES) {
      for (const criteria of V5_A02_CRITERIA_STATES) {
        const request = cleanJoin();
        const target = request.predecessor_observations[1];
        target.state = state;
        if (state === "observed") {
          target.outcome.acceptance_state = acceptance;
          target.outcome.criteria_state = criteria;
          target.outcome_digest = digest(target.outcome);
        } else {
          target.outcome = null;
          target.outcome_digest = null;
          target.observed_at = null;
        }
        shapes.push(request);
      }
    }
  }
  for (const conclusion of V5_A02_GATE_CONCLUSIONS) {
    const request = cleanJoin();
    request.gates[0].conclusion = conclusion;
    shapes.push(request);
  }
  for (const readbackState of V5_A02_OBSERVATION_STATES) {
    const request = cleanJoin();
    request.scheduler_readback.state = readbackState;
    if (readbackState !== "observed") {
      request.scheduler_readback.observed_at = null;
      request.scheduler_readback.observed_digest = null;
    }
    shapes.push(request);
  }
  // THE REVIEWER'S CONSTRUCTION, verbatim in spirit: one gate, conclusion
  // success, no dependencies. Against the old module this returned
  // `green: true` and `decision: "allow"`.
  shapes.push({ gates: [{ gate_id: "one-gate", conclusion: "success", depends_on: [] }] });
  shapes.push([{ gate_id: "one-gate", conclusion: "success", depends_on: [] }]);
  // And the shapes that try to say the answer outright.
  shapes.push({ ...cleanJoin(), verified: true, decision: "allow", green: true, passable: true });
  shapes.push({ joins_exactly: true, ok: true });
  shapes.push({}, null, undefined, "allow", 1, true, []);
  return shapes;
}

const PUBLIC_FUNCTIONS_OVER_CALLER_INPUT = [
  ["emitGateZeroOutcome", emitGateZeroOutcome],
  ["readGateZeroPredecessorJoin", readGateZeroPredecessorJoin],
  ["readGateGraphAssurance", readGateGraphAssurance],
];

test("SURFACE: the public export list is exactly the unavailable surface", () => {
  assert.deepEqual(Object.keys(surface).sort(), EXPECTED_PUBLIC_EXPORTS);
  for (const name of Object.keys(surface)) {
    assert.ok(!/^(classify|evaluate|derive)/.test(name),
      `${name} is a classifier name on the public surface`);
    assert.ok(!name.includes("would_"), `${name} is a classifier field on the public surface`);
  }
});

/**
 * Exactly what the producer registration module may export. The authority-
 * bearing record is ONE frozen constant over four hard-bound predecessors; the
 * rest are the pure constants it is assembled from. There is no builder, so
 * there is no argument, so there is no caller-supplied predecessor set — which
 * was the PR 990 defect: an exported builder handed back an authority-stamped
 * provisional registration over whatever references the caller passed in.
 */
const EXPECTED_PRODUCER_EXPORTS = [
  "GATE_ZERO_STEP_REF",
  "RESOLVED_FROM_R7",
  "UNRESOLVED_WITHOUT_R7",
  "V5_A02_GATE_ZERO_COMBINER",
  "V5_A02_GATE_ZERO_GATE_ID",
  "V5_A02_GATE_ZERO_ORACLE_REF",
  "V5_A02_GATE_ZERO_ORACLE_SEAT_CHARTER_REF",
  "V5_A02_GATE_ZERO_ORACLE_SEAT_DECISION_REF",
  "V5_A02_GATE_ZERO_ORACLE_VERSION",
  "V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS",
  "V5_A02_GATE_ZERO_PRODUCER_DECISION_REF",
  "V5_A02_GATE_ZERO_PRODUCER_REGISTRATION",
  "V5_A02_GATE_ZERO_PRODUCER_REGISTRATION_STATUS",
  "V5_A02_GATE_ZERO_PRODUCER_ROLE",
  "V5_A02_GATE_ZERO_R7_AMENDMENT_DECISION_REF",
  "V5_A02_GATE_ZERO_R7_ENTRY_WITNESS",
  "V5_A02_GATE_ZERO_R7_ENTRY_WITNESS_DECIDED_BY",
  "V5_A02_GATE_ZERO_R7_PACKET_SHA256",
  "V5_A02_GATE_ZERO_R7_SUPERSEDED_PACKET_SHA256",
  "V5_A02_GATE_ZERO_RECEIPT_REF",
  "V5_A02_GATE_ZERO_RETRY_POLICY",
  "V5_A02_PRODUCER_REGISTRATION_SCHEMA_VERSION",
  "V5_A02_SCHEDULER_STEP_REF",
  "v5A02GateZeroR7Presence",
];

test("PRODUCER: the registration module exports the frozen record and one bytes-only reader", () => {
  assert.deepEqual(Object.keys(producerModule).sort(), EXPECTED_PRODUCER_EXPORTS);
  // EXACTLY ONE export is callable, and it is the byte verifier. The PR 990
  // defect was an exported BUILDER — the only shape that can take a predecessor
  // reference and hand back an authority-stamped record over it. The verifier is
  // the opposite shape: its one argument is bytes, and every other argument
  // throws rather than being interpreted. Proved by value, not by name.
  const callable = Object.entries(producerModule)
    .filter(([, value]) => typeof value === "function").map(([name]) => name);
  assert.deepEqual(callable, ["v5A02GateZeroR7Presence"],
    "an exported builder can be handed caller-supplied predecessor references");
  const reader = producerModule.v5A02GateZeroR7Presence;
  assert.equal(reader.length, 1, "the reader takes bytes and nothing else");
  // An arrow, per amendment 2 of the standing rule: no .prototype, not
  // constructable, and Symbol.hasInstance answers false without touching its
  // left operand.
  assert.equal(Object.hasOwn(reader, "prototype"), false);
  assert.throws(() => Reflect.construct(reader, [""]), TypeError);
  assert.equal({} instanceof reader, false);
  assert.equal(reader[Symbol.hasInstance]({ r7_entry_witness: true }), false);
  // Every shape that is not bytes throws, including the ones that try to name
  // the answer or to hand in a reference set.
  for (const shape of [undefined, null, true, false, 0, 1, {}, [],
    { r7_entry_present: true }, { witness_conjunction: true },
    { registry_entry: {}, predecessor_step_refs: ["step:anything"] },
    ["step:scheduler-active-receipt"], () => true, Symbol("packet"),
    new Proxy({}, { get: () => true })])
    assert.throws(() => reader(shape), V5BoundaryError,
      `${String(typeof shape)} was interpreted instead of refused`);
  // And proved again in the source, so a SECOND callable is red on sight rather
  // than red only once someone adds it to the list above.
  const source = readFileSync(
    fileURLToPath(new URL("../src/gate-zero-producer-registration.v5.js", import.meta.url)), "utf8");
  for (const shape of [/\bexport\s+function\b/, /\bexport\s+default\b/])
    assert.equal(shape.test(source), false,
      `the registration module exports a callable: ${shape}`);
  const arrowExports = [...source.matchAll(
    /\bexport\s+(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?(?:function\b|\()/g)]
    .map(match => match[1]);
  assert.deepEqual(arrowExports, ["v5A02GateZeroR7Presence"],
    "only the byte verifier may be an exported callable");
});

test("PRODUCER: the four canonical predecessors are hard-bound into the frozen registration", () => {
  const registration = producerModule.V5_A02_GATE_ZERO_PRODUCER_REGISTRATION;
  assert.deepEqual([...registration.registry_entry.depends_on_step_refs],
    [...producerModule.V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS]);
  assert.deepEqual([...producerModule.V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS], [
    "step:scheduler-active-receipt",
    "step:wr40-repository-outcome",
    "step:wr46-dissolution-outcome",
    "step:wr54-backup-recovery-outcome",
  ]);
  // Frozen all the way down: no caller can edit the record in place either.
  assert.ok(Object.isFrozen(registration));
  assert.ok(Object.isFrozen(registration.registry_entry));
  assert.ok(Object.isFrozen(registration.registry_entry.depends_on_step_refs));
  assert.ok(Object.isFrozen(producerModule.V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS));
  assert.throws(() => { registration.registry_entry.depends_on_step_refs.push("step:mine"); },
    TypeError);
});

test("PRODUCER: the checker's binding constant IS the frozen registration", async () => {
  // Same object, not an equal copy — the checker cannot be reading a second
  // registration built over some other predecessor set.
  assert.equal(surface.V5_A02_GATE_ZERO_PRODUCER_REGISTRATION,
    producerModule.V5_A02_GATE_ZERO_PRODUCER_REGISTRATION);
  assert.equal(surface.V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS,
    producerModule.V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS);
  assert.equal(surface.V5_A02_SCHEDULER_STEP_REF, producerModule.V5_A02_SCHEDULER_STEP_REF);
  // And what the checker reports comes from that one record.
  const result = await emitGateZeroOutcome(cleanJoin());
  assert.equal(result.producer_role,
    producerModule.V5_A02_GATE_ZERO_PRODUCER_REGISTRATION.registry_entry.producer_role);
  assert.equal(result.producer_registration, surface.V5_A02_GATE_ZERO_PRODUCER_REGISTRATION);
});

test("SURFACE: no caller-controlled shape produces a privileged outcome", async () => {
  const shapes = callerControlledShapes();
  assert.ok(shapes.length >= 40, "the sweep must cover the caller-controlled domain");
  for (const [name, fn] of PUBLIC_FUNCTIONS_OVER_CALLER_INPUT) {
    for (const shape of shapes) {
      const result = await fn(shape);
      assert.equal(result.status, "unavailable", name);
      assert.equal(result.decision, "refuse", name);
      assert.equal(result.request_read, false, name);
      assert.equal(result.caller_evidence_admitted, false, name);
      assert.ok(V5_A02_GATE_ZERO_REASON_IDS.includes(result.reason_id), name);
      assert.deepEqual(privilegedFindings(result), [],
        `${name} leaked a privileged outcome for ${String(JSON.stringify(shape)).slice(0, 80)}`);
      assert.ok(Object.isFrozen(result), name);
      assert.deepEqual(result.effects, V5_NO_EFFECTS, name);
    }
  }
});

test("SURFACE: the answer is byte-identical across every caller shape", async () => {
  for (const [name, fn] of PUBLIC_FUNCTIONS_OVER_CALLER_INPUT) {
    const first = digest(await fn(cleanJoin()));
    for (const shape of callerControlledShapes())
      assert.equal(digest(await fn(shape)), first, `${name} answered differently for a caller shape`);
    assert.equal(digest(await fn()), first, `${name} answered differently for no argument at all`);
  }
});

test("SURFACE: the Gate Zero outcome is not passable and carries no join", async () => {
  for (const shape of [cleanJoin(), undefined, { gates: [] }]) {
    const result = await emitGateZeroOutcome(shape);
    assert.equal(result.passable, false);
    // THE REFUSAL MOVED ONE STEP FURTHER ALONG, which is this slice. The
    // producer seam is BOUND — a module implements it — and the refusal is the
    // producer's own. In a test there is no authenticated call, so what it
    // refuses on is the identity it cannot derive: a receipt signed by nobody is
    // the defect the PR 1013 review named, and this is the surface half of the
    // control that proves it cannot happen.
    assert.equal(result.reason_id, "gate_zero_producer_identity_refused");
    assert.equal(result.producer_bound, true);
    assert.equal(result.producer_answer.run_binding_status, "derived");
    assert.equal(result.producer_seam, V5_A02_GATE_ZERO_PRODUCER_SEAM);
    assert.equal(result.gate_zero_step_ref, GATE_ZERO_STEP_REF);
    // The defect the reviewer named: a successful join inside a refusal.
    assert.equal(result.join, null, "no join may ride inside the refusal");
    assert.equal(result.predecessor_evidence_read, null);
    assert.equal(Object.hasOwn(result, "joins_exactly"), false);
    // NOTHING IS OWED ANY MORE. All four seams have something behind them; what
    // is missing is rows, and a missing row is not an owed seam. `seams_bound`
    // carries the whole list so the empty filter hides nothing.
    assert.deepEqual(result.owed_seams, []);
    assert.deepEqual(result.seams_bound.map(entry => entry.bound), [true, true, true, true]);
  }
});

test("SURFACE: the contract is REGISTERED with the oracle seat STAFFED, and neither is a signature", async () => {
  const result = await emitGateZeroOutcome(cleanJoin());
  // The five the 2026-09-11 ruling settled are reported, and each one matches
  // the registration rather than a literal typed twice.
  const entry = V5_A02_GATE_ZERO_PRODUCER_REGISTRATION.registry_entry;
  assert.equal(result.producer_role, entry.producer_role);
  assert.equal(result.oracle_ref, entry.oracle_ref);
  assert.equal(result.output_schema_ref, entry.output_schema_ref);
  assert.equal(result.evidence_scope, entry.evidence_scope);
  assert.equal(result.produced_gate_id, entry.produces_gate_ids[0]);
  assert.equal(result.producer_registration_status, "registered");
  assert.equal(result.producer_registration_decision_ref,
    "20c83902-f150-4d59-beca-915c5c871f95");
  // Whether r7 carries the entry is UNDETERMINED here — the packet's bytes are
  // not in this repository — and the seat is STAFFED. Those are separate facts,
  // and the refusal below turns on NEITHER of them: it turns on the producer seam
  // being unbuilt, which is why `passable` is asserted false at the end of this
  // test with both of these settled.
  assert.equal(result.r7_entry_witness, null);
  assert.equal(Object.hasOwn(result, "r7_entry_present"), false,
    "the privileged spelling must not come back under any value");
  assert.equal(result.r7_entry_witness_decided_by,
    "v5A02GateZeroR7Presence(<r7 design packet bytes>).witness_conjunction");
  assert.equal(result.producer_registration.oracle_seat_bound, true);
  // No run has happened, so no outcome exists to report.
  for (const field of ["outcome_digest", "observed_at"])
    assert.equal(result[field], null, `${field} must be null, not invented`);
  // Every field of the entry is now a value read from r7; a null would mean the
  // amendment left a hole, and tools/doctorcre-v5-review.cjs refuses such a row.
  for (const [field, value] of Object.entries(entry))
    assert.notEqual(value, null, `${field} must carry r7's answer, not a hole`);
  assert.deepEqual(V5_A02_GATE_ZERO_PRODUCER_REGISTRATION.unresolved_without_r7, []);
  assert.deepEqual(
    V5_A02_GATE_ZERO_PRODUCER_REGISTRATION.resolved_from_r7.map(item => item.field).sort(),
    ["causal_phase", "consumes_gate_ids", "produces_gate_ids[0]", "target_dag"]);
  // THE THREE THAT LEFT THIS LIST ON 2026-09-11, and they left because Joe
  // answered them, not because anybody trimmed the list: cards 11, 12 and 13
  // each name a store, and the readers those rulings switched on are bound.
  for (const answered of [
    "which store an accepted predecessor outcome is read from",
    "which scheduler surface a canary and its readback are read from",
    "which surface a gate's own conclusion is read from",
  ])
    assert.equal(result.undecided_governance_questions.includes(answered), false,
      `${answered} is ruled and must not still be listed as undecided`);
  // AND CARD 9 LEFT IT ON 2026-09-13, when the seat was staffed. Card 10 left
  // on 2026-09-12 with the loop-589 amendment. With all five cards answered and
  // all three rulings live, this list is EMPTY — and the gate still refuses,
  // which is the point: an answered governance question is not a signature.
  assert.deepEqual([...result.undecided_governance_questions], []);
  assert.ok(!result.undecided_governance_questions.some(
    question => question.includes("whether r7 itself carries the registration")),
  "r7 carries it; the question must not still be listed as open");
  // CARD 9 NAMED A CHARTER AND THEN A DESK. The charter ruling says WHICH KIND
  // of seat may hold the oracle; the staffing ruling says WHO, and the holder is
  // reported beside both refs so a reader can check the boolean against its
  // authority rather than trusting it. CARD 10's amendment landed, and the
  // surface reports it the only honest way: the ruling's decision ref, and a
  // witness that is NULL because deciding it needs the packet's bytes.
  // `r7_entry_present` — the flag this surface used to carry — must be gone
  // entirely, not merely false.
  assert.equal(result.oracle_seat_charter_ref, "charter:reviewer");
  assert.equal(result.oracle_seat_charter_decision_ref,
    "8a1dad08-8707-4bb0-a159-c2831a00cea2");
  assert.equal(result.oracle_seat_bound, true);
  assert.equal(result.oracle_seat_holder_ref, "seat:codex-reviewer:gpt-5.6-sol");
  assert.equal(result.oracle_seat_staffing_decision_ref,
    "359784f1-5d9e-4e11-bcce-af8b0dfcc5e0");
  assert.equal(result.producer_registration.oracle_seat_owed, null,
    "a seat that is held may not still be reported as owed");
  // AND THE SEAT MOVED NOTHING IT MAY NOT. It binds the producer seam — that is
  // this slice — and it still does not sign: `passable` is false over a run
  // binding that names no rows, which is the confusion card 9 could introduce.
  assert.equal(result.passable, false);
  assert.equal(result.producer_bound, true);
  assert.equal(result.r7_amendment_decision_ref,
    "311a9af5-3685-4c47-a158-f8dd70870ca1");
  assert.equal(result.r7_entry_witness, null);
  assert.equal(Object.hasOwn(result, "r7_entry_present"), false);
  assert.equal(Object.hasOwn(result.producer_registration, "r7_entry_amendment_applied"), false,
    "the amendment landed; a flag saying it did not must not survive the merge");
});

/**
 * THE R7 ENTRY IS PROVEN BY BYTES, NOT BY A FLAG AND NOT BY A PIN COMPARED TO
 * ANOTHER PIN. The registration's `r7_entry_witness` is routed through
 * `v5A02GateZeroR7Presence`, whose only argument is the packet's bytes; these
 * cases are that derivation's falsifiers. They are exercised against a packet
 * built to carry the entry but not hashing to the pin, against packets missing
 * the gate and the role, against a drifted row, and — in the case below that
 * takes the real reconstructed packets — against the amended bytes and the
 * superseded bytes themselves.
 */
function r7PacketLike(overrides = {}) {
  const entry = V5_A02_GATE_ZERO_PRODUCER_REGISTRATION.registry_entry;
  return JSON.stringify({
    receipt_producer_step_registry: [{ ...entry }],
    consumer_gate_registry: [{
      gate_id: V5_A02_GATE_ZERO_GATE_ID,
      receipt_producer_step_refs: [GATE_ZERO_STEP_REF],
    }],
    producer_role_registry: [V5_A02_GATE_ZERO_PRODUCER_ROLE],
    ...overrides,
  });
}

test("PRODUCER: r7 presence is read from the packet, and no argument short of it answers true", () => {
  assert.throws(() => v5A02GateZeroR7Presence(true), V5BoundaryError);
  assert.throws(() => v5A02GateZeroR7Presence({ r7_entry_present: true }), V5BoundaryError);
  assert.throws(() => v5A02GateZeroR7Presence(), V5BoundaryError);
  // THE DERIVATION IS NOT A COMPARISON OF TWO PINS. Without bytes the answer is
  // null, not true: any revision that goes back to deciding this from the
  // pinned constants alone turns THIS assertion red.
  assert.equal(V5_A02_GATE_ZERO_PRODUCER_REGISTRATION.r7_entry_witness, null);
  assert.equal(producerModule.V5_A02_GATE_ZERO_R7_ENTRY_WITNESS, null);
  assert.equal(Object.hasOwn(V5_A02_GATE_ZERO_PRODUCER_REGISTRATION, "r7_entry_present"), false);
  // A packet whose CONTENT is right but whose bytes are not the pinned packet:
  // every content finding holds and the conjunction is still false. This is the
  // case that goes red if the digest term is dropped from the conjunction.
  const lookalike = v5A02GateZeroR7Presence(r7PacketLike());
  assert.equal(lookalike.entry_matches, true);
  assert.equal(lookalike.gate_registered, true);
  assert.equal(lookalike.role_registered, true);
  assert.equal(lookalike.digest_matches, false);
  assert.equal(lookalike.witness_conjunction, false,
    "content without the pinned digest is not the packet");
  assert.equal(Object.hasOwn(lookalike, "present"), false,
    "the privileged spelling must not come back under any value");
  // Each content finding fails on its own mutation.
  const noRole = v5A02GateZeroR7Presence(r7PacketLike({ producer_role_registry: [] }));
  assert.equal(noRole.role_registered, false);
  const noGate = v5A02GateZeroR7Presence(r7PacketLike({ consumer_gate_registry: [] }));
  assert.equal(noGate.gate_registered, false);
  const drifted = JSON.parse(r7PacketLike());
  drifted.receipt_producer_step_registry[0].causal_phase = "production_outcome";
  assert.equal(v5A02GateZeroR7Presence(JSON.stringify(drifted)).entry_matches, false,
    "a row that differs by one closed-registry value is not this registration");
  // Unparseable bytes deny rather than throw.
  assert.equal(v5A02GateZeroR7Presence("not json").witness_conjunction, false);
  // NO caller-controlled byte shape reaches a true conjunction, and none of them
  // brings a privileged word back either.
  const byteShapes = [r7PacketLike(), r7PacketLike({ producer_role_registry: [] }),
    r7PacketLike({ consumer_gate_registry: [] }), JSON.stringify(drifted), "not json", "",
    JSON.stringify({}), JSON.stringify({ present: true, r7_entry_present: true }),
    JSON.stringify({ receipt_producer_step_registry: "everything" }),
    Buffer.from(r7PacketLike()), Buffer.alloc(0)];
  for (const bytes of byteShapes) {
    const finding = v5A02GateZeroR7Presence(bytes);
    assert.equal(finding.witness_conjunction, false,
      `a caller-supplied byte shape reached a true conjunction: ${String(bytes).slice(0, 60)}`);
    assert.deepEqual(privilegedFindings(finding), [],
      `the r7 finding leaked a privileged outcome for ${String(bytes).slice(0, 60)}`);
    assert.ok(Object.isFrozen(finding));
  }
  // The two pins are different packets, and the superseded one is named.
  assert.notEqual(V5_A02_GATE_ZERO_R7_PACKET_SHA256,
    V5_A02_GATE_ZERO_R7_SUPERSEDED_PACKET_SHA256);
  for (const sha of [V5_A02_GATE_ZERO_R7_PACKET_SHA256,
    V5_A02_GATE_ZERO_R7_SUPERSEDED_PACKET_SHA256])
    assert.match(sha, /^[0-9a-f]{64}$/);
});

/**
 * THE SAME CHECK AGAINST THE TWO REAL PACKETS, WHICH IS THE ONLY PLACE A TRUE
 * CONJUNCTION CAN COME FROM.
 *
 * r7 is not a file in this repository — it is 62 base64 chunk sections in the
 * doctrine store, 740KB reassembled, and the amended and superseded packets
 * together are 1.4MB — so it is supplied by path, exactly as
 * tools/doctorcre-v5-review.cjs is handed the design and constitution by path
 * rather than carrying them. Point CARR_R7_DESIGN_PACKET at the amended bytes
 * and CARR_R7_DESIGN_PACKET_SUPERSEDED at the pre-amendment bytes and both run.
 * They are skipped, never faked: a fixture standing in for the packet would
 * prove nothing about the pin, which is the entire point of a pin.
 *
 * Run on this branch 2026-09-13 against the packet read back out of the doctrine
 * store after the consumer-gate-receipt digest amendment:
 *
 *   amended    sha256 4379c60e…  digest_matches true   witness_conjunction true
 *
 * THE SUPERSEDED LEG WAS NOT SUPPLIED ON THAT RUN, and it is worth saying why
 * rather than leaving a reader to assume it passed. `ef34aa54…` is the packet
 * from BEFORE the loop-589 amendment, and the store no longer serves those
 * bytes: the 62 chunk sections were overwritten in place by that amendment, so
 * the only way to hand this test `ef34aa54…` is to hold a copy of the file. A
 * caller who has one still runs the leg; a caller who does not gets the skip
 * this test was built to give rather than a reconstruction standing in for the
 * packet. The amended leg is the one that proves the pin, and it ran.
 *
 * The control that WAS available ran out of band on 2026-09-13: `ea40f61a…`, the
 * packet carrying the Gate Zero entry but predating the digest rule, answered
 * digest_matches false with entry_matches, gate_registered and role_registered
 * all true — right entry, wrong bytes, conjunction false.
 */
test("PRODUCER: the amended r7 packet witnesses the registration and the superseded one does not", (t) => {
  const amendedPath = process.env.CARR_R7_DESIGN_PACKET;
  const supersededPath = process.env.CARR_R7_DESIGN_PACKET_SUPERSEDED;
  if (!amendedPath && !supersededPath)
    return t.skip("set CARR_R7_DESIGN_PACKET and CARR_R7_DESIGN_PACKET_SUPERSEDED to the reconstructed r7 packets");

  if (amendedPath) {
    const finding = v5A02GateZeroR7Presence(readFileSync(amendedPath));
    assert.equal(finding.observed_sha256, V5_A02_GATE_ZERO_R7_PACKET_SHA256,
      `CARR_R7_DESIGN_PACKET is not the pinned packet: ${finding.observed_sha256}`);
    assert.equal(finding.digest_matches, true);
    assert.equal(finding.is_superseded_packet, false);
    assert.equal(finding.entry_matches, true);
    assert.equal(finding.gate_registered, true);
    assert.equal(finding.role_registered, true);
    assert.equal(finding.witness_conjunction, true);
  }

  if (supersededPath) {
    // The packet as it stood BEFORE the amendment. Every finding must fail:
    // these bytes hash to the superseded pin, and the packet has no Gate Zero
    // producer row, gate or role in it at all.
    const finding = v5A02GateZeroR7Presence(readFileSync(supersededPath));
    assert.equal(finding.observed_sha256, V5_A02_GATE_ZERO_R7_SUPERSEDED_PACKET_SHA256,
      `CARR_R7_DESIGN_PACKET_SUPERSEDED is not the superseded packet: ${finding.observed_sha256}`);
    assert.equal(finding.digest_matches, false);
    assert.equal(finding.is_superseded_packet, true);
    assert.equal(finding.parsed, true, "the superseded packet must parse, so the false is about content");
    assert.equal(finding.entry_matches, false);
    assert.equal(finding.gate_registered, false);
    assert.equal(finding.role_registered, false);
    assert.equal(finding.witness_conjunction, false);
  }
});

test("SURFACE: a BOUND producer does not make the gate passable either", async () => {
  // The exact confusion this slice could introduce: a producer exists, so a
  // reader might take the gate for built-and-therefore-passing. It is not. A
  // built seam is a place a signature can come from; the signature still has to
  // be earned over rows, and there are no rows named.
  const result = await emitGateZeroOutcome(cleanJoin());
  assert.equal(result.passable, false);
  assert.equal(result.producer_bound, true);
  assert.equal(result.status, "unavailable");
  assert.equal(result.decision, "refuse");
  assert.equal(result.join, null);
  assert.deepEqual(result.owed_seams, []);
  // The two frozen `false`s left the preimage rather than being reworded: a
  // sealed policy identity may not carry a per-run verdict, and it may not carry
  // a claim the surface can now disprove.
  assert.equal(Object.hasOwn(v5A02GateZeroPolicyPreimage(), "gate_zero_passable"), false);
  assert.equal(Object.hasOwn(v5A02GateZeroPolicyPreimage(), "public_surface_answers"), false);
  // And the two that stayed are DERIVED, so both move when their seam does.
  assert.equal(v5A02GateZeroPolicyPreimage().authoritative_readers_bound, true);
  assert.equal(v5A02GateZeroPolicyPreimage().producer_bound, true);
});

/**
 * THE WIRING, AND THE ONE THING IT DID NOT DO.
 *
 * Joe ruled cards 11, 12 and 13 on 2026-09-11 and PR 1001 built their readers,
 * so three of the four seams have an authoritative surface behind them and this
 * module imports it. What that buys is a READING, and what Gate Zero is missing
 * is a SIGNATURE: which accepted outcome, which canary row, which commit and
 * which declared check a run stands on are the producer's bindings, and cards 9
 * and 10 left that seam unbuilt on purpose. So every answer still refuses — one
 * step further along, on the reason that is now the true one.
 */
test("SURFACE: all four seams are bound, and the two synchronous reads still refuse", async () => {
  const join = readGateZeroPredecessorJoin();
  assert.equal(join.predecessor_outcome_reader_bound, true);
  assert.equal(join.scheduler_reader_bound, true);
  // THE REASON MOVED, AND IT HAD TO. "the producer seam is unavailable" stopped
  // being true the moment something stood behind it. What is true instead is
  // narrower and is about THIS function: a join is what the emission produces
  // from rows, and a synchronous query of this surface does not read rows.
  assert.equal(join.reason_id, "gate_zero_join_is_produced_not_queried");
  assert.equal(join.decided_by, "evidence_seams_bound_producer_seam_built");
  assert.deepEqual(join.owed_seams, []);

  const graph = readGateGraphAssurance();
  assert.equal(graph.gate_conclusion_reader_bound, true);
  assert.equal(graph.reason_id, "gate_zero_join_is_produced_not_queried");
  assert.deepEqual(graph.owed_seams, []);

  // Still a refusal, on all three, whatever is bound.
  for (const result of [join, graph, await emitGateZeroOutcome(cleanJoin())]) {
    assert.equal(result.status, "unavailable");
    assert.equal(result.decision, "refuse");
    assert.equal(result.request_read, false);
    assert.equal(result.caller_evidence_admitted, false);
    assert.equal(result.passable ?? false, false, "a read refused and claimed a signature");
  }

  // And every seam reports bound in the whole list, not only in the named
  // booleans above — which is the state this slice put the surface in.
  const bound = new Map((await emitGateZeroOutcome(cleanJoin())).seams_bound
    .map(entry => [entry.seam, entry.bound]));
  assert.deepEqual([...bound.entries()].sort(), [
    [V5_A02_GATE_CONCLUSION_READER_SEAM, true],
    [V5_A02_GATE_ZERO_PRODUCER_SEAM, true],
    [V5_A02_PREDECESSOR_OUTCOME_READER_SEAM, true],
    [V5_A02_SCHEDULER_READER_SEAM, true],
  ].sort());
});

/**
 * THE SWITCH TURNS BOTH WAYS, AND THIS IS THE PROOF.
 *
 * The binding condition is `seamRulingRef(card)` — the same ruling table the
 * readers ask — so putting `null` back on a card's `decision_id:` line unbinds
 * the seam HERE as well as there. The falsifiable form of "nothing else moved"
 * is a digest: with all three lines null, the three public answers must be the
 * exact bytes main published before any of this landed.
 *
 * THE DIGESTS BELOW ARE PINNED, not recomputed from src, and that is the whole
 * point. They were taken from `origin/main` at 64b22a4b — "Admit the
 * repository's real check names in the Gate Zero conclusion reader (#1003)" —
 * by calling the three exports of the unmodified module and digesting each
 * answer. A recomputation from src would confirm itself; a literal cannot.
 *
 * HOW THE UNRULED TREE IS REACHED, and why it is not a door. src is copied to a
 * temp directory under node_modules/.cache, the three ruling lines in the COPY
 * are set back to null, and the COPY'S OWN gate module is imported. Nothing in
 * src is edited, no argument selects it, no environment variable points at it —
 * it is the same staging gate-zero-seam-readers.v5.test.mjs uses, for the same
 * reason.
 */
/**
 * WHAT THE PIN COVERS, AND THE ONE PLACE IT CANNOT.
 *
 * The two reader answers are pinned WHOLE: nothing about them may move in an
 * unruled tree. `emitGateZeroOutcome` carries fields this change added on
 * purpose — cards 9 and 10, which are a charter and a sealed packet and have
 * nothing to do with the rulings switch, and the three per-card booleans the
 * 2026-09-12 review asked for, which report each ruled reader's state
 * individually — so it is pinned with those removed. Both halves are asserted:
 * the additions are exactly these eleven names, and everything that is not one
 * of them is main's bytes.
 */
const CARD_9_ANSWER_FIELDS = Object.freeze([
  "oracle_seat_bound",
  "oracle_seat_charter_ref",
  "oracle_seat_charter_decision_ref",
  // The staffing half, added 2026-09-13. Both are NULL in every staged tree the
  // pin is taken against, because those trees stage the seat unstaffed the way
  // main shipped it — so they are stripped for being fields main does not have,
  // not for carrying a value the comparison would otherwise catch.
  "oracle_seat_holder_ref",
  "oracle_seat_staffing_decision_ref",
  // Card 10's ref, which main carries on the registration and this branch also
  // reports at the top of the answer.
  "r7_amendment_decision_ref",
]);
const CARD_9_REGISTRATION_FIELDS = Object.freeze([
  "oracle_seat_charter_ref",
  "oracle_seat_charter_decision_ref",
  "oracle_seat_holder_ref",
  "oracle_seat_staffing_decision_ref",
]);

/**
 * The three booleans that say which ruled reader is bound, one card at a time.
 * They are this branch's addition to the emitted answer — the two reader answers
 * already carried the same names on main — so the pin below is taken without
 * them and their own values are asserted per permutation further down.
 */
const PER_CARD_ANSWER_FIELDS = Object.freeze([
  "predecessor_outcome_reader_bound",
  "scheduler_reader_bound",
  "gate_conclusion_reader_bound",
]);

/**
 * THE POLICY VERSION, PUT BACK TO THE PRE-PR COMMIT'S, AND WHY THAT IS ONE
 * NORMALIZATION RATHER THAN A HOLE IN THE PIN.
 *
 * `V5_A02_POLICY_VERSION` moved 2 -> 3 in this slice, on purpose and with a
 * reason written beside it: version 2's whole claim was that the surface answers
 * `unavailable` for every caller on every input, and the surface can now answer
 * over rows. The number therefore appears in every object this gate builds, so
 * an unruled tree cannot be byte-identical to main no matter what else is true.
 *
 * What the pin is FOR is everything else — that with the three rulings back to
 * null and the seat unstaffed, not one other byte of the refusal moved. So the
 * version is set back and the whole rest of the object is compared exactly. A
 * change to any other field is still red, which is the property this pin
 * carries; the one field it forgives is the one this PR declares it moved.
 */
const PRE_PR_POLICY_VERSION = 2;
function atPrePrPolicyVersion(answer) {
  assert.equal(answer.policy_version, V5_A02_POLICY_VERSION,
    "the answer does not carry this branch's policy version, so the normalization is wrong");
  assert.notEqual(V5_A02_POLICY_VERSION, PRE_PR_POLICY_VERSION,
    "the policy version did not move, so this normalization is hiding nothing and must go");
  return { ...answer, policy_version: PRE_PR_POLICY_VERSION };
}

/**
 * THE r7 PACKET DIGEST, PUT BACK TO THE PRE-PR COMMIT'S, AND WHY THIS IS THE
 * SAME KIND OF NORMALIZATION AS THE POLICY VERSION ABOVE RATHER THAN A RE-PIN.
 *
 * The 2026-09-13 consumer-gate-receipt digest amendment — act 5 of accepted plan
 * PLAN-a5059eb52474-v3 — re-froze the r7 packet, so
 * `V5_A02_GATE_ZERO_R7_PACKET_SHA256` moved and with it the one byte of
 * `producer_registration` that carries it. That is card 10's value moving again,
 * which is precisely what `withoutCards9And10` exists to lift out; it did not
 * lift this field because on the pre-PR commit the field was not moving.
 *
 * THE ALTERNATIVE WAS WORSE, and it is worth saying which one was rejected.
 * Re-pinning `MAIN_ANSWER_DIGESTS.emitGateZeroOutcome` would have forgiven EVERY
 * other byte at the same time, and editing the committed baseline snapshot is the
 * defect that file's own header warns about. Setting back one named field keeps
 * the pin's property: a change to anything else is still red.
 *
 * IT IS GUARDED THE SAME WAY. If the two digests are ever equal the normalization
 * is hiding nothing and must go, and the assertion below says so.
 */
const PRE_PR_R7_PACKET_SHA256 =
  "ea40f61a9081814e53c989f2f945c61b270597cdfeafc4ec535578e60462a8f6";
function atPrePrR7PacketDigest(answer) {
  assert.equal(answer.producer_registration.r7_packet_sha256,
    V5_A02_GATE_ZERO_R7_PACKET_SHA256,
    "the answer does not carry this branch's r7 packet digest, so the normalization is wrong");
  assert.notEqual(V5_A02_GATE_ZERO_R7_PACKET_SHA256, PRE_PR_R7_PACKET_SHA256,
    "the r7 packet digest did not move, so this normalization is hiding nothing and must go");
  return { ...answer, producer_registration: {
    ...answer.producer_registration, r7_packet_sha256: PRE_PR_R7_PACKET_SHA256 } };
}

/**
 * THE ONE FIELD THIS SLICE ADDS TO THE EMITTED ANSWER. It carries whatever the
 * bound producer said, so a consumer can see WHICH clause or WHICH missing row
 * decided a refusal. On an unruled tree the seam is shut and it is null — which
 * is still a field main did not have, so the pin is taken without it and its own
 * values are asserted where the producer is actually reached.
 */
const PRODUCER_ANSWER_FIELDS = Object.freeze(["producer_answer"]);

/** The answer with cards 9 and 10, the per-card booleans and the producer's own answer lifted out. */
function withoutCards9And10(answer) {
  const stripped = { ...answer };
  for (const field of [...CARD_9_ANSWER_FIELDS, ...PER_CARD_ANSWER_FIELDS,
    ...PRODUCER_ANSWER_FIELDS]) {
    assert.ok(Object.hasOwn(stripped, field), `${field} is not on the answer`);
    delete stripped[field];
  }
  const registration = { ...stripped.producer_registration };
  for (const field of CARD_9_REGISTRATION_FIELDS) {
    assert.ok(Object.hasOwn(registration, field), `${field} is not on the registration`);
    delete registration[field];
  }
  stripped.producer_registration = registration;
  return stripped;
}

/**
 * WHAT MAIN ANSWERS, read off the commit the pre-PR baseline pins and not off a
 * ref. The first two are the baseline's own `gate_answer_digests`; the third is
 * the emission, which the baseline does not carry because this branch adds
 * fields to it on purpose.
 *
 * THEY MOVED ON 2026-09-12 and the move is the point: PR 1009 changed this
 * surface while this branch was open — byte-derived r7 presence, the renamed
 * witness fields, the reworded reader refusals — so the three digests taken
 * from `229980a5` were answers no commit gives any more. Re-derived from
 * `e05c8939`, which is what `PRE_PR_COMMIT` names.
 */
const MAIN_ANSWER_DIGESTS = Object.freeze({
  readGateZeroPredecessorJoin:
    "sha256:fd7fade6a1042745171147cd6bbdb699883a841aea158e53436c43a77ab5954a",
  readGateGraphAssurance:
    "sha256:31c447c1b354cc453c2eea90127a899519bcea514e398bb1ddf5cbc299b9445a",
  // Taken from that commit the same way, then passed through withoutCards9And10
  // — on main that function is the identity, because main has no card-9 field.
  emitGateZeroOutcome:
    "sha256:04e750af7bf7728bc0a52ffd6e5d1dd40c9c88c80459647fccdd9c49ae3361b6",
});

// THE TWO READER DIGESTS ARE THE BASELINE'S OWN, not a second copy of them: the
// snapshot carries the same pair, digest-authenticated, so a re-pin that updated
// one and forgot the other is red here rather than silently self-consistent.
assert.deepEqual([MAIN_ANSWER_DIGESTS.readGateZeroPredecessorJoin,
  MAIN_ANSWER_DIGESTS.readGateGraphAssurance].sort(),
[...PRE_PR_BASELINE.gate_answer_digests],
"the pinned main answers are not the pre-PR commit's, which the baseline holds");

/** The three ruled lines as src holds them, and the null each goes back to. */
const RULED_DECISION_LINES = Object.freeze([
  '    decision_id: "16c7cdfb-b675-4b6a-bbff-4bbdab46baf8",\n',
  '    decision_id: "f7c486d6-5bee-4c4c-a76f-c0f162f66db8",\n',
  '    decision_id: "87e9e11e-64b2-49b3-a6aa-4901c24eaa91",\n',
]);
const NULL_DECISION_LINE = "    decision_id: null,\n";

/**
 * THE STORE HALF OF EACH RULING, which is the half the PR 1004 re-review found
 * the gate was not asking about. Each anchor is the card's `store_ref:` line
 * together with the `decision_id:` line beneath it — the decision id is what
 * makes the pair unique, since the store refs themselves also appear in the
 * table's closed list and in its comments.
 *
 * A card's store is rotated to THE NEXT CARD'S, which is the probe the review
 * ran: a store ref the table registers and a reader in this same file serves, so
 * the ruling is valid, well-formed and live — and not the store the rotated
 * card's own reader opens. The only thing wrong with it is the disagreement, and
 * the disagreement is the whole question.
 */
const RULED_STORE_REFS = Object.freeze([
  "record-layer:work-request-outcome-feedback",
  "control-plane:ops.service+ops.run",
  "github:checks",
]);
const ROTATED_STORE_REF = index => RULED_STORE_REFS[(index + 1) % RULED_STORE_REFS.length];
const STORE_LINE = ref => `    store_ref: "${ref}",\n`;

/** The file the mutation control rewrites, and the line it rewrites in it. */
const GATE_MODULE_FILE = "gate-zero-assurance.v5.js";

/**
 * CARD 9'S SWITCH, AND IT IS ONE LINE, exactly like the three ruling lines
 * above. `holder_ref` is where the staffing ruling goes; null is the unstaffed
 * state main shipped.
 *
 * EVERY STAGED TREE UNSTAFFS IT BY DEFAULT, and that is deliberate rather than
 * convenient: the trees below exist to answer "what did main answer", and main
 * had no seat. Staging a staffed seat into a tree pinned against main's bytes
 * would put this branch's own card-9 value inside the baseline it is measured
 * against, which is the same defect the moving-ref baseline had.
 */
const REGISTRATION_MODULE_FILE = "gate-zero-producer-registration.v5.js";
const STAFFED_SEAT_LINE = '  holder_ref: "seat:codex-reviewer:gpt-5.6-sol",\n';
const UNSTAFFED_SEAT_LINE = "  holder_ref: null,\n";

/**
 * The other two lines of the declaration, so the seat's falsifiers can move ONE
 * of them at a time. A seat that bound on a holder alone would be a seat anybody
 * could claim; these are how that is proved false rather than asserted.
 *
 * BOTH ANCHORS ARE CONSTANT REFERENCES rather than the values they resolve to,
 * because src holds each authority-bearing id in exactly one place and the
 * declaration cites it by name. A falsifier replaces the whole line with a
 * literal, which is what a drifted declaration would look like — so the staging
 * still asks the question it asked when the line carried a uuid of its own.
 */
const SEAT_CHARTER_LINE = "  charter_ref: V5_A02_GATE_ZERO_ORACLE_SEAT_CHARTER_REF,\n";
const SEAT_STAFFING_LINE =
  "  staffing_decision_ref: V5_A02_GATE_ZERO_ORACLE_SEAT_STAFFING_DECISION_REF,\n";
/**
 * THE THREE READER SEAMS' BINDING PREDICATE, one line each — it moved out of
 * `boundSeam` and onto the bindings themselves when card 9 got a seam whose
 * authority is a seat declaration rather than a store ruling. The control is the
 * same control: each reader seam goes back to asking the ruling table directly,
 * which reads ANY non-null ruling as bound and is the half that let the gate and
 * its reader disagree.
 */
const BOUND_PREDICATE_LINES = Object.freeze([11, 12, 13].map(card =>
  `    ruled: () => ruledCardBinding("card:${card}") !== null,\n`));
const DIVERGENT_PREDICATE_LINES = Object.freeze([11, 12, 13].map(card =>
  `    ruled: () => looseRulingRef("card:${card}") !== null,\n`));
const READERS_IMPORT_TAIL = '} from "./gate-zero-seam-readers.v5.js";\n';
const DIVERGENT_IMPORT =
  'import { seamRulingRef as looseRulingRef } from "./gate-zero-seam-rulings.v5.js";\n';

const stagedTrees = [];

after(() => {
  for (const base of stagedTrees) rmSync(base, { recursive: true, force: true });
  releasePrePrTrees();
});

/**
 * A copy of src with the NAMED cards' ruling lines set back to null — by default
 * all three, which is the tree main shipped, and otherwise exactly the ones asked
 * for. Withdrawing one ruling at a time is how the 2026-09-12 review's first
 * finding is tested: three cards, three separate seams, three separate answers.
 *
 * Nothing in src is edited, no argument of any export selects the copy, and no
 * environment variable points at it: the copy is reached by importing it.
 */
function stageTree(withdrawnCards = [0, 1, 2],
  { mismatchedCards = [], divergentGate = false, staffedSeat = false,
    seatEdit = [] } = {}) {
  const cache = fileURLToPath(new URL("../node_modules/.cache/", import.meta.url));
  mkdirSync(cache, { recursive: true });
  const base = mkdtempSync(join(cache, "gate-zero-unruled-"));
  stagedTrees.push(base);
  const target = join(base, "src");
  cpSync(fileURLToPath(new URL("../src/", import.meta.url)), target, { recursive: true });

  const rulingsPath = join(target, "gate-zero-seam-rulings.v5.js");
  let rulings = readFileSync(rulingsPath, "utf8");
  // THE STORE ROTATION FIRST, because its anchor includes the decision line that
  // the withdrawal below replaces. Rotating after a withdrawal would look for a
  // pair that no longer exists.
  RULED_DECISION_LINES.forEach((decisionLine, index) => {
    const anchor = STORE_LINE(RULED_STORE_REFS[index]) + decisionLine;
    assert.equal(rulings.split(anchor).length - 1, 1,
      "a staging anchor no longer matches a store-and-ruling pair in src");
    if (mismatchedCards.includes(index))
      rulings = rulings.replace(anchor, STORE_LINE(ROTATED_STORE_REF(index)) + decisionLine);
  });
  RULED_DECISION_LINES.forEach((anchor, index) => {
    assert.equal(rulings.split(anchor).length - 1, 1,
      "a staging anchor no longer matches a ruling line in src");
    if (withdrawnCards.includes(index)) rulings = rulings.replace(anchor, NULL_DECISION_LINE);
  });
  assert.equal(rulings.split(NULL_DECISION_LINE).length - 1, withdrawnCards.length,
    "the staging left the wrong number of unruled lines");
  writeFileSync(rulingsPath, rulings);

  // CARD 9'S LINE, AND IT TURNS BOTH WAYS FROM HERE. Unstaffed unless a test
  // asks otherwise, so every pin taken against main is taken over main's seat;
  // `seatEdit` is how the falsifiers move one line of the declaration instead.
  const seatEdits = [...(staffedSeat ? [] : [[STAFFED_SEAT_LINE, UNSTAFFED_SEAT_LINE]]),
    ...seatEdit];
  if (seatEdits.length > 0) {
    const registrationPath = join(target, REGISTRATION_MODULE_FILE);
    let registration = readFileSync(registrationPath, "utf8");
    for (const [anchor, replacement] of seatEdits) {
      assert.equal(registration.split(anchor).length - 1, 1,
        "a staging anchor no longer matches a line of the seat declaration in src");
      registration = registration.replace(anchor, replacement);
    }
    writeFileSync(registrationPath, registration);
  }

  // THE MUTATION CONTROL'S TREE, and it is a source rewrite rather than a flag
  // in src: the gate goes back to asking the ruling table itself and reading any
  // non-null ruling as a bound seam, which is exactly the predicate this
  // correction deleted. Nothing in src carries it.
  if (divergentGate) {
    const gatePath = join(target, GATE_MODULE_FILE);
    let gate = readFileSync(gatePath, "utf8");
    BOUND_PREDICATE_LINES.forEach((anchor, index) => {
      assert.equal(gate.split(anchor).length - 1, 1,
        "the gate's binding predicate is no longer the line this control replaces");
      gate = gate.replace(anchor, DIVERGENT_PREDICATE_LINES[index]);
    });
    assert.equal(gate.split(READERS_IMPORT_TAIL).length - 1, 1,
      "the gate's reader import is no longer where this control adds the old one");
    gate = gate.replace(READERS_IMPORT_TAIL, READERS_IMPORT_TAIL + DIVERGENT_IMPORT);
    writeFileSync(gatePath, gate);
  }
  return target;
}

/** The tree main shipped: all three rulings withdrawn. */
function stageUnruledTree() {
  return stageTree();
}

/** One staged tree's gate module, imported from the copy. */
function gateOfTree(target) {
  return import(pathToFileURL(join(target, GATE_MODULE_FILE)).href);
}

/** The same tree's readers and its ruling table, so all three answer together. */
function readersOfTree(target) {
  return import(pathToFileURL(join(target, "gate-zero-seam-readers.v5.js")).href);
}

function rulingsOfTree(target) {
  return import(pathToFileURL(join(target, "gate-zero-seam-rulings.v5.js")).href);
}

/**
 * Whether a reader's answer is its OWN — that is, whether the ruling let it open
 * its store at all. A reader that refuses on its ruling hands back the gate's
 * refusal verbatim, and that object has no card, no store and no query digest on
 * it. This is the reader half of "bound", asked without importing anything
 * private.
 */
function readerReachedItsStore(answered) {
  return Object.hasOwn(answered, "card_ref");
}

test("SWITCH: with the three rulings back to null, the answers are main's bytes", async () => {
  const target = stageUnruledTree();
  const unruled = await import(
    pathToFileURL(join(target, "gate-zero-assurance.v5.js")).href);

  for (const name of ["readGateZeroPredecessorJoin", "readGateGraphAssurance"])
    assert.equal(digest(atPrePrPolicyVersion(unruled[name]())), MAIN_ANSWER_DIGESTS[name],
      `${name} no longer answers what main answered while unruled`);
  // And the emission, with cards 9 and 10 lifted out: every other byte is main's.
  assert.equal(digest(atPrePrR7PacketDigest(
      atPrePrPolicyVersion(withoutCards9And10(await unruled.emitGateZeroOutcome())))),
    MAIN_ANSWER_DIGESTS.emitGateZeroOutcome,
    "the emitted answer moved for a reason other than cards 9 and 10");

  // And the readings the answers report are the readings main reported.
  const unruledJoin = unruled.readGateZeroPredecessorJoin();
  assert.equal(unruledJoin.reason_id, "predecessor_outcome_reader_unavailable");
  assert.equal(unruledJoin.predecessor_outcome_reader_bound, false);
  assert.equal(unruledJoin.scheduler_reader_bound, false);
  assert.equal(unruledJoin.decided_by, "no_authoritative_reader");
  assert.equal(unruled.readGateGraphAssurance().gate_conclusion_reader_bound, false);
  for (const result of [unruledJoin, unruled.readGateGraphAssurance(),
    await unruled.emitGateZeroOutcome()])
    for (const entry of result.seams_bound)
      assert.equal(entry.bound, false, `${entry.seam} reported bound in an unruled tree`);

  // The staging is a copy; src itself still carries Joe's three rulings.
  assert.equal(readGateZeroPredecessorJoin().predecessor_outcome_reader_bound, true);
});

test("SWITCH: the shipped answers are NOT main's bytes, so the pin can fail", async () => {
  // Without this, a wiring that did nothing would pass the test above silently.
  for (const [name, fn] of [
    ["readGateZeroPredecessorJoin", readGateZeroPredecessorJoin],
    ["readGateGraphAssurance", readGateGraphAssurance],
  ])
    assert.notEqual(digest(atPrePrPolicyVersion(fn())), MAIN_ANSWER_DIGESTS[name],
      `${name} still answers exactly what it answered unwired`);
  // The emission too, and it must differ for the READER reason and not only
  // because cards 9 and 10 added four names: strip those and it still moves.
  assert.notEqual(digest(atPrePrPolicyVersion(withoutCards9And10(await emitGateZeroOutcome()))),
    MAIN_ANSWER_DIGESTS.emitGateZeroOutcome,
    "the emitted answer moved only by the card 9 and 10 fields");
});

// ---------------------------------------------------------------------------
// ONE CARD AT A TIME — the 2026-09-12 review's first finding.
//
// Three cards, three rulings, three seams. The defect was a single
// `readersBound` flag: with all three ruled the emission still reported that no
// predecessor or scheduler reader existed, and withdrawing ONE ruling reopened
// all three governance questions. Every permutation below is a staged tree with
// exactly one ruling line back to null, and each is asserted to move exactly its
// own card's report and nothing else.
// ---------------------------------------------------------------------------

/** The three ruled cards, in the order their ruling lines sit in the table. */
const READER_CARDS = Object.freeze([
  Object.freeze({
    key: "predecessor", card: "card:11", bound: "predecessor_outcome_reader_bound",
    seam: V5_A02_PREDECESSOR_OUTCOME_READER_SEAM,
    question: "which store an accepted predecessor outcome is read from",
    // The reader behind this card, the gate answer it falls back to when its
    // ruling does not hold, and a well-formed query — well-formed so that a
    // refusal below is never attributable to the query. No call in the
    // store-mismatch test reaches a store: every one of them is refused on the
    // ruling before the query is read at all.
    reader: "readPredecessorOutcomeEvidence", fallback: "readGateZeroPredecessorJoin",
    query: { stepRef: "step:wr46-dissolution-outcome", outcomeHash: `sha256:${"a".repeat(64)}` },
  }),
  Object.freeze({
    key: "scheduler", card: "card:12", bound: "scheduler_reader_bound",
    seam: V5_A02_SCHEDULER_READER_SEAM,
    question: "which scheduler surface a canary and its readback are read from",
    reader: "readSchedulerCanaryEvidence", fallback: "readGateZeroPredecessorJoin",
    query: { serviceKey: "carr-fleet-sync", canaryRunKey: "canary-join" },
  }),
  Object.freeze({
    key: "conclusion", card: "card:13", bound: "gate_conclusion_reader_bound",
    seam: V5_A02_GATE_CONCLUSION_READER_SEAM,
    question: "which surface a gate's own conclusion is read from",
    reader: "readGateConclusionEvidence", fallback: "readGateGraphAssurance",
    query: { headSha: "a".repeat(40), checkName: "main canary" },
  }),
]);

/**
 * Card 9, which no ruling line here can close. Card 10 left this list when the
 * loop-589 amendment was applied to the frozen packet — what replaced it is not
 * a question but a witness over the packet's bytes, and a witness that answers
 * `null` for want of bytes is not an open governance question.
 */
const PRODUCER_QUESTIONS = Object.freeze([
  "which independent seat holds oracle:gate-producer:gate-zero-read-only",
]);

/** What the emission says about the join for each state of the two join cards. */
const JOIN_SENTENCES = Object.freeze({
  both: "no authoritative predecessor-outcome or scheduler reader exists to join",
  predecessor: "no authoritative accepted-outcome seam is bound, so there is nothing for a scheduler canary to join against",
  scheduler: "the ruled accepted-outcome seam is bound and no authoritative scheduler surface is bound, so there is nothing to join it against",
  // The producer seam is BOUND in the shipped tree, so the clause names what is
  // actually true of a synchronous query: aiming the seams is the emission's
  // job, not this read's.
  neither: "the two ruled evidence seams are bound and the producer seam that names the rows to join is bound, and a query of this surface does not aim it",
  // The same clause with card 9's line back to null: the sentence names the
  // empty seat again, which is what main said and what the seat's own mutation
  // control below asserts.
  neither_unstaffed: "the two ruled evidence seams are bound and no seat holds the producer that would name the rows to join",
});

/**
 * CARD 9 — THE SEAT, AND ITS MUTATION CONTROL.
 *
 * `oracle_seat_bound` is not a record-layer row, a doctrine section, a config
 * file or a field any caller can set. It is derived from a declaration committed
 * to gate-zero-producer-registration.v5.js, exactly as the three seam rulings
 * are one committed line each — so the only way to prove the derivation is to
 * stage a copy of src with that line moved and read what the whole module then
 * answers.
 *
 * THREE THINGS ARE PROVED HERE AND THEY ARE DIFFERENT THINGS.
 *
 *   (1) THE SWITCH TURNS. Unstaffed, the surface answers what main answered:
 *       the seat is unbound, card 9's governance question is back on the list,
 *       the refusal is decided by the empty seat, and both staffing fields are
 *       null. Staffed, all four move together. Without this a `true` typed into
 *       the source would pass every other test in this file.
 *
 *   (2) IT DOES NOT TURN ON A CLAIM. A holder alone does not staff the seat:
 *       each falsifier moves ONE line of the declaration — the charter away from
 *       the one Joe's card-9 ruling names, the staffing ruling to something that
 *       is not a decision id, the holder to something that is not a seat ref —
 *       and the seat is unbound for each. Fail-closed is the default answer,
 *       which is the same "no" this surface gave before any seat was named.
 *
 *   (3) THE SEAT IS STILL LOAD-BEARING NOW THAT THE PRODUCER EXISTS, and this
 *       is the clause that matters most — it is the one that changed. The
 *       producer seam's `ruled` thunk asks card 9's declaration, so an unstaffed
 *       seat closes the seam and the surface goes back, byte for byte, to the
 *       refusal it gave before this slice: `producer_bound: false`, card 9's
 *       governance question back on the list, the refusal decided by the empty
 *       seat. A staffed seat BINDS the seam — and still does not make the gate
 *       pass, because a seam is a place a signature can come from and the
 *       signature is earned over rows. `passable` is false either way, which is
 *       exactly the authority this slice must not hand anybody.
 */
const SEAT_STAFFED_FIELDS = Object.freeze({
  oracle_seat_bound: true,
  oracle_seat_holder_ref: "seat:codex-reviewer:gpt-5.6-sol",
  oracle_seat_staffing_decision_ref: "359784f1-5d9e-4e11-bcce-af8b0dfcc5e0",
});
const SEAT_UNSTAFFED_FIELDS = Object.freeze({
  oracle_seat_bound: false,
  oracle_seat_holder_ref: null,
  oracle_seat_staffing_decision_ref: null,
});

/** A staged tree with all three rulings live and the seat as the case asks. */
function stageSeatTree(options) {
  return stageTree([], options);
}

/**
 * Every path at which two answers differ, one level into `producer_registration`
 * and by canonical bytes below that. It reports paths rather than a boolean so
 * the assertion names what moved instead of only that something did.
 */
function movedFields(left, right) {
  const moved = [];
  const keys = [...new Set([...Object.keys(left), ...Object.keys(right)])].sort();
  for (const key of keys) {
    if (key === "producer_registration") continue;
    if (canonicalJsonOf(left[key]) !== canonicalJsonOf(right[key])) moved.push(`$.${key}`);
  }
  const leftRegistration = left.producer_registration ?? {};
  const rightRegistration = right.producer_registration ?? {};
  const registrationKeys = [...new Set([...Object.keys(leftRegistration),
    ...Object.keys(rightRegistration)])].sort();
  for (const key of registrationKeys)
    if (canonicalJsonOf(leftRegistration[key]) !== canonicalJsonOf(rightRegistration[key]))
      moved.push(`$.producer_registration.${key}`);
  return moved.sort();
}

const canonicalJsonOf = value => JSON.stringify(value ?? null);

test("SEAT: an unstaffed seat refuses on the seat, and a staffed one binds", async () => {
  const unstaffed = await gateOfTree(stageSeatTree({ staffedSeat: false }));
  const emittedUnstaffed = await unstaffed.emitGateZeroOutcome();
  for (const [field, value] of Object.entries(SEAT_UNSTAFFED_FIELDS))
    assert.equal(emittedUnstaffed[field], value, `${field} with the seat unstaffed`);
  assert.equal(emittedUnstaffed.producer_registration.oracle_seat_bound, false);
  assert.equal(emittedUnstaffed.producer_registration.oracle_seat_owed,
    "an independent seat, distinct from the V5-A02 builder, holding oracle:gate-producer:gate-zero-read-only");
  // The question comes back, and it comes back alone: the three reader cards
  // stay ruled in this tree, so nothing else reopens with it.
  assert.deepEqual([...emittedUnstaffed.undecided_governance_questions],
    [...PRODUCER_QUESTIONS]);
  assert.equal(emittedUnstaffed.decided_by, "evidence_seams_bound_producer_unstaffed");
  assert.equal(emittedUnstaffed.join_unavailable_because, JOIN_SENTENCES.neither_unstaffed);
  assert.ok(emittedUnstaffed.unavailable_because.includes("no seat staffs"),
    "the unstaffed refusal must say the seat is empty");
  assert.equal(unstaffed.readGateZeroPredecessorJoin().decided_by,
    "evidence_seams_bound_producer_unstaffed");
  assert.equal(unstaffed.readGateGraphAssurance().decided_by,
    "evidence_seams_bound_producer_unstaffed");

  // AND STAFFED, which is what src ships. Read off the shipped module rather
  // than a second staged tree: the thing a consumer gets is the subject.
  const emittedStaffed = await emitGateZeroOutcome();
  for (const [field, value] of Object.entries(SEAT_STAFFED_FIELDS))
    assert.equal(emittedStaffed[field], value, `${field} with the seat staffed`);
  assert.equal(emittedStaffed.producer_registration.oracle_seat_owed, null);
  assert.deepEqual([...emittedStaffed.undecided_governance_questions], []);
  assert.equal(emittedStaffed.decided_by, "evidence_seams_bound_producer_seam_built");
  assert.equal(emittedStaffed.join_unavailable_because, JOIN_SENTENCES.neither);
  assert.equal(emittedStaffed.unavailable_because.includes("no seat staffs"), false);

  // THE CONTROL IS A CONTROL: the two answers differ, so a derivation that had
  // been hard-coded either way would be red here rather than quietly agreeing.
  assert.notEqual(digest(emittedUnstaffed), digest(emittedStaffed),
    "moving the seat declaration changed nothing, so the switch is not the switch");

  // AND THE CEILING, NAMED RATHER THAN STRIPPED. Every field whose value moves
  // when the seat does is listed, and the list is asserted to be EXACTLY what
  // moved — so a sixth field that started following the seat is red here, and so
  // is one of these quietly ceasing to.
  assert.deepEqual(movedFields(emittedUnstaffed, emittedStaffed), [
    "$.decided_by",
    "$.join_unavailable_because",
    "$.not_passable_because",
    "$.oracle_seat_bound",
    "$.oracle_seat_holder_ref",
    "$.oracle_seat_staffing_decision_ref",
    // THE FIVE THIS SLICE ADDED TO CARD 9'S BLAST RADIUS, and every one of them
    // is still card 9's: the seat declaration is what the producer seam's
    // `ruled` thunk asks, so staffing it binds the seam, empties the owed list,
    // flips the seam's own entry in `seams_bound`, reaches the producer for the
    // first time, and moves the refusal from "nothing implements this" to the
    // producer's own reason. `passable` is NOT among them and must never be.
    "$.owed_seams",
    "$.producer_answer",
    "$.producer_bound",
    "$.producer_registration.oracle_seat_bound",
    "$.producer_registration.oracle_seat_holder_ref",
    "$.producer_registration.oracle_seat_owed",
    "$.producer_registration.oracle_seat_staffing_decision_ref",
    "$.reason_id",
    "$.seams_bound",
    "$.unavailable_because",
    "$.undecided_governance_questions",
  ], "staffing the seat moved a field that is not card 9's");
  assert.equal(emittedUnstaffed.passable, emittedStaffed.passable,
    "staffing the seat moved the one field a seat may never move");
});

test("SEAT: an unstaffed seat closes the built producer seam, and neither state passes", async () => {
  const unstaffed = await gateOfTree(stageSeatTree({ staffedSeat: false }));
  // THE SEAT IS THE SWITCH, STILL. The producer module is in this staged tree
  // exactly as it is in src — nothing was deleted, no argument was passed — and
  // the seam is shut anyway, because the only thing that opens it is card 9's
  // declaration. This is the mutation control the seam study asked for: put
  // `holder_ref: null` back and the whole slice goes dark.
  const emittedUnstaffed = await unstaffed.emitGateZeroOutcome();
  assert.equal(emittedUnstaffed.producer_bound, false, "an unstaffed seat left the seam open");
  assert.equal(emittedUnstaffed.reason_id, "gate_zero_producer_seam_unavailable");
  assert.equal(emittedUnstaffed.producer_answer, null,
    "an unstaffed seat still reached the producer");
  assert.deepEqual(emittedUnstaffed.owed_seams, [V5_A02_GATE_ZERO_PRODUCER_SEAM]);
  assert.equal(unstaffed.v5A02GateZeroPolicyPreimage().producer_bound, false);

  // AND STAFFED, which is what src ships: the seam is bound and the refusal is
  // the producer's own. In a test nothing established an authenticated call, so
  // what it refuses on is the identity it will not manufacture.
  const emittedStaffed = await emitGateZeroOutcome();
  assert.equal(emittedStaffed.producer_bound, true);
  assert.equal(emittedStaffed.reason_id, "gate_zero_producer_identity_refused");
  assert.equal(emittedStaffed.producer_answer.decision, "refuse");
  assert.deepEqual(emittedStaffed.owed_seams, []);
  assert.equal(v5A02GateZeroPolicyPreimage().producer_bound, true);

  // NEITHER STATE PASSES, and that is the clause a staffed seat must never move.
  for (const [label, emitted] of [["unstaffed", emittedUnstaffed], ["staffed", emittedStaffed]]) {
    assert.equal(emitted.passable, false, `passable while ${label}`);
    assert.equal(emitted.status, "unavailable", `status while ${label}`);
    assert.equal(emitted.decision, "refuse", `decision while ${label}`);
    assert.equal(emitted.outcome_digest, null, `outcome_digest while ${label}`);
    assert.equal(emitted.observed_at, null, `observed_at while ${label}`);
    assert.equal(emitted.join, null, `join while ${label}`);
  }
});

/**
 * ONE LINE MOVED PER CASE, and each is a declaration a seat might plausibly
 * carry — not a garbage value. The charter case is the one that matters most: a
 * later seat that claimed the oracle without holding the charter card 9 named
 * would be checkable against a named authority, and this is that check.
 */
const SEAT_FALSIFIERS = Object.freeze([
  {
    name: "a holder under a charter card 9 did not name",
    edit: [SEAT_CHARTER_LINE, '  charter_ref: "charter:builder",\n'],
  },
  {
    name: "a staffing ruling that is not a decision id",
    edit: [SEAT_STAFFING_LINE, '  staffing_decision_ref: "approved-by-the-orchestrator",\n'],
  },
  {
    name: "a staffing ruling that is a near miss for one",
    edit: [SEAT_STAFFING_LINE, '  staffing_decision_ref: "359784F1-5D9E-4E11-BCCE-AF8B0DFCC5E0",\n'],
  },
  // THE THREE CASES A UUID-SHAPE CHECK COULD NOT TELL APART, which is the whole
  // of the 2026-09-12 review's first finding. Each of these is well formed, each
  // passed the regex the predicate used to run, and none of them is the staffing
  // ruling.
  {
    name: "a well-formed decision id that is not this seat's staffing ruling",
    edit: [SEAT_STAFFING_LINE,
      '  staffing_decision_ref: "76782922-f9b5-4492-83fc-f144184c61ff",\n'],
  },
  {
    name: "the idempotency key the blanket approval was logged under",
    edit: [SEAT_STAFFING_LINE,
      '  staffing_decision_ref: "5e2b8c1a-9f47-4d63-b0e5-7a3d1c9f2e84",\n'],
  },
  {
    name: "the charter ruling standing in the staffing slot",
    edit: [SEAT_STAFFING_LINE,
      '  staffing_decision_ref: "8a1dad08-8707-4bb0-a159-c2831a00cea2",\n'],
  },
  {
    name: "a holder that is not a seat ref",
    edit: [STAFFED_SEAT_LINE, '  holder_ref: "gpt-5.6-sol",\n'],
  },
  {
    name: "a holder that is a charter rather than a desk",
    edit: [STAFFED_SEAT_LINE, '  holder_ref: "charter:reviewer",\n'],
  },
]);

test("SEAT: the seat binds on the whole declaration, and fails closed on any of it", async () => {
  for (const falsifier of SEAT_FALSIFIERS) {
    const tree = await gateOfTree(
      stageSeatTree({ staffedSeat: true, seatEdit: [falsifier.edit] }));
    const emitted = await tree.emitGateZeroOutcome();
    assert.equal(emitted.oracle_seat_bound, false, falsifier.name);
    assert.equal(emitted.oracle_seat_holder_ref, null, falsifier.name);
    assert.equal(emitted.oracle_seat_staffing_decision_ref, null, falsifier.name);
    assert.deepEqual([...emitted.undecided_governance_questions],
      [...PRODUCER_QUESTIONS], falsifier.name);
    assert.equal(emitted.passable, false, falsifier.name);
    // AND THE SEAM CLOSES WITH IT. One wrong line of the declaration does not
    // merely stop reporting a holder — it takes the producer seam down, which is
    // what makes the seat load-bearing rather than decorative.
    assert.equal(emitted.producer_bound, false, falsifier.name);
  }
  // AND THE FALSIFIERS ARE FALSIFIERS: the unmodified declaration, staged the
  // same way through the same machinery, binds. Without this the five cases
  // above would pass just as well against a staging step that broke the file.
  const intact = await gateOfTree(stageSeatTree({ staffedSeat: true }));
  const emittedIntact = await intact.emitGateZeroOutcome();
  assert.equal(emittedIntact.oracle_seat_bound, true,
    "the staging itself unstaffs the seat, so the falsifiers prove nothing");
  // AND THE ONE VALUE THAT BINDS IS THE DECISION'S OWN ID. The falsifiers above
  // are only falsifiers if the thing they were moved away from is the real
  // ruling, so it is named here rather than left implied by "the file as it is".
  assert.equal(emittedIntact.oracle_seat_staffing_decision_ref,
    "359784f1-5d9e-4e11-bcce-af8b0dfcc5e0",
    "the seat binds on something other than the blanket approval's decision id");
});

test("PER READER: with all three ruled, nothing reports a reader as missing", async () => {
  const emitted = await emitGateZeroOutcome();
  // The list the review found wrong: with three live rulings and card 9's seat
  // staffed it is EMPTY, and the gate refuses anyway.
  assert.deepEqual([...emitted.undecided_governance_questions], []);
  for (const card of READER_CARDS) {
    assert.equal(emitted.undecided_governance_questions.includes(card.question), false, card.card);
    assert.equal(emitted[card.bound], true, card.bound);
  }
  // And the sentence the review quoted: it may not say a reader is absent when
  // none is.
  assert.equal(emitted.join_unavailable_because, JOIN_SENTENCES.neither);
  assert.equal(emitted.join_unavailable_because.includes("no authoritative predecessor-outcome or scheduler reader exists"),
    false, "the emission still reports both readers missing while both are bound");
  // And the two synchronous reads refuse on what is now the true reason: the
  // producer seam is bound, so "unavailable" stopped being a thing this surface
  // may say about it.
  assert.equal(readGateZeroPredecessorJoin().reason_id, "gate_zero_join_is_produced_not_queried");
  assert.equal(readGateGraphAssurance().reason_id, "gate_zero_join_is_produced_not_queried");
});

test("PER READER: withdrawing one ruling reopens that card's question and no other", async () => {
  const digests = new Map();
  for (const [index, card] of READER_CARDS.entries()) {
    const target = stageTree([index]);
    // THE STAGING TARGETED THE NAMED CARD, proved in the staged ruling table
    // itself rather than inferred from the answer that came out of it. A fixture
    // that nulled the wrong line would otherwise pass every clause below by
    // accident.
    const rulings = await import(
      pathToFileURL(join(target, "gate-zero-seam-rulings.v5.js")).href);
    assert.equal(rulings.seamRulingRef(card.card), null, `${card.card} is still ruled`);
    for (const other of READER_CARDS.filter(one => one !== card))
      assert.notEqual(rulings.seamRulingRef(other.card), null,
        `${other.card} was withdrawn as well as ${card.card}`);

    const tree = await gateOfTree(target);
    const emitted = await tree.emitGateZeroOutcome();
    digests.set(card.key, digest(emitted));

    // ONE reader question, and it is this card's.
    assert.deepEqual([...emitted.undecided_governance_questions],
      [...PRODUCER_QUESTIONS, card.question], card.card);
    // The three per-card booleans and the whole seam list agree on WHICH.
    const bound = new Map(emitted.seams_bound.map(entry => [entry.seam, entry.bound]));
    for (const one of READER_CARDS) {
      assert.equal(emitted[one.bound], one !== card, `${one.bound} with ${card.card} withdrawn`);
      assert.equal(bound.get(one.seam), one !== card, `${one.seam} with ${card.card} withdrawn`);
      assert.equal(emitted.owed_seams.includes(one.seam), one === card, one.seam);
    }
    // And the two reader answers, each about its own seams.
    const joined = tree.readGateZeroPredecessorJoin();
    const graph = tree.readGateGraphAssurance();
    assert.equal(joined.predecessor_outcome_reader_bound, card.key !== "predecessor");
    assert.equal(joined.scheduler_reader_bound, card.key !== "scheduler");
    assert.equal(graph.gate_conclusion_reader_bound, card.key !== "conclusion");
    if (card.key === "predecessor") {
      assert.equal(joined.reason_id, "predecessor_outcome_reader_unavailable");
      assert.ok(joined.unavailable_because.includes("the ruled scheduler canary seam is bound"),
        "the join still reports the scheduler seam as absent");
      assert.equal(graph.reason_id, "gate_zero_producer_seam_unavailable");
      assert.equal(emitted.join_unavailable_because, JOIN_SENTENCES.predecessor);
    }
    if (card.key === "scheduler") {
      assert.equal(joined.reason_id, "scheduler_canary_seam_unavailable");
      assert.ok(joined.unavailable_because.startsWith("the ruled accepted-outcome seam"),
        "the join still reports the predecessor seam as absent");
      assert.equal(graph.reason_id, "gate_zero_producer_seam_unavailable");
      assert.equal(emitted.join_unavailable_because, JOIN_SENTENCES.scheduler);
    }
    if (card.key === "conclusion") {
      assert.equal(joined.reason_id, "gate_zero_producer_seam_unavailable");
      assert.equal(graph.reason_id, "gate_conclusion_reader_unavailable");
      // The UNSTAFFED spelling, because a staged tree stages main's seat. The
      // staffed one is what src answers, asserted where JOIN_SENTENCES is used
      // against the shipped module.
      assert.equal(emitted.join_unavailable_because, JOIN_SENTENCES.neither_unstaffed);
    }
    // A withdrawal is neither the shipped answer nor the all-three-null one.
    assert.notEqual(digest(emitted), digest(await emitGateZeroOutcome()),
      `withdrawing ${card.card} changed nothing`);
    // AND THE ALL-THREE SENTENCE IS A LIVE ONE. The literal here is the branch
    // this module actually answers with when every reader card is withdrawn and
    // the seat is staged unstaffed, so a withdrawal that answered as though all
    // three were gone is caught. It was main's sentence before this correction,
    // which made the assertion vacuous.
    assert.notEqual(emitted.unavailable_because,
      "no seat holds the oracle and no evidence reader is bound to this surface, so nothing here can produce or stand behind a Gate Zero outcome",
      `withdrawing ${card.card} answered as though all three were withdrawn`);
    // Whatever it answers is still registered, and still a refusal.
    for (const answer of [emitted, joined, graph]) {
      assert.ok(V5_A02_GATE_ZERO_REASON_IDS.includes(answer.reason_id), answer.reason_id);
      assert.equal(answer.decision, "refuse");
      assert.equal(answer.status, "unavailable");
      assert.equal(answer.request_read, false);
    }
    assert.equal(emitted.passable, false);
  }
  // MUTATION CONTROL ACROSS THE PERMUTATIONS. Three different withdrawals, three
  // different answers: a gate that still read the three cards as one flag would
  // hand back identical bytes for all three, which is the defect itself.
  assert.equal(new Set(digests.values()).size, 3,
    "two different withdrawals produced the same answer");
  // And none of the three is the all-withdrawn answer either.
  const allWithdrawn = digest(await (await gateOfTree(stageTree())).emitGateZeroOutcome());
  for (const [key, one] of digests)
    assert.notEqual(one, allWithdrawn, `withdrawing ${key} alone answered as three withdrawals`);
});

// ---------------------------------------------------------------------------
// THE STORE HALF OF EVERY RULING — the PR 1004 re-review's finding, and the
// defect was a DISAGREEMENT rather than a missing check.
//
// `boundSeam()` read any non-null ruling as a bound seam. The reader requires
// more: the ruling must also name the one store that card's reader serves. So a
// ruling that kept its decision id and named ANOTHER REGISTERED STORE made the
// gate report the seam bound while the reader refused that same ruling and fell
// back without a ruling reference — the gate promising evidence no reader would
// ever produce. The permutation above could not see it, because withdrawing a
// decision id fails both predicates at once.
//
// So each card is probed with a valid, live, registered ruling that names the
// NEXT card's store, and the two halves of the system are asked the same
// question: is this seam bound. They must answer the same way, for the same
// reason, and the answer must be no.
// ---------------------------------------------------------------------------

test("PER READER: a ruling naming another registered store leaves the card unbound, in the gate and in the reader alike", async () => {
  for (const [index, card] of READER_CARDS.entries()) {
    const target = stageTree([], { mismatchedCards: [index] });

    // THE PROBE IS A RULING, NOT A WITHDRAWAL — proved in the staged table
    // itself. The decision id is intact, the store it names is registered, it is
    // the store another card's reader in this same table serves, and it is not
    // this card's. A fixture that nulled a line instead would pass every clause
    // below for the wrong reason.
    const rulings = await rulingsOfTree(target);
    const ruled = rulings.seamRulingRef(card.card);
    assert.notEqual(ruled, null, `${card.card} lost its ruling, so this probe is a withdrawal`);
    assert.equal(ruled.store_ref, ROTATED_STORE_REF(index), card.card);
    assert.notEqual(ruled.store_ref, RULED_STORE_REFS[index],
      `${card.card} still names its own store, so nothing is mismatched`);
    const rotatedOnto = READER_CARDS[(index + 1) % READER_CARDS.length];
    assert.equal(ruled.store_ref, rulings.seamRulingRef(rotatedOnto.card).store_ref,
      "the probe names a store no card in this table rules, so it is not a valid ruling");

    // THE GATE. This card is unbound, the other two are not, and the answer is
    // the one a withdrawal produces: same reason, same sentence, same bytes.
    const tree = await gateOfTree(target);
    const emitted = await tree.emitGateZeroOutcome();
    const bound = new Map(emitted.seams_bound.map(entry => [entry.seam, entry.bound]));
    for (const one of READER_CARDS) {
      assert.equal(emitted[one.bound], one !== card, `${one.bound} with ${card.card} mismatched`);
      assert.equal(bound.get(one.seam), one !== card, `${one.seam} with ${card.card} mismatched`);
      assert.equal(emitted.owed_seams.includes(one.seam), one === card, one.seam);
    }
    assert.deepEqual([...emitted.undecided_governance_questions],
      [...PRODUCER_QUESTIONS, card.question], card.card);
    assert.equal(digest(emitted),
      digest(await (await gateOfTree(stageTree([index]))).emitGateZeroOutcome()),
      `a mismatched store answered differently from withdrawing ${card.card}`);

    // THE READER, asked with a well-formed query. It refuses, it never opens its
    // store, and what it hands back IS the gate's refusal — so the reason the
    // reader gives and the reason the gate gives are the same string, because
    // they are the same object.
    const readers = await readersOfTree(target);
    const answered = await readers[card.reader](card.query);
    const refusal = tree[card.fallback]();
    assert.equal(answered.reason_id, refusal.reason_id,
      `${card.card}: the reader and the gate name different reasons for one unbound card`);
    assert.equal(digest(answered), digest(refusal),
      `${card.card}: the reader answered something other than the gate's own refusal`);
    for (const field of ["card_ref", "store_ref", "ruling_decision_ref", "query_digest"])
      assert.equal(Object.hasOwn(answered, field), false,
        `${card.card}: the reader answered with ${field}, so it acted on the ruling`);
    assert.equal(answered.decision, "refuse");
    assert.equal(answered.status, "unavailable");

    // THE AGREEMENT, in one line: what the gate says about this seam is what the
    // reader did about it.
    assert.equal(emitted[card.bound], readerReachedItsStore(answered),
      `${card.card}: the gate and the reader disagree about whether the seam is bound`);

    // MUTATION CONTROL. The same mismatched ruling against a tree whose gate
    // asks the ruling table directly again — the predicate this correction
    // deleted. The gate reports the seam BOUND, the reader still never reaches
    // its store, and the line above is what catches that: with the divergence
    // back, the two sides no longer agree.
    const control = stageTree([], { mismatchedCards: [index], divergentGate: true });
    assert.ok(readFileSync(join(control, GATE_MODULE_FILE), "utf8")
      .includes(DIVERGENT_PREDICATE_LINES[0].trim()),
      "the control did not reintroduce the old predicate, so it proves nothing");
    const controlEmitted = await (await gateOfTree(control)).emitGateZeroOutcome();
    const controlAnswered = await (await readersOfTree(control))[card.reader](card.query);
    assert.equal(controlEmitted[card.bound], true,
      `${card.card}: the control's gate did not report the mismatched ruling as bound`);
    assert.equal(readerReachedItsStore(controlAnswered), false,
      `${card.card}: the control's reader acted on a ruling naming another store`);
    assert.notEqual(controlEmitted[card.bound], readerReachedItsStore(controlAnswered),
      `${card.card}: the control reproduced no disagreement, so the clause above cannot fail`);
  }
});

// ---------------------------------------------------------------------------
// THE CLOSED UNION, SWEPT OVER EVERY BRANCH-OWNED STRING — the review's second
// finding. The old sweep read a reduced exact-match set, so a new value carrying
// `read` or `commit` passed.
//
// WHAT IS SWEPT AND WHAT IS NOT, because on this surface that boundary is the
// whole design. Main's own vocabulary is saturated with union words — the seam
// names end in `-reader`, `request_read` and `caller_evidence_admitted` are
// fields of every answer, `predecessor_set_incomplete` is a registered reason —
// and renaming them would change what Gate Zero says to every caller, which
// amendment 3 of 2026-09-12 puts out of scope for a slice that forwards them.
//
// AMENDMENT 5 OF 2026-09-12 SAYS HOW THAT BOUNDARY MAY BE DRAWN, and the fourth
// correction redraws it: the unit of pass-through is THE VALUE, not the file and
// not a category, and a test may exempt only an explicit list of values it
// PROVES stood on origin/main before this PR. "Historical strings" as a class is
// not a list and is not allowed to be one.
//
// SO THE LIST IS THE PRE-PR COMMIT'S, READ OUT OF GIT AND COMMITTED AS VALUES.
// The fourth correction read it from `origin/main` at test time, and the fifth
// review named what that costs: the ref moves, so the merge of this very PR
// turns the baseline into this PR's own values and empties the diff the staging
// depended on. The commit 229980a5 — the merge-base, the last state that
// predates PR 1004 — does not move. Its gate's whole vocabulary is committed in
// `gate-zero-pre-pr-baseline.v5.json`, digest-pinned in the helper, and
// REGENERATED below from `git show 229980a5:<path>` wherever that commit is
// reachable. An author's pinned digest could only ever prove what the author
// believed; a digest over values the commit itself still answers with proves
// what the commit says. EVERY VALUE NOT IN THAT SET IS SWEPT WITH NO EXEMPTION
// AT ALL, including each of the eight this branch declares below.
// ---------------------------------------------------------------------------

// The closed union is declared ONCE, near the top of this file, and the sweep
// below reads that one — it used to carry its own verbatim copy, which the
// 2026-09-12 merge collapsed. `PRIVILEGED_WORDS.length` is asserted to be 28
// there, so a word dropped from the union turns a test red rather than quietly
// narrowing both sweeps at once.

/**
 * Every way one string can carry a privileged word: it IS one, or it CONTAINS one
 * anywhere with case folded away, plus the two shapes the rule names by pattern.
 */
function privilegedWordsIn(text) {
  const folded = text.toLowerCase();
  const found = [];
  for (const word of PRIVILEGED_WORDS) {
    if (folded === word) found.push(`is ${word}`);
    else if (folded.includes(word)) found.push(`carries ${word}`);
  }
  if (/^would_/.test(folded)) found.push("would_ prefix");
  if (folded.includes("_if_authoritative")) found.push("_if_authoritative");
  return found;
}

/** Every key at every depth and every string leaf, with where it was found. */
function collectStrings(value, path, into) {
  if (Array.isArray(value)) {
    value.forEach((entry, index) => collectStrings(entry, `${path}[${index}]`, into));
    return into;
  }
  if (value !== null && typeof value === "object") {
    for (const [key, entry] of Object.entries(value)) {
      if (!into.has(key)) into.set(key, `${path}.${key} (key)`);
      collectStrings(entry, `${path}.${key}`, into);
    }
    return into;
  }
  if (typeof value === "string" && !into.has(value)) into.set(value, path);
  return into;
}

const DIGEST_SHAPE = /^sha256:[0-9a-f]{64}$/;

/**
 * Every string a module namespace can hand a consumer: each export NAME, each
 * key and string leaf of each exported value, and the RETURN of every callable
 * export.
 *
 * TWO RETURNS ARE NORMALIZED, AND THE NORMALIZATION IS PROVED. The policy digest
 * is a hex identity — it carries no vocabulary at all, and it is asserted to BE
 * the digest of the preimage before it is skipped. The canonical bytes are that
 * same preimage serialized, asserted to round-trip and to digest to the same
 * value, and then walked as the STRUCTURE they stand for — so the preimage's own
 * strings are swept, once, rather than a serialization of them being swept as one
 * enormous string.
 */
function surfaceVocabulary(namespace, label) {
  const into = new Map();
  for (const [name, value] of Object.entries(namespace)) {
    if (!into.has(name)) into.set(name, `${label}.${name} (export name)`);
    if (typeof value !== "function") { collectStrings(value, `${label}.${name}`, into); continue; }
    // THE ONE EXPORT THAT TAKES AN ARGUMENT, and the argument is required — it
    // throws rather than interpreting a missing packet, which is the property
    // the falsifiers above exist to hold. So it is walked over a caller-shaped
    // packet: the finding's whole vocabulary is swept, and the bytes are ones a
    // caller could actually supply.
    if (name === "v5A02GateZeroR7Presence") {
      collectStrings(value(r7PacketLike()), `${label}.${name}(packet)`, into);
      continue;
    }
    const answer = value();
    if (typeof answer !== "string") {
      collectStrings(answer, `${label}.${name}()`, into);
      continue;
    }
    const preimageDigest = digest(namespace.v5A02GateZeroPolicyPreimage());
    if (DIGEST_SHAPE.test(answer)) {
      assert.equal(answer, preimageDigest, `${label}.${name} is not the preimage's digest`);
      continue;
    }
    const parsed = JSON.parse(answer);
    assert.equal(JSON.stringify(parsed), answer, `${label}.${name} is not canonical bytes`);
    assert.equal(digest(parsed), preimageDigest, `${label}.${name} is not the preimage`);
    collectStrings(parsed, `${label}.${name}() parsed`, into);
  }
  return into;
}

/**
 * EVERY STRING THE PRE-PR GATE HANDED A CONSUMER — the explicit list the
 * amendment allows a test to exempt, and the only one. It is produced by the
 * same walker over the same export surface, so a value is exempt exactly when
 * the commit that predates this PR already answered with it.
 *
 * The values are committed; the digest that authenticates them is pinned in the
 * helper; the test below regenerates them from the commit itself. Nothing here
 * reads a ref that can move under it.
 */
assert.equal(digest(PRE_PR_BASELINE), PRE_PR_BASELINE_DIGEST,
  "the committed pre-PR baseline is not the snapshot this suite pins");

const PRE_PR_VOCABULARY = new Set(PRE_PR_BASELINE.surface_vocabulary);
assert.ok(PRE_PR_VOCABULARY.size > 0, "the pre-PR gate handed back no vocabulary at all");
assert.equal(PRE_PR_VOCABULARY.size, PRE_PR_BASELINE.surface_vocabulary.length,
  "the snapshot lists a value twice, so it is not the set its digest stands for");

test("BASELINE: the pre-PR vocabulary is that commit's own, regenerated from git", async () => {
  // THE DIGEST HALF RUNS EVERYWHERE, and these are its mutation controls: a
  // snapshot edited to admit one more string, or to name another commit, is a
  // different snapshot and the pin says so.
  assert.equal(digest(PRE_PR_BASELINE), PRE_PR_BASELINE_DIGEST);
  assert.notEqual(
    digest({ ...PRE_PR_BASELINE,
      surface_vocabulary: [...PRE_PR_BASELINE.surface_vocabulary, "would_join_exactly"] }),
    PRE_PR_BASELINE_DIGEST,
    "a snapshot with a string added to it digests the same, so the pin proves nothing");
  assert.notEqual(digest({ ...PRE_PR_BASELINE, commit: "0".repeat(40) }),
    PRE_PR_BASELINE_DIGEST,
    "a snapshot naming a different commit digests the same");

  // THE REGENERATION HALF IS SKIPPABLE ONLY WHERE THE COMMIT IS GENUINELY GONE,
  // and the probe is proved to answer false rather than to answer false always.
  assert.equal(commitReachable("0".repeat(40)), false,
    "the reachability probe calls a commit that cannot exist reachable");
  if (!prePrCommitReachable()) {
    console.log(`REGENERATION SKIPPED: ${PRE_PR_COMMIT} is not in this checkout`);
    return;
  }

  const regenerated =
    [...surfaceVocabulary(await gateOfTree(stagePrePrTree()), "pre-pr").keys()].sort();
  assert.deepEqual(regenerated, [...PRE_PR_BASELINE.surface_vocabulary],
    "the committed pre-PR vocabulary is not what that commit's gate answers with");
  // NON-VACUOUS, three ways: the walk found a real surface, it would have caught
  // a snapshot one string wide of the commit, and the tree it walked is the
  // commit's rather than this branch's wearing its name.
  assert.ok(regenerated.length > 100,
    `the pre-PR gate answered with only ${regenerated.length} strings`);
  assert.notDeepEqual(regenerated,
    [...PRE_PR_BASELINE.surface_vocabulary, "would_join_exactly"].sort(),
    "the comparison above accepts a vocabulary with a string added to it");
  assert.notDeepEqual([...new Set(surfaceVocabulary(surface, "live").keys())].sort(), regenerated,
    "the staged pre-PR tree answers exactly what this branch answers, so it is not the commit's");
  for (const added of BRANCH_ADDED_SURFACE_STRINGS)
    assert.equal(PRE_PR_VOCABULARY.has(added), false,
      `${added} is declared as this branch's but stands in the pre-PR baseline`);
});

/**
 * Every string this branch adds to the UNRULED surface, and nothing else may be
 * added without appearing here. Four are card 9 — a charter ref, its decision,
 * and the two field names that carry them — and the fifth is the reason id the
 * per-card refusal needed.
 *
 * IT SHRANK ON 2026-09-12. `311a9af5…`, the two `r7_entry_amendment_*` names and
 * `r7_amendment_decision_ref` were this branch's while PR 1009 was open; 1009
 * then merged carrying the amendment and its ref, so all four are the pre-PR
 * commit's strings now and declaring any of them would be claiming somebody
 * else's work. The branch still REPORTS the ref at the top of the emitted
 * answer, which is a field this branch adds over a string it did not invent —
 * and the strip list above is where that addition is accounted for.
 *
 * THIS LIST EXEMPTS NOTHING. The only exemption is the pre-PR commit's own
 * vocabulary, committed as values and regenerated from that commit above, and
 * each of these is asserted to be ABSENT from it, to be present on the surface,
 * and to be swept like everything else — so a declared string that failed the
 * sweep fails here rather than riding the declaration in.
 */
const BRANCH_ADDED_SURFACE_STRINGS = Object.freeze([
  "8a1dad08-8707-4bb0-a159-c2831a00cea2",
  "charter:reviewer",
  "oracle_seat_charter_decision_ref",
  "oracle_seat_charter_ref",
  // Card 9's staffing half, added 2026-09-13. TWO FIELD NAMES AND NO VALUES,
  // which is the whole shape of this addition on the unruled surface: the staged
  // tree unstaffs the seat the way main shipped it, so both fields are null
  // there and neither the holder ref nor the staffing decision id reaches the
  // vocabulary. They reach it on the SHIPPED surface, and the seat's own
  // mutation control is where that is asserted.
  "oracle_seat_holder_ref",
  "oracle_seat_staffing_decision_ref",
  "scheduler_canary_seam_unavailable",
  // THE PRODUCER'S EIGHT REASON IDS, added by this slice. They reach the UNRULED
  // surface because the closed reason registry is an exported constant — the
  // vocabulary a consumer can be handed is the whole registry, whether or not a
  // given tree can reach a given member. Nine of them are the producer's own
  // closed list, re-registered here so the gate can express a refusal the
  // producer can reach; the tenth is the gate's, for the two synchronous reads
  // once a join is something the emission produces rather than something a query
  // returns. Every one of them is swept for the union like any other addition —
  // being declared buys exemption from NOTHING, which is why "unpasted" and
  // "non_green" are not among them: both carried a union word, and both were
  // renamed rather than declared.
  // THE TWO ADDED 2026-09-12 split the producer's absence vocabulary: git
  // metadata it cannot resolve, and a sealed file that is not on disk, stop
  // sharing an id with a ruled row that is genuinely absent. Both were checked
  // against the union like every other addition, which is why HEAD's commit
  // object is named `head_revision_object` inside them.
  "gate_zero_candidate_metadata_absent",
  "gate_zero_evidence_unavailable",
  "gate_zero_gate_graph_clause_failed",
  "gate_zero_join_is_produced_not_queried",
  "gate_zero_negative_admission_unproved",
  "gate_zero_predecessor_clause_failed",
  "gate_zero_producer_identity_refused",
  "gate_zero_run_binding_unnamed",
  "gate_zero_scheduler_clause_failed",
  "gate_zero_sealed_artifact_absent",
  // THE RE-FROZEN r7 PACKET DIGEST, added 2026-09-13 by the consumer-gate-receipt
  // digest amendment. It is a VALUE and not a field name, and it is declared for
  // the same reason every other addition is: it stands on the unruled surface as
  // `producer_registration.r7_packet_sha256` and it did not stand in the pre-PR
  // baseline, whose copy of that field is the packet before this amendment.
  "4379c60e9a4fefbcf044f4bc5a34e5a95c90f77b9adf348b6da7475e17d5e6d7",
]);

/**
 * THE SENTENCES A STAFFED SEAT ANSWERS WITH WHILE A READER CARD IS NOT RULED —
 * the combination no staged tree used to produce, because every staged tree
 * unstaffs the seat by default. The sweep below walks the trees that reach them,
 * and this list is how that walk is proved non-vacuous.
 *
 * THE LIST MOVED WITH THIS SLICE, and the reason is worth stating rather than
 * quietly re-pinning. A staffed seat now BINDS the producer seam — that is the
 * seam's whole binding condition — so "the producer seam is unbuilt" is no
 * longer something a staffed seat can say, under any combination of withdrawn
 * cards. What a staffed seat with a withdrawn reader answers instead is the
 * producer's own refusal, and one gate sentence about it. The four sentences
 * that used to be here are not deleted from the module: they moved to the
 * fail-closed branch where a staffed seat's holder does not resolve, which no
 * staged tree can reach because the holder always does.
 */
const STAFFED_WITHDRAWN_SENTENCES = Object.freeze([
  "the bound producer refused over the rows the three ruled evidence seams returned, so there is nothing to sign",
  "this module was not called inside an authenticated call, so there is no execution context to derive a producer identity from",
]);

test("SWEEP: the closed union, over every branch-owned string the surface hands back", async () => {
  // THE PRE-PR COMMIT'S OWN LIST, committed as values and regenerated from that
  // commit by the baseline test above rather than read off a ref that moves.
  const main = PRE_PR_VOCABULARY;

  // EACH DECLARED ADDITION IS PROVED TO BE AN ADDITION. A string that already
  // stood before this PR is not this branch's to declare, and a declaration
  // cannot be used to bless a privileged one: both halves are asserted before
  // anything is skipped.
  for (const added of BRANCH_ADDED_SURFACE_STRINGS) {
    assert.equal(main.has(added), false,
      `${added} is declared as this branch's but already stands in the pre-PR baseline`);
    assert.deepEqual(privilegedWordsIn(added), [], `the declared addition ${added} is privileged`);
  }

  const unruled = await gateOfTree(stageTree());
  const unruledVocabulary = surfaceVocabulary(unruled, "unruled");
  for (const added of BRANCH_ADDED_SURFACE_STRINGS)
    assert.ok(unruledVocabulary.has(added), `${added} is declared but is not on the surface`);
  // AND NOTHING ELSE IS ADDED TO THE UNRULED SURFACE. The pre-PR list plus
  // exactly the declared eight is the whole of it, so a ninth addition is named
  // here rather than discovered by the sweep below.
  assert.deepEqual(
    [...unruledVocabulary.keys()].filter(text => !main.has(text)).sort(),
    [...BRANCH_ADDED_SURFACE_STRINGS].sort(),
    "the unruled surface carries a string that is neither the pre-PR baseline's nor declared");

  // Every tree this branch can be switched into, the shipped one first — AND
  // BOTH SEAT STATES OF EACH, which the 2026-09-12 review's second finding is
  // about. `stageTree` unstaffs the seat unless asked otherwise, so the sweep
  // used to see the staffed seat only on the tree where all three readers are
  // bound. Four of this branch's sentences are answered only when a seat IS
  // staffed and a reader card is NOT ruled, and nothing looked at them.
  const namespaces = [["live", surface]];
  for (const [index, card] of READER_CARDS.entries()) {
    namespaces.push([`withdrawn:${card.key}`, await gateOfTree(stageTree([index]))]);
    namespaces.push([`withdrawn-staffed:${card.key}`,
      await gateOfTree(stageTree([index], { staffedSeat: true }))]);
  }
  namespaces.push(["unruled-staffed",
    await gateOfTree(stageTree(undefined, { staffedSeat: true }))]);
  namespaces.push(["unruled", unruled]);

  const branchOwned = new Map();
  const sweep = (vocabulary, label) => {
    for (const [text, where] of vocabulary) {
      // THE ONE EXEMPTION, AND IT IS A LIST OF VALUES THE PRE-PR COMMIT ALREADY
      // ANSWERED WITH — not a category, not a file, not a shape.
      if (main.has(text)) continue;
      branchOwned.set(text, `${label} ${where}`);
      assert.deepEqual(privilegedWordsIn(text), [],
        `${label} ${where} carries a privileged word: ${JSON.stringify(text)}`);
    }
  };
  for (const [label, namespace] of namespaces)
    sweep(surfaceVocabulary(namespace, label), label);

  // AND OVER EVERY CALLER-CONTROLLED SHAPE, including the reviewer's one-gate
  // construction: a sweep that only ever saw the no-argument answer would not
  // have seen a value a request could have put there.
  for (const [name, fn] of PUBLIC_FUNCTIONS_OVER_CALLER_INPUT)
    for (const shape of callerControlledShapes())
      sweep(collectStrings(await fn(shape), `${name}(shape)`, new Map()), name);

  // NON-VACUOUS: the sweep has branch-owned strings to look at, and every one of
  // the declared eight is among them — the exemption list is the pre-PR
  // commit's, so nothing this branch declares can duck the sweep by being
  // declared. Fourteen is the
  // floor this branch ships: the eight declared additions, plus the opaque
  // decided_by token and the five sentences the ruled and part-ruled refusals
  // answer with, which appear only on a ruled tree.
  for (const added of BRANCH_ADDED_SURFACE_STRINGS)
    assert.ok(branchOwned.has(added), `${added} was declared but never swept`);
  // AND THE FOUR SENTENCES THE EXTENSION EXISTS TO REACH ARE REACHED. Without
  // this, a staged tree that quietly stopped staffing the seat would shrink the
  // sweep back to what it was and every assertion above would still pass.
  for (const sentence of STAFFED_WITHDRAWN_SENTENCES)
    assert.ok(branchOwned.has(sentence),
      `a staffed-and-withdrawn sentence was never swept: ${JSON.stringify(sentence)}`);
  assert.ok(branchOwned.size >= 14,
    `the sweep found only ${branchOwned.size} branch-owned strings: ${[...branchOwned.keys()]}`);
});

test("SWEEP: the sweep catches what it exists to catch", () => {
  // Exact, substring, case, and the two patterns the rule names.
  assert.deepEqual(privilegedWordsIn("ok"), ["is ok"]);
  assert.ok(privilegedWordsIn("ruled_readers_bound_producer_unstaffed").includes("carries read"),
    "the name this branch replaced would pass the sweep");
  assert.ok(privilegedWordsIn("which COMMIT a run stands on").includes("carries commit"));
  // `would_` and `_if_authoritative` are the two patterns the rule names; `join`
  // on its own is not a union word, and the sweep must not pretend it is.
  assert.deepEqual(privilegedWordsIn("would_join_exactly_if_authoritative"),
    ["would_ prefix", "_if_authoritative"]);
  assert.deepEqual(privilegedWordsIn("joins_exactly"), ["is joins_exactly"]);
  assert.deepEqual(privilegedWordsIn("evidence_seams_bound_producer_unstaffed"), [],
    "the replacement name is itself privileged");
  // The walker reaches keys, leaves, nested arrays and a callable's return.
  const reached = collectStrings({ outer: [{ inner: "leaf" }] }, "$", new Map());
  for (const text of ["outer", "inner", "leaf"]) assert.ok(reached.has(text), text);
  const returned = surfaceVocabulary({ answered: () => ({ nested: ["value"] }) }, "probe");
  for (const text of ["answered", "nested", "value"]) assert.ok(returned.has(text), text);
  // AND THE BOUNDARY IS LOAD-BEARING: the live surface does carry union words, in
  // the pre-PR vocabulary, so a sweep with no boundary would fail and one with a
  // boundary that admitted everything would prove nothing.
  const privileged = [...surfaceVocabulary(surface, "live").keys()]
    .filter(text => privilegedWordsIn(text).length > 0);
  assert.ok(privileged.length > 0,
    "nothing on the surface carries a union word, so the boundary above is untested");
});

// ---------------------------------------------------------------------------
// AMENDMENT 2's CLOSED SHAPE, FOR EVERY PUBLIC CALLABLE THIS CHANGE TOUCHED.
// ---------------------------------------------------------------------------

/** Every export of this module that is a function. All five changed here. */
const PUBLIC_CALLABLES = Object.freeze([
  "emitGateZeroOutcome",
  "readGateGraphAssurance",
  "readGateZeroPredecessorJoin",
  "v5A02GateZeroPolicyCanonicalBytes",
  "v5A02GateZeroPolicyDigest",
  "v5A02GateZeroPolicyPreimage",
  // Re-exported from the registration module, which owns it: one byte verifier,
  // not two. It is enumerated here because it is reachable HERE, and it wears
  // the same closed shape the six above do.
  "v5A02GateZeroR7Presence",
]);

/**
 * Clauses (a), (b) and (d) of amendment 2, as a list of findings rather than a
 * pile of assertions — so the same function can be run against something that
 * FAILS it and prove the check is load-bearing.
 */
function shapeFindings(fn) {
  const found = [];
  if (typeof fn !== "function") return ["not a function"];
  // (a) an arrow or a bound function: no `prototype`, and the engine refuses to
  // construct it before a line of module code runs.
  if (Object.hasOwn(fn, "prototype")) found.push("carries a prototype");
  try { Reflect.construct(fn, []); found.push("is constructable"); } catch { /* the engine's */ }
  // (b) an own Symbol.hasInstance DATA property that answers false without
  // touching the left operand.
  const descriptor = Object.getOwnPropertyDescriptor(fn, Symbol.hasInstance);
  if (descriptor === undefined) found.push("has no own Symbol.hasInstance");
  else {
    if (!Object.hasOwn(descriptor, "value")) found.push("hasInstance is an accessor");
    if (descriptor.writable !== false) found.push("hasInstance is writable");
    if (descriptor.configurable !== false) found.push("hasInstance is configurable");
    if (descriptor.enumerable !== false) found.push("hasInstance is enumerable");
    if (typeof descriptor.value !== "function") found.push("hasInstance is not callable");
  }
  // The operand is never read: every trap on it throws, and `instanceof` is still
  // false rather than the caller's own error.
  const hostile = new Proxy({}, {
    get() { throw new Error("the operand was read"); },
    getPrototypeOf() { throw new Error("the operand's chain was walked"); },
  });
  try {
    if (hostile instanceof fn) found.push("answered instanceof with true");
  } catch (error) { found.push(`instanceof touched the operand: ${error.message}`); }
  // (d) no Proxy wrapper around the export.
  if (types.isProxy(fn)) found.push("is a Proxy");
  if (!Object.isFrozen(fn)) found.push("is not frozen");
  return found;
}

/**
 * EVERY FUNCTION EXPORT OF A MODULE NAMESPACE, AND WHAT EACH ONE FAILS.
 *
 * Enumerated rather than listed, so a callable added to either module is
 * measured without anyone remembering to name it here, and labelled by module
 * and export so a failure says which one.
 */
function moduleShapeFindings(namespace, label) {
  return Object.entries(namespace)
    .filter(([, value]) => typeof value === "function")
    .flatMap(([name, value]) => shapeFindings(value).map(one => `${label}.${name}: ${one}`));
}

/**
 * THE HELPER MODULE'S OWN EXPORTS ARE IN SCOPE, which the seventh review had to
 * point out. Amendment 2's shape is a property of an exported callable, not of a
 * directory: a test helper imported by two suites is as reachable a public
 * surface as src/, and all four of its callables were constructable.
 */
const HELPER_CALLABLES = Object.freeze([
  "commitReachable",
  "prePrCommitReachable",
  "releasePrePrTrees",
  "stagePrePrTree",
]);

test("SHAPE: every public callable wears amendment 2's closed shape", async () => {
  for (const name of PUBLIC_CALLABLES) {
    assert.ok(Object.hasOwn(surface, name), `${name} is not exported`);
    assert.deepEqual(shapeFindings(surface[name]), [], name);
  }
  // Every function export is on the list, so a new one cannot skip the shape.
  assert.deepEqual(Object.entries(surface)
    .filter(([, value]) => typeof value === "function").map(([name]) => name).sort(),
    [...PUBLIC_CALLABLES].sort());
  assert.deepEqual(moduleShapeFindings(surface, "gate-zero-assurance.v5.js"), []);

  // AND THE PRE-PR BASELINE HELPER, export by export, enumerated the same way.
  assert.deepEqual(moduleShapeFindings(prePrBaselineHelper, "pre-pr-baseline helper"), []);
  assert.deepEqual(Object.entries(prePrBaselineHelper)
    .filter(([, value]) => typeof value === "function").map(([name]) => name).sort(),
    [...HELPER_CALLABLES].sort(),
    "the helper module's callables are not the four this shape test names");

  // THE MUTATION CONTROL FOR THE ENUMERATION: a module written the way those
  // four were written — a plain `export function` declaration — is red, and it
  // is red on the three clauses that separate a declaration from a bound arrow.
  // A control that only fed `shapeFindings` a function would not prove the
  // ENUMERATION reaches a module's exports, so this one is a real module.
  const cache = fileURLToPath(new URL("../node_modules/.cache/", import.meta.url));
  mkdirSync(cache, { recursive: true });
  const controlDirectory = mkdtempSync(join(cache, "gate-zero-helper-shape-"));
  stagedTrees.push(controlDirectory);
  writeFileSync(join(controlDirectory, "control.mjs"),
    "const closed = fn => { Object.defineProperty(fn, Symbol.hasInstance,"
    + " { value: () => false, writable: false, enumerable: false, configurable: false });"
    + " return Object.freeze(fn); };\n"
    + "export const alreadyClosed = closed((() => undefined).bind(null));\n"
    + "export function commitReachable() { return false; }\n");
  const control =
    await import(pathToFileURL(join(controlDirectory, "control.mjs")).href);
  const controlFindings = moduleShapeFindings(control, "control");
  for (const clause of ["carries a prototype", "is constructable", "has no own Symbol.hasInstance"])
    assert.ok(controlFindings.includes(`control.commitReachable: ${clause}`),
      `the control is not red on "${clause}": ${controlFindings}`);
  // ISOLATED: the closed export in the same module is clean, so the control
  // measures the plain declaration and not the enumeration failing wholesale.
  assert.deepEqual(controlFindings.filter(one => !one.startsWith("control.commitReachable:")), []);
  // NON-VACUOUS, three ways: a plain function, a class and a Proxy over a closed
  // callable each fail, and they fail on the clauses that separate them.
  const plain = shapeFindings(function ordinary() {});
  assert.ok(plain.includes("carries a prototype"), plain);
  assert.ok(plain.includes("is constructable"), plain);
  assert.ok(plain.includes("has no own Symbol.hasInstance"), plain);
  assert.ok(shapeFindings(class Refused {}).length >= 3);
  assert.ok(shapeFindings(new Proxy(surface.readGateGraphAssurance, {})).includes("is a Proxy"));
  // A hasInstance that walks the operand is caught rather than crashing the test.
  const walker = Object.defineProperty(() => undefined, Symbol.hasInstance, {
    value: operand => Object.getPrototypeOf(operand) !== null,
    writable: false, enumerable: false, configurable: false,
  });
  assert.ok(shapeFindings(Object.freeze(walker)).some(one => one.startsWith("instanceof touched")));
});

/**
 * NO CALLER-SUPPLIED READER, AND NO ROUTE TO ONE — proved with V8's own module
 * parser and with behaviour, and no longer with a regex over source text.
 *
 * The standing rule of 2026-09-11 requires a real parser for a static import
 * guard, and the 2026-09-12 review found this test still matching source text.
 * Each clause moved to the instrument that actually decides it:
 *
 *   * WHAT THIS MODULE IMPORTS — `dependencySpecifiers` off a parsed module
 *     record, which a dynamic or concatenated specifier cannot hide from.
 *   * WHETHER A BINDING DOOR IS EXPORTED — the module namespace's own key list,
 *     asserted exactly above and re-asked here by name; an `export function
 *     bindReader` is a new key and fails both.
 *   * WHETHER THE ENVIRONMENT CAN BIND ONE — a copy of the tree imported in a
 *     child process under hostile variables, whose three answers must be the
 *     bytes this process gets without them. That is the property the old
 *     `/process\.env/` grep was standing in for, and it holds however the module
 *     spells the lookup.
 */
test("SURFACE: the readers are imported, never handed in", () => {
  const directory = fileURLToPath(new URL("../src", import.meta.url));
  const specifiers = moduleImports(directory)["gate-zero-assurance.v5.js"];
  assert.ok(specifiers.includes("./gate-zero-seam-readers.v5.js"),
    "the readers are not imported by this module");
  assert.equal(specifiers.includes("./gate-zero-seam-rulings.v5.js"), false,
    "the gate imports the ruling table again, which is how its predicate drifted from the reader's");
  // And the binding predicate arrives by the INTERNAL path both files import,
  // which is the third correction's finding: sharing it through the reader's
  // public namespace made the reader promise a fifth name.
  assert.ok(specifiers.includes("./internal/gate-zero-seam-binding.v5.js"),
    "the gate no longer imports the shared ruling predicate it binds seams on");
  for (const name of Object.keys(surface))
    assert.equal(/bind/i.test(name), false, `${name} is a binding door on the surface`);
  // Every exported callable still takes at most one argument, and none of them
  // is a reader.
  for (const [, fn] of PUBLIC_FUNCTIONS_OVER_CALLER_INPUT)
    assert.ok(fn.length <= 1, "an export takes a second argument");
});

test("SURFACE: no environment variable binds a seam", async () => {
  // A FRESH PROCESS, because a module reads its environment while it evaluates:
  // the variables are set before the import, not after it.
  const script = `
    const { pathToFileURL } = require("node:url");
    import(pathToFileURL(process.argv[1]).href).then(async gate => {
      const { digest } = await import(pathToFileURL(process.argv[2]).href);
      process.stdout.write(JSON.stringify({
        join: digest(gate.readGateZeroPredecessorJoin()),
        graph: digest(gate.readGateGraphAssurance()),
        emitted: digest(await gate.emitGateZeroOutcome()),
      }));
    }).catch(error => { process.stderr.write(String(error)); process.exit(1); });
  `;
  const gatePath = fileURLToPath(new URL("../src/gate-zero-assurance.v5.js", import.meta.url));
  const trustPath = fileURLToPath(new URL("../src/artifact-trust.js", import.meta.url));
  const hostile = {
    ...process.env,
    CARR_GATE_ZERO_PRODUCER: "allow",
    CARR_GATE_ZERO_PREDECESSOR_OUTCOME_READER: "bound",
    CARR_GATE_ZERO_SCHEDULER_READER: "bound",
    CARR_GATE_ZERO_GATE_CONCLUSION_READER: "bound",
    GATE_ZERO_PASSABLE: "true",
    SEAM_RULING_DECISION_ID: "00000000-0000-4000-8000-000000000000",
  };
  const run = spawnSync(process.execPath, ["-e", script, gatePath, trustPath],
    { encoding: "utf8", env: hostile });
  assert.equal(run.status, 0, `the child failed: ${run.stderr}`);
  assert.deepEqual(JSON.parse(run.stdout), {
    join: digest(readGateZeroPredecessorJoin()),
    graph: digest(readGateGraphAssurance()),
    emitted: digest(await emitGateZeroOutcome()),
  }, "an environment variable moved an answer");
});

test("SURFACE: a producer cannot be handed in as a second argument", () => {
  assert.throws(
    () => emitGateZeroOutcome(cleanJoin(), { emitOutcome: () => ({ passable: true }) }),
    error => error instanceof V5BoundaryError &&
      error.code === "gate_zero_producer_is_not_an_argument");
});

// ---------------------------------------------------------------------------
// The classifier entry is unreachable from production. Parsed, not grepped.
// ---------------------------------------------------------------------------

/**
 * V8's own ESM parser, through vm.SourceTextModule in a child process — a real
 * parser, and the one Node itself uses, rather than a regex over source text.
 * `dependencySpecifiers` is the module record's own import list, so a dynamic
 * concatenated specifier cannot hide from it the way it could from a grep.
 */
function moduleImports(directory) {
  const script = `
    const { readdirSync, readFileSync } = require("node:fs");
    const { join } = require("node:path");
    const vm = require("node:vm");
    const dir = process.argv[1];
    const out = {};
    for (const name of readdirSync(dir).sort()) {
      if (!name.endsWith(".js")) continue;
      const source = readFileSync(join(dir, name), "utf8");
      out[name] = new vm.SourceTextModule(source, { identifier: name }).dependencySpecifiers;
    }
    process.stdout.write(JSON.stringify(out));
  `;
  const run = spawnSync(process.execPath, ["--experimental-vm-modules", "-e", script, directory],
    { encoding: "utf8" });
  assert.equal(run.status, 0, `the module parser failed: ${run.stderr}`);
  return JSON.parse(run.stdout);
}

test("ISOLATION: src holds no test-only entry, and none of it reaches the test tree", () => {
  const directory = fileURLToPath(new URL("../src", import.meta.url));
  const strays = readdirSync(directory).filter(name => /\.(testonly|testhelper)\./.test(name));
  assert.deepEqual(strays, [], "a test-only entry is sitting in the production source directory");

  const imports = moduleImports(directory);
  // The parser must have seen this slice at all, or the scan proves nothing.
  assert.ok(Object.hasOwn(imports, "gate-zero-assurance.v5.js"));
  assert.ok(Object.keys(imports).length > 100, "every module in src must have been parsed");

  const offenders = Object.entries(imports)
    .filter(([, specifiers]) => specifiers.some(one =>
      one.includes("/test/") || one.startsWith("../test") ||
      one.includes(".testonly.") || one.includes(".testhelper.")))
    .map(([name]) => name);
  assert.deepEqual(offenders, [], "a production module reached into the test directory");

  // And specifically: the public surface imports seven modules, none of them this
  // slice's classifiers. The fifth is the producer registration — a frozen
  // constant table plus the one byte verifier, reaching only the sha256 and
  // canonical-JSON helpers that verifier needs and the boundary error it throws;
  // the sixth is the ruled readers, added on 2026-09-12; the seventh is the
  // shared ruling predicate on its internal path — and the ruling table is NOT
  // among them, which is the PR 1004 re-review's finding: the binding condition
  // reaches this module through the one predicate the readers ask too, so there
  // is one predicate over that table instead of two that can disagree.
  // THE ORDER MATTERS AND IS ASSERTED: the producer registration must be
  // instantiated before the readers, because the readers read one of its
  // constants through this module's re-export at their own module scope, and
  // the two files form a cycle. Move the reader import above it and the import
  // order that starts at the gate hits a temporal dead zone.
  assert.deepEqual(imports["gate-zero-assurance.v5.js"],
    ["./artifact-trust.js", "./closed-callable.js", "./global-boundaries.v5.js", "./identity.js",
      "./benchmark-minimum.v5.js", "./gate-zero-producer-registration.v5.js",
      "./gate-zero-seam-readers.v5.js", "./internal/gate-zero-seam-binding.v5.js",
      // THE EIGHTH, AND IT IS LAST ON PURPOSE. The producer imports the readers,
      // the readers import this gate, and this gate imports the producer — one
      // cycle on top of the one that was already here. Everything that crosses
      // it is a thunk read at call time, and the producer is imported after the
      // registration and the readers so that whichever module a process starts
      // at, nothing reads a `const` binding still in its temporal dead zone.
      "./gate-zero-producer.v5.js"]);
  // AND THE PRODUCER'S OWN GRAPH, asserted the same way: it reaches the readers
  // and the registration and nothing test-shaped, and it does NOT import the
  // gate — a producer that imported the surface it answers for would be able to
  // read its own verdict back.
  // THERE IS NO `node:` BUILTIN LEFT IN THIS GRAPH, and that is standing-rule
  // amendment 9 (2026-09-14) asserted structurally. The producer used to import
  // `node:fs`, `node:path`, `node:url` and `node:zlib` to walk up for a `.git`,
  // resolve HEAD, inflate loose objects and hash the working tree — every one of
  // those a read of a CHECKOUT, which the deployed Worker does not have. The
  // candidate is build-time metadata now: `./build-stamp.js` reads the vars
  // bin/deploy-worker.sh stamped, through the same function release.js reads
  // GIT_SHA with. A `node:fs` back in this list is the regression, and it is red
  // here before any behavioural case has to catch it.
  //
  // `./gate-zero-seam-stores.v5.js` is the other addition, and it is the ONE
  // store this module opens: the candidate-build record it takes its subject
  // maker from. It is not card evidence — no clause reads it and no ruling names
  // it — and the readers suite's ISOLATION case carries the closed two-importer
  // set and the reasoning.
  //
  // `./closed-callable.js` is a UNIFICATION rather than a dependency: amendment
  // 2's closed shape used to be copied into six modules, and the fifth review
  // round found five of the copies already diverged from the shared one in the
  // same way — none of them froze the callable. There is one definition now.
  assert.deepEqual(imports["gate-zero-producer.v5.js"],
    ["./artifact-trust.js", "./build-stamp.js", "./closed-callable.js",
      "./global-boundaries.v5.js", "./identity.js",
      "./benchmark-minimum.v5.js", "./gate-zero-producer-registration.v5.js",
      "./gate-zero-seam-readers.v5.js", "./gate-zero-seam-stores.v5.js"]);
  assert.deepEqual(imports["gate-zero-producer-registration.v5.js"],
    ["./artifact-trust.js", "./global-boundaries.v5.js", "./benchmark-minimum.v5.js"]);
});

/**
 * The same privileged words, checked as VALUES ONLY. The public sweep also flags
 * any `would_` FIELD, which is right there and wrong here — a clause answers in
 * `would_` fields by design. What a clause may never do is put one of the words
 * a consumer acts on into a value, under any field name at all.
 */
function privilegedValueFindings(value, path = "$", found = []) {
  if (Array.isArray(value)) {
    value.forEach((entry, index) => privilegedValueFindings(entry, `${path}[${index}]`, found));
    return found;
  }
  if (value !== null && typeof value === "object") {
    for (const [key, entry] of Object.entries(value))
      privilegedValueFindings(entry, `${path}.${key}`, found);
    return found;
  }
  if (typeof value === "string" && CLAUSE_PRIVILEGED_VALUES.has(value)) found.push(`${path} === ${value}`);
  return found;
}

/** The words a consumer would act on, as VALUES, for the clause sweep below. */
const CLAUSE_PRIVILEGED_VALUES = new Set([
  ...PRIVILEGED_VALUES, "joins_exactly", "coverage_complete", "ok", "covered", "activated",
]);

test("VOCABULARY: no clause answer contains a privileged word as a value", () => {
  // The sibling half of this slice answered `would_derive_state_if_authoritative:
  // "operational"` — a conditional field with the privileged word sitting inside
  // it — and a reviewer was right that the value is what a consumer reads. These
  // clauses answer in booleans under `would_*` names and carry no such value; the
  // sweep is here so a later edit cannot introduce one unnoticed.
  const answers = [
    classifyGateGraph(cleanGates()),
    classifyGateZeroJoin(cleanJoin()),
    classifySchedulerCanary(cleanCanary(), cleanReadback(), SCHEDULER_OUTCOME_DIGEST),
  ];
  for (const stepRef of V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS)
    answers.push(classifyPredecessorObservation(observation(stepRef, "sweep"),
      { step_ref: stepRef, as_of_ms: Date.parse(AS_OF) }));
  for (const conclusion of V5_A02_GATE_CONCLUSIONS)
    answers.push(classifyGateGraph([
      { gate_id: "root", conclusion, depends_on: [] },
      { gate_id: "leaf", conclusion: "success", depends_on: ["root"] },
    ]));
  assert.ok(answers.length >= 8, "the clause sweep must cover the clause domain");
  for (const answer of answers)
    assert.deepEqual(privilegedValueFindings(answer), [],
      `${answer.classification ?? "clause"} answered with a privileged word`);
});

test("ISOLATION: the clause helper answers only in the conditional", () => {
  const source = readFileSync(
    fileURLToPath(new URL("./gate-zero-classifiers.v5.testhelper.mjs", import.meta.url)), "utf8");
  // No privileged result field is even spelled in the classifier's returns.
  for (const forbidden of ["decision:", "green:", "joins_exactly:", "passable:", "ok:"])
    assert.equal(source.includes(`\n    ${forbidden}`), false,
      `${forbidden} is a privileged result field`);
  for (const result of [
    classifyGateGraph(cleanGates()),
    classifyGateZeroJoin(cleanJoin()),
    classifyPredecessorObservation(observation("step:wr46-dissolution-outcome", "wr46"),
      { step_ref: "step:wr46-dissolution-outcome", as_of_ms: Date.parse(AS_OF) }),
    classifySchedulerCanary(cleanCanary(), cleanReadback(), SCHEDULER_OUTCOME_DIGEST),
  ]) {
    assert.equal(result.is_not_authority, true);
    assert.equal(result.evidence_source, V5_A02_CLASSIFIER_EVIDENCE_SOURCE);
    assert.equal(Object.hasOwn(result, "decision"), false);
  }
});

// ===========================================================================
// HALF B — THE CLASSIFIERS, CLAUSE BY CLAUSE.
// ===========================================================================

test("PREDECESSORS: the four bound predecessors match the frozen plan validator exactly", () => {
  const validator = readFileSync(
    fileURLToPath(new URL("../../tools/doctorcre-v5-review.cjs", import.meta.url)), "utf8");
  const block = validator.slice(validator.indexOf("const gateZeroExpected = ["));
  const stated = [...block.slice(0, block.indexOf("]")).matchAll(/"(step:[a-z0-9-]+)"/g)]
    .map(match => match[1]).sort();
  assert.equal(stated.length, 4, "the validator must still name four predecessors");
  assert.deepEqual([...V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS], stated);
});

test("PREDECESSORS: the scheduler step ref is one of the four", () => {
  assert.ok(V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS.includes(V5_A02_SCHEDULER_STEP_REF));
});

test("JOIN: the clean four would join exactly, and that is a shape statement", () => {
  const result = classifyGateZeroJoin(cleanJoin());
  assert.equal(result.would_join_exactly_if_authoritative, true);
  assert.equal(result.reason_id, null);
  assert.deepEqual(result.predecessors_missing, []);
  assert.deepEqual(result.predecessors_unsatisfied, []);
  assert.equal(result.is_gate_zero_pass, false, "a shape join is never a Gate Zero pass");
  assert.equal(result.is_not_authority, true);
  assert.equal(result.evidence_source, V5_A02_CLASSIFIER_EVIDENCE_SOURCE);
  assert.deepEqual(result.effects, V5_NO_EFFECTS);
});

test("JOIN: a missing predecessor refuses and names it", () => {
  const request = cleanJoin();
  request.predecessor_observations = request.predecessor_observations
    .filter(entry => entry.step_ref !== "step:wr54-backup-recovery-outcome");
  const result = classifyGateZeroJoin(request);
  assert.equal(result.would_join_exactly_if_authoritative, false);
  assert.equal(result.reason_id, "predecessor_set_incomplete");
  assert.deepEqual(result.predecessors_missing, ["step:wr54-backup-recovery-outcome"]);
});

test("JOIN: a pending predecessor is a stand-in and is named absent", () => {
  const request = cleanJoin();
  const target = request.predecessor_observations[1];
  target.state = "pending";
  target.outcome = null;
  target.outcome_digest = null;
  target.observed_at = null;
  const result = classifyGateZeroJoin(request);
  assert.equal(result.would_join_exactly_if_authoritative, false);
  assert.equal(result.predecessor_results["step:wr40-repository-outcome"].reason_id,
    "predecessor_outcome_absent");
});

test("JOIN: an accepted criteria_not_met outcome clears no milestone", () => {
  const request = cleanJoin();
  const target = request.predecessor_observations[1];
  target.outcome.criteria_state = "criteria_not_met";
  target.outcome_digest = digest(target.outcome);
  const result = classifyGateZeroJoin(request);
  assert.equal(result.would_join_exactly_if_authoritative, false);
  assert.equal(result.reason_id, "predecessor_criteria_not_met");
});

test("JOIN: a proposal pending human acceptance does not close a predecessor", () => {
  const request = cleanJoin();
  const target = request.predecessor_observations[1];
  target.outcome.acceptance_state = "pending_human_acceptance";
  target.outcome_digest = digest(target.outcome);
  assert.equal(classifyGateZeroJoin(request).reason_id, "predecessor_outcome_not_accepted");
});

test("JOIN: an outcome that does not hash to its own digest is refused", () => {
  const request = cleanJoin();
  request.predecessor_observations[1].outcome.outcome_ref = "outcome:swapped-after-hashing";
  assert.equal(classifyGateZeroJoin(request).reason_id, "predecessor_outcome_digest_mismatch");
});

test("JOIN: an outcome body naming another step cannot stand in", () => {
  const request = cleanJoin();
  const target = request.predecessor_observations[1];
  target.outcome.step_ref = "step:wr46-dissolution-outcome";
  target.outcome_digest = digest(target.outcome);
  assert.equal(classifyGateZeroJoin(request).reason_id, "predecessor_outcome_bound_to_other_step");
});

test("JOIN: one outcome record cannot close two predecessors", () => {
  const request = cleanJoin();
  // Two DIFFERENT bodies, each correctly naming its own step, but both citing
  // the SAME outcome record. Every binding check passes; only the reuse clause
  // can catch it.
  const wr40 = outcomeBody("step:wr40-repository-outcome", "wr46");
  const wr46 = outcomeBody("step:wr46-dissolution-outcome", "wr46");
  request.predecessor_observations[1] = {
    step_ref: "step:wr40-repository-outcome", state: "observed",
    outcome_digest: digest(wr40), outcome: wr40, observed_at: "2026-09-06T16:03:09Z",
  };
  request.predecessor_observations[2] = {
    step_ref: "step:wr46-dissolution-outcome", state: "observed",
    outcome_digest: digest(wr46), outcome: wr46, observed_at: "2026-09-06T16:03:09Z",
  };
  const result = classifyGateZeroJoin(request);
  assert.equal(result.would_join_exactly_if_authoritative, false);
  assert.equal(result.reason_id, "duplicate_predecessor_outcome");
  assert.equal(result.predecessor_results["step:wr46-dissolution-outcome"].reason_id,
    "duplicate_predecessor_outcome");
  assert.equal(
    result.predecessor_results["step:wr40-repository-outcome"].would_satisfy_if_authoritative, true,
    "the first citation stands; the second one is the reuse");
});

test("JOIN: an outcome observed after the reference instant is not evidence at it", () => {
  const request = cleanJoin();
  request.predecessor_observations[1].observed_at = "2026-09-12T00:00:00Z";
  assert.equal(classifyGateZeroJoin(request).reason_id, "predecessor_observed_after_reference");
});

test("JOIN: the refusal still reports the exact shapes it was shown", () => {
  const request = cleanJoin();
  request.predecessor_observations[2].outcome.criteria_state = "criteria_not_met";
  request.predecessor_observations[2].outcome_digest =
    digest(request.predecessor_observations[2].outcome);
  const cited = classifyGateZeroJoin(request).predecessor_shapes_cited;
  assert.equal(cited.length, 4);
  const wr46 = cited.find(entry => entry.step_ref === "step:wr46-dissolution-outcome");
  assert.equal(wr46.criteria_state, "criteria_not_met");
  assert.equal(wr46.would_satisfy_if_authoritative, false);
  assert.equal(wr46.reason_id, "predecessor_criteria_not_met");
  assert.equal(wr46.outcome_ref, "outcome:wr46");
});

test("JOIN: a step outside the four is unreadable, not refusable", () => {
  const request = cleanJoin();
  request.predecessor_observations[1].step_ref = "step:some-other-outcome";
  assert.throws(() => classifyGateZeroJoin(request),
    error => error instanceof V5BoundaryError && error.code === "unknown_predecessor_step");
});

test("JOIN: the same predecessor twice is unreadable", () => {
  const request = cleanJoin();
  request.predecessor_observations.push(observation("step:wr40-repository-outcome", "again"));
  assert.throws(() => classifyGateZeroJoin(request),
    error => error instanceof V5BoundaryError && error.code === "duplicate_predecessor_observation");
});

test("JOIN: an unknown field on an observation is unreadable", () => {
  const request = cleanJoin();
  request.predecessor_observations[1].verified = true;
  assert.throws(() => classifyGateZeroJoin(request),
    error => error instanceof V5BoundaryError && error.code === "unknown_field");
});

test("JOIN: a pending observation may not smuggle an outcome alongside it", () => {
  const request = cleanJoin();
  request.predecessor_observations[1].state = "pending";
  assert.throws(() => classifyGateZeroJoin(request),
    error => error instanceof V5BoundaryError && error.code === "unobserved_states_an_outcome");
});

// ---------------------------------------------------------------------------
// SCHEDULER CANARY AND READBACK.
// ---------------------------------------------------------------------------

test("SCHEDULER: the clean canary and readback would join exactly", () => {
  const result = classifySchedulerCanary(cleanCanary(), cleanReadback(), SCHEDULER_OUTCOME_DIGEST);
  assert.equal(result.would_satisfy_if_authoritative, true);
});

test("SCHEDULER: a readback of another canary does not join", () => {
  const readback = cleanReadback();
  readback.canary_id = "canary-from-last-week";
  assert.equal(classifySchedulerCanary(cleanCanary(), readback, SCHEDULER_OUTCOME_DIGEST).reason_id,
    "scheduler_readback_canary_mismatch");
});

test("SCHEDULER: a readback carrying a different digest does not join", () => {
  const readback = cleanReadback();
  readback.observed_digest = `sha256:${"b".repeat(64)}`;
  assert.equal(classifySchedulerCanary(cleanCanary(), readback, SCHEDULER_OUTCOME_DIGEST).reason_id,
    "scheduler_canary_digest_mismatch");
});

test("SCHEDULER: a readback at its own dispatch instant describes nothing running", () => {
  const canary = cleanCanary();
  const readback = cleanReadback();
  readback.observed_at = canary.dispatched_at;
  assert.equal(classifySchedulerCanary(canary, readback, SCHEDULER_OUTCOME_DIGEST).reason_id,
    "scheduler_readback_not_after_dispatch");
});

test("SCHEDULER: an absent readback is a canary nobody heard", () => {
  const readback = {
    canary_id: "canary-2026-09-11-0001", state: "absent",
    observed_at: null, observed_digest: null,
  };
  assert.equal(classifySchedulerCanary(cleanCanary(), readback, SCHEDULER_OUTCOME_DIGEST).reason_id,
    "scheduler_readback_absent");
});

test("SCHEDULER: a canary not bound to the accepted scheduler outcome does not join", () => {
  const canary = cleanCanary();
  canary.scheduler_outcome_digest = `sha256:${"c".repeat(64)}`;
  assert.equal(classifySchedulerCanary(canary, cleanReadback(), SCHEDULER_OUTCOME_DIGEST).reason_id,
    "scheduler_canary_not_bound_to_receipt");
});

test("SCHEDULER: with no accepted scheduler outcome the canary is unbindable", () => {
  assert.equal(classifySchedulerCanary(cleanCanary(), cleanReadback(), null).reason_id,
    "scheduler_canary_not_bound_to_receipt");
});

test("JOIN: a failed scheduler predecessor makes its canary unbindable inside the join", () => {
  const request = cleanJoin();
  const target = request.predecessor_observations.find(
    entry => entry.step_ref === V5_A02_SCHEDULER_STEP_REF);
  target.state = "absent";
  target.outcome = null;
  target.outcome_digest = null;
  target.observed_at = null;
  const result = classifyGateZeroJoin(request);
  assert.equal(result.would_join_exactly_if_authoritative, false);
  assert.equal(result.scheduler_canary_result.reason_id, "scheduler_canary_not_bound_to_receipt");
});

// ---------------------------------------------------------------------------
// NON-GREEN PROPAGATION. "A failed injected gate cannot claim green."
// ---------------------------------------------------------------------------

test("GRAPH: the clean graph would be green", () => {
  const result = classifyGateGraph(cleanGates());
  assert.equal(result.would_be_green_if_authoritative, true);
  assert.deepEqual(result.non_green_gates, []);
  assert.deepEqual(result.inherited_non_green_gates, []);
});

test("GRAPH: an injected failed gate makes the graph non-green and is named", () => {
  const gates = cleanGates();
  gates[2].conclusion = "failure";
  const result = classifyGateGraph(gates);
  assert.equal(result.would_be_green_if_authoritative, false);
  assert.equal(result.reason_id, "gate_graph_not_green");
  assert.deepEqual(result.non_green_gates, ["scheduler-truth"]);
});

test("GRAPH: a gate reporting success over a failed ancestor cannot claim green", () => {
  const gates = cleanGates();
  gates[1].conclusion = "failure";               // repo-hygiene, the root
  const result = classifyGateGraph(gates);
  assert.equal(result.would_be_green_if_authoritative, false);
  assert.deepEqual(result.non_green_gates, ["repo-hygiene"]);
  assert.deepEqual(result.inherited_non_green_gates,
    ["assurance-fabric-preactivation", "scheduler-truth"]);
});

test("GRAPH: non-green propagates transitively, not just one hop", () => {
  const gates = [
    { gate_id: "leaf", conclusion: "success", depends_on: ["middle"] },
    { gate_id: "middle", conclusion: "success", depends_on: ["root"] },
    { gate_id: "root", conclusion: "failure", depends_on: [] },
  ];
  const result = classifyGateGraph(gates);
  assert.equal(result.would_be_green_if_authoritative, false);
  assert.deepEqual(result.inherited_non_green_gates, ["leaf", "middle"]);
});

test("GRAPH: every non-success conclusion is non-green", () => {
  for (const conclusion of ["cancelled", "failure", "pending", "unknown"]) {
    const gates = cleanGates();
    gates[1].conclusion = conclusion;
    assert.equal(classifyGateGraph(gates).would_be_green_if_authoritative, false,
      `${conclusion} must not be green`);
  }
  const gates = cleanGates();
  gates[1].conclusion = "success";
  assert.equal(classifyGateGraph(gates).would_be_green_if_authoritative, true);
});

test("GRAPH: a gate whose ancestor is not in the graph is not green", () => {
  const gates = cleanGates();
  gates[0].depends_on = ["never-read"];
  const result = classifyGateGraph(gates);
  assert.equal(result.would_be_green_if_authoritative, false);
  assert.equal(result.reason_id, "gate_dependency_unknown");
  assert.deepEqual(result.unknown_dependencies, ["assurance-fabric-preactivation->never-read"]);
});

test("GRAPH: an empty graph is not green", () => {
  assert.equal(classifyGateGraph([]).would_be_green_if_authoritative, false);
});

test("GRAPH: a self-asserted green field is unreadable", () => {
  const gates = cleanGates();
  gates[1].conclusion = "failure";
  gates[1].claimed_green = true;
  assert.throws(() => classifyGateGraph(gates),
    error => error instanceof V5BoundaryError && error.code === "unknown_field");
});

test("GRAPH: a cyclic dependency graph is unreadable", () => {
  const gates = [
    { gate_id: "a", conclusion: "success", depends_on: ["b"] },
    { gate_id: "b", conclusion: "success", depends_on: ["a"] },
  ];
  assert.throws(() => classifyGateGraph(gates),
    error => error instanceof V5BoundaryError && error.code === "cyclic_gate_graph");
});

test("GRAPH: an unsorted or repeating dependency list is unreadable", () => {
  const unsorted = cleanGates();
  unsorted[0].depends_on = ["scheduler-truth", "repo-hygiene"];
  assert.throws(() => classifyGateGraph(unsorted),
    error => error instanceof V5BoundaryError && error.code === "unsorted_list");
  const repeating = cleanGates();
  repeating[0].depends_on = ["repo-hygiene", "repo-hygiene"];
  assert.throws(() => classifyGateGraph(repeating),
    error => error instanceof V5BoundaryError && error.code === "duplicate_member");
});

test("JOIN: a non-green gate graph blocks the join even with four clean predecessors", () => {
  const request = cleanJoin();
  request.gates[1].conclusion = "failure";
  const result = classifyGateZeroJoin(request);
  assert.deepEqual(result.predecessors_unsatisfied, []);
  assert.equal(result.would_join_exactly_if_authoritative, false);
  assert.equal(result.reason_id, "gate_graph_not_green");
});

// ---------------------------------------------------------------------------
// ONE CLAUSE, STANDING ALONE.
// ---------------------------------------------------------------------------

test("CLAUSE: one observation can be judged without the rest of the join", () => {
  const result = classifyPredecessorObservation(
    observation("step:wr46-dissolution-outcome", "wr46"),
    { step_ref: "step:wr46-dissolution-outcome", as_of_ms: Date.parse(AS_OF) });
  assert.equal(result.would_satisfy_if_authoritative, true);
  assert.equal(result.detail.step_ref, "step:wr46-dissolution-outcome");
});

test("CLAUSE: an observation bound to a different step is refused by name", () => {
  const result = classifyPredecessorObservation(
    observation("step:wr46-dissolution-outcome", "wr46"),
    { step_ref: "step:wr40-repository-outcome", as_of_ms: Date.parse(AS_OF) });
  assert.equal(result.reason_id, "predecessor_outcome_bound_to_other_step");
});

// ---------------------------------------------------------------------------
// POLICY IDENTITY.
// ---------------------------------------------------------------------------

test("POLICY: the preimage is stable, caller-independent, and carries no per-run verdict", () => {
  const preimage = v5A02GateZeroPolicyPreimage();
  assert.deepEqual(preimage.decision_ids, ["Q017.D1", "Q036.D1", "Q067.D1", "Q086.D1"]);
  assert.deepEqual(preimage.decision_ids, [...V5_A02_DECISION_IDS]);
  assert.equal(preimage.schema_version, V5_A02_GATE_ZERO_SCHEMA_VERSION);
  assert.equal(preimage.policy_version, V5_A02_POLICY_VERSION);
  // THE TWO FROZEN CLAIMS ARE GONE, not reworded and not derived. Each said the
  // surface can never answer yes, which stopped being true when the producer
  // seam was built; and a DERIVED version of either would put a per-run verdict
  // inside a caller-independent policy identity, which is worse than a stale
  // one. What the preimage still carries is what is true of the POLICY.
  assert.equal(Object.hasOwn(preimage, "gate_zero_passable"), false);
  assert.equal(Object.hasOwn(preimage, "public_surface_answers"), false);
  // TRUE since this slice, and DERIVED rather than typed: something implements
  // the producer behind card 9's staffed seat. It says false again in a tree
  // whose seat declaration is unstaffed, which the SEAT test proves.
  assert.equal(preimage.producer_bound, true);
  // TRUE since 2026-09-12, and DERIVED rather than typed: cards 11, 12 and 13
  // are ruled and their readers are bound. It says false again in a tree whose
  // ruling lines are null, which the SWITCH test proves by digest.
  assert.equal(preimage.authoritative_readers_bound, true);
  assert.deepEqual(preimage.owed_seams, [...V5_A02_GATE_ZERO_OWED_SEAMS]);
  // It takes no caller input, and proves it by ignoring some.
  assert.equal(digest(v5A02GateZeroPolicyPreimage({ passable: true })),
    digest(v5A02GateZeroPolicyPreimage()));
});

test("POLICY: the digest is deterministic and matches its canonical bytes", () => {
  assert.equal(v5A02GateZeroPolicyDigest(), v5A02GateZeroPolicyDigest());
  assert.equal(v5A02GateZeroPolicyDigest(), digest(v5A02GateZeroPolicyPreimage()));
  assert.equal(v5A02GateZeroPolicyCanonicalBytes(),
    JSON.stringify(JSON.parse(v5A02GateZeroPolicyCanonicalBytes())));
});

test("POLICY: every reason either half can answer with is registered", async () => {
  // Three spellings, because a reason reachable by ANY route must be in the
  // closed registry: the clauses answer through `wouldNot("id", ...)` and
  // `reason("id")`, and the public surface passes the id as the second argument
  // of its one `unavailable(...)` shape.
  const citations = path => {
    const source = readFileSync(fileURLToPath(new URL(path, import.meta.url)), "utf8");
    return [
      ...[...source.matchAll(/reason\("([a-z_]+)"\)/g)].map(match => match[1]),
      ...[...source.matchAll(/wouldNot\("([a-z_]+)"/g)].map(match => match[1]),
      ...[...source.matchAll(/unavailable\(\s*"[a-z_]+",\s*"([a-z_]+)"/g)].map(match => match[1]),
    ];
  };
  const publicCitations = citations("../src/gate-zero-assurance.v5.js");
  assert.ok(publicCitations.length >= 3, "the public surface must cite its own refusals");
  const classifierCitations = citations("./gate-zero-classifiers.v5.testhelper.mjs");
  assert.ok(classifierCitations.length >= 10, "the clauses must cite their own refusals");
  for (const id of [...publicCitations, ...classifierCitations])
    assert.ok(V5_A02_GATE_ZERO_REASON_IDS.includes(id), `${id} is not registered`);
  // And the behavioural half: whatever the surface actually answers is registered.
  for (const result of [await emitGateZeroOutcome({}), readGateZeroPredecessorJoin(), readGateGraphAssurance()])
    assert.ok(V5_A02_GATE_ZERO_REASON_IDS.includes(result.reason_id), result.reason_id);
});

test("POLICY: every result on both halves carries the no-effects marker", async () => {
  for (const result of [
    await emitGateZeroOutcome(cleanJoin()), readGateZeroPredecessorJoin(), readGateGraphAssurance(),
    classifyGateZeroJoin(cleanJoin()), classifyGateGraph(cleanGates()),
  ]) {
    assert.deepEqual(result.effects, V5_NO_EFFECTS);
    assert.ok(Object.isFrozen(result));
  }
});
