// V5-J103 part two — the five Journey 1 correspondence judgments the
// 2026-09-20 amendment added (c4..c8), proved case by case.
//
// Pure and synthetic: nothing here reaches a database, provider, network, clock
// or filesystem. Every fixture names test data only.
//
// What this suite holds, beyond each clause's own behaviour:
//   * EVERY result carries the ceiling — no automatic internal update, the
//     amendment's gate named as absent with a deny absence behaviour, no
//     authority, not dispatchable, no provider operation — and the suite checks
//     it on every result it produced, not on a sample;
//   * no result anywhere carries a confirmed document state, an asserted
//     attendance, or a routable destination.

import test from "node:test";
import assert from "node:assert/strict";

import { digest } from "../src/artifact-trust.js";
import * as J from "../src/governed-correspondence-journey.v5.js";
import {
  V5_J102_EVIDENCE_INTEGRITY,
  V5_J102_EVIDENCE_LOADER,
} from "../src/cre-lifecycle.v5.js";

const {
  classifySignedLeaseSignal, resolveCorrespondenceParticipant, classifyCalendarTouch,
  classifyCorrespondenceCommitments, evaluateCorrespondenceMerge, evaluateConsumerCircuit,
  commitmentDedupeKey,
} = J;

const PRODUCED = [];
function keep(r) { PRODUCED.push(r); return r; }

const d = (s) => digest(`fixture:${s}`);
const msg = (n = 1) => ({ source_system: "fixture-mail", native_id: `msg-${n}`, native_id_epoch: 0 });
const evt = (n = 1) => ({ source_system: "fixture-calendar", native_id: `evt-${n}`, native_id_epoch: 0 });

function expectCode(fn, code) {
  assert.throws(fn, (e) => e instanceof J.V5J103JourneyError && e.code === code, `expected ${code}`);
}

// --------------------------------------------------------------------------- c4

const clearSignal = (over = {}) => ({
  record_ref: "deal-fixture-1", message: msg(), signal_basis: "counterparty_statement",
  clarity: "clear", clarity_proposed_by: "model_seam", observed_at: "2026-09-20T15:00:00Z", ...over,
});
const validEvidence = (docOver = {}, provOver = {}) => ({
  evidence_kind: "executed_lease",
  provenance: { loaded_by: V5_J102_EVIDENCE_LOADER, reader: "fixture-reader", loaded_at: "2026-09-20T15:01:00Z", integrity: V5_J102_EVIDENCE_INTEGRITY, ...provOver },
  document: { document_id: "doc-fixture-1", content_digest: d("lease"), signature_state: "fully_executed", validity_state: "effective", version_state: "current", ...docOver },
});

test("c4: a clear signed-lease email proposes execution_reported and document_pending", () => {
  const r = keep(classifySignedLeaseSignal({ signal: clearSignal() }));
  assert.equal(r.decision, "propose_execution_reported_document_pending");
  assert.equal(r.proposal.execution_state, "execution_reported");
  assert.equal(r.proposal.document_state, "document_pending");
  assert.equal(r.document_route, "no_artifact_presented");
  assert.equal(r.document_confirmed, false);
});

test("c4: a valid-looking artifact routes to J102 and still never confirms here", () => {
  const r = keep(classifySignedLeaseSignal({ signal: clearSignal(), document_evidence: validEvidence() }));
  assert.equal(r.document_route, "route_to_lifecycle_confirmation");
  assert.equal(r.document_confirmation_owner, J.V5_J103J_DOCUMENT_CONFIRMATION_OWNER);
  assert.equal(r.document_state, "document_pending");
  assert.equal(r.proposal.document_state, "document_pending");
  assert.equal(r.document_confirmed, false);
});

