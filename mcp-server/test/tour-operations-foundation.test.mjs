import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import {
  CANONICAL_FACT_REQUIRED_FIELDS,
  validateCanonicalFieldAssertion,
  validateFoundationEntityFixture,
  validateProjectionFact,
} from "../src/tour-operations-contract.js";

const root = path.resolve(import.meta.dirname, "../..");
const read = file => fs.readFileSync(path.join(root, file), "utf8");
const contract = JSON.parse(read("workspace/contracts/tour-operations-foundation.v1.json"));
const fixture = JSON.parse(read("mcp-server/test/fixtures/tour-operations-foundation.v1.json"));
const migration = read("migrations/0318_tour_operations_foundation.sql");
const rightsHardeningMigration = read("migrations/0427_tour_rights_projection_hardening.sql");

test("foundation contract preserves provenance, conflicts, audit, rights versions and tenant integrity", () => {
  assert.equal(contract.version, "1.8.0");
  for (const field of ["organization_tenant_id", "property_id", "field_key", "source_evidence_id", "observed_at", "effective_from", "rights_receipt_id", "confidence", "data_classification"]) assert(contract.canonical_record_policy.required_fact_metadata.includes(field), field);
  for (const entity of ["FactConflict", "AuditEvent", "TourPropertyMembership", "ProjectionFact"]) assert.ok(contract.entities[entity], entity);
  assert.match(contract.entities.RightsReceipt.rule, /immutable versioned.*fail closed/i);
  assert.match(contract.canonical_record_policy.tenant_integrity, /tenant-qualified/i);
  assert.match(contract.entities.Property.rule, /identity-only/i);
});

test("foundation fixture covers every accepted entity with schema-aligned shapes and enums", () => {
  assert.deepEqual(Object.keys(fixture.contract_entities).sort(), Object.keys(contract.entities).sort());
  for (const [entityName, entityContract] of Object.entries(contract.entities)) {
    const record = fixture.contract_entities[entityName];
    assert.equal(validateFoundationEntityFixture(entityName, record, entityContract), true, entityName);
  }
  assert.match(rightsHardeningMigration, /add column if not exists rights_provider text,[\s\S]*add column if not exists rights_policy_key text/i);
  assert.deepEqual(contract.entities.SourceEvidence.required.slice(-3),
    ["rights_provider", "rights_policy_key", "data_classification"]);
});

