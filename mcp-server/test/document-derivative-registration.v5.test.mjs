// V5-F01 — the DOCUMENT half of the derivative-registration rule, proved case by
// case.
//
// Everything here is synthetic and nothing reaches a real database, provider or
// network. The module under test is pure, so these tests prove the things a Node
// suite can actually prove about a provenance seam:
//
//   * that a derived document version binds to the EXACT stored artifact, and
//     that the bytes it binds to are the bytes the record layer stores;
//   * that a forged source, an unknown artifact, a mismatched document and a
//     repointing attempt each refuse under their own name;
//   * that an original and a legacy-unknown document are recorded as DIFFERENT
//     things, and that neither becomes the other by the absence of evidence;
//   * that no answer, anywhere, claims coverage, exhaustiveness or a deletion;
//   * that a source artifact is never inferred from a document's own bytes;
//   * that the same request evaluated twice produces byte-identical records, so a
//     replay returns the same digests rather than a second, different write.
//
// The things a pure suite cannot prove — real atomicity, real append-only
// refusal, real direct-DML refusal, real idempotency substitution, real
// concurrency — belong to the SQL fixture in
// mcp-server/test/document-derivative-registration-postgres.sql and to the local
// disposable-database gate, and they are proved there rather than asserted twice
// here.
//
// TWO SQL FILES ARE READ AS TEXT, and nothing else touches the filesystem. The
// last section below opens ops/document-derivative-registration.candidate.sql and
// the fixture beside this file and asserts that the vocabulary, the derivative
// kind, the reserved-kind list and the three free-text bounds written there are
// the ones this module enforces. No database is reached and no SQL is executed:
// this is a SOURCE-PARITY check, and it is the only kind of parity a Node suite
// can honestly make about a schema it cannot apply. It catches exactly the drift
// that would otherwise be found by a production apply — a bound changed on one
// side of the seam and not the other.
//
// NO FIXTURE NAMES A REAL THING. Every document id, artifact digest, object key,
// drive item and workflow name below is unmistakably test data.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { canonicalJson, digest } from "../src/artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import { V5_NO_EFFECTS } from "../src/global-boundaries.v5.js";
import {
  V5_F01_DERIVATIVE_LINK_SCHEMA_VERSION,
  V5_F01_PARSED_PROPOSAL_DERIVATIVE_KIND,
  V5_F01_RESERVED_DERIVATIVE_KINDS,
  V5F01Error,
  evaluateDerivativeRegistration,
} from "../src/record-source-authority.v5.js";
import {
  V5_F01_STORE_RECORD_KINDS,
  v5F01StoreEnvelope,
} from "../src/record-source-authority-store.v5.js";
import {
  V5_F01_DOCUMENT_PROVENANCE_STATES,
  V5_F01_DOCUMENT_SOURCE_RECORD_KIND,
  V5_F01_DOCUMENT_VERSION_DERIVATIVE_KIND,
  V5_F01_STORED_DOCUMENT_SOURCE_SCHEMA_VERSION,
  V5F01DocumentSourceError,
  assertDocumentSourceContract,
  assertDocumentSourceDeclaration,
  assertDocumentSourceIntegrationComplete,
  composeDocumentSourceEnvelopes,
  composeDocumentSourceRecords,
  documentSourceIntegrationGaps,
  documentSourceProvenanceEnvelope,
  documentVersionDerivativeId,
  evaluateDocumentSourceBinding,
  storedDocumentSourceProvenanceRecord,
} from "../src/document-derivative-registration.v5.js";

// --- synthetic fixtures ----------------------------------------------------

const NOW = "2026-09-09T12:00:00.000Z";
const D = n => `sha256:${String(n).padStart(2, "0").repeat(32)}`;

const ARTIFACT_DIGEST = D(21);
const OTHER_ARTIFACT_DIGEST = D(22);
const DOC_BYTES = D(11);
const SEALED_BYTES = D(11);
const PRODUCER = "syn_test_document_producer";

const SOURCE_ARTIFACT = Object.freeze({
  artifact_digest: ARTIFACT_DIGEST,
  created_at: "2026-09-01T09:00:00Z",
});

/** A coherent draft document: nothing delivered, nothing signed, no filing due. */
function draftDocument(overrides = {}) {
  return {
    document_class: "synthetic_test_lease_abstract",
    neon_identity: {
      document_id: "synthetic-doc-0001",
      content_digest: DOC_BYTES,
      version_no: 1,
    },
    object_storage_identity: null,
    onedrive_identity: null,
    preparation_state: "drafting",
    delivery_state: "undelivered",
    signature_state: "unsigned",
    validity_state: "draft",
    version_state: "current",
    ...overrides,
  };
}

const DERIVED_SOURCE = Object.freeze({
  provenance_state: "derived_from_stored_artifact",
  source_artifact_digest: ARTIFACT_DIGEST,
  producer_workflow: PRODUCER,
  producer_run_ref: "syn-test-run-0001",
});

const ORIGINAL_SOURCE = Object.freeze({
  provenance_state: "original_first_party",
  basis_statement: "authored in the record layer by the synthetic test workflow",
});

const LEGACY_SOURCE = Object.freeze({
  provenance_state: "legacy_provenance_unknown",
  basis_statement: "imported before the registration rule; the original is not known",
});

function bind(overrides = {}) {
  return evaluateDocumentSourceBinding({
    tenant: ORGANIZATION_TENANT_ID,
    document: draftDocument(),
    source: DERIVED_SOURCE,
    source_artifact: SOURCE_ARTIFACT,
    recorded_by: "codex",
    now: NOW,
    ...overrides,
  });
}

function refuses(fn, code) {
  assert.throws(fn, error => {
    assert.ok(error instanceof V5F01DocumentSourceError || error instanceof V5F01Error,
      `expected a document-source or kernel error, got ${error?.name}: ${error?.message}`);
    assert.equal(error.code, code, `expected code ${code}, got ${error.code}`);
    return true;
  });
}

// ===========================================================================
// The vocabulary.
// ===========================================================================

