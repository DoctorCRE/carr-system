// DoctorCRE v5 slice V5-J103, part two: the five Journey 1 correspondence
// judgments the 2026-09-20 planning amendment added to the slice, as a pure
// decision kernel.
//
// governed-correspondence.v5.js (PR #983) covers the first three done clauses —
// provenance-preserving reads, both-values conflict review, and drafts that
// cannot dispatch. The catalog entry was amended on 2026-09-20 (Joe, "do it")
// and gained five more, none of which that kernel addresses:
//
//   c4  A clear signed-lease email records EXECUTION-REPORTED / DOCUMENT-PENDING,
//       never document-confirmed without a valid artifact.
//   c5  Resolve attendee email, invite and recent correspondence BEFORE human
//       ambiguity escalation; a genuinely new party is proposed with research
//       and sourced uncertainty.
//   c6  Scheduled versus past calendar-derived touch is explicit; cancellation
//       and correction reconcile and never assert attendance.
//   c7  Requested tasks, promised commitments and suggestions stay separate;
//       dedupe is deterministic and completion is evidence-backed.
//   c8  Merges into an established record or its history need human approval;
//       replaying the same evidence cannot defeat a correction; a scoped
//       consumer circuit break stops consumption and preserves capture.
//
// THE AMENDMENT ALSO SETS THE CEILING, and this file is built under it. Its
// runtime_effect_status is NOT_ACTIVATED and the absence behaviour of
// step:governed-correspondence-internal-update-independent-receipt is "DENY
// internal updates; no fallback to general J1 receipt, code tests, high Jev
// probability, or doctrine approval". So every judgment below ends in a
// PROPOSAL. None of them writes, none of them claims authority, and every
// result carries `automatic_internal_update: false` with the gate that denies it
// named. Passing this suite is explicitly one of the fallbacks the amendment
// refuses, and this header says so rather than letting a reader infer otherwise.
//
// WHAT THIS FILE DOES NOT DECIDE, because an owner already does:
//
//   * DOCUMENT CONFIRMATION IS J102's. A lease becomes executed on the lifecycle
//     only through cre-lifecycle.v5.js, from evidence the record layer loaded
//     itself. This module never emits a confirmed document state: the most a
//     valid-looking artifact earns here is a ROUTE to J102's transition, and the
//     execution fact stays `execution_reported` with the document `pending`.
//   * FIELD AUTHORITY IS F01's. A merge proposal names the fields it would touch
//     and stops; it never decides which source owns them.
//   * SENDING IS NOBODY's IN CARR. No result names a provider operation, no
//     input may carry a destination, and a dispatch-shaped field name is a
//     contract violation that throws before any value is read — the same rule
//     governed-correspondence.v5.js enforces, reusing its fragment list.
//
// MODEL JUDGMENT IS AN INPUT, NEVER A VERDICT. Whether a signed-lease email is
// clear, and whether a sentence is a request, a promise or a suggestion, are
// judgments a model may PROPOSE. They arrive here as typed, enumerated values
// tagged with who proposed them, and they only ever produce proposals. Arithmetic
// — which instant is earlier, whether two revisions are ordered, whether two
// keys are equal — is done in this code and never delegated.
//
// THE MODULE IS PURE. No filesystem, network, database, scheduler, environment
// or clock: `now` arrives from the caller wherever one is needed. V5_NO_EFFECTS
// rides on every result.

import { canonicalJson, digest } from "./artifact-trust.js";
import { V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import {
  V5_J103_CREDENTIAL_FRAGMENTS,
  V5_J103_DISPATCH_FRAGMENTS,
  V5_J103_SEND_AUTHORITY_HOLDER,
  V5_J103_SOURCE_CONTENT_FRAGMENTS,
} from "./governed-correspondence.v5.js";
import {
  V5_F01_SIGNATURE_STATES,
  V5_F01_VALIDITY_STATES,
  V5_F01_VERSION_STATES,
} from "./record-source-authority.v5.js";
import {
  V5_J102_EVIDENCE_INTEGRITY,
  V5_J102_EVIDENCE_LOADER,
  V5_J102_EXECUTION_EVIDENCE_KINDS,
} from "./cre-lifecycle.v5.js";

export { V5_NO_EFFECTS };

export const V5_J103J_SCHEMA_VERSION = "doctorcre-v5-j103-journey-judgments.v1";
export const V5_J103J_POLICY_VERSION = 1;

/** The amendment's gate. Its absence DENIES every automatic internal update. */
export const V5_J103J_INTERNAL_UPDATE_STEP =
  "step:governed-correspondence-internal-update-independent-receipt";

/** Where document confirmation is decided. Named, never performed here. */
export const V5_J103J_DOCUMENT_CONFIRMATION_OWNER = "cre-lifecycle.v5.js#evaluateLifecycleTransition";

/** The one lease execution evidence kind this module routes. Bound by import. */
export const V5_J103J_LEASE_EVIDENCE_KIND = "executed_lease";
if (!V5_J102_EXECUTION_EVIDENCE_KINDS.includes(V5_J103J_LEASE_EVIDENCE_KIND)) {
  // A rename in J102 must break this module loudly at load, not leave it routing
  // a kind the lifecycle no longer recognizes.
  throw new Error("J102 no longer registers executed_lease as an execution evidence kind");
}

// ---------------------------------------------------------------------------
// Validators. Local by design, like every v5 module in this lane.
// ---------------------------------------------------------------------------

const SHA256_REF = /^sha256:[0-9a-f]{64}$/;
const EXTERNAL_IDENT = /^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,254}$/;
const INTERNAL_REF = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}$/;
const UNSAFE_TEXT =
  /[\u0000-\u001F\u007F-\u009F​-‏‪-‮⁠-⁤⁦-⁩﻿]/u;
