// V5-J103 — governed correspondence reads, drafts, proposed facts and source
// reconciliation, proved case by case.
//
// Everything here is synthetic and nothing reaches a database, a provider, a
// network, a clock or the filesystem. The module under test is pure, so this
// suite can prove the properties that actually matter about a correspondence
// kernel:
//
//   * that a read PRESERVES account and native provenance on every answer it
//     gives, including the refusals,
//   * that a draft and a proposed fact are structurally unable to dispatch — no
//     recipient destination, no provider operation, and a dispatch field refused
//     before any value is read — with F10 as the INDEPENDENT ORACLE for the send
//     refusal, since F10 owns the operation registry,
//   * that a conflict shows BOTH values with the source that owns each, and that
//     every way of saying "whoever wrote last wins" is refused by name,
//   * that the four conflict kinds this module routes are exactly the four F01
//     emits — driven out of resolveObservation rather than asserted from a
//     comment,
//   * that the observation candidate a proposal builds is one F01 ITSELF accepts,
//     so the mapping is not graded by the code that produced it,
//   * that ambiguity resolves to private and never falls through to a read,
//   * and that both-partner coverage is unreachable from every input.
//
// NO FIXTURE NAMES A REAL THING: every account, participant, digest and native id
// below is unmistakably test data on an .invalid domain.

import test from "node:test";
import assert from "node:assert/strict";
// Read by the dependency-boundary test at the bottom of this file, which reads
// J103's own source and the rest of mcp-server/src the way a linter would. The
// module under test still reaches no filesystem; this suite does.
import { readFileSync, readdirSync, mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { tmpdir } from "node:os";
import { join, basename } from "node:path";
import esbuild from "esbuild";

import { canonicalJson, digest } from "../src/artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  V5_NO_EFFECTS,
  evaluatePrivacyBoundary,
} from "../src/global-boundaries.v5.js";
import {
  V5_F01_RECONCILIATION_SCHEMA_VERSION,
  compileFieldAuthorityRegistry,
  resolveObservation,
} from "../src/record-source-authority.v5.js";
import {
  V5_F10_ADAPTER_KIND,
  V5_F10_WRITE_OPERATIONS,
  compilePartnerInstallation,
  evaluateConnectorOperation,
} from "../src/partner-mail-calendar.v5.js";
import * as F10_NAMESPACE from "../src/partner-mail-calendar.v5.js";
import * as J103_NAMESPACE from "../src/governed-correspondence.v5.js";
import {
  V5J103Error,
  V5_J103_ADAPTERS,
  V5_J103_ADAPTER_KINDS,
  V5_J103_ADAPTER_READ_RECEIPT_SEAM,
  V5_J103_RECONCILIATION_ITEM_SEAM,
  V5_J103_AVAILABILITY_STATES,
  V5_J103_BINDING_SCHEMA_VERSION,
  V5_J103_CONFLICT_KINDS,
  V5_J103_CONFLICT_ROUTES,
  V5_J103_CONFLICT_SCHEMA_VERSION,
  V5_J103_CORRESPONDENCE_STATES,
  V5_J103_CREDENTIAL_FRAGMENTS,
  V5_J103_DISPATCH_FRAGMENTS,
  V5_J103_DRAFT_SCHEMA_VERSION,
  V5_J103_NON_READING_AVAILABILITY,
  V5_J103_PROPOSAL_SCHEMA_VERSION,
  V5_J103_REFUSED_RESOLUTION_BASES,
  V5_J103_RESOLUTION_BASIS,
  V5_J103_SEND_AUTHORITY_HOLDER,
  V5_J103_SEND_AUTHORITY_SEAM,
  V5_J103_SETTLED_DECISIONS,
  V5_J103_SETTLED_DECISION_IDS,
  V5_J103_SOURCE_CONTENT_FRAGMENTS,
  V5_J103_TAINT_CLASS,
  V5_J103_THREAD_SCHEMA_VERSION,
  assertJ103DecisionBinding,
  buildSourceConflictQueueEntry,
  V5_J103_PRIVILEGED_OUTCOMES,
  V5_J103_PUBLIC_SURFACE,
  __j103ClassificationProbe,
  compileCorrespondenceBinding,
  correspondenceBindingCanonicalBytes,
  draftCorrespondence,
  evaluateProposedFact,
  governedCorrespondenceGaps,
  projectCorrespondenceCoverage,
  readCorrespondenceThread,
  resolveAdapterAvailability,
  v5J103AbsentWriteOperations,
  v5J103CorrespondenceProjection,
  v5J103DecisionSubsetDigest,
  v5J103DecisionSubsetPreimage,
  v5J103PolicyCanonicalBytes,
  v5J103PolicyDigest,
  v5J103PolicyPreimage,
} from "../src/governed-correspondence.v5.js";

// ---------------------------------------------------------------------------
// THE CLASSIFICATION PROBE — the only route to the decision logic, and it cannot
// hand back an outcome.
//
// Round two of this review found the previous correction's actual defect: the
// logic had been kept as `unwired*` EXPORTS, so `read`, `covered`, `drafted`,
// `proposed` and `queued` were still returnable to any caller willing to call the
// other name, with `wired: false` riding along as a label. The logic is now
// module-private, and the single non-consumer entry that runs it renames every
// verdict on the way out — `would_read_if_authoritative` and its four siblings —
// and throws if a privileged string survives the rename.
//
// Every assertion below that used to read `.decision === "read"` therefore reads
// `.classification === "would_read_if_authoritative"`: the rendering is proved,
// and proving it does not also deliver it.
// ---------------------------------------------------------------------------

const probe = __j103ClassificationProbe;

/** The classification name for an internal verdict, so the tests read as intent. */
const WOULD_READ = "would_read_if_authoritative";
const WOULD_COVER = "would_cover_if_authoritative";
const WOULD_DRAFT = "would_draft_if_authoritative";
const WOULD_PROPOSE = "would_propose_if_authoritative";
const WOULD_QUEUE = "would_queue_if_authoritative";

// ---------------------------------------------------------------------------
// Fixtures.
// ---------------------------------------------------------------------------

const NOW = "2026-09-11T18:00:00Z";
const SOURCE = "outlook_graph_test";
const JOE_ACCOUNT = "joe@carr-test.invalid";
const DELL_ACCOUNT = "dell@carr-test.invalid";

const sha = suffix => `sha256:${suffix.padEnd(64, "0").slice(0, 64)}`;

function bindingConfig(overrides = {}) {
  return {
    partner_slug: "joe",
    adapter_kind: V5_F10_ADAPTER_KIND,
    account: JOE_ACCOUNT,
    source_system: SOURCE,
    availability: "available",
    binding_version: 1,
    ...overrides,
  };
}

/**
 * THE FIXTURE BINDING, and it is the one nearly every rendering test below uses.
 *
 * It carries the availability the test says it does, which is exactly what the
 * WIRED compiler refuses to do: no adapter read receipt exists in this
 * repository, so compileCorrespondenceBinding derives `unavailable` for every
 * binding and a caller cannot type its way to a read. Everything a fixture
 * binding produces is stamped `wired: false` and is refused by the wired seams.
 */
function binding(overrides = {}) {
  return probe.fixtureBinding(bindingConfig(overrides));
}

/** The wired binding a real caller gets. Its availability is never the config's. */
function wiredBinding(overrides = {}) {
  return compileCorrespondenceBinding(bindingConfig(overrides));
}

function thread(overrides = {}) {
  return {
    account: JOE_ACCOUNT,
    native_identity: {
      source_system: SOURCE,
      native_id: "thread-aaa-test",
      native_id_epoch: "epoch-1",
    },
    participants: [
      { participant_ref: "party:joe-test", role: "originator", party_kind: "partner",
        address_digest: sha("a1") },
      { participant_ref: "party:counterparty-test", role: "principal_recipient",
        party_kind: "counterparty", address_digest: sha("b2") },
    ],
    message_refs: [
      { provider_thread_id: "pt-1-test", provider_message_id: "pm-1-test",
        occurred_at: "2026-09-10T15:00:00Z" },
      { provider_thread_id: "pt-1-test", provider_message_id: "pm-2-test",
        occurred_at: "2026-09-10T17:30:00Z" },
    ],
    attachments: [
      { attachment_id: "att-1-test", media_type: "application/pdf", byte_length: 4096,
        content_digest: sha("c3") },
    ],
    related_records: [{ record_kind: "deal", record_ref: "deal:test-0001" }],
    correspondence_state: "awaiting_us",
    relevance_state: "relevant_business_context",
    declared_data_classes: ["lease_economics"],
    started_at: "2026-09-10T15:00:00Z",
    last_activity_at: "2026-09-10T17:30:00Z",
    observed_at: "2026-09-10T18:00:00Z",
    ...overrides,
  };
}

/** A FIXTURE read: the predicate, not the interface. Every result says so. */
function read(overrides = {}, b = binding()) {
  return probe.wouldRead({ binding: b, thread: thread(overrides), now: NOW });
}

function draft(overrides = {}) {
  return {
    draft_kind: "reply_in_thread",
    authored_by: "model",
    draft_body: "Confirming the lease economics discussed on the thread; ready for your review.",
    intended_participant_refs: ["party:counterparty-test"],
    declared_data_classes: ["lease_economics"],
    ...overrides,
  };
}

function proposal(overrides = {}) {
  return {
    entity: "correspondence_derived_term",
    field: "base_rent_psf",
    value_digest: sha("d4"),
    rationale: "The counterparty restated the number in the second message on this thread.",
    proposed_by: "model",
    evidence_provider_message_id: "pm-2-test",
    declared_data_classes: ["lease_economics"],
    ...overrides,
  };
}

/**
 * A field-authority registry F01 compiles, shaped for a correspondence-derived
 * term. `untrusted_external` is the registered taint because that is what the
 * adapter boundary carries; anything else would make every J103 candidate refuse
 * at F01's taint check, which is the correct behaviour and a useless fixture.
 */
function registry(entryOverrides = {}) {
  return compileFieldAuthorityRegistry({
    schema_version: "doctorcre-v5-f01-field-authority-registry.v1",
    registry_version: 1,
    tenant: ORGANIZATION_TENANT_ID,
    entries: [{
      entity: "correspondence_derived_term",
      field: "base_rent_psf",
      authoritative_home: "neon_record_layer",
      owner_source: "carr_record_layer_test",
      permitted_sources: [
        { source_system: "carr_record_layer_test", direction: "bidirectional" },
        { source_system: SOURCE, direction: "inbound" },
      ],
      requires_account_identity: true,
      requires_native_identity: true,
      version_comparator: "integer_sequence",
      conflict_behavior: "reconcile",
      human_resolver_class: "partner_business_review",
      readback_required: false,
      sensitivity_classes: ["lease_economics"],
      taint_class: "untrusted_external",
      ...entryOverrides,
    }],
  });
}

/** Every result this suite produces, collected so the no-dispatch claim is total. */
const PRODUCED_RESULTS = [];
function capture(result) {
  PRODUCED_RESULTS.push(result);
  return result;
}

// ---------------------------------------------------------------------------
// The settled decisions.
// ---------------------------------------------------------------------------

test("the four settled decisions are the four this slice was dispatched against", () => {
  assert.deepEqual(V5_J103_SETTLED_DECISION_IDS, ["Q005.D1", "Q076.D1", "Q133.D1", "Q134.D1"]);
});

test("the decision subset digest is derived from the table, not typed beside it", () => {
  const rebuilt = {
    schema_version: "doctorcre-v5-j103-decision-subset.v1",
    decisions: ["Q005.D1", "Q076.D1", "Q133.D1", "Q134.D1"].map(decision_id => ({
      decision_id,
      settled_requirement: V5_J103_SETTLED_DECISIONS[decision_id].settled_requirement,
      source_evidence_digest: V5_J103_SETTLED_DECISIONS[decision_id].source_evidence_digest,
    })),
  };
  assert.deepEqual(v5J103DecisionSubsetPreimage(), rebuilt);
  assert.equal(v5J103DecisionSubsetDigest(), digest(rebuilt));
});

test("a decision binding accepts the reviewed four and refuses drift in both directions", () => {
  const decisions = Object.fromEntries(V5_J103_SETTLED_DECISION_IDS.map(id => [id, {
    source_evidence_digest: V5_J103_SETTLED_DECISIONS[id].source_evidence_digest,
    settled_requirement: V5_J103_SETTLED_DECISIONS[id].settled_requirement,
  }]));
  assert.equal(assertJ103DecisionBinding({
    decisions, decision_subset_digest: v5J103DecisionSubsetDigest(),
  }), true);

  const missing = { ...decisions };
  delete missing["Q076.D1"];
  assert.throws(() => assertJ103DecisionBinding({ decisions: missing }),
    e => e instanceof V5J103Error && e.code === "decision_binding_drift");

  const extra = { ...decisions, "Q999.D1": { source_evidence_digest: "0".repeat(64) } };
  assert.throws(() => assertJ103DecisionBinding({ decisions: extra }),
    e => e.code === "decision_binding_drift");

  const reworded = {
    ...decisions,
    "Q133.D1": { ...decisions["Q133.D1"], settled_requirement: "Outlook remains mailbox truth." },
  };
  assert.throws(() => assertJ103DecisionBinding({ decisions: reworded }),
    e => e.code === "decision_binding_drift" && e.detail.decision_id === "Q133.D1");
});

test("Q076's settled text is carried verbatim, including the clause the module enforces", () => {
  assert.match(V5_J103_SETTLED_DECISIONS["Q076.D1"].settled_requirement,
    /never timestamp alone; expose values and provenance/);
  assert.match(V5_J103_SETTLED_DECISIONS["Q134.D1"].settled_requirement,
    /never claims both-partner coverage/);
});

