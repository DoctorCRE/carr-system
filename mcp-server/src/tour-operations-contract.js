// Facts-only Tour projection contracts. Rendering and publication authority
// are deliberately elsewhere.
import { sha256 } from "./sha256.js";

export const PUBLIC_TOUR_FIELD_KEYS = new Set([
  "display.name", "display.address", "suite", "property_type", "size",
  "asking_economics", "availability", "parking", "access", "photos",
  "floor_plan", "source_attribution", "as_of", "caveat",
]);
export const REQUIRED_PUBLIC_PROPERTY_FIELDS = Object.freeze(["display.name", "display.address"]);

// The CLIENT allowlist (V5-J303). PUBLIC_TOUR_FIELD_KEYS above is the set of
// fact shapes the record can hold safely; it is NOT what a client may see.
// Joe's ruling (decision 4ab3933e, 2026-09-24): a client sees exactly what is
// on today's Tour PDF -- property name, address, suite, space type, size,
// asking economics, availability and parking. Notes, owner contacts, access
// notes and every other field stay internal.
//
// Default deny: a field is internal until it is added HERE and to
// ops.tour_client_field_keys() in the same change. The database copy is the
// authority (it guards the fact insert inside the seal and names the only
// columns the share reads select); this copy refuses a bad seal before the
// database is touched and is the last filter on every client response.
// test/tour-client-share-allowlist.test.mjs binds the two lists together.
export const CLIENT_TOUR_FIELD_KEYS = Object.freeze([
  "display.name", "display.address", "suite", "property_type", "size",
  "asking_economics", "availability", "parking",
]);
export function isClientTourFieldKey(key) {
  return typeof key === "string" && CLIENT_TOUR_FIELD_KEYS.includes(key);
}
// Column name each allowlisted field takes in a client packet row.
export const CLIENT_TOUR_PACKET_COLUMNS = Object.freeze({
  "display.name": "name", "display.address": "address", suite: "suite",
  property_type: "property_type", size: "size", asking_economics: "asking_economics",
  availability: "availability", parking: "parking",
});
// Structural keys a client stop carries besides its fields: an opaque
// per-projection property reference and the stop's order and label.
export const CLIENT_TOUR_STOP_ENVELOPE_KEYS = Object.freeze(["property_ref", "route_sequence", "route_label"]);
export const PUBLIC_ASSET_REFERENCE_RE = /^asset:public:[A-Za-z0-9_-]{16,256}$/;

const metricValue = value =>
  (typeof value === "number" && Number.isFinite(value)) ||
  (typeof value === "string" && value.trim().length > 0 && value.trim().length <= 120);
function approvedMetricIsSafe(value) {
  if (!value || Array.isArray(value) || typeof value !== "object") return false;
  const keys = new Set(["value", "unit", "min", "max", "currency", "period", "label"]);
  const ownKeys = Reflect.ownKeys(value);
  for (let index = 0; index < ownKeys.length; index++)
    if (typeof ownKeys[index] !== "string" || !keys.has(ownKeys[index])) return false;
  if (!Object.hasOwn(value, "value") && !Object.hasOwn(value, "min") && !Object.hasOwn(value, "max"))
    return false;
  const metricFields = ["value", "min", "max"];
  for (let index = 0; index < metricFields.length; index++) {
    const key = metricFields[index];
    if (Object.hasOwn(value, key) && !metricValue(value[key])) return false;
  }
  const labelFields = ["unit", "currency", "period", "label"];
  for (let index = 0; index < labelFields.length; index++) {
    const key = labelFields[index];
    if (Object.hasOwn(value, key) &&
        (typeof value[key] !== "string" || !value[key].trim() || value[key].trim().length > 120))
      return false;
  }
  return !(typeof value.min === "number" && typeof value.max === "number" && value.min > value.max);
}
export function requiredTimestamp(value) {
  if (typeof value !== "string" ||
      !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/.test(value)) return false;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return false;
  return parsed.toISOString() === value || parsed.toISOString().replace(".000Z", "Z") === value;
}

const FACT_CONFIDENCE = new Set(["low", "medium", "high", "unknown"]);
const FACT_CLASSIFICATION = new Set(["public", "client_authorized", "internal", "restricted"]);
const FACT_REVIEW_STATE = new Set(["unreviewed", "reviewed", "conflicted", "superseded", "withdrawn"]);
export const CANONICAL_FACT_REQUIRED_FIELDS = Object.freeze([
  "organization_tenant_id", "property_id", "field_key", "value", "source_evidence_id", "rights_receipt_id",
  "observed_at", "effective_from", "effective_to", "confidence",
  "data_classification", "review_state",
]);

function postgresTextIsSafe(value) {
  if (typeof value !== "string" || value.includes("\u0000")) return false;
  for (let index = 0; index < value.length; index++) {
    const code = value.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return false;
      index++;
    } else if (code >= 0xdc00 && code <= 0xdfff) return false;
  }
  return true;
}

const UNSAFE_SNAPSHOT = Symbol("unsafe-snapshot");

function canonicalJsonSnapshot(value, topLevel = true, seen = new Set()) {
  if (value === null) return topLevel ? UNSAFE_SNAPSHOT : null;
  if (typeof value === "string") return postgresTextIsSafe(value) ? value : UNSAFE_SNAPSHOT;
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return Number.isFinite(value) ? value : UNSAFE_SNAPSHOT;
  if (typeof value !== "object" || seen.has(value)) return UNSAFE_SNAPSHOT;
  const prototype = Object.getPrototypeOf(value);
  if ((Array.isArray(value) && prototype !== Array.prototype) ||
      (!Array.isArray(value) && prototype !== Object.prototype && prototype !== null))
    return UNSAFE_SNAPSHOT;
  for (let cursor = value; cursor; cursor = Object.getPrototypeOf(cursor)) {
    const descriptor = Object.getOwnPropertyDescriptor(cursor, "toJSON");
    if (descriptor && (!Object.hasOwn(descriptor, "value") ||
        typeof descriptor.value === "function")) return UNSAFE_SNAPSHOT;
  }
  const ownKeys = Reflect.ownKeys(value);
  for (let index = 0; index < ownKeys.length; index++)
    if (typeof ownKeys[index] === "symbol") return UNSAFE_SNAPSHOT;
  seen.add(value);
  let snapshot;
  if (Array.isArray(value)) {
    const values = safeArrayValues(value);
    if (!values) {
      seen.delete(value);
      return UNSAFE_SNAPSHOT;
    }
    snapshot = new Array(values.length);
    for (let index = 0; index < values.length; index++) {
      const item = canonicalJsonSnapshot(values[index], false, seen);
      if (item === UNSAFE_SNAPSHOT) {
        seen.delete(value);
        return UNSAFE_SNAPSHOT;
      }
      snapshot[index] = item;
    }
  } else {
    snapshot = Object.create(null);
    for (let index = 0; index < ownKeys.length; index++) {
      const key = ownKeys[index];
      const descriptor = Object.getOwnPropertyDescriptor(value, key);
      if (typeof key !== "string" || key === "__proto__" || !postgresTextIsSafe(key) || !descriptor?.enumerable ||
          !Object.hasOwn(descriptor, "value")) {
        seen.delete(value);
        return UNSAFE_SNAPSHOT;
      }
      const item = canonicalJsonSnapshot(descriptor.value, false, seen);
      if (item === UNSAFE_SNAPSHOT) {
        seen.delete(value);
        return UNSAFE_SNAPSHOT;
      }
      snapshot[key] = item;
    }
  }
  seen.delete(value);
  return snapshot;
}

function canonicalJsonValueIsSafe(value, topLevel = true) {
  return canonicalJsonSnapshot(value, topLevel) !== UNSAFE_SNAPSHOT;
}

export function snapshotCanonicalJsonValue(value) {
  const snapshot = canonicalJsonSnapshot(value);
  if (snapshot === UNSAFE_SNAPSHOT) throw new Error("CANONICAL_JSON_UNSAFE");
  return snapshot;
}

const ownEnumerableDataDescriptor = (record, field) => {
  const descriptor = Object.getOwnPropertyDescriptor(record, field);
  return descriptor && descriptor.enumerable && Object.hasOwn(descriptor, "value")
    ? descriptor : null;
};

