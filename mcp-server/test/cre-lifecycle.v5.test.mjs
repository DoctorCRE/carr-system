
import test from "node:test";
import assert from "node:assert/strict";

import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import { V5_F01_AUTHORITY_INJECTION_FRAGMENTS } from "../src/record-source-authority.v5.js";
import {
  V5_J102_ASSIGNMENT_PHASES,
  V5_J102_AUTHORITY_INJECTION_FRAGMENTS,
  V5_J102_CONFLICT_KINDS,
  V5_J102_DEAL_AXES,
  V5_J102_NEGOTIATION_STATES,
  V5_J102_RELATIONSHIP_STATES,
  V5_J102_EVIDENCE_KINDS,
  V5_J102_FIELD_CLASS_REGISTRY,
  V5_J102_INITIALIZATION_IDS,
  V5_J102_ACTIVE_NEGOTIATION_STATES,
  V5_J102_INITIALIZED_SUBJECT_KINDS,
  V5_J102_PARTNER_AUTHORED_RECORD_KINDS,
  V5_J102_SETTLED_DECISIONS,
  V5_J102_SETTLED_DECISION_IDS,
  V5_J102_SUBJECT_BINDING_SOURCES,
  V5_J102_TRANSITION_IDS,
  V5_J102_UNCLASSIFIED_FIELD_POLICY,
  V5_J102_USER_CORRECTIONS,
  V5J102Error,
  applyLifecycleModelProposal,
  assertJ102DecisionBinding,
  classifyLegacyLifecycleRow,
  compareMigrationShadow,
  evaluateConcurrentEdit,
  evaluateLifecycleInitialization,
  evaluateLifecycleTransition,
  evaluateSelectedPropertyConstraint,
  projectLegacyCompatibilityView,
  projectOwnershipAndFreshness,
  projectSalesforceReference,
  v5J102DecisionSubsetDigest,
  v5J102EvidenceContract,
  v5J102FieldClass,
  v5J102InitializationContract,
  v5J102MigrationReadiness,
  v5J102PolicyDigest,
  v5J102Projection,
  v5J102TransitionContract,
  v5J102TransitionTruthTable,
} from "../src/cre-lifecycle.v5.js";


const NOW = "2026-09-09T12:00:00.000Z";
const T = {
  early: "2026-09-01T09:00:00Z",
  mid: "2026-09-05T09:00:00Z",
  late: "2026-09-08T09:00:00Z",
  future: "2026-09-10T09:00:00Z",
};
const D = n => `sha256:${String(n).padStart(2, "0").repeat(32)}`;

const PARTNER = Object.freeze({
  slug: "joe", human: true, authorization_class: "verified_partner",
  derived_by: "authenticated_handler_context",
});
const AGENT = Object.freeze({
  slug: "codex", human: false, authorization_class: "sponsored_agent",
  derived_by: "server_established_transaction_context",
});

const relationship = (over = {}) => ({
  subject_kind: "relationship", subject_id: "rel-synthetic-1",
  relationship_state: "prospect", active_engagement_count: 0, party_id: null, ...over,
});
const engagement = (over = {}) => ({
  subject_kind: "engagement", subject_id: "eng-synthetic-1",
  relationship_id: "rel-synthetic-1", engagement_state: "active",
  representation_basis: "signed_engagement_letter",
  effective_from: T.early, effective_to: null, ...over,
});
const assignment = (over = {}) => ({
  subject_kind: "assignment", subject_id: "asg-synthetic-1",
  engagement_id: "eng-synthetic-1", assignment_phase: "search",
  open_negotiation_count: 0, selected_property_id: null,
  active_lease_draft_target_id: null, pending_deal_id: null,
  multi_target_exception_ref: null, ...over,
});
const negotiation = (over = {}) => ({
  subject_kind: "property_negotiation", subject_id: "neg-synthetic-1",
  assignment_id: "asg-synthetic-1", property_id: "prop-synthetic-1",
  negotiation_state: "loi_drafted", ...over,
});
const deal = (over = {}) => ({
  subject_kind: "deal", subject_id: "deal-synthetic-1",
  assignment_id: "asg-synthetic-1", property_id: "prop-synthetic-1",
  instrument_kind: "lease", deal_state: "pending", execution_state: "unexecuted",
  diligence_state: "not_applicable", closing_state: "not_reached",
  commission_agreement_state: "absent", invoice_state: "not_invoiced",
  payment_state: "unpaid", completion_state: "open",
  cancellation_reason: null, closing_date: null, ...over,
});

const provenance = reader => ({
  loaded_by: "server_record_layer", reader, loaded_at: NOW,
  integrity: "recomputed_from_committed_row",
});

const DEFAULT_BOUND_ID = Object.freeze({
  relationship: "rel-synthetic-1", engagement: "eng-synthetic-1",
  assignment: "asg-synthetic-1", property_negotiation: "neg-synthetic-1",
  deal: "deal-synthetic-1",
});

const boundTo = (evidence_kind, bind = {}) => {
  const contract = v5J102EvidenceContract(evidence_kind);
  const subject_kind = bind.subject_kind ?? contract.binds_subject_kind ?? "deal";
  const first_party = contract.source === "first_party_record";
  return {
    subject_kind,
    subject_id: bind.subject_id ?? DEFAULT_BOUND_ID[subject_kind],
    bound_by: bind.bound_by ?? (first_party ? "first_party_record" : "stored_evidence_subject_link"),
    binding_digest: bind.binding_digest ?? (first_party ? D(2) : D(8)),
  };
};

const documentEvidence = (evidence_kind, over = {}, bind = {}) => ({
  evidence_kind, source: "f01_document", reference: "doc-synthetic-1",
  subject_binding: boundTo(evidence_kind, bind),
  document: {
    document_id: "doc-synthetic-1", document_class: "synthetic_agreement",
    version_no: 1, content_digest: D(1),
    preparation_state: "approved_for_delivery", delivery_state: "delivered",
    signature_state: "fully_executed", validity_state: "effective",
    version_state: "current", effective_from: null, effective_to: null, ...over,
  },
  provenance: provenance("ops.f01_read.document"),
});

const recordEvidence = (evidence_kind, over = {}, bind = {}) => ({
  evidence_kind, source: "first_party_record", reference: "rec-synthetic-1",
  subject_binding: boundTo(evidence_kind, bind),
  record: {
    record_kind: v5J102EvidenceContract(evidence_kind).record_kind,
    record_id: "rec-synthetic-1", content_digest: D(2),
    recorded_by: "joe",
    recorded_by_authorization_class: "verified_partner",
    recorded_at: T.mid, ...over,
  },
  provenance: provenance("ops.j102_first_party_record"),
});

const artifactEvidence = (evidence_kind, over = {}, bind = {}) => ({
  evidence_kind, source: "f01_corporate_artifact", reference: D(3),
  subject_binding: boundTo(evidence_kind, bind),
  artifact: {
    artifact_digest: D(3), content_digest: D(4),
    source_system: "synthetic_counterparty", evidence_class: "synthetic_countersigned_loi",
    observed_at: T.mid, ...over,
  },
  provenance: provenance("ops.f01_stored_artifact"),
});

const approvalEvidence = (evidence_kind, over = {}, bind = {}) => ({
  evidence_kind, source: "typed_approval", reference: "appr-synthetic-1",
  subject_binding: boundTo(evidence_kind, bind),
  approval: {
    approval_kind: v5J102EvidenceContract(evidence_kind).approval_kind,
    approval_ref: "appr-synthetic-1", approver_slug: "joe",
    approver_authorization_class: "verified_partner", approved_at: T.early,
    scope: "synthetic fixture scope", ...over,
  },
  provenance: provenance("ops.j102_typed_approval"),
});

const evaluate = over => evaluateLifecycleTransition({
  tenant: ORGANIZATION_TENANT_ID, actor: PARTNER, now: NOW, ...over,
});

const initialize = over => evaluateLifecycleInitialization({
  tenant: ORGANIZATION_TENANT_ID, actor: PARTNER, now: NOW,
  ...(over.initialization_id === "initialize-property-negotiation" &&
      !Object.prototype.hasOwnProperty.call(over, "active_negotiations_for_property")
    ? { active_negotiations_for_property: [] } : {}),
  ...over,
});

const client = (over = {}) =>
  relationship({ relationship_state: "client", active_engagement_count: 1, ...over });


test("the thirteen settled decisions are bound exactly, and drift is refused in both directions", () => {
  assert.equal(V5_J102_SETTLED_DECISION_IDS.length, 13);
  const binding = {
    decisions: Object.fromEntries(V5_J102_SETTLED_DECISION_IDS.map(id => [id, {
      source_evidence_digest: V5_J102_SETTLED_DECISIONS[id].source_evidence_digest,
      settled_requirement: V5_J102_SETTLED_DECISIONS[id].settled_requirement,
    }])),
    decision_subset_digest: v5J102DecisionSubsetDigest(),
  };
  assert.equal(assertJ102DecisionBinding(binding), true);

  const missing = structuredClone(binding);
  delete missing.decisions["Q094.D1"];
  delete missing.decision_subset_digest;
  assert.throws(() => assertJ102DecisionBinding(missing),
    e => e instanceof V5J102Error && e.code === "decision_binding_drift");

  const extra = structuredClone(binding);
  extra.decisions["Q999.D1"] = { source_evidence_digest: "a".repeat(64) };
  delete extra.decision_subset_digest;
  assert.throws(() => assertJ102DecisionBinding(extra),
    e => e instanceof V5J102Error && e.code === "decision_binding_drift");

  const tampered = structuredClone(binding);
  tampered.decisions["Q078.D1"].source_evidence_digest = "b".repeat(64);
  delete tampered.decision_subset_digest;
  assert.throws(() => assertJ102DecisionBinding(tampered),
    e => e instanceof V5J102Error && e.code === "decision_binding_drift");
});

test("every transition cites settled decisions, declares its five columns, and creates no Tour", () => {
  const cited = new Set();
  for (const id of V5_J102_TRANSITION_IDS) {
    const contract = v5J102TransitionContract(id);
    assert.ok(contract.required_evidence_alternatives.length >= 1, `${id} requires evidence`);
    assert.ok(contract.permitted_actor_classes.length >= 1, `${id} names permitted actors`);
    assert.ok(contract.coupled_facts.length >= 1, `${id} names its coupled facts`);
    assert.ok(typeof contract.reversibility === "string", `${id} declares reversibility`);
    assert.equal(contract.free_form_stage_update_permitted, false);
    assert.equal(contract.creates_or_activates_tour, false, `${id} must not touch a Tour`);
    contract.decision_refs.forEach(ref => cited.add(ref));
  }
  for (const id of ["Q069.D1", "Q072.D1", "Q077.D1", "Q078.D1", "Q080.D1",
    "Q082.D1", "Q094.D1", "Q095.D1", "Q096.D1"]) {
    assert.ok(cited.has(id), `${id} is cited by no transition`);
  }
});