// ---------------------------------------------------------------------------
// The binding.
// ---------------------------------------------------------------------------

test("a compiled binding names one partner, one account and one read-only adapter", () => {
  const b = binding();
  assert.equal(b.schema_version, V5_J103_BINDING_SCHEMA_VERSION);
  assert.equal(b.tenant, ORGANIZATION_TENANT_ID);
  assert.equal(b.partner_slug, "joe");
  assert.equal(b.account, JOE_ACCOUNT);
  assert.equal(b.adapter_mode, "read");
  assert.equal(b.authoritative_home, V5_J103_ADAPTERS[V5_F10_ADAPTER_KIND].authoritative_home);
  assert.ok(Object.isFrozen(b));
  assert.equal(correspondenceBindingCanonicalBytes(b), canonicalJson(JSON.parse(
    canonicalJson({
      schema_version: V5_J103_BINDING_SCHEMA_VERSION,
      wired: b.wired,
      tenant: b.tenant,
      partner_slug: b.partner_slug,
      adapter_kind: b.adapter_kind,
      adapter_mode: b.adapter_mode,
      authoritative_home: b.authoritative_home,
      retrieval_class: b.retrieval_class,
      account: b.account,
      source_system: b.source_system,
      claimed_availability: b.claimed_availability,
      availability: b.availability,
      availability_source: b.availability_source,
      availability_owed_seam: b.availability_owed_seam,
      binding_version: b.binding_version,
    }))));
  // The preimage carries the claim AND the derived answer AND the mode, so the
  // digest of a fixture binding can never collide with a wired one's.
  assert.equal(b.wired, false);
  assert.notEqual(b.binding_digest, wiredBinding().binding_digest);
});

test("a credential in the binding config is refused by field name, before any value is read", () => {
  for (const fragment of ["access_token", "client_secret", "oauth_state", "session_cookie"]) {
    assert.throws(
      () => compileCorrespondenceBinding({
        partner_slug: "joe", adapter_kind: V5_F10_ADAPTER_KIND, account: JOE_ACCOUNT,
        source_system: SOURCE, availability: "available", binding_version: 1, [fragment]: "x",
      }),
      e => e instanceof V5J103Error && e.code === "credential_in_correspondence_request",
      `expected ${fragment} to be refused`);
  }
  assert.ok(V5_J103_CREDENTIAL_FRAGMENTS.includes("token"));
});

test("an unregistered adapter kind is not a policy question", () => {
  assert.throws(() => binding({ adapter_kind: "some_other_adapter" }),
    e => e.code === "unregistered_adapter_kind");
  assert.deepEqual(V5_J103_ADAPTER_KINDS, [V5_F10_ADAPTER_KIND]);
});

test("a partner slug is a CARR reference, cannot be an address, and must be a partner", () => {
  assert.throws(() => binding({ partner_slug: "joe@carr-test.invalid" }),
    e => e.code === "invalid_reference");
  assert.throws(() => binding({ partner_slug: "someone-else" }),
    e => e.code === "unknown_partner");
});

test("a binding edited after compilation no longer hashes to its own digest", () => {
  const forged = { ...binding(), account: DELL_ACCOUNT };
  assert.throws(() => probe.wouldRead({ binding: forged, thread: thread(), now: NOW }),
    e => e.code === "binding_digest_mismatch");
});

test("a hand-built object claiming to be compiled is refused", () => {
  const b = binding();
  const forged = { ...b, compiled: false };
  assert.throws(() => probe.wouldRead({ binding: forged, thread: thread(), now: NOW }),
    e => e.code === "binding_not_compiled");
});

// ---------------------------------------------------------------------------
// checkable_done 1 — reads preserve account and native provenance.
// ---------------------------------------------------------------------------

test("a read preserves the account and the full native identity triple", () => {
  const result = capture(read());
  assert.equal(result.classification, WOULD_READ);
  assert.equal(result.schema_version, V5_J103_THREAD_SCHEMA_VERSION);
  assert.equal(result.provenance.account, JOE_ACCOUNT);
  assert.equal(result.provenance.source_system, SOURCE);
  assert.deepEqual(result.provenance.native_identity, {
    source_system: SOURCE, native_id: "thread-aaa-test", native_id_epoch: "epoch-1",
  });
  assert.equal(result.provenance.partner_slug, "joe");
  assert.equal(result.provenance.adapter_kind, V5_F10_ADAPTER_KIND);
  assert.equal(result.provenance.taint_class, V5_J103_TAINT_CLASS);
  assert.equal(result.provenance.authoritative_home, "outlook");
  assert.equal(result.mailbox_remains_truth, true);
});

test("provenance rides on the refusals too, so a refusal can be audited", () => {
  const cases = [
    read({ relevance_state: "unrelated" }),
    read({ relevance_state: "ambiguous" }),
    read({ declared_data_classes: ["phi"] }),
    read({}, binding({ availability: "unavailable" })),
  ];
  for (const result of cases) {
    capture(result);
    assert.notEqual(result.classification, WOULD_READ);
    assert.equal(result.provenance.account, JOE_ACCOUNT);
    assert.equal(result.provenance.native_identity.native_id, "thread-aaa-test");
    assert.equal(result.thread, null);
  }
});

test("the thread carries participants, provider ids, attachments, related records and state", () => {
  const t = capture(read()).thread;
  assert.equal(t.correspondence_state, "awaiting_us");
  assert.deepEqual(t.participants.map(p => p.participant_ref).sort(),
    ["party:counterparty-test", "party:joe-test"]);
  assert.deepEqual(t.message_refs.map(m => m.provider_message_id), ["pm-1-test", "pm-2-test"]);
  assert.equal(t.message_refs[0].provider_thread_id, "pt-1-test");
  assert.deepEqual(t.attachments[0],
    { attachment_id: "att-1-test", media_type: "application/pdf", byte_length: 4096,
      content_digest: sha("c3") });
  assert.deepEqual(t.related_records, [{ record_kind: "deal", record_ref: "deal:test-0001" }]);
  assert.equal(t.started_at, "2026-09-10T15:00:00Z");
  assert.ok(t.thread_ref.startsWith("sha256:"));
});

test("the thread ref is bound to the native identity and the account, not to a label", () => {
  const a = read().thread.thread_ref;
  const b = read({ native_identity: { source_system: SOURCE, native_id: "thread-aaa-test",
    native_id_epoch: "epoch-2" } }).thread.thread_ref;
  assert.notEqual(a, b, "a recycled native id under a new epoch is a different thread");
});

test("an account outside the binding is refused, and so is a foreign source system", () => {
  assert.equal(capture(read({ account: DELL_ACCOUNT })).reason_id, "account_outside_binding");
  assert.equal(capture(read({
    native_identity: { source_system: "other_source", native_id: "x-test", native_id_epoch: "e" },
  })).reason_id, "native_identity_source_mismatch");
});

test("an adapter nobody has observed is unavailable, and unknown sits with unavailable", () => {
  for (const availability of V5_J103_NON_READING_AVAILABILITY) {
    const result = capture(read({}, binding({ availability })));
    assert.equal(result.classification, "would_unavailable");
    assert.equal(result.reason_id, "authorized_adapter_unavailable");
    assert.equal(result.availability, availability);
  }
  assert.deepEqual([...V5_J103_AVAILABILITY_STATES].sort(),
    ["available", "unavailable", "unknown"]);
});

test("privacy is S01's answer carried through, and it is evaluated before relevance", () => {
  const refused = capture(read({ declared_data_classes: ["phi"] }));
  const s01 = evaluatePrivacyBoundary({ data_classes: ["phi"] });
  assert.equal(refused.classification, "would_refuse");
  assert.equal(refused.reason_id, s01.reason_id);
  assert.deepEqual(refused.prohibited_classes, [...s01.prohibited_classes]);

  const routed = capture(read({ declared_data_classes: ["aggregate_patient_volume_estimate"] }));
  assert.equal(routed.classification, "would_needs_independent_privacy_route");
  assert.equal(routed.required_evidence,
    evaluatePrivacyBoundary({ data_classes: ["aggregate_patient_volume_estimate"] }).required_evidence);

  // The ordering claim: an UNRELATED thread carrying PHI still refuses on privacy,
  // which is only true if privacy ran first. Excluded-without-classification and
  // classified-then-excluded are different facts about the same item.
  const both = capture(read({ relevance_state: "unrelated", declared_data_classes: ["phi"] }));
  assert.equal(both.classification, "would_refuse");
  assert.equal(both.reason_id, "phi_or_raw_patient_location_refused");
});

test("unrelated mail is excluded and ambiguity stays private; neither falls through", () => {
  assert.equal(capture(read({ relevance_state: "unrelated" })).classification, "would_exclude_unrelated");
  const ambiguous = capture(read({ relevance_state: "ambiguous" }));
  assert.equal(ambiguous.classification, "would_withhold_ambiguous");
  assert.equal(ambiguous.reason_id, "ambiguity_remains_private");
  assert.equal(ambiguous.thread, null);
});

test("an unreadable thread is not a judgement: shape defects throw rather than answer", () => {
  assert.throws(() => read({ correspondence_state: "sort_of_open" }),
    e => e.code === "unknown_correspondence_state");
  assert.throws(() => read({ last_activity_at: "2026-09-09T00:00:00Z" }),
    e => e.code === "thread_time_inverted");
  assert.throws(() => read({ started_at: "2026-02-31T00:00:00Z" }),
    e => e.code === "invalid_timestamp");
  assert.throws(() => read({ participants: [
    { participant_ref: "party:joe-test", role: "originator", party_kind: "partner",
      address_digest: sha("a1") },
    { participant_ref: "party:joe-test", role: "copied", party_kind: "partner",
      address_digest: sha("a1") },
  ] }), e => e.code === "duplicate_participant");
  assert.throws(() => read({ message_refs: [
    { provider_thread_id: "pt-1-test", provider_message_id: "pm-1-test",
      occurred_at: "2026-09-10T15:00:00Z" },
    { provider_thread_id: "pt-1-test", provider_message_id: "pm-1-test",
      occurred_at: "2026-09-10T16:00:00Z" },
  ] }), e => e.code === "duplicate_provider_message_id");
  assert.throws(() => read({ message_refs: [
    { provider_thread_id: "pt-1-test", provider_message_id: "pm-9-test",
      occurred_at: "2027-01-01T00:00:00Z" },
  ] }), e => e.code === "message_occurs_after_now");
});

test("a thread observed after now refuses rather than being read", () => {
  assert.equal(capture(read({ observed_at: "2026-09-11T18:00:01Z" })).reason_id, "observed_after_now");
});

// ---------------------------------------------------------------------------
// The structural seams: content, credentials, dispatch, addresses.
// ---------------------------------------------------------------------------

test("raw correspondence never crosses the seam, refused by field name", () => {
  for (const field of ["body", "body_html", "message_text", "subject_line", "preview_snippet",
    "attachment_bytes"]) {
    assert.throws(() => probe.wouldRead({
      binding: binding(), thread: { ...thread(), [field]: "x" }, now: NOW,
    }), e => e instanceof V5J103Error && e.code === "source_content_must_not_cross_seam",
    `expected ${field} to be refused`);
  }
  assert.ok(V5_J103_SOURCE_CONTENT_FRAGMENTS.includes("subject"));
});

test("the attachment descriptor holds no filename and no bytes", () => {
  assert.throws(() => read({ attachments: [{ attachment_id: "att-1-test",
    media_type: "application/pdf", byte_length: 1, content_digest: sha("c3"),
    filename: "lease.pdf" }] }), e => e.code === "unknown_field");
});

test("a dispatch instruction is refused before any value is read", () => {
  for (const field of ["send_after", "dispatch_at", "deliver_to", "smtp_relay",
    "schedule_send_at", "recipient_address"]) {
    assert.throws(() => probe.wouldRead({
      binding: binding(), thread: { ...thread(), [field]: "x" }, now: NOW,
    }), e => e.code === "dispatch_instruction_refused", `expected ${field} to be refused`);
  }
  assert.ok(V5_J103_DISPATCH_FRAGMENTS.includes("send"));
});

test("a routable address anywhere but the partner's own account is refused", () => {
  // The value scan runs before the field validators, so the refusal names the
  // boundary that was crossed rather than the syntax rule that would also have
  // caught it.
  assert.throws(() => read({ participants: [
    { participant_ref: "counterparty@elsewhere.invalid", role: "principal_recipient",
      party_kind: "counterparty", address_digest: sha("b2") },
  ] }), e => e.code === "routable_address_refused");
  assert.throws(() => read({ related_records: [
    { record_kind: "party", record_ref: "mailto:someone" },
  ] }), e => e.code === "routable_address_refused");
  // And the narrow reference validator is the second line: a reference that is
  // not address-shaped but is still not a CARR reference refuses on its own terms.
  assert.throws(() => read({ related_records: [
    { record_kind: "party", record_ref: "party ref with spaces" },
  ] }), e => e.code === "invalid_reference");
  // The exemption is exactly one key wide, and it is pinned to the binding.
  assert.equal(read().classification, WOULD_READ, "the partner's own account may be an address");
  assert.equal(read({ account: DELL_ACCOUNT }).reason_id, "account_outside_binding");
});