function safeArrayValues(value) {
  if (!Array.isArray(value) || Object.getPrototypeOf(value) !== Array.prototype)
    return null;
  const ownKeys = Reflect.ownKeys(value);
  for (let keyIndex = 0; keyIndex < ownKeys.length; keyIndex++) {
    const key = ownKeys[keyIndex];
    if (typeof key !== "string") return null;
    if (key === "length") continue;
    if (!/^(?:0|[1-9][0-9]*)$/.test(key)) return null;
    const index = Number(key);
    if (!Number.isSafeInteger(index) || index >= value.length || String(index) !== key)
      return null;
  }
  const copy = new Array(value.length);
  for (let index = 0; index < value.length; index++) {
    const descriptor = Object.getOwnPropertyDescriptor(value, String(index));
    if (!descriptor || !descriptor.enumerable || !Object.hasOwn(descriptor, "value"))
      return null;
    copy[index] = descriptor.value;
  }
  return copy;
}

function arrayContains(values, candidate) {
  for (let index = 0; index < values.length; index++)
    if (values[index] === candidate) return true;
  return false;
}

function nonemptyStringArrayIsSafe(values) {
  if (!values?.length) return false;
  for (let index = 0; index < values.length; index++)
    if (typeof values[index] !== "string" || !values[index].trim() ||
        !postgresTextIsSafe(values[index])) return false;
  return true;
}

const plainRecordEnvelopeIsSafe = record => {
  const prototype = Object.getPrototypeOf(record);
  if (prototype !== Object.prototype && prototype !== null) return false;
  for (let cursor = record; cursor; cursor = Object.getPrototypeOf(cursor)) {
    const descriptor = Object.getOwnPropertyDescriptor(cursor, "toJSON");
    if (descriptor && (!Object.hasOwn(descriptor, "value") ||
        typeof descriptor.value === "function")) return false;
  }
  return true;
};

function snapshotRecord(record, fields, requiredFields = []) {
  if (!record || Array.isArray(record) || typeof record !== "object" ||
      !plainRecordEnvelopeIsSafe(record)) return null;
  const snapshot = Object.create(null);
  for (let index = 0; index < fields.length; index++) {
    const field = fields[index];
    const descriptor = Object.getOwnPropertyDescriptor(record, field);
    if (!descriptor) {
      if (arrayContains(requiredFields, field)) return null;
      continue;
    }
    if (!descriptor.enumerable || !Object.hasOwn(descriptor, "value")) return null;
    snapshot[field] = descriptor.value;
  }
  return snapshot;
}

function snapshotRecordArray(value, fields, requiredFields = []) {
  const rows = safeArrayValues(value);
  if (!rows) return null;
  const snapshots = new Array(rows.length);
  for (let index = 0; index < rows.length; index++) {
    const snapshot = snapshotRecord(rows[index], fields, requiredFields);
    if (!snapshot) return null;
    snapshots[index] = snapshot;
  }
  return snapshots;
}

export function validateCanonicalFieldAssertion(assertion) {
  // Foundation contract primitive only. Runtime adapter enforcement belongs to
  // the dependency-gated rights-and-public-projection service slice.
  if (!assertion || Array.isArray(assertion) || typeof assertion !== "object")
    throw new Error("FACT_ASSERTION_REQUIRED");
  if (!plainRecordEnvelopeIsSafe(assertion)) throw new Error("FACT_ASSERTION_UNSAFE");
  for (const field of CANONICAL_FACT_REQUIRED_FIELDS)
    if (!ownEnumerableDataDescriptor(assertion, field))
      throw new Error(`FACT_METADATA_REQUIRED:${field}`);
  if (typeof assertion.organization_tenant_id !== "string" ||
      !assertion.organization_tenant_id.trim() || !postgresTextIsSafe(assertion.organization_tenant_id))
    throw new Error("FACT_METADATA_INVALID:organization_tenant_id");
  if (typeof assertion.field_key !== "string" ||
      !assertion.field_key.trim() || !postgresTextIsSafe(assertion.field_key))
    throw new Error("FACT_METADATA_INVALID:field_key");
  for (const field of ["property_id", "source_evidence_id", "rights_receipt_id"])
    if (typeof assertion[field] !== "string" || !FOUNDATION_UUID_RE.test(assertion[field]))
      throw new Error(`FACT_METADATA_INVALID:${field}`);
  if (!canonicalJsonValueIsSafe(assertion.value)) throw new Error("FACT_METADATA_INVALID:value");
  if (!requiredTimestamp(assertion.observed_at))
    throw new Error("FACT_METADATA_INVALID:observed_at");
  if (!requiredTimestamp(assertion.effective_from))
    throw new Error("FACT_METADATA_INVALID:effective_from");
  if (assertion.effective_to !== null && !requiredTimestamp(assertion.effective_to))
    throw new Error("FACT_METADATA_INVALID:effective_to");
  if (assertion.effective_to !== null &&
      new Date(assertion.effective_to) < new Date(assertion.effective_from))
    throw new Error("FACT_EFFECTIVE_INTERVAL_INVALID");
  if (!FACT_CONFIDENCE.has(assertion.confidence))
    throw new Error("FACT_METADATA_INVALID:confidence");
  if (!FACT_CLASSIFICATION.has(assertion.data_classification))
    throw new Error("FACT_METADATA_INVALID:data_classification");
  if (!FACT_REVIEW_STATE.has(assertion.review_state))
    throw new Error("FACT_METADATA_INVALID:review_state");
  return true;
}

const FOUNDATION_ENTITY_ENUMS = Object.freeze({
  Property: { property_status: ["active", "inactive", "withdrawn", "unknown"] },
  RightsReceipt: { status: ["active", "expired", "revoked", "unknown"] },
  SourceEvidence: {
    evidence_class: ["direct_source", "linked_artifact", "public_mirror", "inference"],
    retrieval_status: ["read", "partial", "inaccessible", "failed"],
    data_classification: ["public", "client_authorized", "internal", "restricted"],
  },
  FieldAssertion: {
    confidence: ["low", "medium", "high", "unknown"],
    data_classification: ["public", "client_authorized", "internal", "restricted"],
    review_state: ["unreviewed", "reviewed", "conflicted", "superseded", "withdrawn"],
  },
  FactConflict: { state: ["open", "resolved", "superseded"] },
  Tour: { tour_status: ["draft", "active", "completed", "cancelled", "archived"] },
  PublicTourProjection: {
    status: ["draft", "qc_blocked", "approved", "published", "superseded", "quarantined", "rolled_back"],
  },
  ProjectionFact: { display_field_key: [...PUBLIC_TOUR_FIELD_KEYS] },
  CheatSheetRevision: { status: ["draft", "saved", "superseded"] },
  ShareGrant: {
    audience: ["client", "internal"],
    status: ["active", "revoked", "expired", "rotated"],
  },
  QualityFinding: {
    artifact_type: ["public_projection", "pdf", "map", "cheat_sheet", "share_grant"],
    severity: ["blocker", "error", "warning", "info"],
    state: ["open", "accepted_risk", "resolved", "superseded"],
  },
  Publication: {
    publication_state: ["draft", "pending_qc", "approved", "published", "quarantined", "rolled_back"],
  },
});
const FOUNDATION_ARRAY_FIELDS = new Set([
  "allowed_field_classes", "allowed_use_classes", "permission_scopes",
]);
const FOUNDATION_OBJECT_FIELDS = new Set(["content", "evidence", "payload"]);
const FOUNDATION_POSITIVE_INTEGER_FIELDS = new Set([
  "receipt_version", "route_version", "route_sequence", "projection_version",
  "revision_number", "grant_version",
]);
const FOUNDATION_TIMESTAMP_FIELDS = new Set([
  "reviewed_at", "effective_at", "expires_at", "revoked_at", "retrieved_at",
  "observed_at", "effective_from", "effective_to", "opened_at", "selected_at",
  "as_of", "created_at", "occurred_at",
]);
const FOUNDATION_DIGEST_FIELDS = new Set([
  "receipt_digest", "content_digest", "assertion_set_digest", "projection_digest",
  "token_digest", "event_digest",
]);
const FOUNDATION_NULLABLE_FIELDS = new Set([
  "expires_at", "revoked_at", "supersedes_receipt_id", "effective_to",
  "rotated_from_grant_id",
]);
const FOUNDATION_UUID_FIELDS = new Set([
  "property_id", "rights_receipt_id", "supersedes_receipt_id", "source_evidence_id",
  "field_assertion_id", "conflict_id", "tour_id", "tour_stop_id", "projection_id",
  "projection_fact_id", "cheat_sheet_revision_id", "share_grant_id",
  "rotated_from_grant_id", "qc_finding_id", "artifact_id", "publication_id",
  "audit_event_id", "entity_id",
]);
const FOUNDATION_TEXT_FIELDS = new Set([
  "organization_tenant_id", "property_status", "provider", "policy_key", "terms_url",
  "reviewer", "intended_use", "status", "stable_locator", "evidence_class",
  "retrieval_status", "rights_provider", "rights_policy_key", "data_classification",
  "field_key", "confidence", "review_state", "state", "tour_name",
  "tour_status", "canonical_dataset_version", "route_label", "display_field_key",
  "editor_actor_id", "audience", "artifact_type", "check_id", "severity",
  "publication_state", "event_type", "entity_type", "actor_id",
]);
const FOUNDATION_DIGEST_RE = /^sha256:[a-f0-9]{64}$/;
const FOUNDATION_UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const foundationUuidIsSafe = value => typeof value === "string" && FOUNDATION_UUID_RE.test(value);
const foundationDigestIsSafe = value => typeof value === "string" && FOUNDATION_DIGEST_RE.test(value);