for (const [label, ev, defect] of [
  ["partially signed", validEvidence({ signature_state: "partially_signed" }), "not_fully_executed"],
  ["draft validity", validEvidence({ validity_state: "draft" }), "not_effective"],
  ["superseded version", validEvidence({ version_state: "superseded" }), "not_current_version"],
  ["caller-loaded", validEvidence({}, { loaded_by: "caller" }), "not_loaded_by_record_layer"],
  ["integrity trusted", validEvidence({}, { integrity: "trusted_from_caller" }), "integrity_not_recomputed"],
  ["wrong kind", { ...validEvidence(), evidence_kind: "signed_purchase_contract" }, "not_an_executed_lease_artifact"],
]) {
  test(`c4: an invalid artifact (${label}) keeps the document pending and names the defect`, () => {
    const r = keep(classifySignedLeaseSignal({ signal: clearSignal(), document_evidence: ev }));
    assert.equal(r.document_route, "artifact_not_valid_for_confirmation");
    assert.equal(r.artifact_defect, defect);
    assert.equal(r.document_confirmation_owner, null);
    assert.equal(r.document_state, "document_pending");
    assert.equal(r.document_confirmed, false);
  });
}

test("c4: an unclear signal proposes nothing", () => {
  const r = keep(classifySignedLeaseSignal({ signal: clearSignal({ clarity: "unclear" }) }));
  assert.equal(r.decision, "withhold_unclear_signal");
  assert.equal(r.proposal, null);
});

test("c4: provenance of the message is carried through verbatim", () => {
  const r = keep(classifySignedLeaseSignal({ signal: clearSignal({ message: msg(42) }) }));
  assert.deepEqual({ ...r.message }, msg(42));
});

test("c4: a confirmed state cannot be requested by the caller", () => {
  expectCode(() => classifySignedLeaseSignal({ signal: { ...clearSignal(), document_state: "document_confirmed" } }), "unknown_field");
});

// --------------------------------------------------------------------------- c5

const participant = { participant_ref: "participant-fixture-1", address_digest: d("addr"), thread_ref: "thread-fixture-1" };
const rung = (name, status, parties = []) => ({
  rung: name, status,
  matches: parties.map((p, i) => ({ party_ref: p, evidence_digest: d(`${name}-${p}-${i}`), observed_at: "2026-09-19T10:00:00Z" })),
  searched_evidence_digest: status === "not_checked" ? null : d(`search-${name}`),
});
const ladder = (a, b, c) => [
  rung("attendee_address_match", ...a), rung("calendar_invite_participant", ...b), rung("recent_correspondence_counterpart", ...c),
];

test("c5: an unchecked rung blocks escalation AND new-party creation", () => {
  const r = keep(resolveCorrespondenceParticipant({ participant, rungs: ladder(["matched", ["party-a", "party-b"]], ["not_checked"], ["checked_no_match"]) }));
  assert.equal(r.decision, "resolution_incomplete");
  assert.deepEqual([...r.unchecked_rungs], ["calendar_invite_participant"]);
  assert.equal(r.escalation, null);
  assert.equal(r.proposal, null);
});

test("c5: a later rung disambiguates before any human is asked", () => {
  const r = keep(resolveCorrespondenceParticipant({ participant, rungs: ladder(["matched", ["party-a", "party-b"]], ["checked_no_match"], ["matched", ["party-a"]]) }));
  assert.equal(r.decision, "propose_link_existing_party");
  assert.equal(r.proposal.party_ref, "party-a");
  assert.equal(r.escalation, null);
});

test("c5: ambiguity surviving the whole ladder escalates, privately", () => {
  const r = keep(resolveCorrespondenceParticipant({ participant, rungs: ladder(["matched", ["party-a", "party-b"]], ["matched", ["party-a", "party-b"]], ["checked_no_match"]) }));
  assert.equal(r.decision, "escalate_ambiguity_to_human");
  assert.deepEqual([...r.escalation.candidates], ["party-a", "party-b"]);
  assert.equal(r.escalation.visible_to_partner, false);
});

test("c5: rungs that contradict escalate as a contradiction, not a pick", () => {
  const r = keep(resolveCorrespondenceParticipant({ participant, rungs: ladder(["matched", ["party-a"]], ["matched", ["party-b"]], ["checked_no_match"]) }));
  assert.equal(r.decision, "escalate_contradiction_to_human");
  assert.equal(r.proposal, null);
});