test("a draft body carrying an address or a phone number is refused", () => {
  const b = binding();
  const r = read({}, b);
  assert.throws(() => probe.wouldDraft({ binding: b, thread_read: r, now: NOW,
    draft: draft({ draft_body: "Reply to counterparty@elsewhere.invalid with the terms." }) }),
  e => e.code === "routable_address_refused");
  assert.throws(() => probe.wouldDraft({ binding: b, thread_read: r, now: NOW,
    draft: draft({ draft_body: "Call them on +1 555 010 4477 to confirm." }) }),
  e => e.code === "routable_address_refused");
});

test("a guard can never refuse a field this module's own schemas require", () => {
  // The load-time self-check proves it; this asserts the case that makes the
  // asymmetry visible. `draft_body` contains "body" and is legitimate precisely
  // because the draft seam does not run the source-content scan.
  const b = binding();
  const drafted = capture(probe.wouldDraft({
    binding: b, thread_read: read({}, b), now: NOW, draft: draft(),
  }));
  assert.equal(drafted.classification, WOULD_DRAFT);
  assert.ok(V5_J103_SOURCE_CONTENT_FRAGMENTS.includes("body"));
});

// ---------------------------------------------------------------------------
// Coverage — the claim that is unreachable from every input.
// ---------------------------------------------------------------------------

test("both-partner coverage is unavailable from every input", () => {
  const b = binding();
  const combined = capture(probe.wouldCover({
    binding: b, requested_partner_slugs: ["joe", "dell"],
  }));
  assert.equal(combined.classification, "would_unavailable");
  assert.equal(combined.reason_id, "combined_partner_coverage_unavailable");

  const other = capture(probe.wouldCover({
    binding: b, requested_partner_slugs: ["dell"],
  }));
  assert.equal(other.classification, "would_unavailable");
  assert.equal(other.reason_id, "partner_outside_binding_not_covered");

  const own = capture(probe.wouldCover({
    binding: b, requested_partner_slugs: ["joe"],
  }));
  assert.equal(own.classification, WOULD_COVER);
  assert.equal(own.covered_partner_slug, "joe");
  assert.equal(own.combined_partner_coverage, "unavailable");
  assert.equal(own.coverage_gate_satisfied, false);
});

test("an unavailable adapter cannot even cover its own partner", () => {
  const result = capture(probe.wouldCover({
    binding: binding({ availability: "unknown" }), requested_partner_slugs: ["joe"],
  }));
  assert.equal(result.classification, "would_unavailable");
  assert.equal(result.reason_id, "authorized_adapter_unavailable");
});

test("every thread answer states single-partner coverage, including the refusals", () => {
  for (const result of [read(), read({ relevance_state: "unrelated" }),
    read({}, binding({ availability: "unavailable" }))]) {
    assert.equal(result.partner_coverage.combined_partner_coverage, "unavailable");
    assert.equal(result.partner_coverage.covered_partner_slug, "joe");
  }
});

// ---------------------------------------------------------------------------
// checkable_done 3 — drafts and proposed facts cannot dispatch a provider effect.
// ---------------------------------------------------------------------------

test("a draft is produced for a human to send and names no provider operation", () => {
  const b = binding();
  const result = capture(probe.wouldDraft({
    binding: b, thread_read: read({}, b), now: NOW, draft: draft(),
  }));
  assert.equal(result.classification, WOULD_DRAFT);
  assert.equal(result.schema_version, V5_J103_DRAFT_SCHEMA_VERSION);
  assert.equal(result.dispatchable, false);
  assert.equal(result.provider_operation, null);
  assert.equal(result.requires_human_send, true);
  assert.equal(result.send_authority_holder, V5_J103_SEND_AUTHORITY_HOLDER);
  assert.equal(result.send_authority_seam, V5_J103_SEND_AUTHORITY_SEAM);
  assert.equal(result.model_seam, "correspondence_draft");
  assert.equal(result.draft.body_digest, digest(draft().draft_body));
  assert.deepEqual(result.draft.intended_participant_refs, ["party:counterparty-test"]);
  assert.equal(result.effects, V5_NO_EFFECTS);
});

test("the send refusal is proved where send is owned, not by this module's own say-so", () => {
  // F10 is the independent oracle: it holds the connector operation registry, and
  // every write in it refuses by name. A J103 draft changes nothing about that.
  const installation = compilePartnerInstallation({
    partner_slug: "joe", device_id: "device-test-1", account: JOE_ACCOUNT,
    source_system: SOURCE, item_kinds: ["mail_message"], deployment_state: "deployed",
    installation_version: 1,
  });
  for (const operation of V5_F10_WRITE_OPERATIONS) {
    const answer = evaluateConnectorOperation({ installation, operation });
    assert.equal(answer.decision, "refuse", `${operation} must refuse`);
    assert.equal(answer.reason_id, "write_capability_not_in_this_slice");
  }
  assert.ok(V5_F10_WRITE_OPERATIONS.includes("send_mail_message"));
  // And J103 names none of them anywhere in its own policy.
  const policyText = v5J103PolicyCanonicalBytes();
  for (const operation of v5J103AbsentWriteOperations()) {
    assert.ok(!policyText.includes(`"${operation}"`),
      `J103 policy must not name the write operation ${operation}`);
  }
  assert.deepEqual(v5J103PolicyPreimage().draft_and_proposal.send_operations_here, []);
});

test("no result this suite produced is dispatchable or carries a provider operation", () => {
  assert.ok(PRODUCED_RESULTS.length > 12, "the sweep needs results to sweep");
  for (const result of PRODUCED_RESULTS) {
    assert.equal(result.dispatchable, false);
    assert.equal(result.provider_operation, null);
    assert.equal(result.effects, V5_NO_EFFECTS);
    assert.ok(Object.isFrozen(result));
  }
});

test("a withheld or excluded thread cannot become a draft or a proposal", () => {
  const b = binding();
  for (const overrides of [{ relevance_state: "ambiguous" }, { relevance_state: "unrelated" },
    { declared_data_classes: ["phi"] }]) {
    const r = read(overrides, b);
    assert.throws(() => probe.wouldDraft({ binding: b, thread_read: r, now: NOW, draft: draft() }),
      e => e.code === "work_from_non_readable_thread");
    assert.throws(() => probe.wouldProposeFact({ binding: b, thread_read: r, now: NOW,
      proposal: proposal() }), e => e.code === "work_from_non_readable_thread");
  }
});

test("a hand-built read result claiming a withheld thread is still refused", () => {
  // The mutation check found this one: every genuine non-read answer also carries
  // `thread: null`, so the decision check looks redundant until a caller forges a
  // result. It is the case the check exists for, and now the case the suite holds.
  const b = binding();
  const genuine = read({}, b);
  const forged = { ...read({ relevance_state: "ambiguous" }, b), thread: genuine.thread };
  assert.throws(() => probe.wouldDraft({
    binding: b, thread_read: forged, now: NOW, draft: draft(),
  }), e => e.code === "work_from_non_readable_thread");
  assert.throws(() => probe.wouldProposeFact({
    binding: b, thread_read: forged, now: NOW, proposal: proposal(),
  }), e => e.code === "work_from_non_readable_thread");
});

test("one partner's thread is not another partner's to draft from", () => {
  const joe = binding();
  const dell = binding({ partner_slug: "dell", account: DELL_ACCOUNT });
  const joeRead = read({}, joe);
  assert.throws(() => probe.wouldDraft({
    binding: dell, thread_read: joeRead, now: NOW, draft: draft(),
  }), e => e.code === "thread_outside_binding");
});

test("a recipient who is not on the thread is refused rather than quietly added", () => {
  const b = binding();
  const result = capture(probe.wouldDraft({
    binding: b, thread_read: read({}, b), now: NOW,
    draft: draft({ intended_participant_refs: ["party:someone-else-test"] }),
  }));
  assert.equal(result.classification, "would_refuse");
  assert.equal(result.reason_id, "recipient_outside_thread");
  assert.deepEqual(result.unknown_participant_refs, ["party:someone-else-test"]);
  assert.equal(result.draft, null);
});

test("a reply into a resolved thread, and a draft dated before the read, both refuse", () => {
  const b = binding();
  const resolved = read({ correspondence_state: "resolved" }, b);
  assert.equal(capture(probe.wouldDraft({
    binding: b, thread_read: resolved, now: NOW, draft: draft(),
  })).reason_id, "reply_into_resolved_thread_refused");

  assert.equal(capture(probe.wouldDraft({
    binding: b, thread_read: read({}, b), now: "2026-09-10T17:59:00Z", draft: draft(),
  })).reason_id, "draft_precedes_thread_observation");
});

test("a draft carrying a prohibited class refuses on S01's answer", () => {
  const b = binding();
  const result = capture(probe.wouldDraft({
    binding: b, thread_read: read({}, b), now: NOW,
    draft: draft({ declared_data_classes: ["patient_record"] }),
  }));
  assert.equal(result.classification, "would_refuse");
  assert.equal(result.reason_id, "phi_or_raw_patient_location_refused");
  assert.equal(result.draft, null);
  assert.equal(result.dispatchable, false);
});

// ---------------------------------------------------------------------------
// Proposed facts — inferred, never established.
// ---------------------------------------------------------------------------

test("a proposed fact cites the message it came from and establishes nothing", () => {
  const b = binding();
  const result = capture(probe.wouldProposeFact({
    binding: b, thread_read: read({}, b), now: NOW, proposal: proposal(),
  }));
  assert.equal(result.classification, WOULD_PROPOSE);
  assert.equal(result.schema_version, V5_J103_PROPOSAL_SCHEMA_VERSION);
  assert.equal(result.applied, false);
  assert.equal(result.authority_established, false);
  assert.equal(result.next_authority_step, "record-source-authority.v5.js resolveObservation");
  assert.equal(result.model_seam, "proposed_fact");
  assert.equal(result.observation_candidate.observed_at, "2026-09-10T17:30:00Z");
  assert.equal(result.observation_candidate.taint_class, V5_J103_TAINT_CLASS);
  assert.equal(result.observation_candidate.account, JOE_ACCOUNT);
});

test("a proposal citing a message that is not on the thread is refused", () => {
  const b = binding();
  const result = capture(probe.wouldProposeFact({
    binding: b, thread_read: read({}, b), now: NOW,
    proposal: proposal({ evidence_provider_message_id: "pm-99-test" }),
  }));
  assert.equal(result.classification, "would_refuse");
  assert.equal(result.reason_id, "evidence_message_not_on_thread");
  assert.equal(result.observation_candidate, null);
});

test("a proposal citing evidence from after now is refused, naming the evidence", () => {
  const b = binding();
  const r = read({}, b);
  const result = capture(probe.wouldProposeFact({
    binding: b, thread_read: r, now: "2026-09-10T16:00:00Z", proposal: proposal(),
  }));
  assert.equal(result.reason_id, "evidence_message_occurs_after_now");
});

test("a proposal carrying a field that claims authority is refused", () => {
  const b = binding();
  const r = read({}, b);
  for (const field of ["authorized_by", "approved_by_partner", "owner_override", "acting_as"]) {
    assert.throws(() => probe.wouldProposeFact({
      binding: b, thread_read: r, now: NOW, proposal: { ...proposal(), [field]: "joe" },
    }), e => e.code === "authority_claim_in_proposal", `expected ${field} to be refused`);
  }
});

test("ordinary business prose is not an authority claim", () => {
  const b = binding();
  const result = capture(probe.wouldProposeFact({
    binding: b, thread_read: read({}, b), now: NOW,
    proposal: proposal({
      rationale: "The landlord said they would grant an extension once the admin fee is approved.",
    }),
  }));
  assert.equal(result.classification, WOULD_PROPOSE,
    "the authority guard reads field names, not the words a broker uses");
});

test("the observation candidate is one F01 itself accepts", () => {
  // F01 is the independent oracle for the observation shape: if resolveObservation
  // can read the candidate and reach a decision about the FIELD rather than about
  // the request, the mapping is right. It is not graded by the code that built it.
  const b = binding();
  const result = probe.wouldProposeFact({
    binding: b, thread_read: read({}, b), now: NOW, proposal: proposal(),
  });
  const resolved = resolveObservation({
    tenant: ORGANIZATION_TENANT_ID,
    registry: registry(),
    observation: { ...result.observation_candidate, version: 1 },
    now: NOW,
  });
  assert.equal(resolved.entity, "correspondence_derived_term");
  assert.equal(resolved.field, "base_rent_psf");
  // The adapter is a permitted inbound source but not the owner, so F01 refuses
  // the establishment. That is the correct answer and the point: J103 proposed,
  // F01 decided, and the decision was no.
  assert.equal(resolved.decision, "refuse");
  assert.equal(resolved.reason_id, "non_owner_cannot_establish_field");
  assert.equal(resolved.applied, false);
  assert.equal(resolved.silent_last_write_wins, false);
});

// ---------------------------------------------------------------------------
// checkable_done 2 — conflicts show both values and the owning source.
// ---------------------------------------------------------------------------