test("foundation fixture validation rejects incomplete, unknown, and schema-invalid records", () => {
  const cases = [
    ["SourceEvidence", "rights_provider", undefined, /FOUNDATION_ENTITY_FIELD_REQUIRED:SourceEvidence\.rights_provider/],
    ["ProjectionFact", "route_version", undefined, /FOUNDATION_ENTITY_FIELD_REQUIRED:ProjectionFact\.route_version/],
    ["ShareGrant", "audience", undefined, /FOUNDATION_ENTITY_FIELD_REQUIRED:ShareGrant\.audience/],
    ["QualityFinding", "artifact_type", "tour_projection", /FOUNDATION_ENTITY_ENUM_INVALID:QualityFinding\.artifact_type/],
    ["QualityFinding", "severity", "blocking", /FOUNDATION_ENTITY_ENUM_INVALID:QualityFinding\.severity/],
    ["AuditEvent", "payload", undefined, /FOUNDATION_ENTITY_FIELD_REQUIRED:AuditEvent\.payload/],
  ];
  for (const [entityName, field, value, expected] of cases) {
    const invalid = structuredClone(fixture.contract_entities[entityName]);
    if (value === undefined) delete invalid[field]; else invalid[field] = value;
    assert.throws(() => validateFoundationEntityFixture(entityName, invalid, contract.entities[entityName]), expected);
  }
  assert.throws(() => validateFoundationEntityFixture("Property",
    {...fixture.contract_entities.Property, display_name: "not canonical"}, contract.entities.Property),
  /FOUNDATION_ENTITY_FIELD_UNKNOWN:Property\.display_name/);
  assert.throws(() => validateFoundationEntityFixture("ShareGrant",
    {...fixture.contract_entities.ShareGrant, permission_scopes: ["comment"]}, contract.entities.ShareGrant),
  /FOUNDATION_ENTITY_SCOPE_INVALID:ShareGrant\.permission_scopes/);
  assert.throws(() => validateFoundationEntityFixture("ProjectionFact",
    {...fixture.contract_entities.ProjectionFact, display_field_key: "internal_note"}, contract.entities.ProjectionFact),
  /FOUNDATION_ENTITY_ENUM_INVALID:ProjectionFact\.display_field_key/);
  assert.throws(() => validateFoundationEntityFixture("Property",
    {...fixture.contract_entities.Property, property_id: 7}, contract.entities.Property),
  /FOUNDATION_ENTITY_UUID_INVALID:Property\.property_id/);
  assert.throws(() => validateFoundationEntityFixture("Property",
    {...fixture.contract_entities.Property, organization_tenant_id: ""}, contract.entities.Property),
  /FOUNDATION_ENTITY_TEXT_INVALID:Property\.organization_tenant_id/);
  assert.throws(() => validateFoundationEntityFixture("Tour",
    {...fixture.contract_entities.Tour, tour_name: {text: "invalid"}}, contract.entities.Tour),
  /FOUNDATION_ENTITY_TEXT_INVALID:Tour\.tour_name/);
  assert.throws(() => validateFoundationEntityFixture("AuditEvent",
    {...fixture.contract_entities.AuditEvent, entity_id: []}, contract.entities.AuditEvent),
  /FOUNDATION_ENTITY_UUID_INVALID:AuditEvent\.entity_id/);
  assert.throws(() => validateFoundationEntityFixture("RightsReceipt",
    {...fixture.contract_entities.RightsReceipt, allowed_use_classes: ["   "]}, contract.entities.RightsReceipt),
  /FOUNDATION_ENTITY_ARRAY_INVALID:RightsReceipt\.allowed_use_classes/);
  assert.throws(() => validateFoundationEntityFixture("RightsReceipt",
    {...fixture.contract_entities.RightsReceipt, allowed_use_classes: new Array(1)}, contract.entities.RightsReceipt),
  /FOUNDATION_ENTITY_ARRAY_INVALID:RightsReceipt\.allowed_use_classes/);
  const accessorScopes = [];
  Object.defineProperty(accessorScopes, "0", {enumerable: true, get: () => "view_packet"});
  assert.throws(() => validateFoundationEntityFixture("ShareGrant",
    {...fixture.contract_entities.ShareGrant, permission_scopes: accessorScopes}, contract.entities.ShareGrant),
  /FOUNDATION_ENTITY_ARRAY_INVALID:ShareGrant\.permission_scopes/);
  assert.throws(() => validateFoundationEntityFixture("AuditEvent",
    {...fixture.contract_entities.AuditEvent, payload: {unsafe: 1n}}, contract.entities.AuditEvent),
  /FOUNDATION_ENTITY_JSON_INVALID:AuditEvent\.payload/);
  assert.throws(() => validateFoundationEntityFixture("AuditEvent",
    {...fixture.contract_entities.AuditEvent,
      event_digest: {toString: () => "sha256:" + "f".repeat(64)}}, contract.entities.AuditEvent),
  /FOUNDATION_ENTITY_DIGEST_INVALID:AuditEvent\.event_digest/);
  const shadowedScopes = ["edit_cheat_sheet"];
  shadowedScopes.some = () => false;
  assert.throws(() => validateFoundationEntityFixture("ShareGrant",
    {...fixture.contract_entities.ShareGrant, permission_scopes: shadowedScopes}, contract.entities.ShareGrant),
  /FOUNDATION_ENTITY_SCOPE_INVALID:ShareGrant\.permission_scopes/);
  const grantWithSerializer = structuredClone(fixture.contract_entities.ShareGrant);
  Object.defineProperty(grantWithSerializer, "toJSON", {value: () => ({corrupted: true})});
  assert.throws(() => validateFoundationEntityFixture("ShareGrant", grantWithSerializer,
    contract.entities.ShareGrant), /FOUNDATION_ENTITY_SERIALIZATION_INVALID:ShareGrant/);
  const scopesWithSerializer = ["view_packet"];
  Object.defineProperty(scopesWithSerializer, "toJSON", {value: () => ["edit_cheat_sheet"]});
  assert.throws(() => validateFoundationEntityFixture("ShareGrant",
    {...fixture.contract_entities.ShareGrant, permission_scopes: scopesWithSerializer},
    contract.entities.ShareGrant), /FOUNDATION_ENTITY_SERIALIZATION_INVALID:ShareGrant/);
  assert.throws(() => validateFoundationEntityFixture("FieldAssertion",
    {...fixture.contract_entities.FieldAssertion, effective_to: "2026-07-31T00:00:00Z"},
    contract.entities.FieldAssertion), /FOUNDATION_ENTITY_INTERVAL_INVALID:FieldAssertion\.effective_to/);
  assert.throws(() => validateFoundationEntityFixture("RightsReceipt",
    {...fixture.contract_entities.RightsReceipt, expires_at: fixture.contract_entities.RightsReceipt.effective_at},
    contract.entities.RightsReceipt), /FOUNDATION_ENTITY_INTERVAL_INVALID:RightsReceipt\.expires_at/);
  assert.throws(() => validateFoundationEntityFixture("RightsReceipt",
    {...fixture.contract_entities.RightsReceipt, status: "revoked", revoked_at: null},
    contract.entities.RightsReceipt), /FOUNDATION_ENTITY_REVOCATION_INVALID:RightsReceipt\.revoked_at/);
  assert.throws(() => validateFoundationEntityFixture("ShareGrant",
    {...fixture.contract_entities.ShareGrant, expires_at: "2026-08-24T12:10:00Z"},
    contract.entities.ShareGrant), /FOUNDATION_ENTITY_INTERVAL_INVALID:ShareGrant\.expires_at/);
  assert.throws(() => validateFoundationEntityFixture("ShareGrant",
    {...fixture.contract_entities.ShareGrant, status: "revoked", revoked_at: null},
    contract.entities.ShareGrant), /FOUNDATION_ENTITY_REVOCATION_INVALID:ShareGrant\.revoked_at/);
});