test("the four overruled recommendations are recorded in code with their corrections", () => {
  const byRequirement = Object.fromEntries(
    V5_J102_USER_CORRECTIONS.map(c => [c.requirement_id, c]));
  assert.deepEqual(Object.keys(byRequirement).sort(), ["Q069", "Q078", "Q094", "Q095"]);
  for (const correction of V5_J102_USER_CORRECTIONS) {
    assert.ok(correction.superseded_recommendation.length > 0);
    assert.ok(correction.user_correction.length > 0);
    assert.ok(correction.encoded_as.length > 0);
  }
  assert.match(byRequirement.Q078.user_correction, /accepted LOI would be a pending deal/);
  assert.match(byRequirement.Q094.user_correction, /doesn't close until the final closing day/);
  assert.match(byRequirement.Q095.user_correction, /we dont do multiple lease drafts/);
});

test("the authority-injection fragment list agrees with F01's, so the two cannot drift", () => {
  assert.deepEqual([...V5_J102_AUTHORITY_INJECTION_FRAGMENTS].sort(),
    [...V5_F01_AUTHORITY_INJECTION_FRAGMENTS].sort());
});

test("the policy digest is stable across calls and covers the transition table", () => {
  assert.equal(v5J102PolicyDigest(), v5J102PolicyDigest());
  const projection = v5J102Projection();
  assert.equal(projection.policy_digest, v5J102PolicyDigest());
  assert.equal(projection.transitions.length, V5_J102_TRANSITION_IDS.length);
  assert.equal(projection.initializations.length, V5_J102_INITIALIZATION_IDS.length);
  assert.equal(projection.evidence_kinds.length, V5_J102_EVIDENCE_KINDS.length);
  assert.equal(projection.journey_three_excluded.tour_activation, false);
  assert.equal(projection.legacy.may_retire_callers, false);
  assert.deepEqual(projection.subject_creation_doors.by_initialization, {
    relationship: "initialize-prospect-relationship",
    assignment: "initialize-assignment",
    property_negotiation: "initialize-property-negotiation",
  });
  assert.deepEqual(projection.subject_creation_doors.coupled_to_transition, {
    engagement: "establish-client-and-engagement",
    deal: "commit-winning-property",
  });
  assert.equal(
    projection.subject_creation_doors.primary_subject_of_a_transition_is_never_created, true);
  assert.equal(projection.subject_creation_doors.initialization_requires_evidence, false);
});


test("Q077: an active signed representation agreement creates the Client AND the Engagement together", () => {
  const answer = evaluate({
    transition_id: "establish-client-and-engagement",
    subject: relationship(),
    evidence: [documentEvidence("signed_engagement_letter")],
    declared: { new_subject_id: "eng-synthetic-1" },
  });
  assert.equal(answer.decision, "allow");
  assert.equal(answer.reason_id, "active_representation_creates_client_and_engagement");
  assert.equal(answer.proposed_state.relationship.relationship_state, "client");
  assert.equal(answer.proposed_state.engagement.engagement_state, "active");
  assert.equal(answer.proposed_state.engagement.representation_basis, "signed_engagement_letter");
  assert.equal(answer.proposed_state.relationship.active_engagement_count, 1);
  assert.deepEqual(answer.coupled_facts_committed,
    ["relationship.relationship_state", "engagement.engagement_state",
      "engagement.representation_basis"]);
  assert.equal(answer.atomic_or_refuse, true);
  assert.deepEqual(answer.events.map(e => e.event_kind),
    ["client_status_established", "engagement_opened"]);
  assert.equal(answer.pre_signature_work_remains_prospect, true);
  assert.equal(answer.salesforce_opportunity_considered, false);
  assert.equal(answer.applied, false);
});

test("Q077: pre-signature work stays a Prospect — an unsigned or ineffective agreement refuses", () => {
  for (const [over, axis] of [
    [{ signature_state: "partially_signed" }, "signature_state"],
    [{ validity_state: "draft" }, "validity_state"],
    [{ version_state: "superseded" }, "version_state"],
  ]) {
    const answer = evaluate({
      transition_id: "establish-client-and-engagement",
      subject: relationship(),
      evidence: [documentEvidence("signed_engagement_letter", over)],
      declared: { new_subject_id: "eng-synthetic-1" },
    });
    assert.equal(answer.decision, "refuse");
    assert.equal(answer.reason_id, "document_state_not_met");
    assert.equal(answer.document_axis, axis);
    assert.equal(answer.proposed_state, null);
    assert.deepEqual(answer.events, []);
  }
});

test("Q077: a dated window is checked when the record layer carries one, and reported when it does not", () => {
  const early = evaluate({
    transition_id: "establish-client-and-engagement",
    subject: relationship(),
    evidence: [documentEvidence("signed_engagement_letter", { effective_from: T.future })],
    declared: { new_subject_id: "eng-synthetic-1" },
  });
  assert.equal(early.reason_id, "representation_not_yet_effective");

  const expired = evaluate({
    transition_id: "establish-client-and-engagement",
    subject: relationship(),
    evidence: [documentEvidence("signed_engagement_letter",
      { effective_from: T.early, effective_to: T.mid })],
    declared: { new_subject_id: "eng-synthetic-1" },
  });
  assert.equal(expired.reason_id, "representation_no_longer_active");

  const unwindowed = evaluate({
    transition_id: "establish-client-and-engagement",
    subject: relationship(),
    evidence: [documentEvidence("signed_engagement_letter")],
    declared: { new_subject_id: "eng-synthetic-1" },
  });
  assert.equal(unwindowed.decision, "allow");
  assert.equal(unwindowed.effective_window_carried, false);
  assert.equal(unwindowed.activeness_established_by, "f01_document_validity_state");
  assert.equal(unwindowed.proposed_state.engagement.effective_from, null);
});

test("Q077: an approved equivalent rests on a partner approval, and a sponsored-agent approval refuses", () => {
  const answer = evaluate({
    transition_id: "establish-client-and-engagement",
    subject: relationship(),
    evidence: [approvalEvidence("approved_representation_equivalent")],
    declared: { new_subject_id: "eng-synthetic-1" },
  });
  assert.equal(answer.decision, "allow");
  assert.equal(answer.proposed_state.engagement.representation_basis,
    "approved_representation_equivalent");

  assert.throws(() => evaluate({
    transition_id: "establish-client-and-engagement",
    subject: relationship(),
    evidence: [approvalEvidence("approved_representation_equivalent",
      { approver_authorization_class: "sponsored_agent" })],
    declared: { new_subject_id: "eng-synthetic-1" },
  }), e => e instanceof V5J102Error && e.code === "approval_authority_insufficient");
});

test("Q077: naming BOTH representation bases is ambiguous and refuses rather than picking one", () => {
  const answer = evaluate({
    transition_id: "establish-client-and-engagement",
    subject: relationship(),
    evidence: [documentEvidence("signed_engagement_letter"),
      approvalEvidence("approved_representation_equivalent")],
    declared: { new_subject_id: "eng-synthetic-1" },
  });
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "ambiguous_evidence_basis");
});

test("a relationship that is already a Client cannot be made one again", () => {
  const answer = evaluate({
    transition_id: "establish-client-and-engagement",
    subject: relationship({ relationship_state: "client", active_engagement_count: 1 }),
    evidence: [documentEvidence("signed_engagement_letter")],
    declared: { new_subject_id: "eng-synthetic-2" },
  });
  assert.equal(answer.reason_id, "prerequisite_not_met");
  assert.equal(answer.unmet_axis, "relationship_state");
  assert.equal(answer.observed, "client");
});


test("Q079/Q080: search initiation opens an Assignment without duplicating the client", () => {
  const answer = evaluate({
    transition_id: "open-assignment",
    subject: assignment({ assignment_phase: "research" }),
    related: { relationship: relationship({ relationship_state: "client", active_engagement_count: 1 }),
      engagement: engagement() },
    evidence: [recordEvidence("search_initiation")],
    declared: { mandate_scope: "search" },
  });
  assert.equal(answer.decision, "allow");
  assert.equal(answer.proposed_state.assignment.assignment_phase, "search");
  assert.equal(answer.duplicates_client, false);
  assert.equal(answer.closes_sibling_assignments, false);
});

test("Q077: an Assignment cannot be opened for a Prospect or under an inactive Engagement", () => {
  const prospect = evaluate({
    transition_id: "open-assignment",
    subject: assignment(),
    related: { relationship: relationship(), engagement: engagement() },
    evidence: [recordEvidence("search_initiation")],
    declared: { mandate_scope: "search" },
  });
  assert.equal(prospect.reason_id, "client_status_required");

  const terminated = evaluate({
    transition_id: "open-assignment",
    subject: assignment(),
    related: { relationship: relationship({ relationship_state: "client", active_engagement_count: 1 }),
      engagement: engagement({ engagement_state: "terminated" }) },
    evidence: [recordEvidence("search_initiation")],
    declared: { mandate_scope: "search" },
  });
  assert.equal(terminated.reason_id, "engagement_not_active");
});

test("H2: opening an Assignment cannot rewind a committed one, or leave a conflicting row", () => {
  const client = relationship({ relationship_state: "client", active_engagement_count: 1 });
  const open = over => evaluate({
    transition_id: "open-assignment",
    subject: assignment(over),
    related: { relationship: client, engagement: engagement() },
    evidence: [recordEvidence("search_initiation")],
    declared: { mandate_scope: "search" },
  });

  const committed = open({
    assignment_phase: "committed", selected_property_id: "prop-synthetic-1",
    active_lease_draft_target_id: "prop-synthetic-1", pending_deal_id: "deal-synthetic-1",
  });
  assert.equal(committed.decision, "refuse");
  assert.equal(committed.reason_id, "prerequisite_not_met");
  assert.equal(committed.unmet_axis, "assignment_phase");
  assert.deepEqual(committed.permitted, ["research", "search"]);

  for (const phase of ["negotiation", "concluded"]) {
    assert.equal(open({ assignment_phase: phase }).reason_id, "prerequisite_not_met",
      `${phase} must not be reopened by a mandate record`);
  }

  assert.equal(open({ pending_deal_id: "deal-synthetic-1" }).reason_id,
    "assignment_holds_pending_deal");
  assert.equal(open({ selected_property_id: "prop-synthetic-1" }).reason_id,
    "assignment_holds_committed_target");
  assert.equal(open({ active_lease_draft_target_id: "prop-synthetic-1" }).reason_id,
    "assignment_holds_committed_target");

  const narrowed = evaluate({
    transition_id: "open-assignment",
    subject: assignment({ open_negotiation_count: 2 }),
    related: { relationship: client, engagement: engagement() },
    evidence: [recordEvidence("search_initiation")],
    declared: { mandate_scope: "research" },
  });
  assert.equal(narrowed.reason_id, "open_negotiations_outlast_research_scope");

  const strayEngagement = evaluate({
    transition_id: "open-assignment",
    subject: assignment({ engagement_id: "eng-synthetic-other" }),
    related: { relationship: client, engagement: engagement() },
    evidence: [recordEvidence("search_initiation")],
    declared: { mandate_scope: "search" },
  });
  assert.equal(strayEngagement.reason_id, "assignment_not_under_loaded_engagement");

  const strayClient = evaluate({
    transition_id: "open-assignment",
    subject: assignment(),
    related: {
      relationship: relationship({ subject_id: "rel-synthetic-other",
        relationship_state: "client", active_engagement_count: 1 }),
      engagement: engagement(),
    },
    evidence: [recordEvidence("search_initiation")],
    declared: { mandate_scope: "search" },
  });
  assert.equal(strayClient.reason_id, "engagement_not_under_loaded_relationship");
});

test("Q072: research versus search is declared by the mandate, never guessed", () => {
  const answer = evaluate({
    transition_id: "open-assignment",
    subject: assignment(),
    related: { relationship: relationship({ relationship_state: "client", active_engagement_count: 1 }),
      engagement: engagement() },
    evidence: [recordEvidence("search_initiation")],
  });
  assert.equal(answer.reason_id, "mandate_scope_required");
});


test("OVERRULED-RULE GUARD (Q078): an LOI submission moves the Assignment to negotiation and creates NO Deal", () => {
  const answer = evaluate({
    transition_id: "record-loi-submission",
    subject: negotiation(),
    related: { assignment: assignment() },
    evidence: [documentEvidence("submitted_loi", { document_class: "letter_of_intent" })],
  });
  assert.equal(answer.decision, "allow");
  assert.equal(answer.proposed_state.property_negotiation.negotiation_state, "loi_submitted");
  assert.equal(answer.proposed_state.assignment.assignment_phase, "negotiation");
  assert.equal(answer.creates_deal, false);
  assert.equal(answer.concurrent_negotiations_permitted, true);
  assert.equal(answer.proposed_state.deal, undefined, "no deal is proposed by an LOI submission");
});

test("OVERRULED-RULE GUARD (Q078): an ACCEPTED LOI still creates no Deal — commitment does", () => {
  const answer = evaluate({
    transition_id: "record-loi-acceptance",
    subject: negotiation({ negotiation_state: "loi_submitted" }),
    evidence: [artifactEvidence("counterparty_loi_acceptance")],
  });
  assert.equal(answer.decision, "allow");
  assert.equal(answer.proposed_state.property_negotiation.negotiation_state, "loi_accepted");
  assert.equal(answer.creates_deal, false);
  assert.equal(answer.requires_selection_and_commitment_for_deal, true);
  assert.equal(answer.concurrent_acceptances_permitted, true);
  assert.equal(answer.proposed_state.deal, undefined);
});

test("OVERRULED-RULE GUARD (Q078): selection AND commitment create the PENDING Deal, not an executed one", () => {
  const answer = evaluate({
    transition_id: "commit-winning-property",
    subject: assignment({ assignment_phase: "negotiation", open_negotiation_count: 3 }),
    related: { property_negotiation: negotiation({ negotiation_state: "loi_accepted" }) },
    evidence: [recordEvidence("winner_selection_commitment")],
    declared: { instrument_kind: "lease", new_deal_id: "deal-synthetic-1" },
  });
  assert.equal(answer.decision, "allow");
  assert.equal(answer.reason_id, "selection_and_commitment_create_pending_deal");
  assert.equal(answer.proposed_state.deal.deal_state, "pending");
  assert.equal(answer.proposed_state.deal.execution_state, "unexecuted");
  assert.equal(answer.proposed_state.deal.closing_state, "not_reached");
  assert.equal(answer.proposed_state.assignment.assignment_phase, "committed");
  assert.equal(answer.proposed_state.assignment.selected_property_id, "prop-synthetic-1");
  assert.equal(answer.proposed_state.assignment.active_lease_draft_target_id, "prop-synthetic-1");
  assert.equal(answer.proposed_state.property_negotiation.negotiation_state, "selected_winner");
  assert.equal(answer.alternative_negotiations_retained, true);
  assert.equal(answer.alternative_negotiations_modified, 0);
  assert.deepEqual(answer.events.map(e => e.event_kind),
    ["winning_property_selected", "assignment_committed", "pending_deal_created"]);
});

test("Q078: commitment requires an ACCEPTED negotiation, and only a verified partner may commit", () => {
  const notAccepted = evaluate({
    transition_id: "commit-winning-property",
    subject: assignment({ assignment_phase: "negotiation", open_negotiation_count: 1 }),
    related: { property_negotiation: negotiation({ negotiation_state: "loi_submitted" }) },
    evidence: [recordEvidence("winner_selection_commitment")],
    declared: { instrument_kind: "lease", new_deal_id: "deal-synthetic-1" },
  });
  assert.equal(notAccepted.reason_id, "winning_negotiation_not_accepted");

  const asAgent = evaluate({
    transition_id: "commit-winning-property",
    actor: AGENT,
    subject: assignment({ assignment_phase: "negotiation", open_negotiation_count: 1 }),
    related: { property_negotiation: negotiation({ negotiation_state: "loi_accepted" }) },
    evidence: [recordEvidence("winner_selection_commitment")],
    declared: { instrument_kind: "lease", new_deal_id: "deal-synthetic-1" },
  });
  assert.equal(asAgent.reason_id, "actor_class_not_permitted");
  assert.deepEqual(asAgent.permitted_actor_classes, ["verified_partner"]);
});