test("there are exactly three honest answers about where a document came from", () => {
  assert.deepEqual([...V5_F01_DOCUMENT_PROVENANCE_STATES], [
    "derived_from_stored_artifact",
    "original_first_party",
    "legacy_provenance_unknown",
  ]);
  // A fourth would be the place "probably derived" or "no link found" gets
  // written down as if it were an answer.
  assert.equal(V5_F01_DOCUMENT_PROVENANCE_STATES.length, 3);
  assert.ok(Object.isFrozen(V5_F01_DOCUMENT_PROVENANCE_STATES));
});

test("the document derivative kind is a constant and does not collide with the proposal kind", () => {
  assert.equal(V5_F01_DOCUMENT_VERSION_DERIVATIVE_KIND, "f01_document_version");
  assert.notEqual(V5_F01_DOCUMENT_VERSION_DERIVATIVE_KIND,
    V5_F01_PARSED_PROPOSAL_DERIVATIVE_KIND);
});

test("a document version's derivative id is the (id, version) pair, and the separator is checked", () => {
  assert.equal(documentVersionDerivativeId("synthetic-doc-0001", 3), "synthetic-doc-0001:3");
  // ":" is inside the external-identifier alphabet, so ("a:1", 2) and ("a", ...)
  // could otherwise fold to one key and one document version would silently
  // shadow another's provenance.
  refuses(() => documentVersionDerivativeId("synthetic:doc", 1), "document_id_contains_separator");
  refuses(() => documentVersionDerivativeId("synthetic-doc-0001", 0), "invalid_shape");
});

// ===========================================================================
// The derived case: an exact, immutable binding.
// ===========================================================================

test("a derived document version binds to the exact stored artifact", () => {
  const result = bind();
  assert.equal(result.decision, "allow");
  assert.equal(result.reason_id, "document_source_binding_registered");
  assert.equal(result.provenance_state, "derived_from_stored_artifact");
  assert.equal(result.source_artifact_digest, ARTIFACT_DIGEST);
  assert.equal(result.derivative_id, "synthetic-doc-0001:1");
  assert.equal(result.records_written, 3);

  const link = result.derivative_link;
  assert.equal(link.schema_version, V5_F01_DERIVATIVE_LINK_SCHEMA_VERSION);
  assert.equal(link.tenant, ORGANIZATION_TENANT_ID);
  assert.equal(link.source_artifact_digest, ARTIFACT_DIGEST);
  assert.equal(link.derivative_kind, V5_F01_DOCUMENT_VERSION_DERIVATIVE_KIND);
  assert.equal(link.derivative_id, "synthetic-doc-0001:1");
  assert.equal(link.producer_workflow, PRODUCER);
  assert.equal(link.producer_run_ref, "syn-test-run-0001");
  // The four claims the row makes about itself, hashed with it.
  assert.equal(link.registration_is_provenance, true);
  assert.equal(link.is_exhaustive_inventory, false);
  assert.equal(link.establishes_coverage, false);
  assert.equal(link.permits_deletion, false);
});

test("the link binds to the bytes the record layer actually stores", () => {
  const result = bind();
  // The derivative's content digest must be the digest of the STORED document
  // record, not the kernel's projection digest: the SQL writer checks
  // derivative_content_digest against the document row's own document_digest, and
  // a link bound to any other preimage would refuse there instead of here.
  const envelope = v5F01StoreEnvelope("stored_document_version", { ...result.document_record });
  assert.equal(result.document_digest, envelope.record_digest);
  assert.equal(result.derivative_link.derivative_content_digest, envelope.record_digest);
  assert.equal(result.derivative_link.evidence_digest, envelope.record_digest);
  assert.equal(result.derivative_link.evidence_ref, "stored_document_version");
});

test("produced_at is the server instant, never the producer's own clock", () => {
  const result = bind();
  assert.equal(result.derivative_link.produced_at, NOW);
  assert.equal(result.recorded_at, NOW);
  // And the caller cannot state it: `produced_at` is on the store's derived-only
  // list, so naming it is refused by name rather than quietly ignored.
  refuses(() => bind({ source: { ...DERIVED_SOURCE, produced_at: "2020-01-01T00:00:00Z" } }),
    "caller_derived_field_refused");
});

test("a production that would precede the artifact it came from refuses", () => {
  // produced_at is always the server instant, so the only way to reach this is an
  // artifact the record layer says was created later than now — which is exactly
  // the case a producer's backdated clock would otherwise manufacture.
  const result = bind({
    source_artifact: { artifact_digest: ARTIFACT_DIGEST, created_at: "2026-09-10T09:00:00Z" },
  });
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "production_precedes_source_artifact");
  assert.equal(result.records_written, 0);
  assert.equal(result.derivative_link, null);
});

// ===========================================================================
// Forged source, unknown artifact, mismatched document.
// ===========================================================================

test("an artifact nobody stored is not brought into existence by naming it", () => {
  const result = bind({ source_artifact: null });
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "unknown_source_artifact");
  assert.equal(result.records_written, 0);
  assert.equal(result.derivative_link, null);
});

test("a forged source refuses: the loaded artifact is not the one named", () => {
  const result = bind({
    source_artifact: { artifact_digest: OTHER_ARTIFACT_DIGEST, created_at: "2026-09-01T09:00:00Z" },
  });
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "source_artifact_mismatch");
  assert.equal(result.loaded_artifact_digest, OTHER_ARTIFACT_DIGEST);
  assert.equal(result.records_written, 0);
});

