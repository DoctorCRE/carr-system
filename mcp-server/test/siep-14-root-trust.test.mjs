import assert from "node:assert/strict";
import { createHash, generateKeyPairSync, sign } from "node:crypto";
import fs from "node:fs";
import test from "node:test";

import { artifactManifestDigest, digest, transparencyEntryDigest } from "../src/artifact-trust.js";
import { containsForbiddenRootMaterial, custodianSetDigest, evaluateRootTrust,
  rootTrustEventDigest, verifyArtifactRootBindingAgainstDigest,
  verifyRootTrustChainAgainstDigest } from "../src/root-trust.js";

const migration = fs.readFileSync(new URL("../../migrations/0458_siep14_root_trust.sql", import.meta.url), "utf8");
const sha = value => `sha256:${createHash("sha256").update(value).digest("hex")}`;
const CUSTODIANS = [generateKeyPairSync("ed25519"), generateKeyPairSync("ed25519"),
  generateKeyPairSync("ed25519")];
const CUSTODIAN_DESCRIPTORS = CUSTODIANS.map(({ privateKey, publicKey }) => {
  const publicRaw = publicKey.export({ format: "der", type: "spki" }).subarray(-32);
  return { privateKey, publicRaw, custodian_key_digest: digest(publicRaw) };
});
const REVIEWED_KEYS = CUSTODIAN_DESCRIPTORS.map(item => item.custodian_key_digest);
const REVIEWED_SET_DIGEST = custodianSetDigest(CUSTODIAN_DESCRIPTORS);

function signedFixture() {
  const { privateKey, publicKey } = generateKeyPairSync("ed25519");
  const publicRaw = publicKey.export({ format: "der", type: "spki" }).subarray(-32);
  const manifest = { artifact_digest: sha("artifact"), artifact_kind: "binary",
    media_type: "application/octet-stream", byte_length: 8, source_ref: "git:test",
    source_digest: sha("source"), sbom_digest: null, provenance_digest: sha("provenance"),
    policy_epoch: 1, policy_epoch_digest: sha("epoch"), manifest_digest: null };
  manifest.manifest_digest = artifactManifestDigest(manifest);
  const signatureBytes = sign(null, Buffer.from(manifest.manifest_digest), privateKey);
  const signature = { algorithm: "ed25519", manifest_digest: manifest.manifest_digest,
    public_key: publicRaw.toString("base64"), signature: signatureBytes.toString("base64"),
    signature_digest: digest(signatureBytes), signer_key_digest: digest(publicRaw),
    signed_payload_digest: manifest.manifest_digest, signature_scope: "scac-artifact-manifest.v1" };
  const entry = { entry_no: 1, entry_digest: null, previous_entry_digest: null,
    manifest_digest: manifest.manifest_digest, signature_digest: signature.signature_digest,
    statement_digest: sha("statement"), entry_kind: "artifact_inclusion" };
  entry.entry_digest = transparencyEntryDigest(entry);
  return { artifact: { manifest, signature, transparency: [entry] }, keyDigest: signature.signer_key_digest };
}

function event(overrides = {}, signerIndexes = [0, 1]) {
  const value = { event_no: 1, event_digest: null, previous_event_digest: null, action: "establish",
    subject_key_digest: sha("root-a"), replacement_key_digest: null, threshold: 2,
    custodian_set_digest: null, custodian_attestations: [],
    recovery_receipt_digest: null, policy_epoch: 1, policy_epoch_digest: sha("epoch"),
    production_trust_active: false, ...overrides };
  value.custodian_set_digest = REVIEWED_SET_DIGEST;
  const statement = digest({ schema_version: "scac-root-trust-event.v1", event_no: value.event_no,
    previous_event_digest: value.previous_event_digest, action: value.action,
    subject_key_digest: value.subject_key_digest, replacement_key_digest: value.replacement_key_digest,
    threshold: value.threshold, custodian_set_digest: value.custodian_set_digest,
    recovery_receipt_digest: value.recovery_receipt_digest,
    policy_epoch: value.policy_epoch, policy_epoch_digest: value.policy_epoch_digest });
  value.custodian_attestations = signerIndexes.map(index => CUSTODIAN_DESCRIPTORS[index]).map(
    ({ privateKey, publicRaw, custodian_key_digest }) => {
    const signature = sign(null, Buffer.from(statement), privateKey);
    return { algorithm: "ed25519", custodian_key_digest,
      public_key: publicRaw.toString("base64"), signature: signature.toString("base64"),
      signature_digest: digest(signature), signed_payload_digest: statement };
    });
  value.event_digest = rootTrustEventDigest(value);
  return value;
}

test("quorum-bound root can bind a valid artifact only for nonproduction", () => {
  const { artifact, keyDigest } = signedFixture();
  const rootEvent = event({ subject_key_digest: keyDigest });
  const state = verifyArtifactRootBindingAgainstDigest(
    artifact, [rootEvent], rootEvent.custodian_set_digest, REVIEWED_KEYS, 2);
  assert.equal(state.root_binding_state, "current_nonproduction_root");
  assert.equal(state.artifact_trust_state, "eligible_nonproduction_only");
  assert.equal(state.root_trust_operational, false);
  assert.equal(state.production_enforcement_active, false);
});