/** Drive F01 until it emits each conflict kind, so the route table is checked against reality. */
function f01Conflicts() {
  const reg = registry();
  const owner = "carr_record_layer_test";
  const nativeIdentity = system => ({
    source_system: system, native_id: "term-1-test", native_id_epoch: "epoch-1",
  });
  const observation = (source_system, over = {}) => ({
    entity: "correspondence_derived_term",
    field: "base_rent_psf",
    tenant: ORGANIZATION_TENANT_ID,
    source_system,
    account: "acct-test-1",
    native_identity: nativeIdentity(source_system),
    value_digest: sha("aa"),
    version: 2,
    observed_at: "2026-09-10T12:00:00Z",
    provenance: { adapter_kind: "adapter_test", evidence_ref: "ev-1-test",
      retrieval_class: "class_test" },
    declared_data_classes: ["lease_economics"],
    taint_class: "untrusted_external",
    ...over,
  });
  const current = (over = {}) => ({
    entity: "correspondence_derived_term",
    field: "base_rent_psf",
    tenant: ORGANIZATION_TENANT_ID,
    account: "acct-test-1",
    // F01 requires the established record's native identity to belong to the
    // source that owns the field; it is the owner's record, not the adapter's.
    native_identity: nativeIdentity(owner),
    value_digest: sha("bb"),
    version: 2,
    owner_source: owner,
    observed_at: "2026-09-09T12:00:00Z",
    event_seq: 1,
    last_event_digest: sha("cc"),
    ...over,
  });
  const items = {};
  const add = result => {
    assert.ok(result.reconciliation_item, `expected a reconciliation item, got ${result.reason_id}`);
    items[result.reconciliation_item.conflict_kind] = result.reconciliation_item;
  };
  // Nothing established, and a non-owner tries to establish it.
  add(resolveObservation({ tenant: ORGANIZATION_TENANT_ID, registry: reg,
    observation: observation(SOURCE), now: NOW }));
  // Same version, different value.
  add(resolveObservation({ tenant: ORGANIZATION_TENANT_ID, registry: reg,
    observation: observation(SOURCE), current_state: current(), now: NOW }));
  // Newer version from a non-owner with a different value.
  add(resolveObservation({ tenant: ORGANIZATION_TENANT_ID, registry: reg,
    observation: observation(SOURCE, { version: 3 }), current_state: current(), now: NOW }));
  // An opaque comparator cannot order two different tokens.
  const opaque = registry({ version_comparator: "opaque_equality" });
  add(resolveObservation({ tenant: ORGANIZATION_TENANT_ID, registry: opaque,
    observation: observation(SOURCE, { version: "etag-b-test" }),
    current_state: current({ version: "etag-a-test" }), now: NOW }));
  return items;
}

test("the four conflict kinds this module routes are exactly the four F01 emits", () => {
  const emitted = Object.keys(f01Conflicts()).sort();
  assert.deepEqual(emitted, [...V5_J103_CONFLICT_KINDS],
    "the route table must cover exactly F01's vocabulary, no more and no less");
  for (const kind of emitted) {
    assert.ok(["human_business_review", "source_authority_review"]
      .includes(V5_J103_CONFLICT_ROUTES[kind]));
  }
});

test("a queued conflict shows both values, each labelled with the source that owns it", () => {
  const item = f01Conflicts().equal_version_contradiction;
  const entry = capture(probe.wouldQueueConflict({
    reconciliation_item: item,
    presented_values: {
      established: { value_text: "32.50", value_digest: item.established.value_digest,
        declared_data_classes: ["lease_economics"] },
      observed: { value_text: "33.75", value_digest: item.observed.value_digest,
        declared_data_classes: ["lease_economics"] },
    },
    now: NOW,
  }));
  assert.equal(entry.classification, WOULD_QUEUE);
  assert.equal(entry.schema_version, V5_J103_CONFLICT_SCHEMA_VERSION);
  assert.equal(entry.both_values_visible, true);
  assert.equal(entry.sides.established.value_text, "32.50");
  assert.equal(entry.sides.observed.value_text, "33.75");
  assert.equal(entry.sides.established.source_system, "carr_record_layer_test");
  assert.equal(entry.sides.established.owns_the_field, true);
  assert.equal(entry.sides.observed.source_system, SOURCE);
  assert.equal(entry.sides.observed.owns_the_field, false);
  assert.equal(entry.owner_source, "carr_record_layer_test");
  assert.equal(entry.authoritative_home, "neon_record_layer");
  assert.equal(entry.human_resolver_class, "partner_business_review");
  assert.equal(entry.route, "human_business_review");
  assert.equal(entry.resolved_by_machine, false);
  assert.equal(entry.applied, false);
});

test("the owning side is computed from F01's owner_source, not asserted by the caller", () => {
  const item = f01Conflicts().forbidden_overwrite_by_non_owner;
  const entry = capture(probe.wouldQueueConflict({
    reconciliation_item: item,
    presented_values: {
      established: { value_text: "owner value", value_digest: item.established.value_digest,
        declared_data_classes: ["lease_economics"] },
      observed: { value_text: "adapter value", value_digest: item.observed.value_digest,
        declared_data_classes: ["lease_economics"] },
    },
    now: NOW,
  }));
  assert.equal(entry.sides.observed.owns_the_field, false);
  assert.equal(entry.route, "source_authority_review");
});

test("a conflict with no established side presents one value and says so", () => {
  const item = f01Conflicts().non_owner_establishment_attempt;
  assert.equal(item.established, null);
  const entry = capture(probe.wouldQueueConflict({
    reconciliation_item: item,
    presented_values: {
      observed: { value_text: "33.75", value_digest: item.observed.value_digest,
        declared_data_classes: ["lease_economics"] },
    },
    now: NOW,
  }));
  assert.equal(entry.classification, WOULD_QUEUE);
  assert.equal(entry.both_values_visible, false);
  assert.equal(entry.sides.established, null);
});

test("a review surface may not invent a side of the conflict", () => {
  const item = f01Conflicts().non_owner_establishment_attempt;
  assert.throws(() => probe.wouldQueueConflict({
    reconciliation_item: item,
    presented_values: {
      established: { value_text: "invented", value_digest: sha("ee"),
        declared_data_classes: ["lease_economics"] },
      observed: { value_text: "33.75", value_digest: item.observed.value_digest,
        declared_data_classes: ["lease_economics"] },
    },
    now: NOW,
  }), e => e.code === "presented_value_without_conflict_side");
});

test("a presented value that is not the value F01 conflicted on is refused", () => {
  const item = f01Conflicts().equal_version_contradiction;
  const wrongObserved = capture(probe.wouldQueueConflict({
    reconciliation_item: item,
    presented_values: {
      established: { value_text: "32.50", value_digest: item.established.value_digest,
        declared_data_classes: ["lease_economics"] },
      observed: { value_text: "a value nobody observed", value_digest: sha("ff"),
        declared_data_classes: ["lease_economics"] },
    },
    now: NOW,
  }));
  assert.equal(wrongObserved.classification, "would_refuse");
  assert.equal(wrongObserved.reason_id, "presented_value_digest_mismatch");
  assert.equal(wrongObserved.side, "observed");
  assert.equal(wrongObserved.sides, null);

  const wrongEstablished = capture(probe.wouldQueueConflict({
    reconciliation_item: item,
    presented_values: {
      established: { value_text: "a value nobody established", value_digest: sha("ef"),
        declared_data_classes: ["lease_economics"] },
      observed: { value_text: "33.75", value_digest: item.observed.value_digest,
        declared_data_classes: ["lease_economics"] },
    },
    now: NOW,
  }));
  assert.equal(wrongEstablished.reason_id, "presented_value_digest_mismatch");
  assert.equal(wrongEstablished.side, "established");
});

test("every way of saying whoever wrote last wins is refused by name", () => {
  const item = f01Conflicts().equal_version_contradiction;
  const sides = {
    established: { value_text: "32.50", value_digest: item.established.value_digest,
      declared_data_classes: ["lease_economics"] },
    observed: { value_text: "33.75", value_digest: item.observed.value_digest,
      declared_data_classes: ["lease_economics"] },
  };
  for (const basis of V5_J103_REFUSED_RESOLUTION_BASES) {
    const entry = capture(probe.wouldQueueConflict({
      reconciliation_item: item, presented_values: sides,
      proposed_resolution_basis: basis, now: NOW,
    }));
    assert.equal(entry.classification, "would_refuse", `${basis} must refuse`);
    assert.equal(entry.reason_id, "timestamp_alone_refused");
    assert.equal(entry.sides, null);
  }
  const unregistered = capture(probe.wouldQueueConflict({
    reconciliation_item: item, presented_values: sides,
    proposed_resolution_basis: "whichever_looks_right", now: NOW,
  }));
  assert.equal(unregistered.reason_id, "unregistered_resolution_basis");

  const accepted = capture(probe.wouldQueueConflict({
    reconciliation_item: item, presented_values: sides,
    proposed_resolution_basis: V5_J103_RESOLUTION_BASIS, now: NOW,
  }));
  assert.equal(accepted.classification, WOULD_QUEUE);
  assert.equal(accepted.timestamp_alone_is_not_authority, true);
});

test("a queue entry is built only from an item F01 emitted", () => {
  const item = f01Conflicts().equal_version_contradiction;
  assert.equal(item.schema_version, V5_F01_RECONCILIATION_SCHEMA_VERSION);
  assert.throws(() => probe.wouldQueueConflict({
    reconciliation_item: { ...item, schema_version: "something-else.v1" },
    presented_values: { observed: { value_text: "x", value_digest: item.observed.value_digest,
      declared_data_classes: ["lease_economics"] } },
    now: NOW,
  }), e => e.code === "uncompiled_reconciliation_item");
});

test("a conflict queued before the observation that caused it is refused", () => {
  const item = f01Conflicts().equal_version_contradiction;
  const entry = capture(probe.wouldQueueConflict({
    reconciliation_item: item,
    presented_values: {
      established: { value_text: "32.50", value_digest: item.established.value_digest,
        declared_data_classes: ["lease_economics"] },
      observed: { value_text: "33.75", value_digest: item.observed.value_digest,
        declared_data_classes: ["lease_economics"] },
    },
    now: "2026-09-10T11:00:00Z",
  }));
  assert.equal(entry.reason_id, "conflict_observed_after_now");
});

test("a presented value carrying a prohibited class refuses on S01's answer", () => {
  const item = f01Conflicts().equal_version_contradiction;
  const entry = capture(probe.wouldQueueConflict({
    reconciliation_item: item,
    presented_values: {
      established: { value_text: "32.50", value_digest: item.established.value_digest,
        declared_data_classes: ["lease_economics"] },
      observed: { value_text: "33.75", value_digest: item.observed.value_digest,
        declared_data_classes: ["patient_identifier"] },
    },
    now: NOW,
  }));
  assert.equal(entry.classification, "would_refuse");
  assert.equal(entry.reason_id, "phi_or_raw_patient_location_refused");
  assert.equal(entry.sides, null);
});

// ---------------------------------------------------------------------------
// The policy digest, the projection and the gaps.
// ---------------------------------------------------------------------------

test("the policy digest is deterministic and hashes the bytes it publishes", () => {
  assert.equal(v5J103PolicyDigest(), v5J103PolicyDigest());
  assert.equal(v5J103PolicyDigest(), digest(v5J103PolicyPreimage()));
  assert.equal(v5J103PolicyCanonicalBytes(), canonicalJson(v5J103PolicyPreimage()));
});

test("the policy binds the prohibitions, not only the capabilities", () => {
  const policy = v5J103PolicyPreimage();
  assert.equal(policy.read_interface.source_agnostic, true);
  assert.equal(policy.read_interface.subject_line_stored, false);
  assert.equal(policy.read_interface.attachment_filename_stored, false);
  assert.equal(policy.read_interface.privacy_evaluated_before_relevance, true);
  assert.equal(policy.coverage.combined_partner_coverage_reachable_here, false);
  assert.equal(policy.draft_and_proposal.holds_routable_destination, false);
  assert.equal(policy.draft_and_proposal.names_provider_operation, false);
  assert.equal(policy.draft_and_proposal.establishes_authority, false);
  assert.equal(policy.source_reconciliation.resolved_by_machine, false);
  assert.deepEqual(policy.source_reconciliation.refused_resolution_bases,
    [...V5_J103_REFUSED_RESOLUTION_BASES]);
  assert.equal(policy.acceptance.satisfied_by_this_module, false);
  assert.deepEqual(policy.requirement_ids, ["Q005", "Q076", "Q133", "Q134"]);
});

test("a stale expected digest refuses rather than serving the projection", () => {
  const projection = v5J103CorrespondenceProjection({
    expected_policy_digest: v5J103PolicyDigest(),
  });
  assert.equal(projection.policy_digest, v5J103PolicyDigest());
  assert.equal(projection.accepts_anything, false);
  assert.equal(projection.external_send_capability_present, false);
  assert.equal(projection.combined_partner_coverage_available, false);
  assert.equal(projection.journey_one_production_outcome_present, false);
  assert.throws(() => v5J103CorrespondenceProjection({ expected_policy_digest: sha("99") }),
    e => e.code === "stale_expected_digest");
});

test("every named gap is a missing fact, and none of them is landed", () => {
  const gaps = governedCorrespondenceGaps();
  assert.ok(gaps.length >= 5);
  for (const gap of gaps) {
    assert.equal(gap.landed, false);
    assert.ok(gap.gap.length > 0 && gap.where.length > 0 && gap.what.length > 0);
  }
  assert.ok(gaps.some(g => g.gap === "no_external_send_authority_decision"));
  assert.ok(gaps.some(g => g.gap === "no_persistence"));
  assert.ok(gaps.some(g => g.gap === "no_both_partner_coverage"));
});

test("the module reaches no clock, network, database or filesystem", () => {
  // Every evaluation takes `now` from the caller, and the same inputs answer the
  // same way whatever the machine's clock says.
  const b = binding();
  assert.deepEqual(read({}, b), read({}, b));
  assert.deepEqual(
    probe.wouldDraft({ binding: b, thread_read: read({}, b), now: NOW, draft: draft() }),
    probe.wouldDraft({ binding: b, thread_read: read({}, b), now: NOW, draft: draft() }));
});