test("c5: an empty ladder proposes a new party with sourced uncertainty per attribute", () => {
  const r = keep(resolveCorrespondenceParticipant({ participant, rungs: ladder(["checked_no_match"], ["checked_no_match"], ["checked_no_match"]) }));
  assert.equal(r.decision, "propose_new_party_with_research");
  assert.equal(r.proposal.action, "create_party");
  assert.equal(r.proposal.research.length, J.V5_J103J_RESEARCH_ATTRIBUTES.length);
  for (const item of r.proposal.research) {
    assert.equal(item.status, "unknown");
    assert.equal(item.sourced_uncertainty.length, 3);
    for (const s of item.sourced_uncertainty) assert.match(s.searched_evidence_digest, /^sha256:/);
  }
  // Proposed, not created.
  assert.equal(r.automatic_internal_update, false);
});

test("c5: rung order and completeness are enforced", () => {
  expectCode(() => resolveCorrespondenceParticipant({ participant, rungs: ladder(["checked_no_match"], ["checked_no_match"], ["checked_no_match"]).slice(0, 2) }), "invalid_shape");
  const dup = ladder(["checked_no_match"], ["checked_no_match"], ["checked_no_match"]);
  dup[2] = { ...dup[0] };
  expectCode(() => resolveCorrespondenceParticipant({ participant, rungs: dup }), "duplicate_rung");
  const lie = ladder(["matched"], ["checked_no_match"], ["checked_no_match"]);
  expectCode(() => resolveCorrespondenceParticipant({ participant, rungs: lie }), "matched_rung_without_match");
});

test("c5: a raw address in place of a digest is refused before anything is read", () => {
  expectCode(() => resolveCorrespondenceParticipant({ participant: { ...participant, address_digest: "someone@example.invalid.test" }, rungs: [] }), "routable_address_refused");
});

// --------------------------------------------------------------------------- c6

const event = (over = {}) => ({
  record_ref: "deal-fixture-1", event: evt(), revision: 3, status: "confirmed",
  starts_at: "2026-09-26T14:00:00Z", ends_at: "2026-09-26T15:00:00Z", ...over,
});
const NOW = "2026-09-25T12:00:00Z";

test("c6: a future event is a scheduled meeting and not a touch", () => {
  const r = keep(classifyCalendarTouch({ event: event(), prior: null, now: NOW }));
  assert.equal(r.temporality, "scheduled");
  assert.equal(r.decision, "propose_scheduled_meeting");
  assert.equal(r.proposal.counts_as_touch, false);
});

test("c6: a past event is a calendar-derived touch with attendance not asserted", () => {
  const r = keep(classifyCalendarTouch({ event: event({ starts_at: "2026-09-24T14:00:00Z", ends_at: "2026-09-24T15:00:00Z" }), prior: null, now: NOW }));
  assert.equal(r.temporality, "past");
  assert.equal(r.decision, "propose_past_calendar_touch");
  assert.equal(r.proposal.counts_as_touch, true);
  assert.equal(r.proposal.attendance, "not_asserted");
});

test("c6 boundaries: an event ending exactly now is past (end <= now); starting exactly now is not scheduled", () => {
  const endsNow = keep(classifyCalendarTouch({ event: event({ starts_at: "2026-09-25T11:00:00Z", ends_at: NOW }), prior: null, now: NOW }));
  assert.equal(endsNow.temporality, "past");
  assert.equal(endsNow.decision, "propose_past_calendar_touch");
  assert.equal(endsNow.proposal.counts_as_touch, true);
  const oneMsLater = keep(classifyCalendarTouch({ event: event({ starts_at: "2026-09-25T11:00:00Z", ends_at: "2026-09-25T12:00:00.001Z" }), prior: null, now: NOW }));
  assert.equal(oneMsLater.temporality, "in_progress");
  assert.equal(oneMsLater.proposal, null);
  const startsNow = keep(classifyCalendarTouch({ event: event({ starts_at: NOW, ends_at: "2026-09-25T13:00:00Z" }), prior: null, now: NOW }));
  assert.equal(startsNow.temporality, "in_progress");
  const zeroLengthNow = keep(classifyCalendarTouch({ event: event({ starts_at: NOW, ends_at: NOW }), prior: null, now: NOW }));
  assert.equal(zeroLengthNow.temporality, "past");
});

