// DoctorCRE v5 slice V5-J102: the persistence tail for the CRE lifecycle.
//
// The pure kernel in cre-lifecycle.v5.js decides. This module is what makes
// those decisions RECORDS: it loads the stored subjects and the stored evidence,
// derives the actor and the instant from the server, runs the kernel unchanged,
// and hands the exact canonical preimages to the security-definer functions in
// ops/cre-lifecycle.candidate.sql that own the compare-and-swap, the append-only
// history and the evidence recheck under lock.
//
// THE DIVISION OF LABOUR, because it is the whole design:
//
//   KERNEL      Judges. Owns the vocabulary, the transition table, the refusal
//               matrix and the exact coupled facts. Unchanged and unwrapped:
//               this module imports it and reimplements none of it.
//   THIS MODULE Derives and loads. Resolves every caller REFERENCE into a
//               server-loaded record, takes the actor from the authenticated
//               transaction context and the instant from the server, refuses
//               caller authority and asserted-fact injection before any write,
//               and builds the envelopes.
//   DATABASE    Enforces. Recomputes every digest from committed bytes, refuses
//               a stale subject digest, RE-READS the exact evidence under the
//               lock it already holds and binds it to the subject the transition
//               advances, admits the transition against its own transcription of
//               the kernel's actor, subject and field contracts, refuses direct
//               DML, and returns the readback.
//
// WHY THE DATABASE RESTATES SOME OF THE KERNEL'S CONTRACT AT ALL. Everything this
// module checks it checks in JavaScript, and ops.j102_apply_transition is granted
// to carr_writer as well as carr_authority — so `authorityOnly` here and
// `permitted_actor_classes` in the kernel are controls on THIS caller and on
// nothing else. ops.j102_admission_policy() in the candidate SQL therefore
// carries the same contracts, and the parity tests in this slice's suite assert
// the two are equal contract by contract. That is a transcription with a
// comparison, not a second opinion: nothing in the SQL map decides anything the
// kernel does not already decide, and the day it drifts the suite fails.
//
// A CALLER SUPPLIES REFERENCES, NEVER FACTS. Every write operation below takes
// ids, digests and a small closed set of declared choices. It cannot supply a
// tenant, an actor, a clock, a subject, an evidence record, a document state, a
// decision or a digest of anything this module computes; the derived-field guard
// refuses each of those BY NAME, so the refusal says what was attempted rather
// than reporting a generic unknown field.
//
// WHICH SCHEMA THIS MODULE REQUIRES, said plainly. It calls ops.j102_* from
// ops/cre-lifecycle.candidate.sql — including ops.j102_initialize_subject, the
// separate narrow writer the three initialization operations use — which is an
// UNNUMBERED CANDIDATE and is not applied anywhere by this slice. It also calls four functions that arrive with
// domain.sql — ops.f01_principal, ops.f01_now_text, ops.f01_read and
// ops.f01_stored_artifact — and it calls them rather than restating them,
// because the authenticated principal, the server clock and the document and
// artifact readbacks each have exactly one home and this is not it. Against a
// database carrying neither hunk every operation fails on a missing function,
// which is loud and closed.
//
// TWO EVIDENCE READERS DO NOT EXIST, AND THE PATHS THAT NEED THEM FAIL CLOSED.
// See V5_J102_ABSENT_EVIDENCE_READERS below: nothing in this record layer
// authenticates a representation-equivalence approval or a multi-target
// exception approval, because nothing PRODUCES one. Those two paths refuse with
// the missing fact named. They are not stubbed, defaulted, or satisfied from a
// caller field, and landing a producer for one does not silently open the other.
//
// THE THREE INITIALIZATION OPERATIONS ARE WIRED, AND THEY ARE NOT A BYPASS.
// `initialize-prospect-relationship`, `initialize-assignment` and
// `initialize-property-negotiation` create the FIRST row of a chain — the one
// thing no transition may do, because a transition's own prerequisites need a
// committed row to be checked against. They run through their own writer
// (ops.j102_initialize_subject) against their own admission map, they create only
// the earliest declared state of their kind, and ops.j102_apply_transition still
// refuses a proposed primary subject by name. Every transition prerequisite,
// every evidence contract and every subject binding is exactly as it was.
//
// Q103'S TWO CALLERS ARE WIRED AT SOURCE. `record-lifecycle-reconciliation`
// evaluates one concurrent edit against the version the caller decided against
// and the version the DATABASE holds, and appends a visible unresolved item
// through ops.j102_record_reconciliation_item; the `ownership_and_freshness` read
// kind composes two EXISTING SQL reads and projects what they establish. Both are
// described in V5_J102_WIRED_CONCURRENCY_CAPABILITIES with their exact residuals.
//
// WHAT REMAINS UNWIRED IS NOW TWO FACTS RATHER THAN TWO CALLERS. No relation in
// this record layer records WHO OWNS a subject or WHAT AUTOMATION IS RUNNING
// against it, so the projection reports both as UNKNOWN — which is the answer the
// kernel was built to distinguish from "none" — and V5_J102_UNWIRED_CAPABILITIES
// names each with the exact minimal change that would produce it. Neither is
// stubbed, defaulted, or answered from a caller.
//
// REGISTERED. v5J102ToolRegistrations() is the description tools.js registers
// from through creLifecycleStoreTools() at the end of this file: every verb on
// the writer connection, so mcp.js's setWriterActorContext sets the actor and the
// server-verified sponsor for each call. The twenty-one verbs are sealed in SCAC
// v80 (migration 0705) over the store SQL numbered as migration 0704. What stays
// false is `accepted`: registration is not a partner's acceptance.
//
// WHAT THIS MODULE IS NOT. It performs no provider call, calls no Salesforce API,
// sends nothing, and completes no acceptance.

