// V5-F10 — partner-scoped Mail and Calendar adapters, proved case by case.
//
// Everything here is synthetic and nothing reaches a database, a provider, a
// network, a clock or the filesystem. The module under test is pure, so this
// suite can prove the properties that actually matter about a connector kernel:
//
//   * that ONE package serves BOTH partners and neither installation can reach
//     the other's account, native identity or scope,
//   * that a credential and a message body are refused STRUCTURALLY, by field
//     name, before any value is read,
//   * that ambiguity resolves to private and never falls through to include,
//   * that the privacy boundary is S01's answer carried through rather than a
//     second copy of the PHI list,
//   * that an included item becomes an artifact F01 ITSELF admits — F01 is the
//     independent oracle for the shape, so the mapping is not graded by the code
//     that produced it,
//   * that the offline queue delivers exactly once and refuses the one conflict
//     nobody downstream can untangle,
//   * and that combined Joe/Dell coverage is unreachable from every input.
//
// NO FIXTURE NAMES A REAL THING: every account, device, digest and native id
// below is unmistakably test data on an .invalid domain.

import test from "node:test";
import assert from "node:assert/strict";

import { canonicalJson, digest } from "../src/artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  V5_NO_EFFECTS,
  V5_PERMITTED_DATA_CLASSES,
  evaluatePrivacyBoundary,
  v5BoundaryPolicyPreimage,
} from "../src/global-boundaries.v5.js";
import {
  V5_F01_EVIDENCE_CLASSES,
  V5_F01_HOMES,
  V5_F01_TAINT_CLASSES,
  admitCorporateArtifact,
} from "../src/record-source-authority.v5.js";
import {
  V5F10Error,
  V5_F10_ACTION_CAPABILITY_SEAM,
  V5_F10_AUTHORITATIVE_HOME,
  V5_F10_CLASSIFICATION_DECISIONS,
  V5_F10_CLASSIFICATION_SCHEMA_VERSION,
  V5_F10_CONNECTOR_GATE_ID,
  V5_F10_CREDENTIAL_FRAGMENTS,
  V5_F10_DEPLOYMENT_STATES,
  V5_F10_EVIDENCE_CLASS,
  V5_F10_INDEPENDENT_RECEIPT_STEP,
  V5_F10_INGESTION_SCOPES,
  V5_F10_ITEM_KINDS,
  V5_F10_NON_INGESTING_DEPLOYMENT_STATES,
  V5_F10_OPERATIONS,
  V5_F10_OPERATION_KEYS,
  V5_F10_QUEUE_DISPOSITIONS,
  V5_F10_RAW_CONTENT_FRAGMENTS,
  V5_F10_READ_OPERATIONS,
  V5_F10_RELEVANCE_STATES,
  V5_F10_RETRIEVAL_CLASSES,
  V5_F10_TAINT_CLASS,
  V5_F10_WRITE_OPERATIONS,
  classifyCorrespondenceItem,
  compilePartnerInstallation,
  evaluateConnectorOperation,
  evaluateIngestionScope,
  partnerConnectorGaps,
  partnerInstallationCanonicalBytes,
  reconcileOfflineQueue,
  toCorporateArtifactCandidate,
  v5F10ConnectorProjection,
  v5F10PolicyCanonicalBytes,
  v5F10PolicyDigest,
  v5F10PolicyPreimage,
} from "../src/partner-mail-calendar.v5.js";

// ---------------------------------------------------------------------------
// Fixtures. Two partners, two devices, two accounts, one package.
// ---------------------------------------------------------------------------

const SOURCE = "outlook-fixture";
const NOW = "2026-09-10T18:00:00Z";
const CONTENT_A = `sha256:${"a1".repeat(32)}`;
const CONTENT_B = `sha256:${"b2".repeat(32)}`;

const PARTNERS = Object.freeze({
  joe: { account: "joe-fixture@partner-devices.invalid", device_id: "fixture-device-joe" },
  dell: { account: "dell-fixture@partner-devices.invalid", device_id: "fixture-device-dell" },
});

function installationConfig(partner, overrides = {}) {
  return {
    installation_version: 1,
    partner_slug: partner,
    device_id: PARTNERS[partner].device_id,
    source_system: SOURCE,
    account: PARTNERS[partner].account,
    item_kinds: ["mail_message", "calendar_event"],
    deployment_state: "deployed",
    ...overrides,
  };
}

function install(partner, overrides = {}) {
  return compilePartnerInstallation(installationConfig(partner, overrides));
}

function item(partner, overrides = {}) {
  return {
    item_kind: "mail_message",
    account: PARTNERS[partner].account,
    native_identity: {
      source_system: SOURCE,
      native_id: "fixture-native-0001",
      native_id_epoch: "fixture-epoch-1",
    },
    native_version: "fixture-change-key-1",
    content_digest: CONTENT_A,
    byte_length: 4096,
    observed_at: "2026-09-10T17:00:00Z",
    relevance_state: "relevant_business_context",
    declared_data_classes: ["tenant_business_contact"],
    ...overrides,
  };
}

function classify(installation, partner, overrides = {}, now = NOW) {
  return classifyCorrespondenceItem({ installation, item: item(partner, overrides), now });
}

function throwsWithCode(fn, code) {
  try {
    fn();
  } catch (error) {
    assert.ok(error instanceof V5F10Error, `expected V5F10Error, got ${error?.name}: ${error?.message}`);
    assert.equal(error.code, code, `expected code "${code}", got "${error.code}": ${error.message}`);
    return error;
  }
  assert.fail(`expected a throw with code "${code}", nothing was thrown`);
}

// ---------------------------------------------------------------------------
// ONE PACKAGE, PER-PARTNER ISOLATION. checkable_done item 1.
// ---------------------------------------------------------------------------

test("ISOLATION: the same package compiles a separate installation for each partner", () => {
  const joe = install("joe");
  const dell = install("dell");
  assert.deepEqual(joe.covers_partners, ["joe"]);
  assert.deepEqual(dell.covers_partners, ["dell"]);
  assert.notEqual(joe.installation_digest, dell.installation_digest);
  // The digest is over the identity, not the deployment posture: two partners
  // differ, and the same partner redeployed does not become a different install.
  assert.equal(install("joe", { deployment_state: "not_deployed" }).installation_digest,
    joe.installation_digest);
});

test("ISOLATION: an installation names exactly one partner and cannot name two", () => {
  throwsWithCode(() => compilePartnerInstallation(
    { ...installationConfig("joe"), partner_slugs: ["joe", "dell"] }), "unknown_field");
  throwsWithCode(() => compilePartnerInstallation(
    installationConfig("joe", { partner_slug: ["joe", "dell"] })), "invalid_shape");
});

