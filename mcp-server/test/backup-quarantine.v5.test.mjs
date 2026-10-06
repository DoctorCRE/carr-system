// V5-F08 — the one-way quarantine admission and the classify-or-purge terminal
// reconciliation, proved clause by clause.
//
// The positive case comes first on purpose: a rule that only ever refuses cannot
// be told apart from a broken one, so every admission negative below is a single
// named mutation of ONE clean request that admits.
//
// TWO THINGS THIS SUITE REFUSES TO DO. It never asserts a value it computed with
// the function under test — the two seals are re-derived here from the digest
// kernel with their preimages written out by hand, and the policy digest is
// re-hashed with node's own createHash — and it never lets a negative pass on an
// unrelated reason: every refusal asserts the blocking check as well as the
// reason id, so a request that refuses for the wrong reason fails here.
//
//   node --test mcp-server/test/backup-quarantine.v5.test.mjs
//                mcp-server/test/global-boundaries.v5.test.mjs

import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { digest } from "../src/artifact-trust.js";
import {
  V5BoundaryError,
  V5_NO_EFFECTS,
  V5_PROHIBITED_DATA_CLASSES,
  V5_DATA_CLASSES,
} from "../src/global-boundaries.v5.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  V5_BACKUP_QUARANTINE_SCHEMA_VERSION,
  V5_BACKUP_QUARANTINE_POLICY_VERSION,
  V5_SOURCE_ROSTER_ENTRY_KIND,
  V5_QUARANTINE_TERMINAL_RECEIPT_KIND,
  V5_QUARANTINE_DIRECTION,
  V5_BACKUP_ADMISSION_CHECKS,
  V5_CHECKS_BEFORE_ANY_CONTENT_FACT_IS_READ,
  V5_ADMISSION_CHECK_STATES,
  V5_SOURCE_PHI_CAPABILITIES,
  V5_ADMISSIBLE_SOURCE_PHI_CAPABILITY,
  V5_SCAN_COMPLETION_STATES,
  V5_SCANNER_RETENTION_STATES,
  V5_ADMISSIBLE_SCANNER_RETENTION,
  V5_SCAN_FINDING_STATES,
  V5_SCAN_FINDING_KEYS,
  V5_OPAQUE_CONTENT_STATES,
  V5_QUARANTINE_TERMINAL_OUTCOMES,
  V5_RECONCILIATION_EXCEPTION_KINDS,
  V5_BACKUP_QUARANTINE_REASON_IDS,
  V5_RESTORE_AND_RECOVERY_MATRIX_SEAM,
  V5_OUTBOUND_RECONCILIATION_SEAM,
  V5_ENUMERATED_SOURCE_ROSTER_SEAM,
  V5_NON_RETAINING_SCANNER_SEAM,
  V5_BACKUP_BLOCKING_DECISION_IDS,
  backupSourceRosterEntryDigest,
  quarantineTerminalReceiptDigest,
  normalizeBackupSourceRoster,
  normalizeBackupSourceScanReport,
  evaluateBackupSourceAdmission,
  reconcileQuarantineTerminalOutcomes,
  v5BackupQuarantinePolicyPreimage,
  v5BackupQuarantinePolicyDigest,
  v5BackupQuarantinePolicyCanonicalBytes,
  v5BackupQuarantineProjection,
} from "../src/backup-quarantine.v5.js";

const SRC_PATH = fileURLToPath(new URL("../src/backup-quarantine.v5.js", import.meta.url));

const SOURCE_ID = "crm.lease-economics-export";
const OTHER_SOURCE_ID = "crm.inbound-document-drop";
const OBJECT_ID = "quarantine-object-0001";
const CONTENT = "sha256:" + "1".repeat(64);
const OTHER_CONTENT = "sha256:" + "2".repeat(64);
const ROSTER_DIGEST = "sha256:" + "3".repeat(64);
const WRONG_POLICY_DIGEST = "sha256:" + "4".repeat(64);
const HOLDER = "joe-local";
const AUTHORITY = "joe";

/** Two permitted classes, deliberately written out of order in the fixtures. */
const ENUMERATED_CLASSES = ["lease_economics", "property_attribute"];

function boundaryError(code) {
  return error => error instanceof V5BoundaryError && error.code === code;
}

// ---------------------------------------------------------------------------
// One clean request, and the single-field mutations of it.
// ---------------------------------------------------------------------------

function rosterEntry(overrides = {}) {
  const fields = {
    source_id: SOURCE_ID,
    source_label: "CRM lease economics nightly export",
    phi_capability: "phi_incapable",
    declared_data_classes: ENUMERATED_CLASSES,
    ...overrides,
  };
  const { source_id, ...rest } = fields;
  return { ...rest, sealed_entry_digest: backupSourceRosterEntryDigest(fields) };
}

function roster({ entries, ...overrides } = {}) {
  return normalizeBackupSourceRoster({
    roster_digest: ROSTER_DIGEST,
    enumeration_authority: AUTHORITY,
    entries: entries ?? { [SOURCE_ID]: rosterEntry() },
    ...overrides,
  });
}

function scan({ findings = {}, ...overrides } = {}) {
  return normalizeBackupSourceScanReport({
    scanner_id: "carr.source-scanner",
    scanner_version: "1.4.0",
    scanned_content_digest: CONTENT,
    completion: "complete",
    retention: "non_retaining",
    findings: {
      phi: "none_found",
      credential: "none_found",
      nested_archive: "none_found",
      opaque_or_encrypted: "absent",
      ...findings,
    },
    ...overrides,
  });
}

function candidate(overrides = {}) {
  return {
    source_id: SOURCE_ID,
    object_id: OBJECT_ID,
    content_digest: CONTENT,
    declared_data_classes: ["lease_economics"],
    ...overrides,
  };
}

function actor(overrides = {}) {
  return { slug: HOLDER, human: false, sponsoring_human_slug: "joe", ...overrides };
}

