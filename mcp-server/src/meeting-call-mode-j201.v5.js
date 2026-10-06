// DoctorCRE v5 slice V5-J201 — non-recording Meeting and Call Mode, as a pure
// deterministic kernel.
//
// Four settled decisions (Q059.D3, Q074.D1, Q088.D1, Q123.D2) are encoded here
// as ONE closed, versioned domain contract with a deterministic digest, plus
// pure evaluators over that contract. Canonicalization and hashing come from
// artifact-trust.js, the no-effects marker and the privacy boundary come from
// global-boundaries.v5.js, the record homes and evidence vocabulary come from
// record-source-authority.v5.js (V5-F01), and the partner predicate comes from
// identity.js. This file reimplements none of them.
//
// THE ONE SENTENCE THE WHOLE SLICE TURNS ON, from Q088.D1: DETECTION CREATES A
// PROMPT, NOT PERMISSION TO RECORD. Everything below is arranged so that no
// input, in any order, can turn an observation into a recording. `recording` is
// the literal string "denied" on every result this module can produce, success
// and refusal alike, and there is no field, flag, policy input or seam through
// which a caller can move it. The recording policy itself is TABLED, not
// decided; V5_J201_RECORDING_POLICY_SEAM names where a future decision would
// live, so a reader cannot mistake this refusal for a ruling and cannot mistake
// the absence of a ruling for a decision.
//
// WHAT IS CODE HERE AND WHAT MUST ARRIVE AS TYPED POLICY, because the line is
// the point of the slice:
//
//   IN CODE — the STRUCTURE the four decisions settle. That a calendar entry
//   alone never suffices; that an `unknown` signal never corroborates; that
//   reconciliation keys on native source identity and never on time overlap;
//   that a recycled native id refuses instead of merging; that activation is an
//   explicit human one-tap and nothing else; that models may only speak after
//   activation and only into declared seams. These are identity, not knobs.
//
//   AS TYPED POLICY INPUT — the detection WINDOW and the corroboration COUNT.
//   How many seconds before a calendar start a meeting may be observed, how many
//   after its end, and how many corroborating signals a deployment demands. This
//   module invents none of them and ships no default for any of them; a missing
//   or malformed detection policy refuses. ONE FLOOR IS STRUCTURE RATHER THAN
//   POLICY and is named here so it is not mistaken for the other kind: the
//   required corroborating-signal count may be RAISED by policy but may never be
//   lowered below one, because "calendar plus at least one other indicator" is
//   the settled requirement itself (Q088.D1, and Joe's own wording on it) rather
//   than a tuning choice.
//
// TWO KINDS OF NO, following the sibling v5 modules deliberately:
//   * A POLICY ANSWER is returned — a frozen result whose `decision` is one of
//     this module's registered values, with a stable `reason_id`. A refusal is
//     an answer the caller may record. "The calendar source is not available" is
//     one of these: an unbound or undeployed adapter is a thing a caller is
//     allowed to report, and it refuses with `calendar_source_unavailable`
//     rather than being stubbed into a pretend success.
//   * A CONTRACT VIOLATION throws V5J201Error. Unknown fields, unknown
//     vocabulary values, open schemas, malformed Unicode, unreadable timestamps
//     and any field whose NAME reaches for audio are not policy questions; the
//     module cannot read the request at all, so it fails closed rather than
//     guessing which settled boundary was meant.
//
// THE UPSTREAM SEAM, AND WHY IT IS A SEAM. Q059.D3 and this slice's admission
// note both say to bind whichever authorized Calendar/presence adapter is
// current. V5-F10 (partner-scoped Mail and Calendar adapters) is the adapter
// that will most likely supply it, and its read contract is the shape this
// module validates against — but F10 had not merged when this slice was built,
// so THIS FILE IMPORTS NOTHING FROM IT. `bindMeetingSourceAdapter` takes a
// descriptor from ANY source and checks it against this module's own declared
// read contract. F10 satisfies that contract; so would a different authorized
// adapter; an absent one refuses honestly. Nothing here depends on F10 landing.
//
// THE ONE-AUTHORITY RULE. This module stores no ledger, no prompt history, no
// session, no note, no meeting and no receipt, so it creates no second
// authority. Every function is pure: no filesystem, no network, no database, no
// provider, no scheduler, no environment and no clock. Every evaluation that
// depends on time takes `now` from its caller. `V5_NO_EFFECTS` rides on every
// result to say so in the record.
//
// WHAT THIS FILE IS NOT. It is not the local companion process, not a Teams or
// Zoom client, not an audio subsystem, not a notification surface, not
// persistence and not an acceptance path. IT ALSO RAISES NO PROMPT TODAY: the
// durable owner of the prompt ledger is not built here, a stateless kernel
// cannot be handed one it can trust, and so `evaluateActivationPrompt` refuses
// every ledger rather than prompting on a caller's say-so. It launches nothing, opens no device
// and reads no calendar; every fact it decides on is a TYPED OBSERVATION THE
// CALLER SUPPLIES. It does not make J201 complete: the companion, the prompt
// surface, the note store and the governed production outcomes are all deferred,
// and `meetingModeGaps()` says so by name.

import { canonicalJson, digest } from "./artifact-trust.js";
import { ORGANIZATION_TENANT_ID, authorizationClassForActor, isKnownPartner } from "./identity.js";
import {
  V5_NO_EFFECTS,
  V5_DATA_CLASSES,
  evaluatePrivacyBoundary,
} from "./global-boundaries.v5.js";
import {
  V5_F01_EVIDENCE_CLASSES,
  V5_F01_HOMES,
  V5_F01_TAINT_CLASSES,
} from "./record-source-authority.v5.js";

export { V5_NO_EFFECTS };

export const V5_J201_SCHEMA_VERSION = "doctorcre-v5-meeting-call-mode.v1";
export const V5_J201_POLICY_VERSION = 1;

export const V5_J201_READ_CONTRACT_SCHEMA_VERSION =
  "doctorcre-v5-j201-meeting-source-read-contract.v1";
export const V5_J201_OBSERVATION_SCHEMA_VERSION =
  "doctorcre-v5-j201-meeting-observation.v1";
export const V5_J201_RECONCILIATION_SCHEMA_VERSION =
  "doctorcre-v5-j201-meeting-reconciliation.v1";
export const V5_J201_PROMPT_SCHEMA_VERSION =
  "doctorcre-v5-j201-activation-prompt.v1";
export const V5_J201_SESSION_SCHEMA_VERSION =
  "doctorcre-v5-j201-meeting-mode-session.v1";
export const V5_J201_PROPOSAL_SCHEMA_VERSION =
  "doctorcre-v5-j201-model-proposal.v1";
export const V5_J201_CANDIDATE_SCHEMA_VERSION =
  "doctorcre-v5-j201-meeting-record-link-candidate.v1";
export const V5_J201_PROJECTION_SCHEMA_VERSION =
  "doctorcre-v5-j201-meeting-mode-projection.v1";

// ---------------------------------------------------------------------------
// Local primitives. Each v5 module carries its own copy on purpose: a shared
// assertion library would become a place a caller could weaken one module's
// floor by editing another's.
// ---------------------------------------------------------------------------

const EXTERNAL_IDENT = /^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,254}$/;
// Control characters, bidirectional overrides, zero-width and other invisible
// format characters. An identifier that renders as another identifier is an
// identity split waiting to happen, so it is refused rather than normalized.
const UNSAFE_TEXT =
  /[\u0000-\u001F\u007F-\u009F\u200B-\u200F\u202A-\u202E\u2060-\u2064\u2066-\u2069\uFEFF]/u;
const ISO_INSTANT =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(?:Z|([+-])(\d{2}):(\d{2}))$/;

/**
 * The separator used to fold a native-identity triple into one meeting key.
 *
 * THE INVARIANT: the separator must be a character no validated identifier can
 * contain, or two different meetings could fold to one key and Joe's meeting
 * would silently swallow Dell's. NUL is refused by assertSafeText and is outside
 * EXTERNAL_IDENT, so it can never appear in any part; the suite proves that
 * directly rather than assuming it. WRITTEN AS AN ESCAPE, NEVER AS A LITERAL.
 */
const KEY_SEPARATOR = "\u0000";