test("ISOLATION: partnerhood is identity.js's test, so a non-partner installation refuses", () => {
  for (const slug of ["claude", "codex", "hermes-pilot", "joe-local", "studio"]) {
    const error = throwsWithCode(
      () => compilePartnerInstallation(installationConfig("joe", { partner_slug: slug })),
      "unknown_partner");
    assert.equal(error.detail.partner_slug, slug);
  }
});

test("ISOLATION: one partner's installation refuses the other partner's account", () => {
  const joe = install("joe");
  const crossed = classify(joe, "joe", { account: PARTNERS.dell.account });
  assert.equal(crossed.decision, "refuse");
  assert.equal(crossed.reason_id, "account_outside_partner_installation");
  assert.equal(crossed.admissible_item, null);
  // And the mirror image, so the isolation is not an accident of which partner
  // happened to be first.
  const dell = install("dell");
  const mirrored = classify(dell, "dell", { account: PARTNERS.joe.account });
  assert.equal(mirrored.reason_id, "account_outside_partner_installation");
});

test("ISOLATION: one partner's installation refuses the other partner's source system", () => {
  const joe = install("joe");
  const foreign = classify(joe, "joe", {
    native_identity: { source_system: "other-tenant-mailbox", native_id: "n1", native_id_epoch: "e1" },
  });
  assert.equal(foreign.decision, "refuse");
  assert.equal(foreign.reason_id, "native_identity_source_mismatch");
});

test("ISOLATION: the identical battery passes for both partners and differs only by identity", () => {
  const battery = installation => [
    evaluateConnectorOperation({ installation, operation: "list_mail_messages" }),
    evaluateConnectorOperation({ installation, operation: "send_mail_message" }),
    classifyCorrespondenceItem({ installation, item: item(installation.partner_slug), now: NOW }),
    classifyCorrespondenceItem({
      installation,
      item: item(installation.partner_slug, { relevance_state: "ambiguous" }),
      now: NOW,
    }),
    evaluateIngestionScope({
      installation, requested_scope: "single_partner",
      requested_partner_slug: installation.partner_slug,
    }),
    evaluateIngestionScope({ installation, requested_scope: "combined_partners" }),
  ].map(result => `${result.decision}:${result.reason_id}`);

  const joeRun = battery(install("joe"));
  const dellRun = battery(install("dell"));
  assert.deepEqual(joeRun, dellRun, "the package must not special-case either partner");
  assert.ok(joeRun.every(entry => entry.length > 0));
});

test("ISOLATION: interleaving two installations leaves no state behind", () => {
  const joe = install("joe");
  const dell = install("dell");
  const isolatedJoe = classify(joe, "joe");
  const isolatedDell = classify(dell, "dell");

  // Same calls, interleaved, plus a cross-account refusal in between.
  const a = classify(joe, "joe");
  classify(joe, "joe", { account: PARTNERS.dell.account });
  const b = classify(dell, "dell");
  classify(dell, "dell", { relevance_state: "ambiguous" });
  const c = classify(joe, "joe");

  assert.equal(canonicalJson(a), canonicalJson(isolatedJoe));
  assert.equal(canonicalJson(b), canonicalJson(isolatedDell));
  assert.equal(canonicalJson(c), canonicalJson(isolatedJoe));
  assert.notEqual(canonicalJson(isolatedJoe), canonicalJson(isolatedDell));
});

// ---------------------------------------------------------------------------
// CREDENTIALS NEVER LEAVE THE ORIGIN DEVICE. checkable_done item 2.
// ---------------------------------------------------------------------------

test("CREDENTIALS: every registered credential fragment is refused as a config field name", () => {
  for (const fragment of V5_F10_CREDENTIAL_FRAGMENTS) {
    const error = throwsWithCode(
      () => compilePartnerInstallation({ ...installationConfig("joe"), [`mailbox_${fragment}`]: "x" }),
      "credential_in_installation_config");
    assert.equal(error.detail.fragment, fragment);
    // Upper case does not smuggle one past the check.
    throwsWithCode(
      () => compilePartnerInstallation({
        ...installationConfig("joe"), [`MAILBOX_${fragment.toUpperCase()}`]: "x",
      }),
      "credential_in_installation_config");
  }
});

test("CREDENTIALS: the credential check runs BEFORE the shape check", () => {
  // A config that is BOTH credential-bearing and structurally broken must report
  // the credential. Reporting "missing_field" first would mean a well-formed
  // config carrying a secret got further into the module than a malformed one.
  const error = throwsWithCode(
    () => compilePartnerInstallation({ partner_slug: "joe", access_token: "x" }),
    "credential_in_installation_config");
  assert.equal(error.detail.fragment, "token");
});

test("CREDENTIALS: a compiled installation carries no credential-shaped field", () => {
  const joe = install("joe");
  for (const key of Object.keys(joe)) {
    const normalized = key.toLowerCase();
    assert.ok(!V5_F10_CREDENTIAL_FRAGMENTS.some(f => normalized.includes(f)),
      `compiled installation exposes a credential-shaped field "${key}"`);
  }
  // The claim lives at policy level, hashed, rather than as a flag on the object
  // that travels.
  assert.equal(v5F10PolicyPreimage().installation.holds_credentials, false);
  assert.equal(v5F10PolicyPreimage().installation.credentials_centralized, false);
  // Nothing in the hashed installation bytes either.
  const bytes = partnerInstallationCanonicalBytes(joe).toLowerCase();
  for (const fragment of V5_F10_CREDENTIAL_FRAGMENTS) {
    assert.ok(!bytes.includes(fragment), `installation preimage mentions "${fragment}"`);
  }
});

test("CREDENTIALS: raw correspondence is refused as an item field name", () => {
  for (const fragment of V5_F10_RAW_CONTENT_FRAGMENTS) {
    const error = throwsWithCode(
      () => classify(install("joe"), "joe", { [`message_${fragment}`]: "unread by design" }),
      "raw_content_must_not_leave_origin_device");
    assert.equal(error.detail.fragment, fragment);
  }
});

test("CREDENTIALS: a content digest and a byte length are measurements, not content", () => {
  // The negative above must not be so broad that the metadata this seam exists
  // to carry trips it. If it did, no item could ever be classified at all.
  const result = classify(install("joe"), "joe");
  assert.equal(result.decision, "include");
  assert.equal(result.raw_content_observed, false);
  assert.equal(result.credentials_observed, 0);
  assert.equal(result.admissible_item.content_digest, CONTENT_A);
  assert.equal(result.admissible_item.byte_length, 4096);
});

// ---------------------------------------------------------------------------
// THE DATA BOUNDARY. Minimum necessary; ambiguity stays private.
// ---------------------------------------------------------------------------