test("c6: an in-progress event is withheld, neither scheduled nor past", () => {
  const r = keep(classifyCalendarTouch({ event: event({ starts_at: "2026-09-25T11:30:00Z", ends_at: "2026-09-25T12:30:00Z" }), prior: null, now: NOW }));
  assert.equal(r.decision, "withhold_in_progress");
});

const prior = (over = {}) => ({ revision: 3, status: "confirmed", starts_at: "2026-09-26T14:00:00Z", ends_at: "2026-09-26T15:00:00Z", proposal_digest: d("prior-proposal"), ...over });

test("c6: cancellation of a scheduled meeting withdraws it and asserts nothing", () => {
  const r = keep(classifyCalendarTouch({ event: event({ revision: 4, status: "cancelled" }), prior: prior(), now: NOW }));
  assert.equal(r.decision, "reconcile_cancellation");
  assert.equal(r.reconciliation.action, "withdraw_scheduled_meeting");
  assert.equal(r.reconciliation.attendance, "not_asserted");
});

test("c6: cancellation after the fact marks the touch unverified, not unattended", () => {
  const past = { starts_at: "2026-09-24T14:00:00Z", ends_at: "2026-09-24T15:00:00Z" };
  const r = keep(classifyCalendarTouch({ event: event({ revision: 4, status: "cancelled", ...past }), prior: prior(past), now: NOW }));
  assert.equal(r.reconciliation.action, "mark_calendar_touch_cancelled_unverified");
  assert.equal(r.attendance_asserted, false);
});

test("c6: a source correction supersedes the prior proposal", () => {
  const r = keep(classifyCalendarTouch({ event: event({ revision: 5, starts_at: "2026-09-27T14:00:00Z", ends_at: "2026-09-27T15:00:00Z" }), prior: prior(), now: NOW }));
  assert.equal(r.decision, "reconcile_correction");
  assert.equal(r.reconciliation.supersedes_proposal_digest, d("prior-proposal"));
});

test("c6: ordering is by source revision, never by arrival — a stale revision is refused", () => {
  const r = keep(classifyCalendarTouch({ event: event({ revision: 2, starts_at: "2026-09-28T14:00:00Z", ends_at: "2026-09-28T15:00:00Z" }), prior: prior(), now: NOW }));
  assert.equal(r.decision, "refuse_stale_revision");
});

test("c6: the same revision replayed is a no-op; the same revision with new content is a source split", () => {
  const r = keep(classifyCalendarTouch({ event: event(), prior: prior(), now: NOW }));
  assert.equal(r.decision, "no_change_duplicate_revision");
  expectCode(() => classifyCalendarTouch({ event: event({ starts_at: "2026-09-29T14:00:00Z", ends_at: "2026-09-29T15:00:00Z" }), prior: prior(), now: NOW }), "revision_content_split");
});

test("c6: an attendance claim cannot be passed in", () => {
  expectCode(() => classifyCalendarTouch({ event: { ...event(), attended: true }, prior: null, now: NOW }), "unknown_field");
});

// --------------------------------------------------------------------------- c7

const item = (over = {}) => ({
  kind: "requested_task", kind_proposed_by: "model_seam", record_ref: "deal-fixture-1",
  owed_by_ref: "partner-joe", owed_to_ref: "party-fixture-2", action_key: "send-floor-plan",
  due_on: "2026-09-30", source_message: msg(), evidence_digest: d("sentence"), completion: null, ...over,
});