export function validateFoundationEntityFixture(entityName, record, entityContract) {
  if (!record || Array.isArray(record) || typeof record !== "object" ||
      !entityContract || typeof entityContract !== "object")
    throw new Error(`FOUNDATION_ENTITY_REQUIRED:${entityName}`);
  const fields = [...new Set([...(entityContract.identity || []), ...(entityContract.required || [])])];
  for (const field of fields)
    if (!ownEnumerableDataDescriptor(record, field))
      throw new Error(`FOUNDATION_ENTITY_FIELD_REQUIRED:${entityName}.${field}`);
  for (const field of Object.keys(record))
    if (!fields.includes(field))
      throw new Error(`FOUNDATION_ENTITY_FIELD_UNKNOWN:${entityName}.${field}`);
  for (const [field, allowed] of Object.entries(FOUNDATION_ENTITY_ENUMS[entityName] || {}))
    if (!allowed.includes(record[field]))
      throw new Error(`FOUNDATION_ENTITY_ENUM_INVALID:${entityName}.${field}`);
  for (const field of fields) {
    const value = record[field];
    if (value === null && FOUNDATION_NULLABLE_FIELDS.has(field)) continue;
    if (value == null) throw new Error(`FOUNDATION_ENTITY_VALUE_REQUIRED:${entityName}.${field}`);
    if (FOUNDATION_UUID_FIELDS.has(field) &&
        (typeof value !== "string" || !FOUNDATION_UUID_RE.test(value)))
      throw new Error(`FOUNDATION_ENTITY_UUID_INVALID:${entityName}.${field}`);
    if (FOUNDATION_TEXT_FIELDS.has(field) &&
        (typeof value !== "string" || !value.trim() || !postgresTextIsSafe(value)))
      throw new Error(`FOUNDATION_ENTITY_TEXT_INVALID:${entityName}.${field}`);
    if (FOUNDATION_ARRAY_FIELDS.has(field) &&
        (!Array.isArray(value) || value.length === 0 ||
         !Array.from({length: value.length}, (_, index) => index).every(index =>
           {
             const descriptor = Object.getOwnPropertyDescriptor(value, String(index));
             return descriptor && Object.hasOwn(descriptor, "value") &&
               typeof descriptor.value === "string" && descriptor.value.trim() &&
               postgresTextIsSafe(descriptor.value);
           })))
      throw new Error(`FOUNDATION_ENTITY_ARRAY_INVALID:${entityName}.${field}`);
    if (FOUNDATION_OBJECT_FIELDS.has(field) &&
        (Array.isArray(value) || typeof value !== "object" ||
         !canonicalJsonValueIsSafe(value, false)))
      throw new Error(`FOUNDATION_ENTITY_JSON_INVALID:${entityName}.${field}`);
    if (FOUNDATION_POSITIVE_INTEGER_FIELDS.has(field) &&
        (!Number.isInteger(value) || value < 1))
      throw new Error(`FOUNDATION_ENTITY_INTEGER_INVALID:${entityName}.${field}`);
    if (FOUNDATION_TIMESTAMP_FIELDS.has(field) && !requiredTimestamp(value))
      throw new Error(`FOUNDATION_ENTITY_TIMESTAMP_INVALID:${entityName}.${field}`);
    if (FOUNDATION_DIGEST_FIELDS.has(field) &&
        (typeof value !== "string" || !FOUNDATION_DIGEST_RE.test(value)))
      throw new Error(`FOUNDATION_ENTITY_DIGEST_INVALID:${entityName}.${field}`);
  }
  if (entityName === "FieldAssertion" && !canonicalJsonValueIsSafe(record.value))
    throw new Error("FOUNDATION_ENTITY_JSON_INVALID:FieldAssertion.value");
  if (entityName === "FieldAssertion" && record.effective_to !== null &&
      new Date(record.effective_to) < new Date(record.effective_from))
    throw new Error("FOUNDATION_ENTITY_INTERVAL_INVALID:FieldAssertion.effective_to");
  if (entityName === "RightsReceipt" && record.expires_at !== null &&
      new Date(record.expires_at) <= new Date(record.effective_at))
    throw new Error("FOUNDATION_ENTITY_INTERVAL_INVALID:RightsReceipt.expires_at");
  if (entityName === "RightsReceipt" && record.status === "revoked" && record.revoked_at === null)
    throw new Error("FOUNDATION_ENTITY_REVOCATION_INVALID:RightsReceipt.revoked_at");
  if (entityName === "ShareGrant" && record.expires_at !== null &&
      new Date(record.expires_at) <= new Date(record.created_at))
    throw new Error("FOUNDATION_ENTITY_INTERVAL_INVALID:ShareGrant.expires_at");
  if (entityName === "ShareGrant" && record.status === "revoked" && record.revoked_at === null)
    throw new Error("FOUNDATION_ENTITY_REVOCATION_INVALID:ShareGrant.revoked_at");
  if (entityName === "PublicTourProjection" && record.facts_only !== true)
    throw new Error("FOUNDATION_ENTITY_FACTS_ONLY_REQUIRED:PublicTourProjection.facts_only");
  if (entityName === "ShareGrant") {
    const scopes = record.permission_scopes;
    if (Array.from({length: scopes.length}, (_, index) => index).some(index => {
      const descriptor = Object.getOwnPropertyDescriptor(scopes, String(index));
      return !descriptor || !Object.hasOwn(descriptor, "value") ||
        !["view_packet", "view_map"].includes(descriptor.value);
    })) throw new Error("FOUNDATION_ENTITY_SCOPE_INVALID:ShareGrant.permission_scopes");
  }
  if (!canonicalJsonValueIsSafe(record, false))
    throw new Error(`FOUNDATION_ENTITY_SERIALIZATION_INVALID:${entityName}`);
  return true;
}
const requiredTime = (value, error) => {
  if (!requiredTimestamp(value)) throw new Error(error);
  return new Date(value);
};

function rightsTimestampsAreValid(receipt) {
  return requiredTimestamp(receipt?.effective_at) &&
    requiredTimestamp(receipt.reviewed_at) &&
    (receipt.expires_at === null || requiredTimestamp(receipt.expires_at)) &&
    (receipt.revoked_at === null || requiredTimestamp(receipt.revoked_at));
}