test("a source artifact is NEVER the document's own bytes", () => {
  // The Neon content digest.
  const neon = bind({ source: { ...DERIVED_SOURCE, source_artifact_digest: DOC_BYTES } });
  assert.equal(neon.decision, "refuse");
  assert.equal(neon.reason_id, "document_bytes_are_not_a_source_artifact");
  assert.equal(neon.offending_field, "document.neon_identity.content_digest");

  // The sealed object-storage copy, which is the same bytes under another name.
  const sealed = bind({
    document: draftDocument({
      object_storage_identity: {
        object_key: "synthetic/test/doc-0001.pdf",
        content_digest: SEALED_BYTES,
        byte_length: 1024,
        sealed: true,
      },
    }),
    source: { ...DERIVED_SOURCE, source_artifact_digest: SEALED_BYTES },
  });
  assert.equal(sealed.decision, "refuse");
  assert.equal(sealed.reason_id, "document_bytes_are_not_a_source_artifact");

  // The filed OneDrive official copy. A drive id or an item id cannot even reach
  // this field — it is not a digest — and the digest that CAN reach it is still
  // the document, not its origin.
  const filed = bind({
    document: draftDocument({
      preparation_state: "approved_for_delivery",
      delivery_state: "delivered",
      signature_state: "fully_executed",
      validity_state: "effective",
      onedrive_identity: {
        drive_id: "SYNTHETIC-DRIVE-0001",
        item_id: "SYNTHETIC-ITEM-0001",
        content_digest: DOC_BYTES,
        filing_state: "filed",
      },
    }),
    source: { ...DERIVED_SOURCE, source_artifact_digest: DOC_BYTES },
  });
  assert.equal(filed.decision, "refuse");
  assert.equal(filed.reason_id, "document_bytes_are_not_a_source_artifact");

  // And a OneDrive item id offered as a source artifact is not a digest at all.
  refuses(() => bind({
    source: { ...DERIVED_SOURCE, source_artifact_digest: "SYNTHETIC-ITEM-0001" },
  }), "invalid_digest");
});

test("a prior statement about ANOTHER document version refuses rather than being read", () => {
  const result = bind({
    prior_provenance: {
      document_id: "synthetic-doc-0002",
      version_no: 1,
      document_digest: D(31),
      provenance_state: "derived_from_stored_artifact",
      source_artifact_digest: ARTIFACT_DIGEST,
    },
  });
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "prior_provenance_names_another_document_version");
  assert.equal(result.prior_document_id, "synthetic-doc-0002");
});

test("a document whose states cannot all be true at once records no provenance", () => {
  const result = bind({
    // Signed without ever being delivered.
    document: draftDocument({ signature_state: "partially_signed" }),
  });
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "document_state_incoherent");
  assert.equal(result.violated_constraint, "signature_requires_delivery");
  assert.equal(result.document_record, null);
  assert.equal(result.records_written, 0);
});

// ===========================================================================
// Repointing: one derivative has one original.
// ===========================================================================

test("repointing a document version at a different artifact refuses", () => {
  const first = bind();
  const result = bind({
    source: { ...DERIVED_SOURCE, source_artifact_digest: OTHER_ARTIFACT_DIGEST },
    source_artifact: { artifact_digest: OTHER_ARTIFACT_DIGEST, created_at: "2026-09-01T09:00:00Z" },
    prior_provenance: {
      document_id: "synthetic-doc-0001",
      version_no: 1,
      document_digest: first.document_digest,
      provenance_state: "derived_from_stored_artifact",
      source_artifact_digest: ARTIFACT_DIGEST,
    },
  });
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "document_source_repointing_refused");
  assert.equal(result.prior_source_artifact_digest, ARTIFACT_DIGEST);
  assert.equal(result.records_written, 0);
});

test("changing a document version's provenance STATE is a rewrite and refuses", () => {
  const result = bind({
    prior_provenance: {
      document_id: "synthetic-doc-0001",
      version_no: 1,
      document_digest: D(31),
      provenance_state: "original_first_party",
      source_artifact_digest: null,
    },
  });
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "document_provenance_state_rebinding_refused");
  assert.equal(result.prior_provenance_state, "original_first_party");
});

test("rebinding the same source to different document bytes refuses", () => {
  const result = bind({
    prior_document_digest: D(51),
    prior_provenance: {
      document_id: "synthetic-doc-0001",
      version_no: 1,
      document_digest: D(41),
      provenance_state: "derived_from_stored_artifact",
      source_artifact_digest: ARTIFACT_DIGEST,
    },
  });
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "document_source_content_rebinding_refused");
  // THE TWO PRIORS ARE DIFFERENT FACTS AND THE ANSWER KEEPS THEM APART. The
  // statement's digest is what refused; the CAS operand is what the write was
  // decided against, and it is the value ops.f01_record_document compares to
  // ops.f01_document_current. An earlier form spread `base` and then re-used its
  // prior_document_digest key, so this refusal reported the statement's digest as
  // the CAS operand and a caller retrying from the answer would have decided
  // against a version pointer that was never current.
  assert.equal(result.prior_statement_document_digest, D(41));
  assert.equal(result.prior_document_digest, D(51));
  assert.notEqual(result.prior_statement_document_digest, result.prior_document_digest);
});

test("a genesis rebinding refusal still reports the absent CAS operand as absent", () => {
  const result = bind({
    prior_provenance: {
      document_id: "synthetic-doc-0001",
      version_no: 1,
      document_digest: D(41),
      provenance_state: "derived_from_stored_artifact",
      source_artifact_digest: ARTIFACT_DIGEST,
    },
  });
  assert.equal(result.reason_id, "document_source_content_rebinding_refused");
  assert.equal(result.prior_statement_document_digest, D(41));
  assert.equal(result.prior_document_digest, null);
});

test("a LATER version may name a different source; the exact version is what is immutable", () => {
  // Version 1 came from one artifact. Version 2 is a different derivative — a
  // re-export, or first-party work after a derived draft — and the contract does
  // not make a document's history immutable, only each version's own statement.
  const first = bind();
  const second = bind({
    document: draftDocument({
      neon_identity: { document_id: "synthetic-doc-0001", content_digest: D(12), version_no: 2 },
    }),
    prior_document_digest: first.document_digest,
    source: { ...DERIVED_SOURCE, source_artifact_digest: OTHER_ARTIFACT_DIGEST },
    source_artifact: { artifact_digest: OTHER_ARTIFACT_DIGEST, created_at: "2026-09-01T09:00:00Z" },
    // The prior LOADED for version 2 is version 2's own, and there is none yet.
    prior_provenance: null,
  });
  assert.equal(second.decision, "allow");
  assert.equal(second.source_artifact_digest, OTHER_ARTIFACT_DIGEST);
  assert.equal(second.derivative_id, "synthetic-doc-0001:2");
  assert.notEqual(second.derivative_id, first.derivative_id);

  // A version 2 that is declared first-party is equally allowed: an original
  // successor to a derived version is an ordinary thing and refusing it would be
  // this module inventing a rule nobody settled.
  const original = bind({
    document: draftDocument({
      neon_identity: { document_id: "synthetic-doc-0001", content_digest: D(12), version_no: 2 },
    }),
    prior_document_digest: first.document_digest,
    source: ORIGINAL_SOURCE,
    source_artifact: null,
  });
  assert.equal(original.decision, "allow");
  assert.equal(original.reason_id, "document_declared_original_no_source_artifact");

  // And a statement about version 1 is NOT read across into version 2's decision:
  // it names another version, so it refuses rather than being consulted.
  const crossed = bind({
    document: draftDocument({
      neon_identity: { document_id: "synthetic-doc-0001", content_digest: D(12), version_no: 2 },
    }),
    prior_document_digest: first.document_digest,
    prior_provenance: {
      document_id: "synthetic-doc-0001",
      version_no: 1,
      document_digest: first.document_digest,
      provenance_state: "derived_from_stored_artifact",
      source_artifact_digest: ARTIFACT_DIGEST,
    },
  });
  assert.equal(crossed.decision, "refuse");
  assert.equal(crossed.reason_id, "prior_provenance_names_another_document_version");
  assert.equal(crossed.prior_version_no, 1);
});