/** The one clean request every admission negative below mutates by one field. */
function admit(overrides = {}) {
  return evaluateBackupSourceAdmission({
    actor: actor(),
    roster: roster(),
    candidate: candidate(),
    scan: scan(),
    ...overrides,
  });
}

// ---------------------------------------------------------------------------
// The positive.
// ---------------------------------------------------------------------------

test("positive: a clean candidate from an enumerated PHI-incapable source is admitted", () => {
  const result = admit();
  assert.equal(result.decision, "admit");
  assert.equal(result.reason_id, "admitted_to_one_way_quarantine_after_all_negatives_cleared");
  assert.equal(result.blocking_check, null);
  assert.deepEqual(result.checks_satisfied, [...V5_BACKUP_ADMISSION_CHECKS]);
  assert.deepEqual(result.checks_not_reached, []);
  assert.equal(result.candidate.source_enumerated, true);
  assert.equal(result.candidate.enumeration_authority, AUTHORITY);
  assert.equal(result.actor.slug, HOLDER);
  // Read from identity.js rather than re-derived here.
  assert.equal(result.actor.authorization_class, "sponsored_agent");
  // An admit is not a copy, and the quarantine has no way out.
  assert.equal(result.quarantine_direction, V5_QUARANTINE_DIRECTION);
  assert.equal(result.grants_read_back, false);
  assert.equal(result.copies_source_bytes, false);
  assert.equal(result.reads_source_content, false);
  assert.equal(result.runs_scanner, false);
  assert.deepEqual(result.effects, V5_NO_EFFECTS);
});

// ---------------------------------------------------------------------------
// Catalog item 2 — only enumerated PHI-incapable source enters quarantine.
// ---------------------------------------------------------------------------

test("clause 2: a source that is not on the roster refuses before any content fact is read", () => {
  // The scan is immaculate. It is never reached, which is the point: the
  // enumeration cannot be satisfied by a clean bill of health.
  const result = admit({ candidate: candidate({ source_id: OTHER_SOURCE_ID }) });
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "source_not_enumerated");
  assert.equal(result.blocking_check, "source_enumerated");
  assert.equal(result.check_states.source_enumerated.enumerated, false);
  assert.equal(result.candidate.source_enumerated, false);
  for (const check of ["scan_coverage", "scanner_retention", "phi_finding", "credential_finding",
    "nested_archive", "opaque_or_encrypted"]) {
    assert.equal(result.check_states[check].state, "not_reached", check);
    assert.equal(result.check_states[check].blocked_by, "source_enumerated", check);
  }
  assert.deepEqual(result.checks_satisfied, []);
});

test("clause 2: a roster entry re-pointed after signing refuses on its own moved seal", () => {
  // Sealed as phi_capable, then re-pointed to phi_incapable without re-sealing —
  // the registry-side half of the enumeration negative. It must refuse on the
  // SEAL, before the (now attractive) capability field is read at all.
  const sealedAsCapable = rosterEntry({ phi_capability: "phi_capable" });
  const moved = { ...sealedAsCapable, phi_capability: "phi_incapable" };
  const result = admit({ roster: roster({ entries: { [SOURCE_ID]: moved } }) });
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "roster_entry_digest_moved");
  assert.equal(result.blocking_check, "roster_entry_integrity");
  assert.equal(result.check_states.roster_entry_integrity.sealed_entry_digest,
    sealedAsCapable.sealed_entry_digest);
  assert.notEqual(result.check_states.roster_entry_integrity.recomputed_entry_digest,
    sealedAsCapable.sealed_entry_digest);
  assert.equal(result.check_states.source_phi_capability.state, "not_reached");
});

test("clause 2: a PHI-capable source refuses and an unassessed one blocks with its own reason", () => {
  const capable = admit({
    roster: roster({ entries: { [SOURCE_ID]: rosterEntry({ phi_capability: "phi_capable" }) } }),
  });
  assert.equal(capable.decision, "refuse");
  assert.equal(capable.reason_id, "source_not_phi_incapable");
  assert.equal(capable.blocking_check, "source_phi_capability");
  assert.equal(capable.check_states.source_phi_capability.state, "violated");

  // Nobody assessed it. That is a different fact from an assessment that found
  // PHI capability, and it gets its own reason id and its own state.
  const unassessed = admit({
    roster: roster({ entries: { [SOURCE_ID]: rosterEntry({ phi_capability: "unassessed" }) } }),
  });
  assert.equal(unassessed.decision, "refuse");
  assert.equal(unassessed.reason_id, "source_phi_capability_unassessed");
  assert.equal(unassessed.blocking_check, "source_phi_capability");
  assert.equal(unassessed.check_states.source_phi_capability.state, "unobservable");
  assert.notEqual(unassessed.reason_id, capable.reason_id);
});

test("clause 2: an object may not declare a class its source was never enumerated to hold", () => {
  // "market_comp" is a permitted class that clears the privacy boundary on its
  // own, so this refusal can only come from the enumeration check.
  const result = admit({ candidate: candidate({ declared_data_classes: ["market_comp"] }) });
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "declared_class_outside_enumerated_source");
  assert.equal(result.blocking_check, "declared_class_within_enumerated_source");
  assert.deepEqual(result.check_states.declared_class_within_enumerated_source
    .classes_outside_enumeration, ["market_comp"]);
  assert.equal(result.check_states.declared_data_class_boundary.state, "not_reached");

  // Both enumerated classes together still clear.
  assert.equal(admit({ candidate: candidate({ declared_data_classes: ENUMERATED_CLASSES }) }).decision,
    "admit");
});