test("correspondence states and the module's own vocabulary stay closed", () => {
  assert.deepEqual([...V5_J103_CORRESPONDENCE_STATES],
    ["awaiting_counterparty", "awaiting_us", "informational", "resolved"]);
  assert.throws(() => read({ relevance_state: "probably_relevant" }),
    e => e.code === "unknown_relevance_state");
  assert.throws(() => probe.wouldRead({
    binding: binding(), thread: { ...thread(), extra_field: 1 }, now: NOW,
  }), e => e.code === "unknown_field");
});

// ---------------------------------------------------------------------------
// Review 983, defect 1 — `availability: "available"` was the CALLER'S word and it
// unlocked the read and everything downstream of it.
//
// The rule these tests hold: while no owner issues an adapter read receipt, no
// combination of caller inputs — an availability string, a hand-built binding, a
// correctly recomputed digest, a forged read result — reaches `read`, `covered`,
// `drafted` or `proposed`. Honestly deferred means unreachable, not reachable by
// saying the magic word.
// ---------------------------------------------------------------------------

/** Run one attempt and report what came back, whether it answered or threw. */
function attempt(fn) {
  try {
    const value = fn();
    return { threw: false, value, decision: value.decision, code: null };
  } catch (error) {
    return { threw: true, value: null, decision: null, code: error.code ?? null, error };
  }
}

/**
 * Build a compiled-looking binding from whole cloth and hash it correctly.
 *
 * This is the forger's entire kit and it is not a lot: the compiled shape is
 * public, the preimage is published by correspondenceBindingCanonicalBytes, and
 * `digest` is an ordinary import. A self-consistent digest is therefore free to
 * anyone — which is exactly why availability cannot rest on one.
 */
function forgeBinding(overrides = {}) {
  const adapter = V5_J103_ADAPTERS[V5_F10_ADAPTER_KIND];
  const forged = {
    compiled: true,
    wired: true,
    schema_version: V5_J103_BINDING_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    partner_slug: "joe",
    adapter_kind: V5_F10_ADAPTER_KIND,
    adapter_mode: adapter.mode,
    authoritative_home: adapter.authoritative_home,
    retrieval_class: adapter.retrieval_class,
    account: JOE_ACCOUNT,
    source_system: SOURCE,
    claimed_availability: "available",
    availability: "available",
    availability_source: "owner_issued_adapter_read_receipt",
    availability_owed_seam: null,
    binding_version: 1,
    ...overrides,
  };
  const binding_digest = digest({
    schema_version: forged.schema_version,
    wired: forged.wired,
    tenant: forged.tenant,
    partner_slug: forged.partner_slug,
    adapter_kind: forged.adapter_kind,
    adapter_mode: forged.adapter_mode,
    authoritative_home: forged.authoritative_home,
    retrieval_class: forged.retrieval_class,
    account: forged.account,
    source_system: forged.source_system,
    claimed_availability: forged.claimed_availability,
    availability: forged.availability,
    availability_source: forged.availability_source,
    availability_owed_seam: forged.availability_owed_seam,
    binding_version: forged.binding_version,
  });
  return { ...forged, binding_digest };
}

test("availability is asked of an authority that has nothing to issue, whatever it is asked", () => {
  const queries = [
    {},
    { partner_slug: "joe", adapter_kind: V5_F10_ADAPTER_KIND },
    { partner_slug: "joe", availability: "available" },
    { receipt: { availability: "available", issued_by: "the caller" } },
    { availability: "available", binding_digest: sha("ab"), attestation: sha("cd") },
  ];
  for (const query of queries) {
    const resolved = resolveAdapterAvailability(query);
    assert.equal(resolved.availability, "unavailable", JSON.stringify(query));
    assert.equal(resolved.availability_source, "no_adapter_read_receipt");
    assert.equal(resolved.availability_owed_seam, V5_J103_ADAPTER_READ_RECEIPT_SEAM);
  }
});

test("the wired compiler records the caller's availability as a claim and derives its own", () => {
  for (const claimed of V5_J103_AVAILABILITY_STATES) {
    const b = wiredBinding({ availability: claimed });
    assert.equal(b.claimed_availability, claimed);
    assert.equal(b.availability, "unavailable", `claiming ${claimed} must not make it so`);
    assert.equal(b.availability_source, "no_adapter_read_receipt");
    assert.equal(b.availability_owed_seam, V5_J103_ADAPTER_READ_RECEIPT_SEAM);
    assert.equal(b.wired, true);
  }
  // The unavailable answer names the seam that is owed rather than implying a
  // permission somewhere could change it.
  const answer = capture(readCorrespondenceThread({
    binding: wiredBinding(), thread: thread(), now: NOW,
  }));
  assert.equal(answer.decision, "unavailable");
  assert.equal(answer.reason_id, "authorized_adapter_unavailable");
  assert.equal(answer.owed_seam, V5_J103_ADAPTER_READ_RECEIPT_SEAM);
  assert.equal(answer.claimed_availability, "available");
  assert.equal(answer.thread, null);
  assert.equal(answer.wired, true);
});

test("no availability value, binding shape or digest trick reaches read, covered, drafted or proposed", () => {
  const shapes = [
    ["the wired compiler told the adapter is available",
      () => wiredBinding({ availability: "available" })],
    ["the wired compiler told unknown", () => wiredBinding({ availability: "unknown" })],
    ["the wired compiler told unavailable", () => wiredBinding({ availability: "unavailable" })],
    ["a fixture binding spent on the wired interface", () => binding()],
    ["a forged compiled binding, digest recomputed to match", () => forgeBinding()],
    ["a forged binding claiming an owner-issued receipt source",
      () => forgeBinding({ availability_source: "owner_issued_adapter_read_receipt" })],
    ["a forged binding whose claim and derivation disagree",
      () => forgeBinding({ claimed_availability: "unknown" })],
    ["a forged binding with a stale digest",
      () => ({ ...forgeBinding(), binding_digest: sha("ff") })],
    ["a compiled binding edited to available after the fact",
      () => ({ ...wiredBinding(), availability: "available" })],
    ["a compiled binding edited to claim the receipt source",
      () => ({ ...wiredBinding(), availability_source: "owner_issued_adapter_read_receipt" })],
    ["a fixture binding repainted as wired", () => ({ ...binding(), wired: true })],
  ];

  for (const [label, make] of shapes) {
    const b = make();

    const readAttempt = attempt(() => readCorrespondenceThread({
      binding: b, thread: thread(), now: NOW,
    }));
    assert.notEqual(readAttempt.decision, "read", `${label}: reached a read`);
    if (!readAttempt.threw) {
      assert.equal(readAttempt.decision, "unavailable", label);
      assert.equal(readAttempt.value.owed_seam, V5_J103_ADAPTER_READ_RECEIPT_SEAM, label);
      assert.equal(readAttempt.value.thread, null, label);
      capture(readAttempt.value);
    }

    for (const slugs of [["joe"], ["joe", "dell"], ["dell"]]) {
      const coverage = attempt(() => projectCorrespondenceCoverage({
        binding: b, requested_partner_slugs: slugs,
      }));
      assert.notEqual(coverage.decision, "covered", `${label}: covered ${slugs.join("+")}`);
      if (!coverage.threw) capture(coverage.value);
    }

    // Every read result a caller can lay hands on, including ones shaped to pass
    // each check the draft seam makes in turn.
    const fixtureRead = read();
    const candidates = [
      ["the wired answer itself", readAttempt.value],
      ["an unwired predicate result", fixtureRead],
      ["an unwired result repainted as wired", { ...fixtureRead, wired: true }],
      ["an unwired result repainted and re-bound",
        { ...fixtureRead, wired: true, binding_digest: b.binding_digest }],
      ["a wholly hand-built read", {
        ...fixtureRead, wired: true, binding_digest: b.binding_digest,
        decision: "read", reason_id: "relevant_business_context_within_boundary",
      }],
    ];
    for (const [candidateLabel, thread_read] of candidates) {
      if (thread_read === null) continue;
      const drafted = attempt(() => draftCorrespondence({
        binding: b, thread_read, now: NOW, draft: draft(),
      }));
      assert.notEqual(drafted.decision, "drafted", `${label} / ${candidateLabel}: drafted`);
      if (!drafted.threw) capture(drafted.value);
      const proposed = attempt(() => evaluateProposedFact({
        binding: b, thread_read, now: NOW, proposal: proposal(),
      }));
      assert.notEqual(proposed.decision, "proposed", `${label} / ${candidateLabel}: proposed`);
      if (!proposed.threw) capture(proposed.value);
    }
  }
});

test("a read result shaped like this module's own is refused, because it is not one", () => {
  // The check the field-by-field ones cannot make. Every field on a read result is
  // a field a caller could have typed, so the draft seam asks a question the caller
  // cannot answer: did this object come out of this module?
  const b = binding();
  const genuine = read({}, b);
  // The forger's best copy: every field of a genuine classification, with the
  // privileged verdict typed back in by hand where the rename took it out. This is
  // the most complete read result a caller can build, and it is still not one.
  const { classification, classification_only, not_an_outcome, ...fields } = genuine;
  assert.equal(classification, WOULD_READ);
  assert.equal(classification_only, true);
  assert.equal(not_an_outcome, true);
  const copy = { ...fields, decision: "read" };
  assert.deepEqual(Object.keys(copy).sort().filter(k => k !== "decision"),
    Object.keys(genuine).sort()
      .filter(k => !["classification", "classification_only", "not_an_outcome"].includes(k)),
    "the copy carries every field a genuine result carries");
  assert.throws(() => probe.wouldDraft({
    binding: b, thread_read: copy, now: NOW, draft: draft(),
  }), e => e instanceof V5J103Error && e.code === "thread_read_not_produced_here");
  assert.throws(() => probe.wouldProposeFact({
    binding: b, thread_read: copy, now: NOW, proposal: proposal(),
  }), e => e.code === "thread_read_not_produced_here");
  // And the genuine one still works, so the check is not refusing everything.
  assert.equal(probe.wouldDraft({
    binding: b, thread_read: genuine, now: NOW, draft: draft(),
  }).classification, WOULD_DRAFT);
});

test("the predicates are marked not wired, and neither path is a door into the other", () => {
  assert.equal(read().wired, false);
  assert.equal(binding().wired, false);
  assert.equal(binding().availability_source, "fixture_declared_not_wired");
  assert.equal(probe.wouldCover({
    binding: binding(), requested_partner_slugs: ["joe"],
  }).wired, false);

  assert.throws(() => readCorrespondenceThread({ binding: binding(), thread: thread(), now: NOW }),
    e => e.code === "binding_mode_mismatch", "a fixture binding is not a binding");
  assert.throws(() => probe.wouldRead({
    binding: wiredBinding(), thread: thread(), now: NOW,
  }), e => e.code === "binding_mode_mismatch", "a wired binding is not a fixture");
  assert.throws(() => probe.wouldDraft({
    binding: binding(),
    thread_read: { ...read(), wired: true },
    now: NOW,
    draft: draft(),
  }), e => e.code === "thread_read_mode_mismatch");
});

// ---------------------------------------------------------------------------
// Review 983, defect 2 — a caller-provided reconciliation item was accepted on its
// schema-version string alone, and its caller-provided owner, home, conflict kind
// and sides could then put a visible `queued` review prompt in front of a partner.
// That contradicted this module's own gap list, which says F01 ships no mailbox
// field-authority registry at all.
// ---------------------------------------------------------------------------

/** The presented values that match an item's digests, so the fixture is queue-worthy. */
function presentedFor(item) {
  const side = digestValue => ({
    value_text: "42.50", value_digest: digestValue, declared_data_classes: ["lease_economics"],
  });
  return item.established === null || item.established === undefined
    ? { observed: side(item.observed.value_digest) }
    : { observed: side(item.observed.value_digest),
      established: side(item.established.value_digest) };
}

/**
 * Well-formed items varying every field a caller controls: who owns the field,
 * where the record lives, which conflict kind and therefore which review route,
 * who resolves it, and which values are in conflict. Each one is built from an
 * item F01 really emitted, so none of them can be dismissed as malformed.
 */
function forgedReconciliationItems() {
  const emitted = f01Conflicts();
  const genuine = emitted.equal_version_contradiction;
  const variations = [
    ["untouched, straight from F01", genuine],
    ["the adapter named as the owner of the field",
      { ...genuine, owner_source: SOURCE }],
    ["the mailbox named as the authoritative home",
      { ...genuine, authoritative_home: "outlook" }],
    ["a resolver class nobody registered",
      { ...genuine, human_resolver_class: "resolve_it_automatically" }],
    ["a different entity and field entirely",
      { ...genuine, entity: "partner_compensation", field: "commission_split" }],
    ["no established side, so one value is presented alone",
      { ...genuine, established: null }],
    ["the observed side relabelled as the owner's own record",
      { ...genuine, observed: { ...genuine.observed, source_system: genuine.owner_source } }],
  ];
  for (const kind of V5_J103_CONFLICT_KINDS) {
    variations.push([`routed as ${kind}`, { ...emitted[kind] }]);
  }
  return variations;
}

test("a forged reconciliation item cannot queue a review prompt, however it is shaped", () => {
  for (const [label, item] of forgedReconciliationItems()) {
    const request = { reconciliation_item: item, presented_values: presentedFor(item), now: NOW };
    const wired = attempt(() => buildSourceConflictQueueEntry(request));
    assert.equal(wired.threw, false, `${label}: ${wired.code}`);
    assert.notEqual(wired.decision, "queued", `${label}: queued a caller-supplied item`);
    assert.equal(wired.decision, "unavailable", label);
    assert.equal(wired.value.reason_id, "reconciliation_item_not_store_issued", label);
    assert.equal(wired.value.owed_seam, V5_J103_RECONCILIATION_ITEM_SEAM, label);
    assert.equal(wired.value.field_authority_gap, "no_mailbox_field_authority_registry", label);
    // Nothing is shown to anybody and nothing is applied.
    assert.equal(wired.value.visible, false, label);
    assert.equal(wired.value.queued_at, null, label);
    assert.equal(wired.value.sides, null, label);
    assert.equal(wired.value.applied, false, label);
    assert.equal(wired.value.resolved_by_machine, false, label);
    capture(wired.value);
  }
});