function rightsReceiptAuthorityIsComplete(receipt) {
  if (!receipt || !rightsTimestampsAreValid(receipt) ||
      !foundationUuidIsSafe(receipt.id) ||
      typeof receipt.organization_tenant_id !== "string" || !receipt.organization_tenant_id.trim() ||
      !postgresTextIsSafe(receipt.organization_tenant_id) ||
      typeof receipt.provider !== "string" || !receipt.provider.trim() ||
      !postgresTextIsSafe(receipt.provider) ||
      (receipt.sku != null && (typeof receipt.sku !== "string" || !receipt.sku.trim() ||
       !postgresTextIsSafe(receipt.sku))) ||
      typeof receipt.policy_key !== "string" || !receipt.policy_key.trim() ||
      !postgresTextIsSafe(receipt.policy_key) ||
      !Number.isInteger(receipt.receipt_version) || receipt.receipt_version < 1 ||
      !foundationDigestIsSafe(receipt.receipt_digest) ||
      typeof receipt.terms_url !== "string" || !postgresTextIsSafe(receipt.terms_url) ||
      typeof receipt.reviewer !== "string" || !receipt.reviewer.trim() ||
      !postgresTextIsSafe(receipt.reviewer) ||
      typeof receipt.intended_use !== "string" || !receipt.intended_use.trim() ||
      !postgresTextIsSafe(receipt.intended_use) ||
      (receipt.status !== "active" && receipt.status !== "revoked" && receipt.status !== "expired") ||
      (receipt.status === "revoked" && receipt.revoked_at == null)) return false;
  try {
    const terms = new URL(receipt.terms_url);
    if (terms.protocol !== "https:") return false;
  } catch {
    return false;
  }
  if ((receipt.receipt_version === 1 && receipt.supersedes_receipt_id !== null) ||
      (receipt.receipt_version > 1 && !foundationUuidIsSafe(receipt.supersedes_receipt_id)))
    return false;
  return true;
}

function revocationApplies(receipt, at, revocations) {
  if (receipt.revoked_at && new Date(receipt.revoked_at) <= at) return true;
  for (let index = 0; index < revocations.length; index++) {
    const item = revocations[index];
    if (!requiredTimestamp(item.revoked_at)) throw new Error("RIGHTS_UNKNOWN");
    const receiptId = item.rights_receipt_id ?? item.receipt_id;
    if (typeof receiptId !== "string" || !receiptId) throw new Error("RIGHTS_UNKNOWN");
    if (receiptId === receipt.id && new Date(item.revoked_at) <= at) return true;
  }
  return false;
}

const RIGHTS_RECEIPT_AUTHORITY_FIELDS = Object.freeze([
  "id", "organization_tenant_id", "provider", "sku", "policy_key",
  "receipt_version", "receipt_digest", "terms_url", "reviewed_at", "reviewer",
  "intended_use", "allowed_field_classes", "allowed_use_classes", "effective_at",
  "expires_at", "revoked_at", "supersedes_receipt_id", "status",
]);
const RIGHTS_RECEIPT_REQUIRED_FIELDS = Object.freeze([
  "id", "organization_tenant_id", "provider", "policy_key", "receipt_version",
  "receipt_digest", "terms_url", "reviewed_at", "reviewer", "intended_use",
  "allowed_field_classes", "allowed_use_classes", "effective_at", "expires_at",
  "revoked_at", "supersedes_receipt_id", "status",
]);
const REVOCATION_AUTHORITY_FIELDS = Object.freeze([
  "id", "organization_tenant_id", "rights_receipt_id", "receipt_id", "revoked_at",
  "receipt_digest", "actor_id", "created_at",
]);
const EVIDENCE_AUTHORITY_FIELDS = Object.freeze([
  "id", "organization_tenant_id", "rights_receipt_id", "rights_provider",
  "rights_policy_key", "rights_receipt_digest", "retrieved_at", "retrieval_status",
]);
const ASSERTION_AUTHORITY_FIELDS = Object.freeze([
  "id", "organization_tenant_id", "property_id", "field_key", "value",
  "source_evidence_id", "rights_receipt_id", "observed_at", "effective_from",
  "effective_to", "confidence", "data_classification", "review_state",
]);
const MEMBERSHIP_AUTHORITY_FIELDS = Object.freeze([
  "id", "organization_tenant_id", "tour_id", "property_id", "route_version",
  "route_sequence", "route_label", "selected_at", "assertion_set_digest",
]);
const PROJECTION_FACT_AUTHORITY_FIELDS = Object.freeze([
  "id", "organization_tenant_id", "projection_id", "property_id",
  "field_assertion_id", "route_version", "display_field_key", "value",
  "source_evidence_id", "rights_receipt_id", "observed_at", "effective_from",
  "effective_to",
]);
const PROJECTION_AUTHORITY_FIELDS = Object.freeze([
  "id", "organization_tenant_id", "tour_id", "projection_version", "route_version",
  "as_of", "facts_only", "projection_digest", "status", "seal_receipt",
]);
const SEAL_AUTHORITY_FIELDS = Object.freeze([
  "id", "organization_tenant_id", "projection_id", "sealed_at", "sealed_state",
  "canonical_projection_digest", "actor_id", "receipt_digest",
]);
const CONFLICT_AUTHORITY_FIELDS = Object.freeze([
  "id", "conflict_id", "organization_tenant_id", "property_id", "field_key",
  "state", "opened_at",
]);
const RESOLUTION_AUTHORITY_FIELDS = Object.freeze([
  "id", "organization_tenant_id", "conflict_id", "selected_field_assertion_id",
  "rationale", "evidence", "resolver_actor_id", "resolved_at", "receipt_digest",
  "created_at",
]);
const PARTICIPANT_AUTHORITY_FIELDS = Object.freeze([
  "organization_tenant_id", "conflict_id", "field_assertion_id", "participant_role",
]);
const MAP_POINT_AUTHORITY_FIELDS = Object.freeze([
  "property_id", "coordinate_candidate_id", "entrance_verification_receipt_id",
  "route_version",
]);
const EVIDENCE_REQUIRED_FIELDS = Object.freeze([
  "id", "organization_tenant_id", "rights_receipt_id", "rights_provider",
  "rights_policy_key", "retrieved_at", "retrieval_status",
]);
const ASSERTION_REQUIRED_FIELDS = Object.freeze([
  "id", "organization_tenant_id", "property_id", "field_key", "value",
  "source_evidence_id", "rights_receipt_id", "observed_at", "effective_from",
  "effective_to", "confidence", "data_classification", "review_state",
]);
const MEMBERSHIP_REQUIRED_FIELDS = Object.freeze([
  "organization_tenant_id", "tour_id", "property_id", "route_version", "selected_at",
]);
const PROJECTION_FACT_REQUIRED_FIELDS = Object.freeze([
  "organization_tenant_id", "projection_id", "property_id", "field_assertion_id",
  "route_version", "display_field_key",
]);
const PROJECTION_DIGEST_FACT_REQUIRED_FIELDS = Object.freeze([
  "property_id", "field_assertion_id", "route_version", "display_field_key",
]);
const PROJECTION_REQUIRED_FIELDS = Object.freeze([
  "id", "organization_tenant_id", "tour_id", "projection_version", "route_version",
  "as_of", "facts_only", "projection_digest", "status",
]);
const PROJECTION_DIGEST_REQUIRED_FIELDS = Object.freeze([
  "id", "organization_tenant_id", "tour_id", "projection_version", "route_version", "as_of",
]);
const PROJECTION_FACT_CONTEXT_REQUIRED_FIELDS = Object.freeze([
  "id", "organization_tenant_id", "tour_id", "route_version", "as_of",
]);
const SEAL_REQUIRED_FIELDS = Object.freeze([
  "organization_tenant_id", "projection_id", "sealed_at", "sealed_state",
  "canonical_projection_digest", "actor_id", "receipt_digest",
]);
const CONFLICT_REQUIRED_FIELDS = Object.freeze([
  "organization_tenant_id", "property_id", "field_key", "state", "opened_at",
]);
const RESOLUTION_REQUIRED_FIELDS = Object.freeze([
  "id", "organization_tenant_id", "conflict_id", "selected_field_assertion_id",
  "rationale", "evidence", "resolver_actor_id", "resolved_at", "receipt_digest",
  "created_at",
]);
const PARTICIPANT_REQUIRED_FIELDS = Object.freeze([
  "organization_tenant_id", "conflict_id", "field_assertion_id", "participant_role",
]);
const MAP_POINT_REQUIRED_FIELDS = MAP_POINT_AUTHORITY_FIELDS;