import { canonicalJson, digest } from "./artifact-trust.js";
import {
  ORGANIZATION_TENANT_ID,
  isKnownActor,
  authorizationClassForActor,
  personalScopeForActor,
} from "./identity.js";
import { V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import {
  V5_J102_ACTOR_CLASSES,
  V5_J102_AUTHORITY_INJECTION_FRAGMENTS,
  V5_J102_ASSERTED_FACT_FRAGMENTS,
  V5_J102_DEAL_AXES,
  V5_J102_EVIDENCE_INTEGRITY,
  V5_J102_EVIDENCE_LOADER,
  V5_J102_EVIDENCE_KINDS,
  V5_J102_INITIALIZATION_IDS,
  V5_J102_MATERIAL_FIELD_CLASSES,
  V5_J102_PARTNER_AUTHORED_RECORD_KINDS,
  V5_J102_SUBJECT_KINDS,
  V5_J102_TRANSITION_IDS,
  evaluateConcurrentEdit,
  evaluateConcurrentTransition,
  evaluateLifecycleInitialization,
  V5_J102_ACTIVE_NEGOTIATION_STATES,
  v5J102TransitionWrites,
  evaluateLifecycleTransition,
  projectOwnershipAndFreshness,
  projectSalesforceReference,
  v5J102DecisionSubsetDigest,
  v5J102EvidenceContract,
  v5J102InitializationContract,
  v5J102PolicyDigest,
  v5J102TransitionContract,
} from "./cre-lifecycle.v5.js";

export const V5_J102_STORE_SCHEMA_VERSION =
  "doctorcre-v5-j102-cre-lifecycle-store.v1";
export const V5_J102_ENVELOPE_SCHEMA_VERSION =
  "doctorcre-v5-j102-stored-record-envelope.v1";

export const V5_J102_STORED_SUBJECT_SCHEMA_VERSION =
  "doctorcre-v5-j102-stored-lifecycle-subject.v1";
export const V5_J102_STORED_EVENT_SCHEMA_VERSION =
  "doctorcre-v5-j102-stored-lifecycle-event.v1";
export const V5_J102_STORED_FACT_SCHEMA_VERSION =
  "doctorcre-v5-j102-stored-first-party-record.v1";
export const V5_J102_STORED_REFERENCE_SCHEMA_VERSION =
  "doctorcre-v5-j102-stored-salesforce-reference.v1";
export const V5_J102_STORED_CORRECTION_SCHEMA_VERSION =
  "doctorcre-v5-j102-stored-correction-receipt.v1";
export const V5_J102_STORED_EVIDENCE_LINK_SCHEMA_VERSION =
  "doctorcre-v5-j102-stored-evidence-subject-link.v1";

/** The exact record_kind vocabulary the ops relations enforce. */
export const V5_J102_STORE_RECORD_KINDS = Object.freeze([
  "stored_lifecycle_subject",
  "stored_lifecycle_event",
  "stored_first_party_record",
  "stored_salesforce_reference",
  "stored_correction_receipt",
  "stored_evidence_subject_link",
  "stored_reconciliation_item",
]);

export const V5_J102_OPERATIONS = Object.freeze([
  "read-cre-lifecycle",
  "record-lifecycle-fact",
  "record-evidence-subject-link",
  // The three initialization operations. Each creates the FIRST row of a chain
  // and advances nothing: a prospect relationship, an assignment under an already
  // active engagement held by a client, and a property negotiation under an
  // assignment that is still open. Every transition below still requires its own
  // evidence and its own prerequisites afterwards.
  "initialize-prospect-relationship",
  "initialize-assignment",
  "initialize-property-negotiation",
  "record-representation-agreement",
  "open-cre-assignment",
  "record-loi-submission",
  "record-loi-acceptance",
  "commit-winning-property",
  "record-deal-execution",
  "record-diligence-outcome",
  "record-deal-closing",
  "cancel-pending-deal",
  "record-deal-axis",
  "link-salesforce-reference",
  "record-lifecycle-correction",
  // Q103's visible reconciliation. It evaluates one concurrent edit against the
  // version the caller decided against and the version the database actually
  // holds, and writes the resulting item where a person can find it. It advances
  // no lifecycle state and resolves nothing.
  "record-lifecycle-reconciliation",
  // Q081's shadow: compares every legacy row with the J102 projection its
  // Salesforce reference links and appends one run record. Moves no state.
  "run-migration-shadow",
]);

/**
 * THE TWO EVIDENCE READERS THIS RECORD LAYER DOES NOT HAVE.
 *
 * Both are typed_approval kinds, and the gap is a PRODUCER gap rather than a
 * reader gap: no workflow anywhere writes a representation-equivalence approval
 * or a multi-target exception approval, so there is nothing for a reader to
 * return and nothing for a transition to bind to. The honest consequence is that
 * both paths refuse, today and until a producer lands.
 *
 * WHAT IS DELIBERATELY NOT DONE INSTEAD, because each would manufacture the
 * authority the absent record is supposed to carry: accepting the approval from
 * the caller, reading one out of configuration, treating an approval REFERENCE
 * stored on an assignment row as the approval itself, defaulting the equivalence
 * to "an ETL-like document counts", or treating "the reader is not built" as
 * "the approval is not required".
 *
 * THE TWO ARE INDEPENDENT ON PURPOSE. Landing a producer for representation
 * equivalence must not silently open multi-target exceptions, so each names its
 * own missing fact and each is checked separately.
 *
 * ops.j102_typed_approval() in the candidate SQL is the same refusal one layer
 * down: it is granted to no role and always raises. This list exists so the
 * refusal is a POLICY ANSWER a caller can record rather than a database error,
 * and so the two halves cannot drift apart without the suite noticing.
 */
export const V5_J102_ABSENT_EVIDENCE_READERS = Object.freeze({
  approved_representation_equivalent: Object.freeze({
    evidence_kind: "approved_representation_equivalent",
    approval_kind: "representation_equivalence_approval",
    missing_fact: "authenticated_representation_equivalence_approval",
    why: "Q077 admits a representation agreement other than an ETL, but only on a typed authenticated approval that this class of agreement counts. No producer writes one and no relation holds one, so the equivalence cannot be established without inventing the business policy the approval is supposed to carry.",
    produced_by: "not_produced_by_this_slice",
  }),
  multi_target_exception_approval: Object.freeze({
    evidence_kind: "multi_target_exception_approval",
    approval_kind: "multi_target_exception",
    missing_fact: "authenticated_multi_target_exception_approval",
    why: "Q095 permits a second selected property or lease-draft target only on an explicitly approved exception. No producer writes one, so the single-target constraint holds unconditionally today.",
    produced_by: "not_produced_by_this_slice",
  }),
});

/**
 * THE CAPABILITIES THIS RECORD LAYER DOES NOT WIRE, named rather than implied.
 *
 * The absent-reader registry above covers evidence this layer cannot READ. This
 * one covers behaviour this layer does not CONNECT, and it exists because the
 * absence is invisible from the kernel suite: the kernel evaluates a concurrent
 * edit and an ownership projection perfectly well, and nothing there notices that
 * no shipped operation ever calls either.
 *
 * NOTHING BELOW IS A PLAN, A SCHEDULE OR A PROMISE. Each entry names one exact
 * missing producer or caller and who would have to own it. Neither is worked
 * around anywhere in this module: no reconciliation item and no ownership
 * projection is written or exposed by any code path that ships here.
 *
 * THE THREE INITIALIZATION ENTRIES THAT USED TO SIT HERE ARE GONE BECAUSE THEY
 * WERE BUILT, not because the standard moved — see
 * V5_J102_WIRED_INITIALIZATION_CAPABILITIES below, which records what landed and
 * what deliberately did not.
 */
export const V5_J102_UNWIRED_CAPABILITIES = Object.freeze([
  // THE TWO ENTRIES THIS LIST USED TO CARRY WERE THE CALLERS. Both are built now
  // — see V5_J102_WIRED_CONCURRENCY_CAPABILITIES below — and what is left is
  // narrower and more exact: two FACTS ABOUT THE WORLD that no relation in this
  // record layer holds, so no caller of any shape could report them honestly.
  // Each names the exact minimal change that would produce it.
  Object.freeze({
    capability: "subject_ownership_authority",
    missing_fact: "any authoritative record of WHICH PARTNER owns a lifecycle subject",
    why: "Q103 asks every view to show the current owner. Nothing in this rail holds one: ops.j102_subject_current carries `updated_by`, which is who last WROTE the row, and reporting that as the owner would answer a different question with a confident-looking value. `read-cre-lifecycle` kind `ownership_and_freshness` therefore returns owner_slug null with owner_known false, and says so rather than filling it in.",
    exact_minimal_change: "an owner column on ops.j102_subject_current (or a J102-scoped ownership relation) written by its own registered partner-authored writer, plus the field on the kernel's subject shape. Both are SQL and kernel-vocabulary changes and are Root's to schedule; this module invents neither.",
    produced_by: "not_produced_by_this_slice",
  }),
  Object.freeze({
    capability: "active_automation_registry",
    missing_fact: "any record of automation IN PROGRESS against a lifecycle subject",
    why: "Q103 asks every view to show in-progress automation affecting the record. No relation here records a run against a subject, and an empty list would be the one wrong answer — 'nothing is running' and 'nobody asked' are different states, and the kernel is built to distinguish them. The read kind therefore returns active_automation null with active_automation_known false.",
    exact_minimal_change: "a J102-scoped automation-run relation (automation_id, kind, subject, started_by, started_at, ended_at) with a reader, written by whatever schedules the runs. Until one exists the honest answer is unknown, and that is what is returned.",
    produced_by: "not_produced_by_this_slice",
  }),
]);

/**
 * WHAT Q103's TWO CALLERS NOW DO, and exactly what they still do not.
 *
 * These replace the two entries that used to sit in the unwired list. They are
 * recorded here rather than deleted because a reader comparing revisions needs to
 * see that the entries went away because the work landed, and needs the residuals
 * in the same breath as the claim.
 *
 * NEITHER IS REGISTERED AT RUNTIME. `v5J102ToolRegistrations()` is still a
 * DESCRIPTION: this module touches no tools.js, no mutation registry and no
 * generated catalog, and the four false flags on every registration entry say so.
 * "Wired at source" and "reachable by a partner in the product" are different
 * claims and this registry makes only the first.
 */
export const V5_J102_WIRED_CONCURRENCY_CAPABILITIES = Object.freeze([
  Object.freeze({
    capability: "reconciliation_runtime_integration",
    operation: "record-lifecycle-reconciliation",
    kernel_entry_point: "evaluateConcurrentEdit",
    sql_writer: "ops.j102_record_reconciliation_item",
    what_it_does: "Claims its idempotency key before reading any state, loads the subject, takes the CURRENT version digest from the stored row rather than from the caller, stamps every incoming edit with the derived actor and the server instant, runs the kernel, and — when the kernel says reconcile — writes one visible item carrying both the caller's edits and the authoritative evidence of the other side: the current committed state and the tail of the append-only history that produced it.",
    idempotency: "WRITER-CLAIMED, through the same ops.j102_claim_idempotency / ops.j102_settle_idempotency every sibling uses. A replay returns the stored outcome and writes nothing; the same key over DIFFERENT bytes refuses rather than substituting one conflict for another; and the key is bound to the actor, so it cannot be replayed by somebody else. THE EARLIER READ-BEFORE-WRITE DUPLICATE CHECK IS GONE AS A CORRECTNESS CLAIM — it was not atomic, two callers passed it simultaneously, and it could not distinguish a stale reading from a current one.",
    distinct_proposals: "PRESERVED, deliberately. Two different edit sets against the same two version digests are two real conflicts and both land visibly: idempotency is keyed on the REQUEST, which covers the edits, and there is no unique index over the version pair — one would silently discard the second proposal.",
    labels: "RE-DERIVED AT THE WRITE BOUNDARY, not accepted. `conflict_kind` and every edit's `field_class` are derived-only fields here, so no caller of this store supplies either — but ops.j102_record_reconciliation_item is granted to carr_writer, and a direct caller could otherwise file a visible item labelled with a conflict kind nothing emits, or a lifecycle field labelled `routine`, and the receipt derives its material and unclassified field lists from exactly those stored labels. The writer now checks both against ops.j102_admission_policy()'s transcription of V5_J102_CONFLICT_KINDS and V5_J102_FIELD_CLASS_REGISTRY, on BOTH preserved sides, and the relation restates the conflict vocabulary and the five subject kinds as CHECKs. It is a mislabelling refusal rather than a state bypass closed: an item still cannot merge, apply or resolve anything.",
    staleness: "BOUND AT THE WRITE BOUNDARY, not at read time. The writer locks the subject in the established tier-2 order, compare-and-swaps it, and then requires the item's own `current_version_digest`, the state snapshot it shows and the newest row of its history evidence to match the committed subject and its committed history. A reading that went stale between the caller's read and the insert cannot be filed as a current fact.",
    residual: "THE CONCURRENT EDIT SET IS NOT CHARACTERIZED, and the item says so. This layer can prove that the subject MOVED (the base digest is not the stored one) and can show what it moved TO and which transitions did it, but it cannot enumerate the other writer's field-level edits: ops.j102_subject exposes no prior_state_digest, so there is nothing to anchor a diff to. The kernel's own `concurrent_change_not_characterized` branch is exactly this case and reconciles visibly rather than merging on an absence, which is the conservative half of Q103 and not a gap in the integration.",
    exact_minimal_change_for_the_residual: "surface `prior_state_digest` from ops.j102_subject (it is already inside the hashed envelope and CHECK-bound; the reader simply does not return it). With it, a base that equals the stored prior digest identifies the ONE transition that has run since, whose moved-field set is already declared in the admission map — turning an uncharacterized conflict into a characterized one for the single-step case. SQL change, Root's to schedule.",
    registered_at_runtime: false,
  }),
  Object.freeze({
    capability: "ownership_and_freshness_exposure",
    operation: "read-cre-lifecycle",
    read_kind: "ownership_and_freshness",
    kernel_entry_point: "projectOwnershipAndFreshness",
    what_it_does: "Composes two EXISTING SQL read kinds — `subject` and `subject_events` — and feeds the kernel only what those readbacks actually establish: the recomputed state digest, and the last material change taken from the newest row of the append-only history. The subject row's own updated_by/updated_at are cross-checked against that event, and a disagreement refuses rather than picking one.",
    residual: "OWNER AND ACTIVE AUTOMATION ARE NOT SUPPLIED, because no relation holds either — see the two entries in V5_J102_UNWIRED_CAPABILITIES. The kernel reports owner_known false and active_automation_known false, which is the distinction it was built to make: unknown is not the same answer as none.",
    registered_at_runtime: false,
  }),
]);

/**
 * THE INITIALIZATION GAP IS CLOSED, AND CLOSED NARROWLY — recorded here because
 * the three entries this registry used to carry, and the fourth that said the
 * rail had no bootstrap at any layer, are gone from it and a reader comparing two
 * revisions should see WHY rather than that they disappeared.
 *
 * WHAT LANDED: three initialization operations, each with its own admission in
 * this store and its own writer in the candidate SQL
 * (ops.j102_initialize_subject), each creating the earliest declared state of one
 * subject kind under a parent chain re-checked under the writer's own lock.
 *
 * WHAT DID NOT: ops.j102_apply_transition still REFUSES to create the primary
 * subject of a transition (`j102_primary_subject_creation_refused`), and the
 * initialization writer is a different function with a different map — it can
 * create only the three kinds below, only in their fixed initial state, and it
 * performs no transition. The null-operand path through the transition writer is
 * not reopened, and every transition prerequisite remains mandatory.
 */
export const V5_J102_WIRED_INITIALIZATION_CAPABILITIES = Object.freeze([
  Object.freeze({
    capability: "relationship_prospect_initialization",
    operation: "initialize-prospect-relationship",
    creates_subject_kind: "relationship",
    initial_state: "prospect",
    requires_evidence: false,
    why: "Q069/Q077 start Journey 1 at a prospect, and pursuing a party is not a fact a document establishes. The row it writes says only that this relationship is a prospect holding no engagements; `record-representation-agreement` is still the only door to client status and still requires an active signed representation agreement.",
  }),
  Object.freeze({
    capability: "assignment_initialization",
    operation: "initialize-assignment",
    creates_subject_kind: "assignment",
    initial_state: "research",
    requires_evidence: false,
    why: "Q079's several mandates per client. The assignment is created under an ACTIVE engagement held by a relationship that is already a CLIENT, both re-read under the writer's lock, at the earliest declared phase. `open-cre-assignment` remains the only way to reach `search` and the only producer of an assignment_opened event, so the mandate record stays load-bearing for both — but it is NOT the only way past `research`: record-loi-submission admits a research assignment and writes `negotiation`, so a created shell can reach negotiation with no mandate ever written. Whether a mandate must precede an LOI is an OWNER QUESTION, unsettled by all thirteen decisions and deliberately not encoded here.",
  }),
  Object.freeze({
    capability: "property_negotiation_initialization",
    operation: "initialize-property-negotiation",
    creates_subject_kind: "property_negotiation",
    initial_state: "loi_drafted",
    requires_evidence: false,
    why: "Q095's concurrent LOIs. The negotiation is created under an assignment that is still open — a committed or concluded one refuses, on the same bound the kernel already applies to a fresh LOI — and `record-loi-submission` still requires the delivered LOI document bound to that negotiation.",
  }),
]);

/**
 * THE QUESTIONS THIS SLICE DOES NOT ANSWER, kept in code beside the answers so
 * that "nobody decided this" cannot decay into "somebody must have".
 *
 * NONE OF THESE IS A POLICY. Each names a decision the thirteen accepted
 * decisions leave open, what the rail does TODAY in the absence of an answer, and
 * — where there is one — the exact narrow change that would encode each answer.
 * The behaviour they describe is deliberately NOT changed pending an owner
 * ruling: encoding one on an author's or a reviewer's reading is the failure this
 * registry exists to make visible.
 *
 * The last entry is a different kind of thing and says so: an implementation
 * assumption that is live, defensible and unratified.
 */
export const V5_J102_OPEN_OWNER_QUESTIONS = Object.freeze([
  Object.freeze({
    question: "must an assignment be OPENED on a mandate record before an LOI may be drafted or submitted under it?",
    status: "unsettled_pending_owner_ruling",
    today: "No. `initialize-assignment` creates the row at `research`, `initialize-property-negotiation` admits an assignment in research, search or negotiation, and `record-loi-submission` admits the same three and writes `negotiation`. So an assignment can reach `negotiation` through an LOI with no mandate record ever written and no `assignment_opened` event in its history. `open-assignment` remains the only way to reach `search` and the only producer of that event.",
    why_unsettled: "Q072.D1 maps search initiation TO research or search; it states no ordering obligation, and no other accepted decision names one. Q080.D1 gives the assignment its phases and is silent on what may reach them.",
    narrow_change_if_the_answer_is_yes: "drop `research` from initialize-property-negotiation's admitted parent phases in the kernel contract and in the SQL admission map. It would also stop a research-scope assignment from ever holding a draft, which is a real cost and part of the decision.",
    encoded_without_a_ruling: false,
  }),
  // THE OWNER'S RULINGS OF 2026-09-25, kept beside the open question so the
  // registry shows what was decided, when, and exactly what encodes it.
  Object.freeze({
    question: "what links a J102 relationship to a CARR party or contact record?",
    status: "ruled_by_owner",
    ruling: "(a) an OPTIONAL declared reference from a prospect relationship to the CARR party record.",
    ruled_on: "2026-09-25",
    today: "`initialize-prospect-relationship` accepts an optional `party_id`; the relationship row carries it (null when none is named); the SQL writer refuses an id no `public.party` row holds (`j102_party_not_found`). The link makes two prospects for one party DETECTABLE; it does not forbid them.",
    why_unsettled: "It is not: Q083.D1 left it open and the owner ruled.",
    encoded_by: ["kernel optional_declared_identifiers", "SQL creation_shape optional_declared_identifier", "writer party existence check"],
    encoded_without_a_ruling: false,
  }),
  Object.freeze({
    question: "may one assignment hold two property negotiations against the SAME property?",
    status: "ruled_by_owner",
    ruling: "(b) at most one ACTIVE negotiation per (tenant, assignment, property); a dead or closed one does not block a new one.",
    ruled_on: "2026-09-25",
    today: "ACTIVE is loi_drafted, loi_submitted, loi_countered and loi_accepted. The store loads the live siblings and the kernel refuses `active_negotiation_exists_for_property`; the SQL writer re-checks committed rows (`j102_active_negotiation_exists`); the partial unique index `ops.j102_one_active_negotiation_per_property` backstops a true race. `selected_winner` is NOT active: a pending deal already holds the assignment at committed, and after a cancelled deal the old winner must not block the property.",
    why_unsettled: "It is not: Q095.D1 was silent and the owner ruled.",
    encoded_by: ["kernel refusal", "SQL writer check", "partial unique index"],
    encoded_without_a_ruling: false,
  }),
  Object.freeze({
    question: "may a SPONSORED AGENT create a prospect, an assignment shell or an LOI draft?",
    status: "ruled_by_owner",
    ruling: "(c) yes, like a partner, recorded as sponsored_agent under the sponsoring partner.",
    ruled_on: "2026-09-25",
    today: "DONE. All three initializations admit verified_partner and sponsored_agent. Every lifecycle subject row and event row now carries `sponsoring_partner`: the partner's own slug on a partner's session, the server-verified sponsor on an agent's or a writer-credential session. The store takes it from the authenticated grant (identity.js personalScopeForActor), the database derives it independently (ops.j102_sponsoring_partner(), from f01_principal and the server-set carr.sponsoring_human_slug), the store refuses when the two disagree, the SQL writers refuse an envelope naming any other value (j102_sponsor_mismatch), and a CHECK binds the column to the hashed envelope. A caller-supplied `sponsoring_partner` is refused as a derived-only field.",
    encoded_by: ["store principal.sponsoring_partner", "ops.j102_sponsoring_partner()", "writer j102_sponsor_mismatch", "column CHECKs on subject and event"],
    why_unsettled: "It is not: the owner ratified the parity.",
    residual_to_weigh: "`initialize-prospect-relationship` has no parent, no evidence and no rate bound, so an authenticated sponsored agent can create prospect rows limited only by id uniqueness.",
    encoded_without_a_ruling: false,
  }),
  Object.freeze({
    question: "may a concurrent write that no longer matches the current row be re-admitted against it (rebase and re-admit)?",
    status: "ruled_by_owner",
    ruling: "(d) ratified.",
    ruled_on: "2026-09-25",
    today: "Q103: a disjoint write over exactly one characterized intervening write is re-admitted against the CURRENT row and merged; overlapping or uncharacterized writes file one visible reconciliation item; nothing is overwritten.",
    why_unsettled: "It is not: the owner ratified it.",
    encoded_without_a_ruling: false,
  }),
  Object.freeze({
    question: "how is a verified partner recorded when an operation runs on the writer credential?",
    status: "ruled_by_owner",
    ruling: "(e) as sponsored_agent under the partner's own slug.",
    ruled_on: "2026-09-25",
    today: "A non-authorityOnly, non-humanOnly operation called by a verified partner adopts the credential's sponsored_agent class under the partner's slug and reports the split beside the result; every other disagreement refuses.",
    why_unsettled: "It is not: the owner ratified it.",
    encoded_without_a_ruling: false,
  }),
]);

export class V5J102StoreError extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = "V5J102StoreError";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function fail(code, message, detail) {
  throw new V5J102StoreError(code, message, detail);
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

// ---------------------------------------------------------------------------
// The closed caller surface.
//
// THREE GUARDS, and they name different attempts on purpose.
//
//   AUTHORITY INJECTION — a field purporting to confer authority. Reused from
//     the kernel by IMPORT rather than copied, so the two lists cannot drift.
//   ASSERTED-FACT INJECTION — a field stating the outcome the transition exists
//     to establish. Also imported.
//   DERIVED-VALUE INJECTION — a field the SERVER owns: the tenant, the instant,
//     the loaded subject, the loaded evidence, the evaluated decision, a digest
//     of something this module computes. These are not authority claims and not
//     outcome claims, so neither kernel list names them, but a caller supplying
//     one would be choosing the evidence its own write is judged against. That
//     is the same failure wearing different clothes.
//
// A DIGEST IS NOT ALWAYS AN INJECTION, and the difference matters. A caller may
// name `expected_state_digest` to state the version it decided against, and may
// name `expected_content_digest` to pin the document version it means. Both are
// CHECKED against a readback or a recomputation and never trusted: a pin that
// does not match refuses. Everything else in the derived list is refused by name.
// ---------------------------------------------------------------------------

export const V5_J102_DERIVED_ONLY_FIELDS = deepFreeze([
  "tenant", "now", "server_time", "recorded_at", "evaluated_at", "updated_at",
  "recorded_by", "updated_by", "sponsor", "sponsoring_human_slug", "sponsoring_partner",
  "authenticated_identity", "human", "via", "client_id",
  "subject", "subjects", "related", "current_state", "prior_state",
  "evidence", "evidence_record", "document", "artifact", "record", "approval",
  "provenance", "loaded_by", "integrity",
  // The subject binding and the author's class are DERIVED FROM STORED ROWS. A
  // caller able to name either would be asserting which deal an authentic record
  // is about, or which authority wrote it — the two facts BLOCK-2 and H5 exist
  // to take out of a caller's hands.
  "subject_binding", "bound_by", "binding_digest", "link_digest",
  "recorded_by_authorization_class", "bound_subject_kind", "bound_subject_id",
  "decision", "reason_id", "outcome", "applied", "proposed_state", "events",
  "coupled_facts_committed", "reversibility", "decision_refs",
  "policy_digest", "domain_policy_digest", "decision_subset_digest",
  "state_digest", "event_digest", "record_digest", "envelope_digest",
  "subject_digest", "reference_digest", "receipt_digest", "reconciliation_digest",
  "event_seq", "last_event_digest", "freshness_age_seconds",
  "owner_known", "freshness_known", "active_automation_known",
  // The lifecycle state axes themselves. A caller that could name one would be
  // performing the free-form stage update Q082 removed, which is why every one
  // of them is refused here rather than merely absent from a schema.
  "relationship_state", "engagement_state", "assignment_phase", "negotiation_state",
  ...V5_J102_DEAL_AXES,
  "representation_basis", "selected_property_id", "active_lease_draft_target_id",
  "pending_deal_id", "cancellation_reason", "closing_date",
  // Q103's OWN DERIVED FACTS. The owner, the freshness pair and the automation
  // list are answers this layer reads off stored rows or does not have at all; a
  // caller that could supply one would be answering the question the projection
  // exists to answer. `edited_by` and `edited_at` are the same shape one level
  // down: WHO made an edit and WHEN is the derived principal and the server
  // clock, never the edit's own account of itself. `field_class` is refused for
  // the reason the kernel removed it — labelling a lifecycle field `routine`
  // bought the one merge branch that can silently overwrite another partner.
  "owner_slug", "last_material_change_at", "last_material_change_by",
  "active_automation", "automation_id",
  "edited_by", "edited_at", "field_class",
  "base_version_digest", "current_version_digest", "conflict_kind",
  "incoming_edits", "concurrent_edits", "proposed_by", "resolved_by_machine",
  "item_digest", "item_seq", "merged", "auto_merged_fields",
]);

function assertNoAccessorsOrHiddenKeys(object, path) {
  if (Object.prototype.hasOwnProperty.call(object, "__proto__")) {
    fail("prototype_key_refused", `${path}.__proto__ is an own key; the shape is refused rather than read`,
      { path });
  }
  if (Object.getOwnPropertySymbols(object).length > 0) {
    fail("symbol_key_refused", `${path} carries symbol keys, which would ride along unread`, { path });
  }
  for (const key of Object.getOwnPropertyNames(object)) {
    const descriptor = Object.getOwnPropertyDescriptor(object, key);
    if (descriptor.get !== undefined || descriptor.set !== undefined) {
      fail("accessor_property_refused",
        `${path}.${key} is an accessor; a value that can change between reads cannot bind a write`,
        { path: `${path}.${key}` });
    }
  }
}

function assertNoInjectedKeys(object, allowed, path) {
  for (const key of Object.keys(object)) {
    if (allowed.includes(key)) continue;
    const normalized = key.toLowerCase();
    for (const fragment of V5_J102_AUTHORITY_INJECTION_FRAGMENTS) {
      if (normalized.includes(fragment)) {
        fail("caller_authority_field_refused",
          `${path}.${key} names authority the caller cannot supply; the handler derives actor and tenant`,
          { path: `${path}.${key}`, key, fragment });
      }
    }
    for (const fragment of V5_J102_ASSERTED_FACT_FRAGMENTS) {
      if (normalized.includes(fragment)) {
        fail("caller_asserted_fact_refused",
          `${path}.${key} asserts a fact the transition establishes from evidence; supply a reference, not a verdict`,
          { path: `${path}.${key}`, key, fragment });
      }
    }
    if (V5_J102_DERIVED_ONLY_FIELDS.includes(normalized)) {
      fail("caller_derived_field_refused",
        `${path}.${key} is derived by the server or loaded from the database; a caller may not supply it`,
        { path: `${path}.${key}`, key });
    }
  }
}

/** An open schema is a contract violation: an unread field is an unenforced one. */
function assertClosed(object, allowed, required, path) {
  if (!isPlainObject(object)) fail("invalid_shape", `${path} must be a plain object`, { path });
  assertNoAccessorsOrHiddenKeys(object, path);
  assertNoInjectedKeys(object, allowed, path);
  for (const key of Object.keys(object)) {
    if (!allowed.includes(key)) {
      fail("unknown_field", `unknown field "${key}" at ${path}`, { path: `${path}.${key}`, key });
    }
  }
  for (const key of required) {
    if (!(key in object) || object[key] === undefined || object[key] === null) {
      fail("missing_field", `${path}.${key} is required`, { path: `${path}.${key}` });
    }
  }
  return object;
}

function assertIdempotencyKey(value, path) {
  if (typeof value !== "string" || value.length === 0 || value.length > 200 ||
      !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(value)) {
    fail("invalid_idempotency_key", `${path} is not a permitted idempotency key`, { path });
  }
  return value;
}

function assertIdent(value, path, { maxLength = 128 } = {}) {
  if (typeof value !== "string" || value.length === 0 || value.length > maxLength ||
      !/^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]*$/.test(value)) {
    fail("invalid_identifier", `${path} is not a permitted identifier`, { path });
  }
  return value;
}

function assertDigestRef(value, path) {
  if (typeof value !== "string" || !/^sha256:[0-9a-f]{64}$/.test(value)) {
    fail("invalid_digest", `${path} must be a "sha256:" reference over 64 lower-case hex characters`,
      { path });
  }
  return value;
}

// M1. THE TYPED FIELDS OF A BUSINESS RECORD, CHECKED BEFORE THE DURABLE WRITE.
//
// These used to go in unvalidated. A malformed closing_date failed loudly at
// ops.f01_instant, but a non-string reason or detail stored perfectly well — and
// then made the record UNREADABLE as evidence later, at which point the kernel's
// assertLifecycleEvidence THREW a contract violation instead of returning a
// refusal. That breaks the module's own two-kinds-of-no contract at the store
// boundary, and it breaks it long after the request that caused it. The fix is
// to refuse the malformed field where it arrives, in the same shape the SQL
// CHECK constraints enforce it.
const ISO_INSTANT_TEXT =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$/;

// WRITTEN AS NUMERIC RANGES RATHER THAN A REGEX CHARACTER CLASS, for the reason
// the kernel's own copy of this guard records: a literal control byte in the
// committed source makes the file read as BINARY to file(1), rg and git diff, so
// the module that refuses invisible characters stops being reviewable as text
// itself. Hex code points cannot become bytes by accident.
const UNSAFE_CODE_POINT_RANGES = Object.freeze([
  [0x0000, 0x001f], [0x007f, 0x009f], [0x200b, 0x200f], [0x202a, 0x202e],
  [0x2060, 0x2064], [0x2066, 0x2069], [0xfeff, 0xfeff],
]);

function hasUnsafeCodePoint(value) {
  for (const character of value) {
    const point = character.codePointAt(0);
    for (const [low, high] of UNSAFE_CODE_POINT_RANGES) {
      if (point >= low && point <= high) return true;
    }
  }
  return false;
}

function assertPlainText(value, path, { maxLength }) {
  if (typeof value !== "string" || value.length === 0) {
    fail("invalid_shape", `${path} must be a non-empty string`, { path });
  }
  if (value.length > maxLength) {
    fail("text_too_long", `${path} may be at most ${maxLength} characters`,
      { path, length: value.length });
  }
  if (typeof value.isWellFormed === "function" && !value.isWellFormed()) {
    fail("malformed_unicode", `${path} contains an unpaired surrogate`, { path });
  }
  if (hasUnsafeCodePoint(value)) {
    fail("unsafe_unicode", `${path} contains a control, bidirectional or invisible format character`,
      { path });
  }
  if (value.normalize("NFC") !== value) {
    fail("non_canonical_unicode", `${path} is not in Unicode NFC; it is refused rather than normalized`,
      { path });
  }
  if (value.trim() !== value) {
    fail("untrimmed_text", `${path} has leading or trailing whitespace`, { path });
  }
  return value;
}

function assertInstantText(value, path) {
  if (typeof value !== "string" || !ISO_INSTANT_TEXT.test(value) ||
      !Number.isFinite(Date.parse(value))) {
    fail("invalid_timestamp", `${path} must be an ISO-8601 instant with an explicit offset`, { path });
  }
  // The calendar is checked against the LITERAL fields, because Date.parse
  // silently normalizes 31 February into 3 March and a closing date nobody wrote
  // is not the date the deal closed on.
  const [y, mo, d] = value.slice(0, 10).split("-").map(Number);
  const daysInMonth = mo === 2
    ? ((y % 4 === 0 && y % 100 !== 0) || y % 400 === 0 ? 29 : 28)
    : [4, 6, 9, 11].includes(mo) ? 30 : 31;
  if (mo < 1 || mo > 12 || d < 1 || d > daysInMonth) {
    fail("invalid_timestamp",
      `${path} names an instant that does not exist on the calendar; it is not normalized into a different one`,
      { path });
  }
  return value;
}

// ---------------------------------------------------------------------------
// Evidence REFERENCES. A caller names one of four shapes, and each resolves to a
// server-loaded record through exactly one reader.
//
// THE PIN IS THE POINT OF THE DOCUMENT SHAPE. A caller that names only a
// document id is asking about "whatever that document says now", and a decision
// taken against a version that moved between the read and the write is the
// concurrency defect Q103 exists to prevent. So a document reference carries the
// exact version and the exact content digest it means; the load refuses when the
// stored version differs, and ops.j102_apply_transition re-reads the same pin
// under its lock.
// ---------------------------------------------------------------------------

// The union, used only to read `evidence_kind` before the shape is known. The
// per-source lists below are what actually bind: a document reference that also
// carried an artifact digest would be a request naming two different pieces of
// evidence, and the narrower check refuses it rather than silently reading one.
const EVIDENCE_REF_KEYS = Object.freeze([
  "evidence_kind", "document_id", "expected_version_no", "expected_content_digest",
  "artifact_digest", "record_id", "approval_ref",
]);
const DOCUMENT_REF_KEYS = Object.freeze([
  "evidence_kind", "document_id", "expected_version_no", "expected_content_digest",
]);
const ARTIFACT_REF_KEYS = Object.freeze(["evidence_kind", "artifact_digest"]);
const RECORD_REF_KEYS = Object.freeze(["evidence_kind", "record_id"]);
const APPROVAL_REF_KEYS = Object.freeze(["evidence_kind", "approval_ref"]);

function assertEvidenceRef(raw, path) {
  assertClosed(raw, EVIDENCE_REF_KEYS, ["evidence_kind"], path);
  const evidence_kind = raw.evidence_kind;
  if (!V5_J102_EVIDENCE_KINDS.includes(evidence_kind)) {
    fail("unknown_evidence_kind", `"${String(evidence_kind)}" is not a registered evidence kind`,
      { path: `${path}.evidence_kind`, registered: [...V5_J102_EVIDENCE_KINDS] });
  }
  const contract = v5J102EvidenceContract(evidence_kind);
  const out = { evidence_kind, source: contract.source };
  if (contract.source === "f01_document") {
    assertClosed(raw, DOCUMENT_REF_KEYS, DOCUMENT_REF_KEYS, path);
    out.document_id = assertIdent(raw.document_id, `${path}.document_id`);
    if (!Number.isSafeInteger(raw.expected_version_no) || raw.expected_version_no < 1) {
      fail("invalid_shape", `${path}.expected_version_no must be a positive integer`,
        { path: `${path}.expected_version_no` });
    }
    out.expected_version_no = raw.expected_version_no;
    out.expected_content_digest = assertDigestRef(raw.expected_content_digest,
      `${path}.expected_content_digest`);
  } else if (contract.source === "f01_corporate_artifact") {
    assertClosed(raw, ARTIFACT_REF_KEYS, ARTIFACT_REF_KEYS, path);
    out.artifact_digest = assertDigestRef(raw.artifact_digest, `${path}.artifact_digest`);
  } else if (contract.source === "first_party_record") {
    assertClosed(raw, RECORD_REF_KEYS, RECORD_REF_KEYS, path);
    out.record_id = assertIdent(raw.record_id, `${path}.record_id`);
    // The record KIND is taken from the evidence contract, never from the
    // caller — and `record_kind` is deliberately absent from every reference
    // shape above so it cannot be supplied at all. A caller able to name it
    // could point a closing transition at an invoice record and have the
    // kernel's kind check pass against the caller's own claim.
    out.record_kind = contract.record_kind;
  } else {
    assertClosed(raw, APPROVAL_REF_KEYS, APPROVAL_REF_KEYS, path);
    out.approval_ref = assertIdent(raw.approval_ref, `${path}.approval_ref`, { maxLength: 255 });
    out.approval_kind = contract.approval_kind;
  }
  return deepFreeze(out);
}

// ---------------------------------------------------------------------------
// The closed caller schemas, one per operation.
// ---------------------------------------------------------------------------

const SUBJECT_REF_KEYS = Object.freeze(["subject_kind", "subject_id", "expected_state_digest"]);

function assertSubjectRef(raw, path, expected_kind) {
  assertClosed(raw, SUBJECT_REF_KEYS, ["subject_kind", "subject_id"], path);
  if (raw.subject_kind !== expected_kind) {
    fail("subject_kind_mismatch", `${path}.subject_kind must be "${expected_kind}"`,
      { path: `${path}.subject_kind`, expected: expected_kind, actual: raw.subject_kind });
  }
  return deepFreeze({
    subject_kind: expected_kind,
    subject_id: assertIdent(raw.subject_id, `${path}.subject_id`),
    // NULLABLE ON PURPOSE, and null means something exact: "I decided against
    // this subject not existing yet". It is compared against the stored digest
    // the same way a present one is, so a subject that appeared underneath the
    // caller refuses rather than being created twice.
    expected_state_digest: raw.expected_state_digest === undefined ||
      raw.expected_state_digest === null
      ? null : assertDigestRef(raw.expected_state_digest, `${path}.expected_state_digest`),
  });
}

const FACT_KEYS = Object.freeze([
  "schema_version", "idempotency_key", "fact",
]);
// `subject_kind` and `subject_id` are REFERENCES, and that is why a caller may
// name them: they say which deal, assignment or client this business record is
// ABOUT. What the caller cannot do is state them later, at the transition — the
// binding is written into the record once, by its author, and every reader takes
// it from the stored row.
const FACT_BODY_KEYS = Object.freeze([
  "record_kind", "record_id", "subject_kind", "subject_id",
  "reason", "detail", "closing_date", "supporting_document_id",
]);

// One evidence→subject association. The caller names the exact evidence pin and
// the exact subject; the store checks BOTH against the record layer before it
// writes anything, so an association can only ever be made between a document
// version F01 really holds and a lifecycle subject this rail really holds.
const LINK_KEYS = Object.freeze(["schema_version", "idempotency_key", "link"]);
const LINK_BODY_KEYS = Object.freeze([
  "evidence_source", "document_id", "expected_version_no", "expected_content_digest",
  "artifact_digest", "subject_kind", "subject_id",
]);
const LINK_DOCUMENT_KEYS = Object.freeze([
  "evidence_source", "document_id", "expected_version_no", "expected_content_digest",
  "subject_kind", "subject_id",
]);
const LINK_ARTIFACT_KEYS = Object.freeze([
  "evidence_source", "artifact_digest", "subject_kind", "subject_id",
]);
export const V5_J102_LINKABLE_EVIDENCE_SOURCES =
  Object.freeze(["f01_document", "f01_corporate_artifact"]);

const TRANSITION_PAYLOAD_KEYS = Object.freeze([
  "schema_version", "idempotency_key", "subject_ref", "related_refs", "evidence_refs", "declared",
]);
// THE INITIALIZATION PAYLOAD, AND THE THREE KEYS IT DOES NOT HAVE.
//
// No `subject_ref`, because the subject does not exist yet and the id it will
// have is a DECLARED identifier rather than a reference to something. No
// `evidence_refs`, because no evidence in this rail can bind to a subject that
// does not exist — a caller naming one gets `unknown_field` rather than a
// refusal, since there is no evidence question to answer. And no state, phase or
// axis of any kind: the created shape is fixed by the kernel's initialization
// contract, and the derived-field guard refuses every lifecycle axis by name.
//
// `related_refs` carries the PARENT, as a subject reference with its own optional
// compare-and-swap digest, so an assignment is created under the exact engagement
// the caller decided against and a parent that moved refuses.
const INITIALIZATION_PAYLOAD_KEYS = Object.freeze([
  "schema_version", "idempotency_key", "related_refs", "declared",
]);
const INITIALIZATION_DECLARED_KEYS = Object.freeze(["new_subject_id", "property_id", "party_id"]);
const RELATED_REF_KEYS = Object.freeze([
  "relationship", "engagement", "assignment", "property_negotiation", "deal",
]);
// THE DECLARED CHOICES COME IN TWO HALVES, and they are not the same kind of
// thing.
//
//   DOMAIN FIELDS are the kernel's own vocabulary: the mandate scope, the
//     instrument kind, the payment level, the returned-to phase, the diligence
//     result, and the ids of subjects a transition creates. They are forwarded
//     verbatim, and the kernel's closed `request.declared` is what validates
//     them — this module adds no vocabulary of its own to that judgement.
//   THE SELECTOR is consumed HERE and travels no further. `axis` names which of
//     the four orthogonal deal verbs record-deal-axis is asking for; it is the
//     store's dispatch input, not a fact about a deal, and the kernel's declared
//     contract does not carry it. Forwarding it would ask the kernel to widen a
//     closed surface for a value it has no use for, so it is dropped at this
//     boundary instead.
//
// A SELECTOR CANNOT SMUGGLE ANYTHING, because it does not reach the judgement:
// it chooses among registered transition ids by exact name (AXIS_TRANSITIONS
// below) and an unregistered name determines no transition and writes nothing.
const DECLARED_DOMAIN_KEYS = Object.freeze([
  "mandate_scope", "instrument_kind", "return_phase", "payment_level",
  "diligence_result", "new_subject_id", "new_deal_id",
]);
const DECLARED_SELECTOR_KEYS = Object.freeze(["axis"]);
const DECLARED_KEYS = Object.freeze([...DECLARED_DOMAIN_KEYS, ...DECLARED_SELECTOR_KEYS]);

/** The two halves, exported so the suite can prove the split has not drifted. */
export const V5_J102_DECLARED_DOMAIN_FIELDS = DECLARED_DOMAIN_KEYS;
export const V5_J102_DECLARED_SELECTOR_FIELDS = DECLARED_SELECTOR_KEYS;

const REFERENCE_KEYS = Object.freeze([
  "schema_version", "idempotency_key", "opportunity_id", "opportunity_name",
  "opportunity_phase", "observed_at", "linked_subject_kind", "linked_subject_id",
]);

const CORRECTION_KEYS = Object.freeze([
  "schema_version", "idempotency_key", "subject_ref", "correction_record_id",
  "corrected_fields", "reason",
]);

// Q103's concurrent edit, as a caller may state it — and the three halves it may
// NOT state.
//
// `subject_ref.expected_state_digest` IS the base version: the caller says which
// version it decided against, in the same shape every other operation uses for
// the same purpose. It is REQUIRED here, because a concurrent-edit question with
// no base is not a question. The CURRENT version is read off the stored row and
// is never a caller input.
//
// An edit names a FIELD and a VALUE DIGEST and nothing else. `edited_by` and
// `edited_at` are the derived principal and the server clock; `field_class` is
// derived by the kernel from its own registry, and was removed from that shape
// precisely because a caller labelling a lifecycle field `routine` bought the one
// branch that silently overwrites the other partner.
const RECONCILIATION_KEYS = Object.freeze([
  "schema_version", "idempotency_key", "subject_ref", "edits",
]);
const EDIT_REF_KEYS = Object.freeze(["field", "value_digest"]);

const READ_SELECTOR_KEYS = Object.freeze([
  "kind", "subject_kind", "subject_id", "legacy_row_id", "opportunity_id",
]);

export const V5_J102_READ_KINDS = Object.freeze([
  "subject", "subject_events", "first_party_record", "evidence_subject_links",
  "salesforce_references", "correction_receipts", "reconciliation_items",
  "compatibility_view", "migration_shadow",
  // Q103's second half. COMPOSED HERE rather than added to ops.j102_read, which
  // is a closed vocabulary and would have needed a SQL change to gain a kind:
  // this one is built from the two SQL read kinds that already exist, so the
  // exposure lands without touching the candidate.
  "ownership_and_freshness",
]);

/**
 * The read kinds this module ANSWERS ITSELF from other reads, rather than
 * forwarding to ops.j102_read.
 *
 * Every kind outside this set is passed through verbatim and the database's own
 * closed vocabulary decides it. A kind inside it never reaches ops.j102_read at
 * all — which is exactly why it needs no SQL change — and is instead composed
 * from readbacks that were each verified inside PostgreSQL.
 */
export const V5_J102_COMPOSED_READ_KINDS = Object.freeze(["ownership_and_freshness"]);

/**
 * WHICH TRANSITION EACH WRITE OPERATION PERFORMS, and where the answer comes
 * from when there is more than one.
 *
 * Two operations dispatch on LOADED state rather than on a caller field, which
 * is the point: `record-deal-execution` chooses the lease or the purchase
 * transition from the stored deal's own instrument kind, so a caller cannot ask
 * for the purchase semantics on a lease deal, and `record-deal-axis` chooses
 * among the four orthogonal axes from a declared axis name that is validated
 * against the registry.
 */
/**
 * EXPORTED, because the SQL admission map has to be checked against it.
 *
 * `ops.j102_admission_policy()` restates which operation may perform which
 * transition so that a direct caller holding the writer's EXECUTE grant cannot
 * name a partner-only transition beside a routine operation. For the two
 * DISPATCHING operations the answer is not a single transition, and the SQL map
 * has to know both halves. Exporting these two tables — rather than letting the
 * parity test hard-code them — is what keeps the SQL restatement a transcription
 * of this file rather than a second opinion about it.
 */
export const V5_J102_AXIS_TRANSITIONS = Object.freeze({
  commission_agreement_state: "record-commission-agreement",
  invoice_state: "record-invoice-issued",
  payment_state: "record-payment",
  completion_state: "record-completion",
});

/** Q094's split: the transition `record-deal-execution` runs, by stored instrument. */
export const V5_J102_INSTRUMENT_TRANSITIONS = Object.freeze({
  lease: "record-lease-execution",
  renewal: "record-lease-execution",
  amendment: "record-lease-execution",
  purchase: "record-purchase-contract-execution",
});

const AXIS_TRANSITIONS = V5_J102_AXIS_TRANSITIONS;

const OPERATION_SCHEMAS = deepFreeze({
  "read-cre-lifecycle": {
    write: false, humanOnly: false, authorityOnly: false, transition: null,
    keys: ["schema_version", "selector"], required: ["selector"],
  },
  // The one write that is NOT a transition. It records a first-party business
  // fact — a mandate, a commitment, a diligence outcome, a closing date, an
  // invoice, a payment, a completion, a failure reason — so that a later
  // transition has something server-held to be judged against. It advances no
  // lifecycle state on its own, which is stated on its every answer.
  "record-lifecycle-fact": {
    write: true, humanOnly: false, authorityOnly: false, transition: null,
    keys: FACT_KEYS, required: ["idempotency_key", "fact"],
  },
  // BLOCK-2's other half. F01 owns documents and corporate artifacts and carries
  // no lifecycle binding on either, and this slice does not patch F01's schema to
  // add one. The association therefore lives in a J102-OWNED relation, written
  // through this operation by a verified partner against a document version F01
  // really holds and a subject this rail really holds.
  //
  // authorityOnly, and the reason is H5's reason. Saying "this executed lease is
  // THIS client's deal" is a partner's statement about a transaction, not a
  // clerical act, and an agent that could make it could bind any authentic lease
  // to any deal and then present it as evidence.
  "record-evidence-subject-link": {
    write: true, humanOnly: false, authorityOnly: true, transition: null,
    keys: LINK_KEYS, required: ["idempotency_key", "link"],
  },
  // THE THREE INITIALIZATIONS. Each creates one subject and performs no
  // transition, which is why `transition` is null on all three and
  // `initialization` names the kernel contract instead. None is authorityOnly:
  // creating an empty prospect, an assignment shell under a client's active
  // engagement, or an LOI draft carries no evidence-bound fact, and the class
  // that may ADVANCE each of them is the class that may create it. The kernel
  // holds the same two classes on each contract and the SQL map restates them, so
  // an agent that reached the writer directly is admitted no wider.
  "initialize-prospect-relationship": {
    write: true, humanOnly: false, authorityOnly: false, transition: null,
    initialization: "initialize-prospect-relationship", subject_kind: "relationship",
    keys: INITIALIZATION_PAYLOAD_KEYS, required: ["idempotency_key", "declared"],
  },
  "initialize-assignment": {
    write: true, humanOnly: false, authorityOnly: false, transition: null,
    initialization: "initialize-assignment", subject_kind: "assignment",
    keys: INITIALIZATION_PAYLOAD_KEYS,
    required: ["idempotency_key", "declared", "related_refs"],
  },
  "initialize-property-negotiation": {
    write: true, humanOnly: false, authorityOnly: false, transition: null,
    initialization: "initialize-property-negotiation", subject_kind: "property_negotiation",
    keys: INITIALIZATION_PAYLOAD_KEYS,
    required: ["idempotency_key", "declared", "related_refs"],
  },
  "record-representation-agreement": {
    write: true, humanOnly: false, authorityOnly: false,
    transition: "establish-client-and-engagement",
    subject_kind: "relationship",
    keys: TRANSITION_PAYLOAD_KEYS, required: ["idempotency_key", "subject_ref", "evidence_refs"],
  },
  "open-cre-assignment": {
    write: true, humanOnly: false, authorityOnly: false,
    transition: "open-assignment", subject_kind: "assignment",
    keys: TRANSITION_PAYLOAD_KEYS, required: ["idempotency_key", "subject_ref", "evidence_refs"],
  },
  "record-loi-submission": {
    write: true, humanOnly: false, authorityOnly: false,
    transition: "record-loi-submission", subject_kind: "property_negotiation",
    keys: TRANSITION_PAYLOAD_KEYS, required: ["idempotency_key", "subject_ref", "evidence_refs"],
  },
  "record-loi-acceptance": {
    write: true, humanOnly: false, authorityOnly: false,
    transition: "record-loi-acceptance", subject_kind: "property_negotiation",
    keys: TRANSITION_PAYLOAD_KEYS, required: ["idempotency_key", "subject_ref", "evidence_refs"],
  },
  // authorityOnly: the kernel admits only a verified partner, and the check is
  // made HERE as well so an agent that somehow reached this function still
  // refuses before any load happens.
  "commit-winning-property": {
    write: true, humanOnly: false, authorityOnly: true,
    transition: "commit-winning-property", subject_kind: "assignment",
    keys: TRANSITION_PAYLOAD_KEYS, required: ["idempotency_key", "subject_ref", "evidence_refs"],
  },
  "record-deal-execution": {
    write: true, humanOnly: false, authorityOnly: false,
    transition: "dispatch_on_instrument_kind", subject_kind: "deal",
    keys: TRANSITION_PAYLOAD_KEYS, required: ["idempotency_key", "subject_ref", "evidence_refs"],
  },
  "record-diligence-outcome": {
    write: true, humanOnly: false, authorityOnly: false,
    transition: "record-diligence-outcome", subject_kind: "deal",
    keys: TRANSITION_PAYLOAD_KEYS, required: ["idempotency_key", "subject_ref", "evidence_refs"],
  },
  "record-deal-closing": {
    write: true, humanOnly: false, authorityOnly: true,
    transition: "record-deal-closing", subject_kind: "deal",
    keys: TRANSITION_PAYLOAD_KEYS, required: ["idempotency_key", "subject_ref", "evidence_refs"],
  },
  "cancel-pending-deal": {
    write: true, humanOnly: false, authorityOnly: true,
    transition: "cancel-pending-deal", subject_kind: "deal",
    keys: TRANSITION_PAYLOAD_KEYS, required: ["idempotency_key", "subject_ref", "evidence_refs"],
  },
  "record-deal-axis": {
    write: true, humanOnly: false, authorityOnly: false,
    transition: "dispatch_on_declared_axis", subject_kind: "deal",
    keys: TRANSITION_PAYLOAD_KEYS, required: ["idempotency_key", "subject_ref", "evidence_refs", "declared"],
  },
  "link-salesforce-reference": {
    write: true, humanOnly: false, authorityOnly: false, transition: null,
    keys: REFERENCE_KEYS,
    required: ["idempotency_key", "opportunity_id", "opportunity_name", "opportunity_phase",
      "observed_at"],
  },
  // humanOnly AND authorityOnly. Q082's correction is the one path that changes
  // state without new business evidence, so it is the one path that must be a
  // person: an agent cannot correct the record on its own authority, and no
  // assistant text is ever the approval.
  "record-lifecycle-correction": {
    write: true, humanOnly: true, authorityOnly: true, transition: null,
    keys: CORRECTION_KEYS,
    required: ["idempotency_key", "subject_ref", "correction_record_id", "corrected_fields", "reason"],
  },
  // NEITHER humanOnly NOR authorityOnly, deliberately. Raising a conflict is not
  // an exercise of authority — it is the record layer noticing that two writers
  // disagree, and both classes edit records. It RESOLVES nothing: the item lands
  // unresolved and visible, `resolved_by_machine` is false in the kernel, in this
  // module and as a CHECK on the relation, and no lifecycle state moves.
  "record-lifecycle-reconciliation": {
    write: true, humanOnly: false, authorityOnly: false, transition: null,
    keys: RECONCILIATION_KEYS,
    required: ["idempotency_key", "subject_ref", "edits"],
  },
  // A WRITE ONLY IN THE SENSE THAT IT APPENDS ITS OWN OBSERVATION. It changes no
  // business record on either side, which is why it is not authorityOnly.
  "run-migration-shadow": {
    write: true, humanOnly: false, authorityOnly: false, transition: null,
    keys: ["schema_version", "idempotency_key"], required: ["idempotency_key"],
  },
});

/** The closed caller schemas, for the parent's registration and for tests. */
export function v5J102StoreOperationSchemas() {
  return OPERATION_SCHEMAS;
}

/**
 * The registration description the parent may build a tool surface from.
 *
 * A DESCRIPTION, NOT A REGISTRATION. This module does not reach tools.js, mcp.js
 * or the mutation registry, and calling this function registers nothing. The
 * four false flags at the foot of each entry say what the parent still owes.
 */
export function v5J102ToolRegistrations() {
  const roles = {
    "read-cre-lifecycle":
      "Read lifecycle subjects, events, references, receipts and compatibility projections with recomputed integrity; no side write.",
    "record-lifecycle-fact":
      "Append one authenticated first-party business record, bound to the exact subject it is about, so a later transition has server-held evidence; advances no lifecycle state.",
    "record-evidence-subject-link":
      "Append one partner-authored association between an exact F01 document version or corporate artifact and one lifecycle subject; advances no lifecycle state and creates no document.",
    "initialize-prospect-relationship":
      "Create one relationship in the PROSPECT state. It is not a Client: an active signed representation agreement is still what creates client status.",
    "initialize-assignment":
      "Create one Assignment under an already ACTIVE Engagement held by a CLIENT, in the earliest research phase. It reaches no search and appends no assignment_opened event: open-cre-assignment on a mandate record is the only producer of either. It does not by itself force a mandate before an LOI — record-loi-submission admits a research assignment — and that ordering is an unsettled owner question.",
    "initialize-property-negotiation":
      "Create one property negotiation under an Assignment that is still open (research, search or negotiation), as an LOI DRAFT. It submits nothing, requires no mandate, and creates no Deal.",
    "record-representation-agreement":
      "Establish Client status and the active Engagement together from an active signed representation agreement, atomically or not at all.",
    "open-cre-assignment":
      "Open one Assignment in research or search under an active Engagement; never duplicates the client.",
    "record-loi-submission":
      "Record an LOI submission and move the Assignment to negotiation; creates no Deal.",
    "record-loi-acceptance":
      "Record a counterparty acceptance on one property negotiation; creates no Deal and supersedes no alternative.",
    "commit-winning-property":
      "Select and commit to the winning accepted LOI, creating the one pending negotiation Deal; retains every alternative negotiation.",
    "record-deal-execution":
      "Mark the executed lease, or the executed-but-pending purchase contract entering due diligence, from the stored deal's own instrument kind.",
    "record-diligence-outcome":
      "Record a waived, satisfied or failed diligence outcome; never cancels the deal.",
    "record-deal-closing":
      "Close the Deal on the actual final closing date, committing the business state, the closing axis and the date together or refusing together.",
    "cancel-pending-deal":
      "Cancel a pending Deal with its preserved reason and return the Assignment to search or negotiation; the Client relationship is untouched.",
    "record-deal-axis":
      "Advance exactly one of the commission, invoice, payment or completion axes, leaving every other axis unchanged.",
    "link-salesforce-reference":
      "Record one external Salesforce opportunity reference with its own name and phase and progressively link it; never sets DoctorCRE lifecycle state.",
    "record-lifecycle-correction":
      "Append one human, authority-held correction receipt with its reason and evidence; history is preserved and nothing is overwritten silently.",
    "record-lifecycle-reconciliation":
      "Judge one concurrent edit against the version the caller decided against and the version the database holds, and append a VISIBLE unresolved conflict item when they differ; it merges nothing, resolves nothing and moves no lifecycle state.",
    "run-migration-shadow":
      "Compare every legacy deal row with the lifecycle projection its Salesforce reference links, side by side, and append one immutable run record of matches, differences and unlinked rows; modifies neither side and retires no caller.",
  };
  const handlers = {
    "read-cre-lifecycle": "readCreLifecycle",
    "record-lifecycle-fact": "recordLifecycleFact",
    "record-evidence-subject-link": "recordEvidenceSubjectLink",
    "initialize-prospect-relationship": "initializeProspectRelationship",
    "initialize-assignment": "initializeAssignment",
    "initialize-property-negotiation": "initializePropertyNegotiation",
    "record-representation-agreement": "recordRepresentationAgreement",
    "open-cre-assignment": "openCreAssignment",
    "record-loi-submission": "recordLoiSubmission",
    "record-loi-acceptance": "recordLoiAcceptance",
    "commit-winning-property": "commitWinningProperty",
    "record-deal-execution": "recordDealExecution",
    "record-diligence-outcome": "recordDiligenceOutcome",
    "record-deal-closing": "recordDealClosing",
    "cancel-pending-deal": "cancelPendingDeal",
    "record-deal-axis": "recordDealAxis",
    "link-salesforce-reference": "linkSalesforceReference",
    "record-lifecycle-correction": "recordLifecycleCorrection",
    "record-lifecycle-reconciliation": "recordLifecycleReconciliation",
    "run-migration-shadow": "runMigrationShadow",
  };
  const entries = V5_J102_OPERATIONS.map(name => ({
    name,
    store_operation: name,
    write: OPERATION_SCHEMAS[name].write,
    humanOnly: OPERATION_SCHEMAS[name].humanOnly,
    authorityOnly: OPERATION_SCHEMAS[name].authorityOnly,
    role: roles[name],
    handler: handlers[name],
    input_keys: [...OPERATION_SCHEMAS[name].keys],
    required_keys: [...OPERATION_SCHEMAS[name].required],
    // M-a. THE PREREQUISITE THAT IS NOT `authorityOnly`, AND IS AS BINDING.
    //
    // `authorityOnly: false` used to be the whole of what this description said
    // about who could complete an operation, and for the document- and
    // artifact-backed transitions it stopped being true when BLOCK-2 landed. Their
    // evidence now requires a stored evidence -> subject association, and
    // `record-evidence-subject-link` is authorityOnly — so a sponsored agent can
    // START one of these and cannot finish it unless a verified partner has
    // already written the association for that exact document VERSION.
    //
    // The version half is the sharp edge and is stated rather than left to be
    // discovered: an association is pinned to (ref, version_no, content_digest),
    // so a lease that gains a version at signature is not the document that was
    // associated, and it needs a NEW partner-written association AFTER signing.
    // For `record-deal-execution` that means the ordinary path cannot be completed
    // by an agent alone even in principle.
    //
    // NOTHING NEW IS APPROVED HERE and no writer class is widened. This is a
    // DESCRIPTION of a prerequisite that already binds, derived from the kernel's
    // own evidence contracts, so `capabilities()` stops reporting an authority
    // requirement that is not the operative one.
    ...associationPrerequisite(name),
    ...primarySubjectPrerequisite(name),
    // Registered in tools.js, sealed in SCAC v80 (0705) and bound to migration
    // 0704. Acceptance is a partner's act and is never claimed here.
    registered_in_scac: true,
    registered_in_mutation_registry: true,
    migration_bound: true,
    accepted: false,
  }));
  // THE AUTHORITY-ROUTED DOOR TO THE SAME FACT WRITER. The server routes a verb
  // over the partner's authority credential only when the VERB is authorityOnly,
  // and every non-authorityOnly verb runs on the writer credential, where F01
  // derives a sponsored agent. So a partner can author the four partner-only
  // fact kinds (closing settlement, deal failure, winning-property commitment,
  // lifecycle correction) only through a second verb over the same store
  // operation that IS authorityOnly. It adds no operation and widens nothing: the
  // store's H5 check and the SQL CHECK still decide on the database's class.
  const fact = entries.find(e => e.name === "record-lifecycle-fact");
  entries.push({
    ...fact,
    name: V5_J102_PARTNER_FACT_VERB,
    store_operation: "record-lifecycle-fact",
    authorityOnly: true,
    role: "Append one partner-authored first-party business record (closing settlement, deal failure, winning-property commitment or lifecycle correction) on the partner's authority credential, bound to the exact subject it is about; advances no lifecycle state.",
  });
  return deepFreeze(entries);
}

/** The authority-routed verb name for partner-authored facts; see above. */
export const V5_J102_PARTNER_FACT_VERB = "record-partner-lifecycle-fact";

/**
 * THE SUBJECT AN OPERATION CANNOT CREATE, said per operation.
 *
 * Every write operation here advances a subject that MUST ALREADY EXIST: the
 * handler refuses `subject_not_found`, and ops.j102_apply_transition now refuses
 * a proposed primary subject with a null compare-and-swap operand rather than
 * seeding one with vacuous prerequisites. The only subjects created anywhere are
 * the two the kernel's own evaluator creates — the engagement named by
 * `declared.new_subject_id` in establish-client-and-engagement, and the pending
 * deal named by `declared.new_deal_id` in commit-winning-property — and both are
 * COUPLED subjects of a transition whose primary was loaded.
 *
 * The pair below is asserted behaviourally in the suite: the kernel is run and
 * its proposed_state is checked for exactly these kinds appearing where no input
 * subject did, so this description cannot drift from the evaluator.
 */
const CREATED_COUPLED_SUBJECTS = Object.freeze({
  "establish-client-and-engagement": Object.freeze(["engagement"]),
  "commit-winning-property": Object.freeze(["deal"]),
});

/**
 * WHICH OPERATION CREATES A SUBJECT OF EACH KIND, derived rather than restated:
 * the three initialization schemas above name three kinds, and the two coupled
 * creations name the other two. This is what lets a capability description say
 * "this operation needs an assignment, and THIS is where one comes from" instead
 * of reporting a prerequisite with no answer beside it.
 */
function creatingOperationFor(subject_kind) {
  for (const name of V5_J102_OPERATIONS) {
    const schema = OPERATION_SCHEMAS[name];
    if (schema.initialization !== undefined &&
        v5J102InitializationContract(schema.initialization).subject_kind === subject_kind) {
      return name;
    }
  }
  for (const [transition_id, kinds] of Object.entries(CREATED_COUPLED_SUBJECTS)) {
    if (!kinds.includes(subject_kind)) continue;
    const name = V5_J102_OPERATIONS.find(op => OPERATION_SCHEMAS[op].transition === transition_id);
    if (name !== undefined) return name;
  }
  return null;
}

function primarySubjectPrerequisite(operation) {
  const schema = OPERATION_SCHEMAS[operation];
  if (schema.initialization !== undefined) {
    const contract = v5J102InitializationContract(schema.initialization);
    return {
      // An initialization CREATES its subject, so it requires no existing one —
      // and that is the one place in this module where that is true. It is stated
      // beside the parent it DOES require, so the pair cannot be read as "this
      // operation requires nothing".
      requires_existing_primary_subject: false,
      creates_primary_subject_kind: contract.subject_kind,
      creates_coupled_subject_kinds: [],
      requires_existing_parent_subject_kind: contract.parent_subject_kind,
      parent_subject_created_by_operation:
        contract.parent_subject_kind === null
          ? null : creatingOperationFor(contract.parent_subject_kind),
      required_context_subject_kinds: contract.required_context.map(rule => rule.subject),
      performs_transition: false,
    };
  }
  if (schema.transition === null) {
    return { requires_existing_primary_subject: false, creates_coupled_subject_kinds: [] };
  }
  const transitions = schema.transition === "dispatch_on_instrument_kind"
    ? [...new Set(Object.values(V5_J102_INSTRUMENT_TRANSITIONS))].sort()
    : schema.transition === "dispatch_on_declared_axis"
      ? [...new Set(Object.values(V5_J102_AXIS_TRANSITIONS))].sort()
      : [schema.transition];
  const created = [...new Set(transitions.flatMap(id => CREATED_COUPLED_SUBJECTS[id] ?? []))].sort();
  const primary_subject_kind = v5J102TransitionContract(transitions[0]).subject_kind;
  return {
    requires_existing_primary_subject: true,
    primary_subject_kind,
    creates_coupled_subject_kinds: created,
    // Named on the description itself, because "where does a subject of this kind
    // come from" is the first thing a reader of a capability list needs. It read
    // `null` while nothing created one; it now names the operation that does, and
    // a kind with no creating operation would report null again rather than
    // implying one exists.
    primary_subject_created_by_operation: creatingOperationFor(primary_subject_kind),
  };
}

/**
 * Which evidence kinds an operation's transition can rest on, and which of those
 * need a partner-written association first. Derived from the kernel's exported
 * contracts — this function states no requirement the kernel does not already
 * impose.
 */
function associationPrerequisite(operation) {
  const schema = OPERATION_SCHEMAS[operation];
  if (schema.transition === null) {
    return {
      required_evidence_alternatives: null,
      requires_partner_written_evidence_association: false,
      association_required_evidence_kinds: [],
      association_prerequisite: null,
    };
  }
  const transitions = schema.transition === "dispatch_on_instrument_kind"
    ? [...new Set(Object.values(V5_J102_INSTRUMENT_TRANSITIONS))].sort()
    : schema.transition === "dispatch_on_declared_axis"
      ? [...new Set(Object.values(V5_J102_AXIS_TRANSITIONS))].sort()
      : [schema.transition];
  const alternatives = transitions.flatMap(id =>
    v5J102TransitionContract(id).required_evidence_alternatives);
  const kinds = [...new Set(alternatives.flat())].sort();
  // Only document and artifact evidence binds through the stored association; a
  // first-party record carries its own typed binding and needs none.
  const associated = kinds.filter(kind => {
    const source = v5J102EvidenceContract(kind).source;
    return source === "f01_document" || source === "f01_corporate_artifact";
  });
  return {
    dispatched_transitions: transitions,
    required_evidence_alternatives: alternatives.map(set => [...set]),
    requires_partner_written_evidence_association: associated.length > 0,
    association_required_evidence_kinds: associated,
    association_prerequisite: associated.length === 0 ? null : {
      written_by_operation: "record-evidence-subject-link",
      written_by_authorization_class: "verified_partner",
      pinned_on: ["evidence_ref", "version_no", "content_digest", "subject"],
      must_precede_this_operation: true,
      why: `${operation} rests on ${associated.join(", ")}, which binds to its subject through a stored evidence-subject association rather than through the document itself. The association is written only by record-evidence-subject-link, which is authorityOnly, and it is pinned to one exact document version — so a document that gains a version needs a NEW partner-written association before this operation can use it.`,
    },
  };
}

// ---------------------------------------------------------------------------
// The authenticated transaction context.
//
// The actor arrives from the handler's own authenticated context — never from a
// tool payload — and is then CHECKED AGAINST THE DATABASE's independently
// derived answer. Two derivations that disagree is not a value to reconcile; it
// is a request that cannot be attributed, so it refuses before any write.
// ---------------------------------------------------------------------------

const CONTEXT_KEYS = Object.freeze(["actor"]);

function assertAuthenticatedContext(context) {
  assertClosed(context ?? {}, CONTEXT_KEYS, CONTEXT_KEYS, "context");
  const actor = context.actor;
  if (!isPlainObject(actor) || !isKnownActor(actor.slug)) {
    fail("unauthenticated_actor",
      "context.actor must be an authenticated actor from the server-established grant",
      { path: "context.actor" });
  }
  const authorization_class = authorizationClassForActor(actor);
  // identity.js knows more classes than this lifecycle admits — probe, review
  // and unsponsored agents among them. Rather than letting the kernel throw an
  // unknown-vocabulary error on a class that is simply not entitled to move a
  // business record, the boundary is drawn here, by name.
  if (!V5_J102_ACTOR_CLASSES.includes(authorization_class)) {
    fail("actor_class_not_admitted_for_lifecycle",
      `${actor.slug} holds ${authorization_class}, which is not admitted to the CRE lifecycle`,
      { actor_slug: actor.slug, authorization_class,
        admitted: [...V5_J102_ACTOR_CLASSES] });
  }
  // OWNER RULING (c), 2026-09-25: EVERY WRITE IS RECORDED UNDER THE SPONSORING
  // PARTNER. The same server-derived sponsor mcp.js hands the database as
  // carr.sponsoring_human_slug: a partner's own session is its own sponsor, an
  // agent's is the sponsor its authenticated grant carries. Never a payload field.
  const scope = personalScopeForActor(actor);
  if (scope?.status !== "personal" || !["joe", "dell"].includes(scope.sponsor)) {
    fail("sponsoring_partner_unavailable",
      `${actor.slug} carries no verified sponsoring partner, so no lifecycle write can be recorded under one`,
      { actor_slug: actor.slug, scope_status: scope?.status ?? null });
  }
  return deepFreeze({
    slug: actor.slug,
    human: actor.human === true,
    authorization_class,
    sponsoring_partner: scope.sponsor,
    derived_by: "authenticated_handler_context",
  });
}

/**
 * The kernel's actor is WHO decides; the sponsor is under whom the write is
 * RECORDED, which only the store and the SQL writer stamp. The kernel's actor
 * shape stays closed at its four keys.
 */
function kernelActor(principal) {
  const { sponsoring_partner: _recordedUnder, ...actor } = principal;
  return actor;
}

function assertOperationAuthority(operation, principal) {
  const schema = OPERATION_SCHEMAS[operation];
  if (schema.humanOnly && principal.human !== true) {
    fail("human_only_operation_refused",
      `${operation} is humanOnly; ${principal.slug} is not a human principal`,
      { operation, actor_slug: principal.slug });
  }
  if (schema.authorityOnly && principal.authorization_class !== "verified_partner") {
    fail("authority_only_operation_refused",
      `${operation} is authorityOnly; ${principal.slug} holds ${principal.authorization_class}`,
      { operation, actor_slug: principal.slug, authorization_class: principal.authorization_class });
  }
  return true;
}

// ---------------------------------------------------------------------------
// Envelopes.
//
// `record` is the exact preimage the kernel produced (or, for the store-own
// kinds, the exact preimage defined here). `record_digest` is its digest, and
// the ops CHECK constraints recompute both inside PostgreSQL, so an envelope
// that lies about its own bytes cannot be stored at all.
// ---------------------------------------------------------------------------

function storeEnvelope(record_kind, record, extra = {}) {
  if (!V5_J102_STORE_RECORD_KINDS.includes(record_kind)) {
    fail("unknown_record_kind", `"${record_kind}" is not a registered stored record kind`,
      { record_kind });
  }
  return deepFreeze({
    schema_version: V5_J102_ENVELOPE_SCHEMA_VERSION,
    record_kind,
    tenant: ORGANIZATION_TENANT_ID,
    record,
    record_digest: digest(record),
    domain_policy_digest: v5J102PolicyDigest(),
    decision_subset_digest: v5J102DecisionSubsetDigest(),
    ...extra,
  });
}

/** The exact canonical bytes an envelope hashes to, for the byte-for-byte fixtures. */
export function v5J102EnvelopeCanonicalBytes(envelope) {
  return canonicalJson(envelope);
}

export function v5J102StoreEnvelope(record_kind, record, extra = {}) {
  return storeEnvelope(record_kind, record, extra);
}

export function storedSubjectRecord({ subject, transition_id, prior_state_digest, updated_by,
  sponsoring_partner, updated_at }) {
  return {
    schema_version: V5_J102_STORED_SUBJECT_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    subject_kind: subject.subject_kind,
    subject_id: subject.subject_id,
    state: subject,
    established_by_transition: transition_id,
    prior_state_digest: prior_state_digest ?? null,
    updated_by,
    sponsoring_partner,
    updated_at,
  };
}

export function storedEventRecord({ event, transition_id, evidence_references, recorded_by,
  sponsoring_partner, recorded_at }) {
  return {
    schema_version: V5_J102_STORED_EVENT_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    event,
    transition_id,
    // The exact evidence the transition rests on, by reference, so the history
    // says what a change was judged against rather than only what it changed.
    //
    // THE WHOLE SET GOES ON EVERY EVENT OF ONE CALL, including a coupled event
    // that names no single pin of its own — `assignment_returned_to_market` has
    // no `evidence_reference` in its detail, because the kernel gives it none.
    // The two are different questions: which pin an event NAMES is inside
    // `event`, and what the transition that produced it RESTED ON is this array,
    // which is the same answer for every event the transition appends. The
    // database enforces both, separately.
    evidence_references: [...evidence_references],
    recorded_by,
    sponsoring_partner,
    recorded_at,
  };
}

export function storedFirstPartyFactRecord({
  fact, recorded_by, recorded_by_authorization_class, recorded_at,
}) {
  return {
    schema_version: V5_J102_STORED_FACT_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    record_kind: fact.record_kind,
    record_id: fact.record_id,
    // THE BINDING, written once and never re-stated. A closing settlement says
    // which deal closed at the moment somebody records that it closed; a later
    // transition reads that and cannot point the record at a different deal.
    subject_kind: fact.subject_kind,
    subject_id: fact.subject_id,
    reason: fact.reason ?? null,
    detail: fact.detail ?? null,
    closing_date: fact.closing_date ?? null,
    supporting_document_id: fact.supporting_document_id ?? null,
    recorded_by,
    // THE AUTHOR'S CLASS, derived from the authenticated principal that wrote the
    // row. It is what makes "a partner stated this" checkable afterwards.
    recorded_by_authorization_class,
    recorded_at,
    // Stated in the record itself, because this is the shape most easily
    // mistaken for a state change: it is a business fact with an author, and a
    // transition still has to accept it.
    advances_lifecycle_state: false,
  };
}

export function storedEvidenceSubjectLinkRecord({
  link, associated_by, associated_by_authorization_class, associated_at,
}) {
  return {
    schema_version: V5_J102_STORED_EVIDENCE_LINK_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    evidence_source: link.evidence_source,
    // The EXACT pin. A document that gains a version is not this document, so a
    // new version needs its own association rather than inheriting one — which
    // is the same rule the evidence pin itself follows.
    evidence_ref: link.evidence_ref,
    version_no: link.version_no,
    content_digest: link.content_digest,
    subject_kind: link.subject_kind,
    subject_id: link.subject_id,
    associated_by,
    associated_by_authorization_class,
    associated_at,
    // The three things an association is NOT.
    advances_lifecycle_state: false,
    creates_document: false,
    asserts_document_state: false,
  };
}

export function storedSalesforceReferenceRecord({ reference, recorded_by, recorded_at }) {
  return {
    schema_version: V5_J102_STORED_REFERENCE_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    opportunity_id: reference.opportunity_id,
    // Salesforce's own name and phase, preserved verbatim and never mapped.
    opportunity_name: reference.opportunity_name,
    opportunity_phase: reference.opportunity_phase,
    linked_subject_kind: reference.linked_subject_kind,
    linked_subject_id: reference.linked_subject_id,
    observed_at: reference.observed_at,
    is_external_corporate_reference: true,
    phase_label_is_doctorcre_state: false,
    recorded_by,
    recorded_at,
  };
}

/**
 * One visible reconciliation item, as the relation must receive it.
 *
 * IT IS FLAT, AND THAT IS THE RELATION'S SHAPE RATHER THAN A CHOICE. Every other
 * stored record in this module nests the kernel's answer under its own schema
 * version; ops.j102_reconciliation_item instead reads `conflict_kind`,
 * `base_version_digest`, `current_version_digest`, `subject_kind`, `subject_id`
 * and `proposed_by` off the TOP LEVEL, and CHECK-binds `incoming_edits`,
 * `concurrent_edits` and `resolved_by_machine` there too. So the kernel's item
 * fields are carried BYTE FOR BYTE at the top level — including its own
 * schema_version, which is accurate: this is that item — and this module adds
 * exactly two kinds of key beside them.
 *
 * WHAT THIS MODULE ADDS, AND WHY EACH IS THE STORE'S FACT AND NOT THE KERNEL'S:
 *
 *   THE SUBJECT. evaluateConcurrentEdit is never told which record it is judging;
 *   it compares two digests and two edit sets. Which subject those belong to is
 *   the store's own validated reference, and the relation requires it — an item
 *   that cannot say what it is about is not visible in any useful sense.
 *
 *   THE OTHER SIDE'S EVIDENCE. The kernel preserves both edit sets, and on the
 *   uncharacterized branch the concurrent set is genuinely empty — this layer can
 *   prove the subject MOVED and cannot enumerate whose field went where. What it
 *   CAN show is authoritative and is shown instead of guessed: the committed
 *   state as it stands now, and the tail of the append-only history that produced
 *   it, each row naming its transition, its actor and its instant. A person
 *   resolving this has the two versions and the changes between them.
 */
export function storedReconciliationItemRecord({
  item, subject_kind, subject_id, current_state, history_tail, characterized,
}) {
  return {
    ...item,
    subject_kind,
    subject_id,
    // The store's own evidence, labelled as such so nothing here reads as part of
    // the kernel's judgement.
    concurrent_change_evidence: {
      characterized,
      why: characterized
        ? "exactly one committed transition lies between the base and the current row (the row's own prior_state_digest is the base), so the concurrent edit set is that transition's declared write set, each value the digest of what the row now holds"
        : "the current row's prior_state_digest is not the caller's base, or the write that produced it is not a registered transition, so more than one write (or an undeclared one) lies between them; the other writer's field edits cannot be enumerated, and the conflict reconciles visibly rather than merging on an absence",
      current_state,
      current_state_source: "ops.j102_read.subject",
      history_tail,
      history_tail_source: "ops.j102_read.subject_events",
      history_tail_is_complete: false,
    },
    // The three properties a reader of this row needs before acting on it, and
    // the ones the relation itself CHECK-binds.
    resolved_by_machine: false,
    visible: true,
    applied: false,
  };
}

export function storedCorrectionReceiptRecord({
  subject_kind, subject_id, correction_record_id, corrected_fields, reason,
  prior_state_digest, corrected_by, corrected_at,
}) {
  return {
    schema_version: V5_J102_STORED_CORRECTION_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    subject_kind,
    subject_id,
    correction_record_id,
    corrected_fields: [...corrected_fields],
    reason,
    prior_state_digest: prior_state_digest ?? null,
    corrected_by,
    corrected_at,
    // The three properties that make a correction reviewable rather than a
    // quiet edit.
    append_only: true,
    prior_state_preserved: true,
    derived_from_assistant_text: false,
  };
}

// ---------------------------------------------------------------------------
// The store.
// ---------------------------------------------------------------------------

function requireDb(db) {
  if (!db || typeof db.query !== "function") {
    fail("database_handle_required",
      "createCreLifecycleStore requires an injected database handle with query(text, params)");
  }
  return db;
}

async function one(client, text, params = []) {
  const result = await client.query(text, params);
  const rows = result?.rows ?? [];
  return rows.length > 0 ? rows[0] : null;
}

const J = value => JSON.stringify(value ?? null);
const parse = value => (typeof value === "string" ? JSON.parse(value) : value);

/**
 * Build the store. Everything it touches is injected: the database handle, and
 * the authenticated context supplied per call. It opens no connection, reads no
 * environment, discovers no credential and holds no clock.
 */
export function createCreLifecycleStore({ db } = {}) {
  const handle = requireDb(db);

  async function withTransaction(fn) {
    if (typeof handle.transaction === "function") return handle.transaction(fn);
    await handle.query("BEGIN");
    try {
      const result = await fn(handle);
      await handle.query("COMMIT");
      return result;
    } catch (error) {
      try { await handle.query("ROLLBACK"); } catch { /* the original error is the answer */ }
      throw error;
    }
  }

  /**
   * Open one operation: derive the actor and the instant FROM THE SERVER, and
   * refuse when the database's independently derived actor is not the one the
   * handler authenticated.
   */
  async function openOperation(client, operation, principal) {
    const row = await one(client,
      "SELECT ops.f01_principal() AS principal, ops.f01_now_text() AS server_now, " +
      "ops.j102_sponsoring_partner() AS sponsoring_partner");
    if (!row) {
      fail("transaction_context_unavailable",
        "the database did not return a principal; the transaction context was never established",
        { operation });
    }
    const dbPrincipal = parse(row.principal);
    if (dbPrincipal?.actor_slug !== principal.slug) {
      fail("actor_context_mismatch",
        "the database-derived actor is not the handler's authenticated actor; the write cannot be attributed",
        { operation, handler_actor: principal.slug, database_actor: dbPrincipal?.actor_slug ?? null });
    }
    // The database derives the sponsor from the login on its own side; the
    // handler's and the database's must agree, or the write is not attributable.
    if (row.sponsoring_partner !== principal.sponsoring_partner) {
      fail("sponsor_context_mismatch",
        "the database-derived sponsoring partner is not the handler's; the write cannot be recorded under either",
        { operation, handler_sponsoring_partner: principal.sponsoring_partner,
          database_sponsoring_partner: row.sponsoring_partner ?? null });
    }
    // THE CLASS IS COMPARED ON EVERY OPERATION, not only the authorityOnly ones.
    // It used to be checked only where authority was required, which left the
    // ordinary writes attributing an author class the database might not agree
    // with — and since H5 puts the AUTHOR'S CLASS on the record itself, a
    // disagreement there would be a durable false statement about who wrote a
    // business fact rather than a transient one about who is asking.
    if (dbPrincipal.human !== principal.human ||
        dbPrincipal.authorization_class !== principal.authorization_class) {
      // THE ONE DISAGREEMENT THAT IS ADOPTED RATHER THAN REFUSED. A verified
      // partner calling an operation that is NOT authorityOnly arrives, by the
      // server's connection routing, on the ordinary writer credential, and F01
      // derives every writer-credential principal as a sponsored agent. The
      // record must not claim more authority than the credential that wrote it
      // carried, so the store ADOPTS the database's narrower class for this
      // operation and states the split beside the result. It is safe in one
      // direction only: every partner-only transition is authorityOnly, so the
      // adopted class can be refused by the kernel but never admitted beyond
      // what the handler already held. Any other disagreement — a wider
      // database class, a different person, an authorityOnly operation — still
      // refuses, because it cannot be attributed.
      // humanOnly is excluded as well as authorityOnly: an act reserved to a human
      // must never proceed under a class the database says is not one.
      const adoptable = OPERATION_SCHEMAS[operation]?.authorityOnly === false &&
        OPERATION_SCHEMAS[operation]?.humanOnly === false &&
        principal.authorization_class === "verified_partner" && principal.human === true &&
        dbPrincipal.authorization_class === "sponsored_agent" && dbPrincipal.human === false;
      if (!adoptable) {
        fail("actor_context_mismatch",
          "the database principal and the handler's disagree about the actor's class or personhood",
          { operation, actor_slug: principal.slug,
            handler_authorization_class: principal.authorization_class,
            database_authorization_class: dbPrincipal.authorization_class ?? null });
      }
      return {
        now: row.server_now,
        database_principal: dbPrincipal,
        principal: deepFreeze({
          slug: principal.slug,
          human: false,
          authorization_class: "sponsored_agent",
          // Ruling (e): under the partner's own slug; ruling (c): under the
          // partner as sponsor. The same partner, both checked above.
          sponsoring_partner: principal.sponsoring_partner,
          derived_by: "server_established_transaction_context",
        }),
        credential_split: deepFreeze({
          handler_authorization_class: principal.authorization_class,
          handler_human: principal.human,
          credential_authorization_class: dbPrincipal.authorization_class,
          adopted: "credential_class",
          why: `${operation} is not authorityOnly, so it ran on the writer credential; the record carries that credential's class, not the handler's wider one`,
        }),
      };
    }
    return { now: row.server_now, database_principal: dbPrincipal, principal, credential_split: null };
  }

  function requestDigest(operation, payload, principal) {
    // M-b. WHAT THIS DIGEST IS, AND WHAT IT IS NOT.
    //
    // IT IS: this caller's own digest of this caller's own intent — the
    // operation, the actor and the exact validated payload — bound to the
    // idempotency key so that a replay of the same bytes returns the same record
    // and the same key over different bytes refuses rather than substituting one
    // write for another. That property holds for any caller that computes it
    // honestly, including this one.
    //
    // IT IS NOT A PROOF TO THE DATABASE. The payload never crosses the wire, so
    // ops.j102_apply_transition cannot recompute this and does not claim to: it
    // shape-checks the digest and binds it to the key, and its receipt now says
    // exactly that (`request_digest_scope`). A DIRECT caller therefore
    // self-asserts its own idempotency binding; the worst case is replay
    // confusion for that caller, not an escalation, because nothing downstream
    // reads this digest as evidence of anything.
    //
    // WHAT THE DATABASE DOES VOUCH FOR is a different digest and is recomputed
    // rather than supplied: `committed_content_digest` in the receipt, taken from
    // the state digests and event digests that actually landed. The two are
    // reported separately and neither is described as the other.
    return digest({
      schema_version: V5_J102_STORE_SCHEMA_VERSION,
      operation,
      actor_slug: principal.slug,
      payload,
    });
  }

  function begin(operation, payload, context) {
    const schema = OPERATION_SCHEMAS[operation];
    const principal = assertAuthenticatedContext(context);
    assertOperationAuthority(operation, principal);
    const validated = assertClosed(payload ?? {}, schema.keys, schema.required, "payload");
    if (validated.schema_version !== undefined &&
        validated.schema_version !== V5_J102_STORE_SCHEMA_VERSION) {
      fail("unknown_schema_version",
        `payload.schema_version must be "${V5_J102_STORE_SCHEMA_VERSION}"`,
        { operation, expected: V5_J102_STORE_SCHEMA_VERSION });
    }
    if (schema.write) assertIdempotencyKey(validated.idempotency_key, "payload.idempotency_key");
    return { principal, payload: validated };
  }

  function result(operation, decision, reason_id, extra = {}) {
    return deepFreeze({
      schema_version: V5_J102_STORE_SCHEMA_VERSION,
      operation,
      tenant: ORGANIZATION_TENANT_ID,
      decision,
      reason_id,
      ...extra,
      // Stated on every result: persistence is a record, never an act in the
      // world. Nothing here signs, sends, files, pays, calls Salesforce or
      // touches a Tour.
      provider_calls: 0,
      salesforce_calls: 0,
      documents_sent: 0,
      creates_or_activates_tour: false,
      effects: V5_NO_EFFECTS,
    });
  }

  /**
   * SQL constructs and integrity-checks the saved outcome. Initial application
   * and replay use this same projection, so no freshly evaluated decision can
   * overwrite the meaning of a previously committed mutation — a replay reports
   * what LANDED, not what the world would say now.
   */
  function resultFromOutcome(operation, outcome, principal) {
    if (!isPlainObject(outcome) || outcome.operation !== operation ||
        outcome.actor_slug !== principal.slug) {
      fail("invalid_stored_outcome", "stored outcome operation or actor does not match", { operation });
    }
    // M-2. THE DIAGNOSTIC REASON IS READ UNDER THE NAME THE DATABASE GIVES IT.
    //
    // ops.j102_apply_transition no longer emits a bare `reason_id` beside the
    // properties it enforced: the reason is the KERNEL's diagnostic for the
    // branch its evaluator took, the database cannot recompute it, and echoing it
    // under the same name as derived fields made a caller-asserted string read as
    // an authoritative one. It arrives as `caller_reported_reason_id`, with its
    // own scope label beside it, and the non-transition writers — which DO decide
    // their own reason — still name it `reason_id`.
    const storedReason = outcome.caller_reported_reason_id ?? outcome.reason_id;
    if (typeof storedReason !== "string" || storedReason.length === 0) {
      fail("invalid_stored_outcome", "stored outcome is missing its original reason", { operation });
    }
    const extra = {
      actor_slug: outcome.actor_slug,
      // H4's receipt half: the instant the DATABASE committed under, reported
      // back rather than re-derived, so a caller records the server's answer
      // instead of its own idea of when this happened.
      committed_at: outcome.committed_at ?? null,
      readback: outcome.readback ?? null,
    };
    if (OPERATION_SCHEMAS[operation].initialization !== undefined) {
      Object.assign(extra, {
        initialization_id: outcome.initialization_id,
        created_subject_kind: outcome.created_subject_kind ?? null,
        created_subject_id: outcome.created_subject_id ?? null,
        subject_digests: outcome.subject_digests ?? null,
        event_digests: outcome.event_digests ?? null,
        decision_refs: outcome.decision_refs ?? [],
        decision_refs_source: outcome.decision_refs_source ?? null,
        caller_reported_reason_id: outcome.caller_reported_reason_id ?? null,
        caller_reported_reason_id_scope: outcome.caller_reported_reason_id_scope ?? null,
        admission_policy_id: outcome.admission_policy_id ?? null,
        actor_authorization_class: outcome.actor_authorization_class ?? null,
        // WHAT THE DATABASE ENFORCED, read off the stored outcome rather than
        // asserted here: the created row is exactly the shape the initialization
        // contract fixes, every parent it runs under was locked, unmoved and met
        // its declared conditions, and the subject did not already exist.
        //
        // AND WHICH PARENTS THOSE WERE. The two booleans are unconditional trues
        // in the writer, so on a PARENTLESS creation they describe the empty set —
        // a prospect receipt would otherwise read as though a chain had been
        // walked. `context_subjects_consulted` is the checkable half: `[]` for a
        // prospect, the engagement and relationship keys for an assignment, the
        // assignment key for a negotiation. It is READ OFF the stored outcome,
        // never re-derived here, so a replay reports what the write consulted.
        creation_shape_enforced: outcome.creation_shape_enforced === true,
        required_context_enforced: outcome.required_context_enforced === true,
        parent_subjects_locked_and_unmoved:
          outcome.parent_subjects_locked_and_unmoved === true,
        context_subjects_consulted: outcome.context_subjects_consulted ?? [],
        context_subjects_consulted_count:
          outcome.context_subjects_consulted_count ?? null,
        subject_created: outcome.subject_created === true,
        // THE ANTI-BYPASS STATEMENT, on every initialization receipt. This writer
        // performs no transition, so it checked no transition prerequisite and
        // skipped none: the transitions that follow still require theirs.
        transition_applied: false,
        transition_prerequisites_bypassed: false,
        advances_lifecycle_state: false,
        evidence_required: false,
        evidence_supplied: 0,
        request_digest_scope: outcome.request_digest_scope ?? null,
        committed_content_digest: outcome.committed_content_digest ?? null,
        committed_content_digest_source: outcome.committed_content_digest_source ?? null,
        partial_application: false,
        free_form_stage_update: false,
      });
    } else if (OPERATION_SCHEMAS[operation].transition !== null) {
      Object.assign(extra, {
        transition_id: outcome.transition_id,
        subject_digests: outcome.subject_digests ?? null,
        event_digests: outcome.event_digests ?? null,
        // M-2's receipt half. The database DERIVES both of these from the
        // admission contract for the transition it actually applied, rather than
        // echoing the diagnostics it was handed, and it says so in its own
        // `*_source` fields — carried through here so a caller records which of
        // the two they are reading. The kernel's diagnostic reason is beside
        // them under its own name, labelled as the caller's assertion.
        coupled_facts_committed: outcome.coupled_facts_committed ?? [],
        coupled_facts_committed_source: outcome.coupled_facts_committed_source ?? null,
        decision_refs: outcome.decision_refs ?? [],
        decision_refs_source: outcome.decision_refs_source ?? null,
        caller_reported_reason_id: outcome.caller_reported_reason_id ?? null,
        caller_reported_reason_id_scope: outcome.caller_reported_reason_id_scope ?? null,
        // Q103: read off the STORED outcome, so a replay reports the merge the
        // original write made rather than a plain landing.
        ...(Array.isArray(outcome.concurrent_merges) && outcome.concurrent_merges.length > 0
          ? { auto_merged: true, concurrent_merges: outcome.concurrent_merges,
              concurrent_merges_scope: outcome.concurrent_merges_scope ?? null,
              last_writer_wins: false, silent_overwrite: false }
          : {}),
        evidence_rechecked_under_lock: outcome.evidence_rechecked_under_lock === true,
        evidence_bound_under_lock: outcome.evidence_bound_under_lock === true,
        // BLOCK-2's receipt half, reported rather than asserted here: WHICH
        // subject the database bound every evidence pin to, and whether the
        // binding was to that exact subject rather than merely to something in
        // the lock set. Read off the stored outcome, never re-derived, so a
        // replay reports what the write actually enforced.
        evidence_bound_to_primary_subject:
          outcome.evidence_bound_to_primary_subject === true,
        primary_subject_kind: outcome.primary_subject_kind ?? null,
        primary_subject_id: outcome.primary_subject_id ?? null,
        // BLOCK-1's receipt half: which admission map admitted it, and which
        // class the database's own derived principal held while it did.
        admission_policy_id: outcome.admission_policy_id ?? null,
        actor_authorization_class: outcome.actor_authorization_class ?? null,
        // The primary subject was LOADED, and the transition's prerequisites were
        // therefore checked against a committed row. Both are read off the stored
        // outcome rather than asserted here: the database refuses to create a
        // primary subject at all now, so a receipt that said otherwise would be a
        // receipt from a writer this module does not recognise.
        primary_subject_loaded: outcome.primary_subject_loaded === true,
        primary_subject_created: outcome.primary_subject_created === true,
        prerequisites_checked: outcome.prerequisites_checked === true,
        // WHAT THE DATABASE ENFORCED ABOUT THE RESULT, not about the request:
        // every field it wrote landed on the exact value the transition contract
        // computes from the committed prior state and the re-read evidence, the
        // whole coupled subject set was present, and the appended history is
        // exactly the event set this transition produces.
        transition_effects_enforced: outcome.transition_effects_enforced === true,
        required_subject_set_enforced: outcome.required_subject_set_enforced === true,
        required_event_set_enforced: outcome.required_event_set_enforced === true,
        // WHAT THE DATABASE ENFORCED ABOUT THE HISTORY, as distinct from the
        // state: every subject envelope named the transition that actually ran as
        // its own provenance, every event's whole nested payload equalled the one
        // that transition produces, and every event cites exactly the evidence
        // the writer re-read under its own lock.
        subject_provenance_bound_to_transition:
          outcome.subject_provenance_bound_to_transition === true,
        event_payloads_enforced: outcome.event_payloads_enforced === true,
        event_evidence_references_enforced:
          outcome.event_evidence_references_enforced === true,
        created_subject_kinds: outcome.created_subject_kinds ?? [],
        // M-b, carried through: the caller's intent digest and the database's
        // recomputation of what landed are DIFFERENT claims and are reported as
        // two fields, not conflated into one word.
        request_digest_scope: outcome.request_digest_scope ?? null,
        committed_content_digest: outcome.committed_content_digest ?? null,
        committed_content_digest_source: outcome.committed_content_digest_source ?? null,
        // Said on every applied transition, because it is what Q082 buys: the
        // whole coupled set landed, or none of it did.
        partial_application: false,
        free_form_stage_update: false,
      });
    } else if (operation === "record-lifecycle-fact") {
      Object.assign(extra, {
        record_kind: outcome.record_kind, record_id: outcome.record_id,
        record_digest: outcome.record_digest,
        bound_subject_kind: outcome.bound_subject_kind ?? null,
        bound_subject_id: outcome.bound_subject_id ?? null,
        advances_lifecycle_state: false,
      });
    } else if (operation === "record-evidence-subject-link") {
      Object.assign(extra, {
        link_digest: outcome.link_digest,
        evidence_source: outcome.evidence_source,
        bound_subject_kind: outcome.bound_subject_kind ?? null,
        bound_subject_id: outcome.bound_subject_id ?? null,
        advances_lifecycle_state: false,
        creates_document: false,
        asserts_document_state: false,
      });
    } else if (operation === "link-salesforce-reference") {
      Object.assign(extra, {
        opportunity_id: outcome.opportunity_id, reference_digest: outcome.reference_digest,
        linked_subject_kind: outcome.linked_subject_kind ?? null,
        sets_lifecycle_state: false, phase_label_is_doctorcre_state: false,
      });
    } else if (operation === "record-lifecycle-reconciliation") {
      // EVERYTHING HERE IS READ OFF WHAT LANDED, which is what makes a replay
      // report the conflict that was filed rather than the one a fresh evaluation
      // would raise now. The kernel's field-level judgement is not echoed from
      // the diagnostics — it is recomputed from the STORED ITEM's own edits,
      // whose `field_class` the kernel derived from its registry and hashed into
      // the record, so the same answer comes back on the first call and on every
      // replay of it.
      const item = isPlainObject(outcome.readback?.record) ? outcome.readback.record : null;
      const edits = Array.isArray(item?.incoming_edits) ? item.incoming_edits : [];
      Object.assign(extra, {
        subject_kind: outcome.subject_kind ?? null,
        subject_id: outcome.subject_id ?? null,
        conflict_kind: outcome.conflict_kind ?? null,
        base_version_digest: outcome.base_version_digest ?? null,
        current_version_digest: outcome.current_version_digest ?? null,
        subject_moved: outcome.conflict_present === true,
        item_seq: outcome.item_seq ?? null,
        item_digest: outcome.item_digest ?? null,
        reconciliation_item: item,
        incoming_fields: edits.map(edit => edit.field).sort(),
        // Q103's routine/material split, off the stored item rather than a fresh
        // reading of the registry: a field policy has not classified is reported
        // as unclassified, never as routine.
        unclassified_fields: [...new Set(edits
          .filter(edit => edit.field_class === null).map(edit => edit.field))].sort(),
        material_incoming_fields: [...new Set(edits
          .filter(edit => V5_J102_MATERIAL_FIELD_CLASSES.includes(edit.field_class))
          .map(edit => edit.field))].sort(),
        // WHAT THE DATABASE ENFORCED at the write boundary, reported rather than
        // asserted here: the version this item calls current really was current,
        // its state snapshot hashes to that version, and its history evidence
        // ends at the newest committed event.
        current_version_bound_to_committed_row:
          outcome.current_version_bound_to_committed_row === true,
        state_evidence_bound_to_committed_row:
          outcome.state_evidence_bound_to_committed_row === true,
        history_evidence_bound_to_committed_history:
          outcome.history_evidence_bound_to_committed_history === true,
        expected_state_digests: outcome.expected_state_digests ?? null,
        // AND THE THREE PROPERTIES A PERSON READING THE CONFLICT NEEDS.
        visible: outcome.visible === true,
        applied: outcome.applied === true,
        resolved_by_machine: outcome.resolved_by_machine === true,
        merged: false,
        auto_merged_fields: [],
        last_writer_wins: false,
        silent_overwrite: false,
        advances_lifecycle_state: false,
        concurrent_change_characterized: false,
        distinct_proposals_collapsed: outcome.distinct_proposals_collapsed === true,
        records_written: 1,
        caller_reported_reason_id: outcome.caller_reported_reason_id ?? null,
        caller_reported_reason_id_scope: outcome.caller_reported_reason_id_scope ?? null,
        request_digest_scope: outcome.request_digest_scope ?? null,
        committed_content_digest: outcome.committed_content_digest ?? null,
        committed_content_digest_source: outcome.committed_content_digest_source ?? null,
      });
    } else if (operation === "record-lifecycle-correction") {
      Object.assign(extra, {
        receipt_digest: outcome.receipt_digest,
        corrected_fields: outcome.corrected_fields ?? [],
        append_only: true, prior_state_preserved: true,
        derived_from_assistant_text: false,
      });
    } else if (operation === "run-migration-shadow") {
      Object.assign(extra, {
        run_seq: outcome.run_seq, run_digest: outcome.run_digest,
        compared_rows: outcome.compared_rows, matching_rows: outcome.matching_rows,
        differing_rows: outcome.differing_rows, unlinked_rows: outcome.unlinked_rows,
        many_to_one_subjects: outcome.many_to_one_subjects,
        subjects_without_legacy_row: outcome.subjects_without_legacy_row,
        legacy_snapshot_digest: outcome.legacy_snapshot_digest,
        projection_snapshot_digest: outcome.projection_snapshot_digest,
        clean: outcome.clean === true,
        legacy_rows_modified: outcome.legacy_rows_modified,
        retires_any_caller: false,
        advances_lifecycle_state: false,
      });
    } else {
      fail("invalid_stored_outcome", "not a replayable write operation", { operation });
    }
    return result(operation, outcome.decision ?? "allow", storedReason, extra);
  }

  async function replayOutcome(client, operation, request, principal) {
    const row = await one(client,
      "SELECT ops.j102_replay_outcome($1::text, $2::text, $3::text) AS outcome",
      [operation, request.idempotency_key, requestDigest(operation, request, principal)]);
    const outcome = parse(row?.outcome);
    return outcome == null ? null : resultFromOutcome(operation, outcome, principal);
  }

  // -- loading ---------------------------------------------------------------

  /**
   * Load one subject and its CAS digest. A subject that does not exist is not an
   * error here: `establish-client-and-engagement` legitimately creates the
   * engagement, and the transitions that require an existing subject refuse in
   * the kernel with their own reason.
   */
  async function loadSubject(client, subject_kind, subject_id) {
    const row = await one(client, "SELECT ops.j102_subject($1::text, $2::text) AS subject",
      [subject_kind, subject_id]);
    const stored = parse(row?.subject);
    if (stored == null) {
      return { state: null, state_digest: null, prior_state_digest: null,
        established_by_transition: null };
    }
    if (stored.state_digest !== digest(stored.state)) {
      fail("corrupt_stored_subject",
        "the stored subject no longer hashes to its recorded digest; it is refused, not repaired",
        { subject_kind, subject_id });
    }
    // The two provenance fields Q103's characterization reads. Both come from
    // inside the verified envelope; neither is a caller input.
    return { state: stored.state, state_digest: stored.state_digest,
      prior_state_digest: stored.prior_state_digest ?? null,
      established_by_transition: stored.established_by_transition ?? null };
  }

  /**
   * One verified subject readback WITH its provenance columns, through the read
   * door rather than through ops.j102_subject.
   *
   * `loadSubject` above is the WRITE path's reader: it needs the state and the
   * compare-and-swap digest and nothing else. Q103 needs two more columns —
   * `updated_by` and `updated_at` — which ops.j102_read('subject') already
   * returns and which nothing in this module read before. Both readers verify the
   * same way: the digest is recomputed here, in JavaScript, from the state the
   * database handed back, so a row that no longer hashes to its own claim is
   * refused rather than reported as a fact about ownership.
   */
  async function readSubjectVerified(client, subject_kind, subject_id) {
    const row = await one(client, "SELECT ops.j102_read($1::text, $2::jsonb) AS body",
      ["subject", J({ subject_kind, subject_id })]);
    const stored = parse(row?.body)?.body ?? null;
    if (stored == null) return null;
    if (stored.integrity !== "recomputed_from_committed_row") {
      fail("readback_not_recomputed",
        "the subject readback does not claim to have been recomputed from its committed bytes",
        { subject_kind, subject_id, integrity: stored.integrity ?? null });
    }
    if (stored.state_digest !== digest(stored.state)) {
      fail("corrupt_stored_subject",
        "the stored subject no longer hashes to its recorded digest; it is refused, not repaired",
        { subject_kind, subject_id });
    }
    return stored;
  }

  /**
   * The append-only history of one subject, oldest first, as ops.j102_read
   * returns it. Each element is a verified envelope readback, so the last element
   * is the most recent lifecycle change and its `recorded_at` / `recorded_by` are
   * the record layer's own account of when that change happened and who made it.
   */
  async function readSubjectEvents(client, subject_kind, subject_id) {
    const row = await one(client, "SELECT ops.j102_read($1::text, $2::jsonb) AS body",
      ["subject_events", J({ subject_kind, subject_id })]);
    const body = parse(row?.body)?.body ?? [];
    if (!Array.isArray(body)) {
      fail("invalid_stored_history", "the subject history did not read back as a list",
        { subject_kind, subject_id });
    }
    return body;
  }

  function evidenceProvenance(reader, now) {
    return {
      loaded_by: V5_J102_EVIDENCE_LOADER,
      reader,
      loaded_at: now,
      integrity: V5_J102_EVIDENCE_INTEGRITY,
    };
  }

  /**
   * Resolve one caller REFERENCE into a server-loaded evidence record.
   *
   * Returns `{ evidence, recheck }`. The recheck manifest is what travels to
   * ops.j102_apply_transition so the SAME pin is re-read under the lock: a
   * document that gained a version, an artifact that vanished, or a first-party
   * record that was superseded between this read and the write refuses there
   * rather than being applied against a picture that has moved.
   */
  /**
   * Read the independently stored association between one exact evidence pin
   * and one subject, or return null.
   *
   * THE SUBJECT IS THE TRANSITION'S OWN SUBJECT, and it is passed in rather than
   * read out of the association, so this is a lookup that ASKS "is this document
   * version bound to THIS deal" instead of one that reports whichever deal the
   * document happens to mention. The two read the same on a happy path and
   * differ on exactly the case BLOCK-2 names.
   */
  async function loadSubjectBinding(client, { evidence_source, evidence_ref, version_no,
    content_digest, subject_kind, subject_id }) {
    const row = await one(client,
      `SELECT ops.j102_evidence_subject_link($1::text, $2::text, $3::integer, $4::text,
                                             $5::text, $6::text) AS link`,
      [evidence_source, evidence_ref, version_no, content_digest, subject_kind, subject_id]);
    const stored = parse(row?.link);
    if (stored == null || !isPlainObject(stored.record)) return null;
    return { record: stored.record, link_digest: stored.link_digest };
  }

  function unboundEvidenceRefusal(ref, bound, detail) {
    return {
      refusal: {
        evidence_kind: ref.evidence_kind,
        missing_fact: "j102_evidence_subject_association",
        why: `${detail} F01 owns documents and corporate artifacts and carries no lifecycle binding on either, so this rail holds the association in its own relation; record-evidence-subject-link is what writes one, and a partner has to write it for ${bound.subject_kind} ${bound.subject_id} before this evidence can advance that subject.`,
        produced_by: "j102_record_evidence_subject_link",
      },
    };
  }

  async function loadEvidence(client, ref, now, operation, bound) {
    // hasOwnProperty, not a bare lookup. The key arrives from a caller payload,
    // and `{}["constructor"]` answers with something truthy — a fail-closed
    // branch that could be entered or skipped by naming an inherited property is
    // not fail-closed. The kind is already validated against the registry
    // upstream; this makes the guard structural rather than dependent on that.
    const absent = Object.prototype.hasOwnProperty.call(V5_J102_ABSENT_EVIDENCE_READERS,
      ref.evidence_kind)
      ? V5_J102_ABSENT_EVIDENCE_READERS[ref.evidence_kind]
      : undefined;
    if (absent !== undefined) {
      // FAIL CLOSED, with the missing fact NAMED. This is returned as a policy
      // refusal rather than thrown, so a caller can record why the path is shut
      // and what would have to exist to open it.
      return { refusal: absent };
    }
    if (ref.source === "f01_document") {
      const row = await one(client, "SELECT ops.f01_read('document', $1::jsonb) AS body",
        [J({ document_id: ref.document_id })]);
      const verified = parse(row?.body)?.body ?? null;
      if (verified == null || !isPlainObject(verified.record)) {
        return { refusal: { missing_fact: "f01_document_not_found",
          why: `no current document version exists for ${ref.document_id}`,
          evidence_kind: ref.evidence_kind, produced_by: "f01_record_document" } };
      }
      const record = verified.record;
      const identity = record.neon_identity ?? {};
      // THE PIN IS CHECKED, NOT BELIEVED. A caller that named a version or a
      // digest the record layer does not hold has decided against a document
      // that is not the stored one, and that refuses here rather than being
      // silently upgraded to whatever is current.
      if (identity.version_no !== ref.expected_version_no) {
        return { refusal: { missing_fact: "f01_document_version_moved",
          why: `document ${ref.document_id} is at version ${identity.version_no ?? "unknown"}, not the ${ref.expected_version_no} this request was decided against`,
          evidence_kind: ref.evidence_kind, produced_by: "f01_record_document" } };
      }
      if (identity.content_digest !== ref.expected_content_digest) {
        return { refusal: { missing_fact: "f01_document_content_digest_mismatch",
          why: `document ${ref.document_id} version ${ref.expected_version_no} does not carry the content digest this request named`,
          evidence_kind: ref.evidence_kind, produced_by: "f01_record_document" } };
      }
      // BLOCK-2. The pin says the document has not moved; it says nothing about
      // WHOSE document it is. An executed lease for one client would otherwise
      // mark another client's deal executed, with every state check passing.
      const documentBinding = await loadSubjectBinding(client, {
        evidence_source: "f01_document",
        evidence_ref: ref.document_id,
        version_no: ref.expected_version_no,
        content_digest: ref.expected_content_digest,
        subject_kind: bound.subject_kind,
        subject_id: bound.subject_id,
      });
      if (documentBinding === null) {
        return unboundEvidenceRefusal(ref, bound,
          `document ${ref.document_id} version ${ref.expected_version_no} is not associated with ${bound.subject_kind} ${bound.subject_id}.`);
      }
      return {
        evidence: {
          evidence_kind: ref.evidence_kind,
          source: "f01_document",
          reference: ref.document_id,
          subject_binding: {
            subject_kind: documentBinding.record.subject_kind,
            subject_id: documentBinding.record.subject_id,
            bound_by: "stored_evidence_subject_link",
            binding_digest: documentBinding.link_digest,
          },
          document: {
            document_id: identity.document_id,
            document_class: record.document_class,
            version_no: identity.version_no,
            content_digest: identity.content_digest,
            preparation_state: record.preparation_state,
            delivery_state: record.delivery_state,
            signature_state: record.signature_state,
            validity_state: record.validity_state,
            version_state: record.version_state,
            // F01 carries no dated effective window; see the kernel's contract
            // note on signed_engagement_letter for why that is reported rather
            // than filled in.
            effective_from: null,
            effective_to: null,
          },
          provenance: evidenceProvenance("ops.f01_read.document", now),
        },
        recheck: {
          evidence_kind: ref.evidence_kind, source: "f01_document",
          reader: "ops.f01_read.document",
          selector: { document_id: ref.document_id },
          expected_version_no: ref.expected_version_no,
          expected_content_digest: ref.expected_content_digest,
          // The binding travels with the pin, so the writer re-asserts BOTH
          // under the lock it already holds: an association withdrawn between
          // the decision and the write refuses the transition exactly as a moved
          // document version does.
          binding: {
            evidence_source: "f01_document",
            evidence_ref: ref.document_id,
            version_no: ref.expected_version_no,
            content_digest: ref.expected_content_digest,
            subject_kind: bound.subject_kind,
            subject_id: bound.subject_id,
          },
          expected_link_digest: documentBinding.link_digest,
        },
      };
    }
    if (ref.source === "f01_corporate_artifact") {
      const row = await one(client, "SELECT ops.f01_stored_artifact($1::text) AS artifact",
        [ref.artifact_digest]);
      const stored = parse(row?.artifact);
      if (stored == null || !isPlainObject(stored.artifact)) {
        return { refusal: { missing_fact: "f01_artifact_not_found",
          why: `no stored corporate artifact exists for ${ref.artifact_digest}`,
          evidence_kind: ref.evidence_kind, produced_by: "f01_record_artifact" } };
      }
      const artifact = stored.artifact;
      // A COUNTERPARTY ACCEPTANCE IS ABOUT ONE NEGOTIATION. An authentic
      // countersigned LOI for property A cannot be allowed to accept the
      // negotiation on property B, so the artifact binds the same way a document
      // does. An artifact's pin IS its digest, so the association is stored with
      // version_no 0 and the digest in both the reference and the pin column.
      const artifactBinding = await loadSubjectBinding(client, {
        evidence_source: "f01_corporate_artifact",
        evidence_ref: ref.artifact_digest,
        version_no: 0,
        content_digest: ref.artifact_digest,
        subject_kind: bound.subject_kind,
        subject_id: bound.subject_id,
      });
      if (artifactBinding === null) {
        return unboundEvidenceRefusal(ref, bound,
          `corporate artifact ${ref.artifact_digest} is not associated with ${bound.subject_kind} ${bound.subject_id}.`);
      }
      return {
        evidence: {
          evidence_kind: ref.evidence_kind,
          source: "f01_corporate_artifact",
          reference: ref.artifact_digest,
          subject_binding: {
            subject_kind: artifactBinding.record.subject_kind,
            subject_id: artifactBinding.record.subject_id,
            bound_by: "stored_evidence_subject_link",
            binding_digest: artifactBinding.link_digest,
          },
          artifact: {
            artifact_digest: stored.artifact_digest,
            content_digest: artifact.content_digest,
            source_system: artifact.source_system,
            evidence_class: artifact.evidence_class ?? null,
            observed_at: artifact.observed_at,
          },
          provenance: evidenceProvenance("ops.f01_stored_artifact", now),
        },
        recheck: {
          evidence_kind: ref.evidence_kind, source: "f01_corporate_artifact",
          reader: "ops.f01_stored_artifact",
          selector: { artifact_digest: ref.artifact_digest },
          binding: {
            evidence_source: "f01_corporate_artifact",
            evidence_ref: ref.artifact_digest,
            version_no: 0,
            content_digest: ref.artifact_digest,
            subject_kind: bound.subject_kind,
            subject_id: bound.subject_id,
          },
          expected_link_digest: artifactBinding.link_digest,
        },
      };
    }
    const row = await one(client,
      "SELECT ops.j102_first_party_record($1::text, $2::text) AS record",
      [ref.record_kind, ref.record_id]);
    const stored = parse(row?.record);
    if (stored == null || !isPlainObject(stored.record)) {
      return { refusal: { missing_fact: "first_party_record_not_found",
        why: `no ${ref.record_kind} record exists with id ${ref.record_id}; record-lifecycle-fact writes one`,
        evidence_kind: ref.evidence_kind, produced_by: "j102_record_first_party_fact" } };
    }
    const record = stored.record;
    // A record written before the binding existed, or by anything that skipped
    // the writer, cannot be read as bound evidence. It is refused with the
    // missing fact named rather than treated as binding to whatever is being
    // asked about.
    if (typeof record.subject_kind !== "string" || typeof record.subject_id !== "string" ||
        typeof record.recorded_by_authorization_class !== "string") {
      return { refusal: {
        evidence_kind: ref.evidence_kind,
        missing_fact: "first_party_record_subject_binding",
        why: `${ref.record_kind} record ${ref.record_id} carries no typed subject binding and author class; a record that does not say which subject it is about cannot advance one`,
        produced_by: "j102_record_first_party_fact" } };
    }
    if (record.subject_kind !== bound.subject_kind || record.subject_id !== bound.subject_id) {
      return { refusal: {
        evidence_kind: ref.evidence_kind,
        missing_fact: "first_party_record_bound_to_a_different_subject",
        why: `${ref.record_kind} record ${ref.record_id} is bound to ${record.subject_kind} ${record.subject_id}, and this request would advance ${bound.subject_kind} ${bound.subject_id}`,
        produced_by: "j102_record_first_party_fact" } };
    }
    // M1's other half. A stored field that cannot be read as evidence refuses
    // HERE, as a policy answer naming the field, rather than throwing a contract
    // violation out of the kernel's evidence assertion later.
    const unreadable = unreadableFactField(record);
    if (unreadable !== null) {
      return { refusal: {
        evidence_kind: ref.evidence_kind,
        missing_fact: "readable_first_party_record",
        why: `${ref.record_kind} record ${ref.record_id} stores an unreadable ${unreadable}; it is refused rather than being parsed into whatever it resembles`,
        produced_by: "j102_record_first_party_fact" } };
    }
    return {
      evidence: {
        evidence_kind: ref.evidence_kind,
        source: "first_party_record",
        reference: ref.record_id,
        subject_binding: {
          subject_kind: record.subject_kind,
          subject_id: record.subject_id,
          bound_by: "first_party_record",
          binding_digest: stored.record_digest,
        },
        record: {
          record_kind: record.record_kind,
          record_id: record.record_id,
          content_digest: stored.record_digest,
          recorded_by: record.recorded_by,
          recorded_by_authorization_class: record.recorded_by_authorization_class,
          recorded_at: record.recorded_at,
          reason: record.reason ?? null,
          detail: record.detail ?? null,
          closing_date: record.closing_date ?? null,
          supporting_document_id: record.supporting_document_id ?? null,
        },
        provenance: evidenceProvenance("ops.j102_first_party_record", now),
      },
      recheck: {
        evidence_kind: ref.evidence_kind, source: "first_party_record",
        reader: "ops.j102_first_party_record",
        selector: { record_kind: ref.record_kind, record_id: ref.record_id },
        expected_record_digest: stored.record_digest,
        // Re-asserted under the lock, with the digest: a record rewritten to
        // point at a different deal between the decision and the write refuses.
        binding: {
          subject_kind: bound.subject_kind,
          subject_id: bound.subject_id,
        },
      },
    };
  }

  /** The first stored fact field that cannot be read as evidence, or null. */
  function unreadableFactField(record) {
    const text = (value, max) => value === undefined || value === null ||
      (typeof value === "string" && value.length > 0 && value.length <= max &&
       !hasUnsafeCodePoint(value) && value.normalize("NFC") === value && value.trim() === value);
    if (!text(record.reason, 1000)) return "reason";
    if (!text(record.detail, 2000)) return "detail";
    if (record.closing_date !== undefined && record.closing_date !== null &&
        !(typeof record.closing_date === "string" && ISO_INSTANT_TEXT.test(record.closing_date) &&
          Number.isFinite(Date.parse(record.closing_date)))) {
      return "closing_date";
    }
    if (record.supporting_document_id !== undefined && record.supporting_document_id !== null &&
        !(typeof record.supporting_document_id === "string" &&
          /^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,127}$/.test(record.supporting_document_id))) {
      return "supporting_document_id";
    }
    return null;
  }

  // -- the shared transition path -------------------------------------------

  /**
   * Every lifecycle transition runs through here, so there is ONE place that
   * loads, judges, envelopes and applies — and one place a reviewer has to read
   * to know what any of the eleven transition operations does.
   *
   * ORDERED, and the order is load-bearing:
   *   1. Authenticate, validate the closed payload, claim authority.
   *   2. Open the transaction, derive actor and instant from the SERVER.
   *   3. REPLAY FIRST, before any state read. A settled key must return its
   *      stored result even though the world has moved since; a replay after the
   *      CAS would refuse a request that had already succeeded.
   *   4. Load the subject and every related subject, with their CAS digests.
   *   5. Resolve every evidence reference into a server-loaded record. A missing
   *      reader or a moved pin refuses here, with the fact named.
   *   6. Run the KERNEL. It judges; nothing here second-guesses it.
   *   7. On allow, build one envelope per proposed subject and per event, and
   *      hand them to ops.j102_apply_transition with the CAS digests and the
   *      recheck manifest. The database re-reads the evidence under its lock and
   *      writes the whole coupled set or none of it.
   */
  async function runTransition(operation, payload, context, { chooseTransition } = {}) {
    const schema = OPERATION_SCHEMAS[operation];
    let { principal, payload: request } = begin(operation, payload, context);
    const subject_ref = assertSubjectRef(request.subject_ref, "payload.subject_ref", schema.subject_kind);

    const relatedRefs = {};
    if (request.related_refs !== undefined && request.related_refs !== null) {
      const raw = assertClosed(request.related_refs, RELATED_REF_KEYS, [], "payload.related_refs");
      for (const key of RELATED_REF_KEYS) {
        if (raw[key] === undefined || raw[key] === null) continue;
        relatedRefs[key] = assertSubjectRef(raw[key], `payload.related_refs.${key}`, key);
      }
    }

    const rawEvidenceRefs = request.evidence_refs;
    if (!Array.isArray(rawEvidenceRefs) || rawEvidenceRefs.length < 1 || rawEvidenceRefs.length > 8) {
      fail("invalid_shape", "payload.evidence_refs must name between 1 and 8 evidence references",
        { path: "payload.evidence_refs" });
    }
    const evidenceRefs = rawEvidenceRefs.map((raw, i) =>
      assertEvidenceRef(raw, `payload.evidence_refs[${i}]`));

    // `declared` is what the caller declared, and it is what the dispatchers in
    // this module read. `domainDeclared` is the subset the KERNEL's closed
    // contract accepts, and it is the only one that reaches the judgement.
    const declared = {};
    const domainDeclared = {};
    if (request.declared !== undefined && request.declared !== null) {
      const raw = assertClosed(request.declared, DECLARED_KEYS, [], "payload.declared");
      for (const key of DECLARED_KEYS) {
        if (raw[key] === undefined || raw[key] === null) continue;
        declared[key] = raw[key];
        if (DECLARED_DOMAIN_KEYS.includes(key)) domainDeclared[key] = raw[key];
      }
    }

    return withTransaction(async client => {
      const opened = await openOperation(client, operation, principal);
      const { now } = opened;
      principal = opened.principal;
      const replay = await replayOutcome(client, operation, request, principal);
      if (replay !== null) return replay;

      const loadedSubject = await loadSubject(client, subject_ref.subject_kind, subject_ref.subject_id);
      if (loadedSubject.state === null) {
        return result(operation, "refuse", "subject_not_found", {
          actor_slug: principal.slug,
          subject_kind: subject_ref.subject_kind, subject_id: subject_ref.subject_id,
          records_written: 0, readback: null,
        });
      }
      // WHICH TRANSITION THIS IS, decided as soon as the stored subject is known,
      // because Q103's merge question below is a question about ITS write set.
      // The two dispatching operations read only the stored instrument and the
      // declared axis, neither of which a concurrent transition can move.
      const transition_id = typeof chooseTransition === "function"
        ? chooseTransition({ subject: loadedSubject.state, declared })
        : schema.transition;
      if (typeof transition_id !== "string" || !V5_J102_TRANSITION_IDS.includes(transition_id)) {
        return result(operation, "refuse", "transition_not_determined", {
          actor_slug: principal.slug, records_written: 0, readback: null,
        });
      }

      // THE COMPARE-AND-SWAP IS DECIDED AGAINST THE STORED SUBJECT, not against
      // the caller's belief about it — and a subject that MOVED since the caller
      // decided is Q103's question, not an automatic refusal any more.
      //
      // One characterized, non-overlapping intervening transition is MERGED: the
      // transition is judged again, in full, against the current committed row
      // below, and only that judgement lands. Anything else — an overlap, or a
      // movement this layer cannot characterize — is filed as a VISIBLE
      // reconciliation item with both versions preserved, and nothing moves.
      // The database still re-checks the current digest under its own lock, so a
      // third write that lands between this read and the write refuses there.
      const concurrentMerges = [];
      const judgeMovement = async (kind, id, loaded, expected) => {
        if (expected === null || expected === loaded.state_digest) return null;
        const verdict = evaluateConcurrentTransition({
          tenant: ORGANIZATION_TENANT_ID,
          transition_id,
          subject_kind: kind,
          base_version_digest: expected,
          current_version_digest: loaded.state_digest,
          current_prior_state_digest: loaded.prior_state_digest,
          current_established_by_transition: loaded.established_by_transition,
        });
        if (verdict.decision === "allow") {
          concurrentMerges.push({
            subject_kind: kind, subject_id: id,
            base_version_digest: expected, current_version_digest: loaded.state_digest,
            concurrent_transition_id: verdict.concurrent_transition_id,
            incoming_fields: verdict.incoming_fields,
            concurrent_fields: verdict.concurrent_fields,
            readmitted_against_current_row: true,
          });
          return null;
        }
        return fileTransitionConflict(client, {
          operation, request, principal, now, transition_id,
          subject_kind: kind, subject_id: id, verdict,
        });
      };
      const primaryConflict = await judgeMovement(subject_ref.subject_kind, subject_ref.subject_id,
        loadedSubject, subject_ref.expected_state_digest);
      if (primaryConflict !== null) return primaryConflict;

      const related = {};
      const casDigests = {
        [`${subject_ref.subject_kind}:${subject_ref.subject_id}`]: loadedSubject.state_digest,
      };
      for (const [key, ref] of Object.entries(relatedRefs)) {
        const loaded = await loadSubject(client, ref.subject_kind, ref.subject_id);
        if (loaded.state === null) {
          return result(operation, "refuse", "related_subject_not_found", {
            actor_slug: principal.slug, related_kind: key, related_id: ref.subject_id,
            records_written: 0, readback: null,
          });
        }
        const relatedConflict = await judgeMovement(ref.subject_kind, ref.subject_id,
          loaded, ref.expected_state_digest);
        if (relatedConflict !== null) return relatedConflict;
        related[key] = loaded.state;
        casDigests[`${ref.subject_kind}:${ref.subject_id}`] = loaded.state_digest;
      }
      const evidence = [];
      const rechecks = [];
      // THE SUBJECT THE EVIDENCE MUST BE ABOUT is the subject this operation
      // names, taken from the validated reference and not from anything the
      // evidence itself says.
      const boundSubject = {
        subject_kind: subject_ref.subject_kind, subject_id: subject_ref.subject_id,
      };
      for (const ref of evidenceRefs) {
        const loaded = await loadEvidence(client, ref, now, operation, boundSubject);
        if (loaded.refusal !== undefined) {
          return result(operation, "refuse", "required_evidence_unavailable", {
            actor_slug: principal.slug,
            evidence_kind: loaded.refusal.evidence_kind ?? ref.evidence_kind,
            // The whole point of this branch: the caller is told WHICH fact is
            // missing and who would have to produce it, rather than being told
            // the transition failed.
            missing_fact: loaded.refusal.missing_fact,
            missing_fact_reason: loaded.refusal.why,
            produced_by: loaded.refusal.produced_by,
            fabricated_authority: false,
            records_written: 0, readback: null,
          });
        }
        evidence.push(loaded.evidence);
        rechecks.push(loaded.recheck);
      }

      const evaluated = evaluateLifecycleTransition({
        tenant: ORGANIZATION_TENANT_ID,
        transition_id,
        subject: loadedSubject.state,
        related,
        evidence,
        actor: kernelActor(principal),
        // The selector chose the transition above and stops here; only the
        // kernel's own declared vocabulary crosses into the judgement.
        declared: domainDeclared,
        now,
      });
      if (evaluated.decision !== "allow") {
        return result(operation, evaluated.decision, evaluated.reason_id, {
          actor_slug: principal.slug,
          transition_id,
          subject_kind: subject_ref.subject_kind, subject_id: subject_ref.subject_id,
          refusal_detail: refusalDetail(evaluated),
          // A merge candidate that the re-admission refused is NOT merged, and
          // the caller is told the row moved and what moved it.
          ...(concurrentMerges.length === 0 ? {} : {
            auto_merged: false,
            readmission_refused_after_concurrent_change: true,
            concurrent_merges: concurrentMerges,
          }),
          records_written: 0, readback: null,
        });
      }

      // BLOCK-1. EVERY PROPOSED SUBJECT GETS A COMPARE-AND-SWAP OPERAND,
      // INCLUDING THE ONES THIS TRANSITION CREATES.
      //
      // Only LOADED subjects used to appear in the map, so a created subject
      // carried no operand at all — and the writer's CAS loop, which iterated the
      // map, never looked at it. A caller naming an EXISTING deal id as
      // `new_deal_id` therefore had that deal's authoritative current state
      // replaced by a fresh pending one, under a different assignment, with its
      // events left behind: history and current state disagreeing about what that
      // id is. The same shape applied to `new_subject_id` naming an existing
      // engagement.
      //
      // A creation's operand is an EXPLICIT JSON null, which the writer reads as
      // "this subject must be ABSENT" rather than as "no opinion". The two are
      // different requests and used to be the same bytes.
      const expectedStateDigests = { ...casDigests };
      const createdKeys = [];
      for (const [kind, state] of Object.entries(evaluated.proposed_state)) {
        const key = `${kind}:${state.subject_id}`;
        if (Object.prototype.hasOwnProperty.call(expectedStateDigests, key)) continue;
        expectedStateDigests[key] = null;
        createdKeys.push({ key, subject_kind: kind, subject_id: state.subject_id });
      }
      // The collision is ALSO checked here, before the write, so a caller learns
      // that the id it chose is already taken rather than receiving a
      // serialization failure from the writer. The writer's under-lock check is
      // what actually enforces it; this one is what explains it.
      for (const created of createdKeys) {
        const existing = await loadSubject(client, created.subject_kind, created.subject_id);
        if (existing.state !== null) {
          return result(operation, "refuse", "created_subject_id_already_exists", {
            actor_slug: principal.slug,
            transition_id,
            subject_kind: created.subject_kind, subject_id: created.subject_id,
            stored_state_digest: existing.state_digest,
            overwrote_existing_subject: false,
            records_written: 0, readback: null,
          });
        }
      }

      // THE CANONICAL EVIDENCE REFERENCE, and why it is exactly these three keys.
      //
      // ops.j102_apply_transition compares this array, one element for one, with
      // the set ops.j102_recheck_evidence RE-READ under its own lock — F01's own
      // document_id off the document record, the stored-artifact reader's own
      // artifact_digest, the record_id on the committed first-party row — and
      // refuses the whole coupled transition on a duplicate, an extra, a missing
      // one or a wrong kind, source or reference. So the shape here is not a
      // choice this module makes freely: it is the shape the database can
      // independently rebuild from what it actually read.
      //
      // THE SUBJECT BINDING USED TO RIDE ALONG HERE AND NO LONGER DOES, which is
      // a narrowing rather than a loss. Every pin on a transition is proved bound
      // to the ONE primary subject that transition advances — that is what
      // j102_evidence_not_bound_to_primary_subject refuses — and the receipt
      // names that subject in `primary_subject_kind`/`primary_subject_id`. A
      // fourth key restating it would be a fourth key the comparison has to
      // admit, on a fact the comparison already guarantees.
      const evidence_references = evidence.map(e => ({
        evidence_kind: e.evidence_kind, source: e.source, reference: e.reference,
      }));
      const subjectEnvelopes = Object.entries(evaluated.proposed_state).map(([kind, state]) =>
        storeEnvelope("stored_lifecycle_subject", storedSubjectRecord({
          subject: state,
          transition_id,
          // Taken FROM THE MAP rather than computed a second way, so the envelope
          // and the compare-and-swap operand cannot disagree; the writer refuses
          // the pair if they ever do.
          prior_state_digest: expectedStateDigests[`${kind}:${state.subject_id}`],
          updated_by: principal.slug,
          sponsoring_partner: principal.sponsoring_partner,
          updated_at: now,
        }), { alone_sufficient: false }));
      const eventEnvelopes = evaluated.events.map(event =>
        storeEnvelope("stored_lifecycle_event", storedEventRecord({
          event, transition_id, evidence_references,
          recorded_by: principal.slug, sponsoring_partner: principal.sponsoring_partner,
          recorded_at: now,
        }), { append_only: true }));

      const row = await one(client,
        `SELECT ops.j102_apply_transition($1::text, $2::jsonb, $3::jsonb, $4::jsonb,
                                          $5::jsonb, $6::text, $7::text, $8::jsonb) AS outcome`,
        [transition_id, J(expectedStateDigests), J(subjectEnvelopes), J(eventEnvelopes), J(rechecks),
         request.idempotency_key, requestDigest(operation, request, principal),
         J({ operation, reason_id: evaluated.reason_id,
             coupled_facts: evaluated.coupled_facts_committed,
             decision_refs: evaluated.decision_refs,
             // Q103's audit trail: the merge is recorded WITH the write, in the
             // stored outcome, so history and a replay both say this transition
             // was decided against a moved base and merged by machine.
             concurrent_merges: concurrentMerges })]);
      return resultFromOutcome(operation, parse(row.outcome), principal);
    });
  }

  /**
   * Q103's visible half for a TYPED transition: the subject moved under the
   * caller, and the movement either overlaps what this transition writes or
   * cannot be characterized. One reconciliation item is filed — through the
   * same governed writer record-lifecycle-reconciliation uses, under the
   * caller's own idempotency key — and no lifecycle state moves.
   *
   * THE EDIT SETS IT FILES. The incoming side is the transition's declared
   * write set on this subject; each value is the transition's INTENT (its id,
   * the field and the request digest), because the value it would have written
   * was decided against a row that is no longer current. The concurrent side,
   * when characterized, is the intervening transition's declared write set,
   * each value the digest of what the committed row now holds, attributed to
   * the row's own writer and instant. Both are preserved in full.
   */
  async function fileTransitionConflict(client, { operation, request, principal, now,
    transition_id, subject_kind, subject_id, verdict }) {
    // A SUBJECT THIS TRANSITION ONLY READS has an empty write set, so there is
    // no incoming edit to put in front of a person: nothing of this caller's
    // would be lost. The row moved in a way that cannot be traced to one
    // transition, so the read the decision rested on is stale and the caller
    // re-reads — the refusal this path always gave before Q103 was wired.
    if (verdict.incoming_fields.length === 0) {
      return result(operation, "refuse", "stale_related_subject_digest", {
        actor_slug: principal.slug, transition_id, subject_kind, subject_id,
        base_version_digest: verdict.base_version_digest,
        concurrent_change_characterized: verdict.characterized,
        why: "a subject this transition reads but does not write moved in a way that cannot be traced to one registered transition; re-read it and decide again",
        records_written: 0, readback: null,
      });
    }
    // THE SECOND READ MUST BE THE ROW THE VERDICT JUDGED. The characterization
    // (which transition moved the row, which fields it wrote) came from the
    // first read; if the row moved AGAIN in between, labelling the newer row with
    // the older characterization would file a false account. So the digests form
    // a chain, each link checked: first read == verdict (by construction), this
    // read == verdict (below; otherwise a retryable refusal, nothing filed), and
    // ops.j102_record_reconciliation_item re-binds the item's current digest to
    // the committed row UNDER ITS OWN LOCK. No row lock is taken here: these
    // roles hold no UPDATE privilege on the table, deliberately, and the writer's
    // lock is the one that decides.
    const stored = await readSubjectVerified(client, subject_kind, subject_id);
    if (stored == null) {
      return result(operation, "refuse", "subject_not_found", {
        actor_slug: principal.slug, subject_kind, subject_id, records_written: 0, readback: null,
      });
    }
    if (stored.state_digest !== verdict.current_version_digest) {
      return result(operation, "refuse", "subject_moved_again_during_reconciliation", {
        actor_slug: principal.slug, transition_id, subject_kind, subject_id,
        judged_version_digest: verdict.current_version_digest,
        current_version_digest: stored.state_digest,
        why: "the row moved again between the concurrency judgement and filing its reconciliation item; nothing was filed and nothing moved — re-read and decide again",
        records_written: 0, readback: null,
      });
    }
    const intent = requestDigest(operation, request, principal);
    const incoming = verdict.incoming_fields.map(field => ({
      field,
      value_digest: digest({ intent: transition_id, field, request_digest: intent }),
      edited_by: principal.slug,
      edited_at: now,
    }));
    const concurrent = verdict.characterized
      ? verdict.concurrent_fields.map(field => ({
        field,
        value_digest: digest(stored.state[field] ?? null),
        edited_by: stored.updated_by,
        edited_at: stored.updated_at,
      }))
      : null;
    const evaluated = evaluateConcurrentEdit({
      tenant: ORGANIZATION_TENANT_ID,
      base_version_digest: verdict.base_version_digest,
      current_version_digest: stored.state_digest,
      incoming,
      ...(concurrent === null ? {} : { concurrent }),
      actor: kernelActor(principal),
    });
    if (evaluated.decision !== "reconcile" ||
        evaluated.reconciliation_item.conflict_kind !== verdict.conflict_kind) {
      fail("contract_self_check_failed",
        "the field-level evaluator and the transition-level evaluator disagree about this conflict",
        { transition_id, subject_kind, subject_id,
          transition_verdict: verdict.conflict_kind,
          field_verdict: evaluated.reconciliation_item?.conflict_kind ?? evaluated.reason_id });
    }
    const reconciliationRequest = {
      filed_for_operation: operation, transition_id, request_digest: intent,
      subject_kind, subject_id,
    };
    const outcome = await writeReconciliationItem(client, {
      evaluated, subject_kind, subject_id, stored,
      characterized: verdict.characterized,
      idempotency_key: `j102-reconcile-${digest({ transition_key: request.idempotency_key,
        base_version_digest: verdict.base_version_digest, subject_kind, subject_id })
        .slice("sha256:".length, "sha256:".length + 48)}`,
      request_digest: requestDigest("record-lifecycle-reconciliation", reconciliationRequest, principal),
    });
    return result(operation, "reconcile", verdict.reason_id, {
      actor_slug: principal.slug,
      transition_id,
      subject_kind, subject_id,
      base_version_digest: verdict.base_version_digest,
      current_version_digest: stored.state_digest,
      concurrent_transition_id: verdict.concurrent_transition_id,
      overlapping_fields: verdict.overlapping_fields,
      characterized: verdict.characterized,
      auto_merged: false,
      last_writer_wins: false,
      silent_overwrite: false,
      resolved_by_machine: false,
      advances_lifecycle_state: false,
      reconciliation_item: outcome,
      records_written: outcome?.records_written ?? null,
      readback: null,
    });
  }

  /** The one place a reconciliation envelope is built and handed to its writer. */
  async function writeReconciliationItem(client, { evaluated, subject_kind, subject_id, stored,
    characterized, idempotency_key, request_digest }) {
    const events = await readSubjectEvents(client, subject_kind, subject_id);
    const history_tail = events.slice(-5).map(entry => ({
      transition_id: entry.record.transition_id,
      event_kind: entry.record.event?.event_kind ?? null,
      recorded_by: entry.record.recorded_by,
      recorded_at: entry.record.recorded_at,
      record_digest: entry.record_digest,
    }));
    const record = storedReconciliationItemRecord({
      item: evaluated.reconciliation_item,
      subject_kind, subject_id,
      current_state: stored.state,
      history_tail,
      characterized,
    });
    const envelope = storeEnvelope("stored_reconciliation_item", record,
      { append_only: true, visible: true, resolved_by_machine: false });
    const row = await one(client,
      `SELECT ops.j102_record_reconciliation_item($1::jsonb, $2::jsonb,
                                                  $3::text, $4::text, $5::jsonb) AS outcome`,
      [J(envelope),
       J({ [`${subject_kind}:${subject_id}`]: stored.state_digest }),
       idempotency_key, request_digest,
       J({ operation: "record-lifecycle-reconciliation", reason_id: evaluated.reason_id })]);
    return parse(row.outcome);
  }

  /** The refusal fields worth carrying back, without echoing the whole answer. */
  function refusalDetail(evaluated) {
    const detail = {};
    for (const key of ["unmet_axis", "observed", "permitted", "evidence_kind", "document_axis",
      "required", "expected_subject_kind", "permitted_actor_classes", "actor_authorization_class",
      "supplied_evidence_kinds", "required_evidence_alternatives", "unexpected_evidence_kinds",
      "closing_date", "negotiation_state", "assignment_phase", "engagement_state",
      "relationship_state", "diligence_state", "pending_deal_id", "selected_property_id",
      "open_negotiation_count", "instrument_kind", "permitted_instrument_kinds",
      // BLOCK-2 and H5 refusals name WHICH subject the evidence was about and
      // WHO authored it; a refusal that hid either would be unactionable.
      "bound_subject_kind", "bound_subject_id", "bound_by", "required_subject_kind",
      "required_author_class", "evidence_author_class", "evidence_author",
      "active_lease_draft_target_id", "relationship_id", "engagement_id",
      // The initialization refusals name the condition that failed, the context
      // link that did not hold, and the identifier that was missing or unread.
      "unmet_field", "missing_declared_identifier", "unexpected_declared_identifier",
      "active_negotiation_ids",
      "expected_id", "loaded_id"]) {
      if (evaluated[key] !== undefined) detail[key] = evaluated[key];
    }
    return deepFreeze(detail);
  }

  // -- the shared initialization path ----------------------------------------

  /**
   * Every initialization runs through here, so there is ONE place that loads the
   * parent chain, judges, envelopes and creates — and one place a reviewer has to
   * read to know what any of the three initialization operations does.
   *
   * ORDERED, and the order is load-bearing:
   *   1. Authenticate, validate the closed payload, claim authority.
   *   2. Open the transaction, derive actor and instant from the SERVER.
   *   3. REPLAY FIRST, before any state read, exactly as a transition does.
   *   4. Load every parent named in `related_refs`, with its compare-and-swap
   *      digest. A parent that moved refuses here rather than being created
   *      under.
   *   5. Refuse an id that is already taken, so a caller learns that the row it
   *      names exists rather than receiving a serialization failure.
   *   6. Run the KERNEL. It decides whether the parent chain admits this
   *      creation and what the created row must be; nothing here second-guesses
   *      it and nothing here composes a state of its own.
   *   7. Hand the one subject envelope and the one event envelope to
   *      ops.j102_initialize_subject with the parents' compare-and-swap digests
   *      and an EXPLICIT NULL for the created key. The writer re-takes the same
   *      locks, re-checks the parents, holds the row to the contract's fixed
   *      shape, and writes the subject and its history together or neither.
   *
   * IT PERFORMS NO TRANSITION AND REACHES NO TRANSITION WRITER.
   * ops.j102_apply_transition is not called from this path, and its refusal to
   * create a primary subject is untouched.
   */
  async function runInitialization(operation, payload, context) {
    const schema = OPERATION_SCHEMAS[operation];
    const contract = v5J102InitializationContract(schema.initialization);
    let { principal, payload: request } = begin(operation, payload, context);

    const rawDeclared = assertClosed(request.declared, INITIALIZATION_DECLARED_KEYS,
      ["new_subject_id"], "payload.declared");
    const declared = {};
    for (const key of INITIALIZATION_DECLARED_KEYS) {
      if (rawDeclared[key] === undefined || rawDeclared[key] === null) continue;
      declared[key] = assertIdent(rawDeclared[key], `payload.declared.${key}`);
    }

    const relatedRefs = {};
    if (request.related_refs !== undefined && request.related_refs !== null) {
      const raw = assertClosed(request.related_refs, RELATED_REF_KEYS, [], "payload.related_refs");
      for (const key of RELATED_REF_KEYS) {
        if (raw[key] === undefined || raw[key] === null) continue;
        relatedRefs[key] = assertSubjectRef(raw[key], `payload.related_refs.${key}`, key);
      }
    }
    // THE PARENT CHAIN IS NAMED IN FULL OR NOT AT ALL. Every subject the kernel's
    // contract requires must be supplied, because a chain link the caller simply
    // omits is a prerequisite nobody checks — which is how an assignment ends up
    // under a lapsed engagement or a prospect. A related subject the contract does
    // NOT read is refused for the mirror reason: it would be locked, compared and
    // never consulted, which reads as a check that happened.
    const required_related = contract.required_context.map(rule => rule.subject);
    for (const key of required_related) {
      if (relatedRefs[key] === undefined) {
        fail("missing_field",
          `payload.related_refs.${key} is required; ${operation} runs under a ${key} and checks it`,
          { path: `payload.related_refs.${key}`, operation,
            required_related_subject_kinds: required_related });
      }
    }
    for (const key of Object.keys(relatedRefs)) {
      if (!required_related.includes(key)) {
        fail("unexpected_related_subject",
          `${operation} reads ${required_related.length === 0 ? "no related subject" : required_related.join(", ")}, and this request names a ${key}`,
          { path: `payload.related_refs.${key}`, operation,
            required_related_subject_kinds: required_related });
      }
    }

    return withTransaction(async client => {
      const opened = await openOperation(client, operation, principal);
      const { now } = opened;
      principal = opened.principal;
      const replay = await replayOutcome(client, operation, request, principal);
      if (replay !== null) return replay;

      const related = {};
      const expectedStateDigests = {};
      for (const [key, ref] of Object.entries(relatedRefs)) {
        const loaded = await loadSubject(client, ref.subject_kind, ref.subject_id);
        if (loaded.state === null) {
          return result(operation, "refuse", "related_subject_not_found", {
            actor_slug: principal.slug, related_kind: key, related_id: ref.subject_id,
            records_written: 0, readback: null,
          });
        }
        if (ref.expected_state_digest !== null && ref.expected_state_digest !== loaded.state_digest) {
          return result(operation, "refuse", "stale_related_subject_digest", {
            actor_slug: principal.slug, related_kind: key, related_id: ref.subject_id,
            stored_state_digest: loaded.state_digest,
            expected_state_digest: ref.expected_state_digest,
            records_written: 0, readback: null,
          });
        }
        related[key] = loaded.state;
        expectedStateDigests[`${ref.subject_kind}:${ref.subject_id}`] = loaded.state_digest;
      }

      // THE ID MUST BE FREE. The writer's explicit-null operand is what actually
      // enforces this, under the lock, so two concurrent creations of one id
      // serialize and the loser refuses; this read is what turns that into a
      // named answer instead of a serialization failure.
      const existing = await loadSubject(client, contract.subject_kind, declared.new_subject_id);
      if (existing.state !== null) {
        return result(operation, "refuse", "subject_already_exists", {
          actor_slug: principal.slug,
          subject_kind: contract.subject_kind, subject_id: declared.new_subject_id,
          stored_state_digest: existing.state_digest,
          overwrote_existing_subject: false,
          records_written: 0, readback: null,
        });
      }

      // ONE ACTIVE NEGOTIATION PER PROPERTY (owner ruling 2026-09-25): the live
      // siblings are LOADED here and handed to the kernel, which refuses by
      // name; the SQL writer re-checks and a partial unique index backstops.
      let activeSiblings;
      if (contract.unique_active_per_property === true) {
        const assignmentId = related.assignment?.subject_id ?? null;
        activeSiblings = assignmentId === null || typeof declared.property_id !== "string" ? [] :
          (await client.query(
            `SELECT subject_id FROM ops.j102_subject_current
              WHERE tenant = ops.f01_tenant() AND subject_kind = 'property_negotiation'
                AND parent_id = $1 AND envelope -> 'record' -> 'state' ->> 'property_id' = $2
                AND envelope -> 'record' -> 'state' ->> 'negotiation_state' = ANY($3::text[])
              ORDER BY subject_id`,
            [assignmentId, declared.property_id, [...V5_J102_ACTIVE_NEGOTIATION_STATES]]))
            .rows.map(r => r.subject_id);
      }
      const evaluated = evaluateLifecycleInitialization({
        tenant: ORGANIZATION_TENANT_ID,
        initialization_id: schema.initialization,
        related,
        declared,
        actor: kernelActor(principal),
        now,
        ...(activeSiblings === undefined ? {} : { active_negotiations_for_property: activeSiblings }),
      });
      if (evaluated.decision !== "allow") {
        return result(operation, evaluated.decision, evaluated.reason_id, {
          actor_slug: principal.slug,
          initialization_id: schema.initialization,
          subject_kind: contract.subject_kind, subject_id: declared.new_subject_id,
          refusal_detail: refusalDetail(evaluated),
          records_written: 0, readback: null,
        });
      }

      const created = evaluated.created_state;
      // AN EXPLICIT JSON NULL, which the writer reads as "this subject must be
      // ABSENT" rather than as "no opinion" — the same operand a coupled creation
      // carries through the transition writer, and for the same reason.
      expectedStateDigests[`${created.subject_kind}:${created.subject_id}`] = null;

      const subjectEnvelope = storeEnvelope("stored_lifecycle_subject", storedSubjectRecord({
        subject: created,
        // The row's own provenance is the INITIALIZATION that created it, never a
        // transition. A reader of ops.j102_subject can therefore tell a created
        // row from an advanced one without consulting the history.
        transition_id: schema.initialization,
        prior_state_digest: null,
        updated_by: principal.slug,
        sponsoring_partner: principal.sponsoring_partner,
        updated_at: now,
      }), { alone_sufficient: false });
      // AN EMPTY EVIDENCE CITATION, and it is a positive statement rather than an
      // omission: this act rests on no evidence because no evidence in this rail
      // can bind to a subject that does not exist yet. The relation admits an
      // empty array for exactly the three initialization ids and for nothing else,
      // so a TRANSITION still cannot append a history row citing nothing.
      const eventEnvelope = storeEnvelope("stored_lifecycle_event", storedEventRecord({
        event: evaluated.events[0],
        transition_id: schema.initialization,
        evidence_references: [],
        recorded_by: principal.slug,
        sponsoring_partner: principal.sponsoring_partner,
        recorded_at: now,
      }), { append_only: true });

      const row = await one(client,
        `SELECT ops.j102_initialize_subject($1::text, $2::jsonb, $3::jsonb, $4::jsonb,
                                            $5::text, $6::text, $7::jsonb) AS outcome`,
        [schema.initialization, J(expectedStateDigests), J(subjectEnvelope), J(eventEnvelope),
         request.idempotency_key, requestDigest(operation, request, principal),
         J({ operation, reason_id: evaluated.reason_id,
             decision_refs: evaluated.decision_refs })]);
      return resultFromOutcome(operation, parse(row.outcome), principal);
    });
  }

  // -- 1. read-cre-lifecycle -------------------------------------------------

  async function readCreLifecycle(payload, context) {
    const operation = "read-cre-lifecycle";
    let { principal, payload: request } = begin(operation, payload, context);
    const selector = assertClosed(request.selector, READ_SELECTOR_KEYS, ["kind"], "payload.selector");
    if (!V5_J102_READ_KINDS.includes(selector.kind)) {
      fail("unknown_read_kind", `"${selector.kind}" is not a registered read kind`,
        { kind: selector.kind, registered: [...V5_J102_READ_KINDS] });
    }
    if (selector.subject_kind !== undefined && selector.subject_kind !== null &&
        !V5_J102_SUBJECT_KINDS.includes(selector.subject_kind)) {
      fail("unknown_subject_kind", `"${selector.subject_kind}" is not a registered subject kind`,
        { registered: [...V5_J102_SUBJECT_KINDS] });
    }
    return withTransaction(async client => {
      const opened = await openOperation(client, operation, principal);
      const { now } = opened;
      principal = opened.principal;
      if (V5_J102_COMPOSED_READ_KINDS.includes(selector.kind)) {
        return readOwnershipAndFreshness(client, selector, principal, now);
      }
      const row = await one(client, "SELECT ops.j102_read($1::text, $2::jsonb) AS body",
        [selector.kind, J(Object.fromEntries(
          READ_SELECTOR_KEYS.filter(k => k !== "kind" && selector[k] !== undefined)
            .map(k => [k, selector[k]])))]);
      return result(operation, "allow", "read_recomputed_from_committed_rows", {
        actor_slug: principal.slug,
        kind: selector.kind,
        readback: parse(row?.body) ?? null,
        integrity: "recomputed_not_trusted",
        stale_fallback_permitted: false,
      });
    });
  }

  /**
   * Q103's second half: ownership, freshness and in-progress automation, from the
   * rows the database actually holds.
   *
   * WHAT IS DERIVED AND WHAT IS ABSENT, because the difference is the whole point
   * of this read:
   *
   *   state_digest      RECOMPUTED inside PostgreSQL from the committed bytes and
   *                     recomputed again here from the state it returned.
   *   last material     THE NEWEST ROW OF THE APPEND-ONLY HISTORY. Every writer in
   *   change            this rail appends an event in the same transaction as the
   *                     state it writes and stamps both from one instant, so the
   *                     last event IS the last material change — and the subject
   *                     row's own updated_by/updated_at are cross-checked against
   *                     it rather than trusted beside it.
   *   owner             ABSENT. Nothing here records which partner owns a subject.
   *                     `updated_by` is who last WROTE the row and answering with
   *                     it would be answering a different question confidently.
   *   active automation ABSENT. No relation records a run against a subject, and
   *                     an empty list is the one wrong answer: "nothing is
   *                     running" and "nobody asked" are different states.
   *
   * Neither absent fact is supplied to the kernel AT ALL, so it reports
   * `owner_known: false` and `active_automation_known: false` — the distinction it
   * was built to make — and this answer names both missing facts from the
   * unwired registry rather than restating them.
   */
  async function readOwnershipAndFreshness(client, selector, principal, now) {
    const operation = "read-cre-lifecycle";
    if (selector.subject_kind === undefined || selector.subject_kind === null ||
        selector.subject_id === undefined || selector.subject_id === null) {
      fail("missing_field",
        "payload.selector.subject_kind and payload.selector.subject_id are required for an ownership read",
        { kind: selector.kind });
    }
    const subject_kind = selector.subject_kind;
    const subject_id = assertIdent(selector.subject_id, "payload.selector.subject_id");
    const stored = await readSubjectVerified(client, subject_kind, subject_id);
    if (stored == null) {
      return result(operation, "refuse", "subject_not_found", {
        actor_slug: principal.slug, kind: selector.kind,
        subject_kind, subject_id, readback: null,
      });
    }
    const events = await readSubjectEvents(client, subject_kind, subject_id);
    const newest = events.length === 0 ? null : events[events.length - 1];
    const change = newest === null ? null : newest.record;

    // THE TWO ACCOUNTS MUST AGREE. The subject row and its newest history row are
    // written in one transaction from one instant by every writer in this rail, so
    // a disagreement is not a value to pick between — it is a record layer that
    // cannot say when it last changed, and answering anyway would be the
    // confident-looking wrong answer this whole read exists to avoid.
    if (change !== null &&
        (change.recorded_by !== stored.updated_by ||
         Date.parse(change.recorded_at) !== Date.parse(stored.updated_at))) {
      return result(operation, "refuse", "subject_history_disagrees_with_current_state", {
        actor_slug: principal.slug, kind: selector.kind, subject_kind, subject_id,
        current_updated_by: stored.updated_by, current_updated_at: stored.updated_at,
        newest_event_recorded_by: change.recorded_by,
        newest_event_recorded_at: change.recorded_at,
        readback: null,
      });
    }

    const projection = projectOwnershipAndFreshness({
      tenant: ORGANIZATION_TENANT_ID,
      subject_kind,
      subject_id,
      state_digest: stored.state_digest,
      // SUPPLIED ONLY WHERE THE HISTORY ESTABLISHES IT. A subject with no events
      // is a row this rail did not write; freshness is then genuinely unknown and
      // the kernel says so rather than falling back to the row's own updated_at.
      ...(change === null ? {} : {
        last_material_change_at: change.recorded_at,
        last_material_change_by: change.recorded_by,
      }),
      // `owner_slug` and `active_automation` are DELIBERATELY NOT PASSED. See the
      // note above and the two entries in V5_J102_UNWIRED_CAPABILITIES.
      now,
    });

    return result(operation, "allow", "ownership_and_freshness_projected_from_committed_rows", {
      actor_slug: principal.slug,
      kind: selector.kind,
      readback: projection,
      integrity: "recomputed_not_trusted",
      stale_fallback_permitted: false,
      // WHERE EACH FIELD CAME FROM, so a consumer can tell a derived answer from
      // an absent one without reading this function.
      derived_from: {
        state_digest: "ops.j102_read.subject",
        last_material_change: change === null
          ? "no history rows exist for this subject" : "ops.j102_read.subject_events",
        owner_slug: "not_produced_by_this_record_layer",
        active_automation: "not_produced_by_this_record_layer",
      },
      history_events_read: events.length,
      current_state_agrees_with_history: change !== null,
      // The two facts this layer does not hold, taken from the registry rather
      // than restated, so the read and the registry cannot drift.
      missing_facts: V5_J102_UNWIRED_CAPABILITIES.map(entry => ({
        fact: entry.missing_fact,
        why: entry.why,
        exact_minimal_change: entry.exact_minimal_change,
        produced_by: entry.produced_by,
      })),
      owner_known: projection.owner_known,
      freshness_known: projection.freshness_known,
      active_automation_known: projection.active_automation_known,
    });
  }

  // -- 2. record-lifecycle-fact ---------------------------------------------

  async function recordLifecycleFact(payload, context) {
    const operation = "record-lifecycle-fact";
    let { principal, payload: request } = begin(operation, payload, context);
    const fact = assertClosed(request.fact, FACT_BODY_KEYS,
      ["record_kind", "record_id", "subject_kind", "subject_id"], "payload.fact");
    // The kind must be one some evidence contract actually consumes. A record
    // nothing can ever be judged against is not a business fact, it is a note,
    // and this store is not a place to keep notes.
    const contracts = V5_J102_EVIDENCE_KINDS
      .map(kind => v5J102EvidenceContract(kind))
      .filter(c => c.source === "first_party_record");
    const consumed = contracts.map(c => c.record_kind);
    if (!consumed.includes(fact.record_kind)) {
      fail("unknown_first_party_record_kind",
        `"${fact.record_kind}" is consumed by no lifecycle evidence contract`,
        { record_kind: fact.record_kind, registered: [...new Set(consumed)].sort() });
    }
    assertIdent(fact.record_id, "payload.fact.record_id");
    if (!V5_J102_SUBJECT_KINDS.includes(fact.subject_kind)) {
      fail("unknown_subject_kind", `"${fact.subject_kind}" is not a registered subject kind`,
        { path: "payload.fact.subject_kind", registered: [...V5_J102_SUBJECT_KINDS] });
    }
    assertIdent(fact.subject_id, "payload.fact.subject_id");
    // BLOCK-2, at the door the record comes in through: the kind of subject a
    // record may name is fixed by the evidence contract that consumes it, so a
    // closing settlement cannot be bound to an assignment and then used to close
    // a deal by pointing the transition somewhere else.
    const bindsTo = [...new Set(contracts.filter(c => c.record_kind === fact.record_kind)
      .map(c => c.binds_subject_kind).filter(kind => kind !== null))];
    if (bindsTo.length > 0 && !bindsTo.includes(fact.subject_kind)) {
      fail("first_party_record_subject_kind_mismatch",
        `a ${fact.record_kind} record binds to a ${bindsTo.join(" or ")}, not to a ${fact.subject_kind}`,
        { path: "payload.fact.subject_kind", record_kind: fact.record_kind, permitted: bindsTo });
    }
    // H5. THE AUTHOR IS RESTRICTED AT THE WRITER, not merely at the transition.
    // Otherwise a sponsored agent authors the closing date, the winning-property
    // commitment or the failure reason, and a partner performing the transition
    // afterwards launders it into the record.
    if (V5_J102_PARTNER_AUTHORED_RECORD_KINDS.includes(fact.record_kind) &&
        principal.authorization_class !== "verified_partner") {
      fail("partner_authored_record_kind_refused",
        `a ${fact.record_kind} record is authored by a verified partner; ${principal.slug} holds ${principal.authorization_class}`,
        { record_kind: fact.record_kind, actor_slug: principal.slug,
          authorization_class: principal.authorization_class,
          partner_authored_record_kinds: [...V5_J102_PARTNER_AUTHORED_RECORD_KINDS] });
    }
    // M1. The typed fields are validated BEFORE the durable write, in the shape
    // the SQL CHECK constraints enforce, so an unreadable record cannot be stored
    // and then blow up as a contract violation when a transition reads it.
    if (fact.reason !== undefined && fact.reason !== null) {
      assertPlainText(fact.reason, "payload.fact.reason", { maxLength: 1000 });
    }
    if (fact.detail !== undefined && fact.detail !== null) {
      assertPlainText(fact.detail, "payload.fact.detail", { maxLength: 2000 });
    }
    if (fact.closing_date !== undefined && fact.closing_date !== null) {
      assertInstantText(fact.closing_date, "payload.fact.closing_date");
    }
    if (fact.supporting_document_id !== undefined && fact.supporting_document_id !== null) {
      assertIdent(fact.supporting_document_id, "payload.fact.supporting_document_id");
    }
    // The two mandatory fields, checked against the evidence contracts that
    // consume this kind rather than against a list restated here.
    const requiresClosingDate = contracts.some(c =>
      c.record_kind === fact.record_kind && c.requires_closing_date === true);
    if (requiresClosingDate && (fact.closing_date === undefined || fact.closing_date === null)) {
      fail("missing_field",
        `payload.fact.closing_date is required for a ${fact.record_kind} record; Q094 closes a deal on the actual date and on nothing else`,
        { path: "payload.fact.closing_date", record_kind: fact.record_kind });
    }
    const requiresReason = contracts.some(c =>
      c.record_kind === fact.record_kind && c.requires_reason === true);
    if (requiresReason && (fact.reason === undefined || fact.reason === null)) {
      fail("missing_field",
        `payload.fact.reason is required for a ${fact.record_kind} record; a reason is preserved, never inferred`,
        { path: "payload.fact.reason", record_kind: fact.record_kind });
    }

    return withTransaction(async client => {
      const opened = await openOperation(client, operation, principal);
      const { now } = opened;
      principal = opened.principal;
      // H5 AGAIN, ON THE CLASS THE CREDENTIAL CARRIES. A partner reaching this
      // operation over the writer credential has just had the database's
      // sponsored-agent class adopted; a partner-authored kind must then come
      // through the authority-routed verb, where the credential is the partner's.
      if (V5_J102_PARTNER_AUTHORED_RECORD_KINDS.includes(fact.record_kind) &&
          principal.authorization_class !== "verified_partner") {
        fail("partner_authored_record_kind_refused",
          `a ${fact.record_kind} record is authored on a verified partner's authority credential; this call ran on the writer credential as ${principal.authorization_class}`,
          { record_kind: fact.record_kind, actor_slug: principal.slug,
            authorization_class: principal.authorization_class,
            credential_split: opened.credential_split,
            use_verb: "record-partner-lifecycle-fact",
            partner_authored_record_kinds: [...V5_J102_PARTNER_AUTHORED_RECORD_KINDS] });
      }
      const replay = await replayOutcome(client, operation, request, principal);
      if (replay !== null) return replay;
      // The subject a record claims to be about must EXIST. A binding to an id
      // nobody holds is a dangling reference wearing the shape of provenance,
      // and it would sit in the record layer until some future subject took that
      // id and inherited a fact nobody wrote about it.
      const boundSubject = await loadSubject(client, fact.subject_kind, fact.subject_id);
      if (boundSubject.state === null) {
        return result(operation, "refuse", "bound_subject_not_found", {
          actor_slug: principal.slug,
          subject_kind: fact.subject_kind, subject_id: fact.subject_id,
          records_written: 0, readback: null,
        });
      }
      const record = storedFirstPartyFactRecord({
        fact,
        recorded_by: principal.slug,
        recorded_by_authorization_class: principal.authorization_class,
        recorded_at: now,
      });
      const envelope = storeEnvelope("stored_first_party_record", record,
        { append_only: true, advances_lifecycle_state: false });
      const row = await one(client,
        "SELECT ops.j102_record_first_party_fact($1::jsonb, $2::text, $3::text) AS outcome",
        [J(envelope), request.idempotency_key, requestDigest(operation, request, principal)]);
      return resultFromOutcome(operation, parse(row.outcome), principal);
    });
  }

  // -- 2b. record-evidence-subject-link -------------------------------------

  /**
   * Associate one EXACT evidence pin with one lifecycle subject.
   *
   * WHAT THIS IS NOT. It is not a document, not a document state, and not an
   * assertion that anything was signed: F01 owns all three and this operation
   * reads them rather than writing them. It is one partner-authored statement
   * that a document version or a corporate artifact F01 really holds belongs to
   * a lifecycle subject this rail really holds — which is the fact no layer
   * carried, and the reason an executed lease could advance the wrong deal.
   *
   * BOTH ENDS ARE CHECKED BEFORE ANYTHING IS WRITTEN. The document is read back
   * from F01 at the exact version and content digest named, and the subject is
   * loaded from this rail. An association to a version F01 does not hold, or to
   * a subject that does not exist, refuses.
   */
  async function recordEvidenceSubjectLink(payload, context) {
    const operation = "record-evidence-subject-link";
    let { principal, payload: request } = begin(operation, payload, context);
    const raw = assertClosed(request.link, LINK_BODY_KEYS,
      ["evidence_source", "subject_kind", "subject_id"], "payload.link");
    if (!V5_J102_LINKABLE_EVIDENCE_SOURCES.includes(raw.evidence_source)) {
      fail("unknown_evidence_source",
        `"${String(raw.evidence_source)}" is not an evidence source a subject association is held for`,
        { path: "payload.link.evidence_source",
          registered: [...V5_J102_LINKABLE_EVIDENCE_SOURCES] });
    }
    if (!V5_J102_SUBJECT_KINDS.includes(raw.subject_kind)) {
      fail("unknown_subject_kind", `"${String(raw.subject_kind)}" is not a registered subject kind`,
        { path: "payload.link.subject_kind", registered: [...V5_J102_SUBJECT_KINDS] });
    }
    const link = {
      evidence_source: raw.evidence_source,
      subject_kind: raw.subject_kind,
      subject_id: assertIdent(raw.subject_id, "payload.link.subject_id"),
    };
    if (raw.evidence_source === "f01_document") {
      assertClosed(raw, LINK_DOCUMENT_KEYS, LINK_DOCUMENT_KEYS, "payload.link");
      link.evidence_ref = assertIdent(raw.document_id, "payload.link.document_id");
      if (!Number.isSafeInteger(raw.expected_version_no) || raw.expected_version_no < 1) {
        fail("invalid_shape", "payload.link.expected_version_no must be a positive integer",
          { path: "payload.link.expected_version_no" });
      }
      link.version_no = raw.expected_version_no;
      link.content_digest = assertDigestRef(raw.expected_content_digest,
        "payload.link.expected_content_digest");
    } else {
      assertClosed(raw, LINK_ARTIFACT_KEYS, LINK_ARTIFACT_KEYS, "payload.link");
      link.evidence_ref = assertDigestRef(raw.artifact_digest, "payload.link.artifact_digest");
      // An artifact's pin IS its digest, so there is no version to name and the
      // digest stands in both columns. Zero is the version of a thing that has
      // none, said once here rather than left as a null the reader has to guess at.
      link.version_no = 0;
      link.content_digest = link.evidence_ref;
    }

    return withTransaction(async client => {
      const opened = await openOperation(client, operation, principal);
      const { now } = opened;
      principal = opened.principal;
      const replay = await replayOutcome(client, operation, request, principal);
      if (replay !== null) return replay;

      const subject = await loadSubject(client, link.subject_kind, link.subject_id);
      if (subject.state === null) {
        return result(operation, "refuse", "subject_not_found", {
          actor_slug: principal.slug,
          subject_kind: link.subject_kind, subject_id: link.subject_id,
          records_written: 0, readback: null,
        });
      }
      if (link.evidence_source === "f01_document") {
        const row = await one(client, "SELECT ops.f01_read('document', $1::jsonb) AS body",
          [J({ document_id: link.evidence_ref })]);
        const verified = parse(row?.body)?.body ?? null;
        const identity = isPlainObject(verified?.record) ? (verified.record.neon_identity ?? {}) : null;
        if (identity === null || identity.version_no !== link.version_no ||
            identity.content_digest !== link.content_digest) {
          return result(operation, "refuse", "evidence_pin_not_held", {
            actor_slug: principal.slug,
            missing_fact: "f01_document_version_at_the_named_pin",
            missing_fact_reason: `F01 does not hold document ${link.evidence_ref} at version ${link.version_no} with the content digest this association names`,
            produced_by: "f01_record_document",
            records_written: 0, readback: null,
          });
        }
      } else {
        const row = await one(client, "SELECT ops.f01_stored_artifact($1::text) AS artifact",
          [link.evidence_ref]);
        const stored = parse(row?.artifact);
        if (stored == null || !isPlainObject(stored.artifact)) {
          return result(operation, "refuse", "evidence_pin_not_held", {
            actor_slug: principal.slug,
            missing_fact: "f01_corporate_artifact_at_the_named_digest",
            missing_fact_reason: `no stored corporate artifact exists for ${link.evidence_ref}`,
            produced_by: "f01_record_artifact",
            records_written: 0, readback: null,
          });
        }
      }
      const envelope = storeEnvelope("stored_evidence_subject_link",
        storedEvidenceSubjectLinkRecord({
          link,
          associated_by: principal.slug,
          associated_by_authorization_class: principal.authorization_class,
          associated_at: now,
        }), { append_only: true, authorityOnly: true, advances_lifecycle_state: false });
      const row = await one(client,
        "SELECT ops.j102_record_evidence_subject_link($1::jsonb, $2::text, $3::text) AS outcome",
        [J(envelope), request.idempotency_key, requestDigest(operation, request, principal)]);
      return resultFromOutcome(operation, parse(row.outcome), principal);
    });
  }

  // -- 2c. the three initialization operations -------------------------------

  const initializeProspectRelationship = (payload, context) =>
    runInitialization("initialize-prospect-relationship", payload, context);
  const initializeAssignment = (payload, context) =>
    runInitialization("initialize-assignment", payload, context);
  const initializePropertyNegotiation = (payload, context) =>
    runInitialization("initialize-property-negotiation", payload, context);

  // -- 3..13. the transition operations -------------------------------------

  const recordRepresentationAgreement = (payload, context) =>
    runTransition("record-representation-agreement", payload, context);
  const openCreAssignment = (payload, context) =>
    runTransition("open-cre-assignment", payload, context);
  const recordLoiSubmission = (payload, context) =>
    runTransition("record-loi-submission", payload, context);
  const recordLoiAcceptance = (payload, context) =>
    runTransition("record-loi-acceptance", payload, context);
  const commitWinningProperty = (payload, context) =>
    runTransition("commit-winning-property", payload, context);
  const recordDiligenceOutcome = (payload, context) =>
    runTransition("record-diligence-outcome", payload, context);
  const recordDealClosing = (payload, context) =>
    runTransition("record-deal-closing", payload, context);
  const cancelPendingDeal = (payload, context) =>
    runTransition("cancel-pending-deal", payload, context);

  /**
   * The instrument kind comes from the STORED deal, never from the caller.
   *
   * Q094 gives purchase execution different consequences from lease execution —
   * diligence opens for one and not the other — so a caller able to choose which
   * transition ran could obtain the lease semantics on a purchase and skip
   * diligence entirely. Dispatching on the loaded row makes that unreachable.
   */
  const recordDealExecution = (payload, context) =>
    runTransition("record-deal-execution", payload, context, {
      // hasOwnProperty, and read from the exported table rather than an inline
      // ternary, so the SQL admission map has ONE place to be checked against.
      // A deal whose stored instrument kind is not in the table determines no
      // transition and refuses, rather than falling back to the lease semantics.
      chooseTransition: ({ subject }) =>
        (typeof subject.instrument_kind === "string" &&
         Object.prototype.hasOwnProperty.call(V5_J102_INSTRUMENT_TRANSITIONS,
           subject.instrument_kind))
          ? V5_J102_INSTRUMENT_TRANSITIONS[subject.instrument_kind]
          : null,
    });

  const recordDealAxis = (payload, context) =>
    runTransition("record-deal-axis", payload, context, {
      // hasOwnProperty for the same reason loadEvidence uses it: the axis name is
      // a caller value, and an inherited property must not be able to answer for
      // a registered one.
      chooseTransition: ({ declared }) =>
        (typeof declared.axis === "string" &&
         Object.prototype.hasOwnProperty.call(AXIS_TRANSITIONS, declared.axis))
          ? AXIS_TRANSITIONS[declared.axis]
          : null,
    });

  // -- 14. link-salesforce-reference ----------------------------------------

  async function linkSalesforceReference(payload, context) {
    const operation = "link-salesforce-reference";
    let { principal, payload: request } = begin(operation, payload, context);
    // The kernel decides the shape and refuses any attempt to map a Salesforce
    // label onto lifecycle state; this module adds nothing to that judgement.
    const projected = projectSalesforceReference({
      tenant: ORGANIZATION_TENANT_ID,
      opportunity_id: request.opportunity_id,
      opportunity_name: request.opportunity_name,
      opportunity_phase: request.opportunity_phase,
      observed_at: request.observed_at,
      linked_subject_kind: request.linked_subject_kind ?? null,
      linked_subject_id: request.linked_subject_id ?? null,
    });

    return withTransaction(async client => {
      const opened = await openOperation(client, operation, principal);
      const { now } = opened;
      principal = opened.principal;
      const replay = await replayOutcome(client, operation, request, principal);
      if (replay !== null) return replay;
      // A link target must EXIST. Q083 links the opportunity progressively to a
      // real prospect, engagement, assignment or Deal; a link to an id nobody
      // holds is a dangling reference wearing the shape of provenance.
      if (projected.linked_subject_kind !== null) {
        const loaded = await loadSubject(client, projected.linked_subject_kind,
          projected.linked_subject_id);
        if (loaded.state === null) {
          return result(operation, "refuse", "link_target_not_found", {
            actor_slug: principal.slug,
            linked_subject_kind: projected.linked_subject_kind,
            linked_subject_id: projected.linked_subject_id,
            records_written: 0, readback: null,
          });
        }
      }
      const envelope = storeEnvelope("stored_salesforce_reference",
        storedSalesforceReferenceRecord({
          reference: projected, recorded_by: principal.slug, recorded_at: now,
        }), { sets_lifecycle_state: false, phase_label_is_doctorcre_state: false });
      const row = await one(client,
        "SELECT ops.j102_record_salesforce_reference($1::jsonb, $2::text, $3::text) AS outcome",
        [J(envelope), request.idempotency_key, requestDigest(operation, request, principal)]);
      return resultFromOutcome(operation, parse(row.outcome), principal);
    });
  }

  // -- Q081. run-migration-shadow ------------------------------------------

  /**
   * Run the migration shadow once. The DATABASE reads both sides and decides the
   * comparison, because the legacy rows and the projection are both there and a
   * comparison assembled here would be one more copy of each; this module only
   * derives the actor, binds the idempotency key and verifies the answer names
   * the operation and the actor it was asked for.
   */
  async function runMigrationShadow(payload, context) {
    const operation = "run-migration-shadow";
    let { principal, payload: request } = begin(operation, payload, context);
    return withTransaction(async client => {
      const opened = await openOperation(client, operation, principal);
      principal = opened.principal;
      const replay = await replayOutcome(client, operation, request, principal);
      if (replay !== null) return replay;
      const row = await one(client,
        "SELECT ops.j102_run_migration_shadow($1::text, $2::text) AS outcome",
        [request.idempotency_key, requestDigest(operation, request, principal)]);
      return resultFromOutcome(operation, parse(row.outcome), principal);
    });
  }

  // -- 15. record-lifecycle-correction --------------------------------------

  /**
   * A correction is a RECEIPT, not an overwrite.
   *
   * It requires a human verified partner, a reason, and a first-party
   * lifecycle_correction record loaded from the record layer — so the correction
   * itself has an author and durable content, and cannot be conjured from an
   * assistant's summary of what somebody meant. The prior state digest is bound
   * into the receipt, so the history says exactly which version was corrected.
   */
  async function recordLifecycleCorrection(payload, context) {
    const operation = "record-lifecycle-correction";
    let { principal, payload: request } = begin(operation, payload, context);
    const subject_ref = assertClosed(request.subject_ref, SUBJECT_REF_KEYS,
      ["subject_kind", "subject_id"], "payload.subject_ref");
    if (!V5_J102_SUBJECT_KINDS.includes(subject_ref.subject_kind)) {
      fail("unknown_subject_kind", `"${subject_ref.subject_kind}" is not a registered subject kind`,
        { registered: [...V5_J102_SUBJECT_KINDS] });
    }
    assertIdent(subject_ref.subject_id, "payload.subject_ref.subject_id");
    assertIdent(request.correction_record_id, "payload.correction_record_id");
    if (!Array.isArray(request.corrected_fields) || request.corrected_fields.length < 1 ||
        request.corrected_fields.length > 64) {
      fail("invalid_shape", "payload.corrected_fields must name between 1 and 64 fields",
        { path: "payload.corrected_fields" });
    }
    const corrected_fields = request.corrected_fields.map((field, i) =>
      assertIdent(field, `payload.corrected_fields[${i}]`));
    if (typeof request.reason !== "string" || request.reason.trim().length === 0 ||
        request.reason.length > 1000) {
      fail("invalid_shape", "payload.reason must be a non-empty reason of at most 1000 characters",
        { path: "payload.reason" });
    }

    return withTransaction(async client => {
      const opened = await openOperation(client, operation, principal);
      const { now } = opened;
      principal = opened.principal;
      const replay = await replayOutcome(client, operation, request, principal);
      if (replay !== null) return replay;
      const loaded = await loadSubject(client, subject_ref.subject_kind, subject_ref.subject_id);
      if (loaded.state === null) {
        return result(operation, "refuse", "subject_not_found", {
          actor_slug: principal.slug, subject_kind: subject_ref.subject_kind,
          subject_id: subject_ref.subject_id, records_written: 0, readback: null,
        });
      }
      if (subject_ref.expected_state_digest !== undefined &&
          subject_ref.expected_state_digest !== null &&
          subject_ref.expected_state_digest !== loaded.state_digest) {
        return result(operation, "refuse", "stale_subject_digest", {
          actor_slug: principal.slug, stored_state_digest: loaded.state_digest,
          expected_state_digest: subject_ref.expected_state_digest,
          records_written: 0, readback: null,
        });
      }
      // The correction's own record must EXIST and be a lifecycle_correction. An
      // approval that lives only in a chat transcript is not one of these.
      const recordRow = await one(client,
        "SELECT ops.j102_first_party_record($1::text, $2::text) AS record",
        ["lifecycle_correction", request.correction_record_id]);
      const storedRecord = parse(recordRow?.record);
      if (storedRecord == null || !isPlainObject(storedRecord.record)) {
        return result(operation, "refuse", "correction_record_not_found", {
          actor_slug: principal.slug,
          missing_fact: "first_party_lifecycle_correction_record",
          missing_fact_reason: "a correction is receipted against a durable authored record; record-lifecycle-fact writes one",
          produced_by: "j102_record_first_party_fact",
          derived_from_assistant_text: false,
          records_written: 0, readback: null,
        });
      }
      const envelope = storeEnvelope("stored_correction_receipt",
        storedCorrectionReceiptRecord({
          subject_kind: subject_ref.subject_kind,
          subject_id: subject_ref.subject_id,
          correction_record_id: request.correction_record_id,
          corrected_fields,
          reason: request.reason,
          prior_state_digest: loaded.state_digest,
          corrected_by: principal.slug,
          corrected_at: now,
        }), { append_only: true, humanOnly: true, authorityOnly: true });
      const row = await one(client,
        "SELECT ops.j102_record_correction($1::jsonb, $2::text, $3::text) AS outcome",
        [J(envelope), request.idempotency_key, requestDigest(operation, request, principal)]);
      return resultFromOutcome(operation, parse(row.outcome), principal);
    });
  }

  // -- 16. record-lifecycle-reconciliation ----------------------------------

  /**
   * Q103's visible reconciliation, end to end at the record layer.
   *
   * ORDERED, and every step names whose fact it is:
   *   1. Authenticate, validate the closed payload. An edit names a FIELD and a
   *      VALUE DIGEST; who made it and when are derived, and its CLASS is the
   *      kernel's to decide from its own registry.
   *   2. Open the transaction and take the actor and the instant from the SERVER.
   *   3. Load the subject through the verified read door. THE CURRENT VERSION
   *      DIGEST IS THE STORED ONE — a caller supplying it would be choosing the
   *      version its own edit is judged against, which is the whole question.
   *   4. Run the KERNEL. It decides; nothing here second-guesses it, and nothing
   *      here merges.
   *   5. On `allow` there is nothing to reconcile and nothing is written.
   *   6. On `reconcile`, gather the authoritative evidence of the other side —
   *      the committed state and the tail of the history that produced it — and
   *      append ONE visible item through ops.j102_record_reconciliation_item.
   *
   * WHAT THIS PATH DOES NOT DO, and each is a fact rather than an omission:
   *
   *   IT NEVER SUPPLIES A CONCURRENT EDIT SET. A caller's account of what the
   *   other partner changed is precisely the thing that cannot be trusted, and
   *   this layer cannot derive one: ops.j102_subject exposes no prior_state_digest
   *   to anchor a diff to. So the kernel is told nothing about it and takes its
   *   own `concurrent_change_not_characterized` branch, which reconciles visibly
   *   rather than merging on an absence. The item records that it is
   *   uncharacterized; it does not imply the other side made no edits.
   *
   *   IT AUTO-MERGES NOTHING, and cannot. The kernel's merge branch needs a
   *   demonstrably non-overlapping, POLICY-CLASSIFIED, entirely routine edit set,
   *   and it is unreachable twice over here: no concurrent set is supplied, and
   *   the field-class registry registers no routine field at all.
   *
   *   IT DOES NOT DEDUPLICATE BY READING FIRST. An earlier revision looked for an
   *   open item with the same subject, versions and conflict kind and returned it
   *   instead of writing. That could not be a correctness claim: it was not
   *   atomic, two callers passed it at the same instant, and it could not tell a
   *   stale reading from a current one. Duplication is settled where it can be —
   *   under the idempotency key, inside the writer — and the property that
   *   survives is narrower and true: a RETRY of the same request replays, and two
   *   DIFFERENT proposals against the same two versions are two real conflicts
   *   and both stay visible.
   *
   *   AND IT CLAIMS NOTHING THE WRITER ENFORCES. The staleness bindings are the
   *   writer's, under its own lock, at commit time: this layer sends the operand
   *   it read and the evidence it gathered, and the receipt reports what the
   *   database bound rather than what this function hoped.
   */
  async function recordLifecycleReconciliation(payload, context) {
    const operation = "record-lifecycle-reconciliation";
    let { principal, payload: request } = begin(operation, payload, context);
    const raw = assertClosed(request.subject_ref, SUBJECT_REF_KEYS,
      // THE BASE VERSION IS REQUIRED HERE, unlike everywhere else it is optional:
      // a concurrent-edit question with no version to have decided against is not
      // a question, and defaulting it to the stored digest would answer "no
      // conflict" to every caller that forgot to say.
      ["subject_kind", "subject_id", "expected_state_digest"], "payload.subject_ref");
    if (!V5_J102_SUBJECT_KINDS.includes(raw.subject_kind)) {
      fail("unknown_subject_kind", `"${String(raw.subject_kind)}" is not a registered subject kind`,
        { path: "payload.subject_ref.subject_kind", registered: [...V5_J102_SUBJECT_KINDS] });
    }
    const subject_kind = raw.subject_kind;
    const subject_id = assertIdent(raw.subject_id, "payload.subject_ref.subject_id");
    const base_version_digest = assertDigestRef(raw.expected_state_digest,
      "payload.subject_ref.expected_state_digest");

    if (!Array.isArray(request.edits) || request.edits.length < 1 ||
        request.edits.length > 256) {
      fail("invalid_shape", "payload.edits must name between 1 and 256 edited fields",
        { path: "payload.edits" });
    }
    const edits = request.edits.map((edit, i) => {
      const path = `payload.edits[${i}]`;
      assertClosed(edit, EDIT_REF_KEYS, EDIT_REF_KEYS, path);
      return {
        field: assertIdent(edit.field, `${path}.field`),
        value_digest: assertDigestRef(edit.value_digest, `${path}.value_digest`),
      };
    });

    return withTransaction(async client => {
      const opened = await openOperation(client, operation, principal);
      const { now } = opened;
      principal = opened.principal;
      // REPLAY FIRST, before any state is read, exactly as every other write
      // operation here does. A settled key returns its stored outcome even though
      // the subject has moved since, and a replay taken after the read would
      // re-evaluate a conflict that was already filed.
      const replay = await replayOutcome(client, operation, request, principal);
      if (replay !== null) return replay;
      const stored = await readSubjectVerified(client, subject_kind, subject_id);
      if (stored == null) {
        return result(operation, "refuse", "subject_not_found", {
          actor_slug: principal.slug, subject_kind, subject_id,
          records_written: 0, reconciliation_item: null, readback: null,
        });
      }
      const current_version_digest = stored.state_digest;

      // THE CONCURRENT SIDE IS SUPPLIED WHEN, AND ONLY WHEN, IT IS KNOWN. The
      // stored row names the digest it replaced and the transition that wrote
      // it; when that digest is the caller's base, exactly one transition lies
      // between them and its declared write set is the concurrent edit set, each
      // value the digest of what the row now holds. Otherwise nothing is
      // supplied and the kernel reconciles on the uncharacterized branch.
      const characterizedBy = base_version_digest !== current_version_digest &&
        stored.prior_state_digest === base_version_digest &&
        V5_J102_TRANSITION_IDS.includes(stored.established_by_transition)
        ? stored.established_by_transition : null;
      const concurrent = characterizedBy === null ? []
        : (v5J102TransitionWrites(characterizedBy)[subject_kind] ?? []).map(field => ({
          field,
          value_digest: digest(stored.state[field] ?? null),
          edited_by: stored.updated_by,
          edited_at: stored.updated_at,
        }));

      const evaluated = evaluateConcurrentEdit({
        tenant: ORGANIZATION_TENANT_ID,
        base_version_digest,
        // THE DATABASE'S ANSWER, not the caller's.
        current_version_digest,
        // The actor and the instant are stamped here; the caller stated only
        // which field it edited and what the new value hashes to.
        incoming: edits.map(edit => ({
          ...edit, edited_by: principal.slug, edited_at: now,
        })),
        ...(concurrent.length === 0 ? {} : { concurrent }),
        actor: kernelActor(principal),
      });

      const base = {
        actor_slug: principal.slug,
        subject_kind, subject_id,
        base_version_digest, current_version_digest,
        subject_moved: base_version_digest !== current_version_digest,
        incoming_fields: evaluated.incoming_fields,
        merged: evaluated.merged === true,
        auto_merged_fields: evaluated.auto_merged_fields ?? [],
        last_writer_wins: false,
        silent_overwrite: false,
        resolved_by_machine: false,
        advances_lifecycle_state: false,
      };

      if (evaluated.decision === "allow") {
        // The ONLY reachable allow on this path: the subject has not moved, so
        // there is no conflict to make visible and nothing is written. The
        // auto-merge allow needs a concurrent set this layer never supplies AND a
        // routine field the registry does not register.
        if (evaluated.merged === true) {
          fail("unexpected_auto_merge",
            "the kernel auto-merged an edit this layer supplied no concurrent set for; the merge branch must stay unreachable here",
            { subject_kind, subject_id, reason_id: evaluated.reason_id });
        }
        return result(operation, "allow", evaluated.reason_id, {
          ...base, records_written: 0, reconciliation_item: null, readback: null,
        });
      }

      // === RECONCILE: make the conflict visible ==============================
      // THE WRITER BINDS THE REST: the compare-and-swap operand for the one
      // subject the conflict is about, the idempotency key and the request
      // digest, and the kernel's diagnostic labelled as the caller's. A RETRY
      // collapses under the key; two different proposals against the same two
      // versions both land, because there is no unique index over the pair.
      const outcome = await writeReconciliationItem(client, {
        evaluated, subject_kind, subject_id, stored,
        characterized: concurrent.length > 0,
        idempotency_key: request.idempotency_key,
        request_digest: requestDigest(operation, request, principal),
      });
      return resultFromOutcome(operation, outcome, principal);
    });
  }

  return Object.freeze({
    readCreLifecycle,
    recordLifecycleFact,
    runMigrationShadow,
    recordEvidenceSubjectLink,
    initializeProspectRelationship,
    initializeAssignment,
    initializePropertyNegotiation,
    recordRepresentationAgreement,
    openCreAssignment,
    recordLoiSubmission,
    recordLoiAcceptance,
    commitWinningProperty,
    recordDealExecution,
    recordDiligenceOutcome,
    recordDealClosing,
    cancelPendingDeal,
    recordDealAxis,
    linkSalesforceReference,
    recordLifecycleCorrection,
    recordLifecycleReconciliation,
  });
}