test("BOUNDARY: relevant business context is included and carries an admissible item", () => {
  const result = classify(install("joe"), "joe");
  assert.equal(result.decision, "include");
  assert.equal(result.reason_id, "relevant_business_context_within_boundary");
  assert.ok(result.admissible_item !== null);
  assert.match(result.delivery_key, /^sha256:[0-9a-f]{64}$/);
});

test("BOUNDARY: ambiguous correspondence stays private and never falls through to include", () => {
  const result = classify(install("joe"), "joe", { relevance_state: "ambiguous" });
  assert.equal(result.decision, "withhold_ambiguous");
  assert.equal(result.reason_id, "ambiguity_remains_private");
  assert.equal(result.admissible_item, null);
  assert.equal(result.delivery_key, null);
});

test("BOUNDARY: unrelated correspondence is excluded and carries nothing forward", () => {
  const result = classify(install("joe"), "joe", { relevance_state: "unrelated" });
  assert.equal(result.decision, "exclude_unrelated");
  assert.equal(result.reason_id, "unrelated_correspondence_excluded");
  assert.equal(result.admissible_item, null);
  assert.equal(result.delivery_key, null);
});

test("BOUNDARY: there is no relevance state outside the closed three, and no default", () => {
  for (const bogus of ["probably_relevant", "likely_business", "", "relevant"]) {
    throwsWithCode(() => classify(install("joe"), "joe", { relevance_state: bogus }),
      bogus === "" ? "unknown_relevance_state" : "unknown_relevance_state");
  }
  assert.deepEqual([...V5_F10_RELEVANCE_STATES].sort(),
    ["ambiguous", "relevant_business_context", "unrelated"]);
});

test("BOUNDARY: the privacy boundary is S01's answer, evaluated BEFORE relevance", () => {
  // PHI on an item its own device called UNRELATED must still refuse as PHI. If
  // relevance ran first the answer would be exclude_unrelated, and the item
  // would have been dropped without ever meeting the privacy boundary — which is
  // not the same thing as meeting it and failing.
  const result = classify(install("joe"), "joe", {
    relevance_state: "unrelated",
    declared_data_classes: ["phi", "tenant_business_contact"],
  });
  assert.equal(result.decision, "refuse");
  // Computed independently from S01, not read back off the result under test.
  const s01 = evaluatePrivacyBoundary({ data_classes: ["phi", "tenant_business_contact"] });
  assert.equal(result.reason_id, s01.reason_id);
  assert.equal(result.reason_id, "phi_or_raw_patient_location_refused");
  assert.deepEqual(result.prohibited_classes, ["phi"]);
  assert.equal(result.amendment_required, s01.amendment_required);
});

test("BOUNDARY: the aggregate privacy route is carried through, not re-decided here", () => {
  const classes = ["aggregate_patient_location_heatmap"];
  const result = classify(install("joe"), "joe", { declared_data_classes: classes });
  const s01 = evaluatePrivacyBoundary({ data_classes: classes });
  assert.equal(result.decision, "needs_independent_privacy_route");
  assert.equal(result.reason_id, s01.reason_id);
  assert.equal(result.required_evidence, s01.required_evidence);
  assert.deepEqual(result.routed_classes, s01.routed_classes);
  assert.equal(result.admissible_item, null);
});

test("BOUNDARY: an unregistered data class is refused by S01 rather than admitted", () => {
  try {
    classify(install("joe"), "joe", { declared_data_classes: ["mailbox_freeform"] });
    assert.fail("an unregistered data class must not be admitted");
  } catch (error) {
    assert.equal(error.code, "unknown_data_class");
  }
});

test("BOUNDARY: classification is mandatory; an item cannot arrive unclassified", () => {
  throwsWithCode(() => classify(install("joe"), "joe", { declared_data_classes: [] }), "invalid_shape");
  const withoutField = item("joe");
  delete withoutField.declared_data_classes;
  throwsWithCode(
    () => classifyCorrespondenceItem({ installation: install("joe"), item: withoutField, now: NOW }),
    "missing_field");
});

test("BOUNDARY: an item observed after now cannot be classified", () => {
  const result = classify(install("joe"), "joe", { observed_at: "2026-09-10T19:00:00Z" });
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "observed_after_now");
});

test("BOUNDARY: an instant that does not exist on the calendar is not normalized into one", () => {
  const error = throwsWithCode(
    () => classify(install("joe"), "joe", { observed_at: "2026-02-31T00:00:00Z" }),
    "invalid_timestamp");
  assert.match(error.message, /does not exist on the calendar/);
  // 2026 is not a leap year, so this one is a real boundary rather than a typo.
  throwsWithCode(() => classify(install("joe"), "joe", { observed_at: "2026-02-29T00:00:00Z" }),
    "invalid_timestamp");
  assert.equal(classify(install("joe"), "joe", { observed_at: "2024-02-29T00:00:00Z" }).decision,
    "include");
});

test("BOUNDARY: every decision this module returns is inside the closed vocabulary", () => {
  const seen = new Set();
  const joe = install("joe");
  for (const relevance of V5_F10_RELEVANCE_STATES) {
    seen.add(classify(joe, "joe", { relevance_state: relevance }).decision);
  }
  seen.add(classify(joe, "joe", { declared_data_classes: ["phi"] }).decision);
  seen.add(classify(joe, "joe", { declared_data_classes: ["aggregate_patient_volume_estimate"] }).decision);
  for (const decision of seen) {
    assert.ok(V5_F10_CLASSIFICATION_DECISIONS.includes(decision),
      `"${decision}" is returned but is not in the closed decision vocabulary`);
  }
  assert.equal(seen.size, V5_F10_CLASSIFICATION_DECISIONS.length,
    "every registered decision must be reachable, and no unregistered one may be");
});

// ---------------------------------------------------------------------------
// THE EFFECT CLASS: read ingestion only, until a separate action capability.
// ---------------------------------------------------------------------------

test("EFFECT CLASS: every registered write operation refuses by name", () => {
  const joe = install("joe");
  assert.ok(V5_F10_WRITE_OPERATIONS.length >= 6, "the write half must exist to be refused");
  for (const operation of V5_F10_WRITE_OPERATIONS) {
    const result = evaluateConnectorOperation({ installation: joe, operation });
    assert.equal(result.decision, "refuse", operation);
    assert.equal(result.reason_id, "write_capability_not_in_this_slice", operation);
    assert.equal(result.action_capability_seam, V5_F10_ACTION_CAPABILITY_SEAM);
    assert.equal(result.effect_class, "read_ingestion_only_until_separate_action_capability");
  }
});

