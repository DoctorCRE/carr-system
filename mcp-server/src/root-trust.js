import { createPublicKey, verify } from "node:crypto";

import { canonicalJson, digest, verifyArtifactBundle } from "./artifact-trust.js";
import { SCAC_ROOT_TRUST_CONFIG } from "./root-trust-config.js";

const SHA256 = /^sha256:[0-9a-f]{64}$/;
const ACTIONS = new Set(["establish", "rotate", "revoke", "recovery_drill"]);
const ED25519_SPKI_PREFIX = Buffer.from("302a300506032b6570032100", "hex");

function exactObject(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new TypeError(`${label}_malformed`);
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index]))
    throw new TypeError(`${label}_open_or_incomplete`);
}

export function rootTrustEventPayload(event) {
  return {
    schema_version: "scac-root-trust-event.v1",
    event_no: event.event_no,
    previous_event_digest: event.previous_event_digest ?? null,
    action: event.action,
    subject_key_digest: event.subject_key_digest,
    replacement_key_digest: event.replacement_key_digest ?? null,
    threshold: event.threshold,
    custodian_set_digest: event.custodian_set_digest,
    recovery_receipt_digest: event.recovery_receipt_digest ?? null,
    policy_epoch: event.policy_epoch,
    policy_epoch_digest: event.policy_epoch_digest,
  };
}

export function rootTrustEventDigest(event) {
  return digest({ ...rootTrustEventPayload(event),
    custodian_approval_digests: event.custodian_attestations.map(item => item.signature_digest).sort() });
}

function rawBytes(value, length, label) {
  if (typeof value !== "string" || !/^[A-Za-z0-9+/]+={0,2}$/.test(value) || value.length % 4)
    throw new TypeError(`${label}_malformed`);
  const bytes = Buffer.from(value, "base64");
  if (bytes.length !== length || bytes.toString("base64") !== value) throw new TypeError(`${label}_malformed`);
  return bytes;
}

export function custodianSetDigest(attestations) {
  return digest({ schema_version: "scac-root-custodian-set.v1",
    custodian_key_digests: attestations.map(item => item.custodian_key_digest).sort() });
}

function reviewedCustodianSet(expectedCustodianSetDigest, reviewedCustodianKeyDigests) {
  if (!SHA256.test(expectedCustodianSetDigest || "") || !Array.isArray(reviewedCustodianKeyDigests) ||
      reviewedCustodianKeyDigests.length < 2 || reviewedCustodianKeyDigests.length > 12)
    throw new TypeError("reviewed_custodian_set_required");
  const keys = [...reviewedCustodianKeyDigests].sort();
  if (keys.some(key => !SHA256.test(key || "")) || new Set(keys).size !== keys.length ||
      custodianSetDigest(keys.map(custodian_key_digest => ({ custodian_key_digest }))) !== expectedCustodianSetDigest)
    throw new TypeError("reviewed_custodian_set_mismatch");
  return new Set(keys);
}

function verifyCustodianQuorum(event, expectedCustodianSetDigest, reviewedKeys) {
  const statementDigest = digest(rootTrustEventPayload(event));
  const attestations = event.custodian_attestations;
  if (!Array.isArray(attestations) || attestations.length < event.threshold || attestations.length > 12)
    throw new Error("root_trust_quorum_invalid");
  const keys = new Set();
  for (const attestation of attestations) {
    exactObject(attestation, ["algorithm", "custodian_key_digest", "public_key", "signature",
      "signature_digest", "signed_payload_digest"], "custodian_attestation");
    if (attestation.algorithm !== "ed25519" || !SHA256.test(attestation.custodian_key_digest || "") ||
        !SHA256.test(attestation.signature_digest || "") || attestation.signed_payload_digest !== statementDigest)
      throw new Error("root_trust_attestation_malformed");
    const publicKey = rawBytes(attestation.public_key, 32, "custodian_public_key");
    const signature = rawBytes(attestation.signature, 64, "custodian_signature");
    if (digest(publicKey) !== attestation.custodian_key_digest || digest(signature) !== attestation.signature_digest ||
        keys.has(attestation.custodian_key_digest) || !reviewedKeys.has(attestation.custodian_key_digest))
      throw new Error("root_trust_quorum_invalid");
    const key = createPublicKey({ key: Buffer.concat([ED25519_SPKI_PREFIX, publicKey]), format: "der", type: "spki" });
    if (!verify(null, Buffer.from(statementDigest), key, signature))
      throw new Error("root_trust_attestation_invalid");
    keys.add(attestation.custodian_key_digest);
  }
  if (event.custodian_set_digest !== expectedCustodianSetDigest)
    throw new Error("root_trust_custodian_set_unreviewed");
}