test("a statement that agrees with the stored one in every respect is not a rewrite", () => {
  const first = bind();
  const again = bind({
    prior_provenance: {
      document_id: "synthetic-doc-0001",
      version_no: 1,
      document_digest: first.document_digest,
      provenance_state: "derived_from_stored_artifact",
      source_artifact_digest: ARTIFACT_DIGEST,
      derivative_link_digest: digest(first.derivative_link),
    },
  });
  assert.equal(again.decision, "allow");
  assert.equal(again.reason_id, "document_source_binding_registered");
});

// ===========================================================================
// Originals and legacy provenance, kept apart.
// ===========================================================================

test("an original and a legacy-unknown document are recorded as DIFFERENT things", () => {
  const original = bind({ source: ORIGINAL_SOURCE, source_artifact: null });
  const legacy = bind({ source: LEGACY_SOURCE, source_artifact: null });

  assert.equal(original.decision, "allow");
  assert.equal(original.reason_id, "document_declared_original_no_source_artifact");
  assert.equal(original.provenance_state, "original_first_party");

  assert.equal(legacy.decision, "allow");
  assert.equal(legacy.reason_id, "document_declared_legacy_provenance_unknown");
  assert.equal(legacy.provenance_state, "legacy_provenance_unknown");

  // Neither writes a link, and neither claims the other's meaning.
  for (const result of [original, legacy]) {
    assert.equal(result.derivative_link, null);
    assert.equal(result.derivative_id, null);
    assert.equal(result.source_artifact_digest, null);
    assert.equal(result.records_written, 2);
    // The whole reason both need a row: an absent link has never meant an absent
    // derivative, and it must not start meaning one now.
    assert.equal(result.absent_link_means_verified_absence, false);
  }
  assert.notEqual(original.reason_id, legacy.reason_id);
  assert.notEqual(original.provenance_state, legacy.provenance_state);
});

test("an original that also names an origin is two contradictory declarations", () => {
  const named = bind({
    source: { ...ORIGINAL_SOURCE, source_artifact_digest: ARTIFACT_DIGEST },
    source_artifact: SOURCE_ARTIFACT,
  });
  assert.equal(named.decision, "refuse");
  assert.equal(named.reason_id, "source_named_by_original_document");
  assert.equal(named.offending_field, "source.source_artifact_digest");

  const workflow = bind({
    source: { ...LEGACY_SOURCE, producer_workflow: PRODUCER },
    source_artifact: null,
  });
  assert.equal(workflow.decision, "refuse");
  assert.equal(workflow.reason_id, "source_named_by_legacy_document");
  assert.equal(workflow.offending_field, "source.producer_workflow");
});

test("a non-derived declaration must state the basis on which it says so", () => {
  refuses(() => assertDocumentSourceDeclaration({ provenance_state: "original_first_party" }),
    "missing_field");
  refuses(() => assertDocumentSourceDeclaration({ provenance_state: "legacy_provenance_unknown" }),
    "missing_field");
  // And an unregistered fourth answer is a contract violation, not a decision.
  refuses(() => assertDocumentSourceDeclaration({ provenance_state: "probably_derived" }),
    "unknown_document_provenance_state");
  refuses(() => assertDocumentSourceDeclaration({ provenance_state: null }),
    "unknown_document_provenance_state");
});

// ===========================================================================
// Atomic completion: the whole set, or none of it.
// ===========================================================================

test("a derived completion composes three records and binds them to each other", () => {
  const binding = bind();
  const composed = composeDocumentSourceRecords(binding);

  assert.equal(composed.provenance_statement_required, true);
  assert.equal(composed.derivative_link_required, true);
  assert.notEqual(composed.derivative_envelope, null);

  // The provenance statement names THIS document version, THIS source and THIS
  // link, which is exactly what ops.f01_record_document re-checks before writing.
  const record = composed.provenance_record;
  assert.equal(record.schema_version, V5_F01_STORED_DOCUMENT_SOURCE_SCHEMA_VERSION);
  assert.equal(record.tenant, ORGANIZATION_TENANT_ID);
  assert.equal(record.document_id, "synthetic-doc-0001");
  assert.equal(record.version_no, 1);
  assert.equal(record.document_digest, composed.document_envelope.record_digest);
  assert.equal(record.provenance_state, "derived_from_stored_artifact");
  assert.equal(record.source_artifact_digest, ARTIFACT_DIGEST);
  assert.equal(record.derivative_kind, V5_F01_DOCUMENT_VERSION_DERIVATIVE_KIND);
  assert.equal(record.derivative_id, "synthetic-doc-0001:1");
  assert.equal(record.derivative_link_digest, composed.derivative_envelope.record_digest);
  assert.equal(record.producer_workflow, PRODUCER);
  assert.equal(record.producer_run_ref, "syn-test-run-0001");
  assert.equal(record.basis_statement, null);
  assert.equal(record.recorded_by, "codex");
  assert.equal(record.recorded_at, NOW);
  assert.equal(composed.provenance_record_digest, digest(record));

  // The stored link is attributed to the derived principal, never to a caller.
  const stored = composed.derivative_envelope.record;
  assert.equal(stored.registered_by, "codex");
  assert.equal(stored.registered_at, NOW);
});