test("EFFECT CLASS: every registered read operation is allowed on a deployed installation", () => {
  const joe = install("joe");
  assert.ok(V5_F10_READ_OPERATIONS.length >= 4);
  for (const operation of V5_F10_READ_OPERATIONS) {
    const result = evaluateConnectorOperation({ installation: joe, operation });
    assert.equal(result.decision, "allow", operation);
    assert.equal(result.reason_id, "read_ingestion_within_effect_class", operation);
    assert.equal(result.retrieval_class, V5_F10_RETRIEVAL_CLASSES[V5_F10_OPERATIONS[operation].item_kind]);
  }
});

test("EFFECT CLASS: a write refuses AS A WRITE even when the installation is not deployed", () => {
  // The order is the policy. If the deployment check ran first, a send attempt
  // on an undeployed machine would answer "not deployed", which reads as though
  // deploying would make sending available. It would not.
  for (const deployment_state of V5_F10_NON_INGESTING_DEPLOYMENT_STATES) {
    const undeployed = install("joe", { deployment_state });
    const write = evaluateConnectorOperation({ installation: undeployed, operation: "send_mail_message" });
    assert.equal(write.reason_id, "write_capability_not_in_this_slice", deployment_state);
    const read = evaluateConnectorOperation({ installation: undeployed, operation: "list_mail_messages" });
    assert.equal(read.reason_id, "partner_installation_not_deployed", deployment_state);
  }
});

test("EFFECT CLASS: a write refuses AS A WRITE even when its item kind is not installed", () => {
  const mailOnly = install("joe", { item_kinds: ["mail_message"] });
  const write = evaluateConnectorOperation({ installation: mailOnly, operation: "create_calendar_event" });
  assert.equal(write.reason_id, "write_capability_not_in_this_slice");
  const read = evaluateConnectorOperation({ installation: mailOnly, operation: "list_calendar_events" });
  assert.equal(read.decision, "refuse");
  assert.equal(read.reason_id, "item_kind_not_installed");
  assert.deepEqual(read.installed_item_kinds, ["mail_message"]);
});

test("EFFECT CLASS: an unregistered operation THROWS rather than being judged", () => {
  const joe = install("joe");
  for (const operation of ["archive_mail_message", "list_contacts", "", "toString", "__proto__"]) {
    throwsWithCode(() => evaluateConnectorOperation({ installation: joe, operation }),
      "unknown_connector_operation");
  }
  // The two kinds of no, side by side: a registered write is an ANSWER.
  assert.equal(
    evaluateConnectorOperation({ installation: joe, operation: "send_mail_message" }).decision,
    "refuse");
});

test("EFFECT CLASS: the registry labels every operation and the two halves do not overlap", () => {
  assert.deepEqual(
    [...V5_F10_READ_OPERATIONS, ...V5_F10_WRITE_OPERATIONS].sort(), [...V5_F10_OPERATION_KEYS]);
  assert.equal(
    new Set([...V5_F10_READ_OPERATIONS, ...V5_F10_WRITE_OPERATIONS]).size,
    V5_F10_OPERATION_KEYS.length);
  for (const operation of V5_F10_OPERATION_KEYS) {
    assert.ok(["read", "write"].includes(V5_F10_OPERATIONS[operation].mode));
    assert.ok(V5_F10_ITEM_KINDS.includes(V5_F10_OPERATIONS[operation].item_kind));
  }
});

test("EFFECT CLASS: an unobserved deployment is not a deployment", () => {
  // `unknown` sits with `not_deployed` deliberately: reading silence as
  // readiness is exactly the claim this slice must not make.
  assert.ok(V5_F10_NON_INGESTING_DEPLOYMENT_STATES.includes("unknown"));
  assert.deepEqual(
    V5_F10_DEPLOYMENT_STATES.filter(s => !V5_F10_NON_INGESTING_DEPLOYMENT_STATES.includes(s)),
    ["deployed"]);
});

// ---------------------------------------------------------------------------
// THE SOURCE INTEGRATOR SEAM. F01 is the independent oracle for the shape.
// ---------------------------------------------------------------------------

test("SOURCE: an included classification becomes an artifact F01 ITSELF admits", () => {
  const joe = install("joe");
  const classification = classify(joe, "joe");
  const candidate = toCorporateArtifactCandidate({
    installation: joe, classification, evidence_ref: "fixture-device-evidence-0001",
  });
  assert.equal(candidate.admitted, false);
  assert.equal(candidate.f01_admission_required, true);

  // The grading is F01's, not this module's: the candidate is handed straight to
  // the reviewed F01 admission and must come back admitted on its own terms.
  const admitted = admitCorporateArtifact({
    tenant: ORGANIZATION_TENANT_ID, artifact: candidate.artifact, now: NOW,
  });
  assert.notEqual(admitted.decision, "refuse",
    `F01 refused this adapter's own candidate: ${admitted.reason_id}`);
  assert.equal(admitted.decision, "allow");
  assert.equal(admitted.reason_id, "artifact_admitted_as_evidence");
});

test("SOURCE: both item kinds map to F01's mailbox class and keep their own item_kind", () => {
  const joe = install("joe");
  for (const item_kind of V5_F10_ITEM_KINDS) {
    const classification = classify(joe, "joe", { item_kind });
    const candidate = toCorporateArtifactCandidate({
      installation: joe, classification, evidence_ref: "fixture-device-evidence-0002",
    });
    assert.equal(candidate.artifact.evidence_class, V5_F10_EVIDENCE_CLASS);
    assert.equal(candidate.item_kind, item_kind, "the F10-side distinction must survive the mapping");
    assert.equal(candidate.artifact.provenance.retrieval_class, V5_F10_RETRIEVAL_CLASSES[item_kind]);
    const admitted = admitCorporateArtifact({
      tenant: ORGANIZATION_TENANT_ID, artifact: candidate.artifact, now: NOW,
    });
    assert.equal(admitted.decision, "allow", item_kind);
    assert.equal(admitted.reason_id, "artifact_admitted_as_evidence", item_kind);
  }
});

test("SOURCE: taint is never lowered and the caller cannot supply one", () => {
  const joe = install("joe");
  const candidate = toCorporateArtifactCandidate({
    installation: joe, classification: classify(joe, "joe"), evidence_ref: "fixture-evidence-0003",
  });
  assert.equal(candidate.artifact.taint_class, V5_F10_TAINT_CLASS);
  assert.equal(candidate.artifact.taint_class, "untrusted_external");
  // There is no field a caller could set it through, at either seam.
  throwsWithCode(() => classify(joe, "joe", { taint_class: "corporate_source_of_record" }),
    "unknown_field");
  throwsWithCode(() => toCorporateArtifactCandidate({
    installation: joe, classification: classify(joe, "joe"),
    evidence_ref: "fixture-evidence-0003", taint_class: "first_party_record_layer",
  }), "unknown_field");
});

