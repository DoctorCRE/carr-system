// DoctorCRE v5 slice V5-F08 — the typed ONE-WAY QUARANTINE ADMISSION contract
// and the CLASSIFY-OR-PURGE terminal reconciliation (requirement Q034).
//
// This closes catalog `checkable_done` items 1, 2 and 3 — "scanner refuses PHI,
// credentials, nested archives and unclassified opaque/encrypted content",
// "only enumerated PHI-incapable source enters one-way quarantine", and "every
// quarantine object reaches exact classify-or-purge receipt" — as pure
// evaluators over typed observations. Items 4, 5 and 6 are NOT decided here and
// the reason is written down rather than implied; see WHAT IS DEFERRED below.
//
// WHAT THIS FILE IS NOT, said first because "backup" invites the wrong reading.
// It is not a backup workflow, not a scanner, not a quarantine store and not a
// restore controller. It copies nothing, reads no source bytes, opens no file,
// runs no scanner, keeps no inventory and issues no receipt. Every fact it
// decides on is a TYPED OBSERVATION THE CALLER SUPPLIES. An `admit` from here
// says the eleven registered negatives did not fire on the facts as reported;
// it moves no object anywhere, and every result says so in its own fields.
//
// THE SCANNER IS THE THING THAT PRODUCES THE OBSERVATION; THIS MODULE DECIDES
// ON IT. The catalog's concrete output says to "reuse the existing non-retaining
// source scanner where valid" — and there is no such scanner in this repository
// (see V5_NON_RETAINING_SCANNER_SEAM). Writing one here would be worse than
// shipping the gap twice over: a byte-level PHI classifier invented in this file
// would be a SECOND privacy authority beside global-boundaries.v5.js, and the
// slice's own model_judgment_boundary reserves pass/fail to deterministic checks
// and an independent oracle rather than to a classifier's opinion. So the scan
// arrives as a REPORT, its findings are read rather than derived, and a report
// that does not say is treated as a block rather than as a clean bill.
//
// TWO KINDS OF NO, inherited unchanged from global-boundaries.v5.js:
//   * A POLICY ANSWER is RETURNED — a frozen result whose `decision` is "admit"
//     or "refuse" with a stable `reason_id`. A refusal is an answer the caller
//     may record. "I cannot say" is one of these: a scan that did not assess
//     credentials is a thing a caller is allowed to report, and it BLOCKS.
//     Silence is never an admission.
//   * A CONTRACT VIOLATION THROWS V5BoundaryError. Unknown fields, unknown
//     states, open schemas and malformed digests are not policy questions; the
//     module cannot read the request at all, so it fails closed rather than
//     guessing which negative was meant.
//
// NO CALLER MAY NAME WHICH CHECKS APPLY, OR ASSERT ITS OWN ADMISSIBILITY.
// `trusted`, `skip_scan`, `already_reviewed` and `phi_free` are unknown fields,
// so a request that tries to narrow the test cannot be read at all. The check
// list is a module constant, in a fixed order, and the order is load-bearing:
// the first check that does not pass refuses, and everything after it reports
// `not_reached`. THE FIVE SOURCE CHECKS COME FIRST, so an unenumerated source is
// refused BEFORE any content fact about it is read at all. That is what makes
// "only enumerated PHI-incapable source enters quarantine" a structural property
// of this file rather than a promise about it: a source nobody put on the list
// cannot be rescued by a clean scan, because its scan is never reached.
//
// THE ROSTER IS AN INPUT AND NO ROSTER SHIPS HERE. Which sources are enumerated
// PHI-incapable is an assessment a human performs about real systems; this
// repository holds no such enumeration and inventing one would ship an invented
// binding. So the roster arrives the way the supervisor registry arrives in
// command-supervisor-admission.v5.js: caller-supplied, SEALED, and with each
// entry's own digest RECOMPUTED here, so an entry re-pointed after signing
// refuses. A caller can therefore only widen the roster by re-sealing it, which
// is a visible act rather than a field on a request.
//
// PHI IS NOT RE-DECIDED HERE. `declared_data_class_boundary` calls S01's
// evaluatePrivacyBoundary and reads its answer. A second implementation of the
// privacy boundary would be a second place for it to disagree, and the class
// vocabulary, the prohibited set and the aggregate route already live there.
// This module adds no data class and holds no second privacy registry.
//
// WHAT IS DEFERRED, AND WHY IT IS NOT GUESSED HERE. Catalog items 4, 5 and 6 —
// restore from an independently controlled copy with an exact watermark/hash,
// the RPO/RTO cells passing independently, and outbound queues quarantining
// until reconciliation — are named seams below and decided by nothing in this
// file. Every one of them is blocked on a fact this repository does not hold:
// Q034.D1's settled text is doctrine-store text and is absent from
// V5_SETTLED_DECISIONS, `independent_restore_oracle` is a registered producer
// role in benchmark-minimum.v5.js with no gate member naming its step, and the
// business-day calendar the "<= 1 business day" adapter cell would be measured
// against is defined nowhere here. The seams are hashed into the policy
// preimage, so the day any of them is written the policy digest moves and stale
// readers are refused rather than silently reading a policy that now decides
// more than it did.
//
// UPDATE: items 4, 5 and 6 are now decided in recovery-matrix.v5.js, against the
// catalog's stated floor, with exact restore evidence produced by
// bin/restore-rehearse.sh phase 5. This file still decides none of them itself;
// its three clauses now name where they are decided, which moved this digest.

import { canonicalJson, digest } from "./artifact-trust.js";
import {
  V5BoundaryError,
  V5_NO_EFFECTS,
  V5_DATA_CLASSES,
  V5_PROHIBITED_DATA_CLASSES,
  evaluatePrivacyBoundary,
} from "./global-boundaries.v5.js";
import { ORGANIZATION_TENANT_ID, authorizationClassForActor } from "./identity.js";

export const V5_BACKUP_QUARANTINE_SCHEMA_VERSION = "doctorcre-v5-backup-quarantine.v1";
export const V5_BACKUP_QUARANTINE_POLICY_VERSION = 1;

/** The sealed shape whose digest a roster entry's own seal must reproduce. */
export const V5_SOURCE_ROSTER_ENTRY_KIND = "backup-source-roster-entry.v1";

/** The sealed shape whose digest a terminal receipt's own seal must reproduce. */
export const V5_QUARANTINE_TERMINAL_RECEIPT_KIND = "quarantine-terminal-receipt.v1";

/**
 * The quarantine's topology, stated as a constant so it is hashed rather than
 * asserted in prose. Ingress only: nothing in this module returns a path out of
 * quarantine, and every admission result carries `grants_read_back: false`.
 */
export const V5_QUARANTINE_DIRECTION = "one_way_ingress_only";

// --- the deferred seams, each blocked on a fact this repository does not hold -

/** Catalog items 4 and 5. Restore verification and the RPO/RTO cells. */
export const V5_RESTORE_AND_RECOVERY_MATRIX_SEAM =
  "step:v5-f08-restore-and-recovery-matrix-decision";

/** Catalog item 6. Outbound-queue release against external-effect reconciliation. */
export const V5_OUTBOUND_RECONCILIATION_SEAM =
  "step:v5-f08-outbound-queue-reconciliation-decision";

/** The enumeration itself. A human act about real systems; no roster ships here. */
export const V5_ENUMERATED_SOURCE_ROSTER_SEAM =
  "step:v5-f08-phi-incapable-source-enumeration";

/** The scanner whose report this module reads. It does not exist in this tree. */
export const V5_NON_RETAINING_SCANNER_SEAM =
  "step:v5-f08-non-retaining-source-scanner";