test("the items the wired seam refuses are ones the predicate would really have queued", () => {
  // Without this the refusal above proves nothing: a seam that refuses malformed
  // fixtures is not a seam that refuses valid ones.
  let queued = 0;
  for (const [label, item] of forgedReconciliationItems()) {
    const rendered = capture(probe.wouldQueueConflict({
      reconciliation_item: item, presented_values: presentedFor(item), now: NOW,
    }));
    assert.equal(rendered.wired, false, label);
    if (rendered.classification === WOULD_QUEUE) {
      queued += 1;
      assert.equal(rendered.would_be_visible, true, label);
      assert.ok(rendered.sides.observed.value_text.length > 0, label);
    }
  }
  assert.ok(queued >= 6, `expected the fixtures to be queue-worthy, ${queued} were`);
});

test("even an item F01 itself emitted is unavailable, because no store issued it", () => {
  // The point of the check is not that the item is fake. It is that nothing here
  // can tell: F01 emits reconciliation items and keeps none, so provenance is the
  // missing fact, and an item that really is F01's answers the same way.
  for (const item of Object.values(f01Conflicts())) {
    const entry = capture(buildSourceConflictQueueEntry({
      reconciliation_item: item, presented_values: presentedFor(item), now: NOW,
    }));
    assert.equal(entry.decision, "unavailable");
    assert.equal(entry.reason_id, "reconciliation_item_not_store_issued");
  }
  assert.ok(governedCorrespondenceGaps()
    .some(g => g.gap === "no_mailbox_field_authority_registry" && g.landed === false));
});

// ---------------------------------------------------------------------------
// Re-review 983, defects 1 and 2 — THE PUBLIC SURFACE ITSELF.
//
// The first correction kept the decision logic as `unwired*` EXPORTS. That left
// `read`, `covered`, `drafted`, `proposed` and `queued` reachable by direct valid
// caller input through a second name, with `wired: false` riding along as a
// label — and a label is not access control.
//
// These two tests are about the surface rather than about any one seam. The first
// enumerates the exports a consumer sees. The second runs EVERY export over every
// caller-controlled input shape this suite can build, including the reviewer's own
// constructions, and asserts where a privileged outcome string is allowed to
// appear at all — as vocabulary, never as an answer.
// ---------------------------------------------------------------------------

/** The one export that is not consumer surface, marked by a prefix no other carries. */
const NON_CONSUMER_EXPORT = "__j103ClassificationProbe";

test("the export list is the declared public surface plus one non-consumer probe", () => {
  const exported = Object.keys(J103_NAMESPACE).sort();
  assert.ok(exported.length > 40, "there must be a surface to check");

  // Nothing named for the fixture or unwired route survives. This is the literal
  // shape of the defect: a second name for the same decision logic.
  const bypassNames = exported.filter(name => /unwired|fixture|predicate|probe/i.test(name));
  assert.deepEqual(bypassNames, [NON_CONSUMER_EXPORT],
    "the only non-consumer name is the classification probe, and it cannot answer");

  assert.deepEqual(exported, [...V5_J103_PUBLIC_SURFACE, NON_CONSUMER_EXPORT].sort(),
    "the module exports exactly its declared public surface plus the probe");
  assert.ok(!V5_J103_PUBLIC_SURFACE.includes(NON_CONSUMER_EXPORT),
    "the probe must not be on the public surface it is excluded from");
  assert.deepEqual(exported.filter(name => name.startsWith("__")), [NON_CONSUMER_EXPORT]);

  // And the probe's own methods are named for what they are: a classification of
  // what WOULD happen, never the happening.
  assert.deepEqual(Object.keys(probe).sort(),
    ["fixtureBinding", "wouldCover", "wouldDraft", "wouldProposeFact", "wouldQueueConflict",
      "wouldRead"]);
  assert.deepEqual([...V5_J103_PRIVILEGED_OUTCOMES].sort(),
    ["covered", "drafted", "proposed", "queued", "read"]);
});

/**
 * Every value every export of this module returns, over every caller-controlled
 * input shape this suite knows how to build.
 *
 * Two passes, deliberately. The TARGETED pass builds the realistic requests —
 * including the reviewer's exact constructions, each labelled — because a generic
 * pass would mostly throw on shape and prove nothing. The GENERIC pass then calls
 * every export, named or not, with a spread of caller objects, so an export nobody
 * thought to list here is still swept.
 */
function everyPublicReturn() {
  const returns = [];
  const record = (label, fn) => {
    const outcome = attempt(fn);
    if (!outcome.threw) returns.push([label, outcome.value]);
  };

  const callerBindings = [
    ["a wired binding told the adapter is available", () => wiredBinding({ availability: "available" })],
    ["a wired binding told unknown", () => wiredBinding({ availability: "unknown" })],
    ["a forged compiled binding, digest recomputed to match", () => forgeBinding()],
    ["a forged binding claiming an owner-issued receipt", () =>
      forgeBinding({ availability_source: "owner_issued_adapter_read_receipt" })],
    ["a fixture binding from the probe", () => binding()],
    ["a fixture binding repainted as wired", () => ({ ...binding(), wired: true })],
  ];

  for (const [bindingLabel, makeBinding] of callerBindings) {
    const b = attempt(makeBinding);
    if (b.threw) continue;
    record(`${bindingLabel}: the binding itself`, () => b.value);
    record(`${bindingLabel}: read`, () =>
      readCorrespondenceThread({ binding: b.value, thread: thread(), now: NOW }));
    record(`${bindingLabel}: classification read`, () =>
      probe.wouldRead({ binding: b.value, thread: thread(), now: NOW }));
    for (const slugs of [["joe"], ["dell"], ["joe", "dell"]]) {
      record(`${bindingLabel}: coverage ${slugs.join("+")}`, () =>
        projectCorrespondenceCoverage({ binding: b.value, requested_partner_slugs: slugs }));
      record(`${bindingLabel}: classification coverage ${slugs.join("+")}`, () =>
        probe.wouldCover({ binding: b.value, requested_partner_slugs: slugs }));
    }

    // The reviewer's exact read candidates: the module's own classification, that
    // classification repainted, and a wholly hand-built read carrying the
    // privileged verdict.
    const classified = read({}, binding());
    const candidates = [
      ["the wired answer itself", attempt(() =>
        readCorrespondenceThread({ binding: b.value, thread: thread(), now: NOW })).value],
      ["a classification result", classified],
      ["a classification repainted as wired", { ...classified, wired: true }],
      ["a classification repainted and re-bound",
        { ...classified, wired: true, binding_digest: b.value.binding_digest }],
      ["a wholly hand-built read", {
        ...classified, wired: true, binding_digest: b.value.binding_digest,
        decision: "read", reason_id: "relevant_business_context_within_boundary",
      }],
    ];
    for (const [candidateLabel, thread_read] of candidates) {
      if (thread_read === null || thread_read === undefined) continue;
      record(`${bindingLabel} / ${candidateLabel}: draft`, () =>
        draftCorrespondence({ binding: b.value, thread_read, now: NOW, draft: draft() }));
      record(`${bindingLabel} / ${candidateLabel}: classification draft`, () =>
        probe.wouldDraft({ binding: b.value, thread_read, now: NOW, draft: draft() }));
      record(`${bindingLabel} / ${candidateLabel}: proposal`, () =>
        evaluateProposedFact({ binding: b.value, thread_read, now: NOW, proposal: proposal() }));
      record(`${bindingLabel} / ${candidateLabel}: classification proposal`, () =>
        probe.wouldProposeFact({ binding: b.value, thread_read, now: NOW, proposal: proposal() }));
    }
  }

  // The reviewer's second construction: a valid F01-shaped reconciliation item.
  for (const [itemLabel, item] of forgedReconciliationItems()) {
    const request = { reconciliation_item: item, presented_values: presentedFor(item), now: NOW };
    record(`${itemLabel}: conflict queue`, () => buildSourceConflictQueueEntry(request));
    record(`${itemLabel}: classification conflict`, () => probe.wouldQueueConflict(request));
  }

  // The generic pass: every export, named or not, over a spread of caller objects.
  const genericArgs = [
    undefined, null, {}, [], "read", { availability: "available" }, { decision: "read" },
    { classification: "read", wired: true }, { binding: binding(), thread: thread(), now: NOW },
    { binding: wiredBinding(), thread: thread(), now: NOW },
    { binding: binding(), requested_partner_slugs: ["joe"] },
    { binding: binding(), thread_read: read(), now: NOW, draft: draft() },
    { binding: binding(), thread_read: read(), now: NOW, proposal: proposal() },
    { reconciliation_item: f01Conflicts().equal_version_contradiction,
      presented_values: presentedFor(f01Conflicts().equal_version_contradiction), now: NOW },
  ];
  const callables = [
    ...Object.entries(J103_NAMESPACE).filter(([, value]) => typeof value === "function"),
    ...Object.entries(probe).map(([name, fn]) => [`${NON_CONSUMER_EXPORT}.${name}`, fn]),
  ];
  assert.ok(callables.length >= 15, "there must be callable exports to sweep");
  for (const [name, fn] of callables) {
    for (const arg of genericArgs) {
      record(`${name}(${JSON.stringify(arg) ?? "undefined"})`.slice(0, 120), () => fn(arg));
    }
  }

  // And every non-function export, because a constant is a return value too.
  for (const [name, value] of Object.entries(J103_NAMESPACE)) {
    if (typeof value !== "function") returns.push([`the constant ${name}`, value]);
  }
  return returns;
}

/**
 * WHERE A PRIVILEGED OUTCOME STRING MAY APPEAR AT ALL, as a closed list of paths.
 *
 * A blanket "the word never appears" would be false and would teach nothing: the
 * adapter's mode IS the string "read" — it is read-only, and the opposite value
 * would be the alarming one — and the module publishes its own decision
 * vocabulary, which names all five. So the test is stricter than a word ban and
 * more honest than a field-name check: it collects every path at which a
 * privileged string appears across every return above, normalises array indices,
 * and asserts the set is EXACTLY this list. A leak anywhere — `decision: "read"`
 * back on a result, a new "outcome" field, a renamed bypass — adds a path and
 * turns this red.
 */
const PRIVILEGED_STRING_PATHS = [
  // The adapter is read-only. That is a capability, not an answer — and the
  // opposite value here would be the alarming one.
  "binding.adapter_mode",
  "constant.V5_J103_ADAPTERS.v5_f10_partner_mail_calendar_adapter.mode",
  "result.read_interface.adapter_kinds[].mode",
  // The module's published vocabulary, and the same vocabulary echoed into the
  // policy preimage. A list of the names a decision may take is not a decision.
  "constant.V5_J103_CONFLICT_DECISIONS[]",
  "constant.V5_J103_DRAFT_DECISIONS[]",
  "constant.V5_J103_PRIVILEGED_OUTCOMES[]",
  "constant.V5_J103_PROPOSAL_DECISIONS[]",
  "constant.V5_J103_THREAD_DECISIONS[]",
  "result.draft_and_proposal.draft_decisions[]",
  "result.draft_and_proposal.proposal_decisions[]",
  "result.read_interface.decisions[]",
  "result.source_reconciliation.decisions[]",
].sort();

test("no export returns a privileged outcome, whatever a caller passes in", () => {
  const returns = everyPublicReturn();
  assert.ok(returns.length > 200,
    `the sweep must actually have returns to sweep; it had ${returns.length}`);

  const found = new Map();
  const seen = new WeakSet();
  const walk = (value, path, label) => {
    if (typeof value === "string") {
      if (V5_J103_PRIVILEGED_OUTCOMES.includes(value)) {
        if (!found.has(path)) found.set(path, []);
        found.get(path).push(`${label} → "${value}"`);
      }
      return;
    }
    if (Array.isArray(value)) {
      value.forEach(entry => walk(entry, `${path}[]`, label));
      return;
    }
    if (value === null || typeof value !== "object") return;
    if (seen.has(value)) return;
    seen.add(value);
    for (const [key, entry] of Object.entries(value)) walk(entry, `${path}.${key}`, label);
  };

  for (const [label, value] of returns) {
    const root = label.startsWith("the constant ") ? `constant.${label.slice(13)}`
      : label.endsWith("the binding itself") ? "binding"
        : "result";
    walk(value, root, label);
  }

  // THE ASSERTION THAT MATTERS: no decision-bearing field anywhere carries one.
  const decisionFields = [...found.keys()]
    .filter(path => /\.(decision|classification|outcome|verdict|status|answer)$/.test(path));
  assert.deepEqual(decisionFields, [],
    `a privileged outcome was returned as an answer: ${JSON.stringify(
      decisionFields.map(p => [p, found.get(p)[0]]))}`);

  // And the closed list: the only places the words appear at all are vocabulary.
  assert.deepEqual([...found.keys()].sort(), PRIVILEGED_STRING_PATHS,
    "a privileged outcome string appeared at a path this module does not allow it");

  // Non-vacuous: the sweep really does see the vocabulary it is allowing.
  assert.ok(found.size >= 5, "the sweep found nothing at all, which cannot be right");
});