test("a non-derived completion composes two records and MUST compose no link", () => {
  for (const source of [ORIGINAL_SOURCE, LEGACY_SOURCE]) {
    const composed = composeDocumentSourceRecords(bind({ source, source_artifact: null }));
    assert.equal(composed.derivative_envelope, null);
    assert.equal(composed.derivative_link_digest, null);
    assert.equal(composed.derivative_link_required, false);
    assert.equal(composed.provenance_statement_required, true);
    const record = composed.provenance_record;
    assert.equal(record.source_artifact_digest, null);
    assert.equal(record.derivative_link_digest, null);
    assert.equal(record.derivative_kind, null);
    assert.equal(record.derivative_id, null);
    assert.equal(record.producer_workflow, null);
    assert.equal(record.producer_run_ref, null);
    assert.equal(typeof record.basis_statement, "string");
  }
});

test("a refused binding has no records at all", () => {
  const refused = bind({ source_artifact: null });
  refuses(() => composeDocumentSourceRecords(refused), "binding_not_allowed");
  refuses(() => composeDocumentSourceEnvelopes(refused), "binding_not_allowed");
  refuses(() => composeDocumentSourceRecords(null), "binding_not_allowed");
});

test("Q125's one forbidden inference stays forbidden on this surface too", () => {
  // Fully executed with no filed OneDrive copy. The reviewed store PERSISTS this
  // rather than discarding it, so the binding stays recordable and the stored
  // document says out loud that the official filing is incomplete.
  const binding = bind({
    document: draftDocument({
      preparation_state: "approved_for_delivery",
      delivery_state: "delivered",
      signature_state: "fully_executed",
      validity_state: "effective",
    }),
  });
  assert.equal(binding.decision, "allow");
  assert.equal(binding.official_filing_state, "incomplete_official_filing");
  assert.equal(binding.document_record.official_filing_state, "incomplete_official_filing");
  assert.equal(binding.object_storage_success_implies_official_filing, false);
  assert.equal(binding.neon_success_implies_official_filing, false);
  // And the provenance edge is still registered, because the document version is
  // still a derived record: an incomplete filing is not an excuse to lose the
  // provenance of the thing that was produced.
  const composed = composeDocumentSourceRecords(binding);
  assert.notEqual(composed.derivative_envelope, null);
});

// ===========================================================================
// Replay: the same request twice is the same bytes.
// ===========================================================================

test("evaluating the same request twice produces byte-identical records", () => {
  const a = composeDocumentSourceRecords(bind());
  const b = composeDocumentSourceRecords(bind());
  assert.equal(canonicalJson(a.document_record), canonicalJson(b.document_record));
  assert.equal(canonicalJson(a.provenance_record), canonicalJson(b.provenance_record));
  assert.equal(canonicalJson(a.derivative_envelope.record),
    canonicalJson(b.derivative_envelope.record));
  assert.equal(a.document_digest, b.document_digest);
  assert.equal(a.provenance_record_digest, b.provenance_record_digest);
  assert.equal(a.derivative_link_digest, b.derivative_link_digest);
});

test("a different server instant is a different record, so `now` genuinely binds", () => {
  const a = bind();
  const b = bind({ now: "2026-09-09T12:00:01.000Z" });
  assert.notEqual(a.document_digest, b.document_digest);
  assert.notEqual(digest(a.derivative_link), digest(b.derivative_link));
});

test("a different prior-document digest is a different record, so the CAS operand binds", () => {
  const genesis = bind();
  const successor = bind({ prior_document_digest: D(51) });
  assert.equal(genesis.prior_document_digest, null);
  assert.equal(successor.prior_document_digest, D(51));
  assert.notEqual(genesis.document_digest, successor.document_digest);
  assert.equal(successor.document_record.prior_document_digest, D(51));
});

// ===========================================================================
// Nothing here claims coverage, an inventory, or a deletion.
// ===========================================================================

test("no answer, allowed or refused, claims coverage or permits a deletion", () => {
  const answers = [
    bind(),
    bind({ source_artifact: null }),
    bind({ source: ORIGINAL_SOURCE, source_artifact: null }),
    bind({ source: LEGACY_SOURCE, source_artifact: null }),
    bind({ document: draftDocument({ signature_state: "partially_signed" }) }),
  ];
  for (const answer of answers) {
    assert.equal(answer.establishes_coverage, false, answer.reason_id);
    assert.equal(answer.is_exhaustive_inventory, false, answer.reason_id);
    assert.equal(answer.permits_deletion, false, answer.reason_id);
    assert.equal(answer.absent_link_means_verified_absence, false, answer.reason_id);
    assert.equal(answer.source_artifact_inferred_from_document_bytes, false, answer.reason_id);
    assert.equal(answer.source_artifact_inferred_from_onedrive_identity, false, answer.reason_id);
    assert.deepEqual(answer.effects, V5_NO_EFFECTS);
    assert.ok(Object.isFrozen(answer));
  }
});

test("the stored record says the same six things in the bytes it hashes to", () => {
  const record = storedDocumentSourceProvenanceRecord({
    document_id: "synthetic-doc-0001",
    version_no: 1,
    document_digest: D(61),
    provenance_state: "legacy_provenance_unknown",
    basis_statement: "synthetic",
    recorded_by: "codex",
    recorded_at: NOW,
  });
  assert.equal(record.registration_is_provenance, true);
  assert.equal(record.is_exhaustive_inventory, false);
  assert.equal(record.establishes_coverage, false);
  assert.equal(record.permits_deletion, false);
  assert.equal(record.source_artifact_inferred_from_document_bytes, false);
  assert.equal(record.source_artifact_inferred_from_onedrive_identity, false);
  // A record whose flags were edited is a different record with a different
  // digest, which is what makes the CHECK constraints in ops enforceable.
  assert.notEqual(digest(record),
    digest({ ...record, establishes_coverage: true }));
});

// ===========================================================================
// The caller supplies no authority and no derived value.
// ===========================================================================