function rightsReceiptExactlyMatches(left, right) {
  for (let index = 0; index < RIGHTS_RECEIPT_AUTHORITY_FIELDS.length; index++) {
    const field = RIGHTS_RECEIPT_AUTHORITY_FIELDS[index];
    const leftValue = left?.[field];
    const rightValue = right?.[field];
    if (Array.isArray(leftValue) || Array.isArray(rightValue)) {
      const leftValues = safeArrayValues(leftValue);
      const rightValues = safeArrayValues(rightValue);
      if (!leftValues || !rightValues || leftValues.length !== rightValues.length)
        return false;
      for (let valueIndex = 0; valueIndex < leftValues.length; valueIndex++)
        if (leftValues[valueIndex] !== rightValues[valueIndex]) return false;
    } else if (leftValue !== rightValue) return false;
  }
  return true;
}

export function evaluateRightsReceipt(receipt, {
  at, fieldKey = null, useClass, lineage = [], revocations = [],
} = {}) {
  const evaluationTime = requiredTime(at, "RIGHTS_EVALUATION_TIME_REQUIRED");
  const receiptSnapshot = snapshotRecord(receipt, RIGHTS_RECEIPT_AUTHORITY_FIELDS,
    RIGHTS_RECEIPT_REQUIRED_FIELDS);
  const revocationRows = snapshotRecordArray(revocations, REVOCATION_AUTHORITY_FIELDS);
  const lineageRows = snapshotRecordArray(lineage, RIGHTS_RECEIPT_AUTHORITY_FIELDS,
    RIGHTS_RECEIPT_REQUIRED_FIELDS);
  const allowedUseClasses = safeArrayValues(receiptSnapshot?.allowed_use_classes);
  const allowedFieldClasses = safeArrayValues(receiptSnapshot?.allowed_field_classes);
  if (!receiptSnapshot || !rightsReceiptAuthorityIsComplete(receiptSnapshot) ||
      !revocationRows || !lineageRows || !allowedUseClasses || !allowedFieldClasses ||
      !nonemptyStringArrayIsSafe(allowedUseClasses) ||
      !nonemptyStringArrayIsSafe(allowedFieldClasses))
    throw new Error("RIGHTS_UNKNOWN");
  for (let index = 0; index < lineageRows.length; index++)
    if (!rightsReceiptAuthorityIsComplete(lineageRows[index])) throw new Error("RIGHTS_UNKNOWN");
  if (revocationApplies(receiptSnapshot, evaluationTime, revocationRows) ||
      receiptSnapshot.status === "revoked") throw new Error("RIGHTS_REVOKED");
  if (new Date(receiptSnapshot.effective_at) > evaluationTime) throw new Error("RIGHTS_NOT_EFFECTIVE");
  if (receiptSnapshot.status === "expired" ||
      (receiptSnapshot.expires_at && requiredTimestamp(receiptSnapshot.expires_at) &&
       new Date(receiptSnapshot.expires_at) <= evaluationTime)) throw new Error("RIGHTS_EXPIRED");
  if (receiptSnapshot.status !== "active") throw new Error("RIGHTS_UNKNOWN");
  if (!arrayContains(allowedUseClasses, useClass)) throw new Error("RIGHTS_USE_NOT_ALLOWED");
  if (fieldKey != null && !(arrayContains(allowedFieldClasses, fieldKey) ||
      arrayContains(allowedFieldClasses, "*"))) throw new Error("RIGHTS_FIELD_NOT_ALLOWED");
  for (let index = 0; index < lineageRows.length; index++) {
    const item = lineageRows[index];
    if (item.organization_tenant_id !== receiptSnapshot.organization_tenant_id) continue;
    if (item.id === receiptSnapshot.id && !rightsReceiptExactlyMatches(item, receiptSnapshot))
      throw new Error("RIGHTS_CONFLICT");
    if (item.provider !== receiptSnapshot.provider || item.policy_key !== receiptSnapshot.policy_key) continue;
    if (item.id !== receiptSnapshot.id && item.receipt_version === receiptSnapshot.receipt_version &&
        item.status === "active" && new Date(item.effective_at) <= evaluationTime &&
        (!item.expires_at || new Date(item.expires_at) > evaluationTime) &&
        !revocationApplies(item, evaluationTime, revocationRows))
      throw new Error("RIGHTS_CONFLICT");
    if (Number.isInteger(item.receipt_version) && item.receipt_version > receiptSnapshot.receipt_version &&
        new Date(item.effective_at) <= evaluationTime) throw new Error("RIGHTS_SUPERSEDED");
  }
  return true;
}

export function validateEvidenceRightsLineage(evidence, assertion, rightsReceipt) {
  const evidenceSnapshot = snapshotRecord(evidence, EVIDENCE_AUTHORITY_FIELDS,
    EVIDENCE_REQUIRED_FIELDS);
  const assertionSnapshot = snapshotRecord(assertion, ASSERTION_AUTHORITY_FIELDS,
    ASSERTION_REQUIRED_FIELDS);
  const receiptSnapshot = snapshotRecord(rightsReceipt, RIGHTS_RECEIPT_AUTHORITY_FIELDS,
    RIGHTS_RECEIPT_REQUIRED_FIELDS);
  if (!evidenceSnapshot || !assertionSnapshot || !receiptSnapshot ||
      evidenceSnapshot.organization_tenant_id !== assertionSnapshot.organization_tenant_id ||
      assertionSnapshot.organization_tenant_id !== receiptSnapshot.organization_tenant_id ||
      evidenceSnapshot.id !== assertionSnapshot.source_evidence_id ||
      evidenceSnapshot.rights_receipt_id !== assertionSnapshot.rights_receipt_id ||
      evidenceSnapshot.rights_receipt_id !== receiptSnapshot.id ||
      typeof evidenceSnapshot.rights_provider !== "string" || !evidenceSnapshot.rights_provider.trim() ||
      evidenceSnapshot.rights_provider !== receiptSnapshot.provider ||
      typeof evidenceSnapshot.rights_policy_key !== "string" || !evidenceSnapshot.rights_policy_key.trim() ||
      evidenceSnapshot.rights_policy_key !== receiptSnapshot.policy_key ||
      (evidenceSnapshot.rights_receipt_digest != null &&
       evidenceSnapshot.rights_receipt_digest !== receiptSnapshot.receipt_digest))
    throw new Error("EVIDENCE_RIGHTS_LINEAGE_MISMATCH");
  if (!requiredTimestamp(evidenceSnapshot.retrieved_at)) throw new Error("RETRIEVED_AT_REQUIRED");
  if (evidenceSnapshot.retrieval_status !== "read") throw new Error("EVIDENCE_UNRESOLVED");
  if (!requiredTimestamp(assertionSnapshot.observed_at)) throw new Error("OBSERVED_AT_REQUIRED");
  return true;
}

function publicAssetIsSafe(item) {
  if (!item || Array.isArray(item) || typeof item !== "object") return false;
  const keys = new Set(["asset_ref", "alt", "caption", "source"]);
  const ownKeys = Reflect.ownKeys(item);
  for (let index = 0; index < ownKeys.length; index++) {
    const key = ownKeys[index];
    const descriptor = typeof key === "string"
      ? Object.getOwnPropertyDescriptor(item, key) : null;
    if (!descriptor?.enumerable || !Object.hasOwn(descriptor, "value") || !keys.has(key) ||
        (key !== "asset_ref" && typeof descriptor.value !== "string")) return false;
  }
  const assetRef = ownEnumerableDataDescriptor(item, "asset_ref");
  return Boolean(assetRef && PUBLIC_ASSET_REFERENCE_RE.test(assetRef.value || ""));
}