test("OVERRULED-RULE GUARD (Q095): concurrent LOIs are unconstrained; only the SELECTED target is capped", () => {
  const free = assignment({ assignment_phase: "negotiation", open_negotiation_count: 5 });
  const available = evaluateSelectedPropertyConstraint({
    tenant: ORGANIZATION_TENANT_ID, assignment: free,
    candidate_property_id: "prop-synthetic-1",
  });
  assert.equal(available.decision, "allow");
  assert.equal(available.concurrent_lois_permitted, true);
  assert.equal(available.concurrent_acceptances_permitted, true);

  const taken = assignment({
    assignment_phase: "committed", selected_property_id: "prop-synthetic-9",
    active_lease_draft_target_id: "prop-synthetic-9", open_negotiation_count: 5,
  });
  const blocked = evaluateSelectedPropertyConstraint({
    tenant: ORGANIZATION_TENANT_ID, assignment: taken,
    candidate_property_id: "prop-synthetic-1",
  });
  assert.equal(blocked.decision, "refuse");
  assert.equal(blocked.reason_id, "winning_property_already_selected");
  assert.deepEqual(blocked.conflicts, ["selected_property_id", "active_lease_draft_target_id"]);
  assert.equal(blocked.stored_exception_ref_is_not_an_approval, true);

  const excepted = evaluateSelectedPropertyConstraint({
    tenant: ORGANIZATION_TENANT_ID, assignment: taken,
    candidate_property_id: "prop-synthetic-1",
    exception_approval: approvalEvidence("multi_target_exception_approval"),
  });
  assert.equal(excepted.decision, "allow");
  assert.equal(excepted.reason_id, "explicit_approved_exception");
  assert.equal(excepted.exception_used, true);
  assert.equal(excepted.exception_approved_by, "joe");
});

test("Q095: a stored exception reference cannot lift the constraint through the commit path", () => {
  const answer = evaluate({
    transition_id: "commit-winning-property",
    subject: assignment({
      assignment_phase: "negotiation", open_negotiation_count: 2,
      selected_property_id: "prop-synthetic-9",
      multi_target_exception_ref: "appr-synthetic-forged",
    }),
    related: { property_negotiation: negotiation({ negotiation_state: "loi_accepted" }) },
    evidence: [recordEvidence("winner_selection_commitment")],
    declared: { instrument_kind: "lease", new_deal_id: "deal-synthetic-1" },
  });
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "winning_property_already_selected");
});

test("Q095: an assignment already holding a pending Deal cannot commit a second one", () => {
  const answer = evaluate({
    transition_id: "commit-winning-property",
    subject: assignment({ assignment_phase: "negotiation", open_negotiation_count: 2,
      pending_deal_id: "deal-synthetic-0" }),
    related: { property_negotiation: negotiation({ negotiation_state: "loi_accepted" }) },
    evidence: [recordEvidence("winner_selection_commitment")],
    declared: { instrument_kind: "lease", new_deal_id: "deal-synthetic-1" },
  });
  assert.equal(answer.reason_id, "assignment_already_holds_pending_deal");
});

test("Q095: once committed, the Assignment refuses a fresh LOI rather than silently reopening", () => {
  const answer = evaluate({
    transition_id: "record-loi-submission",
    subject: negotiation({ subject_id: "neg-synthetic-2", property_id: "prop-synthetic-2" }),
    related: { assignment: assignment({ assignment_phase: "committed",
      selected_property_id: "prop-synthetic-1", pending_deal_id: "deal-synthetic-1" }) },
    evidence: [documentEvidence("submitted_loi", {}, { subject_id: "neg-synthetic-2" })],
  });
  assert.equal(answer.reason_id, "assignment_already_committed");
});


test("Q078: lease signing marks the executed lease and says nothing about closing", () => {
  const answer = evaluate({
    transition_id: "record-lease-execution",
    subject: deal(),
    evidence: [documentEvidence("executed_lease", { document_class: "lease" })],
  });
  assert.equal(answer.decision, "allow");
  assert.equal(answer.proposed_state.deal.execution_state, "executed");
  assert.equal(answer.proposed_state.deal.deal_state, "pending");
  assert.equal(answer.proposed_state.deal.closing_state, "not_reached");
  assert.equal(answer.execution_implies_closing, false);
});

test("OVERRULED-RULE GUARD (Q094): a signed purchase contract is EXECUTED and the Deal stays PENDING", () => {
  const answer = evaluate({
    transition_id: "record-purchase-contract-execution",
    subject: deal({ instrument_kind: "purchase" }),
    evidence: [documentEvidence("signed_purchase_contract",
      { document_class: "purchase_contract", validity_state: "draft" })],
  });
  assert.equal(answer.decision, "allow");
  assert.equal(answer.legally_executed, true);
  assert.equal(answer.proposed_state.deal.execution_state, "executed");
  assert.equal(answer.proposed_state.deal.deal_state, "pending");
  assert.equal(answer.proposed_state.deal.diligence_state, "in_progress");
  assert.equal(answer.proposed_state.deal.closing_state, "not_reached");
  assert.equal(answer.signing_closes_deal, false);
});

test("Q094: the purchase transition will not run on a lease deal, and vice versa", () => {
  const purchaseOnLease = evaluate({
    transition_id: "record-purchase-contract-execution",
    subject: deal({ instrument_kind: "lease" }),
    evidence: [documentEvidence("signed_purchase_contract")],
  });
  assert.equal(purchaseOnLease.reason_id, "instrument_kind_not_permitted");

  const leaseOnPurchase = evaluate({
    transition_id: "record-lease-execution",
    subject: deal({ instrument_kind: "purchase" }),
    evidence: [documentEvidence("executed_lease")],
  });
  assert.equal(leaseOnPurchase.reason_id, "instrument_kind_not_permitted");
});

test("OVERRULED-RULE GUARD (Q094): signing is not closing — a deal closes only on the actual date", () => {
  const executedPurchase = deal({
    instrument_kind: "purchase", execution_state: "executed", diligence_state: "satisfied",
  });
  const answer = evaluate({
    transition_id: "record-deal-closing",
    subject: executedPurchase,
    evidence: [recordEvidence("final_closing_settlement", { closing_date: T.late })],
  });
  assert.equal(answer.decision, "allow");
  assert.equal(answer.proposed_state.deal.deal_state, "closed");
  assert.equal(answer.proposed_state.deal.closing_state, "closed");
  assert.equal(answer.proposed_state.deal.closing_date, T.late);
  assert.equal(answer.closed_on_execution_evidence, false);
  assert.deepEqual(answer.coupled_facts_committed,
    ["deal.deal_state", "deal.closing_state", "deal.closing_date"]);
});

test("Q094: a closing refuses while diligence is unresolved, and refuses a date that has not arrived", () => {
  const inDiligence = evaluate({
    transition_id: "record-deal-closing",
    subject: deal({ instrument_kind: "purchase", execution_state: "executed",
      diligence_state: "in_progress" }),
    evidence: [recordEvidence("final_closing_settlement", { closing_date: T.late })],
  });
  assert.equal(inDiligence.reason_id, "diligence_not_resolved");

  const scheduled = evaluate({
    transition_id: "record-deal-closing",
    subject: deal({ instrument_kind: "purchase", execution_state: "executed",
      diligence_state: "satisfied" }),
    evidence: [recordEvidence("final_closing_settlement", { closing_date: T.future })],
  });
  assert.equal(scheduled.reason_id, "closing_date_in_the_future");
});

test("Q094: an unexecuted deal cannot close, and a settlement record with no date is unreadable", () => {
  const unexecuted = evaluate({
    transition_id: "record-deal-closing",
    subject: deal({ execution_state: "unexecuted" }),
    evidence: [recordEvidence("final_closing_settlement", { closing_date: T.late })],
  });
  assert.equal(unexecuted.reason_id, "prerequisite_not_met");
  assert.equal(unexecuted.unmet_axis, "execution_state");

  assert.throws(() => evaluate({
    transition_id: "record-deal-closing",
    subject: deal({ execution_state: "executed" }),
    evidence: [recordEvidence("final_closing_settlement")],
  }), e => e instanceof V5J102Error && e.code === "missing_field");
});

test("Q080: diligence is its own axis and a failure does not cancel the deal", () => {
  const answer = evaluate({
    transition_id: "record-diligence-outcome",
    subject: deal({ instrument_kind: "purchase", execution_state: "executed",
      diligence_state: "in_progress" }),
    evidence: [recordEvidence("diligence_outcome")],
    declared: { diligence_result: "failed" },
  });
  assert.equal(answer.decision, "allow");
  assert.equal(answer.proposed_state.deal.diligence_state, "failed");
  assert.equal(answer.proposed_state.deal.deal_state, "pending");
  assert.equal(answer.cancels_deal, false);
});


test("Q080: each money axis moves alone, and every other axis is echoed unchanged", () => {
  const base = deal({ execution_state: "executed", deal_state: "closed",
    closing_state: "closed", closing_date: T.late });
  const cases = [
    ["record-commission-agreement", documentEvidence("commission_agreement"), {},
      "commission_agreement_state", "agreed"],
    ["record-invoice-issued", recordEvidence("invoice_issued"), {}, "invoice_state", "invoiced"],
    ["record-payment", recordEvidence("payment_received"), { payment_level: "paid" },
      "payment_state", "paid"],
    ["record-completion", recordEvidence("completion_recorded"), {},
      "completion_state", "complete"],
  ];
  for (const [transition_id, evidence, declared, axis, value] of cases) {
    const answer = evaluate({ transition_id, subject: base, evidence: [evidence], declared });
    assert.equal(answer.decision, "allow", `${transition_id} allows`);
    assert.equal(answer.proposed_state.deal[axis], value);
    assert.equal(answer.axis, axis);
    for (const other of V5_J102_DEAL_AXES) {
      if (other === axis) continue;
      assert.equal(answer.proposed_state.deal[other], base[other],
        `${transition_id} must not move ${other}`);
    }
    assert.equal(answer.unchanged_axes.length, V5_J102_DEAL_AXES.length - 1);
  }
});

test("Q072/Q080: completion is NOT derived from payment — an unpaid deal may still complete", () => {
  const unpaid = deal({ execution_state: "executed", deal_state: "closed",
    closing_state: "closed", closing_date: T.late, payment_state: "unpaid",
    invoice_state: "not_invoiced" });
  const answer = evaluate({
    transition_id: "record-completion", subject: unpaid,
    evidence: [recordEvidence("completion_recorded")],
  });
  assert.equal(answer.decision, "allow");
  assert.equal(answer.proposed_state.deal.completion_state, "complete");
  assert.equal(answer.proposed_state.deal.payment_state, "unpaid");
});

test("Q080: a payment level that would not move the state refuses rather than writing a no-op", () => {
  const answer = evaluate({
    transition_id: "record-payment",
    subject: deal({ payment_state: "partially_paid" }),
    evidence: [recordEvidence("payment_received")],
    declared: { payment_level: "partially_paid" },
  });
  assert.equal(answer.reason_id, "payment_level_would_not_change_state");
});


test("Q096: a failed pending Deal is cancelled with its reason, and the Client is untouched", () => {
  const client = relationship({ relationship_state: "client", active_engagement_count: 1 });
  const answer = evaluate({
    transition_id: "cancel-pending-deal",
    subject: deal({ assignment_id: "asg-synthetic-1" }),
    related: {
      assignment: assignment({ assignment_phase: "committed", open_negotiation_count: 2,
        selected_property_id: "prop-synthetic-1",
        active_lease_draft_target_id: "prop-synthetic-1",
        pending_deal_id: "deal-synthetic-1" }),
      engagement: engagement(),
      relationship: client,
    },
    evidence: [recordEvidence("deal_failure_record",
      { reason: "synthetic fixture: landlord withdrew before lease execution" })],
    declared: { return_phase: "negotiation" },
  });
  assert.equal(answer.decision, "allow");
  assert.equal(answer.proposed_state.deal.deal_state, "cancelled");
  assert.equal(answer.proposed_state.deal.cancellation_reason,
    "synthetic fixture: landlord withdrew before lease execution");
  assert.equal(answer.proposed_state.assignment.assignment_phase, "negotiation");
  assert.equal(answer.proposed_state.assignment.selected_property_id, null);
  assert.equal(answer.proposed_state.assignment.active_lease_draft_target_id, null);
  assert.equal(answer.proposed_state.assignment.pending_deal_id, null);
  assert.equal(answer.client_relationship_preserved, true);
  assert.equal(answer.relationship_state, "client");
  assert.equal(answer.relationship_rewritten, false);
  assert.equal(answer.proposed_state.relationship, undefined);
  assert.equal(answer.history_preserved, true);
  assert.equal(answer.deal_row_deleted, false);
  assert.equal(answer.negotiation_history_deleted, false);
});

test("Q096: returning to negotiation requires an open negotiation to return to", () => {
  const answer = evaluate({
    transition_id: "cancel-pending-deal",
    subject: deal(),
    related: { assignment: assignment({ assignment_phase: "committed",
      open_negotiation_count: 0, pending_deal_id: "deal-synthetic-1" }) },
    evidence: [recordEvidence("deal_failure_record", { reason: "synthetic fixture reason" })],
    declared: { return_phase: "negotiation" },
  });
  assert.equal(answer.reason_id, "no_open_negotiation_to_return_to");
});

test("Q096: a failure record with no reason is unreadable, and a closed Deal cannot be cancelled", () => {
  assert.throws(() => evaluate({
    transition_id: "cancel-pending-deal",
    subject: deal(),
    related: { assignment: assignment() },
    evidence: [recordEvidence("deal_failure_record")],
    declared: { return_phase: "search" },
  }), e => e instanceof V5J102Error && e.code === "missing_field");

  const closed = evaluate({
    transition_id: "cancel-pending-deal",
    subject: deal({ deal_state: "closed", closing_state: "closed", closing_date: T.late }),
    related: { assignment: assignment() },
    evidence: [recordEvidence("deal_failure_record", { reason: "synthetic fixture reason" })],
    declared: { return_phase: "search" },
  });
  assert.equal(closed.reason_id, "prerequisite_not_met");
  assert.equal(closed.unmet_axis, "deal_state");
});


test("Q082/Q072: model output is never evidence, and the refusal names the source", () => {
  assert.throws(() => evaluate({
    transition_id: "record-lease-execution",
    subject: deal(),
    evidence: [{ ...documentEvidence("executed_lease"), source: "assistant_text" }],
  }), e => e instanceof V5J102Error && e.code === "refused_evidence_source");

  assert.throws(() => evaluate({
    transition_id: "record-lease-execution",
    subject: deal(),
    evidence: [{ ...documentEvidence("executed_lease"), source: "model_output" }],
  }), e => e instanceof V5J102Error && e.code === "refused_evidence_source");
});