// ---------------------------------------------------------------------------
// Review 983, defect 3 — "no sending client is imported" was a clause with no
// test behind it. The F10 oracle test above proves F10 REFUSES every write
// operation, which is a different claim: a send-capable client could be imported
// into THIS module tomorrow without moving a byte of F10's policy text, and that
// test would stay green.
//
// So this one reads J103's own source the way a linter would, and asserts the
// dependency boundary directly: a closed import set, none of it send-capable,
// nothing callable taken from a provider module, and no export that names or
// returns a provider operation.
// ---------------------------------------------------------------------------

const SRC_DIR = new URL("../src/", import.meta.url);
const J103_FILE = "governed-correspondence.v5.js";
const J103_SOURCE = readFileSync(new URL(J103_FILE, SRC_DIR), "utf8");

// ---------------------------------------------------------------------------
// THE IMPORT GUARD IS A REAL PARSER, NOT A HAND-ROLLED TOKENIZER.
//
// Two rounds of review broke the guard by hand. The first version was patterns,
// and they missed `import /* x */ "./google-oidc.js";`. The second version was a
// tokenizer written for this file, and the third review broke THAT: it treated a
// non-breaking space as punctuation, so `import "./google-oidc.js";` — valid
// ECMAScript, a real side-effect import of a fetching module — was invisible and
// the closed-set assertion downstream stayed green.
//
// The lesson both rounds taught is that a JavaScript parser written by hand in a
// test file is a liability, so this one is not written here. The parse is done by
// esbuild, which is present in mcp-server/node_modules (acorn, es-module-lexer and
// typescript are not), and which reports the module's imports — static, dynamic,
// and require() — with the kind of each. Its parser is the same one that compiles
// this repository's Worker bundle, so it agrees with the engine about whitespace,
// comments, strings, templates and regular expressions without this file having an
// opinion about any of them.
//
// Two things esbuild cannot answer are answered CONSERVATIVELY, on purpose. A call
// site whose specifier is not a literal (`import(name)`, `require(name)`) resolves
// to no path at all, and `createRequire` reaches CommonJS without the token
// `require(` appearing anywhere. Both are found by counting names in the raw text,
// which over-reports — a mention inside a comment counts — and that is the safe
// direction: a false alarm is a review, a missed call site is a hole in the
// allow-list.
// ---------------------------------------------------------------------------

/** esbuild refuses to run at all if it is missing, rather than degrading quietly. */
assert.equal(typeof esbuild.buildSync, "function",
  "the import guard needs a real parser; esbuild is not loadable");

/** Parse `source` and return esbuild's import records: `{ path, kind }` each. */
function importRecords(source) {
  const built = esbuild.buildSync({
    stdin: {
      contents: source,
      loader: "js",
      sourcefile: "module-under-guard.js",
      resolveDir: fileURLToPath(SRC_DIR),
    },
    bundle: false,
    write: false,
    metafile: true,
    format: "esm",
    platform: "neutral",
    logLevel: "silent",
    logLimit: 0,
  });
  const output = Object.values(built.metafile.outputs)[0];
  return output === undefined ? [] : output.imports;
}

/**
 * How many times `callee(` appears in the raw text, as a whole word.
 *
 * Deliberately a SUPERSET: an occurrence inside a comment or a string is counted.
 * This number is only ever compared against what the parser resolved, and only to
 * decide whether some call site went unresolved, so counting too many raises a
 * false alarm and counting too few would hide a door.
 */
function callSiteCount(source, callee) {
  return source.split(new RegExp(`(?<![\\w$.])${callee}\\s*\\(`)).length - 1;
}

/**
 * Every module specifier a source file loads, by any form that actually loads.
 *
 * A call site the parser could not resolve to a literal is reported as
 * `<computed import>` / `<computed require>` rather than dropped, and a source
 * that so much as names `createRequire` reports `createRequire`, because a closed
 * allow-list has to notice exactly the specifiers it cannot see.
 */
function importSpecifiers(source) {
  const records = importRecords(source);
  const specifiers = new Set(records.map(record => record.path));
  const resolved = kind => records.filter(record => record.kind === kind).length;
  if (callSiteCount(source, "import") > resolved("dynamic-import")) {
    specifiers.add("<computed import>");
  }
  if (callSiteCount(source, "require") > resolved("require-call")) {
    specifiers.add("<computed require>");
  }
  if (/(?<![\w$])createRequire(?![\w$])/.test(source)) specifiers.add("createRequire");
  return [...specifiers].sort();
}

/**
 * The runtime names this module is forbidden to reference, each mapped to a token
 * that cannot occur in source.
 *
 * The key is the thing esbuild's `define` rewrites and the value is what it
 * rewrites it to, so finding the token in the output is proof that the source
 * REFERENCED the name. `define` substitutes identifier references only: it never
 * touches a string, a comment, a property name, or an identifier a local binding
 * shadows. That is exactly the identifier-level question a text scan could not
 * answer, asked of the parser that already read the file.
 *
 * The list names ROOTS rather than call shapes, because enumerating shapes is
 * endless: the fifth review found `process.getBuiltinModule("node:" + "https")`
 * and the sixth found `globalThis.process.getBuiltinModule(...)` reaching the
 * same place around a define that named only the bare form. Banning the root
 * identifier catches every shape written off that root at its first identifier,
 * whatever it reaches for next. The set is the host object (`process`), the
 * global object by any of its spellings (`globalThis`, `global`, `window`,
 * `self`), the CommonJS loader (`require`), the compilers that mint a fresh
 * scope (`Function`, `eval`, `WebAssembly`), and the transports (`Worker`,
 * `fetch`, `XMLHttpRequest`, `WebSocket`). J103 is a pure kernel: it decides, it
 * does not touch the host, so it has no honest use for any of them.
 *
 * What this buys is a LEXICAL invariant and nothing more. It is not a capability
 * sandbox, and it does not claim that every route to the host begins at a listed
 * name. Reflective routes that name none of them exist — `[].filter.constructor`
 * reaches the Function constructor without the identifier `Function`, and
 * comparable prototype and getter walks reach further — and they are outside this
 * guard's scope by design. Non-lexical reachability, including a capability
 * handed to J103 at runtime, is governed by the authority rule and its own tests,
 * not by this list.
 *
 * The two dotted entries are kept alongside their roots so the door tests can name
 * the precise route a review found; esbuild matches the longer key first.
 */
const FORBIDDEN_RUNTIME_DEFINES = {
  process: "__J103_FORBIDDEN_PROCESS__",
  globalThis: "__J103_FORBIDDEN_GLOBALTHIS__",
  global: "__J103_FORBIDDEN_GLOBAL__",
  window: "__J103_FORBIDDEN_WINDOW__",
  self: "__J103_FORBIDDEN_SELF__",
  require: "__J103_FORBIDDEN_REQUIRE__",
  Function: "__J103_FORBIDDEN_FUNCTION__",
  eval: "__J103_FORBIDDEN_EVAL__",
  WebAssembly: "__J103_FORBIDDEN_WEBASSEMBLY__",
  Worker: "__J103_FORBIDDEN_WORKER__",
  fetch: "__J103_FORBIDDEN_FETCH__",
  XMLHttpRequest: "__J103_FORBIDDEN_XHR__",
  WebSocket: "__J103_FORBIDDEN_WEBSOCKET__",
  "globalThis.require": "__J103_FORBIDDEN_REQUIRE__",
  "process.getBuiltinModule": "__J103_FORBIDDEN_BUILTIN__",
  "import.meta.resolve": "__J103_FORBIDDEN_RESOLVE__",
};

/**
 * The source as esbuild's printer writes it back, with every forbidden runtime
 * name rewritten to its token: comments gone, strings untouched.
 *
 * The transform is a full parse and a full print, so what comes out is code and
 * only code, spelled the way the engine reads it rather than the way it was typed.
 */
function printedCode(source) {
  return esbuild.transformSync(source, {
    loader: "js",
    format: "esm",
    platform: "neutral",
    define: FORBIDDEN_RUNTIME_DEFINES,
    minify: false,
    minifyIdentifiers: false,
    minifySyntax: false,
    minifyWhitespace: false,
    legalComments: "none",
    logLevel: "silent",
    logLimit: 0,
  }).code;
}

/**
 * Which forbidden runtime names a source actually REFERENCES, by name.
 *
 * The fourth review found `require?.("node:https")`: esbuild emits no import
 * record for an optionally-called require, so the call site was invisible to the
 * counting above. The fifth review found two more — a `require` inside a string
 * literal that a text scan called a hit, and `process.getBuiltinModule` that no
 * check saw at all. Both are answered the same way: ask the parser which
 * identifiers the source binds to, and no call shape has to be anticipated.
 * Called, optionally called, aliased, read off a global, or reached through a
 * compiled scope — each one is an identifier reference, and each one is rewritten.
 *
 * What this deliberately does NOT claim: a name a local binding shadows is not
 * rewritten, because such a name is that local, not the runtime door. And a
 * capability handed to the module at runtime — a function on a passed-in object —
 * is not lexical and is not visible here at all; that is the authority rule's job.
 *
 * `createRequire` is not on the list: it reaches CommonJS without the token
 * `require` ever standing alone, and it is an import before it is a call, so the
 * specifier check closes it.
 */
function forbiddenRuntimeNames(source) {
  const printed = printedCode(source);
  const found = new Set();
  for (const [name, token] of Object.entries(FORBIDDEN_RUNTIME_DEFINES)) {
    if (printed.includes(token)) found.add(name);
  }
  return [...found].sort();
}

/** Whether a source REFERENCES `require` as an identifier, in any call form. */
function namesRequire(source) {
  return forbiddenRuntimeNames(source).some(name => name.endsWith("require"));
}

/**
 * The names a source file binds out of one specifier — the IMPORTED names, not the
 * local aliases, because it is the imported name that says what was taken.
 *
 * Read out of the linker rather than off a token stream: the source is bundled
 * against an EMPTY stub for each of its relative dependencies, and esbuild reports
 * one "No matching export ... for import X" per name the source asked that module
 * for. A namespace import (`import * as ns`) asks for no names and so reports
 * none — and an assertion of an exact binding list still fails if a named import
 * is replaced by one, which is the case that matters here.
 */