export function snapshotPublicValue(fieldKey, value) {
  const snapshot = canonicalJsonSnapshot(value);
  if (snapshot === UNSAFE_SNAPSHOT) throw new Error("PUBLIC_VALUE_UNSAFE");
  if (fieldKey === "display.name" || fieldKey === "display.address") {
    if (typeof snapshot === "string" && snapshot.trim().length > 0 && snapshot.trim().length <= 360)
      return snapshot;
  } else if (fieldKey === "suite" || fieldKey === "property_type" ||
      fieldKey === "availability" || fieldKey === "parking" || fieldKey === "access" ||
      fieldKey === "source_attribution" || fieldKey === "as_of" || fieldKey === "caveat") {
    if (typeof snapshot === "string") return snapshot;
  } else if (fieldKey === "size" || fieldKey === "asking_economics") {
    if (approvedMetricIsSafe(snapshot)) return snapshot;
  } else if (fieldKey === "photos" || fieldKey === "floor_plan") {
    const assets = safeArrayValues(snapshot);
    if (!assets) throw new Error("PUBLIC_VALUE_UNSAFE");
    for (let index = 0; index < assets.length; index++)
      if (!publicAssetIsSafe(assets[index])) throw new Error("PUBLIC_VALUE_UNSAFE");
    return snapshot;
  }
  throw new Error("PUBLIC_VALUE_UNSAFE");
}

export function publicValueIsSafe(fieldKey, value) {
  try {
    snapshotPublicValue(fieldKey, value);
    return true;
  } catch {
    return false;
  }
}

export function canonicalProjectionDigest(input) {
  const inputSnapshot = snapshotRecord(input, ["projection", "facts", "map_points"]);
  const projection = snapshotRecord(inputSnapshot?.projection, PROJECTION_AUTHORITY_FIELDS,
    PROJECTION_DIGEST_REQUIRED_FIELDS);
  if (!projection || typeof projection !== "object") throw new Error("PROJECTION_REQUIRED");
  if (!requiredTimestamp(projection.as_of)) throw new Error("PROJECTION_AS_OF_REQUIRED");
  const compareUtf8 = (left, right) =>
    Buffer.compare(Buffer.from(String(left), "utf8"), Buffer.from(String(right), "utf8"));
  const factRows = snapshotRecordArray(inputSnapshot?.facts ?? [], PROJECTION_FACT_AUTHORITY_FIELDS,
    PROJECTION_DIGEST_FACT_REQUIRED_FIELDS);
  const mapPointRows = snapshotRecordArray(inputSnapshot?.map_points ?? [], MAP_POINT_AUTHORITY_FIELDS,
    MAP_POINT_REQUIRED_FIELDS);
  if (!factRows || !mapPointRows) throw new Error("PROJECTION_INCOMPLETE");
  if (typeof projection.organization_tenant_id !== "string" ||
      !projection.organization_tenant_id.trim() ||
      !postgresTextIsSafe(projection.organization_tenant_id) ||
      !foundationUuidIsSafe(projection.tour_id) ||
      !foundationUuidIsSafe(projection.id) ||
      !Number.isInteger(projection.projection_version) || projection.projection_version < 1 ||
      !Number.isInteger(projection.route_version) || projection.route_version < 1)
    throw new Error("PROJECTION_INCOMPLETE");
  for (let index = 0; index < factRows.length; index++) {
    const fact = factRows[index];
    if (!foundationUuidIsSafe(fact.property_id) ||
        !foundationUuidIsSafe(fact.field_assertion_id) ||
        !Number.isInteger(fact.route_version) || fact.route_version < 1 ||
        typeof fact.display_field_key !== "string" || !fact.display_field_key.trim() ||
        !postgresTextIsSafe(fact.display_field_key)) throw new Error("PROJECTION_INCOMPLETE");
  }
  for (let index = 0; index < mapPointRows.length; index++) {
    const point = mapPointRows[index];
    if (!foundationUuidIsSafe(point.property_id) ||
        !foundationUuidIsSafe(point.coordinate_candidate_id) ||
        !foundationUuidIsSafe(point.entrance_verification_receipt_id) ||
        !Number.isInteger(point.route_version) || point.route_version < 1)
      throw new Error("PROJECTION_INCOMPLETE");
  }
  projection.tour_id = projection.tour_id.toLowerCase();
  projection.id = projection.id.toLowerCase();
  for (let index = 0; index < factRows.length; index++) {
    factRows[index].property_id = factRows[index].property_id.toLowerCase();
    factRows[index].field_assertion_id = factRows[index].field_assertion_id.toLowerCase();
  }
  for (let index = 0; index < mapPointRows.length; index++) {
    mapPointRows[index].property_id = mapPointRows[index].property_id.toLowerCase();
    mapPointRows[index].coordinate_candidate_id = mapPointRows[index].coordinate_candidate_id.toLowerCase();
    mapPointRows[index].entrance_verification_receipt_id =
      mapPointRows[index].entrance_verification_receipt_id.toLowerCase();
  }
  const facts = new Array(factRows.length);
  for (let index = 0; index < factRows.length; index++) facts[index] = factRows[index];
  const mapPoints = new Array(mapPointRows.length);
  for (let index = 0; index < mapPointRows.length; index++) mapPoints[index] = mapPointRows[index];
  const sortRows = (rows, compare) => {
    for (let index = 1; index < rows.length; index++) {
      const candidate = rows[index];
      let cursor = index - 1;
      while (cursor >= 0 && compare(rows[cursor], candidate) > 0) {
        rows[cursor + 1] = rows[cursor];
        cursor--;
      }
      rows[cursor + 1] = candidate;
    }
  };
  sortRows(facts, (left, right) =>
    compareUtf8(left.property_id || "", right.property_id || "") ||
    compareUtf8(left.display_field_key || "", right.display_field_key || "") ||
    compareUtf8(left.field_assertion_id || "", right.field_assertion_id || ""));
  sortRows(mapPoints, (left, right) =>
    compareUtf8(left.property_id || "", right.property_id || "") ||
    compareUtf8(left.coordinate_candidate_id || "", right.coordinate_candidate_id || "") ||
    compareUtf8(left.entrance_verification_receipt_id || "", right.entrance_verification_receipt_id || ""));
  const encoded = value => Buffer.from(String(value), "utf8").toString("base64");
  const lines = [
    "public-tour-projection-digest.v2",
    encoded(projection.organization_tenant_id),
    String(projection.tour_id),
    String(projection.id),
    String(projection.projection_version),
    String(projection.route_version),
    new Date(projection.as_of).toISOString(),
  ];
  for (let index = 0; index < facts.length; index++) {
    const fact = facts[index];
    lines.push([fact.property_id, fact.field_assertion_id, fact.route_version,
      encoded(fact.display_field_key)].join("|"));
  }
  if (mapPoints.length) {
    lines.push("map");
    for (let index = 0; index < mapPoints.length; index++) {
      const point = mapPoints[index];
      lines.push([point.property_id, point.coordinate_candidate_id,
        point.entrance_verification_receipt_id, point.route_version].join("|"));
    }
  }
  return "sha256:" + sha256(lines.join("\n"));
}

export function assertProjectionDigest(input, claimedDigest = input?.projection?.projection_digest) {
  if (canonicalProjectionDigest(input) !== claimedDigest)
    throw new Error("PROJECTION_DIGEST_MISMATCH");
  return true;
}

export function validateProjectionDraft(projection) {
  const snapshot = snapshotRecord(projection, PROJECTION_AUTHORITY_FIELDS, ["status"]);
  if (!snapshot || snapshot.status !== "draft")
    throw new Error("PROJECTION_STATUS_DRAFT_REQUIRED");
  return true;
}

function normalizeProjectionOptions(receiptLineage, evidence, revocations) {
  if (Array.isArray(receiptLineage)) return { lineage: receiptLineage, evidence, revocations };
  const options = snapshotRecord(receiptLineage, ["lineage", "evidence", "revocations"]);
  if (options) return {
    lineage: options.lineage ?? [],
    evidence: options.evidence ?? evidence,
    revocations: options.revocations ?? revocations,
  };
  return { lineage: [], evidence, revocations };
}