test("Q082: evidence that was not server-loaded is refused, however well-formed", () => {
  const forged = documentEvidence("executed_lease");
  forged.provenance = { ...forged.provenance, loaded_by: "caller" };
  assert.throws(() => evaluate({
    transition_id: "record-lease-execution", subject: deal(), evidence: [forged],
  }), e => e instanceof V5J102Error && e.code === "evidence_not_server_loaded");

  const untrusted = documentEvidence("executed_lease");
  untrusted.provenance = { ...untrusted.provenance, integrity: "trusted_readback" };
  assert.throws(() => evaluate({
    transition_id: "record-lease-execution", subject: deal(), evidence: [untrusted],
  }), e => e instanceof V5J102Error && e.code === "evidence_integrity_not_recomputed");
});

test("Q082: an actor that does not name a server derivation is refused", () => {
  assert.throws(() => evaluate({
    transition_id: "record-lease-execution", subject: deal(),
    evidence: [documentEvidence("executed_lease")],
    actor: { ...PARTNER, derived_by: "caller_supplied" },
  }), e => e instanceof V5J102Error && e.code === "actor_not_server_derived");
});

test("Q082: a caller cannot assert the outcome or smuggle authority into the request", () => {
  for (const [field, code] of [
    ["signed", "caller_asserted_fact_refused"],
    ["approved_equivalent", "caller_asserted_fact_refused"],
    ["executed_at", "caller_asserted_fact_refused"],
    ["acting_as", "caller_authority_field_refused"],
    ["override_prerequisites", "caller_authority_field_refused"],
  ]) {
    assert.throws(() => evaluate({
      transition_id: "record-lease-execution", subject: deal(),
      evidence: [documentEvidence("executed_lease")], [field]: true,
    }), e => e instanceof V5J102Error && e.code === code, `${field} must refuse as ${code}`);
  }
});

test("Q082: evidence for the wrong transition, duplicated, or extra is refused by name", () => {
  const wrong = evaluate({
    transition_id: "record-lease-execution", subject: deal(),
    evidence: [recordEvidence("invoice_issued")],
  });
  assert.equal(wrong.reason_id, "required_evidence_absent");
  assert.deepEqual(wrong.supplied_evidence_kinds, ["invoice_issued"]);

  assert.throws(() => evaluate({
    transition_id: "record-lease-execution", subject: deal(),
    evidence: [documentEvidence("executed_lease"), documentEvidence("executed_lease")],
  }), e => e instanceof V5J102Error && e.code === "duplicate_evidence_kind");

  const extra = evaluate({
    transition_id: "record-lease-execution", subject: deal(),
    evidence: [documentEvidence("executed_lease"), recordEvidence("invoice_issued")],
  });
  assert.equal(extra.reason_id, "unexpected_evidence_supplied");
  assert.deepEqual(extra.unexpected_evidence_kinds, ["invoice_issued"]);
});

test("Q082: a first-party record of the wrong kind cannot stand in for the right one", () => {
  const swapped = recordEvidence("deal_failure_record", { reason: "synthetic fixture reason" });
  swapped.record.record_kind = "invoice";
  assert.throws(() => evaluate({
    transition_id: "cancel-pending-deal", subject: deal(),
    related: { assignment: assignment() }, evidence: [swapped],
    declared: { return_phase: "search" },
  }), e => e instanceof V5J102Error && e.code === "evidence_record_kind_mismatch");
});

test("Q082: evidence recorded after the server instant is refused", () => {
  const answer = evaluate({
    transition_id: "record-invoice-issued", subject: deal(),
    evidence: [recordEvidence("invoice_issued", { recorded_at: T.future })],
  });
  assert.equal(answer.reason_id, "evidence_observed_after_server_time");
});

test("Q082: there is no free-form stage update — an unregistered transition cannot be named", () => {
  assert.throws(() => evaluate({
    transition_id: "set-deal-stage", subject: deal(),
    evidence: [documentEvidence("executed_lease")],
  }), e => e instanceof V5J102Error && e.code === "unknown_transition");
  for (const answerKey of ["free_form_stage_update", "model_output_used_as_evidence",
    "creates_or_activates_tour"]) {
    const answer = evaluate({
      transition_id: "record-lease-execution", subject: deal(),
      evidence: [documentEvidence("executed_lease")],
    });
    assert.equal(answer[answerKey], false, `${answerKey} must be false on every answer`);
  }
});


test("BLOCK-2: a valid closing settlement cannot close a deal it is not bound to", () => {
  const wrongDeal = evaluate({
    transition_id: "record-deal-closing",
    subject: deal({ execution_state: "executed" }),
    evidence: [recordEvidence("final_closing_settlement", { closing_date: T.late },
      { subject_id: "deal-synthetic-elsewhere" })],
  });
  assert.equal(wrongDeal.decision, "refuse");
  assert.equal(wrongDeal.reason_id, "evidence_not_bound_to_subject");
  assert.equal(wrongDeal.bound_subject_id, "deal-synthetic-elsewhere");
  assert.equal(wrongDeal.bound_by, "first_party_record");
  assert.equal(wrongDeal.proposed_state, null);

  const rightDeal = evaluate({
    transition_id: "record-deal-closing",
    subject: deal({ execution_state: "executed" }),
    evidence: [recordEvidence("final_closing_settlement", { closing_date: T.late })],
  });
  assert.equal(rightDeal.decision, "allow");
  assert.equal(rightDeal.proposed_state.deal.closing_date, T.late);
});

test("BLOCK-2: a commitment for another assignment, and a lease for another deal, both refuse", () => {
  const wrongAssignment = evaluate({
    transition_id: "commit-winning-property",
    subject: assignment({ assignment_phase: "negotiation", open_negotiation_count: 1 }),
    related: { property_negotiation: negotiation({ negotiation_state: "loi_accepted" }) },
    evidence: [recordEvidence("winner_selection_commitment", {},
      { subject_id: "asg-synthetic-elsewhere" })],
    declared: { instrument_kind: "lease", new_deal_id: "deal-synthetic-1" },
  });
  assert.equal(wrongAssignment.reason_id, "evidence_not_bound_to_subject");
  assert.equal(wrongAssignment.bound_subject_kind, "assignment");

  const wrongLease = evaluate({
    transition_id: "record-lease-execution",
    subject: deal(),
    evidence: [documentEvidence("executed_lease", { document_class: "lease" },
      { subject_id: "deal-synthetic-another-client" })],
  });
  assert.equal(wrongLease.reason_id, "evidence_not_bound_to_subject");
  assert.equal(wrongLease.bound_by, "stored_evidence_subject_link");

  const wrongNegotiation = evaluate({
    transition_id: "record-loi-acceptance",
    subject: negotiation({ negotiation_state: "loi_submitted" }),
    evidence: [artifactEvidence("counterparty_loi_acceptance", {},
      { subject_id: "neg-synthetic-elsewhere" })],
  });
  assert.equal(wrongNegotiation.reason_id, "evidence_not_bound_to_subject");
});

test("BLOCK-2: evidence bound to the wrong KIND of subject refuses before the id is compared", () => {
  const answer = evaluate({
    transition_id: "record-deal-closing",
    subject: deal({ execution_state: "executed" }),
    evidence: [recordEvidence("final_closing_settlement", { closing_date: T.late },
      { subject_kind: "assignment", subject_id: "asg-synthetic-1" })],
  });
  assert.equal(answer.reason_id, "evidence_bound_to_wrong_subject_kind");
  assert.equal(answer.required_subject_kind, "deal");
});

test("BLOCK-2: every evidence record must carry a server-derived binding, and it cannot be crossed", () => {
  const unbound = { ...documentEvidence("executed_lease") };
  delete unbound.subject_binding;
  assert.throws(() => evaluate({
    transition_id: "record-lease-execution", subject: deal(), evidence: [unbound],
  }), e => e instanceof V5J102Error && e.code === "missing_field");

  const crossed = documentEvidence("executed_lease", {}, { bound_by: "first_party_record" });
  assert.throws(() => evaluate({
    transition_id: "record-lease-execution", subject: deal(), evidence: [crossed],
  }), e => e instanceof V5J102Error && e.code === "subject_binding_source_mismatch");

  const detached = recordEvidence("invoice_issued", {}, { binding_digest: D(9) });
  assert.throws(() => evaluate({
    transition_id: "record-invoice-issued", subject: deal(), evidence: [detached],
  }), e => e instanceof V5J102Error && e.code === "subject_binding_digest_mismatch");

  for (const id of V5_J102_TRANSITION_IDS) {
    const contract = v5J102TransitionContract(id);
    for (const set of contract.required_evidence_alternatives) {
      for (const kind of set) {
        assert.equal(v5J102EvidenceContract(kind).binds_subject_kind, contract.subject_kind,
          `${id} and ${kind} must agree about the subject the evidence binds`);
      }
    }
  }
  assert.deepEqual([...V5_J102_SUBJECT_BINDING_SOURCES],
    ["first_party_record", "stored_evidence_subject_link"]);
});


test("H5: a sponsored agent cannot author a closing, a winner, a failure or a correction proof", () => {
  assert.deepEqual([...V5_J102_PARTNER_AUTHORED_RECORD_KINDS],
    ["closing_settlement", "deal_failure", "lifecycle_correction", "winning_property_commitment"]);

  const laundered = evaluate({
    transition_id: "record-deal-closing",
    subject: deal({ execution_state: "executed" }),
    actor: PARTNER,
    evidence: [recordEvidence("final_closing_settlement",
      { closing_date: T.late, recorded_by: "codex",
        recorded_by_authorization_class: "sponsored_agent" })],
  });
  assert.equal(laundered.decision, "refuse");
  assert.equal(laundered.reason_id, "evidence_author_class_not_permitted");
  assert.equal(laundered.required_author_class, "verified_partner");
  assert.equal(laundered.evidence_author_class, "sponsored_agent");
  assert.equal(laundered.evidence_author, "codex");

  const winner = evaluate({
    transition_id: "commit-winning-property",
    subject: assignment({ assignment_phase: "negotiation", open_negotiation_count: 1 }),
    related: { property_negotiation: negotiation({ negotiation_state: "loi_accepted" }) },
    evidence: [recordEvidence("winner_selection_commitment",
      { recorded_by: "codex", recorded_by_authorization_class: "sponsored_agent" })],
    declared: { instrument_kind: "lease", new_deal_id: "deal-synthetic-1" },
  });
  assert.equal(winner.reason_id, "evidence_author_class_not_permitted");

  const failure = evaluate({
    transition_id: "cancel-pending-deal",
    subject: deal(),
    related: { assignment: assignment({ assignment_phase: "committed",
      open_negotiation_count: 1, pending_deal_id: "deal-synthetic-1" }) },
    evidence: [recordEvidence("deal_failure_record",
      { reason: "synthetic fixture reason", recorded_by: "codex",
        recorded_by_authorization_class: "sponsored_agent" })],
    declared: { return_phase: "negotiation" },
  });
  assert.equal(failure.reason_id, "evidence_author_class_not_permitted");

  const invoice = evaluate({
    transition_id: "record-invoice-issued", subject: deal(), actor: AGENT,
    evidence: [recordEvidence("invoice_issued",
      { recorded_by: "codex", recorded_by_authorization_class: "sponsored_agent" })],
  });
  assert.equal(invoice.decision, "allow");
});


test("Q072: a model proposes within its seam and advances nothing", () => {
  const ok = applyLifecycleModelProposal({
    seam: "inbound_lifecycle_document_kind", label: "lease", confidence: 0.9,
  });
  assert.equal(ok.decision, "allow");
  assert.equal(ok.advances_state, false);
  assert.equal(ok.is_evidence, false);
  assert.equal(ok.requires_deterministic_validation, true);

  const outside = applyLifecycleModelProposal({
    seam: "counterparty_response_hint", label: "definitely_signed",
  });
  assert.equal(outside.decision, "refuse");
  assert.equal(outside.reason_id, "model_label_outside_seam");

  const widening = applyLifecycleModelProposal({
    seam: "suggested_transition", label: "record-deal-closing", apply_now: true,
  });
  assert.equal(widening.decision, "refuse");
  assert.equal(widening.reason_id, "model_widening_refused");
  assert.equal(widening.offending_field, "apply_now");
});


test("Q083: a Salesforce opportunity stays external, keeps its own labels, and sets no state", () => {
  const answer = projectSalesforceReference({
    tenant: ORGANIZATION_TENANT_ID,
    opportunity_id: "006SYNTHETIC001",
    opportunity_name: "Synthetic Medical Group - New Location",
    opportunity_phase: "Pending Deal",
    observed_at: T.mid,
    linked_subject_kind: "assignment",
    linked_subject_id: "asg-synthetic-1",
  });
  assert.equal(answer.decision, "allow");
  assert.equal(answer.reason_id, "external_reference_progressively_linked");
  assert.equal(answer.opportunity_name, "Synthetic Medical Group - New Location");
  assert.equal(answer.opportunity_phase, "Pending Deal");
  assert.equal(answer.is_external_corporate_reference, true);
  assert.equal(answer.creates_doctorcre_client, false);
  assert.equal(answer.creates_doctorcre_deal, false);
  assert.equal(answer.sets_lifecycle_state, false);
  assert.equal(answer.phase_label_is_doctorcre_state, false);
});

test("Q083: an attempt to map a Salesforce label onto lifecycle state refuses by name", () => {
  for (const key of ["deal_state", "maps_to_assignment_phase", "derived_state"]) {
    assert.throws(() => projectSalesforceReference({
      tenant: ORGANIZATION_TENANT_ID, opportunity_id: "006SYNTHETIC001",
      opportunity_name: "Synthetic", opportunity_phase: "Pending Deal",
      observed_at: T.mid, [key]: "pending",
    }), e => e instanceof V5J102Error && e.code === "salesforce_label_mapping_refused",
    `${key} must refuse the mapping`);
  }
});