test("rotation, recovery proof, and revocation replay monotonically", () => {
  const establish = event();
  const rotate = event({ event_no: 2, previous_event_digest: establish.event_digest, action: "rotate",
    subject_key_digest: establish.subject_key_digest, replacement_key_digest: sha("root-b") });
  const recovery = event({ event_no: 3, previous_event_digest: rotate.event_digest, action: "recovery_drill",
    subject_key_digest: rotate.replacement_key_digest, recovery_receipt_digest: sha("offline-drill") });
  const revoke = event({ event_no: 4, previous_event_digest: recovery.event_digest, action: "revoke",
    subject_key_digest: rotate.replacement_key_digest });
  const state = verifyRootTrustChainAgainstDigest(
    [establish, rotate, recovery, revoke], establish.custodian_set_digest, REVIEWED_KEYS, 2);
  assert.equal(state.active_key_digest, null);
  assert.equal(state.recovery_state, "offline_receipt_recorded");
});

test("weak quorum, forks, illegal transitions, operational claims, and secret-shaped material fail closed", () => {
  const weak = event({ threshold: 3 });
  assert.throws(() => verifyRootTrustChainAgainstDigest(
    [weak], weak.custodian_set_digest, REVIEWED_KEYS, 3), /quorum_invalid/);
  const fork = event({ previous_event_digest: sha("fork") });
  assert.throws(() => verifyRootTrustChainAgainstDigest(
    [fork], fork.custodian_set_digest, REVIEWED_KEYS, 2), /malformed_or_operational/);
  const operational = event({ production_trust_active: true });
  assert.throws(() => verifyRootTrustChainAgainstDigest(
    [operational], operational.custodian_set_digest, REVIEWED_KEYS, 2), /malformed_or_operational/);
  const establish = event();
  const badRotate = event({ event_no: 2, previous_event_digest: establish.event_digest, action: "rotate",
    subject_key_digest: sha("not-current"), replacement_key_digest: sha("root-b") });
  assert.throws(() => verifyRootTrustChainAgainstDigest(
    [establish, badRotate], establish.custodian_set_digest, REVIEWED_KEYS, 2), /custodian_set_unreviewed|transition_invalid/);
  const tampered = event();
  tampered.custodian_attestations[0].signature = Buffer.alloc(64).toString("base64");
  assert.throws(() => verifyRootTrustChainAgainstDigest(
    [tampered], tampered.custodian_set_digest, REVIEWED_KEYS, 2), /quorum_invalid|attestation_invalid/);
  const unreviewed = event();
  assert.throws(() => verifyRootTrustChainAgainstDigest(
    [unreviewed], sha("different-custodian-set"), REVIEWED_KEYS, 2), /reviewed_custodian_set_mismatch/);
  assert.throws(() => evaluateRootTrust([unreviewed]), /reviewed_custodian_set_unprovisioned/);
  assert.equal(containsForbiddenRootMaterial({ private_key: "do-not-store" }), true);
  assert.equal(containsForbiddenRootMaterial({ public_key_digest: sha("safe") }), false);
});

test("a reviewed three-member custodian set accepts an actual two-of-three quorum", () => {
  const state = verifyRootTrustChainAgainstDigest([event()], REVIEWED_SET_DIGEST, REVIEWED_KEYS, 2);
  assert.equal(state.chain_state, "valid");
});

test("a signer minority cannot lower the reviewed threshold in a successor event", () => {
  const establish = event({ threshold: 3 }, [0, 1, 2]);
  const lowered = event({ event_no: 2, previous_event_digest: establish.event_digest,
    action: "rotate", subject_key_digest: establish.subject_key_digest,
    replacement_key_digest: sha("root-b"), threshold: 2 });
  assert.throws(() => verifyRootTrustChainAgainstDigest(
    [establish, lowered], REVIEWED_SET_DIGEST, REVIEWED_KEYS, 3), /malformed_or_operational/);
});

test("migration stores public facts only and remains source-only", () => {
  assert.match(migration, /create table ops\.scac_root_trust_key\b/i);
  assert.match(migration, /create table ops\.scac_root_trust_event\b/i);
  assert.match(migration, /octet_length\(public_key_bytes\)\s*=\s*32/i);
  assert.match(migration, /create table ops\.scac_root_custodian_attestation\b/i);
  assert.match(migration, /create table ops\.scac_root_custodian_set_member\b/i);
  assert.match(migration, /before update or delete on ops\.scac_root_trust_/ig);
  assert.equal((migration.match(/before truncate on ops\.scac_root_(?:trust_|custodian_)/ig) || []).length, 4);
  assert.doesNotMatch(migration, /private_key|secret_key|recovery_secret/i);
  assert.match(migration, /production_trust_active boolean not null default false check \(not production_trust_active\)/i);
  assert.doesNotMatch(migration, /^\s*(?:begin|commit)\s*;/im);
});