test("clause 2: the privacy boundary is S01's answer, read rather than re-decided here", () => {
  // A roster enumerated to hold PHI, and a candidate declaring it. The refusal
  // carries global-boundaries.v5.js's OWN reason id, which is what proves the
  // question was asked there rather than answered again here.
  const phiRoster = roster({
    entries: { [SOURCE_ID]: rosterEntry({ declared_data_classes: ["lease_economics", "phi"] }) },
  });
  const refused = admit({
    roster: phiRoster,
    candidate: candidate({ declared_data_classes: ["phi"] }),
  });
  assert.equal(refused.decision, "refuse");
  assert.equal(refused.reason_id, "declared_data_class_refused_by_privacy_boundary");
  assert.equal(refused.blocking_check, "declared_data_class_boundary");
  assert.equal(refused.check_states.declared_data_class_boundary.privacy_decision, "refuse");
  assert.equal(refused.check_states.declared_data_class_boundary.privacy_reason_id,
    "phi_or_raw_patient_location_refused");
  assert.deepEqual(refused.check_states.declared_data_class_boundary.prohibited_classes, ["phi"]);

  // The aggregate route keeps its own reason id rather than collapsing into the
  // refusal: "needs an independent privacy route" and "never without an
  // amendment" are different answers and an operator must be able to tell them
  // apart.
  const routedRoster = roster({
    entries: {
      [SOURCE_ID]: rosterEntry({
        declared_data_classes: ["lease_economics", "aggregate_patient_volume_estimate"],
      }),
    },
  });
  const routed = admit({
    roster: routedRoster,
    candidate: candidate({ declared_data_classes: ["aggregate_patient_volume_estimate"] }),
  });
  assert.equal(routed.reason_id, "declared_data_class_requires_independent_privacy_route");
  assert.equal(routed.check_states.declared_data_class_boundary.privacy_decision,
    "needs_independent_privacy_route");
  assert.deepEqual(routed.check_states.declared_data_class_boundary.routed_classes,
    ["aggregate_patient_volume_estimate"]);
  assert.notEqual(routed.reason_id, refused.reason_id);

  // And this module holds no second privacy registry: its policy binds S01's
  // prohibited set, so the digest moves when that set moves.
  assert.deepEqual(v5BackupQuarantinePolicyPreimage().prohibited_data_classes,
    [...V5_PROHIBITED_DATA_CLASSES].sort());
  assert.deepEqual(v5BackupQuarantinePolicyPreimage().registered_data_classes,
    [...V5_DATA_CLASSES].sort());
  assert.equal(v5BackupQuarantinePolicyPreimage().redecides_privacy_boundary, false);
});

// ---------------------------------------------------------------------------
// Catalog item 1 — the scan refusals.
// ---------------------------------------------------------------------------

test("clause 1: a scan of different bytes is not evidence about these bytes", () => {
  // The scan is clean AND it is about another object. Coverage decides first, so
  // the refusal names the mismatch rather than the wrong object's clean result.
  const result = admit({ scan: scan({ scanned_content_digest: OTHER_CONTENT }) });
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "scan_covers_different_bytes");
  assert.equal(result.blocking_check, "scan_coverage");
  assert.equal(result.check_states.scan_coverage.candidate_content_digest, CONTENT);
  assert.equal(result.check_states.scan_coverage.scanned_content_digest, OTHER_CONTENT);
  assert.equal(result.check_states.phi_finding.state, "not_reached");

  // Even a scan of the wrong bytes that DID find PHI refuses on the mismatch:
  // the finding is not about this object either way.
  const wrongBytesWithPhi = admit({
    scan: scan({ scanned_content_digest: OTHER_CONTENT, findings: { phi: "found" } }),
  });
  assert.equal(wrongBytesWithPhi.reason_id, "scan_covers_different_bytes");
});

test("clause 1: a partial or aborted scan is unobservable, not a clean bill", () => {
  for (const completion of ["partial", "aborted"]) {
    const result = admit({ scan: scan({ completion }) });
    assert.equal(result.decision, "refuse", completion);
    assert.equal(result.reason_id, "scan_incomplete", completion);
    assert.equal(result.blocking_check, "scan_coverage", completion);
    assert.equal(result.check_states.scan_coverage.state, "unobservable", completion);
    assert.equal(result.check_states.scan_coverage.completion, completion);
  }
});

test("clause 1: a retaining scanner refuses, and an unstated retention blocks", () => {
  const retaining = admit({ scan: scan({ retention: "retaining" }) });
  assert.equal(retaining.decision, "refuse");
  assert.equal(retaining.reason_id, "scanner_retains_scanned_content");
  assert.equal(retaining.blocking_check, "scanner_retention");
  assert.equal(retaining.check_states.scanner_retention.state, "violated");
  // It refuses even though every finding is clean: the defect is the copy the
  // scanner made of content whose classification was unknown at the time.
  assert.equal(retaining.check_states.phi_finding.state, "not_reached");

  const unstated = admit({ scan: scan({ retention: "unstated" }) });
  assert.equal(unstated.reason_id, "scanner_retention_unstated");
  assert.equal(unstated.check_states.scanner_retention.state, "unobservable");
  assert.notEqual(unstated.reason_id, retaining.reason_id);
});

test("clause 1: PHI found refuses, and PHI not assessed blocks with its own reason", () => {
  const found = admit({ scan: scan({ findings: { phi: "found" } }) });
  assert.equal(found.decision, "refuse");
  assert.equal(found.reason_id, "phi_found_in_source");
  assert.equal(found.blocking_check, "phi_finding");
  assert.equal(found.check_states.phi_finding.state, "violated");

  const notAssessed = admit({ scan: scan({ findings: { phi: "not_assessed" } }) });
  assert.equal(notAssessed.decision, "refuse");
  assert.equal(notAssessed.reason_id, "phi_not_assessed");
  assert.equal(notAssessed.check_states.phi_finding.state, "unobservable");
  assert.notEqual(notAssessed.reason_id, found.reason_id);
});

test("clause 1: a credential found refuses, and not assessed blocks", () => {
  const found = admit({ scan: scan({ findings: { credential: "found" } }) });
  assert.equal(found.decision, "refuse");
  assert.equal(found.reason_id, "credential_found_in_source");
  assert.equal(found.blocking_check, "credential_finding");

  const notAssessed = admit({ scan: scan({ findings: { credential: "not_assessed" } }) });
  assert.equal(notAssessed.reason_id, "credential_not_assessed");
  assert.equal(notAssessed.blocking_check, "credential_finding");
  assert.equal(notAssessed.check_states.credential_finding.state, "unobservable");
});