export function validateProjectionFact(fact, assertion, membership, projection, rightsReceipt,
  receiptLineage = [], evidence = null, revocations = []) {
  const factSnapshot = snapshotRecord(fact, PROJECTION_FACT_AUTHORITY_FIELDS,
    PROJECTION_FACT_REQUIRED_FIELDS);
  const assertionSnapshot = snapshotRecord(assertion, ASSERTION_AUTHORITY_FIELDS,
    ASSERTION_REQUIRED_FIELDS);
  const membershipSnapshot = snapshotRecord(membership, MEMBERSHIP_AUTHORITY_FIELDS,
    MEMBERSHIP_REQUIRED_FIELDS);
  const projectionSnapshot = snapshotRecord(projection, PROJECTION_AUTHORITY_FIELDS,
    PROJECTION_FACT_CONTEXT_REQUIRED_FIELDS);
  const receiptSnapshot = snapshotRecord(rightsReceipt, RIGHTS_RECEIPT_AUTHORITY_FIELDS,
    RIGHTS_RECEIPT_REQUIRED_FIELDS);
  if (!factSnapshot || !assertionSnapshot || !membershipSnapshot || !projectionSnapshot ||
      !receiptSnapshot) throw new Error("PROJECTION_RIGHTS_REQUIRED");
  if (!PUBLIC_TOUR_FIELD_KEYS.has(factSnapshot.display_field_key))
    throw new Error("PUBLIC_FIELD_NOT_ALLOWLISTED");
  if (factSnapshot.organization_tenant_id !== assertionSnapshot.organization_tenant_id ||
      factSnapshot.organization_tenant_id !== membershipSnapshot.organization_tenant_id ||
      factSnapshot.organization_tenant_id !== projectionSnapshot.organization_tenant_id ||
      factSnapshot.organization_tenant_id !== receiptSnapshot.organization_tenant_id)
    throw new Error("TENANT_SCOPE_REFUSED");
  if (factSnapshot.projection_id !== projectionSnapshot.id ||
      membershipSnapshot.tour_id !== projectionSnapshot.tour_id)
    throw new Error("PROJECTION_BINDING_REFUSED");
  if (factSnapshot.property_id !== assertionSnapshot.property_id ||
      factSnapshot.property_id !== membershipSnapshot.property_id)
    throw new Error("PROJECTION_PROPERTY_MISMATCH");
  if (factSnapshot.field_assertion_id !== assertionSnapshot.id ||
      assertionSnapshot.review_state !== "reviewed" ||
      assertionSnapshot.data_classification !== "public") throw new Error("PUBLIC_ASSERTION_REQUIRED");
  if (assertionSnapshot.confidence !== "low" && assertionSnapshot.confidence !== "medium" &&
      assertionSnapshot.confidence !== "high")
    throw new Error("PUBLIC_ASSERTION_UNRESOLVED");
  if (factSnapshot.display_field_key !== assertionSnapshot.field_key)
    throw new Error("PUBLIC_FIELD_RELABEL_REFUSED");
  if (membershipSnapshot.route_version !== factSnapshot.route_version ||
      projectionSnapshot.route_version !== factSnapshot.route_version) throw new Error("ROUTE_VERSION_MISMATCH");
  if (!requiredTimestamp(projectionSnapshot.as_of) || !requiredTimestamp(membershipSnapshot.selected_at) ||
      !requiredTimestamp(assertionSnapshot.effective_from) ||
      !requiredTimestamp(assertionSnapshot.observed_at) ||
      (assertionSnapshot.effective_to !== null && !requiredTimestamp(assertionSnapshot.effective_to)))
    throw new Error("PUBLIC_ASSERTION_NOT_EFFECTIVE");
  const asOf = new Date(projectionSnapshot.as_of);
  if (new Date(membershipSnapshot.selected_at) > asOf ||
      new Date(assertionSnapshot.effective_from) > asOf ||
      new Date(assertionSnapshot.observed_at) > asOf ||
      (assertionSnapshot.effective_to && new Date(assertionSnapshot.effective_to) <= asOf))
    throw new Error("PUBLIC_ASSERTION_NOT_EFFECTIVE");
  if (assertionSnapshot.rights_receipt_id !== receiptSnapshot.id)
    throw new Error("PUBLIC_RIGHTS_REQUIRED");
  const options = normalizeProjectionOptions(receiptLineage, evidence, revocations);
  if (!options.evidence) throw new Error("PROJECTION_EVIDENCE_REQUIRED");
  const evidenceSnapshot = snapshotRecord(options.evidence, EVIDENCE_AUTHORITY_FIELDS,
    EVIDENCE_REQUIRED_FIELDS);
  if (!evidenceSnapshot) throw new Error("PROJECTION_EVIDENCE_REQUIRED");
  validateEvidenceRightsLineage(evidenceSnapshot, assertionSnapshot, receiptSnapshot);
  if (new Date(evidenceSnapshot.retrieved_at) > asOf)
    throw new Error("PUBLIC_ASSERTION_NOT_EFFECTIVE");
  try {
    evaluateRightsReceipt(receiptSnapshot, {
      at: projectionSnapshot.as_of, fieldKey: assertionSnapshot.field_key,
      useClass: "client_public_display", lineage: options.lineage,
      revocations: options.revocations,
    });
  } catch (error) {
    if (error.message === "RIGHTS_SUPERSEDED") throw new Error("PUBLIC_RIGHTS_SUPERSEDED");
    throw new Error("PUBLIC_RIGHTS_REQUIRED");
  }
  if (!publicValueIsSafe(assertionSnapshot.field_key, assertionSnapshot.value)) {
    if (assertionSnapshot.field_key === "photos" || assertionSnapshot.field_key === "floor_plan")
      throw new Error("PUBLIC_ASSET_REFERENCE_REQUIRED");
    throw new Error("PUBLIC_VALUE_UNSAFE");
  }
  return true;
}

const mapById = values => {
  const mapped = new Map();
  for (let index = 0; index < values.length; index++) mapped.set(values[index].id, values[index]);
  return mapped;
};

function conflictResolutionIsComplete(resolution, conflictId, assertionId, tenantId,
  conflictParticipants) {
  const snapshot = snapshotRecord(resolution, RESOLUTION_AUTHORITY_FIELDS,
    RESOLUTION_REQUIRED_FIELDS);
  if (!snapshot || snapshot.organization_tenant_id !== tenantId ||
      snapshot.conflict_id !== conflictId ||
      snapshot.selected_field_assertion_id !== assertionId ||
      !foundationUuidIsSafe(snapshot.id) ||
      !foundationUuidIsSafe(snapshot.conflict_id) ||
      !foundationUuidIsSafe(snapshot.selected_field_assertion_id) ||
      typeof snapshot.rationale !== "string" || !snapshot.rationale.trim() ||
      !postgresTextIsSafe(snapshot.rationale) ||
      !snapshot.evidence || Array.isArray(snapshot.evidence) ||
      typeof snapshot.evidence !== "object" ||
      !canonicalJsonValueIsSafe(snapshot.evidence, false) ||
      typeof snapshot.resolver_actor_id !== "string" ||
      !snapshot.resolver_actor_id.trim() ||
      !postgresTextIsSafe(snapshot.resolver_actor_id) ||
      !requiredTimestamp(snapshot.resolved_at) ||
      !requiredTimestamp(snapshot.created_at) ||
      !foundationDigestIsSafe(snapshot.receipt_digest)) return false;
  for (let index = 0; index < conflictParticipants.length; index++) {
    const participant = conflictParticipants[index];
    if (participant && participant.organization_tenant_id === tenantId &&
        participant.conflict_id === conflictId &&
        participant.field_assertion_id === assertionId &&
        (participant.participant_role === "candidate" || participant.participant_role === "selected" ||
         participant.participant_role === "rejected"))
      return true;
  }
  return false;
}