test("canonical factual fields require provenance, time, rights, confidence and classification", () => {
  const assertion = fixture.field_assertion;
  assert.equal(validateCanonicalFieldAssertion(assertion), true);
  assert.deepEqual(contract.canonical_record_policy.required_fact_metadata,
    ["organization_tenant_id", "property_id", "field_key", "value", "source_evidence_id", "observed_at", "effective_from", "effective_to", "rights_receipt_id", "confidence", "data_classification", "review_state"]);
  for (const field of CANONICAL_FACT_REQUIRED_FIELDS) {
    const invalid = {...assertion};
    delete invalid[field];
    assert.throws(() => validateCanonicalFieldAssertion(invalid),
      new RegExp(`FACT_METADATA_REQUIRED:${field}`), field);
  }
  assert.throws(() => validateCanonicalFieldAssertion({...assertion, observed_at: "not-a-time"}), /FACT_METADATA_INVALID:observed_at/);
  assert.throws(() => validateCanonicalFieldAssertion({...assertion, organization_tenant_id: ""}), /FACT_METADATA_INVALID:organization_tenant_id/);
  for (const organization_tenant_id of ["tenant\u0000bad", "tenant\ud800"])
    assert.throws(() => validateCanonicalFieldAssertion({...assertion, organization_tenant_id}),
      /FACT_METADATA_INVALID:organization_tenant_id/);
  for (const field_key of ["display.name\u0000bad", "display.name\udc00"])
    assert.throws(() => validateCanonicalFieldAssertion({...assertion, field_key}),
      /FACT_METADATA_INVALID:field_key/);
  assert.throws(() => validateCanonicalFieldAssertion({...assertion, property_id: "not-a-uuid"}), /FACT_METADATA_INVALID:property_id/);
  assert.throws(() => validateCanonicalFieldAssertion({...assertion, effective_to: "2026-07-31T00:00:00Z"}), /FACT_EFFECTIVE_INTERVAL_INVALID/);
  assert.throws(() => validateCanonicalFieldAssertion({...assertion, rights_receipt_id: ""}), /FACT_METADATA_INVALID:rights_receipt_id/);
  assert.throws(() => validateCanonicalFieldAssertion({...assertion, confidence: "guessed"}), /FACT_METADATA_INVALID:confidence/);
  assert.throws(() => validateCanonicalFieldAssertion({...assertion, data_classification: "publicish"}), /FACT_METADATA_INVALID:data_classification/);
  for (const value of [null, NaN, Infinity, () => true, Symbol("fact"), 1n, new Date()])
    assert.throws(() => validateCanonicalFieldAssertion({...assertion, value}), /FACT_METADATA_INVALID:value/);
  const arrayWithSerializer = ["safe"];
  Object.defineProperty(arrayWithSerializer, "toJSON", {value: () => 1n});
  const objectWithSerializer = {safe: true};
  Object.defineProperty(objectWithSerializer, "toJSON", {value: () => 1n});
  const objectWithGetter = {};
  Object.defineProperty(objectWithGetter, "unsafe", {enumerable: true, get: () => 1n});
  const objectWithSerializerGetter = {safe: true};
  Object.defineProperty(objectWithSerializerGetter, "toJSON", {get: () => undefined});
  for (const value of [arrayWithSerializer, objectWithSerializer, objectWithGetter,
    objectWithSerializerGetter])
    assert.throws(() => validateCanonicalFieldAssertion({...assertion, value}), /FACT_METADATA_INVALID:value/);
  for (const value of [{nested: "a\u0000b"}, {"bad\u0000key": true}, "\ud800", {"\udc00": true}])
    assert.throws(() => validateCanonicalFieldAssertion({...assertion, value}), /FACT_METADATA_INVALID:value/);
  const assertionWithValueGetter = {...assertion};
  Object.defineProperty(assertionWithValueGetter, "value", {enumerable: true, get: () => "unstable"});
  assert.throws(() => validateCanonicalFieldAssertion(assertionWithValueGetter), /FACT_METADATA_REQUIRED:value/);
  const inheritedSerializer = Object.getOwnPropertyDescriptor(Object.prototype, "toJSON");
  Object.defineProperty(Object.prototype, "toJSON", {configurable: true, value: () => ({corrupted: true})});
  try {
    assert.throws(() => validateCanonicalFieldAssertion({...assertion}), /FACT_ASSERTION_UNSAFE/);
  } finally {
    if (inheritedSerializer) Object.defineProperty(Object.prototype, "toJSON", inheritedSerializer);
    else delete Object.prototype.toJSON;
  }
  assert.equal(validateCanonicalFieldAssertion({...assertion, value: {suite: null}}), true);
  assert.throws(() => validateFoundationEntityFixture("FieldAssertion",
    {...fixture.contract_entities.FieldAssertion, value: null}, contract.entities.FieldAssertion),
  /FOUNDATION_ENTITY_VALUE_REQUIRED:FieldAssertion\.value/);
});