test("clause 1: a nested archive refuses, and not assessed blocks", () => {
  const found = admit({ scan: scan({ findings: { nested_archive: "found" } }) });
  assert.equal(found.decision, "refuse");
  assert.equal(found.reason_id, "nested_archive_found_in_source");
  assert.equal(found.blocking_check, "nested_archive");
  // The reason says why a nested archive is refused rather than descended into.
  assert.match(found.check_states.nested_archive.reason, /never themselves scanned/);

  const notAssessed = admit({ scan: scan({ findings: { nested_archive: "not_assessed" } }) });
  assert.equal(notAssessed.reason_id, "nested_archive_not_assessed");
  assert.equal(notAssessed.check_states.nested_archive.state, "unobservable");
});

test("clause 1: UNCLASSIFIED opaque or encrypted content refuses; classified opaque content does not", () => {
  const unclassified = admit({
    scan: scan({ findings: { opaque_or_encrypted: "present_unclassified" } }),
  });
  assert.equal(unclassified.decision, "refuse");
  assert.equal(unclassified.reason_id, "unclassified_opaque_or_encrypted_content");
  assert.equal(unclassified.blocking_check, "opaque_or_encrypted");

  const notAssessed = admit({
    scan: scan({ findings: { opaque_or_encrypted: "not_assessed" } }),
  });
  assert.equal(notAssessed.reason_id, "opaque_or_encrypted_content_not_assessed");
  assert.equal(notAssessed.check_states.opaque_or_encrypted.state, "unobservable");

  // The distinction is the whole point of the four-state vocabulary: sealed
  // material whose classification IS known is admissible, so the check does not
  // refuse opacity as such.
  const classified = admit({
    scan: scan({ findings: { opaque_or_encrypted: "present_classified" } }),
  });
  assert.equal(classified.decision, "admit");
  assert.equal(classified.check_states.opaque_or_encrypted.state, "satisfied");
});

// ---------------------------------------------------------------------------
// The order is policy, not presentation.
// ---------------------------------------------------------------------------

test("the order is policy: the five source checks decide before any content fact is read", () => {
  // Structural: the named prefix really is the prefix of the check list.
  assert.deepEqual([...V5_CHECKS_BEFORE_ANY_CONTENT_FACT_IS_READ],
    V5_BACKUP_ADMISSION_CHECKS.slice(0, V5_CHECKS_BEFORE_ANY_CONTENT_FACT_IS_READ.length));

  // Behavioural: a request that violates a source check AND a scan check refuses
  // on the source one, with the scan check never reached.
  const both = admit({
    candidate: candidate({ source_id: OTHER_SOURCE_ID }),
    scan: scan({ findings: { phi: "found", credential: "found" } }),
  });
  assert.equal(both.reason_id, "source_not_enumerated");
  assert.equal(both.check_states.phi_finding.state, "not_reached");

  // And within the scan half, coverage and retention decide before findings.
  const retainingWithPhi = admit({
    scan: scan({ retention: "retaining", findings: { phi: "found" } }),
  });
  assert.equal(retainingWithPhi.reason_id, "scanner_retains_scanned_content");
});

// ---------------------------------------------------------------------------
// Catalog item 3 — the classify-or-purge terminal reconciliation.
// ---------------------------------------------------------------------------

const POLICY_DIGEST_FOR_FIXTURES = v5BackupQuarantinePolicyDigest();

function inventoryEntry(overrides = {}) {
  return {
    object_id: OBJECT_ID,
    content_digest: CONTENT,
    admitted_policy_digest: POLICY_DIGEST_FOR_FIXTURES,
    ...overrides,
  };
}

function receipt(overrides = {}) {
  const fields = {
    receipt_id: "terminal-receipt-0001",
    object_id: OBJECT_ID,
    outcome: "classified",
    content_digest: CONTENT,
    decided_by: "carr.quarantine-classifier",
    ...overrides,
  };
  return { ...fields, sealed_receipt_digest: quarantineTerminalReceiptDigest(fields) };
}

function reconcile(overrides = {}) {
  return reconcileQuarantineTerminalOutcomes({
    inventory: [inventoryEntry()],
    receipts: [receipt()],
    ...overrides,
  });
}

test("clause 3: exactly one sealed receipt per object reconciles complete", () => {
  const result = reconcile();
  assert.equal(result.decision, "complete");
  assert.equal(result.reason_id, "every_quarantine_object_reached_exactly_one_terminal_receipt");
  assert.deepEqual(result.exceptions, []);
  assert.deepEqual(result.exception_kinds_found, []);
  assert.equal(result.objects_examined, 1);
  assert.equal(result.receipts_read, 1);
  assert.equal(result.inventory_empty, false);
  assert.deepEqual(result.terminal_outcome_counts, { classified: 1, purged: 0 });
  assert.equal(result.issues_receipts, false);
  assert.equal(result.purges_anything, false);

  // A purge is the other exact outcome and reconciles the same way.
  const purged = reconcile({ receipts: [receipt({ outcome: "purged" })] });
  assert.equal(purged.decision, "complete");
  assert.deepEqual(purged.terminal_outcome_counts, { classified: 0, purged: 1 });
});

test("clause 3: an object with no terminal receipt is reported by name", () => {
  const result = reconcile({ receipts: [] });
  assert.equal(result.decision, "incomplete");
  assert.equal(result.reason_id, "quarantine_objects_unreconciled");
  assert.deepEqual(result.exception_kinds_found, ["object_without_terminal_receipt"]);
  assert.equal(result.exceptions.length, 1);
  assert.equal(result.exceptions[0].object_id, OBJECT_ID);
  assert.deepEqual(result.terminal_outcome_counts, { classified: 0, purged: 0 });
});