test("a caller cannot name authority or a server-derived value on the declaration", () => {
  const cases = [
    ["actor", "caller_authority_field_refused"],
    ["override", "caller_authority_field_refused"],
    ["trusted_caller", "caller_authority_field_refused"],
    ["authorized_by", "caller_authority_field_refused"],
    ["registered_by", "caller_derived_field_refused"],
    ["recorded_at", "caller_derived_field_refused"],
    ["derivative_coverage", "caller_derived_field_refused"],
    ["link_digest", "caller_derived_field_refused"],
  ];
  for (const [key, code] of cases) {
    refuses(() => assertDocumentSourceDeclaration({ ...DERIVED_SOURCE, [key]: "anything" }), code);
  }
  // And an ordinary unknown field is still an unknown field.
  refuses(() => assertDocumentSourceDeclaration({ ...DERIVED_SOURCE, extra: 1 }), "unknown_field");
});

test("a declaration read through a getter is refused rather than read twice", () => {
  const hostile = { provenance_state: "original_first_party" };
  let reads = 0;
  Object.defineProperty(hostile, "basis_statement", {
    enumerable: true,
    get() { reads += 1; return reads === 1 ? "harmless" : "something else"; },
  });
  refuses(() => assertDocumentSourceDeclaration(hostile), "accessor_property_refused");
});

test("the tenant is checked and never selected", () => {
  refuses(() => bind({ tenant: "some-other-tenant" }), "tenant_mismatch");
  refuses(() => bind({ recorded_by: undefined }), "missing_field");
  refuses(() => bind({ now: "2026-02-31T00:00:00Z" }), "invalid_timestamp");
  refuses(() => bind({ now: "2026-09-09" }), "invalid_timestamp");
  refuses(() => bind({ unexpected: true }), "unknown_field");
});

test("identifier validation does not drift from the kernel's", () => {
  // A zero-width space renders as nothing and would make two workflows look like
  // one. Both this module and the kernel refuse it, and the test asserts the
  // agreement rather than assuming the two copies stayed aligned.
  // BUILT FROM A CODE POINT, NEVER EMBEDDED. A test file that contains the very
  // character it exists to refuse reads as binary to file(1), rg and git diff,
  // which is the same reason the kernel writes its key separator as an escape.
  const invisible = `syn${String.fromCharCode(0x200b)}test`;
  refuses(() => assertDocumentSourceDeclaration({
    ...DERIVED_SOURCE, producer_workflow: invisible,
  }), "unsafe_unicode");
  assert.throws(() => evaluateDerivativeRegistration({
    tenant: ORGANIZATION_TENANT_ID,
    registration: {
      source_artifact_digest: ARTIFACT_DIGEST,
      derivative: { derivative_kind: "k", derivative_id: "i", content_digest: D(71) },
      producer: { producer_workflow: invisible, producer_run_ref: "r" },
      produced_at: NOW,
      evidence: { evidence_ref: "e", evidence_digest: D(72) },
    },
    source_artifact: SOURCE_ARTIFACT,
    now: NOW,
  }), error => error.code === "unsafe_unicode");
});

// ===========================================================================
// The seam is incomplete without the parent hunk, and says so.
// ===========================================================================

test("the provenance envelope refuses until the store registers its record kind", () => {
  const record = storedDocumentSourceProvenanceRecord({
    document_id: "synthetic-doc-0001",
    version_no: 1,
    document_digest: D(61),
    provenance_state: "original_first_party",
    basis_statement: "synthetic",
    recorded_by: "codex",
    recorded_at: NOW,
  });

  if (V5_F01_STORE_RECORD_KINDS.includes(V5_F01_DOCUMENT_SOURCE_RECORD_KIND)) {
    // The parent hunk has landed. The envelope must then be an ordinary store
    // envelope that says the three things every provenance record says.
    const envelope = documentSourceProvenanceEnvelope(record);
    assert.equal(envelope.record_kind, V5_F01_DOCUMENT_SOURCE_RECORD_KIND);
    assert.equal(envelope.record_digest, digest(record));
    assert.equal(envelope.establishes_coverage, false);
    assert.equal(envelope.is_exhaustive_inventory, false);
    assert.equal(envelope.permits_deletion, false);
    const composed = composeDocumentSourceEnvelopes(bind());
    assert.equal(composed.provenance_envelope.record_kind, V5_F01_DOCUMENT_SOURCE_RECORD_KIND);
    return;
  }

  // It has not. This is the state the change ships in, and the refusal is the
  // statement that the seam is not finished: the parent must add
  // "stored_document_source_provenance" to V5_F01_STORE_RECORD_KINDS and amend
  // recordDocumentIdentity to call the six-argument ops.f01_record_document.
  refuses(() => documentSourceProvenanceEnvelope(record), "store_record_kind_not_registered");
  refuses(() => composeDocumentSourceEnvelopes(bind()), "store_record_kind_not_registered");
  assert.throws(() => documentSourceProvenanceEnvelope(record), error =>
    /record-source-authority-store\.v5\.js/.test(error.message));
});

test("the two record kinds this seam does NOT need the parent for are already registered", () => {
  // The document version and the derivative link are ordinary reviewed kinds, so
  // the parts of this seam that can be proved today are proved today.
  assert.ok(V5_F01_STORE_RECORD_KINDS.includes("stored_document_version"));
  assert.ok(V5_F01_STORE_RECORD_KINDS.includes("stored_derivative_link"));
});

test("the module's own contract self-checks hold, and they run from here", () => {
  // They used to run at module evaluation. They cannot: the parent hunk makes
  // record-source-authority-store.v5.js import this module, and two of these read
  // an `export const` of that store — a temporal-dead-zone ReferenceError at
  // import time whenever the store is loaded first, which is every time the
  // persistence tail loads. Running them here keeps the invariant and removes the
  // cycle hazard.
  assert.equal(assertDocumentSourceContract(), true);
});