function importedBindings(source, specifier) {
  const directory = mkdtempSync(join(tmpdir(), "j103-import-guard-"));
  try {
    writeFileSync(join(directory, "entry.js"), source);
    for (const record of importRecords(source)) {
      if (record.path.startsWith(".")) writeFileSync(join(directory, record.path), "export {};\n");
    }
    let diagnostics = [];
    try {
      esbuild.buildSync({
        entryPoints: [join(directory, "entry.js")],
        bundle: true,
        write: false,
        format: "esm",
        platform: "node",
        packages: "external",
        treeShaking: false,
        logLevel: "silent",
        logLimit: 0,
      });
    } catch (failure) {
      diagnostics = failure.errors ?? [];
    }
    const wanted = basename(specifier);
    const names = new Set();
    for (const diagnostic of diagnostics) {
      const match = /^No matching export in "(.+)" for import "(.+)"$/.exec(diagnostic.text);
      if (match !== null && basename(match[1]) === wanted) names.add(match[2]);
    }
    return [...names].sort();
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
}

test("the import parser reads the forms two hand-written guards missed", () => {
  // BOTH REVIEWS' COUNTEREXAMPLES FIRST, because each one was a live hole: the
  // pattern guard did not match the comment form, and the hand tokenizer that
  // replaced it did not match the non-breaking-space form, which is ordinary
  // ECMAScript whitespace. Either one could have added a side-effect import of a
  // fetching module with the closed-set assertion below still green.
  assert.deepEqual(importSpecifiers('import /* x */ "./google-oidc.js";'),
    ["./google-oidc.js"]);
  assert.deepEqual(importSpecifiers('import\u00a0"./google-oidc.js";'),
    ["./google-oidc.js"], "a non-breaking space is whitespace, not punctuation");
  // The same two forms reaching a FORBIDDEN module are caught by the real
  // assertion, not just by the parser in isolation.
  for (const smuggled of ['import /* x */ "node:child_process";',
    'import\u00a0"node:https";',
    'const c = await import("child_process");',
    'const h = require("node:https");']) {
    const found = importSpecifiers(smuggled);
    assert.equal(found.length, 1, smuggled);
    assert.ok(FORBIDDEN_PACKAGES.includes(found[0]),
      `${smuggled} must surface a forbidden specifier, got ${found[0]}`);
  }

  const cases = [
    ['import "./plain.js";', ["./plain.js"]],
    ['import /* a */ /* b */ "./commented.js";', ["./commented.js"]],
    ['import // a line comment\n  "./after-line-comment.js";', ["./after-line-comment.js"]],
    ['import { a as b } from /* c */ "./named.js";', ["./named.js"]],
    ['import def, { a } from "./default-and-named.js";', ["./default-and-named.js"]],
    ['import * as ns from "./star.js";', ["./star.js"]],
    ['export { a } from "./re-exported.js";', ["./re-exported.js"]],
    ['export * from "./star-export.js";', ["./star-export.js"]],
    ['await import("./dynamic.js");', ["./dynamic.js"]],
    ['const p = import(\n  /* lazy */ "./dynamic-multiline.js"\n);', ["./dynamic-multiline.js"]],
    ['const cp = require("child_process");', ["child_process"]],
    ['const r = require(\n  "node:https"\n);', ["node:https"]],
    ['import { createRequire } from "node:module";', ["createRequire", "node:module"]],
    // Computed specifiers are NAMED rather than skipped: an allow-list that
    // silently ignores import(name) is an allow-list with a door in it.
    ['const m = await import(name);', ["<computed import>"]],
    ['const m = require(name);', ["<computed require>"]],
    // A specifier the parser CAN fold is reported as the module it really names,
    // which is strictly better than calling it computed.
    ['const m = await import("./pre" + "fix.js");', ["./prefix.js"]],
    // And the shapes that only LOOK like imports.
    ['// import "./commented-out.js";\nimport "./real.js";', ["./real.js"]],
    ['/* import "./block-commented.js"; */ import "./real.js";', ["./real.js"]],
    ['const s = "import \\"./in-a-string.js\\";";', []],
    ["const t = `import \"./in-a-template.js\";`;", []],
    ['const re = /["\']import "x"/g;\nimport "./real.js";', ["./real.js"]],
    ['const u = import.meta.url;', []],
    ['const ratio = a / b; const other = c / d;', []],
    ["const t = `a ${ 1 / 2 } b`; import \"./real.js\";", ["./real.js"]],
  ];
  for (const [source, expected] of cases) {
    assert.deepEqual(importSpecifiers(source), expected.sort(), source);
  }

  // The parser is trusted with a file the engine agrees is parseable, so a source
  // it silently mis-tokenized would be caught by node itself.
  const checked = spawnSync(process.execPath, ["--check", fileURLToPath(new URL(J103_FILE, SRC_DIR))],
    { encoding: "utf8" });
  assert.equal(checked.status, 0, `node --check rejected the module: ${checked.stderr}`);

  // Non-vacuous: the parser really is reading THIS module, not an empty set.
  assert.ok(importSpecifiers(J103_SOURCE).length >= 5);
  assert.deepEqual(importedBindings('import { A, B as C } from "./x.js";', "./x.js"), ["A", "B"]);
});

/**
 * The modules in mcp-server/src that can actually reach the outside world, found
 * by scanning rather than by typing a list that would rot. A module counts as
 * send-capable if it opens a socket, fetches, shells out, or drives a mail
 * transport — that is the capability J103 must not acquire by import, directly or
 * by name.
 */
const OUTBOUND_CAPABILITY =
  /\bfetch\s*\(|\bXMLHttpRequest\b|new\s+WebSocket\b|\bnodemailer\b|\.sendMail\s*\(|\bchild_process\b|["']node:https?["']|["']https?["']\s*\)|\bgoogleapis\b|@microsoft\/microsoft-graph-client|@azure\/msal|\bsendgrid\b|\bmailgun\b|\bpostmark\b/;

function sendCapableModules() {
  const capable = [];
  for (const file of readdirSync(SRC_DIR)) {
    if (!file.endsWith(".js")) continue;
    // The module under test is the subject, not a candidate: its matches are the
    // patterns it REFUSES ("smtp:", "mailto:"), not capabilities it holds. Its own
    // purity is asserted separately below.
    if (file === J103_FILE) continue;
    if (OUTBOUND_CAPABILITY.test(readFileSync(new URL(file, SRC_DIR), "utf8"))) {
      capable.push(`./${file}`);
    }
  }
  return capable.sort();
}

/** Provider-write packages nothing in this repository depends on, and must not. */
const FORBIDDEN_PACKAGES = [
  "nodemailer", "@sendgrid/mail", "mailgun.js", "postmark", "emailjs", "smtp-client",
  "@microsoft/microsoft-graph-client", "@azure/msal-node", "@azure/identity",
  "googleapis", "google-auth-library", "gmail-api-parse-message",
  "node-fetch", "axios", "undici", "got", "superagent",
  "node:http", "node:https", "node:net", "node:tls", "node:dgram", "node:child_process",
  "http", "https", "net", "tls", "dgram", "child_process",
];

/**
 * The specifiers that hand an ES module a working `require`, and so would let one
 * reach every forbidden package above without naming any of them.
 */
const COMMONJS_BRIDGE_SPECIFIERS = ["node:module", "module"];

test("J103 imports a closed set of modules, and no send-capable client is in it", () => {
  const specifiers = importSpecifiers(J103_SOURCE);
  assert.deepEqual(specifiers, [
    "./artifact-trust.js",
    "./global-boundaries.v5.js",
    "./identity.js",
    "./partner-mail-calendar.v5.js",
    "./record-source-authority.v5.js",
  ], "the import set is closed: a new dependency here is a review, not a detail");

  const capable = sendCapableModules();
  assert.ok(capable.length >= 3,
    `the scan must actually find the repository's outbound modules; it found ${capable.length}`);
  assert.ok(capable.includes("./google-oidc.js"), "the scan must recognise a fetching module");
  for (const forbidden of [...capable, ...FORBIDDEN_PACKAGES]) {
    assert.ok(!specifiers.includes(forbidden),
      `J103 must not import ${forbidden}: it can reach a provider`);
  }

  // And J103 holds no outbound capability of its own, by the same scan that
  // classified the others — minus the two refusal patterns it is allowed to name.
  const withoutRefusals = J103_SOURCE
    .replace(/mailto:\|smtp:\|tel:/g, "")
    .replace(/"smtp"/g, "");
  assert.ok(!OUTBOUND_CAPABILITY.test(withoutRefusals),
    "J103 must hold no outbound capability of its own");

  // And the runtime roots are shut by name. What this asserts is LEXICAL, and
  // exactly this much: the compiled module contains no import outside the closed
  // allow-list above, no dynamic import and no `createRequire`, and no reference
  // to any identifier in FORBIDDEN_RUNTIME_DEFINES — `process`, `globalThis`,
  // `global`, `window`, `self`, `require`, `Function`, `eval`, `WebAssembly`,
  // `Worker`, `fetch`, `XMLHttpRequest`, `WebSocket`. It does NOT assert that
  // nothing reachable from this module can send — a capability handed to J103 at
  // runtime, as a function on a passed-in object, is invisible to any reading of
  // the source. That case is governed by the authority rule and its own tests.
  assert.deepEqual(forbiddenRuntimeNames(J103_SOURCE), [],
    "J103 is a pure kernel; no forbidden root identifier has a use in it");
  for (const bridge of COMMONJS_BRIDGE_SPECIFIERS) {
    assert.ok(!specifiers.includes(bridge),
      `J103 must not import ${bridge}: createRequire mints the require it otherwise lacks`);
  }
  assert.ok(!specifiers.includes("createRequire"),
    "J103 must not so much as name createRequire");
});

test("J103 references no forbidden root identifier, in any call shape", () => {
  // Each review's counterexample, and the answer to all of them is the same: the
  // parser is asked which IDENTIFIERS the source references, so no call shape has
  // to be anticipated. Optional-call require emits no import record; a global read
  // is not a call at all; getBuiltinModule builds its specifier at runtime; and
  // Function and eval spell `require` in a scope that does not exist until then.
  // Banning the ROOTS ends the shape-by-shape chase for anything written off a
  // listed name: a route that starts at `globalThis` or `self` is caught at its
  // first identifier, whatever it reaches for next. It does not end reachability
  // as such — a reflective route naming no listed identifier, such as
  // `[].filter.constructor`, is out of this guard's lexical scope and is governed
  // by the authority rule and its own tests.
  const doors = [
    ['const h = require?.("node:https");', "require"],
    ['globalThis.require("node:https");', "globalThis.require"],
    ["const r = require; r(\"node:https\");", "require"],
    ['const h = (0, require)("node:https");', "require"],
    ['process.getBuiltinModule("node:" + "https");', "process.getBuiltinModule"],
    ['globalThis.process.getBuiltinModule("node:https");', "globalThis"],
    ["const p = self.process;", "self"],
    ["const p = window.process;", "window"],
    ["const p = global.process;", "global"],
    ["fetch(endpoint);", "fetch"],
    ['new Worker("./w.js");', "Worker"],
    ["new WebSocket(endpoint);", "WebSocket"],
    ["new XMLHttpRequest();", "XMLHttpRequest"],
    ["WebAssembly.compile(bytes);", "WebAssembly"],
    ['new Function("return require")();', "Function"],
    ['eval("require");', "eval"],
    ['import.meta.resolve("node:https");', "import.meta.resolve"],
  ];
  for (const [evasion, door] of doors) {
    assert.ok(forbiddenRuntimeNames(evasion).includes(door),
      `${evasion} must be caught by naming ${door}`);
  }

  // createRequire is the door that reaches CommonJS WITHOUT the token `require`
  // ever standing alone, so the name check honestly says no and the specifier
  // check says yes. Both halves are asserted, so neither can quietly stop working.
  const bridged = [
    'import { createRequire } from "node:module";',
    "const r = createRequire(import.meta.url);",
    'r("node:https");',
  ].join("\n");
  assert.deepEqual(forbiddenRuntimeNames(bridged), [],
    "createRequire reaches CommonJS without naming require; the specifier must catch it");
  const bridgedSpecifiers = importSpecifiers(bridged);
  assert.ok(bridgedSpecifiers.includes("node:module"), bridgedSpecifiers.join(", "));
  assert.ok(bridgedSpecifiers.includes("createRequire"), bridgedSpecifiers.join(", "));
  assert.ok(COMMONJS_BRIDGE_SPECIFIERS.some(bridge => bridgedSpecifiers.includes(bridge)));

  // The false positives the fifth review found, which is what identifier-level
  // buys over a text scan: a mention is not a use, in a comment OR in a string.
  for (const mention of [
    '// require("node:https");\nexport const a = 1;',
    '/* require("node:https") */ export const b = 2;',
    'export const note = "require";',
    'export const label = `a ${1} require`;',
    'export const key = { require: 1 }.require;',
    "export const c = requiredFields;",
    "export const d = { self: 1, process: 2, window: 3 };",
    "export const e = (function (process) { return process; })(1);",
  ]) {
    assert.deepEqual(forbiddenRuntimeNames(mention), [],
      `${mention} names no runtime door; it only spells one`);
  }

  // And the real module passes, which is the whole point: the ban costs it nothing.
  assert.deepEqual(forbiddenRuntimeNames(J103_SOURCE), []);
  assert.ok(!namesRequire(J103_SOURCE));
  assert.ok(printedCode(J103_SOURCE).length > 1000, "the printer really ran over the module");
});

test("nothing callable crosses the seam from the one provider-named module J103 imports", () => {
  // partner-mail-calendar.v5.js is the module that OWNS the mail operation
  // registry, and J103 takes three constants from it so its claims are checked
  // against the registry rather than against a literal. What it must never take is
  // something it can CALL: a name is a fact, a function is a capability.
  const bindings = importedBindings(J103_SOURCE, "./partner-mail-calendar.v5.js");
  assert.deepEqual(bindings,
    ["V5_F10_ADAPTER_KIND", "V5_F10_AUTHORITATIVE_HOME", "V5_F10_WRITE_OPERATIONS"]);
  for (const name of bindings) {
    assert.notEqual(typeof F10_NAMESPACE[name], "function",
      `${name} is callable; J103 may import F10's vocabulary, never its behaviour`);
  }
  // The two evaluators that could act are not named anywhere in the source at all.
  for (const executor of ["evaluateConnectorOperation", "reconcileOfflineQueue",
    "toCorporateArtifactCandidate", "compilePartnerInstallation"]) {
    assert.ok(!J103_SOURCE.includes(executor),
      `J103 must not reach F10's ${executor}`);
  }
});

test("no J103 export names a provider operation, and none returns one", () => {
  const providerOperationFragments = [
    ...V5_F10_WRITE_OPERATIONS,
    "sendmail", "sendmessage", "dispatch", "deliver", "transmit", "submit", "postmessage",
  ];
  const exportedFunctions = Object.entries(J103_NAMESPACE)
    .filter(([, value]) => typeof value === "function");
  assert.ok(exportedFunctions.length >= 10, "there must be exports to check");
  for (const [name] of exportedFunctions) {
    const normalized = name.toLowerCase();
    for (const fragment of providerOperationFragments) {
      assert.ok(!normalized.includes(fragment.toLowerCase()),
        `exported function ${name} names the provider operation ${fragment}`);
    }
  }

  // The returned shapes, swept over every result this suite produced plus the
  // projection. A provider operation may appear NOWHERE in them — not as a key,
  // not as a value — and `provider_operation` is null wherever it is carried.
  const operationNames = [...V5_F10_WRITE_OPERATIONS];
  const seen = new Set();
  const sweep = (value, path) => {
    if (value === null || value === undefined) return;
    if (typeof value === "string") {
      for (const operation of operationNames) {
        assert.ok(!value.includes(operation), `${path} carries the operation ${operation}`);
      }
      return;
    }
    if (Array.isArray(value)) {
      value.forEach((entry, index) => sweep(entry, `${path}[${index}]`));
      return;
    }
    if (typeof value !== "object") return;
    if (seen.has(value)) return;
    seen.add(value);
    for (const [key, entry] of Object.entries(value)) {
      for (const operation of operationNames) {
        assert.ok(!key.toLowerCase().includes(operation), `${path}.${key} names ${operation}`);
      }
      if (key === "provider_operation") assert.equal(entry, null, `${path}.${key}`);
      sweep(entry, `${path}.${key}`);
    }
  };
  assert.ok(PRODUCED_RESULTS.length > 12, "the sweep needs results to sweep");
  PRODUCED_RESULTS.forEach((result, index) => sweep(result, `result[${index}]`));
  sweep(v5J103CorrespondenceProjection(), "projection");
  sweep(v5J103PolicyPreimage(), "policy");
  // The one place the operation names legitimately appear is the list of what this
  // module deliberately does NOT hold, imported from F10 rather than retyped.
  assert.deepEqual([...v5J103AbsentWriteOperations()], [...V5_F10_WRITE_OPERATIONS]);
});