test("clause 3: two receipts for one object are an exception even when they agree", () => {
  // Same outcome, same bytes, two receipt ids. "Exact" means exactly one, and a
  // duplicated ending means the store recorded the object's ending twice.
  const agreeing = reconcile({
    receipts: [receipt(), receipt({ receipt_id: "terminal-receipt-0002" })],
  });
  assert.equal(agreeing.decision, "incomplete");
  assert.deepEqual(agreeing.exception_kinds_found, ["object_with_conflicting_terminal_receipts"]);
  assert.deepEqual(agreeing.exceptions[0].outcomes, ["classified"]);
  assert.deepEqual(agreeing.exceptions[0].receipt_ids,
    ["terminal-receipt-0001", "terminal-receipt-0002"]);

  // And the case the requirement is really guarding: purged and classified at once.
  const contradicting = reconcile({
    receipts: [receipt(), receipt({ receipt_id: "terminal-receipt-0002", outcome: "purged" })],
  });
  assert.deepEqual(contradicting.exceptions[0].outcomes, ["classified", "purged"]);
});

test("clause 3: a receipt whose seal has moved does not count toward its object's outcome", () => {
  // Sealed as a purge, then edited to read as a classification. If a moved seal
  // still counted, an edited receipt would satisfy the very requirement the seal
  // exists to prove — so the object must ALSO come back unreconciled.
  const sealedAsPurge = receipt({ outcome: "purged" });
  const edited = { ...sealedAsPurge, outcome: "classified" };
  const result = reconcile({ receipts: [edited] });
  assert.equal(result.decision, "incomplete");
  assert.deepEqual(result.exception_kinds_found,
    ["object_without_terminal_receipt", "receipt_seal_moved"]);
  const moved = result.exceptions.find(e => e.kind === "receipt_seal_moved");
  assert.equal(moved.sealed_receipt_digest, sealedAsPurge.sealed_receipt_digest);
  assert.notEqual(moved.recomputed_receipt_digest, sealedAsPurge.sealed_receipt_digest);
  assert.deepEqual(result.terminal_outcome_counts, { classified: 0, purged: 0 });
});

test("clause 3: a receipt for an object that was never admitted is an exception", () => {
  // The inventory's own object is properly receipted, so the ONLY defect is the
  // stray receipt — a purge nobody can point at an admitted object.
  const result = reconcile({
    receipts: [
      receipt(),
      receipt({ receipt_id: "terminal-receipt-0009", object_id: "quarantine-object-9999",
        outcome: "purged" }),
    ],
  });
  assert.equal(result.decision, "incomplete");
  assert.deepEqual(result.exception_kinds_found, ["receipt_for_object_not_in_quarantine_inventory"]);
  assert.equal(result.exceptions[0].object_id, "quarantine-object-9999");
  assert.equal(result.exceptions[0].outcome, "purged");
});

test("clause 3: a receipt whose content digest is not the inventory's is an exception", () => {
  const result = reconcile({ receipts: [receipt({ content_digest: OTHER_CONTENT })] });
  assert.equal(result.decision, "incomplete");
  assert.deepEqual(result.exception_kinds_found,
    ["receipt_content_digest_does_not_match_inventory"]);
  assert.equal(result.exceptions[0].inventory_content_digest, CONTENT);
  assert.equal(result.exceptions[0].receipt_content_digest, OTHER_CONTENT);
});

test("clause 3: an object admitted under a superseded policy cannot be reconciled here", () => {
  // Its receipt is perfect. The object was admitted under a policy this module
  // no longer implements, so this module is not the one that can close it.
  const result = reconcile({
    inventory: [inventoryEntry({ admitted_policy_digest: WRONG_POLICY_DIGEST })],
  });
  assert.equal(result.decision, "incomplete");
  assert.deepEqual(result.exception_kinds_found, ["object_admitted_under_a_superseded_policy"]);
  assert.equal(result.exceptions[0].admitted_policy_digest, WRONG_POLICY_DIGEST);
  assert.equal(result.exceptions[0].current_policy_digest, result.policy_digest);
});

test("clause 3: every exception is reported, not just the first, and they are sorted by object", () => {
  // Three objects, three different defects, deliberately supplied in an order
  // that is NOT the sorted order so the sort is proved rather than assumed.
  const result = reconcileQuarantineTerminalOutcomes({
    inventory: [
      inventoryEntry({ object_id: "object-c" }),
      inventoryEntry({ object_id: "object-a" }),
      inventoryEntry({ object_id: "object-b", admitted_policy_digest: WRONG_POLICY_DIGEST }),
    ],
    receipts: [
      receipt({ receipt_id: "r-b", object_id: "object-b" }),
      receipt({ receipt_id: "r-c1", object_id: "object-c" }),
      receipt({ receipt_id: "r-c2", object_id: "object-c", outcome: "purged" }),
    ],
  });
  assert.equal(result.decision, "incomplete");
  assert.equal(result.objects_examined, 3);
  assert.deepEqual(result.exceptions.map(e => [e.object_id, e.kind]), [
    ["object-a", "object_without_terminal_receipt"],
    ["object-b", "object_admitted_under_a_superseded_policy"],
    ["object-c", "object_with_conflicting_terminal_receipts"],
  ]);
  assert.deepEqual(result.exception_kinds_found, [
    "object_admitted_under_a_superseded_policy",
    "object_with_conflicting_terminal_receipts",
    "object_without_terminal_receipt",
  ]);
});

test("clause 3: an empty inventory reconciles vacuously and says so out loud", () => {
  const result = reconcileQuarantineTerminalOutcomes({ inventory: [], receipts: [] });
  assert.equal(result.decision, "complete");
  assert.equal(result.objects_examined, 0);
  // Without this field a consumer would read "complete" as evidence that
  // quarantine work was reconciled, when nothing was examined at all.
  assert.equal(result.inventory_empty, true);
});

test("clause 3: a repeated object or receipt id is unreadable rather than an exception", () => {
  assert.throws(() => reconcileQuarantineTerminalOutcomes({
    inventory: [inventoryEntry(), inventoryEntry()], receipts: [receipt()],
  }), boundaryError("duplicate_inventory_object"));
  assert.throws(() => reconcile({ receipts: [receipt(), receipt()] }),
    boundaryError("duplicate_receipt"));
  assert.throws(() => reconcile({ receipts: [receipt({ outcome: "pending" })] }),
    boundaryError("unknown_terminal_outcome"));
  assert.throws(() => reconcileQuarantineTerminalOutcomes({
    inventory: [inventoryEntry()], receipts: [receipt()], as_of: "2026-09-10T00:00:00Z",
  }), boundaryError("unknown_field"));
});

