import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import {
  evaluateRightsReceipt,
  publicValueIsSafe,
  validateEvidenceRightsLineage,
  validateProjectionFact,
} from "../src/tour-operations-contract.js";

const root = path.resolve(import.meta.dirname, "../..");
const fixture = JSON.parse(fs.readFileSync(path.join(root, "mcp-server/test/fixtures/tour-operations-foundation.v1.json"), "utf8"));
const at = fixture.projection.as_of;
const rightsRequest = { at, fieldKey: "display.name", useClass: "client_public_display", lineage: [], revocations: [] };
const accepts = result => assert.ok(result, "valid rights/provenance input must be accepted");
const shadowedArray = (values, method, result) => {
  const array = [...values];
  Object.defineProperty(array, method, { value: () => result, enumerable: false });
  return array;
};
const successorId = "10000000-0000-4000-8000-000000000002";
const conflictingId = "10000000-0000-4000-8000-000000000003";
const otherProviderId = "10000000-0000-4000-8000-000000000004";

test("rights are current only when effective, unexpired, unrevoked, and explicitly allowed", () => {
  accepts(evaluateRightsReceipt(fixture.rights_receipt, rightsRequest));
  assert.throws(() => evaluateRightsReceipt({ ...fixture.rights_receipt, effective_at: "2026-08-26T00:00:00Z" }, rightsRequest), /PUBLIC_RIGHTS_REQUIRED|RIGHTS_(?:NOT_)?YET_EFFECTIVE|NOT_EFFECTIVE/);
  assert.throws(() => evaluateRightsReceipt({ ...fixture.rights_receipt, expires_at: "2026-08-25T12:00:00Z" }, rightsRequest), /PUBLIC_RIGHTS_REQUIRED|RIGHTS_EXPIRED|EXPIRED/);
  assert.throws(() => evaluateRightsReceipt({ ...fixture.rights_receipt, status: "revoked", revoked_at: "2026-08-24T00:00:00Z" }, rightsRequest), /PUBLIC_RIGHTS_REQUIRED|RIGHTS_REVOKED|REVOKED/);
  assert.throws(() => evaluateRightsReceipt({ ...fixture.rights_receipt, status: "unknown" }, rightsRequest), /RIGHTS_UNKNOWN/);
  assert.throws(() => evaluateRightsReceipt({ ...fixture.rights_receipt, intended_use: "" }, rightsRequest), /RIGHTS_UNKNOWN/);
  assert.throws(() => evaluateRightsReceipt(fixture.rights_receipt, { ...rightsRequest, useClass: "internal_export" }), /PUBLIC_RIGHTS_REQUIRED|RIGHTS_USE_NOT_ALLOWED|USE_NOT_ALLOWED/);
  assert.throws(() => evaluateRightsReceipt(fixture.rights_receipt, { ...rightsRequest, fieldKey: "internal_note" }), /PUBLIC_RIGHTS_REQUIRED|RIGHTS_FIELD_NOT_ALLOWED|FIELD_NOT_ALLOWED/);
  assert.throws(() => evaluateRightsReceipt(fixture.rights_receipt, {
    ...rightsRequest,
    lineage: [{ ...fixture.rights_receipt, id: successorId, receipt_version: 2, effective_at: "2026-08-20T00:00:00Z", supersedes_receipt_id: fixture.rights_receipt.id }],
  }), /PUBLIC_RIGHTS_SUPERSEDED|RIGHTS_SUPERSEDED|SUPERSEDED/);
  assert.throws(() => evaluateRightsReceipt(fixture.rights_receipt, {
    ...rightsRequest,
    lineage: [{ ...fixture.rights_receipt, id: conflictingId, receipt_digest: `sha256:${"b".repeat(64)}` }],
  }), /RIGHTS_CONFLICT/);
  accepts(evaluateRightsReceipt(fixture.rights_receipt, {
    ...rightsRequest,
    lineage: [{ ...fixture.rights_receipt }],
  }));
  for (const divergent of [
    { receipt_digest: `sha256:${"c".repeat(64)}` },
    { allowed_field_classes: ["display.name"] },
    { provider: "divergent-provider" },
    { receipt_version: 2, supersedes_receipt_id: successorId },
  ]) assert.throws(() => evaluateRightsReceipt(fixture.rights_receipt, {
    ...rightsRequest,
    lineage: [{ ...fixture.rights_receipt, ...divergent }],
  }), /RIGHTS_CONFLICT/);
  accepts(evaluateRightsReceipt(fixture.rights_receipt, {
    ...rightsRequest,
    lineage: [{ ...fixture.rights_receipt, id: otherProviderId, provider: "other-provider", receipt_version: 2, effective_at: "2026-08-20T00:00:00Z", supersedes_receipt_id: fixture.rights_receipt.id }],
  }));
  for (const field of ["receipt_digest", "terms_url", "reviewed_at", "reviewer"] ) {
    const incomplete = { ...fixture.rights_receipt };
    delete incomplete[field];
    assert.throws(() => evaluateRightsReceipt(incomplete, rightsRequest), /RIGHTS_UNKNOWN/, field);
  }
  for (const field of ["provider", "policy_key"]) {
    const incompleteSuccessor = {
      ...fixture.rights_receipt,
      id: successorId,
      receipt_version: 2,
      supersedes_receipt_id: fixture.rights_receipt.id,
      effective_at: "2026-08-20T00:00:00Z",
    };
    delete incompleteSuccessor[field];
    assert.throws(() => evaluateRightsReceipt(fixture.rights_receipt, {
      ...rightsRequest, lineage: [incompleteSuccessor],
    }), /RIGHTS_UNKNOWN/, field);
  }
  for (const [field, value] of [
    ["organization_tenant_id", "tenant\u0000other"],
    ["provider", "provider\ud800"],
    ["policy_key", "policy\u0000other"],
    ["reviewer", "reviewer\udc00"],
    ["intended_use", "client\u0000display"],
    ["allowed_field_classes", ["display.name\u0000internal"]],
    ["allowed_use_classes", ["client_public_display\ud800"]],
  ]) assert.throws(() => evaluateRightsReceipt({ ...fixture.rights_receipt, [field]: value },
    rightsRequest), /RIGHTS_UNKNOWN/, field);
  for (const field of ["expires_at", "revoked_at"])
    assert.throws(() => evaluateRightsReceipt({ ...fixture.rights_receipt, [field]: undefined },
      rightsRequest), /RIGHTS_UNKNOWN/, `${field} must be explicit null or a timestamp`);
});