const edit = (field, by) => ({
  field, value_digest: D(7), edited_by: by, edited_at: T.mid,
});

test("Q103: a caller cannot label a field's class, and the class is derived from the registry", () => {
  assert.throws(() => evaluateConcurrentEdit({
    tenant: ORGANIZATION_TENANT_ID, actor: PARTNER,
    base_version_digest: D(10), current_version_digest: D(11),
    incoming: [{ ...edit("deal_state", "joe"), field_class: "routine" }],
    concurrent: [edit("payment_state", "dell")],
  }), e => e instanceof V5J102Error && e.code === "unknown_field",
  "a caller-supplied field_class is not a field this evaluator reads");

  assert.equal(v5J102FieldClass("deal_state"), "lifecycle");
  assert.equal(v5J102FieldClass("payment_state"), "financial");
  assert.equal(v5J102FieldClass("supporting_document_id"), "document");
  assert.equal(v5J102FieldClass("some_customer_field"), null,
    "this module classifies no field it does not define");
});

test("Q103: material-class edits reconcile visibly with both versions kept", () => {
  const overlapping = evaluateConcurrentEdit({
    tenant: ORGANIZATION_TENANT_ID, actor: PARTNER,
    base_version_digest: D(10), current_version_digest: D(11),
    incoming: [edit("deal_state", "joe")],
    concurrent: [edit("deal_state", "dell")],
  });
  assert.equal(overlapping.decision, "reconcile");
  assert.equal(overlapping.reason_id, "overlapping_edits_require_reconciliation");
  assert.deepEqual(overlapping.overlapping_fields, ["deal_state"]);
  assert.equal(overlapping.reconciliation_item.visible, true);
  assert.equal(overlapping.reconciliation_item.resolved_by_machine, false);
  assert.equal(overlapping.reconciliation_item.incoming_edits.length, 1);
  assert.equal(overlapping.reconciliation_item.concurrent_edits.length, 1);
  assert.deepEqual(overlapping.preserved_versions, ["base", "current", "incoming"]);

  for (const [field, cls] of [["deal_state", "lifecycle"], ["payment_state", "financial"],
    ["supporting_document_id", "document"]]) {
    const material = evaluateConcurrentEdit({
      tenant: ORGANIZATION_TENANT_ID, actor: PARTNER,
      base_version_digest: D(10), current_version_digest: D(11),
      incoming: [edit(field, "joe")],
      concurrent: [edit("closing_state", "dell")],
    });
    assert.equal(material.decision, "reconcile", `${cls} must reconcile`);
    assert.equal(material.reason_id, "material_class_edits_require_reconciliation");
    assert.equal(material.merged, false);
    assert.equal(v5J102FieldClass(field), cls);
  }
});

test("Q103: an unclassified field never auto-merges, and the missing policy is named", () => {
  const answer = evaluateConcurrentEdit({
    tenant: ORGANIZATION_TENANT_ID, actor: PARTNER,
    base_version_digest: D(10), current_version_digest: D(11),
    incoming: [edit("internal_note", "joe")],
    concurrent: [edit("next_touch_hint", "dell")],
  });
  assert.equal(answer.decision, "reconcile");
  assert.equal(answer.reason_id, "field_classification_not_established");
  assert.equal(answer.merged, false);
  assert.deepEqual(answer.unclassified_fields, ["internal_note", "next_touch_hint"]);
  assert.equal(answer.missing_fact, V5_J102_UNCLASSIFIED_FIELD_POLICY.fact);
  assert.equal(answer.produced_by, "not_produced_by_this_slice");
  assert.equal(answer.reconciliation_item.conflict_kind, "unclassified_field_edit");
  assert.equal(answer.last_writer_wins, false);
  assert.equal(answer.silent_overwrite, false);

  assert.equal(V5_J102_UNCLASSIFIED_FIELD_POLICY.routine_fields_registered, 0);
  assert.equal(Object.values(V5_J102_FIELD_CLASS_REGISTRY).includes("routine"), false);
});

test("Q103: an unmoved base still allows, and an uncharacterized change reconciles", () => {
  const quiet = evaluateConcurrentEdit({
    tenant: ORGANIZATION_TENANT_ID, actor: PARTNER,
    base_version_digest: D(10), current_version_digest: D(10),
    incoming: [edit("deal_state", "joe")],
  });
  assert.equal(quiet.decision, "allow");
  assert.equal(quiet.reason_id, "no_concurrent_movement");
  assert.equal(quiet.merged, false);

  const answer = evaluateConcurrentEdit({
    tenant: ORGANIZATION_TENANT_ID, actor: PARTNER,
    base_version_digest: D(10), current_version_digest: D(11),
    incoming: [edit("internal_note", "joe")],
  });
  assert.equal(answer.decision, "reconcile");
  assert.equal(answer.reason_id, "concurrent_change_not_characterized");
  assert.equal(answer.merged, false);
});

test("Q103: the exported conflict vocabulary IS what the evaluator can file, exactly", () => {
  const filed = new Set();
  const drive = (incoming, concurrent) => {
    const answer = evaluateConcurrentEdit({
      tenant: ORGANIZATION_TENANT_ID, actor: PARTNER,
      base_version_digest: D(10), current_version_digest: D(11),
      incoming, concurrent,
    });
    if (answer.reconciliation_item !== null) filed.add(answer.reconciliation_item.conflict_kind);
    return answer;
  };
  drive([edit("internal_note", "joe")], undefined);
  drive([edit("deal_state", "joe")], [edit("deal_state", "dell")]);
  drive([edit("deal_state", "joe")], [edit("closing_state", "dell")]);
  drive([edit("internal_note", "joe")], [edit("next_touch_hint", "dell")]);

  assert.deepEqual([...filed].sort(), [...V5_J102_CONFLICT_KINDS].sort(),
    "every registered kind is reachable and no reachable kind is unregistered");
  assert.equal(V5_J102_CONFLICT_KINDS.length, new Set(V5_J102_CONFLICT_KINDS).size);

  assert.deepEqual(v5J102Projection().conflict_kinds, [...V5_J102_CONFLICT_KINDS]);

  const item = drive([edit("deal_state", "joe")], [edit("internal_note", "dell")])
    .reconciliation_item;
  for (const e of [...item.incoming_edits, ...item.concurrent_edits]) {
    assert.equal(e.field_class, v5J102FieldClass(e.field), `${e.field} carries its registered class`);
  }
});

test("Q103: ownership and freshness are projected from trusted state, and unknown stays unknown", () => {
  const known = projectOwnershipAndFreshness({
    tenant: ORGANIZATION_TENANT_ID, subject_kind: "deal", subject_id: "deal-synthetic-1",
    owner_slug: "joe", last_material_change_at: T.late, last_material_change_by: "joe",
    state_digest: D(12), active_automation: [], now: NOW,
  });
  assert.equal(known.owner_known, true);
  assert.equal(known.freshness_known, true);
  assert.equal(known.active_automation_known, true);
  assert.deepEqual(known.active_automation, []);
  assert.ok(known.freshness_age_seconds > 0);
  assert.deepEqual(known.inferred_fields, []);

  const unknown = projectOwnershipAndFreshness({
    tenant: ORGANIZATION_TENANT_ID, subject_kind: "deal", subject_id: "deal-synthetic-1",
    state_digest: D(12), now: NOW,
  });
  assert.equal(unknown.owner_known, false);
  assert.equal(unknown.freshness_known, false);
  assert.equal(unknown.freshness_age_seconds, null);
  assert.equal(unknown.active_automation_known, false);
  assert.equal(unknown.active_automation, null);

  assert.throws(() => projectOwnershipAndFreshness({
    tenant: ORGANIZATION_TENANT_ID, subject_kind: "deal", subject_id: "deal-synthetic-1",
    state_digest: D(12), last_material_change_at: T.future, now: NOW,
  }), e => e instanceof V5J102Error && e.code === "last_material_change_after_now");
});


test("Q081: legacy rows are classified from evidence, and ambiguity goes to reconciliation", () => {
  const executed = documentEvidence("executed_lease");
  const closing = recordEvidence("final_closing_settlement", { closing_date: T.late });

  const assignmentRow = classifyLegacyLifecycleRow({
    tenant: ORGANIZATION_TENANT_ID, legacy_row_id: "legacy-1", legacy_phase: "site_selection",
  });
  assert.equal(assignmentRow.classification, "assignment");
  assert.equal(assignmentRow.projected_assignment_phase, "search");
  assert.equal(assignmentRow.label_used_as_state, false);

  const pendingDeal = classifyLegacyLifecycleRow({
    tenant: ORGANIZATION_TENANT_ID, legacy_row_id: "legacy-2", legacy_phase: "due_diligence",
    executed_instrument_evidence: executed,
  });
  assert.equal(pendingDeal.classification, "deal");
  assert.equal(pendingDeal.projected_deal_state, "pending");
  assert.equal(pendingDeal.closed_on_execution_evidence, false);

  const closedDeal = classifyLegacyLifecycleRow({
    tenant: ORGANIZATION_TENANT_ID, legacy_row_id: "legacy-3", legacy_phase: "closing",
    executed_instrument_evidence: executed, closing_evidence: closing,
  });
  assert.equal(closedDeal.projected_deal_state, "closed");

  const ambiguous = classifyLegacyLifecycleRow({
    tenant: ORGANIZATION_TENANT_ID, legacy_row_id: "legacy-4", legacy_phase: "closing",
  });
  assert.equal(ambiguous.decision, "reconcile");
  assert.equal(ambiguous.classification, "requires_reconciliation");
  assert.deepEqual(ambiguous.missing_evidence, ["executed_instrument_evidence"]);

  const contradictory = classifyLegacyLifecycleRow({
    tenant: ORGANIZATION_TENANT_ID, legacy_row_id: "legacy-5", legacy_phase: "closing",
    legacy_closed_flag: true, legacy_outcome: "open",
  });
  assert.equal(contradictory.reason_id, "closed_flag_and_outcome_disagree");
  assert.equal(contradictory.requires_reconciliation, true);
});

test("Q081: the compatibility view is a projection, and the shadow reports without repairing", () => {
  const view = projectLegacyCompatibilityView({
    tenant: ORGANIZATION_TENANT_ID,
    assignment: assignment({ assignment_phase: "committed" }),
    deal: deal({ execution_state: "executed", instrument_kind: "purchase",
      diligence_state: "in_progress" }),
  });
  assert.equal(view.legacy_phase, "due_diligence");
  assert.equal(view.legacy_phase_source, "deal");
  assert.equal(view.legacy_closed, false);
  assert.equal(view.legacy_outcome, "open");
  assert.equal(view.authoritative, false);
  assert.equal(view.writable, false);
  assert.equal(view.derived_from_current_records, true);
  assert.equal(view.retires_any_caller, false);

  const clean = compareMigrationShadow({
    tenant: ORGANIZATION_TENANT_ID,
    legacy_row: { legacy_row_id: "legacy-6", phase: "due_diligence", closed: false, outcome: "open" },
    projected_view: view,
  });
  assert.equal(clean.decision, "allow");
  assert.deepEqual(clean.differences, []);

  const drifted = compareMigrationShadow({
    tenant: ORGANIZATION_TENANT_ID,
    legacy_row: { legacy_row_id: "legacy-7", phase: "closing", closed: true, outcome: "won" },
    projected_view: view,
  });
  assert.equal(drifted.decision, "reconcile");
  assert.equal(drifted.differences.length, 3);
  assert.equal(drifted.requires_human_reconciliation, true);
  assert.equal(drifted.legacy_row_modified, false);
  assert.equal(drifted.projection_modified, false);
});

test("Q081: migration is never claimed complete, and the missing facts are named", () => {
  const bare = v5J102MigrationReadiness({ tenant: ORGANIZATION_TENANT_ID });
  assert.equal(bare.decision, "refuse");
  assert.equal(bare.reason_id, "caller_census_absent");
  assert.equal(bare.may_retire_callers, false);
  assert.equal(bare.migration_complete, false);
  assert.equal(bare.big_bang_rename, false);
  assert.deepEqual(bare.missing_facts.map(f => f.fact),
    ["exact_caller_census", "shadow_comparison_clean_run"]);

  const claimed = v5J102MigrationReadiness({
    tenant: ORGANIZATION_TENANT_ID, shadow_runs: 500,
    caller_census: { census_ref: "census-synthetic-1", enumerated_callers: 12,
      migrated_callers: 12, attested_by: "joe", attested_at: T.late },
  });
  assert.equal(claimed.decision, "refuse");
  assert.equal(claimed.reason_id, "caller_census_supplied_but_unverified");
  assert.equal(claimed.may_retire_callers, false);
  assert.equal(claimed.caller_census_verified, false);
  assert.equal(claimed.migration_complete, false);
});