// ---------------------------------------------------------------------------
// The two seals, re-derived here rather than echoed back.
// ---------------------------------------------------------------------------

test("the roster-entry seal is taken over exactly these fields, in a stable order", () => {
  const fields = {
    source_id: SOURCE_ID,
    source_label: "CRM lease economics nightly export",
    phi_capability: "phi_incapable",
    declared_data_classes: ENUMERATED_CLASSES,
  };
  // Re-derived from the digest kernel with the preimage written out by hand, so
  // a field silently dropped from the seal fails here rather than passing.
  assert.equal(backupSourceRosterEntryDigest(fields), digest({
    schema_version: V5_BACKUP_QUARANTINE_SCHEMA_VERSION,
    entry_kind: V5_SOURCE_ROSTER_ENTRY_KIND,
    source_id: SOURCE_ID,
    source_label: "CRM lease economics nightly export",
    phi_capability: "phi_incapable",
    declared_data_classes: ["lease_economics", "property_attribute"],
  }));
  // The class list is sorted into the seal: the same enumeration written in two
  // orders is the same enumeration and must not move the seal.
  assert.equal(
    backupSourceRosterEntryDigest({ ...fields, declared_data_classes: ["property_attribute", "lease_economics"] }),
    backupSourceRosterEntryDigest(fields));
  // Every sealed field is load-bearing.
  for (const mutation of [
    { source_id: "other.source" },
    { source_label: "a different label" },
    { phi_capability: "phi_capable" },
    { declared_data_classes: ["lease_economics"] },
  ]) {
    assert.notEqual(backupSourceRosterEntryDigest({ ...fields, ...mutation }),
      backupSourceRosterEntryDigest(fields), JSON.stringify(mutation));
  }
});

test("the terminal-receipt seal is taken over exactly these fields", () => {
  const fields = {
    receipt_id: "terminal-receipt-0001",
    object_id: OBJECT_ID,
    outcome: "classified",
    content_digest: CONTENT,
    decided_by: "carr.quarantine-classifier",
  };
  assert.equal(quarantineTerminalReceiptDigest(fields), digest({
    schema_version: V5_BACKUP_QUARANTINE_SCHEMA_VERSION,
    receipt_kind: V5_QUARANTINE_TERMINAL_RECEIPT_KIND,
    receipt_id: "terminal-receipt-0001",
    object_id: OBJECT_ID,
    outcome: "classified",
    content_digest: CONTENT,
    decided_by: "carr.quarantine-classifier",
  }));
  for (const mutation of [
    { receipt_id: "terminal-receipt-0002" },
    { object_id: "quarantine-object-0002" },
    { outcome: "purged" },
    { content_digest: OTHER_CONTENT },
    { decided_by: "somebody-else" },
  ]) {
    assert.notEqual(quarantineTerminalReceiptDigest({ ...fields, ...mutation }),
      quarantineTerminalReceiptDigest(fields), JSON.stringify(mutation));
  }
});

// ---------------------------------------------------------------------------
// The closed policy, its digest, and the deferred clauses.
// ---------------------------------------------------------------------------

test("the policy preimage is closed and carries every vocabulary this module decides against", () => {
  const preimage = v5BackupQuarantinePolicyPreimage();
  assert.equal(preimage.schema_version, V5_BACKUP_QUARANTINE_SCHEMA_VERSION);
  assert.equal(preimage.policy_version, V5_BACKUP_QUARANTINE_POLICY_VERSION);
  assert.equal(preimage.tenant, ORGANIZATION_TENANT_ID);
  assert.deepEqual(preimage.checks_in_order, [...V5_BACKUP_ADMISSION_CHECKS]);
  assert.deepEqual(preimage.check_states, [...V5_ADMISSION_CHECK_STATES].sort());
  assert.deepEqual(preimage.source_phi_capabilities, [...V5_SOURCE_PHI_CAPABILITIES].sort());
  assert.deepEqual(preimage.scan_completion_states, [...V5_SCAN_COMPLETION_STATES].sort());
  assert.deepEqual(preimage.scanner_retention_states, [...V5_SCANNER_RETENTION_STATES].sort());
  assert.deepEqual(preimage.scan_finding_states, [...V5_SCAN_FINDING_STATES].sort());
  assert.deepEqual(preimage.scan_finding_keys, [...V5_SCAN_FINDING_KEYS].sort());
  assert.deepEqual(preimage.opaque_content_states, [...V5_OPAQUE_CONTENT_STATES].sort());
  assert.deepEqual(preimage.terminal_outcomes, [...V5_QUARANTINE_TERMINAL_OUTCOMES].sort());
  assert.deepEqual(preimage.reconciliation_exception_kinds,
    [...V5_RECONCILIATION_EXCEPTION_KINDS].sort());
  assert.deepEqual(preimage.reason_ids, [...V5_BACKUP_QUARANTINE_REASON_IDS].sort());
  assert.equal(preimage.admissible_source_phi_capability, V5_ADMISSIBLE_SOURCE_PHI_CAPABILITY);
  assert.equal(preimage.admissible_scanner_retention, V5_ADMISSIBLE_SCANNER_RETENTION);
  assert.equal(preimage.caller_may_select_checks, false);
  assert.equal(preimage.caller_may_assert_admissibility, false);
  assert.equal(preimage.unstated_observation_blocks, true);
  assert.equal(preimage.first_unsatisfied_check_decides, true);
  assert.equal(preimage.roster_is_an_input, true);
  assert.equal(preimage.ships_a_roster, false);
  assert.equal(preimage.runs_a_scanner, false);
  assert.equal(preimage.exactly_one_terminal_receipt_required, true);
  assert.equal(preimage.copies_source_bytes, false);
  assert.equal(preimage.grants_read_back, false);

  // The KEY SET is closed and written out by hand. A field that leaves, or
  // arrives, moves the policy digest without any assertion above objecting;
  // this list is what makes that fail here instead.
  assert.deepEqual(Object.keys(preimage).sort(), [
    "admissible_scanner_retention", "admissible_source_phi_capability", "blocking_decision_ids",
    "caller_may_assert_admissibility", "caller_may_select_checks", "check_states",
    "checks_before_any_content_fact_is_read", "checks_in_order", "copies_source_bytes",
    "decides_outbound_reconciliation", "decides_recovery_matrix", "decides_restore",
    "enumerated_source_roster_seam", "exactly_one_terminal_receipt_required",
    "first_unsatisfied_check_decides", "grants_read_back", "issues_receipts",
    "non_retaining_scanner_seam", "opaque_content_states", "outbound_reconciliation_clause",
    "outbound_reconciliation_seam", "policy_version", "privacy_authority",
    "prohibited_data_classes", "quarantine_direction", "reason_ids",
    "reconciliation_exception_kinds", "recovery_matrix_clause", "redecides_privacy_boundary",
    "registered_data_classes", "restore_and_recovery_matrix_seam", "restore_clause",
    "roster_entry_kind", "roster_is_an_input", "runs_a_scanner", "scan_completion_states",
    "scan_finding_keys", "scan_finding_states", "scan_report_is_an_input", "scanner_retention_states",
    "schema_version", "ships_a_roster", "source_phi_capabilities", "tenant",
    "terminal_outcomes", "terminal_receipt_kind", "unstated_observation_blocks",
  ]);

  // Every list in the preimage is SORTED, not merely alphabetical by luck.
  for (const key of ["check_states", "source_phi_capabilities", "scan_completion_states",
    "scanner_retention_states", "scan_finding_states", "scan_finding_keys", "opaque_content_states",
    "terminal_outcomes", "reconciliation_exception_kinds", "reason_ids", "prohibited_data_classes",
    "registered_data_classes", "blocking_decision_ids"]) {
    assert.deepEqual(preimage[key], [...preimage[key]].sort(), key);
  }
});