// ---------------------------------------------------------------------------
// Load-time self-checks, for the invariants a later edit could break silently.
// ---------------------------------------------------------------------------

for (const name of V5_J102_OPERATIONS) {
  const schema = OPERATION_SCHEMAS[name];
  if (schema === undefined) {
    throw new V5J102StoreError("contract_self_check_failed", `${name} has no operation schema`);
  }
  if (schema.transition !== null && schema.transition !== "dispatch_on_instrument_kind" &&
      schema.transition !== "dispatch_on_declared_axis" &&
      !V5_J102_TRANSITION_IDS.includes(schema.transition)) {
    throw new V5J102StoreError("contract_self_check_failed",
      `${name} names unregistered transition "${schema.transition}"`);
  }
  if (schema.subject_kind !== undefined && !V5_J102_SUBJECT_KINDS.includes(schema.subject_kind)) {
    throw new V5J102StoreError("contract_self_check_failed",
      `${name} names unregistered subject kind "${schema.subject_kind}"`);
  }
  if (schema.initialization !== undefined) {
    if (!V5_J102_INITIALIZATION_IDS.includes(schema.initialization)) {
      throw new V5J102StoreError("contract_self_check_failed",
        `${name} names unregistered initialization "${schema.initialization}"`);
    }
    // AN OPERATION IS ONE DOOR OR THE OTHER, NEVER BOTH. An operation that both
    // initialized a subject and performed a transition would be the null-operand
    // bypass reassembled out of two halves that are each individually correct.
    if (schema.transition !== null) {
      throw new V5J102StoreError("contract_self_check_failed",
        `${name} both initializes a subject and performs a transition; the two doors stay separate`);
    }
    if (schema.subject_kind !==
        v5J102InitializationContract(schema.initialization).subject_kind) {
      throw new V5J102StoreError("contract_self_check_failed",
        `${name} claims to create a ${schema.subject_kind} and its initialization creates a ` +
        `${v5J102InitializationContract(schema.initialization).subject_kind}`);
    }
  }
}

