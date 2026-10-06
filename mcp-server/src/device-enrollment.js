import { createPublicKey, verify } from "node:crypto";

import { canonicalJson, digest } from "./artifact-trust.js";

const SHA256 = /^sha256:[0-9a-f]{64}$/;
const ED25519_SPKI_PREFIX = Buffer.from("302a300506032b6570032100", "hex");
const RFC3339_UTC = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/;
const FACT_KEYS = ["architecture", "cpu_core_count", "cpu_identifier", "exact_model_identifier",
  "filevault_state", "gpu_core_count", "memory_bytes", "observed_at", "os_build", "os_version",
  "sip_state", "storage_bytes", "virtualization_entitlement"];

export const STUDIO_EXECUTOR_PROFILE = Object.freeze({
  profile_key: "studio-executor",
  availability: "optional_non_blocking",
  required_hardware_facts: Object.freeze([...FACT_KEYS]),
  required_benchmarks: Object.freeze(["thermal_sustained_cpu_gpu", "ssd", "vm_isolation",
    "mlx_inference_context_memory", "concurrent_jobs", "reboot_power_loss", "network_egress",
    "workload_quotas", "failover"]),
  permitted_after_downstream_activation: Object.freeze(["isolated_build_test_vms",
    "governed_model_gateway_mlx_metal_provider", "classification_redaction_indexing_evals",
    "signed_artifact_builds", "clean_room_install_verification", "compute_heavy_routing",
    "provider_egress_sensitive_routing", "warm_ci_cache", "recovery_standby"]),
  hard_boundaries: Object.freeze(["not_source_of_truth", "not_critical_dependency",
    "no_offline_root_signing_authority", "dell_filesystem_admin_stays_dell_local",
    "outage_isolated_from_record_access_dell_and_central_core", "no_claim_before_receipts"]),
});

function exact(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new TypeError(`${label}_malformed`);
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index]))
    throw new TypeError(`${label}_open_or_incomplete`);
}

function bytes(value, length, label) {
  if (typeof value !== "string" || !/^[A-Za-z0-9+/]+={0,2}$/.test(value) || value.length % 4)
    throw new TypeError(`${label}_malformed`);
  const result = Buffer.from(value, "base64");
  if (result.length !== length || result.toString("base64") !== value) throw new TypeError(`${label}_malformed`);
  return result;
}

function canonicalTimestamp(value, label) {
  if (typeof value !== "string" || !RFC3339_UTC.test(value) || !Number.isFinite(Date.parse(value)))
    throw new TypeError(`${label}_malformed`);
  const normalized = new Date(value).toISOString();
  if (normalized !== value && normalized.replace(".000Z", "Z") !== value)
    throw new TypeError(`${label}_malformed`);
  return value;
}

function publicKeyFor(envelope) {
  const raw = bytes(envelope.device_public_key, 32, "device_public_key");
  return { raw, key: createPublicKey({ key: Buffer.concat([ED25519_SPKI_PREFIX, raw]), format: "der", type: "spki" }) };
}

export function deviceFactPayload(envelope) {
  return { schema_version: "scac-device-facts.v1", device_ref: envelope.device_ref,
    sponsor: envelope.sponsor, profile_key: envelope.profile_key, facts: envelope.facts,
    policy_epoch: envelope.policy_epoch, policy_epoch_digest: envelope.policy_epoch_digest };
}