test("rights evaluation rejects caller-controlled array methods and malformed temporal authority", () => {
  const divergent = { ...fixture.rights_receipt, receipt_digest: `sha256:${"d".repeat(64)}` };
  const poisonedPrototype = ["client_public_display"];
  Object.setPrototypeOf(poisonedPrototype, Object.create(Array.prototype));
  for (const receipt of [
    { ...fixture.rights_receipt, allowed_use_classes: shadowedArray([], "includes", true) },
    { ...fixture.rights_receipt, allowed_field_classes: shadowedArray([], "includes", true) },
    { ...fixture.rights_receipt, allowed_use_classes: poisonedPrototype },
    { ...fixture.rights_receipt, expires_at: "not-a-time" },
    { ...fixture.rights_receipt, revoked_at: "not-a-time" },
    { ...fixture.rights_receipt, reviewed_at: "not-a-time" },
  ]) assert.throws(() => evaluateRightsReceipt(receipt, rightsRequest),
    /RIGHTS_UNKNOWN|RIGHTS_USE_NOT_ALLOWED|RIGHTS_FIELD_NOT_ALLOWED/);
  assert.throws(() => evaluateRightsReceipt(fixture.rights_receipt, {
    ...rightsRequest, lineage: shadowedArray([divergent], "filter", []),
  }), /RIGHTS_UNKNOWN|RIGHTS_CONFLICT/);
  for (const lineageTimestamp of ["effective_at", "expires_at", "revoked_at", "reviewed_at"])
    assert.throws(() => evaluateRightsReceipt(fixture.rights_receipt, {
      ...rightsRequest,
      lineage: [{ ...fixture.rights_receipt, id: successorId, receipt_version: 2,
        supersedes_receipt_id: fixture.rights_receipt.id, [lineageTimestamp]: "not-a-time" }],
    }), /RIGHTS_UNKNOWN|RIGHTS_CONFLICT/);
  assert.throws(() => evaluateRightsReceipt(fixture.rights_receipt, {
    ...rightsRequest,
    revocations: shadowedArray([{ rights_receipt_id: fixture.rights_receipt.id,
      revoked_at: "2026-08-24T00:00:00Z" }], "some", false),
  }), /RIGHTS_UNKNOWN|RIGHTS_REVOKED/);
  assert.throws(() => evaluateRightsReceipt(fixture.rights_receipt, {
    ...rightsRequest,
    revocations: [{ rights_receipt_id: fixture.rights_receipt.id, revoked_at: "not-a-time" }],
  }), /RIGHTS_UNKNOWN/);

  for (const [record, field] of [
    [{ ...fixture.rights_receipt }, "effective_at"],
    [{ ...fixture.rights_receipt, id: successorId, receipt_version: 2,
      supersedes_receipt_id: fixture.rights_receipt.id }, "effective_at"],
    [{ rights_receipt_id: fixture.rights_receipt.id }, "revoked_at"],
  ]) {
    delete record[field];
    let reads = 0;
    Object.defineProperty(record, field, {
      enumerable: true,
      get() { return reads++ === 0 ? "2026-08-26T00:00:00Z" : "2026-08-24T00:00:00Z"; },
    });
    const request = record.rights_receipt_id
      ? { ...rightsRequest, revocations: [record] }
      : record.id === successorId
        ? { ...rightsRequest, lineage: [record] }
        : rightsRequest;
    assert.throws(() => evaluateRightsReceipt(
      record.id === successorId || record.rights_receipt_id ? fixture.rights_receipt : record,
      request,
    ), /RIGHTS_UNKNOWN/);
    assert.equal(reads, 0, `${field} accessor must not execute`);
  }
});