/** The decision whose settled text would have to be read before the deferred seams are written. */
export const V5_BACKUP_BLOCKING_DECISION_IDS = Object.freeze(["Q034.D1"]);

/**
 * The registered negatives, IN THE ORDER THEY ARE APPLIED. The order is policy,
 * not presentation: the five source checks come first so that a source nobody
 * enumerated, a roster entry that moved after signing, a source assessed as
 * PHI-capable, an object declaring a class its source never enumerated, and a
 * class the privacy boundary refuses all refuse BEFORE the scan is read at all.
 */
export const V5_BACKUP_ADMISSION_CHECKS = Object.freeze([
  "source_enumerated",
  "roster_entry_integrity",
  "source_phi_capability",
  "declared_class_within_enumerated_source",
  "declared_data_class_boundary",
  "scan_coverage",
  "scanner_retention",
  "phi_finding",
  "credential_finding",
  "nested_archive",
  "opaque_or_encrypted",
]);

/** The five checks decided before any content fact about the object is read. */
export const V5_CHECKS_BEFORE_ANY_CONTENT_FACT_IS_READ = Object.freeze([
  "source_enumerated",
  "roster_entry_integrity",
  "source_phi_capability",
  "declared_class_within_enumerated_source",
  "declared_data_class_boundary",
]);

/**
 * STATE VOCABULARY ONE — what one check can say.
 *
 * `unobservable` is a first-class outcome and it BLOCKS. A backup admission that
 * treats "the scan did not assess that" as "nothing was found" has replaced a
 * check with an assumption, and the thing it would be assuming about is PHI.
 */
export const V5_ADMISSION_CHECK_STATES = Object.freeze([
  "satisfied", "violated", "unobservable", "not_reached",
]);

/**
 * STATE VOCABULARY TWO — what an enumeration says about one source.
 *
 * `unassessed` is not padding and it is not the same as `phi_capable`: a source
 * nobody has assessed and a source assessed as able to carry PHI are different
 * facts about the world, and collapsing them would hide that the enumeration
 * work was never done. Both refuse, with their own reason ids.
 */
export const V5_SOURCE_PHI_CAPABILITIES = Object.freeze([
  "phi_incapable", "phi_capable", "unassessed",
]);

export const V5_ADMISSIBLE_SOURCE_PHI_CAPABILITY = "phi_incapable";

/** How far the scan got. Only a complete scan is evidence about a whole object. */
export const V5_SCAN_COMPLETION_STATES = Object.freeze(["complete", "partial", "aborted"]);

/**
 * STATE VOCABULARY THREE — whether the scanner kept what it read.
 *
 * The catalog's concrete output names a NON-RETAINING scanner specifically. A
 * retaining scanner that reports "no PHI found" has still made a copy of content
 * whose classification was unknown when it made it, which is the copy this
 * slice's excluded scope forbids. So retention is its own check with its own
 * refusal, and `unstated` blocks: a scanner that will not say whether it kept
 * the bytes has not been shown to be the non-retaining one.
 */
export const V5_SCANNER_RETENTION_STATES = Object.freeze([
  "non_retaining", "retaining", "unstated",
]);

export const V5_ADMISSIBLE_SCANNER_RETENTION = "non_retaining";

/** What a scan can say about one looked-for thing. */
export const V5_SCAN_FINDING_STATES = Object.freeze(["none_found", "found", "not_assessed"]);

/** The three findings that use the plain vocabulary, in the order they are read. */
export const V5_SCAN_FINDING_KEYS = Object.freeze(["phi", "credential", "nested_archive"]);

/**
 * STATE VOCABULARY FOUR — opaque or encrypted content, which needs four states
 * rather than three.
 *
 * The catalog refuses "unclassified opaque/encrypted content", not opaque
 * content as such: an encrypted blob whose classification IS known is a
 * different fact from one whose classification is not, and lumping them together
 * would refuse legitimate sealed material while teaching people to route around
 * the check. `present_classified` is the only present-state that clears.
 */
export const V5_OPAQUE_CONTENT_STATES = Object.freeze([
  "absent", "present_classified", "present_unclassified", "not_assessed",
]);

/** The exact terminal outcomes. There is no third, and there is no "pending". */
export const V5_QUARANTINE_TERMINAL_OUTCOMES = Object.freeze(["classified", "purged"]);

/**
 * The reconciliation's exception vocabulary. Every way an inventory and a
 * receipt set can fail to be exactly paired, enumerated so a reconciliation
 * result is machine-readable rather than a sentence.
 */
export const V5_RECONCILIATION_EXCEPTION_KINDS = Object.freeze([
  "object_admitted_under_a_superseded_policy",
  "object_with_conflicting_terminal_receipts",
  "object_without_terminal_receipt",
  "receipt_content_digest_does_not_match_inventory",
  "receipt_for_object_not_in_quarantine_inventory",
  "receipt_seal_moved",
]);

export const V5_BACKUP_QUARANTINE_REASON_IDS = Object.freeze([
  "admitted_to_one_way_quarantine_after_all_negatives_cleared",
  "credential_found_in_source",
  "credential_not_assessed",
  "declared_class_outside_enumerated_source",
  "declared_data_class_refused_by_privacy_boundary",
  "declared_data_class_requires_independent_privacy_route",
  "every_quarantine_object_reached_exactly_one_terminal_receipt",
  "nested_archive_found_in_source",
  "nested_archive_not_assessed",
  "opaque_or_encrypted_content_not_assessed",
  "phi_found_in_source",
  "phi_not_assessed",
  "quarantine_objects_unreconciled",
  "roster_entry_digest_moved",
  "scan_covers_different_bytes",
  "scan_incomplete",
  "scanner_retains_scanned_content",
  "scanner_retention_unstated",
  "source_not_enumerated",
  "source_not_phi_incapable",
  "source_phi_capability_unassessed",
  "unclassified_opaque_or_encrypted_content",
]);

// A `sha256:`-prefixed 64-hex digest. Spellings are NOT normalized into one
// another: comparison is byte equality, so an observed digest must be written
// exactly as the roster or the inventory wrote it.
const SHA256_REF = /^sha256:[0-9a-f]{64}$/;

// A stable identifier: no whitespace, no separators that would let one id read
// as two. Same posture as command-supervisor-admission.v5.js's STABLE_ID.
const STABLE_ID = /^[A-Za-z0-9][A-Za-z0-9._:+-]{0,255}$/;

const ROSTER_KEYS = Object.freeze(["entries", "enumeration_authority", "roster_digest"]);
const ROSTER_ENTRY_KEYS = Object.freeze([
  "declared_data_classes", "phi_capability", "sealed_entry_digest", "source_label",
]);
const SCAN_KEYS = Object.freeze([
  "completion", "findings", "retention", "scanned_content_digest", "scanner_id", "scanner_version",
]);
const FINDINGS_KEYS = Object.freeze(["credential", "nested_archive", "opaque_or_encrypted", "phi"]);
const REQUEST_KEYS = Object.freeze(["actor", "candidate", "roster", "scan"]);
const ACTOR_KEYS = Object.freeze(["human", "probe", "review", "slug", "sponsoring_human_slug"]);
const CANDIDATE_KEYS = Object.freeze([
  "content_digest", "declared_data_classes", "object_id", "source_id",
]);

const RECONCILE_KEYS = Object.freeze(["inventory", "receipts"]);
const INVENTORY_ENTRY_KEYS = Object.freeze([
  "admitted_policy_digest", "content_digest", "object_id",
]);
const RECEIPT_KEYS = Object.freeze([
  "content_digest", "decided_by", "object_id", "outcome", "receipt_id", "sealed_receipt_digest",
]);