test("the policy digest is deterministic and taken over the exact canonical bytes", () => {
  // Re-hashed by hand rather than compared against a copy of the module's own
  // answer, so the digest is checked instead of merely echoed.
  const expected = "sha256:" + createHash("sha256")
    .update(v5BackupQuarantinePolicyCanonicalBytes()).digest("hex");
  assert.equal(v5BackupQuarantinePolicyDigest(), expected);
  // Nothing situational is bound: the digest does not move between requests.
  admit();
  admit({ candidate: candidate({ source_id: OTHER_SOURCE_ID }) });
  reconcile();
  assert.equal(v5BackupQuarantinePolicyDigest(), expected);
  // And both answers carry it, so a consumer can see which policy decided.
  assert.equal(admit().policy_digest, expected);
  assert.equal(reconcile().policy_digest, expected);
});

test("the deferred clauses are named with their missing facts, never guessed", () => {
  assert.equal(V5_RESTORE_AND_RECOVERY_MATRIX_SEAM,
    "step:v5-f08-restore-and-recovery-matrix-decision");
  assert.equal(V5_OUTBOUND_RECONCILIATION_SEAM,
    "step:v5-f08-outbound-queue-reconciliation-decision");
  assert.equal(V5_ENUMERATED_SOURCE_ROSTER_SEAM, "step:v5-f08-phi-incapable-source-enumeration");
  assert.equal(V5_NON_RETAINING_SCANNER_SEAM, "step:v5-f08-non-retaining-source-scanner");
  assert.deepEqual([...V5_BACKUP_BLOCKING_DECISION_IDS], ["Q034.D1"]);

  const preimage = v5BackupQuarantinePolicyPreimage();
  // Decided elsewhere, named here; this file still decides none of them.
  assert.equal(preimage.restore_clause, "decided_in:recovery-matrix.v5.js");
  assert.equal(preimage.recovery_matrix_clause, "decided_in:recovery-matrix.v5.js");
  assert.equal(preimage.outbound_reconciliation_clause, "decided_in:recovery-matrix.v5.js");
  assert.equal(preimage.decides_restore, false);
  assert.equal(preimage.decides_recovery_matrix, false);
  assert.equal(preimage.decides_outbound_reconciliation, false);

  // Every admission result says so, so no consumer can read an admit here as
  // deciding restore, the recovery matrix or an outbound release.
  const result = admit();
  assert.equal(result.restore_decided, false);
  assert.equal(result.recovery_matrix_decided, false);
  assert.equal(result.outbound_reconciliation_decided, false);

  // The header says it out loud and names the decision it is waiting on.
  const source = readFileSync(SRC_PATH, "utf8");
  const header = source.slice(0, source.indexOf("\nimport "));
  assert.ok(/WHAT IS DEFERRED/.test(header));
  assert.ok(header.includes("Q034.D1"));
  assert.ok(/no such scanner in this repository/.test(header));

  // No caller can smuggle a deferred question in as a request field.
  assert.throws(() => evaluateBackupSourceAdmission({
    actor: actor(), roster: roster(), candidate: candidate(), scan: scan(),
    restore: { verified: true },
  }), boundaryError("unknown_field"));
});

test("the projection reports the digest, names its missing facts and accepts nothing", () => {
  const projection = v5BackupQuarantineProjection();
  assert.equal(projection.slice, "V5-F08");
  assert.deepEqual(projection.requirement_ids, ["Q034"]);
  assert.equal(projection.policy_digest, v5BackupQuarantinePolicyDigest());
  assert.equal(projection.checkable_done_decided_here.length, 3);
  assert.equal(projection.accepts_anything, false);
  assert.deepEqual(projection.effects, V5_NO_EFFECTS);
  // The unbuilt halves are named with the fact each is missing, not with a
  // vague "future work".
  const unbuilt = projection.unimplemented_dependencies.join("\n");
  assert.match(unbuilt, /Q034\.D1 settled text/);
  assert.match(unbuilt, /no scanner exists in this repository/);
  assert.match(unbuilt, /independent_restore_oracle/);
  assert.match(unbuilt, /business-day calendar/);

  // A stale expected digest is refused rather than answered.
  assert.equal(v5BackupQuarantineProjection({
    expected_policy_digest: v5BackupQuarantinePolicyDigest(),
  }).slice, "V5-F08");
  assert.throws(() => v5BackupQuarantineProjection({ expected_policy_digest: WRONG_POLICY_DIGEST }),
    boundaryError("stale_expected_digest"));
  assert.throws(() => v5BackupQuarantineProjection({ expected_policy_digest: "deadbeef" }),
    boundaryError("invalid_expected_digest"));
});