export class V5J201Error extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = "V5J201Error";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function fail(code, message, detail) {
  throw new V5J201Error(code, message, detail);
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

/** An open schema is an unenforced one: an unread field is a field nobody checked. */
function assertClosedKeys(object, allowed, path) {
  for (const key of Object.keys(object)) {
    if (!allowed.includes(key)) {
      fail("unknown_field", `unknown field "${key}" at ${path}`, { path: `${path}.${key}`, key });
    }
  }
}

function assertRequiredKeys(object, required, path) {
  for (const key of required) {
    if (!(key in object)) fail("missing_field", `${path}.${key} is required`, { path: `${path}.${key}` });
  }
}

function assertObject(value, path) {
  if (!isPlainObject(value)) fail("invalid_shape", `${path} must be a plain object`, { path });
  return value;
}

function assertArray(value, path, { min = 0, max = 256 } = {}) {
  if (!Array.isArray(value)) fail("invalid_shape", `${path} must be an array`, { path });
  if (value.length < min) {
    fail("invalid_shape", `${path} must hold at least ${min} entries`, { path, length: value.length });
  }
  if (value.length > max) {
    fail("too_many_entries", `${path} may hold at most ${max} entries`, { path, length: value.length });
  }
  return value;
}

function assertSafeText(value, path, { maxLength = 512 } = {}) {
  if (typeof value !== "string" || value.length === 0) {
    fail("invalid_shape", `${path} must be a non-empty string`, { path });
  }
  if (value.length > maxLength) {
    fail("text_too_long", `${path} may be at most ${maxLength} characters`, { path, length: value.length });
  }
  if (typeof value.isWellFormed === "function" && !value.isWellFormed()) {
    fail("malformed_unicode", `${path} contains an unpaired surrogate`, { path });
  }
  if (UNSAFE_TEXT.test(value)) {
    fail("unsafe_unicode", `${path} contains a control, bidirectional or invisible format character`, { path });
  }
  return value;
}

function assertExternalIdent(value, path, { maxLength = 255 } = {}) {
  assertSafeText(value, path, { maxLength });
  if (!EXTERNAL_IDENT.test(value)) {
    fail("invalid_identifier", `${path} is not a permitted external identifier`, { path });
  }
  return value;
}

function assertEnum(value, registered, path, code) {
  if (typeof value !== "string" || !registered.includes(value)) {
    fail(code, `"${String(value)}" is not registered at ${path}`,
      { path, value: typeof value === "string" ? value : null, registered: [...registered] });
  }
  return value;
}

function assertSafeInteger(value, path, { min = 0, max = Number.MAX_SAFE_INTEGER } = {}) {
  if (!Number.isSafeInteger(value) || value < min || value > max) {
    fail("invalid_shape", `${path} must be a safe integer between ${min} and ${max}`, { path });
  }
  return value;
}

function daysInMonth(year, month) {
  if (month === 2) return (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0 ? 29 : 28;
  return [4, 6, 9, 11].includes(month) ? 30 : 31;
}

/**
 * Instants are parsed, never inferred, and the CALENDAR is checked against the
 * literal fields before parsing. Date.parse normalizes an impossible date into a
 * different one — "2026-02-31T00:00:00Z" silently becomes 3 March — and a
 * meeting window computed from an instant nobody wrote would open at the wrong
 * moment. This slice decides whether a partner is in a meeting right now from
 * calendar timing; it does not get to be careless about a clock.
 */
function assertInstant(value, path) {
  const match = typeof value === "string" ? ISO_INSTANT.exec(value) : null;
  if (!match) {
    fail("invalid_timestamp", `${path} must be an ISO-8601 instant with an explicit offset`, { path, value });
  }
  const [, year, month, day, hour, minute, second, , offsetHour, offsetMinute] = match;
  const y = Number(year), mo = Number(month), d = Number(day);
  const h = Number(hour), mi = Number(minute), s = Number(second);
  if (mo < 1 || mo > 12 || d < 1 || d > daysInMonth(y, mo) || h > 23 || mi > 59 || s > 59 ||
      (offsetHour !== undefined && (Number(offsetHour) > 23 || Number(offsetMinute) > 59))) {
    fail("invalid_timestamp",
      `${path} names an instant that does not exist on the calendar; it is not normalized into a different one`,
      { path, value });
  }
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) fail("invalid_timestamp", `${path} is not a readable instant`, { path, value });
  return parsed;
}

function assertTenant(value, path) {
  if (value !== ORGANIZATION_TENANT_ID) {
    fail("tenant_mismatch", `${path} must be "${ORGANIZATION_TENANT_ID}"`,
      { path, expected: ORGANIZATION_TENANT_ID });
  }
  return value;
}

// ---------------------------------------------------------------------------
// The settled decisions. Text and evidence digests are copied verbatim from the
// reviewed r7 design basis (document doctorcre-v5-design-basis, artifact
// doctorcre-v5-design-basis-r7-review.json, whose reassembled bytes hash to
// ef34aa54740dd56508b7cebf05a2a95851aacedbbe4f2e4865a39ffede28f0ad). They are
// identity, not configuration, and they are hashed into the policy preimage so
// a drifted copy moves the digest instead of quietly disagreeing.
// SUPERSEDED DIGEST, kept because it is the packet these rows were copied from:
// the 2026-09-12 Gate Zero amendment re-froze r7 to
// ea40f61a9081814e53c989f2f945c61b270597cdfeafc4ec535578e60462a8f6, and the
// 2026-09-13 consumer-gate-receipt digest amendment re-froze it again to
// 4379c60e9a4fefbcf044f4bc5a34e5a95c90f77b9adf348b6da7475e17d5e6d7. Neither changed
// exactly one decision row, Q036.D1's consumer_gates, and none of the rows below.
// ---------------------------------------------------------------------------

export const V5_J201_SETTLED_DECISIONS = deepFreeze({
  "Q059.D3": {
    settled_requirement: "Next connect Calendar plus Teams or Zoom presence to a one-tap Meeting Mode prompt and route meeting outputs into the same records.",
    source_evidence_digest: "5c58f2233ea82aab0f66bd966467e24875504900bd96fa98be4fa300436fd513",
  },
  "Q074.D1": {
    settled_requirement: "Calendar plus Teams or Zoom presence should prompt once for Meeting Mode rather than record silently; Joe and Dell duplicates reconcile by meeting and native source identity.",
    source_evidence_digest: "065e0061ba243fb9ea90aea94ec9f8b6b10dc6f01a516a1a097f8d6fc4894c24",
  },
  "Q088.D1": {
    settled_requirement: "Infer likely meetings from Calendar timing plus Teams or Zoom presence, audio session, and device signals; prompt once for activation and never treat detection as permission to record.",
    source_evidence_digest: "f8c76d7389f22faaa2510910f7e4f37f001eca5bc06729865478edf1506b1f97",
  },
  "Q123.D2": {
    settled_requirement: "Add Call Mode as the next current product journey without blocking the first launch.",
    source_evidence_digest: "55457ebf6cdb2c1959eadcb5358db6bf67a7366d4f941c28be19a5ddaf397b35",
  },
});

export const V5_J201_SETTLED_DECISION_IDS =
  deepFreeze(Object.keys(V5_J201_SETTLED_DECISIONS).sort());

/**
 * Refuse a caller whose decision subset has drifted from the reviewed one.
 *
 * Drift is checked in BOTH directions — a missing decision and an extra one are
 * both drift — and every source-evidence digest must match exactly. A caller
 * that believes it holds a different subset can prove the disagreement here
 * rather than discovering it after a prompt fired on the wrong rule.
 */
export function assertJ201DecisionBinding(binding) {
  assertObject(binding, "binding");
  assertClosedKeys(binding, ["decisions"], "binding");
  assertRequiredKeys(binding, ["decisions"], "binding");
  const supplied = assertObject(binding.decisions, "binding.decisions");
  const suppliedIds = Object.keys(supplied).sort();
  for (const id of V5_J201_SETTLED_DECISION_IDS) {
    if (!suppliedIds.includes(id)) {
      fail("decision_subset_drift", `binding.decisions is missing "${id}"`,
        { missing: id, expected: [...V5_J201_SETTLED_DECISION_IDS] });
    }
  }
  for (const id of suppliedIds) {
    if (!V5_J201_SETTLED_DECISION_IDS.includes(id)) {
      fail("decision_subset_drift", `binding.decisions carries an unregistered decision "${id}"`,
        { unexpected: id, expected: [...V5_J201_SETTLED_DECISION_IDS] });
    }
    const entry = assertObject(supplied[id], `binding.decisions.${id}`);
    assertClosedKeys(entry, ["settled_requirement", "source_evidence_digest"], `binding.decisions.${id}`);
    assertRequiredKeys(entry, ["settled_requirement", "source_evidence_digest"], `binding.decisions.${id}`);
    const reviewed = V5_J201_SETTLED_DECISIONS[id];
    if (entry.settled_requirement !== reviewed.settled_requirement) {
      fail("decision_text_drift", `binding.decisions.${id}.settled_requirement does not match the reviewed text`,
        { decision_id: id });
    }
    if (entry.source_evidence_digest !== reviewed.source_evidence_digest) {
      fail("decision_evidence_drift", `binding.decisions.${id}.source_evidence_digest does not match the reviewed digest`,
        { decision_id: id, expected: reviewed.source_evidence_digest });
    }
  }
  return deepFreeze({
    decisions_bound: [...V5_J201_SETTLED_DECISION_IDS],
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// The closed vocabularies. Every one of them is hashed into the policy preimage
// below, so a vocabulary that changes moves the digest and stale readers are
// refused rather than silently reading an axis that moved under them.
// ---------------------------------------------------------------------------

/** The two conferencing platforms the settled decisions name, and no others. */
export const V5_J201_PLATFORMS = deepFreeze(["teams", "zoom"]);

/**
 * The four signal kinds Q088.D1 names. `calendar_event` is the anchor; the other
 * three are the corroboration Joe insisted on — "calendar is the biggest
 * indicator ... but the other indicators will need to be applied to ensure im
 * actually on the meeting."
 */
export const V5_J201_SIGNAL_KINDS = deepFreeze([
  "audio_session", "calendar_event", "device_state", "presence_session",
]);

export const V5_J201_CORROBORATING_SIGNAL_KINDS = deepFreeze([
  "audio_session", "device_state", "presence_session",
]);

/**
 * What each corroborating signal may say. Every vocabulary carries an explicit
 * `unknown`, and `unknown` NEVER corroborates: a signal nobody could read is not
 * evidence that a partner is in a meeting, and reading silence as presence is
 * exactly the shape of claim this slice must not make.
 */
export const V5_J201_PRESENCE_STATES = deepFreeze([
  "joined", "not_joined", "unknown",
]);
export const V5_J201_AUDIO_SESSION_STATES = deepFreeze([
  "conference_process_holds_input_device", "no_conference_process", "unknown",
]);
export const V5_J201_DEVICE_STATES = deepFreeze([
  "locked_or_idle", "unlocked_and_attended", "unknown",
]);

/** The one affirmative value of each corroborating vocabulary, as a table. */
const AFFIRMATIVE_SIGNAL_STATE = deepFreeze({
  presence_session: "joined",
  audio_session: "conference_process_holds_input_device",
  device_state: "unlocked_and_attended",
});

const SIGNAL_STATE_VOCABULARY = deepFreeze({
  presence_session: V5_J201_PRESENCE_STATES,
  audio_session: V5_J201_AUDIO_SESSION_STATES,
  device_state: V5_J201_DEVICE_STATES,
});

/** The closed answer set of evaluateMeetingObservation. */
export const V5_J201_OBSERVATION_DECISIONS = deepFreeze([
  "observe_meeting", "refuse", "withhold_insufficient_corroboration",
  "withhold_outside_calendar_window",
]);

/**
 * The closed answer set of evaluateActivationPrompt. BOTH MEMBERS ARE NEGATIVE,
 * and that is the honest state of this slice rather than an oversight: the
 * durable owner of the prompt ledger does not exist in this repository, so
 * nothing can tell this kernel whether a meeting was already prompted. There is
 * no third member for a caller to reach and no input that reaches one.
 */
export const V5_J201_PROMPT_DECISIONS = deepFreeze([
  "refuse_unproven_prompt_ledger", "suppress_not_observed",
]);

/**
 * The answer set of the once-only rule itself — WRITTEN DOWN, UNIT TESTED AND
 * DELIBERATELY NOT WIRED. `unwiredOnceOnlyPromptDecision` returns one of these
 * NAMES and nothing else: not a result, not a prompt, not anything
 * activateMeetingMode will accept. It is the rule the durable prompt-ledger
 * owner will run once that owner exists (V5_J201_PROMPT_LEDGER_OWNER_SEAM).
 */
export const V5_J201_UNWIRED_ONCE_ONLY_DECISIONS = deepFreeze([
  "prompt_once", "suppress_already_active", "suppress_already_prompted",
  "suppress_dismissed",
]);

/**
 * The ONE way back from a dismissal, kept as a constant so that a settled fact
 * does not vanish along with the code path that used to carry it. Q074.D1 is
 * explicit that a dismissal suppresses repeat prompts for that meeting unless it
 * is MANUALLY REOPENED, and "manually reopened" is a human action this module
 * does not model and cannot infer. Whoever builds the ledger owner owes this
 * path; nothing automatic may clear a dismissal.
 */
export const V5_J201_DISMISSAL_REOPEN_REQUIRES = "manual_human_reopen";

/**
 * WHERE A PROMPT LEDGER CAME FROM — A CLAIM THE CALLER MAKES, NEVER AUTHORITY.
 *
 * "Prompt once" (Q074.D1, Q088.D1) is a statement about a meeting's whole life,
 * not about one call. This module is a pure kernel: it holds no state between
 * calls, so it cannot enforce once-only on its own, and no amount of care inside
 * this file changes that. The authoritative owner of the prompt ledger — the
 * durable store that would know what has already been prompted, dismissed or
 * activated — DOES NOT EXIST IN THIS REPOSITORY.
 *
 * THE CORRECTION THAT PRODUCED THIS SHAPE, written down because the mistake is
 * the attractive one: an earlier version of this file accepted a ledger whose
 * `provenance.class` read `authoritative_durable_store` and prompted on it. That
 * is a caller-supplied label standing in for a fact. A string in a request
 * cannot make a durable store exist, and a kernel that believes one has simply
 * moved the trust decision to whoever writes the request. So the class below is
 * RECORDED in the refusal and CREDITED WITH NOTHING: no class, no owner name, no
 * read time and no combination of them reaches a prompt or a suppression.
 *
 *   authoritative_durable_store — what a caller may CLAIM. Nothing here can
 *     check it, so it refuses exactly like the other one.
 *   caller_supplied_unproven — assembled in memory, reconstructed, defaulted,
 *     or of unknown origin.
 *
 * The vocabulary survives because the contract still asks the caller to SAY
 * where the ledger came from — a claim in the transcript is worth having — not
 * because saying it buys anything.
 */
export const V5_J201_LEDGER_PROVENANCE_CLASSES = deepFreeze([
  "authoritative_durable_store", "caller_supplied_unproven",
]);

/**
 * The provenance classes this module will ACT on: none, until the durable
 * prompt-ledger owner at V5_J201_PROMPT_LEDGER_OWNER_SEAM exists and this kernel
 * is given something it can verify. It is an EMPTY LIST rather than an absent
 * one so the emptiness is a fact a test can pin and a mutation can move.
 */
export const V5_J201_TRUSTED_LEDGER_PROVENANCE_CLASSES = deepFreeze([]);

/**
 * Where the durable prompt-ledger owner will live. A seam, for the same reason
 * as the other two: "this slice does not own the ledger" must not read as "no
 * ledger owner is needed", and it must not read as a decision that came back no.
 */
export const V5_J201_PROMPT_LEDGER_OWNER_SEAM =
  "step:v5-j201-durable-prompt-ledger-owner";

/** The closed answer set of reconcileMeetingObservations. */
export const V5_J201_RECONCILIATION_DISPOSITIONS = deepFreeze([
  "distinct_meetings", "refuse_ambiguous_identity", "single_meeting",
]);

/** Where a Meeting Mode session can stand. There is no recording state. */
export const V5_J201_MODE_STATES = deepFreeze([
  "active_non_recording", "ended", "off", "prompted",
]);

/**
 * The recording answer, as a constant rather than a vocabulary. A vocabulary is
 * a set a value can move within; this is not one. Every result carries it.
 */
export const V5_J201_RECORDING_STATE = "denied";

/**
 * Where a recording decision WOULD be made if this product ever gained one.
 * Named as a seam so a reader cannot mistake the refusal for "never" and cannot
 * mistake the absence of a decision for a decision. Q123.D2's acceptance
 * predicate requires recording to remain denied behind its separate policy gate
 * while Call Mode ships, which is precisely what this constant records.
 */
export const V5_J201_RECORDING_POLICY_SEAM =
  "step:v5-j201-recording-policy-decision";

/**
 * The one activation intent this module accepts. Anything else refuses, and the
 * three named below refuse BY NAME so the transcript shows which silent path was
 * attempted rather than a generic unknown value.
 */
export const V5_J201_EXPLICIT_ACTIVATION_INTENT = "one_tap_user_activation";
export const V5_J201_REFUSED_ACTIVATION_INTENTS = deepFreeze([
  "automatic_on_detection", "inherited_from_previous_meeting", "silent_activation",
]);

/**
 * The two seams a model may speak into after activation, with their closed label
 * sets. The slice's model_judgment_boundary is exact: models may summarize notes
 * and propose follow-ups after explicit activation; deterministic detection,
 * dedupe and permission own state. A proposal is therefore a LABEL plus a
 * reference, never a state transition and never free-form authority.
 */
export const V5_J201_MODEL_SEAMS = deepFreeze({
  note_summary_kind: ["action_items", "decisions_reached", "discussion_summary", "unclassified"],
  follow_up_kind: ["introduce_parties", "schedule_next_meeting", "send_document", "update_deal_record"],
});
export const V5_J201_MODEL_SEAM_KEYS = deepFreeze(Object.keys(V5_J201_MODEL_SEAMS).sort());

/**
 * Field-name fragments that mean a caller reached for audio, recording, or a
 * transcript. Checked on NAMES, before any value is read, because the check has
 * to work without ever looking at the thing it is refusing. The fragments are
 * chosen not to collide with the metadata this slice DOES carry: `content_digest`
 * and `byte_length` name a measurement of bytes, never bytes, and no field here
 * is called anything on this list.
 */
export const V5_J201_RECORDING_FRAGMENTS = deepFreeze([
  "audio", "capture", "diariz", "listen", "mic", "pcm", "record",
  "speech", "stream", "transcri", "voice", "waveform",
]);

/**
 * Field-name fragments that mean a proposal reached past classification into
 * authority, permission or state. A model result is a suggestion; a model result
 * that can move a permission is a second authority.
 */
export const V5_J201_MODEL_WIDENING_FRAGMENTS = deepFreeze([
  "activate", "actor", "authority", "authorization", "capability", "consent",
  "grant", "mode_state", "override", "partner", "permission", "privilege",
  "scope", "tenant",
]);

/** This slice's own identity on every provenance record it builds. */
export const V5_J201_ADAPTER_KIND = "v5_j201_meeting_observation_adapter";

/**
 * The three upstream vocabulary values this module binds to BY IMPORT rather
 * than by literal, checked at module load. A calendar entry read out of a
 * partner mailbox is an F01 `corporate_mailbox_item` whose home is `outlook` and
 * whose taint is `untrusted_external`; taint is never lowered at an adapter
 * boundary, and a field a caller could set would be exactly the laundering seam
 * the rule forbids.
 */
export const V5_J201_EVIDENCE_CLASS = "corporate_mailbox_item";
export const V5_J201_AUTHORITATIVE_HOME = "outlook";
export const V5_J201_TAINT_CLASS = "untrusted_external";

/** One retrieval class per signal kind. This slice's identity, not an external fact. */
export const V5_J201_RETRIEVAL_CLASSES = deepFreeze({
  calendar_event: "partner_device_calendar_read",
  presence_session: "partner_device_presence_read",
  audio_session: "partner_device_audio_session_state_read",
  device_state: "partner_device_state_read",
});

/**
 * The data classes a meeting observation may declare. Event metadata and
 * user-entered notes only, per the slice's data_boundary. The privacy answer is
 * S01's, carried through by calling it, never a second copy of the PHI list.
 */
export const V5_J201_INTENDED_USE = "non_recording_meeting_observation";

if (!V5_F01_EVIDENCE_CLASSES.includes(V5_J201_EVIDENCE_CLASS)) {
  throw new V5J201Error("upstream_vocabulary_drift",
    `F01 no longer registers "${V5_J201_EVIDENCE_CLASS}"; this slice has no evidence class to emit`,
    { registered: [...V5_F01_EVIDENCE_CLASSES] });
}
if (!V5_F01_HOMES.includes(V5_J201_AUTHORITATIVE_HOME)) {
  throw new V5J201Error("upstream_vocabulary_drift",
    `F01 no longer registers the "${V5_J201_AUTHORITATIVE_HOME}" home`,
    { registered: [...V5_F01_HOMES] });
}
if (!V5_F01_TAINT_CLASSES.includes(V5_J201_TAINT_CLASS)) {
  throw new V5J201Error("upstream_vocabulary_drift",
    `F01 no longer registers the "${V5_J201_TAINT_CLASS}" taint class`,
    { registered: [...V5_F01_TAINT_CLASSES] });
}

// ---------------------------------------------------------------------------
// The source-agnostic read contract, and the adapter seam that satisfies it.
// ---------------------------------------------------------------------------

/**
 * The read operations this slice needs from whatever Calendar/presence adapter
 * is authorized. Named as OPERATIONS rather than as a module, so any adapter
 * that offers them satisfies the contract and no adapter is privileged by name.
 */
export const V5_J201_REQUIRED_READ_OPERATIONS = deepFreeze([
  "list_calendar_events", "read_calendar_event_metadata",
]);

/** The item kind those operations must carry. */
export const V5_J201_REQUIRED_ITEM_KIND = "calendar_event";

/** The adapter modes this slice will bind. It reads; it never writes a calendar. */
export const V5_J201_BOUND_OPERATION_MODE = "read";

/**
 * Where the authorized-adapter decision lives. Named as a seam for the same
 * reason as the recording seam: an unbound adapter must not read as "no adapter
 * is needed" and must not read as "the decision was made and came back no".
 */
export const V5_J201_CALENDAR_SOURCE_SEAM =
  "step:v5-j201-authorized-calendar-presence-adapter-binding";

/**
 * Where an adapter installation stands on the machine that would run it.
 * `unknown` sits with `not_deployed` on purpose, for the same reason `unknown`
 * never corroborates a signal: a deployment nobody has observed is not a
 * deployment.
 */
export const V5_J201_ADAPTER_DEPLOYMENT_STATES = deepFreeze([
  "deployed", "not_deployed", "unknown",
]);
export const V5_J201_NON_READING_DEPLOYMENT_STATES = deepFreeze([
  "not_deployed", "unknown",
]);

/** The closed answer set of bindMeetingSourceAdapter. */
export const V5_J201_BINDING_DECISIONS = deepFreeze([
  "bound", "refuse_adapter_unavailable", "refuse_read_contract_unsatisfied",
]);

const ADAPTER_DESCRIPTOR_KEYS = Object.freeze([
  "adapter_kind", "authorization_receipt_ref", "deployment_state",
  "evidence_class", "authoritative_home", "taint_class", "operations",
  "observer_account", "platforms",
]);
const ADAPTER_DESCRIPTOR_REQUIRED = Object.freeze([
  "adapter_kind", "deployment_state", "evidence_class", "authoritative_home",
  "taint_class", "operations", "observer_account", "platforms",
]);
const ADAPTER_OPERATION_KEYS = Object.freeze(["mode", "item_kind"]);
const BIND_REQUEST_KEYS = Object.freeze(["tenant", "adapter"]);

function bindingRefusal(reason_id, fields) {
  return deepFreeze({
    schema_version: V5_J201_READ_CONTRACT_SCHEMA_VERSION,
    decision: reason_id === "adapter_unavailable"
      ? "refuse_adapter_unavailable" : "refuse_read_contract_unsatisfied",
    reason_id,
    bound: false,
    calendar_source_seam: V5_J201_CALENDAR_SOURCE_SEAM,
    required_read_operations: [...V5_J201_REQUIRED_READ_OPERATIONS],
    recording: V5_J201_RECORDING_STATE,
    recording_permitted: false,
    ...fields,
    effects: V5_NO_EFFECTS,
  });
}

/**
 * Bind one authorized Calendar/presence adapter to this slice's read contract.
 *
 * ORDERED, so a second reader reaches the same answer from the transcript:
 *   1. The request must be readable, closed and bound to the one server-held
 *      tenant. Otherwise this throws.
 *   2. A descriptor field whose NAME reaches for audio throws before any value
 *      is read. An adapter that offers to record is not an adapter this slice
 *      declines to use; it is a request this slice cannot read.
 *   3. A descriptor that lowers taint, moves the home or changes the evidence
 *      class throws. Those three are upstream identity, not adapter options.
 *   4. A deployment state of `not_deployed` or `unknown` REFUSES with
 *      `adapter_unavailable`. This is the honest answer when the authorized
 *      adapter has not landed, and it is the answer a caller records instead of
 *      a fabricated success.
 *   5. Every required read operation must be present, in `read` mode, carrying
 *      the required item kind. A missing or mis-moded one refuses with
 *      `read_contract_unsatisfied`, naming what was missing.
 *   6. A bound result reports the read operations it bound and the write
 *      operations it deliberately did NOT bind, so a reader can see that the
 *      write half was seen and left alone rather than overlooked.
 */
export function bindMeetingSourceAdapter(request) {
  assertObject(request, "request");
  assertClosedKeys(request, BIND_REQUEST_KEYS, "request");
  assertRequiredKeys(request, BIND_REQUEST_KEYS, "request");
  assertTenant(request.tenant, "request.tenant");

  const raw = assertObject(request.adapter, "request.adapter");
  assertNoRecordingFields(raw, "request.adapter");
  assertClosedKeys(raw, ADAPTER_DESCRIPTOR_KEYS, "request.adapter");
  assertRequiredKeys(raw, ADAPTER_DESCRIPTOR_REQUIRED, "request.adapter");

  const adapter_kind = assertExternalIdent(raw.adapter_kind, "request.adapter.adapter_kind", { maxLength: 128 });
  const observer_account = assertExternalIdent(raw.observer_account, "request.adapter.observer_account", { maxLength: 128 });
  const deployment_state = assertEnum(raw.deployment_state, V5_J201_ADAPTER_DEPLOYMENT_STATES,
    "request.adapter.deployment_state", "unknown_deployment_state");

  if (raw.evidence_class !== V5_J201_EVIDENCE_CLASS) {
    fail("evidence_class_mismatch",
      `request.adapter.evidence_class must be "${V5_J201_EVIDENCE_CLASS}"`,
      { expected: V5_J201_EVIDENCE_CLASS, actual: raw.evidence_class });
  }
  if (raw.authoritative_home !== V5_J201_AUTHORITATIVE_HOME) {
    fail("authoritative_home_mismatch",
      `request.adapter.authoritative_home must be "${V5_J201_AUTHORITATIVE_HOME}"`,
      { expected: V5_J201_AUTHORITATIVE_HOME, actual: raw.authoritative_home });
  }
  if (raw.taint_class !== V5_J201_TAINT_CLASS) {
    fail("taint_lowering_refused",
      `request.adapter.taint_class must be "${V5_J201_TAINT_CLASS}"; taint is never lowered at an adapter boundary`,
      { expected: V5_J201_TAINT_CLASS, actual: raw.taint_class });
  }

  const platforms = assertArray(raw.platforms, "request.adapter.platforms", { min: 1, max: 2 })
    .map((p, i) => assertEnum(p, V5_J201_PLATFORMS, `request.adapter.platforms[${i}]`, "unknown_platform"))
    .sort();
  if (new Set(platforms).size !== platforms.length) {
    fail("duplicate_platform", "request.adapter.platforms repeats a platform", { platforms });
  }

  const operations = assertObject(raw.operations, "request.adapter.operations");
  const operationNames = Object.keys(operations).sort();
  if (operationNames.length === 0) {
    fail("invalid_shape", "request.adapter.operations must name at least one operation",
      { path: "request.adapter.operations" });
  }
  const read_operations = [];
  const unbound_write_operations = [];
  for (const name of operationNames) {
    assertExternalIdent(name, `request.adapter.operations["${name}"]`, { maxLength: 128 });
    const entry = assertObject(operations[name], `request.adapter.operations.${name}`);
    assertClosedKeys(entry, ADAPTER_OPERATION_KEYS, `request.adapter.operations.${name}`);
    assertRequiredKeys(entry, ADAPTER_OPERATION_KEYS, `request.adapter.operations.${name}`);
    assertSafeText(entry.mode, `request.adapter.operations.${name}.mode`, { maxLength: 32 });
    assertSafeText(entry.item_kind, `request.adapter.operations.${name}.item_kind`, { maxLength: 64 });
    if (entry.mode === V5_J201_BOUND_OPERATION_MODE) read_operations.push(name);
    else unbound_write_operations.push(name);
  }

  const authorization_receipt_ref =
    raw.authorization_receipt_ref === undefined || raw.authorization_receipt_ref === null
      ? null
      : assertExternalIdent(raw.authorization_receipt_ref, "request.adapter.authorization_receipt_ref");

  // Step 4. The honest unavailable, before any contract satisfaction is judged:
  // a contract cannot be satisfied by software that is not there.
  if (V5_J201_NON_READING_DEPLOYMENT_STATES.includes(deployment_state)) {
    return bindingRefusal("adapter_unavailable", {
      adapter_kind, observer_account, deployment_state,
      missing_read_operations: [...V5_J201_REQUIRED_READ_OPERATIONS],
    });
  }

  // Step 5.
  const missing_read_operations = [];
  for (const required of V5_J201_REQUIRED_READ_OPERATIONS) {
    const entry = Object.prototype.hasOwnProperty.call(operations, required) ? operations[required] : null;
    if (entry === null || entry.mode !== V5_J201_BOUND_OPERATION_MODE ||
        entry.item_kind !== V5_J201_REQUIRED_ITEM_KIND) {
      missing_read_operations.push(required);
    }
  }
  if (missing_read_operations.length > 0) {
    return bindingRefusal("read_contract_unsatisfied", {
      adapter_kind, observer_account, deployment_state,
      missing_read_operations: missing_read_operations.sort(),
    });
  }

  return deepFreeze({
    schema_version: V5_J201_READ_CONTRACT_SCHEMA_VERSION,
    decision: "bound",
    reason_id: "read_contract_satisfied",
    bound: true,
    adapter_kind,
    observer_account,
    deployment_state,
    platforms,
    evidence_class: V5_J201_EVIDENCE_CLASS,
    authoritative_home: V5_J201_AUTHORITATIVE_HOME,
    taint_class: V5_J201_TAINT_CLASS,
    authorization_receipt_ref,
    bound_read_operations: read_operations.sort(),
    // Named, not silently dropped: a reader can see the write half was seen.
    unbound_write_operations: unbound_write_operations.sort(),
    calendar_source_seam: V5_J201_CALENDAR_SOURCE_SEAM,
    recording: V5_J201_RECORDING_STATE,
    recording_permitted: false,
    effects: V5_NO_EFFECTS,
  });
}

/**
 * Refuse any object carrying a field whose NAME reaches for audio.
 *
 * Run BEFORE the closed-key check everywhere it is used, so the refusal names
 * what was actually attempted instead of a generic unknown field. This is the
 * structural half of "never records": a caller cannot get audio past this module
 * even by inventing a field for it, because the check never looks at values.
 */
function assertNoRecordingFields(object, path) {
  for (const key of Object.keys(object)) {
    const normalized = key.toLowerCase();
    for (const fragment of V5_J201_RECORDING_FRAGMENTS) {
      if (normalized.includes(fragment)) {
        fail("recording_field_refused",
          `${path}.${key} names audio or recording; this slice captures no audio and reads no field that claims to carry it`,
          { path: `${path}.${key}`, fragment, seam: V5_J201_RECORDING_POLICY_SEAM });
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Meeting identity. Every key in this module is built here and nowhere else.
// ---------------------------------------------------------------------------

const NATIVE_IDENTITY_KEYS = Object.freeze(["source_system", "native_id", "native_id_epoch"]);

/**
 * Validate one native source identity.
 *
 * `native_id_epoch` is how a RECYCLED identifier becomes visible, and it is
 * required rather than optional. A conferencing platform that reuses a meeting
 * id for a different meeting supplies a different epoch; the same id under a
 * different epoch is a different meeting wearing an old name. F01 already made
 * this call for corporate artifacts; this slice makes the same one for the same
 * reason, so a stale recurring-meeting id cannot merge tomorrow's call into
 * yesterday's notes.
 */
function assertNativeIdentity(value, path) {
  assertObject(value, path);
  assertNoRecordingFields(value, path);
  assertClosedKeys(value, NATIVE_IDENTITY_KEYS, path);
  assertRequiredKeys(value, NATIVE_IDENTITY_KEYS, path);
  return {
    source_system: assertExternalIdent(value.source_system, `${path}.source_system`, { maxLength: 128 }),
    native_id: assertExternalIdent(value.native_id, `${path}.native_id`, { maxLength: 255 }),
    native_id_epoch: assertExternalIdent(value.native_id_epoch, `${path}.native_id_epoch`, { maxLength: 128 }),
  };
}

/**
 * Fold a platform and a native identity into ONE meeting key.
 *
 * THE RULE THIS ENCODES, from Q074.D1: duplicates reconcile by MEETING AND
 * NATIVE SOURCE IDENTITY. Time is not in the key, and it must not be: two
 * partners in one meeting see the same native id, and two different meetings
 * that happen to overlap do not. A key built from time would merge the second
 * pair and, on a laptop whose clock drifted, split the first.
 */
function meetingKeyFor(platform, native_identity) {
  return [
    platform,
    native_identity.source_system,
    native_identity.native_id,
    native_identity.native_id_epoch,
  ].join(KEY_SEPARATOR);
}

/** The same fold, exported, so a caller can build the key it will later present. */
export function meetingKey(request) {
  assertObject(request, "request");
  assertClosedKeys(request, ["platform", "native_identity"], "request");
  assertRequiredKeys(request, ["platform", "native_identity"], "request");
  const platform = assertEnum(request.platform, V5_J201_PLATFORMS, "request.platform", "unknown_platform");
  return meetingKeyFor(platform, assertNativeIdentity(request.native_identity, "request.native_identity"));
}

// ---------------------------------------------------------------------------
// Detection. Calendar timing plus corroboration, and nothing else.
// ---------------------------------------------------------------------------

/**
 * The corroboration floor. Policy may RAISE the required count; it may never
 * lower it below this, because "calendar plus at least one other indicator" is
 * the settled requirement rather than a tuning choice.
 */
export const V5_J201_MIN_REQUIRED_CORROBORATING_SIGNALS = 1;

/** A deployment may demand at most all three corroborating kinds. */
export const V5_J201_MAX_REQUIRED_CORROBORATING_SIGNALS =
  V5_J201_CORROBORATING_SIGNAL_KINDS.length;

/** Bounds on the detection window, so a "policy" cannot become an always-on prompt. */
export const V5_J201_MAX_WINDOW_LEAD_SECONDS = 3600;
export const V5_J201_MAX_WINDOW_TRAIL_SECONDS = 3600;

const DETECTION_POLICY_KEYS = Object.freeze([
  "window_lead_seconds", "window_trail_seconds", "required_corroborating_signals",
]);

/**
 * Validate one deployment's detection policy. There is no default for any of
 * these: a caller that has not decided its window has not decided when a partner
 * is in a meeting, and this module will not decide it for them.
 */
function assertDetectionPolicy(value, path) {
  assertObject(value, path);
  assertNoRecordingFields(value, path);
  assertClosedKeys(value, DETECTION_POLICY_KEYS, path);
  assertRequiredKeys(value, DETECTION_POLICY_KEYS, path);
  const required = assertSafeInteger(value.required_corroborating_signals,
    `${path}.required_corroborating_signals`,
    { min: 0, max: V5_J201_MAX_REQUIRED_CORROBORATING_SIGNALS });
  if (required < V5_J201_MIN_REQUIRED_CORROBORATING_SIGNALS) {
    fail("corroboration_floor_breached",
      `${path}.required_corroborating_signals may be raised but never lowered below ${V5_J201_MIN_REQUIRED_CORROBORATING_SIGNALS}; calendar timing alone is never a meeting observation`,
      { path, floor: V5_J201_MIN_REQUIRED_CORROBORATING_SIGNALS, supplied: required });
  }
  return {
    window_lead_seconds: assertSafeInteger(value.window_lead_seconds,
      `${path}.window_lead_seconds`, { min: 0, max: V5_J201_MAX_WINDOW_LEAD_SECONDS }),
    window_trail_seconds: assertSafeInteger(value.window_trail_seconds,
      `${path}.window_trail_seconds`, { min: 0, max: V5_J201_MAX_WINDOW_TRAIL_SECONDS }),
    required_corroborating_signals: required,
  };
}

const CALENDAR_SIGNAL_KEYS = Object.freeze([
  "platform", "native_identity", "starts_at", "ends_at",
  "organizer_account", "observer_account", "declared_data_classes",
]);
const CORROBORATING_SIGNAL_KEYS = Object.freeze([
  "signal_kind", "state", "observed_at", "platform",
]);
const OBSERVE_REQUEST_KEYS = Object.freeze([
  "tenant", "now", "adapter_binding", "calendar_signal",
  "corroborating_signals", "detection_policy",
]);

function observationAnswer(fields) {
  return deepFreeze({
    schema_version: V5_J201_OBSERVATION_SCHEMA_VERSION,
    // Carried on EVERY answer, including every refusal. There is no result shape
    // of this module in which recording is anything other than denied.
    recording: V5_J201_RECORDING_STATE,
    recording_permitted: false,
    records_audio: false,
    detection_is_not_consent: true,
    recording_policy_seam: V5_J201_RECORDING_POLICY_SEAM,
    ...fields,
    effects: V5_NO_EFFECTS,
  });
}

/**
 * Decide whether a likely meeting has been observed.
 *
 * ORDERED, so a second reader reaches the same answer from the transcript:
 *   1. The request must be readable, closed and tenant-bound, and no field may
 *      name audio. Otherwise this throws.
 *   2. The adapter binding must be a `bound` result from
 *      bindMeetingSourceAdapter. An unbound or refused binding REFUSES with
 *      `calendar_source_unavailable` — the honest answer when the authorized
 *      Calendar adapter is not there, rather than a stub that pretends.
 *   3. The observing account must match the account the adapter was bound for.
 *      A binding for Joe's mailbox cannot carry an observation of Dell's.
 *   4. The privacy boundary is S01's answer, carried through. A declared class
 *      it refuses refuses here.
 *   5. `now` must fall inside [starts_at - lead, ends_at + trail]. Outside it,
 *      `withhold_outside_calendar_window`.
 *   6. Corroborating signals are counted. Only an AFFIRMATIVE state counts, only
 *      one per signal kind, only within the same window, and only for the same
 *      platform. Below the required count, `withhold_insufficient_corroboration`
 *      — which is the answer for calendar-alone, by construction.
 *   7. Otherwise `observe_meeting`, carrying the meeting key and the
 *      corroboration that earned it. An observation is not a prompt and is not
 *      permission; evaluateActivationPrompt decides the first and only a human
 *      gives the second.
 */
export function evaluateMeetingObservation(request) {
  assertObject(request, "request");
  assertNoRecordingFields(request, "request");
  assertClosedKeys(request, OBSERVE_REQUEST_KEYS, "request");
  assertRequiredKeys(request, OBSERVE_REQUEST_KEYS, "request");
  assertTenant(request.tenant, "request.tenant");
  const now = assertInstant(request.now, "request.now");
  const policy = assertDetectionPolicy(request.detection_policy, "request.detection_policy");

  const raw = assertObject(request.calendar_signal, "request.calendar_signal");
  assertNoRecordingFields(raw, "request.calendar_signal");
  assertClosedKeys(raw, CALENDAR_SIGNAL_KEYS, "request.calendar_signal");
  assertRequiredKeys(raw, CALENDAR_SIGNAL_KEYS, "request.calendar_signal");
  const platform = assertEnum(raw.platform, V5_J201_PLATFORMS,
    "request.calendar_signal.platform", "unknown_platform");
  const native_identity = assertNativeIdentity(raw.native_identity, "request.calendar_signal.native_identity");
  const starts_at = assertInstant(raw.starts_at, "request.calendar_signal.starts_at");
  const ends_at = assertInstant(raw.ends_at, "request.calendar_signal.ends_at");
  if (ends_at < starts_at) {
    fail("invalid_interval", "request.calendar_signal.ends_at precedes starts_at",
      { starts_at: raw.starts_at, ends_at: raw.ends_at });
  }
  const organizer_account = assertExternalIdent(raw.organizer_account,
    "request.calendar_signal.organizer_account", { maxLength: 128 });
  const observer_account = assertExternalIdent(raw.observer_account,
    "request.calendar_signal.observer_account", { maxLength: 128 });

  const declared = assertArray(raw.declared_data_classes,
    "request.calendar_signal.declared_data_classes", { min: 1, max: 16 });
  const seenClass = new Set();
  const declared_data_classes = declared.map((cls, i) => {
    const value = assertEnum(cls, V5_DATA_CLASSES,
      `request.calendar_signal.declared_data_classes[${i}]`, "unknown_data_class");
    if (seenClass.has(value)) {
      fail("duplicate_data_class",
        `request.calendar_signal.declared_data_classes repeats "${value}"`, { value });
    }
    seenClass.add(value);
    return value;
  }).sort();

  const key = meetingKeyFor(platform, native_identity);
  const base = {
    meeting_key: key, platform, native_identity: { ...native_identity },
    observer_account, organizer_account,
    starts_at: raw.starts_at, ends_at: raw.ends_at,
    declared_data_classes: [...declared_data_classes],
  };

  // Step 2. The honest unavailable.
  const binding = assertObject(request.adapter_binding, "request.adapter_binding");
  if (binding.schema_version !== V5_J201_READ_CONTRACT_SCHEMA_VERSION) {
    fail("invalid_adapter_binding",
      `request.adapter_binding must be a ${V5_J201_READ_CONTRACT_SCHEMA_VERSION} result from bindMeetingSourceAdapter`,
      { schema_version: binding.schema_version ?? null });
  }
  if (binding.bound !== true) {
    return observationAnswer({
      ...base, decision: "refuse", reason_id: "calendar_source_unavailable",
      calendar_source_seam: V5_J201_CALENDAR_SOURCE_SEAM,
      adapter_reason_id: typeof binding.reason_id === "string" ? binding.reason_id : null,
    });
  }

  // Step 3.
  if (binding.observer_account !== observer_account) {
    return observationAnswer({
      ...base, decision: "refuse", reason_id: "observer_account_outside_binding",
      bound_observer_account: binding.observer_account,
    });
  }
  if (!Array.isArray(binding.platforms) || !binding.platforms.includes(platform)) {
    return observationAnswer({
      ...base, decision: "refuse", reason_id: "platform_outside_binding",
      bound_platforms: Array.isArray(binding.platforms) ? [...binding.platforms] : [],
    });
  }

  // Step 4. S01 decides privacy, not a second copy of the list here.
  const privacy = evaluatePrivacyBoundary({
    data_classes: declared_data_classes,
    intended_use: V5_J201_INTENDED_USE,
  });
  if (privacy.decision !== "allow") {
    return observationAnswer({
      ...base, decision: "refuse", reason_id: "privacy_boundary_refused",
      privacy_decision: privacy.decision, privacy_reason_id: privacy.reason_id,
    });
  }

  // Step 5.
  const windowOpens = starts_at - policy.window_lead_seconds * 1000;
  const windowCloses = ends_at + policy.window_trail_seconds * 1000;
  if (now < windowOpens || now > windowCloses) {
    return observationAnswer({
      ...base, decision: "withhold_outside_calendar_window",
      reason_id: "now_outside_detection_window",
      detection_policy: { ...policy },
    });
  }

  // Step 6.
  const signals = assertArray(request.corroborating_signals, "request.corroborating_signals",
    { min: 0, max: 32 });
  const affirmed = new Set();
  const rejected = [];
  signals.forEach((entry, i) => {
    const path = `request.corroborating_signals[${i}]`;
    const signal = assertObject(entry, path);
    assertNoRecordingFields(signal, path);
    assertClosedKeys(signal, CORROBORATING_SIGNAL_KEYS, path);
    assertRequiredKeys(signal, CORROBORATING_SIGNAL_KEYS, path);
    const kind = assertEnum(signal.signal_kind, V5_J201_CORROBORATING_SIGNAL_KINDS,
      `${path}.signal_kind`, "unknown_signal_kind");
    const state = assertEnum(signal.state, SIGNAL_STATE_VOCABULARY[kind],
      `${path}.state`, "unknown_signal_state");
    const observed_at = assertInstant(signal.observed_at, `${path}.observed_at`);
    const signalPlatform = assertEnum(signal.platform, V5_J201_PLATFORMS,
      `${path}.platform`, "unknown_platform");
    if (signalPlatform !== platform) {
      rejected.push({ index: i, signal_kind: kind, reason_id: "platform_mismatch" });
      return;
    }
    if (observed_at < windowOpens || observed_at > windowCloses) {
      rejected.push({ index: i, signal_kind: kind, reason_id: "observed_outside_detection_window" });
      return;
    }
    if (observed_at > now) {
      rejected.push({ index: i, signal_kind: kind, reason_id: "observed_after_now" });
      return;
    }
    // `unknown` is a registered state and a legitimate thing to report. It is
    // never corroboration. Silence is not presence.
    if (state !== AFFIRMATIVE_SIGNAL_STATE[kind]) {
      rejected.push({ index: i, signal_kind: kind, reason_id: "state_not_affirmative" });
      return;
    }
    affirmed.add(kind);
  });

  const corroborating_signal_kinds = [...affirmed].sort();
  const shared = {
    ...base,
    detection_policy: { ...policy },
    corroborating_signal_kinds,
    corroborating_signal_count: corroborating_signal_kinds.length,
    rejected_signals: rejected,
  };
  if (corroborating_signal_kinds.length < policy.required_corroborating_signals) {
    return observationAnswer({
      ...shared,
      decision: "withhold_insufficient_corroboration",
      reason_id: corroborating_signal_kinds.length === 0
        ? "calendar_timing_alone_is_not_a_meeting"
        : "corroborating_signal_count_below_policy",
    });
  }

  return observationAnswer({
    ...shared,
    decision: "observe_meeting",
    reason_id: "calendar_window_and_corroboration_agree",
    // Said explicitly on the success path, because this is the exact place a
    // reader might assume an observation carries permission with it.
    prompt_permitted: true,
    activation_required_from_human: true,
  });
}

// ---------------------------------------------------------------------------
// Reconciliation. Joe and Dell in one meeting is one meeting.
// ---------------------------------------------------------------------------

const RECONCILE_OBSERVATION_KEYS = Object.freeze([
  "observer_partner", "platform", "native_identity", "calendar_uid", "observed_at",
]);
const RECONCILE_REQUEST_KEYS = Object.freeze(["tenant", "now", "observations"]);

/**
 * Reconcile many partners' observations into meetings.
 *
 * ORDERED:
 *   1. Readable, closed, tenant-bound request; no field may name audio.
 *   2. Each observation is keyed on platform plus native source identity. THIS
 *      IS THE WHOLE RULE: not time, not title, not attendee list.
 *   3. Two observations under the same key are ONE meeting, whoever saw them.
 *      Joe's and Dell's observations of the same Teams call reconcile here.
 *   4. Two observations under different keys are DISTINCT meetings, even when
 *      their times overlap exactly. Overlap is not identity.
 *   5. One native id seen under two different `native_id_epoch` values, or one
 *      key claimed under two different calendar uids, REFUSES with
 *      `refuse_ambiguous_identity`. Nobody downstream can untangle a recycled
 *      identifier, so this is the one case that refuses rather than guessing —
 *      and it refuses WITHOUT merging, which is the failure that would matter.
 */
export function reconcileMeetingObservations(request) {
  assertObject(request, "request");
  assertNoRecordingFields(request, "request");
  assertClosedKeys(request, RECONCILE_REQUEST_KEYS, "request");
  assertRequiredKeys(request, RECONCILE_REQUEST_KEYS, "request");
  assertTenant(request.tenant, "request.tenant");
  const now = assertInstant(request.now, "request.now");

  const raws = assertArray(request.observations, "request.observations", { min: 1, max: 64 });
  const normalized = raws.map((entry, i) => {
    const path = `request.observations[${i}]`;
    const observation = assertObject(entry, path);
    assertNoRecordingFields(observation, path);
    assertClosedKeys(observation, RECONCILE_OBSERVATION_KEYS, path);
    assertRequiredKeys(observation, RECONCILE_OBSERVATION_KEYS, path);
    const observer_partner = assertExternalIdent(observation.observer_partner,
      `${path}.observer_partner`, { maxLength: 64 });
    if (!isKnownPartner(observer_partner)) {
      fail("unknown_observer_partner",
        `${path}.observer_partner is not a known partner`, { path, observer_partner });
    }
    const platform = assertEnum(observation.platform, V5_J201_PLATFORMS,
      `${path}.platform`, "unknown_platform");
    const native_identity = assertNativeIdentity(observation.native_identity, `${path}.native_identity`);
    const calendar_uid = assertExternalIdent(observation.calendar_uid, `${path}.calendar_uid`);
    const observed_at = assertInstant(observation.observed_at, `${path}.observed_at`);
    if (observed_at > now) {
      fail("observed_after_now", `${path}.observed_at is after request.now`,
        { path, observed_at: observation.observed_at, now: request.now });
    }
    return {
      index: i, observer_partner, platform, native_identity,
      calendar_uid, observed_at: observation.observed_at,
      meeting_key: meetingKeyFor(platform, native_identity),
    };
  });

  // Step 5, first half: one native id under two epochs. Checked across the WHOLE
  // input before any grouping, because the merge it would cause is the defect.
  const epochsByIdentity = new Map();
  for (const entry of normalized) {
    const idKey = [entry.platform, entry.native_identity.source_system, entry.native_identity.native_id]
      .join(KEY_SEPARATOR);
    const seen = epochsByIdentity.get(idKey) ?? new Set();
    seen.add(entry.native_identity.native_id_epoch);
    epochsByIdentity.set(idKey, seen);
    if (seen.size > 1) {
      return deepFreeze({
        schema_version: V5_J201_RECONCILIATION_SCHEMA_VERSION,
        disposition: "refuse_ambiguous_identity",
        reason_id: "native_id_epoch_conflict",
        conflicting_native_id: entry.native_identity.native_id,
        conflicting_epochs: [...seen].sort(),
        meetings: [],
        merged: false,
        recording: V5_J201_RECORDING_STATE,
        recording_permitted: false,
        effects: V5_NO_EFFECTS,
      });
    }
  }

  // Step 5, second half: one meeting key claimed under two calendar uids.
  const groups = new Map();
  for (const entry of normalized) {
    const group = groups.get(entry.meeting_key) ?? {
      meeting_key: entry.meeting_key, platform: entry.platform,
      native_identity: entry.native_identity, calendar_uids: new Set(),
      observers: new Set(), observation_indexes: [],
    };
    group.calendar_uids.add(entry.calendar_uid);
    group.observers.add(entry.observer_partner);
    group.observation_indexes.push(entry.index);
    groups.set(entry.meeting_key, group);
    if (group.calendar_uids.size > 1) {
      return deepFreeze({
        schema_version: V5_J201_RECONCILIATION_SCHEMA_VERSION,
        disposition: "refuse_ambiguous_identity",
        reason_id: "calendar_uid_conflict_under_one_native_identity",
        conflicting_native_id: entry.native_identity.native_id,
        conflicting_calendar_uids: [...group.calendar_uids].sort(),
        meetings: [],
        merged: false,
        recording: V5_J201_RECORDING_STATE,
        recording_permitted: false,
        effects: V5_NO_EFFECTS,
      });
    }
  }

  const meetings = [...groups.values()]
    .map(group => ({
      meeting_key: group.meeting_key,
      platform: group.platform,
      native_identity: { ...group.native_identity },
      calendar_uid: [...group.calendar_uids][0],
      observers: [...group.observers].sort(),
      observation_count: group.observation_indexes.length,
      observation_indexes: [...group.observation_indexes].sort((a, b) => a - b),
      // The duplicate case, said in a field rather than left to be inferred from
      // a count: two partners saw it, and it is still one meeting.
      reconciled_from_duplicate_observations: group.observation_indexes.length > 1,
    }))
    .sort((a, b) => (a.meeting_key < b.meeting_key ? -1 : a.meeting_key > b.meeting_key ? 1 : 0));

  return deepFreeze({
    schema_version: V5_J201_RECONCILIATION_SCHEMA_VERSION,
    disposition: meetings.length === 1 ? "single_meeting" : "distinct_meetings",
    reason_id: "reconciled_on_native_source_identity",
    observation_count: normalized.length,
    meeting_count: meetings.length,
    meetings,
    merged: normalized.length > meetings.length,
    recording: V5_J201_RECORDING_STATE,
    recording_permitted: false,
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// The prompt. Once, and never a recording.
// ---------------------------------------------------------------------------

const PROMPT_LEDGER_KEYS = Object.freeze([
  "prompted_meeting_keys", "dismissed_meeting_keys", "active_meeting_keys",
  "provenance",
]);
const LEDGER_PROVENANCE_KEYS = Object.freeze(["class", "owner", "read_at"]);
const PROMPT_REQUEST_KEYS = Object.freeze(["tenant", "now", "observation", "prompt_ledger"]);

function assertKeyList(value, path) {
  const list = assertArray(value, path, { min: 0, max: 256 });
  return list.map((entry, i) => {
    if (typeof entry !== "string" || entry.length === 0 || entry.length > 1024) {
      fail("invalid_shape", `${path}[${i}] must be a meeting key string`, { path: `${path}[${i}]` });
    }
    return entry;
  });
}

const UNWIRED_LEDGER_VIEW_KEYS = Object.freeze([
  "meeting_key", "prompted_meeting_keys", "dismissed_meeting_keys",
  "active_meeting_keys",
]);

/**
 * THE ONCE-ONLY RULE, WRITTEN DOWN AND DELIBERATELY NOT WIRED.
 *
 * Given a meeting key and a ledger view, this returns the DECISION NAME the
 * once-only rule asks for, and nothing else. It returns a bare string: it is not
 * a result, carries no schema version, cannot show a prompt and cannot be handed
 * to activateMeetingMode, so no caller reaches a privileged outcome through it.
 * evaluateActivationPrompt does not call it — the test suite pins that no input
 * to evaluateActivationPrompt produces any name in
 * V5_J201_UNWIRED_ONCE_ONLY_DECISIONS.
 *
 * It exists because the rule is worth reviewing and testing BEFORE its owner is
 * built. When the durable prompt-ledger owner named in
 * V5_J201_PROMPT_LEDGER_OWNER_SEAM lands, this is the predicate it wires,
 * already proved against fixture ledgers. Until then it is documentation that
 * executes, and `meetingModeGaps().once_only_prompt_enforced_here` stays false.
 */
export function unwiredOnceOnlyPromptDecision(ledger_view) {
  const view = assertObject(ledger_view, "ledger_view");
  assertNoRecordingFields(view, "ledger_view");
  assertClosedKeys(view, UNWIRED_LEDGER_VIEW_KEYS, "ledger_view");
  assertRequiredKeys(view, UNWIRED_LEDGER_VIEW_KEYS, "ledger_view");
  // Validated the way a ledger ENTRY is validated, not as free text: a meeting
  // key is built by meetingKey() and carries a NUL separator on purpose, which
  // assertSafeText refuses by design.
  if (typeof view.meeting_key !== "string" || view.meeting_key.length === 0
      || view.meeting_key.length > 1024) {
    fail("invalid_shape", "ledger_view.meeting_key must be a meeting key string",
      { path: "ledger_view.meeting_key" });
  }
  const prompted = assertKeyList(view.prompted_meeting_keys, "ledger_view.prompted_meeting_keys");
  const dismissed = assertKeyList(view.dismissed_meeting_keys, "ledger_view.dismissed_meeting_keys");
  const active = assertKeyList(view.active_meeting_keys, "ledger_view.active_meeting_keys");

  if (active.includes(view.meeting_key)) return "suppress_already_active";
  // A dismissal's only exit is V5_J201_DISMISSAL_REOPEN_REQUIRES — a person.
  if (dismissed.includes(view.meeting_key)) return "suppress_dismissed";
  if (prompted.includes(view.meeting_key)) return "suppress_already_prompted";
  return "prompt_once";
}

/**
 * Decide whether to raise the one-tap Meeting Mode prompt. TODAY THE ANSWER IS
 * ALWAYS NO, and the shape of the no is the point.
 *
 * WHAT THIS FUNCTION DOES NOT DO, said first because the opposite is the easy
 * thing to believe: it does not enforce once-only across calls, and it does not
 * raise prompts. It is stateless. Once-only is a property of a durable prompt
 * ledger, that ledger has an owner, and THE OWNER DOES NOT EXIST IN THIS
 * REPOSITORY (V5_J201_PROMPT_LEDGER_OWNER_SEAM). A ledger handed to a stateless
 * kernel by a caller is evidence of nothing in either direction, whatever the
 * caller says about where it came from — so every ledger is refused, and the
 * missing owner shows up as a refusal at runtime rather than as a prompt that
 * fires twice.
 *
 * ORDERED:
 *   1. Readable, closed, tenant-bound; no field may name audio.
 *   2. The observation must be an `observe_meeting` answer from
 *      evaluateMeetingObservation. Any other decision suppresses with
 *      `suppress_not_observed`, so a withheld or refused detection cannot leak
 *      a prompt. This runs first because it consults no ledger at all.
 *   3. The ledger must be WELL FORMED — closed keys, a stated provenance, a read
 *      time that is not in the future. A malformed one throws.
 *   4. And then it is refused. `refuse_unproven_prompt_ledger` is the whole
 *      answer for EVERY well-formed ledger: not a suppression and not a prompt,
 *      because a ledger nobody can vouch for decides nothing in either
 *      direction. The refusal carries the class the caller claimed (recorded,
 *      credited with nothing), the empty set of classes this module trusts, and
 *      the seam that owes the owner.
 *
 * NOTHING IN THIS FUNCTION CAN START ANYTHING. Its most permissive answer is a
 * refusal, and every answer says so in `records_audio: false` and
 * `prompt_shown: false`.
 */
export function evaluateActivationPrompt(request) {
  assertObject(request, "request");
  assertNoRecordingFields(request, "request");
  assertClosedKeys(request, PROMPT_REQUEST_KEYS, "request");
  assertRequiredKeys(request, PROMPT_REQUEST_KEYS, "request");
  assertTenant(request.tenant, "request.tenant");
  const now = assertInstant(request.now, "request.now");

  const observation = assertObject(request.observation, "request.observation");
  if (observation.schema_version !== V5_J201_OBSERVATION_SCHEMA_VERSION) {
    fail("invalid_observation",
      `request.observation must be a ${V5_J201_OBSERVATION_SCHEMA_VERSION} result from evaluateMeetingObservation`,
      { schema_version: observation.schema_version ?? null });
  }
  const meeting_key = typeof observation.meeting_key === "string" ? observation.meeting_key : null;

  const ledger = assertObject(request.prompt_ledger, "request.prompt_ledger");
  assertClosedKeys(ledger, PROMPT_LEDGER_KEYS, "request.prompt_ledger");
  assertRequiredKeys(ledger, PROMPT_LEDGER_KEYS, "request.prompt_ledger");
  // Validated and then deliberately NOT CONSULTED. The contract still demands a
  // well-formed ledger — a caller who cannot produce one has a bug worth
  // failing on — but no entry in it can decide anything here, so reading these
  // lists into a decision is exactly what this function must not do.
  assertKeyList(ledger.prompted_meeting_keys, "request.prompt_ledger.prompted_meeting_keys");
  assertKeyList(ledger.dismissed_meeting_keys, "request.prompt_ledger.dismissed_meeting_keys");
  assertKeyList(ledger.active_meeting_keys, "request.prompt_ledger.active_meeting_keys");

  const provenance = assertObject(ledger.provenance, "request.prompt_ledger.provenance");
  assertClosedKeys(provenance, LEDGER_PROVENANCE_KEYS, "request.prompt_ledger.provenance");
  assertRequiredKeys(provenance, LEDGER_PROVENANCE_KEYS, "request.prompt_ledger.provenance");
  const provenance_class = assertEnum(provenance.class, V5_J201_LEDGER_PROVENANCE_CLASSES,
    "request.prompt_ledger.provenance.class", "unknown_ledger_provenance_class");
  assertSafeText(provenance.owner, "request.prompt_ledger.provenance.owner", { maxLength: 255 });
  const read_at = assertInstant(provenance.read_at, "request.prompt_ledger.provenance.read_at");
  if (read_at > now) {
    fail("ledger_read_after_now",
      "request.prompt_ledger.provenance.read_at is after request.now",
      { path: "request.prompt_ledger.provenance.read_at" });
  }

  const answer = fields => deepFreeze({
    schema_version: V5_J201_PROMPT_SCHEMA_VERSION,
    meeting_key,
    records_audio: false,
    recording: V5_J201_RECORDING_STATE,
    recording_permitted: false,
    detection_is_not_consent: true,
    recording_policy_seam: V5_J201_RECORDING_POLICY_SEAM,
    ...fields,
    effects: V5_NO_EFFECTS,
  });

  if (observation.decision !== "observe_meeting") {
    return answer({
      decision: "suppress_not_observed", reason_id: "observation_did_not_observe_a_meeting",
      observation_decision: typeof observation.decision === "string" ? observation.decision : null,
      prompt_shown: false,
    });
  }
  if (meeting_key === null) {
    fail("invalid_observation", "request.observation carries no meeting_key", {});
  }
  // THE REFUSAL, UNCONDITIONAL. Not a branch on the caller's label: there is no
  // label, owner string, read time or ledger contents that reaches past this
  // line, because the fact that would justify going past it — a durable owner
  // that can vouch for this ledger — does not exist to be consulted. The
  // once-only rule itself lives in unwiredOnceOnlyPromptDecision, unreachable
  // from here, so that the missing owner is the only thing standing between
  // this slice and a working prompt.
  return answer({
    decision: "refuse_unproven_prompt_ledger",
    reason_id: "no_durable_prompt_ledger_owner_exists_to_prove_this_ledger",
    prompt_shown: false,
    claimed_ledger_provenance_class: provenance_class,
    trusted_ledger_provenance_classes: [...V5_J201_TRUSTED_LEDGER_PROVENANCE_CLASSES],
    prompt_ledger_owner_seam: V5_J201_PROMPT_LEDGER_OWNER_SEAM,
    once_only_rule_written_down_at: "unwiredOnceOnlyPromptDecision",
  });
}

// ---------------------------------------------------------------------------
// Activation. A person's one tap, and nothing that resembles one.
// ---------------------------------------------------------------------------

const ACTIVATE_REQUEST_KEYS = Object.freeze([
  "tenant", "now", "actor", "prompt", "activation_intent",
]);

/**
 * Activate non-recording Meeting Mode.
 *
 * ORDERED:
 *   1. Readable, closed, tenant-bound; no field may name audio. An
 *      `activation_intent` is a string, never an object, so nothing can ride in
 *      beside it.
 *   2. The prompt must be a `prompt_once` answer. Activating without a prompt
 *      that was actually shown refuses.
 *   3. The actor must be a verified partner by identity.js's own predicate. A
 *      sponsored agent, an unsponsored runtime or a machine seat refuses:
 *      "one-tap" means a person tapped.
 *   4. The intent must be exactly `one_tap_user_activation`. The three silent
 *      intents refuse BY NAME, so a transcript shows which one was attempted.
 *   5. The session opens in `active_non_recording`, and there is no other state
 *      it can open in.
 */
export function activateMeetingMode(request) {
  assertObject(request, "request");
  assertNoRecordingFields(request, "request");
  assertClosedKeys(request, ACTIVATE_REQUEST_KEYS, "request");
  assertRequiredKeys(request, ACTIVATE_REQUEST_KEYS, "request");
  assertTenant(request.tenant, "request.tenant");
  const opened_at = assertInstant(request.now, "request.now");

  const prompt = assertObject(request.prompt, "request.prompt");
  if (prompt.schema_version !== V5_J201_PROMPT_SCHEMA_VERSION) {
    fail("invalid_prompt",
      `request.prompt must be a ${V5_J201_PROMPT_SCHEMA_VERSION} result from evaluateActivationPrompt`,
      { schema_version: prompt.schema_version ?? null });
  }
  const meeting_key = typeof prompt.meeting_key === "string" ? prompt.meeting_key : null;
  const actor = assertObject(request.actor, "request.actor");
  const actor_slug = typeof actor.slug === "string" ? actor.slug : null;

  const answer = fields => deepFreeze({
    schema_version: V5_J201_SESSION_SCHEMA_VERSION,
    meeting_key,
    actor_slug,
    recording: V5_J201_RECORDING_STATE,
    recording_permitted: false,
    records_audio: false,
    audio_retained: false,
    recording_policy_seam: V5_J201_RECORDING_POLICY_SEAM,
    ...fields,
    effects: V5_NO_EFFECTS,
  });

  if (prompt.decision !== "prompt_once" || prompt.prompt_shown !== true) {
    return answer({
      decision: "refuse", reason_id: "activation_without_a_shown_prompt",
      mode_state: "off",
      prompt_decision: typeof prompt.decision === "string" ? prompt.decision : null,
    });
  }
  if (meeting_key === null) {
    fail("invalid_prompt", "request.prompt carries no meeting_key", {});
  }
  if (authorizationClassForActor(actor) !== "verified_partner" || !isKnownPartner(actor_slug)) {
    return answer({
      decision: "refuse", reason_id: "activation_requires_a_verified_partner",
      mode_state: "off",
      authorization_class: authorizationClassForActor(actor),
    });
  }
  if (typeof request.activation_intent !== "string") {
    fail("invalid_shape", "request.activation_intent must be a string",
      { path: "request.activation_intent" });
  }
  if (V5_J201_REFUSED_ACTIVATION_INTENTS.includes(request.activation_intent)) {
    return answer({
      decision: "refuse", reason_id: "silent_activation_refused",
      mode_state: "off",
      attempted_activation_intent: request.activation_intent,
      accepted_activation_intent: V5_J201_EXPLICIT_ACTIVATION_INTENT,
    });
  }
  if (request.activation_intent !== V5_J201_EXPLICIT_ACTIVATION_INTENT) {
    return answer({
      decision: "refuse", reason_id: "explicit_human_activation_required",
      mode_state: "off",
      accepted_activation_intent: V5_J201_EXPLICIT_ACTIVATION_INTENT,
    });
  }

  return answer({
    decision: "activate", reason_id: "explicit_one_tap_activation_by_verified_partner",
    mode_state: "active_non_recording",
    activation_intent: V5_J201_EXPLICIT_ACTIVATION_INTENT,
    opened_at: request.now,
    // The announcement Q074.D1 names lives on the RECORDING path, which is
    // denied here, so this session has nothing to announce. Said in a field so
    // a surface does not invent an announcement for a mode that captures nothing.
    consent_announcement_required: false,
    consent_announcement_reason: "no_audio_is_captured_by_this_mode",
    resumable: true,
  });
}

// ---------------------------------------------------------------------------
// The model boundary. After activation, into declared seams, never onto state.
// ---------------------------------------------------------------------------

const PROPOSAL_KEYS = Object.freeze(["seam", "label", "confidence", "evidence_ref"]);
const MODEL_REQUEST_KEYS = Object.freeze(["tenant", "session", "proposal"]);

/**
 * Accept or refuse one model proposal about a meeting.
 *
 * ORDERED:
 *   1. Readable, closed, tenant-bound. A proposal field whose NAME reaches for
 *      audio, or for authority, permission or state, throws BEFORE the
 *      closed-key check, so the refusal names what was attempted.
 *   2. The session must be `active_non_recording`. A model may not summarize a
 *      meeting nobody activated: the slice's model_judgment_boundary says "after
 *      explicit activation", and this is where that word is enforced.
 *   3. The seam and label must both be registered. An unregistered one throws —
 *      a seam this module has no entry for is not a policy question.
 *   4. An accepted proposal is a LABEL. It carries `is_fact: false` and
 *      `requires_human_confirmation: true`, changes no state, and is not a
 *      follow-up: creating one is a governed record write this module does not
 *      perform and does not authorize.
 */
export function evaluateModelProposal(request) {
  assertObject(request, "request");
  assertNoRecordingFields(request, "request");
  assertClosedKeys(request, MODEL_REQUEST_KEYS, "request");
  assertRequiredKeys(request, MODEL_REQUEST_KEYS, "request");
  assertTenant(request.tenant, "request.tenant");

  const session = assertObject(request.session, "request.session");
  if (session.schema_version !== V5_J201_SESSION_SCHEMA_VERSION) {
    fail("invalid_session",
      `request.session must be a ${V5_J201_SESSION_SCHEMA_VERSION} result from activateMeetingMode`,
      { schema_version: session.schema_version ?? null });
  }

  const proposal = assertObject(request.proposal, "request.proposal");
  assertNoRecordingFields(proposal, "request.proposal");
  for (const key of Object.keys(proposal)) {
    const normalized = key.toLowerCase();
    for (const fragment of V5_J201_MODEL_WIDENING_FRAGMENTS) {
      if (normalized.includes(fragment)) {
        fail("model_widening_refused",
          `request.proposal.${key} reaches past classification into authority, permission or state`,
          { path: `request.proposal.${key}`, fragment });
      }
    }
  }
  assertClosedKeys(proposal, PROPOSAL_KEYS, "request.proposal");
  assertRequiredKeys(proposal, PROPOSAL_KEYS, "request.proposal");

  const seam = assertEnum(proposal.seam, V5_J201_MODEL_SEAM_KEYS, "request.proposal.seam", "unknown_model_seam");
  const label = assertEnum(proposal.label, V5_J201_MODEL_SEAMS[seam], "request.proposal.label", "unknown_model_label");
  if (typeof proposal.confidence !== "number" || !Number.isFinite(proposal.confidence) ||
      proposal.confidence < 0 || proposal.confidence > 1) {
    fail("invalid_shape", "request.proposal.confidence must be a finite number between 0 and 1",
      { path: "request.proposal.confidence" });
  }
  const evidence_ref = assertExternalIdent(proposal.evidence_ref, "request.proposal.evidence_ref");

  const answer = fields => deepFreeze({
    schema_version: V5_J201_PROPOSAL_SCHEMA_VERSION,
    seam, label, evidence_ref,
    confidence: proposal.confidence,
    meeting_key: typeof session.meeting_key === "string" ? session.meeting_key : null,
    // A model proposal is never a fact and never a state change, on every path.
    is_fact: false,
    changes_mode_state: false,
    creates_follow_up: false,
    widens_authority: false,
    recording: V5_J201_RECORDING_STATE,
    recording_permitted: false,
    ...fields,
    effects: V5_NO_EFFECTS,
  });

  if (session.mode_state !== "active_non_recording") {
    return answer({
      decision: "refuse", reason_id: "explicit_activation_required_before_model_proposal",
      requires_human_confirmation: false,
      mode_state: typeof session.mode_state === "string" ? session.mode_state : null,
    });
  }

  return answer({
    decision: "accept", reason_id: "proposal_within_declared_seam_after_activation",
    mode_state: "active_non_recording",
    requires_human_confirmation: true,
  });
}

// ---------------------------------------------------------------------------
// Routing the meeting into the same records (Q059.D3's second clause).
// ---------------------------------------------------------------------------

const CANDIDATE_REQUEST_KEYS = Object.freeze([
  "tenant", "observation", "evidence_ref", "content_digest", "byte_length", "observed_at",
]);

/**
 * Turn an observed meeting into a candidate F01 corporate artifact.
 *
 * This module does NOT admit it. Admission is F01's, and keeping the decision
 * there is the point: a slice that both produced and admitted its own evidence
 * would be grading its own homework. The candidate is built to F01's shape so
 * `admitCorporateArtifact` is the independent oracle for whether it is right,
 * and the suite uses it that way.
 *
 * WHAT TRAVELS: event metadata only — the native identity, the declared data
 * classes, a digest of the metadata the caller measured, and the provenance of
 * the read. No title, no body, no attendee list, no audio, no transcript.
 * `content_digest` and `byte_length` are a MEASUREMENT of bytes the caller
 * holds, never bytes, which is why neither name trips the recording check.
 */
export function toMeetingRecordLinkCandidate(request) {
  assertObject(request, "request");
  assertNoRecordingFields(request, "request");
  assertClosedKeys(request, CANDIDATE_REQUEST_KEYS, "request");
  assertRequiredKeys(request, CANDIDATE_REQUEST_KEYS, "request");
  assertTenant(request.tenant, "request.tenant");

  const observation = assertObject(request.observation, "request.observation");
  if (observation.schema_version !== V5_J201_OBSERVATION_SCHEMA_VERSION) {
    fail("invalid_observation",
      `request.observation must be a ${V5_J201_OBSERVATION_SCHEMA_VERSION} result from evaluateMeetingObservation`,
      { schema_version: observation.schema_version ?? null });
  }
  if (observation.decision !== "observe_meeting") {
    return deepFreeze({
      schema_version: V5_J201_CANDIDATE_SCHEMA_VERSION,
      decision: "refuse",
      reason_id: "no_observed_meeting_to_link",
      observation_decision: typeof observation.decision === "string" ? observation.decision : null,
      candidate: null,
      recording: V5_J201_RECORDING_STATE,
      recording_permitted: false,
      effects: V5_NO_EFFECTS,
    });
  }

  const evidence_ref = assertExternalIdent(request.evidence_ref, "request.evidence_ref");
  const content_digest = assertSafeText(request.content_digest, "request.content_digest", { maxLength: 128 });
  const byte_length = assertSafeInteger(request.byte_length, "request.byte_length", { min: 0 });
  const observed_at = request.observed_at;
  assertInstant(observed_at, "request.observed_at");

  return deepFreeze({
    schema_version: V5_J201_CANDIDATE_SCHEMA_VERSION,
    decision: "candidate",
    reason_id: "observed_meeting_projected_to_f01_artifact_shape",
    meeting_key: observation.meeting_key,
    // Exactly F01's artifact shape. Nothing more travels than F01 will read.
    candidate: {
      source_system: observation.native_identity.source_system,
      source_class: observation.platform,
      source_account: observation.observer_account,
      native_identity: { ...observation.native_identity },
      native_version: observation.starts_at,
      content_digest,
      byte_length,
      observed_at,
      provenance: {
        adapter_kind: V5_J201_ADAPTER_KIND,
        evidence_ref,
        retrieval_class: V5_J201_RETRIEVAL_CLASSES.calendar_event,
      },
      evidence_class: V5_J201_EVIDENCE_CLASS,
      declared_data_classes: [...observation.declared_data_classes],
      taint_class: V5_J201_TAINT_CLASS,
    },
    admitted_here: false,
    admitting_module: "record-source-authority.v5.js",
    recording: V5_J201_RECORDING_STATE,
    recording_permitted: false,
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// What is NOT built here, said by name.
// ---------------------------------------------------------------------------

/**
 * The governed production outcomes that gate FULL v5, quoted from the r7 design
 * basis's own receipt-producer step registry rather than restated.
 *
 * THE READING, stated so it can be checked and disagreed with rather than
 * absorbed: the registry declares SIX steps at causal phase `production_outcome`.
 * Five of them are product-level outcomes and are listed below. The sixth,
 * `step:j1-kernel-production-outcome`, is declared in the registry as a
 * dependency OF `step:j1-core-production-outcome` rather than as an outcome in
 * its own right, which is how the catalog's "five governed production outcomes
 * required for full v5" reconciles with a registry of six. That is a reading of
 * the registry, not a ruling, and the excluded sixth is named below so a reader
 * can check the reading instead of trusting it.
 *
 * NONE OF THEM IS PRODUCIBLE FROM SOURCE. Each is produced by an independent
 * outcome oracle against production, and no test, merge, configuration or
 * activation in this repository can stand in for one. `meetingModeGaps()`
 * therefore reports `full_v5_ready: false` on every input, and there is no input
 * that changes it.
 */
export const V5_J201_PRODUCTION_OUTCOME_STEPS = deepFreeze([
  "step:j1-core-production-outcome",
  "step:j1-pilot-and-dell-beta-outcome",
  "step:journey-three-production-outcome",
  "step:journey-two-production-outcome",
  "step:representative-workflow-production-outcome",
]);

/** The sixth registry entry, named so the reading above can be checked. */
export const V5_J201_KERNEL_PRODUCTION_OUTCOME_STEP =
  "step:j1-kernel-production-outcome";

/** This slice's own two declared evidence inputs, from its catalog entry. */
export const V5_J201_SLICE_EVIDENCE_INPUTS = deepFreeze([
  "step:j1-core-production-outcome",
  "step:journey-two-contract-binding-receipt",
]);

/** The consumer gates every one of this slice's decisions declares. */
export const V5_J201_CONSUMER_GATES = deepFreeze([
  "global-execution-contract-accepted",
  "global-phi-boundary-accepted",
  "global-prompt-injection-boundary-accepted",
  "global-secrets-boundary-accepted",
  "global-source-authority-accepted",
  "journey-two-preactivation-contract-bound",
]);

/**
 * What this slice does NOT contain, named rather than left as an absence.
 *
 * `full_v5_ready` is false on every call and takes no argument that could make
 * it true. The function is deliberately nullary: a parameter would be a place a
 * caller could argue its way to a yes.
 */
export function meetingModeGaps() {
  return deepFreeze({
    schema_version: V5_J201_PROJECTION_SCHEMA_VERSION,
    full_v5_ready: false,
    // Stated rather than implied: this stateless kernel cannot make "prompt
    // once" true by itself, and does not claim to.
    once_only_prompt_enforced_here: false,
    once_only_prompt_reason_id:
      "the_prompt_ledger_is_owned_by_a_durable_store_this_slice_does_not_contain",
    // Stronger than "not enforced", and the stronger statement is the true one:
    // NO INPUT REACHES A PROMPT AT ALL today. An unprovable ledger decides
    // nothing, no ledger here is provable, and no class a caller declares
    // changes that. The set of provenance classes this module acts on is empty.
    prompt_reachable_here: false,
    prompt_unreachable_reason_id:
      "no_durable_prompt_ledger_owner_exists_to_prove_this_ledger",
    trusted_prompt_ledger_provenance_classes: [...V5_J201_TRUSTED_LEDGER_PROVENANCE_CLASSES],
    once_only_rule_written_down_at: "unwiredOnceOnlyPromptDecision",
    dismissal_reopen_requires: V5_J201_DISMISSAL_REOPEN_REQUIRES,
    full_v5_reason_id: "governed_production_outcomes_are_not_producible_from_source",
    outstanding_production_outcome_steps: [...V5_J201_PRODUCTION_OUTCOME_STEPS],
    excluded_from_the_five: V5_J201_KERNEL_PRODUCTION_OUTCOME_STEP,
    slice_evidence_inputs: [...V5_J201_SLICE_EVIDENCE_INPUTS],
    consumer_gates: [...V5_J201_CONSUMER_GATES],
    not_built_here: [
      "the local companion process that observes Zoom or Teams on a partner's Mac",
      "any Calendar, presence or audio-session reader; this module reads typed observations only",
      "the prompt surface, the notes surface and the Call Mode user interface",
      "persistence for the prompt ledger, the session, notes, tasks or follow-ups",
      "the durable, authoritative owner of the prompt ledger that makes \"prompt once\" true across calls, processes and restarts; until it exists this kernel refuses EVERY ledger it is handed and raises no prompt at all, and the once-only rule sits unwired in unwiredOnceOnlyPromptDecision",
      "the governed admission and write of the meeting record-link candidate into the business records; toMeetingRecordLinkCandidate projects the F01 artifact shape and returns admitted_here false, and record-source-authority.v5.js is the module that admits it",
      "the governed record write that creates a follow-up from an accepted proposal",
      "any recording, transcription or audio retention, which stay refused behind their own seam",
    ],
    seams: {
      calendar_source: V5_J201_CALENDAR_SOURCE_SEAM,
      prompt_ledger_owner: V5_J201_PROMPT_LEDGER_OWNER_SEAM,
      recording_policy: V5_J201_RECORDING_POLICY_SEAM,
    },
    recording: V5_J201_RECORDING_STATE,
    recording_permitted: false,
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// The policy digest. One preimage over everything above that a reader could
// disagree about, so drift is visible as a moved hash rather than as a surprise.
// ---------------------------------------------------------------------------

export function v5J201PolicyPreimage() {
  return deepFreeze({
    schema_version: V5_J201_SCHEMA_VERSION,
    policy_version: V5_J201_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    settled_decisions: Object.fromEntries(
      V5_J201_SETTLED_DECISION_IDS.map(id => [id, { ...V5_J201_SETTLED_DECISIONS[id] }])),
    platforms: [...V5_J201_PLATFORMS],
    signal_kinds: [...V5_J201_SIGNAL_KINDS],
    corroborating_signal_kinds: [...V5_J201_CORROBORATING_SIGNAL_KINDS],
    presence_states: [...V5_J201_PRESENCE_STATES],
    audio_session_states: [...V5_J201_AUDIO_SESSION_STATES],
    device_states: [...V5_J201_DEVICE_STATES],
    affirmative_signal_state: { ...AFFIRMATIVE_SIGNAL_STATE },
    observation_decisions: [...V5_J201_OBSERVATION_DECISIONS],
    prompt_decisions: [...V5_J201_PROMPT_DECISIONS],
    unwired_once_only_decisions: [...V5_J201_UNWIRED_ONCE_ONLY_DECISIONS],
    dismissal_reopen_requires: V5_J201_DISMISSAL_REOPEN_REQUIRES,
    ledger_provenance_classes: [...V5_J201_LEDGER_PROVENANCE_CLASSES],
    trusted_ledger_provenance_classes: [...V5_J201_TRUSTED_LEDGER_PROVENANCE_CLASSES],
    prompt_ledger_owner_seam: V5_J201_PROMPT_LEDGER_OWNER_SEAM,
    reconciliation_dispositions: [...V5_J201_RECONCILIATION_DISPOSITIONS],
    mode_states: [...V5_J201_MODE_STATES],
    binding_decisions: [...V5_J201_BINDING_DECISIONS],
    adapter_deployment_states: [...V5_J201_ADAPTER_DEPLOYMENT_STATES],
    non_reading_deployment_states: [...V5_J201_NON_READING_DEPLOYMENT_STATES],
    required_read_operations: [...V5_J201_REQUIRED_READ_OPERATIONS],
    required_item_kind: V5_J201_REQUIRED_ITEM_KIND,
    bound_operation_mode: V5_J201_BOUND_OPERATION_MODE,
    recording_state: V5_J201_RECORDING_STATE,
    recording_policy_seam: V5_J201_RECORDING_POLICY_SEAM,
    calendar_source_seam: V5_J201_CALENDAR_SOURCE_SEAM,
    explicit_activation_intent: V5_J201_EXPLICIT_ACTIVATION_INTENT,
    refused_activation_intents: [...V5_J201_REFUSED_ACTIVATION_INTENTS],
    model_seams: Object.fromEntries(
      V5_J201_MODEL_SEAM_KEYS.map(seam => [seam, [...V5_J201_MODEL_SEAMS[seam]]])),
    recording_fragments: [...V5_J201_RECORDING_FRAGMENTS],
    model_widening_fragments: [...V5_J201_MODEL_WIDENING_FRAGMENTS],
    adapter_kind: V5_J201_ADAPTER_KIND,
    evidence_class: V5_J201_EVIDENCE_CLASS,
    authoritative_home: V5_J201_AUTHORITATIVE_HOME,
    taint_class: V5_J201_TAINT_CLASS,
    retrieval_classes: { ...V5_J201_RETRIEVAL_CLASSES },
    intended_use: V5_J201_INTENDED_USE,
    min_required_corroborating_signals: V5_J201_MIN_REQUIRED_CORROBORATING_SIGNALS,
    max_required_corroborating_signals: V5_J201_MAX_REQUIRED_CORROBORATING_SIGNALS,
    max_window_lead_seconds: V5_J201_MAX_WINDOW_LEAD_SECONDS,
    max_window_trail_seconds: V5_J201_MAX_WINDOW_TRAIL_SECONDS,
    production_outcome_steps: [...V5_J201_PRODUCTION_OUTCOME_STEPS],
    kernel_production_outcome_step: V5_J201_KERNEL_PRODUCTION_OUTCOME_STEP,
    slice_evidence_inputs: [...V5_J201_SLICE_EVIDENCE_INPUTS],
    consumer_gates: [...V5_J201_CONSUMER_GATES],
  });
}

export function v5J201PolicyCanonicalBytes() {
  return canonicalJson(v5J201PolicyPreimage());
}

export function v5J201PolicyDigest() {
  return digest(v5J201PolicyPreimage());
}

/**
 * The whole slice as one readable projection, for an operator surface that wants
 * to show what Meeting Mode will and will not do before anyone turns it on.
 */
export function v5J201MeetingModeProjection() {
  // READ OFF THE GAPS PROJECTION RATHER THAN ASSERTED BESIDE IT. The two said
  // opposite things once — the operator surface promised "prompts once per
  // meeting" while the gaps block beneath it admitted nothing enforced it — and
  // a shared source is the only version of that fix that cannot rot again.
  const gaps = meetingModeGaps();
  return deepFreeze({
    schema_version: V5_J201_PROJECTION_SCHEMA_VERSION,
    policy_digest: v5J201PolicyDigest(),
    policy_version: V5_J201_POLICY_VERSION,
    settled_decision_ids: [...V5_J201_SETTLED_DECISION_IDS],
    platforms: [...V5_J201_PLATFORMS],
    detection_requires: {
      calendar_event: true,
      minimum_corroborating_signals: V5_J201_MIN_REQUIRED_CORROBORATING_SIGNALS,
      corroborating_signal_kinds: [...V5_J201_CORROBORATING_SIGNAL_KINDS],
      unknown_signal_corroborates: false,
    },
    prompts_once_per_meeting: gaps.once_only_prompt_enforced_here,
    prompts_once_per_meeting_reason_id: gaps.once_only_prompt_reason_id,
    prompt_reachable_today: gaps.prompt_reachable_here,
    prompt_ledger_owner_seam: V5_J201_PROMPT_LEDGER_OWNER_SEAM,
    reconciles_on: "platform_and_native_source_identity",
    reconciles_on_time_overlap: false,
    activation: {
      accepted_intent: V5_J201_EXPLICIT_ACTIVATION_INTENT,
      refused_intents: [...V5_J201_REFUSED_ACTIVATION_INTENTS],
      requires_verified_partner: true,
    },
    recording: V5_J201_RECORDING_STATE,
    recording_permitted: false,
    audio_retained: false,
    recording_policy_seam: V5_J201_RECORDING_POLICY_SEAM,
    gaps,
    effects: V5_NO_EFFECTS,
  });
}