test("SOURCE: a withheld or excluded classification cannot become an ingestion candidate", () => {
  const joe = install("joe");
  for (const relevance_state of ["ambiguous", "unrelated"]) {
    const classification = classify(joe, "joe", { relevance_state });
    const error = throwsWithCode(() => toCorporateArtifactCandidate({
      installation: joe, classification, evidence_ref: "fixture-evidence-0004",
    }), "candidate_from_non_included_classification");
    assert.equal(error.detail.decision, classification.decision);
  }
});

test("SOURCE: a refused classification cannot become an ingestion candidate either", () => {
  const joe = install("joe");
  const phi = classify(joe, "joe", { declared_data_classes: ["phi"] });
  throwsWithCode(() => toCorporateArtifactCandidate({
    installation: joe, classification: phi, evidence_ref: "fixture-evidence-0005",
  }), "candidate_from_non_included_classification");
});

test("SOURCE: a hand-built object cannot pose as a classification", () => {
  const joe = install("joe");
  throwsWithCode(() => toCorporateArtifactCandidate({
    installation: joe, evidence_ref: "fixture-evidence-0006",
    classification: { decision: "include", admissible_item: item("joe") },
  }), "uncompiled_classification");
});

test("SOURCE: one partner's classification cannot be candidated under the other's installation", () => {
  const joe = install("joe");
  const dell = install("dell");
  const dellClassification = classify(dell, "dell");
  assert.equal(dellClassification.decision, "include");
  throwsWithCode(() => toCorporateArtifactCandidate({
    installation: joe, classification: dellClassification, evidence_ref: "fixture-evidence-0007",
  }), "classification_outside_installation");
});

test("SOURCE: this module admits nothing of its own", () => {
  const joe = install("joe");
  const candidate = toCorporateArtifactCandidate({
    installation: joe, classification: classify(joe, "joe"), evidence_ref: "fixture-evidence-0008",
  });
  assert.equal(candidate.admitted, false);
  assert.equal(candidate.f01_admission_entrypoint, "admitCorporateArtifact");
  assert.deepEqual(candidate.effects, V5_NO_EFFECTS);
  assert.equal(v5F10PolicyPreimage().source_integration.admits_its_own_output, false);
});

// ---------------------------------------------------------------------------
// THE OFFLINE QUEUE. checkable_done item 3.
// ---------------------------------------------------------------------------

function queue(installation, entries, already_delivered) {
  const request = { installation, entries };
  if (already_delivered !== undefined) request.already_delivered = already_delivered;
  return reconcileOfflineQueue(request);
}

test("QUEUE: a replayed observation is suppressed rather than delivered twice", () => {
  const joe = install("joe");
  const observation = classify(joe, "joe");
  const result = queue(joe, [observation, observation, observation]);
  assert.deepEqual(result.entries.map(e => e.disposition),
    ["deliver", "duplicate_suppressed", "duplicate_suppressed"]);
  assert.deepEqual(result.entries.map(e => e.reason_id),
    ["first_delivery_of_observation", "repeat_in_batch", "repeat_in_batch"]);
  assert.equal(result.delivered_count, 1);
  assert.equal(result.suppressed_count, 2);
  assert.equal(new Set(result.delivered_keys).size, 1);
});

test("QUEUE: an observation the device already handed over is suppressed", () => {
  const joe = install("joe");
  const observation = classify(joe, "joe");
  const result = queue(joe, [observation], [observation.delivery_key]);
  assert.equal(result.entries[0].disposition, "duplicate_suppressed");
  assert.equal(result.entries[0].reason_id, "already_delivered");
  assert.equal(result.delivered_count, 0);
  // And the same batch with an unrelated prior key still delivers.
  const unrelated = queue(joe, [observation], [`sha256:${"cd".repeat(32)}`]);
  assert.equal(unrelated.entries[0].disposition, "deliver");
});

test("QUEUE: two contents for one native item at different instants both deliver, later marked", () => {
  const joe = install("joe");
  const earlier = classify(joe, "joe", { observed_at: "2026-09-10T10:00:00Z", content_digest: CONTENT_A });
  const later = classify(joe, "joe", { observed_at: "2026-09-10T11:00:00Z", content_digest: CONTENT_B });
  const result = queue(joe, [earlier, later]);
  assert.deepEqual(result.entries.map(e => e.disposition), ["deliver", "deliver"]);
  assert.deepEqual(result.entries.map(e => e.revision_of_native_item), [false, true]);
  assert.equal(result.delivered_count, 2);
  // Presented in the other order the ANSWER is the same, because it is about the
  // instants and not about which one the caller listed first.
  const reversed = queue(joe, [later, earlier]);
  assert.deepEqual(reversed.entries.map(e => e.revision_of_native_item), [true, false]);
});

test("QUEUE: the queue does not decide WHICH revision wins", () => {
  const joe = install("joe");
  const result = queue(joe, [
    classify(joe, "joe", { observed_at: "2026-09-10T10:00:00Z", content_digest: CONTENT_A }),
    classify(joe, "joe", { observed_at: "2026-09-10T11:00:00Z", content_digest: CONTENT_B }),
  ]);
  assert.equal(result.revision_order_decided, false);
  assert.equal(result.revision_order_authority, "record-source-authority.v5.js");
  // Both cross the wire; F01 orders them against established state.
  assert.equal(result.delivered_keys.length, 2);
});

test("QUEUE: two contents for one native item at the SAME instant refuse — both of them", () => {
  const joe = install("joe");
  const one = classify(joe, "joe", { observed_at: "2026-09-10T10:00:00Z", content_digest: CONTENT_A });
  const two = classify(joe, "joe", { observed_at: "2026-09-10T10:00:00Z", content_digest: CONTENT_B });
  const result = queue(joe, [one, two]);
  assert.deepEqual(result.entries.map(e => e.disposition),
    ["ambiguous_revision_order_refused", "ambiguous_revision_order_refused"]);
  assert.deepEqual(result.entries.map(e => e.reason_id),
    ["same_instant_content_conflict_has_no_order", "same_instant_content_conflict_has_no_order"]);
  assert.equal(result.delivered_count, 0);
  assert.equal(result.refused_count, 2);
  // The refusal is about the CONFLICT, not about the instant: the same instant
  // with the same content is an ordinary duplicate.
  const harmless = queue(joe, [one, one]);
  assert.deepEqual(harmless.entries.map(e => e.disposition), ["deliver", "duplicate_suppressed"]);
});

