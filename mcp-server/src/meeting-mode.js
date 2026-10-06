// V5-UX-B11 — non-recording shared Meeting Mode: the verbs.
//
// LIBRARY ONLY: no shebang and no main-module construct (the SCAC inventory
// scans for those by substring, so they are described, never spelled). This
// file is a registrySource, so every verb below is an inventoried ingress.
//
// WHAT THE VERBS ARE. Thin, closed doors onto the ops.* store in migration
// 0556. Every state transition, every lock, every idempotency comparison and
// every attribution happens inside those SECURITY DEFINER functions, which
// derive the actor and tenant from the server-installed transaction context. A
// check written only here would be bypassed by a direct SQL call, so this file
// validates shape and refuses early; it never decides authority.
//
// THE CONNECTION FLAGS ARE PART OF THE CONTRACT. The DoctorCRE app calls these
// as the signed-in partner, so the writes declare write: true with
// writerConnection: true (never authorityOnly), and the read declares
// writerConnection with no write flag, which is the `begin read only`
// transaction that still carries the actor context. The functions are granted
// to carr_writer only.
//
// WHAT THIS SLICE DOES NOT DO, said by name:
//   * It records no audio. Every argument NAME is checked against J201's
//     recording fragments, recursively, before anything runs; the store has no
//     audio column and CHECKs ops.meeting.recording to 'denied'. The legacy
//     capture_session recorder is not read or reused, and nothing here is
//     evidence for the later D03 recording extension.
//   * It raises no detection prompt. J201's durable prompt-ledger owner does
//     not exist, so a meeting starts only on a verified partner's explicit
//     one-tap and read-meeting reports the prompt as unavailable at that seam.
//   * It executes no business effect. An accepted action carries the exact
//     canonical call (an existing MCP write verb, its arguments, and the one
//     idempotency key to use). The caller makes that call through the existing
//     verb; record-meeting-action-outcome then reconciles it against the
//     canonical envelope ledger. Proposed is never reported as done.

import {
  V5_J201_EXPLICIT_ACTIVATION_INTENT,
  V5_J201_PLATFORMS,
  V5_J201_PROMPT_LEDGER_OWNER_SEAM,
  V5_J201_RECORDING_FRAGMENTS,
  V5_J201_RECORDING_POLICY_SEAM,
  V5_J201_RECORDING_STATE,
  V5_J201_REFUSED_ACTIVATION_INTENTS,
  V5J201Error,
  meetingKey,
  meetingModeGaps,
} from "./meeting-call-mode-j201.v5.js";

export const MEETING_MODE_SCHEMA_VERSION = "doctorcre-meeting-mode.v1";

/** The write verbs this file registers. tools.js serializes same-key calls to them. */
export const MEETING_MODE_WRITE_VERBS = Object.freeze([
  "start-meeting", "claim-meeting-processing", "add-meeting-note", "propose-meeting-action",
  "decide-meeting-action", "record-meeting-action-outcome", "end-meeting",
]);
export const MEETING_MODE_VERBS = Object.freeze([...MEETING_MODE_WRITE_VERBS, "read-meeting"]);

/** The four recap buckets the handoff names, and the one place a declined action goes. */
export const MEETING_RECAP_BUCKETS = Object.freeze([
  "done", "delegated", "needs_approval", "unresolved",
]);

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
// J201's external-identifier shape, carried locally on purpose (J201: a shared
// assertion library would be a place one module's floor could be weakened).
const IDENT = /^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,127}$/;
const NATIVE_ID = /^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,254}$/;
const CONTROL = /[\u0000-\u001F\u007F-\u009F​-‏‪-‮⁠-⁤⁦-⁩﻿]/u;
const MAX_COMMAND_BYTES = 8192;