test("Q081: a CLEAN shadow run removes only its own missing fact and still retires nobody", () => {
  const run = over => ({ run_digest: D(7), compared_rows: 3, matching_rows: 3,
    differing_rows: 0, unlinked_rows: 0, many_to_one_subjects: 0,
    subjects_without_legacy_row: 0, snapshot_current: true, clean: true, ...over });
  const clean = v5J102MigrationReadiness({ tenant: ORGANIZATION_TENANT_ID, latest_shadow_run: run() });
  assert.equal(clean.shadow_comparison_clean_run, true);
  assert.equal(clean.may_retire_callers, false, "a clean shadow is not a caller census");
  assert.equal(clean.migration_complete, false);
  assert.deepEqual(clean.missing_facts.map(f => f.fact), ["exact_caller_census"]);
  assert.equal(clean.latest_shadow_run.run_digest, D(7));

  for (const over of [
    { matching_rows: 2, differing_rows: 1, clean: false },
    { unlinked_rows: 1, clean: false },
    { compared_rows: 0, matching_rows: 0, clean: false },
    { many_to_one_subjects: 1, clean: false },
    { subjects_without_legacy_row: 1, clean: false },
    { snapshot_current: false },
  ]) {
    const dirty = v5J102MigrationReadiness({ tenant: ORGANIZATION_TENANT_ID, latest_shadow_run: run(over) });
    assert.equal(dirty.shadow_comparison_clean_run, false, JSON.stringify(over));
    assert.deepEqual(dirty.missing_facts.map(f => f.fact),
      ["exact_caller_census", "shadow_comparison_clean_run"]);
    assert.equal(dirty.missing_facts[1].produced_by, "ops.j102_run_migration_shadow");
  }

  assert.throws(() => v5J102MigrationReadiness({ tenant: ORGANIZATION_TENANT_ID,
    latest_shadow_run: run({ unlinked_rows: 4, clean: true }) }),
  e => e.code === "shadow_run_clean_flag_contradicts_counts");
  assert.throws(() => v5J102MigrationReadiness({ tenant: ORGANIZATION_TENANT_ID,
    latest_shadow_run: run({ many_to_one_subjects: 2, clean: true }) }),
  e => e.code === "shadow_run_clean_flag_contradicts_counts");
  const stale = v5J102MigrationReadiness({ tenant: ORGANIZATION_TENANT_ID,
    latest_shadow_run: run({ snapshot_current: false }) });
  assert.equal(stale.latest_shadow_run.clean, true, "the run itself was clean");
  assert.match(stale.missing_facts[1].why, /snapshot that has since moved/);
  assert.throws(() => v5J102MigrationReadiness({ tenant: ORGANIZATION_TENANT_ID,
    latest_shadow_run: run({ snapshot_current: undefined }) }));
  assert.throws(() => v5J102MigrationReadiness({ tenant: ORGANIZATION_TENANT_ID,
    latest_shadow_run: run({ matching_rows: 1 }) }),
  e => e.code === "shadow_run_counts_inconsistent");
  assert.throws(() => v5J102MigrationReadiness({ tenant: ORGANIZATION_TENANT_ID,
    latest_shadow_run: run({ run_digest: "not-a-digest" }) }),
  e => e.code === "invalid_digest");
});


test("Journey 1 end to end: Prospect through Client, Assignment, multiple LOIs, pending Deal and close", () => {
  const established = evaluate({
    transition_id: "establish-client-and-engagement",
    subject: relationship(),
    evidence: [documentEvidence("signed_engagement_letter")],
    declared: { new_subject_id: "eng-synthetic-1" },
  });
  assert.equal(established.decision, "allow");
  const client = established.proposed_state.relationship;
  const eng = established.proposed_state.engagement;

  const opened = evaluate({
    transition_id: "open-assignment",
    subject: assignment({ assignment_phase: "research" }),
    related: { relationship: client, engagement: eng },
    evidence: [recordEvidence("search_initiation")],
    declared: { mandate_scope: "search" },
  });
  let asg = opened.proposed_state.assignment;

  const negotiations = [];
  for (const n of [1, 2, 3]) {
    const submitted = evaluate({
      transition_id: "record-loi-submission",
      subject: negotiation({ subject_id: `neg-synthetic-${n}`, property_id: `prop-synthetic-${n}` }),
      related: { assignment: asg },
      evidence: [documentEvidence("submitted_loi", {}, { subject_id: `neg-synthetic-${n}` })],
    });
    assert.equal(submitted.decision, "allow");
    assert.equal(submitted.creates_deal, false, "no LOI submission creates a Deal");
    asg = submitted.proposed_state.assignment;
    negotiations.push(submitted.proposed_state.property_negotiation);
  }
  assert.equal(asg.open_negotiation_count, 3);

  const accepted = [0, 1].map(i => {
    const answer = evaluate({
      transition_id: "record-loi-acceptance",
      subject: negotiations[i],
      evidence: [artifactEvidence("counterparty_loi_acceptance", {},
        { subject_id: negotiations[i].subject_id })],
    });
    assert.equal(answer.creates_deal, false, "no acceptance creates a Deal");
    return answer.proposed_state.property_negotiation;
  });

  const committed = evaluate({
    transition_id: "commit-winning-property",
    subject: asg,
    related: { property_negotiation: accepted[0] },
    evidence: [recordEvidence("winner_selection_commitment")],
    declared: { instrument_kind: "lease", new_deal_id: "deal-synthetic-1" },
  });
  assert.equal(committed.decision, "allow");
  assert.equal(committed.alternative_negotiations_modified, 0,
    "the losing negotiations are retained untouched");
  const pending = committed.proposed_state.deal;
  assert.equal(pending.deal_state, "pending");

  const executed = evaluate({
    transition_id: "record-lease-execution", subject: pending,
    evidence: [documentEvidence("executed_lease")],
  });
  assert.equal(executed.proposed_state.deal.execution_state, "executed");
  assert.equal(executed.proposed_state.deal.deal_state, "pending");

  const closed = evaluate({
    transition_id: "record-deal-closing", subject: executed.proposed_state.deal,
    evidence: [recordEvidence("final_closing_settlement", { closing_date: T.late })],
  });
  assert.equal(closed.proposed_state.deal.deal_state, "closed");
  assert.equal(closed.proposed_state.deal.closing_date, T.late);
  assert.equal(closed.proposed_state.deal.invoice_state, "not_invoiced");
  assert.equal(closed.proposed_state.deal.payment_state, "unpaid");
  assert.equal(closed.proposed_state.deal.completion_state, "open");
});

test("the failed-deal branch returns the Assignment to work without touching the Client", () => {
  const client = relationship({ relationship_state: "client", active_engagement_count: 1 });
  const eng = engagement();
  const committedAssignment = assignment({
    assignment_phase: "committed", open_negotiation_count: 2,
    selected_property_id: "prop-synthetic-1",
    active_lease_draft_target_id: "prop-synthetic-1",
    pending_deal_id: "deal-synthetic-1",
  });
  const cancelled = evaluate({
    transition_id: "cancel-pending-deal",
    subject: deal(),
    related: { assignment: committedAssignment, engagement: eng, relationship: client },
    evidence: [recordEvidence("deal_failure_record",
      { reason: "synthetic fixture: terms could not be agreed" })],
    declared: { return_phase: "negotiation" },
  });
  const reopened = cancelled.proposed_state.assignment;

  const resubmitted = evaluate({
    transition_id: "record-loi-submission",
    subject: negotiation({ subject_id: "neg-synthetic-9", property_id: "prop-synthetic-9" }),
    related: { assignment: reopened },
    evidence: [documentEvidence("submitted_loi", {}, { subject_id: "neg-synthetic-9" })],
  });
  assert.equal(resubmitted.decision, "allow");
  assert.equal(cancelled.proposed_state.relationship, undefined,
    "a cancelled deal writes no client row");
  assert.equal(cancelled.relationship_rewritten, false);
  assert.equal(cancelled.client_relationship_preserved, true);
  assert.equal(cancelled.relationship_chain_verified, true);
  assert.equal(cancelled.relationship_state, "client");
});

test("M4: a client outside the deal's own chain refuses rather than being rewritten", () => {
  const stranger = relationship({
    subject_id: "rel-synthetic-unrelated", relationship_state: "client",
    active_engagement_count: 1,
  });
  const committedAssignment = assignment({
    assignment_phase: "committed", open_negotiation_count: 2,
    pending_deal_id: "deal-synthetic-1",
  });
  const failure = recordEvidence("deal_failure_record",
    { reason: "synthetic fixture: terms could not be agreed" });

  const unchained = evaluate({
    transition_id: "cancel-pending-deal", subject: deal(),
    related: { assignment: committedAssignment, relationship: stranger },
    evidence: [failure], declared: { return_phase: "negotiation" },
  });
  assert.equal(unchained.decision, "refuse");
  assert.equal(unchained.reason_id, "relationship_chain_not_loaded");

  const wrongChain = evaluate({
    transition_id: "cancel-pending-deal", subject: deal(),
    related: {
      assignment: committedAssignment,
      engagement: engagement(),
      relationship: stranger,
    },
    evidence: [failure], declared: { return_phase: "negotiation" },
  });
  assert.equal(wrongChain.decision, "refuse");
  assert.equal(wrongChain.reason_id, "relationship_not_in_verified_chain");
  assert.equal(wrongChain.proposed_state, null);
});


test("the three initializations declare their contracts, cite settled decisions and carry no evidence", () => {
  assert.deepEqual(V5_J102_INITIALIZATION_IDS,
    ["initialize-assignment", "initialize-property-negotiation",
      "initialize-prospect-relationship"]);
  for (const id of V5_J102_INITIALIZATION_IDS) {
    const contract = v5J102InitializationContract(id);
    assert.equal(contract.initialization_id, id);
    assert.equal(contract.requires_evidence, false);
    assert.equal(contract.advances_lifecycle_state, false);
    assert.equal(contract.establishes_client_status, false);
    assert.equal(contract.creates_deal, false);
    assert.equal(contract.creates_or_activates_tour, false);
    assert.equal(contract.transition_prerequisites_bypassed, false);
    assert.ok(contract.decision_refs.length > 0);
    for (const ref of contract.decision_refs) {
      assert.ok(V5_J102_SETTLED_DECISION_IDS.includes(ref), `${id} cites ${ref}`);
    }
    assert.ok(contract.permitted_actor_classes.length > 0);
    if (contract.parent_subject_kind !== null) {
      assert.ok(contract.required_context.some(r => r.subject === contract.parent_subject_kind),
        `${id} checks the ${contract.parent_subject_kind} it creates under`);
    }
    assert.ok(!V5_J102_TRANSITION_IDS.includes(id));
  }
  assert.deepEqual(V5_J102_INITIALIZED_SUBJECT_KINDS,
    ["assignment", "property_negotiation", "relationship"]);
  assert.ok(!V5_J102_INITIALIZED_SUBJECT_KINDS.includes("engagement"));
  assert.ok(!V5_J102_INITIALIZED_SUBJECT_KINDS.includes("deal"));
  assert.equal(new Set(V5_J102_INITIALIZED_SUBJECT_KINDS).size,
    V5_J102_INITIALIZED_SUBJECT_KINDS.length,
    "each subject kind has exactly one creation door");
  assert.equal(Object.keys(v5J102Projection().subject_creation_doors.by_initialization).length,
    V5_J102_INITIALIZATION_IDS.length,
    "and the projection reports one door per initialization rather than collapsing two");
});

test("a created row is the EARLIEST declared state of its kind, and nothing later", () => {
  const vocabulary = {
    relationship_state: V5_J102_RELATIONSHIP_STATES,
    assignment_phase: V5_J102_ASSIGNMENT_PHASES,
    negotiation_state: V5_J102_NEGOTIATION_STATES,
  };
  const seen = [];
  for (const id of V5_J102_INITIALIZATION_IDS) {
    const contract = v5J102InitializationContract(id);
    for (const [axis, value] of Object.entries(contract.initial_state)) {
      if (vocabulary[axis] === undefined) continue;
      assert.equal(value, vocabulary[axis][0],
        `${id} creates a ${contract.subject_kind} at ${axis} "${value}", and the earliest is "${vocabulary[axis][0]}"`);
      seen.push(axis);
    }
  }
  assert.deepEqual(seen.sort(),
    ["assignment_phase", "negotiation_state", "relationship_state"]);
  const assignment = v5J102InitializationContract("initialize-assignment").initial_state;
  assert.equal(assignment.open_negotiation_count, 0);
  for (const field of ["selected_property_id", "active_lease_draft_target_id",
    "pending_deal_id", "multi_target_exception_ref"]) {
    assert.equal(assignment[field], null, `a created assignment holds no ${field}`);
  }
});

test("Q069/Q077: an initialized relationship is a PROSPECT, and it is not a Client", () => {
  const created = initialize({
    initialization_id: "initialize-prospect-relationship",
    declared: { new_subject_id: "rel-synthetic-1" },
  });
  assert.equal(created.decision, "allow");
  assert.equal(created.reason_id, "prospect_relationship_initialized");
  assert.deepEqual(created.created_state, {
    subject_kind: "relationship", subject_id: "rel-synthetic-1",
    relationship_state: "prospect", active_engagement_count: 0, party_id: null,
  });
  assert.equal(created.establishes_client_status, false);
  assert.equal(created.advances_lifecycle_state, false);
  assert.equal(created.transition_prerequisites_bypassed, false);
  assert.equal(created.requires_evidence, false);
  assert.equal(created.evidence_supplied, 0);
  assert.equal(created.applied, false);
  assert.equal(created.events.length, 1);
  assert.equal(created.events[0].event_kind, "relationship_initialized");
  assert.equal(created.events[0].subject_id, "rel-synthetic-1");
  assert.equal(created.events[0].relationship_state, "prospect");
  assert.equal(created.creates_or_activates_tour, false);

  const opened = evaluate({
    transition_id: "open-assignment",
    subject: assignment({ assignment_phase: "research" }),
    related: { relationship: created.created_state, engagement: engagement() },
    evidence: [recordEvidence("search_initiation")],
    declared: { mandate_scope: "search" },
  });
  assert.equal(opened.decision, "refuse");
  assert.equal(opened.reason_id, "client_status_required");
});

