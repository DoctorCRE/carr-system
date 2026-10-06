import { createHash, createPublicKey, verify } from "node:crypto";

const SHA256 = /^sha256:[0-9a-f]{64}$/;
const MEDIA_TYPE = /^[a-z0-9][a-z0-9.+-]*\/[a-z0-9][a-z0-9.+-]{0,126}$/;
const ARTIFACT_KINDS = new Set(["source_bundle","container_image","vm_image","installer",
  "binary","policy_bundle","model_bundle"]);
const ED25519_SPKI_PREFIX = Buffer.from("302a300506032b6570032100", "hex");

export function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") return `{${Object.keys(value).sort()
    .map(key => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  return JSON.stringify(value);
}

export function digest(value) {
  const bytes = typeof value === "string" || Buffer.isBuffer(value) ? value : canonicalJson(value);
  return `sha256:${createHash("sha256").update(bytes).digest("hex")}`;
}

export function artifactManifestPayload(manifest) {
  return {
    schema_version: "scac-artifact-manifest.v1",
    artifact_digest: manifest.artifact_digest,
    artifact_kind: manifest.artifact_kind,
    media_type: manifest.media_type,
    byte_length: manifest.byte_length,
    source_ref: manifest.source_ref,
    source_digest: manifest.source_digest,
    sbom_digest: manifest.sbom_digest ?? `sha256:${"0".repeat(64)}`,
    provenance_digest: manifest.provenance_digest,
    policy_epoch: manifest.policy_epoch,
    policy_epoch_digest: manifest.policy_epoch_digest,
  };
}

export function artifactManifestDigest(manifest) {
  return digest(artifactManifestPayload(manifest));
}

export function transparencyEntryDigest(entry) {
  return digest({
    schema_version: "scac-artifact-transparency.v1",
    entry_no: entry.entry_no,
    previous_entry_digest: entry.previous_entry_digest ?? null,
    manifest_digest: entry.manifest_digest,
    signature_digest: entry.signature_digest,
    statement_digest: entry.statement_digest,
    entry_kind: "artifact_inclusion",
  });
}

function exactObject(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new TypeError(`${label}_malformed`);
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index]))
    throw new TypeError(`${label}_open_or_incomplete`);
}

function rawBytes(value, length, label) {
  if (!Buffer.isBuffer(value) && (typeof value !== "string" || !/^[A-Za-z0-9+/]+={0,2}$/.test(value) || value.length % 4))
    throw new TypeError(`${label}_malformed`);
  const bytes = Buffer.isBuffer(value) ? value : Buffer.from(value, "base64");
  if (!Buffer.isBuffer(value) && bytes.toString("base64") !== value) throw new TypeError(`${label}_malformed`);
  if (bytes.length !== length) throw new TypeError(`${label}_malformed`);
  return bytes;
}

export function verifyArtifactBundle({ manifest, signature, transparency }) {
  exactObject(manifest, ["artifact_digest","artifact_kind","media_type","byte_length","source_ref",
    "source_digest","sbom_digest","provenance_digest","policy_epoch","policy_epoch_digest","manifest_digest"], "manifest");
  exactObject(signature, ["algorithm","manifest_digest","public_key","signature","signature_digest",
    "signer_key_digest","signed_payload_digest","signature_scope"], "signature");
  if (!Array.isArray(transparency) || transparency.length === 0) throw new TypeError("transparency_unavailable");
  if (!ARTIFACT_KINDS.has(manifest.artifact_kind) || !MEDIA_TYPE.test(manifest.media_type || "") ||
      !Number.isSafeInteger(manifest.byte_length) || manifest.byte_length <= 0 ||
      typeof manifest.source_ref !== "string" || !manifest.source_ref.trim() || manifest.source_ref.length > 500 ||
      !Number.isSafeInteger(manifest.policy_epoch) || manifest.policy_epoch <= 0 ||
      (manifest.sbom_digest !== null && !SHA256.test(manifest.sbom_digest || "")))
    throw new TypeError("manifest_value_malformed");
  for (const field of [manifest.artifact_digest,manifest.source_digest,manifest.provenance_digest,
    manifest.policy_epoch_digest,manifest.manifest_digest,signature.manifest_digest,
    signature.signature_digest,signature.signer_key_digest,signature.signed_payload_digest])
    if (!SHA256.test(field || "")) throw new TypeError("digest_malformed");
  const manifestDigest = artifactManifestDigest(manifest);
  if (manifestDigest !== manifest.manifest_digest || signature.manifest_digest !== manifestDigest ||
      signature.signed_payload_digest !== manifestDigest || signature.algorithm !== "ed25519" ||
      signature.signature_scope !== "scac-artifact-manifest.v1")
    throw new Error("artifact_manifest_or_signature_binding_mismatch");
  const publicKey = rawBytes(signature.public_key, 32, "public_key");
  const signatureBytes = rawBytes(signature.signature, 64, "signature");
  if (digest(publicKey) !== signature.signer_key_digest || digest(signatureBytes) !== signature.signature_digest)
    throw new Error("artifact_signature_digest_mismatch");
  const key = createPublicKey({ key: Buffer.concat([ED25519_SPKI_PREFIX, publicKey]), format: "der", type: "spki" });
  if (!verify(null, Buffer.from(manifestDigest, "utf8"), key, signatureBytes))
    throw new Error("artifact_signature_invalid");
  let previous = null;
  transparency.forEach((entry, index) => {
    exactObject(entry, ["entry_no","entry_digest","previous_entry_digest","manifest_digest",
      "signature_digest","statement_digest","entry_kind"], "transparency_entry");
    if (!Number.isSafeInteger(entry.entry_no) || !SHA256.test(entry.entry_digest || "") ||
        (entry.previous_entry_digest !== null && !SHA256.test(entry.previous_entry_digest || "")) ||
        !SHA256.test(entry.manifest_digest || "") || !SHA256.test(entry.signature_digest || "") ||
        !SHA256.test(entry.statement_digest || "") ||
        entry.entry_no !== index + 1 || entry.previous_entry_digest !== previous ||
        entry.entry_kind !== "artifact_inclusion" || transparencyEntryDigest(entry) !== entry.entry_digest)
      throw new Error("artifact_transparency_gap_fork_or_digest_mismatch");
    previous = entry.entry_digest;
  });
  const inclusion = transparency.find(entry => entry.manifest_digest === manifestDigest &&
    entry.signature_digest === signature.signature_digest);
  if (!inclusion) throw new Error("artifact_transparency_inclusion_missing");
  return Object.freeze({
    artifact_digest: manifest.artifact_digest,
    manifest_digest: manifestDigest,
    signature_state: "cryptographically_valid",
    transparency_state: "included_append_only",
    artifact_trust_state: "untrusted_pending_siep14",
    reason_id: "scac.refusal.root_untrusted",
    root_trust_operational: false,
    production_enforcement_active: false,
  });
}