// Every registered initialization has exactly one operation, so a contract the
// kernel declares cannot sit unreachable and two operations cannot both claim to
// be the door to one creation.
for (const initialization_id of V5_J102_INITIALIZATION_IDS) {
  const operations = V5_J102_OPERATIONS
    .filter(name => OPERATION_SCHEMAS[name].initialization === initialization_id);
  if (operations.length !== 1) {
    throw new V5J102StoreError("contract_self_check_failed",
      `${initialization_id} is performed by ${operations.length} operations; it is performed by exactly one`);
  }
}

// The wired-initialization registry has to keep describing what actually shipped.
for (const entry of V5_J102_WIRED_INITIALIZATION_CAPABILITIES) {
  const schema = OPERATION_SCHEMAS[entry.operation];
  if (schema === undefined || schema.initialization === undefined ||
      v5J102InitializationContract(schema.initialization).subject_kind !==
        entry.creates_subject_kind) {
    throw new V5J102StoreError("contract_self_check_failed",
      `the wired-initialization registry claims ${entry.operation} creates a ${entry.creates_subject_kind}, and the operation table does not agree`);
  }
}

for (const [axis, transition] of Object.entries(AXIS_TRANSITIONS)) {
  if (!V5_J102_DEAL_AXES.includes(axis)) {
    throw new V5J102StoreError("contract_self_check_failed",
      `the axis dispatch names "${axis}", which is not a registered deal axis`);
  }
  if (!V5_J102_TRANSITION_IDS.includes(transition)) {
    throw new V5J102StoreError("contract_self_check_failed",
      `the axis dispatch names unregistered transition "${transition}"`);
  }
}