test("Q077: an Assignment is initialized only under an ACTIVE engagement held by a CLIENT", () => {
  const ok = initialize({
    initialization_id: "initialize-assignment",
    related: { engagement: engagement(), relationship: client() },
    declared: { new_subject_id: "asg-synthetic-1" },
  });
  assert.equal(ok.decision, "allow");
  assert.equal(ok.created_state.assignment_phase, "research",
    "the earliest declared phase, so open-assignment still declares the scope");
  assert.equal(ok.created_state.engagement_id, "eng-synthetic-1");
  assert.equal(ok.created_state.open_negotiation_count, 0);
  assert.equal(ok.created_state.selected_property_id, null);
  assert.equal(ok.created_state.active_lease_draft_target_id, null);
  assert.equal(ok.created_state.pending_deal_id, null);
  assert.equal(ok.parent_subject_kind, "engagement");
  assert.deepEqual(ok.context_verified, ["engagement", "relationship"]);
  assert.equal(ok.opens_assignment, false);

  const cases = [
    [{ relationship: client() }, "engagement_not_loaded"],
    [{ engagement: engagement() }, "relationship_not_loaded"],
    [{ engagement: engagement({ engagement_state: "expired" }), relationship: client() },
      "engagement_not_active"],
    [{ engagement: engagement({ engagement_state: "terminated" }), relationship: client() },
      "engagement_not_active"],
    [{ engagement: engagement(), relationship: relationship() }, "client_status_required"],
    [{ engagement: engagement(), relationship: client({ relationship_state: "client_ended" }) },
      "client_status_required"],
    [{ engagement: engagement({ relationship_id: "rel-synthetic-other" }), relationship: client() },
      "relationship_not_in_verified_chain"],
  ];
  for (const [related, reason_id] of cases) {
    const refused = initialize({
      initialization_id: "initialize-assignment", related,
      declared: { new_subject_id: "asg-synthetic-1" },
    });
    assert.equal(refused.decision, "refuse", `${reason_id} refuses`);
    assert.equal(refused.reason_id, reason_id);
    assert.equal(refused.created_state, null);
    assert.deepEqual(refused.events, []);
  }
});

test("Q095: a negotiation is initialized only under an assignment that is still OPEN", () => {
  const ok = initialize({
    initialization_id: "initialize-property-negotiation",
    related: { assignment: assignment() },
    declared: { new_subject_id: "neg-synthetic-1", property_id: "prop-synthetic-1" },
  });
  assert.equal(ok.decision, "allow");
  assert.deepEqual(ok.created_state, {
    subject_kind: "property_negotiation", subject_id: "neg-synthetic-1",
    assignment_id: "asg-synthetic-1", property_id: "prop-synthetic-1",
    negotiation_state: "loi_drafted",
  });
  assert.equal(ok.submits_loi, false);
  assert.equal(ok.creates_deal, false);

  const committedAssignment = assignment({
    assignment_phase: "committed", selected_property_id: "prop-synthetic-1",
    pending_deal_id: "deal-synthetic-1",
  });
  const refused = initialize({
    initialization_id: "initialize-property-negotiation",
    related: { assignment: committedAssignment },
    declared: { new_subject_id: "neg-synthetic-2", property_id: "prop-synthetic-2" },
  });
  assert.equal(refused.decision, "refuse");
  assert.equal(refused.reason_id, "assignment_already_committed");

  const concluded = initialize({
    initialization_id: "initialize-property-negotiation",
    related: { assignment: assignment({ assignment_phase: "concluded" }) },
    declared: { new_subject_id: "neg-synthetic-3", property_id: "prop-synthetic-3" },
  });
  assert.equal(concluded.decision, "refuse");
  assert.equal(concluded.reason_id, "assignment_phase_not_open");
  assert.equal(concluded.observed, "concluded");

  for (const n of [1, 2, 3]) {
    const drafted = initialize({
      initialization_id: "initialize-property-negotiation",
      related: { assignment: assignment({ open_negotiation_count: n }) },
      declared: { new_subject_id: `neg-synthetic-${n}`, property_id: `prop-synthetic-${n}` },
    });
    assert.equal(drafted.decision, "allow");
  }
});

test("OWNER RULING (b): at most one ACTIVE negotiation per assignment and property", () => {
  const base = {
    initialization_id: "initialize-property-negotiation",
    related: { assignment: assignment() },
    declared: { new_subject_id: "neg-synthetic-9", property_id: "prop-synthetic-1" },
  };
  const blocked = initialize({ ...base, active_negotiations_for_property: ["neg-synthetic-1"] });
  assert.equal(blocked.decision, "refuse");
  assert.equal(blocked.reason_id, "active_negotiation_exists_for_property");
  assert.deepEqual(blocked.active_negotiation_ids, ["neg-synthetic-1"]);
  assert.equal(blocked.created_state, null);
  const blind = evaluateLifecycleInitialization({ tenant: ORGANIZATION_TENANT_ID, actor: PARTNER,
    now: NOW, ...base });
  assert.equal(blind.decision, "refuse");
  assert.equal(blind.reason_id, "active_negotiation_census_absent");
  assert.equal(initialize({ ...base, active_negotiations_for_property: [] }).decision, "allow");
  assert.throws(() => initialize({ initialization_id: "initialize-prospect-relationship",
    declared: { new_subject_id: "rel-synthetic-9" }, active_negotiations_for_property: [] }),
  e => e.code === "unknown_field");
  assert.deepEqual([...V5_J102_ACTIVE_NEGOTIATION_STATES],
    ["loi_drafted", "loi_submitted", "loi_countered", "loi_accepted"]);
});

test("OWNER RULING (a): a prospect may carry an OPTIONAL CARR party reference", () => {
  const linked = initialize({ initialization_id: "initialize-prospect-relationship",
    declared: { new_subject_id: "rel-synthetic-9", party_id: "0b8a3c1e-7d2f-4e5a-9c61-2f3d4e5a6b7c" } });
  assert.equal(linked.decision, "allow");
  assert.equal(linked.created_state.party_id, "0b8a3c1e-7d2f-4e5a-9c61-2f3d4e5a6b7c");
  const unlinked = initialize({ initialization_id: "initialize-prospect-relationship",
    declared: { new_subject_id: "rel-synthetic-10" } });
  assert.equal(unlinked.decision, "allow");
  assert.equal(unlinked.created_state.party_id, null, "optional: a relationship with no known party");
  const misplaced = initialize({ initialization_id: "initialize-assignment",
    related: { engagement: engagement(), relationship: client() },
    declared: { new_subject_id: "asg-synthetic-9", party_id: "party-1" } });
  assert.equal(misplaced.decision, "refuse");
  assert.equal(misplaced.reason_id, "declared_identifier_not_used");
  assert.deepEqual(v5J102InitializationContract("initialize-prospect-relationship")
    .optional_declared_identifiers, ["party_id"]);
});

test("an initialization accepts IDENTIFIERS and nothing else — a state, an approval or evidence refuses", () => {
  for (const bad of [
    { relationship_state: "client" }, { assignment_phase: "committed" },
    { negotiation_state: "loi_accepted" }, { deal_state: "pending" },
    { open_negotiation_count: 4 }, { selected_property_id: "prop-synthetic-1" },
  ]) {
    assert.throws(() => initialize({
      initialization_id: "initialize-prospect-relationship",
      declared: { new_subject_id: "rel-synthetic-1", ...bad },
    }), error => error instanceof V5J102Error &&
      ["unknown_field", "caller_asserted_fact_refused"].includes(error.code));
  }
  assert.throws(() => initialize({
    initialization_id: "initialize-prospect-relationship",
    declared: { new_subject_id: "rel-synthetic-1" },
    evidence: [documentEvidence("signed_engagement_letter")],
  }), error => error instanceof V5J102Error && error.code === "unknown_field");
  assert.throws(() => initialize({
    initialization_id: "initialize-prospect-relationship",
    declared: { new_subject_id: "rel-synthetic-1" }, acting_as: "joe",
  }), error => error instanceof V5J102Error && error.code === "caller_authority_field_refused");
  assert.throws(() => initialize({
    initialization_id: "initialize-prospect-relationship",
    declared: { new_subject_id: "rel-synthetic-1" }, approved_by: "joe",
  }), error => error instanceof V5J102Error && error.code === "caller_authority_field_refused");
  for (const id of ["initialize-deal", "seed-subject", "commit-winning-property",
    "establish-client-and-engagement"]) {
    assert.throws(() => initialize({
      initialization_id: id, declared: { new_subject_id: "rel-synthetic-1" },
    }), error => error instanceof V5J102Error && error.code === "unknown_initialization");
  }
});

test("the declared identifiers are required where used and refused where not", () => {
  const missingId = initialize({
    initialization_id: "initialize-prospect-relationship", declared: {},
  });
  assert.equal(missingId.decision, "refuse");
  assert.equal(missingId.reason_id, "declared_subject_id_required");

  const missingProperty = initialize({
    initialization_id: "initialize-property-negotiation",
    related: { assignment: assignment() },
    declared: { new_subject_id: "neg-synthetic-1" },
  });
  assert.equal(missingProperty.decision, "refuse");
  assert.equal(missingProperty.reason_id, "declared_identifier_required");
  assert.equal(missingProperty.missing_declared_identifier, "property_id");

  const unused = initialize({
    initialization_id: "initialize-prospect-relationship",
    declared: { new_subject_id: "rel-synthetic-1", property_id: "prop-synthetic-1" },
  });
  assert.equal(unused.decision, "refuse");
  assert.equal(unused.reason_id, "declared_identifier_not_used");
  assert.equal(unused.unexpected_declared_identifier, "property_id");

  assert.throws(() => initialize({
    initialization_id: "initialize-prospect-relationship",
    declared: { new_subject_id: "rel synthetic 1" },
  }), error => error instanceof V5J102Error && error.code === "invalid_identifier");
});

test("both admitted actor classes may initialize, and an unregistered class cannot", () => {
  for (const actor of [PARTNER, AGENT]) {
    const created = initialize({
      actor, initialization_id: "initialize-prospect-relationship",
      declared: { new_subject_id: "rel-synthetic-1" },
    });
    assert.equal(created.decision, "allow", `${actor.slug} may create a prospect`);
    assert.equal(created.actor_slug, actor.slug);
  }
  assert.throws(() => initialize({
    actor: { ...PARTNER, authorization_class: "probe" },
    initialization_id: "initialize-prospect-relationship",
    declared: { new_subject_id: "rel-synthetic-1" },
  }), error => error instanceof V5J102Error && error.code === "unknown_actor_class");
  assert.throws(() => initialize({
    actor: { ...PARTNER, derived_by: "caller_supplied" },
    initialization_id: "initialize-prospect-relationship",
    declared: { new_subject_id: "rel-synthetic-1" },
  }), error => error instanceof V5J102Error && error.code === "actor_not_server_derived");
});

test("Journey 1 from its FIRST row: initialization feeds the existing transitions unchanged", () => {
  const prospect = initialize({
    initialization_id: "initialize-prospect-relationship",
    declared: { new_subject_id: "rel-synthetic-1" },
  }).created_state;
  assert.equal(prospect.relationship_state, "prospect");

  const established = evaluate({
    transition_id: "establish-client-and-engagement",
    subject: prospect,
    evidence: [documentEvidence("signed_engagement_letter")],
    declared: { new_subject_id: "eng-synthetic-1" },
  });
  assert.equal(established.decision, "allow");
  const clientRow = established.proposed_state.relationship;
  const eng = established.proposed_state.engagement;
  assert.equal(clientRow.relationship_state, "client");

  const asgCreated = initialize({
    initialization_id: "initialize-assignment",
    related: { engagement: eng, relationship: clientRow },
    declared: { new_subject_id: "asg-synthetic-1" },
  });
  assert.equal(asgCreated.decision, "allow");
  assert.equal(asgCreated.created_state.assignment_phase, "research");

  const unevidenced = evaluate({
    transition_id: "open-assignment", subject: asgCreated.created_state,
    related: { relationship: clientRow, engagement: eng },
    evidence: [recordEvidence("diligence_outcome",
      {}, { subject_kind: "deal", subject_id: "deal-synthetic-1" })],
    declared: { mandate_scope: "search" },
  });
  assert.equal(unevidenced.decision, "refuse");
  assert.equal(unevidenced.reason_id, "required_evidence_absent");

  const opened = evaluate({
    transition_id: "open-assignment", subject: asgCreated.created_state,
    related: { relationship: clientRow, engagement: eng },
    evidence: [recordEvidence("search_initiation")],
    declared: { mandate_scope: "search" },
  });
  assert.equal(opened.decision, "allow");
  const asg = opened.proposed_state.assignment;
  assert.equal(asg.assignment_phase, "search");

  const drafts = [1, 2].map(n => initialize({
    initialization_id: "initialize-property-negotiation",
    related: { assignment: asg },
    declared: { new_subject_id: `neg-synthetic-${n}`, property_id: `prop-synthetic-${n}` },
  }));
  for (const draft of drafts) {
    assert.equal(draft.decision, "allow");
    assert.equal(draft.created_state.negotiation_state, "loi_drafted");
  }

  const uncommittable = evaluate({
    transition_id: "commit-winning-property", subject: asg,
    related: { property_negotiation: drafts[0].created_state },
    evidence: [recordEvidence("winner_selection_commitment")],
    declared: { instrument_kind: "lease", new_deal_id: "deal-synthetic-1" },
  });
  assert.equal(uncommittable.decision, "refuse");
  assert.equal(uncommittable.reason_id, "winning_negotiation_not_accepted");

  const submitted = evaluate({
    transition_id: "record-loi-submission",
    subject: drafts[0].created_state, related: { assignment: asg },
    evidence: [documentEvidence("submitted_loi", {}, { subject_id: "neg-synthetic-1" })],
  });
  assert.equal(submitted.decision, "allow");
  assert.equal(submitted.creates_deal, false, "no LOI submission creates a Deal");
  assert.equal(submitted.proposed_state.assignment.open_negotiation_count, 1);
  assert.equal(submitted.proposed_state.property_negotiation.negotiation_state, "loi_submitted");
});


const GUARD_REFUSAL = Object.freeze({
  subject_kind: "subject_kind_mismatch",
  actor_class: "actor_class_not_permitted",
  prerequisite: "prerequisite_not_met",
  instrument_kind: "instrument_kind_not_permitted",
  required_evidence: "required_evidence_absent",
});

const EXPECTED_CELLS = 411;
const EXPECTED_ADMITTING = 82;
const EXPECTED_REFUSING = 329;