test("QUEUE: a same-instant conflict does not poison an unrelated native item in the batch", () => {
  const joe = install("joe");
  const other = classify(joe, "joe", {
    native_identity: { source_system: SOURCE, native_id: "fixture-native-0002", native_id_epoch: "fixture-epoch-1" },
  });
  const result = queue(joe, [
    classify(joe, "joe", { observed_at: "2026-09-10T10:00:00Z", content_digest: CONTENT_A }),
    other,
    classify(joe, "joe", { observed_at: "2026-09-10T10:00:00Z", content_digest: CONTENT_B }),
  ]);
  assert.deepEqual(result.entries.map(e => e.disposition),
    ["ambiguous_revision_order_refused", "deliver", "ambiguous_revision_order_refused"]);
  assert.equal(result.delivered_count, 1);
});

test("QUEUE: a recycled native id is NAMED as an epoch split and never merged", () => {
  const joe = install("joe");
  const original = classify(joe, "joe");
  const recycled = classify(joe, "joe", {
    native_identity: { source_system: SOURCE, native_id: "fixture-native-0001", native_id_epoch: "fixture-epoch-2" },
    content_digest: CONTENT_B,
  });
  const result = queue(joe, [original, recycled]);
  assert.deepEqual(result.entries.map(e => e.native_id_epoch_split), [true, true]);
  assert.equal(result.epoch_split_count, 2);
  // Not merged, and NOT called a revision of each other: they are different
  // records wearing the same name, and F01 decides that against stored state.
  assert.deepEqual(result.entries.map(e => e.revision_of_native_item), [false, false]);
  assert.deepEqual(result.entries.map(e => e.disposition), ["deliver", "deliver"]);
  // A single epoch is not a split.
  assert.equal(queue(joe, [original]).entries[0].native_id_epoch_split, false);
});

test("QUEUE: the queue holds observations and accepts no mutation, in step with S01", () => {
  const joe = install("joe");
  const result = queue(joe, [classify(joe, "joe")]);
  const s01 = v5BoundaryPolicyPreimage().read_continuity.accepts_offline_mutation;
  assert.equal(s01, false, "S01's own settled Q007.D1 posture");
  assert.equal(result.accepts_offline_mutation, s01,
    "F10's queue must not drift from S01's offline-mutation posture");
  assert.deepEqual(result.effects, V5_NO_EFFECTS);
});

test("QUEUE: nothing but an included classification may be queued", () => {
  const joe = install("joe");
  for (const relevance_state of ["ambiguous", "unrelated"]) {
    throwsWithCode(() => queue(joe, [classify(joe, "joe", { relevance_state })]),
      "queued_non_included_classification");
  }
  throwsWithCode(() => queue(joe, [{ decision: "include", admissible_item: item("joe") }]),
    "uncompiled_classification");
});

test("QUEUE: one partner's queue refuses the other partner's observation", () => {
  const joe = install("joe");
  const dell = install("dell");
  const error = throwsWithCode(() => queue(joe, [classify(joe, "joe"), classify(dell, "dell")]),
    "entry_outside_installation");
  assert.equal(error.detail.path, "request.entries[1]");
});

test("QUEUE: every entry is validated BEFORE any disposition is taken", () => {
  // A queue that answered the first two entries and then threw would have told
  // the caller something it may act on about a batch it never finished reading.
  const joe = install("joe");
  const good = classify(joe, "joe");
  const error = throwsWithCode(
    () => queue(joe, [good, good, classify(joe, "joe", { relevance_state: "ambiguous" })]),
    "queued_non_included_classification");
  assert.equal(error.detail.path, "request.entries[2]");
});

test("QUEUE: the same batch reconciles to byte-identical bytes twice", () => {
  const joe = install("joe");
  const entries = [
    classify(joe, "joe"),
    classify(joe, "joe", { observed_at: "2026-09-10T11:00:00Z", content_digest: CONTENT_B }),
    classify(joe, "joe", { native_identity: { source_system: SOURCE, native_id: "fixture-native-0003", native_id_epoch: "fixture-epoch-1" } }),
  ];
  assert.equal(canonicalJson(queue(joe, entries)), canonicalJson(queue(joe, entries)));
});

test("QUEUE: an empty batch is an answer, not an error", () => {
  const result = queue(install("joe"), []);
  assert.deepEqual(result.entries, []);
  assert.equal(result.delivered_count, 0);
  assert.equal(result.accepts_offline_mutation, false);
});

test("QUEUE: every disposition it can reach is in the registered set", () => {
  const joe = install("joe");
  const same = "2026-09-10T10:00:00Z";
  const result = queue(joe, [
    classify(joe, "joe"),
    classify(joe, "joe"),
    classify(joe, "joe", { observed_at: same, content_digest: CONTENT_A,
      native_identity: { source_system: SOURCE, native_id: "fixture-native-0009", native_id_epoch: "e1" } }),
    classify(joe, "joe", { observed_at: same, content_digest: CONTENT_B,
      native_identity: { source_system: SOURCE, native_id: "fixture-native-0009", native_id_epoch: "e1" } }),
  ]);
  const reached = new Set(result.entries.map(e => e.disposition));
  for (const disposition of reached) {
    assert.ok(V5_F10_QUEUE_DISPOSITIONS.includes(disposition), disposition);
  }
  assert.equal(reached.size, V5_F10_QUEUE_DISPOSITIONS.length,
    "every registered disposition must be reachable, and no unregistered one may be");
});

test("QUEUE: the grouping separator cannot appear inside any half it separates", () => {
  // The fold key joins the native-identity triple with NUL. If NUL could appear
  // inside a source system, a native id or an epoch, two different triples could
  // fold to one key and one native item would silently shadow another. This
  // proves the invariant directly rather than assuming it.
  const joe = install("joe");
  const NUL = "\u0000";
  for (const field of ["source_system", "native_id", "native_id_epoch"]) {
    const native_identity = {
      source_system: SOURCE, native_id: "fixture-native-0001", native_id_epoch: "fixture-epoch-1",
    };
    native_identity[field] = `${native_identity[field]}${NUL}x`;
    throwsWithCode(() => classify(joe, "joe", { native_identity }), "unsafe_unicode");
  }
  // Distinct triples stay distinct.
  const a = classify(joe, "joe");
  const b = classify(joe, "joe", {
    native_identity: { source_system: SOURCE, native_id: "fixture-native-0001x", native_id_epoch: "fixture-epoch-1" },
  });
  assert.notEqual(a.delivery_key, b.delivery_key);
  assert.equal(queue(joe, [a, b]).delivered_count, 2);
});

// ---------------------------------------------------------------------------
// THE PARTNER CONNECTOR GATE. checkable_done items 4 and 5.
// ---------------------------------------------------------------------------