test("this module reads NO store binding while it is being evaluated", () => {
  // The evidence a pure suite can actually produce: the module source names the
  // two store bindings only inside function bodies. A top-level read is what would
  // break the cycle, and it would look like one of these at column zero.
  const source = readFileSync(new URL("../src/document-derivative-registration.v5.js",
    import.meta.url), "utf8");
  const body = source.slice(source.indexOf("export const V5_F01_DOCUMENT_SOURCE_SCHEMA_VERSION"));
  for (const line of body.split("\n")) {
    if (/^\s*(\/\/|\*|\/\*)/.test(line)) continue;
    for (const binding of ["V5_F01_DERIVED_ONLY_FIELDS", "V5_F01_STORE_RECORD_KINDS",
      "v5F01StoreEnvelope", "storedDocumentRecord", "storedDerivativeLinkRecord"]) {
      if (!line.includes(binding)) continue;
      assert.ok(/^\s+/.test(line),
        `${binding} is read at module scope, which is a TDZ ReferenceError once the store imports this module: ${line.trim()}`);
    }
  }
});

// ===========================================================================
// What the parent still has to land, recomputed rather than remembered.
// ===========================================================================

test("the unlanded parent hunks are reported, and a null is never counted as done", () => {
  const gaps = documentSourceIntegrationGaps();
  assert.equal(gaps.length, 4);
  assert.ok(Object.isFrozen(gaps));
  const byName = Object.fromEntries(gaps.map(entry => [entry.gap, entry]));

  // Two are decidable from constants this module imports.
  assert.equal(byName.store_record_kind_not_registered.landed,
    V5_F01_STORE_RECORD_KINDS.includes(V5_F01_DOCUMENT_SOURCE_RECORD_KIND));
  assert.equal(byName.kernel_reserved_kind_not_registered.landed,
    V5_F01_RESERVED_DERIVATIVE_KINDS.includes(V5_F01_DOCUMENT_VERSION_DERIVATIVE_KIND));

  // Two are not, and they say so with null rather than with a comfortable false.
  assert.equal(byName.document_writer_not_amended.landed, null);
  assert.equal(byName.domain_schema_not_folded.landed, null);
  for (const entry of gaps) {
    assert.equal(typeof entry.where, "string");
    assert.ok(entry.what.length > 0);
  }

  // A null is not a pass: the assertion refuses on anything that is not true.
  refuses(() => assertDocumentSourceIntegrationComplete(), "parent_integration_incomplete");
});

test("the kernel does not yet reserve the document-version kind, and that is a REPORTED gap", () => {
  // WHY THIS MATTERS AND IS NOT TIDINESS. A document version's derivative identity
  // is "<document_id>:<version_no>" — a value a caller can predict before the
  // document exists. The identity index is unique per (tenant, kind, id) over an
  // append-only table with no release path, so a public caller that registered
  // ("f01_document_version", "some-doc:1") first would make the genuine completion
  // of that version conflict for ever, and would leave a provenance edge asserting
  // the document came from an artifact it did not.
  //
  // The candidate schema already closes it: ops.f01_reserved_derivative_kinds()
  // there names BOTH kinds, and ops.f01_register_derivative_link refuses against
  // that list before it claims an idempotency key. The kernel mirror is the
  // parent's, and until it lands the public JS surface passes the request through
  // to a database raise instead of returning a named refusal.
  const reserved = [...V5_F01_RESERVED_DERIVATIVE_KINDS];
  assert.ok(reserved.includes(V5_F01_PARSED_PROPOSAL_DERIVATIVE_KIND));
  const gap = documentSourceIntegrationGaps()
    .find(entry => entry.gap === "kernel_reserved_kind_not_registered");
  assert.equal(gap.landed, reserved.includes(V5_F01_DOCUMENT_VERSION_DERIVATIVE_KIND));
  if (!gap.landed) {
    assert.ok(/V5_F01_RESERVED_DERIVATIVE_KINDS/.test(gap.what));
  }
});

// ===========================================================================
// The free-text bounds, and the UTF-16 arithmetic the SQL half has to reproduce.
// ===========================================================================

test("the three free-text bounds are 512, 128 and 255, and they are enforced", () => {
  const basis = "b".repeat(512);
  assert.equal(assertDocumentSourceDeclaration({
    provenance_state: "original_first_party", basis_statement: basis,
  }).basis_statement, basis);
  refuses(() => assertDocumentSourceDeclaration({
    provenance_state: "original_first_party", basis_statement: `${basis}b`,
  }), "text_too_long");

  refuses(() => assertDocumentSourceDeclaration({
    ...DERIVED_SOURCE, producer_workflow: "w".repeat(129),
  }), "text_too_long");
  assert.equal(assertDocumentSourceDeclaration({
    ...DERIVED_SOURCE, producer_workflow: "w".repeat(128),
  }).producer_workflow, "w".repeat(128));

  refuses(() => assertDocumentSourceDeclaration({
    ...DERIVED_SOURCE, producer_run_ref: "r".repeat(256),
  }), "text_too_long");
  assert.equal(assertDocumentSourceDeclaration({
    ...DERIVED_SOURCE, producer_run_ref: "r".repeat(255),
  }).producer_run_ref, "r".repeat(255));

  // The two producer fields are IDENTIFIERS, not free text: ASCII only, so their
  // code-point, code-unit and byte lengths are the same number and the SQL mirror
  // has nothing to reconcile.
  refuses(() => assertDocumentSourceDeclaration({
    ...DERIVED_SOURCE, producer_workflow: "has a space",
  }), "invalid_identifier");
  refuses(() => assertDocumentSourceDeclaration({
    ...DERIVED_SOURCE, producer_run_ref: "runé",
  }), "invalid_identifier");
});

test("a basis statement is refused for the same reasons on both sides of the seam", () => {
  const original = { provenance_state: "original_first_party" };
  // Not NFC: "e" plus a combining acute is a different byte string from "é", and
  // it is refused rather than normalized, so two statements cannot render alike
  // and hash differently.
  refuses(() => assertDocumentSourceDeclaration({
    ...original, basis_statement: `authored here e${String.fromCharCode(0x0301)}`,
  }), "non_canonical_unicode");
  // Invisible, control and bidirectional characters.
  refuses(() => assertDocumentSourceDeclaration({
    ...original, basis_statement: `authored${String.fromCharCode(0x202e)}here`,
  }), "unsafe_unicode");
  refuses(() => assertDocumentSourceDeclaration({
    ...original, basis_statement: `authored${String.fromCharCode(0x0009)}here`,
  }), "unsafe_unicode");
  // Leading or trailing whitespace, INCLUDING the whitespace nobody sees. JS
  // trim() removes U+00A0 and the U+2000 block; a SQL mirror written with btrim()
  // and its default space-only set would accept these, so the candidate spells the
  // set out.
  for (const point of [0x0020, 0x00a0, 0x2003, 0x3000]) {
    refuses(() => assertDocumentSourceDeclaration({
      ...original, basis_statement: `${String.fromCharCode(point)}authored here`,
    }), "untrimmed_text");
    refuses(() => assertDocumentSourceDeclaration({
      ...original, basis_statement: `authored here${String.fromCharCode(point)}`,
    }), "untrimmed_text");
  }
});

