import assert from "node:assert/strict";
import { generateKeyPairSync, sign } from "node:crypto";
import fs from "node:fs";
import test from "node:test";

import { digest } from "../src/artifact-trust.js";
import { benchmarkReceiptPayload, deviceFactPayload, deviceProfileDigest,
  evaluateStudioBenchmarks, STUDIO_EXECUTOR_PROFILE,
  verifyDeviceFactEnvelope } from "../src/device-enrollment.js";

const migration = fs.readFileSync(new URL("../../migrations/0460_siep15_device_enrollment.sql", import.meta.url), "utf8");

function fixture() {
  const { privateKey, publicKey } = generateKeyPairSync("ed25519");
  const raw = publicKey.export({ format: "der", type: "spki" }).subarray(-32);
  const envelope = { device_ref: "joe-studio-pending", sponsor: "joe", profile_key: "studio-executor",
    device_key_digest: digest(raw), device_public_key: raw.toString("base64"), policy_epoch: 1,
    policy_epoch_digest: digest("epoch"), facts: { exact_model_identifier: "Mac-model-discovered",
      cpu_identifier: "arm64-cpu-discovered", cpu_core_count: 16, gpu_core_count: 40,
      memory_bytes: 64 * 1024 ** 3, storage_bytes: 2 * 1024 ** 4, architecture: "arm64",
      os_version: "discovered", os_build: "discovered", filevault_state: "enabled",
      sip_state: "enabled", virtualization_entitlement: true, observed_at: "2026-08-26T00:00:00Z" },
    facts_digest: null, signature: null, signature_digest: null };
  envelope.facts_digest = digest(deviceFactPayload(envelope));
  const signature = sign(null, Buffer.from(envelope.facts_digest), privateKey);
  envelope.signature = signature.toString("base64"); envelope.signature_digest = digest(signature);
  return { envelope, privateKey };
}

function benchmarks(envelope, privateKey) {
  return STUDIO_EXECUTOR_PROFILE.required_benchmarks.map(kind => {
    const receipt = { benchmark_kind: kind, device_ref: envelope.device_ref,
      facts_digest: envelope.facts_digest, profile_digest: deviceProfileDigest(),
      metrics_digest: digest(`metrics:${kind}`), passed: true, observed_at: "2026-08-26T01:00:00Z",
      receipt_digest: null, signed_payload_digest: null, signature: null, signature_digest: null };
    receipt.receipt_digest = digest(benchmarkReceiptPayload(receipt));
    receipt.signed_payload_digest = receipt.receipt_digest;
    const signature = sign(null, Buffer.from(receipt.receipt_digest), privateKey);
    receipt.signature = signature.toString("base64");
    receipt.signature_digest = digest(signature);
    return receipt;
  });
}

test("signed discovered facts remain pending PoP with no privileges", () => {
  const state = verifyDeviceFactEnvelope(fixture().envelope);
  assert.equal(state.enrollment_state, "registered_pending_siep16_pop");
  assert.equal(state.privilege_state, "none");
  assert.equal(state.routing_eligible, false);
});

test("all Studio benchmarks are required but still non-authorizing", () => {
  const { envelope, privateKey } = fixture();
  const incomplete = evaluateStudioBenchmarks(envelope, benchmarks(envelope, privateKey).slice(0, -1));
  assert.equal(incomplete.assurance_state, "benchmarks_incomplete");
  const complete = evaluateStudioBenchmarks(envelope, benchmarks(envelope, privateKey));
  assert.equal(complete.assurance_state, "complete_non_authorizing");
  assert.equal(complete.routing_eligible, false);
  assert.equal(complete.production_enforcement_active, false);
});

test("unsupported security posture, chip marketing claims, tampering, and duplicate receipts fail closed", () => {
  const { envelope, privateKey } = fixture();
  assert.throws(() => verifyDeviceFactEnvelope({ ...envelope, facts: { ...envelope.facts, sip_state: "disabled" } }), /unsupported/);
  assert.throws(() => verifyDeviceFactEnvelope({ ...envelope, facts: { ...envelope.facts, marketing_chip_name: "M5 Max" } }), /open_or_incomplete/);
  assert.throws(() => verifyDeviceFactEnvelope({ ...envelope, signature: Buffer.alloc(64).toString("base64"), signature_digest: digest(Buffer.alloc(64)) }), /signature_invalid/);
  assert.throws(() => verifyDeviceFactEnvelope({ ...envelope, sponsor: "dell" }), /unsupported/);
  assert.throws(() => verifyDeviceFactEnvelope({ ...envelope, facts: { ...envelope.facts, observed_at: "not-a-date" } }), /malformed/);
  assert.throws(() => verifyDeviceFactEnvelope({ ...envelope, facts: { ...envelope.facts, observed_at: "2026-02-30T00:00:00Z" } }), /malformed/);
  const receipts = benchmarks(envelope, privateKey); receipts.push(receipts[0]);
  assert.throws(() => evaluateStudioBenchmarks(envelope, receipts), /invalid_or_duplicate/);
  const tampered = benchmarks(envelope, privateKey);
  tampered[0] = { ...tampered[0], signature: Buffer.alloc(64).toString("base64"),
    signature_digest: digest(Buffer.alloc(64)) };
  assert.throws(() => evaluateStudioBenchmarks(envelope, tampered), /signature_invalid/);
});

test("profile and migration preserve optional Studio and Production fences", () => {
  assert.equal(STUDIO_EXECUTOR_PROFILE.availability, "optional_non_blocking");
  assert.ok(STUDIO_EXECUTOR_PROFILE.hard_boundaries.includes("dell_filesystem_admin_stays_dell_local"));
  assert.doesNotMatch(JSON.stringify(STUDIO_EXECUTOR_PROFILE), /M[0-9]+ (?:Pro|Max|Ultra)/);
  assert.match(migration, /registered_pending_siep16_pop/);
  assert.match(migration, new RegExp(deviceProfileDigest()));
  assert.match(migration,
    /'cpu_core_count',\(p_facts->'cpu_core_count'\)::numeric::bigint[\s\S]*'storage_bytes',\(p_facts->'storage_bytes'\)::numeric::bigint/);
  assert.match(migration, /session_user<>'carr_authority_joe'/);
  assert.match(migration, /grant execute on function ops\.scac_device_enrollment_status\(text\)\s+to carr_authority;/i);
  assert.doesNotMatch(migration, /grant execute on function ops\.scac_device_enrollment_status\(text\)[\s\S]{0,100}carr_(?:reader|writer|jobs)/i);
  assert.match(migration, /routing_eligible boolean not null default false check \(not routing_eligible\)/i);
  assert.match(migration, /production_enforcement_active boolean not null default false check \(not production_enforcement_active\)/i);
  assert.doesNotMatch(migration, /^\s*(?:begin|commit)\s*;/im);
});