test("c7: requests, promises and suggestions stay separate", () => {
  const r = keep(classifyCorrespondenceCommitments({
    items: [item(), item({ kind: "promised_commitment", owed_by_ref: "party-fixture-2", owed_to_ref: "partner-joe" }), item({ kind: "suggestion", action_key: "consider-second-floor" })],
    existing: [],
  }));
  assert.deepEqual(r.outcomes.map(o => o.decision), ["propose_task", "propose_commitment", "note_suggestion"]);
  assert.equal(r.outcomes[2].proposal.creates_task, false);
  assert.equal(r.outcomes[2].proposal.creates_commitment, false);
});

test("c7: a request and a promise with the same words have different dedupe keys", () => {
  assert.notEqual(commitmentDedupeKey(item()), commitmentDedupeKey(item({ kind: "promised_commitment" })));
});

test("c7: dedupe is deterministic within a batch and against existing records", () => {
  const key = commitmentDedupeKey(item());
  const r = keep(classifyCorrespondenceCommitments({
    items: [item(), item({ source_message: msg(2), evidence_digest: d("other-sentence") })],
    existing: [],
  }));
  assert.equal(r.outcomes[1].decision, "duplicate_within_batch");
  assert.equal(r.outcomes[1].first_index, 0);
  const r2 = keep(classifyCorrespondenceCommitments({ items: [item()], existing: [{ dedupe_key: key, state: "open" }] }));
  assert.equal(r2.outcomes[0].decision, "duplicate_of_existing");
});

test("c7: completion needs cited evidence; a bare claim is refused", () => {
  const bare = keep(classifyCorrespondenceCommitments({ items: [item({ completion: { claimed_by: "model_seam", evidence: [] } })], existing: [] }));
  assert.equal(bare.outcomes[0].decision, "completion_refused_no_evidence");
  const backed = keep(classifyCorrespondenceCommitments({
    items: [item({ completion: { claimed_by: "model_seam", evidence: [{ evidence_kind: "correspondence_message_ref", evidence_ref: "msg-ref-3", evidence_digest: d("reply") }] } })],
    existing: [{ dedupe_key: commitmentDedupeKey(item()), state: "open" }],
  }));
  assert.equal(backed.outcomes[0].decision, "propose_evidence_backed_completion");
  assert.equal(backed.outcomes[0].proposal.matches_existing, true);
});

test("c7: a suggestion cannot complete", () => {
  expectCode(() => classifyCorrespondenceCommitments({ items: [item({ kind: "suggestion", completion: { claimed_by: "partner", evidence: [] } })], existing: [] }), "suggestion_cannot_complete");
});

// --------------------------------------------------------------------------- c8

const merge = (over = {}) => ({
  target_ref: "party-fixture-2", target_state: "established", alters_history: false,
  source_ref: "correspondence-fixture-1", evidence_digest: d("merge-evidence"), fields: ["title"], ...over,
});

test("c8: a merge into an established record needs human approval", () => {
  const r = keep(evaluateCorrespondenceMerge({ merge: merge(), corrections: [] }));
  assert.equal(r.decision, "human_approval_required");
  assert.equal(r.requires_human_approval, true);
});

test("c8: a merge that alters history needs human approval even on a provisional record", () => {
  const r = keep(evaluateCorrespondenceMerge({ merge: merge({ target_state: "provisional", alters_history: true }), corrections: [] }));
  assert.equal(r.decision, "human_approval_required");
  assert.equal(r.reason_id, "j103.c8.history_merge_needs_human");
});

test("c8: a provisional merge is still only a proposal under the amendment", () => {
  const r = keep(evaluateCorrespondenceMerge({ merge: merge({ target_state: "provisional" }), corrections: [] }));
  assert.equal(r.decision, "propose_provisional_merge");
  assert.equal(r.automatic_internal_update, false);
});

