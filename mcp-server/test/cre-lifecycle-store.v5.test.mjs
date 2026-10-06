// V5-J102 — the persistence tail, proved case by case.
//
// Everything here is synthetic and nothing reaches a real database, provider,
// network or filesystem. The store runs against a SCRIPTED FAKE HANDLE that
// records every statement and every parameter in order, so these tests prove the
// things a Node suite can actually prove about a persistence layer:
//
//   * which values are DERIVED and which are refused from the caller,
//   * WHICH STATEMENTS RUN, IN WHAT ORDER — the replay-before-state ordering in
//     particular, which is load-bearing rather than incidental,
//   * that subjects and evidence are LOADED and that caller pins are CHECKED
//     against the readback rather than believed,
//   * that the compare-and-swap operands and the evidence recheck manifest
//     actually travel to the database,
//   * that a failing write ROLLS BACK and does not report success,
//   * that a replay reports what LANDED rather than what a fresh evaluation
//     would say now.
//
// NOTHING HERE MIRRORS A RETURN VALUE BACK AT ITSELF. Every assertion is either
// about a statement the store issued, a parameter it bound, the order it did so
// in, or a refusal it produced without issuing a write — never "the fake said X
// and the store returned X".
//
// The things a fake cannot prove — real concurrency, real append-only refusal,
// real direct-DML refusal, real evidence movement under a lock — are the SQL
// fixture's job, and mcp-server/test/cre-lifecycle-postgres.sql asserts them
// there instead of them being claimed twice here.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { canonicalJson, digest } from "../src/artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  V5_J102_AUTHORITY_INJECTION_FRAGMENTS,
  V5_J102_CONFLICT_KINDS,
  V5_J102_TRANSITION_IDS, V5_J102_DEAL_AXES, V5_J102_EVIDENCE_KINDS,
  V5_J102_EVIDENCE_INTEGRITY, V5_J102_EVIDENCE_LOADER,
  V5_J102_EVENT_SCHEMA_VERSION,
  V5_J102_FIELD_CLASSES,
  V5_J102_FIELD_CLASS_REGISTRY,
  V5_J102_INITIALIZATION_IDS,
  V5_J102_MATERIAL_FIELD_CLASSES,
  V5_J102_SUBJECT_KINDS,
  V5_J102_UNCLASSIFIED_FIELD_POLICY,
  assertLifecycleSubject,
  evaluateConcurrentEdit,
  evaluateConcurrentTransition,
  V5_J102_DERIVED_FIELDS,
  v5J102TransitionWrites,
  evaluateLifecycleInitialization,
  evaluateLifecycleTransition, v5J102EvidenceContract,
  v5J102InitializationContract, v5J102TransitionContract,
} from "../src/cre-lifecycle.v5.js";
import {
  V5_J102_ABSENT_EVIDENCE_READERS,
  V5_J102_AXIS_TRANSITIONS,
  V5_J102_COMPOSED_READ_KINDS,
  V5_J102_INSTRUMENT_TRANSITIONS,
  V5_J102_DECLARED_DOMAIN_FIELDS,
  V5_J102_DECLARED_SELECTOR_FIELDS,
  V5_J102_DERIVED_ONLY_FIELDS,
  V5_J102_ENVELOPE_SCHEMA_VERSION,
  V5_J102_OPEN_OWNER_QUESTIONS,
  V5_J102_OPERATIONS,
  V5_J102_READ_KINDS,
  V5_J102_STORE_RECORD_KINDS,
  V5_J102_STORE_SCHEMA_VERSION,
  V5_J102_STORED_EVENT_SCHEMA_VERSION,
  V5_J102_STORED_SUBJECT_SCHEMA_VERSION,
  V5_J102_UNWIRED_CAPABILITIES,
  V5_J102_WIRED_CONCURRENCY_CAPABILITIES,
  V5_J102_WIRED_INITIALIZATION_CAPABILITIES,
  V5J102StoreError,
  createCreLifecycleStore,
  storedEventRecord,
  storedSubjectRecord,
  v5J102EnvelopeCanonicalBytes,
  v5J102StoreEnvelope,
  v5J102StoreOperationSchemas,
  v5J102ToolRegistrations,
} from "../src/cre-lifecycle-store.v5.js";

// --- synthetic fixtures ----------------------------------------------------

const SERVER_NOW = "2026-09-09T12:00:00.000Z";
const T = { early: "2026-09-01T09:00:00Z", mid: "2026-09-05T09:00:00Z", late: "2026-09-08T09:00:00Z" };
const D = n => `sha256:${String(n).padStart(2, "0").repeat(32)}`;

const JOE = Object.freeze({ slug: "joe", display: "Joe", human: true, via: "oauth-google" });
const AGENT = Object.freeze({
  slug: "codex", display: "Codex", human: false, via: "oauth-google",
  sponsoring_human_slug: "joe", human_slug: "joe",
});
const ctx = actor => ({ actor });

const relationshipState = (over = {}) => ({
  subject_kind: "relationship", subject_id: "rel-synthetic-1",
  relationship_state: "prospect", active_engagement_count: 0, party_id: null, ...over,
});
const assignmentState = (over = {}) => ({
  subject_kind: "assignment", subject_id: "asg-synthetic-1",
  engagement_id: "eng-synthetic-1", assignment_phase: "search",
  open_negotiation_count: 0, selected_property_id: null,
  active_lease_draft_target_id: null, pending_deal_id: null,
  multi_target_exception_ref: null, ...over,
});
const dealState = (over = {}) => ({
  subject_kind: "deal", subject_id: "deal-synthetic-1",
  assignment_id: "asg-synthetic-1", property_id: "prop-synthetic-1",
  instrument_kind: "lease", deal_state: "pending", execution_state: "unexecuted",
  diligence_state: "not_applicable", closing_state: "not_reached",
  commission_agreement_state: "absent", invoice_state: "not_invoiced",
  payment_state: "unpaid", completion_state: "open",
  cancellation_reason: null, closing_date: null, ...over,
});

/** The shape ops.j102_subject returns: the state plus its recomputed digest. */
const storedSubject = state => ({
  subject_kind: state.subject_kind,
  subject_id: state.subject_id,
  state,
  state_digest: digest(state),
  established_by_transition: "synthetic-fixture",
  updated_by: "joe",
  updated_at: T.late,
  integrity: "recomputed_from_committed_row",
});

/** The shape ops.f01_read('document', ...) returns: a verified F01 envelope. */
const storedDocument = (over = {}) => {
  const record = {
    schema_version: "doctorcre-v5-f01-stored-document-version.v1",
    document_class: "synthetic_agreement",
    neon_identity: { document_id: "doc-synthetic-1", content_digest: D(1), version_no: 1 },
    preparation_state: "approved_for_delivery", delivery_state: "delivered",
    signature_state: "fully_executed", validity_state: "effective", version_state: "current",
    official_filing_state: "complete_official_filing",
    ...over,
  };
  return { body: { record, record_digest: digest(record), integrity: "recomputed_from_committed_row" } };
};

/**
 * The shape ops.j102_first_party_record returns.
 *
 * IT NAMES ITS SUBJECT. The binding lives on the stored row, not on the request
 * that later reads it, so these fixtures carry the subject each record kind is
 * about — and the adversarial cases below hand back a record about somebody
 * else's deal without changing anything else on it.
 */
const FACT_SUBJECT = Object.freeze({
  assignment_mandate: { subject_kind: "assignment", subject_id: "asg-synthetic-1" },
  winning_property_commitment: { subject_kind: "assignment", subject_id: "asg-synthetic-1" },
});
const storedFact = (record_kind, over = {}) => {
  const bound = FACT_SUBJECT[record_kind] ??
    { subject_kind: "deal", subject_id: "deal-synthetic-1" };
  const record = {
    schema_version: "doctorcre-v5-j102-stored-first-party-record.v1",
    tenant: ORGANIZATION_TENANT_ID, record_kind, record_id: "rec-synthetic-1",
    subject_kind: bound.subject_kind, subject_id: bound.subject_id,
    reason: null, detail: null, closing_date: null, supporting_document_id: null,
    recorded_by: "joe", recorded_by_authorization_class: "verified_partner",
    recorded_at: T.mid, advances_lifecycle_state: false, ...over,
  };
  return { record, record_digest: digest(record), integrity: "recomputed_from_committed_row" };
};

/** The shape ops.j102_evidence_subject_link returns when the pin IS associated. */
const storedLink = (over = {}) => {
  const record = {
    schema_version: "doctorcre-v5-j102-stored-evidence-subject-link.v1",
    tenant: ORGANIZATION_TENANT_ID,
    evidence_source: "f01_document", evidence_ref: "doc-synthetic-1",
    version_no: 1, content_digest: D(1),
    subject_kind: "deal", subject_id: "deal-synthetic-1",
    associated_by: "joe", associated_by_authorization_class: "verified_partner",
    associated_at: T.early,
    advances_lifecycle_state: false, creates_document: false,
    asserts_document_state: false, ...over,
  };
  return { record, link_digest: digest(record), integrity: "recomputed_from_committed_row" };
};

// --- the scripted fake handle ---------------------------------------------

class FakeDb {
  constructor(script = {}) {
    this.script = { ...script };
    this.calls = [];
    this.began = 0;
    this.committed = 0;
    this.rolledBack = 0;
    this.lastWrite = null;
  }

  set(key, value) { this.script[key] = value; return this; }

  /** The ordered list of logical statements, for the ordering assertions. */
  get sequence() {
    return this.calls.map(({ text }) => {
      if (["BEGIN", "COMMIT", "ROLLBACK"].includes(text)) return text;
      const match = /ops\.(f01_[a-z_]+|j102_[a-z_]+)\(/.exec(text);
      return match ? match[1] : "unknown";
    });
  }

  paramsFor(fn) {
    const call = this.calls.find(c => c.text.includes(`ops.${fn}(`));
    return call ? call.params : null;
  }

  callsTo(fn) {
    return this.calls.filter(c => c.text.includes(`ops.${fn}(`));
  }

  async query(text, params = []) {
    this.calls.push({ text, params });
    if (text === "BEGIN") { this.began += 1; return { rows: [] }; }
    if (text === "COMMIT") { this.committed += 1; return { rows: [] }; }
    if (text === "ROLLBACK") { this.rolledBack += 1; return { rows: [] }; }

    if (text.includes("ops.f01_principal()") && text.includes("ops.f01_now_text()")) {
      const principal = this.script.principal ?? {
        actor_slug: "joe", human: true, authorization_class: "verified_partner",
        derived_by: "server_established_transaction_context",
      };
      return { rows: [{
        principal,
        server_now: this.script.server_now ?? SERVER_NOW,
        // Ruling (c): what ops.j102_sponsoring_partner() derives — the partner
        // itself, or (for every synthetic agent here) its sponsor, joe.
        sponsoring_partner: "sponsoring_partner" in this.script ? this.script.sponsoring_partner
          : principal.authorization_class === "verified_partner" ? principal.actor_slug : "joe",
      }] };
    }
    if (text.includes("ops.j102_replay_outcome(")) {
      return { rows: [{ outcome: this.script.replay_outcome ?? null }] };
    }
    if (text.includes("ops.j102_subject(")) {
      const [subject_kind, subject_id] = params;
      const subjects = this.script.subjects ?? {};
      return { rows: [{ subject: subjects[`${subject_kind}:${subject_id}`] ?? null }] };
    }
    if (text.includes("ops.f01_read('document'")) {
      return { rows: [{ body: this.script.document ?? null }] };
    }
    if (text.includes("ops.f01_stored_artifact(")) {
      return { rows: [{ artifact: this.script.artifact ?? null }] };
    }
    if (text.includes("ops.j102_first_party_record(")) {
      const [record_kind] = params;
      const facts = this.script.facts ?? {};
      return { rows: [{ record: facts[record_kind] ?? null }] };
    }
    // THE ASSOCIATION READER ASKS A YES/NO QUESTION, and the fake answers it the
    // same way: keyed on the SUBJECT as well as the pin, so a script that
    // associates a document with one deal answers null for every other deal
    // rather than handing the association back with somebody else's subject on it.
    if (text.includes("ops.j102_evidence_subject_link(")) {
      const [evidence_source, evidence_ref, version_no, content_digest,
        subject_kind, subject_id] = params;
      const links = this.script.links ?? {};
      const key = [evidence_source, evidence_ref, version_no, content_digest,
        subject_kind, subject_id].join("|");
      return { rows: [{ link: links[key] ?? null }] };
    }
    // The read door answers PER KIND when a fixture scripts one, so a composed
    // read that issues two different reads gets two different answers rather than
    // the same blob twice — which is what makes the ownership assertions below
    // about the store's composition rather than about the fake.
    if (text.includes("ops.j102_read(")) {
      const [kind] = params;
      const byKind = this.script.read_bodies ?? {};
      if (Object.prototype.hasOwnProperty.call(byKind, kind)) {
        return { rows: [{ body: { operation: "read-cre-lifecycle", kind, body: byKind[kind] } }] };
      }
      return { rows: [{ body: this.script.read_body ?? { body: null } }] };
    }

    // Owner ruling (b): the store's census of live sibling negotiations.
    if (text.includes("SELECT subject_id FROM ops.j102_subject_current")) {
      return { rows: (this.script.active_negotiations ?? []).map(subject_id => ({ subject_id })) };
    }

    for (const fn of ["j102_apply_transition", "j102_initialize_subject",
      "j102_record_first_party_fact",
      "j102_record_evidence_subject_link", "j102_record_salesforce_reference",
      "j102_record_correction", "j102_record_reconciliation_item"]) {
      if (text.includes(`ops.${fn}(`)) {
        this.lastWrite = { fn, params };
        if (this.script.writeError) throw this.script.writeError;
        return { rows: [{ outcome: this.outcomeFor(fn, params) }] };
      }
    }
    throw new Error(`unscripted statement: ${text}`);
  }

  /**
   * The outcome shape each writer builds, reconstructed FROM THE PARAMETERS the
   * store actually bound. A fake that returned a canned blob would pass whatever
   * the store sent, including nothing at all; reading the parameters is what
   * makes these assertions about the store rather than about the fake.
   */
  outcomeFor(fn, params) {
    const envelope = index => (params[index] == null ? null : JSON.parse(params[index]));
    // The actor the SCRIPTED principal names, not a literal, so a fixture running
    // as a sponsored agent gets an outcome attributed to that agent — which is
    // what resultFromOutcome checks before it reports anything.
    const actor_slug = this.script.principal?.actor_slug ?? "joe";
    if (fn === "j102_apply_transition") {
      const subjects = envelope(2);
      const events = envelope(3);
      const diagnostics = envelope(7);
      return {
        operation: diagnostics.operation,
        decision: "allow",
        outcome: "applied",
        actor_slug,
        transition_id: params[0],
        // M-2, mirrored from the real writer's receipt rather than from the old
        // one: the kernel's diagnostic reason arrives under its own name with its
        // own scope, and the two derived fields arrive with the source label the
        // database stamps on them. The fake is a stand-in for the shape, so it
        // has to be the shape the writer actually returns.
        caller_reported_reason_id: diagnostics.reason_id,
        caller_reported_reason_id_scope:
          "kernel_result_diagnostic_asserted_by_the_caller_and_not_recomputed_here",
        concurrent_merges: diagnostics.concurrent_merges ?? [],
        concurrent_merges_scope: "caller_reported_merge_account_cas_enforced_on_current_row",
        coupled_facts_committed: diagnostics.coupled_facts,
        coupled_facts_committed_source: "derived_from_the_admission_contract",
        decision_refs: diagnostics.decision_refs,
        decision_refs_source: "derived_from_the_admission_contract",
        subject_digests: Object.fromEntries(subjects.map(e =>
          [`${e.record.subject_kind}:${e.record.subject_id}`, digest(e.record.state)])),
        event_digests: events.map(e => e.record_digest),
        evidence_rechecked_under_lock: true,
        readback: { subjects: subjects.map(e => e.record.state) },
      };
    }
    if (fn === "j102_initialize_subject") {
      // The initialization receipt, reconstructed from the parameters the store
      // actually bound. Note what it does NOT carry: no transition_id, no coupled
      // facts, no evidence recheck — because this writer performs no transition
      // and re-reads no evidence, and a fake that invented those fields would let
      // the store's own receipt reader claim properties nothing enforced.
      const subject = envelope(2);
      const event = envelope(3);
      const diagnostics = envelope(6);
      const key = `${subject.record.subject_kind}:${subject.record.subject_id}`;
      return {
        operation: diagnostics.operation,
        decision: "allow",
        outcome: "initialized",
        actor_slug,
        initialization_id: params[0],
        created_subject_kind: subject.record.subject_kind,
        created_subject_id: subject.record.subject_id,
        subject_digests: { [key]: digest(subject.record.state) },
        event_digests: [event.record_digest],
        decision_refs: diagnostics.decision_refs,
        decision_refs_source: "derived_from_the_admission_contract",
        caller_reported_reason_id: diagnostics.reason_id,
        caller_reported_reason_id_scope:
          "kernel_result_diagnostic_asserted_by_the_caller_and_not_recomputed_here",
        creation_shape_enforced: true,
        required_context_enforced: true,
        parent_subjects_locked_and_unmoved: true,
        // The keys the writer says it actually consulted. Reconstructed from the
        // operand map the store bound, minus the created key — which is what the
        // real writer's `v_consulted_keys` amounts to on a well-formed call, and
        // is the field that keeps the two booleans above from reading as a chain
        // walk on a parentless creation.
        context_subjects_consulted: Object.keys(JSON.parse(params[1]))
          .filter(key => key !== `${subject.record.subject_kind}:${subject.record.subject_id}`)
          .sort(),
        context_subjects_consulted_count: Object.keys(JSON.parse(params[1]))
          .filter(key => key !== `${subject.record.subject_kind}:${subject.record.subject_id}`)
          .length,
        subject_created: true,
        readback: { [subject.record.subject_kind]: subject.record.state },
      };
    }
    const record = envelope(0).record;
    if (fn === "j102_record_reconciliation_item") {
      // The GOVERNED writer's receipt, reconstructed from the parameters the
      // store actually bound. It now carries an operation, an actor, a committed
      // instant and the properties it enforced at the write boundary — which is
      // what lets this path go through resultFromOutcome like every sibling and
      // report on replay what LANDED rather than what a fresh reading would say.
      const operands = JSON.parse(params[1]);
      const diagnostics = JSON.parse(params[4]);
      const item_digest = envelope(0).record_digest;
      return {
        operation: diagnostics.operation,
        decision: "reconcile",
        outcome: "recorded",
        actor_slug,
        actor_authorization_class:
          this.script.principal?.authorization_class ?? "verified_partner",
        subject_kind: record.subject_kind,
        subject_id: record.subject_id,
        item_seq: 1,
        item_digest,
        conflict_kind: record.conflict_kind,
        base_version_digest: record.base_version_digest,
        current_version_digest: record.current_version_digest,
        expected_state_digests: operands,
        current_version_bound_to_committed_row: true,
        state_evidence_bound_to_committed_row: true,
        history_evidence_bound_to_committed_history: true,
        conflict_present: true,
        visible: true,
        applied: false,
        resolved_by_machine: false,
        advances_lifecycle_state: false,
        distinct_proposals_collapsed: false,
        committed_at: SERVER_NOW,
        caller_reported_reason_id: diagnostics.reason_id,
        caller_reported_reason_id_scope:
          "kernel_result_diagnostic_asserted_by_the_caller_and_not_recomputed_here",
        request_digest: params[3],
        request_digest_scope: "caller_supplied_intent_digest_not_recomputed_here",
        committed_content_digest: digest({
          item_digest, item_seq: 1, committed_at: SERVER_NOW }),
        committed_content_digest_source: "recomputed_from_committed_rows",
        readback: { item_seq: 1, record },
        external_effects: false,
      };
    }
    if (fn === "j102_record_first_party_fact") {
      return { operation: "record-lifecycle-fact", decision: "allow",
        reason_id: "first_party_record_appended", actor_slug,
        record_kind: record.record_kind, record_id: record.record_id,
        record_digest: envelope(0).record_digest,
        bound_subject_kind: record.subject_kind, bound_subject_id: record.subject_id,
        readback: { record } };
    }
    if (fn === "j102_record_evidence_subject_link") {
      return { operation: "record-evidence-subject-link", decision: "allow",
        reason_id: "evidence_subject_association_appended", actor_slug,
        link_digest: envelope(0).record_digest,
        evidence_source: record.evidence_source,
        bound_subject_kind: record.subject_kind, bound_subject_id: record.subject_id,
        readback: { record } };
    }
    if (fn === "j102_record_salesforce_reference") {
      return { operation: "link-salesforce-reference", decision: "allow",
        reason_id: record.linked_subject_kind == null
          ? "external_reference_recorded_unlinked" : "external_reference_progressively_linked",
        actor_slug, opportunity_id: record.opportunity_id,
        linked_subject_kind: record.linked_subject_kind,
        reference_digest: envelope(0).record_digest, readback: { record } };
    }
    return { operation: "record-lifecycle-correction", decision: "allow",
      reason_id: "correction_receipt_appended", actor_slug,
      receipt_digest: envelope(0).record_digest,
      corrected_fields: record.corrected_fields, readback: { record } };
  }
}

// The evidence references a caller supplies. Note what is NOT here: no document
// state, no signature, no closing date, no record kind.
const documentRef = (evidence_kind, over = {}) => ({
  evidence_kind, document_id: "doc-synthetic-1",
  expected_version_no: 1, expected_content_digest: D(1), ...over,
});
const recordRef = (evidence_kind, over = {}) => ({
  evidence_kind, record_id: "rec-synthetic-1", ...over,
});

// --- registration surface --------------------------------------------------

test("the registration description is a description, and it says what the parent still owes", () => {
  const registrations = v5J102ToolRegistrations();
  // One verb per operation, plus the authority-routed door to the fact writer.
  assert.equal(registrations.length, V5_J102_OPERATIONS.length + 1);
  const partnerFact = registrations.find(e => e.name === "record-partner-lifecycle-fact");
  assert.equal(partnerFact.store_operation, "record-lifecycle-fact");
  assert.equal(partnerFact.handler, "recordLifecycleFact");
  assert.equal(partnerFact.authorityOnly, true);
  assert.equal(registrations.find(e => e.name === "record-lifecycle-fact").authorityOnly, false);
  for (const entry of registrations) {
    assert.ok(V5_J102_OPERATIONS.includes(entry.store_operation));
    assert.ok(entry.role.length > 0, `${entry.name} states its role`);
    assert.ok(entry.handler.length > 0);
    // Registered, sealed (v80) and migration-bound (0704); never accepted.
    assert.equal(entry.registered_in_scac, true);
    assert.equal(entry.registered_in_mutation_registry, true);
    assert.equal(entry.migration_bound, true);
    assert.equal(entry.accepted, false);
  }
  const schemas = v5J102StoreOperationSchemas();
  // Four acts need partner authority: committing to a winner, closing a deal,
  // cancelling one, and correcting the record. Everything else an authenticated
  // sponsored agent may do, because the evidence is what decides.
  // Five acts need partner authority: committing to a winner, closing a deal,
  // cancelling one, correcting the record, and — H5's shape applied to BLOCK-2's
  // producer — saying which transaction a document belongs to. Everything else an
  // authenticated sponsored agent may do, because the evidence is what decides.
  const authorityOnly = V5_J102_OPERATIONS.filter(n => schemas[n].authorityOnly).sort();
  assert.deepEqual(authorityOnly,
    ["cancel-pending-deal", "commit-winning-property", "record-deal-closing",
      "record-evidence-subject-link", "record-lifecycle-correction"]);
  assert.deepEqual(V5_J102_OPERATIONS.filter(n => schemas[n].humanOnly),
    ["record-lifecycle-correction"]);
});

test("the absent-reader registry names real evidence kinds and both missing facts", () => {
  assert.deepEqual(Object.keys(V5_J102_ABSENT_EVIDENCE_READERS).sort(),
    ["approved_representation_equivalent", "multi_target_exception_approval"]);
  for (const entry of Object.values(V5_J102_ABSENT_EVIDENCE_READERS)) {
    assert.ok(entry.missing_fact.length > 0);
    assert.ok(entry.why.length > 0);
    assert.equal(entry.produced_by, "not_produced_by_this_slice");
  }
});

test("an envelope hashes to its own claim, and the canonical bytes are reproducible", () => {
  const record = storedSubjectRecord({
    subject: dealState(), transition_id: "record-lease-execution",
    prior_state_digest: D(9), updated_by: "joe", updated_at: SERVER_NOW,
  });
  const envelope = v5J102StoreEnvelope("stored_lifecycle_subject", record);
  assert.equal(envelope.schema_version, V5_J102_ENVELOPE_SCHEMA_VERSION);
  assert.equal(envelope.record_digest, digest(record));
  assert.equal(v5J102EnvelopeCanonicalBytes(envelope), canonicalJson(envelope));
  assert.throws(() => v5J102StoreEnvelope("stored_invented_kind", record),
    e => e instanceof V5J102StoreError && e.code === "unknown_record_kind");
  assert.equal(V5_J102_STORE_RECORD_KINDS.length, 7);
  assert.ok(V5_J102_STORE_RECORD_KINDS.includes("stored_evidence_subject_link"));
  assert.ok(V5_J102_STORE_RECORD_KINDS.includes("stored_reconciliation_item"));
});

// --- derived-field and authority guards ------------------------------------

test("a caller cannot supply the tenant, the clock, the subject, the evidence or a state axis", async () => {
  const db = new FakeDb();
  const store = createCreLifecycleStore({ db });
  const base = {
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [documentRef("executed_lease")],
  };
  for (const field of ["tenant", "now", "subject", "evidence", "recorded_by",
    "deal_state", "closing_state", "closing_date", "state_digest",
    "relationship_state", "assignment_phase",
    // BLOCK-2 and H5: which subject an authentic record is about, and in what
    // capacity its author wrote it, are read off stored rows and are not a
    // caller's to state on the request that consumes them.
    "subject_binding", "bound_by", "binding_digest", "link_digest",
    "bound_subject_kind", "bound_subject_id"]) {
    await assert.rejects(() => store.recordDealExecution({ ...base, [field]: "x" }, ctx(JOE)),
      e => e instanceof V5J102StoreError && e.code === "caller_derived_field_refused",
      `${field} must be refused as a derived field`);
  }
  // And nothing was attempted against the database.
  assert.equal(db.calls.length, 0);
});

test("a caller cannot assert the outcome or inject authority, and the refusal names which", async () => {
  const db = new FakeDb();
  const store = createCreLifecycleStore({ db });
  const base = {
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [documentRef("executed_lease")],
  };
  for (const field of ["is_signed", "already_executed", "counterparty_accepted",
    // A state axis whose NAME contains an asserted fragment is caught by the
    // asserted guard before the derived one. Both refuse; the reason differs,
    // and the more specific one is the more useful thing to tell a caller.
    "execution_state"]) {
    await assert.rejects(() => store.recordDealExecution({ ...base, [field]: true }, ctx(JOE)),
      e => e instanceof V5J102StoreError && e.code === "caller_asserted_fact_refused",
      `${field} must be refused as an asserted fact`);
  }
  for (const field of ["acting_as", "override_prerequisites", "authorized_by"]) {
    await assert.rejects(() => store.recordDealExecution({ ...base, [field]: "joe" }, ctx(JOE)),
      e => e instanceof V5J102StoreError && e.code === "caller_authority_field_refused",
      `${field} must be refused as an authority claim`);
  }
  assert.equal(db.calls.length, 0);
});

test("the derived-only list covers every lifecycle state axis, so none can be caller-set", () => {
  for (const axis of V5_J102_DEAL_AXES) {
    assert.ok(V5_J102_DERIVED_ONLY_FIELDS.includes(axis), `${axis} must be derived-only`);
  }
  for (const axis of ["relationship_state", "engagement_state", "assignment_phase",
    "negotiation_state", "representation_basis"]) {
    assert.ok(V5_J102_DERIVED_ONLY_FIELDS.includes(axis), `${axis} must be derived-only`);
  }
});

test("an unauthenticated actor, an inadmissible class, and a non-partner on an authorityOnly op all refuse", async () => {
  const db = new FakeDb();
  const store = createCreLifecycleStore({ db });
  const payload = {
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [recordRef("final_closing_settlement")],
  };
  await assert.rejects(() => store.recordDealClosing(payload, ctx({ slug: "nobody", human: true })),
    e => e instanceof V5J102StoreError && e.code === "unauthenticated_actor");
  // identity.js knows classes this lifecycle does not admit; the boundary is
  // drawn here by name rather than surfacing as an unknown-vocabulary throw
  // from the kernel about a class that simply is not entitled to move a record.
  await assert.rejects(() => store.recordDealClosing(payload,
    ctx({ slug: "codex", human: false, probe: true })),
  e => e instanceof V5J102StoreError && e.code === "actor_class_not_admitted_for_lifecycle");
  await assert.rejects(() => store.recordDealClosing(payload, ctx(AGENT)),
    e => e instanceof V5J102StoreError && e.code === "authority_only_operation_refused");
  await assert.rejects(() => store.recordLifecycleCorrection({
    idempotency_key: "j102-fixture-2",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    correction_record_id: "rec-synthetic-1", corrected_fields: ["field_name"],
    reason: "synthetic fixture reason",
  }, ctx(AGENT)), e => e instanceof V5J102StoreError && e.code === "human_only_operation_refused");
  assert.equal(db.calls.length, 0, "no authority failure reaches the database");
});

// --- ordering and bindings -------------------------------------------------

/** The association a lease document needs before it can advance THIS deal. */
const LEASE_LINK_KEY =
  ["f01_document", "doc-synthetic-1", 1, D(1), "deal", "deal-synthetic-1"].join("|");
/** The same, for the engagement letter that establishes THIS client. */
const ETL_LINK_KEY =
  ["f01_document", "doc-synthetic-1", 1, D(1), "relationship", "rel-synthetic-1"].join("|");

function leaseExecutionDb(extra = {}) {
  return new FakeDb({
    subjects: { "deal:deal-synthetic-1": storedSubject(dealState()) },
    document: storedDocument({ document_class: "lease" }),
    links: { [LEASE_LINK_KEY]: storedLink() },
    ...extra,
  });
}

test("replay is claimed BEFORE any state is read, and the whole statement order is exact", async () => {
  const db = leaseExecutionDb();
  const store = createCreLifecycleStore({ db });
  const answer = await store.recordDealExecution({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1",
      expected_state_digest: digest(dealState()) },
    evidence_refs: [documentRef("executed_lease")],
  }, ctx(JOE));
  assert.equal(answer.decision, "allow");
  // THE ORDER IS THE ASSERTION. A replay that ran after the compare-and-swap
  // would refuse a request that had already succeeded, so the claim must come
  // before the first subject read.
  assert.deepEqual(db.sequence, [
    "BEGIN",
    "f01_principal",
    "j102_replay_outcome",
    "j102_subject",
    "f01_read",
    // BLOCK-2: the document is pinned AND its association with this deal is
    // read, both before the writer is called at all.
    "j102_evidence_subject_link",
    "j102_apply_transition",
    "COMMIT",
  ]);
  assert.equal(db.began, 1);
  assert.equal(db.committed, 1);
  assert.equal(db.rolledBack, 0);
});

test("the transition writer receives the CAS digests, the envelopes and the recheck manifest", async () => {
  const db = leaseExecutionDb();
  const store = createCreLifecycleStore({ db });
  await store.recordDealExecution({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [documentRef("executed_lease")],
  }, ctx(JOE));

  const [transition_id, casJson, subjectsJson, eventsJson, recheckJson, key, requestDigest,
    diagnosticsJson] = db.paramsFor("j102_apply_transition");

  // The transition chosen from the STORED instrument kind, not from the caller.
  assert.equal(transition_id, "record-lease-execution");
  assert.ok(V5_J102_TRANSITION_IDS.includes(transition_id));

  // The compare-and-swap operand is the digest of the LOADED state.
  const cas = JSON.parse(casJson);
  assert.deepEqual(Object.keys(cas), ["deal:deal-synthetic-1"]);
  assert.equal(cas["deal:deal-synthetic-1"], digest(dealState()));

  // One subject envelope, carrying the SERVER's actor and instant, hashing to
  // its own claim, and naming the digest it was decided against.
  const subjects = JSON.parse(subjectsJson);
  assert.equal(subjects.length, 1);
  assert.equal(subjects[0].record_kind, "stored_lifecycle_subject");
  assert.equal(subjects[0].tenant, ORGANIZATION_TENANT_ID);
  assert.equal(subjects[0].record.updated_by, "joe");
  assert.equal(subjects[0].record.updated_at, SERVER_NOW);
  assert.equal(subjects[0].record.prior_state_digest, digest(dealState()));
  assert.equal(subjects[0].record.state.execution_state, "executed");
  assert.equal(subjects[0].record_digest, digest(subjects[0].record));
  // HIGH-5. The subject's own provenance is the transition that is actually being
  // applied — the same string the writer compares it against and refuses on. It
  // is the field ops.j102_subject reports as the row's provenance, so a store
  // that stamped anything else would be building a receipt-shaped lie the
  // database now refuses.
  assert.equal(subjects[0].record.established_by_transition, transition_id);
  assert.equal(subjects[0].record.schema_version, V5_J102_STORED_SUBJECT_SCHEMA_VERSION);
  assert.equal(subjects[0].record.tenant, ORGANIZATION_TENANT_ID);
  assert.deepEqual(Object.keys(subjects[0].record).sort(),
    [...admissionPolicy().stored_subject_record_keys].sort());

  // One event envelope, naming the transition and the evidence it rested on.
  const events = JSON.parse(eventsJson);
  assert.equal(events.length, 1);
  assert.equal(events[0].record.event.event_kind, "lease_executed");
  assert.equal(events[0].record.transition_id, "record-lease-execution");
  assert.equal(events[0].record.schema_version, V5_J102_STORED_EVENT_SCHEMA_VERSION);
  assert.equal(events[0].record.tenant, ORGANIZATION_TENANT_ID);
  assert.equal(events[0].record.event.schema_version, V5_J102_EVENT_SCHEMA_VERSION);
  assert.deepEqual(Object.keys(events[0].record).sort(),
    [...admissionPolicy().stored_event_record_keys].sort());
  // THE CANONICAL EVIDENCE REFERENCE, and it is exactly the triple the database
  // can independently rebuild from what its own readers returned: the kind, the
  // source, and the reference — for a document, F01's own document_id. The
  // writer compares this array element for element against
  // ops.j102_recheck_evidence's answer, so a fourth key would be a key the
  // comparison has to admit on a fact it already guarantees (every pin is proved
  // bound to the one primary subject, which the receipt names).
  assert.deepEqual(events[0].record.evidence_references, [{
    evidence_kind: "executed_lease", source: "f01_document", reference: "doc-synthetic-1",
  }]);
  // And the reference really is the DOCUMENT ID, which is what the SQL recheck
  // reads back off F01 — not a label the store chose beside it.
  assert.equal(events[0].record.evidence_references[0].reference,
    JSON.parse(recheckJson)[0].selector.document_id);
  assert.equal(events[0].record_digest, digest(events[0].record));

  // THE RECHECK MANIFEST, which is what lets the database re-read the exact pin
  // AND the exact binding under its own lock. A manifest carrying only the
  // document id would let a newer version satisfy it; one carrying no binding
  // would let somebody else's lease satisfy it.
  const recheck = JSON.parse(recheckJson);
  assert.deepEqual(recheck, [{
    evidence_kind: "executed_lease", source: "f01_document",
    reader: "ops.f01_read.document",
    selector: { document_id: "doc-synthetic-1" },
    expected_version_no: 1, expected_content_digest: D(1),
    binding: {
      evidence_source: "f01_document", evidence_ref: "doc-synthetic-1",
      version_no: 1, content_digest: D(1),
      subject_kind: "deal", subject_id: "deal-synthetic-1",
    },
    expected_link_digest: storedLink().link_digest,
  }]);

  assert.equal(key, "j102-fixture-1");
  assert.match(requestDigest, /^sha256:[0-9a-f]{64}$/);
  const diagnostics = JSON.parse(diagnosticsJson);
  assert.equal(diagnostics.operation, "record-deal-execution");
  assert.equal(diagnostics.reason_id, "lease_execution_marks_executed_lease");
  assert.deepEqual(diagnostics.coupled_facts, ["deal.execution_state"]);
});