export function verifyRootTrustChainAgainstDigest(
  events, expectedCustodianSetDigest, reviewedCustodianKeyDigests, reviewedThreshold) {
  if (!Array.isArray(events) || events.length === 0) throw new TypeError("root_trust_events_unavailable");
  const reviewedKeys = reviewedCustodianSet(expectedCustodianSetDigest, reviewedCustodianKeyDigests);
  if (!Number.isSafeInteger(reviewedThreshold) || reviewedThreshold < 2 ||
      reviewedThreshold > Math.min(8, reviewedKeys.size))
    throw new TypeError("reviewed_custodian_threshold_required");
  let previous = null;
  let activeKey = null;
  let lastRecoveryReceipt = null;
  for (const [index, event] of events.entries()) {
    exactObject(event, ["event_no", "event_digest", "previous_event_digest", "action",
      "subject_key_digest", "replacement_key_digest", "threshold", "custodian_set_digest", "custodian_attestations",
      "recovery_receipt_digest", "policy_epoch", "policy_epoch_digest", "production_trust_active"],
    "root_trust_event");
    if (!Number.isSafeInteger(event.event_no) || event.event_no !== index + 1 ||
        event.previous_event_digest !== previous || !ACTIONS.has(event.action) ||
        !SHA256.test(event.event_digest || "") || !SHA256.test(event.subject_key_digest || "") ||
        (event.replacement_key_digest !== null && !SHA256.test(event.replacement_key_digest || "")) ||
        (event.recovery_receipt_digest !== null && !SHA256.test(event.recovery_receipt_digest || "")) ||
        event.threshold !== reviewedThreshold ||
        !SHA256.test(event.custodian_set_digest || "") ||
        !Number.isSafeInteger(event.policy_epoch) || event.policy_epoch <= 0 ||
        !SHA256.test(event.policy_epoch_digest || "") || event.production_trust_active !== false)
      throw new Error("root_trust_event_malformed_or_operational");
    verifyCustodianQuorum(event, expectedCustodianSetDigest, reviewedKeys);
    if (rootTrustEventDigest(event) !== event.event_digest)
      throw new Error("root_trust_event_digest_mismatch");
    if (event.action === "establish") {
      if (event.event_no !== 1 || activeKey !== null || event.replacement_key_digest !== null ||
          event.recovery_receipt_digest !== null) throw new Error("root_trust_transition_invalid");
      activeKey = event.subject_key_digest;
    } else if (event.action === "rotate") {
      if (activeKey !== event.subject_key_digest || !event.replacement_key_digest ||
          event.replacement_key_digest === activeKey || event.recovery_receipt_digest !== null)
        throw new Error("root_trust_transition_invalid");
      activeKey = event.replacement_key_digest;
    } else if (event.action === "revoke") {
      if (activeKey !== event.subject_key_digest || event.replacement_key_digest !== null ||
          event.recovery_receipt_digest !== null) throw new Error("root_trust_transition_invalid");
      activeKey = null;
    } else {
      if (activeKey !== event.subject_key_digest || event.replacement_key_digest !== null ||
          !event.recovery_receipt_digest) throw new Error("root_trust_transition_invalid");
      lastRecoveryReceipt = event.recovery_receipt_digest;
    }
    previous = event.event_digest;
  }
  return Object.freeze({
    chain_state: "valid",
    active_key_digest: activeKey,
    latest_event_no: events.length,
    latest_event_digest: previous,
    recovery_state: lastRecoveryReceipt ? "offline_receipt_recorded" : "not_yet_recorded",
    recovery_receipt_digest: lastRecoveryReceipt,
    review_binding_state: "caller_digest_matched_non_authorizing",
    root_trust_operational: false,
    production_enforcement_active: false,
  });
}