// The open-question registry has to stay a list of QUESTIONS rather than a list
// of answers: every entry names what the rail does today and why nobody has
// ruled, and exactly one of them may say it is already encoded — the actor-class
// assumption, which is live and labelled as an assumption rather than a ruling.
for (const entry of V5_J102_OPEN_OWNER_QUESTIONS) {
  for (const key of ["question", "status", "today", "why_unsettled"]) {
    if (typeof entry[key] !== "string" || entry[key].length === 0) {
      throw new V5J102StoreError("contract_self_check_failed",
        `the open-question registry entry "${entry.question}" does not state its ${key}`);
    }
  }
  if (typeof entry.encoded_without_a_ruling !== "boolean") {
    throw new V5J102StoreError("contract_self_check_failed",
      `the open-question registry entry "${entry.question}" does not say whether it is already encoded`);
  }
  if (entry.encoded_without_a_ruling &&
      entry.status !== "implementation_assumption_live_and_unratified") {
    throw new V5J102StoreError("contract_self_check_failed",
      `"${entry.question}" is encoded and is not labelled an unratified assumption; ` +
      "an unsettled question that has been encoded is a policy invented on somebody's reading");
  }
  if (entry.status === "ruled_by_owner" &&
      (typeof entry.ruling !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(entry.ruled_on ?? ""))) {
    throw new V5J102StoreError("contract_self_check_failed",
      `"${entry.question}" claims an owner ruling without quoting it and dating it`);
  }
}