function isPlainObject(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

/**
 * Refuse any argument whose NAME reaches for audio, a recording or a
 * transcript, at any depth. Names only, never values: the check has to work
 * without looking at what it refuses. J201's fragment list is the one list.
 */
export function assertNoRecordingFields(value, ToolError, path = "args") {
  if (Array.isArray(value)) {
    value.forEach((entry, i) => assertNoRecordingFields(entry, ToolError, `${path}[${i}]`));
    return;
  }
  if (!isPlainObject(value)) return;
  for (const key of Object.keys(value)) {
    const normalized = key.toLowerCase();
    const fragment = V5_J201_RECORDING_FRAGMENTS.find(f => normalized.includes(f));
    if (fragment) {
      throw new ToolError({ error: "recording_field_refused", path: `${path}.${key}`, fragment,
        recording: V5_J201_RECORDING_STATE, recording_policy_seam: V5_J201_RECORDING_POLICY_SEAM,
        hint: "Meeting Mode captures no audio and reads no field that claims to carry it" });
    }
    assertNoRecordingFields(value[key], ToolError, `${path}.${key}`);
  }
}

function requireUuid(value, field, ToolError) {
  if (!UUID.test(String(value ?? ""))) throw new ToolError({ error: `${field}_invalid`, hint: `${field} is a uuid` });
  return value;
}

function requireIdent(value, field, ToolError, pattern = IDENT) {
  if (typeof value !== "string" || !pattern.test(value))
    throw new ToolError({ error: `${field}_invalid`, hint: `${field} is a plain identifier` });
  return value;
}

function requireText(value, field, max, ToolError, { multiline = true } = {}) {
  if (typeof value !== "string" || !value.trim() || value.length > max
      || CONTROL.test(multiline ? value.replace(/[\n\t]/g, "") : value))
    throw new ToolError({ error: `${field}_invalid`, hint: `${field} is non-empty text of at most ${max} characters` });
  return value;
}

function optionalPositiveInt(value, field, ToolError) {
  if (value === undefined || value === null) return null;
  if (!Number.isSafeInteger(value) || value < 1) throw new ToolError({ error: `${field}_invalid` });
  return value;
}

/**
 * Validate one proposed canonical command. The verb must be an EXISTING MCP
 * write verb this door may point at: not a meeting verb (the meeting is not its
 * own effect), and not a human-only, authority-only or oracle-seat verb, whose
 * own gates a meeting acceptance must not stand in for. The idempotency key is
 * the store's to mint at acceptance, never the proposer's.
 */
export function validateMeetingCommand(command, lookupTool, ToolError) {
  if (command === undefined || command === null) return null;
  if (!isPlainObject(command) || Object.keys(command).some(k => k !== "verb" && k !== "args")
      || typeof command.verb !== "string" || !isPlainObject(command.args)) {
    throw new ToolError({ error: "meeting_command_invalid",
      hint: "a command is exactly { verb, args } with args an object" });
  }
  const tool = lookupTool(command.verb);
  if (!tool || tool.write !== true || MEETING_MODE_VERBS.includes(command.verb)
      || tool.humanOnly === true || tool.authorityOnly === true || tool.oracleSeatOnly === true) {
    throw new ToolError({ error: "meeting_command_not_eligible", verb: command.verb,
      hint: "a meeting action may point only at an existing ordinary CARR write verb" });
  }
  if (Object.hasOwn(command.args, "idempotency_key")) {
    throw new ToolError({ error: "meeting_command_idempotency_key_refused",
      hint: "the operation key is minted when a partner accepts the action" });
  }
  if (new TextEncoder().encode(JSON.stringify(command)).length > MAX_COMMAND_BYTES) {
    throw new ToolError({ error: "meeting_command_too_large", max_bytes: MAX_COMMAND_BYTES });
  }
  return { verb: command.verb, args: command.args };
}

/**
 * The deterministic end-of-meeting recap. Pure: the same actions always give
 * the same buckets.
 *   done           — executed, and reconciled against the canonical ledger
 *   delegated      — handed off through a canonical record, reconciled likewise
 *   needs_approval — a proposal carrying a canonical command, awaiting a partner
 *   unresolved     — tentative discussion with no command, or an accepted
 *                    action whose canonical effect is not yet observed
 * A declined action is closed without action and sits outside the four.
 */
export function meetingRecap(actions) {
  const recap = { done: [], delegated: [], needs_approval: [], unresolved: [], closed_without_action: [] };
  for (const action of Array.isArray(actions) ? actions : []) {
    const revisions = Array.isArray(action.revisions) ? action.revisions : [];
    const current = revisions.find(r => r.revision === action.current_revision) ?? revisions.at(-1) ?? {};
    const entry = { action_number: action.action_number, summary: current.summary ?? null,
      state: action.state, assignee: action.assignee ?? null };
    if (action.state === "executed") recap.done.push({ ...entry, reason_id: "canonical_effect_reconciled" });
    else if (action.state === "delegated") recap.delegated.push({ ...entry, reason_id: "canonical_handoff_reconciled" });
    else if (action.state === "declined") recap.closed_without_action.push({ ...entry, reason_id: "declined_by_partner" });
    else if (action.state === "accepted") recap.unresolved.push({ ...entry, reason_id: "accepted_effect_not_yet_observed" });
    else if (action.state === "proposed" && current.command)
      recap.needs_approval.push({ ...entry, reason_id: "proposed_command_awaits_partner" });
    else recap.unresolved.push({ ...entry, reason_id: "tentative_discussion_without_command" });
  }
  return recap;
}

/** The read verb's shaper. Pure; refuses anything that is not the store's shape. */
export function meetingProjection(facts, ToolError) {
  if (!isPlainObject(facts) || facts.ok !== true || !isPlainObject(facts.meeting)
      || !Array.isArray(facts.actions) || !Array.isArray(facts.notes) || !Array.isArray(facts.stream)) {
    throw new ToolError({ error: facts?.reason_id || "meeting_not_found" });
  }
  if (facts.meeting.recording !== V5_J201_RECORDING_STATE) {
    throw new ToolError({ error: "meeting_recording_state_invalid" });
  }
  const recap = meetingRecap(facts.actions);
  const gaps = meetingModeGaps();
  const lease = facts.lease ?? null;
  const ended = facts.meeting.mode_state === "ended";
  return {
    ok: true,
    schema_version: MEETING_MODE_SCHEMA_VERSION,
    meeting: facts.meeting,
    lease,
    notes: facts.notes,
    actions: facts.actions,
    stream: facts.stream,
    more: facts.more === true,
    recap: {
      ...recap,
      counts: Object.fromEntries([...MEETING_RECAP_BUCKETS, "closed_without_action"]
        .map(bucket => [bucket, recap[bucket].length])),
    },
    // Three different facts, never one "done": nothing was ever recorded,
    // whether the shared processing has finished, and whether the partners
    // have resolved every action.
    status: {
      recording: "never_started",
      processing_complete: ended && !(lease && lease.live === true),
      review_complete: recap.needs_approval.length === 0 && recap.unresolved.length === 0,
    },
    recording: V5_J201_RECORDING_STATE,
    records_audio: false,
    recording_policy_seam: V5_J201_RECORDING_POLICY_SEAM,
    d03_recording: {
      available: false,
      reason_id: "requires_actual_recording_retention_and_activation_evidence",
      legacy_recorder_presence_is_evidence: false,
    },
    detection_prompt: {
      available: gaps.prompt_reachable_here,
      reason_id: gaps.prompt_unreachable_reason_id,
      seam: V5_J201_PROMPT_LEDGER_OWNER_SEAM,
    },
  };
}

function refused(result, ToolError, fallback, context = {}) {
  const { ok: _ok, reason_id, ...rest } = result ?? {};
  return new ToolError({ error: reason_id || fallback, ...context, ...rest });
}

export function meetingModeTools({ withEnvelope, writeEvent, ToolError, lookupTool }) {
  const envelope = (verb, fn) => async (c, actor, args) => {
    assertNoRecordingFields(args, ToolError);
    requireUuid(args.idempotency_key, "idempotency_key", ToolError);
    return withEnvelope(c, actor, verb, args, () => fn(c, actor, args));
  };
  const event = (c, actor, verb, meetingId, field, value, args) =>
    writeEvent(c, actor, verb, "meeting", meetingId, { field, new: value, idempotency_key: args.idempotency_key });
  const one = async (c, sql, params, name) => (await c.query(sql, params)).rows[0]?.[name];
  const base = { schema_version: MEETING_MODE_SCHEMA_VERSION, recording: V5_J201_RECORDING_STATE };
  const instance = { type: "string", description: "The calling device or app session; attribution only, never authority." };

  return {
    "start-meeting": {
      write: true,
      writerConnection: true,
      description: "Start, or rejoin, the ONE shared non-recording meeting for a native source identity (a Teams/Zoom meeting, a calendar event, or an app-minted ad-hoc id). Requires the signed-in partner's explicit one-tap (activation_intent one_tap_user_activation); captures no audio and raises no detection prompt. A second device or a retry returns the same meeting, never a second one.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" },
        title: { type: "string" },
        platform: { type: "string", enum: [...V5_J201_PLATFORMS] },
        native_identity: { type: "object", additionalProperties: false, properties: {
          source_system: { type: "string" }, native_id: { type: "string" }, native_id_epoch: { type: "string" },
        }, required: ["source_system", "native_id", "native_id_epoch"] },
        activation_intent: { type: "string" },
        client_instance: instance,
      }, required: ["idempotency_key", "title", "native_identity", "activation_intent", "client_instance"] },
      handler: envelope("start-meeting", async (c, actor, args) => {
        requireText(args.title, "meeting_title", 200, ToolError, { multiline: false });
        requireIdent(args.client_instance, "client_instance", ToolError);
        if (V5_J201_REFUSED_ACTIVATION_INTENTS.includes(args.activation_intent)) {
          throw new ToolError({ error: "silent_activation_refused",
            attempted_activation_intent: args.activation_intent,
            accepted_activation_intent: V5_J201_EXPLICIT_ACTIVATION_INTENT });
        }
        if (args.activation_intent !== V5_J201_EXPLICIT_ACTIVATION_INTENT) {
          throw new ToolError({ error: "explicit_human_activation_required",
            accepted_activation_intent: V5_J201_EXPLICIT_ACTIVATION_INTENT });
        }
        const identity = args.native_identity;
        if (!isPlainObject(identity)) throw new ToolError({ error: "native_identity_invalid" });
        if (args.platform !== undefined && args.platform !== null) {
          // The J201 kernel owns what a conferencing identity is; ask it.
          try { meetingKey({ platform: args.platform, native_identity: identity }); } catch (e) {
            if (e instanceof V5J201Error) throw new ToolError({ error: "native_identity_invalid", reason: e.code });
            throw e;
          }
        } else {
          requireIdent(identity.source_system, "source_system", ToolError);
          requireIdent(identity.native_id, "native_id", ToolError, NATIVE_ID);
          requireIdent(identity.native_id_epoch, "native_id_epoch", ToolError);
        }
        const result = await one(c,
          "select ops.start_meeting($1::text,$2::text,$3::text,$4::text,$5::text,$6::text,$7::text,$8::uuid) as r",
          [args.platform ?? null, identity.source_system, identity.native_id, identity.native_id_epoch,
            args.title, args.activation_intent, args.client_instance, args.idempotency_key], "r");
        if (!result || result.ok !== true) throw refused(result, ToolError, "meeting_start_refused");
        if (result.deduplicated !== true && result.mode_state !== "ended") {
          await event(c, actor, "start-meeting", result.meeting_id,
            result.joined_existing ? "joined" : "started", { client_instance: args.client_instance }, args);
        }
        return { ok: true, ...base, deduplicated: result.deduplicated === true,
          joined_existing: result.joined_existing === true, meeting_id: result.meeting_id,
          title: result.title, mode_state: result.mode_state, started_by: result.started_by,
          last_seq: result.last_seq, records_audio: false };
      }),
    },

    "claim-meeting-processing": {
      write: true,
      writerConnection: true,
      description: "Claim, renew or release the single processing lease for a shared meeting. One device holds it; a second device is told who holds it and rejoins as a participant instead of starting a duplicate worker. An expired or released lease can be taken over, which advances the lease epoch that fences processing contributions.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" },
        meeting_id: { type: "string" },
        client_instance: instance,
        release: { type: "boolean" },
      }, required: ["idempotency_key", "meeting_id", "client_instance"] },
      handler: envelope("claim-meeting-processing", async (c, actor, args) => {
        requireUuid(args.meeting_id, "meeting_id", ToolError);
        requireIdent(args.client_instance, "client_instance", ToolError);
        const result = await one(c,
          "select ops.claim_meeting_processing($1::uuid,$2::text,$3::boolean,$4::uuid) as r",
          [args.meeting_id, args.client_instance, args.release === true, args.idempotency_key], "r");
        if (!result || result.ok !== true)
          throw refused(result, ToolError, "meeting_processing_claim_refused", { meeting_id: args.meeting_id });
        if (["acquired", "taken_over_after_expiry", "released"].includes(result.decision)) {
          await event(c, actor, "claim-meeting-processing", args.meeting_id, "processing_lease",
            { decision: result.decision, lease_epoch: result.lease?.lease_epoch ?? null }, args);
        }
        return { ok: true, ...base, meeting_id: args.meeting_id, decision: result.decision,
          is_holder: result.is_holder === true, lease: result.lease ?? null };
      }),
    },

    "add-meeting-note": {
      write: true,
      writerConnection: true,
      description: "Append an attributed note to a shared meeting, or a new revision of an existing note under a compare-and-swap on its latest revision. Notes are append-only; a revision never overwrites history.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" },
        meeting_id: { type: "string" },
        body: { type: "string" },
        revises_note_number: { type: "integer", minimum: 1 },
        base_revision: { type: "integer", minimum: 1 },
        client_instance: instance,
      }, required: ["idempotency_key", "meeting_id", "body", "client_instance"] },
      handler: envelope("add-meeting-note", async (c, actor, args) => {
        requireUuid(args.meeting_id, "meeting_id", ToolError);
        requireText(args.body, "meeting_note_body", 20000, ToolError);
        requireIdent(args.client_instance, "client_instance", ToolError);
        const revises = optionalPositiveInt(args.revises_note_number, "revises_note_number", ToolError);
        const baseRevision = optionalPositiveInt(args.base_revision, "base_revision", ToolError);
        const result = await one(c,
          "select ops.add_meeting_note($1::uuid,$2::text,$3::integer,$4::integer,$5::text,$6::uuid) as r",
          [args.meeting_id, args.body, revises, baseRevision, args.client_instance, args.idempotency_key], "r");
        if (!result || result.ok !== true)
          throw refused(result, ToolError, "meeting_note_refused", { meeting_id: args.meeting_id });
        if (result.deduplicated !== true) {
          await event(c, actor, "add-meeting-note", args.meeting_id, "note",
            { note_number: result.note_number, revision: result.revision }, args);
        }
        return { ok: true, ...base, deduplicated: result.deduplicated === true, meeting_id: args.meeting_id,
          note_number: result.note_number, revision: result.revision, seq: result.seq ?? null };
      }),
    },

    "propose-meeting-action": {
      write: true,
      writerConnection: true,
      description: "Add a numbered action to a shared meeting's action stream, or revise a pending one. Tentative discussion stays a proposal. A signed-in partner's explicit_instruction naming a canonical command (an existing CARR write verb and its args) is accepted at once and returns the exact dispatch; nothing else is. A dedupe_key makes repeated contributions about the same action a no-op or a revision, never a duplicate. Processing contributions must present the current processing lease epoch.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" },
        meeting_id: { type: "string" },
        summary: { type: "string" },
        command: { type: "object", additionalProperties: false, properties: {
          verb: { type: "string" }, args: { type: "object" },
        }, required: ["verb", "args"] },
        basis: { type: "string", enum: ["tentative_discussion", "explicit_instruction"] },
        dedupe_key: { type: "string" },
        revises_action_number: { type: "integer", minimum: 1 },
        base_revision: { type: "integer", minimum: 1 },
        processing_epoch: { type: "integer", minimum: 1 },
        client_instance: instance,
      }, required: ["idempotency_key", "meeting_id", "summary", "basis", "client_instance"] },
      handler: envelope("propose-meeting-action", async (c, actor, args) => {
        requireUuid(args.meeting_id, "meeting_id", ToolError);
        requireText(args.summary, "meeting_action_summary", 2000, ToolError);
        requireIdent(args.client_instance, "client_instance", ToolError);
        if (!["tentative_discussion", "explicit_instruction"].includes(args.basis))
          throw new ToolError({ error: "meeting_action_basis_invalid" });
        const command = validateMeetingCommand(args.command, lookupTool, ToolError);
        if (args.dedupe_key !== undefined && args.dedupe_key !== null)
          requireIdent(args.dedupe_key, "dedupe_key", ToolError);
        const revises = optionalPositiveInt(args.revises_action_number, "revises_action_number", ToolError);
        const baseRevision = optionalPositiveInt(args.base_revision, "base_revision", ToolError);
        const epoch = optionalPositiveInt(args.processing_epoch, "processing_epoch", ToolError);
        const result = await one(c,
          "select ops.propose_meeting_action($1::uuid,$2::text,$3::jsonb,$4::text,$5::text,$6::integer,$7::integer,$8::bigint,$9::text,$10::uuid) as r",
          [args.meeting_id, args.summary, command === null ? null : JSON.stringify(command), args.basis,
            args.dedupe_key ?? null, revises, baseRevision, epoch, args.client_instance, args.idempotency_key], "r");
        if (!result || result.ok !== true)
          throw refused(result, ToolError, "meeting_action_refused", { meeting_id: args.meeting_id });
        if (result.deduplicated !== true) {
          await event(c, actor, "propose-meeting-action", args.meeting_id, "action",
            { action_number: result.action?.action_number, revision: result.revision,
              state: result.action?.state }, args);
        }
        return { ok: true, ...base, deduplicated: result.deduplicated === true,
          already_resolved: result.already_resolved === true,
          accepted_as_explicit_instruction: result.accepted_as_explicit_instruction === true,
          meeting_id: args.meeting_id, revision: result.revision ?? null, action: result.action,
          seq: result.seq ?? null };
      }),
    },

    "decide-meeting-action": {
      write: true,
      writerConnection: true,
      description: "A signed-in partner accepts or declines a proposed meeting action at the revision they read. Acceptance requires a canonical command and mints the one idempotency key its dispatch must use; simultaneous or repeated acceptances resolve to that same decision. Accepting records a decision only: the effect happens when the returned dispatch is made through the existing verb and reconciled.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" },
        meeting_id: { type: "string" },
        action_number: { type: "integer", minimum: 1 },
        decision: { type: "string", enum: ["accept", "decline"] },
        base_revision: { type: "integer", minimum: 1 },
        disposition: { type: "string", enum: ["execute", "delegate"] },
        assignee_slug: { type: "string" },
        client_instance: instance,
      }, required: ["idempotency_key", "meeting_id", "action_number", "decision", "base_revision", "client_instance"] },
      handler: envelope("decide-meeting-action", async (c, actor, args) => {
        requireUuid(args.meeting_id, "meeting_id", ToolError);
        requireIdent(args.client_instance, "client_instance", ToolError);
        const actionNumber = optionalPositiveInt(args.action_number, "action_number", ToolError);
        const baseRevision = optionalPositiveInt(args.base_revision, "base_revision", ToolError);
        if (!["accept", "decline"].includes(args.decision)) throw new ToolError({ error: "meeting_decision_invalid" });
        if (args.disposition !== undefined && !["execute", "delegate"].includes(args.disposition))
          throw new ToolError({ error: "meeting_action_disposition_invalid" });
        if (args.assignee_slug !== undefined) requireIdent(args.assignee_slug, "assignee_slug", ToolError);
        const result = await one(c,
          "select ops.decide_meeting_action($1::uuid,$2::integer,$3::text,$4::integer,$5::text,$6::text,$7::text,$8::uuid) as r",
          [args.meeting_id, actionNumber, args.decision, baseRevision, args.disposition ?? null,
            args.assignee_slug ?? null, args.client_instance, args.idempotency_key], "r");
        if (!result || result.ok !== true)
          throw refused(result, ToolError, "meeting_decision_refused", { meeting_id: args.meeting_id });
        if (result.deduplicated !== true && result.already !== true) {
          await event(c, actor, "decide-meeting-action", args.meeting_id, "action_decision",
            { action_number: actionNumber, decision: args.decision, state: result.action?.state }, args);
        }
        return { ok: true, ...base, deduplicated: result.deduplicated === true,
          already: result.already === true, resolved_once: result.resolved_once === true,
          meeting_id: args.meeting_id, action: result.action,
          dispatch: result.action?.dispatch ?? null, effect_executed: false };
      }),
    },

    "record-meeting-action-outcome": {
      write: true,
      writerConnection: true,
      description: "Reconcile an accepted meeting action against the canonical record: it becomes done (or delegated) only when the existing verb's own committed envelope row is found under the action's operation key. When it is not found the answer is not_observed plus the exact retry, which must reuse the same key. Call this after any disconnect before retrying.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" },
        meeting_id: { type: "string" },
        action_number: { type: "integer", minimum: 1 },
        client_instance: instance,
      }, required: ["idempotency_key", "meeting_id", "action_number", "client_instance"] },
      handler: envelope("record-meeting-action-outcome", async (c, actor, args) => {
        requireUuid(args.meeting_id, "meeting_id", ToolError);
        requireIdent(args.client_instance, "client_instance", ToolError);
        const actionNumber = optionalPositiveInt(args.action_number, "action_number", ToolError);
        const result = await one(c,
          "select ops.record_meeting_action_outcome($1::uuid,$2::integer,$3::text,$4::uuid) as r",
          [args.meeting_id, actionNumber, args.client_instance, args.idempotency_key], "r");
        if (!result || result.ok !== true)
          throw refused(result, ToolError, "meeting_outcome_refused", { meeting_id: args.meeting_id });
        if (result.reconciled === true && result.already !== true) {
          await event(c, actor, "record-meeting-action-outcome", args.meeting_id, "action_outcome",
            { action_number: actionNumber, state: result.outcome_state }, args);
        }
        return { ok: true, ...base, already: result.already === true, reconciled: result.reconciled === true,
          outcome_state: result.outcome_state ?? result.action?.state ?? null,
          meeting_id: args.meeting_id, action: result.action, retry: result.retry ?? null };
      }),
    },

    "end-meeting": {
      write: true,
      writerConnection: true,
      description: "A signed-in partner ends a shared meeting. The processing lease ends with it and no further notes or proposals are accepted; pending actions stay decidable and reconcilable, and read-meeting reports the done/delegated/needs-approval/unresolved recap.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" },
        meeting_id: { type: "string" },
        client_instance: instance,
      }, required: ["idempotency_key", "meeting_id", "client_instance"] },
      handler: envelope("end-meeting", async (c, actor, args) => {
        requireUuid(args.meeting_id, "meeting_id", ToolError);
        requireIdent(args.client_instance, "client_instance", ToolError);
        const result = await one(c, "select ops.end_meeting($1::uuid,$2::text,$3::uuid) as r",
          [args.meeting_id, args.client_instance, args.idempotency_key], "r");
        if (!result || result.ok !== true)
          throw refused(result, ToolError, "meeting_end_refused", { meeting_id: args.meeting_id });
        if (result.already !== true) {
          await event(c, actor, "end-meeting", args.meeting_id, "mode_state", { mode_state: "ended" }, args);
        }
        return { ok: true, ...base, already: result.already === true, meeting_id: args.meeting_id,
          ended_at: result.ended_at };
      }),
    },

    "read-meeting": {
      // A READ on the writer connection: mcp.js opens `begin read only` for this
      // flag combination, and it is the only path that installs the actor and
      // tenant context ops.meeting_facts derives visibility from.
      writerConnection: true,
      description: "Read one shared meeting: identity, processing lease, every note revision, every action with its revision history and dispatch, the numbered stream after a sequence (for reconnect), and the deterministic done/delegated/needs-approval/unresolved recap. Reports recording as denied and the detection prompt as unavailable.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        meeting_id: { type: "string" },
        after_seq: { type: "integer", minimum: 0 },
        limit: { type: "integer", minimum: 1, maximum: 500 },
      }, required: ["meeting_id"] },
      handler: async (c, _actor, args) => {
        assertNoRecordingFields(args, ToolError);
        if (!UUID.test(String(args.meeting_id ?? ""))) throw new ToolError({ error: "meeting_not_found" });
        const facts = await one(c, "select ops.meeting_facts($1::uuid,$2::bigint,$3::integer) as f",
          [args.meeting_id, args.after_seq ?? 0, args.limit ?? null], "f");
        return meetingProjection(facts, ToolError);
      },
    },
  };
}