const SUBJECT_FIXTURE = Object.freeze({
  relationship, engagement, assignment,
  property_negotiation: negotiation, deal,
});

const BASELINE = Object.freeze({
  "establish-client-and-engagement": {
    subject: relationship(),
    evidence: [documentEvidence("signed_engagement_letter")],
    declared: { new_subject_id: "eng-synthetic-1" },
  },
  "open-assignment": {
    subject: assignment({ assignment_phase: "search" }),
    related: { relationship: client(), engagement: engagement() },
    evidence: [recordEvidence("search_initiation")],
    declared: { mandate_scope: "search" },
  },
  "record-loi-submission": {
    subject: negotiation({ negotiation_state: "loi_drafted" }),
    related: { assignment: assignment({ assignment_phase: "search" }) },
    evidence: [documentEvidence("submitted_loi")],
  },
  "record-loi-acceptance": {
    subject: negotiation({ negotiation_state: "loi_submitted" }),
    evidence: [artifactEvidence("counterparty_loi_acceptance")],
  },
  "commit-winning-property": {
    subject: assignment({ assignment_phase: "search" }),
    related: { property_negotiation: negotiation({ negotiation_state: "loi_accepted" }) },
    evidence: [recordEvidence("winner_selection_commitment")],
    declared: { instrument_kind: "lease", new_deal_id: "deal-synthetic-1" },
  },
  "record-lease-execution": {
    subject: deal({ instrument_kind: "lease" }),
    evidence: [documentEvidence("executed_lease")],
  },
  "record-purchase-contract-execution": {
    subject: deal({ instrument_kind: "purchase" }),
    evidence: [documentEvidence("signed_purchase_contract")],
  },
  "record-diligence-outcome": {
    subject: deal({ instrument_kind: "purchase", execution_state: "executed",
      diligence_state: "in_progress" }),
    evidence: [recordEvidence("diligence_outcome")],
    declared: { diligence_result: "satisfied" },
  },
  "record-deal-closing": {
    subject: deal({ execution_state: "executed" }),
    evidence: [recordEvidence("final_closing_settlement", { closing_date: T.late })],
  },
  "cancel-pending-deal": {
    subject: deal(),
    related: {
      assignment: assignment({ assignment_phase: "committed", open_negotiation_count: 2,
        selected_property_id: "prop-synthetic-1",
        active_lease_draft_target_id: "prop-synthetic-1",
        pending_deal_id: "deal-synthetic-1" }),
      engagement: engagement(),
      relationship: client(),
    },
    evidence: [recordEvidence("deal_failure_record",
      { reason: "synthetic fixture: terms could not be agreed" })],
    declared: { return_phase: "negotiation" },
  },
  "record-commission-agreement": {
    subject: deal(),
    evidence: [documentEvidence("commission_agreement")],
  },
  "record-invoice-issued": {
    subject: deal(),
    evidence: [recordEvidence("invoice_issued")],
  },
  "record-payment": {
    subject: deal(),
    evidence: [recordEvidence("payment_received")],
    declared: { payment_level: "paid" },
  },
  "record-completion": {
    subject: deal(),
    evidence: [recordEvidence("completion_recorded")],
  },
});

const EVIDENCE_FIXTURE = Object.freeze({
  approved_representation_equivalent: () => approvalEvidence("approved_representation_equivalent"),
  commission_agreement: () => documentEvidence("commission_agreement"),
  completion_recorded: () => recordEvidence("completion_recorded"),
  counterparty_loi_acceptance: () => artifactEvidence("counterparty_loi_acceptance"),
  deal_failure_record: () => recordEvidence("deal_failure_record",
    { reason: "synthetic fixture: terms could not be agreed" }),
  diligence_outcome: () => recordEvidence("diligence_outcome"),
  executed_lease: () => documentEvidence("executed_lease"),
  final_closing_settlement: () => recordEvidence("final_closing_settlement",
    { closing_date: T.late }),
  invoice_issued: () => recordEvidence("invoice_issued"),
  manual_correction: () => recordEvidence("manual_correction",
    { reason: "synthetic fixture: a correction nobody is performing here" }),
  multi_target_exception_approval: () => approvalEvidence("multi_target_exception_approval"),
  payment_received: () => recordEvidence("payment_received"),
  search_initiation: () => recordEvidence("search_initiation"),
  signed_engagement_letter: () => documentEvidence("signed_engagement_letter"),
  signed_purchase_contract: () => documentEvidence("signed_purchase_contract"),
  submitted_loi: () => documentEvidence("submitted_loi"),
  winner_selection_commitment: () => recordEvidence("winner_selection_commitment"),
});

const drive = (transition_id,
  { subject_over = null, subject = null, actor = PARTNER, evidence = null } = {}) => {
  const baseline = BASELINE[transition_id];
  return evaluate({
    transition_id,
    actor,
    subject: subject ?? (subject_over === null
      ? baseline.subject : { ...baseline.subject, ...subject_over }),
    ...(baseline.related === undefined ? {} : { related: baseline.related }),
    evidence: evidence ?? baseline.evidence,
    ...(baseline.declared === undefined ? {} : { declared: baseline.declared }),
  });
};

test("the truth table enumerates every guard of every transition, and the totals are the hand count", () => {
  const table = v5J102TransitionTruthTable();
  assert.equal(table.cell_count, EXPECTED_CELLS);
  assert.equal(table.admitting_cell_count, EXPECTED_ADMITTING);
  assert.equal(table.refusing_cell_count, EXPECTED_REFUSING);
  assert.equal(table.cells.length, EXPECTED_CELLS);
  assert.deepEqual(table.guard_order,
    ["subject_kind", "actor_class", "prerequisite", "instrument_kind", "required_evidence"]);

  assert.deepEqual([...new Set(table.cells.map(c => c.transition_id))].sort(),
    [...V5_J102_TRANSITION_IDS]);
  for (const cell of table.cells) {
    assert.equal(cell.refusal_reason_id,
      cell.guard_admits ? null : GUARD_REFUSAL[cell.guard],
      `${cell.transition_id}/${cell.guard}/${cell.value}`);
    assert.equal(cell.axis_position === null, cell.guard !== "prerequisite",
      `${cell.transition_id}/${cell.guard} axis_position`);
  }

  for (const id of V5_J102_TRANSITION_IDS) {
    const contract = v5J102TransitionContract(id);
    const own = table.cells.filter(c => c.transition_id === id);
    assert.deepEqual(own.filter(c => c.guard === "subject_kind").map(c => c.value),
      ["relationship", "engagement", "assignment", "property_negotiation", "deal"],
      `${id} enumerates every subject kind`);
    assert.deepEqual(own.filter(c => c.guard === "actor_class").map(c => c.value),
      ["verified_partner", "sponsored_agent"], `${id} enumerates every actor class`);
    assert.deepEqual(
      [...new Set(own.filter(c => c.guard === "prerequisite").map(c => c.axis))],
      Object.keys(contract.prerequisites), `${id} enumerates every prerequisite axis, in order`);
    assert.deepEqual(own.filter(c => c.guard === "instrument_kind").map(c => c.value),
      contract.instrument_kinds === null ? [] : ["lease", "purchase", "renewal", "amendment"],
      `${id} enumerates every instrument kind, or declares none`);
    assert.deepEqual(own.filter(c => c.guard === "required_evidence").map(c => c.value),
      [...V5_J102_EVIDENCE_KINDS], `${id} enumerates every registered evidence kind`);
    assert.deepEqual(
      own.filter(c => c.guard === "required_evidence" && c.guard_admits).map(c => c.value).sort(),
      contract.required_evidence_alternatives.filter(set => set.length === 1)
        .map(set => set[0]).sort(),
      `${id} admits exactly its single-record evidence alternatives`);
    for (const [axis, permitted] of Object.entries(contract.prerequisites)) {
      assert.deepEqual(
        own.filter(c => c.guard === "prerequisite" && c.axis === axis && c.guard_admits)
          .map(c => c.value),
        permitted, `${id} admits exactly the declared ${axis} states`);
    }
  }
});

test("every baseline the truth table is driven from is a real ALLOW", () => {
  assert.deepEqual(Object.keys(BASELINE).sort(), [...V5_J102_TRANSITION_IDS]);
  for (const id of V5_J102_TRANSITION_IDS) {
    const answer = drive(id);
    assert.equal(answer.decision, "allow",
      `${id} baseline must allow (got ${answer.reason_id})`);
  }
});

test("the truth table's REFUSING cells all refuse, for the stated guard and no other", () => {
  const table = v5J102TransitionTruthTable();
  const refusing = table.cells.filter(c => !c.guard_admits);
  assert.equal(refusing.length, EXPECTED_REFUSING);

  for (const cell of refusing) {
    const contract = v5J102TransitionContract(cell.transition_id);
    const where = `${cell.transition_id}/${cell.guard}/${cell.axis}=${cell.value}`;
    const answer =
      cell.guard === "subject_kind"
        ? drive(cell.transition_id, { subject: SUBJECT_FIXTURE[cell.value]() })
        : cell.guard === "actor_class"
          ? drive(cell.transition_id, { actor: cell.value === "verified_partner" ? PARTNER : AGENT })
          : cell.guard === "required_evidence"
            ? drive(cell.transition_id, { evidence: [EVIDENCE_FIXTURE[cell.value]()] })
            : drive(cell.transition_id, { subject_over: { [cell.axis]: cell.value } });

    assert.equal(answer.decision, "refuse", where);
    assert.equal(answer.reason_id, GUARD_REFUSAL[cell.guard], where);
    assert.equal(answer.transition_id, cell.transition_id, where);

    if (cell.guard === "subject_kind") {
      assert.equal(answer.subject_kind, cell.value, where);
      assert.equal(answer.expected_subject_kind, contract.subject_kind, where);
    }
    if (cell.guard === "actor_class") {
      assert.equal(answer.actor_authorization_class, cell.value, where);
      assert.deepEqual(answer.permitted_actor_classes, contract.permitted_actor_classes, where);
    }
    if (cell.guard === "prerequisite") {
      assert.equal(answer.unmet_axis, cell.axis, where);
      assert.equal(answer.observed, cell.value, where);
      assert.deepEqual(answer.permitted, contract.prerequisites[cell.axis], where);
    }
    if (cell.guard === "instrument_kind") {
      assert.equal(answer.instrument_kind, cell.value, where);
      assert.deepEqual(answer.permitted_instrument_kinds, contract.instrument_kinds, where);
    }
    if (cell.guard === "required_evidence") {
      assert.deepEqual(answer.supplied_evidence_kinds, [cell.value], where);
      assert.deepEqual(answer.required_evidence_alternatives,
        contract.required_evidence_alternatives, where);
    }
  }
});

test("the truth table's ADMITTING cells are admitted, guard by guard, all the way to allow", () => {
  const table = v5J102TransitionTruthTable();
  const admitting = table.cells.filter(c => c.guard_admits);
  assert.equal(admitting.length, EXPECTED_ADMITTING);

  for (const cell of admitting) {
    const where = `${cell.transition_id}/${cell.guard}/${cell.axis}=${cell.value}`;
    const answer =
      cell.guard === "subject_kind"
        ? drive(cell.transition_id)
        : cell.guard === "actor_class"
          ? drive(cell.transition_id, { actor: cell.value === "verified_partner" ? PARTNER : AGENT })
          : cell.guard === "required_evidence"
            ? drive(cell.transition_id, { evidence: [EVIDENCE_FIXTURE[cell.value]()] })
            : drive(cell.transition_id, { subject_over: { [cell.axis]: cell.value } });
    assert.equal(answer.decision, "allow",
      `${where} must allow (got ${answer.reason_id})`);
  }
});

test("a prerequisite refusal names the FIRST unmet axis, in the declared order", () => {
  const contract = v5J102TransitionContract("record-deal-closing");
  assert.deepEqual(Object.keys(contract.prerequisites),
    ["deal_state", "execution_state", "closing_state"]);

  const bothWrong = drive("record-deal-closing",
    { subject_over: { deal_state: "cancelled", execution_state: "unexecuted" } });
  assert.equal(bothWrong.reason_id, "prerequisite_not_met");
  assert.equal(bothWrong.unmet_axis, "deal_state");

  const secondWrong = drive("record-deal-closing",
    { subject_over: { execution_state: "unexecuted" } });
  assert.equal(secondWrong.reason_id, "prerequisite_not_met");
  assert.equal(secondWrong.unmet_axis, "execution_state");
});

test("the four guards decide BEFORE evidence, so a guard refusal is never an evidence refusal", () => {
  const strangerEvidence = [recordEvidence("final_closing_settlement",
    { closing_date: T.late }, { subject_id: "deal-synthetic-stranger" })];
  const answer = evaluateLifecycleTransition({
    tenant: ORGANIZATION_TENANT_ID, actor: PARTNER, now: NOW,
    transition_id: "record-deal-closing",
    subject: deal({ deal_state: "cancelled", execution_state: "executed" }),
    evidence: strangerEvidence,
  });
  assert.equal(answer.reason_id, "prerequisite_not_met");
  assert.equal(answer.unmet_axis, "deal_state");

  const bound = evaluateLifecycleTransition({
    tenant: ORGANIZATION_TENANT_ID, actor: PARTNER, now: NOW,
    transition_id: "record-deal-closing",
    subject: deal({ execution_state: "executed" }),
    evidence: strangerEvidence,
  });
  assert.equal(bound.reason_id, "evidence_not_bound_to_subject");
});

test("every guard the table declares is live in both directions", () => {
  const table = v5J102TransitionTruthTable();
  for (const guard of table.guard_order) {
    const own = table.cells.filter(c => c.guard === guard);
    assert.ok(own.some(c => c.guard_admits), `${guard} admits some value`);
    assert.ok(own.some(c => !c.guard_admits), `${guard} refuses some value`);
  }
  assert.equal(v5J102Projection().transition_truth_table.cell_count, EXPECTED_CELLS);
});