// The unwired-capability registry has to stay a list of FACTS rather than a list
// of intentions, so every entry must name the missing thing, its owner AND the
// exact change that would produce it — an absence with no named remedy is how a
// gap becomes permanent furniture.
for (const entry of V5_J102_UNWIRED_CAPABILITIES) {
  for (const key of ["capability", "missing_fact", "why", "produced_by", "exact_minimal_change"]) {
    if (typeof entry[key] !== "string" || entry[key].length === 0) {
      throw new V5J102StoreError("contract_self_check_failed",
        `the unwired-capability registry entry "${entry.capability}" does not name its ${key}`);
    }
  }
}

// The wired-concurrency registry has to keep describing what actually shipped,
// and — because "wired" is the claim most easily overstated — every entry must
// name its residual and must NOT claim runtime registration, which this module
// does not perform.
for (const entry of V5_J102_WIRED_CONCURRENCY_CAPABILITIES) {
  for (const key of ["capability", "operation", "kernel_entry_point", "what_it_does",
    "residual"]) {
    if (typeof entry[key] !== "string" || entry[key].length === 0) {
      throw new V5J102StoreError("contract_self_check_failed",
        `the wired-concurrency registry entry "${entry.capability}" does not state its ${key}`);
    }
  }
  if (entry.registered_at_runtime !== false) {
    throw new V5J102StoreError("contract_self_check_failed",
      `"${entry.capability}" claims runtime registration, and this module registers nothing`);
  }
  if (!V5_J102_OPERATIONS.includes(entry.operation)) {
    throw new V5J102StoreError("contract_self_check_failed",
      `"${entry.capability}" names operation "${entry.operation}", which this store does not offer`);
  }
}