test("GATE: combined Joe/Dell coverage is unavailable under every input this module accepts", () => {
  for (const partner of ["joe", "dell"]) {
    for (const deployment_state of V5_F10_DEPLOYMENT_STATES) {
      const installation = install(partner, { deployment_state });
      const result = evaluateIngestionScope({ installation, requested_scope: "combined_partners" });
      assert.equal(result.decision, "unavailable", `${partner}/${deployment_state}`);
      assert.equal(result.reason_id, "combined_partner_coverage_requires_independent_receipt");
      assert.equal(result.required_receipt_step, V5_F10_INDEPENDENT_RECEIPT_STEP);
      assert.equal(result.connector_gate_id, V5_F10_CONNECTOR_GATE_ID);
      assert.equal(result.combined_partner_coverage_claimed, false);
    }
  }
});

test("GATE: deploying an adapter and testing an adapter both fail to satisfy the gate", () => {
  const deployed = install("joe", { deployment_state: "deployed" });
  for (const requested_scope of V5_F10_INGESTION_SCOPES) {
    const request = { installation: deployed, requested_scope };
    if (requested_scope === "single_partner") request.requested_partner_slug = "joe";
    const result = evaluateIngestionScope(request);
    assert.equal(result.satisfied_by_adapter_deployment, false, requested_scope);
    assert.equal(result.satisfied_by_adapter_test, false, requested_scope);
    assert.equal(result.connector_gate_satisfied, false, requested_scope);
  }
  // A fully deployed, allowed single-partner scope is still not the gate.
  const allowed = evaluateIngestionScope({
    installation: deployed, requested_scope: "single_partner", requested_partner_slug: "joe",
  });
  assert.equal(allowed.decision, "allow");
  assert.equal(allowed.connector_gate_satisfied, false);
});

test("GATE: no field reaches an available combined answer", () => {
  const joe = install("joe");
  for (const smuggled of [
    { connector_gate_satisfied: true },
    { receipt: "step:partner-mail-calendar-connectors-independent-receipt" },
    { partner_mail_calendar_connectors_accepted: true },
    { combined_partner_coverage_claimed: true },
  ]) {
    throwsWithCode(
      () => evaluateIngestionScope({ installation: joe, requested_scope: "combined_partners", ...smuggled }),
      "unknown_field");
  }
  // Nor by naming a partner alongside a combined request.
  throwsWithCode(() => evaluateIngestionScope({
    installation: joe, requested_scope: "combined_partners", requested_partner_slug: "dell",
  }), "partner_slug_with_combined_scope");
});

test("GATE: single-partner scope is allowed only for the installation's own partner", () => {
  const joe = install("joe");
  const own = evaluateIngestionScope({
    installation: joe, requested_scope: "single_partner", requested_partner_slug: "joe",
  });
  assert.equal(own.decision, "allow");
  assert.equal(own.reason_id, "single_partner_scope_within_installation");
  const other = evaluateIngestionScope({
    installation: joe, requested_scope: "single_partner", requested_partner_slug: "dell",
  });
  assert.equal(other.decision, "refuse");
  assert.equal(other.reason_id, "partner_scope_outside_installation");
});

test("GATE: an undeployed partner is unavailable, and unavailable is not the same as refused", () => {
  for (const deployment_state of V5_F10_NON_INGESTING_DEPLOYMENT_STATES) {
    const result = evaluateIngestionScope({
      installation: install("dell", { deployment_state }),
      requested_scope: "single_partner", requested_partner_slug: "dell",
    });
    assert.equal(result.decision, "unavailable", deployment_state);
    assert.equal(result.reason_id, "partner_installation_not_deployed", deployment_state);
  }
});

test("GATE: a missing Dell deployment does not block core J1", () => {
  // The whole point of checkable_done item 4: "Dell's adapter is not deployed"
  // and "core J1 is held up" must never be readable as the same fact.
  const dell = install("dell", { deployment_state: "not_deployed" });
  const answers = [
    evaluateIngestionScope({ installation: dell, requested_scope: "single_partner", requested_partner_slug: "dell" }),
    evaluateIngestionScope({ installation: dell, requested_scope: "combined_partners" }),
    evaluateConnectorOperation({ installation: dell, operation: "list_mail_messages" }),
    evaluateConnectorOperation({ installation: dell, operation: "send_mail_message" }),
  ];
  for (const answer of answers) {
    assert.equal(answer.blocks_core_j1, false, answer.reason_id);
  }
  assert.equal(v5F10ConnectorProjection().blocks_core_j1, false);
});

test("GATE: no answer this module can produce ever blocks core J1", () => {
  const joe = install("joe");
  const swept = [
    ...V5_F10_OPERATION_KEYS.map(operation => evaluateConnectorOperation({ installation: joe, operation })),
    ...V5_F10_RELEVANCE_STATES.map(relevance_state => classify(joe, "joe", { relevance_state })),
    classify(joe, "joe", { declared_data_classes: ["phi"] }),
    classify(joe, "joe", { account: PARTNERS.dell.account }),
    evaluateIngestionScope({ installation: joe, requested_scope: "combined_partners" }),
    queue(joe, [classify(joe, "joe")]),
    toCorporateArtifactCandidate({
      installation: joe, classification: classify(joe, "joe"), evidence_ref: "fixture-evidence-0100",
    }),
  ];
  assert.ok(swept.length >= 18);
  for (const answer of swept) {
    assert.equal(answer.blocks_core_j1, false);
    assert.deepEqual(answer.effects, V5_NO_EFFECTS);
  }
});

// ---------------------------------------------------------------------------
// THE POLICY DIGEST, THE PROJECTION AND THE UPSTREAM BINDINGS.
// ---------------------------------------------------------------------------

test("POLICY: the digest is deterministic and the published bytes reproduce it", () => {
  const first = v5F10PolicyDigest();
  assert.match(first, /^sha256:[0-9a-f]{64}$/);
  assert.equal(first, v5F10PolicyDigest());
  assert.equal(v5F10PolicyCanonicalBytes(), canonicalJson(v5F10PolicyPreimage()));
  assert.equal(digest(JSON.parse(v5F10PolicyCanonicalBytes())), first);
});

test("POLICY: nothing situational is bound into the policy bytes", () => {
  const bytes = v5F10PolicyCanonicalBytes();
  for (const situational of [NOW, PARTNERS.joe.account, PARTNERS.dell.account,
    PARTNERS.joe.device_id, SOURCE, CONTENT_A]) {
    assert.ok(!bytes.includes(situational), `policy bytes bind the situational value ${situational}`);
  }
});