test("the executed instrument is chosen from the stored deal, so a purchase cannot borrow lease semantics", async () => {
  const db = new FakeDb({
    subjects: { "deal:deal-synthetic-1": storedSubject(dealState({ instrument_kind: "purchase" })) },
    document: storedDocument({ document_class: "purchase_contract", validity_state: "draft" }),
    links: { [LEASE_LINK_KEY]: storedLink() },
  });
  const store = createCreLifecycleStore({ db });
  await store.recordDealExecution({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [documentRef("signed_purchase_contract")],
  }, ctx(JOE));
  const params = db.paramsFor("j102_apply_transition");
  assert.equal(params[0], "record-purchase-contract-execution");
  const subjects = JSON.parse(params[2]);
  // Q094: executed on the execution axis, pending on the business axis.
  assert.equal(subjects[0].record.state.execution_state, "executed");
  assert.equal(subjects[0].record.state.deal_state, "pending");
  assert.equal(subjects[0].record.state.diligence_state, "in_progress");
});

test("a declared axis selects exactly one transition and the others stay put", async () => {
  const db = new FakeDb({
    subjects: { "deal:deal-synthetic-1": storedSubject(dealState()) },
    facts: { payment: storedFact("payment") },
  });
  const store = createCreLifecycleStore({ db });
  await store.recordDealAxis({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [recordRef("payment_received")],
    declared: { axis: "payment_state", payment_level: "paid" },
  }, ctx(JOE));
  const params = db.paramsFor("j102_apply_transition");
  assert.equal(params[0], "record-payment");
  const state = JSON.parse(params[2])[0].record.state;
  assert.equal(state.payment_state, "paid");
  for (const other of V5_J102_DEAL_AXES.filter(a => a !== "payment_state")) {
    assert.equal(state[other], dealState()[other], `${other} must not move`);
  }
});

test("an unregistered declared axis determines no transition and nothing is written", async () => {
  const db = new FakeDb({
    subjects: { "deal:deal-synthetic-1": storedSubject(dealState()) },
    facts: { payment: storedFact("payment") },
  });
  const store = createCreLifecycleStore({ db });
  const answer = await store.recordDealAxis({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [recordRef("payment_received")],
    declared: { axis: "deal_state", payment_level: "paid" },
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "transition_not_determined");
  assert.equal(db.callsTo("j102_apply_transition").length, 0);
});

test("the declared axis selects a verb HERE and never enters the kernel's declared contract", () => {
  // The axis names which of the four orthogonal deal verbs is being asked for.
  // It is this module's dispatch input, not a fact about a deal, and the
  // kernel's `request.declared` is closed and does not carry it — so the store
  // forwards the domain half and drops the selector. This asks the kernel
  // DIRECTLY which keys it accepts, so the two halves cannot drift apart
  // without the suite naming the key that moved.
  const probe = declared => {
    try {
      evaluateLifecycleTransition({
        tenant: ORGANIZATION_TENANT_ID,
        transition_id: "record-payment",
        subject: dealState(),
        actor: { slug: "joe", human: true, authorization_class: "verified_partner",
          derived_by: "authenticated_handler_context" },
        declared,
        // Deliberately empty: the declared surface is validated before the
        // evidence array's length is, so the probe never has to build evidence
        // to learn whether a key got past that surface.
        evidence: [],
        now: SERVER_NOW,
      });
    } catch (error) { return error.code; }
    return "accepted";
  };
  for (const key of V5_J102_DECLARED_DOMAIN_FIELDS) {
    assert.notEqual(probe({ [key]: "synthetic" }), "unknown_field",
      `${key} is forwarded verbatim, so the kernel must accept it`);
  }
  assert.deepEqual([...V5_J102_DECLARED_SELECTOR_FIELDS], ["axis"]);
  for (const key of V5_J102_DECLARED_SELECTOR_FIELDS) {
    assert.equal(probe({ [key]: "payment_state" }), "unknown_field",
      `${key} is a store selector; forwarding it would ask the kernel to widen a closed surface`);
  }
});

test("a commitment loads the related negotiation and binds BOTH compare-and-swap operands", async () => {
  const asg = assignmentState({ assignment_phase: "negotiation", open_negotiation_count: 3 });
  const neg = {
    subject_kind: "property_negotiation", subject_id: "neg-synthetic-1",
    assignment_id: "asg-synthetic-1", property_id: "prop-synthetic-1",
    negotiation_state: "loi_accepted",
  };
  const db = new FakeDb({
    subjects: {
      "assignment:asg-synthetic-1": storedSubject(asg),
      "property_negotiation:neg-synthetic-1": storedSubject(neg),
    },
    facts: { winning_property_commitment: storedFact("winning_property_commitment") },
  });
  const store = createCreLifecycleStore({ db });
  const answer = await store.commitWinningProperty({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "assignment", subject_id: "asg-synthetic-1" },
    related_refs: { property_negotiation: { subject_kind: "property_negotiation",
      subject_id: "neg-synthetic-1" } },
    evidence_refs: [recordRef("winner_selection_commitment")],
    declared: { instrument_kind: "lease", new_deal_id: "deal-synthetic-1" },
  }, ctx(JOE));
  assert.equal(answer.decision, "allow");

  const params = db.paramsFor("j102_apply_transition");
  // BLOCK-1. EVERY subject the decision touches is a compare-and-swap operand:
  // the ones that were READ, including the negotiation, and the one that is
  // CREATED. A prerequisite that moved invalidates the decision exactly as a
  // target that moved does, and a created id that is already taken must not be
  // upserted over.
  const cas = JSON.parse(params[1]);
  assert.deepEqual(Object.keys(cas).sort(),
    ["assignment:asg-synthetic-1", "deal:deal-synthetic-1",
      "property_negotiation:neg-synthetic-1"]);
  assert.equal(cas["property_negotiation:neg-synthetic-1"], digest(neg));
  // AN EXPLICIT null, present as a key. "This subject must be ABSENT" and "I
  // have no opinion about this subject" used to be the same bytes — no key at
  // all — and the writer could only read the second.
  assert.ok(Object.prototype.hasOwnProperty.call(cas, "deal:deal-synthetic-1"));
  assert.equal(cas["deal:deal-synthetic-1"], null);

  // Three subjects land together: the negotiation, the assignment and the NEW
  // pending deal. Q082's coupled facts, as parameters.
  const subjects = JSON.parse(params[2]);
  assert.deepEqual(subjects.map(e => e.record.subject_kind).sort(),
    ["assignment", "deal", "property_negotiation"]);
  const dealEnvelope = subjects.find(e => e.record.subject_kind === "deal");
  assert.equal(dealEnvelope.record.state.deal_state, "pending");
  assert.equal(dealEnvelope.record.state.execution_state, "unexecuted");
  // A newly created subject declares a null prior digest, and it is the SAME
  // null the operand carries — the writer refuses the pair if they disagree.
  assert.equal(dealEnvelope.record.prior_state_digest, null);
  assert.equal(JSON.parse(params[3]).length, 3, "three events accompany three facts");
});

test("BLOCK-1: a created subject id that is already taken refuses instead of overwriting", async () => {
  // THE REPRODUCER, at the store. deal-A exists, closed, with its closing date,
  // under its own assignment. A verified partner commits a winner on a DIFFERENT
  // assignment and names deal-A as the new deal id. Every kernel check passes,
  // because the kernel has no view of existing deals.
  const asg = assignmentState({ subject_id: "asg-synthetic-2",
    assignment_phase: "negotiation", open_negotiation_count: 1 });
  const neg = {
    subject_kind: "property_negotiation", subject_id: "neg-synthetic-2",
    assignment_id: "asg-synthetic-2", property_id: "prop-synthetic-2",
    negotiation_state: "loi_accepted",
  };
  const existing = dealState({ subject_id: "deal-synthetic-1", deal_state: "closed",
    execution_state: "executed", closing_state: "closed", closing_date: T.late });
  const db = new FakeDb({
    subjects: {
      "assignment:asg-synthetic-2": storedSubject(asg),
      "property_negotiation:neg-synthetic-2": storedSubject(neg),
      "deal:deal-synthetic-1": storedSubject(existing),
    },
    facts: { winning_property_commitment: storedFact("winning_property_commitment",
      { subject_kind: "assignment", subject_id: "asg-synthetic-2" }) },
  });
  const store = createCreLifecycleStore({ db });
  const answer = await store.commitWinningProperty({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "assignment", subject_id: "asg-synthetic-2" },
    related_refs: { property_negotiation: { subject_kind: "property_negotiation",
      subject_id: "neg-synthetic-2" } },
    evidence_refs: [recordRef("winner_selection_commitment")],
    declared: { instrument_kind: "lease", new_deal_id: "deal-synthetic-1" },
  }, ctx(JOE));

  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "created_subject_id_already_exists");
  assert.equal(answer.subject_id, "deal-synthetic-1");
  assert.equal(answer.overwrote_existing_subject, false);
  assert.equal(answer.records_written, 0);
  assert.equal(db.callsTo("j102_apply_transition").length, 0,
    "the writer is never reached, so the closed deal cannot be replaced");
});

test("BLOCK-1: a valid new creation still lands, and its operand is an explicit null", async () => {
  const asg = assignmentState({ assignment_phase: "negotiation", open_negotiation_count: 1 });
  const neg = {
    subject_kind: "property_negotiation", subject_id: "neg-synthetic-1",
    assignment_id: "asg-synthetic-1", property_id: "prop-synthetic-1",
    negotiation_state: "loi_accepted",
  };
  const db = new FakeDb({
    subjects: {
      "assignment:asg-synthetic-1": storedSubject(asg),
      "property_negotiation:neg-synthetic-1": storedSubject(neg),
    },
    facts: { winning_property_commitment: storedFact("winning_property_commitment") },
  });
  const store = createCreLifecycleStore({ db });
  const answer = await store.commitWinningProperty({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "assignment", subject_id: "asg-synthetic-1" },
    related_refs: { property_negotiation: { subject_kind: "property_negotiation",
      subject_id: "neg-synthetic-1" } },
    evidence_refs: [recordRef("winner_selection_commitment")],
    declared: { instrument_kind: "lease", new_deal_id: "deal-synthetic-fresh" },
  }, ctx(JOE));
  assert.equal(answer.decision, "allow");
  const cas = JSON.parse(db.paramsFor("j102_apply_transition")[1]);
  assert.equal(cas["deal:deal-synthetic-fresh"], null);
  // The absence was CHECKED rather than assumed: the store asked for the id it
  // was about to create.
  assert.ok(db.callsTo("j102_subject").some(c => c.params[1] === "deal-synthetic-fresh"));
});

// --- initialization: the creation door -------------------------------------

const engagementState = (over = {}) => ({
  subject_kind: "engagement", subject_id: "eng-synthetic-1",
  relationship_id: "rel-synthetic-1", engagement_state: "active",
  representation_basis: "signed_engagement_letter",
  effective_from: null, effective_to: null, ...over,
});
const clientState = (over = {}) =>
  relationshipState({ relationship_state: "client", active_engagement_count: 1, ...over });

/** A database holding the client and the active engagement an assignment needs. */
const engagedDb = (extra = {}) => new FakeDb({
  subjects: {
    "relationship:rel-synthetic-1": storedSubject(clientState()),
    "engagement:eng-synthetic-1": storedSubject(engagementState()),
  },
  ...extra,
});

const assignmentInit = (over = {}) => ({
  idempotency_key: "j102-fixture-init-1",
  declared: { new_subject_id: "asg-synthetic-9" },
  related_refs: {
    relationship: { subject_kind: "relationship", subject_id: "rel-synthetic-1" },
    engagement: { subject_kind: "engagement", subject_id: "eng-synthetic-1" },
  },
  ...over,
});

test("an initialization claims its key BEFORE any state is read, and the statement order is exact", async () => {
  const db = engagedDb();
  const answer = await createCreLifecycleStore({ db }).initializeAssignment(
    assignmentInit(), ctx(JOE));
  assert.equal(answer.decision, "allow");
  assert.deepEqual(db.sequence, [
    "BEGIN",
    // The actor and the instant, derived from the server.
    "f01_principal",
    // REPLAY FIRST. A settled key must return its stored result even though the
    // world has moved, exactly as it must for a transition.
    "j102_replay_outcome",
    // The parents, then the id being claimed.
    "j102_subject", "j102_subject", "j102_subject",
    // And the one writer. Note what is NOT here: no evidence reader of any kind,
    // because a creation rests on none.
    "j102_initialize_subject",
    "COMMIT",
  ]);
  assert.equal(db.callsTo("j102_apply_transition").length, 0,
    "a creation never reaches the transition writer");
  assert.equal(db.callsTo("f01_read").length + db.callsTo("f01_stored_artifact").length, 0);
});

test("the created subject carries an EXPLICIT NULL operand and every parent carries its digest", async () => {
  const db = engagedDb();
  await createCreLifecycleStore({ db }).initializeAssignment(assignmentInit(), ctx(JOE));
  const params = db.paramsFor("j102_initialize_subject");
  assert.equal(params[0], "initialize-assignment");
  const operands = JSON.parse(params[1]);
  // The created key is present and its operand is null — "this subject must be
  // ABSENT" — while the parents carry the digests the decision was taken against.
  assert.ok(Object.prototype.hasOwnProperty.call(operands, "assignment:asg-synthetic-9"));
  assert.equal(operands["assignment:asg-synthetic-9"], null);
  assert.equal(operands["engagement:eng-synthetic-1"], digest(engagementState()));
  assert.equal(operands["relationship:rel-synthetic-1"], digest(clientState()));

  const envelope = JSON.parse(params[2]);
  assert.equal(envelope.record_kind, "stored_lifecycle_subject");
  assert.equal(envelope.record.prior_state_digest, null, "a creation has no prior state");
  assert.equal(envelope.record.established_by_transition, "initialize-assignment",
    "the row's provenance is the initialization, never a transition");
  assert.equal(envelope.record.updated_by, "joe");
  assert.equal(envelope.record.updated_at, SERVER_NOW, "the instant is the server's");
  // THE CREATED STATE IS THE EARLIEST ONE OF ITS KIND, and the store composed
  // none of it: the kernel did.
  assert.deepEqual(envelope.record.state, assertLifecycleSubject({
    subject_kind: "assignment", subject_id: "asg-synthetic-9",
    engagement_id: "eng-synthetic-1", assignment_phase: "research",
    open_negotiation_count: 0, selected_property_id: null,
    active_lease_draft_target_id: null, pending_deal_id: null,
    multi_target_exception_ref: null,
  }));

  // THE HISTORY CITES NOTHING, and that is a positive statement rather than an
  // omission: no evidence in this rail can bind to a subject that does not exist.
  const event = JSON.parse(params[3]);
  assert.equal(event.record_kind, "stored_lifecycle_event");
  assert.deepEqual(event.record.evidence_references, []);
  assert.equal(event.record.transition_id, "initialize-assignment");
  assert.equal(event.record.event.event_kind, "assignment_initialized");
  assert.equal(event.record.event.assignment_phase, "research");
});

test("the receipt names the parents this creation actually consulted, not a bare boolean", async () => {
  const db = engagedDb();
  const answer = await createCreLifecycleStore({ db }).initializeAssignment(
    assignmentInit(), ctx(JOE));
  assert.equal(answer.decision, "allow");
  // BOTH HALVES. The booleans are the stable field a caller compares across
  // kinds; the consulted list is what makes them checkable, and here it is the
  // two rows the client gate actually rests on.
  assert.equal(answer.required_context_enforced, true);
  assert.equal(answer.parent_subjects_locked_and_unmoved, true);
  assert.deepEqual(answer.context_subjects_consulted,
    ["engagement:eng-synthetic-1", "relationship:rel-synthetic-1"]);
  assert.equal(answer.context_subjects_consulted_count, 2);
  // And the kernel agrees about which kinds those were, from its own answer
  // rather than from the receipt.
  const evaluated = evaluateLifecycleInitialization({
    tenant: ORGANIZATION_TENANT_ID,
    initialization_id: "initialize-assignment",
    related: { engagement: engagementState(), relationship: clientState() },
    declared: { new_subject_id: "asg-synthetic-9" },
    actor: { slug: "joe", human: true, authorization_class: "verified_partner",
      derived_by: "authenticated_handler_context" },
    now: SERVER_NOW,
  });
  assert.deepEqual(evaluated.context_verified, ["engagement", "relationship"]);
  assert.deepEqual(
    answer.context_subjects_consulted.map(key => key.split(":")[0]),
    evaluated.context_verified);
});

test("an id that is already taken refuses, and nothing is written", async () => {
  const db = engagedDb({
    subjects: {
      "relationship:rel-synthetic-1": storedSubject(clientState()),
      "engagement:eng-synthetic-1": storedSubject(engagementState()),
      "assignment:asg-synthetic-9": storedSubject(assignmentState({
        subject_id: "asg-synthetic-9", assignment_phase: "committed",
      })),
    },
  });
  const answer = await createCreLifecycleStore({ db }).initializeAssignment(
    assignmentInit(), ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "subject_already_exists");
  assert.equal(answer.overwrote_existing_subject, false);
  assert.equal(answer.records_written, 0);
  assert.equal(db.callsTo("j102_initialize_subject").length, 0);
});

test("a parent that is absent, stale, or refused by the kernel stops the creation", async () => {
  // Absent.
  const absent = new FakeDb({ subjects: {} });
  const missing = await createCreLifecycleStore({ db: absent }).initializeAssignment(
    assignmentInit(), ctx(JOE));
  assert.equal(missing.reason_id, "related_subject_not_found");
  assert.equal(absent.callsTo("j102_initialize_subject").length, 0);

  // Stale: the caller decided against a version the database no longer holds.
  const stale = engagedDb();
  const moved = await createCreLifecycleStore({ db: stale }).initializeAssignment(
    assignmentInit({
      related_refs: {
        relationship: { subject_kind: "relationship", subject_id: "rel-synthetic-1" },
        engagement: { subject_kind: "engagement", subject_id: "eng-synthetic-1",
          expected_state_digest: D(9) },
      },
    }), ctx(JOE));
  assert.equal(moved.reason_id, "stale_related_subject_digest");
  assert.equal(stale.callsTo("j102_initialize_subject").length, 0);

  // Q077's client gate, refused by the KERNEL and reported with its reason: an
  // engagement that has lapsed, and a relationship that never became a client.
  for (const [subjects, reason_id] of [
    [{ "relationship:rel-synthetic-1": storedSubject(clientState()),
      "engagement:eng-synthetic-1": storedSubject(engagementState({
        engagement_state: "expired" })) }, "engagement_not_active"],
    [{ "relationship:rel-synthetic-1": storedSubject(relationshipState()),
      "engagement:eng-synthetic-1": storedSubject(engagementState()) },
    "client_status_required"],
    [{ "relationship:rel-synthetic-1": storedSubject(clientState()),
      "engagement:eng-synthetic-1": storedSubject(engagementState({
        relationship_id: "rel-synthetic-other" })) }, "relationship_not_in_verified_chain"],
  ]) {
    const db = new FakeDb({ subjects });
    const answer = await createCreLifecycleStore({ db }).initializeAssignment(
      assignmentInit(), ctx(JOE));
    assert.equal(answer.decision, "refuse", reason_id);
    assert.equal(answer.reason_id, reason_id);
    assert.equal(answer.records_written, 0);
    assert.equal(db.callsTo("j102_initialize_subject").length, 0);
  }
});

test("the initialization payload is closed: no evidence, no subject reference, no state", async () => {
  const db = engagedDb();
  const store = createCreLifecycleStore({ db });
  for (const extra of [
    { evidence_refs: [recordRef("search_initiation")] },
    { subject_ref: { subject_kind: "assignment", subject_id: "asg-synthetic-9" } },
  ]) {
    await assert.rejects(() => store.initializeAssignment(assignmentInit(extra), ctx(JOE)),
      e => e instanceof V5J102StoreError && e.code === "unknown_field");
  }
  // A lifecycle axis is refused by name, as a derived field, wherever it is put.
  for (const field of ["assignment_phase", "relationship_state", "deal_state"]) {
    await assert.rejects(
      () => store.initializeAssignment(assignmentInit({ [field]: "committed" }), ctx(JOE)),
      e => e instanceof V5J102StoreError && e.code === "caller_derived_field_refused",
      `${field} must be refused on an initialization too`);
  }
  // And inside `declared`, which is the closed vocabulary of IDENTIFIERS: a state
  // axis there is refused as the derived field it is, and an unregistered
  // identifier as an unknown one.
  await assert.rejects(() => store.initializeAssignment(assignmentInit({
    declared: { new_subject_id: "asg-synthetic-9", assignment_phase: "search" },
  }), ctx(JOE)),
  e => e instanceof V5J102StoreError && e.code === "caller_derived_field_refused");
  await assert.rejects(() => store.initializeAssignment(assignmentInit({
    declared: { new_subject_id: "asg-synthetic-9", mandate_scope: "search" },
  }), ctx(JOE)), e => e instanceof V5J102StoreError && e.code === "unknown_field");
  assert.equal(db.calls.length, 0, "no closed-schema refusal reaches the database");
});

test("the parent chain must be named in full, and a related subject nothing reads refuses", async () => {
  const db = engagedDb();
  const store = createCreLifecycleStore({ db });
  // The engagement an assignment runs under is not optional; omitting it would be
  // omitting the prerequisite rather than the reference.
  await assert.rejects(() => store.initializeAssignment(assignmentInit({
    related_refs: { relationship: { subject_kind: "relationship", subject_id: "rel-synthetic-1" } },
  }), ctx(JOE)), e => e instanceof V5J102StoreError && e.code === "missing_field");
  // A prospect creation reads no parent at all, so naming one is a caller that
  // has misunderstood which row it is creating.
  await assert.rejects(() => store.initializeProspectRelationship({
    idempotency_key: "j102-fixture-init-2",
    declared: { new_subject_id: "rel-synthetic-2" },
    related_refs: { engagement: { subject_kind: "engagement", subject_id: "eng-synthetic-1" } },
  }, ctx(JOE)), e => e instanceof V5J102StoreError && e.code === "unexpected_related_subject");
  assert.equal(db.calls.length, 0);
});

test("OWNER RULING (c): the row and event carry the sponsoring partner, and a disagreeing database sponsor refuses", async () => {
  const AGENT_PRINCIPAL = { actor_slug: "codex", human: false, authorization_class: "sponsored_agent" };
  const db = new FakeDb({ subjects: {}, principal: AGENT_PRINCIPAL });
  const made = await createCreLifecycleStore({ db }).initializeProspectRelationship({
    idempotency_key: "j102-fixture-sponsor-1", declared: { new_subject_id: "rel-synthetic-7" },
  }, ctx(AGENT));
  assert.equal(made.decision, "allow");
  const [, , subjectJson, eventJson] = db.paramsFor("j102_initialize_subject");
  assert.equal(JSON.parse(subjectJson).record.sponsoring_partner, "joe");
  assert.equal(JSON.parse(subjectJson).record.updated_by, "codex");
  assert.equal(JSON.parse(eventJson).record.sponsoring_partner, "joe");

  const disagreeing = new FakeDb({ subjects: {}, principal: AGENT_PRINCIPAL, sponsoring_partner: "dell" });
  await assert.rejects(createCreLifecycleStore({ db: disagreeing }).initializeProspectRelationship({
    idempotency_key: "j102-fixture-sponsor-2", declared: { new_subject_id: "rel-synthetic-8" },
  }, ctx(AGENT)), e => e instanceof V5J102StoreError && e.code === "sponsor_context_mismatch");
  assert.equal(disagreeing.callsTo("j102_initialize_subject").length, 0, "refused before any write");

  await assert.rejects(createCreLifecycleStore({ db: new FakeDb({ subjects: {} }) })
    .initializeProspectRelationship({ idempotency_key: "j102-fixture-sponsor-3",
      sponsoring_partner: "dell", declared: { new_subject_id: "rel-synthetic-9" } }, ctx(JOE)),
  e => e instanceof V5J102StoreError &&
    ["caller_derived_field_refused", "unknown_field"].includes(e.code));

  await assert.rejects(createCreLifecycleStore({ db: new FakeDb({ subjects: {} }) })
    .initializeProspectRelationship({ idempotency_key: "j102-fixture-sponsor-4",
      declared: { new_subject_id: "rel-synthetic-10" } },
    ctx({ slug: "codex", display: "Codex", human: false, via: "oauth-google" })),
  e => e instanceof V5J102StoreError);
});

test("a prospect and a negotiation are created by their own operations, with their own parents", async () => {
  // AS A SPONSORED AGENT throughout, because creating an empty prospect, a
  // negotiation draft or an assignment shell carries no evidence-bound fact and
  // the class that may ADVANCE each of them is the class that may create it. The
  // database's own principal says so too, or the write refuses before it starts.
  const AGENT_PRINCIPAL = {
    actor_slug: "codex", human: false, authorization_class: "sponsored_agent",
  };
  // The prospect: no parent, no evidence, and it is NOT a client.
  const first = new FakeDb({ subjects: {}, principal: AGENT_PRINCIPAL });
  const prospect = await createCreLifecycleStore({ db: first })
    .initializeProspectRelationship({
      idempotency_key: "j102-fixture-init-3",
      declared: { new_subject_id: "rel-synthetic-2" },
    }, ctx(AGENT));
  assert.equal(prospect.decision, "allow");
  assert.equal(prospect.created_subject_kind, "relationship");
  assert.equal(prospect.advances_lifecycle_state, false);
  assert.equal(prospect.transition_applied, false);
  assert.equal(prospect.transition_prerequisites_bypassed, false);
  // THE PARENTLESS RECEIPT SAYS SO. `required_context_enforced` and
  // `parent_subjects_locked_and_unmoved` are unconditional trues in the writer,
  // and on a prospect they describe the EMPTY SET — the consulted list is what
  // stops the pair reading as a chain that was walked.
  assert.deepEqual(prospect.context_subjects_consulted, [],
    "a prospect creation consulted no parent, and the receipt reports which");
  assert.equal(prospect.context_subjects_consulted_count, 0);
  const created = JSON.parse(first.paramsFor("j102_initialize_subject")[2]);
  assert.equal(created.record.state.relationship_state, "prospect");
  assert.equal(created.record.state.active_engagement_count, 0);

  // The negotiation: under an OPEN assignment, as a draft.
  const second = new FakeDb({
    subjects: { "assignment:asg-synthetic-1": storedSubject(assignmentState()) },
    principal: AGENT_PRINCIPAL,
  });
  const draft = await createCreLifecycleStore({ db: second }).initializePropertyNegotiation({
    idempotency_key: "j102-fixture-init-4",
    declared: { new_subject_id: "neg-synthetic-2", property_id: "prop-synthetic-2" },
    related_refs: { assignment: { subject_kind: "assignment", subject_id: "asg-synthetic-1" } },
  }, ctx(AGENT));
  assert.equal(draft.decision, "allow");
  const draftEnvelope = JSON.parse(second.paramsFor("j102_initialize_subject")[2]);
  assert.equal(draftEnvelope.record.state.negotiation_state, "loi_drafted");
  assert.equal(draftEnvelope.record.state.property_id, "prop-synthetic-2");
  assert.equal(draftEnvelope.record.state.assignment_id, "asg-synthetic-1");

  // Q095's bound, through the store: a committed assignment takes no new draft.
  const third = new FakeDb({
    subjects: { "assignment:asg-synthetic-1": storedSubject(assignmentState({
      assignment_phase: "committed", selected_property_id: "prop-synthetic-1",
      pending_deal_id: "deal-synthetic-1" })) },
    principal: AGENT_PRINCIPAL,
  });
  const refused = await createCreLifecycleStore({ db: third }).initializePropertyNegotiation({
    idempotency_key: "j102-fixture-init-5",
    declared: { new_subject_id: "neg-synthetic-3", property_id: "prop-synthetic-3" },
    related_refs: { assignment: { subject_kind: "assignment", subject_id: "asg-synthetic-1" } },
  }, ctx(AGENT));
  assert.equal(refused.reason_id, "assignment_already_committed");
  assert.equal(third.callsTo("j102_initialize_subject").length, 0);
});

test("a failing creation rolls back, and a replayed one reports what LANDED", async () => {
  const failing = engagedDb({ writeError: new Error("synthetic writer failure") });
  await assert.rejects(
    () => createCreLifecycleStore({ db: failing }).initializeAssignment(
      assignmentInit(), ctx(JOE)),
    error => error.message === "synthetic writer failure");
  assert.equal(failing.rolledBack, 1);
  assert.equal(failing.committed, 0);

  const replayed = engagedDb({
    replay_outcome: {
      operation: "initialize-assignment", decision: "allow", outcome: "initialized",
      actor_slug: "joe", initialization_id: "initialize-assignment",
      created_subject_kind: "assignment", created_subject_id: "asg-synthetic-9",
      caller_reported_reason_id: "assignment_initialized_under_active_engagement",
      subject_digests: { "assignment:asg-synthetic-9": D(5) },
      event_digests: [D(6)], creation_shape_enforced: true,
      required_context_enforced: true, parent_subjects_locked_and_unmoved: true,
      subject_created: true, committed_at: SERVER_NOW,
    },
  });
  const answer = await createCreLifecycleStore({ db: replayed }).initializeAssignment(
    assignmentInit(), ctx(JOE));
  assert.equal(answer.reason_id, "assignment_initialized_under_active_engagement");
  assert.equal(answer.created_subject_id, "asg-synthetic-9");
  assert.equal(answer.subject_created, true);
  // A REPLAY READS NO STATE AND WRITES NOTHING. The world may have moved; the
  // stored outcome is what happened.
  assert.deepEqual(replayed.sequence, ["BEGIN", "f01_principal", "j102_replay_outcome", "COMMIT"]);
});

// --- refusals that never reach a write -------------------------------------

test("a stale subject digest the store cannot characterize files a VISIBLE reconciliation item and moves nothing", async () => {
  // Q103. The row names no prior digest equal to the caller's base, so the
  // movement is uncharacterized: it is not a merge, it is not a silent
  // refusal either, it is a conflict a person can see.
  const db = leaseExecutionDb({ read_bodies: {
    subject: subjectReadback(dealState(), { established_by_transition: "commit-winning-property",
      prior_state_digest: null }),
    subject_events: [],
  } });
  const store = createCreLifecycleStore({ db });
  const answer = await store.recordDealExecution({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1",
      expected_state_digest: D(99) },
    evidence_refs: [documentRef("executed_lease")],
  }, ctx(JOE));
  assert.equal(answer.decision, "reconcile");
  assert.equal(answer.reason_id, "concurrent_change_not_characterized");
  assert.equal(answer.current_version_digest, digest(dealState()));
  assert.equal(answer.base_version_digest, D(99));
  assert.equal(answer.auto_merged, false);
  assert.equal(answer.last_writer_wins, false);
  assert.equal(answer.advances_lifecycle_state, false);
  assert.equal(db.callsTo("j102_apply_transition").length, 0);
  assert.equal(db.callsTo("j102_record_reconciliation_item").length, 1);
  assert.equal(db.callsTo("f01_read").length, 0, "evidence is not even loaded");
});

test("a document pin that does not match the readback refuses, and names which pin moved", async () => {
  const movedVersion = leaseExecutionDb({ document: storedDocument({
    document_class: "lease",
    neon_identity: { document_id: "doc-synthetic-1", content_digest: D(1), version_no: 4 },
  }) });
  const store1 = createCreLifecycleStore({ db: movedVersion });
  const versionAnswer = await store1.recordDealExecution({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [documentRef("executed_lease")],
  }, ctx(JOE));
  assert.equal(versionAnswer.decision, "refuse");
  assert.equal(versionAnswer.missing_fact, "f01_document_version_moved");
  assert.equal(movedVersion.callsTo("j102_apply_transition").length, 0);

  const movedBytes = leaseExecutionDb({ document: storedDocument({
    document_class: "lease",
    neon_identity: { document_id: "doc-synthetic-1", content_digest: D(8), version_no: 1 },
  }) });
  const store2 = createCreLifecycleStore({ db: movedBytes });
  const digestAnswer = await store2.recordDealExecution({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [documentRef("executed_lease")],
  }, ctx(JOE));
  assert.equal(digestAnswer.missing_fact, "f01_document_content_digest_mismatch");
  assert.equal(movedBytes.callsTo("j102_apply_transition").length, 0);
});