// Every composed read kind is a registered read kind, or `read-cre-lifecycle`
// would refuse it before reaching the branch that answers it.
for (const kind of V5_J102_COMPOSED_READ_KINDS) {
  if (!V5_J102_READ_KINDS.includes(kind)) {
    throw new V5J102StoreError("contract_self_check_failed",
      `the composed read kind "${kind}" is not a registered read kind`);
  }
}

// Every absent-reader entry must name a REAL evidence kind, or the fail-closed
// branch would never fire and the path it is meant to shut would quietly open.
for (const [kind, entry] of Object.entries(V5_J102_ABSENT_EVIDENCE_READERS)) {
  if (!V5_J102_EVIDENCE_KINDS.includes(kind) || entry.evidence_kind !== kind) {
    throw new V5J102StoreError("contract_self_check_failed",
      `the absent-reader registry names "${kind}", which is not a registered evidence kind`);
  }
  if (v5J102EvidenceContract(kind).source !== "typed_approval") {
    throw new V5J102StoreError("contract_self_check_failed",
      `the absent-reader registry names "${kind}", which is not established from a typed approval`);
  }
}

// ---------------------------------------------------------------------------
// THE DOOR: the J102 verbs, ready for tools.js's registerTools.
//
// Every verb, the one read included, runs on the WRITER connection. mcp.js
// opens that transaction, sets the authenticated actor and the server-verified
// sponsor with setWriterActorContext (carr.acting_actor_slug,
// carr.verified_human_actor_slug, carr.sponsoring_human_slug), and routes an
// authorityOnly verb over the partner's authority credential. The store then
// derives the principal and the sponsoring partner from THAT transaction
// (ops.f01_principal, ops.j102_sponsoring_partner) and refuses when they
// disagree with the handler. The read declares writerConnection without write,
// so mcp.js opens it `begin read only`: the reader connection carries no actor
// context, and a lifecycle read derives its principal like every other call.
// ---------------------------------------------------------------------------