test("the length bound counts UTF-16 code units, which is NOT what SQL length() counts", () => {
  // One astral character is ONE code point and TWO UTF-16 code units. PostgreSQL's
  // length() would count it as one, so a bound written as length(x) <= 512 in the
  // schema would admit a statement of 512 astral characters — 1024 code units —
  // that this function refuses. The candidate mirrors the bound through
  // ops.f01_docsource_utf16_length(), whose arithmetic is
  //     2 * length(x) - length(x with astral characters removed)
  // which is exactly the count below. BUILT FROM CODE POINTS, never embedded, so
  // this file stays reviewable as text.
  const astral = String.fromCodePoint(0x1f5c2);
  assert.equal([...astral].length, 1);
  assert.equal(astral.length, 2);
  assert.equal(`a${astral}`.length, 3);

  const atBound = "x".repeat(510) + astral;      // 510 + 2 = 512 code units
  const overBound = "x".repeat(511) + astral;    // 511 + 2 = 513 code units
  assert.equal(atBound.length, 512);
  assert.equal([...atBound].length, 511);        // what SQL length() would see
  assert.equal(assertDocumentSourceDeclaration({
    provenance_state: "legacy_provenance_unknown", basis_statement: atBound,
  }).basis_statement, atBound);
  refuses(() => assertDocumentSourceDeclaration({
    provenance_state: "legacy_provenance_unknown", basis_statement: overBound,
  }), "text_too_long");
  // The SQL arithmetic, computed here over the same string: code points plus the
  // number of astral characters.
  const codePoints = [...overBound].length;
  const astralCount = [...overBound].filter(c => c.codePointAt(0) > 0xffff).length;
  assert.equal(codePoints + astralCount, overBound.length);
  assert.equal(codePoints + astralCount, 513);
});

// ===========================================================================
// Source parity with the SQL half. Read as text; nothing is executed.
// ===========================================================================

const CANDIDATE_SQL = readFileSync(
  new URL("../../ops/document-derivative-registration.candidate.sql", import.meta.url), "utf8");
const FIXTURE_SQL = readFileSync(
  new URL("./document-derivative-registration-postgres.sql", import.meta.url), "utf8");

test("the candidate schema names the same vocabulary this module enforces", () => {
  for (const state of V5_F01_DOCUMENT_PROVENANCE_STATES) {
    assert.ok(CANDIDATE_SQL.includes(`'${state}'`), `the candidate never names ${state}`);
    assert.ok(FIXTURE_SQL.includes(`'${state}'`), `the fixture never names ${state}`);
  }
  assert.ok(CANDIDATE_SQL.includes(`'${V5_F01_DOCUMENT_VERSION_DERIVATIVE_KIND}'`));
  assert.ok(CANDIDATE_SQL.includes(`'${V5_F01_DOCUMENT_SOURCE_RECORD_KIND}'`));
  assert.ok(CANDIDATE_SQL.includes(V5_F01_STORED_DOCUMENT_SOURCE_SCHEMA_VERSION));
  assert.ok(FIXTURE_SQL.includes(V5_F01_STORED_DOCUMENT_SOURCE_SCHEMA_VERSION));
});

test("the candidate reserves BOTH internally produced derivative kinds", () => {
  // The one line that closes the pre-claim. It has to name the parsed-proposal
  // kind as well, because CREATE OR REPLACE over the shipped function REPLACES the
  // list rather than adding to it — dropping the existing kind here would reopen
  // the hole this same guard exists to close for the other producer.
  const replaced = CANDIDATE_SQL.slice(
    CANDIDATE_SQL.indexOf("FUNCTION ops.f01_reserved_derivative_kinds()"));
  const body = replaced.slice(0, replaced.indexOf("$$;") + 3);
  assert.ok(body.includes(`'${V5_F01_PARSED_PROPOSAL_DERIVATIVE_KIND}'`),
    "the replacement drops the kernel's existing reserved kind");
  assert.ok(body.includes(`'${V5_F01_DOCUMENT_VERSION_DERIVATIVE_KIND}'`),
    "the replacement does not reserve the document-version kind");
});

test("the candidate's free-text bounds are the same three numbers", () => {
  // The bound is mirrored through the UTF-16 helper, never through length(), and
  // the constraint that carries it is named so a reviewer can find both halves.
  assert.ok(CANDIDATE_SQL.includes("f01_docsource_utf16_length"));
  assert.ok(CANDIDATE_SQL.includes("f01_docsource_is_safe_text(basis_statement, 512)"));
  assert.ok(CANDIDATE_SQL.includes("f01_docsource_is_external_ident(producer_workflow, 128)"));
  assert.ok(CANDIDATE_SQL.includes("f01_docsource_is_external_ident(producer_run_ref, 255)"));
  // The identifier alphabet, character for character, is the kernel's.
  assert.ok(CANDIDATE_SQL.includes("^[A-Za-z0-9][A-Za-z0-9._:/@!+=-]*$"));
});

test("the fixture expects the reserved-kind refusal on the public surface", () => {
  // The public registration surface must now refuse a document-version link
  // OUTRIGHT. A fixture that still expected f01_derivative_source_conflict there
  // would be asserting the old, pre-claimable behaviour and would fail against the
  // candidate it exists to exercise.
  assert.ok(FIXTURE_SQL.includes("f01_reserved_derivative_kind"));
  assert.ok(FIXTURE_SQL.includes("f01_derivative_source_conflict"),
    "the genuine source conflict must still be proved somewhere");
});