test("an absent evidence reader fails closed with the missing fact named and no reader called", async () => {
  const db = new FakeDb({
    subjects: { "relationship:rel-synthetic-1": storedSubject(relationshipState()) },
  });
  const store = createCreLifecycleStore({ db });
  const answer = await store.recordRepresentationAgreement({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "relationship", subject_id: "rel-synthetic-1" },
    evidence_refs: [{ evidence_kind: "approved_representation_equivalent",
      approval_ref: "appr-synthetic-1" }],
    declared: { new_subject_id: "eng-synthetic-1" },
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "required_evidence_unavailable");
  assert.equal(answer.missing_fact, "authenticated_representation_equivalence_approval");
  assert.equal(answer.produced_by, "not_produced_by_this_slice");
  assert.equal(answer.fabricated_authority, false);
  assert.ok(answer.missing_fact_reason.length > 0);
  // No approval reader is called and no write is attempted: the refusal is a
  // policy answer, not a database error.
  assert.equal(db.callsTo("j102_typed_approval").length, 0);
  assert.equal(db.callsTo("j102_apply_transition").length, 0);
  // And the ETL path through the SAME operation still works, so the fail-closed
  // branch is narrow rather than a blanket outage.
  const working = new FakeDb({
    subjects: { "relationship:rel-synthetic-1": storedSubject(relationshipState()) },
    document: storedDocument({ document_class: "engagement_letter" }),
    links: { [ETL_LINK_KEY]: storedLink({ subject_kind: "relationship",
      subject_id: "rel-synthetic-1" }) },
  });
  const ok = await createCreLifecycleStore({ db: working }).recordRepresentationAgreement({
    idempotency_key: "j102-fixture-2",
    subject_ref: { subject_kind: "relationship", subject_id: "rel-synthetic-1" },
    evidence_refs: [documentRef("signed_engagement_letter")],
    declared: { new_subject_id: "eng-synthetic-1" },
  }, ctx(JOE));
  assert.equal(ok.decision, "allow");
});