export function evaluateRootTrust(events) {
  const expected = SCAC_ROOT_TRUST_CONFIG.reviewed_custodian_set_digest;
  const reviewedKeys = SCAC_ROOT_TRUST_CONFIG.reviewed_custodian_key_digests;
  const reviewedThreshold = SCAC_ROOT_TRUST_CONFIG.reviewed_threshold;
  if (SCAC_ROOT_TRUST_CONFIG.review_state !== "reviewed" || !SHA256.test(expected || "") ||
      !Number.isSafeInteger(reviewedThreshold))
    throw new Error("reviewed_custodian_set_unprovisioned");
  const state = verifyRootTrustChainAgainstDigest(events, expected, reviewedKeys, reviewedThreshold);
  return Object.freeze({ ...state, review_binding_state: "immutable_source_config_matched" });
}

export function verifyArtifactRootBindingAgainstDigest(
  artifactBundle, rootTrustEvents, expectedCustodianSetDigest, reviewedCustodianKeyDigests, reviewedThreshold) {
  const artifact = verifyArtifactBundle(artifactBundle);
  const root = verifyRootTrustChainAgainstDigest(
    rootTrustEvents, expectedCustodianSetDigest, reviewedCustodianKeyDigests, reviewedThreshold);
  const signerKey = artifactBundle.signature.signer_key_digest;
  const bound = root.active_key_digest !== null && root.active_key_digest === signerKey;
  return Object.freeze({
    artifact_digest: artifact.artifact_digest,
    manifest_digest: artifact.manifest_digest,
    signature_state: artifact.signature_state,
    transparency_state: artifact.transparency_state,
    root_binding_state: bound ? "current_nonproduction_root" : "untrusted_or_revoked_root",
    artifact_trust_state: bound ? "eligible_nonproduction_only" : "untrusted",
    reason_id: bound ? "scac.refusal.production_trust_inactive" : "scac.refusal.root_untrusted",
    root_trust_operational: false,
    production_enforcement_active: false,
  });
}

export function evaluateArtifactRootBinding(artifactBundle, rootTrustEvents) {
  const root = evaluateRootTrust(rootTrustEvents);
  const artifact = verifyArtifactBundle(artifactBundle);
  const bound = root.active_key_digest !== null && root.active_key_digest === artifactBundle.signature.signer_key_digest;
  return Object.freeze({
    artifact_digest: artifact.artifact_digest,
    manifest_digest: artifact.manifest_digest,
    signature_state: artifact.signature_state,
    transparency_state: artifact.transparency_state,
    root_binding_state: bound ? "current_nonproduction_root" : "untrusted_or_revoked_root",
    artifact_trust_state: bound ? "eligible_nonproduction_only" : "untrusted",
    reason_id: bound ? "scac.refusal.production_trust_inactive" : "scac.refusal.root_untrusted",
    review_binding_state: root.review_binding_state,
    root_trust_operational: false,
    production_enforcement_active: false,
  });
}

export function containsForbiddenRootMaterial(value) {
  const serialized = canonicalJson(value).toLowerCase();
  return /private[_ -]?key|secret[_ -]?key|seed phrase|recovery secret|age-secret-key/.test(serialized);
}