test("c8: replaying the evidence a correction superseded is refused, before approval is even considered", () => {
  const corrections = [{ correction_id: "correction-1", target_ref: "party-fixture-2", field: "title", superseded_evidence_digest: d("merge-evidence") }];
  const r = keep(evaluateCorrespondenceMerge({ merge: merge(), corrections }));
  assert.equal(r.decision, "refuse_replay_of_corrected_evidence");
  assert.deepEqual([...r.corrections_held], ["correction-1"]);
  // A different field or a different target is not the corrected fact.
  assert.equal(keep(evaluateCorrespondenceMerge({ merge: merge({ fields: ["phone_digest"] }), corrections })).decision, "human_approval_required");
  assert.equal(keep(evaluateCorrespondenceMerge({ merge: merge({ target_ref: "party-fixture-3" }), corrections })).decision, "human_approval_required");
});

test("c8: an open breaker suspends only its own consumer and scope, and capture is preserved", () => {
  const breakers = [
    { consumer_id: "deal-board", scope: "party-updates", state: "open", opened_reason_id: "bad-batch-1" },
    { consumer_id: "lead-board", scope: "party-updates", state: "closed", opened_reason_id: null },
  ];
  const own = keep(evaluateConsumerCircuit({ consumer_id: "deal-board", scope: "party-updates", capture_digest: d("cap"), breakers }));
  assert.equal(own.decision, "consumption_suspended");
  assert.equal(own.capture, "recorded");
  assert.equal(own.capture_preserved, true);
  const other = keep(evaluateConsumerCircuit({ consumer_id: "lead-board", scope: "party-updates", capture_digest: d("cap"), breakers }));
  assert.equal(other.decision, "consumption_permitted_as_proposal");
  const otherScope = keep(evaluateConsumerCircuit({ consumer_id: "deal-board", scope: "deal-updates", capture_digest: d("cap"), breakers }));
  assert.equal(otherScope.decision, "consumption_permitted_as_proposal");
  assert.equal(otherScope.capture_preserved, true);
});

// --------------------------------------------------------------------------- review round 1 (#1266)

const PAST = { starts_at: "2026-09-24T14:00:00Z", ends_at: "2026-09-24T15:00:00Z" };

test("c6 blocker: a cancelled event whose newer revision only moves its times never becomes a touch", () => {
  const moved = { starts_at: "2026-09-23T14:00:00Z", ends_at: "2026-09-23T15:00:00Z" };
  const r = keep(classifyCalendarTouch({ event: event({ revision: 5, status: "cancelled", ...moved }), prior: prior({ status: "cancelled", ...PAST }), now: NOW }));
  assert.equal(r.decision, "still_cancelled_no_touch");
  assert.equal(r.proposal, null);
  const same = keep(classifyCalendarTouch({ event: event({ revision: 5, status: "cancelled", ...PAST }), prior: prior({ status: "cancelled", ...PAST }), now: NOW }));
  assert.equal(same.proposal, null);
});

test("c6: a cancelled event in the future whose times move is still not a meeting", () => {
  const r = keep(classifyCalendarTouch({ event: event({ revision: 5, status: "cancelled", starts_at: "2026-09-29T14:00:00Z", ends_at: "2026-09-29T15:00:00Z" }), prior: prior({ status: "cancelled" }), now: NOW }));
  assert.equal(r.proposal, null);
});

test("c6: un-cancelling a past event proposes no touch and asks a human", () => {
  const r = keep(classifyCalendarTouch({ event: event({ revision: 5, status: "confirmed", ...PAST }), prior: prior({ status: "cancelled", ...PAST }), now: NOW }));
  assert.equal(r.decision, "reconcile_reinstatement");
  assert.equal(r.proposal, null);
  assert.equal(r.requires_human_approval, true);
  assert.equal(r.reconciliation.attendance, "not_asserted");
});

test("c6: un-cancelling a future event reinstates it as scheduled, not a touch", () => {
  const r = keep(classifyCalendarTouch({ event: event({ revision: 5, status: "confirmed" }), prior: prior({ status: "cancelled" }), now: NOW }));
  assert.equal(r.decision, "reconcile_reinstatement");
  assert.equal(r.proposal.counts_as_touch, false);
});