export function verifyDeviceFactEnvelope(envelope) {
  exact(envelope, ["device_ref", "device_key_digest", "device_public_key", "facts", "facts_digest",
    "policy_epoch", "policy_epoch_digest", "profile_key", "signature", "signature_digest", "sponsor"],
  "device_fact_envelope");
  exact(envelope.facts, FACT_KEYS, "device_facts");
  const facts = envelope.facts;
  if (!/^[a-z0-9][a-z0-9._-]{2,127}$/.test(envelope.device_ref || "") ||
      envelope.sponsor !== "joe" || envelope.profile_key !== "studio-executor" ||
      facts.architecture !== "arm64" || !Number.isSafeInteger(facts.cpu_core_count) || facts.cpu_core_count <= 0 ||
      !Number.isSafeInteger(facts.gpu_core_count) || facts.gpu_core_count <= 0 ||
      !Number.isSafeInteger(facts.memory_bytes) || facts.memory_bytes <= 0 ||
      !Number.isSafeInteger(facts.storage_bytes) || facts.storage_bytes <= 0 ||
      facts.filevault_state !== "enabled" || facts.sip_state !== "enabled" ||
      facts.virtualization_entitlement !== true || !Number.isSafeInteger(envelope.policy_epoch) ||
      envelope.policy_epoch <= 0 || !SHA256.test(envelope.policy_epoch_digest || ""))
    throw new Error("device_facts_unsupported_or_incomplete");
  for (const key of ["exact_model_identifier", "cpu_identifier", "os_version", "os_build", "observed_at"])
    if (typeof facts[key] !== "string" || !facts[key].trim() || facts[key].length > 200)
      throw new Error("device_facts_unsupported_or_incomplete");
  canonicalTimestamp(facts.observed_at, "device_facts_observed_at");
  const publicKey = publicKeyFor(envelope);
  const signature = bytes(envelope.signature, 64, "device_signature");
  const factsDigest = digest(deviceFactPayload(envelope));
  if (factsDigest !== envelope.facts_digest || digest(publicKey.raw) !== envelope.device_key_digest ||
      digest(signature) !== envelope.signature_digest) throw new Error("device_fact_digest_mismatch");
  if (!verify(null, Buffer.from(factsDigest), publicKey.key, signature)) throw new Error("device_fact_signature_invalid");
  return Object.freeze({ device_ref: envelope.device_ref, facts_digest: factsDigest,
    enrollment_state: "registered_pending_siep16_pop",
    cryptographic_state: "self_signature_valid_pending_fresh_challenge",
    assurance_state: "benchmarks_required",
    privilege_state: "none", routing_eligible: false, production_enforcement_active: false });
}

export function benchmarkReceiptPayload(receipt) {
  return { schema_version: "scac-device-benchmark.v1", benchmark_kind: receipt.benchmark_kind,
    device_ref: receipt.device_ref, facts_digest: receipt.facts_digest,
    profile_digest: receipt.profile_digest, metrics_digest: receipt.metrics_digest,
    passed: receipt.passed, observed_at: receipt.observed_at };
}

export function evaluateStudioBenchmarks(envelope, receipts) {
  const factsState = verifyDeviceFactEnvelope(envelope);
  if (!Array.isArray(receipts)) throw new TypeError("benchmark_receipts_malformed");
  const byKind = new Map();
  const publicKey = publicKeyFor(envelope).key;
  const profileDigest = deviceProfileDigest();
  for (const receipt of receipts) {
    exact(receipt, ["benchmark_kind", "device_ref", "facts_digest", "metrics_digest", "observed_at",
      "passed", "profile_digest", "receipt_digest", "signature", "signature_digest",
      "signed_payload_digest"], "benchmark_receipt");
    if (!STUDIO_EXECUTOR_PROFILE.required_benchmarks.includes(receipt.benchmark_kind) ||
        receipt.device_ref !== envelope.device_ref || receipt.facts_digest !== factsState.facts_digest ||
        receipt.profile_digest !== profileDigest || !SHA256.test(receipt.metrics_digest || "") ||
        receipt.passed !== true || byKind.has(receipt.benchmark_kind))
      throw new Error("benchmark_receipt_invalid_or_duplicate");
    canonicalTimestamp(receipt.observed_at, "benchmark_observed_at");
    const expected = digest(benchmarkReceiptPayload(receipt));
    const signature = bytes(receipt.signature, 64, "benchmark_signature");
    if (receipt.receipt_digest !== expected || receipt.signed_payload_digest !== expected ||
        digest(signature) !== receipt.signature_digest) throw new Error("benchmark_receipt_digest_mismatch");
    if (!verify(null, Buffer.from(expected), publicKey, signature))
      throw new Error("benchmark_receipt_signature_invalid");
    byKind.set(receipt.benchmark_kind, receipt);
  }
  const missing = STUDIO_EXECUTOR_PROFILE.required_benchmarks.filter(kind => !byKind.has(kind));
  return Object.freeze({ ...factsState, assurance_state: missing.length ? "benchmarks_incomplete" : "complete_non_authorizing",
    missing_benchmarks: Object.freeze(missing), privilege_state: "none_pending_downstream_controls",
    routing_eligible: false, production_enforcement_active: false });
}

export function deviceProfileDigest() {
  return digest(JSON.parse(canonicalJson(STUDIO_EXECUTOR_PROFILE)));
}