test("POLICY: every closed vocabulary is hashed in, so none of them can drift inline", () => {
  const bytes = v5F10PolicyCanonicalBytes();
  const vocabularies = [
    ...V5_F10_ITEM_KINDS, ...V5_F10_DEPLOYMENT_STATES, ...V5_F10_INGESTION_SCOPES,
    ...V5_F10_RELEVANCE_STATES, ...V5_F10_CLASSIFICATION_DECISIONS, ...V5_F10_QUEUE_DISPOSITIONS,
    ...V5_F10_OPERATION_KEYS, ...V5_F10_CREDENTIAL_FRAGMENTS, ...V5_F10_RAW_CONTENT_FRAGMENTS,
    ...Object.values(V5_F10_RETRIEVAL_CLASSES),
    V5_F10_EVIDENCE_CLASS, V5_F10_TAINT_CLASS, V5_F10_AUTHORITATIVE_HOME,
    V5_F10_ACTION_CAPABILITY_SEAM, V5_F10_CONNECTOR_GATE_ID, V5_F10_INDEPENDENT_RECEIPT_STEP,
  ];
  for (const token of vocabularies) {
    assert.ok(bytes.includes(token), `"${token}" is not hashed into the policy preimage`);
  }
});

test("POLICY: a stale expected digest is refused rather than served", () => {
  assert.doesNotThrow(() => v5F10ConnectorProjection({ expected_policy_digest: v5F10PolicyDigest() }));
  throwsWithCode(() => v5F10ConnectorProjection({ expected_policy_digest: `sha256:${"0".repeat(64)}` }),
    "stale_expected_digest");
  throwsWithCode(() => v5F10ConnectorProjection({ expected_policy_digest: "not-a-digest" }),
    "invalid_expected_digest");
  throwsWithCode(() => v5F10ConnectorProjection({ policy_digest: v5F10PolicyDigest() }), "unknown_field");
});

test("PROJECTION: reading the connector accepts nothing and names its gaps", () => {
  const projection = v5F10ConnectorProjection();
  assert.equal(projection.accepts_anything, false);
  assert.equal(projection.connector_gate_satisfied, false);
  assert.equal(projection.partner_connector_receipt_present, false);
  assert.equal(projection.global_secrets_boundary_receipt_present, false);
  assert.equal(projection.j1_core_production_outcome_present, false);
  assert.equal(projection.combined_partner_coverage_available, false);
  assert.deepEqual(projection.effects, V5_NO_EFFECTS);
  assert.ok(projection.gaps.length >= 6);
  for (const gap of projection.gaps) {
    assert.equal(gap.landed, false);
    assert.ok(gap.gap.length > 0 && gap.where.length > 0 && gap.what.length > 0);
  }
  assert.deepEqual(projection.gaps, partnerConnectorGaps());
});

test("PROJECTION: results are frozen all the way down", () => {
  const joe = install("joe");
  const classification = classify(joe, "joe");
  assert.throws(() => { classification.decision = "refuse"; }, TypeError);
  assert.throws(() => { classification.admissible_item.byte_length = 0; }, TypeError);
  const result = queue(joe, [classification]);
  assert.throws(() => { result.entries[0].delivered = false; }, TypeError);
  assert.throws(() => { result.entries.push({}); }, TypeError);
});

test("UPSTREAM: the evidence class, taint class and home are F01's, not local literals", () => {
  assert.ok(V5_F01_EVIDENCE_CLASSES.includes(V5_F10_EVIDENCE_CLASS));
  assert.ok(V5_F01_TAINT_CLASSES.includes(V5_F10_TAINT_CLASS));
  assert.ok(V5_F01_HOMES.includes(V5_F10_AUTHORITATIVE_HOME));
  // And F01 has no calendar class, which is why both kinds share the mailbox
  // one. If a calendar class ever lands upstream, this assertion is the reminder
  // that the mapping decision above is due for a rewrite.
  assert.equal(V5_F01_EVIDENCE_CLASSES.filter(c => c.includes("calendar")).length, 0);
  assert.ok(partnerConnectorGaps().some(gap => gap.gap === "no_calendar_evidence_class"));
});

test("UPSTREAM: the permitted data classes come from S01 and are not re-listed here", () => {
  const joe = install("joe");
  for (const dataClass of V5_PERMITTED_DATA_CLASSES) {
    const result = classify(joe, "joe", { declared_data_classes: [dataClass] });
    assert.equal(result.decision, "include", dataClass);
  }
  const bytes = v5F10PolicyCanonicalBytes();
  for (const dataClass of V5_PERMITTED_DATA_CLASSES) {
    assert.ok(!bytes.includes(dataClass),
      `F10's policy re-lists S01's permitted class "${dataClass}"; there must be one privacy authority`);
  }
  assert.equal(v5F10PolicyPreimage().data_boundary.privacy_boundary_authority, "global-boundaries.v5.js");
});

test("SCHEMA: every request shape is closed, at every seam", () => {
  const joe = install("joe");
  throwsWithCode(() => compilePartnerInstallation({ ...installationConfig("joe"), region: "us" }),
    "unknown_field");
  throwsWithCode(() => evaluateConnectorOperation({
    installation: joe, operation: "list_mail_messages", folder: "Inbox",
  }), "unknown_field");
  throwsWithCode(() => classifyCorrespondenceItem({
    installation: joe, item: item("joe"), now: NOW, trace: "x",
  }), "unknown_field");
  throwsWithCode(() => classify(joe, "joe", { folder: "Inbox" }), "unknown_field");
  throwsWithCode(() => classify(joe, "joe", {
    native_identity: { source_system: SOURCE, native_id: "n", native_id_epoch: "e", mailbox: "m" },
  }), "unknown_field");
  throwsWithCode(() => reconcileOfflineQueue({
    installation: joe, entries: [], flush: true,
  }), "unknown_field");
});

test("SCHEMA: an uncompiled installation is refused at every seam that takes one", () => {
  const posing = { ...installationConfig("joe"), installation_digest: `sha256:${"e".repeat(64)}` };
  throwsWithCode(() => evaluateConnectorOperation({ installation: posing, operation: "list_mail_messages" }),
    "uncompiled_installation");
  throwsWithCode(() => classifyCorrespondenceItem({ installation: posing, item: item("joe"), now: NOW }),
    "uncompiled_installation");
  throwsWithCode(() => reconcileOfflineQueue({ installation: posing, entries: [] }),
    "uncompiled_installation");
  throwsWithCode(() => evaluateIngestionScope({
    installation: posing, requested_scope: "single_partner", requested_partner_slug: "joe",
  }), "uncompiled_installation");
  throwsWithCode(() => toCorporateArtifactCandidate({
    installation: posing, classification: classify(install("joe"), "joe"), evidence_ref: "e1",
  }), "uncompiled_installation");
});

test("SCHEMA: the installation digest is over the bytes it publishes", () => {
  const joe = install("joe");
  assert.equal(joe.installation_digest, digest(JSON.parse(partnerInstallationCanonicalBytes(joe))));
  // A different device under the same partner is a different installation.
  const other = install("joe", { device_id: "fixture-device-joe-laptop" });
  assert.notEqual(other.installation_digest, joe.installation_digest);
});