// ---------------------------------------------------------------------------
// The two kinds of no, freezing, and the effect contract.
// ---------------------------------------------------------------------------

test("two kinds of no: policy answers are returned and contract violations throw", () => {
  // Returned: an honest "I did not look" is an answer with a reason id.
  assert.equal(admit({ scan: scan({ findings: { credential: "not_assessed" } }) }).decision, "refuse");

  // Thrown: an unreadable request is not a policy question.
  assert.throws(() => normalizeBackupSourceRoster({
    roster_digest: ROSTER_DIGEST, enumeration_authority: AUTHORITY,
    entries: { [SOURCE_ID]: { ...rosterEntry(), phi_capability: "probably_fine" } },
  }), boundaryError("unknown_source_phi_capability"));
  assert.throws(() => normalizeBackupSourceRoster({
    roster_digest: ROSTER_DIGEST, enumeration_authority: AUTHORITY,
    entries: { [SOURCE_ID]: { ...rosterEntry(), declared_data_classes: ["invented_class"] } },
  }), boundaryError("unknown_data_class"));
  assert.throws(() => normalizeBackupSourceRoster({
    roster_digest: ROSTER_DIGEST, enumeration_authority: AUTHORITY,
    entries: { [SOURCE_ID]: { ...rosterEntry(), declared_data_classes: [] } },
  }), boundaryError("missing_field"));
  assert.throws(() => normalizeBackupSourceRoster({
    roster_digest: ROSTER_DIGEST, enumeration_authority: AUTHORITY,
    entries: { [SOURCE_ID]: { ...rosterEntry(), sealed_entry_digest: "deadbeef" } },
  }), boundaryError("invalid_digest"));
  assert.throws(() => normalizeBackupSourceScanReport({
    scanner_id: "s", scanned_content_digest: CONTENT, completion: "mostly",
    retention: "non_retaining",
    findings: { phi: "none_found", credential: "none_found", nested_archive: "none_found",
      opaque_or_encrypted: "absent" },
  }), boundaryError("unknown_scan_completion_state"));
  assert.throws(() => normalizeBackupSourceScanReport({
    scanner_id: "s", scanned_content_digest: CONTENT, completion: "complete",
    retention: "non_retaining",
    findings: { phi: "probably_none", credential: "none_found", nested_archive: "none_found",
      opaque_or_encrypted: "absent" },
  }), boundaryError("unknown_scan_finding_state"));
  // A finding that is simply omitted is unreadable, not clean.
  assert.throws(() => normalizeBackupSourceScanReport({
    scanner_id: "s", scanned_content_digest: CONTENT, completion: "complete",
    retention: "non_retaining",
    findings: { phi: "none_found", credential: "none_found", nested_archive: "none_found" },
  }), boundaryError("missing_field"));
  // The caller cannot narrow the test or assert its own admissibility.
  assert.throws(() => admit({ candidate: { ...candidate(), phi_free: true } }),
    boundaryError("unknown_field"));
  assert.throws(() => evaluateBackupSourceAdmission({
    actor: { slug: HOLDER, authority: "root" }, roster: roster(), candidate: candidate(),
    scan: scan(),
  }), boundaryError("unknown_field"));
  // A hand-built report that is incoherent is unreadable rather than refusable.
  assert.throws(() => admit({ roster: { ...roster(), report_kind: "not_a_roster" } }),
    boundaryError("unnormalized_report"));
  assert.throws(() => admit({ scan: { ...scan(), report_kind: "not_a_scan" } }),
    boundaryError("unnormalized_report"));
});

test("normalized reports and results are frozen, and reading one rewrites nothing", () => {
  const result = admit();
  assert.ok(Object.isFrozen(result));
  assert.ok(Object.isFrozen(result.check_states.phi_finding));
  assert.ok(Object.isFrozen(roster().entries[SOURCE_ID]));
  assert.ok(Object.isFrozen(scan().findings));
  assert.ok(Object.isFrozen(reconcile()));
  assert.throws(() => { result.decision = "admit"; }, TypeError);
  assert.throws(() => { result.checks_required.push("nothing"); }, TypeError);
  assert.equal(admit().decision, "admit");
});

test("the module copies nothing and reads no clock, filesystem, network or environment", () => {
  const source = readFileSync(SRC_PATH, "utf8");
  for (const pattern of [
    /\bnode:fs\b/, /\bnode:net\b/, /\bnode:http\b/, /\bnode:https\b/, /\bnode:child_process\b/,
    /\bnode:worker_threads\b/, /\bspawn\w*\s*\(/, /\bexec\w*\s*\(/, /\bfetch\s*\(/,
    /\bprocess\.env\b/, /\bnew Date\b/, /\bDate\.now\b/, /\bsetTimeout\b/, /\bsetInterval\b/,
    /\brequire\s*\(/, /\bimport\s*\(/, /\bglobalThis\b/,
  ]) {
    assert.ok(!pattern.test(source), `module source must not contain ${pattern}`);
  }
  const imports = [...source.matchAll(/^import\s[^;]*?from\s+"([^"]+)";/gm)].map(match => match[1]).sort();
  assert.deepEqual(imports,
    ["./artifact-trust.js", "./global-boundaries.v5.js", "./identity.js"],
    "the evaluator reuses the existing kernels and keeps no second privacy or actor registry");
});