test("evidence-to-assertion rights lineage binds tenant, receipt, provider, policy, and separate observation/retrieval times", () => {
  accepts(validateEvidenceRightsLineage(fixture.source_evidence, fixture.field_assertion, fixture.rights_receipt));
  assert.throws(() => validateEvidenceRightsLineage({ ...fixture.source_evidence, rights_receipt_id: "wrong" }, fixture.field_assertion, fixture.rights_receipt), /EVIDENCE_RIGHTS|PROVENANCE|RIGHTS/);
  assert.throws(() => validateEvidenceRightsLineage({ ...fixture.source_evidence, rights_provider: "other-provider" }, fixture.field_assertion, fixture.rights_receipt), /EVIDENCE_RIGHTS|PROVENANCE|PROVIDER/);
  assert.throws(() => validateEvidenceRightsLineage({ ...fixture.source_evidence, rights_policy_key: "other-policy" }, fixture.field_assertion, fixture.rights_receipt), /EVIDENCE_RIGHTS|PROVENANCE|POLICY/);
  assert.throws(() => validateEvidenceRightsLineage(fixture.source_evidence, { ...fixture.field_assertion, rights_receipt_id: "wrong" }, fixture.rights_receipt), /ASSERTION_RIGHTS|PROVENANCE|RIGHTS/);
  assert.throws(() => validateEvidenceRightsLineage(fixture.source_evidence, { ...fixture.field_assertion, observed_at: "not-a-time" }, fixture.rights_receipt), /OBSERVED|EFFECTIVE|RIGHTS/);
  assert.throws(() => validateEvidenceRightsLineage({ ...fixture.source_evidence, retrieved_at: "not-a-time" }, fixture.field_assertion, fixture.rights_receipt), /RETRIEVED|EFFECTIVE|RIGHTS/);
  assert.throws(() => validateEvidenceRightsLineage({ ...fixture.source_evidence, retrieval_status: "partial" }, fixture.field_assertion, fixture.rights_receipt), /EVIDENCE_UNRESOLVED/);
  assert.throws(() => validateEvidenceRightsLineage({ ...fixture.source_evidence, rights_provider: undefined }, fixture.field_assertion, fixture.rights_receipt), /EVIDENCE_RIGHTS_LINEAGE_MISMATCH/);
  assert.throws(() => validateEvidenceRightsLineage({ ...fixture.source_evidence, rights_policy_key: undefined }, fixture.field_assertion, fixture.rights_receipt), /EVIDENCE_RIGHTS_LINEAGE_MISMATCH/);
  assert.notEqual(fixture.field_assertion.observed_at, fixture.source_evidence.retrieved_at, "observation and retrieval timestamps must remain distinct provenance facts");
});

test("public assets use opaque public references and never arbitrary URLs", () => {
  assert.equal(publicValueIsSafe("photos", [{ asset_ref: "asset:public:photo-0000000001", alt: "Front", source: "county-records" }]), true);
  assert.equal(publicValueIsSafe("photos", [{ asset_ref: "asset:public:cGhvdG8tMQ0000000", alt: "Front", source: "county-records" }]), true);
  for (const url of ["https://evil.invalid/photo.jpg", "http://provider.invalid/photo.jpg", "javascript:alert(1)", "asset:private:photo-1"]) {
    assert.equal(publicValueIsSafe("photos", [{ asset_ref: url, alt: "unsafe", source: "untrusted" }]), false, url);
  }
  let reads = 0;
  const changingAsset = {};
  Object.defineProperty(changingAsset, "asset_ref", {
    enumerable: true,
    get() { return reads++ === 0 ? "asset:public:photo-0000000001" : "javascript:alert(1)"; },
  });
  assert.equal(publicValueIsSafe("photos", [changingAsset]), false);
  assert.equal(reads, 0, "asset accessors must never execute");
});

