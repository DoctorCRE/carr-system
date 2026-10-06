// DoctorCRE v5 slice V5-A02, half one: THE GATE ZERO PUBLIC SURFACE — and every
// yes it can carry is one it was handed by a bound seam, never one it computed.
//
// WHAT THIS FILE MAY NOT DECIDE, said first because it is the whole point.
//
// (1) THE OUTCOME. Until 2026-09-11 r7 — the frozen design packet — registered
// NO producer, oracle, output schema, evidence scope or gate id for
// `step:gate-zero-read-only-outcome`. Decision
// `20c83902-f150-4d59-beca-915c5c871f95` then ruled the role provisionally, and
// on 2026-09-12 the amendment ruled on loop 589 wrote it into the packet: r7
// carries the producer row, the `gate-zero-read-only-accepted` gate and the
// `independent_control_plane_oracle` role, and the packet re-froze from
// `ef34aa54…` to `ea40f61a…`, and the 2026-09-13 consumer-gate-receipt digest
// amendment re-froze it again to `4379c60e…`.
// gate-zero-producer-registration.v5.js pins that
// digest; whether a given packet IS it is decided by that module's byte
// verifier, `v5A02GateZeroR7Presence(<bytes>)`, re-exported here. The
// registration's own `r7_entry_witness` is NULL, because the packet's bytes are
// not in this repository and neither a yes nor a no may be asserted without them.
//
// THE ORACLE SEAT IS STAFFED, AND STAFFING IT MOVED NOTHING BELOW. Card 9 named
// the reviewer charter on 2026-09-11 and then staffed the desk that charter
// describes on 2026-09-13 — the independent Codex reviewer lane, under decision
// `359784f1-5d9e-4e11-bcce-af8b0dfcc5e0` — so `oracle_seat_bound` is true here,
// nothing is owed on the seat, and no governance question about it is open. The
// three evidence readers — gate-zero-seam-readers.v5.js cards 11, 12 and 13, the
// predecessor-outcome, scheduler-canary and gate-conclusion readers — ARE bound
// to this surface's seams as of 2026-09-12, which is what section (2) below is
// about.
//
// AND THE PRODUCER SEAM IS NOW BOUND, which is what PR 1013 built.
// `seam:gate-zero-read-only-outcome-producer` is implemented by
// gate-zero-producer.v5.js: it derives the rows a Gate Zero run stands on, aims
// the three bound readers at them, applies the three clauses over the readings
// and assembles one `consumer-gate-receipt.v1`. So `producer_bound` is derived
// true while card 9's seat is staffed, and `passable` follows the producer's own
// answer rather than being false by construction.
//
// WHAT THAT DID NOT CHANGE. This file still computes nothing. It holds no
// clause, reads no store and signs nothing; it asks the bound seam and reports
// what came back, and every seam is fail-closed in every direction. Put card 9's
// `holder_ref` back to null and this surface refuses again, byte for byte.
//
// (2) THE EVIDENCE, AND THIS HALF MOVED ON 2026-09-12. A caller object
// describing four accepted outcomes, a canary, a readback and a green gate graph
// is a DESCRIPTION OF EVIDENCE, not evidence, and a public function that turned
// such a description into `ok`, `green`, `joins_exactly` or `allow` would be
// handing out authority that nothing in this system holds. That is the exact
// defect the V5-A02 review of PR 985 named, and the standing rule learned from
// nine review rounds on 2026-09-11 forbids it under ANY name, including a
// "predicate", "fixture" or "unwired" variant. NONE OF THAT CHANGES.
//
// WHAT CHANGED is that three of the four seams stopped being empty. PR 1001
// built the readers for cards 11, 12 and 13 and Joe ruled all three, so an
// accepted predecessor outcome, a scheduler canary and a gate's own conclusion
// each have an authoritative surface to be read from. This module now IMPORTS
// those readers and asks the same ruling table they ask, so the three seams
// report BOUND here on exactly the condition they are open there — and report
// unbound again the moment a `decision_id:` line goes back to null.
//
// WHAT THE TWO READ SURFACES STILL ANSWER. A bound reader is a surface and not a
// subject: which accepted outcome, which canary row, which commit and which
// declared check a run stands on are the PRODUCER's derivation, not a question
// either read surface can put to a caller. So both of them still refuse, and
// they refuse for that reason rather than for a missing reader.
//
// SO THE PUBLIC SURFACE IS THIS, and it is the whole of it:
//
//   readGateZeroPredecessorJoin  -> status "unavailable"; the two ruled readers
//                                   report bound, the aiming is the producer's
//   readGateGraphAssurance       -> status "unavailable"; the ruled conclusion
//                                   reader reports bound, the aiming is the
//                                   producer's
//   emitGateZeroOutcome          -> asks the bound producer seam and reports its
//                                   answer, which is a receipt and a digest when
//                                   every derived row is there and a refusal
//                                   naming the absent one when it is not. It
//                                   still embeds no join of its own.
//
// NONE OF THE THREE READS ITS REQUEST. That is not laziness, it is the boundary:
// if no field of the request can change the answer, then no caller can smuggle
// authority in through one. `request_read: false` says so in every result.
//
// WHERE THE REAL DECISION LOGIC LIVES. The three deterministic clauses V5-A02's
// checkable_done names are implemented in PRODUCTION, in
// gate-zero-producer.v5.js, module-private to it, applied to readings taken by
// ruled readers from ruled stores. They answer held, failed or unknown about a
// row — no clause on this side of the line is written in the conditional, in any
// spelling, and the producer's own suite asserts that.
//
// A test-side helper still evaluates the same clause shapes over a CALLER'S
// object, and it has to answer conditionally because a caller's object is not
// evidence. It lives in test/, this file does not import it, and no production
// module does; gate-zero-assurance.v5.test.mjs proves the isolation with a
// parser-backed import scan of every module in mcp-server/src, and proves this
// surface with a sweep over every caller-controlled shape — including the
// reviewer's one-gate construction.
//
// THE FOUR PREDECESSORS ARE NOT THIS FILE'S CHOICE. They are the frozen plan's,
// enforced today at tools/doctorcre-v5-review.cjs:1234-1244, which asserts that
// `step:gate-zero-read-only-outcome`'s `depends_on` is exactly
// {wr46-dissolution, wr40-repository, wr54-backup-recovery, scheduler-active}.
// They are restated ONCE, in gate-zero-producer-registration.v5.js beside the
// registration they stamp, because that validator is CommonJS plan-shape code no
// ESM module can import; this module re-exports that one list rather than
// keeping a second. gate-zero-assurance.v5.test.mjs reads the validator and
// asserts the two lists are identical — so a change to the frozen plan turns
// this module's test red instead of letting the two drift apart in silence.
//
// TWO KINDS OF NO, inherited unchanged from global-boundaries.v5.js:
//   * A POLICY ANSWER is RETURNED — `decision` is "refuse" with a stable
//     `reason_id`. Silence is never an allow, and on this surface there is no
//     allow at all.
//   * A CONTRACT VIOLATION THROWS V5BoundaryError. Handing a producer in as a
//     second argument is not a policy question.
//
// DECISION BINDING. The catalog maps Q017.D1, Q036.D1, Q067.D1 and Q086.D1 to
// this slice. Only their ROUTING is readable: the v5 design-basis register
// carries Q001-Q015 in full and compacts everything after it to pointers whose
// own canonicalization note says to recompile the text from source item IDs in
// the originating Codex thread, which is not reachable from here. Q017's
// readable disposition is `target: assurance_fabric`, `acceptance_hook:
// assurance-fabric-child-outcome` (doctrine `doctorcre-v5-design-basis`, section
// `requirements-q016-q020`, content hash
// 68f7f22e7964d08d724bacd43e49f0ccb0d20914e354e6a87f9645331d591710). The four
// ids are CARRIED in V5_A02_DECISION_IDS as a binding; this file claims no
// knowledge of their text and derives no behaviour from a guess at it.