const ISO_INSTANT =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(?:Z|([+-])(\d{2}):(\d{2}))$/;
const ROUTABLE_ADDRESS =
  /(^|[\s<,;:"'([])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|\b(?:mailto|smtp|sms|tel|callto|skype):/i;

export class V5J103JourneyError extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = "V5J103JourneyError";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function fail(code, message, detail) {
  throw new V5J103JourneyError(code, message, detail);
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

function assertObject(value, path) {
  if (!isPlainObject(value)) fail("invalid_shape", `${path} must be a plain object`, { path });
  return value;
}

function assertClosed(object, allowed, required, path) {
  assertObject(object, path);
  for (const key of Object.keys(object)) {
    if (!allowed.includes(key)) fail("unknown_field", `unknown field "${key}" at ${path}`, { path: `${path}.${key}`, key });
  }
  for (const key of required) {
    if (!(key in object)) fail("missing_field", `${path}.${key} is required`, { path: `${path}.${key}` });
  }
  return object;
}

function assertArray(value, path, { min = 0, max = 256 } = {}) {
  if (!Array.isArray(value)) fail("invalid_shape", `${path} must be an array`, { path });
  if (value.length < min) fail("invalid_shape", `${path} must hold at least ${min} entries`, { path });
  if (value.length > max) fail("too_many_entries", `${path} may hold at most ${max} entries`, { path });
  return value;
}

function assertSafeText(value, path, { maxLength = 255 } = {}) {
  if (typeof value !== "string" || value.length === 0) fail("invalid_shape", `${path} must be a non-empty string`, { path });
  if (value.length > maxLength) fail("text_too_long", `${path} may be at most ${maxLength} characters`, { path });
  if (typeof value.isWellFormed === "function" && !value.isWellFormed()) fail("malformed_unicode", `${path} contains an unpaired surrogate`, { path });
  if (UNSAFE_TEXT.test(value)) fail("unsafe_unicode", `${path} contains a control, bidirectional or invisible format character`, { path });
  return value;
}

function assertInternalRef(value, path) {
  assertSafeText(value, path);
  if (!INTERNAL_REF.test(value)) fail("invalid_reference", `${path} is not a permitted CARR reference; a reference carries no address characters`, { path });
  return value;
}

function assertExternalIdent(value, path) {
  assertSafeText(value, path);
  if (!EXTERNAL_IDENT.test(value)) fail("invalid_identifier", `${path} is not a permitted external identifier`, { path });
  return value;
}

function assertEnum(value, registered, path, code = "unregistered_value") {
  if (typeof value !== "string" || !registered.includes(value)) {
    fail(code, `"${String(value)}" is not registered at ${path}`, { path, registered: [...registered] });
  }
  return value;
}

function assertSafeInteger(value, path, { min = 0, max = Number.MAX_SAFE_INTEGER } = {}) {
  if (!Number.isSafeInteger(value) || value < min || value > max) fail("invalid_shape", `${path} must be a safe integer between ${min} and ${max}`, { path });
  return value;
}

function assertBoolean(value, path) {
  if (typeof value !== "boolean") fail("invalid_shape", `${path} must be a boolean`, { path });
  return value;
}

function assertSha256Ref(value, path) {
  if (typeof value !== "string" || !SHA256_REF.test(value)) fail("invalid_digest", `${path} must be a "sha256:" reference to a 64-character lower-case digest`, { path });
  return value;
}

function daysInMonth(year, month) {
  if (month === 2) return (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0 ? 29 : 28;
  return [4, 6, 9, 11].includes(month) ? 30 : 31;
}

/** Parsed, never inferred; an impossible calendar date is refused, not normalized. */
function assertInstant(value, path) {
  const match = typeof value === "string" ? ISO_INSTANT.exec(value) : null;
  if (!match) fail("invalid_timestamp", `${path} must be an ISO-8601 instant with an explicit offset`, { path });
  const [, year, month, day, hour, minute, second, , offsetHour, offsetMinute] = match;
  const y = Number(year), mo = Number(month), d = Number(day);
  if (mo < 1 || mo > 12 || d < 1 || d > daysInMonth(y, mo) || Number(hour) > 23 || Number(minute) > 59 ||
      Number(second) > 59 || (offsetHour !== undefined && (Number(offsetHour) > 23 || Number(offsetMinute) > 59))) {
    fail("invalid_timestamp", `${path} names an instant that does not exist on the calendar`, { path });
  }
  return Date.parse(value);
}

/**
 * The name and value sweep every entry runs FIRST, before any field is read as
 * meaning something. A dispatch-shaped or credential-shaped field NAME, a source
 * content field name, or a routable destination VALUE anywhere in the request is
 * a contract violation: the caller has assumed a capability this module does
 * not have, and answering politely would confirm it.
 */
/**
 * Native message identifiers are the one place an "@" is legitimate: an RFC 5322
 * Message-ID is shaped like `local@host` and names a message, not a destination.
 * Only the VALUE of these exact keys skips the address scan; each still passes
 * its own identifier validator, and every other value — any prose, any field a
 * body could hide in — is still scanned.
 */
const MESSAGE_ID_KEYS = Object.freeze(["native_id", "provider_message_id", "provider_thread_id"]);

function sweepRequest(value, path, key = null) {
  if (typeof value === "string") {
    if (key !== null && MESSAGE_ID_KEYS.includes(key)) return;
    if (ROUTABLE_ADDRESS.test(value)) {
      fail("routable_address_refused", `${path} carries a routable destination; nothing in J103 holds one`, { path });
    }
    return;
  }
  if (Array.isArray(value)) { value.forEach((entry, i) => sweepRequest(entry, `${path}[${i}]`)); return; }
  if (value !== null && typeof value === "object") {
    for (const [key, entry] of Object.entries(value)) {
      const lower = key.toLowerCase();
      const dispatch = V5_J103_DISPATCH_FRAGMENTS.find(f => lower.includes(f));
      if (dispatch) fail("dispatch_field_refused", `${path}.${key} names a dispatch capability ("${dispatch}"); J103 has none`, { path: `${path}.${key}`, fragment: dispatch });
      const credential = V5_J103_CREDENTIAL_FRAGMENTS.find(f => lower.includes(f));
      if (credential) fail("credential_field_refused", `${path}.${key} names a credential ("${credential}")`, { path: `${path}.${key}`, fragment: credential });
      const content = V5_J103_SOURCE_CONTENT_FRAGMENTS.find(f => lower.includes(f));
      if (content) fail("source_content_refused", `${path}.${key} names raw correspondence content ("${content}"); the mailbox stays the truth`, { path: `${path}.${key}`, fragment: content });
      sweepRequest(entry, `${path}.${key}`, key);
    }
  }
}

const NATIVE_KEYS = Object.freeze(["source_system", "native_id", "native_id_epoch"]);

function assertNativeRef(value, path) {
  assertClosed(value, NATIVE_KEYS, NATIVE_KEYS, path);
  assertExternalIdent(value.source_system, `${path}.source_system`);
  assertExternalIdent(value.native_id, `${path}.native_id`);
  assertSafeInteger(value.native_id_epoch, `${path}.native_id_epoch`, { min: 0, max: 1_000_000 });
  return value;
}

const PROPOSER_KINDS = deepFreeze(["model_seam", "partner", "deterministic_rule"]);

// ---------------------------------------------------------------------------
// The ceiling every result carries.
// ---------------------------------------------------------------------------

/**
 * The fields every judgment carries, whatever it decided. They are not flags a
 * caller can flip: they are constants of this module, stamped last so no branch
 * can overwrite them, and the suite asserts them on every result it produces.
 */
const CEILING = deepFreeze({
  automatic_internal_update: false,
  internal_update_gate: {
    step: V5_J103J_INTERNAL_UPDATE_STEP,
    status: "absent",
    absence_behavior: "deny",
  },
  authority_established: false,
  dispatchable: false,
  provider_operation: null,
  send_authority_holder: V5_J103_SEND_AUTHORITY_HOLDER,
  effects: V5_NO_EFFECTS,
});

function result(kind, body) {
  const core = {
    schema_version: V5_J103J_SCHEMA_VERSION,
    policy_version: V5_J103J_POLICY_VERSION,
    judgment: kind,
    ...body,
    ...CEILING,
  };
  const withDigest = { ...core, result_digest: digest(canonicalJson(core)) };
  return deepFreeze(withDigest);
}

// ---------------------------------------------------------------------------
// c4 — signed-lease email: execution REPORTED, document PENDING.
// ---------------------------------------------------------------------------

export const V5_J103J_LEASE_SIGNAL_BASES = deepFreeze([
  "counterparty_statement",
  "e_signature_completion_notice",
  "executed_document_attachment_descriptor",
  "partner_statement",
]);
export const V5_J103J_SIGNAL_CLARITY = deepFreeze(["clear", "unclear"]);
export const V5_J103J_LEASE_DECISIONS = deepFreeze([
  "propose_execution_reported_document_pending",
  "withhold_unclear_signal",
]);
export const V5_J103J_DOCUMENT_STATES = deepFreeze(["document_pending"]);
export const V5_J103J_DOCUMENT_ROUTES = deepFreeze([
  "no_artifact_presented",
  "artifact_not_valid_for_confirmation",
  "route_to_lifecycle_confirmation",
]);

const LEASE_REQUEST_KEYS = Object.freeze(["signal", "document_evidence"]);
const LEASE_SIGNAL_KEYS = Object.freeze([
  "record_ref", "message", "signal_basis", "clarity", "clarity_proposed_by", "observed_at",
]);
const LEASE_EVIDENCE_KEYS = Object.freeze(["evidence_kind", "provenance", "document"]);
const LEASE_PROVENANCE_KEYS = Object.freeze(["loaded_by", "reader", "loaded_at", "integrity"]);
const LEASE_DOCUMENT_KEYS = Object.freeze([
  "document_id", "content_digest", "signature_state", "validity_state", "version_state",
]);

/**
 * Why a presented artifact is not one J102 could confirm from, or null when it
 * has the shape J102 would. Even then this module does not confirm: J102 reloads
 * the evidence from its own committed row, and a caller's copy of that row is a
 * description of evidence, not evidence.
 */
function leaseArtifactDefect(evidence) {
  if (evidence.evidence_kind !== V5_J103J_LEASE_EVIDENCE_KIND) return "not_an_executed_lease_artifact";
  if (evidence.provenance.loaded_by !== V5_J102_EVIDENCE_LOADER) return "not_loaded_by_record_layer";
  if (evidence.provenance.integrity !== V5_J102_EVIDENCE_INTEGRITY) return "integrity_not_recomputed";
  if (evidence.document.signature_state !== "fully_executed") return "not_fully_executed";
  if (evidence.document.validity_state !== "effective") return "not_effective";
  if (evidence.document.version_state !== "current") return "not_current_version";
  return null;
}

export function classifySignedLeaseSignal(request) {
  sweepRequest(request, "request");
  assertClosed(request, LEASE_REQUEST_KEYS, ["signal"], "request");
  const signal = assertClosed(request.signal, LEASE_SIGNAL_KEYS, LEASE_SIGNAL_KEYS, "request.signal");
  assertInternalRef(signal.record_ref, "request.signal.record_ref");
  assertNativeRef(signal.message, "request.signal.message");
  assertEnum(signal.signal_basis, V5_J103J_LEASE_SIGNAL_BASES, "request.signal.signal_basis");
  assertEnum(signal.clarity, V5_J103J_SIGNAL_CLARITY, "request.signal.clarity");
  assertEnum(signal.clarity_proposed_by, PROPOSER_KINDS, "request.signal.clarity_proposed_by");
  assertInstant(signal.observed_at, "request.signal.observed_at");

  let route = "no_artifact_presented";
  let artifactDefect = null;
  const evidence = request.document_evidence ?? null;
  if (evidence !== null) {
    assertClosed(evidence, LEASE_EVIDENCE_KEYS, LEASE_EVIDENCE_KEYS, "request.document_evidence");
    assertSafeText(evidence.evidence_kind, "request.document_evidence.evidence_kind");
    const prov = assertClosed(evidence.provenance, LEASE_PROVENANCE_KEYS, LEASE_PROVENANCE_KEYS, "request.document_evidence.provenance");
    assertSafeText(prov.loaded_by, "request.document_evidence.provenance.loaded_by");
    assertSafeText(prov.reader, "request.document_evidence.provenance.reader");
    assertInstant(prov.loaded_at, "request.document_evidence.provenance.loaded_at");
    assertSafeText(prov.integrity, "request.document_evidence.provenance.integrity");
    const doc = assertClosed(evidence.document, LEASE_DOCUMENT_KEYS, LEASE_DOCUMENT_KEYS, "request.document_evidence.document");
    assertInternalRef(doc.document_id, "request.document_evidence.document.document_id");
    assertSha256Ref(doc.content_digest, "request.document_evidence.document.content_digest");
    assertEnum(doc.signature_state, V5_F01_SIGNATURE_STATES, "request.document_evidence.document.signature_state");
    assertEnum(doc.validity_state, V5_F01_VALIDITY_STATES, "request.document_evidence.document.validity_state");
    assertEnum(doc.version_state, V5_F01_VERSION_STATES, "request.document_evidence.document.version_state");
    artifactDefect = leaseArtifactDefect(evidence);
    route = artifactDefect === null ? "route_to_lifecycle_confirmation" : "artifact_not_valid_for_confirmation";
  }

  if (signal.clarity !== "clear") {
    return result("signed_lease_signal", {
      decision: "withhold_unclear_signal",
      reason_id: "j103.c4.unclear_signal_proposes_nothing",
      record_ref: signal.record_ref,
      message: { ...signal.message },
      proposal: null,
      document_state: "document_pending",
      document_route: route,
      artifact_defect: artifactDefect,
      document_confirmed: false,
      requires_human_approval: false,
    });
  }
  return result("signed_lease_signal", {
    decision: "propose_execution_reported_document_pending",
    reason_id: route === "route_to_lifecycle_confirmation"
      ? "j103.c4.execution_reported_confirmation_is_j102s"
      : "j103.c4.execution_reported_document_pending",
    record_ref: signal.record_ref,
    message: { ...signal.message },
    proposal: {
      fact: "lease_execution",
      execution_state: "execution_reported",
      document_state: "document_pending",
      basis: signal.signal_basis,
      clarity_proposed_by: signal.clarity_proposed_by,
      observed_at: signal.observed_at,
    },
    document_state: "document_pending",
    document_route: route,
    document_confirmation_owner: route === "route_to_lifecycle_confirmation"
      ? V5_J103J_DOCUMENT_CONFIRMATION_OWNER : null,
    artifact_defect: artifactDefect,
    // The clause's whole point, as a constant: this module never records a
    // confirmed document, with or without an artifact. J102 does, from its own row.
    document_confirmed: false,
    requires_human_approval: false,
  });
}

// ---------------------------------------------------------------------------
// c5 — participant resolution before ambiguity escalation.
// ---------------------------------------------------------------------------

/** The resolution ladder, in the order the clause names it. Every rung is owed. */
export const V5_J103J_RESOLUTION_RUNGS = deepFreeze([
  "attendee_address_match",
  "calendar_invite_participant",
  "recent_correspondence_counterpart",
]);
export const V5_J103J_RUNG_STATUSES = deepFreeze(["matched", "checked_no_match", "not_checked"]);
export const V5_J103J_RESOLUTION_DECISIONS = deepFreeze([
  "escalate_ambiguity_to_human",
  "escalate_contradiction_to_human",
  "propose_link_existing_party",
  "propose_new_party_with_research",
  "resolution_incomplete",
]);
export const V5_J103J_RESEARCH_ATTRIBUTES = deepFreeze([
  "organization", "relationship_to_subject", "role",
]);

const RESOLUTION_REQUEST_KEYS = Object.freeze(["participant", "rungs"]);
const PARTICIPANT_KEYS = Object.freeze(["participant_ref", "address_digest", "thread_ref"]);
const RUNG_KEYS = Object.freeze(["rung", "status", "matches", "searched_evidence_digest"]);
const MATCH_KEYS = Object.freeze(["party_ref", "evidence_digest", "observed_at"]);

export function resolveCorrespondenceParticipant(request) {
  sweepRequest(request, "request");
  assertClosed(request, RESOLUTION_REQUEST_KEYS, RESOLUTION_REQUEST_KEYS, "request");
  const participant = assertClosed(request.participant, PARTICIPANT_KEYS, PARTICIPANT_KEYS, "request.participant");
  assertInternalRef(participant.participant_ref, "request.participant.participant_ref");
  assertSha256Ref(participant.address_digest, "request.participant.address_digest");
  assertInternalRef(participant.thread_ref, "request.participant.thread_ref");

  const rungs = assertArray(request.rungs, "request.rungs", {
    min: V5_J103J_RESOLUTION_RUNGS.length, max: V5_J103J_RESOLUTION_RUNGS.length,
  });
  const byRung = new Map();
  rungs.forEach((rung, index) => {
    const path = `request.rungs[${index}]`;
    assertClosed(rung, RUNG_KEYS, RUNG_KEYS, path);
    assertEnum(rung.rung, V5_J103J_RESOLUTION_RUNGS, `${path}.rung`);
    if (byRung.has(rung.rung)) fail("duplicate_rung", `${path}.rung repeats "${rung.rung}"`, { path });
    assertEnum(rung.status, V5_J103J_RUNG_STATUSES, `${path}.status`);
    assertArray(rung.matches, `${path}.matches`, { max: 64 });
    rung.matches.forEach((match, m) => {
      assertClosed(match, MATCH_KEYS, MATCH_KEYS, `${path}.matches[${m}]`);
      assertInternalRef(match.party_ref, `${path}.matches[${m}].party_ref`);
      assertSha256Ref(match.evidence_digest, `${path}.matches[${m}].evidence_digest`);
      assertInstant(match.observed_at, `${path}.matches[${m}].observed_at`);
    });
    if (rung.status === "matched" && rung.matches.length === 0) {
      fail("matched_rung_without_match", `${path} says matched and carries no match`, { path });
    }
    if (rung.status !== "matched" && rung.matches.length > 0) {
      fail("unmatched_rung_with_match", `${path} says ${rung.status} and carries matches`, { path });
    }
    if (rung.status === "not_checked") {
      if (rung.searched_evidence_digest !== null) fail("unchecked_rung_with_search", `${path} was not checked and cannot carry a search digest`, { path });
    } else {
      assertSha256Ref(rung.searched_evidence_digest, `${path}.searched_evidence_digest`);
    }
    byRung.set(rung.rung, rung);
  });

  const base = {
    participant_ref: participant.participant_ref,
    thread_ref: participant.thread_ref,
    rungs_walked: V5_J103J_RESOLUTION_RUNGS.map(name => ({
      rung: name, status: byRung.get(name).status,
      candidates: [...new Set(byRung.get(name).matches.map(m => m.party_ref))].sort(),
    })),
    requires_human_approval: false,
  };

  // The order of the clause: resolve through EVERY rung before a human is asked.
  const unchecked = V5_J103J_RESOLUTION_RUNGS.filter(name => byRung.get(name).status === "not_checked");
  if (unchecked.length > 0) {
    return result("participant_resolution", {
      ...base,
      decision: "resolution_incomplete",
      reason_id: "j103.c5.every_rung_before_escalation",
      unchecked_rungs: unchecked,
      proposal: null,
      escalation: null,
    });
  }

  // Narrow by intersection, in ladder order. A rung with no match narrows
  // nothing; a rung that matches narrows to what it and every earlier matching
  // rung agree on.
  let narrowed = null;
  let contradiction = false;
  for (const name of V5_J103J_RESOLUTION_RUNGS) {
    const rung = byRung.get(name);
    if (rung.status !== "matched") continue;
    const refs = new Set(rung.matches.map(m => m.party_ref));
    if (narrowed === null) { narrowed = refs; continue; }
    const next = new Set([...narrowed].filter(ref => refs.has(ref)));
    if (next.size === 0) { contradiction = true; narrowed = new Set([...narrowed, ...refs]); break; }
    narrowed = next;
  }

  if (contradiction) {
    return result("participant_resolution", {
      ...base,
      decision: "escalate_contradiction_to_human",
      reason_id: "j103.c5.rungs_contradict_after_full_walk",
      proposal: null,
      escalation: { kind: "contradiction", candidates: [...narrowed].sort(), visible_to_partner: false },
    });
  }
  if (narrowed !== null && narrowed.size === 1) {
    const [party] = [...narrowed];
    const evidence = [];
    for (const name of V5_J103J_RESOLUTION_RUNGS) {
      for (const match of byRung.get(name).matches) {
        if (match.party_ref === party) evidence.push({ rung: name, evidence_digest: match.evidence_digest });
      }
    }
    return result("participant_resolution", {
      ...base,
      decision: "propose_link_existing_party",
      reason_id: "j103.c5.resolved_by_ladder",
      proposal: { action: "link_participant_to_party", party_ref: party, evidence },
      escalation: null,
    });
  }
  if (narrowed !== null && narrowed.size > 1) {
    return result("participant_resolution", {
      ...base,
      decision: "escalate_ambiguity_to_human",
      reason_id: "j103.c5.ambiguous_after_full_walk",
      proposal: null,
      escalation: { kind: "ambiguity", candidates: [...narrowed].sort(), visible_to_partner: false },
    });
  }
  // Every rung checked and none matched: a genuinely new party, proposed with the
  // research it needs and the uncertainty stated per attribute, each sourced to
  // the searches that came back empty.
  const searched = V5_J103J_RESOLUTION_RUNGS.map(name => ({
    rung: name, searched_evidence_digest: byRung.get(name).searched_evidence_digest,
  }));
  return result("participant_resolution", {
    ...base,
    decision: "propose_new_party_with_research",
    reason_id: "j103.c5.new_party_after_empty_ladder",
    proposal: {
      action: "create_party",
      address_digest: participant.address_digest,
      research: V5_J103J_RESEARCH_ATTRIBUTES.map(attribute => ({
        attribute, status: "unknown", sourced_uncertainty: searched,
      })),
    },
    escalation: null,
  });
}

// ---------------------------------------------------------------------------
// c6 — calendar-derived touches: scheduled versus past, never attendance.
// ---------------------------------------------------------------------------

export const V5_J103J_EVENT_STATUSES = deepFreeze(["cancelled", "confirmed", "tentative"]);
export const V5_J103J_TOUCH_TEMPORALITY = deepFreeze(["in_progress", "past", "scheduled"]);
export const V5_J103J_TOUCH_DECISIONS = deepFreeze([
  "no_change_duplicate_revision",
  "propose_past_calendar_touch",
  "propose_scheduled_meeting",
  "reconcile_cancellation",
  "reconcile_correction",
  "reconcile_reinstatement",
  "refuse_stale_revision",
  "still_cancelled_no_touch",
  "withhold_in_progress",
  "withhold_tentative_past",
]);

const TOUCH_REQUEST_KEYS = Object.freeze(["event", "prior", "now"]);
const EVENT_KEYS = Object.freeze(["record_ref", "event", "revision", "status", "starts_at", "ends_at"]);
const PRIOR_KEYS = Object.freeze(["revision", "status", "starts_at", "ends_at", "proposal_digest"]);

function eventDigest(e) {
  return digest(canonicalJson({ status: e.status, starts_at: e.starts_at, ends_at: e.ends_at }));
}

export function classifyCalendarTouch(request) {
  sweepRequest(request, "request");
  assertClosed(request, TOUCH_REQUEST_KEYS, TOUCH_REQUEST_KEYS, "request");
  const ev = assertClosed(request.event, EVENT_KEYS, EVENT_KEYS, "request.event");
  assertInternalRef(ev.record_ref, "request.event.record_ref");
  assertNativeRef(ev.event, "request.event.event");
  assertSafeInteger(ev.revision, "request.event.revision", { min: 0 });
  assertEnum(ev.status, V5_J103J_EVENT_STATUSES, "request.event.status");
  const start = assertInstant(ev.starts_at, "request.event.starts_at");
  const end = assertInstant(ev.ends_at, "request.event.ends_at");
  if (end < start) fail("event_ends_before_start", "request.event.ends_at is before starts_at", {});
  const now = assertInstant(request.now, "request.now");

  let prior = null;
  if (request.prior !== null) {
    prior = assertClosed(request.prior, PRIOR_KEYS, PRIOR_KEYS, "request.prior");
    assertSafeInteger(prior.revision, "request.prior.revision", { min: 0 });
    assertEnum(prior.status, V5_J103J_EVENT_STATUSES, "request.prior.status");
    assertInstant(prior.starts_at, "request.prior.starts_at");
    assertInstant(prior.ends_at, "request.prior.ends_at");
    assertSha256Ref(prior.proposal_digest, "request.prior.proposal_digest");
  }

  // Arithmetic in code: which side of `now` the event sits on.
  const temporality = start > now ? "scheduled" : end <= now ? "past" : "in_progress";
  const base = {
    record_ref: ev.record_ref,
    event: { ...ev.event },
    revision: ev.revision,
    temporality,
    // Never asserted, whatever the calendar says. A calendar says a meeting was
    // planned; it cannot say anyone came.
    attendance: "not_asserted",
    attendance_asserted: false,
    requires_human_approval: false,
  };

  if (prior !== null) {
    // Ordered by the SOURCE's revision, never by which copy arrived last.
    if (ev.revision < prior.revision) {
      return result("calendar_touch", {
        ...base, decision: "refuse_stale_revision", reason_id: "j103.c6.revision_behind_prior",
        prior_revision: prior.revision, proposal: null, reconciliation: null,
      });
    }
    const sameContent = eventDigest(ev) === eventDigest(prior);
    if (ev.revision === prior.revision) {
      if (!sameContent) {
        fail("revision_content_split", "the same source revision arrived with different content; the source is inconsistent and this module will not pick a side", { revision: ev.revision });
      }
      return result("calendar_touch", {
        ...base, decision: "no_change_duplicate_revision", reason_id: "j103.c6.same_revision_same_content",
        proposal: null, reconciliation: null,
      });
    }
    // A CANCELLED EVENT NEVER BECOMES A TOUCH, whatever else its new revision
    // changed. Every cancelled branch returns here, before the correction branch
    // could build a proposal from it (review blocker 1 on #1266: a cancelled event
    // whose newer revision only moved its times fell through to a correction and
    // came back as a past touch).
    if (ev.status === "cancelled" && prior.status === "cancelled") {
      return result("calendar_touch", {
        ...base,
        decision: sameContent ? "no_change_duplicate_revision" : "still_cancelled_no_touch",
        reason_id: sameContent ? "j103.c6.new_revision_same_content" : "j103.c6.cancelled_event_moved_still_no_touch",
        proposal: null, reconciliation: null,
      });
    }
    if (ev.status === "cancelled") {
      return result("calendar_touch", {
        ...base,
        decision: "reconcile_cancellation",
        reason_id: temporality === "scheduled"
          ? "j103.c6.cancelled_before_it_happened"
          : "j103.c6.cancelled_after_start_attendance_unknown",
        proposal: null,
        reconciliation: {
          action: temporality === "scheduled" ? "withdraw_scheduled_meeting" : "mark_calendar_touch_cancelled_unverified",
          supersedes_proposal_digest: prior.proposal_digest,
          attendance: "not_asserted",
        },
      });
    }
    // UN-CANCELLED. The source says the meeting is back on. That is a statement
    // about a plan, never about attendance: a reinstated future meeting may be
    // proposed as scheduled (not a touch); a reinstated meeting whose time has
    // already passed proposes nothing and is flagged for a human, because
    // "cancelled, then un-cancelled after the fact" says nothing about whether
    // anyone met.
    if (prior.status === "cancelled") {
      const proposal = temporality === "scheduled" ? touchProposal(ev, temporality) : null;
      return result("calendar_touch", {
        ...base,
        decision: "reconcile_reinstatement",
        reason_id: temporality === "scheduled"
          ? "j103.c6.reinstated_before_it_happened"
          : "j103.c6.reinstated_after_start_attendance_unknown",
        proposal,
        reconciliation: {
          action: temporality === "scheduled" ? "reinstate_scheduled_meeting" : "flag_reinstated_after_start_for_human",
          supersedes_proposal_digest: prior.proposal_digest,
          attendance: "not_asserted",
        },
        requires_human_approval: temporality !== "scheduled",
      });
    }
    if (!sameContent) {
      return result("calendar_touch", {
        ...base,
        decision: "reconcile_correction",
        reason_id: "j103.c6.source_corrected_event",
        proposal: touchProposal(ev, temporality),
        reconciliation: {
          action: "supersede_prior_proposal",
          supersedes_proposal_digest: prior.proposal_digest,
          attendance: "not_asserted",
        },
      });
    }
    return result("calendar_touch", {
      ...base, decision: "no_change_duplicate_revision", reason_id: "j103.c6.new_revision_same_content",
      proposal: null, reconciliation: null,
    });
  }

  if (ev.status === "cancelled") {
    return result("calendar_touch", {
      ...base, decision: "reconcile_cancellation", reason_id: "j103.c6.cancelled_with_nothing_to_withdraw",
      proposal: null, reconciliation: { action: "none_nothing_recorded", supersedes_proposal_digest: null, attendance: "not_asserted" },
    });
  }
  if (temporality === "in_progress") {
    return result("calendar_touch", {
      ...base, decision: "withhold_in_progress", reason_id: "j103.c6.neither_scheduled_nor_past",
      proposal: null, reconciliation: null,
    });
  }
  // A tentative meeting that has passed was never confirmed; it is not a touch.
  if (temporality === "past" && ev.status !== "confirmed") {
    return result("calendar_touch", {
      ...base, decision: "withhold_tentative_past", reason_id: "j103.c6.tentative_past_is_not_a_touch",
      proposal: null, reconciliation: null,
    });
  }
  return result("calendar_touch", {
    ...base,
    decision: temporality === "scheduled" ? "propose_scheduled_meeting" : "propose_past_calendar_touch",
    reason_id: temporality === "scheduled" ? "j103.c6.scheduled_is_not_a_touch" : "j103.c6.past_touch_from_calendar",
    proposal: touchProposal(ev, temporality),
    reconciliation: null,
  });
}

/**
 * The ONE place a touch proposal is built, and the one place `counts_as_touch`
 * can become true: a CONFIRMED event that is wholly in the past. Cancelled and
 * tentative events, and anything in progress, get no touch here whichever branch
 * asked — so a future branch cannot reintroduce blocker 1 by forgetting a check.
 */
function touchProposal(ev, temporality) {
  if (temporality === "in_progress" || ev.status === "cancelled") return null;
  if (temporality === "past" && ev.status !== "confirmed") return null;
  return temporality === "scheduled"
    ? { record: "scheduled_meeting", counts_as_touch: false, starts_at: ev.starts_at, ends_at: ev.ends_at, attendance: "not_asserted" }
    : { record: "calendar_derived_touch", counts_as_touch: true, occurred_at: ev.starts_at, basis: "calendar_event_past", attendance: "not_asserted" };
}

// ---------------------------------------------------------------------------
// c7 — requested tasks, promised commitments and suggestions.
// ---------------------------------------------------------------------------

export const V5_J103J_COMMITMENT_KINDS = deepFreeze([
  "promised_commitment", "requested_task", "suggestion",
]);
export const V5_J103J_COMPLETION_EVIDENCE_KINDS = deepFreeze([
  "correspondence_message_ref", "document_ref", "record_ref",
]);
export const V5_J103J_COMMITMENT_DECISIONS = deepFreeze([
  "completion_refused_no_evidence",
  "duplicate_of_existing",
  "duplicate_within_batch",
  "note_suggestion",
  "propose_commitment",
  "propose_evidence_backed_completion",
  "propose_task",
]);

const COMMITMENT_REQUEST_KEYS = Object.freeze(["items", "existing"]);
const ITEM_KEYS = Object.freeze([
  "kind", "kind_proposed_by", "record_ref", "owed_by_ref", "owed_to_ref", "action_key",
  "due_on", "source_message", "evidence_digest", "completion",
]);
const COMPLETION_KEYS = Object.freeze(["claimed_by", "evidence"]);
const COMPLETION_EVIDENCE_KEYS = Object.freeze(["evidence_kind", "evidence_ref", "evidence_digest"]);
const EXISTING_KEYS = Object.freeze(["dedupe_key", "state"]);
const DUE_ON = /^\d{4}-\d{2}-\d{2}$/;

/**
 * The dedupe key. Deterministic over exactly what makes two items the same
 * obligation — its kind, who owes it, to whom, about what, and the normalized
 * action — and over nothing that varies between two mentions of it, such as the
 * message it was seen in. A request and a promise with the same words are
 * different keys on purpose: the clause says they stay separate.
 */
export function commitmentDedupeKey(item) {
  return digest(canonicalJson({
    kind: item.kind,
    record_ref: item.record_ref,
    owed_by_ref: item.owed_by_ref,
    owed_to_ref: item.owed_to_ref,
    action_key: item.action_key,
  }));
}

export function classifyCorrespondenceCommitments(request) {
  sweepRequest(request, "request");
  assertClosed(request, COMMITMENT_REQUEST_KEYS, COMMITMENT_REQUEST_KEYS, "request");
  assertArray(request.items, "request.items", { min: 1, max: 64 });
  assertArray(request.existing, "request.existing", { max: 1024 });
  const existing = new Map();
  request.existing.forEach((row, i) => {
    assertClosed(row, EXISTING_KEYS, EXISTING_KEYS, `request.existing[${i}]`);
    assertSha256Ref(row.dedupe_key, `request.existing[${i}].dedupe_key`);
    assertEnum(row.state, ["completed", "open"], `request.existing[${i}].state`);
    existing.set(row.dedupe_key, row.state);
  });

  const seen = new Map();
  const seenCompletion = new Map();
  const outcomes = request.items.map((item, i) => {
    const path = `request.items[${i}]`;
    assertClosed(item, ITEM_KEYS, ITEM_KEYS, path);
    assertEnum(item.kind, V5_J103J_COMMITMENT_KINDS, `${path}.kind`);
    assertEnum(item.kind_proposed_by, PROPOSER_KINDS, `${path}.kind_proposed_by`);
    assertInternalRef(item.record_ref, `${path}.record_ref`);
    assertInternalRef(item.owed_by_ref, `${path}.owed_by_ref`);
    assertInternalRef(item.owed_to_ref, `${path}.owed_to_ref`);
    assertInternalRef(item.action_key, `${path}.action_key`);
    if (item.due_on !== null && (typeof item.due_on !== "string" || !DUE_ON.test(item.due_on))) {
      fail("invalid_shape", `${path}.due_on must be YYYY-MM-DD or null`, { path });
    }
    assertNativeRef(item.source_message, `${path}.source_message`);
    assertSha256Ref(item.evidence_digest, `${path}.evidence_digest`);

    const key = commitmentDedupeKey(item);
    const outcome = { index: i, kind: item.kind, dedupe_key: key, source_message: { ...item.source_message } };

    if (item.completion !== null) {
      const completion = assertClosed(item.completion, COMPLETION_KEYS, COMPLETION_KEYS, `${path}.completion`);
      assertEnum(completion.claimed_by, PROPOSER_KINDS, `${path}.completion.claimed_by`);
      assertArray(completion.evidence, `${path}.completion.evidence`, { max: 16 });
      completion.evidence.forEach((ev, e) => {
        assertClosed(ev, COMPLETION_EVIDENCE_KEYS, COMPLETION_EVIDENCE_KEYS, `${path}.completion.evidence[${e}]`);
        assertEnum(ev.evidence_kind, V5_J103J_COMPLETION_EVIDENCE_KINDS, `${path}.completion.evidence[${e}].evidence_kind`);
        assertInternalRef(ev.evidence_ref, `${path}.completion.evidence[${e}].evidence_ref`);
        assertSha256Ref(ev.evidence_digest, `${path}.completion.evidence[${e}].evidence_digest`);
      });
      if (item.kind === "suggestion") {
        fail("suggestion_cannot_complete", `${path} is a suggestion; nothing was owed, so nothing completes`, { path });
      }
      if (completion.evidence.length === 0) {
        return { ...outcome, decision: "completion_refused_no_evidence", reason_id: "j103.c7.completion_needs_evidence", proposal: null };
      }
      // One obligation completes once. A second claim for the same obligation in
      // the same batch — the same reply quoted twice, a thread read twice — is a
      // duplicate, not a second completion.
      if (seenCompletion.has(key)) {
        return { ...outcome, decision: "duplicate_within_batch", reason_id: "j103.c7.same_completion_twice", first_index: seenCompletion.get(key), proposal: null };
      }
      seenCompletion.set(key, i);
      return {
        ...outcome,
        decision: "propose_evidence_backed_completion",
        reason_id: "j103.c7.completion_with_cited_evidence",
        proposal: {
          action: "complete_obligation",
          dedupe_key: key,
          matches_existing: existing.has(key),
          claimed_by: completion.claimed_by,
          evidence: completion.evidence.map(ev => ({ ...ev })),
        },
      };
    }

    if (existing.has(key)) {
      return { ...outcome, decision: "duplicate_of_existing", reason_id: "j103.c7.already_recorded", existing_state: existing.get(key), proposal: null };
    }
    if (seen.has(key)) {
      return { ...outcome, decision: "duplicate_within_batch", reason_id: "j103.c7.same_obligation_twice", first_index: seen.get(key), proposal: null };
    }
    seen.set(key, i);

    if (item.kind === "suggestion") {
      return {
        ...outcome, decision: "note_suggestion", reason_id: "j103.c7.suggestion_creates_no_obligation",
        proposal: { action: "note_suggestion", creates_task: false, creates_commitment: false, kind_proposed_by: item.kind_proposed_by },
      };
    }
    return {
      ...outcome,
      decision: item.kind === "requested_task" ? "propose_task" : "propose_commitment",
      reason_id: item.kind === "requested_task" ? "j103.c7.request_is_a_task" : "j103.c7.promise_is_a_commitment",
      proposal: {
        action: item.kind === "requested_task" ? "create_task" : "record_commitment",
        dedupe_key: key,
        owed_by_ref: item.owed_by_ref,
        owed_to_ref: item.owed_to_ref,
        due_on: item.due_on,
        kind_proposed_by: item.kind_proposed_by,
        evidence_digest: item.evidence_digest,
      },
    };
  });

  return result("correspondence_commitments", { outcomes, requires_human_approval: false });
}

// ---------------------------------------------------------------------------
// c8 — merges, corrections and replay, and the scoped consumer circuit.
// ---------------------------------------------------------------------------

export const V5_J103J_TARGET_STATES = deepFreeze(["established", "provisional"]);
export const V5_J103J_MERGE_DECISIONS = deepFreeze([
  "human_approval_required",
  "propose_provisional_merge",
  "refuse_replay_of_corrected_evidence",
]);
export const V5_J103J_BREAKER_STATES = deepFreeze(["closed", "open"]);
export const V5_J103J_CONSUMPTION_DECISIONS = deepFreeze(["consumption_permitted_as_proposal", "consumption_suspended"]);

const MERGE_REQUEST_KEYS = Object.freeze(["merge", "corrections"]);
const MERGE_KEYS = Object.freeze([
  "target_ref", "target_state", "alters_history", "source_ref", "evidence_digest", "fields",
]);
const CORRECTION_KEYS = Object.freeze(["correction_id", "target_ref", "field", "superseded_evidence_digest"]);
const FIELD_NAME = /^[a-z][a-z0-9_]{0,63}$/;

export function evaluateCorrespondenceMerge(request) {
  sweepRequest(request, "request");
  assertClosed(request, MERGE_REQUEST_KEYS, MERGE_REQUEST_KEYS, "request");
  const merge = assertClosed(request.merge, MERGE_KEYS, MERGE_KEYS, "request.merge");
  assertInternalRef(merge.target_ref, "request.merge.target_ref");
  assertEnum(merge.target_state, V5_J103J_TARGET_STATES, "request.merge.target_state");
  assertBoolean(merge.alters_history, "request.merge.alters_history");
  assertInternalRef(merge.source_ref, "request.merge.source_ref");
  assertSha256Ref(merge.evidence_digest, "request.merge.evidence_digest");
  assertArray(merge.fields, "request.merge.fields", { min: 1, max: 64 });
  merge.fields.forEach((f, i) => {
    if (typeof f !== "string" || !FIELD_NAME.test(f)) fail("invalid_field_name", `request.merge.fields[${i}] is not a field name`, {});
  });
  assertArray(request.corrections, "request.corrections", { max: 1024 });
  request.corrections.forEach((c, i) => {
    assertClosed(c, CORRECTION_KEYS, CORRECTION_KEYS, `request.corrections[${i}]`);
    assertInternalRef(c.correction_id, `request.corrections[${i}].correction_id`);
    assertInternalRef(c.target_ref, `request.corrections[${i}].target_ref`);
    if (typeof c.field !== "string" || !FIELD_NAME.test(c.field)) fail("invalid_field_name", `request.corrections[${i}].field is not a field name`, {});
    assertSha256Ref(c.superseded_evidence_digest, `request.corrections[${i}].superseded_evidence_digest`);
  });

  const base = {
    target_ref: merge.target_ref,
    target_state: merge.target_state,
    fields: [...merge.fields].sort(),
    evidence_digest: merge.evidence_digest,
  };

  // Replay first, and regardless of approval: evidence a human already corrected
  // away cannot come back in through a merge, not even an approved one.
  const defeated = request.corrections.filter(c =>
    c.target_ref === merge.target_ref && merge.fields.includes(c.field) &&
    c.superseded_evidence_digest === merge.evidence_digest);
  if (defeated.length > 0) {
    return result("correspondence_merge", {
      ...base,
      decision: "refuse_replay_of_corrected_evidence",
      reason_id: "j103.c8.same_evidence_cannot_defeat_correction",
      corrections_held: defeated.map(c => c.correction_id).sort(),
      proposal: null,
      requires_human_approval: false,
    });
  }
  if (merge.target_state === "established" || merge.alters_history) {
    return result("correspondence_merge", {
      ...base,
      decision: "human_approval_required",
      reason_id: merge.alters_history ? "j103.c8.history_merge_needs_human" : "j103.c8.established_record_needs_human",
      proposal: { action: "merge", source_ref: merge.source_ref, fields: [...merge.fields].sort() },
      requires_human_approval: true,
    });
  }
  return result("correspondence_merge", {
    ...base,
    decision: "propose_provisional_merge",
    reason_id: "j103.c8.provisional_merge_proposed",
    proposal: { action: "merge", source_ref: merge.source_ref, fields: [...merge.fields].sort() },
    requires_human_approval: false,
  });
}

const CIRCUIT_REQUEST_KEYS = Object.freeze(["consumer_id", "scope", "capture_digest", "breakers"]);
const BREAKER_KEYS = Object.freeze(["consumer_id", "scope", "state", "opened_reason_id"]);

/**
 * The scoped consumer circuit. A breaker belongs to ONE consumer on ONE scope;
 * an open breaker suspends that consumer's use of new captures and nothing else.
 * Capture is never suspended by a breaker — the circuit protects downstream
 * consumers from bad input, and dropping the input would destroy the evidence
 * needed to decide the breaker can close.
 */
export function evaluateConsumerCircuit(request) {
  sweepRequest(request, "request");
  assertClosed(request, CIRCUIT_REQUEST_KEYS, CIRCUIT_REQUEST_KEYS, "request");
  assertInternalRef(request.consumer_id, "request.consumer_id");
  assertInternalRef(request.scope, "request.scope");
  assertSha256Ref(request.capture_digest, "request.capture_digest");
  assertArray(request.breakers, "request.breakers", { max: 256 });
  const keys = new Set();
  request.breakers.forEach((b, i) => {
    assertClosed(b, BREAKER_KEYS, BREAKER_KEYS, `request.breakers[${i}]`);
    assertInternalRef(b.consumer_id, `request.breakers[${i}].consumer_id`);
    assertInternalRef(b.scope, `request.breakers[${i}].scope`);
    assertEnum(b.state, V5_J103J_BREAKER_STATES, `request.breakers[${i}].state`);
    if (b.state === "open") assertInternalRef(b.opened_reason_id, `request.breakers[${i}].opened_reason_id`);
    else if (b.opened_reason_id !== null) fail("closed_breaker_with_reason", `request.breakers[${i}] is closed and carries an open reason`, {});
    const k = `${b.consumer_id}\u0000${b.scope}`;
    if (keys.has(k)) fail("duplicate_breaker", `request.breakers[${i}] repeats a consumer and scope`, {});
    keys.add(k);
  });
  const own = request.breakers.find(b => b.consumer_id === request.consumer_id && b.scope === request.scope) ?? null;
  const open = own !== null && own.state === "open";
  return result("consumer_circuit", {
    consumer_id: request.consumer_id,
    scope: request.scope,
    capture_digest: request.capture_digest,
    capture: "recorded",
    capture_preserved: true,
    decision: open ? "consumption_suspended" : "consumption_permitted_as_proposal",
    reason_id: open ? "j103.c8.scoped_breaker_open" : "j103.c8.no_open_breaker_for_scope",
    breaker_reason_id: open ? own.opened_reason_id : null,
    requires_human_approval: false,
  });
}

// ---------------------------------------------------------------------------
// Load-time self-check: the sweep can never refuse a field this module requires.
// The sweep matches SUBSTRINGS of names, which is what lets it catch a field
// nobody anticipated — and what would let it refuse `subject_ref` for carrying
// "subject". Every closed key set is checked against every fragment list here, so
// such a collision fails at import rather than as a mysterious refusal later.
// ---------------------------------------------------------------------------

for (const keys of [
  LEASE_REQUEST_KEYS, LEASE_SIGNAL_KEYS, LEASE_EVIDENCE_KEYS, LEASE_PROVENANCE_KEYS,
  LEASE_DOCUMENT_KEYS, NATIVE_KEYS, RESOLUTION_REQUEST_KEYS, PARTICIPANT_KEYS, RUNG_KEYS,
  MATCH_KEYS, TOUCH_REQUEST_KEYS, EVENT_KEYS, PRIOR_KEYS, COMMITMENT_REQUEST_KEYS, ITEM_KEYS,
  COMPLETION_KEYS, COMPLETION_EVIDENCE_KEYS, EXISTING_KEYS, MERGE_REQUEST_KEYS, MERGE_KEYS,
  CORRECTION_KEYS, CIRCUIT_REQUEST_KEYS, BREAKER_KEYS,
]) {
  for (const key of keys) {
    const lower = key.toLowerCase();
    for (const fragments of [V5_J103_DISPATCH_FRAGMENTS, V5_J103_CREDENTIAL_FRAGMENTS, V5_J103_SOURCE_CONTENT_FRAGMENTS]) {
      const hit = fragments.find(f => lower.includes(f));
      if (hit !== undefined) {
        throw new V5J103JourneyError("guard_refuses_own_schema",
          `the request sweep would refuse "${key}" (fragment "${hit}"), a field this module requires`, { key, fragment: hit });
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Policy digest and surface.
// ---------------------------------------------------------------------------

export function v5J103JourneyPolicyPreimage() {
  return deepFreeze({
    schema_version: V5_J103J_SCHEMA_VERSION,
    policy_version: V5_J103J_POLICY_VERSION,
    internal_update_step: V5_J103J_INTERNAL_UPDATE_STEP,
    document_confirmation_owner: V5_J103J_DOCUMENT_CONFIRMATION_OWNER,
    lease_evidence_kind: V5_J103J_LEASE_EVIDENCE_KIND,
    lease_signal_bases: V5_J103J_LEASE_SIGNAL_BASES,
    lease_decisions: V5_J103J_LEASE_DECISIONS,
    document_states: V5_J103J_DOCUMENT_STATES,
    document_routes: V5_J103J_DOCUMENT_ROUTES,
    resolution_rungs: V5_J103J_RESOLUTION_RUNGS,
    resolution_decisions: V5_J103J_RESOLUTION_DECISIONS,
    research_attributes: V5_J103J_RESEARCH_ATTRIBUTES,
    event_statuses: V5_J103J_EVENT_STATUSES,
    touch_decisions: V5_J103J_TOUCH_DECISIONS,
    commitment_kinds: V5_J103J_COMMITMENT_KINDS,
    completion_evidence_kinds: V5_J103J_COMPLETION_EVIDENCE_KINDS,
    commitment_decisions: V5_J103J_COMMITMENT_DECISIONS,
    merge_decisions: V5_J103J_MERGE_DECISIONS,
    consumption_decisions: V5_J103J_CONSUMPTION_DECISIONS,
    ceiling: CEILING,
  });
}

export function v5J103JourneyPolicyDigest() {
  return digest(canonicalJson(v5J103JourneyPolicyPreimage()));
}

export const V5_J103J_PUBLIC_SURFACE = deepFreeze([
  "V5J103JourneyError", "V5_J103J_BREAKER_STATES", "V5_J103J_COMMITMENT_DECISIONS",
  "V5_J103J_COMMITMENT_KINDS", "V5_J103J_COMPLETION_EVIDENCE_KINDS",
  "V5_J103J_CONSUMPTION_DECISIONS", "V5_J103J_DOCUMENT_CONFIRMATION_OWNER",
  "V5_J103J_DOCUMENT_ROUTES", "V5_J103J_DOCUMENT_STATES", "V5_J103J_EVENT_STATUSES",
  "V5_J103J_INTERNAL_UPDATE_STEP", "V5_J103J_LEASE_DECISIONS", "V5_J103J_LEASE_EVIDENCE_KIND",
  "V5_J103J_LEASE_SIGNAL_BASES", "V5_J103J_MERGE_DECISIONS", "V5_J103J_POLICY_VERSION",
  "V5_J103J_PUBLIC_SURFACE", "V5_J103J_RESEARCH_ATTRIBUTES", "V5_J103J_RESOLUTION_DECISIONS",
  "V5_J103J_RESOLUTION_RUNGS", "V5_J103J_RUNG_STATUSES", "V5_J103J_SCHEMA_VERSION",
  "V5_J103J_SIGNAL_CLARITY", "V5_J103J_TARGET_STATES", "V5_J103J_TOUCH_DECISIONS",
  "V5_J103J_TOUCH_TEMPORALITY", "V5_NO_EFFECTS", "classifyCalendarTouch",
  "classifyCorrespondenceCommitments", "classifySignedLeaseSignal", "commitmentDedupeKey",
  "evaluateConsumerCircuit", "evaluateCorrespondenceMerge", "resolveCorrespondenceParticipant",
  "v5J103JourneyPolicyDigest", "v5J103JourneyPolicyPreimage",
]);