// The normalized shapes, re-checked at the evaluator door.
const NORMALIZED_ROSTER_KEYS = Object.freeze([
  "entries", "enumeration_authority", "report_kind", "roster_digest", "schema_version",
]);
const NORMALIZED_ROSTER_ENTRY_KEYS = Object.freeze([
  "declared_data_classes", "phi_capability", "sealed_entry_digest", "source_id", "source_label",
]);
const NORMALIZED_SCAN_KEYS = Object.freeze([
  "completion", "findings", "report_kind", "retention", "scanned_content_digest",
  "scanner_id", "scanner_version", "schema_version",
]);

function fail(code, message, detail) {
  throw new V5BoundaryError(code, message, detail);
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

function assertArray(value, path) {
  if (!Array.isArray(value)) fail("invalid_shape", `${path} must be an array`, { path });
  return value;
}

/** An open schema is an unenforced one; an unread field could be a smuggled control. */
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

function assertNonEmptyString(value, path) {
  if (typeof value !== "string" || value.length === 0) {
    fail("invalid_shape", `${path} must be a non-empty string`, { path });
  }
  return value;
}

function assertStableId(value, path) {
  assertNonEmptyString(value, path);
  if (!STABLE_ID.test(value)) {
    fail("invalid_identifier", `${path} must be a stable identifier`, { path });
  }
  return value;
}

function assertDigestRef(value, path) {
  if (typeof value !== "string" || !SHA256_REF.test(value)) {
    fail("invalid_digest", `${path} must be a "sha256:" reference to a 64-character lower-case digest`, { path });
  }
  return value;
}

function optionalString(value, path) {
  return value === undefined || value === null ? null : assertNonEmptyString(value, path);
}

function assertEnum(value, allowed, path, code) {
  if (typeof value !== "string" || !allowed.includes(value)) {
    fail(code, `${path} must be one of the registered values`, { path, registered: [...allowed] });
  }
  return value;
}

/**
 * A non-empty, duplicate-free, SORTED list of registered v5 data classes.
 *
 * Sorting here rather than at the comparison site is what makes the roster seal
 * stable: the same enumeration written in two orders is the same enumeration,
 * and a seal that moved because somebody reordered a list would train people to
 * re-seal without reading.
 */
function assertDataClassList(value, path) {
  assertArray(value, path);
  if (value.length === 0) {
    fail("missing_field", `${path} must name at least one registered v5 data class`, { path });
  }
  const seen = new Set();
  for (const [index, entry] of value.entries()) {
    const at = `${path}[${index}]`;
    assertEnum(entry, V5_DATA_CLASSES, at, "unknown_data_class");
    if (seen.has(entry)) fail("duplicate_data_class", `${at} repeats "${entry}"`, { path: at, data_class: entry });
    seen.add(entry);
  }
  return [...value].sort();
}

// ---------------------------------------------------------------------------
// The enumerated source roster.
//
// Each entry says which source was assessed, what that assessment concluded
// about its ability to carry PHI, which data classes it was enumerated to hold,
// and what the entry's own sealed digest was at signing. The seal is RECOMPUTED
// here — that is the "an entry re-pointed after signing refuses" half of the
// enumeration negative, and it is why the digest kernel is reused rather than a
// second canonicalizer written.
//
// NOTHING IN THIS FILE CREATES A ROSTER ENTRY. The enumeration is a human act
// about real systems; see V5_ENUMERATED_SOURCE_ROSTER_SEAM.
// ---------------------------------------------------------------------------

/**
 * The digest a well-formed roster entry's seal must equal. Exported so a caller
 * — or a reviewer — can compute the seal by hand rather than trusting this
 * file's own answer about its own rule.
 */
export function backupSourceRosterEntryDigest(entry) {
  assertObject(entry, "entry");
  assertClosedKeys(entry, ["declared_data_classes", "phi_capability", "source_id", "source_label"], "entry");
  assertRequiredKeys(entry, ["declared_data_classes", "phi_capability", "source_id"], "entry");
  return digest({
    schema_version: V5_BACKUP_QUARANTINE_SCHEMA_VERSION,
    entry_kind: V5_SOURCE_ROSTER_ENTRY_KIND,
    source_id: assertStableId(entry.source_id, "entry.source_id"),
    source_label: optionalString(entry.source_label, "entry.source_label"),
    phi_capability: assertEnum(entry.phi_capability, V5_SOURCE_PHI_CAPABILITIES,
      "entry.phi_capability", "unknown_source_phi_capability"),
    declared_data_classes: assertDataClassList(entry.declared_data_classes, "entry.declared_data_classes"),
  });
}

export function normalizeBackupSourceRoster(roster) {
  assertObject(roster, "roster");
  assertClosedKeys(roster, ROSTER_KEYS, "roster");
  assertRequiredKeys(roster, ["entries", "enumeration_authority", "roster_digest"], "roster");
  const entriesInput = assertObject(roster.entries, "roster.entries");
  const entries = {};
  for (const sourceId of Object.keys(entriesInput)) {
    const at = `roster.entries.${sourceId}`;
    assertStableId(sourceId, at);
    const entry = assertObject(entriesInput[sourceId], at);
    assertClosedKeys(entry, ROSTER_ENTRY_KEYS, at);
    assertRequiredKeys(entry, ["declared_data_classes", "phi_capability", "sealed_entry_digest"], at);
    entries[sourceId] = {
      source_id: sourceId,
      source_label: optionalString(entry.source_label, `${at}.source_label`),
      phi_capability: assertEnum(entry.phi_capability, V5_SOURCE_PHI_CAPABILITIES,
        `${at}.phi_capability`, "unknown_source_phi_capability"),
      declared_data_classes: assertDataClassList(entry.declared_data_classes, `${at}.declared_data_classes`),
      sealed_entry_digest: assertDigestRef(entry.sealed_entry_digest, `${at}.sealed_entry_digest`),
    };
  }
  return deepFreeze({
    report_kind: "backup_source_roster",
    schema_version: V5_BACKUP_QUARANTINE_SCHEMA_VERSION,
    roster_digest: assertDigestRef(roster.roster_digest, "roster.roster_digest"),
    enumeration_authority: assertStableId(roster.enumeration_authority, "roster.enumeration_authority"),
    entries,
  });
}

// ---------------------------------------------------------------------------
// The pre-copy scan report.
//
// Every field here is something a scanner WOULD observe and this module does
// not. The four findings are REQUIRED to be present as states — a report that
// simply omits `credential` is unreadable rather than clean — but each of them
// has a `not_assessed` state that a caller may honestly report, and every one of
// those BLOCKS. That is the deliberate shape: making the findings optional would
// turn an honest "I did not look" into a silent pass, and forbidding
// `not_assessed` would turn it into an unreadable request, which pushes callers
// toward reporting `none_found` for something they never looked at.
// ---------------------------------------------------------------------------

export function normalizeBackupSourceScanReport(scan) {
  assertObject(scan, "scan");
  assertClosedKeys(scan, SCAN_KEYS, "scan");
  assertRequiredKeys(scan,
    ["completion", "findings", "retention", "scanned_content_digest", "scanner_id"], "scan");
  const findingsInput = assertObject(scan.findings, "scan.findings");
  assertClosedKeys(findingsInput, FINDINGS_KEYS, "scan.findings");
  assertRequiredKeys(findingsInput, FINDINGS_KEYS, "scan.findings");
  const findings = {};
  for (const key of V5_SCAN_FINDING_KEYS) {
    findings[key] = assertEnum(findingsInput[key], V5_SCAN_FINDING_STATES,
      `scan.findings.${key}`, "unknown_scan_finding_state");
  }
  findings.opaque_or_encrypted = assertEnum(findingsInput.opaque_or_encrypted,
    V5_OPAQUE_CONTENT_STATES, "scan.findings.opaque_or_encrypted", "unknown_opaque_content_state");
  return deepFreeze({
    report_kind: "backup_source_scan",
    schema_version: V5_BACKUP_QUARANTINE_SCHEMA_VERSION,
    scanner_id: assertStableId(scan.scanner_id, "scan.scanner_id"),
    scanner_version: optionalString(scan.scanner_version, "scan.scanner_version"),
    scanned_content_digest: assertDigestRef(scan.scanned_content_digest, "scan.scanned_content_digest"),
    completion: assertEnum(scan.completion, V5_SCAN_COMPLETION_STATES,
      "scan.completion", "unknown_scan_completion_state"),
    retention: assertEnum(scan.retention, V5_SCANNER_RETENTION_STATES,
      "scan.retention", "unknown_scanner_retention_state"),
    findings,
  });
}

// ---------------------------------------------------------------------------
// Revalidation at the door.
//
// A NORMALIZED REPORT IS REVALIDATED IN FULL, NOT RECOGNIZED BY ITS MARKER. The
// `report_kind` marker says which normalizer a report CLAIMS to come from; it
// proves nothing, because anything can be written by hand or round-tripped
// through JSON and edited. A correctly-shaped hand-built report is accepted by
// design — that is what "revalidated in full" means — and an incoherent one is
// unreadable rather than refusable.
// ---------------------------------------------------------------------------

function badReport(path, message) {
  fail("unnormalized_report", message, { path });
}

function assertReportShape(value, keys, path) {
  if (!isPlainObject(value)) badReport(path, `${path} must be a plain object`);
  for (const key of Object.keys(value)) {
    if (!keys.includes(key)) badReport(`${path}.${key}`, `unknown field "${key}" at ${path}`);
  }
  for (const key of keys) {
    if (!(key in value)) badReport(`${path}.${key}`, `${path}.${key} is missing from the normalized report`);
  }
  return value;
}

function reportString(value, path, { nullable = false } = {}) {
  if (value === null && nullable) return null;
  if (typeof value !== "string" || value.length === 0) badReport(path, `${path} must be a non-empty string`);
  return value;
}

function reportDigest(value, path) {
  if (typeof value !== "string" || !SHA256_REF.test(value)) {
    badReport(path, `${path} must be a readable digest`);
  }
  return value;
}

function reportEnum(value, allowed, path) {
  if (typeof value !== "string" || !allowed.includes(value)) {
    badReport(path, `${path} is not one of the registered values`);
  }
  return value;
}

function assertNormalizedRoster(value, path) {
  assertReportShape(value, NORMALIZED_ROSTER_KEYS, path);
  if (value.report_kind !== "backup_source_roster" ||
      value.schema_version !== V5_BACKUP_QUARANTINE_SCHEMA_VERSION) {
    badReport(path, `${path} must be produced by normalizeBackupSourceRoster`);
  }
  reportDigest(value.roster_digest, `${path}.roster_digest`);
  reportString(value.enumeration_authority, `${path}.enumeration_authority`);
  if (!isPlainObject(value.entries)) badReport(`${path}.entries`, `${path}.entries must be a plain object`);
  for (const sourceId of Object.keys(value.entries)) {
    const at = `${path}.entries.${sourceId}`;
    const entry = assertReportShape(value.entries[sourceId], NORMALIZED_ROSTER_ENTRY_KEYS, at);
    if (entry.source_id !== sourceId) {
      badReport(`${at}.source_id`, `${at}.source_id does not match the key it is filed under`);
    }
    if (!STABLE_ID.test(sourceId)) badReport(at, `${at} is not filed under a stable identifier`);
    reportEnum(entry.phi_capability, V5_SOURCE_PHI_CAPABILITIES, `${at}.phi_capability`);
    reportDigest(entry.sealed_entry_digest, `${at}.sealed_entry_digest`);
    reportString(entry.source_label, `${at}.source_label`, { nullable: true });
    if (!Array.isArray(entry.declared_data_classes) || entry.declared_data_classes.length === 0) {
      badReport(`${at}.declared_data_classes`, `${at}.declared_data_classes must be a non-empty array`);
    }
    for (const [index, dataClass] of entry.declared_data_classes.entries()) {
      reportEnum(dataClass, V5_DATA_CLASSES, `${at}.declared_data_classes[${index}]`);
    }
  }
  return value;
}

function assertNormalizedScan(value, path) {
  assertReportShape(value, NORMALIZED_SCAN_KEYS, path);
  if (value.report_kind !== "backup_source_scan" ||
      value.schema_version !== V5_BACKUP_QUARANTINE_SCHEMA_VERSION) {
    badReport(path, `${path} must be produced by normalizeBackupSourceScanReport`);
  }
  reportString(value.scanner_id, `${path}.scanner_id`);
  reportString(value.scanner_version, `${path}.scanner_version`, { nullable: true });
  reportDigest(value.scanned_content_digest, `${path}.scanned_content_digest`);
  reportEnum(value.completion, V5_SCAN_COMPLETION_STATES, `${path}.completion`);
  reportEnum(value.retention, V5_SCANNER_RETENTION_STATES, `${path}.retention`);
  const findings = assertReportShape(value.findings, [...FINDINGS_KEYS], `${path}.findings`);
  for (const key of V5_SCAN_FINDING_KEYS) {
    reportEnum(findings[key], V5_SCAN_FINDING_STATES, `${path}.findings.${key}`);
  }
  reportEnum(findings.opaque_or_encrypted, V5_OPAQUE_CONTENT_STATES,
    `${path}.findings.opaque_or_encrypted`);
  return value;
}

// ---------------------------------------------------------------------------
// The checks.
//
// Each returns one outcome. `satisfied` is the only state that lets the next
// check run.
// ---------------------------------------------------------------------------

/**
 * The canonical fields come LAST, so a detail key can never overwrite the
 * outcome's own `state` or `reason_id`. That is not defensive tidiness: a detail
 * field named `state` silently rewriting a check's verdict is exactly how a
 * refusal would read as a pass.
 */
function outcome(check, state, reason_id, reason, detail = {}) {
  return { ...detail, check, state, reason_id, reason };
}

function satisfied(check, detail = {}) {
  return outcome(check, "satisfied", null, null, detail);
}

function checkSourceEnumerated(roster, sourceId) {
  const enumerated = Object.prototype.hasOwnProperty.call(roster.entries, sourceId);
  if (enumerated) return satisfied("source_enumerated", { source_id: sourceId, enumerated: true });
  // At the door, and with nothing about which checks it would have faced. A
  // source nobody enumerated does not get told what a clean scan would need.
  return outcome("source_enumerated", "violated", "source_not_enumerated",
    "the candidate's source is not on the enumerated PHI-incapable source roster",
    { source_id: sourceId, enumerated: false });
}

function checkRosterEntryIntegrity(entry) {
  const recomputed = backupSourceRosterEntryDigest({
    source_id: entry.source_id,
    source_label: entry.source_label,
    phi_capability: entry.phi_capability,
    declared_data_classes: entry.declared_data_classes,
  });
  if (recomputed === entry.sealed_entry_digest) {
    return satisfied("roster_entry_integrity", { sealed_entry_digest: entry.sealed_entry_digest });
  }
  return outcome("roster_entry_integrity", "violated", "roster_entry_digest_moved",
    "the roster entry's own sealed digest is not the digest of the entry as it now reads",
    { sealed_entry_digest: entry.sealed_entry_digest, recomputed_entry_digest: recomputed });
}

const PHI_CAPABILITY_REASON = Object.freeze({
  phi_capable: "source_not_phi_incapable",
  unassessed: "source_phi_capability_unassessed",
});

const PHI_CAPABILITY_MESSAGE = Object.freeze({
  phi_capable: "the source is enumerated as able to carry PHI, and maximum-trust quarantine takes only PHI-incapable source",
  unassessed: "the source is on the roster with no PHI-capability assessment, which is not the same as an assessment that found none",
});

function checkSourcePhiCapability(entry) {
  if (entry.phi_capability === V5_ADMISSIBLE_SOURCE_PHI_CAPABILITY) {
    return satisfied("source_phi_capability", { phi_capability: entry.phi_capability });
  }
  // `unassessed` is unobservable rather than violated: nobody has said the
  // source can carry PHI, and nobody has said it cannot. Both block; the record
  // shows which of the two happened.
  const state = entry.phi_capability === "unassessed" ? "unobservable" : "violated";
  return outcome("source_phi_capability", state, PHI_CAPABILITY_REASON[entry.phi_capability],
    PHI_CAPABILITY_MESSAGE[entry.phi_capability], { phi_capability: entry.phi_capability });
}

/**
 * An object may not declare a class its source was never enumerated to hold.
 *
 * This is the check that keeps the enumeration meaningful. Without it the roster
 * would only be gating WHICH source may be copied, and any object from an
 * enumerated source could then declare any class at all — including one the
 * assessment never considered when it concluded the source was PHI-incapable.
 */
function checkDeclaredClassWithinEnumeratedSource(entry, declaredClasses) {
  const enumerated = new Set(entry.declared_data_classes);
  const outside = declaredClasses.filter(dataClass => !enumerated.has(dataClass)).sort();
  const detail = {
    declared_data_classes: [...declaredClasses],
    enumerated_data_classes: [...entry.declared_data_classes],
    classes_outside_enumeration: outside,
  };
  if (outside.length === 0) return satisfied("declared_class_within_enumerated_source", detail);
  return outcome("declared_class_within_enumerated_source", "violated",
    "declared_class_outside_enumerated_source",
    "the candidate declares a data class its source was never enumerated to hold", detail);
}

/**
 * S01 decides the privacy question; this reads its answer.
 *
 * The `needs_independent_privacy_route` answer keeps its own reason id rather
 * than collapsing into the refusal, because the two mean different things to an
 * operator: one says never without an amendment, the other names evidence that
 * could exist. This module produces neither.
 */
function checkDeclaredDataClassBoundary(declaredClasses) {
  const answer = evaluatePrivacyBoundary({ data_classes: [...declaredClasses] });
  const detail = {
    privacy_decision: answer.decision,
    privacy_reason_id: answer.reason_id,
    prohibited_classes: answer.prohibited_classes ?? [],
    routed_classes: answer.routed_classes ?? [],
  };
  if (answer.decision === "allow") return satisfied("declared_data_class_boundary", detail);
  const reason = answer.decision === "needs_independent_privacy_route"
    ? "declared_data_class_requires_independent_privacy_route"
    : "declared_data_class_refused_by_privacy_boundary";
  const message = answer.decision === "needs_independent_privacy_route"
    ? "the declared data classes need an independent privacy route, which this module neither produces nor accepts"
    : "the global privacy boundary refuses the declared data classes";
  return outcome("declared_data_class_boundary", "violated", reason, message, detail);
}

/**
 * A finding about different bytes is not evidence about these bytes.
 *
 * This is the analogue of the supervisor's "a label never outvotes a digest": a
 * scan report naming a scanner, a version and a clean result proves nothing at
 * all about an object whose content digest it never read. It runs before every
 * finding is read, so a mismatched scan refuses on the mismatch rather than on
 * whatever the wrong object's scan happened to say.
 */
function checkScanCoverage(scan, contentDigest) {
  const detail = {
    candidate_content_digest: contentDigest,
    scanned_content_digest: scan.scanned_content_digest,
    completion: scan.completion,
    scanner_id: scan.scanner_id,
    scanner_version: scan.scanner_version,
  };
  if (scan.scanned_content_digest !== contentDigest) {
    return outcome("scan_coverage", "violated", "scan_covers_different_bytes",
      "the scan report covers bytes other than the candidate's", detail);
  }
  if (scan.completion !== "complete") {
    // Unobservable rather than violated: a partial scan found nothing wrong in
    // the part it reached and says nothing about the rest, which is exactly what
    // "I cannot say" means.
    return outcome("scan_coverage", "unobservable", "scan_incomplete",
      `the scan ${scan.completion === "aborted" ? "aborted" : "covered only part of the object"}, so it is not evidence about the whole object`,
      detail);
  }
  return satisfied("scan_coverage", detail);
}

function checkScannerRetention(scan) {
  const detail = { retention: scan.retention, scanner_id: scan.scanner_id };
  if (scan.retention === V5_ADMISSIBLE_SCANNER_RETENTION) {
    return satisfied("scanner_retention", detail);
  }
  if (scan.retention === "unstated") {
    return outcome("scanner_retention", "unobservable", "scanner_retention_unstated",
      "the scan report does not state whether the scanner retained what it read, so it cannot be shown to be the non-retaining one",
      detail);
  }
  return outcome("scanner_retention", "violated", "scanner_retains_scanned_content",
    "the scanner retained the content it read, which is a copy of material whose classification was unknown when the copy was made",
    detail);
}

const FINDING_CHECK = Object.freeze({
  phi_finding: Object.freeze({
    finding: "phi",
    found: "phi_found_in_source",
    not_assessed: "phi_not_assessed",
    found_message: "the scan found PHI in the candidate source",
    not_assessed_message: "the scan did not assess the candidate for PHI, which is not the same as finding none",
  }),
  credential_finding: Object.freeze({
    finding: "credential",
    found: "credential_found_in_source",
    not_assessed: "credential_not_assessed",
    found_message: "the scan found a credential in the candidate source",
    not_assessed_message: "the scan did not assess the candidate for credentials, which is not the same as finding none",
  }),
  nested_archive: Object.freeze({
    finding: "nested_archive",
    found: "nested_archive_found_in_source",
    not_assessed: "nested_archive_not_assessed",
    found_message: "the scan found a nested archive, whose contents were never themselves scanned",
    not_assessed_message: "the scan did not assess the candidate for nested archives, which is not the same as finding none",
  }),
});

function checkFinding(check, scan) {
  const spec = FINDING_CHECK[check];
  const state = scan.findings[spec.finding];
  const detail = { finding: spec.finding, finding_state: state };
  if (state === "none_found") return satisfied(check, detail);
  if (state === "not_assessed") {
    return outcome(check, "unobservable", spec.not_assessed, spec.not_assessed_message, detail);
  }
  return outcome(check, "violated", spec.found, spec.found_message, detail);
}

function checkOpaqueOrEncrypted(scan) {
  const state = scan.findings.opaque_or_encrypted;
  const detail = { finding: "opaque_or_encrypted", finding_state: state };
  if (state === "absent" || state === "present_classified") {
    return satisfied("opaque_or_encrypted", detail);
  }
  if (state === "not_assessed") {
    return outcome("opaque_or_encrypted", "unobservable", "opaque_or_encrypted_content_not_assessed",
      "the scan did not assess the candidate for opaque or encrypted content", detail);
  }
  return outcome("opaque_or_encrypted", "violated", "unclassified_opaque_or_encrypted_content",
    "the candidate carries opaque or encrypted content whose classification is unknown", detail);
}

// ---------------------------------------------------------------------------
// The admission decision — catalog checkable_done items 1 and 2.
// ---------------------------------------------------------------------------

function readCandidate(candidate) {
  assertObject(candidate, "request.candidate");
  assertClosedKeys(candidate, CANDIDATE_KEYS, "request.candidate");
  assertRequiredKeys(candidate,
    ["content_digest", "declared_data_classes", "object_id", "source_id"], "request.candidate");
  return {
    source_id: assertStableId(candidate.source_id, "request.candidate.source_id"),
    object_id: assertStableId(candidate.object_id, "request.candidate.object_id"),
    content_digest: assertDigestRef(candidate.content_digest, "request.candidate.content_digest"),
    declared_data_classes: assertDataClassList(candidate.declared_data_classes,
      "request.candidate.declared_data_classes"),
  };
}

/**
 * Decide whether one candidate object may enter the one-way quarantine.
 *
 *   1. The request must be readable and closed. There is no field by which a
 *      caller can name which checks apply, waive one, or assert that content it
 *      did not scan was fine.
 *   2. The checks run in V5_BACKUP_ADMISSION_CHECKS order and the first one that
 *      does not reach `satisfied` decides the answer; every later check reports
 *      `not_reached`. An unenumerated source, a moved roster seal, a PHI-capable
 *      source, a widened class list and a refused privacy class therefore all
 *      refuse before the scan is read at all.
 *   3. An `admit` is not a copy. It says these eleven negatives did not fire on
 *      the facts as reported; nothing moves, and the result's own fields say so.
 */
export function evaluateBackupSourceAdmission(request) {
  assertObject(request, "request");
  assertClosedKeys(request, REQUEST_KEYS, "request");
  assertRequiredKeys(request, ["actor", "candidate", "roster", "scan"], "request");

  const actorInput = assertObject(request.actor, "request.actor");
  // Closed to exactly the fields identity.js's classifier reads. The
  // classification itself comes from there; this module keeps no second actor
  // registry and decides no authority of its own.
  assertClosedKeys(actorInput, ACTOR_KEYS, "request.actor");
  assertRequiredKeys(actorInput, ["slug"], "request.actor");
  const actorSlug = assertStableId(actorInput.slug, "request.actor.slug");
  const actorAuthorizationClass = authorizationClassForActor(actorInput);

  const roster = assertNormalizedRoster(request.roster, "request.roster");
  const scan = assertNormalizedScan(request.scan, "request.scan");
  const candidate = readCandidate(request.candidate);

  const entry = Object.prototype.hasOwnProperty.call(roster.entries, candidate.source_id)
    ? roster.entries[candidate.source_id]
    : null;

  const evaluators = {
    source_enumerated: () => checkSourceEnumerated(roster, candidate.source_id),
    roster_entry_integrity: () => checkRosterEntryIntegrity(entry),
    source_phi_capability: () => checkSourcePhiCapability(entry),
    declared_class_within_enumerated_source: () =>
      checkDeclaredClassWithinEnumeratedSource(entry, candidate.declared_data_classes),
    declared_data_class_boundary: () => checkDeclaredDataClassBoundary(candidate.declared_data_classes),
    scan_coverage: () => checkScanCoverage(scan, candidate.content_digest),
    scanner_retention: () => checkScannerRetention(scan),
    phi_finding: () => checkFinding("phi_finding", scan),
    credential_finding: () => checkFinding("credential_finding", scan),
    nested_archive: () => checkFinding("nested_archive", scan),
    opaque_or_encrypted: () => checkOpaqueOrEncrypted(scan),
  };

  const checkStates = {};
  const checksSatisfied = [];
  const checksNotReached = [];
  let blockingCheck = null;
  for (const check of V5_BACKUP_ADMISSION_CHECKS) {
    if (blockingCheck !== null) {
      checkStates[check] = outcome(check, "not_reached", null,
        `not reached: ${blockingCheck} refused first`, { blocked_by: blockingCheck });
      checksNotReached.push(check);
      continue;
    }
    const state = evaluators[check]();
    checkStates[check] = state;
    if (state.state === "satisfied") checksSatisfied.push(check);
    else blockingCheck = check;
  }

  const admitted = blockingCheck === null;
  return deepFreeze({
    schema_version: V5_BACKUP_QUARANTINE_SCHEMA_VERSION,
    policy_version: V5_BACKUP_QUARANTINE_POLICY_VERSION,
    policy_digest: v5BackupQuarantinePolicyDigest(),
    decision: admitted ? "admit" : "refuse",
    reason_id: admitted
      ? "admitted_to_one_way_quarantine_after_all_negatives_cleared"
      : checkStates[blockingCheck].reason_id,
    checks_required: [...V5_BACKUP_ADMISSION_CHECKS],
    checks_satisfied: checksSatisfied,
    checks_not_reached: checksNotReached,
    blocking_check: blockingCheck,
    check_states: checkStates,
    candidate: {
      source_id: candidate.source_id,
      object_id: candidate.object_id,
      content_digest: candidate.content_digest,
      declared_data_classes: [...candidate.declared_data_classes],
      source_enumerated: entry !== null,
      roster_digest: roster.roster_digest,
      enumeration_authority: roster.enumeration_authority,
    },
    scan_read: {
      scanner_id: scan.scanner_id,
      scanner_version: scan.scanner_version,
      scanned_content_digest: scan.scanned_content_digest,
      completion: scan.completion,
      retention: scan.retention,
    },
    actor: {
      slug: actorSlug,
      // Read from identity.js and CARRIED, not acted on. This module grants no
      // authority by class and refuses none by class either; the class is in the
      // record so a reader can see who asked.
      authorization_class: actorAuthorizationClass,
    },
    // What this answer is not, in its own fields rather than in a comment.
    quarantine_direction: V5_QUARANTINE_DIRECTION,
    grants_read_back: false,
    copies_source_bytes: false,
    reads_source_content: false,
    runs_scanner: false,
    retains_scanned_content: false,
    // The deferred halves of the catalog, named in every result so no consumer
    // can read an admit here as deciding any of them.
    restore_decided: false,
    recovery_matrix_decided: false,
    outbound_reconciliation_decided: false,
    restore_and_recovery_matrix_seam: V5_RESTORE_AND_RECOVERY_MATRIX_SEAM,
    outbound_reconciliation_seam: V5_OUTBOUND_RECONCILIATION_SEAM,
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// The classify-or-purge terminal reconciliation — catalog checkable_done item 3.
//
// "Every quarantine object reaches EXACT classify-or-purge receipt" is a
// property of a SET, not of one object, so this is a reconciliation rather than
// a gate: it reports EVERY exception it finds instead of stopping at the first.
// An operator holding a list of three unreconciled objects can go and close
// three; an operator holding the first one has to run the reconciliation again
// after each fix.
//
// EXACTLY ONE, NOT AT LEAST ONE. Two receipts for the same object are an
// exception even when they agree, because "exact" is the word the requirement
// uses and a duplicated terminal outcome means the store recorded the same
// object's ending twice — which is how an object appears to have been purged and
// classified at once.
// ---------------------------------------------------------------------------

/**
 * The digest a well-formed terminal receipt's seal must equal. Exported so a
 * caller — or a reviewer — can compute the seal by hand.
 */
export function quarantineTerminalReceiptDigest(receipt) {
  assertObject(receipt, "receipt");
  assertClosedKeys(receipt, ["content_digest", "decided_by", "object_id", "outcome", "receipt_id"], "receipt");
  assertRequiredKeys(receipt, ["content_digest", "decided_by", "object_id", "outcome", "receipt_id"], "receipt");
  return digest({
    schema_version: V5_BACKUP_QUARANTINE_SCHEMA_VERSION,
    receipt_kind: V5_QUARANTINE_TERMINAL_RECEIPT_KIND,
    receipt_id: assertStableId(receipt.receipt_id, "receipt.receipt_id"),
    object_id: assertStableId(receipt.object_id, "receipt.object_id"),
    outcome: assertEnum(receipt.outcome, V5_QUARANTINE_TERMINAL_OUTCOMES,
      "receipt.outcome", "unknown_terminal_outcome"),
    content_digest: assertDigestRef(receipt.content_digest, "receipt.content_digest"),
    decided_by: assertStableId(receipt.decided_by, "receipt.decided_by"),
  });
}

function readInventory(inventory) {
  assertArray(inventory, "request.inventory");
  const byObject = new Map();
  for (const [index, raw] of inventory.entries()) {
    const at = `request.inventory[${index}]`;
    assertObject(raw, at);
    assertClosedKeys(raw, INVENTORY_ENTRY_KEYS, at);
    assertRequiredKeys(raw, INVENTORY_ENTRY_KEYS, at);
    const entry = {
      object_id: assertStableId(raw.object_id, `${at}.object_id`),
      content_digest: assertDigestRef(raw.content_digest, `${at}.content_digest`),
      admitted_policy_digest: assertDigestRef(raw.admitted_policy_digest, `${at}.admitted_policy_digest`),
    };
    // A repeated object id is malformed rather than an exception: an inventory
    // that lists the same object twice cannot be reconciled at all, because
    // "exactly one receipt" has no meaning against two copies of one row.
    if (byObject.has(entry.object_id)) {
      fail("duplicate_inventory_object", `${at} repeats object "${entry.object_id}"`,
        { path: at, object_id: entry.object_id });
    }
    byObject.set(entry.object_id, entry);
  }
  return byObject;
}

function readReceipts(receipts) {
  assertArray(receipts, "request.receipts");
  const read = [];
  const seenIds = new Set();
  for (const [index, raw] of receipts.entries()) {
    const at = `request.receipts[${index}]`;
    assertObject(raw, at);
    assertClosedKeys(raw, RECEIPT_KEYS, at);
    assertRequiredKeys(raw, RECEIPT_KEYS, at);
    const receipt = {
      receipt_id: assertStableId(raw.receipt_id, `${at}.receipt_id`),
      object_id: assertStableId(raw.object_id, `${at}.object_id`),
      outcome: assertEnum(raw.outcome, V5_QUARANTINE_TERMINAL_OUTCOMES, `${at}.outcome`,
        "unknown_terminal_outcome"),
      content_digest: assertDigestRef(raw.content_digest, `${at}.content_digest`),
      decided_by: assertStableId(raw.decided_by, `${at}.decided_by`),
      sealed_receipt_digest: assertDigestRef(raw.sealed_receipt_digest, `${at}.sealed_receipt_digest`),
    };
    if (seenIds.has(receipt.receipt_id)) {
      fail("duplicate_receipt", `${at} repeats receipt "${receipt.receipt_id}"`,
        { path: at, receipt_id: receipt.receipt_id });
    }
    seenIds.add(receipt.receipt_id);
    read.push(receipt);
  }
  return read;
}

function exception(kind, object_id, detail = {}) {
  return { ...detail, kind, object_id };
}

/**
 * Reconcile one quarantine inventory against its terminal receipts.
 *
 * Returns `complete` only when every inventory object reached exactly one
 * receipt, that receipt's own seal still reproduces, its content digest is the
 * one the inventory holds, no receipt names an object that was never admitted,
 * and no object was admitted under a policy this module no longer implements.
 *
 * AN EMPTY INVENTORY IS REPORTED AS EMPTY. It reconciles vacuously, which is
 * true and nearly useless, so `inventory_empty` is in the result: a consumer
 * that reads `complete` as evidence that quarantine work was reconciled can see
 * that no object was examined.
 */
export function reconcileQuarantineTerminalOutcomes(request) {
  assertObject(request, "request");
  assertClosedKeys(request, RECONCILE_KEYS, "request");
  assertRequiredKeys(request, RECONCILE_KEYS, "request");

  const inventory = readInventory(request.inventory);
  const receipts = readReceipts(request.receipts);
  const currentPolicyDigest = v5BackupQuarantinePolicyDigest();

  const receiptsByObject = new Map();
  const exceptions = [];

  for (const receipt of receipts) {
    const recomputed = quarantineTerminalReceiptDigest({
      receipt_id: receipt.receipt_id,
      object_id: receipt.object_id,
      outcome: receipt.outcome,
      content_digest: receipt.content_digest,
      decided_by: receipt.decided_by,
    });
    if (recomputed !== receipt.sealed_receipt_digest) {
      exceptions.push(exception("receipt_seal_moved", receipt.object_id, {
        receipt_id: receipt.receipt_id,
        sealed_receipt_digest: receipt.sealed_receipt_digest,
        recomputed_receipt_digest: recomputed,
      }));
      // A receipt whose seal has moved is not this object's receipt, so it is
      // not counted toward the object's terminal outcome. Counting it would let
      // an edited receipt satisfy the very requirement the seal exists to prove.
      continue;
    }
    if (!inventory.has(receipt.object_id)) {
      exceptions.push(exception("receipt_for_object_not_in_quarantine_inventory", receipt.object_id, {
        receipt_id: receipt.receipt_id, outcome: receipt.outcome,
      }));
      continue;
    }
    const existing = receiptsByObject.get(receipt.object_id);
    if (existing === undefined) receiptsByObject.set(receipt.object_id, [receipt]);
    else existing.push(receipt);
  }

  for (const [objectId, entry] of inventory) {
    if (entry.admitted_policy_digest !== currentPolicyDigest) {
      exceptions.push(exception("object_admitted_under_a_superseded_policy", objectId, {
        admitted_policy_digest: entry.admitted_policy_digest,
        current_policy_digest: currentPolicyDigest,
      }));
    }
    const objectReceipts = receiptsByObject.get(objectId) ?? [];
    if (objectReceipts.length === 0) {
      exceptions.push(exception("object_without_terminal_receipt", objectId, {}));
      continue;
    }
    if (objectReceipts.length > 1) {
      exceptions.push(exception("object_with_conflicting_terminal_receipts", objectId, {
        receipt_ids: objectReceipts.map(r => r.receipt_id).sort(),
        outcomes: [...new Set(objectReceipts.map(r => r.outcome))].sort(),
      }));
      continue;
    }
    const [only] = objectReceipts;
    if (only.content_digest !== entry.content_digest) {
      exceptions.push(exception("receipt_content_digest_does_not_match_inventory", objectId, {
        receipt_id: only.receipt_id,
        inventory_content_digest: entry.content_digest,
        receipt_content_digest: only.content_digest,
      }));
    }
  }

  exceptions.sort((left, right) =>
    left.object_id.localeCompare(right.object_id) || left.kind.localeCompare(right.kind));

  const reconciled = exceptions.length === 0;
  const exceptionKinds = [...new Set(exceptions.map(e => e.kind))].sort();
  const outcomeCounts = { classified: 0, purged: 0 };
  for (const [objectId, list] of receiptsByObject) {
    if (list.length === 1 && inventory.has(objectId)) outcomeCounts[list[0].outcome] += 1;
  }

  return deepFreeze({
    schema_version: V5_BACKUP_QUARANTINE_SCHEMA_VERSION,
    policy_version: V5_BACKUP_QUARANTINE_POLICY_VERSION,
    policy_digest: currentPolicyDigest,
    decision: reconciled ? "complete" : "incomplete",
    reason_id: reconciled
      ? "every_quarantine_object_reached_exactly_one_terminal_receipt"
      : "quarantine_objects_unreconciled",
    objects_examined: inventory.size,
    receipts_read: receipts.length,
    // True and nearly useless, said out loud so it cannot be read as work done.
    inventory_empty: inventory.size === 0,
    terminal_outcome_counts: outcomeCounts,
    exception_kinds_registered: [...V5_RECONCILIATION_EXCEPTION_KINDS],
    exception_kinds_found: exceptionKinds,
    exceptions,
    // What this answer is not.
    issues_receipts: false,
    purges_anything: false,
    classifies_anything: false,
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// The closed, versioned policy preimage and its digest.
//
// Nothing situational is bound — no source, object, roster, scanner, actor or
// acceptance fact — so two callers describing the same policy reach the same
// digest. The digest is an identity for these bytes and nothing else: it is not
// an acceptance, not a receipt, and not evidence for any consumer gate.
//
// EVERY CLOSED VOCABULARY THIS MODULE DECIDES AGAINST IS ENUMERATED HERE. A
// vocabulary that is not in the preimage can change without the policy digest
// moving, which is how a consumer ends up pinned to a policy it is no longer
// reading. Every list is sorted EXPLICITLY rather than left in declaration
// order, so the digest is stable by construction rather than by the luck of a
// list that happens to be alphabetical today.
// ---------------------------------------------------------------------------

export function v5BackupQuarantinePolicyPreimage() {
  return {
    schema_version: V5_BACKUP_QUARANTINE_SCHEMA_VERSION,
    policy_version: V5_BACKUP_QUARANTINE_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    roster_entry_kind: V5_SOURCE_ROSTER_ENTRY_KIND,
    terminal_receipt_kind: V5_QUARANTINE_TERMINAL_RECEIPT_KIND,
    quarantine_direction: V5_QUARANTINE_DIRECTION,
    checks_in_order: [...V5_BACKUP_ADMISSION_CHECKS],
    checks_before_any_content_fact_is_read: [...V5_CHECKS_BEFORE_ANY_CONTENT_FACT_IS_READ],
    check_states: [...V5_ADMISSION_CHECK_STATES].sort(),
    source_phi_capabilities: [...V5_SOURCE_PHI_CAPABILITIES].sort(),
    scan_completion_states: [...V5_SCAN_COMPLETION_STATES].sort(),
    scanner_retention_states: [...V5_SCANNER_RETENTION_STATES].sort(),
    scan_finding_states: [...V5_SCAN_FINDING_STATES].sort(),
    scan_finding_keys: [...V5_SCAN_FINDING_KEYS].sort(),
    opaque_content_states: [...V5_OPAQUE_CONTENT_STATES].sort(),
    terminal_outcomes: [...V5_QUARANTINE_TERMINAL_OUTCOMES].sort(),
    reconciliation_exception_kinds: [...V5_RECONCILIATION_EXCEPTION_KINDS].sort(),
    reason_ids: [...V5_BACKUP_QUARANTINE_REASON_IDS].sort(),
    admissible_source_phi_capability: V5_ADMISSIBLE_SOURCE_PHI_CAPABILITY,
    admissible_scanner_retention: V5_ADMISSIBLE_SCANNER_RETENTION,
    // The privacy vocabulary is S01's and is bound BY REFERENCE rather than
    // copied, so this policy's digest moves when the prohibited set moves. A
    // copy would let the two drift apart silently, which is the whole reason
    // there is only one privacy authority.
    privacy_authority: "global-boundaries.v5.js:evaluatePrivacyBoundary",
    prohibited_data_classes: [...V5_PROHIBITED_DATA_CLASSES].sort(),
    registered_data_classes: [...V5_DATA_CLASSES].sort(),
    redecides_privacy_boundary: false,
    caller_may_select_checks: false,
    caller_may_assert_admissibility: false,
    unstated_observation_blocks: true,
    first_unsatisfied_check_decides: true,
    roster_is_an_input: true,
    ships_a_roster: false,
    scan_report_is_an_input: true,
    runs_a_scanner: false,
    exactly_one_terminal_receipt_required: true,
    // The deferred clauses, hashed in. The day any is written this digest moves,
    // and a consumer pinned to the deferred policy is refused rather than
    // silently reading a policy that now decides more than it did.
    restore_clause: "decided_in:recovery-matrix.v5.js",
    recovery_matrix_clause: "decided_in:recovery-matrix.v5.js",
    outbound_reconciliation_clause: "decided_in:recovery-matrix.v5.js",
    restore_and_recovery_matrix_seam: V5_RESTORE_AND_RECOVERY_MATRIX_SEAM,
    outbound_reconciliation_seam: V5_OUTBOUND_RECONCILIATION_SEAM,
    enumerated_source_roster_seam: V5_ENUMERATED_SOURCE_ROSTER_SEAM,
    non_retaining_scanner_seam: V5_NON_RETAINING_SCANNER_SEAM,
    blocking_decision_ids: [...V5_BACKUP_BLOCKING_DECISION_IDS].sort(),
    decides_restore: false,
    decides_recovery_matrix: false,
    decides_outbound_reconciliation: false,
    copies_source_bytes: false,
    grants_read_back: false,
    issues_receipts: false,
  };
}

/** The deterministic `sha256:` digest of the closed backup-quarantine policy. */
export function v5BackupQuarantinePolicyDigest() {
  return digest(v5BackupQuarantinePolicyPreimage());
}

/** The exact canonical bytes hashed, so a reviewer can check the digest by hand. */
export function v5BackupQuarantinePolicyCanonicalBytes() {
  return canonicalJson(v5BackupQuarantinePolicyPreimage());
}

/**
 * The zero-effect projection of the slice: what this file decides, what it
 * hashes to, and the named seams it does not decide. Reading it accepts nothing.
 */
export function v5BackupQuarantineProjection(options = {}) {
  assertObject(options, "options");
  assertClosedKeys(options, ["expected_policy_digest"], "options");
  const policyDigest = v5BackupQuarantinePolicyDigest();
  if (options.expected_policy_digest !== undefined) {
    if (typeof options.expected_policy_digest !== "string" ||
        !SHA256_REF.test(options.expected_policy_digest)) {
      fail("invalid_expected_digest", "options.expected_policy_digest must be a sha256: reference",
        { path: "options.expected_policy_digest" });
    }
    if (options.expected_policy_digest !== policyDigest) {
      fail("stale_expected_digest",
        "the policy no longer hashes to the expected digest; re-read it rather than acting on the stale one",
        { expected: options.expected_policy_digest, actual: policyDigest });
    }
  }
  return deepFreeze({
    schema_version: V5_BACKUP_QUARANTINE_SCHEMA_VERSION,
    policy_version: V5_BACKUP_QUARANTINE_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    policy_digest: policyDigest,
    slice: "V5-F08",
    requirement_ids: ["Q034"],
    checkable_done_decided_here: [
      "scanner refuses PHI, credentials, nested archives and unclassified opaque/encrypted content",
      "only enumerated PHI-incapable source enters one-way quarantine",
      "every quarantine object reaches exact classify-or-purge receipt",
    ],
    // Named rather than implied, each with the fact that is missing.
    unimplemented_dependencies: [
      "Q034.D1 settled text: doctrine-store text, absent from global-boundaries.v5.js V5_SETTLED_DECISIONS",
      "the enumerated PHI-incapable source roster: a human assessment about real systems; none ships here",
      "the non-retaining source scanner: no scanner exists in this repository; its report is an input",
      "restore verification and the independently controlled copy: V5_RESTORE_AND_RECOVERY_MATRIX_SEAM, decided in recovery-matrix.v5.js",
      "the RPO/RTO cells and the business-day calendar the adapter cell is measured against",
      "outbound-queue release against external-effect reconciliation: V5_OUTBOUND_RECONCILIATION_SEAM, decided in recovery-matrix.v5.js",
      "the independent restore oracle's gate member: independent_restore_oracle is a registered producer " +
        "role in benchmark-minimum.v5.js with no MINIMUM_REQUIRED_MEMBERS entry naming its step",
    ],
    blocking_decision_ids: [...V5_BACKUP_BLOCKING_DECISION_IDS],
    accepts_anything: false,
    effects: V5_NO_EFFECTS,
  });
}