import { canonicalJson, digest } from "./artifact-trust.js";
import { closedCallable } from "./closed-callable.js";
import { V5BoundaryError, V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import { ORGANIZATION_TENANT_ID } from "./identity.js";
import { GATE_ZERO_STEP_REF } from "./benchmark-minimum.v5.js";
import {
  V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS,
  V5_A02_GATE_ZERO_PRODUCER_DECISION_REF,
  V5_A02_GATE_ZERO_PRODUCER_REGISTRATION,
  V5_A02_GATE_ZERO_PRODUCER_REGISTRATION_STATUS,
  V5_A02_GATE_ZERO_ORACLE_SEAT_CHARTER_REF,
  V5_A02_GATE_ZERO_ORACLE_SEAT_DECISION_REF,
  V5_A02_GATE_ZERO_R7_AMENDMENT_DECISION_REF,
  V5_A02_GATE_ZERO_R7_ENTRY_WITNESS,
  V5_A02_GATE_ZERO_R7_ENTRY_WITNESS_DECIDED_BY,
  V5_A02_GATE_ZERO_R7_PACKET_SHA256,
  V5_A02_GATE_ZERO_R7_SUPERSEDED_PACKET_SHA256,
  V5_A02_SCHEDULER_STEP_REF,
  v5A02GateZeroR7Presence,
} from "./gate-zero-producer-registration.v5.js";
import {
  readGateConclusionEvidence,
  readPredecessorOutcomeEvidence,
  readSchedulerCanaryEvidence,
} from "./gate-zero-seam-readers.v5.js";
import { ruledCardBinding } from "./internal/gate-zero-seam-binding.v5.js";
import { v5A02GateZeroEmitOutcome } from "./gate-zero-producer.v5.js";

export { GATE_ZERO_STEP_REF, V5_NO_EFFECTS };

export const V5_A02_GATE_ZERO_SCHEMA_VERSION = "doctorcre-v5-a02-gate-zero-assurance.v1";

/**
 * 3, not 2. Version 1 answered from caller-supplied observations. Version 2
 * answered `unavailable` and named what it was owed. This version can answer:
 * the producer seam is built, so the surface's verdict is now derived from what
 * three ruled readers returned about named rows rather than fixed at refuse.
 */
export const V5_A02_POLICY_VERSION = 3;

/** The four decisions the catalog maps to V5-A02. Carried, not interpreted. */
export const V5_A02_DECISION_IDS = deepFreeze(["Q017.D1", "Q036.D1", "Q067.D1", "Q086.D1"]);

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

// AMENDMENT 2'S CLOSED SHAPE COMES FROM closed-callable.js (amendment 9, fifth
// correction round, 2026-09-14). This file used to define its own copy, on the
// argument that a self-contained module is worth a duplicated primitive. The
// review measured that argument against the copies and it failed: the local
// copies had already DIVERGED from the shared one — they never froze the
// callable, which is clause (c), the clause the first shape enumeration added
// after finding it missing — so the file whose whole job is to close a probe was
// running the unhardened version of the shape. A security primitive that exists
// five times is hardened in one of five places. There is one definition now, and
// the enumeration control walks every export against it.

// ---------------------------------------------------------------------------
// The frozen plan's four bound predecessors, and the scheduler among them.
// ---------------------------------------------------------------------------

/**
 * The four predecessors and the scheduler among them, re-exported from
 * gate-zero-producer-registration.v5.js — which holds them as a hard-bound
 * literal beside the registration they stamp. This module does not restate
 * them: two copies would be two authorities. A member absent there is not
 * admissible evidence for Gate Zero; a member present there is mandatory.
 *
 * The producer registration comes from the same module as ONE frozen constant.
 * There is no builder to call and no predecessor set to hand in. Its
 * `r7_entry_witness` is NULL, not true: the 2026-09-12 amendment wrote the
 * registration into r7 and this repository pins the resulting digest, but the
 * packet's bytes live in the doctrine store, and only bytes decide. The one
 * thing that reads them, `v5A02GateZeroR7Presence`, is re-exported here from
 * the module that owns it — one implementation, not two authorities. Either
 * way the registration names a role and staffs nobody, so it binds nothing.
 */
export {
  V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS,
  V5_A02_GATE_ZERO_PRODUCER_REGISTRATION,
  V5_A02_GATE_ZERO_R7_PACKET_SHA256,
  V5_A02_GATE_ZERO_R7_SUPERSEDED_PACKET_SHA256,
  V5_A02_SCHEDULER_STEP_REF,
  v5A02GateZeroR7Presence,
};

/** What an observation says it saw. "pending" and "absent" are both "no". */
export const V5_A02_OBSERVATION_STATES = deepFreeze(["absent", "observed", "pending"]);

/** Whether the outcome was accepted. Only "accepted" is a closed review. */
export const V5_A02_ACCEPTANCE_STATES = deepFreeze([
  "accepted", "pending_human_acceptance", "rejected",
]);

/**
 * What the accepted outcome CONCLUDED. Accepting a `criteria_not_met` outcome
 * closes a review and clears no milestone, so the two are separate fields and
 * both must be right.
 */
export const V5_A02_CRITERIA_STATES = deepFreeze([
  "criteria_met", "criteria_not_met", "not_observed",
]);

/** A gate's own reported conclusion. Only "success" is green. */
export const V5_A02_GATE_CONCLUSIONS = deepFreeze([
  "cancelled", "failure", "pending", "success", "unknown",
]);

/**
 * Every refusal this slice's Gate Zero half can answer with — the public
 * surface's three reader-unavailable reasons, and the clause reasons the
 * module-private classifiers cite. Closed, so a reason is checkable.
 */
export const V5_A02_GATE_ZERO_REASON_IDS = deepFreeze([
  "duplicate_predecessor_observation",
  // THE PRODUCER'S NINE, registered HERE because this surface builds the answer
  // around them and `reason()` refuses an id it does not know. The producer
  // module carries the same closed list, and the test asserts the two are
  // identical — so a refusal it can reach is a refusal this gate can express.
  //
  // NINE RATHER THAN SEVEN SINCE 2026-09-12: the producer's absence reasons were
  // split so that an unreadable candidate repository, a missing sealed file and
  // a genuinely absent ruled row stop sharing one id. See the producer's own
  // registry for what each one means.
  "gate_zero_candidate_metadata_absent",
  "gate_zero_evidence_unavailable",
  // THE GATE'S OWN, for the two SYNCHRONOUS reads once the producer exists:
  // a join is something the emission produces from rows, not something a
  // query of this surface can return.
  "gate_zero_join_is_produced_not_queried",
  "gate_zero_gate_graph_clause_failed",
  "gate_zero_negative_admission_unproved",
  "gate_zero_predecessor_clause_failed",
  "gate_zero_producer_identity_refused",
  "gate_zero_run_binding_unnamed",
  "gate_zero_scheduler_clause_failed",
  "gate_zero_sealed_artifact_absent",
  "duplicate_predecessor_outcome",
  "gate_conclusion_reader_unavailable",
  "gate_dependency_unknown",
  "gate_graph_not_green",
  "gate_zero_producer_seam_unavailable",
  "predecessor_criteria_not_met",
  "predecessor_observed_after_reference",
  "predecessor_outcome_absent",
  "predecessor_outcome_bound_to_other_step",
  "predecessor_outcome_digest_mismatch",
  "predecessor_outcome_not_accepted",
  "predecessor_outcome_reader_unavailable",
  "predecessor_set_incomplete",
  "scheduler_canary_digest_mismatch",
  "scheduler_canary_seam_unavailable",
  "scheduler_canary_not_bound_to_receipt",
  "scheduler_readback_absent",
  "scheduler_readback_canary_mismatch",
  "scheduler_readback_not_after_dispatch",
  "unknown_predecessor_step",
].sort());

function reason(id) {
  if (!V5_A02_GATE_ZERO_REASON_IDS.includes(id))
    fail("unknown_reason_id", `${id} is not a registered reason`, { reason_id: id });
  return id;
}

// ---------------------------------------------------------------------------
// THE SEAMS, NAMED BY THIS MODULE.
//
// Each is a NAME, not a port: nothing a caller passes can become one, because no
// exported function accepts a producer or a reader as an argument and this
// module exports no way to bind one.
// ---------------------------------------------------------------------------

/**
 * Who or what could issue a Gate Zero outcome. r7 registers the role and a
 * staffed seat holds it; NOTHING IMPLEMENTS IT, which is why this seam is the
 * one still unbound below.
 */
export const V5_A02_GATE_ZERO_PRODUCER_SEAM = "seam:gate-zero-read-only-outcome-producer";

/** Where an ACCEPTED predecessor outcome is read from. Card 11's reader, since 2026-09-12. */
export const V5_A02_PREDECESSOR_OUTCOME_READER_SEAM = "seam:gate-zero-predecessor-outcome-reader";

/** Where a scheduler dispatch and its readback are read from. Card 12's reader, since 2026-09-12. */
export const V5_A02_SCHEDULER_READER_SEAM = "seam:scheduler-canary-reader";

/** Where a gate's own conclusion is read from. Card 13's reader, since 2026-09-12. */
export const V5_A02_GATE_CONCLUSION_READER_SEAM = "seam:gate-conclusion-reader";

/**
 * THE FOUR SEAMS THIS HALF NAMES, which is every seam an answer may stand on.
 * The name is the one main gave it, when all four were in fact owed; three now
 * have a ruled reader behind them, so what is STILL owed is derived per answer by
 * filtering this list through `seamBound` — see `owed_seams` below. The policy
 * preimage keeps reporting the whole list under this name because renaming a
 * pinned preimage field would move the policy digest for a comment's sake.
 */
export const V5_A02_GATE_ZERO_OWED_SEAMS = deepFreeze([
  V5_A02_GATE_CONCLUSION_READER_SEAM,
  V5_A02_GATE_ZERO_PRODUCER_SEAM,
  V5_A02_PREDECESSOR_OUTCOME_READER_SEAM,
  V5_A02_SCHEDULER_READER_SEAM,
].sort());

/**
 * THE BINDINGS, AND WHAT MOVED ON 2026-09-12.
 *
 * Until PR 1001 every entry here was null and every answer below said so in the
 * only way it honestly could: no reader exists. Three of the four seams now have
 * a reader behind them — cards 11, 12 and 13, ruled by Joe on 2026-09-11 and
 * switched on by the three `decision_id:` lines of gate-zero-seam-rulings.v5.js.
 * This module IMPORTS those readers and asks THE SHARED BINDING PREDICATE —
 * `ruledCardBinding`, the one function that decides whether a card is ruled for
 * its reader — so a seam is bound here on exactly the condition it is open
 * there, and the two cannot drift. Put `null` back on a card's ruling line and
 * the seam closes in both files at once.
 *
 * AND ASKING THE TABLE DIRECTLY IS THE DEFECT THAT COST THIS SENTENCE ITS FIRST
 * VERSION. Until the PR 1004 re-review this module imported the ruling table and
 * read any non-null ruling as a bound seam — half of the reader's test, which
 * also requires the ruling to name the store that card's reader actually serves.
 * A ruling naming another registered store made this file report BOUND while the
 * reader refused it and fell back. Two predicates for one question is the bug;
 * there is now one, it lives in internal/gate-zero-seam-binding.v5.js with the
 * store table it has to agree with, and this module no longer imports the ruling
 * table at all. The predicate is shared through that INTERNAL path rather than
 * through the reader's public surface, which the third correction's review
 * required: the reader module's promise is four public names, and a predicate
 * added to it is a fifth.
 *
 * WHAT IS STILL NOT A DOOR. The readers arrive as a module-private import.
 * There is no argument, no setter, no registry keyed by anything a caller
 * controls, and no environment variable that binds a seam: the binding
 * condition is a decision id committed to a file, which is what a ruling is.
 *
 * AND THE PRODUCER SEAM IS BOUND (PR 1013). gate-zero-producer.v5.js implements
 * the pointing these readers were built for: it derives the head revision, the
 * scheduler service and the acceptance receipts from the candidate tree and the
 * ruled stores, aims all three readers at them, and signs the result with the
 * identity of the authenticated call it is running inside. `producer_bound` is
 * derived from card 9's seat exactly as before, so an unstaffed seat still
 * closes this seam and takes `passable` down with it.
 *
 * `holder` IS A THUNK, NOT A VALUE, and that is load-order and not style.
 * gate-zero-seam-readers.v5.js imports this module for its unruled answer, so
 * the two form a cycle; whichever of them a process imports first, the other's
 * body has not run yet. A thunk reads the reader binding when a caller asks,
 * by which time both modules are evaluated. Reading it eagerly here would throw
 * on the import order that starts at the readers.
 */
const V5_A02_GATE_ZERO_SEAM_BINDINGS = Object.freeze({
  // CARD 9, AND IT IS BOUND AS OF THIS SLICE. The producer seam's authority is
  // not a store ruling — there is no store to name — so its `ruled` thunk asks
  // the one authority it does have: card 9's seat declaration, read off the
  // registration's own derived field. Put `holder_ref: null` back on that
  // declaration and this seam closes exactly the way a card with a `null`
  // decision id closes, with nothing else touched.
  [V5_A02_GATE_ZERO_PRODUCER_SEAM]: Object.freeze({
    card_ref: "card:9",
    method: "emitOutcome",
    ruled: () => V5_A02_GATE_ZERO_PRODUCER_REGISTRATION.oracle_seat_bound === true,
    holder: () => Object.freeze({ emitOutcome: v5A02GateZeroEmitOutcome }),
  }),
  [V5_A02_PREDECESSOR_OUTCOME_READER_SEAM]: Object.freeze({
    card_ref: "card:11",
    method: "readOutcome",
    ruled: () => ruledCardBinding("card:11") !== null,
    holder: () => Object.freeze({ readOutcome: readPredecessorOutcomeEvidence }),
  }),
  [V5_A02_SCHEDULER_READER_SEAM]: Object.freeze({
    card_ref: "card:12",
    method: "readCanary",
    ruled: () => ruledCardBinding("card:12") !== null,
    holder: () => Object.freeze({ readCanary: readSchedulerCanaryEvidence }),
  }),
  [V5_A02_GATE_CONCLUSION_READER_SEAM]: Object.freeze({
    card_ref: "card:13",
    method: "readConclusion",
    ruled: () => ruledCardBinding("card:13") !== null,
    holder: () => Object.freeze({ readConclusion: readGateConclusionEvidence }),
  }),
});

/**
 * The bound holder for a seam, or null when it is unavailable. Takes no caller
 * input: the seam and the method are this module's own constants, and the only
 * question asked of anything outside is whether that seam's CARD is ruled FOR
 * ITS READER, which is the reader's question and is answered by the reader.
 *
 * FAIL-CLOSED IN EVERY DIRECTION. An unknown seam, a seam with no card, a card
 * with no live ruling, a card whose ruling names a store its reader does not
 * serve, a thunk that throws, a holder that is not an object or does not carry
 * the method — each answers null, which is the same "no" this surface gave
 * before any ruling existed.
 *
 * THE RULING QUESTION IS NOT ASKED HERE. `ruledCardBinding` is the shared
 * predicate, imported from internal/gate-zero-seam-binding.v5.js — the same one
 * the readers ask: this file cannot answer "bound" for a ruling the reader would
 * refuse, because it is no longer able to form its own opinion about what a
 * ruling means.
 */
function boundSeam(seam, method) {
  const binding = V5_A02_GATE_ZERO_SEAM_BINDINGS[seam];
  if (!isPlainObject(binding) || binding.method !== method) return null;
  // EACH SEAM ASKS THE AUTHORITY THAT OWNS IT, and no seam gets to answer its
  // own question. Cards 11, 12 and 13 ask the shared ruling predicate, which is
  // also the predicate their readers ask. Card 9 asks the seat declaration,
  // which is the registration's own derived field. A thunk that throws is a no.
  let ruled;
  try {
    ruled = binding.ruled();
  } catch {
    return null;
  }
  if (ruled !== true) return null;
  let holder;
  try {
    holder = binding.holder();
  } catch {
    return null;
  }
  return isPlainObject(holder) && typeof holder[method] === "function" ? holder : null;
}

/** Whether a seam has a bound holder at all, asked without naming its method. */
function seamBound(seam) {
  const binding = V5_A02_GATE_ZERO_SEAM_BINDINGS[seam];
  return isPlainObject(binding) && boundSeam(seam, binding.method) !== null;
}

/** Every seam this answer stands on, and whether anything is bound to it. */
function seamsBound(seams) {
  return deepFreeze(seams.map(seam => ({ seam, bound: seamBound(seam) })));
}

/**
 * The one shape every unavailable answer on this surface has.
 *
 * `owed_seams` is what is STILL owed — a seam with a bound holder is not owed —
 * and `seams_bound` carries the whole list with its live state, so nothing is
 * hidden by the filter. While nothing is bound the two say what they have always
 * said, byte for byte, which is the property the unruled tree is tested on.
 */
function unavailable(answer, reasonId, because, seams, extra,
  decidedBy = "no_authoritative_reader") {
  const stands_on = [...seams].sort();
  return deepFreeze({
    schema_version: V5_A02_GATE_ZERO_SCHEMA_VERSION,
    policy_version: V5_A02_POLICY_VERSION,
    answer,
    tenant: ORGANIZATION_TENANT_ID,
    gate_zero_step_ref: GATE_ZERO_STEP_REF,
    status: "unavailable",
    decision: "refuse",
    reason_id: reason(reasonId),
    unavailable_because: because,
    owed_seams: stands_on.filter(seam => !seamBound(seam)),
    seams_bound: seamsBound(stands_on),
    // No field of any request can change any field of this answer.
    request_read: false,
    caller_evidence_admitted: false,
    decided_by: decidedBy,
    model_judgment_admitted: false,
    effects: V5_NO_EFFECTS,
    ...extra,
  });
}

/** The three ruled reader seams, in the order a reviewer reads the cards. */
const V5_A02_GATE_ZERO_READER_SEAMS = deepFreeze([
  V5_A02_PREDECESSOR_OUTCOME_READER_SEAM,
  V5_A02_SCHEDULER_READER_SEAM,
  V5_A02_GATE_CONCLUSION_READER_SEAM,
]);

/** Whether all three ruled readers are bound. The producer is not among them. */
function allReaderSeamsBound() {
  return V5_A02_GATE_ZERO_READER_SEAMS.every(seamBound);
}

/**
 * THE THREE SEAMS, ASKED ONE AT A TIME — and the reason this is a map rather
 * than a boolean is the PR 1004 review's first finding.
 *
 * A single `readersBound` flag made every answer say the same thing about all
 * three cards: with all three ruled the emission still reported that no
 * predecessor or scheduler reader existed, and withdrawing ONE ruling reopened
 * all three governance questions. Each card is its own ruling and its own seam,
 * so each is reported on its own. `withdrawn()` names which of the three a
 * caller is still owed, in card order, and every sentence and list below is
 * derived from it rather than from a single yes/no.
 */
const V5_A02_GATE_ZERO_READER_SEAM_KEYS = deepFreeze(["predecessor", "scheduler", "conclusion"]);

const V5_A02_GATE_ZERO_READER_SEAM_BY_KEY = Object.freeze({
  predecessor: V5_A02_PREDECESSOR_OUTCOME_READER_SEAM,
  scheduler: V5_A02_SCHEDULER_READER_SEAM,
  conclusion: V5_A02_GATE_CONCLUSION_READER_SEAM,
});

/**
 * The governance question each unbound reader seam leaves open, and it is
 * listed for THAT seam alone. Putting `null` back on card 12's ruling line
 * reopens the scheduler question and neither of the other two.
 */
const V5_A02_GATE_ZERO_READER_QUESTIONS = Object.freeze({
  predecessor: "which store an accepted predecessor outcome is read from",
  scheduler: "which scheduler surface a canary and its readback are read from",
  conclusion: "which surface a gate's own conclusion is read from",
});

/** Which of the three ruled reader seams have no bound holder, in card order. */
function withdrawn() {
  return V5_A02_GATE_ZERO_READER_SEAM_KEYS
    .filter(key => !seamBound(V5_A02_GATE_ZERO_READER_SEAM_BY_KEY[key]));
}

/**
 * The one sentence that is true of every answer here once ALL THREE readers are
 * bound: the evidence surfaces are available and the POINTING is not. It is an
 * opaque token on purpose — the PR 1004 review's second finding was that a
 * branch-owned value carrying a word of the closed union is a word a consumer
 * can pattern-match, whatever it was meant to say.
 */
const PRODUCER_SEAT_UNSTAFFED = "evidence_seams_bound_producer_unstaffed";

/**
 * THE SAME REFUSAL, ONE STEP FURTHER ALONG. Card 9's seat is staffed as of
 * 2026-09-13, so "no seat staffs the role" stopped being true — and the seam is
 * still unavailable, because nothing implements the producer behind it. A
 * staffed seat is who may sign; the code that aims the three bound readers at
 * particular rows and signs the result is not written. Two facts, two words.
 */
const PRODUCER_SEAM_UNBUILT = "evidence_seams_bound_producer_seam_unbuilt";

/**
 * THE THIRD STATE, AND IT IS THIS SLICE. The seam is built: a module-private
 * producer stands behind it, and the gate's verdict is derived from what it
 * returns rather than fixed at refuse. Two facts became three, so the token did
 * too — and it stays opaque for the PR 1004 reason: a branch-owned value
 * carrying a word of the closed union is a word a consumer can pattern-match,
 * whatever it was meant to say.
 */
const PRODUCER_SEAM_BUILT = "evidence_seams_bound_producer_seam_built";

/** Whether card 9's seat has a holder. Read off the registration, never typed twice. */
const oracleSeatBound = () => V5_A02_GATE_ZERO_PRODUCER_REGISTRATION.oracle_seat_bound === true;

/** Whether anything actually implements the producer behind the seam. Derived. */
const producerSeamBound = () => boundSeam(V5_A02_GATE_ZERO_PRODUCER_SEAM, "emitOutcome") !== null;

/** Which of the three producer facts decided an answer that got past the readers. */
const producerDecidedBy = () => (producerSeamBound()
  ? PRODUCER_SEAM_BUILT
  : (oracleSeatBound() ? PRODUCER_SEAM_UNBUILT : PRODUCER_SEAT_UNSTAFFED));

/**
 * The clause that says why THIS SYNCHRONOUS QUERY cannot aim the bound readers
 * at particular rows, over the thing that would be named. It blames the empty
 * seat while the seat is empty, the unbuilt seam once it is not, and — once the
 * seam is built — the thing that is then actually true: aiming the readers is
 * what the EMISSION does, and a read-only query of this surface is not it.
 */
const noProducerBecause = (wouldName) => (producerSeamBound()
  ? `the producer seam that names ${wouldName} is bound, and a query of this surface does not aim it`
  : (oracleSeatBound()
    ? `the producer seam that would name ${wouldName} is unbuilt`
    : `no seat holds the producer that would name ${wouldName}`));

/** The reason those two reads refuse, which moves when the seam is built. */
const noProducerReason = () => (producerSeamBound()
  ? "gate_zero_join_is_produced_not_queried"
  : "gate_zero_producer_seam_unavailable");

// ---------------------------------------------------------------------------
// checkable_done 1 — "WR prerequisites and scheduler canary/readback join
// exactly". The clause is implemented and proved; the READING is not available.
// ---------------------------------------------------------------------------

/**
 * What a Gate Zero predecessor join would need and still cannot get.
 *
 * The four predecessors would have to be read from an accepted-outcome store,
 * and the canary and its readback from the scheduler that dispatched them. Both
 * of those readers exist and BOTH ARE BOUND to this surface: cards 11 and 12 in
 * gate-zero-seam-readers.v5.js, bound through V5_A02_GATE_ZERO_SEAM_BINDINGS on
 * exactly the condition their rulings are open. The oracle seat is staffed too
 * — card 9, decision `359784f1-5d9e-4e11-bcce-af8b0dfcc5e0`, 2026-09-13 — so
 * neither a missing reader nor an empty seat is what stands here any more.
 *
 * WHAT IS NOT BUILT IS THE PRODUCER BEHIND THE PRODUCER SEAM. A bound reader
 * answers truthfully about whatever row it is pointed at, and no code in this
 * repository does the pointing: which accepted outcome and which canary row a
 * Gate Zero run stands on are the producer's bindings. So the answer is still
 * `unavailable` for every caller on every input — now naming that ONE owed
 * seam alongside the two bound ones it stands on, and `decided_by` says the
 * seam is unbuilt rather than the seat empty. Put either ruling line back to
 * null and the earlier per-card refusals below answer again.
 */
export const readGateZeroPredecessorJoin = closedCallable(() => {
  const predecessorReader = boundSeam(V5_A02_PREDECESSOR_OUTCOME_READER_SEAM, "readOutcome");
  const schedulerReader = boundSeam(V5_A02_SCHEDULER_READER_SEAM, "readCanary");
  const stood = {
    required_predecessors: [...V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS],
    scheduler_step_ref: V5_A02_SCHEDULER_STEP_REF,
    predecessor_outcome_reader_bound: predecessorReader !== null,
    scheduler_reader_bound: schedulerReader !== null,
  };
  // THE UNRULED ANSWER, unchanged to the byte. While either card is unruled the
  // sentence below is literally true and is the one this surface has always
  // given; a tree with `null` back on the ruling lines reproduces this object's
  // exact digest, and gate-zero-assurance.v5.test.mjs proves it against the
  // digest main published.
  const seams = [V5_A02_PREDECESSOR_OUTCOME_READER_SEAM, V5_A02_SCHEDULER_READER_SEAM];
  if (predecessorReader === null && schedulerReader === null)
    return unavailable(
      "gate_zero_predecessor_join",
      "predecessor_outcome_reader_unavailable",
      "no accepted-outcome or scheduler reader is BOUND to this surface, so the four predecessors, the canary and its readback cannot be read from here",
      seams,
      stood);
  // AND ONE CARD AT A TIME. Each of the two seams has its own ruling, so each
  // absence is its own refusal: a withdrawn card 11 says nothing about card 12,
  // and the reason id names the seam that is actually missing rather than always
  // naming the first one.
  if (predecessorReader === null)
    return unavailable(
      "gate_zero_predecessor_join",
      "predecessor_outcome_reader_unavailable",
      "no authoritative accepted-outcome store is bound to name the four predecessors, and the ruled scheduler canary seam is bound",
      seams,
      stood);
  if (schedulerReader === null)
    return unavailable(
      "gate_zero_predecessor_join",
      "scheduler_canary_seam_unavailable",
      "the ruled accepted-outcome seam for the four predecessors is bound, and no authoritative scheduler surface is bound to name the canary or the observation that answers it",
      seams,
      stood);
  // AND THE RULED ONE, which refuses for a different and more advanced reason.
  // Both readers are bound and either would answer about a row. Nothing here can
  // say WHICH row, because that is the producer's binding and the producer seam
  // is unbuilt — so the refusal moves from "there is nothing to read with" to
  // "nothing is written that would aim it", and the producer seam joins the seams
  // this answer stands on rather than the join being reported as available.
  return unavailable(
    "gate_zero_predecessor_join",
    noProducerReason(),
    `the ruled evidence seams for the four predecessors and for the scheduler canary are bound, and ${noProducerBecause("which accepted outcome and which canary row a Gate Zero run stands on")}`,
    [...seams, V5_A02_GATE_ZERO_PRODUCER_SEAM],
    stood,
    producerDecidedBy());
});

// ---------------------------------------------------------------------------
// checkable_done 2 — "failed injected gate cannot claim green". The propagation
// clause is implemented and proved; the CONCLUSIONS cannot be read.
// ---------------------------------------------------------------------------

/**
 * What a gate-graph assurance would need and cannot get.
 *
 * Non-green propagation is only as good as the conclusions it propagates, and a
 * caller-supplied list of gates with their own conclusions on them is a claim
 * about CI, not a reading of it. The gate-conclusion reader (card 13, in
 * gate-zero-seam-readers.v5.js) IS bound to this seam as of 2026-09-12, and the
 * answer is still `unavailable`: a bound reader is a surface, not a subject.
 */
export const readGateGraphAssurance = closedCallable(() => {
  const conclusionReader = boundSeam(V5_A02_GATE_CONCLUSION_READER_SEAM, "readConclusion");
  // The conclusions vocabulary is an EXPORTED CONSTANT, not a field of this
  // answer: an unavailable answer recites nothing a caller could mistake for a
  // reading.
  const stood = { gate_conclusion_reader_bound: conclusionReader !== null };
  if (conclusionReader === null)
    return unavailable(
      "gate_graph_assurance",
      "gate_conclusion_reader_unavailable",
      "no gate-conclusion reader is BOUND to this surface, so a gate graph can only be asserted by its caller",
      [V5_A02_GATE_CONCLUSION_READER_SEAM],
      stood);
  // Card 13 is ruled, so a conclusion CAN be read — for one commit, under one
  // check this repository declares. Which commit and which check a Gate Zero run
  // is about is the producer's binding, and a graph assembled from commits of
  // this module's own choosing would be this module deciding what it was asked
  // to report on.
  return unavailable(
    "gate_graph_assurance",
    noProducerReason(),
    `the ruled evidence seam for gate conclusions is bound, and ${noProducerBecause("which head revision and which declared check a Gate Zero run stands on")}`,
    [V5_A02_GATE_CONCLUSION_READER_SEAM, V5_A02_GATE_ZERO_PRODUCER_SEAM],
    stood,
    producerDecidedBy());
});

// ---------------------------------------------------------------------------
// The privileged path, and the refusal that stands where it would be.
// ---------------------------------------------------------------------------

/**
 * THE ONLY FUNCTION THAT COULD EMIT A GATE ZERO OUTCOME, AND IT CANNOT.
 *
 * It takes ONE argument, and the arity IS the boundary: there is no producer
 * parameter, so no caller can hand in the authority that is missing. The answer
 * is fixed for every caller on every input:
 *
 *     passable: false, reason "gate_zero_producer_seam_unavailable"
 *
 * AND IT CARRIES NO JOIN. The earlier version of this file returned the full
 * predecessor join beside the refusal, so a caller who supplied a clean fixture
 * got `join.decision: "allow"` and `joins_exactly: true` inside a response whose
 * outer field said refuse. A successful join inside a refusal is still a
 * successful join, and it was reachable from nothing but caller input. What a
 * reader gets instead is the truth: the join is unavailable, and here is every
 * seam it is owed.
 */
export const emitGateZeroOutcome = closedCallable((...received) => {
  // THE ARITY IS STILL THE BOUNDARY, counted from a rest parameter because an
  // arrow has no `arguments` object — and amendment 2 clause (a) requires an
  // arrow or a bound function. The request itself is as unread as it ever was:
  // `received` is measured and never looked into.
  //
  // AND IT IS STILL A SYNCHRONOUS THROW. This function now returns a PROMISE,
  // because the producer behind the seam reads rows out of three ruled stores
  // and a reading takes time — but "you handed me a producer" is a contract
  // violation, not a policy question, so it is refused before anything is
  // awaited and in the caller's own frame.
  if (received.length > 1)
    fail("gate_zero_producer_is_not_an_argument",
      "emitGateZeroOutcome takes one request; the producer is bound by this module",
      { arguments_received: received.length, required_seam: V5_A02_GATE_ZERO_PRODUCER_SEAM });

  const producer = boundSeam(V5_A02_GATE_ZERO_PRODUCER_SEAM, "emitOutcome");
  if (producer === null) return Promise.resolve(emission(null));
  // FAIL-CLOSED ON THE PRODUCER TOO. A producer that throws, or that answers
  // with anything but its own closed shape, is a producer that did not produce:
  // the answer is the same refusal an unbound seam gives, with the seam still
  // reported bound, because pretending a thrown value was a pass is the one
  // thing this surface exists to refuse.
  let produced;
  try {
    produced = producer.emitOutcome();
  } catch {
    return Promise.resolve(emission(null));
  }
  return Promise.resolve(produced).then(answer => emission(answer), () => emission(null));
});

/**
 * ONE BUILDER FOR BOTH ANSWERS, so the refusal and the pass cannot drift into
 * two descriptions of one surface. `produced` is null when nothing stands behind
 * the seam — which is byte-for-byte the answer this surface gave before the
 * producer existed — and is the producer's own closed answer otherwise.
 */
function emission(produced) {
  const entry = V5_A02_GATE_ZERO_PRODUCER_REGISTRATION.registry_entry;
  // THE THREE CARDS, ASKED SEPARATELY. `owed` is the ones with no bound holder,
  // in card order, and every sentence and list below reads it rather than a
  // single flag: with all three bound this answer no longer says a predecessor or
  // scheduler reader is missing, and with ONE withdrawn it reopens that card's
  // governance question alone.
  const owed = withdrawn();
  const readersBound = owed.length === 0;
  const bound = producerSeamBound();
  // WHAT THE PRODUCER SAID, read defensively: a value that is not its closed
  // shape is not a reading, and the surface answers as though nothing stood
  // behind the seam rather than reporting over whatever arrived.
  const reported = isPlainObject(produced) && typeof produced.decision === "string"
    ? produced : null;
  // AN OUTCOME WAS PRODUCED, WHICH IS NOT THE SAME AS A PASS. A run that read
  // all three seams and found a failed gate produced a real outcome with a real
  // digest and a real instant, and its receipt says `status: "fail"`. Both facts
  // are reported, separately, because a consumer needs the digest either way and
  // must never read the first as the second.
  const outcomeProduced = reported !== null && reported.decision === "report"
    && reported.status === "outcome_produced"
    && typeof reported.outcome_digest === "string" && typeof reported.observed_at === "string"
    && isPlainObject(reported.receipt);
  const passes = outcomeProduced && reported.receipt.status === "pass";

  const body = {
    decision_ids: [...V5_A02_DECISION_IDS],
    // DERIVED, and this is what card 9's slice could not do. It is not derived
    // from a join, not from the request, not by any caller, and not from the
    // registration either — a ruled role is not a signature. It is derived from
    // one thing: whether the bound producer returned an outcome over rows three
    // ruled readers took from three ruled stores.
    passable: passes,
    not_passable_because: passes ? null : notPassableBecause(owed, readersBound, bound, reported),
    producer_seam: V5_A02_GATE_ZERO_PRODUCER_SEAM,
    // EACH CARD, ON ITS OWN LINE. `seams_bound` above carries all four seams
    // with their live state; these three say the same thing under the names
    // the two reader answers already use, so a consumer reading only this
    // answer can still tell WHICH of the three is missing.
    predecessor_outcome_reader_bound: !owed.includes("predecessor"),
    scheduler_reader_bound: !owed.includes("scheduler"),
    gate_conclusion_reader_bound: !owed.includes("conclusion"),
    producer_bound: bound,
    producer_is_caller_supplied: false,
    // What decision 20c83902 ruled and the 2026-09-12 amendment then wrote
    // into r7 itself. `producer_registration.r7_packet_sha256` is the digest
    // this repository pins; `r7_entry_witness` is NULL because only the
    // packet's bytes decide and they are not here; and the four fields the
    // ruling could not settle are answered in `resolved_from_r7`.
    producer_role: entry.producer_role,
    oracle_ref: entry.oracle_ref,
    output_schema_ref: entry.output_schema_ref,
    evidence_scope: entry.evidence_scope,
    produced_gate_id: entry.produces_gate_ids[0],
    producer_registration: V5_A02_GATE_ZERO_PRODUCER_REGISTRATION,
    producer_registration_status: V5_A02_GATE_ZERO_PRODUCER_REGISTRATION_STATUS,
    producer_registration_decision_ref: V5_A02_GATE_ZERO_PRODUCER_DECISION_REF,
    // NULL, and null is not a soft false: the packet's bytes are not in this
    // repository, so this surface may assert neither that the amendment landed
    // nor that it did not. `r7_entry_witness_decided_by` names the only thing
    // that decides it, and it takes bytes.
    r7_entry_witness: V5_A02_GATE_ZERO_R7_ENTRY_WITNESS,
    r7_entry_witness_decided_by: V5_A02_GATE_ZERO_R7_ENTRY_WITNESS_DECIDED_BY,
    // Cards 9 and 10, reported as ruled. The charter is named by decision
    // 8a1dad08 and the desk behind it by the staffing ruling beside it; the
    // r7 amendment is ruled by decision 311a9af5 and was applied to the packet
    // on 2026-09-12 — which is why the field beside it is a witness over bytes
    // rather than a flag anybody here can set.
    //
    // EVERY ONE OF THESE IS READ OFF THE REGISTRATION, including the boolean:
    // a second derivation of "is the seat staffed" in this file would be a
    // second authority over card 9, which is the defect the shared ruling
    // predicate was extracted to stop.
    oracle_seat_bound: V5_A02_GATE_ZERO_PRODUCER_REGISTRATION.oracle_seat_bound,
    oracle_seat_holder_ref: V5_A02_GATE_ZERO_PRODUCER_REGISTRATION.oracle_seat_holder_ref,
    oracle_seat_charter_ref: V5_A02_GATE_ZERO_ORACLE_SEAT_CHARTER_REF,
    oracle_seat_charter_decision_ref: V5_A02_GATE_ZERO_ORACLE_SEAT_DECISION_REF,
    oracle_seat_staffing_decision_ref:
      V5_A02_GATE_ZERO_PRODUCER_REGISTRATION.oracle_seat_staffing_decision_ref,
    r7_amendment_decision_ref: V5_A02_GATE_ZERO_R7_AMENDMENT_DECISION_REF,
    // The outcome fields a consumer would need, and they are the producer's or
    // they are null. Nothing here computes a digest or stamps an instant: a
    // surface that minted either would be the producer wearing the gate's name.
    outcome_digest: outcomeProduced ? reported.outcome_digest : null,
    observed_at: outcomeProduced ? reported.observed_at : null,
    // AND the join fields. The producer's clause results ARE the join — the
    // three checkable_done clauses applied to rows — so a pass carries them and
    // a refusal carries null, which is the truth in both directions.
    join: outcomeProduced ? reported.clauses : null,
    join_unavailable_because: outcomeProduced ? null : joinUnavailableBecause(owed),
    predecessor_evidence_read: null,
    // WHAT THE PRODUCER ANSWERED, carried whole so a consumer can see WHICH
    // clause or WHICH missing row decided a refusal rather than only that one
    // did. Null while nothing stands behind the seam.
    producer_answer: reported,
    // WHAT IS STILL UNDECIDED, and as this module ships the answer is NOTHING:
    // the list is EMPTY, and it is empty by derivation rather than by anyone
    // having trimmed it.
    // The three reader questions left on 2026-09-11, when Joe ruled cards 11,
    // 12 and 13 and the readers those rulings switched on became bound above.
    // Each is listed again the moment its own ruling line goes back to null,
    // ONE ENTRY PER WITHDRAWN CARD in card order — so withdrawing card 12
    // reopens the scheduler question and not the other two, which is the defect
    // this list carried until the 2026-09-12 review.
    // CARD 9'S QUESTION IS DERIVED FROM THE SEAT, not carried as a literal: it
    // was listed while nobody held the oracle, it is gone now that the Codex
    // reviewer lane holds it, and it comes back the moment the declaration goes
    // back to unstaffed. Card 10's left on 2026-09-12 with the amendment.
    // AN EMPTY LIST IS NOT AN ALLOW. Every governance question being answered
    // is exactly why the refusal beside it names a MISSING ROW and nothing else
    // — there is no question left to hide behind, and no seam either.
    undecided_governance_questions: deepFreeze([
      ...(oracleSeatBound()
        ? [] : ["which independent seat holds oracle:gate-producer:gate-zero-read-only"]),
      ...owed.map(key => V5_A02_GATE_ZERO_READER_QUESTIONS[key]),
    ]),
  };

  if (outcomeProduced) return produced2Answer(body, reported, passes);
  return unavailable(
    "gate_zero_outcome_emission",
    reported === null ? "gate_zero_producer_seam_unavailable" : reported.reason_id,
    reported === null ? unavailableBecause(owed, readersBound) : reported.unavailable_because,
    [...V5_A02_GATE_ZERO_OWED_SEAMS],
    body,
    // WHO DECIDED THIS REFUSAL. While no reader is bound it is decided by there
    // being nothing authoritative to read; once the three ruled readers are
    // bound it is decided by the producer fact that is true at the time — an
    // unstaffed seat, an unbuilt seam, or a built one whose rows did not hold.
    readersBound ? producerDecidedBy() : "no_authoritative_reader");
}

/**
 * THE SENTENCES FOR AN UNBOUND PRODUCER SEAM, and this function is reached only
 * while the seam is unbound — which, since the seam's binding condition IS card
 * 9's declaration, means one of exactly two things.
 *
 * THE ORDINARY ONE is an unstaffed seat, and those sentences are main's own,
 * word for word: putting `holder_ref` back to null returns this surface to
 * exactly what it said before this slice.
 *
 * THE OTHER IS FAIL-CLOSED RATHER THAN EXPECTED. `boundSeam` also answers null
 * when the holder thunk throws or hands back something without the method, so a
 * staffed seat with no resolvable holder lands here too. It is not "the seam is
 * unbuilt" any more — the module is right there — so that branch says what is
 * actually true instead of repeating a sentence this slice made false.
 */
function unavailableBecause(owed, readersBound) {
  if (readersBound)
    return oracleSeatBound()
      ? "the three ruled evidence seams are bound and the registered producer role is held by a staffed seat, and the producer behind that seam did not resolve"
      : "the three ruled evidence seams are bound, and the registered producer role is held by a charter that no seat staffs";
  if (owed.length === V5_A02_GATE_ZERO_READER_SEAM_KEYS.length)
    return oracleSeatBound()
      ? "the producer behind the seam did not resolve and no evidence seam is bound to this surface, so nothing here can produce or stand behind a Gate Zero outcome"
      : "no seat holds the oracle and no evidence reader is bound to this surface, so nothing here can produce or stand behind a Gate Zero outcome";
  return oracleSeatBound()
    ? "not every ruled evidence seam is bound, and the producer behind the seam the staffed seat works through did not resolve"
    : "not every ruled evidence seam is bound, and the registered producer role is held by a charter that no seat staffs";
}

/**
 * WHY THIS ANSWER IS NOT A SIGNATURE. Derived in all four states, so the
 * sentence a consumer reads is the one that is true when it reads it: an
 * unstaffed seat, an unbuilt seam, a withdrawn reader, or — now that the seam is
 * built — a producer that refused over the rows it was aimed at.
 */
function notPassableBecause(owed, readersBound, bound, reported) {
  if (bound && reported !== null)
    return "the bound producer refused over the rows the three ruled evidence seams returned, so there is nothing to sign";
  if (bound)
    return "the bound producer did not answer in its own closed shape, so nothing it returned may be read as an outcome";
  if (readersBound)
    return oracleSeatBound()
      ? "the producer behind the seam did not resolve, so nothing may aim the three bound evidence seams at the rows a Gate Zero run would stand on, whoever holds the seat"
      : "the registered producer role is unstaffed, so nothing may aim the three bound evidence seams at the rows a Gate Zero run would stand on";
  if (owed.length === V5_A02_GATE_ZERO_READER_SEAM_KEYS.length)
    return oracleSeatBound()
      ? "the producer behind the seam did not resolve, and no predecessor, scheduler or gate-conclusion evidence seam is bound to this surface"
      : "the registered producer role is unstaffed, and no predecessor, scheduler or gate-conclusion reader is bound to this surface";
  return oracleSeatBound()
    ? "the producer behind the seam did not resolve, and not every ruled evidence seam is bound"
    : "the registered producer role is unstaffed, and not every ruled evidence seam is bound";
}

/**
 * THE REASON THERE IS NO JOIN, PER CARD. The 2026-09-12 review found this
 * sentence claiming no predecessor or scheduler reader existed while both were
 * bound; it now says which of the two is missing, and says neither is when
 * neither is.
 */
function joinUnavailableBecause(owed) {
  if (owed.includes("predecessor") && owed.includes("scheduler"))
    return "no authoritative predecessor-outcome or scheduler reader exists to join";
  if (owed.includes("predecessor"))
    return "no authoritative accepted-outcome seam is bound, so there is nothing for a scheduler canary to join against";
  if (owed.includes("scheduler"))
    return "the ruled accepted-outcome seam is bound and no authoritative scheduler surface is bound, so there is nothing to join it against";
  return `the two ruled evidence seams are bound and ${noProducerBecause("the rows to join")}`;
}

/**
 * THE ONE ANSWER ON THIS SURFACE THAT IS NOT A REFUSAL, and every field of it is
 * the producer's or this module's own constant. `decision` is "report", never
 * "allow": a Gate Zero outcome is a statement about rows, and what a consumer
 * does with it is the consumer's gate to decide.
 */
function produced2Answer(body, reported, passes) {
  const stands_on = [...V5_A02_GATE_ZERO_OWED_SEAMS].sort();
  return deepFreeze({
    schema_version: V5_A02_GATE_ZERO_SCHEMA_VERSION,
    policy_version: V5_A02_POLICY_VERSION,
    answer: "gate_zero_outcome_emission",
    tenant: ORGANIZATION_TENANT_ID,
    gate_zero_step_ref: GATE_ZERO_STEP_REF,
    status: "outcome_produced",
    decision: "report",
    // NULL ON A PASS, AND THE CLAUSE'S OWN ID ON A FAIL. An outcome that says
    // `fail` is still an outcome — it has a digest, an instant and twenty-one
    // fields — and a consumer reading only this answer must be able to see WHY
    // it is not a pass without opening the receipt.
    reason_id: passes ? null : reported.reason_id,
    unavailable_because: passes ? null : reported.unavailable_because,
    receipt_status: reported.receipt.status,
    owed_seams: stands_on.filter(seam => !seamBound(seam)),
    seams_bound: seamsBound(stands_on),
    request_read: false,
    caller_evidence_admitted: false,
    decided_by: producerDecidedBy(),
    model_judgment_admitted: false,
    effects: V5_NO_EFFECTS,
    receipt: reported.receipt,
    receipt_schema_ref: reported.receipt_schema_ref,
    outcome_digest_recipe: reported.outcome_digest_recipe,
    negative_admission: reported.negative_admission,
    persisted: false,
    durable_outcome_record_required: true,
    ...body,
  });
}

// ---------------------------------------------------------------------------
// The closed, versioned policy preimage and its digest.
//
// Nothing situational is bound — no observation, canary, gate or instant, and no
// argument is read at all — so two callers describing the same policy reach the
// same digest. It is an identity for these bytes: not an acceptance, not a
// receipt, and not evidence for any consumer gate.
// ---------------------------------------------------------------------------

export const v5A02GateZeroPolicyPreimage = closedCallable(() => ({
    schema_version: V5_A02_GATE_ZERO_SCHEMA_VERSION,
    policy_version: V5_A02_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    decision_ids: [...V5_A02_DECISION_IDS],
    gate_zero_step_ref: GATE_ZERO_STEP_REF,
    predecessor_step_refs: [...V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS],
    scheduler_step_ref: V5_A02_SCHEDULER_STEP_REF,
    observation_states: [...V5_A02_OBSERVATION_STATES].sort(),
    acceptance_states: [...V5_A02_ACCEPTANCE_STATES].sort(),
    criteria_states: [...V5_A02_CRITERIA_STATES].sort(),
    gate_conclusions: [...V5_A02_GATE_CONCLUSIONS].sort(),
    reason_ids: [...V5_A02_GATE_ZERO_REASON_IDS].sort(),
    owed_seams: [...V5_A02_GATE_ZERO_OWED_SEAMS],
    producer_seam: V5_A02_GATE_ZERO_PRODUCER_SEAM,
    producer_registration: V5_A02_GATE_ZERO_PRODUCER_REGISTRATION,
    // DERIVED, not asserted — and this is the field this slice moved. A frozen
    // `false` here was a statement about the repository, and the repository
    // changed: something implements the producer now. It says false again the
    // moment card 9's seat declaration or this module's binding does.
    producer_bound: producerSeamBound(),
    // DERIVED, not asserted: three of the four seams have a ruled reader behind
    // them now, and this preimage would be lying if it still said false. It says
    // false again the moment a ruling line does.
    authoritative_readers_bound: allReaderSeamsBound(),
    // `public_surface_answers: "unavailable"` and `gate_zero_passable: false`
    // are GONE rather than reworded. Both were frozen statements that the
    // surface can never say yes, and the surface can now say yes — over rows,
    // through a bound producer. A policy preimage that still carried them would
    // be a sealed lie, and a derived version of either would put a per-run
    // verdict inside a caller-independent identity. What is left says what is
    // true of the POLICY: which seams exist, which are bound, and what the
    // vocabulary is.
  }));

export const v5A02GateZeroPolicyDigest =
  closedCallable(() => digest(v5A02GateZeroPolicyPreimage()));

export const v5A02GateZeroPolicyCanonicalBytes =
  closedCallable(() => canonicalJson(v5A02GateZeroPolicyPreimage()));

// ---------------------------------------------------------------------------
// THE R7 CHECK lives in gate-zero-producer-registration.v5.js, beside the
// registration whose bytes it verifies, and is re-exported above as
// `v5A02GateZeroR7Presence`. It used to be duplicated here, which is how the
// registration's own field came to be "derived" from a comparison of two pinned
// literals while the real byte verifier sat beside it deciding nothing.
// ---------------------------------------------------------------------------