export function validateProjectionComplete({
  projection, memberships = [], facts = [], assertions = [], evidence = [], rights = [],
  lineage = [], revocations = [], conflicts = [], conflict_resolutions = [],
  conflict_participants = [], map_points = [],
  requiredFieldKeys = REQUIRED_PUBLIC_PROPERTY_FIELDS,
} = {}) {
  const projectionSnapshot = snapshotRecord(projection, PROJECTION_AUTHORITY_FIELDS,
    PROJECTION_REQUIRED_FIELDS);
  if (!projectionSnapshot || projectionSnapshot.status !== "approved")
    throw new Error("PROJECTION_STATUS_APPROVED_REQUIRED");
  const seal = snapshotRecord(projectionSnapshot.seal_receipt, SEAL_AUTHORITY_FIELDS,
    SEAL_REQUIRED_FIELDS);
  if (!seal || seal.organization_tenant_id !== projectionSnapshot.organization_tenant_id ||
      seal.projection_id !== projectionSnapshot.id || seal.sealed_state !== "approved" ||
      !requiredTimestamp(seal.sealed_at) ||
      seal.canonical_projection_digest !== projectionSnapshot.projection_digest ||
      !/^sha256:[a-f0-9]{64}$/.test(seal.receipt_digest || "") ||
      typeof seal.actor_id !== "string" || !seal.actor_id)
    throw new Error("PROJECTION_SEAL_REQUIRED");
  if (!requiredTimestamp(projectionSnapshot.as_of) || projectionSnapshot.facts_only !== true)
    throw new Error("PROJECTION_INCOMPLETE");
  const membershipRows = snapshotRecordArray(memberships, MEMBERSHIP_AUTHORITY_FIELDS,
    MEMBERSHIP_REQUIRED_FIELDS);
  const factRows = snapshotRecordArray(facts, PROJECTION_FACT_AUTHORITY_FIELDS,
    PROJECTION_FACT_REQUIRED_FIELDS);
  const assertionRows = snapshotRecordArray(assertions, ASSERTION_AUTHORITY_FIELDS,
    ASSERTION_REQUIRED_FIELDS);
  const evidenceRows = snapshotRecordArray(evidence, EVIDENCE_AUTHORITY_FIELDS,
    EVIDENCE_REQUIRED_FIELDS);
  const rightsRows = snapshotRecordArray(rights, RIGHTS_RECEIPT_AUTHORITY_FIELDS,
    RIGHTS_RECEIPT_REQUIRED_FIELDS);
  const conflictRows = snapshotRecordArray(conflicts, CONFLICT_AUTHORITY_FIELDS,
    CONFLICT_REQUIRED_FIELDS);
  const resolutionRows = snapshotRecordArray(conflict_resolutions, RESOLUTION_AUTHORITY_FIELDS);
  const participantRows = snapshotRecordArray(conflict_participants,
    PARTICIPANT_AUTHORITY_FIELDS, PARTICIPANT_REQUIRED_FIELDS);
  const requiredFields = safeArrayValues(requiredFieldKeys);
  if (!membershipRows || !factRows || !assertionRows || !evidenceRows || !rightsRows ||
      !conflictRows || !resolutionRows || !participantRows || !requiredFields)
    throw new Error("PROJECTION_INCOMPLETE");
  const selected = [];
  for (let index = 0; index < membershipRows.length; index++) {
    const item = membershipRows[index];
    if (item.organization_tenant_id === projectionSnapshot.organization_tenant_id &&
        item.tour_id === projectionSnapshot.tour_id &&
        item.route_version === projectionSnapshot.route_version &&
        requiredTimestamp(item.selected_at) &&
        new Date(item.selected_at) <= new Date(projectionSnapshot.as_of)) selected.push(item);
  }
  if (!selected.length || selected.length !== membershipRows.length)
    throw new Error("PROJECTION_INCOMPLETE");
  for (let index = 0; index < selected.length; index++) {
    const membership = selected[index];
    if (typeof membership.organization_tenant_id !== "string" ||
        !membership.organization_tenant_id.trim() ||
        !postgresTextIsSafe(membership.organization_tenant_id) ||
        !foundationUuidIsSafe(membership.tour_id) ||
        !foundationUuidIsSafe(membership.property_id) ||
        !Number.isInteger(membership.route_version) || membership.route_version < 1)
      throw new Error("PROJECTION_INCOMPLETE");
  }
  for (let index = 0; index < factRows.length; index++) {
    const fact = factRows[index];
    if (typeof fact.organization_tenant_id !== "string" ||
        !fact.organization_tenant_id.trim() ||
        !postgresTextIsSafe(fact.organization_tenant_id) ||
        !foundationUuidIsSafe(fact.projection_id) ||
        !foundationUuidIsSafe(fact.property_id) ||
        !foundationUuidIsSafe(fact.field_assertion_id) ||
        !Number.isInteger(fact.route_version) || fact.route_version < 1 ||
        typeof fact.display_field_key !== "string" || !fact.display_field_key.trim() ||
        !postgresTextIsSafe(fact.display_field_key)) throw new Error("PROJECTION_INCOMPLETE");
  }
  const selectedPropertyCounts = new Map();
  for (let index = 0; index < selected.length; index++) {
    const propertyId = selected[index].property_id;
    selectedPropertyCounts.set(propertyId, (selectedPropertyCounts.get(propertyId) || 0) + 1);
  }
  const factKeys = new Set();
  for (let index = 0; index < factRows.length; index++) {
    const fact = factRows[index];
    if (selectedPropertyCounts.get(fact.property_id) !== 1)
      throw new Error("PROJECTION_INCOMPLETE");
    const key = `${fact.property_id}\u001f${fact.display_field_key}`;
    if (factKeys.has(key)) throw new Error("PROJECTION_INCOMPLETE");
    factKeys.add(key);
  }
  const assertionById = mapById(assertionRows);
  const evidenceById = mapById(evidenceRows);
  const rightsById = mapById(rightsRows);
  for (let membershipIndex = 0; membershipIndex < selected.length; membershipIndex++) {
    const membership = selected[membershipIndex];
    const propertyFacts = [];
    for (let factIndex = 0; factIndex < factRows.length; factIndex++)
      if (factRows[factIndex].property_id === membership.property_id)
        propertyFacts.push(factRows[factIndex]);
    for (let fieldIndex = 0; fieldIndex < requiredFields.length; fieldIndex++) {
      let found = false;
      for (let factIndex = 0; factIndex < propertyFacts.length; factIndex++)
        if (propertyFacts[factIndex].display_field_key === requiredFields[fieldIndex]) {
          found = true;
          break;
        }
      if (!found) throw new Error("PROJECTION_INCOMPLETE");
    }
    for (let propertyFactIndex = 0; propertyFactIndex < propertyFacts.length; propertyFactIndex++) {
      const fact = propertyFacts[propertyFactIndex];
      const assertion = assertionById.get(fact.field_assertion_id);
      const source = assertion && evidenceById.get(assertion.source_evidence_id);
      const receipt = assertion && rightsById.get(assertion.rights_receipt_id);
      if (!assertion || !source || !receipt) throw new Error("PROJECTION_INCOMPLETE");
      let unresolvedConflict = false;
      for (let conflictIndex = 0; conflictIndex < conflictRows.length; conflictIndex++) {
        const conflict = conflictRows[conflictIndex];
        if (!conflict || conflict.organization_tenant_id !== projectionSnapshot.organization_tenant_id ||
            conflict.property_id !== fact.property_id || conflict.field_key !== fact.display_field_key ||
            conflict.state === "superseded") continue;
        if (requiredTimestamp(conflict.opened_at) &&
            new Date(conflict.opened_at) > new Date(projectionSnapshot.as_of)) continue;
        const conflictId = conflict.id ?? conflict.conflict_id;
        let resolved = false;
        for (let resolutionIndex = 0; resolutionIndex < resolutionRows.length; resolutionIndex++) {
          const resolution = resolutionRows[resolutionIndex];
          if (conflictResolutionIsComplete(resolution, conflictId, assertion.id,
              projectionSnapshot.organization_tenant_id, participantRows) &&
              new Date(resolution.resolved_at) <= new Date(projectionSnapshot.as_of)) {
            resolved = true;
            break;
          }
        }
        if (!resolved) {
          unresolvedConflict = true;
          break;
        }
      }
      if (unresolvedConflict) throw new Error("PUBLIC_ASSERTION_CONFLICTED");
      validateProjectionFact(fact, assertion, membership, projectionSnapshot, receipt, {
        lineage, evidence: source, revocations,
      });
    }
  }
  const digestFacts = new Array(factRows.length);
  for (let index = 0; index < factRows.length; index++) {
    const fact = factRows[index];
    const assertion = assertionById.get(fact.field_assertion_id) || {};
    digestFacts[index] = {
      ...fact, value: assertion.value, source_evidence_id: assertion.source_evidence_id,
      rights_receipt_id: assertion.rights_receipt_id, observed_at: assertion.observed_at,
      effective_from: assertion.effective_from, effective_to: assertion.effective_to,
    };
  }
  assertProjectionDigest({ projection: projectionSnapshot, memberships, facts: digestFacts, map_points });
  return true;
}