test("normalized projection facts refuse cross-tenant, unreviewed, nonpublic, and route-mismatched data", () => {
  const { projection_fact: fact, field_assertion: assertion, membership, projection,
    rights_receipt: rights, source_evidence: evidence } = fixture;
  assert.equal(validateProjectionFact(fact, assertion, membership, projection, rights, {evidence}), true);
  assert.throws(() => validateProjectionFact({...fact, display_field_key: "internal_note"}, assertion, membership, projection, rights), /PUBLIC_FIELD_NOT_ALLOWLISTED/);
  assert.throws(() => validateProjectionFact({...fact, display_field_key: "display.address"}, assertion, membership, projection, rights), /PUBLIC_FIELD_RELABEL_REFUSED/);
  assert.throws(() => validateProjectionFact({...fact, organization_tenant_id: "other"}, assertion, membership, projection, rights), /TENANT_SCOPE_REFUSED/);
  assert.throws(() => validateProjectionFact(fact, {...assertion, review_state: "unreviewed"}, membership, projection, rights), /PUBLIC_ASSERTION_REQUIRED/);
  assert.throws(() => validateProjectionFact(fact, {...assertion, data_classification: "internal"}, membership, projection, rights), /PUBLIC_ASSERTION_REQUIRED/);
  assert.throws(() => validateProjectionFact(fact, assertion, {...membership, route_version: 2}, projection, rights), /ROUTE_VERSION_MISMATCH/);
  assert.throws(() => validateProjectionFact(fact, {...assertion, effective_from: "2026-08-26T00:00:00Z"}, membership, projection, rights), /PUBLIC_ASSERTION_NOT_EFFECTIVE/);
  assert.throws(() => validateProjectionFact(fact, {...assertion, value: {text: "safe", internal_note: "secret"}}, membership, projection, rights, {evidence}), /PUBLIC_VALUE_UNSAFE/);
  assert.throws(() => validateProjectionFact(fact, assertion, membership, {...projection, as_of: "not-a-date"}, rights), /PUBLIC_ASSERTION_NOT_EFFECTIVE/);
  assert.throws(() => validateProjectionFact(fact, assertion, membership, {...projection, as_of: "0"}, rights), /PUBLIC_ASSERTION_NOT_EFFECTIVE/);
  assert.throws(() => validateProjectionFact(fact, assertion, membership, {...projection, as_of: "2026-02-30T00:00:00Z"}, rights), /PUBLIC_ASSERTION_NOT_EFFECTIVE/);
  assert.throws(() => validateProjectionFact(fact, assertion, membership, {...projection, as_of: null}, rights), /PUBLIC_ASSERTION_NOT_EFFECTIVE/);
  assert.throws(() => validateProjectionFact(fact, assertion, membership, {...projection, as_of: 0}, rights), /PUBLIC_ASSERTION_NOT_EFFECTIVE/);
  assert.throws(() => validateProjectionFact(fact, assertion, membership, projection, {...rights, effective_at: "not-a-date"}, {evidence}), /PUBLIC_RIGHTS_REQUIRED/);
  assert.throws(() => validateProjectionFact(fact, assertion, membership, projection, {...rights, effective_at: null}, {evidence}), /PUBLIC_RIGHTS_REQUIRED/);
  assert.throws(() => validateProjectionFact(fact, assertion, membership, projection, {...rights, id: "unrelated"}, {evidence}), /PUBLIC_RIGHTS_REQUIRED/);
  assert.throws(() => validateProjectionFact(fact, assertion, membership, projection, {...rights, allowed_use_classes: "client_public_display", allowed_field_classes: {"display.name": true}}, {evidence}), /PUBLIC_RIGHTS_REQUIRED/);
  assert.throws(() => validateProjectionFact(fact, {...assertion, value: null}, membership, projection, rights,
    {evidence}), /PUBLIC_VALUE_UNSAFE/);
  assert.throws(() => validateProjectionFact({...fact, projection_id: "wrong"}, assertion, membership, projection, rights), /PROJECTION_BINDING_REFUSED/);
  assert.throws(() => validateProjectionFact(fact, assertion, {...membership, tour_id: "wrong"}, projection, rights), /PROJECTION_BINDING_REFUSED/);
  assert.throws(() => validateProjectionFact(fact, assertion, {...membership, selected_at: null}, projection, rights), /PUBLIC_ASSERTION_NOT_EFFECTIVE/);
  assert.throws(() => validateProjectionFact(fact, assertion, {...membership, selected_at: "2026-08-25T13:00:00Z"}, projection, rights), /PUBLIC_ASSERTION_NOT_EFFECTIVE/);
});