test("c6: a tentative event in the past is not a touch, with or without a prior", () => {
  const r = keep(classifyCalendarTouch({ event: event({ status: "tentative", ...PAST }), prior: null, now: NOW }));
  assert.equal(r.decision, "withhold_tentative_past");
  assert.equal(r.proposal, null);
  const corrected = keep(classifyCalendarTouch({ event: event({ revision: 5, status: "tentative", ...PAST }), prior: prior({ status: "confirmed", starts_at: "2026-09-24T13:00:00Z", ends_at: "2026-09-24T14:00:00Z" }), now: NOW }));
  assert.equal(corrected.proposal, null);
});

test("c6: counts_as_touch is true ONLY for a confirmed, wholly past event, over every status x temporality x prior", () => {
  const times = { future: {}, past: PAST, now: { starts_at: "2026-09-25T11:30:00Z", ends_at: "2026-09-25T12:30:00Z" } };
  for (const status of ["confirmed", "tentative", "cancelled"]) {
    for (const [label, t] of Object.entries(times)) {
      for (const priorStatus of [null, "confirmed", "tentative", "cancelled"]) {
        const req = { event: event({ revision: 7, status, ...t }), now: NOW,
          prior: priorStatus === null ? null : prior({ status: priorStatus, starts_at: "2026-09-20T10:00:00Z", ends_at: "2026-09-20T11:00:00Z" }) };
        const r = keep(classifyCalendarTouch(req));
        const touch = r.proposal?.counts_as_touch === true;
        const allowed = status === "confirmed" && label === "past" && priorStatus !== "cancelled";
        assert.ok(!touch || allowed, `${status}/${label}/prior=${priorStatus} proposed a touch`);
      }
    }
  }
});

test("c7: a second completion claim for the same obligation in one batch is a duplicate", () => {
  const claim = { claimed_by: "model_seam", evidence: [{ evidence_kind: "correspondence_message_ref", evidence_ref: "msg-ref-3", evidence_digest: d("reply") }] };
  const r = keep(classifyCorrespondenceCommitments({ items: [item({ completion: claim }), item({ completion: claim, source_message: msg(9) })], existing: [] }));
  assert.equal(r.outcomes[0].decision, "propose_evidence_backed_completion");
  assert.equal(r.outcomes[1].decision, "duplicate_within_batch");
  assert.equal(r.outcomes[1].proposal, null);
});

test("an RFC Message-ID is admitted as a native id, and an address anywhere else is still refused", () => {
  const rfc = { source_system: "fixture-mail", native_id: "CAF0x1a2b.fixture@mail.example.invalid.test", native_id_epoch: 0 };
  const r = keep(classifySignedLeaseSignal({ signal: clearSignal({ message: rfc }) }));
  assert.equal(r.message.native_id, rfc.native_id);
  expectCode(() => classifySignedLeaseSignal({ signal: clearSignal({ message: { ...rfc, source_system: "joe@example.invalid.test" } }) }), "routable_address_refused");
  expectCode(() => classifyCorrespondenceCommitments({ items: [item({ source_message: rfc, action_key: "reply-to-joe@example.invalid.test" })], existing: [] }), "routable_address_refused");
});

test("the ceiling is stamped LAST in result(), so no branch body can override it", async () => {
  const { readFileSync } = await import("node:fs");
  const src = readFileSync(new URL("../src/governed-correspondence-journey.v5.js", import.meta.url), "utf8");
  const fn = src.slice(src.indexOf("function result(kind, body) {"), src.indexOf("// c4 —"));
  const body = fn.indexOf("...body,");
  const ceiling = fn.indexOf("...CEILING,");
  assert.ok(body > 0 && ceiling > 0, "result() no longer spreads body and CEILING");
  assert.ok(ceiling > body, "CEILING must be spread after body");
  assert.equal(fn.slice(ceiling).split("\n")[1].trim(), "};", "nothing may follow CEILING in the result object");
});

// --------------------------------------------------------------------------- dispatch and ceiling