test("public metrics match the deterministic packet renderer contract", () => {
  for (const value of [
    { value: 4200, unit: "SF" },
    { min: 18, max: 24, currency: "USD", period: "SF/YR" },
    { value: "Call for pricing", label: "Asking economics" },
  ]) assert.equal(publicValueIsSafe("size", value), true, JSON.stringify(value));
  for (const value of [
    {}, { value: null }, { value: true }, { unit: "SF" },
    { value: "" }, { min: 25, max: 20 }, { value: 4200, unit: null },
  ]) assert.equal(publicValueIsSafe("size", value), false, JSON.stringify(value));
  const magicKey = { value: 4200, unit: "SF" };
  Object.defineProperty(magicKey, "__proto__", {
    enumerable: true,
    value: { notes: "broker-only", credentials: "secret" },
  });
  assert.equal(publicValueIsSafe("size", magicKey), false,
    "magic property names must remain visible to the allowlist");
});

test("required public display identity rejects blank or oversized text", () => {
  assert.equal(publicValueIsSafe("display.name", "Medical Plaza"), true);
  assert.equal(publicValueIsSafe("display.address", "1 Synthetic Way"), true);
  for (const value of ["", "   ", "A".repeat(361)]) {
    assert.equal(publicValueIsSafe("display.name", value), false, JSON.stringify(value));
    assert.equal(publicValueIsSafe("display.address", value), false, JSON.stringify(value));
  }
});

test("projection fact validation requires exact evidence and current public-display rights", () => {
  const { projection_fact: fact, field_assertion: assertion, membership, projection, rights_receipt: rights } = fixture;
  const evidence = fixture.source_evidence;
  assert.equal(validateProjectionFact(fact, assertion, membership, projection, rights, { evidence }), true);
  assert.throws(() => validateProjectionFact(fact, assertion, membership, projection, rights), /PROJECTION_EVIDENCE_REQUIRED/);
  assert.throws(() => validateProjectionFact(fact, assertion, membership, projection, { ...rights, effective_at: "2026-08-26T00:00:00Z" }, { evidence }), /PUBLIC_RIGHTS_REQUIRED/);
  assert.throws(() => validateProjectionFact(fact, assertion, membership, projection, { ...rights, status: "revoked", revoked_at: "2026-08-24T00:00:00Z" }, { evidence }), /PUBLIC_RIGHTS_REQUIRED/);
  assert.throws(() => validateProjectionFact(fact, assertion, membership, projection, rights, { evidence, lineage: [{ ...rights, id: successorId, receipt_version: 2, effective_at: "2026-08-20T00:00:00Z", supersedes_receipt_id: rights.id }] }), /PUBLIC_RIGHTS_SUPERSEDED/);
  assert.throws(() => validateProjectionFact(fact, { ...assertion, observed_at: "2026-08-26T00:00:00Z" }, membership, projection, rights, { evidence }), /PUBLIC_ASSERTION_NOT_EFFECTIVE/);
  assert.throws(() => validateProjectionFact(fact, assertion, membership, projection, rights, { evidence: { ...evidence, retrieved_at: "2026-08-26T00:00:00Z" } }), /PUBLIC_ASSERTION_NOT_EFFECTIVE/);
  for (const confidence of [undefined, null, "", "unknown", "certain", 1])
    assert.throws(() => validateProjectionFact(fact, { ...assertion, confidence }, membership,
      projection, rights, { evidence }), /PUBLIC_ASSERTION_UNRESOLVED/);
  assert.throws(() => validateProjectionFact(fact, { ...assertion, effective_to: undefined },
    membership, projection, rights, { evidence }), /PUBLIC_ASSERTION_NOT_EFFECTIVE/,
  "required nullable authority must be explicit null or a timestamp");

  const originalIterator = Array.prototype[Symbol.iterator];
  let iteratorError;
  try {
    Array.prototype[Symbol.iterator] = function* () {};
    validateProjectionFact(fact, { ...assertion, organization_tenant_id: "other-tenant" },
      membership, projection, { ...rights, organization_tenant_id: "other-tenant" },
      { evidence: { ...evidence, organization_tenant_id: "other-tenant" } });
  } catch (error) {
    iteratorError = error;
  } finally {
    Array.prototype[Symbol.iterator] = originalIterator;
  }
  assert.match(iteratorError?.message || "", /TENANT_SCOPE_REFUSED/);
});
