import assert from "node:assert/strict";
import { createHash, generateKeyPairSync, sign } from "node:crypto";
import fs from "node:fs";
import test from "node:test";

import {
  artifactManifestDigest,
  digest,
  transparencyEntryDigest,
  verifyArtifactBundle,
} from "../src/artifact-trust.js";

const migration = fs.readFileSync(new URL("../../migrations/0456_siep13_artifact_registry.sql", import.meta.url), "utf8");
const sha = value => `sha256:${createHash("sha256").update(value).digest("hex")}`;

function fixture() {
  const { privateKey, publicKey } = generateKeyPairSync("ed25519");
  const publicRaw = publicKey.export({ format: "der", type: "spki" }).subarray(-32);
  const manifest = {
    artifact_digest: sha("artifact-bytes"), artifact_kind: "source_bundle", media_type: "application/gzip",
    byte_length: 14, source_ref: "git:fixture", source_digest: sha("source"), sbom_digest: null,
    provenance_digest: sha("provenance"), policy_epoch: 1, policy_epoch_digest: sha("epoch"), manifest_digest: null,
  };
  manifest.manifest_digest = artifactManifestDigest(manifest);
  const signatureBytes = sign(null, Buffer.from(manifest.manifest_digest, "utf8"), privateKey);
  const signature = {
    algorithm: "ed25519", manifest_digest: manifest.manifest_digest,
    public_key: publicRaw.toString("base64"), signature: signatureBytes.toString("base64"),
    signature_digest: digest(signatureBytes), signer_key_digest: digest(publicRaw),
    signed_payload_digest: manifest.manifest_digest, signature_scope: "scac-artifact-manifest.v1",
  };
  const entry = {
    entry_no: 1, entry_digest: null, previous_entry_digest: null, manifest_digest: manifest.manifest_digest,
    signature_digest: signature.signature_digest, statement_digest: sha("statement"), entry_kind: "artifact_inclusion",
  };
  entry.entry_digest = transparencyEntryDigest(entry);
  return { manifest, signature, transparency: [entry] };
}

test("valid Ed25519 material and exact inclusion remain explicitly untrusted before SIEP-14", () => {
  const state = verifyArtifactBundle(fixture());
  assert.equal(state.signature_state, "cryptographically_valid");
  assert.equal(state.transparency_state, "included_append_only");
  assert.equal(state.artifact_trust_state, "untrusted_pending_siep14");
  assert.equal(state.reason_id, "scac.refusal.root_untrusted");
  assert.equal(state.root_trust_operational, false);
  assert.equal(state.production_enforcement_active, false);
});

test("tampered signatures, open manifests, and transparency forks fail closed", () => {
  const signed = fixture();
  assert.throws(() => verifyArtifactBundle({ ...signed, signature: { ...signed.signature,
    signature: Buffer.alloc(64).toString("base64"), signature_digest: digest(Buffer.alloc(64)) } }),
    /artifact_signature_invalid/);
  assert.throws(() => verifyArtifactBundle({ ...signed, manifest: { ...signed.manifest, caller_trust: true } }),
    /manifest_open_or_incomplete/);
  const fork = { ...signed.transparency[0], entry_no: 2, previous_entry_digest: null };
  fork.entry_digest = transparencyEntryDigest(fork);
  assert.throws(() => verifyArtifactBundle({ ...signed, transparency: [fork] }),
    /artifact_transparency_gap_fork_or_digest_mismatch/);
});

test("typed verifier rejects every manifest value that the SQL contract rejects", () => {
  for (const change of [
    { byte_length: 0 }, { byte_length: "14" }, { artifact_kind: "unknown" },
    { media_type: "not a media type" }, { source_ref: "" }, { source_ref: "x".repeat(501) },
    { sbom_digest: "garbage" }, { policy_epoch: 0 },
  ]) {
    const signed = fixture();
    assert.throws(() => verifyArtifactBundle({ ...signed, manifest: { ...signed.manifest, ...change } }),
      /manifest_value_malformed/);
  }
});

test("every transparency digest field is typed before chain acceptance", () => {
  for (const field of ["entry_digest","manifest_digest","signature_digest","statement_digest"]) {
    const signed = fixture();
    assert.throws(() => verifyArtifactBundle({ ...signed,
      transparency: [{ ...signed.transparency[0], [field]: "not-a-digest" }] }),
      /artifact_transparency_gap_fork_or_digest_mismatch|digest_malformed/);
  }
});

test("migration keeps three physical stores append-only, owner-only, and non-operational", () => {
  assert.match(migration, /create table ops\.scac_artifact_manifest\b/i);
  assert.match(migration, /create table ops\.scac_artifact_signature\b/i);
  assert.match(migration, /create table ops\.scac_artifact_transparency_entry\b/i);
  assert.match(migration, /octet_length\(public_key_bytes\) = 32/i);
  assert.match(migration, /octet_length\(signature_bytes\) = 64/i);
  assert.match(migration, /transparency chain gap or fork/i);
  assert.match(migration, /before update or delete on ops\.scac_artifact_/ig);
  assert.equal((migration.match(/before truncate on ops\.scac_artifact_/ig) || []).length, 3);
  assert.doesNotMatch(migration, /grant (?:select|insert|update|delete|execute).*carr_(?:reader|writer|jobs|authority)/i);
  assert.match(migration, /root_trust_operational[^\n]*false check \(not root_trust_operational\)/i);
  assert.match(migration, /production_enforcement_active[^\n]*false check \(not production_enforcement_active\)/i);
  assert.doesNotMatch(migration, /^\s*(?:begin|commit)\s*;/im);
});