test("a first-party record that does not exist refuses and names who would produce it", async () => {
  const db = new FakeDb({
    subjects: { "deal:deal-synthetic-1": storedSubject(dealState({ execution_state: "executed" })) },
  });
  const store = createCreLifecycleStore({ db });
  const answer = await store.recordDealClosing({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [recordRef("final_closing_settlement")],
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.missing_fact, "first_party_record_not_found");
  assert.equal(answer.produced_by, "j102_record_first_party_fact");
  assert.equal(db.callsTo("j102_apply_transition").length, 0);
});

test("a kernel refusal is reported with its reason and detail, and nothing is written", async () => {
  const db = new FakeDb({
    // Already executed: the transition's own prerequisite refuses.
    subjects: { "deal:deal-synthetic-1": storedSubject(dealState({ execution_state: "executed" })) },
    document: storedDocument({ document_class: "lease" }),
    links: { [LEASE_LINK_KEY]: storedLink() },
  });
  const store = createCreLifecycleStore({ db });
  const answer = await store.recordDealExecution({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [documentRef("executed_lease")],
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "prerequisite_not_met");
  assert.equal(answer.refusal_detail.unmet_axis, "execution_state");
  assert.equal(answer.refusal_detail.observed, "executed");
  assert.equal(db.callsTo("j102_apply_transition").length, 0);
  assert.equal(db.committed, 1, "a refusal is still a completed transaction, not a rollback");
});

test("the database's own actor must match the handler's, and a mismatch refuses before any write", async () => {
  const db = leaseExecutionDb({
    principal: { actor_slug: "dell", human: true, authorization_class: "verified_partner" },
  });
  const store = createCreLifecycleStore({ db });
  await assert.rejects(() => store.recordDealExecution({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [documentRef("executed_lease")],
  }, ctx(JOE)), e => e instanceof V5J102StoreError && e.code === "actor_context_mismatch");
  assert.equal(db.callsTo("j102_apply_transition").length, 0);
  assert.equal(db.rolledBack, 1, "the failed transaction rolls back");
});

test("a corrupt stored subject is refused rather than repaired", async () => {
  const corrupt = storedSubject(dealState());
  corrupt.state_digest = D(77);
  const db = leaseExecutionDb({ subjects: { "deal:deal-synthetic-1": corrupt } });
  const store = createCreLifecycleStore({ db });
  await assert.rejects(() => store.recordDealExecution({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [documentRef("executed_lease")],
  }, ctx(JOE)), e => e instanceof V5J102StoreError && e.code === "corrupt_stored_subject");
  assert.equal(db.rolledBack, 1);
});

// --- rollback and replay ---------------------------------------------------

test("a failing write rolls back, reports nothing as applied, and rethrows the original error", async () => {
  const failure = Object.assign(new Error("j102_stale_subject_digest"), { code: "40001" });
  const db = leaseExecutionDb({ writeError: failure });
  const store = createCreLifecycleStore({ db });
  await assert.rejects(() => store.recordDealExecution({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [documentRef("executed_lease")],
  }, ctx(JOE)), e => e === failure);
  assert.equal(db.began, 1);
  assert.equal(db.committed, 0, "a failed write never commits");
  assert.equal(db.rolledBack, 1);
  assert.equal(db.sequence.at(-1), "ROLLBACK");
});

test("a replay reports what LANDED, and re-evaluates nothing", async () => {
  const stored = {
    operation: "record-deal-execution", decision: "allow",
    caller_reported_reason_id: "lease_execution_marks_executed_lease",
    caller_reported_reason_id_scope:
      "kernel_result_diagnostic_asserted_by_the_caller_and_not_recomputed_here",
    actor_slug: "joe",
    transition_id: "record-lease-execution",
    subject_digests: { "deal:deal-synthetic-1": D(42) },
    event_digests: [D(43)], coupled_facts_committed: ["deal.execution_state"],
    coupled_facts_committed_source: "derived_from_the_admission_contract",
    decision_refs: ["Q072.D1", "Q078.D1", "Q080.D1"],
    decision_refs_source: "derived_from_the_admission_contract",
    subject_provenance_bound_to_transition: true,
    event_payloads_enforced: true,
    event_evidence_references_enforced: true,
    evidence_rechecked_under_lock: true, readback: { subjects: [] },
  };
  // The stored deal is now CLOSED — a fresh evaluation would refuse. The replay
  // must still return the outcome that was committed, which is the whole point.
  const db = leaseExecutionDb({
    replay_outcome: stored,
    subjects: { "deal:deal-synthetic-1": storedSubject(dealState({ deal_state: "closed" })) },
  });
  const store = createCreLifecycleStore({ db });
  const answer = await store.recordDealExecution({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [documentRef("executed_lease")],
  }, ctx(JOE));
  assert.equal(answer.decision, "allow");
  assert.equal(answer.reason_id, "lease_execution_marks_executed_lease");
  assert.equal(answer.transition_id, "record-lease-execution");
  assert.equal(answer.evidence_rechecked_under_lock, true);
  assert.equal(answer.partial_application, false);
  // M-2. WHICH OF THESE THE DATABASE VOUCHES FOR is carried back, not inferred:
  // the coupled facts and the decision refs are labelled as derived from the
  // admission contract, and the kernel's diagnostic reason is labelled as the
  // caller's own assertion. A reader of a replayed receipt sees the difference.
  assert.equal(answer.coupled_facts_committed_source, "derived_from_the_admission_contract");
  assert.equal(answer.decision_refs_source, "derived_from_the_admission_contract");
  assert.deepEqual(answer.decision_refs,
    v5J102TransitionContract("record-lease-execution").decision_refs);
  assert.equal(answer.caller_reported_reason_id, "lease_execution_marks_executed_lease");
  assert.equal(answer.caller_reported_reason_id_scope,
    "kernel_result_diagnostic_asserted_by_the_caller_and_not_recomputed_here");
  // HIGH-5/HIGH-6, reported: what the write actually enforced about the history.
  assert.equal(answer.subject_provenance_bound_to_transition, true);
  assert.equal(answer.event_payloads_enforced, true);
  assert.equal(answer.event_evidence_references_enforced, true);
  // No state was read and no write was attempted after the replay hit.
  assert.deepEqual(db.sequence, ["BEGIN", "f01_principal", "j102_replay_outcome", "COMMIT"]);
});

test("a stored outcome attributed to another actor is refused rather than replayed", async () => {
  const db = leaseExecutionDb({
    replay_outcome: { operation: "record-deal-execution", actor_slug: "dell",
      reason_id: "lease_execution_marks_executed_lease" },
  });
  const store = createCreLifecycleStore({ db });
  await assert.rejects(() => store.recordDealExecution({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [documentRef("executed_lease")],
  }, ctx(JOE)), e => e instanceof V5J102StoreError && e.code === "invalid_stored_outcome");
});

// --- the non-transition writers --------------------------------------------

const dealSubjectDb = (extra = {}) => new FakeDb({
  subjects: { "deal:deal-synthetic-1": storedSubject(dealState()) }, ...extra,
});

test("a first-party fact is appended with the server's actor, instant, binding and author class", async () => {
  const db = dealSubjectDb();
  const store = createCreLifecycleStore({ db });
  const answer = await store.recordLifecycleFact({
    idempotency_key: "j102-fixture-1",
    fact: { record_kind: "closing_settlement", record_id: "rec-synthetic-1",
      subject_kind: "deal", subject_id: "deal-synthetic-1",
      closing_date: T.late, supporting_document_id: "doc-synthetic-1" },
  }, ctx(JOE));
  assert.equal(answer.decision, "allow");
  assert.equal(answer.advances_lifecycle_state, false);
  const envelope = JSON.parse(db.paramsFor("j102_record_first_party_fact")[0]);
  assert.equal(envelope.record.recorded_by, "joe");
  assert.equal(envelope.record.recorded_at, SERVER_NOW);
  assert.equal(envelope.record.closing_date, T.late);
  // BLOCK-2: the record says which deal it is about, once, at authoring time.
  assert.equal(envelope.record.subject_kind, "deal");
  assert.equal(envelope.record.subject_id, "deal-synthetic-1");
  // H5: and who — in what capacity — wrote it, derived rather than supplied.
  assert.equal(envelope.record.recorded_by_authorization_class, "verified_partner");
  assert.equal(envelope.record.advances_lifecycle_state, false);
  assert.equal(envelope.record_digest, digest(envelope.record));

  // A record about a subject nobody holds is a fact waiting for whatever later
  // takes that id.
  const dangling = new FakeDb({ subjects: {} });
  const answer2 = await createCreLifecycleStore({ db: dangling }).recordLifecycleFact({
    idempotency_key: "j102-fixture-2",
    fact: { record_kind: "closing_settlement", record_id: "rec-synthetic-2",
      subject_kind: "deal", subject_id: "deal-nonexistent", closing_date: T.late },
  }, ctx(JOE));
  assert.equal(answer2.reason_id, "bound_subject_not_found");
  assert.equal(dangling.callsTo("j102_record_first_party_fact").length, 0);
});

test("H5: a sponsored agent cannot AUTHOR a partner-only business record", async () => {
  const db = dealSubjectDb();
  const store = createCreLifecycleStore({ db });
  for (const record_kind of ["closing_settlement", "winning_property_commitment",
    "deal_failure", "lifecycle_correction"]) {
    await assert.rejects(() => store.recordLifecycleFact({
      idempotency_key: `j102-fixture-${record_kind}`,
      fact: {
        record_kind, record_id: "rec-synthetic-1",
        subject_kind: record_kind === "winning_property_commitment" ? "assignment" : "deal",
        subject_id: record_kind === "winning_property_commitment"
          ? "asg-synthetic-1" : "deal-synthetic-1",
        closing_date: record_kind === "closing_settlement" ? T.late : undefined,
        reason: "synthetic fixture reason",
      },
    }, ctx(AGENT)),
    e => e instanceof V5J102StoreError && e.code === "partner_authored_record_kind_refused",
    `${record_kind} must not be agent-authored`);
  }
  assert.equal(db.calls.length, 0, "no partner-only authoring attempt reaches the database");

  // The agent-recordable kinds are untouched, so this is the four and not a
  // blanket suspicion of agents.
  const agentDb = dealSubjectDb({
    principal: { actor_slug: "codex", human: false, authorization_class: "sponsored_agent" },
  });
  const ok = await createCreLifecycleStore({ db: agentDb }).recordLifecycleFact({
    idempotency_key: "j102-fixture-invoice",
    fact: { record_kind: "invoice", record_id: "rec-synthetic-1",
      subject_kind: "deal", subject_id: "deal-synthetic-1" },
  }, ctx(AGENT));
  assert.equal(ok.decision, "allow");
  const envelope = JSON.parse(agentDb.paramsFor("j102_record_first_party_fact")[0]);
  // The class is DERIVED from the principal that wrote the row, so the record
  // itself carries the fact a later transition checks.
  assert.equal(envelope.record.recorded_by_authorization_class, "sponsored_agent");
});

test("M1: the typed fact fields are validated BEFORE the durable write", async () => {
  const store = createCreLifecycleStore({ db: dealSubjectDb() });
  const bad = (fact, code) => assert.rejects(
    () => store.recordLifecycleFact({ idempotency_key: "j102-fixture-1", fact }, ctx(JOE)),
    e => e instanceof V5J102StoreError && e.code === code, JSON.stringify(fact));
  const base = { record_id: "rec-synthetic-1", subject_kind: "deal",
    subject_id: "deal-synthetic-1" };

  // A non-string reason used to store durably and then make the record
  // UNREADABLE as evidence, at which point the kernel THREW instead of refusing.
  await bad({ ...base, record_kind: "deal_failure", reason: { text: "no" } }, "invalid_shape");
  await bad({ ...base, record_kind: "invoice", detail: 17 }, "invalid_shape");
  await bad({ ...base, record_kind: "closing_settlement", closing_date: "2026-02-31T00:00:00Z" },
    "invalid_timestamp");
  await bad({ ...base, record_kind: "closing_settlement", closing_date: "yesterday" },
    "invalid_timestamp");
  await bad({ ...base, record_kind: "invoice", supporting_document_id: "not a document id" },
    "invalid_identifier");
  // The two mandatory fields, taken from the evidence contracts rather than
  // restated: a dateless closing and a reasonless failure.
  await bad({ ...base, record_kind: "closing_settlement" }, "missing_field");
  await bad({ ...base, record_kind: "deal_failure" }, "missing_field");
  // And the binding must be the kind the record's own evidence contract names.
  await bad({ ...base, record_kind: "winning_property_commitment", subject_kind: "deal" },
    "first_party_record_subject_kind_mismatch");
});

test("a first-party record kind no evidence contract consumes is refused", async () => {
  const db = new FakeDb();
  const store = createCreLifecycleStore({ db });
  await assert.rejects(() => store.recordLifecycleFact({
    idempotency_key: "j102-fixture-1",
    fact: { record_kind: "freeform_note", record_id: "rec-synthetic-1",
      subject_kind: "deal", subject_id: "deal-synthetic-1" },
  }, ctx(JOE)), e => e instanceof V5J102StoreError && e.code === "unknown_first_party_record_kind");
  assert.equal(db.calls.length, 0);
});

test("BLOCK-2: an unbound document, and one bound to another subject, both refuse", async () => {
  // NO ASSOCIATION AT ALL. The document is authentic, current, fully executed and
  // pinned exactly; nothing says it is this deal's lease.
  const unbound = new FakeDb({
    subjects: { "deal:deal-synthetic-1": storedSubject(dealState()) },
    document: storedDocument({ document_class: "lease" }),
    links: {},
  });
  const answer = await createCreLifecycleStore({ db: unbound }).recordDealExecution({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [documentRef("executed_lease")],
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "required_evidence_unavailable");
  assert.equal(answer.missing_fact, "j102_evidence_subject_association");
  assert.equal(answer.produced_by, "j102_record_evidence_subject_link");
  assert.equal(unbound.callsTo("j102_apply_transition").length, 0);

  // ASSOCIATED WITH SOMEBODY ELSE'S DEAL. The reader is asked about THIS deal and
  // answers null, so a lease executed for one client cannot mark another's.
  const elsewhere = new FakeDb({
    subjects: { "deal:deal-synthetic-1": storedSubject(dealState()) },
    document: storedDocument({ document_class: "lease" }),
    links: {
      [["f01_document", "doc-synthetic-1", 1, D(1), "deal", "deal-other-client"].join("|")]:
        storedLink({ subject_id: "deal-other-client" }),
    },
  });
  const answer2 = await createCreLifecycleStore({ db: elsewhere }).recordDealExecution({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [documentRef("executed_lease")],
  }, ctx(JOE));
  assert.equal(answer2.missing_fact, "j102_evidence_subject_association");
  assert.equal(elsewhere.callsTo("j102_apply_transition").length, 0);
});

test("BLOCK-2: a stored record bound to a different deal cannot close this one", async () => {
  const db = new FakeDb({
    subjects: { "deal:deal-synthetic-1": storedSubject(dealState({ execution_state: "executed" })) },
    facts: { closing_settlement: storedFact("closing_settlement",
      { subject_kind: "deal", subject_id: "deal-somebody-else", closing_date: T.late }) },
  });
  const answer = await createCreLifecycleStore({ db }).recordDealClosing({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [recordRef("final_closing_settlement")],
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.missing_fact, "first_party_record_bound_to_a_different_subject");
  assert.equal(db.callsTo("j102_apply_transition").length, 0);

  // A record predating the binding is refused rather than read as binding to
  // whatever it is asked about.
  const legacy = storedFact("closing_settlement", { closing_date: T.late });
  delete legacy.record.subject_id;
  legacy.record_digest = digest(legacy.record);
  const old = new FakeDb({
    subjects: { "deal:deal-synthetic-1": storedSubject(dealState({ execution_state: "executed" })) },
    facts: { closing_settlement: legacy },
  });
  const answer2 = await createCreLifecycleStore({ db: old }).recordDealClosing({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [recordRef("final_closing_settlement")],
  }, ctx(JOE));
  assert.equal(answer2.missing_fact, "first_party_record_subject_binding");
});

test("M1: an unreadable stored field refuses as a policy answer, not as a thrown violation", async () => {
  const broken = storedFact("closing_settlement", { closing_date: T.late, reason: 17 });
  broken.record_digest = digest(broken.record);
  const db = new FakeDb({
    subjects: { "deal:deal-synthetic-1": storedSubject(dealState({ execution_state: "executed" })) },
    facts: { closing_settlement: broken },
  });
  const answer = await createCreLifecycleStore({ db }).recordDealClosing({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [recordRef("final_closing_settlement")],
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse", "a refusal, and not a V5J102Error out of the kernel");
  assert.equal(answer.missing_fact, "readable_first_party_record");
  assert.match(answer.missing_fact_reason, /reason/);
});

test("the association producer checks BOTH ends and is partner-only", async () => {
  const db = new FakeDb({
    subjects: { "deal:deal-synthetic-1": storedSubject(dealState()) },
    document: storedDocument({ document_class: "lease" }),
  });
  const store = createCreLifecycleStore({ db });
  const answer = await store.recordEvidenceSubjectLink({
    idempotency_key: "j102-fixture-1",
    link: { evidence_source: "f01_document", document_id: "doc-synthetic-1",
      expected_version_no: 1, expected_content_digest: D(1),
      subject_kind: "deal", subject_id: "deal-synthetic-1" },
  }, ctx(JOE));
  assert.equal(answer.decision, "allow");
  assert.equal(answer.advances_lifecycle_state, false);
  assert.equal(answer.creates_document, false);
  assert.equal(answer.asserts_document_state, false);
  const envelope = JSON.parse(db.paramsFor("j102_record_evidence_subject_link")[0]);
  assert.equal(envelope.record.evidence_ref, "doc-synthetic-1");
  assert.equal(envelope.record.version_no, 1);
  assert.equal(envelope.record.content_digest, D(1));
  assert.equal(envelope.record.associated_by, "joe");
  assert.equal(envelope.record.associated_at, SERVER_NOW);
  assert.equal(envelope.record_digest, digest(envelope.record));

  // A pin F01 does not hold cannot be associated with anything.
  const movedPin = new FakeDb({
    subjects: { "deal:deal-synthetic-1": storedSubject(dealState()) },
    document: storedDocument({ document_class: "lease",
      neon_identity: { document_id: "doc-synthetic-1", content_digest: D(1), version_no: 4 } }),
  });
  const answer2 = await createCreLifecycleStore({ db: movedPin }).recordEvidenceSubjectLink({
    idempotency_key: "j102-fixture-2",
    link: { evidence_source: "f01_document", document_id: "doc-synthetic-1",
      expected_version_no: 1, expected_content_digest: D(1),
      subject_kind: "deal", subject_id: "deal-synthetic-1" },
  }, ctx(JOE));
  assert.equal(answer2.reason_id, "evidence_pin_not_held");
  assert.equal(movedPin.callsTo("j102_record_evidence_subject_link").length, 0);

  // A subject this rail does not hold cannot be associated with anything either.
  const noSubject = new FakeDb({ subjects: {}, document: storedDocument() });
  const answer3 = await createCreLifecycleStore({ db: noSubject }).recordEvidenceSubjectLink({
    idempotency_key: "j102-fixture-3",
    link: { evidence_source: "f01_document", document_id: "doc-synthetic-1",
      expected_version_no: 1, expected_content_digest: D(1),
      subject_kind: "deal", subject_id: "deal-nonexistent" },
  }, ctx(JOE));
  assert.equal(answer3.reason_id, "subject_not_found");

  // And an agent may not say which transaction a document belongs to.
  await assert.rejects(() => store.recordEvidenceSubjectLink({
    idempotency_key: "j102-fixture-4",
    link: { evidence_source: "f01_document", document_id: "doc-synthetic-1",
      expected_version_no: 1, expected_content_digest: D(1),
      subject_kind: "deal", subject_id: "deal-synthetic-1" },
  }, ctx(AGENT)),
  e => e instanceof V5J102StoreError && e.code === "authority_only_operation_refused");
});

test("H1/H3: what remains unwired is two FACTS, not two callers, and each names its remedy", async () => {
  const named = V5_J102_UNWIRED_CAPABILITIES.map(c => c.capability).sort();
  // THE LIST CHANGED SHAPE BECAUSE THE CALLERS WERE BUILT. Q103's reconciliation
  // caller and its ownership read both exist now; what is left is two facts no
  // relation in this record layer holds, so no caller of any shape could report
  // them honestly. Each entry must name the exact minimal change.
  assert.deepEqual(named, [
    "active_automation_registry",
    "subject_ownership_authority",
  ].sort());
  for (const entry of V5_J102_UNWIRED_CAPABILITIES) {
    assert.ok(entry.exact_minimal_change.length > 0,
      `${entry.capability} names the change that would produce it`);
  }
  // AND THE TWO THAT LANDED ARE RECORDED WITH THEIR RESIDUALS, including the one
  // claim this module must not make: neither is registered at runtime.
  assert.deepEqual(
    V5_J102_WIRED_CONCURRENCY_CAPABILITIES.map(c => c.capability).sort(),
    ["ownership_and_freshness_exposure", "reconciliation_runtime_integration"]);
  for (const entry of V5_J102_WIRED_CONCURRENCY_CAPABILITIES) {
    assert.equal(entry.registered_at_runtime, false,
      "wired at source is not registered at runtime, and the registry says only the first");
    assert.ok(entry.residual.length > 0, `${entry.capability} states what it still does not do`);
    assert.ok(V5_J102_OPERATIONS.includes(entry.operation));
  }
  // AND NOTHING QUIETLY DROPPED OFF THE LIST. The three initialization entries
  // and the bootstrap entry are gone because operations exist for them, so the
  // wired registry must account for each subject kind they used to name.
  assert.deepEqual(
    V5_J102_WIRED_INITIALIZATION_CAPABILITIES.map(c => c.creates_subject_kind).sort(),
    ["assignment", "property_negotiation", "relationship"]);
  for (const entry of V5_J102_WIRED_INITIALIZATION_CAPABILITIES) {
    assert.ok(V5_J102_OPERATIONS.includes(entry.operation));
    assert.equal(entry.requires_evidence, false);
    assert.ok(entry.why.length > 0);
  }
  // And the registration surface says the same thing per operation, so a reader
  // of `capabilities()` learns where a subject of each kind comes from rather
  // than only that one is required.
  const schemas = v5J102StoreOperationSchemas();
  for (const entry of v5J102ToolRegistrations()) {
    if (!entry.write || schemas[entry.store_operation].transition === null) continue;
    assert.equal(entry.requires_existing_primary_subject, true,
      `${entry.name} advances a subject that must already exist`);
    assert.ok(V5_J102_OPERATIONS.includes(entry.primary_subject_created_by_operation),
      `${entry.name}'s primary ${entry.primary_subject_kind} is created by a named operation`);
  }
  for (const entry of V5_J102_UNWIRED_CAPABILITIES) {
    assert.equal(entry.produced_by, "not_produced_by_this_slice");
    assert.ok(entry.why.length > 0);
  }

  // AND THE REMAINING PREREQUISITE IS STILL REAL, asserted against behaviour
  // rather than against the list: a transition against a subject that has not
  // been initialized still refuses `subject_not_found` and creates nothing. The
  // initialization operations create; the transition operations never do.
  for (const [handler, subject_kind, subject_id] of [
    ["openCreAssignment", "assignment", "asg-nonexistent"],
    ["recordLoiSubmission", "property_negotiation", "neg-nonexistent"],
  ]) {
    const db = new FakeDb({ subjects: {} });
    const answer = await createCreLifecycleStore({ db })[handler]({
      idempotency_key: "j102-fixture-1",
      subject_ref: { subject_kind, subject_id },
      evidence_refs: [recordRef("search_initiation")],
      declared: { mandate_scope: "search" },
    }, ctx(JOE));
    assert.equal(answer.reason_id, "subject_not_found");
    assert.equal(answer.records_written, 0);
    assert.equal(db.callsTo("j102_apply_transition").length, 0);
  }

  // THE KERNEL IS STILL NOT RE-EXPORTED. The store CALLS evaluateConcurrentEdit
  // and projectOwnershipAndFreshness; it does not hand a caller a pure evaluator
  // to drive with facts of its own, which would be the caller-invented ownership
  // this whole read exists to refuse.
  const store = createCreLifecycleStore({ db: new FakeDb() });
  assert.equal(typeof store.evaluateConcurrentEdit, "undefined");
  assert.equal(typeof store.projectOwnershipAndFreshness, "undefined");
  assert.equal(typeof store.recordLifecycleReconciliation, "function");
  assert.ok(V5_J102_READ_KINDS.includes("ownership_and_freshness"));
  assert.deepEqual([...V5_J102_COMPOSED_READ_KINDS], ["ownership_and_freshness"]);
});

test("the owner questions: rulings are recorded as ruled, and the one still open behaves as if unanswered", () => {
  const byStatus = kind => V5_J102_OPEN_OWNER_QUESTIONS.filter(q => q.status === kind);
  assert.equal(V5_J102_OPEN_OWNER_QUESTIONS.length, 6);
  assert.equal(byStatus("unsettled_pending_owner_ruling").length, 1, "only mandate-before-LOI is open");
  assert.equal(byStatus("ruled_by_owner").length, 5);
  assert.equal(byStatus("implementation_assumption_live_and_unratified").length, 0);
  for (const ruled of byStatus("ruled_by_owner")) {
    assert.ok(ruled.ruling.length > 0 && ruled.ruled_on === "2026-09-25", "each ruling is quoted and dated");
    assert.equal(ruled.encoded_without_a_ruling, false);
  }
  for (const entry of V5_J102_OPEN_OWNER_QUESTIONS) {
    assert.ok(entry.question.length > 0);
    assert.ok(entry.today.length > 0, "each says what the rail does with no answer");
    assert.ok(entry.why_unsettled.length > 0, "and why the thirteen do not settle it");
  }
  // THE MANDATE-BEFORE-LOI QUESTION IS OPEN AND UNENCODED, and the behaviour is
  // the one an unanswered question leaves: a created assignment sits at
  // `research`, and `record-loi-submission` admits `research`. Encoding the
  // answer would mean dropping `research` from the negotiation's admitted parent
  // phases — this asserts it has NOT been dropped, so the test fails the day
  // somebody encodes a ruling nobody gave.
  const mandate = V5_J102_OPEN_OWNER_QUESTIONS.find(q => q.question.includes("mandate record"));
  assert.ok(mandate, "the mandate-before-LOI question is recorded");
  assert.equal(mandate.status, "unsettled_pending_owner_ruling");
  assert.equal(mandate.encoded_without_a_ruling, false);
  assert.deepEqual(
    v5J102InitializationContract("initialize-property-negotiation")
      .required_context[0].conditions[0].in,
    ["research", "search", "negotiation"],
    "a negotiation may still be drafted under a research assignment; the ordering is not encoded");
  assert.ok(v5J102TransitionContract("record-loi-submission")
    .coupled_facts.includes("assignment.assignment_phase"),
    "and the submission still moves the assignment itself, which is what makes the ordering matter");
  assert.equal(
    v5J102InitializationContract("initialize-assignment").initial_state.assignment_phase,
    "research",
    "the created assignment is at research, so open-assignment is not forced by the phase alone");
  // The actor-class parity is now the owner's ruling (c), not an assumption.
  const parity = V5_J102_OPEN_OWNER_QUESTIONS.find(q => q.question.includes("SPONSORED AGENT"));
  assert.equal(parity.status, "ruled_by_owner");
  assert.deepEqual(
    v5J102InitializationContract("initialize-prospect-relationship").permitted_actor_classes,
    ["verified_partner", "sponsored_agent"]);
});

test("a Salesforce reference keeps its own labels, requires a real link target, and sets no state", async () => {
  const db = new FakeDb({
    subjects: { "assignment:asg-synthetic-1": storedSubject(assignmentState()) },
  });
  const store = createCreLifecycleStore({ db });
  const answer = await store.linkSalesforceReference({
    idempotency_key: "j102-fixture-1",
    opportunity_id: "006SYNTHETIC001",
    opportunity_name: "Synthetic Medical Group - New Location",
    opportunity_phase: "Pending Deal",
    observed_at: T.mid,
    linked_subject_kind: "assignment", linked_subject_id: "asg-synthetic-1",
  }, ctx(JOE));
  assert.equal(answer.decision, "allow");
  assert.equal(answer.sets_lifecycle_state, false);
  assert.equal(answer.phase_label_is_doctorcre_state, false);
  const envelope = JSON.parse(db.paramsFor("j102_record_salesforce_reference")[0]);
  assert.equal(envelope.record.opportunity_phase, "Pending Deal");
  assert.equal(envelope.record.opportunity_name, "Synthetic Medical Group - New Location");
  assert.equal(envelope.record.phase_label_is_doctorcre_state, false);

  // A link to a subject nobody holds is a dangling reference wearing the shape
  // of provenance.
  const dangling = new FakeDb({ subjects: {} });
  const answer2 = await createCreLifecycleStore({ db: dangling }).linkSalesforceReference({
    idempotency_key: "j102-fixture-2",
    opportunity_id: "006SYNTHETIC002", opportunity_name: "Synthetic",
    opportunity_phase: "Prospecting", observed_at: T.mid,
    linked_subject_kind: "deal", linked_subject_id: "deal-nonexistent",
  }, ctx(JOE));
  assert.equal(answer2.reason_id, "link_target_not_found");
  assert.equal(dangling.callsTo("j102_record_salesforce_reference").length, 0);
});

test("a correction binds the prior state digest and refuses without a durable correction record", async () => {
  const withRecord = new FakeDb({
    subjects: { "deal:deal-synthetic-1": storedSubject(dealState()) },
    facts: { lifecycle_correction: storedFact("lifecycle_correction",
      { reason: "synthetic fixture: mis-keyed instrument kind" }) },
  });
  const store = createCreLifecycleStore({ db: withRecord });
  const answer = await store.recordLifecycleCorrection({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    correction_record_id: "rec-synthetic-1",
    corrected_fields: ["instrument_kind"],
    reason: "synthetic fixture: mis-keyed instrument kind",
  }, ctx(JOE));
  assert.equal(answer.decision, "allow");
  assert.equal(answer.append_only, true);
  assert.equal(answer.prior_state_preserved, true);
  assert.equal(answer.derived_from_assistant_text, false);
  const envelope = JSON.parse(withRecord.paramsFor("j102_record_correction")[0]);
  assert.equal(envelope.record.prior_state_digest, digest(dealState()));
  assert.equal(envelope.record.corrected_by, "joe");
  assert.equal(envelope.record.derived_from_assistant_text, false);

  // No durable record: an approval that lives only in a transcript is not one.
  const withoutRecord = new FakeDb({
    subjects: { "deal:deal-synthetic-1": storedSubject(dealState()) },
  });
  const answer2 = await createCreLifecycleStore({ db: withoutRecord }).recordLifecycleCorrection({
    idempotency_key: "j102-fixture-2",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    correction_record_id: "rec-nonexistent",
    corrected_fields: ["instrument_kind"],
    reason: "synthetic fixture reason",
  }, ctx(JOE));
  assert.equal(answer2.reason_id, "correction_record_not_found");
  assert.equal(answer2.missing_fact, "first_party_lifecycle_correction_record");
  assert.equal(withoutRecord.callsTo("j102_record_correction").length, 0);
});

// --- Q103: ownership, freshness and visible reconciliation ------------------
//
// THESE RUN THE REAL STORE PATH INTO THE REAL KERNEL. Nothing below hands the
// kernel a fact a caller made up: the state digest, the freshness pair and the
// current version all come out of scripted READBACKS, and the assertions are
// about what the store composed from them.

/** One verified event readback, as ops.j102_read('subject_events') returns it. */
const storedEvent = (over = {}) => {
  const record = {
    schema_version: V5_J102_STORED_EVENT_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    event: { schema_version: V5_J102_EVENT_SCHEMA_VERSION, event_kind: "assignment_opened",
      subject_kind: "assignment", subject_id: "asg-synthetic-1" },
    transition_id: "open-assignment",
    evidence_references: [{ evidence_kind: "search_initiation",
      source: "first_party_record", reference: "rec-synthetic-1" }],
    recorded_by: "joe", recorded_at: T.late, ...over,
  };
  return { record, record_digest: digest(record), integrity: "recomputed_from_committed_row" };
};

/** The shape ops.j102_read('subject') returns: the verified row plus provenance. */
const subjectReadback = (state, over = {}) => ({
  subject_kind: state.subject_kind, subject_id: state.subject_id,
  state, state_digest: digest(state),
  established_by_transition: "open-assignment",
  updated_by: "joe", updated_at: T.late,
  integrity: "recomputed_from_committed_row", ...over,
});

const ownershipDb = (extra = {}) => new FakeDb({
  read_bodies: {
    subject: subjectReadback(assignmentState()),
    subject_events: [storedEvent()],
  },
  ...extra,
});

const ownershipRead = { selector: { kind: "ownership_and_freshness",
  subject_kind: "assignment", subject_id: "asg-synthetic-1" } };

test("Q103: the ownership read projects freshness from the HISTORY and names what it cannot know", async () => {
  const db = ownershipDb();
  const answer = await createCreLifecycleStore({ db }).readCreLifecycle(ownershipRead, ctx(JOE));
  assert.equal(answer.decision, "allow");
  assert.equal(answer.reason_id, "ownership_and_freshness_projected_from_committed_rows");

  // IT IS COMPOSED FROM TWO EXISTING SQL READS, and never asks the database for a
  // read kind it does not have — which is why this landed without a SQL change.
  assert.deepEqual(db.sequence, ["BEGIN", "f01_principal", "j102_read", "j102_read", "COMMIT"]);
  assert.deepEqual(db.callsTo("j102_read").map(c => c.params[0]), ["subject", "subject_events"]);

  const projection = answer.readback;
  // FRESHNESS IS DERIVED, and from the append-only history rather than the row.
  assert.equal(projection.last_material_change_at, T.late);
  assert.equal(projection.last_material_change_by, "joe");
  assert.equal(projection.freshness_known, true);
  assert.equal(projection.freshness_age_seconds,
    Math.floor((Date.parse(SERVER_NOW) - Date.parse(T.late)) / 1000));
  // THE DIGEST IS THE STORED ONE, recomputed here from the state the database
  // returned rather than echoed from a caller.
  assert.equal(projection.state_digest, digest(assignmentState()));
  // AND THE TWO FACTS NOBODY HOLDS ARE UNKNOWN, not empty. "Nothing is running"
  // and "nobody asked" are different answers and the kernel makes the difference.
  assert.equal(projection.owner_slug, null);
  assert.equal(projection.owner_known, false);
  assert.equal(projection.active_automation, null);
  assert.equal(projection.active_automation_known, false);
  assert.deepEqual(projection.inferred_fields, []);
  // The read says WHERE each field came from, and names both missing facts from
  // the registry rather than restating them.
  assert.equal(answer.derived_from.state_digest, "ops.j102_read.subject");
  assert.equal(answer.derived_from.last_material_change, "ops.j102_read.subject_events");
  assert.equal(answer.derived_from.owner_slug, "not_produced_by_this_record_layer");
  assert.equal(answer.derived_from.active_automation, "not_produced_by_this_record_layer");
  assert.equal(answer.missing_facts.length, V5_J102_UNWIRED_CAPABILITIES.length);
  for (const missing of answer.missing_facts) {
    assert.ok(missing.exact_minimal_change.length > 0);
    assert.equal(missing.produced_by, "not_produced_by_this_slice");
  }
});

test("Q103: a subject with NO history reports freshness unknown rather than falling back to the row", async () => {
  const db = ownershipDb({ read_bodies: {
    subject: subjectReadback(assignmentState()),
    subject_events: [],
  } });
  const answer = await createCreLifecycleStore({ db }).readCreLifecycle(ownershipRead, ctx(JOE));
  assert.equal(answer.decision, "allow");
  // The row's own updated_at is RIGHT THERE and is deliberately not used: the
  // question is when the subject last materially changed, and only the history
  // answers that.
  assert.equal(answer.readback.last_material_change_at, null);
  assert.equal(answer.readback.last_material_change_by, null);
  assert.equal(answer.readback.freshness_known, false);
  assert.equal(answer.readback.freshness_age_seconds, null);
  assert.equal(answer.current_state_agrees_with_history, false);
  assert.equal(answer.derived_from.last_material_change,
    "no history rows exist for this subject");
});

test("Q103: a history row that disagrees with the current row refuses rather than picking one", async () => {
  // Every writer in this rail stamps the subject and its event from ONE instant
  // in ONE transaction, so a disagreement is a record layer that cannot say when
  // it last changed — and answering anyway is the confident wrong answer.
  for (const [drift, label] of [
    [{ recorded_by: "dell" }, "a different actor"],
    [{ recorded_at: T.mid }, "a different instant"],
  ]) {
    const db = ownershipDb({ read_bodies: {
      subject: subjectReadback(assignmentState()),
      subject_events: [storedEvent(drift)],
    } });
    const answer = await createCreLifecycleStore({ db }).readCreLifecycle(ownershipRead, ctx(JOE));
    assert.equal(answer.decision, "refuse", label);
    assert.equal(answer.reason_id, "subject_history_disagrees_with_current_state");
    assert.equal(answer.readback, null);
  }
});

test("Q103: the ownership read refuses a missing subject, a corrupt row and an unverified readback", async () => {
  const missing = new FakeDb({ read_bodies: { subject: null, subject_events: [] } });
  const gone = await createCreLifecycleStore({ db: missing })
    .readCreLifecycle(ownershipRead, ctx(JOE));
  assert.equal(gone.reason_id, "subject_not_found");
  assert.equal(gone.readback, null);
  // A row whose stored digest no longer describes its own bytes is refused, not
  // repaired and not reported as an ownership fact.
  const corrupt = new FakeDb({ read_bodies: {
    subject: subjectReadback(assignmentState(), { state_digest: D(9) }),
    subject_events: [storedEvent()],
  } });
  await assert.rejects(
    () => createCreLifecycleStore({ db: corrupt }).readCreLifecycle(ownershipRead, ctx(JOE)),
    e => e instanceof V5J102StoreError && e.code === "corrupt_stored_subject");
  // And a readback that does not claim to have been recomputed inside PostgreSQL
  // is not evidence of anything.
  const trusted = new FakeDb({ read_bodies: {
    subject: subjectReadback(assignmentState(), { integrity: "trusted" }),
    subject_events: [storedEvent()],
  } });
  await assert.rejects(
    () => createCreLifecycleStore({ db: trusted }).readCreLifecycle(ownershipRead, ctx(JOE)),
    e => e instanceof V5J102StoreError && e.code === "readback_not_recomputed");
});

test("Q103: ownership, freshness and automation cannot be supplied by the caller", async () => {
  const db = ownershipDb();
  const store = createCreLifecycleStore({ db });
  // The selector is closed, so there is nowhere to put them; and at the payload
  // level each is refused BY NAME.
  //
  // WHICH GUARD FIRES IS PART OF THE ASSERTION, and `owner_slug` is the one that
  // proves the point. It sits in the AUTHORITY-injection fragment list — the list
  // this slice imports from F01 rather than copying — so it is refused one guard
  // earlier than the rest, as an authority claim rather than as a derived value.
  // That is the stronger refusal and the earlier one, and an assertion that
  // demanded `caller_derived_field_refused` here would have been asking for the
  // guard to be weakened to satisfy it.
  for (const [field, code] of [
    ["owner_slug", "caller_authority_field_refused"],
    ["last_material_change_at", "caller_derived_field_refused"],
    ["last_material_change_by", "caller_derived_field_refused"],
    ["active_automation", "caller_derived_field_refused"],
    ["state_digest", "caller_derived_field_refused"],
  ]) {
    await assert.rejects(
      () => store.readCreLifecycle({ ...ownershipRead, [field]: "x" }, ctx(JOE)),
      e => e instanceof V5J102StoreError && e.code === code,
      `${field} must be refused on the ownership read as ${code}`);
  }
  assert.ok(V5_J102_AUTHORITY_INJECTION_FRAGMENTS.includes("owner_slug"),
    "and it is refused as authority because F01's own fragment list names it");
  await assert.rejects(() => store.readCreLifecycle({
    selector: { ...ownershipRead.selector, owner_slug: "joe" },
  }, ctx(JOE)), e => e instanceof V5J102StoreError &&
    e.code === "caller_authority_field_refused");
  // The subject is not optional for this kind: an ownership projection with no
  // subject is a question about nothing.
  await assert.rejects(() => store.readCreLifecycle({
    selector: { kind: "ownership_and_freshness" },
  }, ctx(JOE)), e => e instanceof V5J102StoreError && e.code === "missing_field");
  assert.equal(db.callsTo("j102_record_reconciliation_item").length, 0);
});

// --- Q103: the reconciliation writer ---------------------------------------

const reconcileDb = (extra = {}) => new FakeDb({
  read_bodies: {
    subject: subjectReadback(assignmentState()),
    subject_events: [storedEvent()],
    reconciliation_items: [],
  },
  ...extra,
});

const reconcilePayload = (over = {}) => ({
  idempotency_key: "j102-fixture-reconcile-1",
  subject_ref: { subject_kind: "assignment", subject_id: "asg-synthetic-1",
    expected_state_digest: D(7) },
  edits: [{ field: "assignment_phase", value_digest: D(3) }],
  ...over,
});

test("Q103: an UNMOVED subject reconciles nothing, writes nothing, and merges nothing", async () => {
  const db = reconcileDb();
  const answer = await createCreLifecycleStore({ db }).recordLifecycleReconciliation(
    reconcilePayload({
      subject_ref: { subject_kind: "assignment", subject_id: "asg-synthetic-1",
        // The version the caller decided against IS the one the database holds.
        expected_state_digest: digest(assignmentState()) },
    }), ctx(JOE));
  assert.equal(answer.decision, "allow");
  assert.equal(answer.reason_id, "no_concurrent_movement");
  assert.equal(answer.subject_moved, false);
  assert.equal(answer.records_written, 0);
  assert.equal(answer.reconciliation_item, null);
  assert.equal(answer.merged, false);
  assert.equal(answer.last_writer_wins, false);
  assert.equal(db.callsTo("j102_record_reconciliation_item").length, 0);
});

test("Q103: a MOVED subject writes ONE visible item carrying both versions and the history between", async () => {
  const db = reconcileDb();
  const answer = await createCreLifecycleStore({ db }).recordLifecycleReconciliation(
    reconcilePayload({
      edits: [
        { field: "assignment_phase", value_digest: D(3) },
        { field: "client_phone_number", value_digest: D(4) },
      ],
    }), ctx(JOE));
  assert.equal(answer.decision, "reconcile");
  assert.equal(answer.reason_id, "concurrent_change_not_characterized");
  assert.equal(answer.subject_moved, true);
  assert.equal(answer.merged, false);
  assert.equal(answer.records_written, 1);
  assert.equal(answer.resolved_by_machine, false);
  assert.equal(answer.advances_lifecycle_state, false);
  assert.equal(answer.item_seq, 1);
  // THE FIELD CLASS IS THE KERNEL'S, from its own registry. `assignment_phase` is
  // lifecycle and classified; `client_phone_number` is a customer-facing field
  // this module does not classify, and it is reported as unclassified rather than
  // guessed routine — which is the difference between reconciling and merging.
  // AND IT IS DERIVED FROM THE STORED ITEM, not from a fresh reading: the class
  // was hashed into the record when the kernel judged it, so a replay of this
  // call reports the same split.
  assert.deepEqual(answer.unclassified_fields, ["client_phone_number"]);
  assert.deepEqual(answer.material_incoming_fields, ["assignment_phase"]);
  assert.deepEqual(answer.incoming_fields, ["assignment_phase", "client_phone_number"]);

  const envelope = JSON.parse(db.paramsFor("j102_record_reconciliation_item")[0]);
  assert.equal(envelope.record_kind, "stored_reconciliation_item");
  const item = envelope.record;
  // THE IDENTITY THE RELATION REQUIRES AND THE KERNEL DOES NOT CARRY.
  assert.equal(item.subject_kind, "assignment");
  assert.equal(item.subject_id, "asg-synthetic-1");
  // THE TWO VERSIONS, the caller's from its own pin and the current from the row.
  assert.equal(item.base_version_digest, D(7));
  assert.equal(item.current_version_digest, digest(assignmentState()));
  // THE INCOMING EDITS, STAMPED. The caller said which field and what the value
  // hashes to; who and when are the derived actor and the server clock.
  assert.deepEqual(item.incoming_edits.map(e => e.field),
    ["assignment_phase", "client_phone_number"]);
  for (const edit of item.incoming_edits) {
    assert.equal(edit.edited_by, "joe");
    assert.equal(edit.edited_at, SERVER_NOW);
  }
  assert.equal(item.incoming_edits[0].field_class, "lifecycle",
    "and the class is derived by the kernel, not supplied");
  // THE OTHER SIDE, as far as this layer can honestly show it: not an invented
  // edit list, but the committed state and the history that produced it.
  assert.deepEqual(item.concurrent_edits, []);
  assert.equal(item.concurrent_change_evidence.characterized, false);
  assert.deepEqual(item.concurrent_change_evidence.current_state, assignmentState());
  assert.equal(item.concurrent_change_evidence.current_state_source, "ops.j102_read.subject");
  assert.equal(item.concurrent_change_evidence.history_tail.length, 1);
  assert.equal(item.concurrent_change_evidence.history_tail[0].transition_id, "open-assignment");
  assert.equal(item.concurrent_change_evidence.history_tail_is_complete, false);
  assert.ok(item.concurrent_change_evidence.why.includes("prior_state_digest"),
    "and it names the exact reader gap that stops it being characterized");
  // THE THREE PROPERTIES A REVIEWER NEEDS, which the relation also CHECK-binds.
  assert.equal(item.visible, true);
  assert.equal(item.applied, false);
  assert.equal(item.resolved_by_machine, false);
  assert.equal(item.proposed_by, "joe");
  assert.equal(envelope.record_digest, digest(item));
  // THE OPERAND IS BOUND AND SENT, so the writer can re-read the subject under
  // its own lock and refuse a row that moved between this read and that write.
  const operands = JSON.parse(db.paramsFor("j102_record_reconciliation_item")[1]);
  assert.deepEqual(operands,
    { "assignment:asg-synthetic-1": digest(assignmentState()) });
  assert.equal(answer.current_version_bound_to_committed_row, true);
  assert.equal(answer.state_evidence_bound_to_committed_row, true);
  assert.equal(answer.history_evidence_bound_to_committed_history, true);
  // AND THE PATH IS GOVERNED LIKE EVERY SIBLING: the key is claimed before any
  // state is read, and the receipt distinguishes the caller's intent digest from
  // what the database recomputed.
  assert.deepEqual(db.sequence, ["BEGIN", "f01_principal", "j102_replay_outcome",
    "j102_read", "j102_read", "j102_record_reconciliation_item", "COMMIT"]);
  assert.equal(answer.request_digest_scope,
    "caller_supplied_intent_digest_not_recomputed_here");
  assert.equal(answer.committed_content_digest_source, "recomputed_from_committed_rows");
  assert.equal(answer.caller_reported_reason_id, "concurrent_change_not_characterized");
  assert.equal(answer.concurrent_change_characterized, false);
  assert.equal(answer.distinct_proposals_collapsed, false);
});

test("Q103: the CURRENT version comes from the stored row, and the edit's author cannot be supplied", async () => {
  const db = reconcileDb();
  const store = createCreLifecycleStore({ db });
  for (const field of ["current_version_digest", "base_version_digest", "conflict_kind",
    "incoming_edits", "concurrent_edits", "proposed_by", "resolved_by_machine"]) {
    await assert.rejects(
      () => store.recordLifecycleReconciliation(reconcilePayload({ [field]: "x" }), ctx(JOE)),
      e => e instanceof V5J102StoreError && e.code === "caller_derived_field_refused",
      `${field} must be refused as a derived field`);
  }
  // And inside an edit: who made it, when, and — the one the kernel removed —
  // what class it is.
  for (const field of ["edited_by", "edited_at", "field_class"]) {
    await assert.rejects(() => store.recordLifecycleReconciliation(reconcilePayload({
      edits: [{ field: "assignment_phase", value_digest: D(3), [field]: "x" }],
    }), ctx(JOE)), e => e instanceof V5J102StoreError && e.code === "caller_derived_field_refused",
    `${field} must be refused inside an edit`);
  }
  assert.equal(db.calls.length, 0, "and no closed-schema refusal reaches the database");
});

test("Q103: the base version is required, and a subject that does not exist refuses", async () => {
  const db = reconcileDb();
  const store = createCreLifecycleStore({ db });
  // A concurrent-edit question with no version to have decided against is not a
  // question; defaulting it to the stored digest would answer "no conflict" to
  // every caller that forgot to say.
  await assert.rejects(() => store.recordLifecycleReconciliation(reconcilePayload({
    subject_ref: { subject_kind: "assignment", subject_id: "asg-synthetic-1" },
  }), ctx(JOE)), e => e instanceof V5J102StoreError && e.code === "missing_field");
  await assert.rejects(() => store.recordLifecycleReconciliation(reconcilePayload({ edits: [] }),
    ctx(JOE)), e => e instanceof V5J102StoreError && e.code === "invalid_shape");

  const empty = new FakeDb({ read_bodies: { subject: null } });
  const answer = await createCreLifecycleStore({ db: empty })
    .recordLifecycleReconciliation(reconcilePayload(), ctx(JOE));
  assert.equal(answer.reason_id, "subject_not_found");
  assert.equal(answer.records_written, 0);
  assert.equal(empty.callsTo("j102_record_reconciliation_item").length, 0);
});

test("Q103: a RETRY replays what landed, and reads no state to decide it", async () => {
  // The stored outcome the writer settled under this key. A replay must report
  // the conflict that WAS filed — including the versions it was filed about —
  // even though the subject has moved on since.
  const settled = {
    operation: "record-lifecycle-reconciliation", decision: "reconcile",
    outcome: "recorded", actor_slug: "joe",
    subject_kind: "assignment", subject_id: "asg-synthetic-1",
    item_seq: 4, item_digest: D(5),
    conflict_kind: "uncharacterized_concurrent_change",
    base_version_digest: D(7), current_version_digest: D(6),
    conflict_present: true, visible: true, applied: false, resolved_by_machine: false,
    current_version_bound_to_committed_row: true,
    state_evidence_bound_to_committed_row: true,
    history_evidence_bound_to_committed_history: true,
    caller_reported_reason_id: "concurrent_change_not_characterized",
    committed_at: T.late,
    readback: { item_seq: 4, record: { incoming_edits: [
      { field: "assignment_phase", value_digest: D(3), field_class: "lifecycle",
        edited_by: "joe", edited_at: T.late }] } },
  };
  const db = reconcileDb({ replay_outcome: settled });
  const answer = await createCreLifecycleStore({ db })
    .recordLifecycleReconciliation(reconcilePayload(), ctx(JOE));
  assert.equal(answer.decision, "reconcile");
  assert.equal(answer.reason_id, "concurrent_change_not_characterized");
  assert.equal(answer.item_seq, 4);
  assert.equal(answer.item_digest, D(5));
  // The versions are the ones that were FILED, not the ones a fresh reading of
  // the subject would produce now.
  assert.equal(answer.current_version_digest, D(6));
  assert.deepEqual(answer.material_incoming_fields, ["assignment_phase"]);
  // A REPLAY READS NO STATE AND WRITES NOTHING.
  assert.deepEqual(db.sequence, ["BEGIN", "f01_principal", "j102_replay_outcome", "COMMIT"]);
  assert.equal(db.callsTo("j102_record_reconciliation_item").length, 0);
  assert.equal(db.callsTo("j102_read").length, 0);
});

test("Q103: a stored outcome attributed to another actor is not replayed to this one", async () => {
  const db = reconcileDb({ replay_outcome: {
    operation: "record-lifecycle-reconciliation", decision: "reconcile",
    actor_slug: "dell", caller_reported_reason_id: "concurrent_change_not_characterized",
  } });
  await assert.rejects(
    () => createCreLifecycleStore({ db }).recordLifecycleReconciliation(
      reconcilePayload(), ctx(JOE)),
    e => e instanceof V5J102StoreError && e.code === "invalid_stored_outcome");
});

test("Q103: the same key over DIFFERENT bytes refuses, and the store does not swallow it", async () => {
  // The refusal itself is the database's: ops.j102_claim_idempotency raises
  // j102_idempotency_payload_mismatch inside the writer, which is where it can be
  // atomic. What this proves is that the store propagates it rather than turning
  // a substituted conflict into a success — the SQL fixture drives the raise.
  const db = reconcileDb({
    writeError: Object.assign(new Error(
      "j102_idempotency_payload_mismatch: key j102-fixture-reconcile-1 already binds a different payload"),
    { code: "23505" }),
  });
  await assert.rejects(
    () => createCreLifecycleStore({ db }).recordLifecycleReconciliation(
      reconcilePayload({ edits: [{ field: "payment_state", value_digest: D(4) }] }), ctx(JOE)),
    error => /j102_idempotency_payload_mismatch/.test(error.message));
  assert.equal(db.rolledBack, 1);
  assert.equal(db.committed, 0);
});

test("Q103: DISTINCT proposals against the same two versions both land, and are not collapsed", async () => {
  // Two callers — or one caller twice — proposing DIFFERENT edits against the
  // same base and the same current have raised TWO real conflicts. The earlier
  // read-before-write check collapsed them on the version pair, which is the
  // wrong answer and was not atomic either; idempotency is keyed on the REQUEST,
  // which covers the edits, so both are visible.
  const db = reconcileDb();
  const store = createCreLifecycleStore({ db });
  const first = await store.recordLifecycleReconciliation(reconcilePayload({
    idempotency_key: "j102-fixture-reconcile-a",
    edits: [{ field: "assignment_phase", value_digest: D(3) }],
  }), ctx(JOE));
  const second = await store.recordLifecycleReconciliation(reconcilePayload({
    idempotency_key: "j102-fixture-reconcile-b",
    edits: [{ field: "assignment_phase", value_digest: D(4) }],
  }), ctx(JOE));

  assert.equal(first.records_written, 1);
  assert.equal(second.records_written, 1);
  assert.equal(first.distinct_proposals_collapsed, false);
  assert.equal(second.distinct_proposals_collapsed, false);
  const writes = db.callsTo("j102_record_reconciliation_item");
  assert.equal(writes.length, 2, "both proposals reached the writer");
  const [a, b] = writes.map(call => JSON.parse(call.params[0]));
  assert.notEqual(a.record_digest, b.record_digest,
    "and they are different items, because the edits differ");
  assert.equal(a.record.base_version_digest, b.record.base_version_digest);
  assert.equal(a.record.current_version_digest, b.record.current_version_digest);
  // NOTHING IS READ TO DECIDE THIS. The store no longer inspects existing items
  // at all, so there is no read-before-write claim left to be wrong about.
  assert.equal(db.callsTo("j102_read").filter(c => c.params[0] === "reconciliation_items").length,
    0, "the store makes no duplicate-suppression readback");
  // The two idempotency keys are bound to two different request digests.
  assert.notEqual(writes[0].params[3], writes[1].params[3]);
});

test("Q103: an unauthenticated or inadmissible actor cannot raise a conflict at all", async () => {
  const db = reconcileDb();
  const store = createCreLifecycleStore({ db });
  await assert.rejects(
    () => store.recordLifecycleReconciliation(reconcilePayload(), ctx({ slug: "nobody", human: true })),
    e => e instanceof V5J102StoreError && e.code === "unauthenticated_actor");
  await assert.rejects(
    () => store.recordLifecycleReconciliation(reconcilePayload(),
      ctx({ slug: "codex", human: false, probe: true })),
    e => e instanceof V5J102StoreError && e.code === "actor_class_not_admitted_for_lifecycle");
  assert.equal(db.calls.length, 0, "no authority failure reaches the database");

  // A SPONSORED AGENT MAY RAISE ONE. Noticing that two writers disagree is not an
  // exercise of authority, and the item resolves nothing.
  const agentDb = reconcileDb({
    principal: { actor_slug: "codex", human: false, authorization_class: "sponsored_agent" },
  });
  const raised = await createCreLifecycleStore({ db: agentDb })
    .recordLifecycleReconciliation(reconcilePayload(), ctx(AGENT));
  assert.equal(raised.decision, "reconcile");
  assert.equal(raised.records_written, 1);
  assert.equal(
    JSON.parse(agentDb.paramsFor("j102_record_reconciliation_item")[0]).record.proposed_by,
    "codex");

  // AND THE DATABASE'S OWN ACTOR MUST BE THE HANDLER'S, on this path too.
  const mismatched = reconcileDb({
    principal: { actor_slug: "dell", human: true, authorization_class: "verified_partner" },
  });
  await assert.rejects(
    () => createCreLifecycleStore({ db: mismatched })
      .recordLifecycleReconciliation(reconcilePayload(), ctx(JOE)),
    e => e instanceof V5J102StoreError && e.code === "actor_context_mismatch");
});

test("Q103: a failing reconciliation write rolls back and reports nothing as landed", async () => {
  const db = reconcileDb({ writeError: new Error("synthetic reconciliation failure") });
  await assert.rejects(
    () => createCreLifecycleStore({ db }).recordLifecycleReconciliation(
      reconcilePayload(), ctx(JOE)),
    error => error.message === "synthetic reconciliation failure");
  assert.equal(db.rolledBack, 1);
  assert.equal(db.committed, 0);
});

// --- reads -----------------------------------------------------------------

test("a read names its kind, recomputes integrity, and refuses an unregistered kind", async () => {
  const db = new FakeDb({ read_body: { kind: "subject", body: { state: dealState() } } });
  const store = createCreLifecycleStore({ db });
  const answer = await store.readCreLifecycle({
    selector: { kind: "subject", subject_kind: "deal", subject_id: "deal-synthetic-1" },
  }, ctx(JOE));
  assert.equal(answer.decision, "allow");
  assert.equal(answer.integrity, "recomputed_not_trusted");
  assert.equal(answer.stale_fallback_permitted, false);
  const [kind, selectorJson] = db.paramsFor("j102_read");
  assert.equal(kind, "subject");
  assert.deepEqual(JSON.parse(selectorJson),
    { subject_kind: "deal", subject_id: "deal-synthetic-1" });

  await assert.rejects(() => store.readCreLifecycle({ selector: { kind: "everything" } }, ctx(JOE)),
    e => e instanceof V5J102StoreError && e.code === "unknown_read_kind");
});

test("every result states that nothing left the record layer", async () => {
  const db = leaseExecutionDb();
  const store = createCreLifecycleStore({ db });
  const answer = await store.recordDealExecution({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [documentRef("executed_lease")],
  }, ctx(JOE));
  assert.equal(answer.schema_version, V5_J102_STORE_SCHEMA_VERSION);
  assert.equal(answer.tenant, ORGANIZATION_TENANT_ID);
  assert.equal(answer.provider_calls, 0);
  assert.equal(answer.salesforce_calls, 0);
  assert.equal(answer.documents_sent, 0);
  assert.equal(answer.creates_or_activates_tour, false);
  assert.equal(answer.effects.creates_effect, false);
  assert.equal(answer.effects.network_calls, 0);
});

test("the store requires an injected handle and opens no connection of its own", () => {
  assert.throws(() => createCreLifecycleStore({}),
    e => e instanceof V5J102StoreError && e.code === "database_handle_required");
  assert.throws(() => createCreLifecycleStore({ db: { notQuery: true } }),
    e => e instanceof V5J102StoreError && e.code === "database_handle_required");
});

// ---------------------------------------------------------------------------
// THE SQL ADMISSION PARITY SUITE.
//
// ops.j102_apply_transition is granted to carr_writer as well as carr_authority,
// and carr_writer always resolves to a sponsored agent. Everything the kernel and
// this store decide about WHO may perform a transition, WHICH subjects and fields
// it may write and WHICH evidence it requires is therefore a control on this
// caller and on no other, so ops/cre-lifecycle.candidate.sql carries the same
// contracts in ops.j102_admission_policy().
//
// TWO COPIES WITH NOTHING BETWEEN THEM ARE A FUTURE CONTRADICTION. These tests
// are the thing between them: they read the map out of the candidate SQL as data
// and assert it EQUALS the kernel's exports, contract by contract and field by
// field, and then assert that the writer actually calls the checks the map exists
// to feed. A map that is correct and unused would pass the first half and fail
// the second.
//
// NOTHING HERE EXECUTES SQL. These assertions are about the reviewed source's
// bytes; the live suite and the db-gate execute it as migration 0704.
// ---------------------------------------------------------------------------

const CANDIDATE_SQL = readFileSync(
  new URL("../../ops/cre-lifecycle.candidate.sql", import.meta.url), "utf8");

test("migration 0704 IS the reviewed candidate, byte for byte", () => {
  // Everything this suite proves about the candidate is only a proof about what
  // runs if the numbered migration is the same bytes. A hand edit to either one
  // alone fails here.
  const migration = readFileSync(
    new URL("../../migrations/0704_cre_lifecycle.sql", import.meta.url), "utf8");
  assert.equal(migration, CANDIDATE_SQL);
  assert.match(CANDIDATE_SQL, /NUMBERED AS migrations\/0704_cre_lifecycle\.sql, byte for byte/);
});

/** The admission map, read out of the candidate SQL's dollar-quoted JSON. */
function admissionPolicy() {
  const open = CANDIDATE_SQL.indexOf("$policy$");
  assert.ok(open > 0, "the candidate SQL carries a $policy$-quoted admission map");
  const close = CANDIDATE_SQL.indexOf("$policy$", open + "$policy$".length);
  assert.ok(close > open, "the admission map is closed by its own dollar quote");
  assert.equal(CANDIDATE_SQL.indexOf("$policy$", close + "$policy$".length), -1,
    "there is exactly ONE admission map in the file, so there is one thing to check");
  return JSON.parse(CANDIDATE_SQL.slice(open + "$policy$".length, close));
}

/**
 * One chunk of SQL with its COMMENTS AND STRING LITERALS removed, leaving the
 * text that actually executes.
 *
 * WHY THIS EXISTS. A test that asks "does this writer call that one" cannot be a
 * search of the source, because a writer legitimately NAMES another one inside
 * the refusal it raises — and a search would then either fail on correct code or
 * be satisfied by rewording the refusal, which is a control weakened to quiet a
 * test. Stripping first makes the question the one that was meant: is the call
 * in the code, rather than in something the code prints.
 *
 * IT IS DELIBERATELY SMALL AND CONSERVATIVE. It understands the two things this
 * file uses — `--` line comments and single-quoted literals with `''` escapes —
 * and the caller controls it in both directions: that real calls survive it, and
 * that real code is not eaten by it. The one shape it cannot see through is
 * dynamic SQL, so the caller asserts separately that the body builds none.
 */
function executableSql(source) {
  let out = "";
  let i = 0;
  while (i < source.length) {
    if (source[i] === "'") {
      i += 1;
      while (i < source.length) {
        if (source[i] === "'") {
          // A doubled quote is an escaped quote INSIDE the literal, not its end.
          if (source[i + 1] === "'") { i += 2; continue; }
          i += 1;
          break;
        }
        i += 1;
      }
      out += " ";
      continue;
    }
    if (source[i] === "-" && source[i + 1] === "-") {
      while (i < source.length && source[i] !== "\n") i += 1;
      out += " ";
      continue;
    }
    if (source[i] === "/" && source[i + 1] === "*") {
      const close = source.indexOf("*/", i + 2);
      i = close === -1 ? source.length : close + 2;
      out += " ";
      continue;
    }
    out += source[i];
    i += 1;
  }
  return out;
}

/**
 * One chunk of SQL with its COMMENTS removed and its literals LEFT IN PLACE.
 *
 * executableSql() above answers "does this code call that function", and to do it
 * safely it also removes the literals — which makes it the wrong tool for the
 * other question this file asks: "does this code compare THAT key against THAT
 * value". A jsonb key is a single-quoted literal, so stripping literals erases
 * the very thing under test, and asserting against the raw body instead would let
 * a comment satisfy the check.
 */
function sqlWithoutComments(source) {
  let out = "";
  let i = 0;
  while (i < source.length) {
    // A quoted literal is copied WHOLE, so a `--` inside one is not a comment.
    if (source[i] === "'") {
      out += source[i];
      i += 1;
      while (i < source.length) {
        out += source[i];
        if (source[i] === "'") {
          if (source[i + 1] === "'") { out += source[i + 1]; i += 2; continue; }
          i += 1;
          break;
        }
        i += 1;
      }
      continue;
    }
    if (source[i] === "-" && source[i + 1] === "-") {
      while (i < source.length && source[i] !== "\n") i += 1;
      out += " ";
      continue;
    }
    if (source[i] === "/" && source[i + 1] === "*") {
      const close = source.indexOf("*/", i + 2);
      i = close === -1 ? source.length : close + 2;
      out += " ";
      continue;
    }
    out += source[i];
    i += 1;
  }
  return out;
}

/** Which transitions each write operation may perform, from the store's own tables. */
function operationsForTransition() {
  const schemas = v5J102StoreOperationSchemas();
  const byTransition = new Map(V5_J102_TRANSITION_IDS.map(id => [id, []]));
  for (const operation of V5_J102_OPERATIONS) {
    const declared = schemas[operation].transition;
    if (declared === null) continue;
    const resolved = declared === "dispatch_on_instrument_kind"
      ? Object.values(V5_J102_INSTRUMENT_TRANSITIONS)
      : declared === "dispatch_on_declared_axis"
        ? Object.values(V5_J102_AXIS_TRANSITIONS)
        : [declared];
    for (const id of new Set(resolved)) {
      assert.ok(byTransition.has(id), `${operation} dispatches to a real transition (${id})`);
      byTransition.get(id).push(operation);
    }
  }
  return byTransition;
}

test("SQL parity: the admission map's transitions ARE the kernel's transition contracts", () => {
  const policy = admissionPolicy();
  assert.equal(policy.invented_business_policy, false);
  assert.deepEqual(Object.keys(policy.transitions).sort(), [...V5_J102_TRANSITION_IDS]);

  const operations = operationsForTransition();
  for (const id of V5_J102_TRANSITION_IDS) {
    const contract = v5J102TransitionContract(id);
    const admitted = policy.transitions[id];
    assert.equal(admitted.subject_kind, contract.subject_kind, `${id} subject_kind`);
    assert.deepEqual(admitted.permitted_actor_classes, contract.permitted_actor_classes,
      `${id} permitted_actor_classes`);
    assert.deepEqual(admitted.prerequisites, contract.prerequisites, `${id} prerequisites`);
    assert.deepEqual(admitted.instrument_kinds, contract.instrument_kinds,
      `${id} instrument_kinds`);
    assert.deepEqual(admitted.required_evidence_alternatives,
      contract.required_evidence_alternatives, `${id} required evidence`);
    assert.deepEqual(admitted.coupled_facts, contract.coupled_facts, `${id} coupled_facts`);
    assert.equal(admitted.creates_deal, contract.creates_deal, `${id} creates_deal`);
    // The operation pairing, from the store's own schemas and dispatch tables
    // rather than restated here, so the SQL map cannot pair an operation with a
    // transition this module would never route to it.
    assert.deepEqual([...admitted.operations].sort(), operations.get(id).sort(),
      `${id} is performed by exactly the operations this store routes to it`);
    assert.ok(admitted.operations.length > 0, `${id} is reachable from some operation`);
    // M-2. The receipt now DERIVES coupled_facts_committed and decision_refs
    // from this map instead of echoing the caller's diagnostics, so both have to
    // be the kernel's own, transition by transition, rather than only the one
    // that was already checked.
    assert.deepEqual(admitted.decision_refs, contract.decision_refs, `${id} decision_refs`);
    // M-1. requires_active_engagement, for EVERY transition rather than for the
    // one that happens to carry it. The flag is on the kernel's contract; the SQL
    // spends it as `required_context`, so the two must agree in BOTH directions
    // — a second transition gaining the flag with no context entry, or a context
    // entry appearing on a transition the kernel does not hold to an engagement,
    // are the same defect from opposite sides.
    assert.equal(admitted.requires_active_engagement, contract.requires_active_engagement,
      `${id} requires_active_engagement`);
    const engagementContext = (admitted.required_context ?? []).some(entry =>
      entry.subject === "engagement" &&
      entry.conditions.some(condition =>
        condition.field === "engagement_state" && condition.equals === "active"));
    assert.equal(engagementContext, contract.requires_active_engagement,
      `${id} spends requires_active_engagement as an active-engagement required_context, or declares neither`);
  }
  // And the flag is not vacuous: exactly one transition carries it today, and a
  // second arriving without its context entry fails the loop above rather than
  // passing unnoticed.
  assert.deepEqual(
    V5_J102_TRANSITION_IDS.filter(id => v5J102TransitionContract(id).requires_active_engagement),
    ["open-assignment"]);
});

test("SQL parity: the map's stored-record constants ARE the store's and the kernel's", () => {
  // HIGH-5. The writer refuses any subject or event envelope whose schema version
  // or tenant is not these, and it reads them off the map rather than restating
  // them, so this is the one comparison that keeps the map's copies honest. A
  // drift here would turn a bounded validation into a blanket refusal of every
  // legitimate call, which is exactly why it is asserted rather than assumed.
  const policy = admissionPolicy();
  assert.equal(policy.stored_subject_schema_version, V5_J102_STORED_SUBJECT_SCHEMA_VERSION);
  assert.equal(policy.stored_event_schema_version, V5_J102_STORED_EVENT_SCHEMA_VERSION);
  assert.equal(policy.event_schema_version, V5_J102_EVENT_SCHEMA_VERSION);
  // The relations restate all three as CHECK constraints, so a row carrying a
  // foreign schema cannot exist even if a future writer forgets to ask.
  for (const literal of [V5_J102_STORED_SUBJECT_SCHEMA_VERSION,
    V5_J102_STORED_EVENT_SCHEMA_VERSION, V5_J102_EVENT_SCHEMA_VERSION]) {
    assert.ok(CANDIDATE_SQL.includes(`'${literal}'`),
      `the relations bind ${literal} structurally as well as in the writer`);
  }

  // The record key sets the writer holds envelopes to are the shapes the store
  // actually builds — read off the store's own builders, not restated here.
  const subjectRecord = storedSubjectRecord({
    subject: dealState(), transition_id: "record-completion",
    prior_state_digest: D(1), updated_by: "joe", updated_at: SERVER_NOW,
  });
  assert.deepEqual([...policy.stored_subject_record_keys].sort(),
    Object.keys(subjectRecord).sort());
  const eventRecord = storedEventRecord({
    event: { schema_version: V5_J102_EVENT_SCHEMA_VERSION, event_kind: "completion_recorded",
      subject_kind: "deal", subject_id: "deal-synthetic-1" },
    transition_id: "record-completion", evidence_references: [],
    recorded_by: "joe", recorded_at: SERVER_NOW,
  });
  assert.deepEqual([...policy.stored_event_record_keys].sort(), Object.keys(eventRecord).sort());
  // The four keys lifecycleEvent() always writes, which are the ones the writer
  // skips when it walks an event's detail.
  // Both sides sorted, so this compares the SET rather than the order. The map
  // lists these in the order lifecycleEvent() writes them; the assertion is
  // about which four keys they are.
  assert.deepEqual([...policy.event_identity_keys].sort(),
    ["event_kind", "schema_version", "subject_id", "subject_kind"].sort());
  assert.deepEqual([...policy.evidence_reference_keys].sort(),
    ["evidence_kind", "reference", "source"]);
  // M-4: no derived event kind survives anywhere — not in the map, not in the
  // interpreter, and not in the prose that described one as intentional.
  assert.equal(policy.derived_event_kinds, false);
  assert.equal(CANDIDATE_SQL.includes("event_kind_from"), false,
    "the derived-kind branch and its rule are gone from the SQL, not merely unused");
});

test("SQL parity: `writes` is DERIVED from the coupled facts, plus three named derived fields", () => {
  const policy = admissionPolicy();
  // The escape hatch is exactly three fields wide, and every one of them is a
  // counter or a mirror the kernel moves and does not list as a coupled fact. If
  // it ever grows, this line is what makes that visible.
  assert.deepEqual([...policy.derived_fields].sort(), [
    "assignment.active_lease_draft_target_id",
    "assignment.open_negotiation_count",
    "relationship.active_engagement_count",
  ]);
  const allCoupled = new Set(
    V5_J102_TRANSITION_IDS.flatMap(id => v5J102TransitionContract(id).coupled_facts));
  for (const derived of policy.derived_fields) {
    assert.equal(allCoupled.has(derived), false,
      `${derived} is a derived field precisely because no transition couples it`);
  }

  for (const id of V5_J102_TRANSITION_IDS) {
    const contract = v5J102TransitionContract(id);
    const admitted = policy.transitions[id];
    // WHICH SUBJECTS: exactly the primary one plus the prefixes of the coupled
    // facts. Nothing else may be written by this transition, which is what stops
    // an allowed operation carrying an unrelated row beside its real one.
    const expectedKinds = [...new Set([
      contract.subject_kind,
      ...contract.coupled_facts.map(fact => fact.split(".")[0]),
    ])].sort();
    assert.deepEqual(Object.keys(admitted.writes).sort(), expectedKinds,
      `${id} writes exactly its primary subject and its coupled subjects`);

    for (const [kind, fields] of Object.entries(admitted.writes)) {
      const coupled = contract.coupled_facts
        .filter(fact => fact.startsWith(`${kind}.`))
        .map(fact => fact.slice(kind.length + 1));
      for (const fact of coupled) {
        assert.ok(fields.includes(fact),
          `${id} must be able to move the coupled fact ${kind}.${fact}`);
      }
      for (const field of fields) {
        assert.ok(coupled.includes(field) || policy.derived_fields.includes(`${kind}.${field}`),
          `${id} declares ${kind}.${field} movable, and it is neither a coupled fact nor a named derived field`);
      }
    }
  }
});

test("SQL parity: the admission map's evidence table IS the kernel's evidence contracts", () => {
  const policy = admissionPolicy();
  const kinds = Object.keys(policy.evidence).sort();
  assert.deepEqual(kinds, [...V5_J102_EVIDENCE_KINDS]);
  for (const kind of kinds) {
    const contract = v5J102EvidenceContract(kind);
    const admitted = policy.evidence[kind];
    assert.equal(admitted.source, contract.source, `${kind} source`);
    assert.equal(admitted.binds_subject_kind, contract.binds_subject_kind ?? null,
      `${kind} binds_subject_kind`);
    assert.equal(admitted.record_kind, contract.record_kind ?? null, `${kind} record_kind`);
    assert.equal(admitted.requires_author_class, contract.requires_author_class ?? null,
      `${kind} requires_author_class`);
    assert.equal(admitted.requires_closing_date, contract.requires_closing_date === true,
      `${kind} requires_closing_date`);
    assert.deepEqual(admitted.permitted_actor_classes, [...contract.permitted_actor_classes],
      `${kind} permitted_actor_classes`);
    // THE DOCUMENT'S STATE AXES. The pin proves the document has not moved and
    // says nothing about whether it is signed, delivered, effective or current --
    // so a lease that is authentically pinned, correctly associated and NOT
    // EXECUTED would have marked a deal executed on a direct call. Q077's active
    // signed ETL and Q078's lease signing are exactly these axes.
    assert.deepEqual(admitted.document_states ?? null, contract.document_states ?? null,
      `${kind} document_states`);
  }
  // And the four document-backed kinds really do carry axes, so the check above
  // is not vacuously satisfied by a table of nulls.
  const withStates = Object.keys(policy.evidence)
    .filter(kind => policy.evidence[kind].document_states != null).sort();
  assert.deepEqual(withStates, ["commission_agreement", "executed_lease",
    "signed_engagement_letter", "signed_purchase_contract", "submitted_loi"]);
});

test("SQL parity: the parent-reference fields cover every kind and close every coupled chain", () => {
  const policy = admissionPolicy();
  assert.deepEqual(Object.keys(policy.parent_reference_fields).sort(),
    [...V5_J102_SUBJECT_KINDS].sort());
  // Every transition that writes more than one subject must be EXPRESSIBLE as a
  // chain, or the writer's chain check would refuse a legitimate walk.
  for (const id of V5_J102_TRANSITION_IDS) {
    const contract = v5J102TransitionContract(id);
    const kinds = Object.keys(policy.transitions[id].writes);
    for (const kind of kinds) {
      if (kind === contract.subject_kind) continue;
      assert.ok(
        policy.parent_reference_fields[contract.subject_kind] !== null ||
        policy.parent_reference_fields[kind] !== null,
        `${id} couples ${kind} to a ${contract.subject_kind} and neither kind can name the other`);
    }
  }
});

test("BLOCK-1: the three partner-only transitions are partner-only in the SQL map too", () => {
  const policy = admissionPolicy();
  const schemas = v5J102StoreOperationSchemas();
  const partnerOnly = V5_J102_TRANSITION_IDS
    .filter(id => v5J102TransitionContract(id).permitted_actor_classes.length === 1 &&
      v5J102TransitionContract(id).permitted_actor_classes[0] === "verified_partner")
    .sort();
  assert.deepEqual(partnerOnly,
    ["cancel-pending-deal", "commit-winning-property", "record-deal-closing"]);
  for (const id of partnerOnly) {
    assert.deepEqual(policy.transitions[id].permitted_actor_classes, ["verified_partner"],
      `${id} is partner-only where a direct caller reaches it`);
    // And every operation that could name it is authorityOnly here, so the two
    // layers agree rather than one covering for the other.
    for (const operation of policy.transitions[id].operations) {
      assert.equal(schemas[operation].authorityOnly, true,
        `${operation} performs ${id} and must be authorityOnly in this store`);
    }
  }
});

test("BLOCK-1/BLOCK-2/HIGH-1: the writer actually USES the map, refusal by refusal", () => {
  // A correct map that nothing consults would pass every assertion above. These
  // are the call sites, named by the exact refusal each one raises.
  const writer = CANDIDATE_SQL.slice(
    CANDIDATE_SQL.indexOf("create or replace function ops.j102_apply_transition("));
  assert.ok(writer.length > 0, "the transition writer is in the candidate");
  for (const required of [
    // BLOCK-1: the class is DERIVED, and the three admission questions are asked.
    "ops.f01_principal() ->> 'authorization_class'",
    "j102_unknown_transition",
    "j102_operation_transition_mismatch",
    "j102_actor_class_not_permitted",
    // ROOT BLOCKER 1: the primary subject is loaded, never created, and only the
    // two coupled subjects the kernel creates may be created at all.
    "j102_primary_subject_creation_refused",
    "j102_subject_creation_not_permitted",
    "j102_primary_subject_not_found",
    "j102_coupled_subject_not_found",
    // BLOCK-2: which subjects, which fields, which chain, which prerequisites.
    "j102_subject_kind_not_written_by_transition",
    "j102_duplicate_proposed_subject_kind",
    "j102_primary_subject_not_proposed",
    "j102_coupled_subject_not_in_chain",
    "j102_prerequisite_not_met",
    "j102_instrument_kind_not_permitted",
    "j102_field_not_movable_by_transition",
    // ROOT BLOCKER 2: the whole coupled set, the evaluator's own prior
    // conditions, the exact target of every moved field, and the exact event set.
    "j102_required_subject_not_proposed",
    "j102_prior_condition_not_met",
    "j102_transition_effect_not_canonical",
    "j102_transition_effect_missing",
    "j102_created_subject_shape_mismatch",
    "j102_created_subject_field_not_canonical",
    "j102_required_context_not_met",
    "j102_event_set_mismatch",
    "j102_event_missing_or_wrong",
    "j102_event_not_produced_by_transition",
    // HIGH-1: the event's own claim and the subject it names.
    "j102_event_digest_mismatch",
    "j102_event_transition_mismatch",
    "j102_event_subject_not_advanced",
    // HIGH-5: the subject's own provenance, its schema, its tenant and its shape.
    // The forged established_by_transition is the one this list exists for.
    "j102_subject_provenance_mismatch",
    "j102_subject_schema_version_mismatch",
    "j102_subject_tenant_mismatch",
    "j102_subject_record_kind_mismatch",
    "j102_subject_record_shape_unrecognised",
    "j102_subject_header_state_mismatch",
    "j102_subject_envelope_not_an_object",
    // HIGH-6: the whole event payload, and the evidence the history cites.
    "j102_event_schema_version_mismatch",
    "j102_event_payload_schema_version_mismatch",
    "j102_event_tenant_mismatch",
    "j102_event_record_kind_mismatch",
    "j102_event_record_shape_unrecognised",
    "j102_event_envelope_not_an_object",
    "j102_event_detail_not_produced_by_transition",
    "j102_event_detail_missing",
    "j102_event_detail_not_canonical",
    "j102_event_evidence_references_missing",
    "j102_event_evidence_references_not_rechecked",
    "j102_event_evidence_reference_not_rechecked",
    "j102_event_evidence_reference_duplicated",
    // M-3: the operand map's own type, named before anything indexes into it.
    "j102_expected_state_digests_not_an_object",
  ]) {
    assert.ok(writer.includes(required), `the writer raises or derives ${required}`);
  }
  // HIGH-6: the canonical reference set is built from the RECHECK's answer, and
  // the event's citations are compared against that rather than against the
  // manifest or the diagnostics.
  assert.match(writer,
    /into v_canonical_refs\s*\n\s*from jsonb_array_elements\(v_checked\)/,
    "the citations are compared against what the recheck re-read, not against the request");
  // M-2: the two derived receipt fields come off the admission contract, and the
  // caller's diagnostic reason is carried under its own name with its own scope.
  assert.ok(writer.includes("'coupled_facts_committed', coalesce(v_contract -> 'coupled_facts'"));
  assert.ok(writer.includes("'decision_refs', coalesce(v_contract -> 'decision_refs'"));
  assert.ok(writer.includes("'caller_reported_reason_id', p_diagnostics ->> 'reason_id'"),
    "M-2: the kernel's diagnostic reason is labelled as the caller's, not restated as a fact");
  assert.equal(/'reason_id',\s*p_diagnostics/.test(writer), false,
    "M-2: and it is no longer reported under the authoritative name");
  assert.equal(/'coupled_facts_committed',\s*coalesce\(p_diagnostics/.test(writer), false,
    "M-2: coupled_facts_committed is derived, never echoed");
  assert.equal(/'decision_refs',\s*coalesce\(p_diagnostics/.test(writer), false,
    "M-2: decision_refs is derived, never echoed");
  // The effect interpreter is CALLED, in all three places the map declares a
  // value: a creation's shape, a prior condition's comparand, and a moved field.
  assert.ok(writer.split("ops.j102_expected_value(").length - 1 >= 4,
    "the writer computes its expected values rather than carrying a second opinion");
  // And the receipt no longer reports a bypass as a caveat.
  assert.ok(writer.includes("'primary_subject_created', false"));
  assert.ok(writer.includes("'prerequisites_checked', true"));
  assert.ok(writer.includes("'transition_effects_enforced', true"));
  assert.ok(writer.includes("'required_event_set_enforced', true"));
  // The recheck is handed the primary subject rather than left to check the
  // manifest against itself. This is the whole of BLOCKER-2's close.
  assert.match(writer,
    /ops\.j102_recheck_evidence\(\s*p_evidence_recheck,\s*p_transition_id,\s*v_primary_kind,\s*v_primary_id\)/);
  // And the receipt no longer claims more than the code enforces.
  assert.ok(writer.includes("'evidence_bound_to_primary_subject', true"));
  assert.ok(writer.includes("'request_digest_scope', 'caller_supplied_intent_digest_not_recomputed_here'"),
    "M-b: the caller's intent digest is named as caller-supplied, not as a database proof");
  assert.ok(writer.includes("'committed_content_digest_source', 'recomputed_from_committed_rows'"),
    "M-b: what the database DOES vouch for is recomputed and reported separately");
});

test("BLOCK-2: the evidence recheck binds to the primary subject and closes the manifest", () => {
  const start = CANDIDATE_SQL.indexOf("create or replace function ops.j102_recheck_evidence(");
  assert.ok(start > 0);
  const recheck = CANDIDATE_SQL.slice(start,
    CANDIDATE_SQL.indexOf("create or replace function ops.j102_apply_transition("));
  for (const required of [
    "j102_primary_subject_unresolved",
    "j102_unknown_evidence_kind",
    "j102_duplicate_evidence_kind",
    "j102_evidence_source_mismatch",
    "j102_actor_class_not_permitted_for_evidence",
    "j102_evidence_binding_required",
    "j102_evidence_bound_to_wrong_subject_kind",
    // Membership in the lock set is NOT the check; identity with the advancing
    // subject is. This is the refusal that stops "add deal B to the CAS map and
    // propose deal A".
    "j102_evidence_not_bound_to_primary_subject",
    "j102_evidence_record_kind_mismatch",
    "j102_evidence_author_class_not_permitted",
    "j102_evidence_alternative_not_satisfied",
    "j102_closing_date_in_the_future",
    // The document's own state axes, which the pin does not cover.
    "j102_document_state_not_met",
  ]) {
    assert.ok(recheck.includes(required), `the recheck raises ${required}`);
  }
  // The one-argument form is gone, so nothing can establish "the evidence was
  // exact" without being told which subject it was exact FOR.
  assert.ok(CANDIDATE_SQL.includes("drop function if exists ops.j102_recheck_evidence(jsonb);"));
  assert.equal(CANDIDATE_SQL.includes("ops.j102_recheck_evidence(p_recheck jsonb)\n"), false);
});

test("HIGH-2: the candidate refuses an incompatible existing shape and alters nothing", () => {
  // No ALTER, no backfill, no ordinal. The remedy the file names is a fresh
  // database or an already-exact one, and it says so instead of no-opping.
  assert.equal(/\balter\s+table\b/i.test(CANDIDATE_SQL), false,
    "the candidate contains no ALTER TABLE; it is source, not a migration");
  assert.equal(/\bdrop\s+table\b/i.test(CANDIDATE_SQL), false,
    "and it drops no relation, so nothing it may be applied beside loses data");
  // The only UPDATE anywhere is the idempotency settle and the upsert's own
  // ON CONFLICT clause. Neither touches a lifecycle row that was already there,
  // which is what "no backfill program" means concretely.
  const updates = CANDIDATE_SQL.match(/\bupdate\s+ops\.j102_[a-z_]+/gi) ?? [];
  assert.deepEqual([...new Set(updates.map(u => u.toLowerCase().replace(/\s+/g, " ")))],
    ["update ops.j102_idempotency"]);
  assert.ok(CANDIDATE_SQL.includes("j102_incompatible_existing_schema"));
  for (const column of ["bound_subject_kind", "bound_subject_id", "recorded_by_class"]) {
    // The three columns the corrections added are exactly the ones an earlier
    // candidate's table would be missing, so they must be in the preflight list.
    const preflight = CANDIDATE_SQL.slice(0, CANDIDATE_SQL.indexOf("-- Guards."));
    assert.ok(preflight.includes(`'${column}'`),
      `the preflight names ${column}, which an earlier candidate's table would lack`);
  }
});

test("SQL parity: the map's INITIALIZATIONS are the kernel's initialization contracts", () => {
  const policy = admissionPolicy();
  assert.deepEqual(Object.keys(policy.initializations).sort(), [...V5_J102_INITIALIZATION_IDS]);
  const schemas = v5J102StoreOperationSchemas();
  for (const id of V5_J102_INITIALIZATION_IDS) {
    const contract = v5J102InitializationContract(id);
    const admitted = policy.initializations[id];
    assert.equal(admitted.subject_kind, contract.subject_kind, `${id} subject_kind`);
    assert.deepEqual(admitted.permitted_actor_classes, contract.permitted_actor_classes,
      `${id} permitted_actor_classes`);
    assert.deepEqual(admitted.decision_refs, contract.decision_refs, `${id} decision_refs`);
    assert.equal(admitted.requires_evidence, false, `${id} requires no evidence`);
    assert.equal(admitted.parent_subject_kind, contract.parent_subject_kind ?? null,
      `${id} parent_subject_kind`);
    assert.deepEqual(admitted.declared_identifiers, contract.declared_identifiers,
      `${id} declared_identifiers`);
    // The operation pairing, from the store's own schemas rather than restated.
    const operations = V5_J102_OPERATIONS.filter(name => schemas[name].initialization === id);
    assert.deepEqual([...admitted.operations].sort(), operations.sort(),
      `${id} is performed by exactly the operation this store routes to it`);
    // THE CREATION SHAPE COVERS EXACTLY THE CREATED ROW, key for key — its own
    // identity, the parent reference where there is one, the declared
    // identifiers, and the kernel's fixed initial state. A key the map does not
    // name is a key the writer refuses; a key it names that the kernel does not
    // write is a field the writer would demand and never get.
    assert.deepEqual(Object.keys(admitted.creation_shape).sort(), [...new Set([
      "subject_kind", "subject_id",
      ...(contract.parent_reference_field === null ? [] : [contract.parent_reference_field]),
      ...contract.declared_identifiers,
      ...contract.optional_declared_identifiers,
      ...Object.keys(contract.initial_state),
    ])].sort(), `${id} creation shape covers exactly the created row`);
    assert.deepEqual(admitted.optional_declared_identifiers, contract.optional_declared_identifiers,
      `${id} optional_declared_identifiers`);
    assert.equal(admitted.unique_active_per_property === true, contract.unique_active_per_property,
      `${id} one-active-per-property`);
    for (const [field, value] of Object.entries(contract.initial_state)) {
      const effect = admitted.creation_shape[field];
      assert.ok(effect, `${id} fixes ${field} in the SQL creation shape too`);
      assert.equal(effect.op, "const",
        `${id}.${field} is a constant, not something a caller may choose`);
      assert.deepEqual(effect.value, value, `${id}.${field} is exactly the kernel's value`);
    }
    // The context conditions, in both directions.
    assert.deepEqual(
      (admitted.required_context ?? []).map(rule => rule.subject),
      contract.required_context.map(rule => rule.subject), `${id} required_context subjects`);
    // The event: one, of the kernel's kind, on the created subject, with the
    // kernel's own detail fields and nothing else.
    assert.equal(admitted.event.event_kind, contract.event_kind, `${id} event_kind`);
    assert.equal(admitted.event.subject, contract.subject_kind, `${id} event subject`);
    assert.deepEqual(Object.keys(admitted.event.detail).sort(),
      [...contract.event_detail_fields].sort(), `${id} event detail fields`);
  }
  // AND THE TWO COUPLED CREATIONS ARE NOT REACHABLE THROUGH THIS DOOR. An
  // engagement or a deal created outside its transition would be client status
  // with no coupled change, or the overruled Q078 rule back again.
  for (const admitted of Object.values(policy.initializations)) {
    assert.ok(!["engagement", "deal"].includes(admitted.subject_kind));
  }
  assert.equal(policy.initialization_requires_evidence, false);
  assert.equal(policy.initialization_performs_transition, false);
  assert.equal(policy.initialization_writer, "ops.j102_initialize_subject");
});

test("ANTI-BYPASS: the creation door is a separate writer and apply_transition still refuses a primary", () => {
  // The refusal that must survive the arrival of a creation door, and the note
  // that says why it is separate rather than a flag on one writer.
  assert.ok(CANDIDATE_SQL.includes("j102_primary_subject_creation_refused"),
    "the transition writer still refuses to create the subject it advances");
  assert.equal(admissionPolicy().subject_creation,
    "coupled_only_never_the_primary_subject",
    "and the map still says creation inside a transition is coupled-only");
  // Every transition's own primary subject is still update-only in the map.
  const policy = admissionPolicy();
  for (const id of V5_J102_TRANSITION_IDS) {
    const contract = v5J102TransitionContract(id);
    assert.equal(policy.transitions[id].subjects[contract.subject_kind].mode, "update",
      `${id} still advances a ${contract.subject_kind} that already exists`);
  }
  // The initialization writer exists, is its own function, and does not reach the
  // transition writer.
  const start = CANDIDATE_SQL.indexOf("create or replace function ops.j102_initialize_subject(");
  assert.ok(start > 0, "the candidate carries a separate initialization writer");
  const body = CANDIDATE_SQL.slice(start,
    CANDIDATE_SQL.indexOf("comment on function ops.j102_initialize_subject", start));

  // ===========================================================================
  // "IT NEVER CALLS THE TRANSITION WRITER" IS A CLAIM ABOUT CODE, NOT ABOUT TEXT.
  //
  // The first version of this assertion required the writer's SOURCE to contain
  // no "ops.j102_apply_transition" anywhere, and that tested nothing worth
  // testing: the writer NAMES the transition writer inside the refusal it raises
  // when a caller offers a transition id to the creation door, so the string is
  // legitimately present and the assertion failed against correct code. The two
  // ways to "fix" that are both wrong — deleting the guard removes a control, and
  // rewording the refusal keeps the control while making it unsayable.
  //
  // So the check is made against the EXECUTABLE text: string literals and
  // comments are removed and what remains is what actually runs. A real
  // `perform ops.j102_apply_transition(...)` survives that and fails the
  // assertion; a refusal message does not. Both directions are then controlled
  // below, because a stripper that removed too much would make this vacuous.
  // ===========================================================================
  assert.ok(body.includes("j102_transition_is_not_an_initialization"),
    "the creation door refuses a transition id by name");
  assert.ok(body.includes("ops.j102_apply_transition"),
    "and that refusal NAMES the writer that does perform transitions, which is why this cannot be a plain text search");

  const executable = executableSql(body);
  assert.equal(executable.includes("j102_apply_transition"), false,
    "the initialization writer never invokes the transition writer");
  // The one route by which a call could hide inside a literal the stripper
  // removes is dynamic SQL, and this writer builds none.
  assert.equal(/\bexecute\b/i.test(executable), false,
    "and it composes no dynamic SQL, so no call can hide in a string it executes");

  // CONTROL ONE — THE STRIPPER KEEPS THE CODE. If it removed too much, the
  // assertion above would pass against anything at all.
  for (const call of ["ops.j102_claim_idempotency(", "ops.j102_expected_value(",
    "ops.j102_settle_idempotency(", "ops.j102_subject(", "pg_advisory_xact_lock(",
    "insert into ops.j102_subject_current", "insert into ops.j102_subject_event"]) {
    assert.ok(executable.includes(call),
      `the executable text still carries ${call}, so the stripper took comments and literals and not code`);
  }
  // CONTROL TWO — A REAL CALL WOULD FAIL THIS TEST. The invocation is spliced
  // into a COPY of the writer's source; nothing on disk is touched, no SQL runs.
  const mutated = body.replace(
    "v_replay := ops.j102_claim_idempotency(",
    "perform ops.j102_apply_transition('open-assignment', p_expected_state_digests,\n"
    + "    '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, p_idempotency_key, p_request_digest,\n"
    + "    p_diagnostics);\n  v_replay := ops.j102_claim_idempotency(");
  assert.notEqual(mutated, body, "the control mutation actually applied");
  assert.equal(executableSql(mutated).includes("j102_apply_transition"), true,
    "a real invocation survives the stripping, so this assertion would fail on one");
  // And the mutation is a CALL rather than more prose: the raw text of both
  // carries the name, and only the executable text tells them apart.
  assert.equal(body.includes("ops.j102_apply_transition"),
    mutated.includes("ops.j102_apply_transition"),
    "the raw text cannot distinguish the refusal message from a call, which is the point");

  for (const refusal of [
    "j102_transition_is_not_an_initialization", "j102_initialization_is_not_an_update",
    "j102_subject_already_exists", "j102_created_subject_field_not_canonical",
    "j102_created_subject_shape_mismatch", "j102_required_context_not_met",
    "j102_required_context_not_locked", "j102_initialization_cites_evidence",
    "j102_operand_subject_not_read_by_initialization", "j102_subject_provenance_mismatch",
  ]) {
    assert.ok(body.includes(refusal), `the initialization writer carries ${refusal}`);
  }
  // THE OPERAND SET IS CLOSED BY IDENTITY, NOT BY KIND, and it can only be closed
  // AFTER the context loop has resolved which parent id this call actually reads.
  // Closing it by kind alone leaves `assignment:somebody-elses-id` locked,
  // compare-and-swapped, never consulted, and reported in the receipt's
  // `expected_state_digests` beside `required_context_enforced: true`.
  const identityCheck = body.indexOf("v_consulted_keys @> jsonb_build_array(to_jsonb(v_key))");
  const contextLoop = body.indexOf("j102_required_context_not_met");
  assert.ok(identityCheck > 0, "the writer closes the operand set by identity");
  assert.ok(identityCheck > contextLoop,
    "and it does so AFTER resolving the parents, which is the only point at which the identities exist");
  // AND THE RECEIPT REPORTS THAT SET. Two unconditional booleans describe the
  // empty set on a parentless creation; the consulted list is what makes them
  // checkable rather than decorative.
  assert.ok(body.includes("'context_subjects_consulted', v_consulted_keys"),
    "the receipt names which parents were consulted");
  assert.ok(body.includes("v_consulted_keys := v_consulted_keys"),
    "and the list is built from rules that actually resolved, not from the request");
  // THE CONCURRENCY CLAIM IS CONDITIONED ON THE ISOLATION LEVEL RATHER THAN
  // STATED FLATLY. The shared advisory lock serializes two creations of one id
  // either way and neither overwrites the other — but the NAMED refusal
  // (`j102_subject_already_exists`) is what a READ COMMITTED loser sees, while at
  // REPEATABLE READ or SERIALIZABLE its snapshot predates the winner's commit,
  // the compare-and-swap passes and the primary key raises 23505 instead. This
  // rail issues a bare BEGIN and sets no isolation level, so the message is the
  // deployment's and the safety is the rail's.
  for (const phrase of ["READ COMMITTED", "PRIMARY KEY", "23505"]) {
    assert.ok(CANDIDATE_SQL.includes(phrase),
      `the concurrency note names ${phrase} rather than promising one refusal unconditionally`);
  }
  assert.equal(CANDIDATE_SQL.includes("refuses rather than overwriting it"), false,
    "and the unconditional wording it replaced is gone rather than sitting beside the qualified one");
  // The three initialization ids are the ONLY history rows admitted to cite no
  // evidence, and the relation says so structurally rather than the writer alone.
  for (const id of V5_J102_INITIALIZATION_IDS) {
    assert.ok(CANDIDATE_SQL.includes(`'${id}'`),
      `the event relation's empty-citation carve-out names ${id}`);
  }
  const citeCheck = CANDIDATE_SQL.slice(
    CANDIDATE_SQL.indexOf("constraint j102_event_cites_evidence"),
    CANDIDATE_SQL.indexOf("constraint j102_event_record_schema_version"));
  for (const id of V5_J102_INITIALIZATION_IDS) {
    assert.ok(citeCheck.includes(`'${id}'`),
      `${id} is inside the constraint's own carve-out, not merely somewhere in the file`);
  }
  assert.equal(citeCheck.includes("open-assignment"), false,
    "and no transition is inside it, so a transition still cannot cite nothing");
});

test("Q103: the reconciliation writer is governed, and the ungoverned overload is DROPPED", () => {
  // THE DROP IS THE ASSERTION THAT MATTERS. `create or replace` with a new
  // argument list creates an OVERLOAD: the old one-argument writer — no
  // idempotency key, no compare-and-swap operand, no re-read of the subject —
  // would still be callable by anything holding its grant, and every property the
  // replacement adds would be one call away from being skipped.
  assert.ok(CANDIDATE_SQL.includes(
    "drop function if exists ops.j102_record_reconciliation_item(jsonb);"),
  "the ungoverned single-argument writer is dropped, not shadowed");
  assert.ok(CANDIDATE_SQL.includes(
    "create or replace function ops.j102_record_reconciliation_item(\n  p_envelope jsonb, p_expected_state_digests jsonb,\n  p_idempotency_key text, p_request_digest text, p_diagnostics jsonb)"),
  "and the governed form takes the operand map, the key, the request digest and the diagnostics");
  // No stale signature survives in a grant, a revoke or the shadow assertion —
  // any one of them would fail at apply time against the dropped function.
  const stale = CANDIDATE_SQL.split("\n")
    .filter(line => line.includes("j102_record_reconciliation_item"))
    .filter(line => /record_reconciliation_item\(jsonb\)/.test(line));
  assert.deepEqual(stale,
    ["drop function if exists ops.j102_record_reconciliation_item(jsonb);"],
    "the one-argument signature appears only in its own drop");

  const start = CANDIDATE_SQL.indexOf(
    "create or replace function ops.j102_record_reconciliation_item(");
  const body = CANDIDATE_SQL.slice(start,
    CANDIDATE_SQL.indexOf("comment on function ops.j102_record_reconciliation_item", start));
  const executable = executableSql(body);
  // IT CLAIMS BEFORE IT READS, and settles what it wrote — the same governance
  // every sibling writer carries.
  assert.ok(executable.includes("ops.j102_claim_idempotency("));
  assert.ok(executable.includes("ops.j102_settle_idempotency("));
  assert.ok(executable.indexOf("ops.j102_claim_idempotency(") <
    executable.indexOf("ops.j102_subject_current"),
  "the key is claimed before any state is read");
  // IT TAKES THE ESTABLISHED TIER-2 LOCK before the compare-and-swap.
  assert.ok(executable.indexOf("pg_advisory_xact_lock") <
    executable.indexOf("ops.f01_digest_jsonb(v_stored_state)"),
  "the subject is locked before its digest is compared");
  // AND THE THREE STALENESS BINDINGS ARE IN THE CODE, not in a comment.
  for (const refusal of ["j102_reconciliation_current_version_not_current",
    "j102_reconciliation_state_evidence_stale",
    "j102_reconciliation_history_evidence_stale",
    "j102_reconciliation_without_conflict", "j102_reconciliation_resolves_itself",
    "j102_item_operand_set_mismatch", "j102_stale_subject_digest",
    "j102_item_conflict_kind_unregistered", "j102_item_field_class_mismatch"]) {
    assert.ok(body.includes(refusal), `the writer carries ${refusal}`);
  }
  // THE TWO LABEL CHECKS READ THE MAP, rather than restating the vocabulary in a
  // second place that nothing compares. A literal list here would pass the
  // refusal-code assertion above and drift from the kernel silently.
  assert.ok(executable.includes("ops.j102_admission_policy()"),
    "the writer's vocabulary is the admission map's, not its own copy");
  // The rest are comparisons against particular jsonb KEYS, which are literals —
  // so they are asserted against the body with its comments removed and its
  // literals kept, and a prose mention of either cannot satisfy them.
  const code = sqlWithoutComments(body);
  assert.ok(code.includes("v_recon -> 'conflict_kinds'"),
    "conflict_kind is checked against the map's registered kinds");
  assert.ok(code.includes("v_recon -> 'field_class_registry' -> v_field"),
    "and every edit's class is checked against the map's registry entry for THAT field");
  // BOTH SIDES. A lying concurrent_edits label misleads the person resolving the
  // conflict exactly as much as a lying incoming one.
  assert.ok(code.includes("array['incoming_edits', 'concurrent_edits']"),
    "the field-class check walks both preserved sides");
  // AND THE RESOLUTION FLAGS ARE JSON BOOLEANS at both the writer and the
  // relation, so the string "false" is not accepted where the boolean is meant.
  for (const [field, value] of [["resolved_by_machine", "false"], ["visible", "true"],
    ["applied", "false"]]) {
    assert.ok(code.includes(`(v_record -> '${field}') is distinct from '${value}'::jsonb`),
      `${field} is compared as a jsonb boolean rather than through ->>`);
    assert.equal(code.includes(`v_record ->> '${field}'`), false,
      `and the text comparison of ${field} is gone rather than sitting beside it`);
  }
  const relation = CANDIDATE_SQL.slice(
    CANDIDATE_SQL.indexOf("create table if not exists ops.j102_reconciliation_item ("),
    CANDIDATE_SQL.indexOf("comment on table ops.j102_reconciliation_item"));
  assert.ok(relation.includes("j102_item_resolution_flags_are_booleans"),
    "the relation pins the three flags' JSON TYPE as well as their value");
  for (const kind of V5_J102_CONFLICT_KINDS) {
    assert.ok(relation.includes(`'${kind}'`),
      `the relation's own conflict_kind CHECK names ${kind}`);
  }
  for (const kind of V5_J102_SUBJECT_KINDS) {
    assert.ok(relation.includes(`'${kind}'`),
      `and its subject_kind CHECK carries the same five kinds j102_subject_current does (${kind})`);
  }
  // The replay door admits the operation, or the key could never be claimed.
  const replay = CANDIDATE_SQL.slice(
    CANDIDATE_SQL.indexOf("create or replace function ops.j102_replay_outcome("),
    CANDIDATE_SQL.indexOf("create or replace function ops.j102_claim_idempotency("));
  assert.ok(replay.includes("'record-lifecycle-reconciliation'"),
    "the closed write-operation vocabulary admits the reconciliation operation");
  // AND NO UNIQUE INDEX COLLAPSES DISTINCT PROPOSALS on the version pair.
  assert.equal(/create\s+unique\s+index[^;]*j102_reconciliation_item/i.test(CANDIDATE_SQL), false,
    "two different proposals against one version pair are two conflicts, and both stay visible");
});

test("SQL parity: the admission map's reconciliation vocabulary IS the kernel's", () => {
  // THE POINT OF THE TRANSCRIPTION IS THAT IT IS ONE CONTRACT WITH TWO READERS.
  // The writer refuses a conflict_kind and a field_class against the map, so a map
  // that has drifted from the kernel would refuse real kernel answers at the
  // database, or admit labels the kernel never files. Both are failures here.
  const recon = admissionPolicy().reconciliation;
  assert.deepEqual(recon.conflict_kinds, [...V5_J102_CONFLICT_KINDS]);
  assert.deepEqual(recon.field_classes, [...V5_J102_FIELD_CLASSES]);
  assert.deepEqual(recon.material_field_classes, [...V5_J102_MATERIAL_FIELD_CLASSES]);
  assert.equal(recon.caller_supplied_field_class_admitted, false);
  assert.equal(recon.writer, "ops.j102_record_reconciliation_item");

  // FIELD BY FIELD, IN BOTH DIRECTIONS. A missing entry would let a real edit be
  // refused as mislabelled; an extra one would let an unregistered field carry a
  // class — and `routine` is the class that would matter.
  assert.deepEqual(Object.keys(recon.field_class_registry).sort(),
    Object.keys(V5_J102_FIELD_CLASS_REGISTRY).sort());
  for (const [field, cls] of Object.entries(V5_J102_FIELD_CLASS_REGISTRY)) {
    assert.equal(recon.field_class_registry[field], cls, `${field} is classified ${cls}`);
  }
  // The registry's own statement about itself travels with it, so a routine entry
  // added on one side is visible on the other.
  assert.equal(recon.routine_fields_registered,
    V5_J102_UNCLASSIFIED_FIELD_POLICY.routine_fields_registered);
  assert.equal(Object.values(recon.field_class_registry).includes("routine"), false);
  // An unregistered field's class is JSON null in both places, which is what the
  // writer compares an edit against.
  assert.equal(recon.unregistered_field_class, null);

  // AND THE KINDS ARE THE ONES THE EVALUATOR ACTUALLY FILES, driven rather than
  // restated — the same check the kernel suite makes, made here against the SQL.
  const edit = (field) => ({
    field, value_digest: `sha256:${"7".repeat(64)}`, edited_by: "joe", edited_at: T.mid,
  });
  const kinds = new Set();
  for (const [incoming, concurrent] of [
    [[edit("internal_note")], undefined],
    [[edit("deal_state")], [edit("deal_state")]],
    [[edit("deal_state")], [edit("closing_state")]],
    [[edit("internal_note")], [edit("next_touch_hint")]],
  ]) {
    const answer = evaluateConcurrentEdit({
      tenant: ORGANIZATION_TENANT_ID,
      actor: { slug: "joe", human: true, authorization_class: "verified_partner",
        derived_by: "authenticated_handler_context" },
      base_version_digest: `sha256:${"1".repeat(64)}`,
      current_version_digest: `sha256:${"2".repeat(64)}`,
      incoming, concurrent,
    });
    if (answer.reconciliation_item !== null) kinds.add(answer.reconciliation_item.conflict_kind);
  }
  assert.deepEqual([...kinds].sort(), [...recon.conflict_kinds].sort(),
    "the SQL vocabulary is exactly the set evaluateConcurrentEdit can produce");
});

test("M-c/M-f: the association pins its version, and a future Salesforce observation refuses", () => {
  assert.ok(CANDIDATE_SQL.includes("j102_link_version_matches_envelope"),
    "M-c: version_no is CHECK-bound to the envelope like every other binding column");
  assert.ok(CANDIDATE_SQL.includes("j102_link_pin_not_held: F01 holds document % at version %"),
    "M-c: the association's version is compared to F01's, not only its content digest");
  assert.ok(CANDIDATE_SQL.includes("j102_observed_at_in_the_future"),
    "M-f: Salesforce cannot claim to have observed its own record after our clock");
});

test("M-a: capabilities report the association prerequisite that actually binds", () => {
  const registrations = v5J102ToolRegistrations();
  const byName = Object.fromEntries(registrations.map(entry => [entry.name, entry]));

  // The document-backed transitions: `authorityOnly: false` and yet not completable
  // by an agent alone, because the association their evidence needs is
  // partner-written. Saying only the first was the misreport.
  for (const name of ["record-representation-agreement", "record-deal-execution",
    "record-loi-submission", "record-loi-acceptance"]) {
    const entry = byName[name];
    assert.equal(entry.authorityOnly, false, `${name} is not itself authorityOnly`);
    assert.equal(entry.requires_partner_written_evidence_association, true,
      `${name} rests on evidence that needs a partner-written association`);
    assert.equal(entry.association_prerequisite.written_by_operation,
      "record-evidence-subject-link");
    assert.equal(entry.association_prerequisite.written_by_authorization_class,
      "verified_partner");
    assert.ok(entry.association_prerequisite.pinned_on.includes("version_no"),
      `${name}'s prerequisite states the version pin, which is the sharp edge`);
    assert.equal(v5J102StoreOperationSchemas()["record-evidence-subject-link"].authorityOnly,
      true, "and the operation that writes one really is authorityOnly");
  }
  // The record-backed ones need no association at all: a first-party record
  // carries its own typed binding.
  for (const name of ["open-cre-assignment", "record-diligence-outcome",
    "record-deal-closing", "cancel-pending-deal"]) {
    assert.equal(byName[name].requires_partner_written_evidence_association, false,
      `${name} rests on first-party records, which bind themselves`);
    assert.equal(byName[name].association_prerequisite, null);
  }
  // Nothing was widened: the authorityOnly set is unchanged.
  const schemas = v5J102StoreOperationSchemas();
  assert.deepEqual(V5_J102_OPERATIONS.filter(n => schemas[n].authorityOnly).sort(),
    ["cancel-pending-deal", "commit-winning-property", "record-deal-closing",
      "record-evidence-subject-link", "record-lifecycle-correction"]);
  // A dispatching operation reports BOTH of the transitions it can reach.
  assert.deepEqual(byName["record-deal-execution"].dispatched_transitions,
    ["record-lease-execution", "record-purchase-contract-execution"]);
  assert.deepEqual(byName["record-deal-axis"].dispatched_transitions,
    ["record-commission-agreement", "record-completion", "record-invoice-issued",
      "record-payment"]);
});

test("the SQL fixture stays UNEXECUTED source, and rolls every synthetic row back", () => {
  const fixture = readFileSync(
    new URL("./cre-lifecycle-postgres.sql", import.meta.url), "utf8");
  assert.ok(fixture.trimEnd().endsWith("rollback;"),
    "the fixture ends in a rollback, so running it leaves nothing behind");
  assert.equal(/^\s*commit\s*;/im.test(fixture), false,
    "the fixture never commits");
  assert.ok(fixture.includes("THIS FILE HAS NOT BEEN EXECUTED"),
    "the fixture says plainly that it is source and not a result");
  // The adversarial groups, named so a future edit that drops one is visible
  // here rather than only in a diff.
  for (const group of ["A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9", "A10",
    // HIGH-5's forged provenance and M-3's untyped operand map. Both refuse
    // BEFORE any state is read, so unlike U1/U2 they really fire in a file with
    // no committed subject.
    "A11", "A12"]) {
    assert.ok(fixture.includes(`=== ${group}:`), `the fixture carries adversarial group ${group}`);
  }
  // U1 AND U2 NOW RUN AGAINST A COMMITTED ROW. They were unreachable while
  // nothing could create an assignment, and reported themselves UNPROVEN with a
  // reason the creation door made false. They have MOVED into the walk rather
  // than been relabelled: each now asserts the check it is aimed at, and the old
  // reason is gone rather than reworded.
  for (const group of ["U1", "U2"]) {
    assert.ok(fixture.includes(`=== ${group}:`), `the fixture carries group ${group}`);
    assert.ok(fixture.includes(`${group}a PROVED`),
      `${group} proves the check it is aimed at rather than reporting a compare-and-swap refusal`);
  }
  // THE RETIRED LABEL IS NO LONGER A REASON ANYWHERE. It meant two different
  // propositions — "no transition creates its own primary subject" (still true)
  // and "this fixture cannot obtain a committed assignment" (now false) — and a
  // notice giving it as the reason for a skip would be giving a reason that no
  // longer holds. It survives only in the note recording its retirement, which is
  // what a reader of the diff needs.
  const staleLabelInOutput = fixture.split("\n")
    .filter(line => line.includes("j102_fixture_bootstrap_absent"))
    .filter(line => /\braise\b/.test(line) || /^\s*'/.test(line));
  assert.deepEqual(staleLabelInOutput, [],
    "the retired label is not given as a reason in any notice or exception");
  assert.equal(
    fixture.split("j102_fixture_bootstrap_absent").length - 1, 1,
    "and it appears exactly once, in the note that retires it");
  assert.ok(fixture.includes("=== J4:"),
    "and the negative context cases the walk makes reachable are carried");
  // Q103's conflict writer, structurally and behaviourally. The behavioural group
  // needs a committed subject, so it lives inside the walk for the same reason
  // U1 and U2 do.
  assert.ok(fixture.includes("=== S9:"),
    "the fixture asserts the reconciliation writer is governed and the old one is gone");
  assert.ok(fixture.includes("=== R:"),
    "and drives it against a committed subject: replay, payload mismatch, distinct proposals, stale operands");
  for (const refusal of ["j102_idempotency_payload_mismatch", "j102_stale_subject_digest",
    "j102_reconciliation_current_version_not_current",
    "j102_reconciliation_state_evidence_stale",
    "j102_reconciliation_history_evidence_stale",
    "j102_reconciliation_without_conflict",
    // And the two label refusals, which are what stop the queue a person reads
    // from carrying a category nothing files or a lifecycle field called routine.
    "j102_item_conflict_kind_unregistered", "j102_item_field_class_mismatch"]) {
    assert.ok(fixture.includes(refusal), `the conflict group drives ${refusal}`);
  }
  // THE CLOSING NOTICES MUST ACCOUNT FOR BOTH NEW GROUPS, or a skipped walk reads
  // as a run that proved the conflict writer. S9 is structural and runs always; R
  // lives INSIDE the walk and runs only when J does, which is exactly the
  // distinction the two lines have to carry.
  const notices = fixture.split("\n").filter(line => line.includes("RUNNABLE GROUPS PASSED")
    || line.includes("PARTIAL RUN"));
  assert.equal(notices.length, 2, "there are exactly two closing notices to keep honest");
  const [full, partial] = notices;
  for (const group of ["S1-S9", "R1-R15", "J4", "U1", "U2"]) {
    assert.ok(full.includes(group), `the full-run notice enumerates ${group}`);
  }
  assert.ok(partial.includes("S1-S9"),
    "the partial-run notice enumerates S9, which is structural and DOES run");
  assert.ok(partial.includes("R1-R15 did not run"),
    "and says the conflict group did not run rather than leaving it out");
  assert.ok(partial.includes("ANY BEHAVIOUR OF THE Q103 CONFLICT WRITER"),
    "and says what that costs the reader rather than only naming the group");
  assert.ok(partial.includes("S9 ran and is structural only"),
    "and does not let a structural group stand in for the behavioural one");
  // THE ONE ARM THAT IS STILL UNREACHABLE HERE IS NAMED, not left to be inferred
  // from the cases that happen to be present.
  assert.ok(fixture.includes("UNPROVEN IN THIS FILE -- j102_required_context_not_met"),
    "the fixture names the context arm no shipped door can drive");
  // AND A SKIPPED WALK CANNOT READ AS A GREEN ONE. The row counts compare against
  // counters the walk increments, so they pass identically when it stops early;
  // the closing notice is what distinguishes the two runs.
  assert.ok(fixture.includes("PARTIAL RUN"),
    "a run that skipped the walk says so at the end rather than reporting all groups passed");
  assert.ok(fixture.includes("Do not read this as a green J"),
    "and it says plainly what a green exit status would otherwise imply");
  assert.ok(fixture.includes("=== S7:"),
    "the fixture asserts the provenance and citation bindings structurally too");
  // THE CREATION DOOR, STRUCTURALLY AND BEHAVIOURALLY, AND THE WALK IT MAKES
  // POSSIBLE.
  assert.ok(fixture.includes("=== S8:"),
    "the fixture asserts the initialization half of the admission map structurally");
  assert.ok(fixture.includes("=== I1:"),
    "the fixture carries the creation door's own refusal matrix");
  assert.ok(fixture.includes("=== J:"),
    "the fixture carries the positive walk from a legitimate first row");
  // AND THE ANTI-BYPASS GROUP SURVIVES THE ARRIVAL OF THAT DOOR. P0 is the case
  // that must keep refusing: a transition may not create the subject it advances,
  // whatever else can now create one.
  assert.ok(fixture.includes("=== P0:"),
    "the fixture still proves the transition writer creates no primary subject");
  assert.ok(fixture.includes("j102_primary_subject_creation_refused"),
    "and it names that refusal by the writer's own word for it");
  // The walk skips rather than fabricates when a real prerequisite is absent, and
  // each skip names WHICH prerequisite rather than a generic miss.
  assert.ok(fixture.includes("J3 SKIPPED -- UNMET PREREQUISITE"),
    "the walk names why it stopped rather than inventing a document or a role");
  assert.equal(/create\s+role/i.test(fixture), false,
    "and it still creates no role, so no identity is minted to get past a gate");

  // THE CURRENT F01 DOCUMENT WRITER IS THE SIX-ARGUMENT ONE. domain.sql drops its
  // four-argument form the moment the document-source hunk is present, so a
  // fixture gated on that overload alone always skipped on exactly the
  // configuration that hunk targets. Both are probed by exact signature.
  assert.ok(fixture.includes("ops.f01_record_document(jsonb,jsonb,jsonb,text,text,text)"),
    "the walk reaches the CURRENT six-argument F01 document writer");
  assert.ok(fixture.includes("ops.f01_record_document(jsonb,text,text,text)"),
    "and still handles the four-argument form where that is what a database has");
  assert.ok(fixture.includes("'provenance_state', 'original_first_party'"),
    "the ETL is recorded as the original it is, with no artifact and no derivative link");
  assert.ok(fixture.includes("basis_statement"),
    "and it carries the basis a non-derived statement is required to state");
  assert.equal(fixture.includes("derived_from_stored_artifact"), false,
    "no derivative coverage is invented to get a document written");
  // AND AN F01 REFUSAL IS A FAILURE, NOT A SKIP. The earlier revision wrapped the
  // call in `exception when others` → notice, so a wrong envelope shape reported
  // as a missing prerequisite.
  const j3 = fixture.slice(fixture.indexOf("=== J3:"), fixture.indexOf("if v_walk_ok then"));
  assert.ok(j3.length > 0, "the J3 leg is locatable");
  assert.equal(/exception\s+when\s+others/i.test(j3), false,
    "the F01 call is not wrapped in a catch-all that turns a refusal into a skip");
});

// ---------------------------------------------------------------------------
// THE TRANSITION-EFFECT PARITY SUITE.
//
// The map above says which FIELDS a transition may move. That is not the same
// question as what it moves them TO, and the difference is the whole of the
// second root correction: a routine `open-assignment` may write
// `assignment_phase`, and `assignment_phase: "committed"` is a commitment
// performed without any of a commitment's evidence.
//
// So `subjects` and `events` in the admission map carry the EXACT result, and
// these tests prove the transcription by running the kernel and requiring the
// map to PREDICT its answer:
//
//   1. the kernel evaluates a real transition and returns proposed_state/events,
//   2. the map's own rules are interpreted against the same inputs,
//   3. the two must agree field by field and event by event.
//
// NOTHING HERE MIRRORS THE MAP AT ITSELF. The oracle is the kernel's evaluator,
// which knows nothing about the SQL; a map that drifts from it fails here.
//
// WHAT THIS CANNOT PROVE, said plainly: the interpreter below is a
// TRANSCRIPTION of ops.j102_expected_value and of the writer's comparison loop,
// written in JavaScript because no database is reachable from this suite. It
// proves the MAP is right. That the PL/pgSQL implementation of the same
// algorithm behaves identically is unexecuted, and is the SQL fixture's job.
// ---------------------------------------------------------------------------

const MISSING = Symbol("missing");
const at = (object, field) =>
  object !== undefined && object !== null &&
  Object.prototype.hasOwnProperty.call(object, field) ? object[field] : MISSING;
const orNull = value => (value === MISSING ? null : value);
const show = value => (value === MISSING ? "absent" : JSON.stringify(value));

/** ops.j102_expected_value, transcribed. Same three answer shapes. */
function expectedValue(effect, ctx) {
  const from = source =>
    source === "prior" ? ctx.prior : source === "proposed" ? ctx.proposed : ctx.context;
  switch (effect.op) {
    case "const":
      return { kind: "exact", value: orNull(at(effect, "value")) };
    case "unbound":
      return { kind: "unbound" };
    // THE ONE ANSWER SHAPE THE SQL CANNOT BIND TO A VALUE, and the seam is real
    // rather than a gap: the store sends the writer a proposed state, never the
    // caller's `declared` object, so SQL can hold the property a new negotiation
    // concerns to the IDENTIFIER SHAPE and no further. The KERNEL binds the value
    // — it composes the created state from `declared` itself — so the parity
    // comparison below asks SQL for the shape and the kernel for the value, and
    // says which layer answers which.
    case "declared_identifier":
      return { kind: "declared_identifier", field: effect.field };
    case "optional_declared_identifier":
      return { kind: "optional_declared_identifier", field: effect.field };
    case "proposed_subject_id":
      return { kind: "exact", value: orNull(at(ctx.ids, effect.subject)) };
    case "subject_field":
      return { kind: "exact", value: orNull(at(at(from(effect.source), effect.subject), effect.field)) };
    case "evidence_fact":
      return { kind: "exact",
        value: orNull(at(at(ctx.facts, effect.evidence_kind), effect.fact)) };
    case "supplied_evidence_kind": {
      const kinds = Object.keys(ctx.facts);
      return kinds.length === 0
        ? { kind: "any_of", values: [] } : { kind: "exact", value: kinds[0] };
    }
    case "prior_plus": {
      const observed = at(at(ctx.prior, effect.subject), effect.field);
      if (typeof observed !== "number") return { kind: "any_of", values: [] };
      return { kind: "exact", value: observed + effect.add };
    }
    case "prior_plus_conditional": {
      const observed = at(at(ctx.prior, effect.subject), effect.field);
      if (typeof observed !== "number") return { kind: "any_of", values: [] };
      const when = effect.when;
      const seen = at(at(from(when.source), when.subject), when.field);
      return { kind: "exact",
        value: seen === when.equals ? observed + effect.add : observed };
    }
    case "case_on_field": {
      const observed = at(at(from(effect.source), effect.subject), effect.field);
      for (const [value, branch] of Object.entries(effect.cases)) {
        if (observed === value) return expectedValue(branch, ctx);
      }
      return expectedValue(effect.default, ctx);
    }
    case "case_on_evidence": {
      for (const kind of Object.keys(ctx.facts)) {
        if (effect.cases[kind] !== undefined) return expectedValue(effect.cases[kind], ctx);
      }
      return { kind: "any_of", values: [] };
    }
    case "one_of": {
      const values = effect.values.filter(candidate => {
        for (const guard of effect.guards ?? []) {
          if (guard.value !== candidate) continue;
          const requires = guard.requires;
          const observed = at(at(from(requires.source), requires.subject), requires.field);
          if (Object.prototype.hasOwnProperty.call(requires, "equals") &&
              observed !== requires.equals) return false;
          if (Object.prototype.hasOwnProperty.call(requires, "at_least") &&
              (typeof observed !== "number" || observed < requires.at_least)) return false;
        }
        if (effect.differs_from_prior === true) {
          const before = at(at(ctx.prior, effect.prior_subject), effect.prior_field);
          if (before === candidate) return false;
        }
        return true;
      });
      return { kind: "any_of", values };
    }
    default:
      throw new Error(`the admission map uses an effect op this suite cannot read: ${effect.op}`);
  }
}

/** The writer's comparison loop, transcribed. Returns every complaint it makes. */
function complaintsAgainstPolicy(policy, transition_id, proposed_state, ctx) {
  const rules = policy.transitions[transition_id].subjects;
  const complaints = [];
  for (const kind of Object.keys(rules)) {
    if (proposed_state[kind] === undefined) {
      complaints.push(`j102_required_subject_not_proposed: ${kind}`);
    }
  }
  for (const kind of Object.keys(proposed_state)) {
    if (rules[kind] === undefined) {
      complaints.push(`j102_subject_kind_not_written_by_transition: ${kind}`);
    }
  }
  for (const [kind, rule] of Object.entries(rules)) {
    const state = proposed_state[kind];
    if (state === undefined) continue;
    if (rule.mode === "create") {
      for (const field of new Set([...Object.keys(rule.creation_shape), ...Object.keys(state)])) {
        if (rule.creation_shape[field] === undefined || at(state, field) === MISSING) {
          complaints.push(`j102_created_subject_shape_mismatch: ${kind}.${field}`);
          continue;
        }
        const expected = expectedValue(rule.creation_shape[field], ctx);
        const actual = at(state, field);
        if (expected.kind === "exact" && actual !== expected.value) {
          complaints.push(
            `j102_created_subject_field_not_canonical: ${kind}.${field} is ${show(actual)}, not ${show(expected.value)}`);
        } else if (expected.kind === "any_of" && !expected.values.includes(actual)) {
          complaints.push(`j102_created_subject_field_not_canonical: ${kind}.${field} is ${show(actual)}`);
        }
      }
      continue;
    }
    const prior = ctx.prior[kind];
    for (const condition of rule.prior_conditions ?? []) {
      const observed = at(prior, condition.field);
      if (condition.must_be_null === true && observed !== null) {
        complaints.push(`j102_prior_condition_not_met: ${kind}.${condition.field}`);
      }
      if (Object.prototype.hasOwnProperty.call(condition, "equals") &&
          observed !== condition.equals) {
        complaints.push(`j102_prior_condition_not_met: ${kind}.${condition.field}`);
      }
      if (condition.in !== undefined && !condition.in.includes(observed)) {
        complaints.push(`j102_prior_condition_not_met: ${kind}.${condition.field}`);
      }
      if (condition.not_in !== undefined && condition.not_in.includes(observed)) {
        complaints.push(`j102_prior_condition_not_met: ${kind}.${condition.field}`);
      }
      if (condition.equals_subject_id !== undefined &&
          observed !== orNull(at(ctx.ids, condition.equals_subject_id))) {
        complaints.push(`j102_prior_condition_not_met: ${kind}.${condition.field}`);
      }
      if (condition.null_or_matches !== undefined) {
        const expected = expectedValue(condition.null_or_matches, ctx);
        if (observed !== null && observed !== expected.value) {
          complaints.push(`j102_prior_condition_not_met: ${kind}.${condition.field}`);
        }
      }
    }
    for (const field of new Set([...Object.keys(prior ?? {}), ...Object.keys(state)])) {
      const effect = rule.effects[field];
      const actual = at(state, field);
      if (effect === undefined) {
        if (actual !== at(prior, field)) {
          complaints.push(`j102_field_not_movable_by_transition: ${kind}.${field}`);
        }
        continue;
      }
      const expected = expectedValue(effect, ctx);
      if (expected.kind === "unbound") continue;
      if (expected.kind === "exact" && actual !== expected.value) {
        complaints.push(
          `j102_transition_effect_not_canonical: ${kind}.${field} is ${show(actual)}, not ${show(expected.value)}`);
      } else if (expected.kind === "any_of" && !expected.values.includes(actual)) {
        complaints.push(
          `j102_transition_effect_not_canonical: ${kind}.${field} is ${show(actual)}, not one of ${JSON.stringify(expected.values)}`);
      }
    }
    for (const field of Object.keys(rule.effects)) {
      if (at(state, field) === MISSING) {
        complaints.push(`j102_transition_effect_missing: ${kind}.${field}`);
      }
    }
  }
  return complaints;
}

/**
 * The writer's event checks, transcribed. THREE questions, not one:
 *
 *   1. the SET — which kinds, on which subjects, exactly and pairwise;
 *   2. the PAYLOAD — every non-identity key of the nested event, against the
 *      map's `detail` template, evaluated by the same interpreter the state
 *      targets use. This is the class root named: `event_kind` and `subject`
 *      can both be right while `closing_date`, `cancellation_reason`,
 *      `evidence_reference` or an axis value inside the event is a lie;
 *   3. the EVIDENCE REFERENCES the stored event record cites, against the
 *      canonical set the recheck re-read.
 *
 * `events` is the kernel's own event array. `references` is what the store
 * stamps on every one of them; a caller passing something else is exercising
 * question 3.
 */
const EVENT_IDENTITY_KEYS = ["schema_version", "event_kind", "subject_kind", "subject_id"];

function eventComplaints(policy, transition_id, events, ctx, references = ctx.references) {
  const specs = policy.transitions[transition_id].events;
  const required = specs.map(spec => ({
    event_kind: spec.event_kind,
    subject_kind: spec.subject,
    subject_id: orNull(at(ctx.ids, spec.subject)),
  }));
  const supplied = events.map(event => ({
    event_kind: event.event_kind, subject_kind: event.subject_kind, subject_id: event.subject_id,
  }));
  const complaints = [];
  if (supplied.length !== required.length) {
    complaints.push(`j102_event_set_mismatch: ${supplied.length} for ${required.length}`);
  }
  const remaining = [...supplied];
  for (const want of required) {
    const index = remaining.findIndex(have =>
      have.event_kind === want.event_kind && have.subject_kind === want.subject_kind &&
      have.subject_id === want.subject_id);
    if (index === -1) {
      complaints.push(`j102_event_missing_or_wrong: ${want.event_kind} on ${want.subject_kind}`);
      continue;
    }
    remaining.splice(index, 1);
  }
  for (const extra of remaining) {
    complaints.push(`j102_event_not_produced_by_transition: ${extra.event_kind}`);
  }

  for (const event of events) {
    const spec = specs.find(entry =>
      entry.event_kind === event.event_kind && entry.subject === event.subject_kind);
    // An event with no spec is already complained about above as a wrong or
    // extra one; there is nothing to compare its payload to.
    if (spec === undefined) continue;
    if (event.schema_version !== V5_J102_EVENT_SCHEMA_VERSION) {
      complaints.push(
        `j102_event_payload_schema_version_mismatch: ${event.event_kind} is ${show(event.schema_version)}`);
    }
    const detail = spec.detail ?? {};
    for (const field of new Set([...Object.keys(detail), ...Object.keys(event)])) {
      if (EVENT_IDENTITY_KEYS.includes(field)) continue;
      if (detail[field] === undefined) {
        complaints.push(
          `j102_event_detail_not_produced_by_transition: ${event.event_kind}.${field}`);
        continue;
      }
      if (at(event, field) === MISSING) {
        complaints.push(`j102_event_detail_missing: ${event.event_kind}.${field}`);
        continue;
      }
      const expected = expectedValue(detail[field], ctx);
      const actual = at(event, field);
      if (expected.kind === "unbound") continue;
      if (expected.kind === "exact" && actual !== expected.value) {
        complaints.push(
          `j102_event_detail_not_canonical: ${event.event_kind}.${field} is ${show(actual)}, not ${show(expected.value)}`);
      } else if (expected.kind === "any_of" && !expected.values.includes(actual)) {
        complaints.push(
          `j102_event_detail_not_canonical: ${event.event_kind}.${field} is ${show(actual)}`);
      }
    }
  }

  // The evidence references the stored event record carries, against what the
  // recheck actually re-read. Absent `references` means the caller is only
  // exercising the set and payload halves.
  if (references !== undefined) {
    const canonical = new Map(ctx.canonical_references.map(ref => [ref.evidence_kind, ref]));
    for (const event of events) {
      if (!Array.isArray(references)) {
        complaints.push(`j102_event_evidence_references_missing: ${event.event_kind}`);
        continue;
      }
      if (references.length !== ctx.canonical_references.length) {
        complaints.push(
          `j102_event_evidence_references_not_rechecked: ${event.event_kind} cites ${references.length} for ${ctx.canonical_references.length}`);
      }
      const seen = new Set();
      for (const ref of references) {
        const want = canonical.get(ref?.evidence_kind);
        if (want === undefined) {
          complaints.push(
            `j102_event_evidence_reference_not_rechecked: ${event.event_kind} cites ${show(ref?.evidence_kind)}`);
          continue;
        }
        if (seen.has(ref.evidence_kind)) {
          complaints.push(
            `j102_event_evidence_reference_duplicated: ${event.event_kind} cites ${ref.evidence_kind} twice`);
          continue;
        }
        seen.add(ref.evidence_kind);
        // Whole-object equality, exactly as the SQL compares jsonb: an extra
        // key, a missing key or a changed value is not the element the recheck
        // built.
        if (canonicalJson(ref) !== canonicalJson(want)) {
          complaints.push(
            `j102_event_evidence_reference_not_rechecked: ${event.event_kind} cites ${JSON.stringify(ref)}, re-read as ${JSON.stringify(want)}`);
        }
      }
    }
  }
  return complaints;
}

// --- the walks the kernel is actually run through --------------------------

// The kernel's own actor shape, which is not the store's caller context: this is
// what evaluateLifecycleTransition takes, server-derived class and all.
const KERNEL_PARTNER = Object.freeze({
  slug: "joe", human: true, authorization_class: "verified_partner",
  derived_by: "authenticated_handler_context",
});
const KERNEL_AGENT = Object.freeze({
  slug: "cre-agent", human: false, authorization_class: "sponsored_agent",
  derived_by: "server_established_transaction_context",
});
const NOW = "2026-09-09T12:00:00.000Z";

const provenance = reader => ({
  loaded_by: V5_J102_EVIDENCE_LOADER, reader, loaded_at: T.mid,
  integrity: V5_J102_EVIDENCE_INTEGRITY,
});

const kernelDocument = (evidence_kind, bound, over = {}) => ({
  evidence_kind, source: "f01_document", reference: "j102-doc-1",
  subject_binding: { ...bound, bound_by: "stored_evidence_subject_link", binding_digest: D(21) },
  document: {
    document_id: "j102-doc-1", document_class: "agreement", version_no: 3, content_digest: D(22),
    preparation_state: "prepared", delivery_state: "delivered",
    signature_state: "fully_executed", validity_state: "effective", version_state: "current",
    effective_from: null, effective_to: null, ...over,
  },
  provenance: provenance("ops.f01_read.document"),
});

const kernelArtifact = (evidence_kind, bound) => ({
  evidence_kind, source: "f01_corporate_artifact", reference: D(23),
  subject_binding: { ...bound, bound_by: "stored_evidence_subject_link", binding_digest: D(24) },
  artifact: {
    artifact_digest: D(23), content_digest: D(23), source_system: "counterparty-mail",
    evidence_class: "counterparty_acceptance", observed_at: T.mid,
  },
  provenance: provenance("ops.f01_stored_artifact"),
});

const kernelRecord = (evidence_kind, record_kind, bound, over = {}) => ({
  evidence_kind, source: "first_party_record", reference: "j102-record-1",
  subject_binding: { ...bound, bound_by: "first_party_record", binding_digest: D(25) },
  record: {
    record_kind, record_id: "j102-record-1", content_digest: D(25),
    recorded_by: "joe", recorded_by_authorization_class: "verified_partner",
    recorded_at: T.mid, reason: null, detail: null, closing_date: null,
    supporting_document_id: null, ...over,
  },
  provenance: provenance("ops.j102_first_party_record"),
});

/**
 * The facts the SQL recheck returns for one evidence item, from the same row.
 *
 * `reference` is built the way ops.j102_recheck_evidence builds it: off the
 * READER's own answer for that source — F01's document_id, the stored artifact's
 * digest, the record_id on the committed first-party row — and never off the
 * selector the caller wrote. The assertion below that this equals the kernel
 * evidence item's own `reference` is what ties the two together: the store sets
 * that field from the same three identifiers, so a map that predicts the
 * kernel's `evidence_reference` from these facts predicts what the SQL will
 * compare the history against.
 */
const referenceFor = item =>
  item.source === "first_party_record" ? item.record.record_id
    : item.source === "f01_document" ? item.document.document_id
      : item.artifact.artifact_digest;

const factsFor = evidence => {
  const facts = {};
  for (const item of evidence) {
    // The store's own `reference` on the loaded evidence IS the reader's
    // identifier for that pin. If that ever stops being true, the SQL's
    // recomputed reference and the kernel's event field diverge, and this is
    // where it surfaces rather than in an unexecuted PL/pgSQL comparison.
    assert.equal(item.reference, referenceFor(item),
      `${item.evidence_kind}: the evidence reference must be the identifier its own reader returns`);
    if (item.source === "first_party_record") {
      facts[item.evidence_kind] = {
        reference: referenceFor(item),
        record_kind: item.record.record_kind, record_id: item.record.record_id,
        closing_date: item.record.closing_date, reason: item.record.reason,
        subject_kind: item.subject_binding.subject_kind,
        subject_id: item.subject_binding.subject_id,
      };
    } else if (item.source === "f01_document") {
      facts[item.evidence_kind] = {
        reference: referenceFor(item),
        document_id: item.document.document_id, version_no: item.document.version_no,
        content_digest: item.document.content_digest,
      };
    } else {
      facts[item.evidence_kind] = {
        reference: referenceFor(item),
        artifact_digest: item.artifact.artifact_digest,
      };
    }
  }
  return facts;
};

/**
 * ops.j102_recheck_evidence's canonical reference set, transcribed: exactly the
 * triple the writer rebuilds from what it re-read, and exactly what the store
 * stamps into every event's `evidence_references`.
 */
const canonicalReferences = evidence =>
  evidence.map(item => ({
    evidence_kind: item.evidence_kind, source: item.source, reference: referenceFor(item),
  }));

const REL = { subject_kind: "relationship", subject_id: "j102-rel-1",
  relationship_state: "prospect", active_engagement_count: 0, party_id: null };
const CLIENT = { ...REL, relationship_state: "client", active_engagement_count: 1 };
const ENG = { subject_kind: "engagement", subject_id: "j102-eng-1",
  relationship_id: "j102-rel-1", engagement_state: "active",
  representation_basis: "signed_engagement_letter", effective_from: null, effective_to: null };
const ASG = (over = {}) => ({ subject_kind: "assignment", subject_id: "j102-asg-1",
  engagement_id: "j102-eng-1", assignment_phase: "research", open_negotiation_count: 0,
  selected_property_id: null, active_lease_draft_target_id: null, pending_deal_id: null,
  multi_target_exception_ref: null, ...over });
const NEG = (over = {}) => ({ subject_kind: "property_negotiation", subject_id: "j102-neg-1",
  assignment_id: "j102-asg-1", property_id: "j102-prop-1", negotiation_state: "loi_drafted",
  ...over });
const DEAL = (over = {}) => ({ subject_kind: "deal", subject_id: "j102-deal-1",
  assignment_id: "j102-asg-1", property_id: "j102-prop-1", instrument_kind: "lease",
  deal_state: "pending", execution_state: "unexecuted", diligence_state: "not_applicable",
  closing_state: "not_reached", commission_agreement_state: "absent",
  invoice_state: "not_invoiced", payment_state: "unpaid", completion_state: "open",
  cancellation_reason: null, closing_date: null, ...over });

const relationshipBinding = { subject_kind: "relationship", subject_id: "j102-rel-1" };
const assignmentBinding = { subject_kind: "assignment", subject_id: "j102-asg-1" };
const negotiationBinding = { subject_kind: "property_negotiation", subject_id: "j102-neg-1" };
const dealBinding = { subject_kind: "deal", subject_id: "j102-deal-1" };

/**
 * One walk per transition the kernel can be driven through here.
 *
 * `approved_representation_equivalent` has no walk on purpose: no producer
 * writes the typed approval it rests on, ops.j102_typed_approval is granted to
 * nobody and always raises, and the store refuses the path with the missing fact
 * named. Manufacturing one here to widen the table would be inventing exactly
 * the authority that record is supposed to carry.
 */
const WALKS = [
  { transition_id: "establish-client-and-engagement", actor: KERNEL_PARTNER,
    subject: REL, related: {}, declared: { new_subject_id: "j102-eng-1" },
    evidence: [kernelDocument("signed_engagement_letter", relationshipBinding)],
    prior: { relationship: REL }, context: {} },
  { transition_id: "open-assignment", actor: KERNEL_AGENT,
    subject: ASG(), related: { engagement: ENG, relationship: CLIENT },
    declared: { mandate_scope: "search" },
    evidence: [kernelRecord("search_initiation", "assignment_mandate", assignmentBinding,
      { recorded_by_authorization_class: "sponsored_agent" })],
    prior: { assignment: ASG() }, context: { engagement: ENG, relationship: CLIENT } },
  { transition_id: "record-loi-submission", actor: KERNEL_AGENT,
    subject: NEG(), related: { assignment: ASG({ assignment_phase: "search" }) }, declared: {},
    evidence: [kernelDocument("submitted_loi", negotiationBinding)],
    prior: { property_negotiation: NEG(), assignment: ASG({ assignment_phase: "search" }) },
    context: {} },
  { transition_id: "record-loi-acceptance", actor: KERNEL_AGENT,
    subject: NEG({ negotiation_state: "loi_submitted" }), related: {}, declared: {},
    evidence: [kernelArtifact("counterparty_loi_acceptance", negotiationBinding)],
    prior: { property_negotiation: NEG({ negotiation_state: "loi_submitted" }) }, context: {} },
  { transition_id: "commit-winning-property", actor: KERNEL_PARTNER,
    subject: ASG({ assignment_phase: "negotiation", open_negotiation_count: 2 }),
    related: { property_negotiation: NEG({ negotiation_state: "loi_accepted" }) },
    declared: { instrument_kind: "lease", new_deal_id: "j102-deal-1" },
    evidence: [kernelRecord("winner_selection_commitment", "winning_property_commitment",
      assignmentBinding)],
    prior: { assignment: ASG({ assignment_phase: "negotiation", open_negotiation_count: 2 }),
      property_negotiation: NEG({ negotiation_state: "loi_accepted" }) },
    context: {} },
  { transition_id: "record-lease-execution", actor: KERNEL_AGENT,
    subject: DEAL(), related: {}, declared: {},
    evidence: [kernelDocument("executed_lease", dealBinding)],
    prior: { deal: DEAL() }, context: {} },
  { transition_id: "record-purchase-contract-execution", actor: KERNEL_AGENT,
    subject: DEAL({ instrument_kind: "purchase" }), related: {}, declared: {},
    evidence: [kernelDocument("signed_purchase_contract", dealBinding)],
    prior: { deal: DEAL({ instrument_kind: "purchase" }) }, context: {} },
  { transition_id: "record-diligence-outcome", actor: KERNEL_AGENT,
    subject: DEAL({ instrument_kind: "purchase", execution_state: "executed",
      diligence_state: "in_progress" }),
    related: {}, declared: { diligence_result: "satisfied" },
    evidence: [kernelRecord("diligence_outcome", "diligence_outcome", dealBinding,
      { recorded_by_authorization_class: "sponsored_agent" })],
    prior: { deal: DEAL({ instrument_kind: "purchase", execution_state: "executed",
      diligence_state: "in_progress" }) }, context: {} },
  { transition_id: "record-deal-closing", actor: KERNEL_PARTNER,
    subject: DEAL({ execution_state: "executed", diligence_state: "satisfied" }),
    related: {}, declared: {},
    evidence: [kernelRecord("final_closing_settlement", "closing_settlement", dealBinding,
      { closing_date: T.early })],
    prior: { deal: DEAL({ execution_state: "executed", diligence_state: "satisfied" }) },
    context: {} },
  { transition_id: "cancel-pending-deal", actor: KERNEL_PARTNER,
    subject: DEAL(),
    related: { assignment: ASG({ assignment_phase: "committed", open_negotiation_count: 1,
      selected_property_id: "j102-prop-1", active_lease_draft_target_id: "j102-prop-1",
      pending_deal_id: "j102-deal-1" }) },
    declared: { return_phase: "negotiation" },
    evidence: [kernelRecord("deal_failure_record", "deal_failure", dealBinding,
      { reason: "the counterparty withdrew before lease signature" })],
    prior: { deal: DEAL(), assignment: ASG({ assignment_phase: "committed",
      open_negotiation_count: 1, selected_property_id: "j102-prop-1",
      active_lease_draft_target_id: "j102-prop-1", pending_deal_id: "j102-deal-1" }) },
    context: {} },
  { transition_id: "record-commission-agreement", actor: KERNEL_AGENT,
    subject: DEAL(), related: {}, declared: {},
    evidence: [kernelDocument("commission_agreement", dealBinding)],
    prior: { deal: DEAL() }, context: {} },
  { transition_id: "record-invoice-issued", actor: KERNEL_AGENT,
    subject: DEAL(), related: {}, declared: {},
    evidence: [kernelRecord("invoice_issued", "invoice", dealBinding,
      { recorded_by_authorization_class: "sponsored_agent" })],
    prior: { deal: DEAL() }, context: {} },
  { transition_id: "record-payment", actor: KERNEL_AGENT,
    subject: DEAL({ payment_state: "partially_paid" }), related: {},
    declared: { payment_level: "paid" },
    evidence: [kernelRecord("payment_received", "payment", dealBinding,
      { recorded_by_authorization_class: "sponsored_agent" })],
    prior: { deal: DEAL({ payment_state: "partially_paid" }) }, context: {} },
  { transition_id: "record-completion", actor: KERNEL_AGENT,
    subject: DEAL(), related: {}, declared: {},
    evidence: [kernelRecord("completion_recorded", "completion", dealBinding,
      { recorded_by_authorization_class: "sponsored_agent" })],
    prior: { deal: DEAL() }, context: {} },
];

function runWalk(walk) {
  const answer = evaluateLifecycleTransition({
    tenant: ORGANIZATION_TENANT_ID,
    transition_id: walk.transition_id,
    subject: walk.subject,
    related: walk.related,
    evidence: walk.evidence,
    actor: walk.actor,
    declared: walk.declared,
    now: NOW,
  });
  assert.equal(answer.decision, "allow",
    `${walk.transition_id} must be an ALLOW for the parity check to mean anything (${answer.reason_id})`);
  const ids = Object.fromEntries(
    Object.entries(answer.proposed_state).map(([kind, state]) => [kind, state.subject_id]));
  const canonical_references = canonicalReferences(walk.evidence);
  const ctx = {
    prior: walk.prior, proposed: answer.proposed_state, context: walk.context,
    ids, facts: factsFor(walk.evidence),
    canonical_references,
    // What the store stamps on every event of this call — the same array, which
    // is why the writer requires it identically on each of them.
    references: canonical_references,
  };
  return { answer, ctx };
}

test("SQL parity: every transition the map declares has a subject rule set and an event set", () => {
  const policy = admissionPolicy();
  assert.equal(policy.subject_creation, "coupled_only_never_the_primary_subject");
  for (const id of V5_J102_TRANSITION_IDS) {
    const contract = v5J102TransitionContract(id);
    const admitted = policy.transitions[id];
    // The subject rules cover EXACTLY the subjects `writes` covers, which the
    // test above has already tied to the kernel's coupled facts.
    assert.deepEqual(Object.keys(admitted.subjects).sort(), Object.keys(admitted.writes).sort(),
      `${id} declares a rule for every subject it writes`);
    assert.equal(admitted.subjects[contract.subject_kind].role, "primary",
      `${id}'s primary subject is the kernel's subject_kind`);
    assert.equal(admitted.subjects[contract.subject_kind].mode, "update",
      `${id} advances a ${contract.subject_kind} that already exists; the kernel never creates one`);
    for (const [kind, rule] of Object.entries(admitted.subjects)) {
      if (kind === contract.subject_kind) continue;
      assert.equal(rule.role, "coupled");
      assert.ok(["update", "create"].includes(rule.mode));
    }
    // Every field `writes` names is a field with a declared TARGET, and every
    // field with a target is a field `writes` names. One is the permission, the
    // other is the value; neither may exist without the other.
    for (const [kind, fields] of Object.entries(admitted.writes)) {
      const rule = admitted.subjects[kind];
      if (rule.mode === "create") continue;
      assert.deepEqual(Object.keys(rule.effects).sort(), [...fields].sort(),
        `${id} moves exactly the ${kind} fields it declares, to declared values`);
    }
    assert.ok(admitted.events.length >= 1, `${id} appends at least one event`);
    // Every event declares its whole nested payload, and no two events of one
    // transition share a (kind, subject) — which is what lets the writer find the
    // spec for a supplied event after the set check has matched it.
    const seen = new Set();
    for (const spec of admitted.events) {
      assert.equal(typeof spec.event_kind, "string",
        `${id} names its event kinds literally; nothing here is derived`);
      assert.ok(spec.detail !== undefined && spec.detail !== null &&
        typeof spec.detail === "object" && !Array.isArray(spec.detail),
        `${id}'s ${spec.event_kind} declares which nested facts it carries, not only its kind`);
      const key = `${spec.event_kind}:${spec.subject}`;
      assert.equal(seen.has(key), false, `${id} appends ${key} once`);
      seen.add(key);
      assert.ok(Object.prototype.hasOwnProperty.call(admitted.subjects, spec.subject),
        `${id}'s ${spec.event_kind} names a subject this transition advances`);
    }
  }
  // The two created subjects, and there are exactly two.
  const creating = V5_J102_TRANSITION_IDS.flatMap(id =>
    Object.entries(policy.transitions[id].subjects)
      .filter(([, rule]) => rule.mode === "create")
      .map(([kind]) => `${id}:${kind}`)).sort();
  assert.deepEqual(creating, [
    "commit-winning-property:deal",
    "establish-client-and-engagement:engagement",
  ]);
});

test("SQL parity: a created subject's shape IS the kernel's declared shape for that kind", () => {
  const policy = admissionPolicy();
  // Read off the kernel rather than restated: assertLifecycleSubject returns
  // every declared key for a kind, null where absent, so its output keys are the
  // canonical shape a creation must carry exactly.
  const canonicalKeys = subject => Object.keys(assertLifecycleSubject(subject)).sort();
  assert.deepEqual(
    Object.keys(policy.transitions["establish-client-and-engagement"].subjects.engagement
      .creation_shape).sort(),
    canonicalKeys(ENG));
  assert.deepEqual(
    Object.keys(policy.transitions["commit-winning-property"].subjects.deal.creation_shape).sort(),
    canonicalKeys(DEAL()));
});

test("SQL parity: the map PREDICTS the kernel's proposed state and events, walk by walk", () => {
  const policy = admissionPolicy();
  const covered = new Set();
  for (const walk of WALKS) {
    const { answer, ctx } = runWalk(walk);
    covered.add(walk.transition_id);
    assert.deepEqual(complaintsAgainstPolicy(policy, walk.transition_id, answer.proposed_state, ctx),
      [], `${walk.transition_id}: the admission map admits the kernel's own answer`);
    assert.deepEqual(eventComplaints(policy, walk.transition_id, answer.events, ctx),
      [], `${walk.transition_id}: the admission map admits the kernel's own events`);
    // And the coupled subject set is the same set, in both directions.
    assert.deepEqual(Object.keys(answer.proposed_state).sort(),
      Object.keys(policy.transitions[walk.transition_id].subjects).sort(),
      `${walk.transition_id}: the required subject set is the set the kernel writes`);
    // WHICH SUBJECTS THE KERNEL CREATES, read off its own answer: a proposed kind
    // that was not among the loaded ones is a creation. The map's create-mode
    // subjects must be exactly those, and so must the store's description of
    // them -- so neither can drift from the evaluator.
    const created = Object.keys(answer.proposed_state)
      .filter(kind => walk.prior[kind] === undefined).sort();
    assert.deepEqual(created,
      Object.entries(policy.transitions[walk.transition_id].subjects)
        .filter(([, rule]) => rule.mode === "create").map(([kind]) => kind).sort(),
      `${walk.transition_id}: the map creates exactly what the kernel creates`);
    const operation = policy.transitions[walk.transition_id].operations[0];
    const registration = v5J102ToolRegistrations().find(entry => entry.name === operation);
    for (const kind of created) {
      assert.ok(registration.creates_coupled_subject_kinds.includes(kind),
        `${operation} reports that it creates a ${kind}`);
    }
    assert.equal(registration.requires_existing_primary_subject, true,
      `${operation} advances a subject that must already exist`);
  }
  // EVERY transition in the table is walked. A map checked against twelve of
  // fourteen would leave the two unchecked ones free to say anything.
  const unwalked = V5_J102_TRANSITION_IDS.filter(id => !covered.has(id));
  assert.deepEqual(unwalked, [],
    "every transition in the table is walked at least once");
});

test("SQL parity: the required context IS the context the kernel refuses without", () => {
  const policy = admissionPolicy();
  const declared = policy.transitions["open-assignment"].required_context;
  assert.deepEqual(declared.map(entry => `${entry.subject}.${entry.conditions[0].field}`),
    ["engagement.engagement_state", "relationship.relationship_state"]);
  assert.deepEqual(declared.map(entry => entry.conditions[0].equals), ["active", "client"]);

  // AND THE KERNEL REALLY DOES REFUSE WITHOUT THEM, which is what makes the two
  // entries a transcription rather than a policy this map invented. The SQL reads
  // both subjects out of the compare-and-swap operand set, so they are locked and
  // digest-checked; a direct caller cannot open an assignment under a lapsed
  // engagement by declining to mention it.
  const walk = WALKS.find(entry => entry.transition_id === "open-assignment");
  const refusal = (related, reason) => {
    const answer = evaluateLifecycleTransition({
      tenant: ORGANIZATION_TENANT_ID, transition_id: "open-assignment",
      subject: walk.subject, related, evidence: walk.evidence, actor: walk.actor,
      declared: walk.declared, now: NOW,
    });
    assert.equal(answer.decision, "refuse");
    assert.equal(answer.reason_id, reason);
  };
  refusal({ engagement: { ...ENG, engagement_state: "expired" }, relationship: CLIENT },
    "engagement_not_active");
  refusal({ engagement: ENG, relationship: { ...CLIENT, relationship_state: "prospect" } },
    "client_status_required");
  refusal({ relationship: CLIENT }, "engagement_not_loaded");
});

test("ROOT BLOCKER 2: the map REFUSES a permitted field carrying an unpermitted value", () => {
  const policy = admissionPolicy();
  const walkFor = id => WALKS.find(walk => walk.transition_id === id);
  const mutate = (id, change) => {
    const { answer, ctx } = runWalk(walkFor(id));
    const proposed = Object.fromEntries(
      Object.entries(answer.proposed_state).map(([kind, state]) => [kind, { ...state }]));
    change(proposed);
    return complaintsAgainstPolicy(policy, id, proposed, ctx);
  };
  const complains = (id, change, fragment) => {
    const complaints = mutate(id, change);
    assert.ok(complaints.some(complaint => complaint.includes(fragment)),
      `${id} must refuse this payload with ${fragment}; it said ${JSON.stringify(complaints)}`);
  };

  // THE ROUTINE CALL THAT COMMITS AN ASSIGNMENT. `assignment_phase` is a field
  // open-assignment moves; `committed` is not a value it moves it to.
  complains("open-assignment", p => { p.assignment.assignment_phase = "committed"; },
    "j102_transition_effect_not_canonical");
  // The same field, DELETED. A key that is not there is not the value either.
  complains("open-assignment", p => { delete p.assignment.assignment_phase; },
    "j102_transition_effect_missing");
  // A pending deal id invented on a permitted subject through an unpermitted field.
  complains("open-assignment", p => { p.assignment.pending_deal_id = "j102-deal-9"; },
    "j102_field_not_movable_by_transition");
  // A counter set to an arbitrary number rather than to its derived successor.
  complains("record-loi-submission", p => { p.assignment.open_negotiation_count = 40; },
    "j102_transition_effect_not_canonical");
  // THE MISSING COUPLED SUBJECT: the deal is cancelled and the assignment is not
  // returned, which is the subset of a coupled write Q082 refuses.
  complains("cancel-pending-deal", p => { delete p.assignment; },
    "j102_required_subject_not_proposed");
  // The cleared references, kept instead of cleared.
  complains("cancel-pending-deal", p => { p.assignment.pending_deal_id = "j102-deal-1"; },
    "j102_transition_effect_not_canonical");
  complains("cancel-pending-deal", p => { p.assignment.selected_property_id = "j102-prop-1"; },
    "j102_transition_effect_not_canonical");
  // A reason that is not the reason the stored failure record gives.
  complains("cancel-pending-deal", p => { p.deal.cancellation_reason = "a reason nobody recorded"; },
    "j102_transition_effect_not_canonical");
  // A closing date that is not the settlement's date -- BLOCK-2's original
  // consequence, now refused on the VALUE as well as on the binding.
  complains("record-deal-closing", p => { p.deal.closing_date = "2027-01-01T00:00:00.000Z"; },
    "j102_transition_effect_not_canonical");
  // A deal born closed, a deal born under another assignment, a deal missing an
  // axis, and a deal carrying a field the kernel never creates.
  complains("commit-winning-property", p => { p.deal.deal_state = "closed"; },
    "j102_created_subject_field_not_canonical");
  complains("commit-winning-property", p => { p.deal.assignment_id = "j102-asg-9"; },
    "j102_created_subject_field_not_canonical");
  complains("commit-winning-property", p => { delete p.deal.invoice_state; },
    "j102_created_subject_shape_mismatch");
  complains("commit-winning-property", p => { p.deal.smuggled_field = "anything"; },
    "j102_created_subject_shape_mismatch");
  // The assignment pointing at a deal this call did not create.
  complains("commit-winning-property", p => { p.assignment.pending_deal_id = "j102-deal-9"; },
    "j102_transition_effect_not_canonical");
  // An unrelated subject travelling beside a legitimate request.
  complains("record-completion", p => { p.relationship = { ...CLIENT }; },
    "j102_subject_kind_not_written_by_transition");
  // Q072's payment level that would not change the state: prior is
  // partially_paid, so `partially_paid` is not among the values on THIS path.
  complains("record-payment", p => { p.deal.payment_state = "partially_paid"; },
    "j102_transition_effect_not_canonical");
});

test("ROOT BLOCKER 2: the map REFUSES a wrong, missing or extra event", () => {
  const policy = admissionPolicy();
  const walkFor = id => WALKS.find(walk => walk.transition_id === id);
  const complainsAbout = (id, change, fragment) => {
    const { answer, ctx } = runWalk(walkFor(id));
    const events = answer.events.map(event => ({ ...event }));
    const mutated = change(events) ?? events;
    const complaints = eventComplaints(policy, id, mutated, ctx);
    assert.ok(complaints.some(complaint => complaint.includes(fragment)),
      `${id} must refuse this history with ${fragment}; it said ${JSON.stringify(complaints)}`);
  };

  // THE EMPTY ARRAY. A transition that appends nothing is a state change nobody
  // can audit; "at least one event" would have caught this one and none of the
  // three below.
  complainsAbout("record-completion", () => [], "j102_event_set_mismatch");
  // ONE OF TWO. The deal is cancelled in the history and the assignment never
  // came back to the market.
  complainsAbout("cancel-pending-deal", events => events.slice(0, 1),
    "j102_event_set_mismatch");
  // A PLAUSIBLE WRONG KIND on a correctly bound subject -- the residual the
  // previous correction acknowledged and left open.
  complainsAbout("record-lease-execution", events => { events[0].event_kind = "deal_closed"; },
    "j102_event_missing_or_wrong");
  // AN EXTRA EVENT for a subject this call does advance, which the subject check
  // alone would not catch.
  complainsAbout("record-completion", events => [...events, { ...events[0] }],
    "j102_event_set_mismatch");
  // AN EVENT FOR AN UNRELATED SUBJECT.
  complainsAbout("record-completion",
    events => [{ ...events[0], subject_id: "j102-deal-c" }], "j102_event_missing_or_wrong");
  // THE KIND THE KERNEL DOES NOT SPELL. `payment_${level}` is record-payment's
  // REASON_ID; the event kind beside it is the constant `payment_recorded`, and
  // an event calling itself `payment_partially_paid` is a kind this transition
  // never appends rather than a derived kind that disagrees with its level.
  complainsAbout("record-payment",
    events => { events[0].event_kind = "payment_partially_paid"; }, "j102_event_missing_or_wrong");
});

test("ROOT residual: the map REFUSES a lying nested event fact, on a right kind and subject", () => {
  // The class the event-SET check cannot reach. Every payload below carries the
  // correct event kind, on the correct subject, in the correct number, beside a
  // state row the map admits — and says something inside the event that the
  // transition did not produce. The event is what a reviewer reads history from,
  // so a lie here is a lie on the review surface itself.
  const policy = admissionPolicy();
  const walkFor = id => WALKS.find(walk => walk.transition_id === id);
  const complainsAbout = (id, change, fragment) => {
    const { answer, ctx } = runWalk(walkFor(id));
    const events = answer.events.map(event => ({ ...event }));
    const mutated = change(events) ?? events;
    const complaints = eventComplaints(policy, id, mutated, ctx);
    assert.ok(complaints.some(complaint => complaint.includes(fragment)),
      `${id} must refuse this event payload with ${fragment}; it said ${JSON.stringify(complaints)}`);
  };

  // Q094's date, INSIDE the event. The state row closes on the settlement's own
  // date and the history says a different one; both are durable, and they
  // disagree about the single fact Q094 is about.
  complainsAbout("record-deal-closing",
    events => { events[0].closing_date = "2027-01-01T00:00:00.000Z"; },
    "j102_event_detail_not_canonical");
  // Q096's reason, INSIDE the event: a reason nobody recorded, on a cancellation
  // whose state row carries the recorded one.
  complainsAbout("cancel-pending-deal",
    events => { events[0].cancellation_reason = "a reason nobody recorded"; },
    "j102_event_detail_not_canonical");
  // THE EVIDENCE REFERENCE THE EVENT NAMES, pointed at another document.
  complainsAbout("record-lease-execution",
    events => { events[0].evidence_reference = "j102-doc-9"; },
    "j102_event_detail_not_canonical");
  // The axis value inside an axis event, disagreeing with the axis the same call
  // writes.
  complainsAbout("record-payment",
    events => { events[0].payment_state = "partially_paid"; },
    "j102_event_detail_not_canonical");
  complainsAbout("record-diligence-outcome",
    events => { events[0].diligence_state = "waived"; },
    "j102_event_detail_not_canonical");
  // The coupled event's own phase, on the assignment a cancelled deal returns.
  complainsAbout("cancel-pending-deal",
    events => { events[1].assignment_phase = "committed"; },
    "j102_event_detail_not_canonical");
  // Identity fields inside a coupled event: the property a winner selection
  // names, and the deal an assignment commitment points at.
  complainsAbout("commit-winning-property",
    events => { events[0].property_id = "j102-prop-9"; },
    "j102_event_detail_not_canonical");
  complainsAbout("commit-winning-property",
    events => { events[1].pending_deal_id = "j102-deal-9"; },
    "j102_event_detail_not_canonical");
  complainsAbout("open-assignment",
    events => { events[0].engagement_id = "j102-eng-9"; },
    "j102_event_detail_not_canonical");
  complainsAbout("record-loi-submission",
    events => { events[0].assignment_id = "j102-asg-9"; },
    "j102_event_detail_not_canonical");
  // A key the kernel never puts on an event, hashed into the history's bytes.
  complainsAbout("record-completion",
    events => { events[0].approved_by = "somebody"; },
    "j102_event_detail_not_produced_by_transition");
  // A key the kernel always puts there, deleted.
  complainsAbout("record-invoice-issued",
    events => { delete events[0].evidence_reference; },
    "j102_event_detail_missing");
  // The kernel's own event schema version, restamped.
  complainsAbout("record-completion",
    events => { events[0].schema_version = "doctorcre-v5-j102-lifecycle-event.v0"; },
    "j102_event_payload_schema_version_mismatch");

  // AND THE THREE COUPLED EVENTS THAT CARRY NO EVIDENCE REFERENCE AT ALL are the
  // kernel's own shape, not an omission to be filled in. A caller ADDING one is
  // adding a fact the kernel never wrote.
  for (const [id, index] of [["commit-winning-property", 0], ["commit-winning-property", 1],
    ["cancel-pending-deal", 1]]) {
    const { answer } = runWalk(walkFor(id));
    assert.ok(!Object.prototype.hasOwnProperty.call(answer.events[index], "evidence_reference"),
      `${id} event ${index} names no single evidence reference; the map must not invent one`);
  }
  complainsAbout("cancel-pending-deal",
    events => { events[1].evidence_reference = "j102-record-1"; },
    "j102_event_detail_not_produced_by_transition");
});

test("HIGH-6: the event's evidence_references must be the set the recheck re-read", () => {
  const policy = admissionPolicy();
  const walkFor = id => WALKS.find(walk => walk.transition_id === id);
  const cites = (id, references, fragment) => {
    const { answer, ctx } = runWalk(walkFor(id));
    const complaints = eventComplaints(policy, id, answer.events, ctx, references);
    assert.ok(complaints.some(complaint => complaint.includes(fragment)),
      `${id} must refuse this citation with ${fragment}; it said ${JSON.stringify(complaints)}`);
  };
  const truth = id => canonicalReferences(walkFor(id).evidence);

  // THE HONEST CASE FIRST, so the refusals below mean something: the array the
  // store actually stamps is admitted, on every event of every walk.
  for (const walk of WALKS) {
    const { answer, ctx } = runWalk(walk);
    assert.deepEqual(
      eventComplaints(policy, walk.transition_id, answer.events, ctx,
        canonicalReferences(walk.evidence)),
      [], `${walk.transition_id}: the references the store stamps are the ones re-read`);
  }

  // AN EMPTY ARRAY. The history says the transition rested on nothing.
  cites("record-lease-execution", [], "j102_event_evidence_references_not_rechecked");
  // A DOCUMENT THE TRANSITION NEVER RESTED ON, with an otherwise perfect call.
  cites("record-lease-execution",
    [{ ...truth("record-lease-execution")[0], reference: "j102-doc-9" }],
    "j102_event_evidence_reference_not_rechecked");
  // THE RIGHT REFERENCE UNDER THE WRONG KIND, and under the wrong source.
  cites("record-lease-execution",
    [{ ...truth("record-lease-execution")[0], evidence_kind: "signed_purchase_contract" }],
    "j102_event_evidence_reference_not_rechecked");
  cites("record-lease-execution",
    [{ ...truth("record-lease-execution")[0], source: "first_party_record" }],
    "j102_event_evidence_reference_not_rechecked");
  // THE SAME PIN TWICE: one record counted as two.
  cites("record-lease-execution",
    [truth("record-lease-execution")[0], { ...truth("record-lease-execution")[0] }],
    "j102_event_evidence_reference_duplicated");
  // AN EXTRA PIN riding beside the real one.
  cites("record-lease-execution",
    [...truth("record-lease-execution"),
      { evidence_kind: "invoice_issued", source: "first_party_record", reference: "j102-record-1" }],
    "j102_event_evidence_references_not_rechecked");
  // A FOURTH KEY. The canonical element is exactly the triple the database can
  // rebuild from what it read; anything else is not that element.
  cites("record-lease-execution",
    [{ ...truth("record-lease-execution")[0], subject_binding: { subject_kind: "deal" } }],
    "j102_event_evidence_reference_not_rechecked");
  // AND NOT AN ARRAY AT ALL.
  cites("record-lease-execution", "j102-doc-1", "j102_event_evidence_references_missing");
});

test("SQL parity: the effect vocabulary in the map is exactly the one the SQL implements", () => {
  // The interpreter above is a transcription, so the two could drift by one op
  // and nothing else would notice. This reads the ops the PL/pgSQL function
  // branches on, and the ops the map actually uses, and requires both to sit
  // inside what this suite can evaluate.
  const start = CANDIDATE_SQL.indexOf("create or replace function ops.j102_expected_value(");
  assert.ok(start > 0, "the candidate carries the effect interpreter");
  const source = CANDIDATE_SQL.slice(start,
    CANDIDATE_SQL.indexOf("comment on function ops.j102_expected_value"));
  const sqlOps = [...new Set([...source.matchAll(/v_op = '([a-z_]+)'/g)].map(m => m[1]))].sort();
  const readable = ["case_on_evidence", "case_on_field", "const", "declared_identifier",
    "evidence_fact", "one_of", "optional_declared_identifier", "prior_plus",
    "prior_plus_conditional", "proposed_subject_id",
    "subject_field", "supplied_evidence_kind", "unbound"];
  assert.deepEqual(sqlOps, readable,
    "the SQL interpreter implements exactly the ops this suite can evaluate");

  const policy = admissionPolicy();
  const used = new Set();
  const walk = node => {
    if (Array.isArray(node)) { node.forEach(walk); return; }
    if (node === null || typeof node !== "object") return;
    if (typeof node.op === "string") used.add(node.op);
    Object.values(node).forEach(walk);
  };
  walk(policy.transitions);
  // `declared_identifier` IS THE ONE OP NO TRANSITION MAY USE, and the transition
  // writer refuses the answer shape it produces. A transition target is always
  // derivable from the committed row, the coupled subjects or the re-read
  // evidence, so an unenumerable caller-chosen value there would be a hole.
  assert.equal(used.has("declared_identifier"), false,
    "no transition target is a caller-declared identifier");
  assert.ok(CANDIDATE_SQL.includes("j102_expected_value_kind_unsupported"),
    "and the transition writer fails closed on an answer shape it does not compare");
  const initializationOps = new Set();
  const walkInit = node => {
    if (Array.isArray(node)) { node.forEach(walkInit); return; }
    if (node === null || typeof node !== "object") return;
    if (typeof node.op === "string") initializationOps.add(node.op);
    Object.values(node).forEach(walkInit);
  };
  walkInit(policy.initializations);
  for (const op of [...used, ...initializationOps]) {
    assert.ok(readable.includes(op), `the map uses the effect op ${op}, which the SQL implements`);
  }
  // And the creation shapes reach for nothing that reads stored EVIDENCE, which
  // there is none of at creation time.
  for (const op of initializationOps) {
    assert.equal(["evidence_fact", "case_on_evidence", "supplied_evidence_kind"].includes(op),
      false, `an initialization computes ${op}, which would read evidence it cannot have`);
  }
  // The ONE unbound value in the whole map, named here so a second one cannot
  // arrive quietly. It sits on the approved-representation-equivalence branch,
  // which no manifest can reach because the typed approval reader is private and
  // always raises.
  const unbound = [];
  const findUnbound = (node, path) => {
    if (Array.isArray(node)) { node.forEach((item, i) => findUnbound(item, `${path}[${i}]`)); return; }
    if (node === null || typeof node !== "object") return;
    if (node.op === "unbound") { unbound.push(path); return; }
    for (const [key, value] of Object.entries(node)) findUnbound(value, `${path}.${key}`);
  };
  findUnbound(policy.transitions, "transitions");
  findUnbound(policy.initializations, "initializations");
  assert.deepEqual(unbound, [
    "transitions.establish-client-and-engagement.subjects.engagement.creation_shape.effective_from.cases.approved_representation_equivalent",
  ], "still exactly one unbound value, and no initialization adds a second");
  assert.ok(Object.keys(V5_J102_ABSENT_EVIDENCE_READERS)
    .includes("approved_representation_equivalent"),
    "and that branch is unreachable because its reader does not exist");
});

// ---------------------------------------------------------------------------
// THE INITIALIZATION-EFFECT PARITY SUITE.
//
// The structural test above compares the map's initialization half to the
// kernel's contracts key by key. That is not the same question as "does the map
// PREDICT what the kernel does", and the difference is where a real divergence
// would live: change `"relationship_state": "client"` to `"prospect"` in the
// map's required_context, or point an `identified_by` at `subject_id` instead of
// `relationship_id`, and every structural assertion still passes while the
// DATABASE would admit an assignment under a prospect that the kernel refuses.
//
// So these tests RUN `evaluateLifecycleInitialization` and require the map to
// reproduce its answer: every created field, every event detail, the
// non-constant effects, the identified_by chain and every required-context
// condition. Then they mutate the map, one thing at a time, and require each
// mutation to be CAUGHT — because a comparison that cannot fail proves nothing.
// ---------------------------------------------------------------------------

const IDENT_SHAPE = /^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]{0,127}$/;

/**
 * One walk per initialization, with the parent rows in the state the kernel
 * requires. These are the same synthetic subjects the transition walks use, so a
 * created assignment really is the one the transition suite then advances.
 */
const INIT_WALKS = [
  {
    initialization_id: "initialize-prospect-relationship",
    actor: KERNEL_AGENT,
    related: {},
    declared: { new_subject_id: "j102-rel-1" },
  },
  {
    initialization_id: "initialize-assignment",
    actor: KERNEL_PARTNER,
    related: { engagement: ENG, relationship: CLIENT },
    declared: { new_subject_id: "j102-asg-1" },
  },
  {
    initialization_id: "initialize-property-negotiation",
    actor: KERNEL_AGENT,
    related: { assignment: ASG({ assignment_phase: "search" }) },
    declared: { new_subject_id: "j102-neg-1", property_id: "j102-prop-1" },
  },
];

const runInitWalk = walk => evaluateLifecycleInitialization({
  tenant: ORGANIZATION_TENANT_ID,
  initialization_id: walk.initialization_id,
  related: walk.related,
  declared: walk.declared,
  actor: walk.actor,
  now: NOW,
  ...(walk.initialization_id === "initialize-property-negotiation"
    ? { active_negotiations_for_property: walk.active_negotiations_for_property ?? [] } : {}),
});

/**
 * The proposed created state a caller would send for one walk, composed from the
 * kernel's own contract. The writer receives a proposal and holds it to the map;
 * this is that proposal, and it is what the map's `identified_by: {source:
 * "created"}` resolves the first parent from — so it exists for the REFUSAL
 * walks too, where the kernel produces no created state at all.
 */
function proposedFor(walk) {
  const contract = v5J102InitializationContract(walk.initialization_id);
  const parent = contract.parent_subject_kind === null
    ? {}
    : { [contract.parent_reference_field]:
        walk.related[contract.parent_subject_kind]?.subject_id ?? null };
  return {
    subject_kind: contract.subject_kind,
    subject_id: walk.declared.new_subject_id,
    ...parent,
    ...Object.fromEntries(contract.declared_identifiers.map(f => [f, walk.declared[f]])),
    ...Object.fromEntries(contract.optional_declared_identifiers.map(f => [f, walk.declared[f] ?? null])),
    ...contract.initial_state,
  };
}

/**
 * THE CONTEXT HALF OF THE WRITER, transcribed: resolve each required-context rule
 * the way `ops.j102_initialize_subject` resolves it and check its conditions
 * against the loaded row. `source: "created"` reads the proposed row's own parent
 * reference; `source: "context"` reads a field on the hop before it, which is the
 * whole of "the engagement→relationship link is verified, not assumed" on the SQL
 * side. Returns the complaints, so an EMPTY list means the map would admit.
 */
function mapContext(policy, initialization_id, proposed, related) {
  const admitted = policy.initializations[initialization_id];
  const complaints = [];
  const context = {};
  const consulted = [];
  if (admitted === undefined) return { complaints: ["j102_unknown_initialization"], consulted };
  for (const rule of admitted.required_context ?? []) {
    const by = rule.identified_by;
    const id = by.source === "created"
      ? orNull(at(proposed, by.field))
      : orNull(at(at(context, by.subject), by.field));
    if (id === null) {
      complaints.push(`j102_required_context_unidentified: ${rule.subject}`);
      continue;
    }
    const loaded = related[rule.subject];
    if (loaded === undefined || loaded.subject_id !== id) {
      complaints.push(
        `j102_required_context_not_locked: ${rule.subject}:${id} is not the ${rule.subject} this call was given`);
      continue;
    }
    for (const condition of rule.conditions ?? []) {
      const observed = orNull(at(loaded, condition.field));
      const met = Object.prototype.hasOwnProperty.call(condition, "equals")
        ? observed === condition.equals
        : Object.prototype.hasOwnProperty.call(condition, "in")
          ? condition.in.includes(observed)
          : Object.prototype.hasOwnProperty.call(condition, "not_in")
            ? !condition.not_in.includes(observed)
            : false;
      if (!met) {
        complaints.push(
          `j102_required_context_not_met: ${rule.subject}.${condition.field} is ${show(observed)}`);
      }
    }
    context[rule.subject] = loaded;
    consulted.push(`${rule.subject}:${id}`);
  }
  return { complaints, context, consulted };
}

/**
 * The rest of the writer's comparison loop: predict the created shape and the
 * event from the map and compare them to what the kernel actually produced.
 * Returns every complaint, so a divergence is a list rather than a thrown
 * assertion and the negative arm can require one.
 */
function initializationComplaints(policy, walk, answer) {
  const admitted = policy.initializations[walk.initialization_id];
  if (admitted === undefined) return ["j102_unknown_initialization"];
  const created = answer.created_state;
  const resolved = mapContext(policy, walk.initialization_id, created, walk.related);
  const complaints = [...resolved.complaints];
  const context = resolved.context ?? {};
  const consulted = resolved.consulted ?? [];

  const ctx = {
    prior: {},
    proposed: { [admitted.subject_kind]: created },
    context,
    ids: { [admitted.subject_kind]: created.subject_id },
    facts: {},
  };
  const compare = (label, effect, actual) => {
    const expected = expectedValue(effect, ctx);
    if (expected.kind === "optional_declared_identifier") {
      // Null when the caller named nothing; otherwise the same identifier rule,
      // bound to the declared value. (The writer also binds it to a real party.)
      const declaredValue = walk.declared[expected.field] ?? null;
      if (actual !== declaredValue) {
        complaints.push(`${label}: the kernel bound ${show(actual)} and the caller declared ${show(declaredValue)}`);
      }
      if (actual !== null && (typeof actual !== "string" || !IDENT_SHAPE.test(actual))) {
        complaints.push(`${label}: ${show(actual)} is not a permitted identifier`);
      }
      return;
    }
    if (expected.kind === "declared_identifier") {
      // SQL holds the SHAPE; the kernel binds the VALUE. Both halves are asserted
      // here, and neither is asserted as the other.
      if (typeof actual !== "string" || !IDENT_SHAPE.test(actual)) {
        complaints.push(`${label}: ${show(actual)} is not a permitted identifier`);
      }
      if (actual !== walk.declared[expected.field]) {
        complaints.push(
          `${label}: the kernel bound ${show(actual)} and the map names the declared ${expected.field}, which is ${show(walk.declared[expected.field])}`);
      }
      return;
    }
    if (expected.kind === "exact") {
      if (JSON.stringify(actual ?? null) !== JSON.stringify(expected.value ?? null)) {
        complaints.push(`${label}: map says ${show(expected.value)}, kernel produced ${show(actual)}`);
      }
      return;
    }
    if (expected.kind === "any_of") {
      if (!expected.values.some(v => JSON.stringify(v) === JSON.stringify(actual))) {
        complaints.push(`${label}: map admits ${JSON.stringify(expected.values)}, kernel produced ${show(actual)}`);
      }
      return;
    }
    complaints.push(`${label}: the map computes an answer shape this writer does not compare`);
  };

  // 3. THE CREATED SHAPE, key set and every value — including the NON-CONSTANT
  //    ones the structural test cannot reach: the subject id, the parent
  //    reference resolved through the context, and the declared property.
  const shapeKeys = Object.keys(admitted.creation_shape ?? {}).sort();
  const createdKeys = Object.keys(created).sort();
  if (JSON.stringify(shapeKeys) !== JSON.stringify(createdKeys)) {
    complaints.push(
      `j102_created_subject_shape_mismatch: map ${JSON.stringify(shapeKeys)}, kernel ${JSON.stringify(createdKeys)}`);
  }
  for (const field of shapeKeys) {
    if (!Object.prototype.hasOwnProperty.call(created, field)) continue;
    compare(`created.${field}`, admitted.creation_shape[field], created[field]);
  }

  // 4. THE EVENT: its kind, its subject, and every non-identity detail.
  const event = answer.events[0];
  const spec = admitted.event ?? {};
  if (spec.event_kind !== event.event_kind) {
    complaints.push(`j102_event_missing_or_wrong: map ${spec.event_kind}, kernel ${event.event_kind}`);
  }
  if (spec.subject !== event.subject_kind) {
    complaints.push(`j102_event_missing_or_wrong: map subject ${spec.subject}, kernel ${event.subject_kind}`);
  }
  if (created.subject_id !== event.subject_id) {
    complaints.push("j102_event_subject_not_advanced: the event names another row");
  }
  const detailKeys = Object.keys(spec.detail ?? {}).sort();
  const eventDetailKeys = Object.keys(event)
    .filter(k => !EVENT_IDENTITY_KEYS.includes(k)).sort();
  if (JSON.stringify(detailKeys) !== JSON.stringify(eventDetailKeys)) {
    complaints.push(
      `j102_event_detail_not_produced_by_transition: map ${JSON.stringify(detailKeys)}, kernel ${JSON.stringify(eventDetailKeys)}`);
  }
  for (const field of detailKeys) {
    compare(`event.${field}`, spec.detail[field], orNull(at(event, field)));
  }

  // 5. AND THE SET THE WRITER SAYS IT CONSULTED, which is what the receipt
  //    reports beside its two unconditional booleans.
  if (JSON.stringify(consulted.sort()) !==
      JSON.stringify([...(answer.context_verified ?? [])]
        .map(kind => `${kind}:${walk.related[kind]?.subject_id}`).sort())) {
    complaints.push(
      `context_verified: map consulted ${JSON.stringify(consulted)}, kernel verified ${JSON.stringify(answer.context_verified)}`);
  }
  return complaints;
}

/**
 * THE WALKS THE KERNEL REFUSES, and why the positive ones are not enough.
 *
 * A positive walk can only catch a map that predicts the WRONG ANSWER. It cannot
 * catch a map that is merely MORE PERMISSIVE — drop `relationship_state: client`
 * from the required context, or widen the negotiation's phase set, and every
 * positive prediction still matches while the database admits calls the kernel
 * refuses. That is the exact drift the review found, so each refusal below is a
 * case the kernel turns down and the map must turn down too.
 */
const INIT_REFUSAL_WALKS = [
  { initialization_id: "initialize-assignment", actor: KERNEL_PARTNER,
    related: { engagement: ENG, relationship: REL },
    declared: { new_subject_id: "j102-asg-1" },
    reason_id: "client_status_required",
    why: "Q077: work before signature stays prospect work" },
  { initialization_id: "initialize-assignment", actor: KERNEL_PARTNER,
    related: { engagement: { ...ENG, engagement_state: "expired" }, relationship: CLIENT },
    declared: { new_subject_id: "j102-asg-1" },
    reason_id: "engagement_not_active",
    why: "an assignment does not start under a lapsed representation agreement" },
  { initialization_id: "initialize-assignment", actor: KERNEL_PARTNER,
    related: { engagement: { ...ENG, relationship_id: "j102-rel-other" }, relationship: CLIENT },
    declared: { new_subject_id: "j102-asg-1" },
    reason_id: "relationship_not_in_verified_chain",
    why: "the engagement's own client is the one that must be a client" },
  { initialization_id: "initialize-property-negotiation", actor: KERNEL_AGENT,
    related: { assignment: ASG({ assignment_phase: "committed",
      selected_property_id: "j102-prop-1", pending_deal_id: "j102-deal-1" }) },
    declared: { new_subject_id: "j102-neg-2", property_id: "j102-prop-2" },
    reason_id: "assignment_already_committed",
    why: "Q095: once the winner is selected a fresh LOI is a different decision" },
  { initialization_id: "initialize-property-negotiation", actor: KERNEL_AGENT,
    related: { assignment: ASG({ assignment_phase: "concluded" }) },
    declared: { new_subject_id: "j102-neg-3", property_id: "j102-prop-3" },
    reason_id: "assignment_phase_not_open",
    why: "a concluded assignment takes no new drafts" },
];

/**
 * Every disagreement between the kernel and the map, over both arms. Empty means
 * the two agree on what they produce AND on what they refuse.
 */
function initializationDrift(policy) {
  const drift = [];
  for (const walk of INIT_WALKS) {
    const answer = runInitWalk(walk);
    if (answer.decision !== "allow") {
      drift.push(`${walk.initialization_id}: the kernel refused a walk it should allow`);
      continue;
    }
    for (const complaint of initializationComplaints(policy, walk, answer)) {
      drift.push(`${walk.initialization_id}: ${complaint}`);
    }
  }
  for (const walk of INIT_REFUSAL_WALKS) {
    const answer = runInitWalk(walk);
    if (answer.decision !== "refuse" || answer.reason_id !== walk.reason_id) {
      drift.push(
        `${walk.initialization_id}: the kernel answered ${answer.decision}/${answer.reason_id} where ${walk.reason_id} was expected`);
      continue;
    }
    // The map is handed the SAME proposal a caller would send, and must refuse it
    // for a reason of its own. An empty complaint list is the map admitting what
    // the kernel turned down.
    const { complaints } = mapContext(policy, walk.initialization_id,
      proposedFor(walk), walk.related);
    if (complaints.length === 0) {
      drift.push(
        `${walk.initialization_id}: the map ADMITS a call the kernel refuses with ${walk.reason_id} (${walk.why})`);
    }
  }
  return drift;
}

test("SQL parity: the map PREDICTS the kernel's created row and event, initialization by initialization", () => {
  const policy = admissionPolicy();
  assert.equal(INIT_WALKS.length, V5_J102_INITIALIZATION_IDS.length,
    "every registered initialization is walked");
  for (const walk of INIT_WALKS) {
    const answer = runInitWalk(walk);
    assert.equal(answer.decision, "allow", `${walk.initialization_id} allows`);
    assert.equal(answer.events.length, 1, `${walk.initialization_id} appends exactly one event`);
    assert.deepEqual(initializationComplaints(policy, walk, answer), [],
      `${walk.initialization_id}: the SQL map predicts the kernel exactly`);
  }
});

test("SQL parity: the map REFUSES every call the kernel refuses, on the same context", () => {
  const policy = admissionPolicy();
  for (const walk of INIT_REFUSAL_WALKS) {
    const answer = runInitWalk(walk);
    assert.equal(answer.decision, "refuse", walk.why);
    assert.equal(answer.reason_id, walk.reason_id, walk.why);
    assert.equal(answer.created_state, null, "and it creates nothing");
    const { complaints } = mapContext(policy, walk.initialization_id,
      proposedFor(walk), walk.related);
    assert.ok(complaints.length > 0,
      `the SQL map must also refuse ${walk.initialization_id} here — ${walk.why}`);
  }
  // AND THE CLIENT GATE IS NOT VACUOUS at the kernel: the same call with the
  // chain intact is allowed, so the refusals above are the conditions biting
  // rather than a walk that could never work.
  const allowed = runInitWalk(INIT_WALKS.find(w => w.initialization_id === "initialize-assignment"));
  assert.equal(allowed.decision, "allow");
  assert.deepEqual(allowed.context_verified, ["engagement", "relationship"]);
});

test("SQL parity: a drifted initialization map is CAUGHT, mutation by mutation", () => {
  const base = admissionPolicy();
  const clone = () => JSON.parse(JSON.stringify(base));

  // Each entry mutates ONE thing in the map and must be caught by one of the two
  // arms. Without this test the comparison above could be vacuous — a predictor
  // that agreed with everything would pass it just as well — and each mutation
  // here is a shape a plausible edit or "simplification" actually takes.
  const mutations = [
    // The condition the whole client gate rests on, flipped to admit a prospect.
    ["initialize-assignment", "the relationship condition admits a prospect", policy => {
      policy.initializations["initialize-assignment"]
        .required_context[1].conditions[0].equals = "prospect";
    }],
    // The engagement condition, flipped to admit a lapsed engagement.
    ["initialize-assignment", "the engagement condition admits an expired engagement", policy => {
      policy.initializations["initialize-assignment"]
        .required_context[0].conditions[0].equals = "expired";
    }],
    // The conditions dropped entirely, which is the shape a "simplification"
    // takes: the map would then admit anything the kernel refuses.
    ["initialize-assignment", "the relationship conditions are dropped", policy => {
      policy.initializations["initialize-assignment"].required_context[1].conditions = [];
    }],
    // THE CHAIN ITSELF. `relationship_id` → `subject_id` resolves the second hop
    // to the engagement's own id, so the row the map reads is not the one the
    // kernel verified — this is the whole of "the link is verified, not assumed"
    // on the SQL side.
    ["initialize-assignment", "the chained identified_by points at the wrong field", policy => {
      policy.initializations["initialize-assignment"]
        .required_context[1].identified_by.field = "subject_id";
    }],
    // The FIRST hop's identifier, read off the created row.
    ["initialize-assignment", "the first identified_by reads the wrong created field", policy => {
      policy.initializations["initialize-assignment"]
        .required_context[0].identified_by.field = "subject_id";
    }],
    // The created phase, moved past the earliest declared one.
    ["initialize-assignment", "the created assignment is born in search", policy => {
      policy.initializations["initialize-assignment"]
        .creation_shape.assignment_phase.value = "search";
    }],
    // A NON-CONSTANT effect: the parent reference, blanked.
    ["initialize-assignment", "the parent reference is blanked", policy => {
      policy.initializations["initialize-assignment"].creation_shape.engagement_id =
        { op: "const", value: null };
    }],
    // The created id, which no structural assertion covers.
    ["initialize-prospect-relationship", "the created id is a constant", policy => {
      policy.initializations["initialize-prospect-relationship"]
        .creation_shape.subject_id = { op: "const", value: "j102-rel-other" };
    }],
    // A relationship born a client.
    ["initialize-prospect-relationship", "the created relationship is born a client", policy => {
      policy.initializations["initialize-prospect-relationship"]
        .creation_shape.relationship_state.value = "client";
    }],
    // An extra key in the creation shape, and a missing one.
    ["initialize-prospect-relationship", "the creation shape gains a key", policy => {
      policy.initializations["initialize-prospect-relationship"]
        .creation_shape.invented_field = { op: "const", value: null };
    }],
    ["initialize-prospect-relationship", "the creation shape loses a key", policy => {
      delete policy.initializations["initialize-prospect-relationship"]
        .creation_shape.active_engagement_count;
    }],
    // THE DECLARED IDENTIFIER, in both directions: pinned to a constant, and
    // pointed at the wrong declared field.
    ["initialize-property-negotiation", "the declared property becomes a constant", policy => {
      policy.initializations["initialize-property-negotiation"]
        .creation_shape.property_id = { op: "const", value: "j102-prop-other" };
    }],
    ["initialize-property-negotiation", "the declared identifier names the wrong field", policy => {
      policy.initializations["initialize-property-negotiation"]
        .creation_shape.property_id.field = "new_subject_id";
    }],
    // The negotiation's phase set, widened to admit a committed assignment.
    ["initialize-property-negotiation", "the assignment phase set is widened", policy => {
      policy.initializations["initialize-property-negotiation"]
        .required_context[0].conditions[0].in = ["committed"];
    }],
    // THE EVENT, in all three of its halves: kind, subject and nested detail.
    ["initialize-property-negotiation", "the event kind drifts", policy => {
      policy.initializations["initialize-property-negotiation"].event.event_kind = "loi_submitted";
    }],
    ["initialize-assignment", "the event names another subject kind", policy => {
      policy.initializations["initialize-assignment"].event.subject = "engagement";
    }],
    ["initialize-assignment", "the event's phase detail is a constant", policy => {
      policy.initializations["initialize-assignment"].event.detail.assignment_phase =
        { op: "const", value: "committed" };
    }],
    ["initialize-assignment", "the event gains a detail field", policy => {
      policy.initializations["initialize-assignment"].event.detail.invented =
        { op: "const", value: "x" };
    }],
    ["initialize-property-negotiation", "the event loses a detail field", policy => {
      delete policy.initializations["initialize-property-negotiation"].event.detail.property_id;
    }],
    // A parent dropped from required_context entirely: the map would then consult
    // one row where the kernel verified two.
    ["initialize-assignment", "the relationship hop is removed", policy => {
      policy.initializations["initialize-assignment"].required_context =
        [policy.initializations["initialize-assignment"].required_context[0]];
    }],
  ];

  // THE UNMUTATED MAP IS CLEAN FIRST, so what follows is measuring drift rather
  // than a detector that complains about everything.
  assert.deepEqual(initializationDrift(base), [],
    "the shipped map and the kernel agree on both what they produce and what they refuse");

  for (const [id, label, mutate] of mutations) {
    const policy = clone();
    mutate(policy);
    const drift = initializationDrift(policy);
    assert.ok(drift.length > 0,
      `DRIFT NOT CAUGHT — ${id}: ${label}. The map and the kernel disagree and both arms said nothing.`);
  }
  // Every mutation is a real change to the map, so a typo in a path above would
  // otherwise "pass" by mutating nothing at all.
  for (const [, label, mutate] of mutations) {
    const policy = clone();
    mutate(policy);
    assert.notDeepEqual(policy.initializations, base.initializations,
      `${label}: the mutation actually changed the map`);
  }
});

test("SQL parity: the map's context conditions and identified_by ARE the kernel's, field for field", () => {
  const policy = admissionPolicy();
  for (const id of V5_J102_INITIALIZATION_IDS) {
    const contract = v5J102InitializationContract(id);
    const admitted = policy.initializations[id];
    const rules = admitted.required_context ?? [];
    assert.equal(rules.length, contract.required_context.length, `${id} context rule count`);
    rules.forEach((rule, i) => {
      const declared = contract.required_context[i];
      assert.equal(rule.subject, declared.subject, `${id} context[${i}] subject`);
      // THE CONDITIONS, compared rather than counted. This is the assertion whose
      // absence let `relationship_state: client` become `prospect` silently.
      assert.deepEqual(rule.conditions, declared.conditions, `${id} context[${i}] conditions`);
      // AND THE RESOLUTION. The first hop reads the created row's own parent
      // reference; a later hop reads the field the kernel's `chained_from` names.
      if (declared.chained_from === null) {
        assert.deepEqual(rule.identified_by,
          { source: "created", field: contract.parent_reference_field },
          `${id} context[${i}] resolves the parent from the created row`);
      } else {
        assert.deepEqual(rule.identified_by,
          { source: "context", subject: declared.chained_from.subject,
            field: declared.chained_from.field },
          `${id} context[${i}] resolves through the hop before it`);
      }
    });
  }
});

// ---------------------------------------------------------------------------
// Q103 for typed transitions: evaluateConcurrentTransition and its write sets.
// ---------------------------------------------------------------------------

test("SQL parity: the kernel's transition write sets are a SUPERSET of the admission map's `writes`", () => {
  // A merge is decided on these sets, so an under-reported write could merge
  // over another partner's field. Over-reporting only turns a merge into a
  // visible reconciliation.
  const policy = admissionPolicy();
  assert.deepEqual([...V5_J102_DERIVED_FIELDS].sort(), [...policy.derived_fields].sort(),
    "the kernel and the SQL name the same derived counters and mirrors");
  for (const id of V5_J102_TRANSITION_IDS) {
    const kernel = v5J102TransitionWrites(id);
    assert.deepEqual(Object.keys(kernel).sort(), Object.keys(policy.transitions[id].writes).sort(),
      `${id} writes the same subject kinds in both`);
    for (const [kind, fields] of Object.entries(policy.transitions[id].writes)) {
      for (const field of fields) {
        assert.ok(kernel[kind].includes(field),
          `${id} may move ${kind}.${field} in SQL and the kernel's write set omits it`);
      }
    }
  }
});

const CT = (over = {}) => ({
  tenant: ORGANIZATION_TENANT_ID, transition_id: "record-invoice-issued", subject_kind: "deal",
  base_version_digest: D(1), current_version_digest: D(2),
  current_prior_state_digest: D(1),
  current_established_by_transition: "record-commission-agreement", ...over,
});

test("Q103: a characterized, disjoint intervening transition merges — conditionally on re-admission", () => {
  const v = evaluateConcurrentTransition(CT());
  assert.equal(v.decision, "allow");
  assert.equal(v.reason_id, "nonoverlapping_transition_merged_on_current_row");
  assert.equal(v.merged, true);
  assert.equal(v.readmission_required, true);
  assert.deepEqual(v.incoming_fields, ["invoice_state"]);
  assert.deepEqual(v.concurrent_fields, ["commission_agreement_state"]);
  assert.equal(v.last_writer_wins, false);
  assert.equal(v.silent_overwrite, false);
});

test("Q103: an overlapping intervening transition reconciles, naming the shared fields", () => {
  const v = evaluateConcurrentTransition(CT({ transition_id: "record-payment",
    current_established_by_transition: "record-payment" }));
  assert.equal(v.decision, "reconcile");
  assert.equal(v.conflict_kind, "overlapping_field_edit");
  assert.deepEqual(v.overlapping_fields, ["payment_state"]);
  assert.equal(v.merged, false);
  // Closing and cancellation both write deal_state.
  const w = evaluateConcurrentTransition(CT({ transition_id: "record-deal-closing",
    current_established_by_transition: "cancel-pending-deal" }));
  assert.equal(w.decision, "reconcile");
  assert.deepEqual(w.overlapping_fields, ["deal_state"]);
});

test("Q103: anything not ONE declared transition away is uncharacterized and reconciles", () => {
  for (const over of [
    { current_prior_state_digest: D(9) },          // more than one write between
    { current_prior_state_digest: null },          // a created row / no anchor
    { current_established_by_transition: "record-lifecycle-correction" }, // not a transition
    { current_established_by_transition: null },
  ]) {
    const v = evaluateConcurrentTransition(CT(over));
    assert.equal(v.decision, "reconcile", JSON.stringify(over));
    assert.equal(v.conflict_kind, "uncharacterized_concurrent_change");
    assert.equal(v.characterized, false);
    assert.equal(v.merged, false);
  }
});

test("Q103: no movement is the ordinary optimistic path, and no merge is claimed", () => {
  const v = evaluateConcurrentTransition(CT({ current_version_digest: D(1) }));
  assert.equal(v.decision, "allow");
  assert.equal(v.reason_id, "no_concurrent_movement");
  assert.equal(v.merged, false);
});

test("Q103: the coupled subject is judged on ITS write sets, not the primary's", () => {
  // An LOI submission writes the assignment's phase and counters; a concurrent
  // commitment wrote the same assignment fields, so the related assignment
  // reconciles even though the primary negotiation might not.
  const v = evaluateConcurrentTransition(CT({ transition_id: "record-loi-submission",
    subject_kind: "assignment", current_established_by_transition: "commit-winning-property" }));
  assert.equal(v.decision, "reconcile");
  assert.ok(v.overlapping_fields.includes("assignment_phase"));
  // A read-only related subject (the engagement an open-assignment reads) has
  // no write set, so any characterized change to it merges and is re-admitted.
  const e = evaluateConcurrentTransition(CT({ transition_id: "open-assignment",
    subject_kind: "engagement", current_established_by_transition: "establish-client-and-engagement" }));
  assert.equal(e.decision, "allow");
  assert.deepEqual(e.incoming_fields, []);
});

test("Q103: a characterized stale subject on the TRANSITION path merges, is re-judged on the current row, and says so", async () => {
  const current = dealState({ execution_state: "executed", commission_agreement_state: "agreed" });
  const db = new FakeDb({
    subjects: { "deal:deal-synthetic-1": { ...storedSubject(current),
      established_by_transition: "record-commission-agreement", prior_state_digest: D(42) } },
    facts: { invoice: storedFact("invoice") },
  });
  const store = createCreLifecycleStore({ db });
  const answer = await store.recordDealAxis({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1", expected_state_digest: D(42) },
    evidence_refs: [recordRef("invoice_issued")],
    declared: { axis: "invoice_state" },
  }, ctx(JOE));
  assert.equal(answer.decision, "allow", JSON.stringify(answer).slice(0, 500));
  assert.equal(answer.auto_merged, true);
  assert.equal(answer.concurrent_merges[0].concurrent_transition_id, "record-commission-agreement");
  // THE WRITE IS DECIDED AGAINST THE CURRENT ROW: its operand is the current
  // digest, never the caller's stale base.
  const cas = JSON.parse(db.paramsFor("j102_apply_transition")[1]);
  assert.equal(cas["deal:deal-synthetic-1"], digest(current));
  const subjects = JSON.parse(db.paramsFor("j102_apply_transition")[2]);
  assert.equal(subjects[0].record.state.commission_agreement_state, "agreed",
    "the other partner's field survives in what is written");
  assert.equal(subjects[0].record.state.invoice_state, "invoiced");
});

test("Q103: an overlapping stale subject on the TRANSITION path files ONE visible item and applies nothing", async () => {
  const current = dealState({ payment_state: "partially_paid" });
  const db = new FakeDb({
    subjects: { "deal:deal-synthetic-1": { ...storedSubject(current),
      established_by_transition: "record-payment", prior_state_digest: D(42) } },
    facts: { payment: storedFact("payment") },
    read_bodies: {
      subject: subjectReadback(current, { established_by_transition: "record-payment",
        prior_state_digest: D(42), updated_by: "dell" }),
      subject_events: [],
    },
  });
  const store = createCreLifecycleStore({ db });
  const answer = await store.recordDealAxis({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1", expected_state_digest: D(42) },
    evidence_refs: [recordRef("payment_received")],
    declared: { axis: "payment_state", payment_level: "paid" },
  }, ctx(JOE));
  assert.equal(answer.decision, "reconcile");
  assert.deepEqual(answer.overlapping_fields, ["payment_state"]);
  assert.equal(db.callsTo("j102_apply_transition").length, 0);
  assert.equal(db.callsTo("j102_record_reconciliation_item").length, 1);
  const envelope = JSON.parse(db.paramsFor("j102_record_reconciliation_item")[0]);
  assert.equal(envelope.record.conflict_kind, "overlapping_field_edit");
  assert.deepEqual(envelope.record.concurrent_edits.map(e => [e.field, e.edited_by]),
    [["payment_state", "dell"]]);
  assert.equal(envelope.record.concurrent_change_evidence.characterized, true);
  const diagnostics = JSON.parse(db.paramsFor("j102_record_reconciliation_item")[4]);
  assert.equal(diagnostics.operation, "record-lifecycle-reconciliation",
    "the governed writer is reached under its own operation name");
});