const J102_TOOL_KEY_SCHEMAS = deepFreeze({
  schema_version: { type: "string" },
  idempotency_key: { type: "string" },
  selector: { type: "object" },
  fact: { type: "object" },
  link: { type: "object" },
  related_refs: { type: "object" },
  declared: { type: "object" },
  subject_ref: { type: "object" },
  evidence_refs: { type: "array" },
  opportunity_id: { type: "string" },
  opportunity_name: { type: "string" },
  opportunity_phase: { type: "string" },
  observed_at: { type: "string" },
  linked_subject_kind: { type: "string" },
  linked_subject_id: { type: "string" },
  correction_record_id: { type: "string" },
  corrected_fields: { type: "array" },
  reason: { type: "string" },
  edits: { type: "array" },
});

const J102_DATABASE_REFUSAL = /^((?:j102|f01)_[a-z0-9_]+)/;
const J102_ERROR_NAMES = Object.freeze(["V5J102StoreError", "V5J102Error"]);

/** The closed JSON schema for one verb, built from the store's own key list. */
export function v5J102ToolInputSchema(operation) {
  const schema = OPERATION_SCHEMAS[operation];
  if (!schema) {
    throw new V5J102StoreError("unknown_operation", `"${operation}" is not a J102 operation`, { operation });
  }
  const properties = {};
  for (const key of schema.keys) {
    if (!J102_TOOL_KEY_SCHEMAS[key]) {
      throw new V5J102StoreError("tool_key_without_schema",
        `${operation} accepts "${key}" but the door declares no JSON type for it`, { operation, key });
    }
    properties[key] = { ...J102_TOOL_KEY_SCHEMAS[key] };
  }
  return { type: "object", additionalProperties: false, properties, required: [...schema.required] };
}

/**
 * A handle over ONE already-open client whose transaction() runs the body on
 * that same client. mcp.js owns BEGIN/COMMIT for every verb; a store that
 * issued its own would commit half a verb, or warn and nest.
 */
export function v5J102TransactionScopedHandle(client) {
  if (!client || typeof client.query !== "function") {
    throw new V5J102StoreError("database_handle_required", "the verb has no open database client");
  }
  const handle = {
    query: (text, params) => client.query(text, params),
    transaction: fn => fn(handle),
  };
  return handle;
}

/** Translate a J102 refusal into the tool surface's refusal, keeping its code. */
export function v5J102ToolRefusal(error, ToolError) {
  if (error && J102_ERROR_NAMES.includes(error.name) && typeof error.code === "string") {
    return new ToolError({
      error: error.code, message: error.message,
      ...(error.detail !== undefined ? { detail: error.detail } : {}),
    });
  }
  const match = typeof error?.message === "string" ? J102_DATABASE_REFUSAL.exec(error.message) : null;
  if (match && typeof error?.code === "string") {
    return new ToolError({ error: match[1], message: error.message, sqlstate: error.code });
  }
  return null;
}

/**
 * The J102 verbs for tools.js. `createStore` is injectable for the offline
 * suite; production uses the store above.
 */
export function creLifecycleStoreTools({
  withEnvelope, ToolError, createStore = createCreLifecycleStore,
} = {}) {
  if (typeof withEnvelope !== "function" || typeof ToolError !== "function") {
    throw new V5J102StoreError("tool_wiring_incomplete",
      "creLifecycleStoreTools needs the shared withEnvelope and ToolError");
  }
  const tools = {};
  for (const registration of v5J102ToolRegistrations()) {
    const { name, store_operation, write, humanOnly, authorityOnly, handler: method, role } = registration;
    const run = async (client, actor, args) => {
      const store = createStore({ db: v5J102TransactionScopedHandle(client) });
      try {
        const answer = await store[method](args ?? {}, { actor: { ...(actor ?? {}) } });
        return {
          ok: answer.decision !== "refuse",
          ...answer,
          effects: { ...answer.effects, database_writes: write ? "j102_record_layer_rows_only" : 0 },
        };
      } catch (error) {
        const refusal = v5J102ToolRefusal(error, ToolError);
        if (refusal) throw refusal;
        throw error;
      }
    };
    tools[name] = {
      write,
      writerConnection: true,
      ...(humanOnly ? { humanOnly: true } : {}),
      ...(authorityOnly ? { authorityOnly: true } : {}),
      description: `DoctorCRE v5 J102 CRE lifecycle. ${role}`,
      inputSchema: v5J102ToolInputSchema(store_operation),
      handler: write
        ? async (c, actor, args) => withEnvelope(c, actor, name, args, () => run(c, actor, args))
        : async (c, actor, args) => run(c, actor, args),
    };
  }
  return tools;
}

// The door must cover exactly the operations the store serves.
for (const operation of V5_J102_OPERATIONS) v5J102ToolInputSchema(operation);