test("no request may carry a dispatch-shaped field, anywhere", () => {
  for (const name of ["send_now", "dispatch", "schedule_send", "outbound", "recipient_address", "autosend", "transmit"]) {
    expectCode(() => classifySignedLeaseSignal({ signal: { ...clearSignal(), [name]: true } }), "dispatch_field_refused");
    expectCode(() => evaluateCorrespondenceMerge({ merge: merge(), corrections: [], nested: { [name]: 1 } }), "dispatch_field_refused");
  }
});

test("no request may carry a routable destination value, anywhere", () => {
  for (const value of ["joe@example.invalid.test", "mailto:x", "tel:5551234567"]) {
    expectCode(() => classifyCorrespondenceCommitments({ items: [item({ action_key: value })], existing: [] }), "routable_address_refused");
  }
});

test("no request may carry raw correspondence content or a credential", () => {
  expectCode(() => classifySignedLeaseSignal({ signal: { ...clearSignal(), subject_line: "x" } }), "source_content_refused");
  expectCode(() => classifySignedLeaseSignal({ signal: { ...clearSignal(), message_body: "x" } }), "source_content_refused");
  expectCode(() => classifySignedLeaseSignal({ signal: { ...clearSignal(), access_token: "x" } }), "credential_field_refused");
});

test("the public surface is exactly the declared surface", () => {
  assert.deepEqual(Object.keys(J).sort(), [...J.V5_J103J_PUBLIC_SURFACE].sort());
});

test("no export names a provider write operation", async () => {
  const { V5_F10_WRITE_OPERATIONS } = await import("../src/partner-mail-calendar.v5.js");
  for (const name of Object.keys(J)) {
    for (const op of V5_F10_WRITE_OPERATIONS) assert.ok(!name.toLowerCase().includes(String(op).toLowerCase()), `${name} names ${op}`);
  }
});

function walk(value, visit, path = "") {
  visit(value, path);
  if (Array.isArray(value)) value.forEach((v, i) => walk(v, visit, `${path}[${i}]`));
  else if (value && typeof value === "object") for (const [k, v] of Object.entries(value)) walk(v, visit, `${path}.${k}`);
}

test("EVERY produced result carries the ceiling and no forbidden state", () => {
  assert.ok(PRODUCED.length >= 30, `only ${PRODUCED.length} results produced`);
  for (const r of PRODUCED) {
    assert.ok(Object.isFrozen(r));
    assert.equal(r.automatic_internal_update, false);
    assert.equal(r.internal_update_gate.step, J.V5_J103J_INTERNAL_UPDATE_STEP);
    assert.equal(r.internal_update_gate.status, "absent");
    assert.equal(r.internal_update_gate.absence_behavior, "deny");
    assert.equal(r.authority_established, false);
    assert.equal(r.dispatchable, false);
    assert.equal(r.provider_operation, null);
    assert.equal(r.effects.creates_effect, false);
    assert.equal(r.effects.provider_actions, 0);
    walk(r, (v, path) => {
      if (typeof v !== "string") return;
      assert.notEqual(v, "document_confirmed", `${path} confirms a document`);
      assert.ok(!/^attended$|^did_attend$/.test(v), `${path} asserts attendance`);
      // A native message id may be an RFC Message-ID; it names a message, not a destination.
      if (!/\.(native_id|provider_message_id|provider_thread_id)$/.test(path)) {
        assert.ok(!/@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/.test(v), `${path} carries a destination`);
      }
    });
    walk(r, (v, path) => {
      if (path.endsWith(".attendance_asserted")) assert.equal(v, false);
      if (path.endsWith(".document_confirmed")) assert.equal(v, false);
    });
  }
});

test("the policy digest is stable and moves with the vocabulary", () => {
  assert.equal(J.v5J103JourneyPolicyDigest(), J.v5J103JourneyPolicyDigest());
  assert.match(J.v5J103JourneyPolicyDigest(), /^sha256:[0-9a-f]{64}$/);
});