test("migration is additive, tenant-qualified, temporal, rights-safe and append-only", () => {
  assert.match(migration, /^begin;/m); assert.match(migration, /create table if not exists ops\.tour_fact_conflict/i);
  assert.match(migration, /create table if not exists ops\.tour_conflict_resolution_receipt/i);
  assert.match(migration, /create table if not exists ops\.tour_audit_event/i);
  assert.match(migration, /create table if not exists ops\.tour_property_membership/i);
  assert.match(migration, /create table if not exists ops\.tour_public_projection_fact/i);
  assert.match(migration, /foreign key \(organization_tenant_id, property_id\)/i);
  assert.match(migration, /allowed_field_classes jsonb not null, allowed_use_classes jsonb not null/i);
  assert.match(migration, /effective_at timestamptz not null, expires_at timestamptz, revoked_at timestamptz/i);
  assert.match(migration, /effective_to is null or effective_to >= effective_from/i);
  assert.match(migration, /projection fact lacks current public assertion, rights, or safe value/i);
  assert.match(migration, /as_of timestamptz not null/i);
  assert.match(migration, /tour_public_value_safe/i);
  for (const table of ["tour_source_evidence", "tour_field_assertion", "tour_cheat_sheet_revision", "tour_audit_event"]) assert.match(migration, new RegExp(`create trigger ${table}_append_only before update or delete`, "i"), table);
  assert.match(migration, /rights receipt refuses source intake/i); assert.match(migration, /rights receipt refuses asserted field\/use/i);
  assert.match(migration, /nested exception rolls every synthetic row[\s\S]*tour foundation acceptance proof rollback/i);
  assert.match(migration, /rollback is forward-only: revoke grants and quarantine projections/i);
  assert.doesNotMatch(migration, /drop\s+table|truncate\s+table/i); assert.match(migration, /commit;/i);
});
